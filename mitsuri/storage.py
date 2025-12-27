import datetime
import logging

import certifi
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from mitsuri.config import (
    MONGO_URI,
    MONGO_MAX_POOL_SIZE,
    MONGO_MIN_POOL_SIZE,
    HISTORY_LIMIT,
    MAX_HISTORY_STORED,
)

logger = logging.getLogger(__name__)


def create_mongo_client():
    """Create MongoDB client with connection pooling and proper configuration."""
    try:
        mongo_client = MongoClient(
            MONGO_URI,
            serverSelectionTimeoutMS=5000,
            tls=True,
            tlsCAFile=certifi.where(),
            maxPoolSize=MONGO_MAX_POOL_SIZE,
            minPoolSize=MONGO_MIN_POOL_SIZE,
            retryWrites=True,
            retryReads=True,
            connectTimeoutMS=10000,
            socketTimeoutMS=10000,
        )
        mongo_client.admin.command("ping")
        logger.info("✅ MongoDB connected with pool size: %d-%d", 
                   MONGO_MIN_POOL_SIZE, MONGO_MAX_POOL_SIZE)
        return mongo_client
    except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
        logger.critical("❌ MongoDB connection failed: %s", exc)
        raise


def initialize_indexes(db):
    """Create all necessary indexes for optimal query performance."""
    logger.info("🔧 Creating database indexes...")
    
    chat_collection = db["chat_info"]
    history_collection = db["chat_history"]
    
    # Chat collection indexes
    chat_collection.create_index([("chat_id", ASCENDING)], unique=True)
    chat_collection.create_index([("type", ASCENDING)])
    chat_collection.create_index([("last_active", DESCENDING)])
    
    # History collection indexes - CRITICAL for performance
    history_collection.create_index([
        ("chat_id", ASCENDING),
        ("timestamp", DESCENDING)
    ])
    history_collection.create_index([("timestamp", ASCENDING)])  # For cleanup
    
    # TTL index - auto-delete old messages after 30 days (optional)
    # Uncomment if you want automatic cleanup:
    # history_collection.create_index(
    #     [("timestamp", ASCENDING)],
    #     expireAfterSeconds=2592000  # 30 days
    # )
    
    logger.info("✅ Database indexes created successfully!")


def save_user(chat_collection, update):
    """Save or update user/chat information with optimized upsert."""
    try:
        chat = update.effective_chat
        user = update.effective_user
        
        data = {
            "chat_id": chat.id,
            "type": chat.type,
            "last_active": datetime.datetime.utcnow(),
        }
        
        if user:
            data["username"] = user.username
            data["first_name"] = user.first_name

        # Upsert is atomic and fast with proper index
        chat_collection.update_one(
            {"chat_id": chat.id},
            {"$set": data},
            upsert=True
        )
        
    except Exception as exc:
        logger.error("❌ DB Error in save_user: %s", exc)


def get_chat_history(history_collection, chat_id):
    """
    Retrieve chat history with optimized query.
    Uses compound index for lightning-fast lookups.
    """
    try:
        # This query uses the compound index (chat_id, timestamp)
        history_docs = (
            history_collection
            .find(
                {"chat_id": chat_id},
                {"_id": 0, "role": 1, "content": 1}  # Project only needed fields
            )
            .sort("timestamp", DESCENDING)
            .limit(HISTORY_LIMIT)
        )

        # Reverse to get chronological order
        history = []
        for doc in reversed(list(history_docs)):
            history.append((doc["role"], doc["content"]))
        
        return history
        
    except Exception as exc:
        logger.error("❌ Error retrieving history: %s", exc)
        return []


def save_chat_history(history_collection, chat_id, role, content):
    """
    Save chat history with optimized cleanup strategy.
    Cleanup runs in background to avoid blocking.
    """
    try:
        # Insert new message
        history_collection.insert_one({
            "chat_id": chat_id,
            "role": role,
            "content": content,
            "timestamp": datetime.datetime.utcnow(),
        })
        
        # Note: Cleanup moved to background task (see background_tasks.py)
        # This keeps the main path fast
        
    except Exception as exc:
        logger.error("❌ Error saving history: %s", exc)


def cleanup_old_history(history_collection, chat_id):
    """
    Cleanup old history for a specific chat.
    Should be called by background worker, not in main request path.
    """
    try:
        count = history_collection.count_documents({"chat_id": chat_id})
        
        if count > MAX_HISTORY_STORED:
            # Get IDs of oldest messages
            oldest = (
                history_collection
                .find({"chat_id": chat_id}, {"_id": 1})
                .sort("timestamp", ASCENDING)
                .limit(count - MAX_HISTORY_STORED)
            )
            
            ids_to_delete = [doc["_id"] for doc in oldest]
            
            if ids_to_delete:
                result = history_collection.delete_many({"_id": {"$in": ids_to_delete}})
                logger.debug("🧹 Cleaned %d old messages for chat %s", 
                           result.deleted_count, chat_id)
                
    except Exception as exc:
        logger.error("❌ Error cleaning history: %s", exc)


def get_all_chat_ids(chat_collection, batch_size=100):
    """
    Generator that yields chat IDs in batches for efficient broadcasting.
    Uses cursor with no timeout for large datasets.
    """
    try:
        cursor = chat_collection.find(
            {},
            {"chat_id": 1, "_id": 0},
            no_cursor_timeout=True
        )
        
        batch = []
        for doc in cursor:
            batch.append(doc["chat_id"])
            if len(batch) >= batch_size:
                yield batch
                batch = []
        
        if batch:  # Yield remaining
            yield batch
            
        cursor.close()
        
    except Exception as exc:
        logger.error("❌ Error fetching chat IDs: %s", exc)
        yield []


def get_stats(chat_collection, history_collection):
    """Get bot statistics with optimized aggregation."""
    try:
        # Use indexes for fast counting
        user_count = chat_collection.count_documents({"type": "private"})
        group_count = chat_collection.count_documents({"type": {"$ne": "private"}})
        
        # For very large collections, use estimated count
        total_messages = history_collection.estimated_document_count()
        
        return {
            "users": user_count,
            "groups": group_count,
            "messages": total_messages
        }
    except Exception as exc:
        logger.error("❌ Error fetching stats: %s", exc)
        return {"users": 0, "groups": 0, "messages": 0}
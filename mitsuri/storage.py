import datetime
import logging

import certifi
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from mitsuri.config import MONGO_URI

logger = logging.getLogger(__name__)

def create_mongo_client():
    try:
        mongo_client = MongoClient(
            MONGO_URI,
            serverSelectionTimeoutMS=5000,
            tls=True,
            tlsCAFile=certifi.where(),
        )
        mongo_client.admin.command("ping")
        logger.info("✅ MongoDB connected successfully!")
        return mongo_client
    except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
        logger.critical("❌ MongoDB connection failed: %s", exc)
        raise


def save_user(chat_collection, update):
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

        chat_collection.update_one({"chat_id": chat.id}, {"$set": data}, upsert=True)
        logger.debug("📝 User saved/updated: %s", chat.id)
    except Exception as exc:
        logger.error("❌ DB Error in save_user: %s", exc)


def get_chat_history(history_collection, chat_id, limit=6):
    try:
        history_docs = (
            history_collection.find({"chat_id": chat_id})
            .sort("timestamp", -1)
            .limit(limit)
        )

        history = []
        for doc in reversed(list(history_docs)):
            history.append((doc["role"], doc["content"]))
        return history
    except Exception as exc:
        logger.error("❌ Error retrieving history: %s", exc)
        return []


def save_chat_history(history_collection, chat_id, role, content):
    try:
        history_collection.insert_one(
            {
                "chat_id": chat_id,
                "role": role,
                "content": content,
                "timestamp": datetime.datetime.utcnow(),
            }
        )

        count = history_collection.count_documents({"chat_id": chat_id})
        if count > 20:
            oldest = (
                history_collection.find({"chat_id": chat_id})
                .sort("timestamp", 1)
                .limit(count - 20)
            )

            ids_to_delete = [doc["_id"] for doc in oldest]
            history_collection.delete_many({"_id": {"$in": ids_to_delete}})
    except Exception as exc:
        logger.error("❌ Error saving history: %s", exc)

"""In-memory caching without Redis - 100% FREE!"""
import hashlib
import logging
import time
from collections import defaultdict, deque
from typing import Optional

from mitsuri.config import (
    RATE_LIMIT_WINDOW,
    RATE_LIMIT_MAX,
    CACHE_TTL_SECONDS,
)

logger = logging.getLogger(__name__)


class InMemoryCache:
    """
    100% FREE caching solution using Python data structures.
    No Redis needed! Works great for single-instance deployments.
    """
    
    def __init__(self):
        # Rate limiting: user_id -> list of timestamps
        self.rate_limits = defaultdict(list)
        
        # Response cache: key -> (response, expiry_time)
        self.response_cache = {}
        
        # Common responses cache
        self.common_cache = {}
        
        # Group cooldowns: chat_id -> last_message_time
        self.group_cooldowns = {}
        
        # Broadcast tracking
        self.broadcasts = {}
        
        # Cleanup tracking
        self.last_cleanup = time.time()
        
        logger.info("✅ In-memory cache initialized (FREE mode)")
    
    async def initialize(self):
        """Compatibility method - no async init needed for in-memory."""
        logger.info("💾 Running in FREE mode (in-memory cache)")
        logger.info("💡 Tip: Add Redis for persistent caching (optional)")
    
    async def close(self):
        """Cleanup - nothing to close for in-memory."""
        pass
    
    # ==================== Rate Limiting ====================
    
    async def check_rate_limit(self, user_id: int) -> bool:
        """
        Check if user is within rate limit.
        Returns True if allowed, False if rate limited.
        """
        now = time.time()
        timestamps = self.rate_limits[user_id]
        
        # Remove old timestamps outside the window
        timestamps[:] = [ts for ts in timestamps if now - ts < RATE_LIMIT_WINDOW]
        
        if len(timestamps) >= RATE_LIMIT_MAX:
            return False
        
        # Add current timestamp
        timestamps.append(now)
        
        # Periodic cleanup to prevent memory bloat
        await self._cleanup_if_needed()
        
        return True
    
    async def get_rate_limit_status(self, user_id: int) -> dict:
        """Get current rate limit status for user."""
        now = time.time()
        timestamps = self.rate_limits[user_id]
        
        # Clean old timestamps
        timestamps[:] = [ts for ts in timestamps if now - ts < RATE_LIMIT_WINDOW]
        
        return {
            "requests": len(timestamps),
            "limit": RATE_LIMIT_MAX,
            "window": RATE_LIMIT_WINDOW,
            "resets_in": RATE_LIMIT_WINDOW
        }
    
    # ==================== Group Cooldown ====================
    
    async def check_group_cooldown(self, chat_id: int, cooldown_seconds: int) -> bool:
        """
        Check if group is in cooldown period.
        Returns True if message should be processed, False if in cooldown.
        """
        now = time.time()
        last_time = self.group_cooldowns.get(chat_id, 0)
        
        if now - last_time < cooldown_seconds:
            return False  # Still in cooldown
        
        # Set new cooldown
        self.group_cooldowns[chat_id] = now
        return True
    
    # ==================== Response Caching ====================
    
    def _generate_cache_key(self, chat_id: int, message: str) -> str:
        """Generate cache key for message."""
        msg_hash = hashlib.md5(message.lower().strip().encode()).hexdigest()[:16]
        return f"response:{chat_id}:{msg_hash}"
    
    async def get_cached_response(self, chat_id: int, message: str) -> Optional[str]:
        """Get cached AI response if available."""
        key = self._generate_cache_key(chat_id, message)
        
        if key in self.response_cache:
            response, expiry = self.response_cache[key]
            
            if time.time() < expiry:
                logger.info("💾 Cache HIT for chat %s", chat_id)
                return response
            else:
                # Expired, remove it
                del self.response_cache[key]
        
        return None
    
    async def cache_response(self, chat_id: int, message: str, response: str):
        """Cache AI response for future use."""
        key = self._generate_cache_key(chat_id, message)
        expiry = time.time() + CACHE_TTL_SECONDS
        self.response_cache[key] = (response, expiry)
        logger.debug("💾 Cached response for chat %s", chat_id)
    
    # ==================== Common Responses Cache ====================
    
    async def get_common_response(self, message: str) -> Optional[str]:
        """Get cached response for common queries."""
        normalized = message.lower().strip()
        key = hashlib.md5(normalized.encode()).hexdigest()[:16]
        
        if key in self.common_cache:
            response, expiry = self.common_cache[key]
            
            if time.time() < expiry:
                logger.info("💾 Common response cache HIT")
                return response
            else:
                del self.common_cache[key]
        
        return None
    
    async def cache_common_response(self, message: str, response: str):
        """Cache common responses with longer TTL."""
        normalized = message.lower().strip()
        key = hashlib.md5(normalized.encode()).hexdigest()[:16]
        
        # Common responses cached for 24 hours
        expiry = time.time() + 86400
        self.common_cache[key] = (response, expiry)
        logger.debug("💾 Cached common response")
    
    # ==================== Broadcast State ====================
    
    async def start_broadcast(self, broadcast_id: str, total_users: int):
        """Initialize broadcast tracking."""
        self.broadcasts[broadcast_id] = {
            "total": total_users,
            "sent": 0,
            "failed": 0,
            "started": int(time.time())
        }
    
    async def update_broadcast_stats(self, broadcast_id: str, sent: int = 0, failed: int = 0):
        """Update broadcast progress."""
        if broadcast_id in self.broadcasts:
            self.broadcasts[broadcast_id]["sent"] += sent
            self.broadcasts[broadcast_id]["failed"] += failed
    
    async def get_broadcast_stats(self, broadcast_id: str) -> dict:
        """Get broadcast statistics."""
        return self.broadcasts.get(broadcast_id, {})
    
    # ==================== Cleanup ====================
    
    async def _cleanup_if_needed(self):
        """
        Periodic cleanup to prevent memory bloat.
        Runs every 5 minutes to clean expired entries.
        """
        now = time.time()
        
        # Only cleanup every 5 minutes
        if now - self.last_cleanup < 300:
            return
        
        self.last_cleanup = now
        
        # Clean expired cache entries
        expired_keys = [
            key for key, (_, expiry) in self.response_cache.items()
            if now > expiry
        ]
        for key in expired_keys:
            del self.response_cache[key]
        
        # Clean expired common cache
        expired_common = [
            key for key, (_, expiry) in self.common_cache.items()
            if now > expiry
        ]
        for key in expired_common:
            del self.common_cache[key]
        
        # Clean old broadcast data (older than 1 hour)
        old_broadcasts = [
            bid for bid, data in self.broadcasts.items()
            if now - data["started"] > 3600
        ]
        for bid in old_broadcasts:
            del self.broadcasts[bid]
        
        # Clean old group cooldowns (older than 1 hour)
        old_cooldowns = [
            chat_id for chat_id, last_time in self.group_cooldowns.items()
            if now - last_time > 3600
        ]
        for chat_id in old_cooldowns:
            del self.group_cooldowns[chat_id]
        
        # Clean rate limit data for inactive users (older than window)
        inactive_users = [
            user_id for user_id, timestamps in self.rate_limits.items()
            if not timestamps or now - timestamps[-1] > RATE_LIMIT_WINDOW * 2
        ]
        for user_id in inactive_users:
            del self.rate_limits[user_id]
        
        if expired_keys or expired_common or old_broadcasts:
            logger.info(
                "🧹 Cache cleanup: removed %d cached responses, %d common, %d broadcasts",
                len(expired_keys), len(expired_common), len(old_broadcasts)
            )


# Global cache instance (works without Redis!)
cache = InMemoryCache()
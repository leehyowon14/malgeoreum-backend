import math
import time

from limits import RateLimitItemPerSecond
from limits.errors import StorageError
from limits.storage import MemoryStorage, RedisStorage
from limits.strategies import FixedWindowRateLimiter

from app.config import Settings
from app.errors import IssueError


class IssueRateLimiter:
    def __init__(self, settings: Settings):
        if settings.redis_url:
            storage = RedisStorage(
                settings.redis_url,
                wrap_exceptions=True,
                socket_connect_timeout=1,
                socket_timeout=1,
            )
        else:
            storage = MemoryStorage()
        self.strategy = FixedWindowRateLimiter(storage)
        self.limit = RateLimitItemPerSecond(
            settings.issue_limit, settings.issue_window_seconds, namespace="malgeoreum-issue-v1"
        )

    def consume(self, user_key: str) -> None:
        try:
            if self.strategy.hit(self.limit, user_key):
                return
            window = self.strategy.get_window_stats(self.limit, user_key)
        except StorageError:
            raise IssueError("temporarily_unavailable") from None
        retry_after = max(1, math.ceil(window.reset_time - time.time()))
        raise IssueError("rate_limited", retry_after=retry_after)

    def close(self) -> None:
        storage = self.strategy.storage
        if isinstance(storage, RedisStorage):
            storage.get_connection().close()

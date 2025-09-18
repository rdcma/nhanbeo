from typing import Optional

try:
    import redis  # type: ignore
except Exception:  # pragma: no cover
    redis = None  # type: ignore

from .config import get_redis_url


class CounterStore:
    def __init__(self) -> None:
        url = get_redis_url()
        self.client = None
        if url and redis is not None:
            try:
                self.client = redis.Redis.from_url(url, decode_responses=True)
            except Exception:
                self.client = None

    def increase_and_get(self, key: str, ttl_seconds: int = 900) -> int:
        if self.client is None:
            # fallback in-memory counter per process (dev only)
            return _InMemoryCounter.increase_and_get(key, ttl_seconds)
        pipe = self.client.pipeline()
        pipe.incr(key)
        pipe.expire(key, ttl_seconds)
        count, _ = pipe.execute()
        return int(count)

    def get_current(self, key: str) -> int:
        if self.client is None:
            return _InMemoryCounter.get_current(key)
        try:
            val = self.client.get(key)
            return int(val) if val is not None else 0
        except Exception:
            return 0

    def reset(self, key: str) -> None:
        if self.client is None:
            _InMemoryCounter.reset(key)
            return
        try:
            # Delete key and ensure value is removed entirely
            self.client.delete(key)
        except Exception:
            pass


class _InMemoryCounter:
    _store: dict = {}

    @classmethod
    def increase_and_get(cls, key: str, ttl_seconds: int) -> int:
        # naive counter without TTL eviction; good enough for dev
        val = cls._store.get(key, 0) + 1
        cls._store[key] = val
        return val

    @classmethod
    def reset(cls, key: str) -> None:
        if key in cls._store:
            del cls._store[key]

    @classmethod
    def get_current(cls, key: str) -> int:
        return int(cls._store.get(key, 0))



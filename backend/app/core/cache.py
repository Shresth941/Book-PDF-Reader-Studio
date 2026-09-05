from collections import OrderedDict
from threading import RLock
from time import monotonic
from typing import Generic, Hashable, TypeVar


Key = TypeVar("Key", bound=Hashable)
Value = TypeVar("Value")


class TimedLruCache(Generic[Key, Value]):
    """Small in-memory cache for repeated local-reader requests."""

    def __init__(self, max_entries: int, ttl_seconds: float | None = None):
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._items: OrderedDict[Key, tuple[float, Value]] = OrderedDict()
        self._lock = RLock()

    def get(self, key: Key) -> Value | None:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            created_at, value = item
            if self.ttl_seconds is not None and monotonic() - created_at > self.ttl_seconds:
                del self._items[key]
                return None
            self._items.move_to_end(key)
            return value

    def set(self, key: Key, value: Value) -> None:
        with self._lock:
            self._items[key] = (monotonic(), value)
            self._items.move_to_end(key)
            while len(self._items) > self.max_entries:
                self._items.popitem(last=False)

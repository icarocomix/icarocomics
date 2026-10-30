"""Cache LRU limitata in byte (non in numero di voci): le pagine hanno pesi molto diversi."""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Hashable, Iterable
from typing import Any


class LruCache:
    def __init__(self, budget_bytes: int):
        self._budget = budget_bytes
        self._items: OrderedDict[Hashable, tuple[Any, int]] = OrderedDict()
        self._size = 0
        self._protected: frozenset = frozenset()
        # Il lock serve perché la cache dei byte compressi viene usata dai thread di prefetch.
        self._lock = threading.Lock()

    @property
    def size_bytes(self) -> int:
        return self._size

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, key: Hashable) -> bool:
        with self._lock:
            return key in self._items

    def get(self, key: Hashable) -> Any | None:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            self._items.move_to_end(key)
            return item[0]

    def put(self, key: Hashable, value: Any, cost: int) -> None:
        with self._lock:
            old = self._items.pop(key, None)
            if old is not None:
                self._size -= old[1]
            self._items[key] = (value, cost)
            self._size += cost
            self._evict_locked()

    def set_protected(self, keys: Iterable[Hashable]) -> None:
        # Le voci della finestra di lettura corrente (pagina visibile + vicine) non vengono
        # sacrificate anche se il budget viene superato temporaneamente.
        with self._lock:
            self._protected = frozenset(keys)
            self._evict_locked()

    def discard(self, key: Hashable) -> None:
        with self._lock:
            item = self._items.pop(key, None)
            if item is not None:
                self._size -= item[1]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._size = 0
            self._protected = frozenset()

    def _evict_locked(self) -> None:
        if self._size <= self._budget:
            return
        for key in list(self._items):
            if self._size <= self._budget:
                break
            if key in self._protected:
                continue
            _value, cost = self._items.pop(key)
            self._size -= cost

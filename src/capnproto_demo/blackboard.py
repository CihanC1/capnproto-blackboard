"""The shared in-memory blackboard (plain Python, no RPC code in here).

The server creates exactly ONE ``Blackboard`` and every client connection uses
it. That single shared object is what lets independent clients communicate
indirectly: one client writes, another reads.
"""

from __future__ import annotations

import threading


class Blackboard:
    """A thread-safe key/value store.

    asyncio runs the server in a single thread, so operations cannot interleave
    anyway; the lock makes each operation atomic even if the store is someday
    used from several threads, and documents the intent.
    """

    def __init__(self) -> None:
        self._entries: dict[str, str] = {}
        self._lock = threading.Lock()

    def put(self, key: str, value: str) -> bool:
        """Store ``value`` under ``key``. Return True if the key is new."""
        with self._lock:
            created = key not in self._entries
            self._entries[key] = value
            return created

    def get(self, key: str) -> str | None:
        """Return the value, or None if the key does not exist.

        An empty string is a legitimate value, so callers must compare
        against ``None`` and never test truthiness.
        """
        with self._lock:
            return self._entries.get(key)

    def remove(self, key: str) -> bool:
        """Delete ``key``. Return True if it existed."""
        with self._lock:
            return self._entries.pop(key, None) is not None

    def items(self) -> list[tuple[str, str]]:
        """Return a snapshot of all entries, sorted by key."""
        with self._lock:
            return sorted(self._entries.items())

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

"""
Per-task working memory.

WorkingMemory is a lightweight key-value store that lives for the
duration of a single agent task.  It is always cleared when the task
ends (or when ``clear()`` is called explicitly).

It does NOT persist to disk — if persistence is needed that is a
separate concern handled by a storage layer (to be built in a later
milestone).
"""

from __future__ import annotations

from typing import Any


class WorkingMemory:
    """In-memory key-value store scoped to a single agent task.

    All values are stored in a plain Python dictionary.  This class is
    intentionally simple — no TTLs, no serialisation, no concurrency
    locking.  Thread safety is not a requirement at this stage.

    Example usage::

        memory = WorkingMemory()
        memory.store("email_count", 5)
        print(memory.get("email_count"))   # 5
        memory.clear()
        print(memory.get("email_count"))   # None
    """

    def __init__(self) -> None:
        """Initialise an empty working memory store."""
        self._store: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def store(self, key: str, value: Any) -> None:
        """Save ``value`` under ``key``.

        Overwrites any existing value for the same key silently.

        Args:
            key:   String identifier for the value.  Must be non-empty.
            value: Any Python object to store.

        Raises:
            ValueError: If ``key`` is empty or not a string.
        """
        if not isinstance(key, str) or not key:
            raise ValueError(
                f"WorkingMemory key must be a non-empty string, got: {key!r}"
            )
        self._store[key] = value

    def get(self, key: str) -> Any:
        """Retrieve the value stored under ``key``.

        Args:
            key: The key to look up.

        Returns:
            The stored value, or ``None`` if the key does not exist.
        """
        return self._store.get(key)

    def clear(self) -> None:
        """Delete all entries from working memory.

        Called automatically by BaseAgent when a task completes or fails.
        Safe to call multiple times — clearing an empty store is a no-op.
        """
        self._store.clear()

    def get_all(self) -> dict[str, Any]:
        """Return a shallow copy of the entire memory store.

        Returns a copy so callers cannot accidentally mutate internal state.

        Returns:
            Dict mapping all current keys to their stored values.
        """
        return dict(self._store)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        """Return the number of stored entries."""
        return len(self._store)

    def __contains__(self, key: str) -> bool:
        """Support ``key in memory`` syntax."""
        return key in self._store

    def __repr__(self) -> str:
        return f"<WorkingMemory entries={len(self._store)}>"

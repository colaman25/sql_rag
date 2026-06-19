import os
from abc import ABC, abstractmethod


class SessionStore(ABC):
    @abstractmethod
    def get(self, session_id: str) -> dict | None:
        ...

    @abstractmethod
    def set(self, session_id: str, data: dict) -> None:
        ...


class InMemorySessionStore(SessionStore):
    def __init__(self):
        self._store = {}

    def get(self, session_id: str) -> dict | None:
        return self._store.get(session_id)

    def set(self, session_id: str, data: dict) -> None:
        self._store[session_id] = data


def get_session_store() -> SessionStore:
    backend = os.getenv("SESSION_STORE", "memory")
    if backend == "memory":
        return InMemorySessionStore()
    raise ValueError(f"Unknown SESSION_STORE backend: {backend!r}")

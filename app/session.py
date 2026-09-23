"""In-memory session store. Nothing here survives a restart, on purpose (see DECISIONS.md)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from src.catalog.loader import get_catalog
from src.state import WorkflowState


@dataclass
class Session:
    state: WorkflowState
    workflow: dict | None = None


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(self) -> Session:
        session_id = uuid.uuid4().hex
        session = Session(state=WorkflowState.new(session_id, get_catalog()))
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def save(self, session: Session) -> None:
        self._sessions[session.state.session_id] = session

    def delete(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None

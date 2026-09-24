"""WorkflowState: everything collected so far, and where each value came from."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from src.catalog.loader import Catalog


class Filled(BaseModel):
    """A value plus its provenance: the exact user text it was read from, and on which turn."""
    value: Any
    span: str
    turn: int


class SlotState(BaseModel):
    choice: Filled | None = None          # chosen node type, for 'choose' slots only
    params: dict[str, Filled] = Field(default_factory=dict)


class PendingAmbiguity(BaseModel):
    """Words that named more than one option for a requirement not being asked about yet.

    Like a value, it keeps the exact user text it came from and the turn it was said
    on, so it can be checked against the transcript before it narrows a question.
    """
    requirement: str
    span: str
    options: list[Any]
    turn: int


class Turn(BaseModel):
    role: Literal["user", "agent"]
    text: str


class WorkflowState(BaseModel):
    session_id: str
    slots: dict[str, SlotState]
    asked: dict[str, int] = Field(default_factory=dict)     # requirement id -> times asked
    last_asked: str | None = None
    transcript: list[Turn] = Field(default_factory=list)
    declined: list[str] = Field(default_factory=list)       # things the catalog cannot express
    rejections: list[dict] = Field(default_factory=list)    # grounding log, kept for inspection
    # requirement id -> narrowed options waiting for that requirement to be asked
    pending_ambiguities: dict[str, PendingAmbiguity] = Field(default_factory=dict)

    @classmethod
    def new(cls, session_id: str, catalog: Catalog) -> "WorkflowState":
        return cls(session_id=session_id, slots={s.id: SlotState() for s in catalog.slots})

    # -- reading -------------------------------------------------------------

    def node_type(self, slot_id: str, catalog: Catalog) -> str | None:
        """The node a slot holds: fixed by the catalog, or whatever the user chose."""
        fixed = catalog.slot(slot_id).node
        if fixed:
            return fixed
        choice = self.slots[slot_id].choice
        return choice.value if choice else None

    def get(self, req_id: str) -> Filled | None:
        slot_id, _, param = req_id.partition(".")
        slot = self.slots[slot_id]
        return slot.params.get(param) if param else slot.choice

    def value(self, req_id: str) -> Any:
        filled = self.get(req_id)
        return filled.value if filled else None

    @property
    def user_turn(self) -> int:
        """Index of the most recent user turn (0-based count of user messages)."""
        return sum(1 for t in self.transcript if t.role == "user") - 1

    def user_text(self, turn: int) -> str:
        return [t.text for t in self.transcript if t.role == "user"][turn]

    # -- writing -------------------------------------------------------------

    def set(self, req_id: str, value: Any, span: str, turn: int) -> None:
        slot_id, _, param = req_id.partition(".")
        slot = self.slots[slot_id]
        filled = Filled(value=value, span=span, turn=turn)
        if param:
            slot.params[param] = filled
        else:
            if slot.choice is None or slot.choice.value != value:
                # A different node type means different params; the old ones no longer apply.
                slot.params = {}
            slot.choice = filled

    def mark_asked(self, req_id: str) -> None:
        self.asked[req_id] = self.asked.get(req_id, 0) + 1
        self.last_asked = req_id

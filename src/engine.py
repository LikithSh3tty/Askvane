"""Completeness engine: what is missing, and which single question comes next.

No LLM anywhere in this module. Requirements are recomputed from the current
state on every call, so choosing Gmail creates a label requirement and switching
to Outlook replaces it; nothing is held as a fixed checklist.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from src.catalog.loader import Catalog, ParamSpec, get_catalog
from src.state import PendingAmbiguity, SlotState, WorkflowState

REPHRASE_AFTER = 2   # asked this many times without an answer -> come at it differently


@dataclass(frozen=True)
class Requirement:
    id: str                       # "trigger", "trigger.monitor_location", ...
    slot: str
    param: str | None             # None when the requirement is choosing the slot's node
    hint: str
    node: str | None = None       # node type the param belongs to
    options: tuple = ()           # allowed values, when the answer is a closed set
    required: bool = True
    rephrase: bool = False


def param_required(spec: ParamSpec, slot: SlotState) -> bool:
    if spec.required:
        return True
    if spec.required_if:
        return all(
            slot.params.get(other) is not None and slot.params[other].value in values
            for other, values in spec.required_if.items()
        )
    return False


def requirements(state: WorkflowState, catalog: Catalog | None = None,
                 include_optional: bool = False, include_filled: bool = False) -> list[Requirement]:
    """Unset requirements in question order: slot order, then declaration order.

    `include_optional` adds optional params of chosen nodes (accepted if volunteered).
    `include_filled` adds requirements that already hold a value, so a correction
    ("actually, use email instead") can overwrite them.
    """
    catalog = catalog or get_catalog()
    out: list[Requirement] = []
    for slot_spec in catalog.slots:
        slot = state.slots[slot_spec.id]
        node_type = state.node_type(slot_spec.id, catalog)
        if node_type is None or (include_filled and slot_spec.choose):
            out.append(Requirement(
                id=slot_spec.id, slot=slot_spec.id, param=None,
                hint=slot_spec.prompt_hint or slot_spec.display,
                options=tuple(catalog.choices(slot_spec.id)),
            ))
            if node_type is None:
                continue   # this slot's params do not exist until its node is chosen
        for name, spec in catalog.nodes[node_type].params.items():
            if name in slot.params and not include_filled:
                continue
            required = param_required(spec, slot)
            # Optional params are never asked about, only accepted if volunteered.
            # A conditional param whose condition is unmet does not exist yet.
            if not required and (not include_optional or spec.required_if):
                continue
            out.append(Requirement(
                id=f"{slot_spec.id}.{name}", slot=slot_spec.id, param=name, node=node_type,
                hint=spec.prompt_hint or name.replace("_", " "),
                options=tuple(spec.enum or ()), required=required,
            ))
    return out


def open_requirements(state: WorkflowState, catalog: Catalog | None = None) -> list[Requirement]:
    return requirements(state, catalog)


def is_complete(state: WorkflowState, catalog: Catalog | None = None) -> bool:
    catalog = catalog or get_catalog()
    kinds = {catalog.nodes[t].kind for s in catalog.slots
             if (t := state.node_type(s.id, catalog)) is not None}
    return not open_requirements(state, catalog) and {"trigger", "action"} <= kinds


def next_question(state: WorkflowState, catalog: Catalog | None = None) -> Requirement | None:
    """Exactly one requirement, never a list. Flagged for rephrasing if asking again would repeat."""
    pending = open_requirements(state, catalog)
    if not pending:
        return None
    req = pending[0]
    return replace(req, rephrase=state.asked.get(req.id, 0) >= REPHRASE_AFTER)


def pending_for(state: WorkflowState, req: Requirement) -> PendingAmbiguity | None:
    """The narrowing parked for the requirement about to be asked, checked before open wording."""
    return state.pending_ambiguities.get(req.id)


def settle_pending(state: WorkflowState, catalog: Catalog | None = None) -> list[str]:
    """Drop parked ambiguities whose requirement is answered or no longer exists.

    An answer clears its narrowing whether or not it was one of the candidates;
    a node change that removes the requirement takes its narrowing with it.
    Stale narrowing is worse than none.
    """
    live = {r.id for r in open_requirements(state, catalog)}
    stale = [req_id for req_id in state.pending_ambiguities if req_id not in live]
    for req_id in stale:
        del state.pending_ambiguities[req_id]
    return stale

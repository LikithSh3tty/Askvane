"""State -> workflow JSON and the collected-information table. No LLM.

The workflow is laid out in the order data moves: trigger, the label/folder
filter it implies, duplicate handling, then (only if the user asked for a
condition) an extract step and the condition, whose `yes` branch reaches the
action and whose `no` branch ends.
"""
from __future__ import annotations

import re
from typing import Any

from src.catalog.loader import Catalog, get_catalog
from src.engine import open_requirements, param_required
from src.state import WorkflowState

NONE = "–"
OPERATOR_SYMBOL = {"gt": ">", "lt": "<", "eq": "=", "contains": "contains"}
CURRENCY = re.compile(r"[₹$€£]")
LOCATION_PARAM = "monitor_location"
# The brief's wording for a values row whose node declares no label of its own.
PARAMS_ROW = {"trigger": "Monitor Location", "action": "Channel / Recipient"}


def _resolved_params(state: WorkflowState, slot_id: str, catalog: Catalog,
                     skip: tuple = ()) -> dict[str, Any]:
    node_type = state.node_type(slot_id, catalog)
    slot = state.slots[slot_id]
    out: dict[str, Any] = {}
    for name, spec in catalog.nodes[node_type].params.items():
        if name in skip:
            continue
        if name in slot.params:
            out[name] = slot.params[name].value
        elif spec.default is not None and (not spec.required_if or param_required(spec, slot)):
            out[name] = spec.default
    return out


def _location_label(state: WorkflowState, catalog: Catalog) -> str:
    spec = catalog.nodes[state.node_type("trigger", catalog)].params[LOCATION_PARAM]
    kind = (spec.display_suffix or "(Label)").strip("()")
    return f"Filter by {kind}"


def generate(state: WorkflowState, catalog: Catalog | None = None) -> dict:
    catalog = catalog or get_catalog()
    nodes: list[dict] = []
    edges: list[dict] = []

    def add(node_type: str, label: str, params: dict, after: str | None, branch: str | None = None) -> str:
        node_id = f"n{len(nodes) + 1}"
        nodes.append({"id": node_id, "type": node_type, "label": label, "params": params})
        if after:
            edge = {"from": after, "to": node_id}
            if branch:
                edge["branch"] = branch
            edges.append(edge)
        return node_id

    trigger_type = state.node_type("trigger", catalog)
    trigger_params = _resolved_params(state, "trigger", catalog, skip=(LOCATION_PARAM,))
    last = add(trigger_type, catalog.nodes[trigger_type].label, trigger_params, None)

    location = state.value(f"trigger.{LOCATION_PARAM}")
    if location is not None:
        last = add("filter_by_label", _location_label(state, catalog), {"label": location}, last)

    if state.value("dedupe.enabled") is True:
        last = add("deduplicate", catalog.nodes["deduplicate"].label, {"enabled": True}, last)

    action_type = state.node_type("action", catalog)
    action_params = _resolved_params(state, "action", catalog)
    if state.value("filter.mode") == "filtered":
        field = state.value("filter.field")
        last = add("extract_fields", catalog.nodes["extract_fields"].label, {"fields": [field]}, last)
        cond = add("condition", catalog.nodes["condition"].label,
                   _resolved_params(state, "filter", catalog, skip=("mode",)), last)
        add(action_type, catalog.nodes[action_type].label, action_params, cond, branch="yes")
        nodes.append({"id": "end", "type": "end", "label": "End", "params": {}})
        edges.append({"from": cond, "to": "end", "branch": "no"})
    else:
        add(action_type, catalog.nodes[action_type].label, action_params, last)

    return {"nodes": nodes, "edges": edges}


# -- the collected-information table ---------------------------------------

def _format_value(filled, spec=None) -> str:
    value = filled.value
    if spec is not None and spec.enum and isinstance(value, str):
        return value.replace("_", " ")   # payment_failed -> payment failed
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        symbol = CURRENCY.search(filled.span)
        text = f"{value:,}" if isinstance(value, int) else f"{value:,.2f}"
        return f"{symbol.group(0) if symbol else ''}{text}"
    return str(value)


def _params_text(state: WorkflowState, slot_id: str, catalog: Catalog, required_only: bool) -> str:
    node_type = state.node_type(slot_id, catalog)
    if node_type is None:
        return NONE
    slot = state.slots[slot_id]
    parts = []
    for name, spec in catalog.nodes[node_type].params.items():
        if name in slot.params and (param_required(spec, slot) or not required_only):
            text = _format_value(slot.params[name], spec)
            parts.append(f"{text} {spec.display_suffix}" if spec.display_suffix else text)
    return ", ".join(parts) or NONE


def _condition_text(state: WorkflowState) -> str:
    mode = state.value("filter.mode")
    if mode is None:
        return NONE
    if mode == "all":
        return "Every item"
    field = state.value("filter.field")
    op = state.value("filter.operator")
    value = state.get("filter.value")
    parts = [str(field).capitalize() if field else "?", OPERATOR_SYMBOL.get(op, "?"),
             _format_value(value) if value else "?"]
    return " ".join(parts)


def _preferences_text(state: WorkflowState, catalog: Catalog) -> str:
    prefs = []
    for slot_spec in catalog.slots:
        node_type = state.node_type(slot_spec.id, catalog)
        if node_type is None:
            continue
        slot = state.slots[slot_spec.id]
        for name, spec in catalog.nodes[node_type].params.items():
            if name in slot.params and not param_required(spec, slot):
                prefs.append(f"{name.replace('_', ' ')}: {_format_value(slot.params[name], spec)}")
    return "; ".join(prefs) or NONE


def _node_row(state: WorkflowState, slot_id: str, catalog: Catalog) -> str:
    """Label of the row naming a slot's node: the node's own, else the slot's."""
    node_type = state.node_type(slot_id, catalog)
    label = catalog.nodes[node_type].state_row if node_type else None
    return label or catalog.slot(slot_id).display


def _params_row(state: WorkflowState, slot_id: str, catalog: Catalog) -> str:
    """Label of the row carrying a node's required values, from the params that declare one."""
    node_type = state.node_type(slot_id, catalog)
    labels: list[str] = []
    if node_type:
        slot = state.slots[slot_id]
        for spec in catalog.nodes[node_type].params.values():
            if spec.state_row and param_required(spec, slot) and spec.state_row not in labels:
                labels.append(spec.state_row)
    return " / ".join(labels) or PARAMS_ROW[slot_id]


def state_table(state: WorkflowState, catalog: Catalog | None = None) -> list[dict]:
    catalog = catalog or get_catalog()

    def display(slot_id: str) -> str:
        node_type = state.node_type(slot_id, catalog)
        return (catalog.nodes[node_type].display or catalog.nodes[node_type].label) if node_type else NONE

    dedupe = state.get("dedupe.enabled")
    remaining = len(open_requirements(state, catalog))
    # Labels come from the catalog; the order is the reference table's.
    rows = [
        (_node_row(state, "trigger", catalog), display("trigger")),
        (_params_row(state, "trigger", catalog), _params_text(state, "trigger", catalog, required_only=True)),
        (_node_row(state, "filter", catalog), _condition_text(state)),
        (_node_row(state, "action", catalog), display("action")),
        (_params_row(state, "action", catalog), _params_text(state, "action", catalog, required_only=True)),
        (_node_row(state, "dedupe", catalog), _format_value(dedupe) if dedupe else NONE),
        ("Additional Preferences", _preferences_text(state, catalog)),
        ("Status", "All information collected" if remaining == 0
         else f"{remaining} item{'s' if remaining != 1 else ''} still needed"),
    ]
    return [{"parameter": p, "value": v} for p, v in rows]

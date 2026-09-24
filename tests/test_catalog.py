import textwrap

import pytest

from src.catalog.loader import CatalogError, load_catalog

REQUIRED_NODES = {
    "gmail_trigger", "outlook_trigger", "webhook_trigger", "schedule_trigger",
    "slack_notify", "email_send", "extract_fields", "http_request",
    "filter_by_label", "condition", "deduplicate",
}


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


def write(tmp_path, body: str):
    path = tmp_path / "nodes.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


VALID_SLOTS = """
slots:
  - { id: trigger, choose: trigger, display: Trigger, prompt_hint: which trigger }
nodes:
  t:
    kind: trigger
    label: T
    params: { x: { required: true } }
"""


def test_catalog_loads(catalog):
    assert REQUIRED_NODES <= set(catalog.nodes)
    assert [s.id for s in catalog.slots] == ["trigger", "filter", "action", "dedupe"]


def test_every_node_declares_a_param(catalog):
    for name, node in catalog.nodes.items():
        assert node.params, name


def test_every_enum_default_is_in_its_enum(catalog):
    for node in catalog.nodes.values():
        for param in node.params.values():
            if param.enum is not None and param.default is not None:
                assert param.default in param.enum


def test_conditional_requirements_exist(catalog):
    # Requirements that only appear once another param takes a value.
    assert catalog.nodes["condition"].params["value"].required_if == {"mode": ["filtered"]}
    assert catalog.nodes["schedule_trigger"].params["day_of_week"].required_if == {"frequency": ["weekly"]}


def test_minimal_valid_catalog_loads(tmp_path):
    assert load_catalog(write(tmp_path, VALID_SLOTS)).choices("trigger") == ["t"]


@pytest.mark.parametrize("body", [
    # unknown key on a param
    VALID_SLOTS.replace("required: true", "required: true, colour: red"),
    # node without a kind
    VALID_SLOTS.replace("kind: trigger\n", ""),
    # enum default outside its own enum
    VALID_SLOTS.replace("{ required: true }", "{ enum: [a, b], default: c }"),
    # node with no params
    VALID_SLOTS.replace("params: { x: { required: true } }", "params: {}"),
    # required_if pointing at a param that does not exist
    VALID_SLOTS.replace("{ required: true }", "{ required_if: { ghost: [1] } }"),
    # slot naming a node that does not exist
    VALID_SLOTS.replace("choose: trigger, display", "node: ghost, display"),
    # not YAML at all
    "slots: [unclosed",
    # a state-table row label that is blank, or not a string
    VALID_SLOTS.replace("label: T\n", "label: T\n    state_row: \"  \"\n"),
    VALID_SLOTS.replace("{ required: true }", "{ required: true, state_row: [a, b] }"),
], ids=["unknown-key", "missing-kind", "default-outside-enum", "no-params",
        "required-if-unknown", "slot-unknown-node", "bad-yaml", "blank-state-row", "state-row-not-text"])
def test_malformed_catalog_raises(tmp_path, body):
    with pytest.raises(CatalogError):
        load_catalog(write(tmp_path, body))


def test_state_row_is_optional_and_read_when_given(tmp_path):
    body = VALID_SLOTS.replace("label: T\n", "label: T\n    state_row: Source\n") \
                      .replace("{ required: true }", "{ required: true, state_row: Thing }")
    node = load_catalog(write(tmp_path, body)).nodes["t"]
    assert node.state_row == "Source" and node.params["x"].state_row == "Thing"
    assert load_catalog(write(tmp_path, VALID_SLOTS)).nodes["t"].state_row is None

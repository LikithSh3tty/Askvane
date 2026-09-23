"""Parse and validate nodes.yaml. A malformed catalog fails at startup, not mid-conversation."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

CATALOG_PATH = Path(__file__).with_name("nodes.yaml")


class CatalogError(ValueError):
    """Raised when nodes.yaml does not describe a valid catalog."""


class _Strict(BaseModel):
    # extra="forbid" is what turns a typo in the YAML into a startup error.
    model_config = ConfigDict(extra="forbid", frozen=True)


class ParamSpec(_Strict):
    required: bool = False
    required_if: dict[str, list[Any]] | None = None
    enum: list[Any] | None = None
    aliases: dict[Any, list[str]] | None = None
    default: Any = None
    prompt_hint: str | None = None
    display_suffix: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "ParamSpec":
        if self.required and self.required_if:
            raise ValueError("a param cannot be both required and required_if")
        if self.enum is not None:
            if self.default is not None and self.default not in self.enum:
                raise ValueError(f"default {self.default!r} is not in enum {self.enum}")
            for member in self.aliases or {}:
                if member not in self.enum:
                    raise ValueError(f"alias key {member!r} is not in enum {self.enum}")
        elif self.aliases:
            raise ValueError("aliases need an enum to map onto")
        return self


class NodeSpec(_Strict):
    kind: Literal["trigger", "action", "logic"]
    label: str
    display: str | None = None
    aliases: list[str] = Field(default_factory=list)
    selectable: bool = True
    params: dict[str, ParamSpec]

    @model_validator(mode="after")
    def _check(self) -> "NodeSpec":
        if not self.params:
            raise ValueError("a node must declare at least one param")
        for name, spec in self.params.items():
            for other, values in (spec.required_if or {}).items():
                target = self.params.get(other)
                if target is None:
                    raise ValueError(f"{name}.required_if names unknown param {other!r}")
                if target.enum is not None and any(v not in target.enum for v in values):
                    raise ValueError(f"{name}.required_if uses values outside {other}'s enum")
        return self


class SlotSpec(_Strict):
    id: str
    display: str
    choose: Literal["trigger", "action"] | None = None
    node: str | None = None
    prompt_hint: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "SlotSpec":
        if (self.choose is None) == (self.node is None):
            raise ValueError(f"slot {self.id!r} needs exactly one of 'choose' or 'node'")
        if self.choose and not self.prompt_hint:
            raise ValueError(f"slot {self.id!r} chooses a node and needs a prompt_hint")
        return self


class Catalog(_Strict):
    slots: list[SlotSpec]
    nodes: dict[str, NodeSpec]

    @model_validator(mode="after")
    def _check(self) -> "Catalog":
        ids = [s.id for s in self.slots]
        if len(ids) != len(set(ids)):
            raise ValueError("slot ids must be unique")
        for slot in self.slots:
            if slot.node and slot.node not in self.nodes:
                raise ValueError(f"slot {slot.id!r} names unknown node {slot.node!r}")
            if slot.choose and not self.choices(slot.id):
                raise ValueError(f"slot {slot.id!r} has no selectable {slot.choose} nodes")
        return self

    def slot(self, slot_id: str) -> SlotSpec:
        return next(s for s in self.slots if s.id == slot_id)

    def choices(self, slot_id: str) -> list[str]:
        """Node types a 'choose' slot may hold, in declaration order."""
        kind = self.slot(slot_id).choose
        return [n for n, spec in self.nodes.items() if spec.kind == kind and spec.selectable]


def load_catalog(path: Path | str = CATALOG_PATH) -> Catalog:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return Catalog.model_validate(raw)
    except (yaml.YAMLError, ValueError, TypeError) as exc:
        raise CatalogError(f"invalid catalog {path}: {exc}") from exc


@lru_cache(maxsize=1)
def get_catalog() -> Catalog:
    return load_catalog()

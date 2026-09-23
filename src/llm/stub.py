"""Deterministic offline provider driven by a lookup table.

It plays the part of the model in tests and in the eval: for each scripted
utterance it returns what a model might extract, including deliberate
hallucinations the grounding guard has to catch. An input it has no entry for
raises StubMiss instead of inventing something plausible.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from src.llm.base import LLM, LLMError

RESPONSES_PATH = Path(__file__).with_name("stub_responses.yaml")


class StubMiss(LLMError):
    """The stub has no scripted answer for this input."""


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


class StubLLM(LLM):
    name = "stub"

    def __init__(self, path: Path | str = RESPONSES_PATH):
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        self.extractions = {normalize(k): v for k, v in data["extraction"].items()}
        self.questions = data["question"]
        self.ambiguity_template = data["ambiguity_template"]

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        payload = json.loads(user)
        task = schema.get("title")
        if task == "extraction":
            return self._extract(payload)
        if task == "question":
            return self._question(payload)
        raise StubMiss(f"stub does not handle task {task!r}")

    def _extract(self, payload: dict) -> dict:
        key = normalize(payload["utterance"])
        if key not in self.extractions:
            raise StubMiss(f"no scripted extraction for {payload['utterance']!r}")
        entry = self.extractions[key] or {}
        return {"extractions": list(entry.get("extractions", [])),
                "unsupported": list(entry.get("unsupported", []))}

    def _question(self, payload: dict) -> dict:
        ambiguity = payload.get("ambiguity")
        if ambiguity:
            options = ambiguity["options"]
            listed = ", ".join(options[:-1]) + " or " + options[-1]
            return {"question": self.ambiguity_template.format(span=ambiguity["span"], options=listed)}
        req = payload["requirement"]
        entry = self.questions.get(f"{req}:{payload.get('node')}") or self.questions.get(req)
        if entry is None:
            raise StubMiss(f"no scripted question for requirement {req!r}")
        return {"question": entry["rephrase" if payload.get("rephrase") else "ask"]}

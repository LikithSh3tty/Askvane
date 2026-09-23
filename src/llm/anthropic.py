"""Claude provider.

The build spec suggested prefilling the opening `{` of the reply. Current Claude
models reject assistant prefill with a 400, so JSON is enforced with structured
outputs (`output_config.format`) instead, which constrains decoding to the
schema and is a stronger guarantee than a prefill ever was.
"""
from __future__ import annotations

import json
import os

import anthropic

from src.llm.base import LLM, LLMError

DEFAULT_MODEL = "claude-opus-5"


class AnthropicLLM(LLM):
    name = "anthropic"

    def __init__(self, model: str | None = None):
        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        body = {k: v for k, v in schema.items() if k != "title"}
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
            # Reading slots out of one sentence is a small task; low effort keeps turns fast.
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": body}},
            # If a safety classifier declines, let the API retry on its default fallback.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise LLMError("model declined the request")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise LLMError(f"no text in response (stop_reason={response.stop_reason})")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"response was not JSON: {text[:200]!r}") from exc

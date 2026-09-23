"""The one interface every LLM call goes through, so any call can be replaced by the stub."""
from __future__ import annotations

import os
from abc import ABC, abstractmethod


class LLMError(RuntimeError):
    """The provider could not produce a usable JSON answer."""


class LLM(ABC):
    name: str

    @abstractmethod
    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        """Send a system prompt and a user message; return JSON matching `schema`.

        `schema["title"]` names the task ("extraction", "question") so the stub
        can dispatch on it; real providers strip it before sending.
        """


def make_llm(provider: str | None = None) -> LLM:
    """`stub`, `anthropic`, or None to pick from the environment."""
    provider = provider or os.environ.get("LLM_PROVIDER") or (
        "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "stub")
    if provider == "stub":
        from src.llm.stub import StubLLM
        return StubLLM()
    if provider == "anthropic":
        from src.llm.anthropic import AnthropicLLM
        return AnthropicLLM()
    raise ValueError(f"unknown LLM provider {provider!r}")

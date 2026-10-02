"""Agent base: shared LLM call and JSON repair helpers.

Every agent depends on this module. parse_json repairs dirty LLM output
(markdown fences, trailing commas, single quotes).
"""
from __future__ import annotations

import json
import re
from typing import Any

from domain.ports import LLMPort


async def call_llm(llm: LLMPort, prompt: str) -> str:
    """Call the LLM and return its raw text output."""
    return await llm.complete(prompt)


def parse_json(text: str) -> dict[str, Any]:
    """Parse JSON from possibly-dirty LLM output.

    Args:
        text: Raw LLM output that should contain a JSON object.

    Returns:
        The parsed dict, or {} on unrecoverable input.
    """
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    text = text.strip()

    for candidate in (text, _repair(text)):
        try:
            result = json.loads(candidate)
            return result if isinstance(result, dict) else {}
        except json.JSONDecodeError:
            continue
    return {}


def _repair(text: str) -> str:
    """Best-effort repair: strip trailing commas and normalize quotes."""
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text.replace("'", '"')

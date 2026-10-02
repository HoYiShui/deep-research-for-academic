"""Spike: verify DeepSeek Anthropic-compatible API + JSON-mode stability.

Run against the real endpoint with a key set:
    ANTHROPIC_API_KEY=... python scripts/spike_deepseek_json.py

Checks that judgment-extraction and policy-routing prompts come back as
parseable JSON, and that parse_json recovers from markdown fences / trailing
commas / single quotes. Prints raw + parsed output for each prompt.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from domain.research.agents.base import parse_json
from infrastructure.llm.deepseek import DeepSeekLLM

_JUDGMENT_PROMPT = (
    "You are a research planning assistant. Given a brief draft and the user's "
    "latest answer, judge which fields are still missing. Respond with JSON only:\n"
    '{"missing_fields": ["..."], "questions": ["..."], "brief_patch": {}, '
    '"assumptions": ["..."]}\n\n'
    'Brief draft: {}\nUser answer: "design a phishing detector for enterprise email"\n'
)

_ROUTING_PROMPT = (
    "You are a critic. Review a draft claim binding and output the issue_type, "
    "severity, and fillable flag. Respond with JSON only:\n"
    '{"issue_type": "missing_source", "severity": "critical", "fillable": true}\n\n'
    'Binding: {"statement_id": "st-0", "cited_evidence_ids": ["missing-e1"]}\n'
    'Known evidence ids: ["e1"]\n'
)


async def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Set ANTHROPIC_API_KEY to run against DeepSeek; skipping.")
        return
    llm = DeepSeekLLM()
    for name, prompt in [("judgment", _JUDGMENT_PROMPT), ("routing", _ROUTING_PROMPT)]:
        raw = await llm.complete(prompt)
        print(f"--- {name} raw ---\n{raw}\n")
        print(f"--- {name} parsed ---\n{parse_json(raw)}\n")


if __name__ == "__main__":
    asyncio.run(main())

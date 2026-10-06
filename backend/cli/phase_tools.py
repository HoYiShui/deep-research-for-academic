"""Isolated phase-debug I/O; never writes a Run ledger or borrows its lease.

The supplied snapshot is local input, not authority to modify its persisted Run.
Physical debug model calls are reported separately from committed budget_used.
"""

import asyncio
import json

from application.errors import AppError
from application.settings import Settings
from cli import output
from domain.ports import AdapterError
from infrastructure.llm.deepseek import DeepSeekLLM


class DebugTools:
    def __init__(self, state, *, fake, seed=None):
        self.state, self.fake, self.seed = state, fake, 0 if seed is None else seed
        self.config = state.run_metadata.config
        if self.config.versions.prompt_versions[state.phase] != "mono-v1":
            raise output.UsageError("State prompt version differs from configured worker")
        self.output_limit = min(16384, self.config.limits.tokens)
        self.usage = {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0}
        self.model = None
        if not fake:
            settings = Settings.load()
            versions = self.config.versions
            if self.config.source_policy.private_only or settings.llm_local:
                raise AppError(
                    "privacy_policy_conflict", "Local model debug adapter is not configured"
                )
            if state.source_selection.knowledge_base_ids:
                raise output.EnvError("Knowledge-base version authorization is not configured")
            if (versions.llm_provider, versions.llm_model, versions.llm_revision) != (
                "anthropic_compatible",
                settings.llm_model,
                settings.llm_revision,
            ):
                raise output.UsageError("State model version differs from configured adapter")
            if not settings.anthropic_api_key.get_secret_value():
                raise output.EnvError("Model API key is not configured")
            self.model = DeepSeekLLM(
                retries=0,
                api_key=settings.anthropic_api_key.get_secret_value(),
                base_url=settings.anthropic_base_url,
                model=settings.llm_model,
                timeout_s=self.config.timeouts_s.llm,
                max_tokens=self.output_limit,
            )

    async def invoke(self, operation, payload):
        if operation != "llm" or payload.get("phase") != self.state.phase:
            raise AppError("invalid_state", "Debug tool operation differs from requested phase")
        prompt = payload.get("prompt")
        if not isinstance(prompt, str):
            raise AppError("invalid_state", "Invalid debug model input")
        if self.fake:
            return self.fake_plan()
        # Conservative allowance, not measured use. Refuse another call before
        # its input-byte/output allowance could exceed this isolated debug cap.
        reserved = len(prompt.encode()) + 64 + self.output_limit
        if self.usage["llm_calls"] >= self.config.limits.llm_calls or (
            self.usage["input_tokens"] + self.usage["output_tokens"] + reserved
            > self.config.limits.tokens
        ):
            raise AppError("budget_exhausted", "Debug model budget is exhausted")
        self.usage["llm_calls"] += 1
        async with asyncio.timeout(self.config.timeouts_s.llm):
            response = await self.model.complete_metered(prompt)
        self.usage["input_tokens"] += response.input_tokens
        self.usage["output_tokens"] += response.output_tokens
        if self.usage["input_tokens"] + self.usage["output_tokens"] > self.config.limits.tokens:
            raise AppError("budget_exhausted", "Debug model response exceeded token budget")
        if response.stop_reason != "end_turn" or response.model != self.config.versions.llm_model:
            raise AdapterError(
                "llm",
                "model_output_invalid",
                "Model response was truncated or changed model",
                False,
                "plan",
            )
        return response.text

    def fake_plan(self):
        # Controlled schema fixture only. No invented sources or measured results.
        return json.dumps(
            {
                "section_plans": [
                    {
                        "section_id": f"section_{i}",
                        "title": f"Controlled section {i}",
                        "objective": "Verify phase dispatch and merge with controlled output",
                        "claim_specs": [
                            {
                                "spec_id": f"debug-{self.seed}-spec-{i}",
                                "text": "Controlled claim requiring future evidence",
                                "required_conditions": [],
                                "required_source_tiers": [],
                            }
                        ],
                        "sub_questions": ["Controlled retrieval query"],
                        "retrieval_anchors": [],
                        "evidence_requirements": [],
                        "analysis_requirements": [],
                    }
                    for i in range(1, 6)
                ]
            }
        )

    async def close(self):
        if self.model is not None:
            await self.model.aclose()

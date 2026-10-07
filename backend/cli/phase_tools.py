"""Isolated phase-debug I/O; never writes a Run ledger or borrows its lease.

The supplied snapshot is local input, not authority to modify its persisted Run.
Physical debug model calls are reported separately from committed budget_used.
"""

import asyncio
import json
from pathlib import Path

from application.errors import AppError
from application.settings import Settings
from cli import output
from cli.research_tools import ResearchDebugTools
from domain.ports import AdapterError
from infrastructure.llm.deepseek import DeepSeekLLM
from infrastructure.parser.html import HTML_PARSER_VERSION
from infrastructure.parser.mineru_output import MINERU_PARSER_VERSION


class DebugTools:
    def __init__(self, state, *, fake, seed=None):
        self.state, self.fake, self.seed = state, fake, 0 if seed is None else seed
        self.config = state.run_metadata.config
        if self.config.versions.prompt_versions[state.phase] != "mono-v1":
            raise output.UsageError("State prompt version differs from configured worker")
        self.output_limit = min(16384, self.config.limits.tokens)
        self.usage = {
            "llm_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "search_calls": 0,
            "fetch_calls": 0,
        }
        self.model = None
        self.research = None
        settings = Settings.load() if not fake else None
        if state.phase == "research":
            if self.config.source_policy.private_only or state.source_selection.knowledge_base_ids:
                raise output.EnvError("Knowledge-base research debug is not configured")
            if not fake and self.config.versions.parser_version not in {
                HTML_PARSER_VERSION,
                MINERU_PARSER_VERSION,
            }:
                raise output.EnvError(
                    "Research debug requires parser_version=dr4a-html-v1 or dr4a-mineru-4.0.10-standard-v1"
                )
            if (
                not fake
                and self.config.versions.parser_version == MINERU_PARSER_VERSION
                and (
                    not settings.mineru_models_dir
                    or not (Path(settings.mineru_models_dir) / "dr4a-models.json").is_file()
                )
            ):
                raise output.EnvError("Prepared local MINERU_MODELS_DIR is required for PDF debug")
        if not fake:
            versions = self.config.versions
            if self.config.source_policy.private_only or settings.llm_local:
                raise AppError(
                    "privacy_policy_conflict", "Local model debug adapter is not configured"
                )
            if state.source_selection.knowledge_base_ids:
                raise output.EnvError("Knowledge-base version authorization is not configured")
            if any(source.data_classification != "public" for source in state.sources.values()):
                raise AppError(
                    "privacy_policy_conflict",
                    "Private snapshot facts cannot use external debug adapters",
                )
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
        if state.phase == "research":
            self.research = ResearchDebugTools(settings, self.config, self.usage, fake=fake)

    def for_unit(self, unit):
        if self.research is not None:
            self.research.for_unit(unit)

    async def invoke(self, operation, payload):
        if operation in {"search", "fetch"} and self.research is not None:
            return await self.research.invoke(operation, payload)
        if operation != "llm" or payload.get("phase") != self.state.phase:
            raise AppError("invalid_state", "Debug tool operation differs from requested phase")
        prompt = payload.get("prompt")
        if not isinstance(prompt, str):
            raise AppError("invalid_state", "Invalid debug model input")
        if self.fake:
            return (
                self.fake_plan()
                if self.state.phase == "plan"
                else json.dumps({"evidence": [], "claims": [], "observations": []})
            )
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
                self.state.phase,
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
        if self.research is not None:
            await self.research.close()
        if self.model is not None:
            await self.model.aclose()

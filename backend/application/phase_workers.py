"""Canonical worker adapters. Tool callbacks own budget/cache/protocol I/O."""

from domain.ports import AdapterError
from domain.research.agents import architect
from domain.research.phase_contracts import PhaseResult


class _ContextLLM:
    def __init__(self, context, phase):
        self.context, self.phase = context, phase

    async def complete(self, prompt):
        result = await self.context.invoke("llm", {"phase": self.phase, "prompt": prompt})
        if not isinstance(result, str):
            raise AdapterError(
                "llm",
                "model_output_invalid",
                "Tool completion has an invalid shape",
                False,
                self.phase,
            )
        return result


async def plan_worker(value, context):
    plans = await architect.plan(
        _ContextLLM(context, "plan"),
        value.values["research_brief"],
        source_selection=value.values["source_selection"],
        timeout_s=context.config.timeouts_s.llm,
    )
    return PhaseResult(
        phase="plan",
        unit_id=context.unit_id,
        input_hash=value.semantic_hash,
        changes={"section_plans": plans},
        degradations=[],
        failures=[],
    )

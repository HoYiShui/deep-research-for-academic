"""Prompt identities frozen into a Run, including few-shot and output models."""


def prompt_versions():
    from domain.research.agents import architect, critic, extraction, writer
    from domain.research.ids import canonical_hash

    inputs = {
        "clarify": [architect.CLARIFY_PROMPT_TEMPLATE, architect.CLARIFY_FEW_SHOTS],
        "plan": [
            architect.PLAN_PROMPT_TEMPLATE,
            architect.PLAN_TASK_DIMENSIONS,
            architect.PlanOutput.model_json_schema(),
        ],
        "research": [
            extraction.EXTRACTION_PROMPT_TEMPLATE,
            extraction.EXTRACTION_FEW_SHOTS,
            extraction.ExtractionOutput.model_json_schema(),
            "originals-v1",
        ],
        "analyze": ["literal-context-v1"],
        "write": [
            writer.WRITE_PROMPT_TEMPLATE,
            writer.WRITE_FEW_SHOTS,
            writer.ChapterDraft.model_json_schema(),
            "materialize-chapter-schema-v3",
        ],
        "review": [
            critic.REVIEW_PROMPT_TEMPLATE,
            critic.REVIEW_FEW_SHOTS,
            critic.DraftReview.model_json_schema(),
            "review-context-v2",
        ],
    }
    return {phase: "prompt-" + canonical_hash(content) for phase, content in inputs.items()}

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain.research.tool_calls import ToolCallIdentity, ToolCallRecord


def identity(**changes):
    return ToolCallIdentity.model_validate(
        {
            "run_id": uuid4(),
            "tool": "llm",
            "provider": "deepseek",
            "version": "plan-prompt-v1/model-revision-v1",
            "arguments": {"prompt": "Public question", "max_tokens": 16384},
            "input_hash": "a" * 64,
            "source_policy": {
                "categories": ["papers", "web"],
                "knowledge_base_ids": [],
                "private_only": False,
            },
            "knowledge_snapshot": [],
            **changes,
        }
    )


def test_identity_is_order_independent_but_preserves_actual_input():
    first = identity()
    value = first.model_dump(mode="json")
    value["source_policy"]["categories"].reverse()
    value["arguments"] = {"max_tokens": 16384, "prompt": "Public question"}
    second = ToolCallIdentity.model_validate(value)
    assert first.call_key == second.call_key
    assert first.call_id == second.call_id
    value["arguments"]["prompt"] += " "
    assert ToolCallIdentity.model_validate(value).call_key != first.call_key


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", uuid4()),
        ("provider", "local"),
        ("version", "v2"),
        ("input_hash", "b" * 64),
        ("tool", "search"),
    ],
)
def test_meaningful_scope_and_version_changes_do_not_hit_cache(field, value):
    first = identity()
    second = ToolCallIdentity.model_validate(first.model_dump() | {field: value})
    assert first.call_key != second.call_key


@pytest.mark.parametrize("extra", [{"attempt": 2}, {"timestamp": "now"}])
def test_attempt_and_time_cannot_enter_semantic_identity(extra):
    with pytest.raises(ValidationError):
        ToolCallIdentity.model_validate(identity().model_dump() | extra)


def test_nonfinite_and_out_of_scope_knowledge_rejected():
    with pytest.raises(ValidationError):
        identity(arguments={"x": float("nan")})
    with pytest.raises(ValidationError):
        identity(arguments={"x": object()})
    with pytest.raises(ValidationError):
        identity(
            knowledge_snapshot=[
                {
                    "kb_id": uuid4(),
                    "document_id": uuid4(),
                    "document_version_id": uuid4(),
                    "index_version": "index-v1",
                }
            ]
        )


def record(**changes):
    call = identity()
    now = datetime.now(UTC)
    return ToolCallRecord.model_validate(
        {
            "call_id": call.call_id,
            "run_id": call.run_id,
            "call_key": call.call_key,
            "status": "reserved",
            "request_hash": call.call_key,
            "result_object_key": None,
            "result_hash": None,
            "failure": None,
            "budget_units": 1,
            "created_at": now,
            "updated_at": now,
            **changes,
        }
    )


def test_success_requires_durable_result_and_hash():
    with pytest.raises(ValidationError):
        record(status="succeeded")
    with pytest.raises(ValidationError):
        record(result_hash="b" * 64)
    assert (
        record(
            status="succeeded",
            result_object_key="tool-results/run1/" + "b" * 64,
            result_hash="b" * 64,
        ).status
        == "succeeded"
    )

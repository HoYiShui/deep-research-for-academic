"""Probe rejects false completion and uses explicit control requests only."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from scripts.verify_clarify_http import VerificationError
from scripts.verify_run_http import verify


def responses(*, change=None, initial_status="ready", resume_allowed=False):
    session, run = str(uuid4()), str(uuid4())
    done = {
        "session_id": session,
        "run_id": run,
        "timestamp": datetime.now(UTC).isoformat(),
        "checkpoint_seq": 2,
        "status": "cancelled",
        "review_verdict": None,
        "final_report_url": None,
        "failure": None,
    }
    terminal = {"session_id": session, "run_id": run, "checkpoint_seq": 2, "status": "cancelled"}
    mime = "text/event-stream"
    if change == "foreign":
        done["run_id"] = str(uuid4())
    elif change == "regression":
        done["checkpoint_seq"] = 0
    elif change == "mismatch":
        terminal["checkpoint_seq"] = 3
    elif change == "mime":
        mime = "application/json"
    elif change == "fake_completed":
        done["status"] = "completed"
    wire = f"id: {uuid4()}\nevent: done\ndata: {json.dumps(done)}\n\n"
    if change == "no_done":
        wire = ": heartbeat\n\n"
    requests, reads = [], 0

    def handler(request):
        nonlocal reads
        requests.append(request)
        if request.method == "POST":
            if request.url.path.endswith("/resume"):
                return httpx.Response(202, json={"session_id": session, "status": "ready"})
            return httpx.Response(202, json={"session_id": session, "status": "cancelling"})
        if request.url.path.endswith("/events"):
            return httpx.Response(200, text=wire, headers={"content-type": mime})
        reads += 1
        return httpx.Response(
            200,
            json=terminal
            if reads > 1
            else {
                "session_id": session,
                "run_id": run,
                "checkpoint_seq": 1,
                "status": initial_status,
                "resume_allowed": resume_allowed,
            },
        )

    return session, requests, handler


@pytest.mark.parametrize(
    "change", ["foreign", "regression", "mismatch", "mime", "fake_completed", "no_done"]
)
async def test_rejects_unproven_terminal(change):
    session, requests, handler = responses(change=change)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    ) as http:
        with pytest.raises(VerificationError):
            await verify(http, session_id=session)
    assert all(request.method == "GET" for request in requests)


@pytest.mark.parametrize("action", [None, "cancel", "resume"])
async def test_control_is_explicit_and_resume_uses_latest_seq(action):
    session, requests, handler = responses(
        initial_status="failed" if action == "resume" else "ready", resume_allowed=True
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    ) as http:
        result = await verify(http, session_id=session, action=action)
    assert result["status"] == "cancelled" and result["report"] is None
    writes = [request for request in requests if request.method == "POST"]
    assert len(writes) == (0 if action is None else 1)
    if writes:
        assert writes[0].headers["Idempotency-Key"]
        assert json.loads(writes[0].content) == (
            {"checkpoint_seq": 1} if action == "resume" else {}
        )


async def test_nonresumable_does_not_write():
    session, requests, handler = responses(initial_status="failed")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    ) as http:
        with pytest.raises(VerificationError, match="resumable"):
            await verify(http, session_id=session, action="resume")
    assert len(requests) == 1 and requests[0].method == "GET"

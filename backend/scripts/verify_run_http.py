"""Observe/control an explicitly selected accepted Run over live HTTP/SSE.

Create/clarify with --query/--answers-file; --approve-file is explicit approval.
An existing accepted --session without these options is observed read-only.
This probe never approves model assumptions or starts another Run on reconnect.
Model-mode is an operator declaration, not proof of real business execution.
"""

import argparse
import asyncio
import json
import math
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from pydantic import TypeAdapter, ValidationError

from domain.research.run_events import RunFrame
from scripts.verify_clarify_http import VerificationError, _read_json
from scripts.verify_clarify_http import verify as clarify

_frames = TypeAdapter(RunFrame)


async def verify(http, *, session_id, action=None):
    try:
        session_id = str(UUID(str(session_id)))
    except ValueError:
        raise VerificationError("session must be a UUID") from None
    if action not in {None, "cancel", "resume"}:
        raise VerificationError("unsupported Run action")
    path = f"/research/{session_id}"
    trace = []

    async def request(method, suffix="", body=None, expected=(200,)):
        headers = {"Idempotency-Key": str(uuid4())} if method == "POST" else {}
        response = await http.request(method, path + suffix, json=body, headers=headers)
        if response.status_code not in expected:
            raise VerificationError(f"HTTP {response.status_code}: Run request not accepted")
        try:
            value = response.json()
        except ValueError:
            raise VerificationError("Run response is not JSON") from None
        if not isinstance(value, dict) or value.get("session_id") != session_id:
            raise VerificationError("Run response has wrong Session identity")
        trace.append({"method": method, "path": path + suffix, "status": response.status_code})
        return value

    view = await request("GET")
    try:
        run_id = str(UUID(view["run_id"]))
        seq = view["checkpoint_seq"]
        if type(seq) is not int or seq < 1:
            raise ValueError
    except (KeyError, ValueError, TypeError):
        raise VerificationError("Session has no valid accepted Run") from None
    if action == "resume":
        if view.get("status") != "failed" or not view.get("resume_allowed"):
            raise VerificationError("Run is not explicitly resumable")
        await request("POST", "/resume", {"checkpoint_seq": seq}, expected=(202,))
    elif action == "cancel":
        await request("POST", "/cancel", {}, expected=(200, 202))

    events, current_id, kind, done = [], None, None, None
    async with http.stream("GET", path + "/events") as response:
        if response.status_code != 200:
            raise VerificationError(f"HTTP {response.status_code}: SSE not accepted")
        if not response.headers.get("content-type", "").startswith("text/event-stream"):
            raise VerificationError("Events endpoint is not SSE")
        async for line in response.aiter_lines():
            if line.startswith("id: "):
                current_id = line[4:]
            elif line.startswith("event: "):
                kind = line[7:]
            elif line.startswith("data: "):
                try:
                    frame = _frames.validate_python(
                        json.loads(line[6:]) | {"event": kind, "event_id": current_id}
                    )
                except (ValueError, TypeError, ValidationError):
                    raise VerificationError("SSE frame violates the Run contract") from None
                if str(frame.session_id) != session_id or str(frame.run_id) != run_id:
                    raise VerificationError("SSE frame has wrong Run identity")
                if frame.checkpoint_seq < seq:
                    raise VerificationError("SSE checkpoint seq regressed")
                seq = frame.checkpoint_seq
                events.append(frame.model_dump(mode="json"))
                if frame.event == "error" and frame.fatal:
                    raise VerificationError("Fatal SSE diagnostic; no terminal state assumed")
                if frame.event == "done":
                    done = frame
                    break
                if len(events) > 10000:
                    raise VerificationError("SSE event limit exceeded")
    if done is None:
        raise VerificationError("SSE ended without persisted terminal evidence")
    view = await request("GET")
    if (view.get("run_id"), view.get("status"), view.get("checkpoint_seq")) != (
        run_id,
        done.status,
        done.checkpoint_seq,
    ):
        raise VerificationError("SSE terminal state differs from current HTTP state")
    report = await request("GET", "/report") if done.status == "completed" else None
    if report is not None and report.get("review_verdict") != done.review_verdict:
        raise VerificationError("Report verdict differs from committed Run")
    return {"status": done.status, "view": view, "events": events, "report": report, "trace": trace}


async def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    initial = parser.add_mutually_exclusive_group(required=True)
    initial.add_argument("--session")
    initial.add_argument("--query", help="public test query; does not implicitly approve Brief")
    parser.add_argument("--answers-file", type=Path)
    parser.add_argument("--approve-file", type=Path)
    parser.add_argument("--action", choices=["cancel", "resume"])
    parser.add_argument("--model-mode", required=True, choices=["real", "controlled"])
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args(argv)
    origin = urlsplit(args.url)
    if (
        origin.scheme not in {"http", "https"}
        or not origin.hostname
        or origin.username
        or origin.password
        or origin.query
        or origin.fragment
        or origin.path not in {"", "/"}
        or not math.isfinite(args.timeout)
        or args.timeout <= 0
    ):
        parser.error("url must be a plain HTTP origin; timeout must be positive and finite")
    try:
        async with (
            asyncio.timeout(args.timeout),
            httpx.AsyncClient(base_url=args.url, timeout=args.timeout) as http,
        ):
            session_id = args.session
            if (
                args.query is not None
                or args.answers_file is not None
                or args.approve_file is not None
            ):
                if args.action is not None:
                    raise VerificationError(
                        "Clarify inputs cannot be combined with a Run control action"
                    )
                result = await clarify(
                    http,
                    query=args.query,
                    session_id=session_id,
                    answers=_read_json(args.answers_file),
                    approval=_read_json(args.approve_file),
                )
                session_id = result["view"]["session_id"]
                if result["view"]["run_id"] is None:
                    print(
                        json.dumps(
                            result
                            | {"status": result["view"]["status"], "model_mode": args.model_mode},
                            ensure_ascii=False,
                        )
                    )
                    return 0
            result = await verify(http, session_id=session_id, action=args.action)
        print(json.dumps(result | {"model_mode": args.model_mode}, ensure_ascii=False))
        return 0
    except (VerificationError, httpx.HTTPError, TimeoutError) as exc:
        message = (
            str(exc)
            if isinstance(exc, VerificationError)
            else "HTTP probe unavailable or timed out"
        )
        print(json.dumps({"status": "failed", "message": message}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

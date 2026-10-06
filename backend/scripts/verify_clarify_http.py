"""Live HTTP clarification probe; never auto-approve model assumptions.

Run with `uv run python -m scripts.verify_clarify_http --help` from backend.
Output contains the public test query/brief: use public fixtures, not private data.
The model-mode label is declared by the operator, not inferred from /health.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from pydantic import TypeAdapter, ValidationError

from domain.research.models import Positive, Record, ResearchBrief, SessionStatus
from interface.dto.research import ClarifyResponse, MessageRequest, ReadyResponse, ResearchRequest


class VerificationError(Exception):
    pass


class Approval(Record):
    session_id: UUID
    brief_version: Positive
    research_brief: ResearchBrief


def _answers(values):
    if not isinstance(values, list) or len(values) > 20:
        raise VerificationError("answers must be a list of at most 20 explicit messages")
    result = []
    for value in values:
        if not isinstance(value, dict) or "brief_version" in value:
            raise VerificationError("answer objects cannot supply a stale brief_version")
        try:
            MessageRequest.model_validate(value | {"brief_version": 1})
        except ValidationError:
            raise VerificationError("answer violates the message contract") from None
        result.append(value)
    return result


async def verify(http, *, query=None, session_id=None, answers=None, approval=None):
    """Only call public HTTP endpoints; approval must match a freshly read view."""
    if (query is None) == (session_id is None):
        raise VerificationError("provide exactly one query or session")
    values = _answers([] if answers is None else answers)
    try:
        if query is not None:
            ResearchRequest(query=query)
        else:
            session_id = str(UUID(str(session_id)))
        approved = Approval.model_validate(approval) if approval is not None else None
    except (ValueError, TypeError):
        raise VerificationError("query, session or approval violates its contract") from None
    trace = []

    async def request(method, path, body=None, *, expected=200):
        headers = {"Idempotency-Key": str(uuid4())} if method == "POST" else {}
        try:
            response = await http.request(method, path, json=body, headers=headers)
        except httpx.HTTPError:
            raise VerificationError("HTTP dependency unavailable; no acceptance assumed") from None
        if response.status_code >= 400:
            try:
                code = response.json()["error"]["code"]
            except (ValueError, KeyError, TypeError):
                code = "unexpected_response"
            raise VerificationError(f"HTTP {response.status_code}: {code}")
        if response.status_code != expected:
            raise VerificationError("HTTP success status violates the research contract")
        try:
            result = response.json()
            if method == "POST":
                schema = ReadyResponse if response.status_code == 202 else ClarifyResponse
                TypeAdapter(schema).validate_python(result)
            elif (
                not isinstance(result, dict)
                or not {"session_id", "status", "brief_version", "run_id", "checkpoint_seq"}
                <= result.keys()
            ):
                raise ValueError("Incomplete SessionView")
            if method == "GET":
                UUID(result["session_id"])
                TypeAdapter(SessionStatus).validate_python(result["status"])
                TypeAdapter(Positive).validate_python(result["brief_version"])
                if result["run_id"] is not None:
                    UUID(result["run_id"])
                    TypeAdapter(Positive).validate_python(result["checkpoint_seq"])
        except (ValueError, TypeError):
            raise VerificationError("HTTP response violates the research contract") from None
        trace.append(
            {
                "method": method,
                "path": path,
                "status_code": response.status_code,
                "request_id": response.headers.get("X-Request-ID"),
                "idempotency_key": headers.get("Idempotency-Key"),
                "request": body,
                "response": result,
            }
        )
        return result

    if query is not None:
        created = await request("POST", "/research", {"query": query}, expected=201)
        session_id = created["session_id"]
    path = f"/research/{session_id}"
    view = await request("GET", path)
    for value in values:
        if view["status"] != "ask":
            raise VerificationError(
                "remaining answers cannot be sent outside ask; inspect the brief"
            )
        await request("POST", path + "/messages", value | {"brief_version": view["brief_version"]})
        view = await request("GET", path)
    if approved is not None:
        # Do not derive approval from a model response or silently accept defaults.
        current = {
            "session_id": view["session_id"],
            "brief_version": view["brief_version"],
            "research_brief": view.get("research_brief"),
        }
        if view["status"] != "confirm" or approved.model_dump(mode="json") != current:
            raise VerificationError("approval does not match the current confirm brief/version")
        await request(
            "POST",
            path + "/confirm",
            {
                "accepted": True,
                "brief_version": approved.brief_version,
            },
            expected=202,
        )
        view = await request("GET", path)
        if view["run_id"] is None:
            raise VerificationError("confirmation did not persist a run")
    return {
        "status": "ok",
        "view": view,
        "trace": trace,
        "approval_required": view["status"] == "confirm",
    }


def _read_json(path):
    if path is None:
        return None
    try:
        if path.stat().st_size > 1_000_000:
            raise VerificationError("input file exceeds 1 MB")
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise VerificationError("input file is unavailable or invalid JSON") from None


async def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="backend HTTP origin")
    initial = parser.add_mutually_exclusive_group(required=True)
    initial.add_argument("--query", help="public test research query")
    initial.add_argument("--session", help="resume an existing session UUID")
    parser.add_argument("--answers-file", type=Path, help="JSON array of explicit message bodies")
    parser.add_argument(
        "--approve-file", type=Path, help="reviewed session_id/version/full research_brief"
    )
    parser.add_argument("--model-mode", required=True, choices=["real", "controlled"])
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args(argv)
    url = urlsplit(args.url)
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in {"", "/"}
        or not math.isfinite(args.timeout)
        or args.timeout <= 0
    ):
        parser.error("url must be a plain HTTP origin; timeout must be positive and finite")
    try:
        async with httpx.AsyncClient(base_url=args.url, timeout=args.timeout) as http:
            result = await verify(
                http,
                query=args.query,
                session_id=args.session,
                answers=_read_json(args.answers_file),
                approval=_read_json(args.approve_file),
            )
        print(json.dumps(result | {"model_mode": args.model_mode}, ensure_ascii=False))
        return 0
    except VerificationError as exc:
        print(json.dumps({"status": "failed", "message": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

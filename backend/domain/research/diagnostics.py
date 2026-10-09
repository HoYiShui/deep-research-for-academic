"""Opt-in execution diagnostics, separate from facts, SSE and tool authority."""

from contextlib import contextmanager
from contextvars import ContextVar

_sink = ContextVar("research_diagnostic_sink", default=None)
_scope = ContextVar("research_diagnostic_scope", default=None)


@contextmanager
def diagnostic_scope(*, sink=None, **fields):
    """Async children inherit the sink; sibling units keep independent scope."""
    token = _scope.set((_scope.get() or {}) | fields)
    sink_token = _sink.set(sink) if sink is not None else None
    try:
        yield
    finally:
        _scope.reset(token)
        if sink_token is not None:
            _sink.reset(sink_token)


def diagnostic(event, *, content=None, **fields):
    sink = _sink.get()
    if sink is not None:
        try:
            sink((_scope.get() or {}) | fields | {"event": event}, content=content)
        except Exception:  # noqa: BLE001, S110 -- an observer cannot alter execution
            pass


def input_summary(tool, arguments):
    if tool == "llm":
        return {"prompt_chars": len(arguments.get("prompt", ""))}
    return arguments


def result_summary(tool, content):
    try:
        return _result_summary(tool, content)
    except (TypeError, ValueError, KeyError, AttributeError):
        return {"result_type": type(content).__name__}


def _result_summary(tool, content):
    if tool == "llm" and isinstance(content, dict):
        return {
            key: content.get(key)
            for key in ("model", "stop_reason", "input_tokens", "output_tokens", "response_id")
        } | {"response_chars": len(content.get("text", ""))}
    if tool == "llm":
        return {"response_chars": len(content) if isinstance(content, str) else 0}
    if tool == "search":
        outcomes = content.get("outcomes") if isinstance(content, dict) else None
        if outcomes is not None:
            return {
                "outcomes": [
                    item | {"items": result_summary(tool, item["items"])} for item in outcomes
                ]
            }
        return [
            {key: item.get(key) for key in ("source_id", "provider", "title", "url", "source_tier")}
            for item in content
        ]
    if tool == "fetch" and isinstance(content, dict):
        fetched = content.get("fetched", content)
        return {
            key: fetched.get(key)
            for key in (
                "final_url",
                "media_type",
                "content_ref",
                "parsed_content_ref",
                "parser_version",
            )
        }
    return {"result_type": type(content).__name__}

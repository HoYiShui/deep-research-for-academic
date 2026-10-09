"""A real local HTTP transport with explicitly controlled model responses."""

import asyncio
import json
from contextlib import asynccontextmanager


def http_message(message):
    """Encode one Messages API response as the SSE stream a streaming SDK reads."""
    events = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": message
                | {
                    "content": [],
                    "stop_reason": None,
                    "usage": message["usage"] | {"output_tokens": 0},
                },
            },
        )
    ]
    for index, block in enumerate(message["content"]):
        events += [
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": block | {"text": ""},
                },
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "text_delta", "text": block["text"]},
                },
            ),
            ("content_block_stop", {"type": "content_block_stop", "index": index}),
        ]
    events += [
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": message["stop_reason"], "stop_sequence": None},
                "usage": {"output_tokens": message["usage"]["output_tokens"]},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]
    body = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode()
    return (
        b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: "
        + str(len(body)).encode()
        + b"\r\nConnection: close\r\n\r\n"
        + body
    )


@asynccontextmanager
async def model_server(model, text, *, hold=False):
    calls, tasks = [], set()
    entered, release = asyncio.Event(), asyncio.Event()

    async def handler(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            length = next(
                int(line.split(b":", 1)[1])
                for line in head.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            calls.append(json.loads(await reader.readexactly(length)))
            entered.set()
            if hold:
                await release.wait()
            message = {
                "id": "controlled-response",
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [
                    {
                        "type": "text",
                        "text": text(calls[-1])
                        if callable(text)
                        else text[min(len(calls) - 1, len(text) - 1)]
                        if isinstance(text, list)
                        else text,
                    }
                ],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 20, "output_tokens": 30},
            }
            writer.write(http_message(message))
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            tasks.discard(task)

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}", calls, entered
    finally:
        server.close()
        for task in tuple(tasks):
            task.cancel()
        await asyncio.gather(*tuple(tasks), return_exceptions=True)
        await server.wait_closed()

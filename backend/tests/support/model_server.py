"""A real local HTTP transport with explicitly controlled model responses."""

import asyncio
import json
from contextlib import asynccontextmanager


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
            body = json.dumps(
                {
                    "id": "controlled-response",
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [
                        {
                            "type": "text",
                            "text": text[min(len(calls) - 1, len(text) - 1)]
                            if isinstance(text, list)
                            else text,
                        }
                    ],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 20, "output_tokens": 30},
                }
            ).encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )
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

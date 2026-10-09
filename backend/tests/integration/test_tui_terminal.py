"""Actual pi-tui entry and keyboard input over an invocation-owned POSIX PTY."""

import asyncio
import os
import re
import struct
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from tests.integration.test_verify_clarify_http import server
from tests.support.run_diagnostics import diagnose_on_failure

pty = pytest.importorskip("pty", reason="Terminal integration requires POSIX PTY")
fcntl = pytest.importorskip("fcntl")
termios = pytest.importorskip("termios")


@asynccontextmanager
async def terminal(url):
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 32, 100, 0, 0))
    os.set_blocking(master, False)
    process = None
    loop = asyncio.get_running_loop()
    captured = bytearray()

    def read():
        try:
            captured.extend(os.read(master, 65536))
        except BlockingIOError:
            pass
        except OSError:
            loop.remove_reader(master)

    try:
        process = await asyncio.create_subprocess_exec(
            "node",
            "--import",
            "tsx",
            "src/main.ts",
            cwd=Path(__file__).resolve().parents[3] / "tui",
            env=os.environ | {"DR4A_API_URL": url, "TERM": "xterm-256color"},
            stdin=slave,
            stdout=slave,
            stderr=slave,
        )
        loop.add_reader(master, read)

        def text():
            # Strip control sequences for text assertions, not a visual renderer.
            return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", captured.decode(errors="replace"))

        async def visible(fragment):
            async with asyncio.timeout(10):
                while fragment not in text():
                    assert process.returncode is None, text()[-2000:]
                    await asyncio.sleep(0.02)

        yield process, lambda value: os.write(master, value.encode()), visible, text
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except TimeoutError:
                process.kill()
                await process.wait()
        loop.remove_reader(master)
        os.close(master)
        os.close(slave)


async def test_actual_tui_keyboard_clarify_confirm_cancel_and_cli_hint(pg_database):
    pool, database = pg_database

    async def persisted(status):
        async with asyncio.timeout(10):
            while await pool.fetchval("SELECT status FROM sessions") != status:
                await asyncio.sleep(0.02)

    async with server(database) as url, terminal(url) as (process, send, visible, text):
        await visible("DR4A")
        send("Design a public evaluation\r")
        await persisted("ask")
        await visible("Clarify")
        assert await pool.fetchval("SELECT count(*) FROM research_runs") == 0
        send("Public intrusion detector evaluation\r")
        await persisted("confirm")
        await visible("Research Brief")
        await visible("Design a reproducible evaluation")
        assert await pool.fetchval("SELECT count(*) FROM research_runs") == 0
        send("/confirm\r")
        await persisted("ready")
        await visible("Brief/Run 已保存")
        send("/session\r")
        await visible("cli dump")
        await visible("--debug-db --json")
        send("/cancel\r")
        await persisted("cancelled")
        await visible("cancelled")
        assert await pool.fetchval("SELECT status FROM research_runs") == "cancelled"
        assert await pool.fetchval("SELECT attempt_count FROM research_runs") == 0
        assert await pool.fetchval("SELECT count(*) FROM reports") == 0
        assert "请求失败" not in text() and "contract_error" not in text()
        send("\x03")
        await asyncio.wait_for(process.wait(), timeout=3)
        assert process.returncode == 0


async def test_actual_tui_renders_live_progress_and_controlled_report(pg_database, object_cache):
    pool, database = pg_database
    async with (
        server(database, run_bucket=object_cache.bucket, pause="progress") as url,
        terminal(url) as (process, send, visible, text),
        diagnose_on_failure(pool),
    ):
        await visible("DR4A")
        send("Design a public evaluation\r")
        await visible("Clarify")
        send("Public intrusion detector evaluation\r")
        await visible("Research Brief")
        assert await pool.fetchval("SELECT count(*) FROM research_runs") == 0
        send("/confirm\r")
        await visible("query_completed")
        await visible("needs_more_work")
        await visible("Report")
        await visible("References")
        assert await pool.fetchval("SELECT status FROM sessions") == "completed"
        assert await pool.fetchval("SELECT status FROM research_runs") == "completed"
        assert await pool.fetchval("SELECT checkpoint_seq FROM research_runs") == 20
        assert await pool.fetchval("SELECT attempt_count FROM research_runs") == 1
        assert await pool.fetchval("SELECT count(*) FROM reports") == 1
        assert await pool.fetchval("SELECT count(*) FROM tool_call_attempts") == 1
        assert "contract_error" not in text() and "无法连接后端" not in text()
        send("\x03")
        await asyncio.wait_for(process.wait(), timeout=3)
        assert process.returncode == 0

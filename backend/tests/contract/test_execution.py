"""Contract test for CodeExecutionPort (Docker, mocked subprocess)."""

import json
from unittest.mock import patch

import pytest

from infrastructure.sandbox.docker import DockerExecution


class _FakeProc:
    async def wait(self):
        return 0

    def kill(self):
        pass


@pytest.mark.asyncio
async def test_docker_execute_returns_empty_when_no_result() -> None:
    with patch(
        "infrastructure.sandbox.docker.asyncio.create_subprocess_exec",
        return_value=_FakeProc(),
    ) as mock_exec:
        adapter = DockerExecution()
        result = await adapter.execute("print(1)", {"x": 1})
        assert result == {}
        assert mock_exec.called

"""No-model TCP test runtime, guarded to invocation-owned PG and MinIO."""

import os
import re
import signal
from urllib.parse import urlsplit

from application.bootstrap import HttpRuntime
from application.settings import Settings
from interface.main import create_app


class NoModel:
    async def complete(self, *args, **kwargs):
        raise AssertionError("Knowledge management must not call a model")


def create_test_app():
    settings = Settings.load()
    database = urlsplit(settings.database_url.get_secret_value()).path.removeprefix("/")
    if (
        os.environ.get("DR4A_TEST_HTTP_MODE") != "controlled"
        or not re.fullmatch(r"dr4a_test_[a-f0-9]{32}", database)
        or not re.fullmatch(r"dr4a-test-[a-f0-9]{32}", settings.minio_bucket)
    ):
        raise RuntimeError("KB test runtime requires isolated PG and MinIO")
    runtime = HttpRuntime(settings=settings, llm=NoModel())
    if os.environ.get("DR4A_TEST_PAUSE") == "kb_creation":
        original = runtime.knowledge_index.ensure_partition

        async def pause(identity):
            await original(identity)
            # Parent receives an exact crash boundary after a real external write
            # and before PG active/response commit. No finally on SIGKILL.
            print("KB_PARTITION_WRITTEN", flush=True)
            os.kill(os.getpid(), signal.SIGSTOP)

        runtime.knowledge_index.ensure_partition = pause
    return create_app(settings=settings, container_factory=lambda config: runtime)

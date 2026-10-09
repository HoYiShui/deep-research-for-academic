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
    if os.environ.get("DR4A_TEST_PAUSE") in {"kb_delete_index", "kb_delete_objects"}:
        boundary = os.environ["DR4A_TEST_PAUSE"]
        original = (
            runtime.knowledge_index.drop_partition
            if boundary == "kb_delete_index"
            else runtime.knowledge_content.delete_prefix
        )

        async def pause_cleanup(identity):
            await original(identity)
            print("KB_DELETE_WRITTEN", flush=True)
            os.kill(os.getpid(), signal.SIGSTOP)

        if boundary == "kb_delete_index":
            runtime.knowledge_index.drop_partition = pause_cleanup
        else:
            runtime.knowledge_content.delete_prefix = pause_cleanup
    return create_app(settings=settings, container_factory=lambda config: runtime)

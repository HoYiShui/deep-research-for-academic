"""Explicit test-only server factory, never imported by the production root."""

import asyncio
import json
import os
import re
import signal
from urllib.parse import urlsplit

from application.bootstrap import HttpRuntime
from application.phase_executor import PhaseExecutor
from application.phase_tools import ModelBinding
from application.report_serializer import ReportPublisher
from application.run_driver import RunDriver
from application.settings import Settings
from infrastructure.storage.content_cache import MinioResultCache
from interface.main import create_app
from tests.integration.test_mono_phase_tools import ControlledModel as PlanModel
from tests.integration.test_mono_run_driver import controlled_worker


class ControlledModel:
    async def complete(self, prompt):
        context = json.loads(prompt.split("Context JSON:\n", 1)[1])
        patch = (
            {}
            if not context["history"]
            else {
                "task_type": "evaluation_design",
                "decision_goal": "Design a reproducible evaluation",
                "research_object": "An intrusion detector",
                "deliverable": "An evaluation protocol",
            }
        )
        return json.dumps(
            {
                "missing_fields": [],
                "questions": [],
                "brief_patch": patch,
                "assumptions": [],
                "field_reasons": {},
            }
        )


def create_test_app():
    settings = Settings.load()
    database = urlsplit(settings.database_url.get_secret_value()).path.removeprefix("/")
    if os.environ.get("DR4A_TEST_HTTP_MODE") != "controlled" or not re.fullmatch(
        r"dr4a_test_[a-f0-9]{32}", database
    ):
        raise RuntimeError("Controlled server requires an explicit isolated test database")
    return create_app(
        settings=settings,
        container_factory=lambda config: HttpRuntime(settings=config, llm=ControlledModel()),
    )


def create_run_test_app():
    """Explicit full controlled Driver, only in invocation-owned PG and bucket."""
    settings = Settings.load()
    database = urlsplit(settings.database_url.get_secret_value()).path.removeprefix("/")
    bucket = os.environ.get("DR4A_TEST_CACHE_BUCKET", "")
    if (
        os.environ.get("DR4A_TEST_HTTP_MODE") != "controlled"
        or not re.fullmatch(r"dr4a_test_[a-f0-9]{32}", database)
        or not re.fullmatch(r"dr4a-test-[a-f0-9]{32}", bucket)
    ):
        raise RuntimeError("Controlled Run server requires isolated PG and MinIO")

    class Runtime(HttpRuntime):
        async def prepare(self):
            await super().prepare()
            if os.environ.get("DR4A_TEST_PAUSE") == "confirm":
                # Freeze and idempotency commit have finished before this wake.
                # SIGSTOP is an exact test window, SIGKILL is sent by the parent.
                self.research.wake = lambda: os.kill(os.getpid(), signal.SIGSTOP)

        async def aclose(self):
            try:
                await super().aclose()
            finally:
                await cache.close()

    cache = MinioResultCache(
        settings.minio_endpoint,
        settings.minio_access_key.get_secret_value(),
        settings.minio_secret_key.get_secret_value(),
        bucket,
        secure=settings.minio_secure,
    )

    def executor(runtime):
        async def before_worker(value, context):
            pause = os.environ.get("DR4A_TEST_PAUSE")
            if pause == "rework" and value.phase == "write" and value.values["draft_version"] == 1:
                await asyncio.Event().wait()
            if pause == "research" and value.phase == "research":
                while not await context.cancel_check():
                    await asyncio.sleep(0.02)
            if pause == "progress" and value.phase == "research":
                # Explicit test gate: register the real HTTP subscriber before
                # allowing the controlled unit to complete. No timing guesses.
                async with asyncio.timeout(10):
                    while runtime.run_event_bus.subscriber_count == 0:
                        await asyncio.sleep(0.01)

        driver = RunDriver(
            store=runtime.repository_store,
            cache=cache,
            executor=PhaseExecutor(
                dict.fromkeys(
                    ["plan", "research", "analyze", "write", "review"],
                    controlled_worker(
                        [],
                        rework=os.environ.get("DR4A_TEST_REWORK") == "1",
                        before_worker=before_worker,
                    ),
                )
            ),
            model=ModelBinding(
                PlanModel(settings.llm_model),
                "anthropic_compatible",
                settings.llm_model,
                settings.llm_revision,
                1000,
            ),
            model_slots=asyncio.Semaphore(settings.llm_concurrency),
            clock=runtime.clock,
            publish=ReportPublisher(runtime.repository_store, runtime.clock).publish,
            unit_committed=lambda event: None,
            phase_committed=lambda event: None,
            finished=lambda event: None,
            diagnostic=runtime.run_event_bus.emit,
        )
        return driver.execute

    return create_app(
        settings=settings,
        container_factory=lambda config: Runtime(
            settings=config, llm=ControlledModel(), run_executor_factory=executor
        ),
    )

"""Explicit test-only server factory, never imported by the production root."""

import json
import os
import re
from urllib.parse import urlsplit

from application.bootstrap import HttpRuntime
from application.settings import Settings
from interface.main import create_app


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

"""FastAPI application entry point."""

from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from application.bootstrap import Container, HttpRuntime
from application.settings import Settings
from interface.http_errors import install_http_errors
from interface.router.auth import router as auth_router
from interface.router.ingestion_jobs import router as ingestion_router
from interface.router.knowledge_base import router as kb_router
from interface.router.research import router as research_router


def create_app(
    *,
    settings: Settings | None = None,
    container_factory: Callable[[Settings], Container] | None = None,
) -> FastAPI:
    """Overrides are explicit test composition, never a runtime fake fallback."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Validate before constructing adapters or accepting requests.
        config = settings or Settings.load()
        factory = container_factory or (lambda config: HttpRuntime(settings=config))
        container = factory(config)
        try:
            prepare = getattr(container, "prepare", None)
            if prepare is not None:
                await prepare()
            app.state.settings = config
            app.state.container = container
            yield
        finally:
            try:
                await container.aclose()
            finally:
                for name in ("container", "settings"):
                    if hasattr(app.state, name):
                        delattr(app.state, name)

    app = FastAPI(title="Deep Research Agent", lifespan=lifespan)
    install_http_errors(app)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Process liveness only; dependency readiness is a separate capability."""
        return {"status": "ok"}

    app.include_router(auth_router)
    app.include_router(research_router)
    app.include_router(kb_router)
    app.include_router(ingestion_router)
    return app


app = create_app()

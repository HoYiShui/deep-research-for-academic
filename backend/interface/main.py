"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from application.settings import Settings
from interface.router.auth import router as auth_router
from interface.router.knowledge_base import router as kb_router
from interface.router.research import router as research_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Reject unsafe configuration before accepting HTTP requests."""
    app.state.settings = Settings.load()
    yield


app = FastAPI(title="Deep Research Agent", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    """Lightweight process health endpoint for container orchestration."""
    return {"status": "ok"}


app.include_router(auth_router)
app.include_router(research_router)
app.include_router(kb_router)

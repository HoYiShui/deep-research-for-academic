"""FastAPI application entry point."""

from fastapi import FastAPI

from interface.router.knowledge_base import router as kb_router
from interface.router.research import router as research_router

app = FastAPI(title="Deep Research Agent")
app.include_router(research_router)
app.include_router(kb_router)

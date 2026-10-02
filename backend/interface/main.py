"""FastAPI application entry point (walking skeleton)."""

from fastapi import FastAPI

from interface.router.research import router

app = FastAPI(title="Deep Research Agent")
app.include_router(router)

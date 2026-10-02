"""HTTP routes for authentication (register / login)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from application.bootstrap import get_container

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", status_code=201)
async def register(body: dict) -> dict:
    """Register a user with a bcrypt-hashed password."""
    try:
        return await get_container().auth.register(body["email"], body["password"])
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/login")
async def login(body: dict) -> dict:
    """Verify credentials and return a JWT."""
    try:
        return await get_container().auth.login(body["email"], body["password"])
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

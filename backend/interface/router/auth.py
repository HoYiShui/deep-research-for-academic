"""HTTP routes for authentication (register / login)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from application.bootstrap import get_container
from interface.dto.auth import LoginRequest, RegisterRequest, RegisterResponse, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", status_code=201, response_model=RegisterResponse)
async def register(body: RegisterRequest, request: Request) -> dict:
    """Register a user with a bcrypt-hashed password."""
    try:
        return await get_container(request).auth.register(body.email, body.password)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request) -> dict:
    """Verify credentials and return a JWT."""
    try:
        return await get_container(request).auth.login(body.email, body.password)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

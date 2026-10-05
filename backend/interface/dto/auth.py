"""Auth DTOs (request/response models)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from interface.dto.base import RequestDTO


class RegisterRequest(RequestDTO):
    """POST /auth/register body."""

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(RequestDTO):
    """POST /auth/login body."""

    email: str
    password: str


class RegisterResponse(BaseModel):
    """Register response."""

    user_id: str


class TokenResponse(BaseModel):
    """Login response."""

    access_token: str

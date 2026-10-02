"""Auth use case: register (bcrypt hash) and login (JWT).

Auth is not a domain concept: these are thin application-level cases over the
UserStorePort. The interface layer validates tokens and derives user_id.
"""

from __future__ import annotations

import os
import uuid

import bcrypt
import jwt

from application.ports import UserStorePort


class AuthService:
    """Register and authenticate users."""

    def __init__(self, users: UserStorePort, secret: str | None = None) -> None:
        self._users = users
        self._secret = secret or os.environ.get("JWT_SECRET", "")

    async def register(self, email: str, password: str) -> dict:
        """Register a new user with a bcrypt-hashed password.

        Args:
            email: The user's email (unique).
            password: The plaintext password (hashed, never stored).

        Returns:
            {"user_id"}.

        Raises:
            ValueError: if the email is already registered.
        """
        if await self._users.get_by_email(email):
            raise ValueError("email already registered")
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        user_id = uuid.uuid4().hex
        await self._users.create(user_id, email, password_hash)
        return {"user_id": user_id}

    async def login(self, email: str, password: str) -> dict:
        """Verify credentials and issue a JWT.

        Args:
            email: The user's email.
            password: The plaintext password.

        Returns:
            {"access_token"}.

        Raises:
            ValueError: if the credentials are invalid.
        """
        user = await self._users.get_by_email(email)
        if not user or not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
            raise ValueError("invalid credentials")
        token = jwt.encode({"sub": user["user_id"], "email": email}, self._secret, algorithm="HS256")
        return {"access_token": token}

    def verify_token(self, token: str) -> str | None:
        """Decode a JWT and return the user_id, or None if invalid."""
        try:
            payload = jwt.decode(token, self._secret, algorithms=["HS256"])
            return payload.get("sub")
        except jwt.PyJWTError:
            return None

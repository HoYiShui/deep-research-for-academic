"""Shared request validation policy; responses retain their own schema."""

from pydantic import BaseModel, ConfigDict


class RequestDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

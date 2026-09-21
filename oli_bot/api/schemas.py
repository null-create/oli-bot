"""Typed request bodies for the API server routes.

``PUT /v1/config`` intentionally stays a free-form ``dict`` — it is a dynamic
mapping driven by ``_FLAT_TO_NESTED``.
"""

from pydantic import BaseModel


class RenameSessionBody(BaseModel):
    name: str = ""


class SetWorkspaceBody(BaseModel):
    path: str = ""
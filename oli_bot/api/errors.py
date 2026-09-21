"""Error handling for the API server.

Centralizes the OpenAI-style ``{"error": {"message": ...}}`` response shape.
"""

from typing import Any, Dict

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse


def error_body(message: str, **extra: Any) -> Dict[str, Any]:
    return {"error": {"message": message, **extra}}


class AgentError(Exception):
    """Raised when the agent run fails; formatted as an OpenAI error body."""

    def __init__(self, message: str, code: str = "agent_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code

    def response(self) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=error_body(
                message=self.message,
                type="server_error",
                code=self.code,
            ),
        )


async def _agent_error_handler(request: Request, exc: AgentError) -> JSONResponse:
    return exc.response()


async def _http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(exc.detail),
        headers=exc.headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AgentError, _agent_error_handler)
    app.add_exception_handler(HTTPException, _http_error_handler)
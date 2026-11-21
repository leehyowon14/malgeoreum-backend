from typing import Generic, Optional, TypeVar

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


Code = TypeVar("Code")


class ErrorDetail(BaseModel, Generic[Code]):
    code: Code


class ErrorResponse(BaseModel, Generic[Code]):
    error: ErrorDetail[Code]


class IssueError(Exception):
    def __init__(self, code: str, *, retry_after: Optional[int] = None):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


def issue_error_response(request: Request, exc: IssueError) -> JSONResponse:
    status = {
        "invalid_token": 401,
        "rate_limited": 429,
        "temporarily_unavailable": 503,
        "transcription_unavailable": 503,
    }[exc.code]
    headers = {"Cache-Control": "no-store"}
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    if exc.retry_after is not None:
        headers["Retry-After"] = str(exc.retry_after)
    return JSONResponse({"error": {"code": exc.code}}, status_code=status, headers=headers)

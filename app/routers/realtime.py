from typing import Literal

from fastapi import APIRouter, Depends, Request, Response

from app.errors import ErrorResponse
from app.firebase import verified_user_key
from app.transcription import ClientSecret


router = APIRouter(prefix="/v1/realtime", tags=["realtime"])


@router.post(
    "/client-secrets",
    operation_id="issueRealtimeClientSecret",
    summary="WebRTC 한국어 전사 자격증명 발급",
    responses={
        200: {
            "description": "연결 자격증명",
            "headers": {
                "Cache-Control": {"required": True, "schema": {"type": "string", "enum": ["no-store"]}}
            },
        },
        401: {"description": "인증 실패", "model": ErrorResponse[Literal["invalid_token"]]},
        429: {
            "description": "UID별 발급 제한",
            "model": ErrorResponse[Literal["rate_limited"]],
            "headers": {
                "Retry-After": {"required": True, "schema": {"type": "integer", "minimum": 1}}
            },
        },
        503: {
            "description": "전사 연결 불가",
            "model": ErrorResponse[Literal["temporarily_unavailable", "transcription_unavailable"]],
        },
    },
)
def issue_client_secret(
    request: Request, response: Response, user_key: str = Depends(verified_user_key)
) -> ClientSecret:
    request.app.state.rate_limiter.consume(user_key)
    secret = request.app.state.issuer.issue(user_key)
    response.headers["Cache-Control"] = "no-store"
    return secret

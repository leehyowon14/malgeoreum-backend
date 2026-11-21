import time
from typing import Optional

import httpx
from pydantic import BaseModel, Field

from app.errors import IssueError


class ClientSecret(BaseModel):
    client_secret: str = Field(min_length=1, repr=False)
    expires_at: int = Field(description="Unix seconds", json_schema_extra={"format": "int64"})


class TranscriptionIssuer:
    def __init__(self, client: httpx.Client, api_key: Optional[str]):
        self.client = client
        self.api_key = api_key

    def issue(self, user_key: str) -> ClientSecret:
        if not self.api_key:
            raise IssueError("transcription_unavailable")
        try:
            response = self.client.post(
                "https://api.openai.com/v1/realtime/client_secrets",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "OpenAI-Safety-Identifier": user_key,
                },
                json={
                    "expires_after": {"anchor": "created_at", "seconds": 60},
                    "session": {
                        "type": "transcription",
                        "audio": {
                            "input": {
                                "transcription": {
                                    "model": "gpt-live-transcribe",
                                    "languages": ["ko"],
                                    "delay": "minimal",
                                },
                                "turn_detection": None,
                            }
                        },
                    },
                },
            )
        except httpx.RequestError:
            raise IssueError("temporarily_unavailable") from None

        try:
            body = response.json()
        except ValueError:
            body = None
        if response.status_code == 429:
            error = body.get("error") if isinstance(body, dict) else None
            quota_errors = ("insufficient_quota", "billing_hard_limit_reached")
            if isinstance(error, dict):
                if error.get("code") in quota_errors or error.get("type") in quota_errors:
                    raise IssueError("transcription_unavailable")
            raise IssueError("temporarily_unavailable")
        if response.is_server_error or response.status_code == 408:
            raise IssueError("temporarily_unavailable")
        if not response.is_success:
            raise IssueError("transcription_unavailable")

        if not isinstance(body, dict):
            raise IssueError("temporarily_unavailable")
        value = body.get("value")
        expires_at = body.get("expires_at")
        if not isinstance(value, str) or not value.strip():
            raise IssueError("temporarily_unavailable")
        if type(expires_at) is not int or expires_at <= time.time():
            raise IssueError("temporarily_unavailable")
        return ClientSecret(client_secret=value, expires_at=expires_at)

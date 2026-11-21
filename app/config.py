import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


def positive_integer(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except ValueError:
        raise ValueError(f"{name} must be a positive integer") from None
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


@dataclass(frozen=True)
class Settings:
    firebase_project_id: Optional[str] = None
    openai_api_key: Optional[str] = field(default=None, repr=False)
    redis_url: Optional[str] = field(default=None, repr=False)
    issue_limit: int = 5
    issue_window_seconds: int = 60

    @classmethod
    def from_environment(cls) -> "Settings":
        load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
        return cls(
            firebase_project_id=os.getenv("FIREBASE_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT"),
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            redis_url=os.getenv("REDIS_URL") or None,
            issue_limit=positive_integer("ISSUE_LIMIT", 5),
            issue_window_seconds=positive_integer("ISSUE_WINDOW_SECONDS", 60),
        )

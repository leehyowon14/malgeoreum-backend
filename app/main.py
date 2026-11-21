from contextlib import ExitStack, asynccontextmanager
from typing import Optional
from uuid import uuid4

import firebase_admin
import httpx
from fastapi import FastAPI

from app.config import Settings
from app.errors import IssueError, issue_error_response
from app.rate_limit import IssueRateLimiter
from app.routers import health, realtime
from app.transcription import TranscriptionIssuer


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config = settings or Settings.from_environment()
        with ExitStack() as resources:
            app.state.firebase = None
            if config.firebase_project_id:
                firebase = firebase_admin.initialize_app(
                    options={"projectId": config.firebase_project_id, "httpTimeout": 3},
                    name=f"malgeoreum-{uuid4().hex}",
                )
                resources.callback(firebase_admin.delete_app, firebase)
                app.state.firebase = firebase
            client = resources.enter_context(httpx.Client(timeout=httpx.Timeout(5, connect=2)))
            app.state.issuer = TranscriptionIssuer(client, config.openai_api_key)
            app.state.rate_limiter = IssueRateLimiter(config)
            resources.callback(app.state.rate_limiter.close)
            yield

    app = FastAPI(title="말걸음 MVP", version="1.0.0", lifespan=lifespan)
    app.add_exception_handler(IssueError, issue_error_response)
    app.include_router(health.router)
    app.include_router(realtime.router)
    return app


app = create_app()

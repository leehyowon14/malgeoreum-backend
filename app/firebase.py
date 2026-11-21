import hashlib
import os
from typing import Optional

from fastapi import Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth, exceptions
from google.auth.exceptions import DefaultCredentialsError, GoogleAuthError

from app.errors import IssueError


bearer = HTTPBearer(scheme_name="firebase", bearerFormat="Firebase ID token", auto_error=False)


def verified_user_key(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer),
) -> str:
    if not credentials:
        raise IssueError("invalid_token")
    firebase_app = request.app.state.firebase
    # The emulator accepts unsigned ID tokens. Issuance requires a signed identity.
    if firebase_app is None or os.getenv("FIREBASE_AUTH_EMULATOR_HOST"):
        raise IssueError("transcription_unavailable")
    try:
        claims = auth.verify_id_token(credentials.credentials, app=firebase_app, check_revoked=True)
    except (auth.InvalidIdTokenError, auth.UserDisabledError, auth.UserNotFoundError):
        raise IssueError("invalid_token") from None
    except (
        DefaultCredentialsError,
        exceptions.PermissionDeniedError,
        exceptions.UnauthenticatedError,
    ):
        raise IssueError("transcription_unavailable") from None
    except (exceptions.FirebaseError, GoogleAuthError):
        raise IssueError("temporarily_unavailable") from None
    identity = f"{firebase_app.project_id}:{claims['uid']}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()

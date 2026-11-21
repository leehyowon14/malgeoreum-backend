import json
import os
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import httpx
import jwt
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from google.oauth2.credentials import Credentials
from limits.errors import StorageError

from app.config import Settings
from app.main import create_app
from app.transcription import TranscriptionIssuer


PROJECT = "demo-malgeoreum-test"
PATH = "/v1/realtime/client-secrets"


class RealtimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.public_key = cls.signing_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {"FIREBASE_AUTH_EMULATOR_HOST": ""}))
        self.stack.enter_context(
            patch("google.auth.default", return_value=(Credentials("test-adc"), PROJECT))
        )
        self.stack.enter_context(
            patch.object(requests.Session, "send", autospec=True, side_effect=self.firebase_response)
        )
        self.app = create_app(Settings(firebase_project_id=PROJECT, issue_limit=5))
        self.client = self.stack.enter_context(TestClient(self.app))
        self.upstream_calls = []
        self.expiry = int(time.time()) + 60
        self.upstream = httpx.Response(200, json={"value": "ephemeral-test", "expires_at": self.expiry})
        provider = self.stack.enter_context(
            httpx.Client(transport=httpx.MockTransport(self.openai_response))
        )
        self.app.state.issuer = TranscriptionIssuer(provider, "test-server-key")

    def firebase_response(self, session, request, **kwargs):
        # Only Google's HTTPS endpoints are substituted. The Admin SDK checks the signed JWT.
        response = requests.Response()
        response.status_code = 200
        response.request = request
        response.url = request.url
        if request.url.startswith("https://www.googleapis.com/robot/v1/metadata/x509/"):
            body = {"test-key-id": self.public_key}
        elif request.url == f"https://identitytoolkit.googleapis.com/v1/projects/{PROJECT}/accounts:lookup":
            uid = json.loads(request.body)["localId"][0]
            valid_since = int(time.time()) + 30 if uid == "revoked" else 0
            user = {"localId": uid, "disabled": uid == "disabled", "validSince": str(valid_since)}
            body = {"users": [user]}
        else:
            self.fail("Unexpected Firebase request")
        response._content = json.dumps(body).encode()
        return response

    def openai_response(self, request):
        self.upstream_calls.append(request)
        if isinstance(self.upstream, Exception):
            raise self.upstream
        return self.upstream

    def token(self, uid="presenter", *, signing_key=None, **changes):
        now = int(time.time())
        claims = {
            "iss": f"https://securetoken.google.com/{PROJECT}",
            "aud": PROJECT,
            "sub": uid,
            "iat": now,
            "exp": now + 3600,
            "auth_time": now,
            **changes,
        }
        return jwt.encode(claims, signing_key or self.signing_key, algorithm="RS256",
                          headers={"kid": "test-key-id"})

    def issue(self, token, **kwargs):
        return self.client.post(PATH, headers={"Authorization": f"Bearer {token}"}, **kwargs)

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.json(), {"error": {"code": code}})
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_grant_uses_only_trusted_settings(self):
        response = self.issue(self.token(), json={"model": "ignored", "uid": "other", "prompt": "private"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"client_secret": "ephemeral-test", "expires_at": self.expiry})
        self.assertEqual(response.headers["cache-control"], "no-store")
        (request,) = self.upstream_calls
        self.assertEqual(request.method, "POST")
        self.assertEqual(str(request.url), "https://api.openai.com/v1/realtime/client_secrets")
        self.assertEqual(request.headers["authorization"], "Bearer test-server-key")
        self.assertRegex(request.headers["openai-safety-identifier"], r"^[0-9a-f]{64}$")
        body = json.loads(request.content)
        self.assertEqual(body["expires_after"], {"anchor": "created_at", "seconds": 60})
        self.assertEqual(body["session"], {
            "type": "transcription", "audio": {"input": {
                "transcription": {"model": "gpt-live-transcribe", "languages": ["ko"], "delay": "minimal"},
                "turn_detection": None,
            }},
        })

    def test_denied_tokens_never_reach_openai(self):
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cases = {
            "malformed": "malformed",
            "expired": self.token(exp=1),
            "wrong project": self.token(aud="another-project"),
            "wrong issuer": self.token(iss="another-issuer"),
            "wrong signing key": self.token(signing_key=other_key),
            "revoked": self.token("revoked"),
            "disabled": self.token("disabled"),
        }
        for reason, token in cases.items():
            with self.subTest(reason=reason):
                self.assert_error(self.issue(token), 401, "invalid_token")
        for headers in ({}, {"Authorization": "Basic test"}, {"Authorization": "Bearer"}):
            response = self.client.post(PATH, headers=headers)
            self.assert_error(response, 401, "invalid_token")
            self.assertEqual(response.headers["www-authenticate"], "Bearer")
        self.assertFalse(self.upstream_calls)

    def test_uid_limit_in_parallel_and_another_user(self):
        token = self.token()
        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(self.issue, [token] * 12))
        self.assertEqual(sum(r.status_code == 200 for r in responses), 5)
        for response in (r for r in responses if r.status_code != 200):
            self.assert_error(response, 429, "rate_limited")
            self.assertIn(int(response.headers["retry-after"]), range(1, 61))
        self.assertEqual(len(self.upstream_calls), 5)
        self.assertEqual(self.issue(self.token("another-user")).status_code, 200)

    def test_limiter_store_failure_stops_issuance(self):
        with patch.object(self.app.state.rate_limiter.strategy, "hit", side_effect=StorageError(Exception())):
            self.assert_error(self.issue(self.token()), 503, "temporarily_unavailable")
        self.assertFalse(self.upstream_calls)

    def test_upstream_failure_policy(self):
        cases = [
            (httpx.Response(401, text="provider diagnostic"), "transcription_unavailable"),
            (httpx.Response(429, json={"error": {"code": "insufficient_quota"}}), "transcription_unavailable"),
            (httpx.Response(429, json={"error": {"code": "rate_limit_exceeded"}}), "temporarily_unavailable"),
            (httpx.Response(503), "temporarily_unavailable"),
            (httpx.ReadTimeout("test timeout"), "temporarily_unavailable"),
        ]
        for i, (response, code) in enumerate(cases):
            with self.subTest(case=i):
                self.upstream = response
                self.assert_error(self.issue(self.token(str(i))), 503, code)
        self.assertEqual(len(self.upstream_calls), len(cases))

    def test_malformed_or_expired_success_cannot_escape(self):
        for i, payload in enumerate(([], {}, {"value": "", "expires_at": self.expiry},
                                     {"value": "expired", "expires_at": 1},
                                     {"value": "invalid-expiry", "expires_at": str(self.expiry)})):
            self.upstream = httpx.Response(200, json=payload)
            self.assert_error(self.issue(self.token(str(i))), 503, "temporarily_unavailable")

    def test_firebase_outage_and_emulator_do_not_issue(self):
        with patch.object(requests.Session, "send", side_effect=requests.ConnectionError()):
            self.assert_error(self.issue(self.token()), 503, "temporarily_unavailable")
        with patch.dict(os.environ, {"FIREBASE_AUTH_EMULATOR_HOST": "localhost:9099"}):
            self.assert_error(self.issue(self.token()), 503, "transcription_unavailable")
        self.assertFalse(self.upstream_calls)

    def test_liveness_and_published_contract(self):
        with TestClient(create_app(Settings())) as unconfigured:
            self.assertEqual(unconfigured.get("/health").json(), {"status": "ok"})
            self.assert_error(unconfigured.post(PATH, headers={"Authorization": "Bearer test"}),
                              503, "transcription_unavailable")
            spec = unconfigured.get("/openapi.json").json()
        contract = Path(__file__).resolve().parents[1] / "docs/openapi.json"
        self.assertEqual(json.loads(contract.read_text()), spec)
        self.assertEqual(spec["paths"][PATH]["post"]["security"], [{"firebase": []}])
        self.assertFalse(self.upstream_calls)

# 말걸음 백엔드

FastAPI는 로그인한 발표자에게 한국어 실시간 전사용 단기 자격증명을 발급한다. Android와 브라우저 테스트베드는 이 자격증명으로 OpenAI와 WebRTC 연결을 열고 마이크 트랙을 보낸다. 발표 계획과 실행 기록은 기기에 저장한다.

프론트엔드에서 음성을 연결하고 대본 위치를 추적하는 절차와 의사코드는 [프론트엔드 구현 안내서](docs/frontend-implementation.md)에 있다. 데이터 구조와 발급 응답은 [클라이언트 연동 계약](docs/contract-v1.md)과 [OpenAPI 명세](docs/openapi.json)에서 확인할 수 있다. [백엔드 구현 계획과 검증 현황](docs/implementation-plan.md)

## 로컬 실행

저장소 최상위 디렉터리에서 실행한다. Python 3.12를 권장한다.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

[API 문서](http://127.0.0.1:8000/docs)에서 요청과 응답을 확인할 수 있다. `GET /health`는 FastAPI 프로세스가 요청을 처리할 수 있으면 `{"status":"ok"}`를 반환한다.

## 환경 설정

앱 시작 시 이 디렉터리의 `.env`를 읽고, 실행 환경에 이미 설정된 값이 우선한다. 기존 `.env`에 필요한 항목만 추가한다. [설정 예시](.env.example)

| 변수 | 용도 |
| --- | --- |
| `OPENAI_API_KEY` | 서버에서 단기 자격증명을 발급할 때 쓰는 OpenAI 프로젝트 키 |
| `FIREBASE_PROJECT_ID` | 클라이언트와 같은 Firebase 프로젝트 ID. Google Cloud에서는 `GOOGLE_CLOUD_PROJECT`도 사용할 수 있다. |
| `GOOGLE_APPLICATION_CREDENTIALS` | 로컬에서 서비스 계정을 쓸 때 지정하는 저장소 밖의 자격증명 파일 경로. 배포 환경에서는 런타임 서비스 계정의 Application Default Credentials도 지원한다. `check_revoked=True`를 사용하므로 해당 프로젝트의 사용자 조회 권한(`firebaseauth.users.get`)이 필요하다. |
| `ISSUE_LIMIT`, `ISSUE_WINDOW_SECONDS` | 양의 정수. 기본값은 사용자별 60초 동안 발급 시도 5회다. 첫 시도부터 창이 시작되고 공급자가 실패한 시도도 집계한다. |
| `REDIS_URL` | 여러 워커·인스턴스를 실행할 때 모두 같은 Redis 주소(`redis://` 또는 `rediss://`)를 설정한다. 생략하면 로컬 워커 한 개의 메모리를 사용하고 재시작 시 카운터가 초기화된다. |

공개 서버는 HTTPS로 제공한다. 발급 경로는 Firebase Admin SDK로 서명, 프로젝트, 만료, 계정 비활성화와 토큰 취소 여부를 확인한다. Redis에는 프로젝트와 UID를 함께 해시한 키와 만료되는 카운터만 둔다.

## 클라이언트 연결

1. 마이크 사용을 선택한 클라이언트가 Firebase ID 토큰을 `Authorization: Bearer <ID_TOKEN>`으로 보내고, 본문 없이 `POST /v1/realtime/client-secrets`를 호출한다. 수동 시작에는 이 호출이 필요하지 않다.
2. 성공하면 `client_secret`과 Unix 초 단위 `expires_at`을 받는다. 연결 유효 시간은 발급 후 60초다. 이미 열린 세션은 계속 사용할 수 있다.
3. 앱은 마이크 트랙과 `oai-events` 데이터 채널을 만들고, 단기 키로 SDP offer를 OpenAI의 `/v1/realtime/calls`에 보낸다. 이후 오디오 전송과 전사 수신은 같은 WebRTC 연결에서 처리한다. 재연결 시 단기 키를 새로 요청한다. [OpenAI WebRTC](https://developers.openai.com/api/docs/guides/realtime-webrtc) · [실시간 전사](https://developers.openai.com/api/docs/guides/realtime-transcription)

클라이언트와 서버는 같은 Firebase 프로젝트를 사용한다. Firebase에서 이메일과 비밀번호 로그인을 활성화하고, Android에는 서버의 기본 주소를 지정한다. 웹 클라이언트에서는 동일 출처의 프록시를 통해 발급 경로를 호출할 수 있다.

| 발급 응답 | 앱의 동작 |
| --- | --- |
| 401 `invalid_token` | ID 토큰을 강제 갱신하고 한 번 다시 요청한다. |
| 429 `rate_limited` | `Retry-After`초 동안 발급 요청을 보류한다. |
| 503 `temporarily_unavailable` | 인증 서비스·카운터·전사 공급자의 일시 장애다. 수동 진행을 제공하고 이후 재연결할 수 있다. |
| 503 `transcription_unavailable` | Firebase·OpenAI 설정 또는 할당량 확인이 필요하다. 수동 진행을 제공한다. |

## 검사

```sh
python -m unittest discover -s tests -v
```

테스트는 로컬에서 서명한 테스트 토큰을 실제 Firebase Admin SDK에 전달하고 Google과 OpenAI의 HTTP 응답을 대체한다. 토큰 거부 시 발급 0회, 동시 요청 한도, 공급자 오류 처리, 성공 응답과 공개 명세를 확인한다.

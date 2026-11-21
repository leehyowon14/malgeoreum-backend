# 말걸음 MVP 연동 계약 v1

2026년 11월 21일 기준으로 말걸음 v03의 기능 요구사항과 화면별 데이터 경로를 정의한다. 앱의 처리 순서와 의사코드는 [프론트엔드 구현 안내서](frontend-implementation.md)에, 서버 요청과 응답은 [OpenAPI 명세](openapi.json)에 있다.

## 화면별 데이터 경로

| 화면 | 읽기와 쓰기 |
|---|---|
| S-01/02 로그인·가입 | Android Firebase Auth SDK에서 이메일·비밀번호로 인증한다. 비밀번호 확인은 앱에서 처리한다. 로그아웃 시 음성 연결과 계정별 화면 상태를 비우고 Room 자료는 보존한다. |
| S-03 나의 발표 | 현재 UID의 초안·저장 계획과 최근 리허설을 조회한다. 초안은 편집 가능하고 실행에는 확정 계획이 필요하다. |
| S-04 대본 → 구간별 목표 → 문장 중요도 | 초안을 로컬 편집하고 검증 후 새 계획 버전을 한 트랜잭션으로 발행한다. 묶기·해제는 문장 순서와 원문을 보존한다. |
| S-03-R 마이크 요청 → S-06 리허설 / S-08 실전 | 권한과 음성 사용 동의를 받은 경우에만 발급 API를 호출한다. 수동 시작은 현재 UID의 유효한 계획만으로 가능하다. |
| S-08-M 수동 진행 / 묶음 복원 | 다음 문장은 현재 문장 완료를 확정한다. 체크포인트에 해당하면 한 번만 통과로 기록한다. 스크롤·위치 지정·회색 문장 탭은 통과를 만들지 않는다. 회색 문장을 누르면 묶음 전체를 복원한다. |
| S-07 결과·계획 수정 / S-08-X 종료 | 실행 시작 당시 목표로 결과를 계산한다. 수정 저장은 다음 실행부터 적용한다. 종료 확인에서 취소하면 진행을 유지한다. |

## Room 저장 계약

ID는 로컬 생성 UUID, UID는 Firebase 문자열이다. 표시 날짜는 UTC epoch 밀리초를 현지화하고, 목표·관측·구간 길이는 정수 밀리초를 사용한다. 모든 DAO 접근에 현재 UID를 적용하고 자식은 소유 발표 또는 실행을 통해 조회한다. 다른 UID·버전의 참조를 거부한다.

| 엔터티 | 키와 주요 필드 |
|---|---|
| Presentation | id, ownerUid, updatedAt, publishedVersionId(선택). 한 발표에 편집 중 초안 한 개와 여러 발행 버전을 둔다. |
| PlanVersion | id, presentationId, revision, state(draft/published), title, limitMs. 발행한 버전은 불변이며 편집 시 초안으로 복사한다. |
| Section / Sentence | Section(id, versionId, position, title), Sentence(id, sectionId, position, text, importance, groupId?). 같은 부모 안의 position은 유일하다. |
| SentenceGroup / Checkpoint | Group(id, sectionId), Checkpoint(id, versionId, sentenceId, label, cumulativeTargetMs). 체크포인트는 같은 버전의 문장 끝을 가리킨다. |
| Run | id, versionId, mode(rehearsal/live), status(running/paused/completed/interrupted), startedAt, startMonotonicMs, elapsedMs, activeMs. 재시작은 새 id를 만든다. |
| RunEvent | id, runId, sequence, kind, source, generation, sentenceId?, checkpointId?, groupId?, elapsedMs, activeMs, observedActiveMs?, correctsEventId?. (runId, sequence)는 유일하고 동일 조작의 재시도에는 같은 id를 쓴다. |
| UserSettings | ownerUid 기본키, 화면·알림 설정. 계정 전환 시 해당 UID로 다시 조회한다. |

- 발행 시 목표는 대본 순서대로 증가하고 마지막 목표는 마지막 문장과 전체 제한 시간에 고정한다. 구간 배정은 인접 누적 목표의 차이다. 초안에는 미입력을 허용한다.
- v1의 묶음 정책은 같은 구간의 연속된 두 문장 이상, 문장당 한 묶음이다. 중요도는 문장별 required/normal/supplementary로 유지한다. 실전에서는 보충부터 줄이고 혼합 묶음에는 가장 높은 중요도를 적용한다. 현재 문장 또는 필수 문장을 포함하면 묶음 전체를 보호한다.
- RunEvent에는 통과, 생략·복원, 위치 지정, 연결 단절·회복, 일시정지·재개, 보정·무효화를 구분해 남긴다. 세션 내 단조 시계로 전체 경과를 계산하고 명시적 리허설 일시정지만 activeMs에서 뺀다. 전사 수신 시각과 실제 통과 관측값을 구분한다. 실행·체크포인트별 유효 통과는 한 건으로 제한하고 보정은 원 이벤트를 참조한다. 중복 확인과 이벤트 쓰기는 같은 트랜잭션에서 수행한다.
- S-07 예시에서 전체 5:48, 일시정지 0:20이면 비교 시간은 5:28이다. 기대 효과 통과가 미확인이면 종료 시각이 있어도 마지막 구간 소요 시간은 null이다. 저장 실패 시 기록 대기 상태를 알리고 같은 ID로 재시도한다. 대본과 시계는 계속 진행한다. 앱 종료 후에는 저장된 기록만 열고 다음 실행을 새로 시작한다.

## 음성 연결과 서버 책임

1. 앱은 Firebase ID 토큰을 얻어 HTTPS로 POST /v1/realtime/client-secrets를 호출한다. 본문은 보내지 않는다. 서버는 Firebase Admin SDK로 해당 프로젝트 토큰을 검증하고 검증된 UID에 발급 제한을 적용한다. Firebase 비밀번호, 대본, 실행 ID는 발급 입력에 포함하지 않는다.
2. FastAPI는 서버 비밀값으로 POST https://api.openai.com/v1/realtime/client_secrets 를 호출한다. 아래 JSON은 서버가 구성한다. 클라이언트에 반환할 값은 value를 매핑한 client_secret과 공급자의 expires_at(Unix 초)이다. 응답에는 Cache-Control: no-store를 지정한다. 토큰과 응답 본문은 로그에서 제외한다.

```json
{
  "expires_after": {"anchor": "created_at", "seconds": 60},
  "session": {
    "type": "transcription",
    "audio": {
      "input": {
        "transcription": {"model": "gpt-live-transcribe", "languages": ["ko"], "delay": "minimal"},
        "turn_detection": null
      }
    }
  }
}
```

3. Android는 마이크 트랙과 데이터 채널을 만든 뒤 단기 토큰을 Bearer로 사용해 SDP offer를 POST https://api.openai.com/v1/realtime/calls 에 application/sdp로 보낸다. 반환된 SDP answer를 적용한다. 오디오는 WebRTC 트랙으로 전송하고 앱이 발화 경계를 감지해 데이터 채널로 input_audio_buffer.commit을 보낸다. 전사 프롬프트에 대본을 넣지 않는다.
4. conversation.item.input_audio_transcription.delta는 후보 위치 표시, completed는 로컬 매칭 후 통과 판단에 사용한다. committed의 item_id와 previous_item_id로 발화 순서를 복원한다. 중복 이벤트와 이전 실행·추적 세대의 결과는 버린다. 위치 변경, 수동 전환, 재연결, 실행 종료 시 세대를 바꾼다.
5. 단기 토큰 만료 시각은 신규 연결에 사용 가능한 기한이다. 재연결할 때 새 토큰을 받고 현재 위치 확인 후 추적을 재개한다. 사용자가 수동 모드를 선택한 경우 명시적 재개를 기다린다. 앱은 음성 원본과 전사 전문을 영구 저장하지 않는다.

## 앱의 문장 추적과 복귀

문장 누락과 오인식에 따른 위치 복구는 프론트엔드가 전담한다. 앱은 부분 전사에 대본 어절 전체가 나타나면 밑줄을 표시한다. 공백을 정규화해 띄어쓰기 차이와 확정 경계에서 나뉜 단어를 처리한다. 같은 단어가 여러 곳에 있으면 최근 최대 네 어절의 문맥으로 위치를 찾는다. 전사 수정으로 표시 근거가 사라지면 밑줄을 바로잡는다. 완료 전사에서 문장 끝이 확인되면 읽기와 통과를 기록한다. 한 전사가 두 문장에 걸치면 앞 문장 끝과 다음 문장 시작을 함께 대조하고, 앞 문장의 통과 시각은 수신 시각 추정으로 표시한다.

테스트베드는 보통 현재 위치부터 네 문장을 본다. 실행 중 커서가 3.5초간 전진하지 않으면 열두 문장, 8초가 지나면 남은 대본까지 살핀다. 확장 범위에서는 세 어절 이상, 공백 제외 여덟 글자 이상의 문맥이 한 위치를 뚜렷하게 가리켜야 이동한다. 건너간 체크포인트는 미확인으로 남는다. 발표자는 ‘수동 진행 → 여기부터 이어가기’로 직접 위치를 지정할 수도 있다.

백엔드는 단기 키를 발급할 때 한국어 실시간 전사의 `delay`를 `minimal`로 요청한다. WebRTC 오디오는 계속 전송한다. 테스트베드는 약 320ms의 쉼에서 확정 요청을 보낸다. 발화가 2.4초를 넘으면 180ms의 쉼에서도 확정하고, 연속 발화는 약 4.2초에서 나눈다. 단어 표시에는 확정 전 도착한 전사 조각을 바로 사용한다. [OpenAI 실시간 전사](https://developers.openai.com/api/docs/guides/realtime-transcription)

## 발급 실패와 검증 기준

| HTTP / error.code | 앱 처리 |
|---|---|
| 401 / invalid_token | Firebase 토큰 강제 갱신 후 한 번 재요청. 실패하면 인증 안내. |
| 429 / rate_limited | UID 발급 제한. Retry-After(초) 전에는 재요청을 막는다. |
| 503 / transcription_unavailable | 공급자 할당량·설정 문제 등 자동 재시도가 부적절한 경우. 수동 진행을 제공한다. |
| 503 / temporarily_unavailable | 공급자 타임아웃·일시 장애. 수동 진행을 제공하고 연결 복구 시 제한적으로 재시도한다. |

발급 제한의 기본값은 UID당 60초에 5회다. 횟수와 창 크기는 배포 설정으로 변경하며 다중 워커·인스턴스는 같은 Redis의 카운터를 공유한다. 카운터 키와 OpenAI의 `OpenAI-Safety-Identifier`에는 Firebase 프로젝트 ID와 UID를 함께 해시한 값을 사용한다. OpenAI 장기 키와 Firebase 검증 설정은 서버에서 주입한다. 로그인 사용자 식별이 불가능한 경우 계정별 자료를 열지 않는 것을 v1 기본값으로 정한다.

구현 수용 기준은 잘못된 Firebase 토큰이면 OpenAI 호출 0회, 수동 시작이면 발급 요청 0회, 재연결 후 오래된 완료 이벤트가 도착해도 통과 증가 0건이다. 실기기에서는 한국어 마이크 입력 → commit → completed와 장애 중 시계 연속성을 확인한다. 발급 API 구현과 실제 WebRTC 시험 결과는 [구현 계획의 검증 현황](implementation-plan.md)에 기록한다.

[OpenAI 단기 자격증명](https://developers.openai.com/api/reference/resources/realtime/subresources/client_secrets/methods/create) · [실시간 전사](https://developers.openai.com/api/docs/guides/realtime-transcription) · [WebRTC 연결](https://developers.openai.com/api/docs/guides/realtime-webrtc)

# 말걸음 프론트엔드 구현 안내서

## Abstract

말걸음은 발표자의 목소리를 대본과 대조해 읽는 위치를 표시하고, 미리 정한 문장 끝을 통과한 시각을 기록하는 앱이다. FastAPI는 로그인한 사용자에게 음성 연결용 단기 키를 발급한다. 앱은 OpenAI에서 받은 부분 전사로 단어에 밑줄을 긋고, 완료 전사나 사용자의 확인으로 발표 기록을 갱신한다. 이 문서는 Android 개발자가 같은 동작을 구현할 수 있도록 기기 내 저장, 음성 연결, 대본 추적과 수동 복구의 판단 순서를 설명한다. [서버 연동 및 데이터 계약](contract-v1.md)과 [HTTP 명세](openapi.json)

## 계정별 계획과 실행 상태

사용자는 Firebase에서 이메일과 비밀번호로 로그인한다. 앱은 현재 사용자의 식별자(UID)로 발표 목록, 계획, 실행 기록과 설정을 조회한다. Android의 저장소는 Room이다. 모든 읽기와 쓰기에 UID를 적용하며, 문장이나 이벤트처럼 UID가 없는 자식 자료는 소유한 발표 또는 실행을 통해 접근한다. 로그아웃하면 마이크와 연결을 닫고 메모리의 계정 상태를 비운다. 같은 사용자가 다시 로그인하면 기기에 보존한 자료를 연다.

계획은 대본의 구간과 문장, 문장별 중요도, 묶음, 체크포인트로 구성한다. 체크포인트는 이름과 누적 목표 시각을 부여한 문장 끝이다. 한 구간에 여러 체크포인트를 둘 수 있다. 연결된 문장을 삭제하면 대체 위치를 선택하게 한다. 묶음에는 같은 구간 안에서 연속된 두 문장 이상을 넣고 원문과 순서를 유지한다. 한 문장은 한 묶음에 속하며, 해제할 때는 묶음 전체를 푼다.

초안은 입력 도중에도 저장할 수 있다. 발행할 때는 제목, 구간명과 문장이 비어 있는지, 제한 시간이 양의 정수인지 확인한다. 체크포인트의 문장 위치와 누적 목표 시간은 각각 앞에서 뒤로 엄격히 증가해야 한다. 마지막 체크포인트는 마지막 문장 끝과 전체 제한 시간에 고정한다. 실행은 시작 당시 버전을 참조하므로, 결과 화면에서 목표를 수정해도 과거 리허설의 비교 기준은 보존된다.

```text
publish(uid, draft, expectedEditVersion):
    validate(draft)
    transaction:
        project = loadOwnedProject(uid, draft.projectId)
        require project.editVersion == expectedEditVersion
        version = insertImmutableVersion(project, draft)
        setPublishedVersion(project, version.id)
        increment(project.editVersion)
        clearPublishedDraft(project)

startRun(uid, versionId, mode, control):
    plan = loadOwnedPublishedVersion(uid, versionId)
    run = newRun(newId(), plan.id, mode, control, monotonicNow())
    saveForOwner(uid, run)
    return run
```

| 실행 중 관리할 값 | 용도 |
| --- | --- |
| 실행 ID, 계획 버전, 추적 세대 | 재시작한 실행과 수동 조작 전의 응답을 구별한다. 추적 세대는 추적 기준을 바꿀 때 증가시키는 번호다. |
| 현재 문장, 단어 커서, 화면에 보이는 위치 | 현재 문장은 완료를 기다리는 대상이다. 커서는 인식된 대본 단어의 원문 범위다. 사용자가 스크롤하면 보는 위치만 바뀐다. |
| 읽은 문장, 유효 통과, 생략 및 복원 이력 | 확정한 진행과 실전에서 줄인 분량을 저장한다. 처리한 조작 ID를 이용해 중복 적용을 막는다. |
| 전체 경과, 일시정지 제외 경과 | 같은 단조 시계를 기준으로 계산한다. 모든 시간 간격은 밀리초로 저장한다. |

## 마이크 연결과 발화 구간

준비 화면에는 사용할 계획 버전과 제한 시간, 마이크 음성이 전사 서비스로 전송된다는 점을 표시한다. 수동 시작을 선택하면 즉시 로컬 실행을 만든다. 음성 시작을 선택하면 마이크 권한을 받은 뒤 Firebase ID 토큰으로 FastAPI에 단기 키를 요청한다. ID 토큰은 FastAPI에, 단기 키는 OpenAI에 보낸다. 앱은 어느 요청에도 대본을 넣지 않는다. WebRTC의 오디오 트랙으로 마이크를 전송하고 `oai-events` 데이터 채널에서 전사를 받는다(OpenAI, n.d.-b).

```text
openVoice(plan, mode, choice):
    if choice == MANUAL: return startRun(currentUid, plan.id, mode, MANUAL)
    mic = requestMicrophone()
    # 취소, 거부, 연결 실패 시 확보한 트랙과 연결을 닫고 수동 시작을 제공한다.
    require issuanceAllowedNow(currentUid)
    for forceRefresh in [false, true]:
        token = firebaseUser.getIdToken(forceRefresh)
        reply = POST(backend + "/v1/realtime/client-secrets", bearer=token, body=none)
        if reply.status == 401 and not forceRefresh: continue
        if reply.status == 429: blockIssuanceFor(currentUid, reply.header("Retry-After"), SECONDS)
        requireSuccessOrHandleAsBelow(reply)
        break
    require reply.client_secret is nonempty and reply.expires_at > unixSecondsNow()
    pc = newPeerConnection()
    pc.addAudioTrack(mic)
    events = pc.createDataChannel("oai-events")
    installEventAndDisconnectHandlers(events, pc)
    offer = pc.createOffer()
    pc.setLocalDescription(offer)
    answer = POST("https://api.openai.com/v1/realtime/calls", bearer=reply.client_secret,
                  contentType="application/sdp", body=offer.sdp)
    pc.setRemoteDescription(type="answer", sdp=answer.successfulBody)
    waitUntilOpen(events)
    startNewRunOrResumeExistingRunInSearchMode()
```

단기 키의 `expires_at`은 연결을 새로 열 수 있는 기한이며 Unix 초 단위다. 서버는 발급 시점부터 60초를 요청한다. 연결이 열리면 만료 시각에 맞춰 세션을 다시 열 필요가 없다. 재연결할 때는 새 단기 키를 받는다. 첫 요청의 401은 토큰을 강제 갱신해 한 번 재시도하고, 두 번째 401은 재로그인을 안내한다. 429는 `Retry-After`초가 지난 뒤 발급할 수 있다. 503의 `temporarily_unavailable`은 일시 장애로 처리한다. `transcription_unavailable`이면 수동 진행을 제공한다.

서버는 `gpt-live-transcribe`에 한국어, `delay=minimal`, `turn_detection=null`을 설정한다. 앱은 오디오를 계속 보내면서 발화의 경계를 감지하면 `input_audio_buffer.commit`을 보내 그 구간을 마감한다. 부분 전사는 마감 전에 도착할 수 있다(OpenAI, n.d.-a). 아래 수치는 데스크톱 테스트베드에서 사용한 초기값이다. `RMS`는 오디오 샘플의 제곱평균제곱근이며 여기서는 마이크 입력의 크기를 판별하는 데 쓴다.

```text
every 50 ms while dataChannel is open:
    voiced = rms(audioSamples) > 0.015
    if voiced:
        firstVoice = firstVoice ?? monotonicNow()
        lastVoice = monotonicNow()
        observedActive = run.activeMs
        voicedFrames += 1
    if firstVoice is absent: continue
    age = monotonicNow() - firstVoice
    silence = monotonicNow() - lastVoice
    if silence >= 320 or (age >= 2400 and silence >= 180) or age >= 4200:
        if voicedFrames >= 3:
            enqueueSentCommit(observedActive, monotonicNow())
            send("input_audio_buffer.commit")
        resetVoiceWindow()
```

## 전사 순서와 시각 복원

하나의 발화 구간에는 `item_id`가 붙는다. `delta`는 추가된 전사 조각이고 `completed.transcript`는 해당 구간의 완료 전문이다. 같은 구간의 조각을 이어 붙이면서 임시 단어를 표시한다. 완료 전문이 오면 그 텍스트로 다시 판단한다. 앞서 표시한 단어와 달라질 수 있기 때문이다. 발화 순서는 `committed.previous_item_id`로 연결하고, 발화의 관측 시각은 앱이 해당 확정 요청을 보내기 직전에 마지막으로 소리를 감지한 시각에 대응시킨다.

```text
onMessage(message, connectionRunId, connectionGeneration):
    if run or generation changed, or tracking stopped or paused: return
    item = message.item_id
    if item is missing or item already delivered: return
    if message is delta and (event_id is absent or new for this item):
        remember event_id for this item if present
        buffer[item].partial += message.delta
    if message is committed and item has no previous link yet:
        buffer[item].previous = message.previous_item_id  # 첫 발화는 null
        buffer[item].observed = popOldestSentCommit()?.observedActive
    if message is completed:
        buffer[item].final = message.transcript           # 빈 문자열도 도착한 완료다
    while an item has previous == lastDelivered and final is present:
        markDeliveredAndAdvanceChain(itemFound)            # 수신 순서용 집합
        evaluateFinal(itemFound.final, itemFound.id, itemFound.observed)
    showPartialOfNextPendingItemIfChanged()
```

연결 직후 `lastDelivered`는 `null`이다. 아직 `committed`가 오지 않은 항목은 선행 관계 미상으로 관리해 첫 발화와 구별한다. 첫 부분 전사는 바로 표시할 수 있다. 두 발화가 겹치면 선행 항목의 완료를 처리한 뒤 후행 항목의 누적 조각을 표시한다. 완료가 역순으로 도착하거나 같은 완료가 재전송돼도 진행은 한 번만 바뀐다. 전사와 조각 버퍼, 단어 커서는 연결 동안 메모리에만 둔다.

## 인식된 단어의 표시와 위치 복귀

대본을 열 때 문자, 숫자, 결합 문자의 연속을 어절로 나눈다. 각 어절에 문장 번호와 어절 번호, 원문에서의 시작과 끝 위치를 보관한다. 비교할 때는 유니코드 NFKC 정규화와 소문자화를 적용하고 공백과 문장부호를 제거한다. 화면에는 보관한 원문 범위로 밑줄을 긋는다. 대본이 ‘도서관에서’이고 전사에 ‘도서관에’까지만 왔다면 대기한다. 같은 발화가 ‘서울’에서 ‘서울역’으로 이어져 ‘서울’의 위치 근거가 사라지면 임시 밑줄을 거둔다.

```text
locateWord(partialOrFinalText):
    heard = words(partialOrFinalText)
    context = lastConfirmedWords(upTo=4) + heard
    stalled = activeMs - lastForwardCursorChange
    reach = remainingScript if stalled >= 8000 else (12 if stalled >= 3500 else 4)
    for each script word in searchWindow(reach):
        compare suffixes of 1 to 4 words ending at this word with suffixes of context
        require an exact joined suffix on both sides ending at a word boundary
        score surrounding words by similarity, favoring longer matching context
        outside the first 4 lines require >= 3 words, >= 8 characters and similarity >= 0.86
    candidate = bestUnambiguousMatch()
    if no candidate:
        retry with up to 2 trailing heard words removed if the tail is partial or misrecognized
    if candidate exists: return its original text range
    if this is a partial and the existing draft cursor still matches this item: retain it
    else: return the last confirmed word range, if any
```

문맥 비교는 띄어쓰기가 달라지거나 단어가 두 발화에 나뉘어 도착하는 경우를 처리한다. 현재 커서 근처에서는 온전한 한 어절로 이동할 수 있다. 멀리 있는 단어와 대조할 때는 여러 어절이 함께 맞아야 한다. 테스트베드는 문맥 유사도에 `0.075 × (비교 어절 수 − 1)`을 더한다. 상위 두 후보의 점수 차가 0.055 미만이면 보류한다. 같은 문장 안에서 한 어절이 반복되고 가까운 쪽의 점수가 더 낮지 않을 때는 그 위치를 택할 수 있다. 같은 단어를 여러 번 받아도 커서가 앞으로 움직인 시각은 갱신하지 않는다. 발표자가 한두 문장을 건너뛰거나 더 멀리 이동했을 때 탐색 범위를 넓힐 수 있는 이유다.

## 문장 완료와 체크포인트 기록

부분 전사로 바뀌는 값은 화면의 커서와 후보 문장이다. 읽기와 체크포인트 통과는 완료 전사가 문장 끝을 충분히 뒷받침하거나 사용자가 직접 확인했을 때 기록한다. 예를 들어 1번을 읽은 뒤 4번으로 건너뛰면 4번의 단어에 밑줄을 긋고, 4번의 끝이 확인됐을 때 그 문장을 읽기로 기록한다. 2번과 3번의 체크포인트는 미확인으로 남는다.

```text
evaluateFinal(text, operationId, observedActive):
    if operation already evaluated, stale generation or observation older than the last accepted: return
    if a boundary between the current sentence and the next is clear in text:
        split at that boundary
        evaluateFinal(before, derivedId(operationId, currentSentenceId), absent)
        evaluateFinal(after, operationId, observedActive)
        return
    markOperationEvaluated(operationId)                   # 완료 판단용 집합
    word = locateWord(text)
    target = findUnambiguousSentenceEnding(text, priorFinalCandidates)
    if no target and confirmedWordContextSupportsTheSentenceEnd(word): target = word.line
    if no target: retain supported word, wait for more speech or manual input, return
    run.control = AUTO
    setCurrentSentence(target)
    if target belongs to a pending omission: restoreItsGroupAsSpoken()
    completeSentenceOnce(target, operationId, source=AUTO, observedActive)
```

완료 전문이 대본 한 문장 전체로 끝나면 일치 후보로 삼는다. 정규화한 원문이 10자 이상이고 전사가 문장 끝에서 원문의 78% 이상과 일치하는 경우도 후보에 넣는다. 반복 문장이면 직전 완료 후보 바로 뒤에 오는 위치가 하나인지 확인한다. 오인식이 섞이면 원문과 전사가 각각 12자 이상일 때 편집 거리로 비교한다. 유사도는 `1 − 편집 거리 / 두 문자열 중 긴 길이`이며 0.80 이상, 차순위와의 차이는 0.10 이상이어야 한다. 끝 세 글자가 맞는지도 확인한다. 이 경로로 다른 문장에 건너갈 때는 직전 완료 전사가 그 앞 문장을 가리켰다는 근거를 요구한다. 점수는 앱이 대본 위치를 고르는 기준이다.

짧게 끊긴 발화는 어절 문맥으로도 마칠 수 있다. 같은 문장에서 앞서 확정한 어절에서 끝말까지 이어졌거나 여러 어절이 문장 끝을 한 위치로 가리킬 때 사용한다. 중간 어절을 틀려도 마지막 어절과 그 근처에 실제로 들어온 앞 어절이 맞으면 끝을 확인할 수 있다. 완료 전문 하나에 두 문장이 들어온 경우에는 앞 문장 끝과 다음 문장 시작을 함께 확인해 나눈다. 마지막 소리를 관측한 시각은 뒤쪽 조각에만 부여하고, 앞쪽 조각의 통과 시각은 `estimated`로 구분한다.

## 수동 진행과 연결 복구

| 조작 또는 사건 | 앱의 처리 |
| --- | --- |
| 대본 스크롤, 현재 위치로 보기 | 보이는 범위를 바꾼다. 사용자가 탐색하는 동안 자동 스크롤을 멈춘다. |
| 여기부터 이어가기 | 선택한 문장을 현재 위치로 지정하고 추적 세대를 바꾼다. 중간에 있는 체크포인트는 미확인으로 유지한다. |
| 다음 문장 | 누르는 순간의 대상 문장, 세대, 조작 ID와 시각을 고정한다. 같은 입력은 한 번만 현재 문장을 완료하고 다음 문장으로 이동시킨다. |
| 체크포인트 통과 확인 | 리허설에 표시한 체크포인트 한 곳에 누른 시각을 기록한다. 해당 실행에 이미 유효한 통과가 있으면 유지한다. |
| 연결 단절 또는 응답 지연 | 대본과 시계를 유지한 채 음성 자원을 닫는다. 일시 장애는 지연 후 새 키로 연결하고 현재 위치를 다시 찾는다. |
| 마이크 권한 거부, 입력 중단, 명시적 수동 전환 | 수동 진행을 제공한다. 사용자가 음성을 다시 선택하면 권한과 연결을 확인한다. |

테스트베드는 보낸 확정 요청이나 그에 대한 완료가 15초 넘게 밀리면 일시 장애로 처리한다. 진행 중 일시 장애는 1초, 2초, 4초 간격으로 최대 세 번 재연결한다. 첫 세션 종료는 재연결할 수 있고 반복 종료는 수동으로 전환한다. 사용자가 직접 수동 모드를 택하면 예약한 재연결도 취소한다. 새 연결과 수동 위치 지정, 일시정지, 종료 시에는 세대를 바꾼다. 새 연결에는 이전 연결의 단어 문맥과 발화 버퍼를 이어 붙이지 않는다.

## 실전의 생략 판단과 복원

앱은 같은 계획 버전에서 일시정지 없이 완료한 최근 리허설의 구간 소요 시간을 사용한다. 유효한 관측이 없는 구간은 계획의 배정 시간을 쓴다. 구간 안에서는 문장 길이에 비례해 예상 시간을 나눈다. 실전에서 남은 원고를 말하는 데 걸릴 예상 시간이 실제 남은 시간보다 15초 넘게 길면, 앞으로 읽을 문장에서 줄일 분량을 계산한다. 자동 위치가 보류되거나 관측이 3초 넘게 늦었거나 수신 시각으로 추정한 경우에는 그 응답으로 새 생략을 결정하지 않는다.

```text
deficit = sum(duration of remaining nonomitted sentences) - (limitMs - activeMs)
if mode == LIVE and control in {AUTO, MANUAL} and current observation is usable:
    if deficit > 15000:
        protect past, current, read and restored sentences and all their groups
        rank each remaining group by its highest importance
        for unit in supplementary units followed by normal units:
            if deficit <= 0: break
            markWholeUnitGray(unit)
            appendOmissionEvent(unit, estimatedSavingMs)
            deficit -= estimatedSavingMs
        showRemainingOverrun(max(0, deficit))
```

생략 단위는 묶음 또는 묶음에 속하지 않은 한 문장이다. 필수 문장이 든 묶음은 보호한다. 회색 문장을 탭하면 묶음 전체를 복원하고 해당 실행에서 다시 자동 생략되지 않도록 한다. 자동 추적에서는 회색 묶음을 건너간 시점에 생략을 대기 상태로 둔다. 발표자가 그 내용을 실제로 읽으면 발화에 따른 복원으로 기록한다. 뒤 문장을 확인하면 생략이 확정된다. 수동으로 다음 문장에 진행했을 때는 지나간 회색 묶음의 생략을 바로 기록한다. 생략으로 절약할 예상 시간과 관측한 통과 시각은 서로 다른 필드에 둔다.

## 기록 저장과 리허설 결과

발표 시계는 단조 시계의 차이로 계산한다. 전체 경과에는 사용자가 명시한 리허설 일시정지도 포함한다. 목표와 비교할 때는 여기서 일시정지 시간을 뺀다. 연결 단절, 질의응답, 제한 시간 초과 중에도 시계는 계속 간다. 처음부터 다시 연습하면 기존 실행을 중단으로 저장하고 새 실행 ID를 발급한다. 종료를 취소하면 기존 실행을 계속한다.

```text
completeSentenceOnce(target, operationId, source, observedActive):
    require current run, generation, control and target still match
    applyOnceInMemory(operationId):
        markRead(target)
        if target ends a checkpoint with no valid pass:
            at = clamp(observedActive ?? activeMs, 0, activeMs)
            evidence = ESTIMATED if source == AUTO and observedActive is absent else source
            appendPass(checkpointId, at, evidence, operationId)
        advanceToNextEligibleSentence()
    enqueueOrderedWrite(ownerUid, runId, eventsWithStableIds)

flush(queueEntry):
    transaction:
        require run belongs to queueEntry.ownerUid
        skip operations already committed for this run
        write events once by ID, enforcing unique (runId, sequence)
        update run position and at most one effective pass per (runId, checkpointId)
    on failure: retain queueEntry and show pending save
    on retry: use the same run ID, event IDs and sequence
```

저장이 지연돼도 현재 실행 화면은 메모리의 진행을 사용한다. 종료 저장에 실패하면 같은 실행을 열린 상태로 두고 저장을 재시도한다. 사용자가 통과 시각을 보정하거나 무효화하면 원본 ID를 참조하는 새 이벤트를 추가한다. 앱을 다시 열 때는 계정의 저장된 이력을 읽고 다음 실행을 새로 시작한다. 마이크 원음, 전사 전문과 단기 키는 실행 기록에 저장하지 않는다.

구간 배정 시간은 현재 누적 목표에서 앞 누적 목표를 뺀 값이다. 구간의 실제 소요 시간은 같은 방식으로 유효한 두 통과 시각의 차이를 구하며, 첫 구간의 시작은 0초다. 미확인, 수신 시각 추정, 순서가 뒤집힌 관측은 개별 구간 계산에서 제외한다. 가운데 통과가 비면 양쪽의 유효한 지점 사이 합계만 표시한다. 결과에서 관측을 새 계획에 적용할 때는 현재 버전과 관측 위치가 같은지 확인한다. 예를 들어 앞 누적 목표가 1분 20초이고 이번 구간의 실제 소요가 1분 45초라면 새 누적 목표는 3분 5초다. 저장 전에 인접 구간의 배정 변화도 보여준다.

## 인수 시험

| 재현 조건 | 기대 결과 |
| --- | --- |
| ‘도서관에’가 먼저 도착하고 ‘서’가 뒤늦게 도착 | ‘도서관에서’는 마지막 음절이 전사에 들어왔을 때 표시된다. 부분 전사 중 통과는 0건이다. |
| 1번 다음에 3번, 이어서 6번 낭독 | 실제 발화한 문장에서 커서가 복귀한다. 건너간 문장을 읽기로 기록하거나 통과 시각을 채우지 않는다. |
| 발화 B가 A보다 먼저 완료된 뒤 A의 완료와 B의 중복 완료가 도착 | 선행 관계에 따라 A, B 순서로 한 번씩 처리한다. |
| 수동 위치 지정 직후 이전 연결의 완료가 도착 | 사용자가 지정한 위치와 체크포인트 기록이 유지된다. 수동 시작에서는 키 발급 요청이 0건이다. |
| 필수 문장이 든 묶음과 보충 묶음이 남은 실전에서 15초 초과 부족 | 보충 묶음부터 생략한다. 회색 문장을 탭하면 묶음 전체가 복원된다. |
| 저장 실패 중 다음 문장을 확인하고 같은 조작을 다시 저장 | 시계와 수동 진행을 사용할 수 있고, 복구 후 유효 통과는 한 건이다. 다른 UID로는 해당 실행을 조회할 수 없다. |

2026년 11월 21일 데스크톱 Chromium에서 한국어 합성 음성 30어절을 독립된 WebRTC 연결로 세 차례 재생했다. 90회 중 원문과 일치해 표시된 85회의 첫소리부터 화면 표시까지 중앙값은 834ms, 끝소리부터 표시까지는 318ms였다. 끝소리 기준 0.5초 미만은 전체 90회 중 82회였다. 별도의 한국어 낭독 녹음에서는 한 문장과 두 문장을 건너뛴 뒤 모두 바로 다음에 읽은 문장에서 복귀했다. Android의 물리 마이크, 통화로 인한 입력 중단과 복귀, 앱 전환 중 시계는 실기기 인수 시험에 남아 있다.

## 참고문헌

OpenAI. (n.d.-a). *Realtime transcription*. [OpenAI API 문서](https://developers.openai.com/api/docs/guides/realtime-transcription)

OpenAI. (n.d.-b). *WebRTC*. [OpenAI API 문서](https://developers.openai.com/api/docs/guides/voice-webrtc?voice-api=realtime)

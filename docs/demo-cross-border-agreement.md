# Cross-border collaboration agreement

## Evidence boundary

- **Actual project background (participant attestation):** the project lead worked from Toronto while collaborating asynchronously with developers in Korea.
- **Demo reenactment:** the five-minute demo represents a Seoul PM and a Toronto developer as roles. It does not claim that two people are connected live during the recording.
- **Verifiable product evidence:** GitHub commits, pull requests, Actions runs, AI findings, handshakes, and overrides created during the demo are real records produced by the deployed system.

## Shared context

| Field | Seoul PM role | Toronto developer role |
| --- | --- | --- |
| Time zone | Asia/Seoul, UTC+9 | America/Toronto, UTC-4 during EDT |
| Working language | Korean | English |
| Primary responsibility | Product intent and acceptance criteria | Implementation and operational debugging |
| Handoff preference | State the decision, reason, owner, and due time | Include reproducible evidence and unresolved questions |

The roles above are participant-declared working context. The system must not infer communication preferences or ability from nationality or location.

## Accepted decision: privacy-safe debugging

- Status: Active
- Owner: Project team
- Scope: Logs, analytics, and AI-assisted debugging
- Reason: Cross-organizational debugging needs shared evidence, but raw messages may contain personal or confidential information.

> 원문 사용자 메시지는 외부 분석 서비스에 저장하지 않는다. 디버깅에는 익명화된 집계 지표와 재현 가능한 오류 코드만 사용한다.

English working translation: Do not store raw user messages in external analytics services. Use anonymized aggregate metrics and reproducible error codes for debugging.

## Asynchronous collaboration protocol

1. A contributor records the proposed change in a GitHub pull request in their preferred working language.
2. Alignment Memory compares the proposal with active, evidence-linked decisions rather than relying on who happens to be online.
3. The reviewer receives a Context Passport containing the decision, rationale, time-zone context, and source link in their selected language.
4. The reviewer records `agree`, `needs clarification`, or `disagree` through a Handshake. The response remains attached to the analysis instead of disappearing in chat.
5. If the team intentionally changes direction, an authorized member records an Override with a reason; the earlier evidence remains append-only.

## Demo acceptance criteria

- An English PR proposing raw-message logging must be flagged against the Korean decision above.
- The finding must cite an exact quote and GitHub source URL.
- A Korean PM role and an English Toronto developer role must be able to inspect the same issue without sharing a time zone or language.
- A human Handshake must be visible in the collaboration history before the demo ends.

## Live verification record

- Verification date: 2026-08-20 (America/Toronto)
- Baseline sync status: Pending until both the Analyze and Publish GitHub Actions complete successfully.
- Evidence rule: Every AI-generated quote must match text in its stored source exactly; an unsupported paraphrase must be rejected before publication.
- Resilience rule: If an AI response references a node that was not persisted, ignore only that unresolved edge and preserve the remaining verified knowledge instead of failing the whole synchronization.
- Failure audit: Analyze runs `32410973909` and `32411118467` were rejected before publication; they remain visible and must not be described as successful live proof.
- Recording rule: A pending or failed run must never be presented as a live success. Keep the run URL and the corrective pull request as part of the collaboration history.
- Role-play rule: Label the Seoul PM and Toronto developer views as a reenactment throughout the recording.

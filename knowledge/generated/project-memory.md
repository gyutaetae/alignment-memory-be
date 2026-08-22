# Generated Project Memory

> Generated deterministically from validated evidence. Edit source records, not this file.

- Repository: `gyutaetae/alignment-memory-be`
- Knowledge revision: `8`
- Source head: `5222ca926abf431032c2c99f5e3abf4c8ba81464`

## Contents

- [[project-memory#decision:no-raw-message-logging|No raw message logging in external analytics]]
- [[project-memory#goal:privacy-safe-collaboration|Privacy-safe collaboration]]
- [[project-memory#Do not store raw user messages in external analytics|Do not store raw user messages in external analytics]]

## Goals

### goal:privacy-safe-collaboration

- Title: Privacy-safe collaboration
- Type: `goal`
- Status: `active`
- Summary: Ensure privacy by not storing raw user messages in external analytics and using only anonymized data for debugging.
- Evidence:
  - [원문 사용자 메시지는 외부 분석 서비스에 저장하지 않는다.](https://github.com/gyutaetae/alignment-memory-be/pull/2.diff) (`603d0d72-375d-54b1-bde7-35760e91f02b`)


## Requirements

### Do not store raw user messages in external analytics

- Title: Do not store raw user messages in external analytics
- Type: `requirement`
- Status: `active`
- Summary: Raw user messages must not be stored in external analytics services.
- Evidence:
  - [Do not store raw user messages in external analytics services. Use anonymized aggregate metrics and reproducible error codes for debugging.](https://github.com/gyutaetae/alignment-memory-be/blob/main/docs/demo-cross-border-agreement.md) (`f812a9eb-bcbd-57b5-91cf-41bcbc469ba1`)
  - [원문 사용자 메시지는 외부 분석 서비스에 저장하지 않는다.](https://github.com/gyutaetae/alignment-memory-be/pull/2.diff) (`603d0d72-375d-54b1-bde7-35760e91f02b`)


## Decisions

### decision:no-raw-message-logging

- Title: No raw message logging in external analytics
- Type: `decision`
- Status: `active`
- Summary: Do not store raw user messages in external analytics services; use only anonymized metrics and error codes for debugging.
- Evidence:
  - [> 원문 사용자 메시지는 외부 분석 서비스에 저장하지 않는다. 디버깅에는 익명화된 집계 지표와 재현 가능한 오류 코드만 사용한다.](https://github.com/gyutaetae/alignment-memory-be/blob/main/docs/demo-cross-border-agreement.md) (`f812a9eb-bcbd-57b5-91cf-41bcbc469ba1`)

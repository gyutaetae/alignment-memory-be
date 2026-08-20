# Generated Project Memory

> Generated deterministically from validated evidence. Edit source records, not this file.

- Repository: `gyutaetae/alignment-memory-be`
- Knowledge revision: `6`
- Source head: `7c48e9a4db1a8ce1a62891be4cbeda55a8ab2a71`

## Contents

- [[project-memory#ADR-017|Narrow MVP boundary]]
- [[project-memory#exclude-browser-extension|Exclude browser extension and external ingestion]]
- [[project-memory#Do not store raw user messages in external analytics|Do not store raw user messages in external analytics]]

## Requirements

### Do not store raw user messages in external analytics

- Title: Do not store raw user messages in external analytics
- Type: `requirement`
- Status: `active`
- Summary: "원문 사용자 메시지는 외부 분석 서비스에 저장하지 않는다."
- Evidence:
  - [> 원문 사용자 메시지는 외부 분석 서비스에 저장하지 않는다. 디버깅에는 익명화된 집계 지표와 재현 가능한 오류 코드만 사용한다.

English working translation: Do not store raw user messages in external analytics services. Use anonymized aggregate metrics and reproducible error codes for debugging.](https://github.com/gyutaetae/alignment-memory-be/blob/main/docs/demo-cross-border-agreement.md) (`f812a9eb-bcbd-57b5-91cf-41bcbc469ba1`)
  - ["원문 사용자 메시지는 외부 분석 서비스에 저장하지 않는다."](https://github.com/gyutaetae/alignment-memory-be/pull/2.diff) (`603d0d72-375d-54b1-bde7-35760e91f02b`)


## Decisions

### ADR-017

- Title: Narrow MVP boundary
- Type: `decision`
- Status: `active`
- Summary: Decision: The MVP boundary is one public GitHub repository with native integration only.
- Evidence:
  - [Decision: The MVP boundary is one public GitHub repository with native integration only.](https://github.com/gyutaetae/alignment-memory-be/blob/main/docs/adr.md) (`51516476-870f-4f9a-92b2-699107892b8d`)

### exclude-browser-extension

- Title: Exclude browser extension and external ingestion
- Type: `decision`
- Status: `active`
- Summary: Browser extension; Slack, Notion, Figma, or Agora ingestion are Non-goals.
- Evidence:
  - [Browser extension; Slack, Notion, Figma, or Agora ingestion are Non-goals.](https://github.com/gyutaetae/alignment-memory-be/blob/main/docs/prd.md) (`8ea10c2f-0ee5-4f2b-aa61-4602c5bc2b12`)

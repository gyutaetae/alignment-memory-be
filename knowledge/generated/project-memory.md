# Generated Project Memory

> Generated deterministically from validated evidence. Edit source records, not this file.

- Repository: `gyutaetae/alignment-memory-be`
- Knowledge revision: `4`
- Source head: `1c9e43c34433c346dc7a3310c1061f99b38ca0d5`

## Contents

- [[project-memory#ADR-017|Narrow MVP boundary]]
- [[project-memory#Privacy-Decision|Privacy decision on user message storage]]
- [[project-memory#PRD-Non-goals|Non-goals]]

## Requirements

### PRD-Non-goals

- Title: Non-goals
- Type: `requirement`
- Status: `active`
- Summary: Browser extension; Slack, Notion, Figma, or Agora ingestion are Non-goals.
- Evidence:
  - [Browser extension; Slack, Notion, Figma, or Agora ingestion are Non-goals.](https://github.com/gyutaetae/alignment-memory-be/blob/main/docs/prd.md) (`8ea10c2f-0ee5-4f2b-aa61-4602c5bc2b12`)


## Decisions

### ADR-017

- Title: Narrow MVP boundary
- Type: `decision`
- Status: `active`
- Summary: Decision: one public repository, desktop web, invited collaborators, and three results: Aligned, Missing Alignment, Direct Conflict. Excluded: browser extension; Slack/Notion/Figma/Agora ingestion; private/multi-repo; external forks; Stale Reference; whole-code ingestion. Intent: a complete, deployed, evidence-rich vertical slice is the winning portfolio artifact.
- Evidence:
  - [Decision: The MVP boundary is one public GitHub repository with native integration only.](https://github.com/gyutaetae/alignment-memory-be/blob/main/docs/adr.md) (`51516476-870f-4f9a-92b2-699107892b8d`)

### Privacy-Decision

- Title: Privacy decision on user message storage
- Type: `decision`
- Status: `active`
- Summary: Do not store raw user messages in external analytics services. Use anonymized aggregate metrics and reproducible error codes for debugging.
- Evidence:
  - [> 원문 사용자 메시지는 외부 분석 서비스에 저장하지 않는다. 디버깅에는 익명화된 집계 지표와 재현 가능한 오류 코드만 사용한다.

English working translation: Do not store raw user messages in external analytics services. Use anonymized aggregate metrics and reproducible error codes for debugging.](https://github.com/gyutaetae/alignment-memory-be/blob/main/docs/demo-cross-border-agreement.md) (`f812a9eb-bcbd-57b5-91cf-41bcbc469ba1`)

# Generated Project Memory

> Generated deterministically from validated evidence. Edit source records, not this file.

- Repository: `gyutaetae/alignment-memory-be`
- Knowledge revision: `5`
- Source head: `c7607fb83cc1433debe99bc03f3f36768144107e`

## Contents

- [[project-memory#decision:mvp-boundary|MVP Boundary Decision]]
- [[project-memory#decision:privacy-safe-debugging|Privacy-Safe Debugging Decision]]
- [[project-memory#requirement:non-goals|Non-goals for MVP]]

## Requirements

### requirement:non-goals

- Title: Non-goals for MVP
- Type: `requirement`
- Status: `active`
- Summary: Browser extension; Slack, Notion, Figma, or Agora ingestion are Non-goals.
- Evidence:
  - [Browser extension; Slack, Notion, Figma, or Agora ingestion are Non-goals.](https://github.com/gyutaetae/alignment-memory-be/blob/main/docs/prd.md) (`8ea10c2f-0ee5-4f2b-aa61-4602c5bc2b12`)


## Decisions

### decision:mvp-boundary

- Title: MVP Boundary Decision
- Type: `decision`
- Status: `active`
- Summary: The MVP boundary is one public GitHub repository with native integration only.
- Evidence:
  - [Decision: The MVP boundary is one public GitHub repository with native integration only.](https://github.com/gyutaetae/alignment-memory-be/blob/main/docs/adr.md) (`51516476-870f-4f9a-92b2-699107892b8d`)

### decision:privacy-safe-debugging

- Title: Privacy-Safe Debugging Decision
- Type: `decision`
- Status: `active`
- Summary: Do not store raw user messages in external analytics services. Use anonymized aggregate metrics and reproducible error codes for debugging.
- Evidence:
  - [> 원문 사용자 메시지는 외부 분석 서비스에 저장하지 않는다. 디버깅에는 익명화된 집계 지표와 재현 가능한 오류 코드만 사용한다.

English working translation: Do not store raw user messages in external analytics services. Use anonymized aggregate metrics and reproducible error codes for debugging.](https://github.com/gyutaetae/alignment-memory-be/blob/main/docs/demo-cross-border-agreement.md) (`f812a9eb-bcbd-57b5-91cf-41bcbc469ba1`)

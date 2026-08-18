# Data Schema — Alignment Memory

> Authority: data ownership, entities, relations, constraints, and trust boundaries. Event order is in [flow.md](./flow.md).

## Invariants

1. GitHub source versions and human corrections are append-only.
2. Derived knowledge is replaceable; its evidence is not.
3. Every AI claim carries source IDs, model, prompt version, and validation state.
4. AI interpretations are not profile facts.
5. Repository identity uses GitHub numeric IDs, not mutable names.
6. No hard delete of project history in MVP.

## Trust layers

| Layer | Examples | Authority |
|---|---|---|
| Declared | language, timezone, role, communication preference | member-controlled |
| Observed | authored, approved, objected, reviewed | GitHub source evidence |
| Derived | summary, conflict, stakeholder impact | AI; must be validated and cited |
| Corrective | Handshake, Human Override | authenticated human evidence |

## Core entities

### Identity and repository

| Table | Critical fields |
|---|---|
| `profiles` | `id→auth.users`, `githubUserId`, `login`, `preferredLanguage`, `timezone`, `workingHours`, `role`, `ownership[]`, `communicationPreferences` |
| `githubInstallations` | `id`, `githubInstallationId`, `accountId`, `permissions`, `suspendedAt` |
| `repositories` | `id`, `githubRepositoryId`, `installationId`, `owner`, `name`, `defaultBranch`, `baselineCommitSha`, `knowledgeRevision` |
| `repositoryMembers` | `repositoryId`, `profileId`, `githubPermission`, `active` |

Preferences are self-declared. Derived descriptions of personality, culture, or nationality are forbidden.

### Immutable source evidence

| Table | Critical fields |
|---|---|
| `sources` | `id`, `repositoryId`, `sourceType`, `externalId`, `url` |
| `sourceVersions` | `id`, `sourceId`, `externalVersion`, `content`, `contentHash`, `authorProfileId?`, `occurredAt`, `ingestedAt` |

`sourceType ∈ {markdown, issue, pull_request, pull_request_diff, commit}`. Application source files are not collected; only allowed Markdown and metadata/diff content.

### Versioned knowledge graph

| Table | Critical fields |
|---|---|
| `knowledgeNodes` | `id`, `repositoryId`, `nodeType`, `logicalKey`, `currentVersionId` |
| `knowledgeNodeVersions` | `id`, `nodeId`, `revision`, `title`, `summary`, `status`, `createdBy`, `aiRunId?`, `supersedesVersionId?`, `createdAt` |
| `knowledgeEdges` | `id`, `repositoryId`, `fromNodeId`, `toNodeId`, `relationType`, `validFromRevision`, `validToRevision?` |
| `evidenceLinks` | `id`, `targetType`, `targetId`, `sourceVersionId`, `quote`, `relation`, `verified` |

- `nodeType ∈ {goal, requirement, decision, task, artifact, risk}`.
- `status ∈ {active, superseded, disputed}`.
- Stakeholder memory is expressed through evidence-backed edges such as `authored`, `approved`, `objected_to`, and `owns`; it is not a free-text psychological profile.

### Execution and AI provenance

| Table | Critical fields |
|---|---|
| `syncJobs` | `id`, `repositoryId`, `eventType`, `eventKey`, `status`, `headSha?`, `progress`, `errorCode?`, timestamps |
| `aiRuns` | `id`, `jobId`, `provider`, `requestedModel`, `actualModel`, `promptVersion`, `inputHash`, `outputJson`, `validationStatus`, `usage`, `cost?`, timestamps |
| `alignmentAnalyses` | `id`, `repositoryId`, `prNumber`, `headSha`, `knowledgeRevision`, `outcome`, `aiRunId`, timestamps |
| `alignmentFindings` | `id`, `analysisId`, `findingType`, `targetNodeId?`, `explanation`, `recommendedAction`, `validationStatus` |

완료된 분석의 `outcome ∈ {aligned, direct_conflict, missing_alignment}`다. Provider 또는 검증 오류는 Job을 `analysis_failed`로 끝내며 alignment 결과가 아니다. Model confidence alone cannot select `direct_conflict`.

### Borderless and correction records

| Table | Critical fields |
|---|---|
| `contextPassports` | `id`, `analysisId`, `profileId`, `language`, `content`, `sourceVersionIds[]`, `ambiguities[]`, `aiRunId` |
| `handshakes` | `id`, `analysisId`, `profileId`, `response`, `message`, `sourceLanguage`, `createdAt` |
| `humanOverrides` | `id`, `targetType`, `targetId`, `overrideType`, `reason`, `actorProfileId`, `createdNodeVersionId?`, `createdAt` |
| `generatedArtifacts` | `id`, `repositoryId`, `path`, `contentHash`, `blobSha`, `commitSha`, `knowledgeRevision`, `createdAt` |

## Relations

```text
Repository 1─* Source 1─* SourceVersion
Repository 1─* KnowledgeNode 1─* KnowledgeNodeVersion
KnowledgeNode *─* KnowledgeNode through KnowledgeEdge
SourceVersion 1─* EvidenceLink *─1 claim/finding/version
SyncJob 1─* AiRun 1─* AlignmentAnalysis 1─* AlignmentFinding
AlignmentAnalysis 1─* ContextPassport / Handshake / HumanOverride
Profile *─* Repository through RepositoryMember
```

## Required constraints and indexes

- Unique: `githubInstallations.githubInstallationId`.
- Unique: `repositories.githubRepositoryId`.
- Unique: `repositoryMembers(repositoryId, profileId)`.
- Unique: `sources(repositoryId, sourceType, externalId)`.
- Unique: `sourceVersions(sourceId, contentHash)`.
- Unique: `knowledgeNodes(repositoryId, logicalKey)`.
- Unique: `knowledgeNodeVersions(nodeId, revision)`.
- Unique: `syncJobs(repositoryId, eventKey)`.
- Unique: `alignmentAnalyses(repositoryId, prNumber, headSha, knowledgeRevision)`.
- Unique: `generatedArtifacts(repositoryId, path, contentHash)`.
- Index active node status/type, edge endpoints, source occurrence time, job status, PR/head SHA, and evidence target.

## Access rules

- Repository members may read connected repository data.
- Members may create their own Handshake; only allowed collaborators may trigger Sync.
- Override requires repository write permission and an explicit reason.
- Action may call only signed internal job/result endpoints; it never receives a Supabase service-role key.
- RLS must scope every user-facing table through `repositoryMembers`.

## Explicit exclusions

- No vector column, embedding table, Neo4j mirror, or cross-repository identity merge in MVP.
- Do not store raw API credentials, private thoughts, inferred nationality traits, or arbitrary model-generated file paths.

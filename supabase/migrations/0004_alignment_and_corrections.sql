create table if not exists alignment_analyses (
    id uuid primary key default gen_random_uuid(),
    repository_id uuid not null references repositories(id) on delete restrict,
    pr_number integer not null check (pr_number > 0),
    head_sha text not null check (head_sha ~ '^[0-9a-f]{40,64}$'),
    knowledge_revision integer not null check (knowledge_revision >= 0),
    outcome alignment_outcome not null,
    ai_run_id uuid not null unique references ai_runs(id) on delete restrict,
    created_at timestamptz not null default now(),
    unique (repository_id, pr_number, head_sha, knowledge_revision)
);

create table if not exists alignment_findings (
    id uuid primary key default gen_random_uuid(),
    analysis_id uuid not null references alignment_analyses(id) on delete restrict,
    finding_type alignment_outcome not null,
    target_node_id uuid references knowledge_nodes(id) on delete restrict,
    target_node_type node_type,
    target_node_status knowledge_status,
    contradicts boolean not null default false,
    uncertain boolean not null default false,
    explanation text not null check (length(btrim(explanation)) > 0),
    recommended_action text not null check (length(btrim(recommended_action)) > 0),
    validation_status validation_status not null default 'valid',
    created_at timestamptz not null default now(),
    check (
        finding_type <> 'direct_conflict'
        or (
            target_node_id is not null
            and target_node_type in ('goal', 'requirement', 'decision')
            and target_node_status = 'active'
            and contradicts
            and not uncertain
        )
    )
);

create table if not exists context_passports (
    id uuid primary key default gen_random_uuid(),
    analysis_id uuid not null references alignment_analyses(id) on delete restrict,
    profile_id uuid not null references profiles(id) on delete restrict,
    language text not null check (length(btrim(language)) > 0),
    content text not null check (length(btrim(content)) > 0),
    source_version_ids uuid[] not null check (cardinality(source_version_ids) > 0),
    ambiguities text[] not null default '{}',
    ai_run_id uuid not null references ai_runs(id) on delete restrict,
    created_at timestamptz not null default now(),
    unique (analysis_id, profile_id, language, ai_run_id)
);

create table if not exists handshakes (
    id uuid primary key default gen_random_uuid(),
    analysis_id uuid not null references alignment_analyses(id) on delete restrict,
    profile_id uuid not null references profiles(id) on delete restrict,
    response handshake_response not null,
    message text,
    source_language text not null check (length(btrim(source_language)) > 0),
    created_at timestamptz not null default now()
);

create table if not exists human_overrides (
    id uuid primary key default gen_random_uuid(),
    target_type text not null check (
        target_type in (
            'alignment', 'finding', 'knowledge_node', 'knowledge_node_version'
        )
    ),
    target_id uuid not null,
    override_type override_type not null,
    reason text not null check (length(btrim(reason)) > 0),
    actor_profile_id uuid not null references profiles(id) on delete restrict,
    created_node_version_id uuid references knowledge_node_versions(id) on delete restrict,
    created_at timestamptz not null default now()
);

create table if not exists generated_artifacts (
    id uuid primary key default gen_random_uuid(),
    repository_id uuid not null references repositories(id) on delete restrict,
    path text not null check (
        path ~ '^knowledge/generated/' and path !~ '(^|/)\.\.(/|$)'
    ),
    content_hash text not null check (length(btrim(content_hash)) > 0),
    blob_sha text not null check (blob_sha ~ '^[0-9a-f]{40,64}$'),
    commit_sha text not null check (commit_sha ~ '^[0-9a-f]{40,64}$'),
    knowledge_revision integer not null check (knowledge_revision >= 0),
    created_at timestamptz not null default now(),
    unique (repository_id, path, content_hash)
);

create index if not exists alignment_analyses_pr_head_idx
    on alignment_analyses (repository_id, pr_number, head_sha);

create index if not exists alignment_findings_analysis_idx
    on alignment_findings (analysis_id, finding_type);

create index if not exists context_passports_analysis_idx
    on context_passports (analysis_id, profile_id);

create index if not exists handshakes_analysis_idx
    on handshakes (analysis_id, profile_id, created_at);

create index if not exists human_overrides_target_idx
    on human_overrides (target_type, target_id, created_at);

drop trigger if exists alignment_analyses_append_only on alignment_analyses;
create trigger alignment_analyses_append_only
before update or delete on alignment_analyses
for each row execute function reject_immutable_history_change();

drop trigger if exists alignment_findings_append_only on alignment_findings;
create trigger alignment_findings_append_only
before update or delete on alignment_findings
for each row execute function reject_immutable_history_change();

drop trigger if exists context_passports_append_only on context_passports;
create trigger context_passports_append_only
before update or delete on context_passports
for each row execute function reject_immutable_history_change();

drop trigger if exists handshakes_append_only on handshakes;
create trigger handshakes_append_only
before update or delete on handshakes
for each row execute function reject_immutable_history_change();

drop trigger if exists human_overrides_append_only on human_overrides;
create trigger human_overrides_append_only
before update or delete on human_overrides
for each row execute function reject_immutable_history_change();

drop trigger if exists generated_artifacts_append_only on generated_artifacts;
create trigger generated_artifacts_append_only
before update or delete on generated_artifacts
for each row execute function reject_immutable_history_change();

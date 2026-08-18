create extension if not exists pgcrypto;

do $$
begin
    create type github_permission as enum ('read', 'triage', 'write', 'maintain', 'admin');
exception when duplicate_object then null;
end $$;

do $$
begin
    create type source_type as enum (
        'markdown', 'issue', 'pull_request', 'pull_request_diff', 'commit'
    );
exception when duplicate_object then null;
end $$;

do $$
begin
    create type node_type as enum ('goal', 'requirement', 'decision', 'task', 'artifact', 'risk');
exception when duplicate_object then null;
end $$;

do $$
begin
    create type knowledge_status as enum ('active', 'superseded', 'disputed');
exception when duplicate_object then null;
end $$;

do $$
begin
    create type sync_job_type as enum ('initial_sync', 'pr_analysis', 'merge_publish');
exception when duplicate_object then null;
end $$;

do $$
begin
    create type sync_job_status as enum (
        'queued', 'fetching', 'analyzing', 'validating', 'persisting',
        'writing_github', 'completed', 'failed'
    );
exception when duplicate_object then null;
end $$;

do $$
begin
    create type validation_status as enum ('pending', 'valid', 'invalid');
exception when duplicate_object then null;
end $$;

do $$
begin
    create type alignment_outcome as enum ('aligned', 'direct_conflict', 'missing_alignment');
exception when duplicate_object then null;
end $$;

do $$
begin
    create type evidence_role as enum ('supports', 'contradicts', 'correction');
exception when duplicate_object then null;
end $$;

do $$
begin
    create type handshake_response as enum ('agree', 'needs_clarification', 'disagree');
exception when duplicate_object then null;
end $$;

do $$
begin
    create type override_type as enum (
        'false_positive', 'supersede_decision', 'insufficient_evidence'
    );
exception when duplicate_object then null;
end $$;

create table if not exists profiles (
    id uuid primary key references auth.users(id) on delete restrict,
    github_user_id bigint unique check (github_user_id > 0),
    login text check (login is null or length(btrim(login)) > 0),
    preferred_language text not null default 'en',
    timezone text not null default 'UTC',
    working_hours jsonb not null default '{}'::jsonb,
    role text,
    ownership text[] not null default '{}',
    communication_preferences jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists github_installations (
    id uuid primary key default gen_random_uuid(),
    github_installation_id bigint not null unique check (github_installation_id > 0),
    account_id bigint not null check (account_id > 0),
    permissions jsonb not null default '{}'::jsonb,
    suspended_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists repositories (
    id uuid primary key default gen_random_uuid(),
    github_repository_id bigint not null unique check (github_repository_id > 0),
    installation_id uuid not null references github_installations(id) on delete restrict,
    owner text not null check (length(btrim(owner)) > 0),
    name text not null check (length(btrim(name)) > 0),
    default_branch text not null default 'main' check (length(btrim(default_branch)) > 0),
    baseline_commit_sha text check (
        baseline_commit_sha is null or baseline_commit_sha ~ '^[0-9a-f]{40,64}$'
    ),
    main_commit_sha text check (
        main_commit_sha is null or main_commit_sha ~ '^[0-9a-f]{40,64}$'
    ),
    knowledge_revision integer not null default 0 check (knowledge_revision >= 0),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (owner, name)
);

create table if not exists repository_memberships (
    id uuid primary key default gen_random_uuid(),
    repository_id uuid not null references repositories(id) on delete restrict,
    profile_id uuid not null references profiles(id) on delete restrict,
    github_permission github_permission not null,
    active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (repository_id, profile_id)
);

create table if not exists sources (
    id uuid primary key default gen_random_uuid(),
    repository_id uuid not null references repositories(id) on delete restrict,
    source_type source_type not null,
    external_id text not null check (length(btrim(external_id)) > 0),
    url text not null check (length(btrim(url)) > 0),
    created_at timestamptz not null default now(),
    unique (repository_id, source_type, external_id)
);

create table if not exists source_versions (
    id uuid primary key default gen_random_uuid(),
    source_id uuid not null references sources(id) on delete restrict,
    external_version text not null check (length(btrim(external_version)) > 0),
    content text not null,
    content_hash text not null check (length(btrim(content_hash)) > 0),
    author_profile_id uuid references profiles(id) on delete restrict,
    occurred_at timestamptz not null,
    ingested_at timestamptz not null default now(),
    unique (source_id, content_hash)
);

create index if not exists source_versions_occurred_at_idx
    on source_versions (source_id, occurred_at desc);

create or replace function reject_immutable_history_change()
returns trigger
language plpgsql
as $$
begin
    raise exception '% is append-only', tg_table_name using errcode = '55000';
end;
$$;

drop trigger if exists sources_append_only on sources;
create trigger sources_append_only
before update or delete on sources
for each row execute function reject_immutable_history_change();

drop trigger if exists source_versions_append_only on source_versions;
create trigger source_versions_append_only
before update or delete on source_versions
for each row execute function reject_immutable_history_change();

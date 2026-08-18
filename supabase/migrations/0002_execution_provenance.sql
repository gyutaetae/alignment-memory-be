create table if not exists sync_jobs (
    id uuid primary key default gen_random_uuid(),
    repository_id uuid not null references repositories(id) on delete restrict,
    event_type sync_job_type not null,
    event_key text not null check (length(btrim(event_key)) > 0),
    status sync_job_status not null default 'queued',
    head_sha text check (head_sha is null or head_sha ~ '^[0-9a-f]{40,64}$'),
    progress smallint not null default 0 check (progress between 0 and 100),
    error_code text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    completed_at timestamptz,
    unique (repository_id, event_key),
    check ((status = 'failed') = (error_code is not null)),
    check (status <> 'completed' or progress = 100),
    check ((status in ('completed', 'failed')) = (completed_at is not null))
);

create index if not exists sync_jobs_status_idx
    on sync_jobs (status, created_at);

create index if not exists sync_jobs_head_sha_idx
    on sync_jobs (repository_id, head_sha)
    where head_sha is not null;

create table if not exists ai_runs (
    id uuid primary key default gen_random_uuid(),
    job_id uuid not null references sync_jobs(id) on delete restrict,
    provider text not null check (length(btrim(provider)) > 0),
    requested_model text not null check (length(btrim(requested_model)) > 0),
    actual_model text not null check (length(btrim(actual_model)) > 0),
    prompt_version text not null check (length(btrim(prompt_version)) > 0),
    input_hash text not null check (length(btrim(input_hash)) > 0),
    output_json jsonb not null,
    validation_status validation_status not null,
    usage jsonb not null default '{}'::jsonb,
    cost numeric(18, 8) check (cost is null or cost >= 0),
    created_at timestamptz not null default now(),
    completed_at timestamptz,
    unique (job_id, input_hash, prompt_version)
);

create index if not exists ai_runs_job_idx on ai_runs (job_id, created_at desc);

drop trigger if exists ai_runs_append_only on ai_runs;
create trigger ai_runs_append_only
before update or delete on ai_runs
for each row execute function reject_immutable_history_change();

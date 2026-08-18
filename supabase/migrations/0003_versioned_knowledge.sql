create table if not exists knowledge_nodes (
    id uuid primary key default gen_random_uuid(),
    repository_id uuid not null references repositories(id) on delete restrict,
    node_type node_type not null,
    logical_key text not null check (length(btrim(logical_key)) > 0),
    current_version_id uuid,
    created_at timestamptz not null default now(),
    unique (repository_id, logical_key)
);

create table if not exists knowledge_node_versions (
    id uuid primary key default gen_random_uuid(),
    node_id uuid not null references knowledge_nodes(id) on delete restrict,
    revision integer not null check (revision >= 1),
    title text not null check (length(btrim(title)) > 0),
    summary text not null check (length(btrim(summary)) > 0),
    status knowledge_status not null,
    created_by text not null check (length(btrim(created_by)) > 0),
    ai_run_id uuid references ai_runs(id) on delete restrict,
    supersedes_version_id uuid,
    created_at timestamptz not null default now(),
    unique (node_id, revision),
    unique (node_id, id)
);

do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'knowledge_versions_supersedes_same_node_fk'
    ) then
        alter table knowledge_node_versions
            add constraint knowledge_versions_supersedes_same_node_fk
            foreign key (node_id, supersedes_version_id)
            references knowledge_node_versions(node_id, id)
            on delete restrict
            deferrable initially deferred;
    end if;
end $$;

do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'knowledge_nodes_current_version_same_node_fk'
    ) then
        alter table knowledge_nodes
            add constraint knowledge_nodes_current_version_same_node_fk
            foreign key (id, current_version_id)
            references knowledge_node_versions(node_id, id)
            on delete restrict
            deferrable initially deferred;
    end if;
end $$;

create table if not exists knowledge_edges (
    id uuid primary key default gen_random_uuid(),
    repository_id uuid not null references repositories(id) on delete restrict,
    from_node_id uuid not null references knowledge_nodes(id) on delete restrict,
    to_node_id uuid not null references knowledge_nodes(id) on delete restrict,
    relation_type text not null check (length(btrim(relation_type)) > 0),
    valid_from_revision integer not null check (valid_from_revision >= 1),
    valid_to_revision integer,
    created_at timestamptz not null default now(),
    unique (
        repository_id,
        from_node_id,
        to_node_id,
        relation_type,
        valid_from_revision
    ),
    check (from_node_id <> to_node_id),
    check (valid_to_revision is null or valid_to_revision >= valid_from_revision)
);

create table if not exists evidence_links (
    id uuid primary key default gen_random_uuid(),
    target_type text not null check (
        target_type in (
            'knowledge_node_version', 'knowledge_edge', 'alignment_finding',
            'context_passport'
        )
    ),
    target_id uuid not null,
    source_version_id uuid not null references source_versions(id) on delete restrict,
    quote text not null check (length(btrim(quote)) > 0),
    relation evidence_role not null,
    verified boolean not null default false,
    created_at timestamptz not null default now(),
    unique (target_type, target_id, source_version_id, quote, relation)
);

create index if not exists knowledge_nodes_active_type_idx
    on knowledge_nodes (repository_id, node_type, current_version_id);

create index if not exists knowledge_edges_from_idx
    on knowledge_edges (repository_id, from_node_id, valid_to_revision);

create index if not exists knowledge_edges_to_idx
    on knowledge_edges (repository_id, to_node_id, valid_to_revision);

create index if not exists evidence_links_target_idx
    on evidence_links (target_type, target_id);

create index if not exists evidence_links_source_idx
    on evidence_links (source_version_id);

drop trigger if exists knowledge_node_versions_append_only on knowledge_node_versions;
create trigger knowledge_node_versions_append_only
before update or delete on knowledge_node_versions
for each row execute function reject_immutable_history_change();

drop trigger if exists evidence_links_append_only on evidence_links;
create trigger evidence_links_append_only
before update or delete on evidence_links
for each row execute function reject_immutable_history_change();

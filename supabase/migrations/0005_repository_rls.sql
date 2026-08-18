create or replace function is_repository_member(target_repository_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select exists (
        select 1
        from repository_memberships membership
        where membership.repository_id = target_repository_id
          and membership.profile_id = auth.uid()
          and membership.active
    );
$$;

create or replace function can_write_repository(target_repository_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select exists (
        select 1
        from repository_memberships membership
        where membership.repository_id = target_repository_id
          and membership.profile_id = auth.uid()
          and membership.active
          and membership.github_permission in ('write', 'maintain', 'admin')
    );
$$;

create or replace function shares_repository(target_profile_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select target_profile_id = auth.uid() or exists (
        select 1
        from repository_memberships mine
        join repository_memberships theirs
          on theirs.repository_id = mine.repository_id and theirs.active
        where mine.profile_id = auth.uid()
          and mine.active
          and theirs.profile_id = target_profile_id
    );
$$;

create or replace function can_write_override(target_kind text, target_record_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select case target_kind
        when 'alignment' then exists (
            select 1 from alignment_analyses analysis
            where analysis.id = target_record_id
              and can_write_repository(analysis.repository_id)
        )
        when 'finding' then exists (
            select 1
            from alignment_findings finding
            join alignment_analyses analysis on analysis.id = finding.analysis_id
            where finding.id = target_record_id
              and can_write_repository(analysis.repository_id)
        )
        when 'knowledge_node' then exists (
            select 1 from knowledge_nodes node
            where node.id = target_record_id
              and can_write_repository(node.repository_id)
        )
        when 'knowledge_node_version' then exists (
            select 1
            from knowledge_node_versions version
            join knowledge_nodes node on node.id = version.node_id
            where version.id = target_record_id
              and can_write_repository(node.repository_id)
        )
        else false
    end;
$$;

create or replace function can_read_override(target_kind text, target_record_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select case target_kind
        when 'alignment' then exists (
            select 1 from alignment_analyses analysis
            where analysis.id = target_record_id
              and is_repository_member(analysis.repository_id)
        )
        when 'finding' then exists (
            select 1
            from alignment_findings finding
            join alignment_analyses analysis on analysis.id = finding.analysis_id
            where finding.id = target_record_id
              and is_repository_member(analysis.repository_id)
        )
        when 'knowledge_node' then exists (
            select 1 from knowledge_nodes node
            where node.id = target_record_id
              and is_repository_member(node.repository_id)
        )
        when 'knowledge_node_version' then exists (
            select 1
            from knowledge_node_versions version
            join knowledge_nodes node on node.id = version.node_id
            where version.id = target_record_id
              and is_repository_member(node.repository_id)
        )
        else false
    end;
$$;

comment on function is_repository_member(uuid) is
    'Authenticated user boundary: repository data is visible only to active members.';
comment on function can_write_repository(uuid) is
    'Authenticated write boundary: only active write, maintain, or admin members.';
comment on table sync_jobs is
    'Members may request queued work; only the service_role worker advances state.';
comment on table ai_runs is
    'Worker-owned append-only provenance. The service_role bypasses RLS; user roles are read-only.';

revoke all on function is_repository_member(uuid) from public;
revoke all on function can_write_repository(uuid) from public;
revoke all on function shares_repository(uuid) from public;
revoke all on function can_write_override(text, uuid) from public;
revoke all on function can_read_override(text, uuid) from public;

alter table profiles enable row level security;
alter table github_installations enable row level security;
alter table repositories enable row level security;
alter table repository_memberships enable row level security;
alter table sources enable row level security;
alter table source_versions enable row level security;
alter table sync_jobs enable row level security;
alter table ai_runs enable row level security;
alter table knowledge_nodes enable row level security;
alter table knowledge_node_versions enable row level security;
alter table knowledge_edges enable row level security;
alter table evidence_links enable row level security;
alter table alignment_analyses enable row level security;
alter table alignment_findings enable row level security;
alter table context_passports enable row level security;
alter table handshakes enable row level security;
alter table human_overrides enable row level security;
alter table generated_artifacts enable row level security;

drop policy if exists profiles_member_read on profiles;
create policy profiles_member_read on profiles for select
using (shares_repository(id));

drop policy if exists profiles_self_insert on profiles;
create policy profiles_self_insert on profiles for insert
with check (id = auth.uid());

drop policy if exists profiles_self_update on profiles;
create policy profiles_self_update on profiles for update
using (id = auth.uid()) with check (id = auth.uid());

drop policy if exists installations_member_read on github_installations;
create policy installations_member_read on github_installations for select
using (
    exists (
        select 1 from repositories repository
        where repository.installation_id = github_installations.id
          and is_repository_member(repository.id)
    )
);

drop policy if exists repositories_member_read on repositories;
create policy repositories_member_read on repositories for select
using (is_repository_member(id));

drop policy if exists memberships_member_read on repository_memberships;
create policy memberships_member_read on repository_memberships for select
using (is_repository_member(repository_id));

drop policy if exists sources_member_read on sources;
create policy sources_member_read on sources for select
using (is_repository_member(repository_id));

drop policy if exists source_versions_member_read on source_versions;
create policy source_versions_member_read on source_versions for select
using (
    exists (
        select 1 from sources source
        where source.id = source_versions.source_id
          and is_repository_member(source.repository_id)
    )
);

drop policy if exists sync_jobs_member_read on sync_jobs;
create policy sync_jobs_member_read on sync_jobs for select
using (is_repository_member(repository_id));

drop policy if exists sync_jobs_writer_request on sync_jobs;
create policy sync_jobs_writer_request on sync_jobs for insert
with check (
    can_write_repository(repository_id)
    and status = 'queued'
    and progress = 0
    and error_code is null
    and completed_at is null
);

drop policy if exists ai_runs_member_read on ai_runs;
create policy ai_runs_member_read on ai_runs for select
using (
    exists (
        select 1 from sync_jobs job
        where job.id = ai_runs.job_id
          and is_repository_member(job.repository_id)
    )
);

drop policy if exists knowledge_nodes_member_read on knowledge_nodes;
create policy knowledge_nodes_member_read on knowledge_nodes for select
using (is_repository_member(repository_id));

drop policy if exists knowledge_versions_member_read on knowledge_node_versions;
create policy knowledge_versions_member_read on knowledge_node_versions for select
using (
    exists (
        select 1 from knowledge_nodes node
        where node.id = knowledge_node_versions.node_id
          and is_repository_member(node.repository_id)
    )
);

drop policy if exists knowledge_edges_member_read on knowledge_edges;
create policy knowledge_edges_member_read on knowledge_edges for select
using (is_repository_member(repository_id));

drop policy if exists evidence_links_member_read on evidence_links;
create policy evidence_links_member_read on evidence_links for select
using (
    exists (
        select 1
        from source_versions version
        join sources source on source.id = version.source_id
        where version.id = evidence_links.source_version_id
          and is_repository_member(source.repository_id)
    )
);

drop policy if exists alignment_analyses_member_read on alignment_analyses;
create policy alignment_analyses_member_read on alignment_analyses for select
using (is_repository_member(repository_id));

drop policy if exists alignment_findings_member_read on alignment_findings;
create policy alignment_findings_member_read on alignment_findings for select
using (
    exists (
        select 1 from alignment_analyses analysis
        where analysis.id = alignment_findings.analysis_id
          and is_repository_member(analysis.repository_id)
    )
);

drop policy if exists context_passports_member_read on context_passports;
create policy context_passports_member_read on context_passports for select
using (
    exists (
        select 1 from alignment_analyses analysis
        where analysis.id = context_passports.analysis_id
          and is_repository_member(analysis.repository_id)
    )
);

drop policy if exists handshakes_member_read on handshakes;
create policy handshakes_member_read on handshakes for select
using (
    exists (
        select 1 from alignment_analyses analysis
        where analysis.id = handshakes.analysis_id
          and is_repository_member(analysis.repository_id)
    )
);

drop policy if exists handshakes_self_append on handshakes;
create policy handshakes_self_append on handshakes for insert
with check (
    profile_id = auth.uid()
    and exists (
        select 1 from alignment_analyses analysis
        where analysis.id = handshakes.analysis_id
          and is_repository_member(analysis.repository_id)
    )
);

drop policy if exists human_overrides_member_read on human_overrides;
create policy human_overrides_member_read on human_overrides for select
using (can_read_override(target_type, target_id));

drop policy if exists human_overrides_writer_append on human_overrides;
create policy human_overrides_writer_append on human_overrides for insert
with check (
    actor_profile_id = auth.uid()
    and length(btrim(reason)) > 0
    and can_write_override(target_type, target_id)
);

drop policy if exists generated_artifacts_member_read on generated_artifacts;
create policy generated_artifacts_member_read on generated_artifacts for select
using (is_repository_member(repository_id));

do $$
begin
    if exists (select 1 from pg_roles where rolname = 'authenticated') then
        grant select on
            profiles, github_installations, repositories, repository_memberships,
            sources, source_versions, sync_jobs, ai_runs, knowledge_nodes,
            knowledge_node_versions, knowledge_edges, evidence_links,
            alignment_analyses, alignment_findings, context_passports,
            handshakes, human_overrides, generated_artifacts
        to authenticated;
        grant insert, update on profiles to authenticated;
        grant insert on sync_jobs, handshakes, human_overrides to authenticated;
        grant execute on function is_repository_member(uuid) to authenticated;
        grant execute on function can_write_repository(uuid) to authenticated;
        grant execute on function shares_repository(uuid) to authenticated;
        grant execute on function can_write_override(text, uuid) to authenticated;
        grant execute on function can_read_override(text, uuid) to authenticated;
    end if;
    if exists (select 1 from pg_roles where rolname = 'service_role') then
        grant all privileges on
            profiles, github_installations, repositories, repository_memberships,
            sources, source_versions, sync_jobs, ai_runs, knowledge_nodes,
            knowledge_node_versions, knowledge_edges, evidence_links,
            alignment_analyses, alignment_findings, context_passports,
            handshakes, human_overrides, generated_artifacts
        to service_role;
        grant execute on function is_repository_member(uuid) to service_role;
        grant execute on function can_write_repository(uuid) to service_role;
        grant execute on function shares_repository(uuid) to service_role;
        grant execute on function can_write_override(text, uuid) to service_role;
        grant execute on function can_read_override(text, uuid) to service_role;
    end if;
end $$;

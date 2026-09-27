-- Run once in the existing Supabase project's SQL editor as its administrator.
-- No patient data, API keys or example accounts belong in this migration.
-- See https://supabase.com/docs/guides/storage/security/access-control
begin;
-- Administrators explicitly authorize verified email identities for this app.
-- No addresses are seeded in public source, and clients cannot enumerate the list.
create table if not exists public.dose_atlas_allowed_emails (
    email text primary key check (email = lower(btrim(email)) and length(email) between 3 and 320),
    enabled boolean not null default true
);
alter table public.dose_atlas_allowed_emails enable row level security;
revoke all on public.dose_atlas_allowed_emails from public, anon, authenticated;
revoke all on public.dose_atlas_allowed_emails from service_role;
grant select, insert, update on public.dose_atlas_allowed_emails to service_role;

create or replace function public.dose_atlas_has_access()
returns boolean
language sql stable security definer
set search_path = ''
as $$
    select exists (
        select 1 from auth.users u
        join public.dose_atlas_allowed_emails a on a.email = lower(btrim(u.email))
        where u.id = auth.uid() and u.email_confirmed_at is not null and a.enabled
    );
$$;
revoke all on function public.dose_atlas_has_access() from public, anon;
grant execute on function public.dose_atlas_has_access() to authenticated;

create table if not exists public.dose_atlas_runs (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null references auth.users(id) on delete cascade,
    summary jsonb not null default '{}'::jsonb
        check (jsonb_typeof(summary) = 'object' and octet_length(summary::text) <= 131072),
    created_at timestamptz not null default now(),
    status text not null default 'staging' check (status in ('staging', 'complete'))
);
create index if not exists dose_atlas_runs_owner_created_idx
    on public.dose_atlas_runs(owner_id, created_at desc);
alter table public.dose_atlas_runs enable row level security;
alter table public.dose_atlas_runs force row level security;
revoke all on public.dose_atlas_runs from anon, authenticated;
grant select, insert, delete on public.dose_atlas_runs to authenticated;
-- Users can complete a staging run, but cannot rewrite its owner, metadata or ID.
grant update(status) on public.dose_atlas_runs to authenticated;
drop policy if exists dose_atlas_runs_select_own on public.dose_atlas_runs;
create policy dose_atlas_runs_select_own on public.dose_atlas_runs for select
    to authenticated using (owner_id = (select auth.uid()));
drop policy if exists dose_atlas_runs_insert_own on public.dose_atlas_runs;
create policy dose_atlas_runs_insert_own on public.dose_atlas_runs for insert
    to authenticated with check (owner_id = (select auth.uid()) and status = 'staging');
drop policy if exists dose_atlas_runs_complete_own on public.dose_atlas_runs;
create policy dose_atlas_runs_complete_own on public.dose_atlas_runs for update
    to authenticated using (owner_id = (select auth.uid()) and status = 'staging')
    with check (owner_id = (select auth.uid()) and status = 'complete');
drop policy if exists dose_atlas_runs_delete_own on public.dose_atlas_runs;
create policy dose_atlas_runs_delete_own on public.dose_atlas_runs for delete
    to authenticated using (owner_id = (select auth.uid()));
drop policy if exists dose_atlas_runs_access_guard on public.dose_atlas_runs;
create policy dose_atlas_runs_access_guard on public.dose_atlas_runs as restrictive
for all to authenticated using ((select public.dose_atlas_has_access()))
with check ((select public.dose_atlas_has_access()));

insert into storage.buckets(id, name, public, file_size_limit, allowed_mime_types)
values ('dose-atlas', 'dose-atlas', false, 1048576,
        array['application/json'])
on conflict(id) do update set public = false, file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;
-- Private object names: <authenticated user UUID>/<run UUID>/<fixed filename>.
-- No UPDATE policy: originals cannot be overwritten with an upsert.
drop policy if exists dose_atlas_objects_select_own on storage.objects;
create policy dose_atlas_objects_select_own on storage.objects for select to authenticated
using (bucket_id = 'dose-atlas'
    and (storage.foldername(name))[1] = (select auth.uid()::text)
    and exists (select 1 from public.dose_atlas_runs r
        where r.id::text = (storage.foldername(name))[2]
          and r.owner_id = (select auth.uid())));
drop policy if exists dose_atlas_objects_insert_own on storage.objects;
create policy dose_atlas_objects_insert_own on storage.objects for insert to authenticated
with check (bucket_id = 'dose-atlas'
    and array_length(storage.foldername(name), 1) = 2
    and (storage.foldername(name))[1] = (select auth.uid()::text)
    and storage.filename(name) in ('result.json', 'model_card.json')
    and exists (select 1 from public.dose_atlas_runs r
        where r.id::text = (storage.foldername(name))[2]
          and r.owner_id = (select auth.uid()) and r.status = 'staging'));
drop policy if exists dose_atlas_objects_delete_own on storage.objects;
create policy dose_atlas_objects_delete_own on storage.objects for delete to authenticated
using (bucket_id = 'dose-atlas'
    and (storage.foldername(name))[1] = (select auth.uid()::text)
    and exists (select 1 from public.dose_atlas_runs r
        where r.id::text = (storage.foldername(name))[2]
          and r.owner_id = (select auth.uid())));
-- Restrictive guards are ANDed with existing permissive policies. Even an older
-- application's broad policy cannot expose this bucket. Other buckets are unchanged.
drop policy if exists dose_atlas_bucket_owner_guard on storage.objects;
create policy dose_atlas_bucket_owner_guard on storage.objects as restrictive
for all to authenticated
using (bucket_id <> 'dose-atlas' or (
    (storage.foldername(name))[1] = (select auth.uid()::text)
    and exists (select 1 from public.dose_atlas_runs r
        where r.id::text = (storage.foldername(name))[2]
          and r.owner_id = (select auth.uid()))))
with check (bucket_id <> 'dose-atlas' or (
    (storage.foldername(name))[1] = (select auth.uid()::text)
    and exists (select 1 from public.dose_atlas_runs r
        where r.id::text = (storage.foldername(name))[2]
          and r.owner_id = (select auth.uid()))));
drop policy if exists dose_atlas_bucket_insert_guard on storage.objects;
create policy dose_atlas_bucket_insert_guard on storage.objects as restrictive
for insert to authenticated with check (bucket_id <> 'dose-atlas' or (
    array_length(storage.foldername(name), 1) = 2
    and storage.filename(name) in ('result.json', 'model_card.json')
    and exists (select 1 from public.dose_atlas_runs r
        where r.id::text = (storage.foldername(name))[2]
          and r.owner_id = (select auth.uid()) and r.status = 'staging')));
drop policy if exists dose_atlas_bucket_json_read on storage.objects;
create policy dose_atlas_bucket_json_read on storage.objects as restrictive
for select to authenticated using (bucket_id <> 'dose-atlas' or (
    array_length(storage.foldername(name), 1) = 2
    and storage.filename(name) in ('result.json', 'model_card.json')));
drop policy if exists dose_atlas_bucket_no_update on storage.objects;
create policy dose_atlas_bucket_no_update on storage.objects as restrictive
for update to public using (bucket_id <> 'dose-atlas') with check (bucket_id <> 'dose-atlas');
-- No cross-table lookup for anon: that role has no runs-table grant, and such a
-- lookup would also break otherwise-public reads in unrelated buckets.
drop policy if exists dose_atlas_bucket_anon_guard on storage.objects;
create policy dose_atlas_bucket_anon_guard on storage.objects as restrictive
for all to anon using (bucket_id <> 'dose-atlas') with check (bucket_id <> 'dose-atlas');
drop policy if exists dose_atlas_bucket_access_guard on storage.objects;
create policy dose_atlas_bucket_access_guard on storage.objects as restrictive
for all to authenticated
using (bucket_id <> 'dose-atlas' or (select public.dose_atlas_has_access()))
with check (bucket_id <> 'dose-atlas' or (select public.dose_atlas_has_access()));

commit;
-- This migration does not modify another application's policies. Test separate
-- user A/B accounts against both REST and
-- Storage: anonymous access and cross-owner reads/writes/deletes must fail.
-- Abandoned staging runs may be cleaned administratively: remove their Storage
-- objects first, then their row. User deletion likewise requires object cleanup.

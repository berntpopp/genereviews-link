create table if not exists public.genereview_release_readiness (
    readiness_key      boolean primary key default true check (readiness_key),
    release_tag        text not null,
    is_active          boolean not null check (is_active),
    ready              boolean not null check (ready),
    readiness_marker   text not null check (readiness_marker = 'verified-v1'),
    logical_volumes    text[] not null check (
        logical_volumes = array[
            'genereview_pg_data',
            'genereview_pg_run',
            'genereview_restore_state'
        ]::text[]
    ),
    readiness          jsonb not null,
    recorded_at        timestamptz not null default now()
);

create or replace function public.reject_release_readiness_mutation()
returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
    raise exception 'release readiness records are immutable';
end;
$$;

drop trigger if exists genereview_release_readiness_immutable
    on public.genereview_release_readiness;
create trigger genereview_release_readiness_immutable
before update or delete on public.genereview_release_readiness
for each row execute function public.reject_release_readiness_mutation();

revoke insert, update, delete, truncate on public.genereview_release_readiness from public;

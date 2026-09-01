create table if not exists public.genereview_computation_runs (
    run_id              text primary key check (run_id ~ '^[0-9a-f]{64}$'),
    corpus_version      text not null,
    phase               text not null check (phase in ('ingest', 'embedding')),
    app_git_sha         text not null check (app_git_sha ~ '^[0-9a-f]{40}$'),
    provenance          jsonb not null,
    expected_row_count  bigint not null check (expected_row_count >= 0),
    recorded_at         timestamptz not null,
    unique (corpus_version, phase, run_id)
);

create or replace function public.reject_computation_run_mutation()
returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
    raise exception 'computation run records are immutable';
end;
$$;

drop trigger if exists genereview_computation_runs_immutable
    on public.genereview_computation_runs;
create trigger genereview_computation_runs_immutable
before update or delete on public.genereview_computation_runs
for each row execute function public.reject_computation_run_mutation();

alter table public.genereview_corpus_version
    add column if not exists source_capture jsonb,
    add column if not exists ingest_run_id text references public.genereview_computation_runs(run_id),
    add column if not exists embedding_run_id text references public.genereview_computation_runs(run_id);

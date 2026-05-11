create table if not exists public.genereview_refresh_log (
    refresh_id              uuid primary key default gen_random_uuid(),
    check_time              timestamptz not null default now(),
    file_list_last_updated  text,
    decision                text not null,
    duration_ms             bigint,
    detail                  jsonb not null default '{}'::jsonb
);

create index if not exists genereview_refresh_log_time_idx
    on public.genereview_refresh_log (check_time desc);

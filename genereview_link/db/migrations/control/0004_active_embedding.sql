create table if not exists public.genereview_active_embedding (
    id              int primary key default 1 check (id = 1),
    table_name      text not null default 'genereview_embeddings_bge384',
    model_name      text not null default 'BAAI/bge-small-en-v1.5',
    updated_at      timestamptz not null default now()
);

insert into public.genereview_active_embedding default values on conflict do nothing;

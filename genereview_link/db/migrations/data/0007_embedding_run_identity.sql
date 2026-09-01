alter table genereview_embeddings_bge384
    add column if not exists embedding_run_id text;

create index if not exists genereview_embeddings_bge384_run_idx
    on genereview_embeddings_bge384 (embedding_run_id);

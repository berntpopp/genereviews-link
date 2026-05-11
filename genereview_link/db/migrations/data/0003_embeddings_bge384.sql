create extension if not exists vector;

create table if not exists genereview_embeddings_bge384 (
    nbk_id              text not null,
    passage_id          text not null,
    model_name          text not null default 'BAAI/bge-small-en-v1.5',
    model_revision      text,
    text_hash           text not null,
    embedding           vector(384) not null,
    created_at          timestamptz not null default now(),
    primary key (nbk_id, passage_id),
    foreign key (nbk_id, passage_id)
        references genereview_passages(nbk_id, passage_id)
        on delete cascade
);

-- HNSW index intentionally omitted here. The `embed` CLI builds it post-COPY
-- in Phase 3 to avoid per-row index maintenance during bulk ingest.

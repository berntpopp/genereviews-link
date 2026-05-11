create table if not exists public.genereview_corpus_version (
    version                 text primary key,
    file_list_etag          text,
    tarball_sha256          text,
    tarball_size_bytes      bigint,
    chapter_count           int,
    ingest_started_at       timestamptz not null,
    ingest_finished_at      timestamptz,
    ingest_status           text not null,
    is_active               boolean not null default false,
    notes                   text
);

create unique index if not exists genereview_corpus_version_active_unique
    on public.genereview_corpus_version (is_active) where is_active;

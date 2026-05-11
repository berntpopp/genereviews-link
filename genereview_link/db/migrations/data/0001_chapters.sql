create table if not exists genereview_chapters (
    nbk_id              text primary key,
    short_name          text not null,
    title               text not null,
    pubmed_id           text,
    gene_symbols        text[] not null default '{}',
    omim_ids            text[] not null default '{}',
    authors             text,
    initial_pub_date    date,
    last_updated_date   date,
    corpus_version      text not null,
    nxml_relpath        text not null,
    raw_metadata        jsonb not null default '{}'::jsonb,
    ingested_at         timestamptz not null default now()
);

create index if not exists genereview_chapters_gene_symbols_gin
    on genereview_chapters using gin (gene_symbols);
create index if not exists genereview_chapters_omim_gin
    on genereview_chapters using gin (omim_ids);
create index if not exists genereview_chapters_pubmed_id_idx
    on genereview_chapters (pubmed_id) where pubmed_id is not null;
create index if not exists genereview_chapters_last_updated_idx
    on genereview_chapters (last_updated_date desc);
create index if not exists genereview_chapters_corpus_version_idx
    on genereview_chapters (corpus_version);

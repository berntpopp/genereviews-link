create table if not exists genereview_passages (
    nbk_id              text not null references genereview_chapters(nbk_id) on delete cascade,
    passage_id          text not null,
    chapter_section     text not null,
    heading_path        text,
    section_level       int not null default 1,
    chunk_index         int not null,
    text                text not null,
    text_hash           text not null,
    char_count          int not null,
    token_estimate      int not null,
    corpus_version      text not null,
    search_vector       tsvector generated always as (
        to_tsvector('english',
            coalesce(heading_path, '') || ' ' ||
            chapter_section || ' ' ||
            text
        )
    ) stored,
    created_at          timestamptz not null default now(),
    primary key (nbk_id, passage_id)
);

create index if not exists genereview_passages_search_vector_gin
    on genereview_passages using gin (search_vector);
create index if not exists genereview_passages_nbk_section_idx
    on genereview_passages (nbk_id, chapter_section);
create index if not exists genereview_passages_section_idx
    on genereview_passages (chapter_section);
create index if not exists genereview_passages_corpus_version_idx
    on genereview_passages (corpus_version);

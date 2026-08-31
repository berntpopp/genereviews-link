alter table public.genereview_corpus_version
    add column if not exists listing_relpath text,
    add column if not exists sidedata_title_sha256 text,
    add column if not exists sidedata_title_size_bytes bigint,
    add column if not exists sidedata_genes_sha256 text,
    add column if not exists sidedata_genes_size_bytes bigint,
    add column if not exists sidedata_omim_sha256 text,
    add column if not exists sidedata_omim_size_bytes bigint;

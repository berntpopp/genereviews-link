-- 0006_primary_gene_symbols.sql
-- Add primary_gene_symbols to genereview_chapters.
-- Existing rows keep the default '{}'; they will be repopulated on the
-- next full ingest (see rollout notes in design doc issue #43).
--
-- Rollback is manual:
--   drop index genereview_chapters_primary_gene_gin;
--   alter table genereview_chapters drop column primary_gene_symbols;

alter table genereview_chapters
    add column if not exists primary_gene_symbols text[] not null default '{}';

create index if not exists genereview_chapters_primary_gene_gin
    on genereview_chapters using gin (primary_gene_symbols);

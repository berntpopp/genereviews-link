-- 0004_passage_type_and_tables.sql
-- Phase 7: passage_type discrimination + table-passage support.
-- Existing rows receive passage_type = 'narrative' via the column default.

alter table genereview_passages
    add column if not exists passage_type text not null default 'narrative';

do $$
begin
    if not exists (
        select 1
        from   pg_constraint
        where  conrelid = 'genereview_passages'::regclass
        and    conname  = 'genereview_passages_passage_type_check'
    ) then
        alter table genereview_passages
            add constraint genereview_passages_passage_type_check
            check (passage_type in ('narrative', 'table'));
    end if;
end
$$;

create index if not exists passages_type_chapter_idx
    on genereview_passages (nbk_id, passage_type);

alter table genereview_passages
    add column if not exists table_id text;  -- non-null only for passage_type = 'table'

create unique index if not exists passages_table_id_unique_idx
    on genereview_passages (nbk_id, table_id)
    where passage_type = 'table';

alter table genereview_passages
    add column if not exists table_data jsonb;  -- non-null only for passage_type = 'table'

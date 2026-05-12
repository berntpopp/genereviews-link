-- 0005_passage_role.sql
-- The migration runner sets search_path to the target data schema,public.
-- Keep table names unqualified so the same SQL works for all data schemas.
--
-- Rollback is manual because there is no down-migration runner:
--   drop index idx_passages_role;
--   alter table genereview_passages drop column passage_role;

alter table genereview_passages
    add column if not exists passage_role text not null default 'evidence';

create index if not exists idx_passages_role
    on genereview_passages (passage_role);

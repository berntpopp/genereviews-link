-- The GeneFoundry runtime data identity (v1) of the corpus that is actually restored.
--
-- `genereview_release_readiness` (0007) proves a *manifest-v3 direct* release and is
-- immutable and single-shot by design. This table answers a different question, for every
-- deployment including the legacy manifest-v2 bundle: which reviewed data release is
-- serving right now? It is written only by the no-egress init sidecar, from the artifact
-- it proved byte-for-byte, and only after the manifest's corpus identity has been matched
-- against the rows actually present in the database. `/health` republishes it and
-- re-derives the live facts before it does, so a swapped volume cannot keep the claim.
--
-- Unlike readiness this row is replaceable: activating a new data release must be able to
-- supersede it. It is never written by the unprivileged restore role.
create table if not exists public.genereview_runtime_data_identity (
    identity_key    boolean primary key default true check (identity_key),
    release_tag     text        not null,
    digest          text        not null,
    seed_mode       text        not null check (seed_mode in ('legacy', 'direct')),
    corpus_version  text        not null,
    dump_digest     text        not null,
    counts          jsonb       not null,
    recorded_at     timestamptz not null default now()
);

revoke insert, update, delete, truncate
    on public.genereview_runtime_data_identity from public;

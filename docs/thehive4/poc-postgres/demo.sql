-- TheHive4-on-Postgres PoC — tenancy demonstration.
-- Run after V1-V4 + seed. Shows RLS isolating orgs while honouring shares.
-- The SAME query returns different rows depending only on the session org.
--
-- CRITICAL: become the non-superuser app role first — superusers bypass RLS.
SET ROLE thehive_app;

\echo '================ As org-a (Alice) ================'
SELECT set_current_org('org-a');
\echo '-- cases visible to org-a (expect #1 owned + #3 shared) --'
SELECT number, title, owning_organisation_id FROM case_ ORDER BY number;
\echo '-- observables visible to org-a (expect the 2 under case #3) --'
SELECT data_type, data FROM observable ORDER BY id;

\echo ''
\echo '================ As org-b (Bob) =================='
SELECT set_current_org('org-b');
\echo '-- cases visible to org-b (expect #2 owned + #3 shared-in) --'
SELECT number, title, owning_organisation_id FROM case_ ORDER BY number;
\echo '-- observables visible to org-b (expect the 2 under shared case #3) --'
SELECT data_type, data FROM observable ORDER BY id;

\echo ''
\echo '== Proof of isolation: org-b CANNOT see case #1 even by primary key =='
SELECT set_current_org('org-b');
SELECT count(*) AS should_be_zero
FROM case_ WHERE number = 1;            -- A-only case; RLS hides it

\echo ''
\echo '== Proof writes are gated too: org-b updating case #1 affects 0 rows =='
SELECT set_current_org('org-b');
WITH upd AS (
    UPDATE case_ SET title = 'HACKED' WHERE number = 1 RETURNING 1
)
SELECT count(*) AS rows_updated FROM upd;   -- expect 0 (row invisible => not updated)

\echo ''
\echo '== No org context set => sees nothing =='
SELECT set_config('app.current_org_id', '', false);
SELECT count(*) AS visible_cases FROM case_;   -- expect 0

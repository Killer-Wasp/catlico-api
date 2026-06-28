-- TheHive4-on-Postgres PoC — permission gate demonstration (run after V1-V5 + seed).
-- Proves the TWO-LEVEL `.can()` model: org-profile permission AND share-profile permission.
SET ROLE thehive_app;

\echo '== Setup: Alice is an analyst in org-a; Bob is an analyst in org-b =='
\echo '== Case #3 is owned by org-a (analyst share) and shared to org-b READ-ONLY =='

\echo ''
\echo '-- Alice (analyst, org-a owner share) updates case #3 => allowed (1 row) --'
SELECT set_current_org('org-a'); SELECT set_current_user('alice@org-a.test');
WITH u AS (UPDATE case_ SET title = title || ' [edited by A]' WHERE number = 3 RETURNING 1)
SELECT count(*) AS rows_updated FROM u;     -- expect 1

\echo ''
\echo '-- Bob (analyst in org-b, but case #3 shared to org-b READ-ONLY) updates #3 --'
\echo '-- => org-profile has manageCase, but SHARE profile does not => blocked (0 rows) --'
SELECT set_current_org('org-b'); SELECT set_current_user('bob@org-b.test');
WITH u AS (UPDATE case_ SET title = 'HIJACKED' WHERE number = 3 RETURNING 1)
SELECT count(*) AS rows_updated FROM u;     -- expect 0

\echo ''
\echo '-- Bob can still READ case #3 (org visibility gate from V3 is independent) --'
SELECT number, title FROM case_ WHERE number = 3;   -- visible, title shows Alice''s edit

\echo ''
\echo '-- Bob updates his OWN case #2 (org-b owner share = analyst) => allowed (1 row) --'
WITH u AS (UPDATE case_ SET title = title || ' [edited by B]' WHERE number = 2 RETURNING 1)
SELECT count(*) AS rows_updated FROM u;     -- expect 1

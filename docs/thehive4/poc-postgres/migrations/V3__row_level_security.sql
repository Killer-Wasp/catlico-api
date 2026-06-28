-- TheHive4-on-Postgres PoC — V3: Row-Level Security = the multi-tenant gate.
--
-- This is the relational equivalent of TheHive's `.visible`/`.can` traversal
-- steps: every SELECT/UPDATE/DELETE is transparently restricted to the rows the
-- session's "current organisation" is allowed to see — enforced by the database,
-- not by app code that might forget a filter.
--
-- FORCE ROW LEVEL SECURITY makes even the table owner subject to the policies,
-- so the PoC is demonstrable in a single psql session without role juggling.
-- (In production you'd connect as a non-owner app role instead.)

-- ---- case_share: an org sees only its OWN shares ---------------------------
ALTER TABLE case_share ENABLE ROW LEVEL SECURITY;
ALTER TABLE case_share FORCE  ROW LEVEL SECURITY;
CREATE POLICY share_tenant ON case_share
    USING (organisation_id = current_org());

-- ---- case_: visible if owned by the current org OR shared to it ------------
-- The case_share subquery is itself RLS-filtered to the current org (above),
-- so this composes into "cases I own or that someone shared with me".
ALTER TABLE case_ ENABLE ROW LEVEL SECURITY;
ALTER TABLE case_ FORCE  ROW LEVEL SECURITY;
CREATE POLICY case_tenant ON case_
    USING (
        owning_organisation_id = current_org()
        OR id IN (SELECT case_id FROM case_share)
    );

-- ---- task / observable: visible iff their case is visible ------------------
-- The case_ subquery applies case_'s RLS, cascading the rule downward.
ALTER TABLE task ENABLE ROW LEVEL SECURITY;
ALTER TABLE task FORCE  ROW LEVEL SECURITY;
CREATE POLICY task_tenant ON task
    USING (case_id IN (SELECT id FROM case_));

ALTER TABLE observable ENABLE ROW LEVEL SECURITY;
ALTER TABLE observable FORCE  ROW LEVEL SECURITY;
CREATE POLICY observable_tenant ON observable
    USING (case_id IN (SELECT id FROM case_));

-- NOTE: organisation / app_user / profile are intentionally left without RLS —
-- they are a global directory. Permission-level checks (the `.can(permission)`
-- equivalent) would layer on top of this org gate, e.g. by joining membership
-- + profile_permission for the current user; omitted here to keep the PoC
-- focused on the tenancy boundary.

-- TheHive4-on-Postgres PoC — seed data.
-- Two orgs (A, B). Case #1 owned by A (private). Case #2 owned by B (private).
-- Case #3 owned by A and SHARED with B. Proves isolation + sharing.

INSERT INTO organisation (name, description) VALUES
    ('org-a', 'Alpha SOC'),
    ('org-b', 'Bravo SOC');

INSERT INTO profile (name) VALUES ('analyst'), ('read-only');
INSERT INTO profile_permission (profile_id, permission)
SELECT id, p FROM profile, unnest(ARRAY['manageCase','manageObservable','manageTask']) p
WHERE name = 'analyst';

INSERT INTO app_user (login, name) VALUES
    ('alice@org-a.test', 'Alice (A)'),
    ('bob@org-b.test',   'Bob (B)');

INSERT INTO membership (user_id, organisation_id, profile_id)
SELECT u.id, o.id, p.id
FROM app_user u, organisation o, profile p
WHERE p.name = 'analyst'
  AND ((u.login = 'alice@org-a.test' AND o.name = 'org-a')
    OR (u.login = 'bob@org-b.test'   AND o.name = 'org-b'));

-- Cases (owning org via name lookup)
INSERT INTO case_ (number, title, severity, owning_organisation_id)
SELECT 1, 'A-only phishing wave',      2, id FROM organisation WHERE name='org-a';
INSERT INTO case_ (number, title, severity, owning_organisation_id)
SELECT 2, 'B-only ransomware triage',  3, id FROM organisation WHERE name='org-b';
INSERT INTO case_ (number, title, severity, owning_organisation_id)
SELECT 3, 'Joint APT investigation',   3, id FROM organisation WHERE name='org-a';

-- Owner shares (each owning org gets an owner=true share of its cases)
INSERT INTO case_share (organisation_id, case_id, profile_id, owner)
SELECT c.owning_organisation_id, c.id, (SELECT id FROM profile WHERE name='analyst'), true
FROM case_ c;

-- Case #3 additionally shared from A to B (read-only)
INSERT INTO case_share (organisation_id, case_id, profile_id, owner)
SELECT (SELECT id FROM organisation WHERE name='org-b'),
       c.id,
       (SELECT id FROM profile WHERE name='read-only'),
       false
FROM case_ c WHERE c.number = 3;

-- A couple of observables under case #3
INSERT INTO observable (case_id, data_type, data, ioc)
SELECT id, 'ip', '203.0.113.10', true  FROM case_ WHERE number = 3;
INSERT INTO observable (case_id, data_type, data, ioc)
SELECT id, 'domain', 'evil.example', true FROM case_ WHERE number = 3;

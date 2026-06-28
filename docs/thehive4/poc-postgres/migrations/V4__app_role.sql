-- TheHive4-on-Postgres PoC — V4: the application role.
--
-- IMPORTANT: Row-Level Security is bypassed by SUPERUSERS and by roles with
-- BYPASSRLS, *regardless* of FORCE ROW LEVEL SECURITY. The app MUST connect as
-- an ordinary (non-superuser, non-owner) role for the tenancy gate to apply.
-- This migration creates that role and grants it data access.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'thehive_app') THEN
        CREATE ROLE thehive_app NOSUPERUSER NOBYPASSRLS;
    END IF;
END $$;

GRANT USAGE ON SCHEMA public TO thehive_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES    IN SCHEMA public TO thehive_app;
GRANT USAGE, SELECT                  ON ALL SEQUENCES  IN SCHEMA public TO thehive_app;
GRANT EXECUTE                        ON ALL FUNCTIONS  IN SCHEMA public TO thehive_app;

-- Keep future objects accessible too.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO thehive_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO thehive_app;

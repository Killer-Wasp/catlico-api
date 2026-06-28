-- TheHive4-on-Postgres PoC — V1: RBAC / tenancy core
-- Organisations, profiles (permission sets), users, and per-org membership.
-- Also the session-org helpers used by Row-Level Security in V3.

CREATE TABLE organisation (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE profile (
    id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE profile_permission (
    profile_id BIGINT NOT NULL REFERENCES profile(id) ON DELETE CASCADE,
    permission TEXT   NOT NULL,
    PRIMARY KEY (profile_id, permission)
);

CREATE TABLE app_user (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    login           TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    api_key         TEXT UNIQUE,
    locked          BOOLEAN NOT NULL DEFAULT false,
    password        TEXT,
    totp_secret     TEXT,
    failed_attempts INT,
    last_failed     TIMESTAMPTZ
);

-- Collapses TheHive's User-Role-Profile-Organisation edge chain into one row:
-- "user U is a member of org O with profile P".
CREATE TABLE membership (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    organisation_id BIGINT NOT NULL REFERENCES organisation(id) ON DELETE CASCADE,
    profile_id      BIGINT NOT NULL REFERENCES profile(id),
    UNIQUE (user_id, organisation_id)
);

-- ---- Session "current organisation" context (the AuthContext org) ----------
-- current_org() reads a per-session GUC; RLS policies (V3) key off it.
CREATE FUNCTION current_org() RETURNS BIGINT
    LANGUAGE sql STABLE AS
$$ SELECT NULLIF(current_setting('app.current_org_id', true), '')::bigint $$;

-- Convenience: set the session org by name (the demo uses this).
CREATE FUNCTION set_current_org(p_name TEXT) RETURNS BIGINT
    LANGUAGE plpgsql AS
$$
DECLARE v_id BIGINT;
BEGIN
    SELECT id INTO v_id FROM organisation WHERE name = p_name;
    IF v_id IS NULL THEN RAISE EXCEPTION 'No such organisation: %', p_name; END IF;
    PERFORM set_config('app.current_org_id', v_id::text, false);
    RETURN v_id;
END
$$;

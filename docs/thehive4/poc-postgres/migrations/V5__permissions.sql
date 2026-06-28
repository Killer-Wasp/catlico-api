-- TheHive4-on-Postgres PoC — V5: the permission gate (the `.can(permission)` equivalent).
--
-- V3 gave us the ORG VISIBILITY gate (what rows you can see). This adds the
-- PERMISSION gate on writes. TheHive's `.can(permission)` is TWO-LEVEL:
--   1. the user's profile in the CURRENT ORG must hold the permission
--      (authContext.permissions.contains(permission)), AND
--   2. the profile on the CASE's SHARE to the current org must hold it
--      (share.profile.has(permissions, permission)).
-- So an analyst in org B still cannot edit a case that was shared to org B
-- read-only. We reproduce both levels here as RESTRICTIVE RLS policies.

-- ---- session "current user" context (alongside current_org from V1) --------
CREATE FUNCTION current_user_id() RETURNS BIGINT
    LANGUAGE sql STABLE AS
$$ SELECT NULLIF(current_setting('app.current_user_id', true), '')::bigint $$;

CREATE FUNCTION set_current_user(p_login TEXT) RETURNS BIGINT
    LANGUAGE plpgsql AS
$$
DECLARE v_id BIGINT;
BEGIN
    SELECT id INTO v_id FROM app_user WHERE login = p_login;
    IF v_id IS NULL THEN RAISE EXCEPTION 'No such user: %', p_login; END IF;
    PERFORM set_config('app.current_user_id', v_id::text, false);
    RETURN v_id;
END
$$;

-- Level 1: does the current user's CURRENT-ORG profile hold the permission?
-- SECURITY DEFINER so the policy works regardless of the caller's table grants.
CREATE FUNCTION has_perm(p_perm TEXT) RETURNS BOOLEAN
    LANGUAGE sql STABLE SECURITY DEFINER AS
$$
    SELECT EXISTS (
        SELECT 1
        FROM membership m
        JOIN profile_permission pp ON pp.profile_id = m.profile_id
        WHERE m.user_id = current_user_id()
          AND m.organisation_id = current_org()
          AND pp.permission = p_perm
    )
$$;

-- Level 1 AND Level 2: org profile holds it AND the case's share-to-this-org holds it.
CREATE FUNCTION can_case(p_case_id BIGINT, p_perm TEXT) RETURNS BOOLEAN
    LANGUAGE sql STABLE SECURITY DEFINER AS
$$
    SELECT has_perm(p_perm)
       AND EXISTS (
           SELECT 1
           FROM case_share s
           JOIN profile_permission pp ON pp.profile_id = s.profile_id
           WHERE s.case_id = p_case_id
             AND s.organisation_id = current_org()
             AND pp.permission = p_perm
       )
$$;

-- ---- RESTRICTIVE policies AND-combine with the V3 org-visibility policies ---
-- (Permissive policies OR together; RESTRICTIVE ones AND in — exactly what we want:
--  "row is visible to my org" AND "I'm permitted to write it".)
CREATE POLICY case_insert_perm ON case_ AS RESTRICTIVE FOR INSERT
    WITH CHECK (owning_organisation_id = current_org() AND has_perm('manageCase'));
CREATE POLICY case_update_perm ON case_ AS RESTRICTIVE FOR UPDATE
    USING (can_case(id, 'manageCase'));
CREATE POLICY case_delete_perm ON case_ AS RESTRICTIVE FOR DELETE
    USING (can_case(id, 'manageCase'));

CREATE POLICY task_write_perm ON task AS RESTRICTIVE FOR UPDATE
    USING (can_case(case_id, 'manageTask'));
CREATE POLICY observable_write_perm ON observable AS RESTRICTIVE FOR UPDATE
    USING (can_case(case_id, 'manageObservable'));

GRANT EXECUTE ON FUNCTION current_user_id(), set_current_user(TEXT), has_perm(TEXT), can_case(BIGINT, TEXT) TO thehive_app;

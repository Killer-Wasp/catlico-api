-- TheHive4-on-Postgres PoC — V2: cases, tasks, observables + the Share fan-out.
-- The Share model is the multi-tenant visibility engine; V3 enforces it via RLS.

CREATE TABLE case_ (
    id                     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    number                 INT NOT NULL UNIQUE,
    title                  TEXT NOT NULL,
    description            TEXT NOT NULL DEFAULT '',
    severity               INT NOT NULL DEFAULT 2,
    tlp                    INT NOT NULL DEFAULT 2,
    pap                    INT NOT NULL DEFAULT 2,
    status                 TEXT NOT NULL DEFAULT 'Open',
    assignee_id            BIGINT REFERENCES app_user(id),
    owning_organisation_id BIGINT NOT NULL REFERENCES organisation(id),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE task (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id     BIGINT NOT NULL REFERENCES case_(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    task_group  TEXT NOT NULL DEFAULT 'default',
    status      TEXT NOT NULL DEFAULT 'Waiting',
    assignee_id BIGINT REFERENCES app_user(id)
);

CREATE TABLE observable (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id     BIGINT REFERENCES case_(id) ON DELETE CASCADE,
    data_type   TEXT NOT NULL,
    data        TEXT,
    ioc         BOOLEAN NOT NULL DEFAULT false,
    tlp         INT NOT NULL DEFAULT 2
);

-- A Share makes a case visible to an organisation, with a profile + ownership flag.
CREATE TABLE case_share (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organisation_id BIGINT NOT NULL REFERENCES organisation(id) ON DELETE CASCADE,
    case_id         BIGINT NOT NULL REFERENCES case_(id) ON DELETE CASCADE,
    profile_id      BIGINT NOT NULL REFERENCES profile(id),
    owner           BOOLEAN NOT NULL DEFAULT false,
    UNIQUE (organisation_id, case_id)
);

-- A share can expose only a subset of the case's tasks/observables.
CREATE TABLE share_task (
    share_id        BIGINT NOT NULL REFERENCES case_share(id) ON DELETE CASCADE,
    task_id         BIGINT NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    action_required BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (share_id, task_id)
);
CREATE TABLE share_observable (
    share_id      BIGINT NOT NULL REFERENCES case_share(id) ON DELETE CASCADE,
    observable_id BIGINT NOT NULL REFERENCES observable(id) ON DELETE CASCADE,
    PRIMARY KEY (share_id, observable_id)
);

-- Tags + polymorphic tagging (replaces CaseTag/ObservableTag/... edges).
CREATE TABLE tag (
    id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    namespace TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value     TEXT,
    colour    TEXT NOT NULL DEFAULT '#000000',
    UNIQUE (namespace, predicate, value)
);
CREATE TABLE tagging (
    tag_id        BIGINT NOT NULL REFERENCES tag(id) ON DELETE CASCADE,
    taggable_type TEXT NOT NULL,   -- 'case' | 'observable' | 'alert' | ...
    taggable_id   BIGINT NOT NULL,
    PRIMARY KEY (tag_id, taggable_type, taggable_id)
);

CREATE INDEX ON case_share (organisation_id);
CREATE INDEX ON case_share (case_id);
CREATE INDEX ON observable (case_id);
CREATE INDEX ON task (case_id);

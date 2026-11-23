-- MistakeBook AI — SQLite schema
-- Generated from backend/app/models.py (SQLAlchemy). Kept in sync manually.
-- For PostgreSQL use database/schema.postgres.sql.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Login identifier: users may register with a username, an email, or both.
    username        VARCHAR(64)  NOT NULL UNIQUE,
    email           VARCHAR(255) UNIQUE,               -- optional
    display_name    VARCHAR(120) NOT NULL DEFAULT '',
    password_hash   VARCHAR(255) NOT NULL,
    role            VARCHAR(32)  NOT NULL DEFAULT 'member',
    is_active       BOOLEAN      NOT NULL DEFAULT 1,
    settings_json   TEXT         NOT NULL DEFAULT '{}',
    created_at      DATETIME     NOT NULL,
    updated_at      DATETIME     NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_users_username ON users (username);
CREATE INDEX IF NOT EXISTS ix_users_email ON users (email);

CREATE TABLE IF NOT EXISTS papers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title           VARCHAR(255) NOT NULL DEFAULT '未命名试卷',
    subject         VARCHAR(64)  NOT NULL DEFAULT '未分类',
    status          VARCHAR(32)  NOT NULL DEFAULT 'pending',
    error_message   TEXT         NOT NULL DEFAULT '',
    page_count      INTEGER      NOT NULL DEFAULT 0,
    ocr_provider    VARCHAR(32)  NOT NULL DEFAULT '',
    source_names    TEXT         NOT NULL DEFAULT '',
    created_at      DATETIME     NOT NULL,
    updated_at      DATETIME     NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_papers_user_id ON papers (user_id);
CREATE INDEX IF NOT EXISTS ix_papers_status  ON papers (status);

CREATE TABLE IF NOT EXISTS paper_pages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id        INTEGER      NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    page_no         INTEGER      NOT NULL,
    storage_key     VARCHAR(512) NOT NULL,
    original_name   VARCHAR(255) NOT NULL DEFAULT '',
    width           INTEGER      NOT NULL DEFAULT 0,
    height          INTEGER      NOT NULL DEFAULT 0,
    render_dpi      INTEGER      NOT NULL DEFAULT 150,
    ocr_text        TEXT         NOT NULL DEFAULT '',
    created_at      DATETIME     NOT NULL,
    updated_at      DATETIME     NOT NULL,
    CONSTRAINT uq_paper_page UNIQUE (paper_id, page_no)
);
CREATE INDEX IF NOT EXISTS ix_paper_pages_paper_id ON paper_pages (paper_id);

CREATE TABLE IF NOT EXISTS sections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id        INTEGER      NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    title           VARCHAR(255) NOT NULL DEFAULT '',
    order_index     INTEGER      NOT NULL DEFAULT 0,
    created_at      DATETIME     NOT NULL,
    updated_at      DATETIME     NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sections_paper_id ON sections (paper_id);

CREATE TABLE IF NOT EXISTS questions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id        INTEGER      NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    section_id      INTEGER      REFERENCES sections(id) ON DELETE SET NULL,
    parent_id       INTEGER      REFERENCES questions(id) ON DELETE CASCADE,
    label           VARCHAR(64)  NOT NULL DEFAULT '',
    text            TEXT         NOT NULL DEFAULT '',
    answer_text     TEXT         NOT NULL DEFAULT '',
    subject         VARCHAR(64)  NOT NULL DEFAULT '未分类',
    difficulty      INTEGER      NOT NULL DEFAULT 3,
    order_index     INTEGER      NOT NULL DEFAULT 0,
    content_hash    VARCHAR(64)  NOT NULL DEFAULT '',
    is_practice     BOOLEAN      NOT NULL DEFAULT 0,
    created_at      DATETIME     NOT NULL,
    updated_at      DATETIME     NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_questions_paper_id   ON questions (paper_id);
CREATE INDEX IF NOT EXISTS ix_questions_section_id ON questions (section_id);
CREATE INDEX IF NOT EXISTS ix_questions_parent_id  ON questions (parent_id);
CREATE INDEX IF NOT EXISTS ix_questions_subject    ON questions (subject);
CREATE INDEX IF NOT EXISTS ix_questions_hash       ON questions (content_hash);

-- Geometry + recommendation signal. Never the authoritative "recorded" fact.
CREATE TABLE IF NOT EXISTS question_regions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id           INTEGER      NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    page_id            INTEGER      NOT NULL REFERENCES paper_pages(id) ON DELETE CASCADE,
    page_no            INTEGER      NOT NULL DEFAULT 1,
    question_id        INTEGER      REFERENCES questions(id) ON DELETE SET NULL,
    x                  INTEGER      NOT NULL,
    y                  INTEGER      NOT NULL,
    width              INTEGER      NOT NULL,
    height             INTEGER      NOT NULL,
    region_order       INTEGER      NOT NULL DEFAULT 0,
    wrong_probability  INTEGER      CHECK (wrong_probability IS NULL OR (wrong_probability >= 0 AND wrong_probability <= 100)),
    band               VARCHAR(16)  NOT NULL DEFAULT 'unknown',
    detection_status   VARCHAR(16)  NOT NULL DEFAULT 'ok',
    -- Graphics inside this question, in page pixel space, as JSON
    -- {"v":2,"items":[{"x","y","width","height"}]}. The question *text* stays
    -- text; only these rectangles are carried over as pictures when printing.
    figures_json       TEXT         NOT NULL DEFAULT '[]',
    created_at         DATETIME     NOT NULL,
    updated_at         DATETIME     NOT NULL,
    CHECK (width > 0 AND height > 0 AND x >= 0 AND y >= 0)
);
CREATE INDEX IF NOT EXISTS ix_question_regions_paper_id ON question_regions (paper_id);
CREATE INDEX IF NOT EXISTS ix_question_regions_page_id  ON question_regions (page_id);
CREATE INDEX IF NOT EXISTS ix_question_regions_qid      ON question_regions (question_id);
CREATE INDEX IF NOT EXISTS ix_region_paper_page_order   ON question_regions (paper_id, page_no, region_order);
CREATE INDEX IF NOT EXISTS ix_region_paper_band         ON question_regions (paper_id, band);

-- Per-user draft selection (NOT the错题本 fact).
CREATE TABLE IF NOT EXISTS question_region_selections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    region_id   INTEGER  NOT NULL REFERENCES question_regions(id) ON DELETE CASCADE,
    user_id     INTEGER  NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    is_selected BOOLEAN  NOT NULL DEFAULT 0,
    created_at  DATETIME NOT NULL,
    updated_at  DATETIME NOT NULL,
    CONSTRAINT uq_region_selection UNIQUE (region_id, user_id)
);
CREATE INDEX IF NOT EXISTS ix_selections_region ON question_region_selections (region_id);
CREATE INDEX IF NOT EXISTS ix_selections_user   ON question_region_selections (user_id);

-- The authoritative record that a question IS in the mistake book.
CREATE TABLE IF NOT EXISTS mistakes (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                   INTEGER     NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    question_id               INTEGER     NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    paper_id                  INTEGER     REFERENCES papers(id) ON DELETE SET NULL,
    region_id                 INTEGER     REFERENCES question_regions(id) ON DELETE SET NULL,
    question_image_key        VARCHAR(512) NOT NULL DEFAULT '',
    -- Pictures lifted out of the paper (apparatus, circuits, curves), as JSON
    -- {"v":2,"items":[{"key","width","height","labels"}]}. They are re-inserted
    -- after the question text, so the printed book keeps text as text.
    figures_json              TEXT        NOT NULL DEFAULT '[]',
    selection_source          VARCHAR(32) NOT NULL DEFAULT 'user_explicit',
    wrong_probability_snapshot INTEGER,
    wrong_count               INTEGER     NOT NULL DEFAULT 1,
    status                    VARCHAR(16) NOT NULL DEFAULT 'active',
    note                      TEXT        NOT NULL DEFAULT '',
    created_at                DATETIME    NOT NULL,
    updated_at                DATETIME    NOT NULL,
    CONSTRAINT uq_mistake_user_question UNIQUE (user_id, question_id)
);
CREATE INDEX IF NOT EXISTS ix_mistakes_user_id  ON mistakes (user_id);
CREATE INDEX IF NOT EXISTS ix_mistakes_qid      ON mistakes (question_id);
CREATE INDEX IF NOT EXISTS ix_mistakes_paper_id ON mistakes (paper_id);
CREATE INDEX IF NOT EXISTS ix_mistake_user_status ON mistakes (user_id, status);

CREATE TABLE IF NOT EXISTS tags (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER     NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       VARCHAR(64) NOT NULL,
    created_at DATETIME    NOT NULL,
    updated_at DATETIME    NOT NULL,
    CONSTRAINT uq_tag_user_name UNIQUE (user_id, name)
);
CREATE INDEX IF NOT EXISTS ix_tags_user_id ON tags (user_id);

CREATE TABLE IF NOT EXISTS question_tags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    tag_id      INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    CONSTRAINT uq_question_tag UNIQUE (question_id, tag_id)
);
CREATE INDEX IF NOT EXISTS ix_question_tags_q ON question_tags (question_id);
CREATE INDEX IF NOT EXISTS ix_question_tags_t ON question_tags (tag_id);

CREATE TABLE IF NOT EXISTS knowledge_points (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       VARCHAR(128) NOT NULL,
    subject    VARCHAR(64)  NOT NULL DEFAULT '未分类',
    created_at DATETIME     NOT NULL,
    updated_at DATETIME     NOT NULL,
    CONSTRAINT uq_kp UNIQUE (user_id, subject, name)
);
CREATE INDEX IF NOT EXISTS ix_kp_user_id ON knowledge_points (user_id);

CREATE TABLE IF NOT EXISTS question_knowledge_points (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id        INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    knowledge_point_id INTEGER NOT NULL REFERENCES knowledge_points(id) ON DELETE CASCADE,
    CONSTRAINT uq_q_kp UNIQUE (question_id, knowledge_point_id)
);
CREATE INDEX IF NOT EXISTS ix_qkp_question ON question_knowledge_points (question_id);
CREATE INDEX IF NOT EXISTS ix_qkp_kp       ON question_knowledge_points (knowledge_point_id);

CREATE TABLE IF NOT EXISTS ai_analysis (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                INTEGER      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    question_id            INTEGER      NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    provider               VARCHAR(32)  NOT NULL DEFAULT '',
    model                  VARCHAR(64)  NOT NULL DEFAULT '',
    subject                VARCHAR(64)  NOT NULL DEFAULT '',
    knowledge_points_json  TEXT         NOT NULL DEFAULT '[]',
    difficulty             INTEGER      NOT NULL DEFAULT 3,
    error_reason           VARCHAR(64)  NOT NULL DEFAULT '',
    error_detail           TEXT         NOT NULL DEFAULT '',
    correct_solution       TEXT         NOT NULL DEFAULT '',
    solution_steps_json    TEXT         NOT NULL DEFAULT '[]',
    raw_response           TEXT         NOT NULL DEFAULT '',
    is_fallback            BOOLEAN      NOT NULL DEFAULT 0,
    created_at             DATETIME     NOT NULL,
    updated_at             DATETIME     NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ai_analysis_user ON ai_analysis (user_id);
CREATE INDEX IF NOT EXISTS ix_ai_analysis_qid  ON ai_analysis (question_id);

CREATE TABLE IF NOT EXISTS review_plans (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER     NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mistake_id  INTEGER     NOT NULL REFERENCES mistakes(id) ON DELETE CASCADE,
    stage       INTEGER     NOT NULL DEFAULT 0,
    due_at      DATETIME    NOT NULL,
    status      VARCHAR(16) NOT NULL DEFAULT 'pending',
    last_result VARCHAR(16) NOT NULL DEFAULT '',
    created_at  DATETIME    NOT NULL,
    updated_at  DATETIME    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_review_plans_user    ON review_plans (user_id);
CREATE INDEX IF NOT EXISTS ix_review_plans_mistake ON review_plans (mistake_id);
CREATE INDEX IF NOT EXISTS ix_review_due           ON review_plans (user_id, status, due_at);

CREATE TABLE IF NOT EXISTS review_records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id     INTEGER     NOT NULL REFERENCES review_plans(id) ON DELETE CASCADE,
    user_id     INTEGER     NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    result      VARCHAR(16) NOT NULL DEFAULT 'pass',
    reviewed_at DATETIME    NOT NULL,
    created_at  DATETIME    NOT NULL,
    updated_at  DATETIME    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_review_records_plan ON review_records (plan_id);
CREATE INDEX IF NOT EXISTS ix_review_records_user ON review_records (user_id);

CREATE TABLE IF NOT EXISTS practice_questions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id            INTEGER      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    question_id        INTEGER      REFERENCES questions(id) ON DELETE CASCADE,
    source_question_id INTEGER      REFERENCES questions(id) ON DELETE SET NULL,
    text               TEXT         NOT NULL DEFAULT '',
    answer             TEXT         NOT NULL DEFAULT '',
    explanation        TEXT         NOT NULL DEFAULT '',
    knowledge_point    VARCHAR(128) NOT NULL DEFAULT '',
    created_at         DATETIME     NOT NULL,
    updated_at         DATETIME     NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_practice_user ON practice_questions (user_id);
CREATE INDEX IF NOT EXISTS ix_practice_qid  ON practice_questions (question_id);

CREATE TABLE IF NOT EXISTS attachments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ref_type     VARCHAR(32)  NOT NULL DEFAULT 'question',
    ref_id       INTEGER      NOT NULL DEFAULT 0,
    storage_key  VARCHAR(512) NOT NULL,
    content_type VARCHAR(128) NOT NULL DEFAULT '',
    byte_size    INTEGER      NOT NULL DEFAULT 0,
    created_at   DATETIME     NOT NULL,
    updated_at   DATETIME     NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_attachments_user ON attachments (user_id);

CREATE TABLE IF NOT EXISTS system_settings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        VARCHAR(128) NOT NULL UNIQUE,
    value      TEXT         NOT NULL DEFAULT '',
    is_secret  BOOLEAN      NOT NULL DEFAULT 0,
    created_at DATETIME     NOT NULL,
    updated_at DATETIME     NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_system_settings_key ON system_settings (key);

CREATE TABLE IF NOT EXISTS usage_stats (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER     NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event      VARCHAR(64) NOT NULL,
    value      FLOAT       NOT NULL DEFAULT 1.0,
    created_at DATETIME    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_usage_stats_user  ON usage_stats (user_id);
CREATE INDEX IF NOT EXISTS ix_usage_stats_event ON usage_stats (event);

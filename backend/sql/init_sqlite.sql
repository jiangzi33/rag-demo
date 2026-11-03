-- SQLite 版建表脚本（本地快速体验 / 单元测试用，字段与 init.sql 保持一致）
CREATE TABLE IF NOT EXISTS users
(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username   VARCHAR(32)  NOT NULL UNIQUE,
    password   VARCHAR(128) NOT NULL,
    email      VARCHAR(128) NOT NULL UNIQUE,
    nickname   VARCHAR(32)  NOT NULL,
    avatar     VARCHAR(255) NULL,
    created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_vip     TINYINT      NOT NULL DEFAULT 0,
    status     TINYINT      NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_created_at ON users (created_at);

CREATE TABLE IF NOT EXISTS knowledge_bases
(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       VARCHAR(64) NOT NULL,
    owner_id   INTEGER     NOT NULL,
    created_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (owner_id, name)
);

CREATE INDEX IF NOT EXISTS idx_kb_updated_at ON knowledge_bases (updated_at);

CREATE TABLE IF NOT EXISTS documents
(
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kb_id        INTEGER      NOT NULL,
    title        VARCHAR(128) NOT NULL,
    content      TEXT         NOT NULL,
    author_id    INTEGER      NOT NULL,
    published_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_doc_kb_updated ON documents (kb_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_doc_author_id ON documents (author_id);

CREATE TABLE IF NOT EXISTS permissions
(
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    permission_type VARCHAR(16) NOT NULL CHECK (permission_type IN ('read', 'write', 'manage')),
    user_id         INTEGER     NOT NULL,
    target_type     VARCHAR(16) NOT NULL CHECK (target_type IN ('kb', 'doc')),
    target_id       INTEGER     NOT NULL,
    status          VARCHAR(16) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accept', 'reject')),
    initiator_id    INTEGER     NOT NULL,
    created_at      DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    granted_at      DATETIME    NULL,
    UNIQUE (user_id, target_type, target_id, status)
);

CREATE INDEX IF NOT EXISTS idx_perm_target ON permissions (target_type, target_id, status);

CREATE TABLE IF NOT EXISTS notifications
(
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER      NOT NULL,
    actor_id    INTEGER      NOT NULL,
    type        VARCHAR(32)  NOT NULL,
    content     VARCHAR(512) NOT NULL,
    target_type VARCHAR(16)  NULL,
    target_id   INTEGER      NULL,
    kb_id       INTEGER      NULL,
    is_read     TINYINT      NOT NULL DEFAULT 0,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_notice_user_read ON notifications (user_id, is_read);

CREATE TABLE IF NOT EXISTS invite_links
(
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    token           VARCHAR(64) NOT NULL UNIQUE,
    target_type     VARCHAR(16) NOT NULL CHECK (target_type IN ('kb', 'doc')),
    target_id       INTEGER     NOT NULL,
    permission_type VARCHAR(16) NOT NULL CHECK (permission_type IN ('read', 'write', 'manage')),
    creator_id      INTEGER     NOT NULL,
    expires_at      DATETIME    NULL,
    max_uses        INTEGER     NULL,
    use_count       INTEGER     NOT NULL DEFAULT 0,
    created_at      DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_link_target ON invite_links (target_type, target_id);

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

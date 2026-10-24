-- 知识库管理系统建表脚本 (MySQL 8.x)
CREATE DATABASE IF NOT EXISTS knowledge_base
    DEFAULT CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE knowledge_base;

CREATE TABLE IF NOT EXISTS users
(
    id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    username   VARCHAR(32)     NOT NULL COMMENT '用户名（登录名，唯一）',
    password   VARCHAR(128)    NOT NULL COMMENT '密码（bcrypt 哈希，不存明文）',
    email      VARCHAR(128)    NOT NULL COMMENT '邮箱（唯一）',
    nickname   VARCHAR(32)     NOT NULL COMMENT '昵称',
    avatar     VARCHAR(255)    NULL     DEFAULT NULL COMMENT '头像URL',
    created_at DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '注册时间',
    is_vip     TINYINT(1)      NOT NULL DEFAULT 0 COMMENT '是否VIP：0-否 1-是',
    status     TINYINT         NOT NULL DEFAULT 1 COMMENT '用户状态：0-禁用 1-正常 2-已注销',
    PRIMARY KEY (id),
    UNIQUE KEY uk_username (username),
    UNIQUE KEY uk_email (email),
    KEY idx_created_at (created_at)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_unicode_ci COMMENT ='用户表';

CREATE TABLE IF NOT EXISTS knowledge_bases
(
    id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    name       VARCHAR(64)     NOT NULL COMMENT '知识库名称（同一管理员下唯一）',
    owner_id   BIGINT UNSIGNED NOT NULL COMMENT '知识库管理员，关联 users.id',
    created_at DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间（由 UPDATE 语句显式维护）',
    PRIMARY KEY (id),
    UNIQUE KEY uk_owner_name (owner_id, name),
    KEY idx_updated_at (updated_at)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_unicode_ci COMMENT ='知识库表';

CREATE TABLE IF NOT EXISTS documents
(
    id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    kb_id        BIGINT UNSIGNED NOT NULL COMMENT '所在知识库，关联 knowledge_bases.id',
    title        VARCHAR(128)    NOT NULL COMMENT '文档标题',
    content      MEDIUMTEXT      NOT NULL COMMENT '文档正文（最大 16MB）',
    author_id    BIGINT UNSIGNED NOT NULL COMMENT '文档作者（创建者），关联 users.id',
    published_at DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '发布日期',
    updated_at   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '最近编辑日期（由 UPDATE 语句显式维护）',
    PRIMARY KEY (id),
    KEY idx_kb_updated (kb_id, updated_at),
    KEY idx_author_id (author_id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_unicode_ci COMMENT ='文档表';

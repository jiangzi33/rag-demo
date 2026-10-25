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

CREATE TABLE IF NOT EXISTS permissions
(
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    permission_type VARCHAR(16)     NOT NULL COMMENT '权限类型：read-读 write-写 manage-管理',
    user_id         BIGINT UNSIGNED NOT NULL COMMENT '被授权用户，关联 users.id',
    target_type     VARCHAR(16)     NOT NULL COMMENT '授权对象类型：kb-知识库 doc-文档',
    target_id       BIGINT UNSIGNED NOT NULL COMMENT '授权对象ID，关联 knowledge_bases.id 或 documents.id',
    status          VARCHAR(16)     NOT NULL DEFAULT 'pending' COMMENT '授权状态：pending-待处理 accept-已接受 reject-已拒绝',
    initiator_id    BIGINT UNSIGNED NOT NULL COMMENT '发起人：等于 user_id 为用户申请，否则为管理者邀请',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '发起时间（邀请 / 申请时间）',
    granted_at      DATETIME        NULL     DEFAULT NULL COMMENT '授权时间（状态变为 accept 时写入）',
    PRIMARY KEY (id),
    -- 每种状态最多一条：已生效权限与追加申请的更高权限（pending）可以并存
    UNIQUE KEY uk_user_target_status (user_id, target_type, target_id, status),
    KEY idx_target (target_type, target_id, status),
    CONSTRAINT chk_permission_type CHECK (permission_type IN ('read', 'write', 'manage')),
    CONSTRAINT chk_target_type CHECK (target_type IN ('kb', 'doc')),
    CONSTRAINT chk_status CHECK (status IN ('pending', 'accept', 'reject'))
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_unicode_ci COMMENT ='权限表（知识库 / 文档授权）';

CREATE TABLE IF NOT EXISTS notifications
(
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
    user_id     BIGINT UNSIGNED NOT NULL COMMENT '接收人，关联 users.id',
    actor_id    BIGINT UNSIGNED NOT NULL COMMENT '操作人，关联 users.id',
    type        VARCHAR(32)     NOT NULL COMMENT '类型：perm_invited / perm_approved / perm_rejected / perm_changed / perm_revoked / perm_withdrawn / kb_transferred',
    content     VARCHAR(512)    NOT NULL COMMENT '通知内容（发生时生成，不含操作人）',
    target_type VARCHAR(16)     NULL     DEFAULT NULL COMMENT '相关对象类型：kb / doc',
    target_id   BIGINT UNSIGNED NULL     DEFAULT NULL COMMENT '相关对象ID',
    kb_id       BIGINT UNSIGNED NULL     DEFAULT NULL COMMENT '对象所在知识库，前端拼跳转链接用',
    is_read     TINYINT(1)      NOT NULL DEFAULT 0 COMMENT '是否已读',
    created_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '通知时间',
    PRIMARY KEY (id),
    KEY idx_user_read (user_id, is_read)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_unicode_ci COMMENT ='站内通知表';

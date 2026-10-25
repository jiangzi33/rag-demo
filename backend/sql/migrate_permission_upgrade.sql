-- 已有 MySQL 库升级：支持“已有权限时追加申请更高权限”
-- 唯一键由 (user_id, target_type, target_id) 放宽为 (user_id, target_type, target_id, status)，现有数据无需改动
USE knowledge_base;

ALTER TABLE permissions
    DROP INDEX uk_user_target,
    ADD UNIQUE KEY uk_user_target_status (user_id, target_type, target_id, status);

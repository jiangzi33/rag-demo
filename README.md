# 知识库管理系统 — 用户模块

技术栈：**FastAPI + 手写 SQL（MyBatis 风格 Mapper 层）+ MySQL 8**（后端），**Vue 3 + Element Plus + Vue Router**（前端，CDN 引入，无需构建），JWT 鉴权，bcrypt 密码哈希。

## 目录结构

```
backend/
  sql/init.sql            # MySQL 建表脚本
  sql/init_sqlite.sql     # SQLite 建表脚本（本地体验/测试，启动时自动执行）
  app/
    main.py               # 应用入口：路由注册、静态资源挂载、启动检查表结构
    core/config.py        # 配置（读取 backend/.env）
    core/security.py      # bcrypt 密码哈希、JWT 签发/校验
    core/exceptions.py    # 业务异常 + 统一错误响应
    db/session.py         # 连接池、get_db（≈ SqlSession）
    models/user.py        # User 实体（≈ POJO）、UserStatus 枚举
    mapper/user_mapper.py # 所有 SQL（≈ UserMapper.java + UserMapper.xml）
    schemas/user.py       # 请求/响应模型及校验规则（≈ DTO / VO）
    services/user_service.py  # 业务逻辑 + 事务提交（≈ @Service）
    api/deps.py           # 当前用户 / 管理员鉴权依赖
    api/auth.py           # 注册、登录
    api/users.py          # 个人资料、改密、头像
    api/admin.py          # 管理员：用户列表、状态、VIP
    api/knowledge_bases.py    # 知识库增删改查、转让管理员（models/mapper/schemas/services 下有同名模块）
    api/documents.py          # 文档增删改查（同上）
  tests/test_users.py     # 接口全流程测试（SQLite）
  tests/test_knowledge_bases.py
  tests/test_documents.py
frontend/
  index.html  js/api.js  js/pages.js  js/docs.js  js/app.js  css/style.css
  # 文档页面在 js/docs.js，路由全部嵌在 /kb/:kbId/docs 下，没有独立的文档入口
```

## 快速开始

```bash
# 1. 安装依赖
.venv/bin/pip install -r backend/requirements.txt

# 2. 建库建表（使用 MySQL 时必须执行，启动时会检查 users 表是否存在）
mysql -uroot -p < backend/sql/init.sql

# 3. 配置
cp backend/.env backend/.env   # 修改 DATABASE_URL / JWT_SECRET / ADMIN_USERNAMES

# 4. 启动
cd backend && ../.venv/bin/uvicorn app.main:app --reload --port 8000
```

- 前端页面：http://localhost:8000/
- 接口文档：http://localhost:8000/docs
- 管理员：用 `ADMIN_USERNAMES` 中配置的用户名（默认 `admin`）注册即可获得“用户管理”菜单
- 不想装 MySQL 时可临时用 SQLite：`DATABASE_URL=sqlite:///./dev.db`
- 运行测试：`cd backend && ../.venv/bin/python -m pytest -q`

## 用户表 `users`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT UNSIGNED PK AUTO_INCREMENT | 主键 |
| username | VARCHAR(32) UNIQUE | 用户名，4-32 位，字母开头，字母/数字/下划线 |
| password | VARCHAR(128) | bcrypt 哈希，永不返回给前端 |
| email | VARCHAR(128) UNIQUE | 邮箱，统一转小写存储 |
| nickname | VARCHAR(32) | 昵称，注册不填则默认为用户名 |
| avatar | VARCHAR(255) NULL | 头像 URL（本地上传存于 `backend/uploads/avatars`） |
| created_at | DATETIME | 注册时间，数据库默认 `CURRENT_TIMESTAMP` |
| is_vip | TINYINT(1) | 是否 VIP，默认 0 |
| status | TINYINT | 0-禁用 1-正常 2-已注销（软删除），默认 1 |

## 知识库表 `knowledge_bases`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT UNSIGNED PK AUTO_INCREMENT | 主键 |
| name | VARCHAR(64) | 知识库名称，1-64 字符，同一管理员下唯一（`uk_owner_name`） |
| owner_id | BIGINT UNSIGNED | 知识库管理员，关联 `users.id`；创建者即管理员，可转让 |
| created_at | DATETIME | 创建时间，数据库默认 `CURRENT_TIMESTAMP` |
| updated_at | DATETIME | 更新时间，由 UPDATE 语句显式写 `CURRENT_TIMESTAMP`（兼容 SQLite） |

可见性：所有登录用户都能看到全部知识库；改名需要管理权限，转让 / 删除仅限拥有者或系统管理员（`ADMIN_USERNAMES`），详见下方权限表。

## 文档表 `documents`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT UNSIGNED PK AUTO_INCREMENT | 主键 |
| kb_id | BIGINT UNSIGNED | 所在知识库，关联 `knowledge_bases.id` |
| title | VARCHAR(128) | 文档标题，1-128 字符 |
| content | MEDIUMTEXT | 文档正文，富文本 HTML（最大 16MB，接口限制 200 万字符；图片以 URL 引用，不内嵌 base64） |
| author_id | BIGINT UNSIGNED | 文档作者（创建者），关联 `users.id`，编辑不改变作者 |
| published_at | DATETIME | 发布日期，数据库默认 `CURRENT_TIMESTAMP` |
| updated_at | DATETIME | 最近编辑日期，仅在标题或正文变化时刷新 |

索引：`idx_kb_updated (kb_id, updated_at)` 支撑“知识库内按最近编辑排序”的列表；`idx_author_id` 留给“我写的文档”。
正文由前端富文本编辑器（wangEditor 5）生成，支持字体、字号、文字颜色、背景色、对齐、列表、表格、图片等。
保存时服务端用 nh3 按白名单过滤 HTML（`app/core/html_sanitizer.py`），阅读页展示前再用 DOMPurify 过滤一次。
正文图片上传到 `uploads/doc-images/{kb_id}/`，按文件头识别真实格式（png/jpg/gif/webp，≤10MB），删除知识库时整个目录一并删除；阅读页图片按页面宽度等比自适应。

知识库内的全部文档对所有登录用户可见（列表只含标题、作者、时间），打开 / 编辑 / 删除分别需要读 / 写 / 管理权限。文档增删改会同步刷新知识库的 `updated_at`；删除知识库时同一事务内删除其下所有文档。

## 权限表 `permissions`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT UNSIGNED PK AUTO_INCREMENT | 主键 |
| permission_type | VARCHAR(16) | 权限类型：`read` 读 / `write` 写 / `manage` 管理 |
| user_id | BIGINT UNSIGNED | 被授权用户，关联 `users.id` |
| target_type | VARCHAR(16) | 授权对象类型：`kb` 知识库 / `doc` 文档 |
| target_id | BIGINT UNSIGNED | 授权对象 ID |
| status | VARCHAR(16) | 授权状态：`pending` 待处理 / `accept` 已接受 / `reject` 已拒绝 |
| initiator_id | BIGINT UNSIGNED | 发起人：等于 `user_id` 为用户申请，否则为管理者邀请 |
| created_at | DATETIME | 发起时间 |
| granted_at | DATETIME NULL | 授权时间，状态变为 `accept` 时写入 |

约束：`uk_user_target_status (user_id, target_type, target_id, status)` 保证同一用户对同一对象每种状态最多一条记录：已生效的权限和追加申请的更高权限可以并存（被拒绝后再次发起时复用 reject 记录并重置为 pending）；三个枚举字段都有 CHECK 约束。旧库升级执行 `sql/migrate_permission_upgrade.sql`。

**授权流程**：邀请由管理者发起、被邀请人接受 / 拒绝；申请由用户发起、管理者同意 / 驳回。只有 `accept` 的记录生效。
已有权限时可追加申请（或被邀请）更高的权限，如读 → 写 / 管理：审批期间原权限照常生效，通过后替换为新权限，驳回则保持原权限；列表和详情接口返回 `pending_permission` 表示审批中的申请。

**有效权限**（`app/services/access_service.py`）：无 < 读 < 写 < 管理 < 拥有者（知识库 owner 或系统管理员）。知识库授权覆盖其下所有文档，文档授权只对该篇生效，两者取高。

| 操作 | 所需权限 |
|---|---|
| 查看知识库列表、详情、文档列表 | 无（所有登录用户可见全部知识库和文档，`my_permission` 标明自己的权限） |
| 查看文档 | 文档读 |
| 新建文档 | 知识库写 |
| 编辑文档、上传正文图片 | 文档写 |
| 删除文档 | 文档管理 |
| 修改知识库名称 | 知识库管理 |
| 邀请、审批、修改、撤销授权 | 对象的管理权限（知识库管理者同时管理其下文档的授权） |
| 删除知识库、转让知识库 | 拥有者 |

对象不存在返回 404；存在但权限不足返回 403（前端据此显示“申请权限”入口，用户可针对知识库或单篇文档申请读 / 写 / 管理权限）。删除文档 / 知识库时同一事务内清理相关授权。

## 通知表 `notifications`

| 字段 | 类型 | 说明 |
|---|---|---|
| id | BIGINT UNSIGNED PK AUTO_INCREMENT | 主键 |
| user_id | BIGINT UNSIGNED | 接收人 |
| actor_id | BIGINT UNSIGNED | 操作人 |
| type | VARCHAR(32) | `perm_invited` 邀请 / `perm_approved` 申请通过 / `perm_rejected` 申请驳回 / `perm_changed` 权限调整 / `perm_revoked` 权限撤销 / `perm_withdrawn` 邀请撤回或申请被删 / `kb_transferred` 知识库转让给我 |
| content | VARCHAR(512) | 通知内容，发生时生成（不含操作人），对象之后改名 / 删除不影响 |
| target_type / target_id / kb_id | | 相关对象，前端拼跳转链接 |
| is_read | TINYINT(1) | 是否已读 |
| created_at | DATETIME | 通知时间 |

别人变更了我的权限时，在同一事务中写入通知；自己操作自己的记录（申请、撤回、放弃）不通知。前端顶部铃铛每 30 秒轮询未读数，有新通知时弹出提醒，点击跳转到相关知识库 / 文档。旧 MySQL 库启动时自动建表。

## 数据访问层（MyBatis 风格）

不使用 ORM，所有 SQL 集中写在 `app/mapper/user_mapper.py`，分层与 Spring Boot + MyBatis 一致：

```
Controller (app/api)  →  Service (app/services)  →  Mapper (app/mapper)  →  MySQL
```

| MyBatis | 本项目 |
|---|---|
| `#{username}` 预编译参数 | `:username`（参数绑定，防 SQL 注入） |
| `<sql id="Base_Column_List">` | `BASE_COLUMN_LIST` |
| `<resultMap>` | `User(**row)` 映射成实体 |
| `<where><if test>` | `_build_where()` |
| `<set><if test>` / `updateByPrimaryKeySelective` | `_build_set()` / `update_selective()` |
| `useGeneratedKeys` | `insert()` 返回 `lastrowid` |
| `SqlSession` + `@Transactional` | `get_db()` 提供连接，service 中 `conn.commit()`，异常时自动回滚 |

Mapper 方法：`select_by_id`、`select_by_username`、`select_by_email`、`count_by_condition`、`select_page`、`insert`、`update_selective`、`update_password`、`update_avatar`、`update_status`、`update_vip`、`delete_by_id`。

## 接口一览

统一响应：`{"code": 0, "message": "ok", "data": ...}`，出错时 `code` 为业务错误码（见 `core/exceptions.py`），HTTP 状态码同步设置。
需要登录的接口带请求头 `Authorization: Bearer <token>`。

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| POST | /api/auth/register | 注册，成功直接返回 token | 公开 |
| POST | /api/auth/login | 登录，`account` 支持用户名或邮箱 | 公开 |
| GET | /api/users/me | 当前用户信息 | 登录 |
| PUT | /api/users/me | 修改昵称 / 邮箱 | 登录 |
| PUT | /api/users/me/password | 修改密码（需原密码） | 登录 |
| POST | /api/users/me/avatar | 上传头像（png/jpg/gif/webp，≤2MB） | 登录 |
| GET | /api/admin/users | 分页列表，支持 keyword / status / is_vip 筛选 | 管理员 |
| PATCH | /api/admin/users/{id}/status | 启用 / 禁用 / 注销 | 管理员 |
| PATCH | /api/admin/users/{id}/vip | 设置 / 取消 VIP | 管理员 |
| POST | /api/knowledge-bases | 创建知识库（创建者即管理员） | 登录 |
| GET | /api/knowledge-bases | 知识库列表，分页，支持 keyword；`scope=all`（默认，全部）/ `mine`（拥有的 + 被授权的），返回 `my_permission` | 登录 |
| GET | /api/knowledge-bases/{id} | 知识库详情 | 登录 |
| PUT | /api/knowledge-bases/{id} | 修改名称 | 知识库管理 |
| PATCH | /api/knowledge-bases/{id}/owner | 转让管理员 | 拥有者 |
| DELETE | /api/knowledge-bases/{id} | 删除知识库（连同其下文档） | 拥有者 |
| POST | /api/knowledge-bases/{kb_id}/documents | 新建文档（当前用户为作者） | 知识库写 |
| GET | /api/knowledge-bases/{kb_id}/documents | 全部文档，不含正文，支持标题 keyword，返回每篇的 `my_permission` | 登录 |
| POST | /api/knowledge-bases/{kb_id}/documents/images | 上传正文图片，返回图片 URL | 知识库或其中某篇文档写 |
| GET | /api/knowledge-bases/{kb_id}/documents/{id} | 文档详情（含正文） | 文档读 |
| PUT | /api/knowledge-bases/{kb_id}/documents/{id} | 编辑标题和正文 | 文档写 |
| DELETE | /api/knowledge-bases/{kb_id}/documents/{id} | 删除文档 | 文档管理 |
| POST | /api/permissions/invitations | 邀请用户（按用户名），被邀请人接受后生效 | 对象管理权限 |
| POST | /api/permissions/applications | 申请权限，管理者同意后生效 | 登录 |
| GET | /api/permissions/mine | 与我有关的授权，可按 status 筛选 | 登录 |
| GET | /api/permissions/approvals | 待我审批的申请 | 登录 |
| GET | /api/notifications | 我的通知，分页，`unread_only` 只看未读，返回 `unread` 未读总数 | 登录 |
| POST | /api/notifications/{id}/read | 标记已读 | 本人 |
| POST | /api/notifications/read-all | 全部标记已读 | 登录 |
| GET | /api/permissions?target_type=&target_id= | 某个知识库 / 文档的授权列表 | 对象管理权限 |
| POST | /api/permissions/{id}/accept | 接受邀请 / 同意申请 | 被邀请人 / 对象管理权限 |
| POST | /api/permissions/{id}/reject | 拒绝邀请 / 驳回申请 | 被邀请人 / 对象管理权限 |
| PATCH | /api/permissions/{id} | 修改权限类型 | 对象管理权限 |
| DELETE | /api/permissions/{id} | 撤销 / 放弃 / 撤回 | 对象管理权限 / 被授权人 / 待处理请求的发起人 |

## 设计要点

- 鉴权时每次请求都会回库检查用户状态，管理员禁用账号后，该用户已签发的 token 立即失效。
- 登录失败统一提示“账号或密码错误”，用户不存在时也执行一次哈希校验，防止通过响应时间枚举用户名。
- 并发注册导致的唯一索引冲突会被捕获，转换为友好的业务错误。
- 前后端使用同一套校验规则（用户名格式、密码需含字母和数字等）。
- 管理员身份目前由配置项 `ADMIN_USERNAMES` 决定，没有新增表字段；后续做知识库/团队权限时，建议改成独立的角色表（RBAC）。

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
  tests/test_users.py     # 接口全流程测试（SQLite）
  tests/test_knowledge_bases.py
frontend/
  index.html  js/api.js  js/pages.js  js/app.js  css/style.css
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

权限：知识库管理员或系统管理员（`ADMIN_USERNAMES`）可查看 / 改名 / 转让 / 删除；无权限时统一返回 404，不暴露他人知识库是否存在。

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
| GET | /api/knowledge-bases | 我管理的知识库，分页，支持 keyword | 登录 |
| GET | /api/knowledge-bases/{id} | 知识库详情 | 知识库管理员 |
| PUT | /api/knowledge-bases/{id} | 修改名称 | 知识库管理员 |
| PATCH | /api/knowledge-bases/{id}/owner | 转让管理员 | 知识库管理员 |
| DELETE | /api/knowledge-bases/{id} | 删除知识库 | 知识库管理员 |

## 设计要点

- 鉴权时每次请求都会回库检查用户状态，管理员禁用账号后，该用户已签发的 token 立即失效。
- 登录失败统一提示“账号或密码错误”，用户不存在时也执行一次哈希校验，防止通过响应时间枚举用户名。
- 并发注册导致的唯一索引冲突会被捕获，转换为友好的业务错误。
- 前后端使用同一套校验规则（用户名格式、密码需含字母和数字等）。
- 管理员身份目前由配置项 `ADMIN_USERNAMES` 决定，没有新增表字段；后续做知识库/团队权限时，建议改成独立的角色表（RBAC）。

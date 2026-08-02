# MailPilot API 接口说明

## 1. 基本约定

- 开发直连地址：`http://localhost:8000`
- Nginx 统一入口：`http://localhost:8080`
- API 前缀：`/api/v1`
- OpenAPI：`/docs`
- 除登录和健康检查外，接口使用 `Authorization: Bearer <JWT>`。
- 成功与错误响应都包含 `request_id`。
- 用户只能读取自己的邮件、日历、运行、审批和记忆。
- 管理员可以查询全部审批和审计，但不能替其他用户决定审批。

OpenAPI 是请求字段和响应 Schema 的最终事实来源，本文解释业务用途与权限。

## 2. 统一响应

成功：

```json
{
  "success": true,
  "data": {},
  "request_id": "..."
}
```
失败：

```json
{
  "success": false,
  "error": {
    "code": "STABLE_ERROR_CODE",
    "message": "中文安全提示"
  },
  "request_id": "..."
}
```

## 3. 接口清单

### 3.1 系统与认证

| 方法 | 路径 | 权限 | 用途 |
|---|---|---|---|
| GET | `/health/live` | 公开 | 进程是否存活 |
| GET | `/health/ready` | 公开 | PostgreSQL/Redis 是否可用 |
| GET | `/health/version` | 公开 | 应用版本与环境 |
| POST | `/auth/login` | 公开/限流 | 邮箱密码换 JWT |
| GET | `/users/me` | user/admin | 当前用户 |

### 3.2 邮件

| 方法 | 路径 | 权限 | 用途 |
|---|---|---|---|
| POST | `/emails/import` | user/admin | 导入本地测试邮件 |
| GET | `/emails` | user/admin | 当前用户邮件列表 |
| GET | `/emails/{thread_id}` | owner | 邮件详情 |
| POST | `/emails/{thread_id}/process` | owner | 返回 202 并启动后台 AgentRun |

启动处理只表示任务已创建，不表示危险操作已经完成。客户端继续查询 AgentRun 或订阅 SSE。

### 3.3 日历

| 方法 | 路径 | 权限 | 用途 |
|---|---|---|---|
| POST | `/calendar/events/import` | user/admin | 导入本地测试日程 |
| GET | `/calendar/events` | user/admin | 日程列表 |
| GET | `/calendar/events/{event_id}` | owner | 日程详情 |
| GET | `/calendar/availability` | user/admin | 只读检查时间冲突 |

### 3.4 审批

| 方法 | 路径 | 权限 | 用途 |
|---|---|---|---|
| GET | `/approvals` | user/admin | 用户看自己的；admin 可只读查看全部 |
| GET | `/approvals/{approval_id}` | owner/admin | 审批详情 |
| POST | `/approvals/{id}/approve` | owner | 接受 |
| POST | `/approvals/{id}/reject` | owner | 拒绝 |
| POST | `/approvals/{id}/approve-with-modifications` | owner | 参数校验后修改并接受 |
| POST | `/approvals/{id}/request-regeneration` | owner | 提供反馈并重新生成 |

审批决定后使用同一个 `thread_id` 和 `Command(resume=...)` 恢复。已完成审批不能被第二次改变。

### 3.5 AgentRun 与 SSE

| 方法 | 路径 | 权限 | 用途 |
|---|---|---|---|
| GET | `/agent-runs` | user/admin | 运行列表 |
| GET | `/agent-runs/{run_id}` | owner | 状态、结果、Token、错误 |
| GET | `/agent-runs/{run_id}/events/history` | owner | 按 sequence 增量查询轨迹 |
| GET | `/agent-runs/{run_id}/events` | owner | SSE 实时事件 |

SSE 支持 `after` 或 `Last-Event-ID` 断线续传。事件只保存安全摘要，不复制完整邮件和 Prompt。

### 3.6 长期记忆

| 方法 | 路径 | 权限 | 用途 |
|---|---|---|---|
| POST | `/memories` | user/admin | 创建当前用户记忆 |
| GET | `/memories` | user/admin | 当前用户记忆列表 |
| GET | `/memories/{memory_id}` | owner | 详情 |
| GET | `/memories/{memory_id}/versions` | owner | 不可变版本历史 |
| PUT | `/memories/{memory_id}` | owner | expected_version 乐观并发更新 |
| DELETE | `/memories/{memory_id}` | owner | 删除业务事实并在下次同步清理 Store |

### 3.7 审计

| 方法 | 路径 | 权限 | 用途 |
|---|---|---|---|
| GET | `/audit-logs` | admin | 按用户、动作分页查询审计 |

审计记录不包含密码、完整令牌、完整邮件正文和记忆正文。

## 4. 状态码

| 状态码 | 含义 |
|---:|---|
| 200 | 查询或决定成功 |
| 201 | 资源创建成功 |
| 202 | Agent 后台任务已接受 |
| 204 | 删除成功且无正文 |
| 400/422 | 请求字段或业务参数错误 |
| 401 | 未登录或 JWT 无效 |
| 403 | 已登录但角色/资源权限不足 |
| 404 | 资源不存在；跨用户访问也统一使用 404 |
| 409 | 版本冲突、重复决定或幂等执行进行中 |
| 429 | 登录限流 |
| 503 | 数据库、Redis 或安全基础设施不可用 |

## 5. 一次前端调用

```text
Streamlit 登录
→ POST /auth/login
→ 保存 JWT 到当前 Session
→ GET /emails
→ POST /emails/{thread_id}/process
→ 得到 agent_run_id
→ GET /agent-runs/{id}/events
→ waiting_approval
→ GET /approvals/{id}
→ POST /approvals/{id}/approve
→ SSE 继续收到恢复后的事件
→ completed
```

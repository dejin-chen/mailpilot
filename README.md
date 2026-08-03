# MailPilot：企业邮件与日程协同 Agent

MailPilot 是一个用于学习和求职展示的企业级 Agent 项目。项目采用外层确定性 LangGraph 工作流和内层受控 MCP 工具调用，重点展示状态管理、人工审批、长期记忆、安全、评测、可观测性和工程化部署。

当前已完成第 1～10 阶段：具备用户认证、邮件与日历业务基础、两个 MCP Server、安全 MCP Client，以及由 Typed State、结构化模型节点、条件路由、只读工具执行和明确结束状态组成的确定性 LangGraph 核心工作流；审批恢复、长期记忆、安全加固、SSE 工作台、Langfuse/DeepEval 和 Docker Compose + Nginx 部署均已形成可测试闭环，并提供完整中文技术文档及求职材料。

## 当前已完成

- Python 3.12 工程和 `pyproject.toml` 依赖管理。
- FastAPI 应用工厂、OpenAPI 文档和版本化 API。
- Pydantic Settings 环境配置与启动时校验。
- JSON 结构化日志、`request_id` 和敏感字段脱敏。
- SQLAlchemy 2.x 异步 Engine、Session 和连接池。
- Redis 异步客户端。
- Alembic 异步迁移环境和初始空迁移基线。
- 存活检查与 PostgreSQL/Redis 就绪检查。
- 统一成功/错误响应和基础全局异常处理。
- pytest、pytest-asyncio、覆盖率和 Ruff 配置。
- PostgreSQL、Redis、迁移任务和后端的 Docker Compose 基础服务。
- User 表、Argon2 密码哈希、JWT 登录与当前用户接口。
- EmailThread、EmailMessage、邮件导入事务、重复导入保护和用户数据隔离。
- 邮件表 `20260719_0003` 迁移，以及 Mock 与真实 PostgreSQL 测试。
- CalendarEvent、时间冲突查询、日历幂等保护和 `20260719_0004` 迁移。
- LocalMailProvider、LocalCalendarProvider 及可替换 Provider 协议。
- 邮件导入/列表/详情 API，以及日历导入/列表/详情/可用性 API。
- 可重复执行的中文演示数据脚本和跨用户权限隔离测试。
- 两个独立 FastMCP Server，共暴露 10 个邮件与日历工具。
- `langchain-mcp-adapters` 客户端、显式工具白名单、超时和有限重试。
- MCP 内部服务 Token、隐藏的运行时用户身份和 `request_id` 传递。
- 只读工具自动执行；发送邮件和日历写操作在审批完成前强制拒绝。
- 邮件草稿幂等键、数据库唯一约束和 `20260720_0005` 迁移。
- 真实 Streamable HTTP、LangChain Adapter、MCP Server 与 PostgreSQL 集成测试。
- 第 4 阶段 4.1：Typed State、追加型 Reducer、Pydantic 模型输出合同和 Fake 模型测试。
- OpenAI Compatible 模型配置、结构化输出、有限重试、Token 与延迟统计边界。
- 结构化输出无效时进行有限重试，所有尝试的 Token 和总耗时都会累计；结构化抽取固定使用低随机性配置。
- 默认优化链路保留快速分类，将非忽略邮件的意图提取和计划决策合并为一次 Pydantic 结构化调用；可用 `AGENT_ANALYSIS_MODE=sequential` 回退到基线链路。
- Python 根据精简模型决策生成受控执行计划，并确定草稿用途、收件人、抄送和回复主题；模型只负责需要自然语言能力的语义判断和正文生成。
- 第 4 阶段 4.2：邮件读取、分类、意图提取和执行计划四个节点。
- 不可信邮件 Prompt 隔离、只读工具参数 Schema、会议查日历策略校验。
- Fake 模型测试和真实 DeepSeek 结构化输出冒烟测试。
- 第 4 阶段 4.3：正式 StateGraph、普通 Edge、Conditional Edge 和三种收尾路线。
- 受控只读 MCP 工具执行、标准结果回写、调用计数和首次失败停止。
- 完整 Graph 测试覆盖会议工具路线、忽略路线、模型失败和无工具计划。
- 第 5 阶段 5.1：AgentRun、ApprovalRequest、AuditLog 模型和正式数据库迁移。
- 审批幂等创建、用户隔离、行锁、单次决定、有限状态机和事务内审计。
- 第 5 阶段 5.2：`langgraph-checkpoint-postgres`、安全类型允许列表和 psycopg 异步连接池。
- 四种危险写操作的精确参数 Schema、幂等审批准备节点和 `interrupt` 等待节点。
- 相同 `thread_id` 跨 PostgreSQL 连接关闭与重开后继续恢复，且不重复创建审批。
- Alembic 忽略 LangGraph 自管内部表，Docker 迁移任务同时完成业务迁移和 Checkpointer 初始化。
- 第 5 阶段 5.3：审批列表、详情、接受、拒绝、修改后接受和反馈重新生成 API。
- 普通用户审批数据隔离、管理员只读查看全部审批，以及决定操作的所有者权限校验。
- 审批决定先落库和审计，再按 `AgentRun.graph_thread_id` 发出 `Command(resume=...)`。
- 相同决定重复提交可安全重试恢复，不重复写审批审计；修改参数按对应写工具 Schema 校验。
- 第 5 阶段 5.4：审批接受后进入唯一的 `execute_approved_action` 节点；拒绝和反馈分支不会调用写工具。
- MCP Server 根据内部用户身份、审批状态、操作类型、最终参数和稳定幂等键进行二次校验。
- `send_email`、`create_event`、`reschedule_event`、`cancel_event` 已调用真实业务 Service 和 PostgreSQL。
- `tool_call_logs` 保存执行中、成功、失败和结果未知状态，`20260724_0007` 迁移提供用户级幂等唯一约束。
- 写工具不进行自动重试；重复调用已成功操作时复用原结果，超时结果未知时停止并等待核对。
- 第 5 阶段 5.5：新增完整邮件处理 Graph，使用同一 `thread_id` 从分析运行到审批暂停并在决定后继续。
- 模型生成回复草稿后，Python 强制校验收件人只能是原发件人，再通过 MCP 幂等保存本地草稿。
- 会议时间完整且 `check_availability` 返回可用时，Python 生成 `create_event` 审批方案；冲突或信息不全时生成回复/追问草稿。
- 文字反馈会产生版本递增的新草稿和新审批单，最多重新生成两次，重复提交同一反馈不会再次生成。
- 新增 `POST /api/v1/emails/{thread_id}/process`、AgentRun 列表和状态详情接口。
- AgentRun 保存分类、意图、计划、工具结果、草稿、审批、最终结果和 Token 汇总，但不复制完整邮件正文和 Prompt。
- `20260724_0008` 为 AgentRun 增加 `workflow_name`，确保旧审批闸门和新完整工作流都能恢复正确的 Graph。
- 第 6 阶段 6.1：新增邮件风格、日历偏好和联系人三类结构化长期记忆。
- `memory_profiles` 保存稳定身份和当前版本号，`memory_profile_versions` 追加不可变内容版本与来源。
- 所有 Repository 查询强制包含 `user_id`；其他用户访问统一表现为 404，列表不会混入他人数据。
- 修改接口使用 `expected_version` 防止旧页面覆盖新修改，删除时级联清理正文版本。
- 记忆创建、读取、列表、修改和删除均写入不包含偏好正文的 `AuditLog`。
- 新增长期记忆创建、列表、详情、修改、删除 API，以及 `20260724_0009` 正式迁移。
- 第 6 阶段 6.2：接入 `AsyncPostgresStore`，业务记忆表作为事实来源，Store 作为 Graph 运行时读取副本。
- namespace 使用应用版本、`user_id` 和记忆类型隔离；相同用户可以跨 `thread_id` 共享，不同用户无法互相读取。
- 每次启动完整邮件处理前同步当前版本并清理已删除副本，Store 同步会写入不含正文的审计记录。
- 完整 Graph 新增 `load_memory` 节点，只精确读取默认邮件风格、默认日历偏好和当前发件人联系人。
- 邮件草稿 Prompt 使用风格、签名和相关联系人称呼；计划 Prompt 只把日历偏好作为约束参考，不能代替真实日历查询。
- 本次实际使用的记忆版本引用进入 State、Checkpoint 和 AgentRun 安全快照，保证审批恢复时上下文稳定。
- 组合迁移任务现在依次完成 Alembic、LangGraph Checkpointer 和 PostgreSQL Store 的幂等初始化。
- 第 6 阶段 6.3：Python 先识别“以后、默认、请记住”等明确长期信号，一次性反馈只重生成当前方案。
- LLM 只生成经过 Pydantic 校验的局部记忆 Patch；Python 再校验证据必须来自反馈原文，联系人只能是当前发件人。
- MemoryService 在事务内锁定档案，把 Patch 合并到旧值并追加不可变版本，不允许模型直接写数据库或覆盖整份偏好。
- `approval_request_id` 作为可信来源引用，`20260725_0010` 唯一约束保证工作流重复恢复不会重复创建记忆版本。
- 记忆更新判断、结果和新增模型用量进入 State、Checkpoint 与 AgentRun；审计只记录类型、版本和来源，不复制偏好正文。
- 第 6 阶段 6.4：新增 `GET /api/v1/memories/{memory_id}/versions`，按版本号倒序查看当前用户自己的不可变历史和来源。
- 版本历史查询继续使用 `user_id` 隔离；其他用户统一得到 404，读取审计不复制记忆正文。
- 完整一致性测试证明：审批反馈创建新记忆后，下一个新 `thread_id` 会同步并读取该版本。
- 用户删除业务记忆后，再下一个新任务会清理 PostgreSQL Store 的旧副本，Agent 不再使用已删除偏好。
- 第 6 阶段全量验收覆盖 Schema、Repository、Service、API、Store、State、Checkpoint、AgentRun、审计、幂等和用户隔离。
- 第 7 阶段 7.1：新增 `AgentRunEvent` 时间线和 `20260726_0011` 迁移，单次运行的 `sequence` 严格递增且唯一。
- 事件 payload 采用 Pydantic 白名单摘要，不复制完整邮件正文、Prompt、工具完整输出或凭证。
- 第 7 阶段 7.2：邮件处理启动接口改为 `202 Accepted`，先返回 pending `AgentRun`，再以独立数据库 Session 后台执行。
- LangGraph 使用 `astream(stream_mode="updates", version="v2")` 逐节点写事件；审批恢复后继续写入同一时间线。
- 新增事件历史和 SSE 接口，支持 `after`、`Last-Event-ID`、心跳、等待审批/终态关闭和用户隔离。
- 第 7 阶段 7.3：新增登录、邮件处理台、邮件详情与分析、审批中心、日历、执行轨迹和用户记忆七个中文页面。
- Streamlit 只通过 FastAPI 和 JWT 工作，不直接连接 PostgreSQL；执行轨迹可以消费 SSE。
- 第 7 阶段 7.4：Docker Compose 新增带健康检查的 frontend 服务，`uv.lock` 固定 Streamlit 依赖。
- 新增 Windows Python 3.12 兼容 API 启动脚本，确保 psycopg Checkpointer 使用 SelectorEventLoop。
- 第 8 阶段 8.1：JWT 增加 issuer、audience 和 jti 校验；登录使用虚拟密码哈希隐藏账号是否存在的耗时差异。
- 新增统一管理员 RBAC 依赖和 `GET /api/v1/audit-logs`，支持按用户、动作分页筛选，普通用户返回 403。
- 第 8 阶段 8.2：集中式 Agent Python 安全策略检查工具次数、当前邮件资源范围、会议查询时间和草稿收件人。
- 外部邮件、工具结果和用户反馈继续作为不可信数据；危险工具与自动工具允许列表严格分离。
- 第 8 阶段 8.3：Redis 原子脚本按来源地址和账号限制登录尝试，Key 只保存哈希并返回 429/Retry-After。
- 外部写工具增加 `SET NX EX` 分布式锁；Redis 锁阻止并发进入，数据库幂等键继续保证最终不重复。
- Redis Client 按事件循环共享连接池，应用关闭时释放当前循环资源。
- 第 8 阶段 8.4：MCP 只对连接、传输和超时类瞬时错误有限重试，永久参数错误不重试，写操作始终不自动重试。
- 日志补充 access token、数据库连接串等敏感键脱敏；框架 404/405 也使用统一安全错误响应。
- 登录成功、审批、工具执行、AgentRun 和记忆关键操作均可写入或查询 AuditLog。
- 第 9 阶段 9.1：新增可替换的观测协议、Langfuse SDK v4 适配器和 No-op 降级；未配置 Langfuse 时核心功能照常运行。
- 同一 `agent_run_id` 生成稳定 Trace ID，审批暂停前后的 Graph 节点、LLM Generation 和 MCP Span 可汇入同一 Trace。
- 默认不采集完整邮件、Prompt 和工具输出；递归脱敏 API Key、密码、令牌、连接串和邮箱地址。
- 第 9 阶段 9.2：新增 36 条 JSONL 评测案例，覆盖分类、会议、工具、审批、安全、记忆隔离和重复恢复。
- 第 9 阶段 9.3：实现分类、优先级、工具选择、审批拦截、工具成功、任务完成、延迟、Token 和人工介入率等确定性指标。
- DeepEval 用作补充：默认工具正确性评测不联网，草稿质量 Judge 需要显式启用 OpenAI Compatible 模型。
- 第 9 阶段 9.4：提供无模型 reference 契约基线和真实模型组件评测模式，报告同时输出 JSON 与中文 Markdown。
- 第 10 阶段 10.1：新增 Nginx 统一入口，正确代理 FastAPI、SSE、OpenAPI、Streamlit 和 WebSocket，并阻断公网 MCP 路径。
- 第 10 阶段 10.2：新增生产 Compose 覆盖和生产环境示例；生产模式只发布 Nginx，数据库、Redis、API、UI 和 MCP 仅在内部网络。
- 新增不会回显秘密的生产环境预检，以及只读部署验收脚本；开发与生产 Compose 均通过配置解析。
- 第 10 阶段 10.3：补齐中文部署运维、系统架构、API、记忆和测试评测文档，并更新 MCP 设计说明。
- 第 10 阶段 10.4：提供简历三行描述、一分钟/五分钟讲解稿、高频面试问答和项目边界说明。
- 最终实战验收：Ruff、286 项 pytest、36 条 Reference 评测、108 样本真实模型 A/B 和完整 HTTP E2E 均通过；优化后分析链平均 Token 下降 21.01%、P95 下降 16.84%，完整 E2E P95 下降 43.07%。

## 第 1 阶段请求路径

```text
GET /api/v1/health/ready
  -> RequestContextMiddleware 生成 request_id
  -> FastAPI health.ready
  -> 并行调用 check_database / check_redis
  -> SQLAlchemy SELECT 1 / Redis PING
  -> ApiResponse[HealthData]
  -> JSON 响应和 X-Request-ID
```

## 环境要求

- Python 3.12.x
- Docker Desktop 与 Docker Compose
- Git
- uv

当前默认 `python` 如果不是 3.12，请明确使用 Python 3.12 的可执行文件创建环境，不能复用其他项目的虚拟环境。

## 首次配置

在 PowerShell 中进入项目目录：

```powershell
Set-Location D:\ZM\agent_study\mailpilot
Copy-Item .env.example .env
```

编辑 `.env`，至少替换：

- `POSTGRES_PASSWORD`
- `REDIS_PASSWORD`
- `DATABASE_URL` 与 `DOCKER_DATABASE_URL` 中对应的 URL 编码密码
- `REDIS_URL` 与 `DOCKER_REDIS_URL` 中对应的 URL 编码密码
- 已启用认证的 `JWT_SECRET_KEY` 和仅供内部服务使用的 `MCP_INTERNAL_TOKEN`

`.env` 已加入 `.gitignore`，不得提交。

## 本地开发

安装依赖：

```powershell
uv sync --extra dev
```

需要运行第 9 阶段评测时额外安装评测依赖：

```powershell
uv sync --frozen --extra dev --extra eval
```

如果 `uv` 不在 PATH 中，可使用 Python 3.12 执行：

```powershell
& "<Python 3.12 安装路径>\python.exe" -m uv sync --extra dev
```

先启动数据库和 Redis：

```powershell
docker compose --env-file .env up -d postgres redis
```

执行数据库迁移、LangGraph Checkpointer 和长期 Store 初始化：

```powershell
uv run python backend/scripts/migrate.py
```

启动 API。Windows 本地开发使用兼容 psycopg 异步连接池的启动脚本：

```powershell
uv run python backend/scripts/run_api.py
```

启动 Streamlit：

```powershell
$env:MAILPILOT_API_URL = "http://localhost:8000/api/v1"
uv run streamlit run frontend/app.py
```

## Docker Compose 启动

```powershell
docker compose --env-file .env up --build -d
```

该命令会构建一个共享 Python 应用镜像，启动 PostgreSQL、Redis，先运行一次组合迁移任务（Alembic 业务表 + LangGraph Checkpointer/Store 自管表），再启动 FastAPI、Streamlit、Mail MCP、Calendar MCP 和 Nginx。

完整单机生产命令与环境预检见[部署与运维说明](docs/10-部署与运维.md)。

## 访问地址

推荐统一从 Nginx 进入：

- 中文工作台：<http://localhost:8080>
- OpenAPI：<http://localhost:8080/docs>
- Nginx 健康：<http://localhost:8080/healthz>
- API 就绪：<http://localhost:8080/api/v1/health/ready>

以下为开发环境本机直连排错地址，生产覆盖不会发布这些端口：

- OpenAPI：<http://localhost:8000/docs>
- Streamlit 中文工作台：<http://localhost:8501>
- 存活检查：<http://localhost:8000/api/v1/health/live>
- 就绪检查：<http://localhost:8000/api/v1/health/ready>
- 版本信息：<http://localhost:8000/api/v1/health/version>
- 邮箱密码登录：`POST /api/v1/auth/login`
- 当前用户：`GET /api/v1/users/me`，请求头使用 `Authorization: Bearer <JWT>`
- 邮件导入、列表和详情：`POST /api/v1/emails/import`、`GET /api/v1/emails`、`GET /api/v1/emails/{thread_id}`
- 日历导入、列表和详情：`POST /api/v1/calendar/events/import`、`GET /api/v1/calendar/events`、`GET /api/v1/calendar/events/{event_id}`
- 日历可用性：`GET /api/v1/calendar/availability`
- 长期记忆：`POST /api/v1/memories`、`GET /api/v1/memories`、`GET/PUT/DELETE /api/v1/memories/{memory_id}`、`GET /api/v1/memories/{memory_id}/versions`
- 审批列表与详情：`GET /api/v1/approvals`、`GET /api/v1/approvals/{approval_id}`
- 审批决定：`POST /api/v1/approvals/{approval_id}/approve`、`reject`、`approve-with-modifications`、`request-regeneration`
- Agent 执行轨迹：`GET /api/v1/agent-runs/{run_id}/events/history`
- Agent SSE：`GET /api/v1/agent-runs/{run_id}/events`，支持 `after` 和 `Last-Event-ID`
- 管理员审计日志：`GET /api/v1/audit-logs`，支持 `user_id`、`action`、`offset` 和 `limit`
- Mail MCP 健康检查：<http://localhost:8001/health>
- Calendar MCP 健康检查：<http://localhost:8002/health>

MCP 是内部协议端点，不是给浏览器直接操作的普通 REST API；开发环境直连缺少内部 Token 时返回 401，Nginx 公共入口上的 `/mcp` 固定返回 404。

MailPilot 第一版不开放用户自助注册，登录用户由后续开发数据脚本或管理员流程创建，避免任何人自行注册企业账号。

## 质量检查

```powershell
uv run ruff check .
uv run pytest
uv run pytest --cov=backend/app --cov-report=term-missing
```

单元测试中的依赖健康状态使用 Mock，不要求每次测试都启动 PostgreSQL 或 Redis。设置 `TEST_DATABASE_URL` 后会运行真实 PostgreSQL 集成测试；设置 `TEST_REDIS_URL` 后会运行真实 Redis 登录限流和分布式锁测试；未设置时对应测试会明确显示为 skip。

## 准备本地演示数据

在未提交的 `.env` 中设置 `DEMO_USER_EMAIL` 和 `DEMO_USER_PASSWORD`，数据库迁移完成后运行：

```powershell
uv run python backend/scripts/seed_demo.py
```

脚本通过正式 Service 写入两封中文测试邮件和两条测试日程，不会调用 `create_all()`；相同数据重复运行不会重复插入。

两个 MCP Server 启动后，可以通过 LangChain Adapter 运行只读调用演示：

```powershell
uv run python backend/scripts/mcp_demo.py --query "MailPilot"
```

脚本会以 `DEMO_USER_EMAIL` 对应用户的运行时身份发现安全工具并搜索邮件。它不会发送邮件或创建会议。

## 可观测性与评测

Langfuse 默认关闭。配置以下环境变量后可启用：

```dotenv
LANGFUSE_ENABLED=true
LANGFUSE_BASE_URL=https://your-langfuse.example.com
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_CAPTURE_CONTENT=false
```

`LANGFUSE_CAPTURE_CONTENT=false` 时只上传脱敏摘要。Langfuse 不可用或配置不完整时自动使用 No-op 适配器，不影响 Agent、审批和数据库主流程。

运行 36 条无模型、无写副作用的参考契约评测：

```powershell
uv run python -m evals.run --mode reference
```

显式配置 OpenAI Compatible 模型后，可先运行 3 条真实模型冒烟：

```powershell
uv run python -m evals.run --mode model --limit 3
```

reference 的 100% 是 Fake 依赖驱动的合同基线，不是真实模型准确率。实际报告保存在 `evals/reports/`。

## 数据库迁移规则

- 不在应用启动时调用 `Base.metadata.create_all()`。
- 所有业务表变更都创建 Alembic revision。
- 本地和容器启动前都执行 `alembic upgrade head`。
- 第 1 阶段的初始 revision 是空基线；第 2 阶段开始加入业务表。

## 文档

- [部署与运维说明](docs/10-部署与运维.md)
- [系统架构说明](docs/系统架构说明.md)
- [API 接口说明](docs/API接口说明.md)
- [记忆机制说明](docs/记忆机制说明.md)
- [测试与评测报告](docs/测试与评测报告.md)
- [Benchmark 验证报告](docs/11-Benchmark验证报告.md)
- [Token 与 P95 延迟优化报告](docs/12-Token与延迟优化报告.md)

## 当前范围限制

第 1～10 阶段均已完成。项目已经具备业务数据层、受保护 API、两个 MCP Server、安全 MCP Client、确定性 Graph、审批恢复、真实写工具执行、长期记忆、SSE 中文工作台、安全加固、可选 Langfuse 观测、36 条评测集，以及 Nginx 单机部署和求职材料。

当前边界：后台任务仍为 FastAPI 进程内任务；邮箱/日历 Provider 第一版使用 PostgreSQL 模拟；单机 Compose 不等于高可用生产集群。这些限制在文档和面试材料中明确说明。

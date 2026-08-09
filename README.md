# MailPilot：企业邮件与日程协同 Agent

MailPilot 是面向企业内部邮件处理与日程协调场景的可部署 Agent 系统。它能够分析邮件、识别优先级与处理意图、查询日历、生成回复或会议方案，并在发送邮件、创建或变更日程等写操作前强制进入人工审批；审批完成后，工作流从同一 Checkpoint 安全恢复。

系统采用“外层确定性 LangGraph 工作流 + 内层受控 MCP 工具调用”的架构。LLM 负责自然语言理解和候选内容生成，Python 负责状态机、权限、安全不变量、工具参数与结束条件，以满足企业业务对可控、可追踪、可恢复和可审计的要求。

## 系统概览

```text
企业邮件
  → 分类与优先级判断
  → 意图提取与受控计划生成
  → MCP 查询邮件 / 日历
  → 生成回复草稿或会议方案
  → interrupt 暂停并等待人工审批
  → Command 恢复原工作流
  → 执行经批准的写操作
  → 审计、执行轨迹与长期记忆更新
```

| 能力域 | 实现 |
|---|---|
| Agent 编排 | LangGraph Typed State、条件边、`interrupt` / `Command`、PostgreSQL Checkpointer、`thread_id` 恢复 |
| 工具协议 | Mail MCP 与 Calendar MCP，FastMCP + Streamable HTTP + `langchain-mcp-adapters` |
| 人机协同 | 接受、拒绝、修改后接受、反馈重生成；审批决定持久化后恢复同一 Graph |
| 安全治理 | JWT/RBAC、Prompt Injection 隔离、工具白名单、Pydantic 参数合同、写操作审批、Redis 锁与数据库幂等键 |
| 记忆系统 | 用户隔离的业务事实表、不可变版本记录、PostgreSQL Store 运行时副本与跨会话复用 |
| 可观测性 | AgentRun、AgentRunEvent、ToolCallLog、AuditLog、SSE 时间线与可选 Langfuse Trace |
| 产品与部署 | Streamlit 工作台、FastAPI、PostgreSQL、Redis、Nginx、Docker Compose |

## 核心工程设计

### 确定性 Agent 编排

- Graph 的节点、条件边和终态在代码中显式定义，模型不能自行扩展执行路径。
- LLM 只返回经过 Pydantic 校验的结构化结果；步骤编号、会议时间、收件人和工具参数由 Python 构造并复核。
- 默认链路保留快速分类，将非忽略邮件的“意图提取 + 计划决策”合并为一次结构化调用；可通过 `AGENT_ANALYSIS_MODE=sequential` 回退到基线链路。

### 危险操作审批与幂等执行

- 查询邮件、读取日历等只读工具可自动执行；发送邮件、创建、改期和取消会议必须经过审批。
- `interrupt` 将执行现场持久化到 PostgreSQL，审批 API 使用 `Command(resume=...)` 恢复原 `thread_id`，不重跑已完成节点。
- MCP Server 对内部身份、审批状态、操作类型、最终参数和幂等键进行二次校验。
- Redis 分布式锁控制并发进入，数据库唯一约束保证最终幂等；发送邮件和日历变更等外部副作用写工具不自动重试，只读与本地幂等工具可有限重试，结果未知时停止并等待核对。

### 可版本化长期记忆

- Checkpointer 保存单个任务的执行现场；Store 保存可跨任务复用的运行时记忆，两者职责分离。
- 业务记忆表是可查看、可修改、可删除的事实来源，Store 是按 `user_id` 和记忆类型隔离的 Graph 读取副本。
- 只有包含“以后、默认、请记住”等明确长期信号的反馈才会生成局部 Patch；模型不能直接覆盖整份偏好或写数据库。
- 实际使用的记忆版本进入 Checkpoint 与 AgentRun 快照，确保审批恢复前后上下文一致。

### 安全与审计

- 邮件正文、工具结果和用户反馈均按不可信数据处理，与系统指令和工具合同隔离。
- JWT 校验 issuer、audience 与 jti；管理员接口使用独立 RBAC 依赖，业务查询强制携带用户边界。
- AgentRunEvent 与 AuditLog 仅记录白名单摘要，默认不复制完整邮件、Prompt 或凭证；经审批的最终写参数与结果保存在受权限约束的 ToolCallLog 中，用于恢复、幂等和审计。
- MCP 内部服务不经 Nginx 暴露；结构化日志按敏感字段名脱敏，外部观测内容默认只记录结构，显式采集时再掩码邮箱、令牌和连接串。

## 实测结果

| 指标 | 实测结果 |
|---|---:|
| 自动化测试 | 286 passed |
| 真实模型分析链 | 36 条数据集 × 3 轮，共 108 个样本 |
| 分析链平均 Token | 3992.39 → 3153.64，下降 21.01% |
| 分析链 P95 延迟 | 12.84 秒 → 10.68 秒，下降 16.84% |
| 完整 HTTP E2E 平均 Token | 4865.25 → 3857.39，下降 20.72% |
| 完整 HTTP E2E P95 | 21.33 秒 → 12.14 秒，下降 43.07% |
| 完整 E2E 任务结果符合预期率 | 97.22% |
| 危险操作审批门禁 | 26/26 通过 |

以上结果来自本地固定数据集 Benchmark，不代表生产 SLA。P95 使用同一报告内的全部样本计算，没有排除失败或慢请求；测试模型、样本、链路和适用边界见 [Token 与 P95 延迟优化报告](docs/12-Token与延迟优化报告.md) 与 [Benchmark 验证报告](docs/11-Benchmark验证报告.md)。

## 核心代码入口

| 目标 | 入口文件 |
|---|---|
| 完整 LangGraph 工作流 | [workflow.py](backend/app/agent/workflow.py) |
| 合并后的意图与计划节点 | [analyze_intent_plan.py](backend/app/agent/nodes/analyze_intent_plan.py) |
| 受控执行计划构造与校验 | [planning.py](backend/app/agent/planning.py) |
| 审批暂停与恢复 | [approval.py](backend/app/agent/nodes/approval.py) |
| 邮件与日历 MCP Client | [client.py](backend/app/integrations/mcp/client.py) |
| HTTP Agent 入口 | [emails.py](backend/app/api/v1/emails.py) |
| 评测与 Benchmark | [evals](evals/) |

## 快速部署与验证

### 环境要求

- Python 3.12.x
- Docker Desktop 与 Docker Compose
- Git
- uv

### 首次配置

```powershell
git clone https://github.com/xibeiqiaozhilang-bot/mailpilot.git
cd mailpilot
Copy-Item .env.example .env
```

编辑 `.env`，至少替换以下配置：

- `POSTGRES_PASSWORD`、`REDIS_PASSWORD`
- `DATABASE_URL`、`DOCKER_DATABASE_URL`
- `REDIS_URL`、`DOCKER_REDIS_URL`
- `JWT_SECRET_KEY`、`MCP_INTERNAL_TOKEN`
- OpenAI Compatible 模型地址、模型名与 API Key

`.env` 已加入 `.gitignore`，不得提交到版本库。

### Docker Compose 启动

```powershell
docker compose --env-file .env up --build -d
```

该命令启动 PostgreSQL、Redis、组合迁移任务、FastAPI、Streamlit、Mail MCP、Calendar MCP 与 Nginx。迁移任务会依次完成 Alembic 业务表、LangGraph Checkpointer 和 PostgreSQL Store 初始化。

安装本地命令依赖并初始化验收数据：

```powershell
uv sync --extra dev
uv run python backend/scripts/seed_demo.py
```

脚本通过正式 Service 写入隔离的测试邮件与日历数据，重复执行不会重复插入。随后访问 <http://localhost:8080>，使用 `.env` 中的 `DEMO_USER_EMAIL` 和 `DEMO_USER_PASSWORD` 登录。

### 本地开发

启动数据库与 Redis：

```powershell
docker compose --env-file .env up -d postgres redis
uv run python backend/scripts/migrate.py
```

启动 API；Windows 环境使用兼容 psycopg 异步连接池的入口：

```powershell
uv run python backend/scripts/run_api.py
```

启动 Streamlit：

```powershell
$env:MAILPILOT_API_URL = "http://localhost:8000/api/v1"
uv run streamlit run frontend/app.py
```

## 服务入口

推荐统一通过 Nginx 访问：

- 工作台：<http://localhost:8080>
- OpenAPI：<http://localhost:8080/docs>
- Nginx 健康检查：<http://localhost:8080/healthz>
- API 就绪检查：<http://localhost:8080/api/v1/health/ready>

开发环境可直连 FastAPI `http://localhost:8000` 与 Streamlit `http://localhost:8501`。Mail MCP 与 Calendar MCP 是内部协议端点：缺少内部 Token 时拒绝访问，Nginx 公共入口固定阻断 `/mcp`。

## 质量检查与评测

```powershell
uv run ruff check .
uv run pytest
uv run pytest --cov=backend/app --cov-report=term-missing
```

单元测试通过 Mock 隔离依赖；设置 `TEST_DATABASE_URL` 后运行真实 PostgreSQL 集成测试，设置 `TEST_REDIS_URL` 后运行真实 Redis 限流与分布式锁测试，未配置时相应测试明确标记为 skip。

运行 36 条无模型、无写副作用的参考合同评测：

```powershell
uv run python -m evals.run --mode reference
```

配置 OpenAI Compatible 模型后运行真实模型评测：

```powershell
uv sync --frozen --extra dev --extra eval
uv run python -m evals.run --mode model --limit 3
```

Reference 的 100% 是 Fake 依赖驱动的合同基线，不代表真实模型准确率。可复现 Benchmark 位于 `evals/benchmarks/`，运行报告生成到已被 Git 忽略的 `evals/reports/`。

## 数据库迁移约束

- 应用启动时不调用 `Base.metadata.create_all()`。
- 所有业务表变更均创建 Alembic revision。
- 本地与容器启动前统一执行迁移任务。
- LangGraph Checkpointer 与 Store 自管表由组合迁移脚本幂等初始化。

## 技术文档

- [部署与运维说明](docs/10-部署与运维.md)
- [系统架构说明](docs/系统架构说明.md)
- [API 接口说明](docs/API接口说明.md)
- [记忆机制说明](docs/记忆机制说明.md)
- [测试与评测报告](docs/测试与评测报告.md)
- [Benchmark 验证报告](docs/11-Benchmark验证报告.md)
- [Token 与 P95 延迟优化报告](docs/12-Token与延迟优化报告.md)

## 部署边界与已知限制

当前交付提供单机生产部署基线：Nginx 是唯一公共入口，业务服务与 MCP 位于内部网络，PostgreSQL 和 Redis 使用持久化卷，并具备健康检查、迁移、审计和可选观测链路。以下能力仍需按目标企业环境继续建设：

- 后台任务当前运行在 FastAPI 进程内；多副本或长任务场景应接入外部持久任务队列与 Worker 协调。
- 默认邮箱与日历 Provider 使用 PostgreSQL 实现可复现业务闭环；接入 Gmail、Outlook、飞书等真实系统时需要补充 OAuth、Webhook、同步游标与供应商限流策略。
- 单机 Compose 不等于高可用集群；正式集群还需要负载均衡、托管数据库与 Redis、备份恢复、迁移锁、集中日志和告警。
- 当前以 `user_id` 实现数据隔离；组织级租户、企业 SSO、密钥管理与更细粒度策略需结合实际身份体系扩展。

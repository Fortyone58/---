# LangChain 受控 RAG 验收报告

日期：2026-10-04

## 本次范围

本报告记录 v0.3 当前 LangChain 迁移的实际代码与验证结果，不修改或重写历史 `REPORT.md`、`REPORT-v02.md`、`REPORT-v03.md`、`REPORT-RAG.md`。本次仅涉及 AI/RAG 编排、必要依赖、测试、技术材料和评测脚本容错；Vue/FastAPI 的认证、岗位、申请、工时、薪酬、匹配、审计和 MySQL 主流程未重写。

实现口径：**LangChain 编排受控 RAG 与只读工具；FastAPI 执行权限和业务规则；BGE + Qdrant 完成语义检索。** 系统采用线性、可审查的受控编排。

## 实现核验

| 项目 | 实际实现 |
| --- | --- |
| LangChain 依赖 | `langchain-core==1.6.6`、`langchain-openai==1.6.7` 已锁定于 `backend/pyproject.toml` 和 `backend/uv.lock`。 |
| 真实请求路径 | 模型可用时，`agent.py` 调用 `llm.complete`，再进入 `langchain_flow.complete`；`ChatPromptTemplate` 渲染系统提示、已脱敏历史和带出处证据，`ChatOpenAI` 调用配置的 OpenAI 兼容模型，固定只读工具 schema 通过 `bind_tools` 交给模型。 |
| 工具边界 | 模型返回的工具调用只是数据。实际执行仍只在 `agent._run_tool`：工具白名单、Pydantic、当前登录用户、用户/单位资源范围和只读 SQL 查询均在 FastAPI 内复核。 |
| 输出边界 | 政策/规则/工资标准/历史招聘/报名条件/学校细则先强制走已核验原文检索，最终显示原文摘录和确定性边界；不把合法引用编号当作模型自由结论的语义证明。薪酬数字和写操作声明仍在模型输出后校验，异常时返回真实原文、关键词或规则结果。 |
| 检索边界 | SQL 的 `PolicyDoc` 原文和元数据为权威来源；`policy.py` 在向量检索前过滤学校/国家范围、用途、历史资料、劳动标准和时效。精确条款号仍直接定位原文。 |
| 向量索引 | 保持 FastEmbed + `BAAI/bge-small-zh-v1.5` + Qdrant。health check 核对本地 collection、向量维度、实际点数和 manifest 的 `chunks` 数；这些检查或语义查询异常时状态为 `unavailable`，`ready=false`，并标记 `rebuild_required=true`，请求明确走 `keyword_fallback`。这不宣称可检测任意底层存储损坏。 |
| 数据与密钥 | 本次安全修复未读取、打印、修改或提交私有 `.env` 的密钥/数据库密码/JWT 密钥；没有运行重置演示数据脚本。 |

## 自动测试

### 历史记录

以下是迁移期间留下的历史结果，不是本轮安全修复的执行证据：

```powershell
uv run pytest -q
```

- `uv sync --frozen`：`Checked 79 packages in 3ms`。
- `uv run pytest -q`：`170 passed, 5 skipped, 1 warning in 46.72s`。warning 为 Starlette `BlockingPortal` 的第三方弃用提示。
- 这条默认 pytest 记录不覆盖隔离安全测试。默认启动会先加载 `backend/tests/conftest.py`，间接导入正常 `app.config`；安全测试发现该模块已加载后跳过。因此默认全量 pytest 未执行该隔离套件中的任何安全用例。
- 更早的隔离日志曾记为安全测试 `35 passed`、`test_agent.py` 31 项、`test_rag.py` 19 项及评测脚本 1 项；这些都是历史记录，不能代替本轮结果。

### 本轮实际执行

以下隔离入口在 `backend` 目录执行。`--noconftest` 阻止正常测试配置提前导入 `app.config`；测试随后在导入应用前注入 fake config，仅使用 `tmp_path`、SQLite 和 mock，不读取项目 `.env`、不连接真实数据库/Qdrant 服务或模型：

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
uv run --no-sync pytest --noconftest -p no:cacheprovider -q tests/test_agent_safety_isolated.py
uv run --no-sync pytest --noconftest -p no:cacheprovider -q tests/test_langchain_flow.py
uv run --no-sync ruff check app tests
```

- `test_agent_safety_isolated.py`：`72 passed, 1 warning`；warning 为 Starlette `BlockingPortal` 第三方弃用提示。
- `test_langchain_flow.py`：`3 passed`，使用本地 mock transport，不访问外部模型。
- `ruff check app tests`：通过；前端 `npm run lint`：通过；`git diff --check`：通过。
- 本轮未运行默认全量 pytest、依赖正常 conftest 的 `test_agent.py`/`test_rag.py`、应用启动、MySQL 评测或真实模型请求。

新增/更新测试覆盖：

- `test_langchain_flow.py`：验证 `ChatPromptTemplate`、`ChatOpenAI`、固定只读工具 schema 和工具消息实际进入 LangChain 运行层；并以本地 `httpx.MockTransport` 验证锁定的真实 `ChatOpenAI` 调用 OpenAI 兼容的 `/v1/chat/completions` 路径、DeepSeek 预设的禁用推理参数和禁止跟随重定向行为，不访问外网。
- `test_agent.py`：验证模型路径进入受控 LangChain 运行层；LangChain 超时后回退到已授权的业务规则结果；非法/可写工具、跨单位对象和敏感字段不能越权或写库；无依据政策、错误引用/数字、历史隔离及密钥不泄漏仍被回退/拒绝。上下文仅在当前服务和紧邻上一条回答均带验证标记、且服务商、规范化端点、模型 ID 完全一致时复用；离线、降级、未验证、升级前旧记录、切换模型或切回旧模型都不会预取或转发旧上下文。
- `test_rag.py`：验证 collection 缺失或无法打开时不误报 ready，并明确关键词降级；点数不一致、点数读取失败和查询异常由隔离安全测试覆盖。
- `test_agent_safety_isolated.py`：以临时配置、SQLite、mock 和 `tmp_path` 覆盖固定/临时岗自然语言薪酬问法、岗位发现优先级、模型偏航到岗位工具的拒绝、已验证原文与资料缺口、只读岗位状态说明、各类写入请求/模型谎报及业务快照不变、Qdrant 点数异常和数据库 503。
- `test_evaluate_rag.py`：验证 MySQL 未启动时只返回简短中文启动提示、不输出连接栈、不生成评测输出，也不触发数据重置。

前端在 `frontend` 目录执行：

```powershell
npm run lint
npm run build
```

实际结果：lint 通过；Vite 6.3.5 生产构建通过，`3062 modules transformed`，最终构建耗时 `3.01s`。

## 真实 RAG 评测

仅使用项目受管 MySQL `127.0.0.1:13308`，未连接其他 MySQL，也没有重置任何数据。

```powershell
# 项目根目录
.\backend\.venv\Scripts\python.exe .\scripts\mysql_runtime.py start

# backend 目录
uv run python ..\scripts\evaluate_rag.py --output ..\work\rag-evaluation-langchain.json

# 项目根目录
.\backend\.venv\Scripts\python.exe .\scripts\mysql_runtime.py stop
```

实际运行：启动结果为 MySQL `8.0.45`、端口 `13308`；评测完成后返回 `stopped`。输出已写入 `work/rag-evaluation-langchain.json`。

固定开发题集结果：

| 指标 | 关键词基线 | BGE + Qdrant 融合检索 |
| --- | ---: | ---: |
| 题数 | 21 | 21 |
| 符合预期 | 14 | 21 |
| Recall@5（16 道有依据题） | 56.25% | 100% |
| Top-1（16 道有依据题） | 56.25% | 87.5% |
| 无依据/资料缺口拒答（5 题） | 5/5 | 5/5 |

该结果仅是固定开发题集的检索验收，**不是**通用 AI 准确率、线上模型回答准确率或学校政策正确性的保证。

## 未验证项与风险

- 未调用外部模型服务进行在线质量验收：本次没有读取或使用私有 API 密钥。LangChain 实际请求路径通过可注入 `ChatOpenAI` 测试和全量回归验证；管理员配置真实兼容模型后，仍应单独记录连接、工具格式兼容和回答质量。
- 本校完整现行勤工助学细则、具体时薪/酬金和困难认定全文仍未取得。系统对这些问题应保持资料缺口提示，不得由模型补全。
- Qdrant 本地模式由单个应用进程持有；多 worker 或跨机器部署前需要改为受管 Qdrant 服务模式并增加相应运维验证。
- 固定题集仍需与独立、未参与调试的问题集分开评估，尤其是复杂追问、引用覆盖率和跨模型兼容性。

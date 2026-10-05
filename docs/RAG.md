# 本地政策 RAG 与 LangChain 受控编排

2026-10-04起，模型可用时的回答路径真实使用 LangChain；语义检索仍复用现有已核验政策库。嵌入在本机 CPU 上执行，不需要额外 API 密钥。系统保留 FastEmbed、`BAAI/bge-small-zh-v1.5` 和 Qdrant，采用线性、可审查的受控编排。

## 处理流程

1. 从SQL读取已核验资料，按原有条款/问答定位分段；长段按最多320字符、64字符重叠切片，保存原文起止位置。
2. FastEmbed的`BAAI/bge-small-zh-v1.5`分别编码资料与问题；512维向量写入Qdrant本地持久存储。
3. 检索前按当前SQL中的学校/国家范围、年份、资料编号、用途、有效日期、截止日期过滤候选。历史招聘和劳动标准不会自动变成本校当前依据。
4. 主题关键词和向量余弦检索分别排序，再用Reciprocal Rank Fusion合并。余弦阈值默认0.65，每个原文段保留一个最佳片段。确切条款编号直接定位，避免语义排序改变条号。
5. `backend/app/langchain_flow.py` 通过 `ChatPromptTemplate` 组织系统提示、已脱敏历史和带出处证据，通过 `ChatOpenAI` 调用已配置的 OpenAI 兼容模型，并只绑定服务器生成的只读工具 schema。
6. LangChain 返回的工具调用只是未授权的数据意图。`agent.py` 再以 Pydantic Schema、当前 JWT 身份、用户/单位资源范围和工具白名单验证后，才执行只读查询。政策/规则/工资标准/历史招聘/报名条件/学校细则问题先强制走 `policy.py` 的已核验原文检索，不能落入模拟岗位查询；政策事实统一显示原文摘录和确定性边界，不把“有引用编号”的模型自由结论标为已验证。薪酬数字和写操作声明仍在模型输出后校验。

短口语查询使用“校园勤工助学政策”作为领域上下文，不向查询中补入答案或具体数值。已识别主题仍受原文主题约束，避免词义相近但适用条款错误。

## 当前部署

- MySQL中的14份核验资料保持原样；派生索引共155个片段，维度512。
- 模型缓存：`data/runtime/rag/models`；向量库：`data/runtime/rag/vectors`；清单：`data/runtime/rag/manifest.json`。这些目录已由现有gitignore排除。
- 索引只包含政策原文，不包含学生、工时、工资或聊天记录。
- 本机已下载模型，正常请求仅加载本地缓存，不会自动下载模型。管理员页面重建也不发起模型下载。
- FastEmbed和Qdrant本地模式随应用运行，无需另起向量数据库服务器。
- Qdrant本地目录由单个应用进程持有，当前启动脚本使用单个Uvicorn进程。多worker或跨机器部署需要迁移到Qdrant服务模式。
- `langchain-core==1.6.6` 与 `langchain-openai==1.6.7` 锁定在 `backend/uv.lock`；两者只承担提示词、模型调用和只读工具描述编排，不拥有业务数据库权限。

## 新机器准备

在backend目录运行：

```powershell
uv sync --frozen
uv run python ../scripts/prepare_rag.py --download
```

第一次允许下载公开嵌入模型并从已核验SQL资料生成索引。现有机器重建可运行不含`--download`的同一命令，或在管理员“AI模型配置”里的“政策知识库”点击“重建索引”。CLI重建需在应用未占用同一向量目录时运行。

`.env.example`登记了`RAG_ENABLED`、`RAG_MODEL`、`RAG_MIN_SCORE`和可选目录覆盖。默认启用；设置`RAG_ENABLED=false`并重启即使用原关键词检索。回答模型仍沿用原有管理员配置。

## 更新与降级

SQL是唯一原文依据。每次检索比较内容指纹，资料内容、标题、分段或核验状态变化后更新索引；单纯适用范围变更即时通过SQL过滤生效。引用从当前SQL重建，不信任历史向量载荷里的政策文本。

重建先写新 collection，成功后原子替换清单，再删除旧 collection；失败保留旧索引，但本轮使用当前原文的关键词结果，明确标记`keyword_fallback`。状态检查不仅检查 manifest 和模型缓存，还会打开 Qdrant 本地存储，确认 collection 存在、向量维度匹配，并以精确点数核对 manifest 的 `chunks` 数。collection 缺失、无法打开、维度不符、点数缺失/不一致，或后续语义查询异常时，状态为 `unavailable`、`ready=false`、`rebuild_required=true`，查询明确标记关键词降级。该健康检查覆盖上述可观测条件，不宣称能够识别任意底层存储损坏。重建入口仅管理员可用，成功记录操作审计。

回答状态保存在聊天日志并显示在 AI 页面：`policy_source` 表示政策问题直接显示已核验原文，`policy_no_evidence` 表示资料缺口拒答，`policy_boundary` 表示历史/时效/适用范围边界，`model_fallback` 表示模型超时、格式或业务数字校验失败后退回原文/规则。政策问题不将模型自由生成结论标记为 `model_grounded`；这些状态不混作“AI 成功”。

会话上下文只在当前服务已完成连接验证、且紧邻上一条回答记录也带有当时已验证标记，服务商、规范化接口地址和模型 ID 与当前值完全相同的情况下复用；升级前没有该验证标记的旧记录也不复用。即使之后切回旧服务，也不会跨越中间的不同服务记录转发更早上下文。离线回答、模型降级回答、未验证服务、切换服务商或同端点切换模型都不会预取或转发旧政策/业务追问上下文；模糊薪酬追问会要求明确月份。

## 验证

在 `backend` 目录用隔离入口验证，不加载根测试配置：

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
uv run --no-sync pytest --noconftest -p no:cacheprovider -q tests/test_agent_safety_isolated.py
uv run --no-sync pytest --noconftest -p no:cacheprovider -q tests/test_langchain_flow.py
uv run --no-sync ruff check app tests
```

默认 pytest 会先加载 `tests/conftest.py` 并间接导入正常 `app.config`，因此隔离安全测试会跳过；默认全量测试结果不代表已覆盖该隔离套件。隔离入口只注入 fake config，使用临时 SQLite、`tmp_path` 和 mock，不读取项目 `.env`。

真实评测前后使用项目受管 MySQL，且只使用 `13308`：

```powershell
# 项目根目录
.\backend\.venv\Scripts\python.exe .\scripts\mysql_runtime.py start

# backend 目录
uv run python ..\scripts\evaluate_rag.py --output ..\work\rag-evaluation-langchain.json

# 项目根目录
.\backend\.venv\Scripts\python.exe .\scripts\mysql_runtime.py stop
```

评测只读正式政策资料，在临时目录创建独立向量索引，不创建正式聊天记录，也不重置业务数据。MySQL 未启动时，脚本只输出中文启动提示而不输出连接栈。固定 21 题包括 16 道有依据题和 5 道无依据/资料缺口题：历史开发结果为关键词基线 Recall@5 56.25%、混合检索 Recall@5 100%、混合 Top-1 87.5%、拒答 5/5。题目用于开发验收，不能宣称为未知问题的通用准确率；题目和原始排名结果保存在`data/rag/evaluation.json`及`acceptance/rag-retrieval-2026-10-03.json`。

模型选择与API依据：[FastEmbed模型表](https://qdrant.github.io/fastembed/examples/Supported_Models/)、[FastEmbed检索说明](https://qdrant.github.io/fastembed/qdrant/Retrieval_with_FastEmbed/)、[Qdrant本地模式](https://github.com/qdrant/qdrant-client/blob/master/README.md)。

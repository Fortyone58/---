# 架构与规则解释

```mermaid
flowchart LR
  B[Vue3 + Element Plus\n5173 本地浏览器] -->|JWT / HTTP API| F[FastAPI 8000\n角色与资源归属校验]
  F --> T[SQLAlchemy事务\n状态 / 名额 / 快照 / 审计]
  T --> D[(MySQL8专用演示库\n13308 数据已迁移及验收)]
  F --> M[四维规则匹配\n无模型调用]
  F --> P[已核验政策原文查询\nSQL权威原文 + 官方出处]
  F --> A[LangChain受控编排\nPrompt + ChatOpenAI + 只读工具描述]
  A --> P
  P --> RAG[范围/时效/用途过滤\n关键词 + BGE语义检索]
  RAG --> V[(Qdrant本地向量索引\n512维 + 内容指纹)]
  RAG --> E[检索原文片段与出处]
  E --> A
  A --> C[FastAPI二次校验\nPydantic / 权限 / 引用 / 数字 / 拒绝写入]
  C --> H[带引用回答或原文/规则降级]
  R[独立pytest临时库] -.-> F
```

```mermaid
erDiagram
  unit ||--o{ users : binds
  unit ||--o{ job : publishes
  users ||--o{ application : applies
  job ||--o{ application : receives
  job ||--o{ job_skill : requires
  application ||--o{ work_hour : records
  work_hour o|--o| work_hour : corrects
  users ||--o{ audit_log : operates
  users ||--o{ chat_log : queries
  users ||--o{ system_setting : modifies
  policy_doc {
    int id
    string source_url
    string version
    bool verified
    json sections
  }
```

```mermaid
stateDiagram-v2
  [*] --> pending_review
  pending_review --> approved: 单位批准
  pending_review --> rejected: 单位驳回
  pending_review --> withdrawn: 本人撤销
  approved --> withdrawn: 本人撤销
  approved --> onboard: 单位确认上岗
  onboard --> finished: 单位确认结束
```

工时当前链尾按“日期 / 原始创建时间 / 原始ID”排序，在学生跨岗位范围计算周/月前缀；任一超限，整条进入待核。核实绑定当前链尾，更正重新核实。旧记录不重复相加。

临时岗 F(H)=H×冻结时薪；固定岗 F(H)=min(H,40)/40×冻结月薪基准。正常=F(正常有效H)，待核=F(正常H+待核H)−F(正常H)。每组未舍入结果先汇总，最后ROUND_HALF_UP保留两位。

总匹配分=(40T+25D+25K+10L)/100。T按合并时段交集分钟；D为确认后的0/50/100演示映射；K精确词表命中；L同区100/相邻70/跨两区40。缺资料不生成总分；空技能列表有效。附录A黄金72/80/null与并列101、103、102已自动测试。

所有匹配权重、困难映射、工资折算、名额批次和异常核实流程是原型约定；教育部条款有独立原文来源，不把模拟算法写成学校制度。

AI/RAG 的准确表述是：**LangChain 编排受控 RAG 与只读工具；FastAPI 执行权限和业务规则；BGE + Qdrant 完成语义检索。** LangChain 不拥有数据库写入权限，也不能绕过 JWT、Pydantic、资源范围、政策适用范围、引用和数字校验。SQL 政策原文与元数据是权威来源，Qdrant 仅为可重建派生索引；collection 缺失、损坏或不可打开时，状态显示不可用并走关键词降级。系统保持线性、可审查的受控编排，固定 21 题开发集的检索结果不等同于通用 AI 准确率。

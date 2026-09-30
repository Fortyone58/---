# 数据与接口

10张表沿用计划：users、unit、job、application、job_skill、policy_doc、chat_log、work_hour、system_setting、audit_log。字段定义以backend/app/models.py为准；迁移版本0001。

| 数据 | 关键口径 |
| --- | --- |
| users | role=student/unit/aid/admin；status=active/pending_activation；skills、slots的null表示未填写，[]表示明确空列表；困难确认仅aid |
| slots | [{day:1..7,start:"HH:MM",end:"HH:MM"}]，同日开始小于结束，不接受跨午夜 |
| job | category=temporary/fixed；wage为十进制；status=draft/published/closed；close_reason=full/manual/null；单位不可变 |
| application | unique(student_id,job_id,application_no)，序号仅1/2；job_snapshot为提交时展示；salary_snapshot为批准时冻结，工资数值用十进制字符串 |
| work_hour | previous_id唯一防分叉；root_id及root_created_at用于稳定排序；当前链尾才累计；验证绑定链尾 |
| system_setting | matching_weights总和100，整数0..100；每次保存版本+1；schema_version记录初始迁移 |
| policy_doc | 官方来源、标题、发布主体、版本、核验和导入时间、定位/原文；学校政策有单独标识 |
| audit_log | 成功写入同事务追加；before/after JSON不含密码/令牌；失败操作不记录为业务成功 |
| chat_log | user_id由JWT确定；conversation_id最大64字符；response.kind区分policy/assistant；只读本人历史；旧original_query日志兼容读取；无需新增表迁移 |

业务日期按Asia/Shanghai。工时为自然日；记录时间为中国本地时间ISO字符串；工时/金额返回两位十进制字符串。GET接口不产生业务写入。

认证使用Authorization: Bearer TOKEN。API参考交互文档：http://127.0.0.1:8000/docs。错误统一{error,message,details?}；输入400、未登录401、无权/未启用403、不存在404、业务冲突409。v0.2助手的含糊条件或禁止动作返回200结构化说明，非学生返回403。

| 接口 | 范围 / 写入 |
| --- | --- |
| GET /api/ping | 启动检查，无身份，无敏感配置 |
| GET /api/meta | 词表、角色、规则版本与实际能力；policy=original_query、job_assistant=rule_assistant、external_model_connected=false |
| POST /api/auth/register、login；GET me | 注册只能student待启用；凭据错401，未启用403；JWT只绑定用户ID |
| GET/PUT /api/profiles/me | 学生本人可改姓名、专业、技能、时段、区域；额外身份字段拒绝 |
| GET/POST /api/jobs；GET/PUT/DELETE /{id}；POST /{id}/actions | 单位本单位写入；publish/close；学生不可见草稿；关联/非草稿删除409 |
| GET/POST /api/applications；POST /{id}/actions | 学生本人提交/撤销；单位所属申请审核、上岗、结束；其他角色全局只读 |
| GET/POST /api/workhours；POST /{id}/corrections、verify；GET /{id}/history | 单位所属上岗登记/链尾更正；aid核实；学生本人、单位本单位、aid/admin全局读 |
| GET /api/payroll/summary、stats?month=YYYY-MM | 按身份与自然月汇总；正常/待核分列；岗位申请统计是当前状态 |
| GET /api/matching | 仅学生本人；可申请岗位；完整分数、缺失、版本和依据 |
| GET/POST /api/admin/users；POST /{id}/activate、hardship | admin创建非学生/启用学生；aid确认等级；unit/student禁止 |
| GET/POST /api/admin/units | admin管理单位；aid可读取 |
| GET /api/admin/settings；PUT /matching；GET /api/admin/audit | admin；权重变更版本化；审计只读 |
| GET/POST /api/policies；GET /{id} | 查询已核验原文；仅aid明确核验后导入；禁止未核验资料参与查询 |
| POST /api/qa | question和conversation_id；原文/条款/主题检索，有限追问只读同用户同会话的上一条政策查询；学校范围缺失拒答 |
| GET /api/qa/history?conversation_id=... | 最近100条本人政策查询；可选会话筛选，指定时按提问次序返回；不混入助手日志 |
| GET /api/qa/conversations | 最近50个本人政策会话，首问标题、末问、条数与更新时间 |
| POST /api/assistant/messages | 仅学生；独立解析本次条件，实际岗位与四维分数，最多8条；只追加查询日志，不写业务表或成功业务审计 |
| GET /api/assistant/history?conversation_id=... | 最近20条本人助手问题；可选会话筛选；按当前公开岗位和本人申请状态重新计算，标记historical=true |

SQLite写请求在依赖入口BEGIN IMMEDIATE。MySQL应用连接使用READ COMMITTED，锁定岗位保护名额、锁学生保护跨岗累计，并锁当前链尾；工时重算在flush后统一读取持久化的排序字段，避免DATETIME秒精度与内存微秒混用。v0.2在独立临时MySQL 8.0.45完成92项回归，包括2名额/3批准、两单位各5小时并发。报告不代表已有SQLite数据迁移或用户现有MySQL服务验收。

助手返回字段包含kind、mode、status、answer、items、filters、warnings、missing_profile、total、shown、notice。status为matched/partial/no_match/needs_clarification/refused/help。needs_clarification的parsed_filters是已识别但未执行的条件。工资过滤必须标明时薪或固定月基准，不把固定工资当成时薪。时间条件判断至少一条完整岗位时段，其他排班以四维匹配的覆盖分钟为准。每条问题独立，不借历史补筛选条件；这不是模型动作Schema。

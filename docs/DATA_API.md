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
| chat_log | user_id由JWT确定；用户仅本人检索历史；question与原文返回，不依赖模型 |

业务日期按Asia/Shanghai。工时为自然日；记录时间为中国本地时间ISO字符串；工时/金额返回两位十进制字符串。GET接口不产生业务写入。

认证使用Authorization: Bearer TOKEN。API参考交互文档：http://127.0.0.1:8000/docs。错误统一{error,message,details?}；输入400、未登录401、无权/未启用403、不存在404、业务冲突409；未实施助手503。

| 接口 | 范围 / 写入 |
| --- | --- |
| GET /api/ping | 启动检查，无身份，无敏感配置 |
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
| POST /api/qa；GET /history | 身份绑定原文检索与本人历史；学校范围缺失拒答 |
| POST /api/assistant/messages | 未实施，返回503，不写业务库 |

SQLite写请求在依赖入口BEGIN IMMEDIATE；MySQL锁定岗位保护名额、锁学生保护跨岗累计，并锁当前链尾。MySQL代码分支未在用户现有数据库上验收，不能宣称已验证MySQL并发。

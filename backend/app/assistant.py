"""A bounded, explainable job search assistant backed only by current public jobs.

This is a rule parser, not a language model. Each message is an independent query;
chat history never grants new permissions or supplies implicit filters.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select

from .config import SKILLS
from .models import ChatLog, Job, SystemSetting, Unit
from .services import job_data, match_score, now, stamp

NOTICE = "明确条件检索 · 未调用大模型。每条问题独立解析；四维规则分数使用已保存档案，不代表录用概率；困难代码为演示约定。"
MAX_RESULTS = 8
DAY_NAMES = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}
DAY_CODES = {char: code for code, chars in enumerate(["一1", "二2", "三3", "四4", "五5", "六6", "日天7"], 1)
             for char in chars}
SKILL_ALIASES = {
    "office": ("办公软件", "office", "word", "ppt"),
    "excel": ("表格处理", "excel", "电子表格"),
    "python": ("python", "python编程"),
    "writing": ("文字写作", "写作", "文案"),
    "photography": ("摄影", "拍照"),
    "design": ("设计", "ps", "photoshop"),
    "communication": ("沟通", "交流"),
    "organization": ("活动组织", "组织活动"),
    "s1": ("技能s1", "s1"),
    "s2": ("技能s2", "s2"),
}
UNIT_ALIASES = {
    "图书馆": ("图书馆",),
    "数字校园实验室": ("数字校园实验室", "实验室", "数字校园"),
    "后勤服务中心": ("后勤服务中心", "后勤中心", "后勤"),
    "大学生活动中心": ("大学生活动中心", "活动中心"),
}
PERIODS = {"上午": (480, 720), "早上": (480, 720), "下午": (720, 1080),
           "晚上": (1080, 1440), "晚间": (1080, 1440), "全天": (0, 1440)}


@dataclass
class Criteria:
    areas: set = field(default_factory=set)
    excluded_areas: set = field(default_factory=set)
    units: set = field(default_factory=set)
    excluded_units: set = field(default_factory=set)
    skills: set = field(default_factory=set)
    excluded_skills: set = field(default_factory=set)
    any_skill: bool = False
    no_skills: bool = False
    category: str | None = None
    wage: Decimal | None = None
    wage_category: str | None = None
    days: set = field(default_factory=set)
    window: tuple | None = None
    title_keyword: str | None = None
    filters: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    consumed: list = field(default_factory=list)

    def add_filter(self, key, label, value):
        self.filters.append({"key": key, "label": label, "value": value})

    def mark(self, start, end):
        self.consumed.append((start, end))


def _require_student(user):
    if user.role != "student":
        raise HTTPException(403, "岗位助手仅供学生检索本人可申请岗位")
    if user.status != "active":
        raise HTTPException(403, "账号尚未启用，请联系管理员")


def _is_negated(text, start):
    prefix = text[max(0, start - 15):start]
    return bool(re.search(r"(?:不要|不想要|不考虑|不选|排除|避开|不在|不去|不会|不擅长|不懂|"
                          r"不需要|无需|不用|没学过|不熟悉)\s*(?:去|用|会|住|需要|找|的)?\s*$", prefix))


def _aliases(text, aliases):
    # Longest aliases first avoids double-counting "活动组织" / "组织" etc.
    occupied = []
    for alias in sorted(set(aliases), key=len, reverse=True):
        pattern = re.escape(alias.lower())
        if re.fullmatch(r"[a-z][a-z0-9]*", alias.lower()):
            pattern = rf"(?<![a-z0-9]){pattern}(?![a-z0-9])"
        for hit in re.finditer(pattern, text):
            if any(start < hit.end() and hit.start() < end for start, end in occupied):
                continue
            occupied.append((hit.start(), hit.end()))
            yield hit


def _clock(value):
    value = value.replace("：", ":")
    if ":" in value:
        hour, minute = map(int, value.split(":"))
    elif "点" in value:
        hour_text, minute_text = value.split("点", 1)
        hour = int(hour_text)
        minute = 30 if minute_text == "半" else int(minute_text.rstrip("分") or "0")
    else:
        hour, minute = int(value), 0
    if not 0 <= hour <= 24 or not 0 <= minute < 60 or (hour == 24 and minute):
        raise ValueError("invalid clock")
    return hour * 60 + minute


def _time_label(window):
    return "–".join(f"{value // 60:02}:{value % 60:02}" for value in window)


def _parse_question(question, units):
    text = unicodedata.normalize("NFKC", question).lower()
    criteria = Criteria()
    for hit in re.finditer(r"([abc])\s*(?:区域|校区|区)", text):
        area = hit.group(1).upper()
        (criteria.excluded_areas if _is_negated(text, hit.start()) else criteria.areas).add(area)
        criteria.mark(hit.start(), hit.end())
    if criteria.areas & criteria.excluded_areas:
        criteria.errors.append("同一区域同时被选中和排除，请明确要保留的区域。")
    for value, key, prefix in [(criteria.areas, "areas", "区域"),
                               (criteria.excluded_areas, "excluded_areas", "排除区域")]:
        if value:
            criteria.add_filter(key, f"{prefix}：{' / '.join(sorted(value))} 区", sorted(value))

    for unit in units:
        for hit in _aliases(text, (unit.name, *UNIT_ALIASES.get(unit.name, ()))):
            (criteria.excluded_units if _is_negated(text, hit.start()) else criteria.units).add(unit.id)
            criteria.mark(hit.start(), hit.end())
    if criteria.units & criteria.excluded_units:
        criteria.errors.append("同一单位同时被选中和排除，请明确单位条件。")
    for values, key, prefix in [(criteria.units, "units", "单位"),
                                (criteria.excluded_units, "excluded_units", "排除单位")]:
        if values:
            names = [unit.name for unit in units if unit.id in values]
            criteria.add_filter(key, f"{prefix}：{' / '.join(names)}", sorted(values))

    positive_skill_hits = []
    for skill, aliases in SKILL_ALIASES.items():
        for hit in _aliases(text, aliases):
            if _is_negated(text, hit.start()):
                criteria.excluded_skills.add(skill)
            else:
                criteria.skills.add(skill)
                positive_skill_hits.append((hit.start(), hit.end()))
            criteria.mark(hit.start(), hit.end())
    if len(criteria.skills) > 1:
        skill_phrase = text[min(hit[0] for hit in positive_skill_hits):max(hit[1] for hit in positive_skill_hits)]
        criteria.any_skill = bool(re.search(r"或|任一|任意一种", skill_phrase))
    no_skills_hit = re.search(r"不限技能|不要求技能|无技能要求|无需技能", text)
    if no_skills_hit:
        criteria.no_skills = True
        criteria.mark(no_skills_hit.start(), no_skills_hit.end())
        criteria.add_filter("no_skills", "岗位不限技能", True)
    if criteria.skills & criteria.excluded_skills or criteria.skills and criteria.no_skills:
        criteria.errors.append("技能条件互相冲突，请选择要求指定技能，或选择不限技能岗位。")
    if criteria.skills:
        labels = " / ".join(SKILLS[key] for key in sorted(criteria.skills))
        criteria.add_filter("skills", f"岗位技能包含{'任一项' if criteria.any_skill else '全部'}：{labels}",
                            {"values": sorted(criteria.skills), "operator": "any" if criteria.any_skill else "all"})
        criteria.warnings.append("提到的技能按岗位要求筛选；这次提问不会修改你的技能档案。")
    if criteria.excluded_skills:
        labels = " / ".join(SKILLS[key] for key in sorted(criteria.excluded_skills))
        criteria.add_filter("excluded_skills", f"排除要求这些技能的岗位：{labels}", sorted(criteria.excluded_skills))

    categories = set()
    category_hits = []
    for label, category in [("临时", "temporary"), ("固定", "fixed")]:
        for hit in re.finditer(label + r"(?:岗位|岗|工作)?", text):
            if _is_negated(text, hit.start()):
                categories.add("fixed" if category == "temporary" else "temporary")
            else:
                categories.add(category)
            category_hits.append((hit.start(), hit.end()))
            criteria.mark(hit.start(), hit.end())
    if len(categories) == 1:
        criteria.category = categories.pop()
        criteria.add_filter("category", "临时岗位" if criteria.category == "temporary" else "固定岗位",
                            criteria.category)
    elif len(categories) > 1:
        first, last = min(hit[0] for hit in category_hits), max(hit[1] for hit in category_hits)
        category_phrase, category_suffix = text[first:last], text[last:last + 8]
        if not re.search(r"或", category_phrase) and not re.match(r"\s*(?:都可以|均可|两种都|类型不限)", category_suffix):
            criteria.errors.append("固定岗和临时岗同时出现，请选择一种，或明确说两种都可以。")

    wage_signal = bool(re.search(r"工资|薪资|时薪|月薪|每小时|每月|元|块|报酬", text))
    hourly = bool(re.search(r"时薪|每小时|/小时|元一小时|元每小时|小时至少", text))
    monthly = bool(re.search(r"月薪|每月|/月|元一个月|月基准", text))
    wage_hits = list(re.finditer(r"(-?\d+(?:\.\d+)?)\s*(?:元|块)", text)) if wage_signal else []
    if not wage_hits and wage_signal:
        wage_hits = list(re.finditer(r"(?:时薪|月薪|工资|薪资|每小时|每月)\s*"
                                    r"(?:至少|不低于|不少于|最低|>=|≥|为|:)?\s*(-?\d+(?:\.\d+)?)", text))
    if wage_signal and wage_hits:
        values = {Decimal(hit.group(1)) for hit in wage_hits}
        if len(values) > 1:
            criteria.errors.append("检测到多个工资数值；当前支持一个最低工资，请一次给出一个下限。")
        elif not re.search(r"至少|不低于|不少于|最低|以上|起|>=|≥", text):
            criteria.errors.append("请明确工资下限，例如“时薪至少 20 元”；当前不处理精确工资或上限条件。")
        elif re.search(r"以下|不高于|不超过|最多|以内|<=|≤", text):
            criteria.errors.append("当前支持最低工资，不支持工资区间或最高工资，请保留一个明确下限。")
        elif hourly and monthly:
            criteria.errors.append("时薪和月薪不能直接比较，请一次选择一种计薪方式。")
        elif not hourly and not monthly and not criteria.category:
            criteria.errors.append("这个工资数值缺少单位，请说明时薪或固定岗月薪基准。")
        elif next(iter(values)) < 0 or next(iter(values)) > Decimal("100000"):
            criteria.errors.append("工资下限应在 0 至 100000 元之间。")
        else:
            criteria.wage = next(iter(values))
            criteria.wage_category = "temporary" if hourly else "fixed" if monthly else criteria.category
            if criteria.category and criteria.category != criteria.wage_category:
                criteria.errors.append("岗位类型与工资单位不一致：临时岗按时薪，固定岗按月薪基准筛选。")
            prefix = "时薪" if criteria.wage_category == "temporary" else "固定岗月薪基准"
            criteria.add_filter("minimum_wage", f"{prefix}至少 {criteria.wage:g} 元",
                                {"amount": str(criteria.wage), "category": criteria.wage_category})
        for hit in wage_hits:
            criteria.mark(hit.start(), hit.end())
    elif wage_signal and re.search(r"工资|薪资|时薪|月薪|报酬", text):
        criteria.warnings.append("工资要求未含明确数字，未设置工资筛选；请试“时薪至少 20 元”。")

    for hit in re.finditer(r"(?:周|星期|礼拜)([一二三四五六日天1234567])", text):
        if _is_negated(text, hit.start()):
            criteria.errors.append("当前时间筛选需要正向条件，请改为“周二下午”等明确可用时段。")
        criteria.days.add(DAY_CODES[hit.group(1)])
        criteria.mark(hit.start(), hit.end())
    for hit in re.finditer("周末", text):
        criteria.days.update({6, 7})
        criteria.mark(hit.start(), hit.end())
        if _is_negated(text, hit.start()):
            criteria.errors.append("请用明确的工作日时间替代排除周末条件。")
    periods = set()
    for label, window in PERIODS.items():
        for hit in re.finditer(label, text):
            periods.add(window)
            criteria.mark(hit.start(), hit.end())
            if _is_negated(text, hit.start()):
                criteria.errors.append("当前时间筛选需要正向条件，请直接给出需要的时间范围。")
    time_pattern = (r"(\d{1,2}(?:[:：]\d{2}|点(?:半|\d{1,2}分?)?)?)\s*"
                    r"(?:-|–|—|~|～|至|到)\s*(\d{1,2}(?:[:：]\d{2}|点(?:半|\d{1,2}分?)?)?)")
    windows = set()
    for hit in re.finditer(time_pattern, text):
        # A bare salary range is never interpreted as an hour range.
        if not (criteria.days or any(char in hit.group(0) for char in [":", "：", "点"])):
            continue
        try:
            start, end = _clock(hit.group(1)), _clock(hit.group(2))
            if start >= end:
                raise ValueError("inverted range")
            windows.add((start, end))
        except ValueError:
            criteria.errors.append("时间范围无效；请使用同一天的开始和结束时间，例如“周一 14:00–16:00”。")
        criteria.mark(hit.start(), hit.end())
    if len(windows) > 1 or len(periods) > 1:
        criteria.errors.append("检测到多个不同时间范围；请一次使用同一范围，例如“周一或周二下午”。")
    elif windows:
        criteria.window = next(iter(windows))
        if periods and not (next(iter(periods))[0] <= criteria.window[0] < criteria.window[1] <= next(iter(periods))[1]):
            criteria.errors.append("文字时段和具体时间不一致，请明确使用一个时间范围。")
    elif periods:
        criteria.window = next(iter(periods))
    if criteria.days or criteria.window:
        days_label = " / ".join(DAY_NAMES[day] for day in sorted(criteria.days)) if criteria.days else "任意周几"
        window_label = _time_label(criteria.window) if criteria.window else "全天"
        criteria.add_filter("schedule", f"至少一个完整岗位时段在：{days_label} {window_label}",
                            {"days": sorted(criteria.days) or list(range(1, 8)),
                             "window": list(criteria.window or (0, 1440)), "operator": "one_complete_slot"})
        criteria.warnings.append("时间筛选检查至少一个完整岗位时段；其余排班仍需核对详情。档案匹配会计算全部时段覆盖率。")
        if re.search(r"只|仅", text):
            criteria.errors.append("“只在这些时间有空”需要核对全部排班。请先在我的档案填写完整可用时间，再查看规则匹配。")

    keyword_hit = re.search(r"(?:关键词|岗位名包含|岗位名称包含|岗位名含)\s*[:：]?\s*[\"“]?([^，,。；;！？?\"”]{1,30})", text)
    if not keyword_hit:
        keyword_hit = re.search(r"[\"“]([^\"”]{1,30})[\"”]", text)
    if keyword_hit:
        criteria.title_keyword = keyword_hit.group(1).strip()
        criteria.mark(keyword_hit.start(), keyword_hit.end())
        criteria.add_filter("title_keyword", f"岗位名称含：{criteria.title_keyword}", criteria.title_keyword)

    # Explain unparsed text rather than implying arbitrary natural language was understood.
    residual = list(text)
    for start, end in criteria.consumed:
        residual[start:end] = " " * (end - start)
    residual = "".join(residual)
    grammar = (r"正在招聘|开放申请|招聘中|适合我的|适合我|帮我找|帮我|我希望|我想要|我想|我会|"
               r"我擅长|想找|推荐|岗位|职位|工作|检索|筛选|查找|查询|看看|选择|要求|"
               r"哪些|什么|有没有|是否有|可以申请|可申请|申请|相关|符合|包括|包含|"
               r"工资|薪资|时薪|月薪|每小时|每月|小时|报酬|元|块|最低|至少|不低于|不少于|"
               r"以上|>=|≥|起|不想要|不要|不需要|排除|避开|不会|不懂|不擅长|不考虑|不在|"
               r"不去|不用|无需|不熟悉|没学过|有空|空闲|可上班|能上班|上班|课余|时间|时段|"
               r"都可以|均可|任意一种|任一项|任一|任意|不限|或|或者|和|与|且|以及|也|"
               r"请|现在|在招|给我|一些|一个|的|我|要|能|有|在|会|擅长|到|为")
    residual = re.sub(grammar, "", residual)
    residual = re.sub(r"[\s，,。；;！？?、/\\()（）:+\-=<>]+", "", residual)
    if residual:
        criteria.warnings.append(f"这段表述未转为筛选条件：{residual[:80]}。可以改为明确的单位、区域、技能、类型、工资下限或周几时段。")
    criteria.warnings = list(dict.fromkeys(criteria.warnings))
    criteria.errors = list(dict.fromkeys(criteria.errors))
    return criteria


def _refusal(question):
    text = unicodedata.normalize("NFKC", question).lower()
    if re.search(r"草稿|未发布|已关闭|已满额|他人|别人|其他学生|所有学生|所有用户|所有申请|"
                 r"密码|令牌|token|jwt|\.env|数据库连接|系统提示|内部提示|管理员权限|越权|"
                 r"\b(?:select|insert|delete|drop|update)\s+", text):
        return "我只能检索当前在招且你本人可申请的岗位，不能提供草稿、其他人的记录、账号凭据或内部信息。"
    if re.search(r"审核|批准|驳回|撤销|撤回|登记|更正|发布|下架|删除|修改|更新|设置|启用|禁用|"
                 r"确认上岗|结束岗位|转账|结算|发薪|自动申请|代申请|替我申请|帮我申请|申请这个|"
                 r"申请岗位|提交申请|我要申请|报名|提交工时", text):
        return "我可以帮你找岗位和解释匹配原因，但不能代申请、审核或修改业务数据。请打开岗位详情，由本人填写理由并提交；其他业务使用对应角色的页面。"
    return None


def _missing_profile(user):
    missing = []
    if not user.slots:
        missing.append("可用时段待填写")
    if user.skills is None:
        missing.append("技能档案待填写")
    if user.area not in {"A", "B", "C"}:
        missing.append("常用区域待填写")
    if not user.hardship_confirmed or user.hardship not in {"D1", "D2", "D3"}:
        missing.append("困难等级待资助中心确认")
    return missing


def _fits(criteria, job, skills):
    if criteria.areas and job.area not in criteria.areas or job.area in criteria.excluded_areas:
        return False
    if criteria.units and job.unit_id not in criteria.units or job.unit_id in criteria.excluded_units:
        return False
    if criteria.category and job.category != criteria.category:
        return False
    if criteria.wage is not None and (job.category != criteria.wage_category or job.wage < criteria.wage):
        return False
    if criteria.no_skills and skills or criteria.excluded_skills & skills:
        return False
    if criteria.skills and (not criteria.skills & skills if criteria.any_skill else not criteria.skills <= skills):
        return False
    if criteria.title_keyword and criteria.title_keyword not in job.title.lower():
        return False
    if criteria.days or criteria.window:
        days = criteria.days or set(range(1, 8))
        start, end = criteria.window or (0, 1440)
        if not any(slot["day"] in days and start <= _clock(slot["start"]) < _clock(slot["end"]) <= end
                   for slot in job.slots):
            return False
    return True


def _item(db, user, job, data, setting, filters):
    score = match_score(user, job, data["skills"], setting.value) if setting else None
    if score:
        score.pop("_raw_total")
        score.pop("_raw_time")
        score["parameter_version"] = setting.version
    reasons = [f["label"] for f in filters]
    if score:
        if score["required_minutes"] and user.slots:
            reasons.append(f"档案可用时段覆盖 {score['covered_minutes']} / {score['required_minutes']} 分钟")
        if score["hit_skills"]:
            reasons.append("档案技能命中：" + "、".join(SKILLS[skill] for skill in score["hit_skills"]))
        elif not data["skills"]:
            reasons.append("岗位不限技能")
        if user.area:
            reasons.append(score["location_note"])
        if score["missing"]:
            reasons.append("匹配分数未完整计算：" + "；".join(score["missing"]))
    else:
        reasons.append("匹配参数尚未配置，未生成匹配分数")
    return {"job": data, "matching": score, "reasons": list(dict.fromkeys(reasons))}


def _build_reply(db, user, question):
    response = {"kind": "assistant", "mode": "rule_assistant", "question": question, "answer": "",
                "items": [], "filters": [], "warnings": [], "missing_profile": _missing_profile(user),
                "total": 0, "shown": 0, "status": "matched", "notice": NOTICE}
    refusal = _refusal(question)
    if refusal:
        return {**response, "answer": refusal, "status": "refused"}
    if re.search(r"政策|每周.{0,8}(?:上限|最多|多少小时)|每月.{0,8}(?:上限|最多|多少小时)|学校规定|本校规定", question):
        return {**response, "answer": "政策问题请使用“政策查询”，查看已核验原文和出处。岗位助手不生成政策结论。",
                "status": "help", "action": {"label": "打开政策查询", "path": "/policies"}}
    if re.search(r"(?:我的|本人)(?:申请|工时|工资|薪酬|审核进度)", question):
        return {**response, "answer": "请在“我的申请”或“工时薪酬”查看本人的业务记录。这里提供当前岗位条件检索。", "status": "help"}
    units = list(db.scalars(select(Unit).order_by(Unit.id)))
    criteria = _parse_question(question, units)
    response["filters"] = [{"key": "scope", "label": "当前在招且本人可申请", "value": "published_applyable"},
                           *criteria.filters]
    response["warnings"] = criteria.warnings
    if criteria.errors:
        return {**response, "filters": [], "parsed_filters": criteria.filters,
                "answer": "需要先明确条件：" + " ".join(criteria.errors), "status": "needs_clarification"}
    if not criteria.filters and criteria.warnings:
        return {**response, "filters": [],
                "answer": "暂时没有识别出可执行的条件。请试“推荐适合我的岗位”或“图书馆 A 区临时岗，时薪至少 20 元”。",
                "status": "needs_clarification"}
    if not criteria.filters and not re.search(r"岗位|工作|职位|推荐|找|适合我|检索|筛选|查询", question):
        return {**response, "filters": [],
                "answer": "我可以按单位、区域、技能、固定或临时岗位、最低工资和周几时段检索。请用一句话说明想找的岗位。",
                "status": "help"}
    setting = db.scalar(select(SystemSetting).where(SystemSetting.key == "matching_weights"))
    if not setting:
        response["warnings"].append("系统尚未配置匹配参数，结果暂按发布时间排序。")
    items = []
    for job in db.scalars(select(Job).where(Job.status == "published")):
        data = job_data(db, job, user.id)
        if not data["can_apply"] or data["remaining"] <= 0 or not _fits(criteria, job, set(data["skills"])):
            continue
        items.append(_item(db, user, job, data, setting, criteria.filters))
    items.sort(key=lambda item: (item["matching"] is None or item["matching"]["total"] is None,
                                 -Decimal((item["matching"] or {}).get("total") or "0"),
                                 -Decimal(((item["matching"] or {}).get("parts") or {}).get("time") or "0"),
                                 -(job_published_time(item["job"]["published_at"])), item["job"]["id"]))
    response["total"] = len(items)
    response["items"] = items[:MAX_RESULTS]
    response["shown"] = len(response["items"])
    if not items:
        response["status"] = "no_match"
        response["answer"] = "没有找到同时满足这些条件的可申请岗位。可以降低工资下限、放宽区域或去掉一个条件，再重新检索。"
    else:
        response["status"] = "partial" if response["warnings"] else "matched"
        response["answer"] = f"找到 {len(items)} 个符合已生效条件的可申请岗位，展示前 {response['shown']} 个。"
        response["answer"] += "按档案匹配分数排序。" if setting else "按发布时间排序。"
        if response["missing_profile"]:
            response["answer"] += "你的档案有待补充项，未生成完整匹配分数。"
    return response


def job_published_time(value):
    # Lexical UTC-independent integer preserves local chronological ordering.
    return int(re.sub(r"\D", "", value or "")[:14] or "0")


def assistant_reply(db, user, data):
    _require_student(user)
    response = _build_reply(db, user, data.question)
    created_at = now()
    response["created_at"] = stamp(created_at)
    db.add(ChatLog(user_id=user.id, conversation_id=data.conversation_id, question=data.question,
                   response=response, created_at=created_at))
    return response


def assistant_history(db, user, conversation_id=None):
    _require_student(user)
    stmt = select(ChatLog).where(ChatLog.user_id == user.id, ChatLog.response["kind"].as_string() == "assistant")
    if conversation_id is not None:
        stmt = stmt.where(ChatLog.conversation_id == conversation_id)
    rows = list(db.scalars(stmt.order_by(ChatLog.id.desc()).limit(20)))
    # Re-run historical questions against current public data, never return stale
    # snapshots of jobs that have become private/closed or no longer applyable.
    return {"items": [{"id": row.id, "conversation_id": row.conversation_id, "question": row.question,
                        "response": {**_build_reply(db, user, row.question), "historical": True,
                                     "history_notice": "历史提问，岗位与匹配结果按当前数据重新计算。"},
                        "created_at": stamp(row.created_at)} for row in reversed(rows)]}

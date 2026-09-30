"""Grounded policy lookup and private conversation history.

This is deterministic, topic-aware original-text retrieval. Source documents and
past messages are data; they cannot supply instructions or authorize operations.
"""

import re
import unicodedata

from sqlalchemy import and_, func, or_, select

from .models import ChatLog, PolicyDoc
from .services import now, stamp


# Query aliases expand into terms that must actually occur in verified sections.
# They never supply an answer or a policy value.
TOPICS = {
    "weekly": (("每周", "一周", "每星期", "一个星期", "周工时", "周上限"), ("每周",)),
    "monthly": (("每月", "一个月", "月工时", "月上限"), ("每月", "按月")),
    "holiday": (("寒暑假", "寒假", "暑假", "假期", "放假"), ("寒暑假",)),
    "worktime": (("工时", "工作时间", "工作多久", "几小时", "多长时间", "时间限制"),
                 ("时间原则上", "每周")),
    "fixed": (("固定岗位", "固定岗", "长期岗位", "长期岗"), ("固定岗位",)),
    "temporary": (("临时岗位", "临时岗", "短期岗位", "短期岗"), ("临时岗位",)),
    "pay": (("报酬", "酬金", "工资", "薪酬", "薪资", "收入", "计酬", "多少钱", "多少元", "按小时"),
            ("报酬", "酬金", "工资", "计酬")),
    "payment": (("谁发", "谁付", "发放", "支付", "发钱"), ("发放", "支付")),
    "hardship": (("家庭经济困难", "困难学生", "贫困", "困难优先", "扶困", "优先考虑"),
                 ("家庭经济困难", "扶困优先")),
    "application": (("申请", "报名", "招聘", "怎么参加", "如何参加"), ("申请",)),
    "off_campus": (("校外", "社会兼职", "外面兼职"), ("校外",)),
    "agreement": (("协议", "合同", "签约"), ("协议",)),
    "safety": (("危险", "有毒", "有害", "安全", "受伤", "事故"),
               ("危险", "有毒", "有害", "安全", "伤害事故")),
    "training": (("培训", "岗前", "上岗前"), ("培训", "岗前")),
    "dispute": (("纠纷", "争议", "赔偿"), ("纠纷", "争议")),
    "organization": (("管理机构", "管理组织", "谁管理", "组织", "负责"),
                     ("资助管理机构", "管理服务组织", "统一组织和管理")),
    "definition": (("什么是勤工助学", "勤工助学是什么", "勤工助学的定义"), ("所称勤工助学活动",)),
    "principle": (("原则", "宗旨"), ("原则", "宗旨")),
}

SCHOOL_MARKERS = ("我校", "本校", "我们学校", "我们大学", "咱们学校", "我所在的学校", "学校细则", "本学院")
NATIONAL_MARKERS = ("全国", "国家规定", "教育部", "国家政策", "通用规定")
FOLLOWUP_MARKERS = ("那", "这个", "这些", "该规定", "上述", "刚才", "继续", "详细", "依据", "具体怎么", "呢")
NOTICE = "政策原文查询；未调用大模型，不生成政策结论。核验来源不代表已确认现行效力，请结合版本与学校正式细则阅读。"


def _normalized(value):
    return unicodedata.normalize("NFKC", value).lower()


def _article_number(value):
    if value.isdigit():
        return int(value)
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    units = {"十": 10, "百": 100, "千": 1000}
    total, current = 0, 0
    for char in value:
        if char in digits:
            current = digits[char]
        elif char in units:
            total += (current or 1) * units[char]
            current = 0
        else:
            return None
    return total + current


def _articles(value):
    matches = re.findall(r"(?:第\s*)?([零〇一二两三四五六七八九十百千\d]+)\s*条", _normalized(value))
    return sorted({number for match in matches if (number := _article_number(match)) is not None})


def _generic_followup(question):
    """Only inherit an omitted subject, never an unknown new subject."""
    compact = re.sub(r"[\s?？。!！,，:：;；]+", "", _normalized(question))
    subject = r"(?:那|这个|这些|该规定|上述规定|上述条款|上述|刚才的规定|刚才|这条规定|这个规定|这条|它)?"
    request = (r"(?:具体(?:是)?怎么规定(?:的)?|具体(?:的)?规定(?:是什么)?|是什么(?:意思)?|什么意思|"
               r"怎么理解|详细(?:说说|解释|说明|内容)|详细一点|有(?:什么)?依据|依据(?:是(?:什么|哪条))?|"
               r"出处(?:在哪里|是什么|是哪里)?|请(?:再)?详细(?:说说|解释|说明)|再(?:说说|解释|说明)|"
               r"继续(?:解释|说明|说说)?|展开(?:说说|解释|说明)?)")
    return bool(re.fullmatch(subject + r"(?:能否|可以|能)?" + request + r"(?:吗|呢|呀|一下)?", compact))


def _policy_logs(user_id, conversation_id=None):
    kind = ChatLog.response["kind"].as_string()
    mode = ChatLog.response["mode"].as_string()
    stmt = select(ChatLog).where(ChatLog.user_id == user_id,
                                or_(kind == "policy", and_(kind.is_(None), mode == "original_query")))
    if conversation_id is not None:
        stmt = stmt.where(ChatLog.conversation_id == conversation_id)
    return stmt


def _doc_source(doc):
    return {"id": doc.id, "title": doc.title, "publisher": doc.publisher, "source_url": doc.source_url,
            "version": doc.version, "verified": doc.verified, "verified_at": stamp(doc.verified_at),
            "imported_at": stamp(doc.imported_at), "verification_note": doc.verification_note,
            "is_school_policy": doc.is_school_policy, "section_count": len(doc.sections)}


def _query_plan(question, previous):
    normalized = _normalized(question)
    topics = {name for name, (aliases, _terms) in TOPICS.items() if any(alias in normalized for alias in aliases)}
    if re.search(r"\d+(?:\.\d+)?\s*元", normalized):
        topics.add("pay")
    articles = _articles(normalized)
    previous_plan = previous.response.get("retrieval", {}) if previous else {}
    explicit_school = any(marker in normalized for marker in SCHOOL_MARKERS)
    explicit_national = any(marker in normalized for marker in NATIONAL_MARKERS)
    followup = bool(previous and (any(marker in normalized for marker in FOLLOWUP_MARKERS)
                                 or _generic_followup(normalized)))
    school = explicit_school or (followup and not explicit_national and previous_plan.get("scope") == "school")
    context_topics = set()
    context_articles = []
    if followup and not articles:
        old_topics = set(previous_plan.get("topics", [])) & TOPICS.keys()
        if not topics and _generic_followup(normalized):
            context_topics = old_topics
            # Re-fetch originals rather than trusting stored citation text.
            context_articles = previous_plan.get("article_numbers", [])
            if not context_topics and not context_articles:
                context_articles = sorted({number for hit in previous.response.get("citations", [])
                                           for number in _articles(hit.get("location", ""))})
        elif {"fixed", "temporary"} & topics and "pay" in old_topics and "pay" not in topics:
            context_topics = {"pay"}
        elif "pay" in topics and not ({"fixed", "temporary"} & topics):
            context_topics = old_topics & {"fixed", "temporary", "off_campus"}
        elif "payment" in topics:
            context_topics = old_topics & {"off_campus"}
    topics |= context_topics
    used_context = bool(context_topics or context_articles or (school and not explicit_school))
    return {"topics": sorted(topics), "article_numbers": articles or context_articles,
            "scope": "school" if school else ("national" if explicit_national else "all"),
            "used_context": used_context,
            "previous_question": previous.question if used_context else None}


def _score_section(section, question, plan):
    text = _normalized(section["text"])
    location = _normalized(section["location"])
    section_articles = _articles(location)
    if plan["article_numbers"]:
        return 100 if set(section_articles) & set(plan["article_numbers"]) else 0
    topics = set(plan["topics"])
    found = {name for name in topics if any(term in text for term in TOPICS[name][1])}
    # A combined question must keep its specific dimension: "临时岗工资" should
    # not retrieve the fixed-pay rule, and "每周" must not retrieve monthly pay.
    required = topics & {"weekly", "holiday", "fixed", "temporary", "off_campus"}
    both_categories_pay = {"fixed", "temporary", "pay"} <= topics
    if both_categories_pay:
        required -= {"fixed", "temporary"}
        if not ({"fixed", "temporary"} & found):
            return 0
    if required and not required <= found:
        return 0
    if "pay" in topics and "pay" not in found:
        return 0
    if "monthly" in topics and not ({"fixed", "temporary", "pay"} & topics) and "monthly" not in found:
        return 0
    if "worktime" in topics and "pay" not in topics and "worktime" not in found:
        return 0
    if topics:
        if not found:
            return 0
        score = len(found) * 10
        # Keep the concrete limit clause above paragraphs that merely say "每月".
        if topics & {"weekly", "monthly", "worktime", "holiday"} and "时间原则上" in text:
            score += 5
        if "payment" in topics and "支付" in text:
            score += 5
        return score
    compact = re.sub(r"[\s?？。!！,，:：;；]+", "", _normalized(question))
    return 10 if compact and (compact in text or compact in location) else 0


def query_policy(db, user, data):
    previous = db.scalar(_policy_logs(user.id, data.conversation_id).order_by(ChatLog.id.desc()).limit(1))
    plan = _query_plan(data.question, previous)
    docs = list(db.scalars(select(PolicyDoc).where(PolicyDoc.verified.is_(True)).order_by(PolicyDoc.id)))
    if plan["scope"] == "school":
        docs = [doc for doc in docs if doc.is_school_policy]
    elif plan["scope"] == "national":
        docs = [doc for doc in docs if not doc.is_school_policy]
    hits = []
    for doc in docs:
        for index, section in enumerate(doc.sections):
            score = _score_section(section, data.question, plan)
            if not score and not plan["article_numbers"] and data.question in doc.title:
                score = 1
            if score:
                hits.append({"score": score, "index": index, "text": section["text"],
                             "location": section["location"], "source": _doc_source(doc)})
    hits.sort(key=lambda hit: (-hit["score"], hit["source"]["id"], hit["index"]))
    citations = [{key: value for key, value in hit.items() if key not in {"score", "index"}}
                 for hit in hits[:5]]
    if citations:
        answer = f"找到 {len(citations)} 段已核验原文，请结合条款上下文阅读。"
    elif plan["scope"] == "school" and not docs:
        answer = "当前知识库没有可靠依据：尚未收录已核验的本校细则。请向学校资助中心确认。"
    else:
        answer = "当前知识库没有可靠依据。可换个表述或指定条款编号；学校细则尚未收录时，请向学校资助中心确认。"
    response = {"kind": "policy", "mode": "original_query", "question": data.question,
                "conversation_id": data.conversation_id, "answer": answer,
                "citations": citations, "notice": NOTICE, "retrieval": plan}
    db.add(ChatLog(user_id=user.id, conversation_id=data.conversation_id, question=data.question,
                   response=response, created_at=now()))
    db.flush()
    return response


def policy_history(db, user, conversation_id=None):
    rows = list(db.scalars(_policy_logs(user.id, conversation_id).order_by(ChatLog.id.desc()).limit(100)))
    if conversation_id is not None:
        rows.reverse()
    return {"items": [{"id": row.id, "conversation_id": row.conversation_id, "question": row.question,
                       "response": row.response, "created_at": stamp(row.created_at)} for row in rows]}


def policy_conversations(db, user):
    visible = _policy_logs(user.id).subquery()
    groups = db.execute(select(visible.c.conversation_id, func.min(visible.c.id), func.max(visible.c.id),
                               func.count()).group_by(visible.c.conversation_id)
                        .order_by(func.max(visible.c.id).desc()).limit(50))
    items = []
    for conversation_id, first_id, last_id, count in groups:
        first, last = db.get(ChatLog, first_id), db.get(ChatLog, last_id)
        items.append({"conversation_id": conversation_id, "title": first.question[:50],
                      "message_count": count, "last_question": last.question,
                      "updated_at": stamp(last.created_at)})
    return {"items": items}

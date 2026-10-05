"""Grounded policy lookup and private conversation history.

Scope/time filtering precedes keyword and local semantic retrieval. Source
documents and past messages are data, never instructions or authorization.
"""

import re
import unicodedata
from types import SimpleNamespace

from sqlalchemy import and_, func, or_, select

from . import rag
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
    "pay": (("报酬", "酬金", "工资", "薪酬", "薪资", "时薪", "收入", "计酬", "多少钱", "多少元", "按小时",
             "能赚", "能挣", "能拿"),
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
    "school_system": (("有没有勤工助学", "有勤工助学", "勤工助学制度", "是否有勤工助学"),
                      ("学校建立勤工助学制度",)),
    "address": (("学校地址", "校区地址", "校区在哪", "学校在哪", "学校位于", "江夏", "哪个区",
                 "武汉设计工程学院在哪"),
                ("武汉校区", "江夏区", "学校地址")),
    "consultation": (("咨询", "联系电话", "电话", "找哪个部门", "找谁", "联系谁", "资助中心"),
                     ("经济资助中心", "联系方式", "027-81733022")),
    "recognition": (("困难认定", "经济困难学生认定", "经济困难认定", "认定档", "困难等级", "困难档", "几档"),
                    ("认定", "资助档次")),
    "hardship_levels": (("困难等级", "困难档", "认定档", "几档", "认定等级"),
                        ("认定等级", "资助档次", "困难等级", "两个等级")),
    "proof": (("民政盖章", "民政证明", "困难证明", "经济情况证明", "书面承诺", "证明材料"),
              ("民政部门", "书面承诺", "证明", "支撑材料")),
    "minimum_wage": (("最低工资", "最低小时工资", "非全日制", "劳动关系", "劳动标准"),
                     ("最低工资", "最低小时工资", "劳动关系", "非全日制")),
}

SCHOOL_MARKERS = ("我校", "本校", "我们学校", "我们大学", "咱们学校", "我所在的学校", "学校细则", "本学院",
                  "武汉设计工程学院", "武设院", "学校勤工助学")
NATIONAL_MARKERS = ("全国", "国家规定", "教育部", "国家政策", "通用规定")
FOLLOWUP_MARKERS = ("那", "这个", "这些", "该规定", "上述", "刚才", "继续", "详细", "依据", "具体怎么", "呢")
NOTICE = "政策原文查询；未调用大模型，不生成政策结论。核验来源不代表已确认现行效力，请结合版本与学校正式细则阅读。"
SOURCE_NAMES = ("藏龙美术馆", "美术馆", "学生助理", "招生章程", "取消一批证明")


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


def policy_document_data(doc, include_sections=False):
    metadata = doc.source_metadata or {}
    expired = bool(doc.expires_at and doc.expires_at < now())
    result = {"source_key": doc.source_key, "usage_scope": doc.usage_scope,
              "current_answer_allowed": doc.current_answer_allowed,
              "publication_date": doc.publication_date.isoformat() if doc.publication_date else None,
              "effective_from": doc.effective_from.isoformat() if doc.effective_from else None,
              "expires_at": stamp(doc.expires_at), "past_deadline": expired,
              "applicability": doc.applicability or "", "limitations": metadata.get("limitations", []),
              "verification_status": metadata.get("verification_status", "verified_original"),
              "validity_evidence": metadata.get("validity_evidence", doc.verification_note),
              "category": metadata.get("category"), "document_number": metadata.get("document_number"),
              "signed_date": metadata.get("signed_date"), "school": metadata.get("school"),
              "campus": metadata.get("campus"), "raw_sha256": metadata.get("raw_sha256"),
              "curated_text_sha256": metadata.get("curated_text_sha256")}
    result.update(_doc_source_legacy(doc))
    if include_sections:
        result["sections"] = doc.sections
    return result


def _doc_source_legacy(doc):
    return {"id": doc.id, "title": doc.title, "publisher": doc.publisher, "source_url": doc.source_url,
            "version": doc.version, "verified": doc.verified, "verified_at": stamp(doc.verified_at),
            "imported_at": stamp(doc.imported_at), "verification_note": doc.verification_note,
            "is_school_policy": doc.is_school_policy, "section_count": len(doc.sections)}


def _query_plan(question, previous):
    if isinstance(previous, str):
        previous = SimpleNamespace(question=previous, response={"retrieval": _query_plan(previous, None)})
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
    years = sorted(set(re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", normalized)))
    source_ids = sorted({f"S{identity}" for identity in re.findall(r"(?<![a-z0-9])s(\d{2,3})(?!\d)", normalized)})
    archive_requested = bool(years or source_ids or any(word in normalized for word in
                                                      ("历史", "以前", "当年", "旧版", "往年")))
    # An explicitly historical question may ask whether that old batch is still
    # open. An unqualified "现在" follow-up instead returns to current evidence.
    current_request = any(word in normalized for word in ("现在", "当前", "今年", "最新"))
    inherited_source = (followup and not current_request and not archive_requested and
                        (_generic_followup(normalized) or
                         (bool(topics) and topics <= set(previous_plan.get("topics", [])))))
    if inherited_source:
        years = previous_plan.get("years", [])
        source_ids = previous_plan.get("source_ids", [])
        archive_requested = previous_plan.get("archive_requested", False)
        used_context = bool(used_context or years or source_ids)
    names = [name for name in SOURCE_NAMES if name in normalized]
    if "藏龙美术馆" in names:
        names.remove("美术馆")
    if inherited_source and not names:
        names = previous_plan.get("source_names", [])
        used_context = bool(used_context or names)
    return {"topics": sorted(topics), "article_numbers": articles or context_articles,
            "scope": "school" if school else ("national" if explicit_national else "all"),
            "used_context": used_context,
            "previous_question": previous.question if used_context else None,
            "years": years, "source_ids": source_ids, "archive_requested": archive_requested,
            "source_names": names, "labor_question": "minimum_wage" in topics}


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
    required = topics & {"weekly", "holiday", "fixed", "temporary", "off_campus", "school_system",
                         "address", "consultation", "recognition", "hardship_levels", "proof", "minimum_wage"}
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
        if topics & {"minimum_wage", "address"}:
            score += sum(5 for place in ("武汉", "湖北", "江夏") if place in _normalized(question) and place in text)
        return score
    compact = re.sub(r"[\s?？。!！,，:：;；]+", "", _normalized(question))
    return 10 if compact and (compact in text or compact in location) else 0


def _document_allowed(doc, plan, reference_time):
    metadata = doc.source_metadata or {}
    years = plan["years"]
    if plan["source_ids"] and doc.source_key not in plan["source_ids"]:
        return False, "不属于指定资料编号"
    if plan["source_names"] and not any(name in doc.title for name in plan["source_names"]):
        return False, "不属于指定资料名称"
    if years and not any(year in str(doc.publication_date or "") + doc.version +
                         str(metadata.get("signed_date", "")) for year in years):
        return False, "不属于明确询问的年份版本"
    if doc.usage_scope == "labor_reference" and not plan["labor_question"] and not plan["source_ids"]:
        return False, "劳动关系工资资料不能充当本校勤工助学报酬规定"
    archive = doc.usage_scope == "archive_only" or not doc.current_answer_allowed
    expired = bool(doc.expires_at and doc.expires_at < reference_time)
    if (archive or expired) and not plan["archive_requested"]:
        return False, "历史、截止或效力未确认资料仅供明确的历史查询"
    if doc.effective_from and doc.effective_from > reference_time.date():
        return False, "尚未到执行日期"
    return True, None


def retrieve_policy(db, question, previous=None, limit=5):
    """Read verified evidence without logging or changing business records.

    This same scope/time filter is used by original-query and AI tools. The
    caller must scope previous to the authenticated user's conversation.
    """
    plan = _query_plan(question, previous)
    verified_docs = list(db.scalars(select(PolicyDoc).where(PolicyDoc.verified.is_(True)).order_by(PolicyDoc.id)))
    docs = verified_docs
    if plan["scope"] == "school":
        docs = [doc for doc in docs if doc.is_school_policy]
    elif plan["scope"] == "national":
        docs = [doc for doc in docs if not doc.is_school_policy]
    hits = []
    excluded = []
    eligible_ids = set()
    reference_time = now()
    for doc in docs:
        allowed, reason = _document_allowed(doc, plan, reference_time)
        if not allowed:
            excluded.append({"source_key": doc.source_key, "title": doc.title, "reason": reason})
            continue
        eligible_ids.add(doc.id)
        for index, section in enumerate(doc.sections):
            score = _score_section(section, question, plan)
            if not score and not plan["article_numbers"] and question in doc.title:
                score = 1
            if not score and not plan["article_numbers"] and not plan["topics"] and (
                    plan["source_ids"] or plan["source_names"]):
                score = 1
            if score:
                hits.append({"score": score, "index": index, "text": section["text"],
                             "location": section["location"], "source": policy_document_data(doc),
                             "quote_source_url": section.get("source_url", doc.source_url)})
    hits.sort(key=lambda hit: (-hit["score"], hit["source"]["id"], hit["index"]))
    citations = [{key: value for key, value in hit.items() if key not in {"score", "index"}}
                 for hit in hits[:max(1, min(limit, 10))]]
    plan["mode"] = "article_exact" if plan["article_numbers"] else "keyword"
    if rag.options().enabled and not plan["article_numbers"]:
        def score_chunk(chunk):
            score = _score_section({"location": chunk.location, "text": chunk.text}, question, plan)
            if plan["topics"] and not score:
                return -1
            if _generic_followup(question) and not plan["used_context"]:
                return -1
            if not score and not plan["topics"] and (plan["source_ids"] or plan["source_names"]):
                score = 1
            if not score and question in chunk.title:
                score = 1
            return score

        semantic_query = (f"{plan['previous_question']}\n{question}"
                          if plan["used_context"] and _generic_followup(question) else question)
        semantic_query = re.sub(r"^(?:(?:根据|关于)\s*)?(?:国家政策|校园勤工助学|勤工助学)\s*[：:]\s*",
                                "", semantic_query)
        semantic_query = f"校园勤工助学政策：{semantic_query}"
        ranked, retrieval = rag.hybrid_search(verified_docs, eligible_ids, semantic_query, score_chunk,
                                              max(1, min(limit, 10)))
        plan.update(retrieval)
        if retrieval["mode"] != "keyword_fallback":
            by_id = {doc.id: doc for doc in verified_docs}
            citations = []
            for hit in ranked:
                chunk = hit["chunk"]
                doc = by_id[chunk.document_id]
                section = doc.sections[chunk.section_index]
                citations.append({"text": chunk.text, "location": chunk.location,
                                  "source": policy_document_data(doc),
                                  "quote_source_url": section.get("source_url", doc.source_url),
                                  "chunk": {"id": chunk.id, "start": chunk.start, "end": chunk.end},
                                  "retrieval_method": hit["method"], "semantic_score": hit["semantic_score"]})
    plan["excluded_sources"] = excluded
    boundaries = []
    if any(hit["source"]["usage_scope"] == "archive_only" or hit["source"]["past_deadline"]
           for hit in citations):
        boundaries.append("以下引用包含历史存档；已截止招聘不能作为当前在招岗位，旧版资料不能直接作为现行细则。")
    if any(hit["source"]["usage_scope"] == "labor_reference" for hit in citations):
        boundaries.append("劳动最低工资资料适用于其规定的劳动关系；24元/小时不是已确认的本校勤工助学时薪。")
    if citations:
        answer = f"找到 {len(citations)} 段已核验原文，请结合条款上下文阅读。"
        if boundaries:
            answer += " " + " ".join(boundaries)
    elif plan["scope"] == "school" and not docs:
        answer = "当前知识库没有可靠依据：尚未收录已核验的本校细则。请向学校资助中心确认。"
    elif plan["scope"] == "school" and "pay" in plan["topics"]:
        answer = ("当前知识库没有可靠依据：已核验公开资料尚未确认本校具体岗位时薪。"
                  "不能将国家临时岗位原则12元或劳动标准24元视为本校实际时薪，请向学校资助中心确认。")
    elif plan["scope"] == "school" and set(plan["topics"]) & {"recognition", "hardship_levels", "proof"}:
        answer = ("当前知识库没有可靠依据：本校完整现行困难认定细则尚未取得。"
                  "2019年旧版不能直接作为当前等级或盖章要求，请向学校资助中心确认。")
    elif plan["scope"] == "school" and "application" in plan["topics"] and not plan["archive_requested"]:
        answer = ("当前知识库没有可靠依据：本校统一申请细则及当前仍可报名的招聘公告尚未确认。"
                  "已归档的历史招聘批次已经截止，请查看学校最新公告或向学校资助中心咨询。"
                  "系统中的演示岗位不能视为学校当前真实招聘。")
    else:
        answer = "当前知识库没有可靠依据。可换个表述或指定条款编号；学校细则尚未收录时，请向学校资助中心确认。"
    notice = NOTICE
    if plan["mode"] == "hybrid":
        notice = "政策原文查询；使用语义与关键词查找依据，未调用回答模型。请核对原文版本、适用范围与学校细则。"
    elif plan["mode"] == "keyword_fallback":
        notice += " 本地语义检索暂不可用，本轮使用关键词检索。"
    return {"answer": answer, "citations": citations, "notice": notice, "retrieval": plan,
            "boundaries": boundaries}


def query_policy(db, user, data):
    previous = db.scalar(_policy_logs(user.id, data.conversation_id).order_by(ChatLog.id.desc()).limit(1))
    evidence = retrieve_policy(db, data.question, previous)
    response = {"kind": "policy", "mode": "original_query", "question": data.question,
                "conversation_id": data.conversation_id, **evidence}
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

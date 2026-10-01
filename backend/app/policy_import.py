"""Load the reviewed offline official-source bundle without inventing policy.

Archive hashes are checked before database changes. The curated header is review
metadata, not quoted official text. Imports preserve document IDs and never
modify jobs, wages, qualification records, or previous conversations.
"""

import hashlib
import json
import re
from datetime import date, datetime, time
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .models import AuditLog, PolicyDoc
from .services import now

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "docs/research/verified_sources.json"
USAGES = {"school_fact", "general_policy", "official_explanation", "labor_reference", "archive_only"}
ARTICLE = re.compile(r"^第[零〇一二两三四五六七八九十百千\d]+条(?=\s|$)", re.MULTILINE)
CHAPTER = re.compile(r"\n第[零〇一二两三四五六七八九十百千\d]+章[^\n]*\s*$")
FAQ_HEADING = re.compile(r"^(?P<heading>[一二三四五六七八九十]+、[^\n]{3,}[？?])\s*$", re.MULTILINE)


def _url_identity(value):
    parsed = urlsplit(value)
    return (parsed.hostname or "").lower() + parsed.path.rstrip("/")


def _official_url(value):
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    official = any(host == domain or host.endswith("." + domain)
                   for domain in ("wids.edu.cn", "moe.gov.cn", "hubei.gov.cn"))
    if (parsed.scheme not in {"http", "https"} or not official or parsed.username or parsed.password
            or parsed.port not in {None, 80, 443}):
        raise ValueError("Source bundle contains a non-official URL")
    return _url_identity(value)


def _checked_file(root, relative, expected):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Source archive file is missing or outside the research bundle")
    if not re.fullmatch(r"[0-9a-f]{64}", expected or ""):
        raise ValueError("Source archive must have a SHA-256 digest")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError(f"Archive digest mismatch: {relative}")
    return content


def _date(value):
    return date.fromisoformat(value) if value else None


def _deadline(value):
    if not value:
        return None
    # A deadline specified only as a day remains that day, not its beginning.
    result = datetime.fromisoformat(value) if "T" in value else datetime.combine(_date(value), time.max)
    if result.tzinfo is not None:
        result = result.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    return result


def _sections(curated):
    if "【原文】" not in curated:
        raise ValueError("Curated document is missing its official-text separator")
    body = curated.split("【原文】", 1)[1].strip()
    matches = list(ARTICLE.finditer(body))
    sections = []
    if matches:
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
            text = CHAPTER.sub("", body[match.start():end]).strip()
            sections.append({"location": match.group(), "text": text})
    else:
        # The reviewer already limited the body to official content. Retain
        # headings in nearby chunks so numbered subparagraphs keep context.
        questions = list(FAQ_HEADING.finditer(body))
        if len(questions) >= 2:
            first = questions[0].start()
            introduction = body[:first].strip()
            if introduction:
                sections.append({"location": "答记者问：导语", "text": introduction})
            for index, match in enumerate(questions):
                end = questions[index + 1].start() if index + 1 < len(questions) else len(body)
                sections.append({"location": f"答记者问：{match.group('heading')}",
                                 "text": body[match.start():end].strip()})
        else:
            paragraphs = [part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()]
            for index, paragraph in enumerate(paragraphs, 1):
                if len(paragraph) > 10000:
                    raise ValueError("Official paragraph exceeds the supported quote size")
                sections.append({"location": f"正文第{index}段", "text": paragraph})
    if not sections or len(sections) > 200 or any(len(section["text"]) > 10000 for section in sections):
        raise ValueError("Official document has unsupported section sizes")
    return sections


def build_verified_documents(manifest_path=DEFAULT_MANIFEST):
    """Return validated documents for preview/import; does not open a database."""
    manifest_path = Path(manifest_path).resolve()
    root = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if _date(manifest["checked_on"]) > now().date():
        raise ValueError("Source review date cannot be in the future")
    documents, seen = [], set()
    for source in manifest["sources"]:
        identity = source["id"]
        if not re.fullmatch(r"S\d{2,3}", identity) or identity in seen:
            raise ValueError("Source identifiers must be unique reviewed S-numbers")
        seen.add(identity)
        usage = source["rag_usage"]
        if usage not in USAGES or source["verification_status"] != "official_body_verified":
            raise ValueError("Source bundle contains an unverified or unsupported source")
        _official_url(source["source_url"])
        _official_url(source["verified_final_url"])
        current = source["default_current_answer_allowed"]
        if not isinstance(current, bool) or (usage == "archive_only" and current):
            raise ValueError("Archived sources cannot be current-answer evidence")
        if source["category"].startswith("historical_") and (current or usage != "archive_only"):
            raise ValueError("Historical material requires archive-only classification")
        if "labor" in source["category"] and usage != "labor_reference":
            raise ValueError("Labor standards must remain labor-reference evidence")
        _checked_file(root, source["raw_relative_path"], source["raw_sha256"])
        _checked_file(root, source["archived_page_text_relative_path"], source["archived_page_text_sha256"])
        curated = _checked_file(root, source["curated_text_relative_path"], source["curated_text_sha256"])
        sections = _sections(curated.decode("utf-8"))
        attachments = []
        for attachment in source.get("attachments", []):
            _official_url(attachment["source_url"])
            if attachment["verification_status"] != "official_attachment_visually_verified":
                raise ValueError("Only independently verified attachments can supply evidence")
            _checked_file(root, attachment["raw_relative_path"], attachment["raw_sha256"])
            transcription = _checked_file(root, attachment["transcription_relative_path"],
                                          attachment["transcription_sha256"])
            # The transcription's header describes its scope. Quote the
            # explicitly reviewed verbatim transcription, never that header.
            quotation = attachment.get("first_tier_transcription")
            if not quotation or quotation not in transcription.decode("utf-8"):
                raise ValueError("Attachment transcription does not contain the reviewed quotation")
            sections.append({"location": "官方附件第一档（核验转录）", "text": quotation,
                             "source_url": attachment["source_url"]})
            attachments.append(attachment)
        verified_on = _date(source["verified_on"])
        if verified_on > now().date():
            raise ValueError("Source verification cannot be in the future")
        metadata = {key: value for key, value in source.items() if key != "raw_path"}
        metadata.update({"bundle_checked_on": manifest["checked_on"], "attachments": attachments})
        verification = (f"资料{identity}：{source['verification_status']}；核验日期{source['verified_on']}。"
                        f"{source['validity_evidence']} 使用限制：" + "；".join(source["limitations"]))
        documents.append({"source_key": identity, "title": source["title"], "publisher": source["publisher"],
                          "source_url": source["source_url"],
                          "version": source["version"] or source["document_number"] or
                          source["publication_date"] or "核验时公开页面（未标注版本）",
                          "verified": True, "verified_at": datetime.combine(verified_on, time.min),
                          "verification_note": verification, "sections": sections,
                          "is_school_policy": source["school"] == manifest["school"],
                          "usage_scope": usage, "current_answer_allowed": current,
                          "publication_date": _date(source["publication_date"]),
                          "effective_from": _date(source["effective_from"]),
                          "expires_at": _deadline(source["expires_at"]),
                          "applicability": source["jurisdiction_and_applicability"], "source_metadata": metadata})
    if not documents:
        raise ValueError("Source bundle is empty")
    return documents


def import_verified_documents(db, documents, actor_id=None):
    """Upsert reviewed originals atomically; caller owns commit/rollback."""
    existing = list(db.scalars(select(PolicyDoc).with_for_update()))
    results = []
    for values in documents:
        key, url = values["source_key"], _official_url(values["source_url"])
        by_key = [doc for doc in existing if doc.source_key == key]
        by_url = [doc for doc in existing if _url_identity(doc.source_url) == url]
        if len(by_key) > 1 or len(by_url) > 1:
            raise ValueError("Duplicate existing source identity requires review before import")
        doc = next(iter(by_key or by_url), None)
        if doc and _url_identity(doc.source_url) != url:
            raise ValueError("An existing source identifier points to another official document")
        if doc is None:
            doc = PolicyDoc(**values, imported_at=now())
            db.add(doc)
            existing.append(doc)
            status = "created"
        else:
            changed = any(getattr(doc, name) != value for name, value in values.items())
            status = "updated" if changed else "unchanged"
            if changed:
                for name, value in values.items():
                    setattr(doc, name, value)
        db.flush()
        results.append({"source_key": key, "id": doc.id, "title": doc.title, "status": status,
                        "usage_scope": doc.usage_scope, "current_answer_allowed": doc.current_answer_allowed,
                        "section_count": len(doc.sections)})
    summary = {"documents": len(results), "created": sum(item["status"] == "created" for item in results),
               "updated": sum(item["status"] == "updated" for item in results),
               "unchanged": sum(item["status"] == "unchanged" for item in results), "items": results}
    if summary["created"] or summary["updated"]:
        db.add(AuditLog(actor_id=actor_id, action="policy.import_reviewed_bundle", resource_type="policy_doc",
                        resource_id=None, after=summary, created_at=now()))
        db.flush()
    return summary

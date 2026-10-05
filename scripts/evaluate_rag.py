"""Compare keyword and actual local-model retrieval against a fixed smoke set."""

import argparse
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import rag  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.policy import retrieve_policy  # noqa: E402


def matches(hit, expected):
    return any(hit["source"] == item["source"] and hit["location"].startswith(item["location"])
               for item in expected)


def evaluate(db, cases):
    rows = []
    for case in cases:
        result = retrieve_policy(db, case["question"])
        hits = [{"source": hit["source"].get("source_key"), "location": hit["location"],
                 "method": hit.get("retrieval_method", "keyword"), "score": hit.get("semantic_score")}
                for hit in result["citations"]]
        correct = any(matches(hit, case["expected"]) for hit in hits) if case["expected"] else not hits
        top_one = bool(hits and matches(hits[0], case["expected"])) if case["expected"] else not hits
        rows.append({**case, "correct": correct, "top_one_correct": top_one, "hits": hits,
                     "retrieval": result["retrieval"]})
    positive = [row for row in rows if row["expected"]]
    negative = [row for row in rows if not row["expected"]]
    return {"cases": len(rows), "correct": sum(row["correct"] for row in rows),
            "recall_at_5": sum(row["correct"] for row in positive) / len(positive),
            "top_one_accuracy": sum(row["top_one_correct"] for row in positive) / len(positive),
            "refusal_accuracy": sum(row["correct"] for row in negative) / len(negative), "rows": rows}


def _database_unavailable():
    print("无法连接项目专用 MySQL（13308），未执行 RAG 评测，也没有重置任何数据。", file=sys.stderr)
    print("请在项目根目录执行：", file=sys.stderr)
    print(r".\backend\.venv\Scripts\python.exe .\scripts\mysql_runtime.py start", file=sys.stderr)
    print("确认启动后在 backend 目录重新运行本脚本；评测完成后执行同一命令的 stop。", file=sys.stderr)
    return 2


def _evaluate_with_database(cases):
    model_cache = rag.options().model_cache
    original = {key: os.environ.get(key) for key in ("RAG_ENABLED", "QINGHE_RAG_DIR", "RAG_MODEL_CACHE")}
    try:
        with TemporaryDirectory(prefix="qinghe-rag-eval-") as temporary:
            os.environ["QINGHE_RAG_DIR"] = temporary
            os.environ["RAG_MODEL_CACHE"] = str(model_cache)
            with SessionLocal() as db:
                # Fail before expensive embedding work and keep connection details private.
                db.execute(text("SELECT 1"))
                os.environ["RAG_ENABLED"] = "false"
                baseline = evaluate(db, cases)
                rag.close_indexes()
                os.environ["RAG_ENABLED"] = "true"
                hybrid = evaluate(db, cases)
                status = rag.status()
            rag.close_indexes()
    finally:
        rag.close_indexes()
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return baseline, hybrid, status


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    dataset = json.loads((ROOT / "data/rag/evaluation.json").read_text(encoding="utf-8"))
    try:
        baseline, hybrid, status = _evaluate_with_database(dataset["cases"])
    except SQLAlchemyError:
        return _database_unavailable()
    result = {"dataset": dataset["description"], "model": status["model"], "dimension": status["dimension"],
              "min_score": status["min_score"], "keyword": baseline, "hybrid": hybrid}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"model": result["model"], "keyword": {key: value for key, value in baseline.items()
                                                            if key != "rows"},
                      "hybrid": {key: value for key, value in hybrid.items() if key != "rows"}}, indent=2))
    failures = [row["id"] for row in hybrid["rows"] if not row["correct"]]
    if any(row["retrieval"].get("mode") == "keyword_fallback" for row in hybrid["rows"]):
        print("实际本地模型评测失败：存在关键词降级查询。", file=sys.stderr)
        return 1
    print(json.dumps({"failed_cases": failures}))
    if failures:
        print("检索验收失败：" + ", ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

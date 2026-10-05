"""Prepare the local embedding model and policy index without editing business data."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app import rag  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import PolicyDoc  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true", help="Allow downloading the embedding model")
    args = parser.parse_args()
    try:
        with SessionLocal() as db:
            documents = list(db.scalars(select(PolicyDoc).where(PolicyDoc.verified.is_(True))))
            result = rag.rebuild(documents, download=args.download)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except rag.RagUnavailable as error:
        print(json.dumps({"ready": False, "reason": error.reason}))
        raise SystemExit(1) from None
    finally:
        rag.close_indexes()


if __name__ == "__main__":
    main()

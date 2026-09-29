"""Ingestion job: python -m app.ingestion.run [--recreate] [--data-dir PATH]"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import Counter
from pathlib import Path

from app.config import get_settings
from app.ingestion.loaders import load_all
from app.retrieval.embeddings import get_embedder
from app.retrieval.store import ensure_collection, get_qdrant, upsert_chunks


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    log = logging.getLogger("ingest")
    settings = get_settings()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recreate", action="store_true", help="Drop and rebuild the collection")
    parser.add_argument("--data-dir", type=Path, default=settings.data_dir)
    args = parser.parse_args()

    started = time.perf_counter()
    chunks = load_all(args.data_dir)
    if not chunks:
        log.error("No chunks produced from %s", args.data_dir)
        return 1

    embedder = get_embedder()
    client = get_qdrant()
    ensure_collection(client, settings.collection_name, embedder.dense_dim, recreate=args.recreate)
    n = upsert_chunks(client, settings.collection_name, chunks, embedder)

    by_dept = Counter(c.department.value for c in chunks)
    log.info(
        "Indexed %d chunks into '%s' in %.1fs: %s",
        n,
        settings.collection_name,
        time.perf_counter() - started,
        dict(sorted(by_dept.items())),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

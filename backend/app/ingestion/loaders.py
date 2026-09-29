"""Turn raw company files into RBAC-tagged chunks.

* Markdown -> Docling DocumentConverter + HybridChunker (structure-aware, token-bounded,
  keeps the heading path so each chunk is self-describing).
* HR CSV   -> one natural-language record per employee (so "who manages X" style questions
  can be answered by retrieval), tagged with the HR department.
"""

from __future__ import annotations

import csv
import logging
from collections.abc import Iterator
from pathlib import Path

from app.ingestion.models import Chunk
from app.rbac.policy import Department

log = logging.getLogger(__name__)

TOKENIZER_MODEL = "BAAI/bge-small-en-v1.5"
MAX_TOKENS = 384


def department_for_path(path: Path, data_dir: Path) -> Department:
    """Access is derived from the top-level folder: data/raw/<department>/..."""
    top = path.relative_to(data_dir).parts[0]
    try:
        return Department(top)
    except ValueError as exc:
        raise ValueError(
            f"{path}: top-level folder '{top}' is not a known department "
            f"({', '.join(d.value for d in Department)}). Refusing to ingest untagged data."
        ) from exc


class MarkdownLoader:
    def __init__(self) -> None:
        from docling.chunking import HybridChunker
        from docling.datamodel.base_models import InputFormat
        from docling.document_converter import DocumentConverter
        from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer

        self._converter = DocumentConverter(allowed_formats=[InputFormat.MD])
        self._chunker = HybridChunker(
            tokenizer=HuggingFaceTokenizer.from_pretrained(TOKENIZER_MODEL, max_tokens=MAX_TOKENS),
            merge_peers=True,
        )

    def load(self, path: Path, data_dir: Path) -> list[Chunk]:
        department = department_for_path(path, data_dir)
        doc = self._converter.convert(path).document
        title = (
            next((t.text for t in doc.texts if getattr(t, "label", None) == "title"), None)
            or path.stem.replace("_", " ").title()
        )
        source = str(path.relative_to(data_dir))

        chunks: list[Chunk] = []
        for i, ch in enumerate(self._chunker.chunk(dl_doc=doc)):
            headings = list(getattr(ch.meta, "headings", None) or [])
            text = self._chunker.contextualize(chunk=ch)
            if len(text.strip()) < 40:
                continue
            chunks.append(
                Chunk(
                    text=text,
                    department=department,
                    source=source,
                    title=title,
                    section=" > ".join(headings),
                    doc_type="handbook" if department == Department.GENERAL else "report",
                    chunk_index=i,
                )
            )
        log.info("%s -> %d chunks (%s)", source, len(chunks), department.value)
        return chunks


def _hr_record_text(row: dict[str, str]) -> str:
    return (
        f"Employee record {row['employee_id']}: {row['full_name']}, {row['role']} in the "
        f"{row['department']} department, based in {row['location']}. "
        f"Email: {row['email']}. Date of birth: {row['date_of_birth']}. "
        f"Joined on {row['date_of_joining']}. Reports to manager {row['manager_id']}. "
        f"Annual salary: {row['salary']}. Leave balance: {row['leave_balance']} days, "
        f"leaves taken: {row['leaves_taken']}. Attendance: {row['attendance_pct']}%. "
        f"Performance rating: {row['performance_rating']} out of 5, "
        f"last reviewed on {row['last_review_date']}."
    )


def load_hr_csv(path: Path, data_dir: Path) -> list[Chunk]:
    department = department_for_path(path, data_dir)
    source = str(path.relative_to(data_dir))
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    chunks = [
        Chunk(
            text=_hr_record_text(row),
            department=department,
            source=source,
            title="HR Employee Records",
            section=row["employee_id"],
            doc_type="employee_record",
            chunk_index=i,
            extra={
                "employee_id": row["employee_id"],
                "employee_department": row["department"],
            },
        )
        for i, row in enumerate(rows)
    ]
    log.info("%s -> %d employee records (%s)", source, len(chunks), department.value)
    return chunks


def iter_source_files(data_dir: Path) -> Iterator[Path]:
    for path in sorted(data_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in {".md", ".csv"}:
            yield path


def load_all(data_dir: Path) -> list[Chunk]:
    md_loader: MarkdownLoader | None = None
    chunks: list[Chunk] = []
    for path in iter_source_files(data_dir):
        if path.suffix.lower() == ".csv":
            chunks.extend(load_hr_csv(path, data_dir))
        else:
            md_loader = md_loader or MarkdownLoader()
            chunks.extend(md_loader.load(path, data_dir))
    return chunks

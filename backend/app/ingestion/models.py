import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from app.rbac.policy import Department, allowed_roles_for

_NAMESPACE = uuid.UUID("5b7c1b7e-4a1e-4b8e-9c55-0f8f1c3a9d21")


@dataclass
class Chunk:
    text: str
    department: Department
    source: str
    title: str
    section: str = ""
    doc_type: str = "document"
    chunk_index: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        # Deterministic so re-ingestion upserts instead of duplicating.
        return str(uuid.uuid5(_NAMESPACE, f"{self.source}#{self.chunk_index}"))

    def payload(self) -> dict[str, Any]:
        data = asdict(self)
        data["department"] = self.department.value
        data["allowed_roles"] = allowed_roles_for(self.department)
        extra = data.pop("extra")
        data.update(extra)
        return data

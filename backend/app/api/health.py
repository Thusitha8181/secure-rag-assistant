from fastapi import APIRouter, Response, status

from app.config import get_settings
from app.retrieval.store import get_qdrant

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("")
def liveness() -> dict[str, str]:
    s = get_settings()
    return {"status": "ok", "version": s.app_version, "environment": s.environment}


@router.get("/ready")
def readiness(response: Response) -> dict[str, object]:
    s = get_settings()
    checks: dict[str, object] = {}
    try:
        info = get_qdrant().get_collection(s.collection_name)
        checks["qdrant"] = {"ok": True, "points": info.points_count}
    except Exception as exc:
        checks["qdrant"] = {"ok": False, "error": type(exc).__name__}
    checks["llm_configured"] = bool(s.groq_api_key)
    ready = all(v.get("ok", True) if isinstance(v, dict) else bool(v) for v in checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"ready": ready, "checks": checks}

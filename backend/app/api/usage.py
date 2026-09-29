from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query

from app.auth.deps import get_current_user, require_roles
from app.auth.security import User
from app.config import get_settings
from app.observability.usage import UsageStore, UsageSummary, get_usage_store
from app.rbac.policy import USAGE_ADMIN_ROLES

router = APIRouter(prefix="/api/usage", tags=["usage"])


@router.get("/me")
def my_usage(
    user: User = Depends(get_current_user), store: UsageStore = Depends(get_usage_store)
) -> dict:
    today = store.user_day(user.username, datetime.now(UTC).date())
    quota = get_settings().daily_token_quota
    return {
        "username": user.username,
        "today": {**today.model_dump(), "total_tokens": today.total_tokens},
        "daily_token_quota": quota,
        "remaining_tokens": max(quota - today.total_tokens, 0) if quota > 0 else None,
    }


@router.get("/summary", response_model=UsageSummary)
def usage_summary(
    days: int = Query(7, ge=1, le=90),
    _: User = Depends(require_roles(USAGE_ADMIN_ROLES)),
    store: UsageStore = Depends(get_usage_store),
) -> UsageSummary:
    return store.summary(days)

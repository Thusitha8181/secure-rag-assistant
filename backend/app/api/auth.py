from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth.deps import get_current_user
from app.auth.security import User, authenticate, create_access_token, list_users
from app.config import get_settings
from app.rbac.policy import ROLE_DESCRIPTIONS, departments_for

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(User):
    departments: list[str]
    access_summary: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


def _user_out(user: User) -> UserOut:
    return UserOut(
        **user.model_dump(),
        departments=departments_for(user.role),
        access_summary=ROLE_DESCRIPTIONS[user.role],
    )


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest) -> LoginResponse:
    user = authenticate(body.username.strip().lower(), body.password)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    token, expires_in = create_access_token(user)
    return LoginResponse(access_token=token, expires_in=expires_in, user=_user_out(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> UserOut:
    return _user_out(user)


@router.get("/demo-users", response_model=list[UserOut])
def demo_users() -> list[UserOut]:
    """Public list of seeded accounts so reviewers can try every role (DEMO_MODE only)."""
    if not get_settings().demo_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return [_user_out(u) for u in list_users()]

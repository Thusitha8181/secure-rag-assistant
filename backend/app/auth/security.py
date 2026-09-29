from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import bcrypt
import jwt
import yaml
from pydantic import BaseModel

from app.config import get_settings
from app.rbac.policy import Role

# Used to equalise timing when the username does not exist.
_DUMMY_HASH = bcrypt.hashpw(b"timing-equaliser", bcrypt.gensalt(rounds=12))


class User(BaseModel):
    username: str
    name: str
    title: str
    role: Role


class _StoredUser(User):
    password_hash: str


@lru_cache
def _load_users(path: Path) -> dict[str, _StoredUser]:
    data = yaml.safe_load(path.read_text()) or {}
    return {u["username"]: _StoredUser(**u) for u in data.get("users", [])}


def list_users() -> list[User]:
    users = _load_users(get_settings().users_file).values()
    return [User(**u.model_dump(exclude={"password_hash"})) for u in users]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def authenticate(username: str, password: str) -> User | None:
    stored = _load_users(get_settings().users_file).get(username)
    candidate = stored.password_hash.encode() if stored else _DUMMY_HASH
    ok = bcrypt.checkpw(password.encode(), candidate)
    if not (stored and ok):
        return None
    return User(**stored.model_dump(exclude={"password_hash"}))


def create_access_token(user: User) -> tuple[str, int]:
    s = get_settings()
    expires_in = s.jwt_expiry_minutes * 60
    now = datetime.now(UTC)
    claims = {
        "sub": user.username,
        "name": user.name,
        "title": user.title,
        "role": user.role.value,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    return jwt.encode(claims, s.jwt_secret, algorithm=s.jwt_algorithm), expires_in


def decode_access_token(token: str) -> User:
    s = get_settings()
    claims = jwt.decode(
        token,
        s.jwt_secret,
        algorithms=[s.jwt_algorithm],
        options={"require": ["exp", "sub", "role"]},
    )
    return User(
        username=claims["sub"], name=claims["name"], title=claims["title"], role=claims["role"]
    )


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "hash":
        print(hash_password(sys.argv[2]))
    else:
        print("usage: python -m app.auth.security hash '<password>'")

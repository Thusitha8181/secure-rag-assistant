import jwt
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app

DEMO_PASSWORD = "demo1234"


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _login(client: TestClient, username: str, password: str = DEMO_PASSWORD):
    return client.post("/api/auth/login", json={"username": username, "password": password})


@pytest.mark.parametrize(
    "username,role",
    [
        ("fiona.finance", "finance"),
        ("mark.marketing", "marketing"),
        ("hana.hr", "hr"),
        ("eli.engineering", "engineering"),
        ("cara.ceo", "c_level"),
        ("evan.employee", "employee"),
    ],
)
def test_login_issues_token_with_role(client: TestClient, username: str, role: str) -> None:
    res = _login(client, username)
    assert res.status_code == 200
    body = res.json()
    assert body["user"]["role"] == role
    s = get_settings()
    claims = jwt.decode(body["access_token"], s.jwt_secret, algorithms=[s.jwt_algorithm])
    assert claims["role"] == role and claims["sub"] == username


def test_wrong_password_rejected(client: TestClient) -> None:
    assert _login(client, "cara.ceo", "nope").status_code == 401
    assert _login(client, "nobody", "nope").status_code == 401


def test_me_requires_valid_token(client: TestClient) -> None:
    assert client.get("/api/auth/me").status_code == 401
    assert (
        client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401
    )
    token = _login(client, "hana.hr").json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert set(me.json()["departments"]) == {"hr", "general"}


def test_forged_role_claim_rejected(client: TestClient) -> None:
    """A token signed with another key (e.g. self-promoted to c_level) must not validate."""
    forged = jwt.encode(
        {"sub": "evan.employee", "name": "x", "title": "x", "role": "c_level", "exp": 9999999999},
        "attacker-secret-that-is-long-enough-for-hs256",
        algorithm="HS256",
    )
    res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert res.status_code == 401


def test_demo_users_listed_without_hashes(client: TestClient) -> None:
    users = client.get("/api/auth/demo-users").json()
    assert len(users) == 6
    assert all("password_hash" not in u for u in users)

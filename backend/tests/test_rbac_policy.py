import pytest

from app.rbac.policy import (
    HR_ANALYTICS_ROLES,
    PII_PRIVILEGED_ROLES,
    Department,
    Role,
    allowed_roles_for,
    can_access,
    departments_for,
)

# The access matrix from the requirements, written out explicitly so any policy change
# has to be made consciously in two places.
EXPECTED = {
    Role.FINANCE: {"finance", "general"},
    Role.MARKETING: {"marketing", "general"},
    Role.HR: {"hr", "general"},
    Role.ENGINEERING: {"engineering", "general"},
    Role.C_LEVEL: {"finance", "marketing", "hr", "engineering", "general"},
    Role.EMPLOYEE: {"general"},
}


@pytest.mark.parametrize("role", list(Role))
def test_departments_for_role_matches_requirements(role: Role) -> None:
    assert set(departments_for(role)) == EXPECTED[role]


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("dept", list(Department))
def test_can_access_is_consistent(role: Role, dept: Department) -> None:
    assert can_access(role, dept) == (dept.value in EXPECTED[role])
    assert (role.value in allowed_roles_for(dept)) == can_access(role, dept)


def test_only_c_level_has_full_access() -> None:
    full = {r for r in Role if set(departments_for(r)) == {d.value for d in Department}}
    assert full == {Role.C_LEVEL}


def test_sensitive_capabilities_are_restricted() -> None:
    assert {Role.HR, Role.C_LEVEL} == PII_PRIVILEGED_ROLES
    assert {Role.HR, Role.C_LEVEL} == HR_ANALYTICS_ROLES


def test_unknown_department_rejected() -> None:
    with pytest.raises(ValueError):
        allowed_roles_for("legal")

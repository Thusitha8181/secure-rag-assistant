"""Single source of truth for who can see what.

Access is decided by *department* of the source document. Every chunk stores the
roles allowed to read it (`allowed_roles`) at ingestion time, and every vector
search filters on the caller's role, so the LLM never sees unauthorized context.
"""

from enum import StrEnum


class Role(StrEnum):
    FINANCE = "finance"
    MARKETING = "marketing"
    HR = "hr"
    ENGINEERING = "engineering"
    C_LEVEL = "c_level"
    EMPLOYEE = "employee"


class Department(StrEnum):
    FINANCE = "finance"
    MARKETING = "marketing"
    HR = "hr"
    ENGINEERING = "engineering"
    GENERAL = "general"


ALL_ROLES: frozenset[Role] = frozenset(Role)

DEPARTMENT_ACCESS: dict[Department, frozenset[Role]] = {
    Department.FINANCE: frozenset({Role.FINANCE, Role.C_LEVEL}),
    Department.MARKETING: frozenset({Role.MARKETING, Role.C_LEVEL}),
    Department.HR: frozenset({Role.HR, Role.C_LEVEL}),
    Department.ENGINEERING: frozenset({Role.ENGINEERING, Role.C_LEVEL}),
    Department.GENERAL: ALL_ROLES,
}

# Roles allowed to see raw employee PII (names with salary, DOB, email, ...).
PII_PRIVILEGED_ROLES: frozenset[Role] = frozenset({Role.HR, Role.C_LEVEL})

# Roles allowed to run aggregate SQL over the HR table.
HR_ANALYTICS_ROLES: frozenset[Role] = DEPARTMENT_ACCESS[Department.HR]

# Roles allowed to see the organisation-wide usage / cost dashboard.
USAGE_ADMIN_ROLES: frozenset[Role] = frozenset({Role.C_LEVEL})

ROLE_DESCRIPTIONS: dict[Role, str] = {
    Role.FINANCE: "Financial reports, marketing expenses, equipment costs, reimbursements",
    Role.MARKETING: "Campaign performance, customer feedback, sales metrics",
    Role.HR: "Employee data, attendance, payroll, performance reviews",
    Role.ENGINEERING: "Technical architecture, development processes, operational guidelines",
    Role.C_LEVEL: "Full access to all company data",
    Role.EMPLOYEE: "General company information: policies, events, FAQs",
}


def allowed_roles_for(department: Department | str) -> list[str]:
    return sorted(r.value for r in DEPARTMENT_ACCESS[Department(department)])


def departments_for(role: Role | str) -> list[str]:
    role = Role(role)
    return sorted(d.value for d, roles in DEPARTMENT_ACCESS.items() if role in roles)


def can_access(role: Role | str, department: Department | str) -> bool:
    return Role(role) in DEPARTMENT_ACCESS[Department(department)]

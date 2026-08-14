"""What each role may do.

One table, read by one dependency. A route without a permission requirement is
a bug, so :func:`audit_route_coverage` exists to let a test assert that every
mutating endpoint declares one.

The split the business asked for:

* the accountant does the day-to-day work and **cannot change an issued
  document** — he can propose an amendment, which waits for the owner;
* the owner can do everything, including voiding and deleting;
* the viewer is for handing the CA read-only access at year end.
"""

from __future__ import annotations

from enum import StrEnum

from ..models import Role


class Permission(StrEnum):
    # --- day-to-day -------------------------------------------------------
    DOCUMENT_CREATE = "document.create"
    DOCUMENT_ISSUE = "document.issue"
    RECEIPT_RECORD = "receipt.record"
    ORDER_TRANSITION = "order.transition"
    EXTRACTION_RUN = "extraction.run"
    EXTRACTION_APPROVE = "extraction.approve"

    # --- changing something already issued --------------------------------
    DOCUMENT_AMEND = "document.amend"              # propose a new version
    DOCUMENT_AMEND_APPROVE = "document.amend.approve"  # make it take effect
    DOCUMENT_VOID = "document.void"
    DOCUMENT_DELETE = "document.delete"            # physical erasure

    # --- configuration ----------------------------------------------------
    MASTER_EDIT = "master.edit"          # customers, suppliers, items
    TAXRATE_EDIT = "taxrate.edit"        # GST rates — changes what tax is charged
    SETTINGS_EDIT = "settings.edit"      # company profile, Tally ledgers, series
    USER_MANAGE = "user.manage"

    # --- reading ----------------------------------------------------------
    REPORT_READ = "report.read"
    AUDIT_READ = "audit.read"
    TALLY_EXPORT = "tally.export"


_VIEWER: frozenset[Permission] = frozenset(
    {Permission.REPORT_READ, Permission.AUDIT_READ}
)

_ACCOUNTANT: frozenset[Permission] = _VIEWER | frozenset(
    {
        Permission.DOCUMENT_CREATE,
        Permission.DOCUMENT_ISSUE,
        Permission.RECEIPT_RECORD,
        Permission.ORDER_TRANSITION,
        Permission.EXTRACTION_RUN,
        Permission.EXTRACTION_APPROVE,
        # He may *propose* a change to an issued document. He may not approve
        # it, void it, or delete it — that is the whole point.
        Permission.DOCUMENT_AMEND,
        Permission.MASTER_EDIT,
        Permission.TALLY_EXPORT,
    }
)

# The owner is defined as "everything" rather than a list, so a permission
# added later cannot accidentally leave the owner locked out of it.
_OWNER: frozenset[Permission] = frozenset(Permission)

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.OWNER: _OWNER,
    Role.ACCOUNTANT: _ACCOUNTANT,
    Role.VIEWER: _VIEWER,
}


class PermissionDenied(PermissionError):
    def __init__(self, permission: Permission, role: Role) -> None:
        self.permission = permission
        self.role = role
        super().__init__(
            f"your role ({role}) cannot {permission}. "
            "Ask the owner to do this, or to change your role."
        )


def permissions_for(role: Role | str) -> frozenset[Permission]:
    return ROLE_PERMISSIONS.get(Role(role), frozenset())


def has(role: Role | str, permission: Permission) -> bool:
    return permission in permissions_for(role)


def require(role: Role | str, permission: Permission) -> None:
    if not has(role, permission):
        raise PermissionDenied(permission, Role(role))

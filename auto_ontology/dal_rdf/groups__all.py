# SPDX-License-Identifier: Apache-2.0
"""RDF port of ``auto_ontology.dal.groups``: every public function, reads and writes.

Groups (``user_group``) and memberships (``user_group__member``, composite key,
the Better Auth id stored as a plain literal with no foreign key) live in the
graph. **Accounts do not**: ``frontend."user"`` belongs to Prisma / Better Auth,
is not part of the catalog metadata and so has no Direct-Mapping triples. The
three account lookups (``_accounts_by_id``, ``get_account``, the account half of
``list_users_and_groups``) therefore still read that table through the DAL
store; with no Postgres they raise exactly as upstream does when the table is
missing.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

from auto_ontology.dal import schema as s
from auto_ontology.dal.groups import _ACCOUNT_SQL, GroupNameTaken, UnknownGroupMembers
from auto_ontology.dal.session import store
from auto_ontology.dal_rdf import _gov
from auto_ontology.dal_rdf._common import P, client, iri, select_rows, wrap
from auto_ontology.rdf import writes
from auto_ontology.rdf.vocab import row_id, row_iri

PORTS = [
    "list_groups",
    "get_group",
    "groups_for_user",
    "get_account",
    "list_users_and_groups",
    "create_group",
    "update_group",
    "delete_group",
]

_GROUP_COLUMNS = ["name", "description", "color", "role", "created"]


def _require_role(role: str) -> None:
    if role not in (s.GROUP_ROLE_ADMIN, s.GROUP_ROLE_VIEWER):
        raise ValueError(f"Role must be admin or viewer, got {role!r}")


def _name_taken(name: str, *, exclude_id: str | None = None) -> bool:
    wanted = _gov.input_fold(name)
    return any(
        _gov.stored_fold(g.get("name")) == wanted and g["id"] != exclude_id
        for g in select_rows("user_group", ["name"])
    )


def _accounts_by_id(user_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not user_ids:
        return {}
    rows = store().query_read(
        text(_ACCOUNT_SQL + " WHERE id = ANY(:user_ids)"), {"user_ids": list(user_ids)}
    )
    return {row["id"]: dict(row) for row in rows}


def _missing_user_ids(user_ids: list[str]) -> list[str]:
    found = _accounts_by_id(user_ids)
    return [user_id for user_id in user_ids if user_id not in found]


def _user_view(account: dict[str, Any] | None, user_id: str) -> dict[str, Any]:
    if account is None:
        return {"id": user_id, "name": "", "email": "", "role": None}
    return {
        "id": account["id"],
        "name": account["name"] or "",
        "email": account["email"] or "",
        "role": account["role"],
    }


def _members(group_ids: list[str]) -> list[tuple[str, str]]:
    """``(group_id, user_id)`` for every membership of these groups."""
    rows = client().select(
        "SELECT ?group ?user WHERE { "
        + wrap(
            f"?m a <urn:ao:user_group__member> ; {P.user_group__member.ref_group_id} ?group ; "
            f"{P.user_group__member.user_id} ?user ."
        )
        + " }"
    )
    wanted = set(group_ids)
    return [(row_id(r["group"]), r["user"]) for r in rows if row_id(r["group"]) in wanted]


def _attach_users(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not groups:
        return []
    member_rows = _members([g["id"] for g in groups])
    accounts = _accounts_by_id(list({user_id for _, user_id in member_rows}))
    members: dict[str, list[str]] = {}
    for group_id, user_id in member_rows:
        members.setdefault(group_id, []).append(user_id)
    attached: list[dict[str, Any]] = []
    for group in groups:
        users = [_user_view(accounts.get(u), u) for u in members.get(group["id"], [])]
        users.sort(key=lambda user: ((user["name"] or "").lower(), user["id"]))
        attached.append({**group, "description": group["description"] or "", "users": users})
    return attached


def _group_rows(*, group_id: str | None = None, user_id: str | None = None) -> list[dict[str, Any]]:
    groups = [{"id": r["id"], **{c: r.get(c) for c in _GROUP_COLUMNS}} for r in select_rows("user_group", _GROUP_COLUMNS)]
    if user_id is not None:
        belonging = {g for g, u in _members([x["id"] for x in groups]) if u == user_id}
        groups = [g for g in groups if g["id"] in belonging]
    if group_id is not None:
        groups = [g for g in groups if g["id"] == group_id]
    groups.sort(key=lambda g: _gov.sort_key(g["name"], g["id"]))
    return _attach_users(groups)


def _member_rows(group_id: str, user_ids: list[str]) -> list[dict[str, Any]]:
    # A repeated id is the primary-key violation Postgres raises; nothing is written.
    if len(set(user_ids)) != len(user_ids):
        raise _gov.integrity_error("INSERT INTO user_group__member", "pk_user_group__member")
    return [{"group_id": group_id, "user_id": user_id} for user_id in user_ids]


def list_groups() -> list[dict[str, Any]]:
    return _group_rows()


def get_group(group_id: str) -> dict[str, Any] | None:
    rows = _group_rows(group_id=group_id)
    return rows[0] if rows else None


def groups_for_user(user_id: str) -> list[dict[str, Any]]:
    return _group_rows(user_id=user_id)


def get_account(user_id: str) -> dict[str, Any] | None:
    rows = store().query_read(text(_ACCOUNT_SQL + " WHERE id = :user_id"), {"user_id": user_id})
    if not rows:
        return None
    account = rows[0]
    return _user_view(dict(account), account["id"])


def list_users_and_groups() -> dict[str, Any]:
    accounts = store().query_read(text(_ACCOUNT_SQL + " ORDER BY lower(name), id"))
    groups = list_groups()
    group_ids: dict[str, list[str]] = {}
    for group in groups:
        for user in group["users"]:
            group_ids.setdefault(user["id"], []).append(group["id"])
    users = [
        {**_user_view(dict(account), account["id"]), "groups": group_ids.get(account["id"], [])}
        for account in accounts
    ]
    return {"users": users, "groups": groups}


def create_group(
    *, name: str, description: str | None, color: str, role: str, user_ids: list[str]
) -> dict[str, Any]:
    _require_role(role)
    if _name_taken(name):
        raise GroupNameTaken(name)
    missing = _missing_user_ids(user_ids)
    if missing:
        raise UnknownGroupMembers(missing)

    group = writes.apply_defaults(
        "user_group", {"name": name, "description": description, "color": color, "role": str(role)}
    )
    members = _member_rows(group["id"], user_ids)
    lines = writes.row_triples("user_group", group)
    for member in members:
        lines.extend(writes.row_triples("user_group__member", member))
    client().update(f"INSERT DATA {{ GRAPH {iri(writes.data_graph_iri())} {{ {' '.join(lines)} }} }}")

    created = get_group(group["id"])
    if created is None:  # pragma: no cover - the insert just wrote this id
        raise RuntimeError(f"Group {group['id']!r} missing after insert")
    return created


def update_group(
    *, group_id: str, updates: dict[str, Any], user_ids: list[str] | None
) -> dict[str, Any] | None:
    if "role" in updates:
        _require_role(updates["role"])
    if user_ids is not None:
        missing = _missing_user_ids(user_ids)
        if missing:
            raise UnknownGroupMembers(missing)

    if not _gov.existing_ids("user_group", [group_id]):
        return None
    if "name" in updates and _name_taken(updates["name"], exclude_id=group_id):
        raise GroupNameTaken(updates["name"])
    new_members = _member_rows(group_id, user_ids) if user_ids is not None else None
    if updates:
        writes.update_row("user_group", group_id, {k: (str(v) if k == "role" else v) for k, v in updates.items()})
    if new_members is not None:
        old = [
            row_iri("user_group__member", {"group_id": g, "user_id": u})
            for g, u in _members([group_id])
        ]
        if old:
            writes.delete_subjects("user_group__member", old)
        if new_members:
            writes.insert_rows("user_group__member", new_members)
    return get_group(group_id)


def delete_group(group_id: str) -> bool:
    if not _gov.existing_ids("user_group", [group_id]):
        return False
    writes.delete_rows("user_group", [group_id])  # memberships cascade
    return True

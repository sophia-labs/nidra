# SPDX-License-Identifier: Apache-2.0
"""RDF port of ``auto_ontology.dal.tags``: the writes and every read except
``fetch_tags_map`` (another lane's port).

Rows live in the Direct-Mapping vocabulary (``tag``, ``tag_target``); writes go
through ``auto_ontology.rdf.writes``. ``write_transaction`` cannot roll back on
this backend, so a function's writes are kept to as few update requests as
possible instead.

``apply_labels_now_matched`` and ``remove_labels_no_longer_matched`` take
``targets`` as ``{kind: matched}``. Upstream, ``matched`` is a SQLAlchemy
``SELECT id`` built by ``auto_ontology.dal.search.matching_id_selects``; here a
``matched`` may also be any iterable of ids (what a ported search yields). A
SQLAlchemy ``Select`` is still run through the DAL ``store()``, which needs
Postgres: that is the one seam in this module that cannot be graph-only until
the search is ported (see ``_matched_ids``).
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from auto_ontology.dal_rdf import _gov
from auto_ontology.dal_rdf._common import P, client, iri, select_rows, values, wrap
from auto_ontology.rdf import writes
from auto_ontology.rdf.vocab import ref_iri, row_id, row_iri_single

logger = logging.getLogger(__name__)

PORTS = [
    "create_tag",
    "update_tag",
    "delete_tag",
    "get_tag",
    "get_tag_by_name",
    "get_or_create_tag",
    "list_tags",
    "count_tags",
    "existing_tag_ids",
    "list_tag_targets",
    "count_tag_targets",
    "attach_tag",
    "attach_tags_by_rule",
    "apply_labels_now_matched",
    "remove_labels_no_longer_matched",
    "detach_tag",
]

PII_TAG_NAME = "PII"

_TAG_COLUMNS = ["name", "created", "modified", "created_by", "modified_by"]
_OUT = ["id", *_TAG_COLUMNS]

#: kind -> (tag_target column, the table that column references)
_KINDS: dict[str, tuple[str, str]] = {
    "term": ("term_id", "term"),
    "table": ("table_id", "catalog_table"),
    "column": ("column_id", "catalog_column"),
    "column_attribute": ("column_attribute_id", "column_attribute"),
    "sql_attribute": ("sql_attribute_id", "sql_attribute"),
}
_ATTRIBUTE_LINKS = {"column_attribute": "column_attribute__term", "sql_attribute": "sql_attribute__term"}


def _column(kind: str) -> tuple[str, str]:
    """``(tag_target column, referenced table)``; ``ValueError`` for an unknown kind,
    with upstream's message."""
    try:
        return _KINDS[kind]
    except KeyError:
        known = ", ".join(sorted(_KINDS))
        raise ValueError(f"Unknown tag target {kind!r}; expected one of {known}") from None


def _shape(row: dict[str, Any]) -> dict[str, Any]:
    return {c: row.get(c) for c in _OUT}


def _all_tags() -> list[dict[str, Any]]:
    return [_shape(r) for r in select_rows("tag", _TAG_COLUMNS)]


def _name_taken(name: str, *, exclude_id: str | None = None) -> bool:
    wanted = _gov.input_fold(name)
    return any(
        _gov.stored_fold(t["name"]) == wanted and t["id"] != exclude_id for t in _all_tags()
    )


def _sorted(tags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(tags, key=lambda t: _gov.sort_key(t["name"], t["id"]))


def _matching(tags: list[dict[str, Any]], search: str | None) -> list[dict[str, Any]]:
    if search is None or search.strip() == "":
        return tags
    pattern = f"%{search.strip()}%"
    return [t for t in tags if _gov.ilike(t["name"], pattern)]


def list_tags(
    *, search: str | None = None, skip: int = 0, limit: int | None = None
) -> list[dict[str, Any]]:
    tags = _sorted(_matching(_all_tags(), search))
    tags = tags[skip or 0 :]
    return tags if limit is None else tags[:limit]


def count_tags(*, search: str | None = None) -> int:
    return len(_matching(_all_tags(), search))


def get_tag(tag_id: str) -> dict[str, Any] | None:
    rows = _gov.rows_by_ids("tag", [tag_id], _TAG_COLUMNS)
    return _shape(rows[0]) if rows else None


def get_tag_by_name(name: str) -> dict[str, Any] | None:
    wanted = _gov.input_fold(name)
    for tag in _sorted(_all_tags()):
        if _gov.stored_fold(tag["name"]) == wanted:
            return tag
    return None


def get_or_create_tag(*, name: str, created_by: str | None = None) -> dict[str, Any]:
    normalized_name = name.strip()
    existing = get_tag_by_name(normalized_name)
    if existing is not None:
        return existing
    try:
        return create_tag(name=normalized_name, created_by=created_by)
    except ValueError:
        existing = get_tag_by_name(normalized_name)
        if existing is None:
            raise
        return existing


def existing_tag_ids(tag_ids: list[str]) -> set[str]:
    if not tag_ids:
        return set()
    return _gov.existing_ids("tag", tag_ids)


# ---------------------------------------------------------------------------
# Targets of a tag
# ---------------------------------------------------------------------------

_HEAD = (
    "?tt a <urn:ao:tag_target> ; {tag_id} {tag} ; {ref} ?item ; {tagged} ?tagged . "
    "OPTIONAL {{ ?tt {tagged_by} ?tagged_by }} "
    "OPTIONAL {{ ?tt {ref_rule} ?rule . OPTIONAL {{ ?rule {rule_name} ?rule_name }} }} "
)


def _kind_rows(tag_id: str, kind: str) -> list[dict[str, Any]]:
    column, table = _column(kind)
    head = _HEAD.format(
        tag_id=P.tag_target.ref_tag_id,
        tag=iri(row_iri_single("tag", tag_id)),
        ref=iri(ref_iri("tag_target", column)),
        tagged=P.tag_target.tagged,
        tagged_by=P.tag_target.tagged_by,
        ref_rule=P.tag_target.ref_rule_id,
        rule_name=P.rule.name,
    )
    name = getattr(P, table).name
    if kind == "term":
        body = f"{head} ?item {name} ?name ."
    elif kind == "table":
        body = (
            f"{head} ?item {name} ?name ; {P.catalog_table.ref_schema_id} ?schema . "
            f"?schema {P.catalog_schema.name} ?sname ; {P.catalog_schema.ref_database_id} ?db . "
            f"?db {P.catalog_database.name} ?dname ."
        )
    elif kind == "column":
        body = (
            f"{head} ?item {name} ?name ; {P.catalog_column.ref_table_id} ?table . "
            f"?table {P.catalog_table.name} ?tname ; {P.catalog_table.ref_schema_id} ?schema . "
            f"?schema {P.catalog_schema.name} ?sname ; {P.catalog_schema.ref_database_id} ?db . "
            f"?db {P.catalog_database.name} ?dname ."
        )
    else:
        link = getattr(P, _ATTRIBUTE_LINKS[kind])
        body = (
            f"{head} ?item {name} ?name . "
            f"OPTIONAL {{ ?link {link.ref_attribute_id} ?item ; {link.ref_term_id} ?term . "
            f"?term {P.term.name} ?path }}"
        )
    selected = "?item ?name ?tagged ?tagged_by ?rule ?rule_name ?path ?db ?dname ?schema ?sname ?table ?tname ?term"
    rows = client().select(f"SELECT {selected} WHERE {{ {wrap(body)} }}")
    out: list[dict[str, Any]] = []
    for r in rows:
        path = None
        parents: dict[str, str | None] = {"database_id": None, "schema_id": None, "table_id": None, "term_id": None}
        if kind in ("table", "column"):
            path = f"{r['dname']}.{r['sname']}" + (f".{r['tname']}" if kind == "column" else "")
            parents["database_id"] = row_id(r["db"])
            parents["schema_id"] = row_id(r["schema"])
            if kind == "column":
                parents["table_id"] = row_id(r["table"])
        elif kind in _ATTRIBUTE_LINKS:
            path = r.get("path")
            parents["term_id"] = row_id(r["term"]) if r.get("term") else None
        rule = r.get("rule")
        out.append(
            {
                "id": row_id(r["item"]),
                "name": r["name"],
                "path": path,
                "type": kind,
                "tagged": r.get("tagged"),
                "tagged_by": r.get("tagged_by"),
                **parents,
                "rule": None if rule is None else {"id": row_id(rule), "name": r.get("rule_name")},
            }
        )
    return out


def _all_targets(tag_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for kind in _KINDS:
        rows.extend(_kind_rows(tag_id, kind))
    return rows


def list_tag_targets(
    tag_id: str, *, skip: int = 0, limit: int | None = None
) -> list[dict[str, Any]]:
    rows = sorted(_all_targets(tag_id), key=lambda r: _gov.sort_key(r["name"], r["id"]))
    rows = rows[skip or 0 :]
    return rows if limit is None else rows[:limit]


def count_tag_targets(tag_id: str) -> int:
    return len(_all_targets(tag_id))


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def create_tag(*, name: str, created_by: str | None = None) -> dict[str, Any]:
    if _name_taken(name):
        raise ValueError(f"Tag with name {name!r} already exists")
    return _shape(writes.insert_row("tag", {"name": name, "created_by": created_by}))


def update_tag(*, tag_id: str, name: str, modified_by: str | None = None) -> dict[str, Any] | None:
    if _name_taken(name, exclude_id=tag_id):
        raise ValueError(f"Tag with name {name!r} already exists")
    if get_tag(tag_id) is None:
        return None
    # ``onupdate=now()`` on ``tag.modified`` is the database's doing; here it is explicit.
    writes.update_row("tag", tag_id, {"name": name, "modified_by": modified_by, "modified": _gov.now()})
    return get_tag(tag_id)


def delete_tag(tag_id: str) -> bool:
    if get_tag(tag_id) is None:
        return False
    # tag_target and rule__tag rows go by the metadata's ON DELETE CASCADE.
    writes.delete_rows("tag", [tag_id])
    return True


def _attribute_without_a_term(kind: str, item_id: str) -> bool:
    link_table = _ATTRIBUTE_LINKS.get(kind)
    if link_table is None:
        return False
    _, table = _column(kind)
    if item_id not in _gov.existing_ids(table, [item_id]):
        return False
    link = getattr(P, link_table)
    rows = client().select(
        "SELECT ?link WHERE { "
        + wrap(f"?link {link.ref_attribute_id} {iri(row_iri_single(table, item_id))} .")
        + " } LIMIT 1"
    )
    return not rows


def _existing_pairs(tag_ids: list[str], kind: str, item_ids: list[str]) -> set[tuple[str, str]]:
    column, table = _column(kind)
    if not tag_ids or not item_ids:
        return set()
    rows = client().select(
        "SELECT ?tag ?item WHERE { "
        + wrap(
            f"{values('tag', [row_iri_single('tag', t) for t in tag_ids])} "
            f"{values('item', [row_iri_single(table, i) for i in item_ids])} "
            f"?tt a <urn:ao:tag_target> ; {P.tag_target.ref_tag_id} ?tag ; {iri(ref_iri('tag_target', column))} ?item ."
        )
        + " }"
    )
    return {(row_id(r["tag"]), row_id(r["item"])) for r in rows}


def attach_tag(
    *, tag_id: str, kind: str, item_id: str, tagged_by: str | None = None
) -> list[dict[str, Any]] | None:
    from auto_ontology.dal import tags as dal_tags

    column, table = _column(kind)
    if _attribute_without_a_term(kind, item_id):
        raise ValueError(f"{kind} {item_id!r} is not a property of any term, so it cannot be tagged")
    # Both ends are foreign keys: a missing tag or object is the ``None`` answer.
    if not _gov.existing_ids("tag", [tag_id]) or not _gov.existing_ids(table, [item_id]):
        return None
    if (tag_id, item_id) not in _existing_pairs([tag_id], kind, [item_id]):
        writes.insert_row("tag_target", {"tag_id": tag_id, "tagged_by": tagged_by, column: item_id})
    return dal_tags.fetch_tags_map(kind, [item_id]).get(item_id, [])


def attach_tags_by_rule(
    *, rule_id: str, tag_ids: list[str], targets: list[tuple[str, str]]
) -> int:
    if not tag_ids or not targets:
        return 0
    by_kind: dict[str, list[str]] = {}
    for kind, item_id in targets:
        by_kind.setdefault(kind, []).append(item_id)
    return _insert_missing(rule_id, tag_ids, {k: list(dict.fromkeys(v)) for k, v in by_kind.items()})


def _insert_missing(rule_id: str, tag_ids: list[str], by_kind: dict[str, list[str]]) -> int:
    """One batch insert of every ``(tag, item)`` pair not yet labelled."""
    tag_ids = list(dict.fromkeys(tag_ids))
    rows: list[dict[str, Any]] = []
    for kind, item_ids in by_kind.items():
        column, _ = _column(kind)
        have = _existing_pairs(tag_ids, kind, item_ids)
        rows.extend(
            {"tag_id": t, "rule_id": rule_id, column: i}
            for t in tag_ids
            for i in item_ids
            if (t, i) not in have
        )
    if rows:
        _check_references(rule_id, rows)
        writes.insert_rows("tag_target", rows)
    return len(rows)


def _check_references(rule_id: str, rows: list[dict[str, Any]]) -> None:
    """``tag_target``'s foreign keys, which the graph does not enforce: a missing
    rule, tag or object is the ``IntegrityError`` Postgres raises (nothing written)."""
    if not _gov.existing_ids("rule", [rule_id]):
        raise _gov.integrity_error("INSERT INTO tag_target", "fk_tag_target_rule_id_rule")
    if not _gov.existing_ids("tag", {r["tag_id"] for r in rows}) >= {r["tag_id"] for r in rows}:
        raise _gov.integrity_error("INSERT INTO tag_target", "fk_tag_target_tag_id_tag")
    for kind, (column, table) in _KINDS.items():
        wanted = {r[column] for r in rows if column in r}
        if wanted and not _gov.existing_ids(table, wanted) >= wanted:
            raise _gov.integrity_error("INSERT INTO tag_target", f"fk_tag_target_{column}_{table}")


def _matched_ids(matched: Any) -> list[str]:
    """The ids a rule's match selects, for one kind.

    An iterable of ids is taken as is. A SQLAlchemy ``Select`` (what
    ``dal.search.matching_id_selects`` builds) is run through the DAL store:
    it cannot be translated to SPARQL, so this is Postgres-bound until the
    search itself is ported and yields ids.
    """
    if hasattr(matched, "compile") and hasattr(matched, "subquery"):
        from auto_ontology.dal.session import store

        return [row["id"] for row in store().query_read(matched)]
    return list(dict.fromkeys(matched))


def apply_labels_now_matched(*, rule_id: str, tag_ids: list[str], targets: dict[str, Any]) -> int:
    if not tag_ids:
        return 0
    by_kind = {kind: _matched_ids(matched) for kind, matched in targets.items()}
    for kind in by_kind:
        _column(kind)
    return _insert_missing(rule_id, tag_ids, by_kind)


_COLUMN_NAMES = [c for c, _ in _KINDS.values()]


def remove_labels_no_longer_matched(*, rule_id: str, targets: dict[str, Any]) -> int:
    matched = {_column(kind)[0]: set(_matched_ids(m)) for kind, m in targets.items()}
    rows = select_rows("tag_target", ["tag_id", *_COLUMN_NAMES], extra=f"?s {P.tag_target.rule_id} {_lit(rule_id)} .")
    doomed = []
    for row in rows:
        kept = any(row.get(col) is not None and row[col] in ids for col, ids in matched.items())
        if not kept:
            doomed.append(row)
    if not doomed:
        return 0
    writes.delete_rows("tag_target", [r["id"] for r in doomed])
    from auto_ontology.dal import tags as dal_tags

    dal_tags._cascade_pii_off_columns(
        [{"tag_id": r.get("tag_id"), "column_id": r.get("column_id")} for r in doomed]
    )
    return len(doomed)


def _lit(value: str) -> str:
    from auto_ontology.rdf.client import lit

    return lit(value)


def detach_tag(*, tag_id: str, kind: str, item_id: str) -> list[dict[str, Any]] | None:
    from auto_ontology.dal import tags as dal_tags

    column, table = _column(kind)
    rows = client().select(
        "SELECT ?tt WHERE { "
        + wrap(
            f"?tt a <urn:ao:tag_target> ; {P.tag_target.ref_tag_id} {iri(row_iri_single('tag', tag_id))} ; "
            f"{iri(ref_iri('tag_target', column))} {iri(row_iri_single(table, item_id))} ."
        )
        + " }"
    )
    if not rows:
        return None
    writes.delete_subjects("tag_target", [r["tt"] for r in rows])
    return dal_tags.fetch_tags_map(kind, [item_id]).get(item_id, [])

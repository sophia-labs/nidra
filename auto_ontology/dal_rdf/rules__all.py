# SPDX-License-Identifier: Apache-2.0
"""RDF port of ``auto_ontology.dal.rules``: every public function, reads and writes.

A rule is a ``rule`` row plus ``rule__tag`` rows (composite key, ``position``
keeps the order the tags were picked). ``create_rule`` writes both in one
INSERT DATA. Cascades (``rule__tag``, and the labels a rule applied) follow the
metadata through ``writes.delete_subjects``.
"""
from __future__ import annotations

from typing import Any

from auto_ontology.dal_rdf import _gov
from auto_ontology.dal_rdf._common import P, client, iri, select_rows, wrap
from auto_ontology.rdf import writes
from auto_ontology.rdf.vocab import row_id, row_iri_single

PORTS = ["create_rule", "list_rules", "count_rules", "update_rule", "delete_rule"]

_RULE_COLUMNS = [
    "name", "search_term", "text_match_option", "filters",
    "created", "modified", "created_by", "modified_by",
]
_OUT = ["id", *_RULE_COLUMNS]
PII_TAG_NAME = "PII"


def _shape(row: dict[str, Any]) -> dict[str, Any]:
    return {c: row.get(c) for c in _OUT}


def _all_rules() -> list[dict[str, Any]]:
    return [_shape(r) for r in select_rows("rule", _RULE_COLUMNS)]


def _name_taken(name: str, *, exclude_id: str | None = None) -> bool:
    wanted = _gov.input_fold(name)
    return any(
        _gov.stored_fold(r["name"]) == wanted and r["id"] != exclude_id for r in _all_rules()
    )


def _taken(name: str) -> ValueError:
    return ValueError(f"Rule with name {name!r} already exists")


def create_rule(
    *,
    name: str,
    search_term: str,
    text_match_option: str,
    filters: dict[str, Any],
    tags: list[str],
    created_by: str,
) -> str:
    if not tags:
        raise ValueError("A rule must apply at least one tag")
    if _name_taken(name):
        raise _taken(name)
    # A tag id that is not a tag, or listed twice, is the foreign-key / primary-key
    # failure Postgres raises; nothing is written in either case.
    if len(set(tags)) != len(tags):
        raise _gov.integrity_error("INSERT INTO rule__tag", "pk_rule__tag")
    if _gov.existing_ids("tag", tags) != set(tags):
        raise _gov.integrity_error("INSERT INTO rule__tag", "fk_rule__tag_tag_id_tag")

    rule = writes.apply_defaults(
        "rule",
        {
            "name": name,
            "search_term": search_term,
            "text_match_option": text_match_option,
            "filters": filters,
            "created_by": created_by,
        },
    )
    lines = writes.row_triples("rule", rule)
    for position, tag_id in enumerate(tags):
        lines.extend(writes.row_triples("rule__tag", {"rule_id": rule["id"], "tag_id": tag_id, "position": position}))
    client().update(f"INSERT DATA {{ GRAPH {iri(writes.data_graph_iri())} {{ {' '.join(lines)} }} }}")
    return rule["id"]


def _tags_by_rule(rule_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    tags: dict[str, list[dict[str, Any]]] = {rule_id: [] for rule_id in rule_ids}
    if not rule_ids:
        return tags
    rows = client().select(
        "SELECT ?rule ?tag ?name ?position WHERE { "
        + wrap(
            f"?rt a <urn:ao:rule__tag> ; {P.rule__tag.ref_rule_id} ?rule ; {P.rule__tag.ref_tag_id} ?tag ; "
            f"{P.rule__tag.position} ?position . ?tag {P.tag.name} ?name ."
        )
        + " }"
    )
    wanted = set(rule_ids)
    rows = [r for r in rows if row_id(r["rule"]) in wanted]
    rows.sort(key=lambda r: (row_id(r["rule"]), r["position"]))
    for r in rows:
        tags[row_id(r["rule"])].append({"id": row_id(r["tag"]), "name": r["name"]})
    return tags


def _matching(rules: list[dict[str, Any]], search: str | None) -> list[dict[str, Any]]:
    if search is None or search.strip() == "":
        return rules
    pattern = f"%{search.strip()}%"
    tagged = _tags_by_rule([r["id"] for r in rules])
    return [
        r for r in rules
        if _gov.ilike(r["name"], pattern) or any(_gov.ilike(t["name"], pattern) for t in tagged[r["id"]])
    ]


def list_rules(*, search: str | None = None, skip: int = 0, limit: int | None = None) -> list[dict[str, Any]]:
    rules = sorted(_matching(_all_rules(), search), key=lambda r: _gov.sort_key(r["name"], r["id"]))
    rules = rules[skip or 0 :]
    if limit is not None:
        rules = rules[:limit]
    if not rules:
        return []
    tags = _tags_by_rule([r["id"] for r in rules])
    return [{**r, "tags": tags[r["id"]]} for r in rules]


def count_rules(*, search: str | None = None) -> int:
    return len(_matching(_all_rules(), search))


def update_rule(*, rule_id: str, name: str, modified_by: str | None = None) -> dict[str, Any] | None:
    if _name_taken(name, exclude_id=rule_id):
        raise _taken(name)
    if not _gov.existing_ids("rule", [rule_id]):
        return None
    writes.update_row("rule", rule_id, {"name": name, "modified_by": modified_by, "modified": _gov.now()})
    row = _shape(_gov.rows_by_ids("rule", [rule_id], _RULE_COLUMNS)[0])
    return {**row, "tags": _tags_by_rule([rule_id])[rule_id]}


def _delete_tags_left_labelling_nothing(tag_ids: list[str]) -> None:
    """Delete whichever of *tag_ids* no label and no other rule holds (never ``PII``)."""
    from auto_ontology.dal_rdf._common import values

    tags = [row_iri_single("tag", t) for t in tag_ids]
    held = client().select(
        "SELECT DISTINCT ?tag WHERE { "
        + wrap(
            f"{values('tag', tags)} "
            f"{{ ?x {P.tag_target.ref_tag_id} ?tag }} UNION {{ ?x {P.rule__tag.ref_tag_id} ?tag }}"
        )
        + " }"
    )
    held_ids = {row_id(r["tag"]) for r in held}
    doomed = [
        t["id"]
        for t in _gov.rows_by_ids("tag", tag_ids, ["name"])
        if t["id"] not in held_ids and _gov.stored_fold(t.get("name")) != PII_TAG_NAME.lower()
    ]
    if doomed:
        writes.delete_rows("tag", doomed)


def delete_rule(rule_id: str, *, keep_tags: bool = False) -> bool:
    existing = _gov.rows_by_ids("rule", [rule_id], ["created_by"])
    labels = select_rows(
        "tag_target",
        ["tag_id", "column_id"],
        extra=f"?s {P.tag_target.rule_id} {_lit(rule_id)} .",
    )
    if keep_tags:
        if labels:
            created_by = existing[0].get("created_by") if existing else None
            writes.update_rows(
                "tag_target",
                [row_iri_single("tag_target", r["id"]) for r in labels],
                {"rule_id": None, "tagged_by": created_by},
            )
        if not existing:
            return False
        writes.delete_rows("rule", [rule_id])
        return True

    applied = [
        row_id(r["tag"])
        for r in client().select(
            "SELECT ?tag WHERE { "
            + wrap(f"?rt a <urn:ao:rule__tag> ; {P.rule__tag.ref_rule_id} {iri(row_iri_single('rule', rule_id))} ; {P.rule__tag.ref_tag_id} ?tag .")
            + " }"
        )
    ]
    labelled_columns = [{"tag_id": r.get("tag_id"), "column_id": r["column_id"]} for r in labels if r.get("column_id")]
    if not existing:
        return False
    writes.delete_rows("rule", [rule_id])  # rule__tag and the rule's labels cascade
    from auto_ontology.dal import tags as dal_tags

    dal_tags._cascade_pii_off_columns(labelled_columns)
    if applied:
        _delete_tags_left_labelling_nothing(applied)
    return True


def _lit(value: str) -> str:
    from auto_ontology.rdf.client import lit

    return lit(value)

# SPDX-License-Identifier: Apache-2.0
"""RDF ports of ``auto_ontology.dal.search``: the global search list, its count,
and the id lists a rule replays (gaps lane).

The SQL builds one UNION over ten tables. Here each label is read from the graph
with the substring match pushed down as a ``FILTER`` (the catalog tables can be
large); the facets that need a join (tag filter, data filter, visibility of
Terms and attributes, the alias match on a Term's synonyms) and the rank are
applied in Python over small maps read from the same graph, so the answer is the
SQL's: the same rows, the same rank and cap, the same breadcrumbs.

``matching_id_selects`` returns ``{label: [ids]}`` instead of SQLAlchemy
Selects; the gov lane's ``tags__writes._matched_ids`` takes any iterable of ids.
``search_tokens`` and the other token helpers are pure functions and need no port.
"""
from __future__ import annotations

from typing import Any

from auto_ontology.catalog.constants import Labels
from auto_ontology.dal.search import (
    LABEL_TAG,
    LIST_LIMIT,
    SEARCH_TYPE_VIEW,
    _SEARCH_LABELS,
    _LABELS_WITHOUT_DESCRIPTION,
    is_view_table_type,
    search_object_type,
    synonym_matches_tokens,
)
from auto_ontology.dal_rdf import _gov
from auto_ontology.dal_rdf._common import P, client, lit, select_rows, values, wrap
from auto_ontology.rdf.vocab import class_iri, prop_iri, row_id, row_iri_single
from auto_ontology.semantic.constants import (
    LABEL_COLUMN_ATTRIBUTE,
    LABEL_PQL_ANALYSIS,
    LABEL_SQL_ATTRIBUTE,
    LABEL_TERM,
    SEMANTIC_SOURCE,
)

PORTS = ["fetch_global_search", "count_global_search", "matching_id_selects"]

_TABLES = {
    Labels.DB: "catalog_database",
    Labels.SCHEMA: "catalog_schema",
    Labels.TABLE: "catalog_table",
    Labels.COLUMN: "catalog_column",
    Labels.CUSTOM_ANALYSIS: "custom_analysis",
    LABEL_TERM: "term",
    LABEL_COLUMN_ATTRIBUTE: "column_attribute",
    LABEL_SQL_ATTRIBUTE: "sql_attribute",
    LABEL_PQL_ANALYSIS: "pql_analysis",
    LABEL_TAG: "tag",
}
_TAG_COLUMNS = {
    LABEL_TERM: "term_id",
    Labels.TABLE: "table_id",
    Labels.COLUMN: "column_id",
    LABEL_COLUMN_ATTRIBUTE: "column_attribute_id",
    LABEL_SQL_ATTRIBUTE: "sql_attribute_id",
}
_ATTRIBUTE_LINKS = {
    LABEL_COLUMN_ATTRIBUTE: "column_attribute__term",
    LABEL_SQL_ATTRIBUTE: "sql_attribute__term",
}
_DATA_LABELS = (Labels.TABLE, Labels.COLUMN)


# -- the facets -------------------------------------------------------------

def _tag_rules_out(label: str, tag_ids, include_untagged: bool) -> bool:
    if not tag_ids and not include_untagged:
        return False
    return label not in _TAG_COLUMNS and not include_untagged


def _data_rules_out(label: str, data_ids) -> bool:
    return bool(data_ids) and label not in _DATA_LABELS


class _Facets:
    """Lazily read maps the Python-side filters need."""

    def __init__(self, tag_ids, include_untagged, data_ids):
        self.tag_ids = set(tag_ids)
        self.include_untagged = include_untagged
        self.data_ids = set(data_ids)
        self._tags: dict[str, dict[str, set[str]]] | None = None
        self._visible: set[str] | None = None
        self._table_schema: dict[str, str | None] | None = None
        self._schema_db: dict[str, str | None] | None = None

    def tag_ok(self, label: str, item_id: str) -> bool:
        if not self.tag_ids and not self.include_untagged:
            return True
        if label not in _TAG_COLUMNS:
            return True
        if self._tags is None:
            self._tags = {lbl: {} for lbl in _TAG_COLUMNS}
            cols = list(_TAG_COLUMNS.values())
            for row in select_rows("tag_target", ["tag_id", *cols]):
                for lbl, col in _TAG_COLUMNS.items():
                    if row.get(col) is not None:
                        self._tags[lbl].setdefault(row[col], set()).add(row.get("tag_id"))
        have = self._tags[label].get(item_id, set())
        if self.tag_ids and have & self.tag_ids:
            return True
        return bool(self.include_untagged and not have)

    def data_ok(self, label: str, row: dict[str, Any]) -> bool:
        if not self.data_ids or label not in _DATA_LABELS:
            return True
        if self._schema_db is None:
            self._schema_db = {r["id"]: r.get("database_id") for r in select_rows("catalog_schema", ["database_id"])}
        if label == Labels.TABLE:
            schema_id = row.get("schema_id")
        else:
            if self._table_schema is None:
                self._table_schema = {r["id"]: r.get("schema_id") for r in select_rows("catalog_table", ["schema_id"])}
            schema_id = self._table_schema.get(row.get("table_id"))
            if schema_id is None:
                return False
        if schema_id is None or schema_id not in self._schema_db:
            return False
        return schema_id in self.data_ids or self._schema_db[schema_id] in self.data_ids

    def visible_terms(self) -> set[str]:
        """Semantic Terms with at least one table representing them."""
        if self._visible is None:
            linked = {
                row_id(r["t"])
                for r in client().select(
                    "SELECT DISTINCT ?t WHERE { " + wrap(f"?l {P.table__term.ref_term_id} ?t") + " }"
                )
            }
            semantic = {r["id"] for r in select_rows("term", ["source"]) if r.get("source") == SEMANTIC_SOURCE}
            self._visible = linked & semantic
        return self._visible


# -- reading one label ------------------------------------------------------

def _all_contain(var: str, tokens: list[str]) -> str:
    return " && ".join(f"CONTAINS(LCASE(STR({var})), {lit(t)})" for t in tokens)


def _text_filter(label: str, tokens: list[str], include_description: bool) -> str:
    cond = f"({_all_contain('?name', tokens)})"
    if include_description and label not in _LABELS_WITHOUT_DESCRIPTION:
        cond = f"({cond} || COALESCE(({_all_contain('?description', tokens)}), false))"
    return f"FILTER{cond}"


def _contains_all(text: str | None, tokens: list[str]) -> bool:
    low = (text or "").lower()
    return text is not None and all(t in low for t in tokens)


def _matches(label: str, row: dict[str, Any], tokens: list[str], include_description: bool) -> bool:
    if _contains_all(row.get("name"), tokens):
        return True
    return bool(
        include_description and label not in _LABELS_WITHOUT_DESCRIPTION and _contains_all(row.get("description"), tokens)
    )


def _read(label: str, tokens: list[str], include_description: bool, extra_cols: list[str] = ()) -> list[dict[str, Any]]:
    table = _TABLES[label]
    cols = ["name", *extra_cols]
    if label not in _LABELS_WITHOUT_DESCRIPTION:
        cols.append("description")
    return select_rows(table, cols, extra=_text_filter(label, tokens, include_description))


def _terms(facets: _Facets) -> list[dict[str, Any]]:
    """Every visible Term with its synonyms list (Terms are few; matched in Python)."""
    rows = select_rows("term", ["name", "description", "name_certified", "description_certified"])
    visible = facets.visible_terms()
    rows = [r for r in rows if r["id"] in visible]
    if not rows:
        return rows
    syn: dict[str, list[str]] = {}
    for r in client().select(
        "SELECT ?s ?v WHERE { "
        + wrap(
            f"?s a <{class_iri('term')}> ; <{prop_iri('term', 'synonyms')}> ?v . "
            f"FILTER(datatype(?v) = <http://www.w3.org/1999/02/22-rdf-syntax-ns#JSON>)"
        )
        + " }"
    ):
        syn[row_id(r["s"])] = list(r["v"] or [])
    for r in rows:
        r["synonyms"] = syn.get(r["id"])
    return rows


def _synonym_hit(row: dict[str, Any], synonym_tokens: list[str]) -> bool:
    return bool(synonym_tokens) and any(synonym_matches_tokens(s, synonym_tokens) for s in (row.get("synonyms") or []))


def _hit(label: str, row: dict[str, Any], **extra: Any) -> dict[str, Any]:
    base = {
        "id": row["id"],
        "name": row["name"],
        "description": None if label in _LABELS_WITHOUT_DESCRIPTION else row.get("description"),
        "label": label,
        "table_type": None,
        "certified": None,
        "certified_flag": None,
        "synonyms": None,
        "synonym_hit": None,
    }
    base.update(extra)
    return base


def _attribute_visible(label: str, ids: list[str], facets: _Facets) -> dict[str, str]:
    """attribute id → its lowest-id visible Term (the one the crumb names)."""
    if not ids:
        return {}
    link = getattr(P, _ATTRIBUTE_LINKS[label])
    out: dict[str, str] = {}
    visible = facets.visible_terms()
    for r in client().select(
        "SELECT ?a ?t WHERE { "
        + wrap(f"{values('a', [row_iri_single(_TABLES[label], i) for i in ids])} ?l {link.ref_attribute_id} ?a ; {link.ref_term_id} ?t")
        + " }"
    ):
        aid, tid = row_id(r["a"]), row_id(r["t"])
        if tid in visible and (aid not in out or tid < out[aid]):
            out[aid] = tid
    return out


def _collect(
    tokens, object_types, include_description, synonym_tokens, tag_ids, include_untagged, data_ids, *, aliases_in_match=False
):
    """``(main hits, alias-only hits)``. With *aliases_in_match* a Term whose
    synonym matches counts as matching and the alias list stays empty."""
    facets = _Facets(tag_ids, include_untagged, data_ids)
    main: list[dict[str, Any]] = []
    alias_only: list[dict[str, Any]] = []

    for label in _SEARCH_LABELS:
        if label == Labels.TABLE or label not in object_types:
            continue
        if _tag_rules_out(label, tag_ids, include_untagged) or _data_rules_out(label, data_ids):
            continue

        if label == LABEL_TERM:
            for r in _terms(facets):
                text = _matches(label, r, tokens, include_description)
                shit = _synonym_hit(r, synonym_tokens)
                if not facets.tag_ok(label, r["id"]):
                    continue
                if text or (aliases_in_match and shit):
                    if facets.data_ok(label, r):
                        main.append(_hit(label, r, certified=_certified(r), synonyms=r.get("synonyms"), synonym_hit=shit if synonym_tokens else None))
                elif shit:
                    alias_only.append(_hit(label, r, certified=_certified(r), synonyms=r.get("synonyms"), synonym_hit=True))
            continue

        if label in _ATTRIBUTE_LINKS:
            rows = [r for r in _read(label, tokens, include_description, ["certified"]) if facets.tag_ok(label, r["id"])]
            reach = _attribute_visible(label, [r["id"] for r in rows], facets)
            for r in rows:
                if r["id"] in reach:
                    main.append(_hit(label, r, certified_flag=r.get("certified")))
            continue

        extra = ["table_id"] if label == Labels.COLUMN else []
        for r in _read(label, tokens, include_description, extra):
            if facets.tag_ok(label, r["id"]) and facets.data_ok(label, r):
                main.append(_hit(label, r))

    if Labels.TABLE in object_types or SEARCH_TYPE_VIEW in object_types:
        wants_table, wants_view = Labels.TABLE in object_types, SEARCH_TYPE_VIEW in object_types
        for r in _read(Labels.TABLE, tokens, include_description, ["table_type", "schema_id"]):
            if not (wants_table and wants_view) and is_view_table_type(r.get("table_type")) != wants_view:
                continue
            if facets.tag_ok(Labels.TABLE, r["id"]) and facets.data_ok(Labels.TABLE, r):
                main.append(_hit(Labels.TABLE, r, table_type=r.get("table_type")))
    return main, alias_only


def _certified(row: dict[str, Any]) -> str:
    both = bool(row.get("name_certified")) and bool(row.get("description_certified"))
    either = bool(row.get("name_certified")) or bool(row.get("description_certified"))
    return "certified" if both else "partial" if either else "pending"


# -- ranking and breadcrumbs --------------------------------------------------

def _rank(hit: dict[str, Any], tokens: list[str]):
    name = (hit["name"] or "").lower()
    bucket = 0 if _contains_all(hit["name"], tokens) else 1 if hit["synonym_hit"] else 2
    return (bucket, len(name), name, hit["id"])


def _crumb(id_: str, name: str | None, label: str):
    return {"id": id_, "name": name, "type": label} if name else None


def _breadcrumbs(hits: list[dict[str, Any]]) -> dict[str, list[dict]]:
    ids: dict[str, list[str]] = {}
    for h in hits:
        ids.setdefault(h["label"], []).append(h["id"])
    out: dict[str, list[dict]] = {}

    def rows(table, items, cols):
        return {r["id"]: r for r in _gov.rows_by_ids(table, items, ["name", *cols])}

    columns = rows("catalog_column", ids.get(Labels.COLUMN, []), ["table_id"])
    tables = rows("catalog_table", set(ids.get(Labels.TABLE, [])) | {c.get("table_id") for c in columns.values() if c.get("table_id")}, ["schema_id"])
    schemas = rows("catalog_schema", set(ids.get(Labels.SCHEMA, [])) | {t.get("schema_id") for t in tables.values() if t.get("schema_id")}, ["database_id"])
    dbs = rows("catalog_database", {s.get("database_id") for s in schemas.values() if s.get("database_id")}, [])

    def db_schema(schema_id):
        schema = schemas.get(schema_id)
        db = dbs.get(schema.get("database_id")) if schema else None
        if not schema or not db:
            return None
        return [c for c in (_crumb(db["id"], db["name"], Labels.DB), _crumb(schema["id"], schema["name"], Labels.SCHEMA)) if c]

    for cid in ids.get(Labels.COLUMN, []):
        col = columns.get(cid)
        table = tables.get(col.get("table_id")) if col else None
        path = db_schema(table.get("schema_id")) if table else None
        if path is not None:
            out[cid] = [*path, *([_crumb(table["id"], table["name"], Labels.TABLE)] if table["name"] else [])]
    for tid in ids.get(Labels.TABLE, []):
        table = tables.get(tid)
        path = db_schema(table.get("schema_id")) if table else None
        if path is not None:
            out[tid] = path
    for sid in ids.get(Labels.SCHEMA, []):
        schema = schemas.get(sid)
        db = dbs.get(schema.get("database_id")) if schema else None
        if db:
            out[sid] = [c for c in [_crumb(db["id"], db["name"], Labels.DB)] if c]

    facets = _Facets([], False, [])
    for label in _ATTRIBUTE_LINKS:
        picked = _attribute_visible(label, ids.get(label, []), facets)
        names = {r["id"]: r for r in _gov.rows_by_ids("term", set(picked.values()), ["name"])}
        for aid, tid in picked.items():
            crumb = _crumb(tid, names.get(tid, {}).get("name"), LABEL_TERM)
            out[aid] = [crumb] if crumb else []
    return out


def _enrich(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    crumbs = _breadcrumbs(rows)
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "description": r["description"],
            "label": r["label"],
            "table_type": r["table_type"],
            "certified": r["certified"] if r["certified"] is not None else r["certified_flag"],
            "breadcrumbs": crumbs.get(r["id"], []),
            "synonyms": r["synonyms"] or [],
        }
        for r in rows
    ]


# -- the three entry points ---------------------------------------------------

def fetch_global_search(
    tokens: list[str],
    object_types: set[str],
    *,
    include_description: bool,
    synonym_tokens: list[str] | None = None,
    tag_ids: list[str] | None = None,
    include_untagged: bool = False,
    data_ids: list[str] | None = None,
    limit: int = LIST_LIMIT,
    synonym_limit: int | None = None,
) -> list[dict[str, Any]]:
    if not tokens or not object_types:
        return []
    main, alias_only = _collect(
        tokens, object_types, include_description, synonym_tokens or [], tag_ids or [], include_untagged, data_ids or []
    )
    main.sort(key=lambda h: _rank(h, tokens))
    rows = main[: max(limit, 0)]
    alias_only.sort(key=lambda h: _rank(h, tokens))
    if synonym_limit is not None:
        alias_only = alias_only[: max(synonym_limit, 0)]
    rows += alias_only
    seen: set[str] = set()
    unique = [r for r in rows if not (r["id"] in seen or seen.add(r["id"]))]
    return _enrich(unique)


def count_global_search(
    tokens: list[str],
    object_types: set[str],
    *,
    include_description: bool,
    synonym_tokens: list[str] | None = None,
    tag_ids: list[str] | None = None,
    include_untagged: bool = False,
    data_ids: list[str] | None = None,
) -> dict[str, int]:
    if not tokens or not object_types:
        return {}
    main, alias_only = _collect(
        tokens, object_types, include_description, synonym_tokens or [], tag_ids or [], include_untagged, data_ids or []
    )
    out: dict[str, int] = {}
    seen: set[tuple[str, str]] = set()
    for h in [*main, *alias_only]:
        if (h["label"], h["id"]) in seen:
            continue
        seen.add((h["label"], h["id"]))
        key = search_object_type(h["label"], h["table_type"])
        if key:
            out[key] = out.get(key, 0) + 1
    return out


def matching_id_selects(
    tokens: list[str],
    object_types: set[str],
    *,
    include_description: bool,
    synonym_tokens: list[str] | None = None,
    tag_ids: list[str] | None = None,
    include_untagged: bool = False,
    data_ids: list[str] | None = None,
) -> dict[str, list[str]]:
    """``{label: [ids]}``: uncapped, unranked, aliases folded into the Term match."""
    if not tokens or not object_types:
        return {}
    main, _ = _collect(
        tokens, object_types, include_description, synonym_tokens or [], tag_ids or [], include_untagged, data_ids or [],
        aliases_in_match=True,
    )
    out: dict[str, list[str]] = {}
    # a label that was asked for and is not ruled out has an entry even when empty
    for label in _SEARCH_LABELS:
        if label == Labels.TABLE:
            continue
        if label in object_types and not _tag_rules_out(label, tag_ids or [], include_untagged) and not _data_rules_out(label, data_ids or []):
            out[label] = []
    if Labels.TABLE in object_types or SEARCH_TYPE_VIEW in object_types:
        out[Labels.TABLE] = []
    for h in main:
        out[h["label"]].append(h["id"])
    return out

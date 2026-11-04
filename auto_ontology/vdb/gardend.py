# SPDX-License-Identifier: Apache-2.0
"""The vector store on gardend: no vectors leave the graph.

``PostgresVDB`` stores embedded texts with metadata in pgvector and does a
filtered k-NN. Here the same records become triples on their catalog row
(``dcterms:description`` carries the embed text, ``urn:ao:vdb#metadata`` the
record's metadata as JSON, ``urn:ao:vdb#collection`` the collection name), and
retrieval is gardend's ``find`` tool (under ``NIDRA_WRITES=commit`` the three
predicates are ledger ops on the same subjects, so a replay or a rebuild of the
fold keeps them: the ledger owns every catalog row there, and a row's index
triples go with it): every entity verbalized into one sentence
and embedded locally with multilingual-e5-small, searched by meaning and by
words. Nothing here calls an embedding endpoint, so the ``EMBED_*`` triplet is
not needed on this backend; ``Retriever`` passes the query text instead of a
vector when the store ``embeds_itself``.

Filters: ``where["label"]`` becomes ``find``'s ``types`` filter (the class as
``find`` labels it, e.g. ``"catalog column"``); other keys are matched against
the stored metadata after the search. ``find`` returns at most 25 hits per
call; ``top_k`` above that is capped and logged.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Iterable

from auto_ontology.rdf.client import GardendError, client, iri, lit, values, wrap
from auto_ontology.rdf.vocab import BASE, DCT_DESCRIPTION, class_iri, parse_row_iri, row_iri_single

logger = logging.getLogger(__name__)

VDB_NS = "urn:ao:vdb#"
P_METADATA = f"{VDB_NS}metadata"
P_COLLECTION = f"{VDB_NS}collection"
P_TEXT = DCT_DESCRIPTION
_FIND_MAX = 25

# -- score -> distance --------------------------------------------------------
#
# The engine reads ``_distance`` (lower is better) against CLARIFY_MAX_DISTANCE
# 0.65 and DEFAULT_MAX_DISTANCE 0.75. ``1 - score`` cannot meet them: ``find``'s
# fused score is 1.0 for a word-for-word hit and is *capped at 0.70* for a hit
# found by meaning alone, so nonsense sat at distance 0.30 and everything
# "was in the schema" (``check_answerable`` could never say no).
#
# Measured on the Pagila/Chinook catalog fixture (274 entities, e5-small, k=25),
# top hit per query, ``find`` default = ``mode="fused"``:
#   10 relevant (column/term names and their descriptions: "film title",
#      "customer email address", "rental date", "payment amount", "replacement
#      cost of a film", "A motion picture available for rental.", "A person who
#      rents films from a store.", "Primary key of customer.", "store
#      inventory", "film category"):  fused 0.94 .. 1.00 (median 1.00);
#                                     vector 0.86 .. 0.91 (median 0.89)
#   10 nonsense ("pension plan contributions", "weather in Lisbon", "quantum
#      chromodynamics lecture", "how to bake sourdough bread", "football world
#      cup winner", "medieval castle architecture", "stock market volatility
#      index", "volcano eruption in Iceland", "guitar chord progressions", "tax
#      return deadline for freelancers"):  fused 0.70 .. 0.70 (all ten; the cap)
#                                          vector 0.75 .. 0.83 (median 0.775)
# Fused separates the two groups by 0.24; vector by 0.03 (relevant min 0.86
# against nonsense max 0.83), too thin to threshold. So the mapping is on the
# fused score and linear: distance = 1.40 - 0.85 * score, clamped at 0.
#   score 1.00 -> 0.55   0.94 -> 0.60   (relevant: below 0.65)
#   score 0.70 -> 0.805  0.50 -> 0.975  (nonsense: above 0.75)
# 0.65 and 0.75 correspond to scores 0.882 and 0.765; between them lies the zone
# the group measurements left empty. Limit: a paraphrase with no word in common
# ("client mailing contact") is capped at 0.70 like nonsense, and the vector
# score does not rescue it (0.84 against nonsense up to 0.83): such a query is
# judged not answerable by the thresholds.
_DISTANCE_INTERCEPT = 1.40
_DISTANCE_SLOPE = 0.85


def distance_from_score(score: float) -> float:
    """The engine's distance (lower is better) for a ``find`` fused score; monotone decreasing."""
    return max(0.0, _DISTANCE_INTERCEPT - _DISTANCE_SLOPE * float(score))

#: AO catalog labels → the DM table whose rows carry them.
LABEL_TABLE = {
    "Database": "catalog_database",
    "Schema": "catalog_schema",
    "Table": "catalog_table",
    "Column": "catalog_column",
    "Sql": "sql_query",
    "CustomAnalysis": "custom_analysis",
    "SqlAttribute": "sql_attribute",
    "Term": "term",
    "ColumnAttribute": "column_attribute",
    "PqlAnalysis": "pql_analysis",
}
TABLE_LABEL = {v: k for k, v in LABEL_TABLE.items()}


def _find_type(table: str) -> str:
    """How ``find`` labels a class: the local name with spaces."""
    return table.replace("_", " ")


def _flatten(records: Iterable[Any]) -> Iterable[dict[str, Any]]:
    for record in records:
        if isinstance(record, list):
            yield from _flatten(record)
        elif isinstance(record, dict):
            yield record


def _ledger_mode() -> bool:
    from auto_ontology.rdf import writes

    return writes.write_mode() == "commit"


def _index_ops(kind: str, subject: str, by_predicate: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """``add``/``remove`` ops for one index subject: its class is its row's table
    (``urn:ao:<table>``), or ``urn:ao:vdb`` for a record with no catalog row."""
    table, _ = parse_row_iri(subject)
    cls = class_iri(table)
    return [
        {"op": kind, "subject": subject, "class": cls, "predicate": predicate, "values": list(terms)}
        for predicate, terms in by_predicate.items()
        if terms
    ]


class GardendVDB:
    """One collection of embedded records, held in the catalog graph."""

    embeds_itself = True
    #: The retriever builds filters as a flat dict for this store
    #: ({"label": "Column" | [...], "database_name": ..., "schema_name": ...}).
    metadata_filter_format = "dict"

    def __init__(self, *, collection_name: str, database_name: str | None = None, reset: bool = False, **_: Any) -> None:
        self.collection = collection_name
        self.database_name = database_name
        self._client = client()
        if reset and database_name:
            self.delete_by_database(database_name)

    # -- writes ---------------------------------------------------------------

    def create_index(self, **kwargs: Any) -> str:
        return self.collection

    def _subject_for(self, metadata: dict[str, Any]) -> str:
        node_id = str(metadata.get("id") or "")
        table = LABEL_TABLE.get(str(metadata.get("label") or ""))
        if node_id and table:
            return row_iri_single(table, node_id)
        # A record without a catalog row: its own resource in the vdb namespace.
        return f"{BASE}vdb/{self.collection}/{node_id or abs(hash(json.dumps(metadata, sort_keys=True, default=str)))}"

    def write_to_index(self, records: list, batch_size: int = 500, **kwargs: Any) -> int:
        from auto_ontology.rdf import writes

        ledger = writes.write_mode() == "commit"
        lines: list[str] = []
        ops: list[dict[str, Any]] = []
        written = 0
        for record in _flatten(records):
            metadata = {k: v for k, v in (record.get("metadata") or {}).items() if k != "embedding"}
            text = record.get("text") or record.get("content") or metadata.get("content")
            if not text:
                continue
            meta = {"collection": self.collection, "metadata": metadata}
            if ledger:
                # The same three values the load below writes, added (a catalog row's
                # description mirror and its index text share dcterms:description).
                subject_iri = self._subject_for(metadata)
                ops.extend(
                    _index_ops("add", subject_iri, {P_TEXT: [writes.term(str(text))], P_METADATA: [writes.term(meta)], P_COLLECTION: [writes.term(self.collection)]})
                )
            else:
                subject = iri(self._subject_for(metadata))
                lines.append(f"{subject} {iri(P_TEXT)} {lit(str(text))} .")
                lines.append(f"{subject} {iri(P_METADATA)} {lit(meta)} .")
                lines.append(f"{subject} {iri(P_COLLECTION)} {lit(self.collection)} .")
            written += 1
            if len(lines) >= batch_size * 3:
                self._client.load("\n".join(lines) + "\n")
                lines = []
            if len(ops) >= batch_size * 3:
                writes.commit(ops)
                ops = []
        if lines:
            self._client.load("\n".join(lines) + "\n")
        if ops:
            writes.commit(ops)
        logger.info("GardendVDB.write_to_index: %d record(s) into %s", written, self.collection)
        return written

    def run(self, records: list) -> int:
        self.create_index()
        return self.write_to_index(records)

    def _delete_where(self, extra: str) -> int:
        g = iri(self._client.graph_id and f"urn:mnemosyne:local:graph:{self._client.graph_id}:user:rdf")
        count = int(
            self._client.scalar(
                f"SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE {{ {wrap(f'?s {iri(P_COLLECTION)} {lit(self.collection)} . {extra}')} }}",
                "n",
            )
            or 0
        )
        if count and _ledger_mode():
            # The ledger owns these values: remove exactly the ones stored (as the
            # DELETE below removes every value of the three predicates there).
            from auto_ontology.rdf import writes

            found = self._client.select_raw(
                f"SELECT ?s ?p ?o WHERE {{ GRAPH {g} {{ ?s {iri(P_COLLECTION)} ?c . FILTER(?c = {lit(self.collection)}) {extra} "
                f"?s ?p ?o . FILTER(?p IN ({iri(P_TEXT)}, {iri(P_METADATA)}, {iri(P_COLLECTION)})) }} }}"
            )
            by_subject: dict[str, dict[str, list[Any]]] = {}
            for r in found:
                if r["o"].startswith("_:"):
                    continue
                by_subject.setdefault(r["s"][1:-1], {}).setdefault(r["p"][1:-1], []).append(writes.nt_term(r["o"]))
            ops = [op for subject, preds in by_subject.items() for op in _index_ops("remove", subject, preds)]
            if ops:
                writes.commit(ops)
        elif count:
            self._client.update(
                f"DELETE {{ GRAPH {g} {{ ?s {iri(P_TEXT)} ?t . ?s {iri(P_METADATA)} ?m . ?s {iri(P_COLLECTION)} ?c }} }} "
                f"WHERE {{ GRAPH {g} {{ ?s {iri(P_COLLECTION)} ?c . FILTER(?c = {lit(self.collection)}) {extra} "
                f"OPTIONAL {{ ?s {iri(P_TEXT)} ?t }} OPTIONAL {{ ?s {iri(P_METADATA)} ?m }} }} }}"
            )
        return count

    def delete_by_database(self, database_name: str) -> list[str]:
        rows = self._client.select(
            f"SELECT ?s ?m WHERE {{ {wrap(f'?s {iri(P_COLLECTION)} {lit(self.collection)} ; {iri(P_METADATA)} ?m .')} }}"
        )
        hits = [r["s"] for r in rows if (r.get("m") or {}).get("metadata", {}).get("database_name") == database_name]
        if hits:
            self._delete_where(values("s", hits))
        return hits

    def delete_all(self) -> int:
        return self._delete_where("")

    def delete_by_id(self, node_id: str) -> int:
        rows = self._client.select(
            f"SELECT ?s ?m WHERE {{ {wrap(f'?s {iri(P_COLLECTION)} {lit(self.collection)} ; {iri(P_METADATA)} ?m .')} }}"
        )
        hits = [r["s"] for r in rows if str((r.get("m") or {}).get("metadata", {}).get("id")) == str(node_id)]
        return self._delete_where(values("s", hits)) if hits else 0

    # -- reads ----------------------------------------------------------------

    def retrieval(self, queries: list, top_k: int = 10, **kwargs: Any) -> list[list[dict]]:
        """k-NN by *text* (``kwargs["texts"]``); the vectors in ``queries`` are ignored."""
        texts = kwargs.get("texts") or []
        raw_where = kwargs.get("where") or {}
        where = dict(raw_where) if isinstance(raw_where, dict) else {}
        if raw_where and not isinstance(raw_where, dict):
            logger.warning("GardendVDB.retrieval: ignoring non-dict filter %r", str(raw_where)[:120])
        label = where.pop("label", None)
        labels = label if isinstance(label, list) else ([label] if label else [])
        types = [_find_type(LABEL_TABLE[lab]) for lab in labels if lab in LABEL_TABLE] or None
        # Over-fetch: find ranks every entity of the graph, and rows of other
        # collections or filtered-out metadata are dropped below.
        k = _FIND_MAX
        if top_k > _FIND_MAX:
            logger.info("GardendVDB.retrieval: top_k %d capped at %d (find's limit)", top_k, _FIND_MAX)
        results: list[list[dict]] = []
        for text in texts:
            try:
                args: dict[str, Any] = {"graphId": self._client.graph_id, "query": text, "k": k}
                if types:
                    args["types"] = types
                found = self._client.mcp_call("find", args)
                hits = found.get("hits") or []
                subjects = [h["iri"] for h in hits]
                meta_by_s: dict[str, dict] = {}
                if subjects:
                    for r in self._client.select(
                        f"SELECT ?s ?m ?t WHERE {{ {wrap(f'{values(chr(115), subjects)} ?s {iri(P_COLLECTION)} {lit(self.collection)} ; {iri(P_METADATA)} ?m . OPTIONAL {{ ?s {iri(P_TEXT)} ?t }}')} }}"
                    ):
                        meta_by_s[r["s"]] = {"metadata": (r.get("m") or {}).get("metadata", {}), "text": r.get("t") or ""}
                out: list[dict] = []
                for h in hits:
                    stored = meta_by_s.get(h["iri"])
                    if stored is None:
                        continue  # a row of another collection, or never embedded
                    metadata = stored["metadata"]
                    if any(str(metadata.get(key)) != str(value) for key, value in where.items()):
                        continue
                    out.append({"text": stored["text"] or h.get("says", ""), "metadata": metadata, "_distance": distance_from_score(h.get("score", 0.0))})
                results.append(out[:top_k])
            except GardendError as exc:
                logger.error("GardendVDB.retrieval: %s", exc)
                results.append([])
        return results

    def close(self) -> None:
        return None

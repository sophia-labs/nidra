# SPDX-License-Identifier: Apache-2.0
"""Postgres catalog → N-Triples, by Direct Mapping of upstream's own metadata.

Every table in ``auto_ontology.dal.schema.METADATA`` is exported: a row is a
resource typed by its table, every non-NULL column is a literal property, and
every foreign-key column also yields a ``ref-`` property to the referenced row.
Arrays are exported twice: once as an ordered JSON literal (lossless) and once
as one triple per member (matchable). Nothing here knows a table by name, so an
upstream schema change is exported without a change here.
"""
from __future__ import annotations

import logging
import sys
from typing import Iterator

from sqlalchemy import select

from auto_ontology.dal.schema import METADATA
from auto_ontology.dal.session import store
from auto_ontology.rdf.client import iri, lit
from auto_ontology.rdf.vocab import (
    DCT_DESCRIPTION,
    DESCRIPTION_COLUMNS,
    LABEL_COLUMNS,
    RDF_TYPE,
    SKOS_PREF_LABEL,
    class_iri,
    prop_iri,
    ref_iri,
    row_iri,
)

logger = logging.getLogger(__name__)


def _triple(s: str, p: str, o: str) -> str:
    return f"{iri(s)} {iri(p)} {o} ."


def export_table(table_name: str) -> Iterator[str]:
    table = METADATA.tables[table_name]
    pk_cols = [c.name for c in table.primary_key.columns]
    if not pk_cols:
        # Direct Mapping would use blank nodes; a hash of all columns keeps it
        # addressable. No catalog table lacks a key today.
        pk_cols = [c.name for c in table.columns]
    fk_by_col = {
        fk.parent.name: (fk.column.table.name, fk.column.name) for fk in table.foreign_keys
    }
    rows = store().query_read(select(table))
    for row in rows:
        subject = row_iri(table_name, {c: row[c] for c in pk_cols})
        yield _triple(subject, RDF_TYPE, iri(class_iri(table_name)))
        for column, value in row.items():
            if value is None:
                continue
            prop = prop_iri(table_name, column)
            yield _triple(subject, prop, lit(value))
            if column in LABEL_COLUMNS and isinstance(value, str) and value:
                yield _triple(subject, SKOS_PREF_LABEL, lit(value))
            if column in DESCRIPTION_COLUMNS and isinstance(value, str) and value:
                yield _triple(subject, DCT_DESCRIPTION, lit(value))
            if isinstance(value, list):
                for member in value:
                    yield _triple(subject, prop, lit(member))
            if column in fk_by_col:
                ref_table, ref_col = fk_by_col[column]
                yield _triple(subject, ref_iri(table_name, column), iri(row_iri(ref_table, {ref_col: value})))


def export_all(tables: list[str] | None = None) -> Iterator[str]:
    names = tables or [t.name for t in METADATA.sorted_tables]
    for name in names:
        n = 0
        for line in export_table(name):
            n += 1
            yield line
        logger.info("%s: %d triples", name, n)


def load(lines: list[str], *, replace: bool = False) -> int:
    """Seed the current graph with Direct-Mapping N-Triples.

    ``NIDRA_WRITES=sparql``: ``/api/rdf/load`` (``replace`` clears the graph first).
    ``NIDRA_WRITES=commit``: every ao subject must be ledger-owned, so the rows go
    through the commit seam as ``set`` ops (``writes.ntriples_ops``). ``replace``
    reads the ao rows present, clears the graph, commits their retraction together
    with the new rows, then rebuilds the sink from the ledger (``replay`` into
    ``live``, the rule after a SPARQL ``CLEAR``).
    """
    from auto_ontology.rdf import writes
    from auto_ontology.rdf.client import client, wrap

    c = client()
    graph = f"<urn:mnemosyne:local:graph:{c.graph_id}:user:rdf>"
    if writes.write_mode() != "commit":
        if replace:
            # Loaded triples live in the graph's authored-RDF named graph; a
            # variable GRAPH target is refused in local authority mode.
            c.update(f"CLEAR SILENT GRAPH {graph}")
        return c.load_lines(lines)
    ops: list[dict] = []
    if replace:
        tables = {class_iri(t.name): t.name for t in METADATA.sorted_tables}
        present = c.select(f"SELECT DISTINCT ?s ?c WHERE {{ {wrap('?s a ?c')} }}")
        ops = [writes.retract_op(tables[r["c"]], r["s"]) for r in present if r["c"] in tables]
        c.update(f"CLEAR SILENT GRAPH {graph}")
    ops.extend(writes.ntriples_ops(lines))
    writes.commit(ops)
    if replace:
        response = c._http.post(f"/emporium/fold/{c.graph_id}/ao/replay", json={"into": "live"})
        if response.status_code != 200:
            raise RuntimeError(f"replay into live: HTTP {response.status_code}: {response.text[:300]}")
    return len(lines)


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    if "--load" in argv:
        from auto_ontology.rdf.client import client

        c = client()
        c.ensure_graph()
        total = load(list(export_all()), replace="--replace" in argv)
        logger.info("loaded %d triples into %s/%s (%d quads now)", total, c.url, c.graph_id, c.count())
        return 0
    for line in export_all():
        sys.stdout.write(line + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

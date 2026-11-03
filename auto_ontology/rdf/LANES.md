# Porting lanes: the agent's catalog reads, SQL → SPARQL

You port one group of `auto_ontology.dal.<module>` read functions to
`auto_ontology/dal_rdf/<module>.py`. Same name, same signature, same return
shape (keys, value types, list order), same edge behaviour (empty input → the
same empty value; the same `try/except` fallbacks). The SQL is the spec: read the
upstream function, write the SPARQL equivalent over the Direct-Mapping
vocabulary. Nothing in `auto_ontology/dal/` is edited.

## Read first
- `auto_ontology/rdf/README.md`, `vocab.py` (IRIs), `client.py` (`select`,
  `wrap`, `values`, `lit`, `iri`, term parsing), `dal_rdf/_common.py` (`P`,
  `select_rows`), `dal_rdf/terms.py` (`fetch_term_synonyms`, a finished port).
- `auto_ontology/dal/schema.py` for the tables your functions join.

## Vocabulary cheatsheet (Direct Mapping of `dal/schema.py`)
- row of table `T` with `id=X`: `<urn:ao:T/id=X>` typed `<urn:ao:T>`; composite
  keys: `<urn:ao:T/a=X;b=Y>` (link tables such as `column_attribute__term`).
- literal column `c`: `<urn:ao:T#c>`; absent when NULL → use `OPTIONAL`.
- foreign-key column `c`: also `<urn:ao:T#ref-c>` → the referenced row IRI. In
  `_common.P`, `P.T.c` is the literal property and `P.T.ref_c` the reference.
- `ARRAY(Text)` columns: one JSON literal (datatype `rdf:JSON`, parsed to a
  Python list, order kept) **and** one plain-string triple per member. Use the
  JSON one when the DAL returns the list, the member ones to match.
- `JSONB` columns: a JSON literal (parsed to dict/list). Booleans, integers,
  datetimes are typed literals and come back as Python `bool`/`int`/`datetime`.
- The id of a row IRI: `row_id(iri)`; build one: `row_iri_single("T", id)`.
- Every pattern inside `GRAPH ?g { ... }` (`wrap()`); an unwrapped pattern
  returns nothing. SPARQL results are unordered: add `ORDER BY` wherever the
  SQL orders, and sort in Python where the SQL relies on insertion order.
- Aggregates, `GROUP_CONCAT`, property paths (`(p1/p2)+`) are available
  (Oxigraph, SPARQL 1.1). `VALUES` for `IN (...)` lists.

## The oracle
Postgres and the gardend graph both hold the same fixture. From your worktree:

    set -a; . ./lane.env; set +a
    uv sync --quiet
    uv run --no-sync pytest auto_ontology/dal/tests/test_golden.py -q                  # Postgres: must stay green
    CATALOG_READ_BACKEND=rdf uv run --no-sync pytest auto_ontology/dal/tests/test_golden.py -q -k "<module>"

A read with no golden entry yet: add a capture in
`dev_tools/fixtures/golden_extra/<lane>.py` (`capture(cap, ids)`; see
`capture_arg_reads` in `dev_tools/fixtures/capture_dal_golden.py` for the fixture
ids and the naming), record it from **Postgres** with

    uv run --no-sync python -m dev_tools.fixtures.capture_dal_golden --only <module>.<function> --out dal_reads.<lane>.json

then make the RDF backend pass it. Cover the empty-input and not-found paths too.
Finish with `CATALOG_READ_BACKEND=rdf AO_RDF_STRICT=0 ... -k "<module>"` green and
the Postgres run green, commit on your branch, and report: functions ported,
captures added, anything the vocabulary could not express.

## Rules
- No mocks, no fakes: the tests run against the real Postgres and gardend.
- Do not edit `auto_ontology/dal/*.py`, `auto_ontology/rdf/*`, `_common.py`, or
  another lane's files. Need a shared helper? Put it in your module and say so.
- No builds, no docker, no `uv sync` beyond your own worktree, no writes to
  gardend or Postgres (reads only; the fixture is shared).
- Never touch `.env`/`lane.env` and never print their values.

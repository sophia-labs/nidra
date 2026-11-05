# Nidra

Nidra is NVIDIA Auto Ontology's engine, the semantic layer and the text-to-SQL
agent, running on the Sophia engine (gardend) instead of Postgres: the catalog,
the glossary, the SQL attributes and the vector index are one RDF graph.

Nidra is built on NVIDIA Auto Ontology (Apache-2.0). Sophia Labs is not
affiliated with NVIDIA.

## Run it

One command (macOS or Linux, with Docker running):

    curl -fsSL https://nidra.computer/install.sh | sh

On Windows, in PowerShell:

    irm https://nidra.computer/install.ps1 | iex

Both ask for an OpenRouter key (or take `OPENROUTER_API_KEY` from the
environment), start one container and print the link to open. Or run the
container yourself:

    docker run -d --name nidra --restart unless-stopped \
      -p 127.0.0.1:8086:8086 -p 127.0.0.1:3001:3001 -v nidra:/data \
      -e OPENROUTER_API_KEY=sk-or-... ghcr.io/sophia-labs/nidra
    docker exec nidra nidra url     # the link to open (it holds the key)

`ghcr.io/sophia-labs/nidra` (`Dockerfile.nidra`) holds the engine, gardend with
its embedding model, and the Sophia UI with the Nidra screens; everything that
lasts is in the `/data` volume. Model settings: `OPENROUTER_API_KEY` alone
means Claude Sonnet through OpenRouter; `NIDRA_MODEL` picks another model;
`DEFAULT_MODELS_ENDPOINT`, `DEFAULT_MODELS_API_KEY` and `DEFAULT_MODELS_MODEL`
name any OpenAI-compatible endpoint instead. `CONNECTION_STRINGS` names your
own databases (below). `NIDRA_URL` is the address printed in the link (default
`http://localhost:8086`).

To build the image: `nidra/oneliner/stage-ui.sh` (copies the built Sophia UI
into `nidra/oneliner/ui`), then `docker build -f Dockerfile.nidra -t
ghcr.io/sophia-labs/nidra .`

**The advanced way, with compose** (the engine and gardend as separate
containers, from the download or this checkout):

    docker load -i nidra-gardend-<date>.tar.gz  # the Sophia engine image, with the embedding model baked in
    tar xzf nidra-local-<date>.tar.gz && cd nidra-local-<date>
    cp .env.example .env                         # set DEFAULT_MODELS_* (any OpenAI-compatible chat model;
                                                 # OpenRouter works) and GARDEND_TOKEN; no embedding endpoint
    docker compose -f compose.nidra.yaml up -d   # builds the engine image on first run
    curl -s http://127.0.0.1:3001/api/health

The engine answers on :3001 (its API is the upstream FastAPI surface; the
front door that authenticates and routes is Sophia's gateway or your Sophia
app, which forwards `x-auto-ontology-user-id`; the engine has no login of its own, so keep
:3001 on 127.0.0.1, as the commands above do). Ask a question:

    curl -s -X POST http://127.0.0.1:3001/api/chat/completions \
      -H 'content-type: application/json' -H 'x-auto-ontology-user-id: me' \
      -d '{"question": "How many tracks did each artist sell last year?"}'

The sample source is Chinook (SQLite, `nidra/data`). Point `CONNECTION_STRINGS`
at your own warehouse (Postgres, Snowflake, Databricks, DuckDB, MySQL, Trino,
ClickHouse, SQLite) to catalogue it; the ingestion service sweeps it every
`INGEST_INTERVAL_HOURS`.

**Attached to your own Sophia app** (the local edition): run without the
`gardend` service (`--scale gardend=0`) and set `GARDEND_URL` to the app's
loopback API (`http://host.docker.internal:7674`), `GARDEND_TOKEN` to a token
from `gardend token create --grant session-all`, `GARDEND_GRAPH_ID` to the
graph you want the catalog in. Turn `find` on for that graph with
`gardend index --graph <id>`. Until the app's gardend carries the Emporium
ledger, also set `NIDRA_WRITES=sparql` (below); `nidra/service/compose.sophia.yaml`
does this for the Nidra service inside Sophia.app.

## Writes: SPARQL or the ledger (`NIDRA_WRITES`)

Every catalog write goes through one seam, `auto_ontology/rdf/writes.py`
`commit(ops, witness, origin, txn)`. A write is a list of ops in the wire form of
gardend's Emporium commit API (`set` with `values[]`, `add`, `remove`,
`retract`), built from the Direct Mapping by `row_ops` / `update_ops` /
`delete_ops` (a cascade is explicit ops: the ledger knows no foreign keys).
`NIDRA_WRITES` (read once at start) picks the transport:

- `commit` (**the default** since 11-05, when the ledger, W7d `7968ea6`, went to
  gardend main): the ops go to `POST /emporium/commit/{graph}/ao?wait=folded`. The
  ledger appends one frame and acks on its durability barrier (the 1 s window);
  the fold applies it to the graph every read uses, and `wait=folded` gives
  read-your-writes. A list column is rewritten whole by one `set values[]`
  (its `rdf:JSON` value plus one value per member). The engine and the ingestion
  service probe `GET /emporium/commit/{graph}/ao/watermark` at start and refuse
  to start against a gardend without the ledger (`LedgerUnavailable`).
- `sparql` (**the opt-out**, for a gardend without the ledger, such as Sophia.app's
  today): the ops are rendered to one SPARQL update, applied atomically by
  Oxigraph. Works with any gardend. Keep one mode per graph: under `sparql` the
  engine warns at start when the ledger already owns the graph's catalog, because
  values SPARQL then changes on those rows are not adopted when `commit` returns.

Identity: the body's `writer` is `NIDRA_ORIGIN` (default `nidra-engine`;
tenant-stable, never a cell or container id); the `witness` is the caller in
`x-auto-ontology-user-id` (`WitnessMiddleware`), else `NIDRA_WITNESS`, else the
writer.

**In commit mode every catalog subject is ledger-owned.** The fold owns only the
subjects it holds frames for: probed on the ledger binary (`3bf51b9`), a value
loaded by `/api/rdf/load` survives a later commit `set` beside the new value, a
`remove` of a loaded value is a no-op, and a `retract` of a loaded subject
removes nothing. So in commit mode a graph is seeded through commit too
(`rdf.export.load`, used by `--load` and `write_diff`, sends `set` ops), and
SPARQL load is a sparql-mode tool.

**Adoption: a graph written by SPARQL comes under the ledger once**
(`auto_ontology/rdf/adopt.py`). An older Nidra volume, or a graph seeded under
`sparql`, holds catalog rows the ledger has never seen. At the API server's start
under `commit`, the ledger adopts them:

- *What moves:* every subject of `user:rdf` typed with a catalog class, and every
  vector-index record with no row (`urn:ao:vdb/...`): all their triples but
  `rdf:type` become `set` ops holding exactly the values stored (columns, mirrors,
  `#ref-` links, and the index's `dcterms:description`, `vdb#metadata`,
  `vdb#collection`). Nothing else is read: Emporium testimony, wires,
  corrections, the Nidra service's questions and every other non-row subject stay
  as they are, and so do their links to rows.
- *Order, and no deletes:* read the rows (cursor-paged), commit them in **one
  transaction** (`nidra-adopt-<hex>`, split into requests of at most 32 MiB),
  wait for the fold, check the ledger's oracle. The fold writes into the same
  graph, so the triples it folds in are the ones already there: nothing is cleared
  or deleted, the graph reads the same throughout, and an adoption that dies
  leaves an unclosed run the fold never applies (the next start adopts again).
- *Once:* the start check counts the adoptable subjects against the subjects the
  ledger owns (the fold's status). A second start finds nothing and logs
  `nothing to adopt (N ao subjects; the ledger owns N)`; an adoption logs how many
  subjects (catalog rows, index records) and values moved, the transaction, the
  oracle and the graph's size before and after. If the ledger owns some subjects
  but fewer than the graph has (rows written by SPARQL beside commits), the fold's
  `heads` route says which are not owned and only those move.
- *Concurrency:* only the API server adopts (`startup_check(adopt=True)`). The
  ingestion service waits until nothing is left to adopt, checking every 2 s for
  up to `NIDRA_ADOPT_WAIT_S` (default 900), then refuses to start; it never adopts
  and never writes into an unadopted graph. One API server per graph.
- *By hand:* `python -m auto_ontology.rdf.adopt` (`--check` reports without
  writing; `--every` asks the fold about every subject).
- *Not adoptable, logged and left:* a second `rdf:type`, a blank-node object
  (neither can be an op; a live replay of the fold drops them), and index triples
  on a row IRI that has no row (adopting would make it one).

**Transactions.** `write_transaction()` under commit collects the block's ops
and sends one request at the end (one frame, atomic); an exception discards
them. A read inside the block first sends what is pending, because a ledger
transaction's frames are invisible to reads until its commit lands. The model
import cannot be one transaction for the same reason (its SQL stage parses
against the catalog its earlier stages wrote), so it commits per stage after a
preflight and is **compensated**: before each write `writes.compensating()`
records the prior state of every subject the write touches; if a later stage
fails, one write retracts what the import created and sets what it changed back
to its prior values, and the error is raised. The ledger keeps the import and
its undo as lineage. (Under `sparql` the same compensation runs as SPARQL.)

**The vector index** (`auto_ontology/vdb/gardend.py`) hangs its text, metadata
and collection triples on the catalog rows, so `find` matches a row by them.
Under `commit` they are ledger ops on those rows (`add` beside the row's own
values; a delete `remove`s exactly the stored values), so a live replay or a fold
rebuild keeps them. Its text predicate is `dcterms:description`, the same as the
description mirror: a delete takes every description value of the row, as the
SPARQL delete always did. This needs gardend's W7d fold (an `add` beside a `set`
on one predicate).

**What stays on SPARQL, and why:** subjects the ledger does not own.
- The Nidra service's own records (`nidra/service/main.mjs`): questions
  (`urn:nidra:question/`), certifications (`urn:nidra:certification/`) and
  being registrations, which link to catalog rows but are not rows.
- After a commit `retract`, any triples left on the retracted subject and the
  links to it from non-catalog subjects (question records) are removed by one
  SPARQL update, as SPARQL mode's delete always did.
- `rdf.export --load --replace` in commit mode clears the graph by SPARQL, then
  commits the retraction of every catalog row it held with the new rows, then
  rebuilds the sink from the ledger (`replay` into `live`).

## How it is built

- `auto_ontology/rdf/`: the Direct Mapping of upstream's catalog schema, the
  gardend client, the write primitive, the backend switch.
- `auto_ontology/dal_rdf/`: every DAL read and write, ported to SPARQL, graded
  by upstream's own golden captures (`auto_ontology/dal/tests/golden/`) and by
  `dev_tools/fixtures/write_diff.py`.
- `auto_ontology/vdb/gardend.py`: the vector store on gardend's `find`.
- `nidra/gardend/`: the gardend release image plus ONNX Runtime.
- `Dockerfile.nidra`, `nidra/oneliner/`: the one-container image (`run.sh`
  supervises gardend, the ingestion service and the engine; the Nidra service
  `nidra/service/main.mjs` runs attached behind gardend) and the installers.
- `nidra/closure.sh`: the proof that nothing of the engine's state is in
  Postgres: seed the fixture with Postgres unreachable, replay the golden
  (through the ledger by default; `NIDRA_WRITES=sparql` for the SPARQL path).

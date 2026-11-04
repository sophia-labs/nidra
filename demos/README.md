# Nidra demos: Auto Ontology parity, and beyond it

Every demo is a script that runs against the live stack and writes what it saw to
`out/NN-name.txt` (header with the time and the claim, every request and answer,
`CHECK PASS/FAIL` lines, a final `STATUS`). Natural-language answers are graded
against the source database itself (SQLite for Chinook, Postgres for Pagila), not
against what the engine says about itself. Nothing below is rounded up: a demo
that half works says `partial`, and a demo the model could not run says
`not graded`.

The recorded outputs are from 2026-11-03, 20:18 to 21:00 (Pacific). They were run
one demo at a time, not in a single `run-all.sh` pass: the OpenRouter key in the
fork's `.env` ran out of credit during the evening (USD 40 total, USD -0.03 left at
21:00), and from about 20:25 the model calls started failing with HTTP 402
`in_flight_budget_exhausted`. Answers lost to that are marked "not graded (402)"
below; they say nothing about the engine.

## The stack the demos ran on

| piece | where |
| --- | --- |
| Nidra engine (Auto Ontology's FastAPI on gardend, no Postgres) | `nidra-engine`, http://127.0.0.1:3001, `CONNECTION_STRINGS` = Chinook only |
| ingestion service | container `nidra-ingestion` (:3002 inside; reached with `docker exec`) |
| graph | scratch gardend http://127.0.0.1:7690, graph `ao-catalog` (Pagila + Chinook catalogs, the hand-authored fixture glossary of 5 terms) |
| models | Sonnet 5.5 (reasoning) and Haiku 5.5 (extraction) through OpenRouter |
| side engine | `lib/side-engine.sh`: the same engine code on :3011, same graph, with `CONNECTION_STRINGS` naming Pagila and Chinook. Started and stopped by `run-all.sh` |

The live engine cannot run Pagila SQL: its `CONNECTION_STRINGS` names only Chinook,
and adding Pagila through `POST /api/connections` would take Chinook away (UI
connections shadow `CONNECTION_STRINGS`, and the structured connection form has no
SQLite type). So every Pagila question runs on the side engine, and the output says
which engine answered. The side engine points `POSTGRES_HOST/PORT` nowhere, like the
container, so it cannot quietly use the host's Postgres for engine state.

## Part 1: Auto Ontology says it does X → Nidra

| Auto Ontology says it does | script | result | status |
| --- | --- | --- | --- |
| **check_readiness**: compiled layer + live connection + catalog (docs/mcp.md) | `01-readiness.py` | the three probes answer; upstream's own fold (`readiness._summarise`, imported unmodified) says **not ready**: "No database connection is configured", while Chinook questions do execute. It reads only UI connections; this engine's connection is `CONNECTION_STRINGS` | partial |
| **Connections management** (README § Connections) | `02-connections.py` | list, env source and `POST /api/connections/test` work (Pagila test passes from inside the container); a wrong password is refused with 422, but the message is a 30 s pool timeout, not the driver's error; Pagila is not connected on the live engine (see above) | partial |
| **NL querying: a count** | `03-ask-count.py`, `03b-count-star-validator.py` | Pagila "How many films": 1000 ✓ (side engine). Chinook "How many tracks": failed this run after 223 s, "SQL can't be constructed from the data": the validator refuses `COUNT(*)` with no column ("SQL doesn't reference any table known to the catalog"), the model keeps writing `COUNT(*)`; 03b reproduces it with no model. The same question passed in a manual run during setup (19:28, 27 s, `COUNT("TrackId")` = 3503; not in `out/`). Pagila on the live engine: not graded in the recorded run (402); the manual setup run (19:32) declined after 4 min ("SQL can't be constructed from the data.", its last rewrite `SELECT COUNT(*) FROM Album WHERE 1 = 0` refused as degenerate) instead of answering from Chinook | partial |
| **NL: aggregates** | `04-ask-aggregate.py` | Chinook revenue per country, top 5 (USA 523.06 … Germany 156.48) ✓; Pagila average rental rate per rating ✓ | works |
| **NL: joins** | `05-ask-join.py` | Chinook top 3 artists by tracks (Iron Maiden 213, U2 135, Led Zeppelin 114) ✓; Pagila top 3 film categories ✗: it grouped by rating, "no category table exists in the schema" (category and film_category are in the catalog; retrieval did not offer them) | partial |
| **NL: a time window** | `06-ask-time-window.py` | Pagila rentals on 25–26 May 2022 = 306 ✓; Chinook Q1 2023 revenue: not graded (402) | partial |
| **NL: a question that needs a glossary term** (Ground) | `07-ask-glossary-term.py` | "songs" (synonym of Track) longer than five minutes = 1069 ✓; "patrons": not graded (402); "customer lifetime value" (a Pagila SQL attribute) ✗: answered from Chinook's Invoice table | partial |
| **check_answerable**, and an unanswerable question | `08-check-answerable.py` | a covered question grades 1.0 ✓; "employees with a pension plan" grades 1.0 ✗; the weather question 0.0 in the recorded run (1.0 in an earlier, overwritten run of 08). Cause: Nidra's vector store sets distance = 1 − find's score, and find's top hit scores about 0.7 even for nonsense, so hits clear the 0.75 threshold. The ask itself: not graded (402) | partial |
| **Glossary**: terms, synonyms, SQL attributes, certification | `09-glossary.py` | terms, get_term, column attributes, SQL attributes, validate (Chinook SQL 200, parse error 422) ✓; **certification writes fail (500)**; search_terms matches names only, not synonyms (upstream does the same); `/api/acronyms` 404 (Next.js route) | partial |
| **Catalog**: tables, columns, FKs, join paths, describe_table | `10-catalog.py` | 37 tables, Track's 9 columns, describe_table, 27 FK edges, Customer → Rental in 4 hops, the graph views ✓; **global search 500** (no RDF port) | partial |
| **Semantic compilation**: trigger and status (Discover) | `11-semantic-compilation.py` | trigger 202 and status answer; the pass is skipped: "semantic: compilation disabled in settings; skipping run". The switch is a Next.js settings route upstream; on Nidra it is the env var `AO_FLAG_SEMANTIC_COMPILATION_ENABLED`, unset on the container. The 5-term glossary is a hand-authored fixture, not a compilation | not yet |
| **Model interchange**: YAML and Ossie export, re-import | `12-model-interchange.py` | native YAML (60 KB), Ossie YAML (55 KB) and the Ossie zip (model, ontology, mapping) export ✓; re-importing the native export into a sandbox copy creates nothing and re-exports identically ✓; the Ossie re-import adds one duplicate column attribute (the SQL attribute "customer lifetime value" comes back as a column attribute) ✗. Imports ran on `duplicate_graph` sandboxes; ao-catalog was only read | partial |
| **Ingestion of a source** | `13-ingestion.py` | `POST /ingest` for Chinook: 202, finished in 2 s, 11 tables / 64 columns, every id and FK unchanged ✓; Pagila: accepted, then "Ingest skipped: no loaded connector for database pagila" | works (Chinook) |
| **MCP: the nine tools** (docs/mcp.md) | `14-mcp-tools.py` | search_terms, get_term, get_term_columns, get_term_sql_attributes, get_sql_attribute, describe_table, check_answerable, check_readiness, ask_question all answer through the FastAPI routes they wrap ✓. **The OAuth MCP front door is not wired in Nidra yet**: no Next.js gateway, no `/.well-known/oauth-authorization-server` (404), nothing on :3000 or :3003, so an MCP client cannot sign in | partial |
| **Skill nvidia-ontology-query**: readiness → search_terms → check_answerable → ask → validate | `15-skill-query.py` | steps 1–3 run (readiness has the false blocker of 01); step 4: not graded (402) | not graded |
| **Skill nvidia-ontology-setup** (verify steps) | `01`, `02`, `14`, `17` | health, status and connection test ✓; OAuth discovery 404; no UI | partial |
| **Skill nvidia-ontology-management** | `09`, `11`, `12` | inspect and validate ✓, export/import with exact readback ✓ (native); edits and certification 500; compilation off | partial |
| **Validate**: queries checked before they run, provenance with every answer | `03b`, `04`–`07` | every answer carries `sql_code` and `sql_columns` (catalog column ids) and passes a validator first; the validator also refuses valid `COUNT(*)` SQL (03b) | partial |
| **Learn**: fold certified corrections and usage back | `16-learn.py` | certification writes 500; usage enters only from the source's query log at ingest (0 queries on SQLite); the "similar questions from conversations" slot in the SQL prompt is never filled (upstream too) | not yet |
| **Auth & RBAC, web UI, deployment** | `17-platform.py` | Docker Compose runs ✓; FastAPI answers with no identity header, any user header, a bogus API key: no auth or RBAC in Nidra (upstream puts both in the Next.js gateway); no UI | partial |

## Part 2: beyond, on the same catalog (gardend through MCP)

| demo | script | result | status |
| --- | --- | --- | --- |
| (a) the catalog as RDF: SPARQL joining terms → column attributes → columns → tables → databases | `20-sparql-catalog.py` | one query returns all 8 term → column paths (the engine's API agrees: 8); the semantic layer's gaps per database (Chinook 10 of 11 tables without a term, Pagila 22 of 26); a two-hop FK walk from rental; the vector index rows (75 data, 13 semantic) in the same graph | works |
| (b) `find` across languages | `21-find-multilingual.py` | Spanish "duración de la canción en milisegundos" and Japanese "曲の長さ（ミリ秒）" → Track.Milliseconds; "tarifa de alquiler de la película" and "映画のレンタル料金" → film.rental_rate; "el empleado de soporte" → Customer.SupportRepId: 5 of 5 top hits right, with provenance (named graph) and a `next` hint. The engine's search_terms returns nothing for "canción", "曲" and even "song" | works |
| (c) a correction as witnessed testimony (Emporium) | `22-emporium-correction.py` | the glossary says Track is "A single piece of recorded music."; Chinook says 214 of 3,503 tracks are MPEG-4 video. Recorded the glossary's statement in the commons and the correction in witness `agent-6e6964726164656d` ("nidra-demos", Claude recording for Vera), superseding it: one lineage, two heads (commons: the glossary's statement; witness: the correction), flagged contested by `emporium_query perspectives=all`; nothing overwritten, `observedBy` names the witness; re-running converges. Not wired back: the engine still answers with the old description | works |
| (d) a restore point before a recompile, and the diff after | `23-restore-point-diff.py` | restore points are captured and diffed ✓, but: they hold documents, folders and wires, not the RDF catalog (the diff sees no catalog change; a SPARQL fingerprint does); the recompile is a no-op on the live stack (11); and on a throwaway graph `restore_to_restore_point` left both RDF loaded after the point and a document created after it in place | partial |
| (e) a wire from a term to a table it should also represent, a comment on a column, then an ingest sweep | `24-wires-comments-ingest.py` | wire `should-also-represent` from the glossary term Customer to Chinook's Customer table (drawn on a whiteboard, as an object-to-object wire must be); a note document embedding Track.Milliseconds as an object card, wired `annotates`, with a comment anchored on "300000"; `docker exec nidra-ingestion` POST /ingest; afterwards wires, comment and their targets are identical | works |
| (f) memory: remember an answer with its SQL, recall it from a paraphrase | `25-memory.py` | remembered with a ToolResult provenance anchor; substring recall ✓; "how many tracks run over 5 min?" recalls it first (0.80 vs 0.65) ✓; the Spanish paraphrase does not (0.546 vs 0.548): the recall model here is BGE Small EN (English only); it first had to be prepared (`prepare_semantic_model`, a one-time download) | partial |
| (g) one answer's trace as a document, found again | `26-answer-trace-document.py` | "Answer trace: How many songs are longer than five minutes?" with steps, SQL, rows and answer, wired `read-column` to Track.Milliseconds; found by `search_documents` (title), `search_blocks` (the SQL) and `get_backlinks` from the column ✓. The answer is demo 14's recorded one (the fresh ask hit the 402), and the document says so | works |
| inventory of what the demos wrote, and how to undo it | `29-undo-check.py` | lists the 3 documents, 3 wires, 3 memory records, the restore points and the scratch graphs; the catalog differs from the snapshot only by the ingest timestamps (`pulled`, FK `last_seen`) of the Chinook sweeps in 13 and 24 | works |

## Restore point and undo

- Restore point taken before any demo wrote: **`rp-5c54b5b4e4ce4c7b9fd584d268c6c94f`**, label
  **`nidra-demos-before-20261103-201821`**, graph `ao-catalog` (0 documents then).
- Catalog snapshot taken with it (all named graphs, N-Quads, 3,884 quads):
  `demos/state/ao-catalog-nidra-demos-before-20261103-201821.nq` (local, git-ignored).
- **The restore point alone will not undo the demos.** Demo 23 shows `restore_to_restore_point`
  leaving later documents and later RDF in place, and restore points do not hold the catalog. To undo:
  1. delete the three documents (`delete_document`): `doc-48b09f62fa404230b800237ad58bc66a` (board),
     `doc-fa3000229d994d5e82fc589f76969a83` (note on Track.Milliseconds),
     `doc-9a70cca6440a4adb9ddcb1fe06013d80` (answer trace); their wires and the comment go with them;
  2. retract the three memory records listed in `out/29-undo-check.txt` (`emporium_retract`; a
     retraction is an event, the log keeps them);
  3. the catalog: only ingest timestamps changed; to return it exactly, clear named graph
     `urn:mnemosyne:local:graph:ao-catalog:user:rdf` and `rdf_load` that graph's lines from the snapshot;
  4. scratch graphs on the scratch gardend: `ao-demo-probe`, `ao-demo-sandbox-*` (4), `ao-demo-rp-*` (2)
     (`manage_graph action=delete`).
- Demo 09 PATCHes a certification and puts it back; on Nidra the PATCH fails, so nothing changed.
- Restore points made by demo 23 runs (labels `nidra-demo-23 …`) are listed in `out/29-undo-check.txt`.

## How to run

    demos/run-all.sh                       # everything, in order; writes out/SUMMARY.txt
    DEMOS_REUSE_ANSWERS=1 demos/run-all.sh # reuse kept answers (state/answers/), ask only what is missing
    demos/run.sh 21-find-multilingual.py   # one demo
    demos/lib/side-engine.sh start|stop    # the Pagila-connected engine on :3011
    demos/lib/credit.sh                    # model credit left on the key in .env

`lib/env.sh` sources the fork's `.env` and reads the gardend token from
`GARDEND_TOKEN_FILE` (default: the session scratchpad's `gd/token-all.json`; set it if
that file is gone). Outputs pass through a redactor that withholds any line holding the
value of a `*KEY`, `*TOKEN`, `*SECRET` or `*PASSWORD` variable; gitleaks finds nothing in
`demos/`. A full run asks about 15 questions; top up the key first.

## Found broken or missing (exact errors)

1. **Certification and term edits answer 500.** `PATCH /api/sql-attributes/{id}` and
   `PATCH /api/terms/{id}`: `sqlalchemy.exc.OperationalError: (psycopg.OperationalError) connection
   failed: connection to server at "127.0.0.1", port 5432 failed: Connection refused`, from
   `auto_ontology/dal/owners.py` line 64 `_stored_owner` (via `edit_subject`, line 87): an unported
   read that still opens Postgres (09, 16).
2. **Global search answers 500.** `POST /api/search/global-search`: `NotImplementedError:
   auto_ontology.dal.search.fetch_global_search has no RDF port (AO_RDF_STRICT=1)` (10).
3. **Semantic compilation cannot be switched on.** `semantic: compilation disabled in settings;
   skipping run`; `GET /api/configurations/semantic-compilation` is 404 on Nidra; the only switch
   is `AO_FLAG_SEMANTIC_COMPILATION_ENABLED=true` on the ingestion container (11, 23).
4. **check_answerable cannot say no.** `auto_ontology/vdb/gardend.py:196` sets
   `_distance = 1.0 - score`; find's fused top-hit score is ~0.7 even for "zzqx flibbertigibbet",
   so most entities pass `coverage_grade`'s `max_distance=0.750` ("employees with a pension plan"
   grades 1.0) (08).
5. **`COUNT(*)` fails validation.** "SQL doesn't reference any table known to the catalog; check the
   query and available schema" for `SELECT COUNT(*) FROM "Track"`: upstream's
   `catalog/sql_parse.py parse_query_single` returns None when the SQL names no column. Makes the
   simplest count flaky (223 s then "SQL can't be constructed from the data.") (03, 03b).
6. **check_readiness false blocker** on env-configured connections: "No database connection is
   configured, so generated SQL cannot execute…" while SQL executes (01; upstream behaviour too).
7. **Pagila is not connected on the live engine**, and cannot be added through the API without
   losing Chinook (UI connections shadow `CONNECTION_STRINGS`; no SQLite connection type); the
   ingestion service resolves the other way (env first), and skips Pagila: "Ingest skipped: no
   loaded connector for database pagila" (02, 13). A bad-password test returns
   "Connection test failed: couldn't get a connection after 30.00 sec" instead of the auth error.
8. **No OAuth MCP front door, no auth or RBAC, no UI in Nidra**: `/.well-known/oauth-authorization-server`
   404, :3000 and :3003 closed; FastAPI serves any caller (14, 17).
9. **Ossie round trip is lossy**: the SQL attribute "customer lifetime value" returns as a new column
   attribute `customer_lifetime_value` (12). Export order of `sql_column_is` is not stable on the
   RDF store (normalised in the comparison).
10. **Answer quality**: Pagila "film categories" grouped by rating ("no category table exists in
    the schema"); "customer lifetime value" answered from Chinook invoices (05, 07).
11. **Restore points do not cover the catalog, and restore did not remove later state**: on a
    throwaway graph, after `restore_to_restore_point` to a point taken on the empty graph, a triple
    loaded later and a document created later were both still there; `diff_restore_points` against
    `current` reports documents added since the point as `docsRemoved` (23, 29).
12. **Semantic recall needed setup and is English-only**: first call: "local embedding model BGE
    Small EN v1.5 Quantized is not prepared; run prepare_semantic_model first"; Spanish paraphrase
    not recalled (25).
13. **Emporium details**: a record is a head only with `isCurrent` and `validFrom` ("sets isCurrent
    but no validFrom (I7)"); `emporium_heads` reads one membrane at a time, so the two heads show
    only through `emporium_query perspectives=all`; `emporium_describe` leaves `testimony` empty for
    memory records; corrections are not read by the text-to-SQL agent (22).
14. **Missing routes on Nidra** (Next.js/Prisma upstream): `/api/acronyms`, `/api/conversations`,
    `/api/analytics`, `/api/configurations/*` all 404.
15. **Model credit**: OpenRouter 402 `in_flight_budget_exhausted`, "This request would exceed your
    available credits given your current in-flight requests"; the engine surfaces it only as
    "LLM failed to produce a response." (03, 06, 07, 08, 15). Running demos in parallel triggers it
    sooner.

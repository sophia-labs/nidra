"""Parity: the business glossary: terms, synonyms, SQL attributes, certification."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from demo import SIDE, Demo, nidra, nidra_at, side_up  # noqa: E402

d = Demo("09-glossary", "glossary: terms, synonyms, SQL attributes, certification",
         "A semantic catalog of terms with synonyms, column mappings and reviewed SQL attributes, each with certification.",
         "README.md § Key Features; docs/mcp.md (search_terms, get_term, get_term_columns, get_term_sql_attributes, get_sql_attribute); "
         "skills/nvidia-ontology-management")

d.section("GET /api/terms (search_terms)")
code, body = nidra("GET", "/api/terms", params={"limit": 50})
terms = body.get("terms", [])
for t in terms:
    d.log(f"- {t['name']}: {t['description']!r} synonyms={t['synonyms']} certification={t['certification']} "
          f"(name_certified={t['name_certified']}, description_certified={t['description_certified']})")
d.check(code == 200 and body.get("total") == len(terms) and len(terms) > 0, f"{len(terms)} terms listed, total={body.get('total')}")

d.section("GET /api/terms?query=… (search_terms)")
code, body = nidra("GET", "/api/terms", params={"query": "fil"})
names = [t["name"] for t in body.get("terms", [])]
d.log(f"query=fil hits: {names}")
d.check(code == 200 and names == ["Film"], "a name substring finds its term")
code, body = nidra("GET", "/api/terms", params={"query": "movie"})
names = [t["name"] for t in body.get("terms", [])]
d.log(f"query=movie (a synonym of Film) hits: {names}")
if not names:
    d.note("search_terms matches term names only, not synonyms: upstream filters name ILIKE '%q%' "
           "(auto_ontology/dal/terms.py _semantic_terms), and the port keeps that. Synonyms reach the "
           "chat agent through retrieval (demo 07), and gardend's find matches them by meaning (demo 21).")

film = next(t for t in terms if t["name"] == "Film")
d.section(f"GET /api/terms/{film['id']} (get_term)")
code, body = nidra("GET", f"/api/terms/{film['id']}")
d.show(f"get_term ({code})", body, 2500)
d.check(code == 200, "get_term answers")

d.section("GET /api/terms/{id}/column-attributes (get_term_columns)")
code, body = nidra("GET", f"/api/terms/{film['id']}/column-attributes")
rows = body.get("data", body if isinstance(body, list) else [])
for r in rows:
    d.log(f"- {r.get('name')} -> column {r.get('column_name') or r.get('source_column') or r.get('column', {})} certified={r.get('certified')}")
d.check(code == 200 and len(rows) > 0, f"{len(rows)} column attributes on Film")

d.section("GET /api/terms/{id}/sql-attributes (get_term_sql_attributes) and GET /api/sql-attributes/{id} (get_sql_attribute)")
code, body = nidra("GET", f"/api/terms/{film['id']}/sql-attributes")
attrs = body.get("data", [])
for a in attrs:
    d.log(f"- {a['name']}: {a['expression']} certified={a['certified']}")
d.check(code == 200 and len(attrs) > 0, f"{len(attrs)} SQL attributes on Film")
code, one = nidra("GET", f"/api/sql-attributes/{attrs[0]['id']}")
d.show(f"get_sql_attribute ({code})", one, 1500)
d.check(code == 200, "get_sql_attribute answers")

d.section("POST /api/sql-attributes/validate (management skill step 4)")
code, ok_body = nidra("POST", "/api/sql-attributes/validate", json={"expression": 'SELECT count("TrackId") FROM "Track"'})
d.show(f"valid Chinook SQL on the live engine ({code})", ok_body, 1200)
d.check(code == 200, "a valid Chinook expression validates on the live engine")
code, pg_body = nidra("POST", "/api/sql-attributes/validate", json={"expression": "SELECT count(film_id) FROM film"})
d.show(f"valid Pagila SQL on the live engine ({code})", pg_body, 1200)
if code != 200:
    d.note("the live engine validates SQL against connected sources only, so a Pagila expression is refused there "
           "('SQL doesn't reference any table known to the catalog') although Pagila is in the catalog (demo 02)")
if side_up():
    code, side_body = nidra_at(SIDE, "POST", "/api/sql-attributes/validate", json={"expression": "SELECT count(film_id) FROM film"})
    d.show(f"the same Pagila SQL on the side engine ({code})", side_body, 1200)
    d.check(code == 200, "the Pagila expression validates where Pagila is connected")
code, bad_body = nidra("POST", "/api/sql-attributes/validate", json={"expression": "SELEC count( FROM"})
d.show(f"broken SQL ({code})", bad_body, 1200)
d.check(code == 422, "a parse failure is HTTP 422, as the skill says")

d.section("certification: certify a SQL attribute, read it back, put it back")
target = next((a for a in attrs if not a["certified"]), attrs[0])
before = target["certified"]
code, patched = nidra("PATCH", f"/api/sql-attributes/{target['id']}", json={"certified": not before})
d.show(f"PATCH certified={not before} ({code})", patched, 1200)
code, readback = nidra("GET", f"/api/sql-attributes/{target['id']}")
readback = readback.get("data", readback)
d.check(code == 200 and readback.get("certified") == (not before), f"certification persisted ({target['name']}: {before} -> {readback.get('certified')})")
if readback.get("certified") != before:
    code, restored = nidra("PATCH", f"/api/sql-attributes/{target['id']}", json={"certified": before})
    code, readback = nidra("GET", f"/api/sql-attributes/{target['id']}")
    readback = readback.get("data", readback)
d.check(readback.get("certified") == before, f"certification is back to {before}")
track = next(t for t in terms if t["name"] == "Track")
code, patched = nidra("PATCH", f"/api/terms/{track['id']}", json={"name_certified": not track["name_certified"]})
d.log(f"PATCH /api/terms/{{Track}} name_certified={not track['name_certified']} -> {code} {str(patched)[:200]}")
code, tb = nidra("GET", f"/api/terms/{track['id']}")
now = (tb.get("data") or tb).get("name_certified") if isinstance(tb, dict) else None
d.check(now == (not track["name_certified"]), f"term certification persisted (Track name_certified {track['name_certified']} -> {now})")
if now != track["name_certified"]:
    nidra("PATCH", f"/api/terms/{track['id']}", json={"name_certified": track["name_certified"]})
if code != 200 or now != (not track["name_certified"]):
    d.note("certification writes fail on Nidra: PATCH /api/sql-attributes/{id} and PATCH /api/terms/{id} answer 500 "
           "because auto_ontology/dal/owners.py edit_subject -> _stored_owner (line 64) still opens Postgres "
           "(psycopg.OperationalError: connection to server at 127.0.0.1:5432 refused)")

d.section("acronyms (part of the glossary surface upstream)")
code, body = nidra("GET", "/api/acronyms")
d.log(f"GET /api/acronyms -> {code} {str(body)[:200]}")
if code == 404:
    d.note("/api/acronyms is a Next.js (Prisma) route upstream; Nidra's FastAPI has no acronyms store")
d.finish()

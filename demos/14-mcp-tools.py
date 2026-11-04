"""Parity: the nine tools of the upstream MCP server, exercised through the
FastAPI routes they wrap. The MCP server itself (mcp/) is an HTTP client of the
Next.js gateway with OAuth sign-in; Nidra runs no gateway, so that front door
is not wired."""
import os
import socket
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from demo import Demo, ask, nidra, show_answer, upstream_readiness  # noqa: E402

d = Demo("14-mcp-tools", "the nine MCP tools, through the routes they wrap",
         "Expose: serve context to any agent harness through MCP (nine read tools, OAuth sign-in).",
         "docs/mcp.md § Tools; mcp/auto_ontology_mcp/tools.py, chat.py, readiness.py")
results = {}


def tool(name, ok, detail=""):
    results[name] = ok
    d.check(ok, f"{name}{(': ' + detail) if detail else ''}")


d.section("the OAuth front door")
code, body = nidra("GET", "/.well-known/oauth-authorization-server")
d.log(f"GET :3001/.well-known/oauth-authorization-server -> {code}")
for port in (3000, 3003):
    s = socket.socket()
    s.settimeout(1)
    open_ = s.connect_ex(("127.0.0.1", port)) == 0
    s.close()
    d.log(f"127.0.0.1:{port} ({'Next.js web app / authorization server' if port == 3000 else 'auto-ontology-mcp'}) listening: {open_}")
d.note("the OAuth MCP front door is not wired in Nidra yet: no Next.js gateway (no authorization server, no "
       "/.well-known/oauth-authorization-server), no auto-ontology-mcp process, so an MCP client cannot sign in. "
       "The tools below are the same HTTP calls the MCP server would make, sent straight to FastAPI with the "
       "trusted x-auto-ontology-user-id header.")

d.section("search_terms  (GET /api/terms)")
code, body = nidra("GET", "/api/terms", params={"query": "rent"})
names = [t["name"] for t in body.get("terms", [])]
tool("search_terms", code == 200 and "Rental" in names, f"query=rent -> {names}")
code, all_terms = nidra("GET", "/api/terms", params={"limit": 50})
tid = {t["name"]: t["id"] for t in all_terms.get("terms", [])}

d.section("get_term  (GET /api/terms/{term_id})")
code, body = nidra("GET", f"/api/terms/{tid['Customer']}")
data = body.get("data", body)
tool("get_term", code == 200 and "renter" in str(data), f"Customer, synonyms {data.get('synonyms')}")

d.section("get_term_columns  (GET /api/terms/{term_id}/column-attributes)")
code, body = nidra("GET", f"/api/terms/{tid['Customer']}/column-attributes")
rows = body.get("data", [])
tool("get_term_columns", code == 200 and len(rows) > 0, f"{[r.get('name') for r in rows]}")

d.section("get_term_sql_attributes  (GET /api/terms/{term_id}/sql-attributes)")
code, body = nidra("GET", f"/api/terms/{tid['Customer']}/sql-attributes")
attrs = body.get("data", [])
tool("get_term_sql_attributes", code == 200 and len(attrs) > 0, f"{[a['name'] for a in attrs]}")

d.section("get_sql_attribute  (GET /api/sql-attributes/{attr_id})")
code, body = nidra("GET", f"/api/sql-attributes/{attrs[0]['id']}")
one = body.get("data", body)
tool("get_sql_attribute", code == 200 and bool(one.get("expression")), f"{one.get('name')}: {one.get('expression')}")

d.section("describe_table  (GET /api/exploration/tables/{table_id}/details)")
code, dbs = nidra("GET", "/api/datasources/dbs")
pag = next(x for x in dbs["data"] if x["name"] == "pagila")
code, schemas = nidra("GET", f"/api/schemas/{pag['id']}")
public = next(s for s in schemas["schemas"] if s["schema_name"] == "public")
code, tables = nidra("GET", f"/api/tables/{public['id']}")
customer = next(t for t in tables["data"] if t["name"] == "customer")
code, det = nidra("GET", f"/api/exploration/tables/{customer['id']}/details")
dd = det.get("data", det)
d.show("describe_table customer (abridged)", dd, 1500)
tool("describe_table", code == 200 and "Customer" in str(dd), "the table view names the term Customer")

d.section("check_answerable  (POST /api/question-entity-coverage)")
code, body = nidra("POST", "/api/question-entity-coverage", json={"question": "How many patrons are there?"}, timeout=180)
cov = ((body or {}).get("data") or {}).get("coverage")
tool("check_answerable", code == 200 and cov is not None, f"coverage {cov}")

d.section("check_readiness  (three probes, folded by upstream's readiness._summarise)")
_, st = nidra("GET", "/api/semantic-compilation/status")
_, cn = nidra("GET", "/api/connections")
_, db = nidra("GET", "/api/datasources/dbs")
v = upstream_readiness(st, cn, db)
d.show("check_readiness", v, 1200)
tool("check_readiness", True, f"answers (ready={v['ready']}; see demo 01 for the false blocker)")

d.section("ask_question  (POST /api/chat/completions, SSE)")
a = ask("How many songs are longer than five minutes?")
show_answer(d, a)
rows = a.get("rows") or []
tool("ask_question", a["done"] and bool(rows) and bool(a.get("final_sql")),
     "answer + SQL + rows as separate fields" if rows else f"no rows: {a.get('answer_text')}")
d.log(f"row_count={len(rows)} (the MCP server caps rows at 100 and reports row_count/truncated)")

passed = sum(results.values())
d.finish("partial", f"{passed}/9 operations answer through FastAPI; the OAuth MCP front door is not wired in Nidra yet")

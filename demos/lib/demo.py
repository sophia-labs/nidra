"""Shared helpers for the demos: one output file per demo, checks, and the
three clients (the Nidra engine's HTTP API, gardend's MCP endpoint, and the
source databases for ground truth).

Every line written to `demos/out/` passes through `_redact`: a line that
contains the value of any *KEY / *TOKEN / *SECRET / *PASSWORD environment
variable is withheld whole, never edited (fail closed).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from typing import Any

import httpx

NIDRA = os.environ.get("NIDRA_URL", "http://127.0.0.1:3001").rstrip("/")
USER = os.environ.get("NIDRA_USER", "vera")
GRAPH = os.environ.get("GARDEND_GRAPH_ID", "ao-catalog")
GARDEND = os.environ.get("GARDEND_URL", "http://127.0.0.1:7690").rstrip("/")
OUT = os.environ.get("DEMOS_OUT") or os.path.join(os.path.dirname(__file__), "..", "out")
FORK = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CHINOOK = os.path.join(FORK, "nidra", "data", "chinook.sqlite")

_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD", re.I)
_SECRETS = sorted(
    {v for k, v in os.environ.items() if _SECRET_NAME.search(k) and v and len(v) >= 6},
    key=len,
    reverse=True,
)


def _redact(line: str) -> str:
    for s in _SECRETS:
        if s in line:
            return "[line withheld: it contained the value of a secret environment variable]"
    return line


def short(obj: Any, limit: int = 4000) -> str:
    text = obj if isinstance(obj, str) else json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    if len(text) > limit:
        text = text[:limit] + f"\n… [{len(text) - limit} more characters not shown]"
    return text


class Demo:
    """One demo run: a header, sections, checks, and a final STATUS line."""

    def __init__(self, name: str, title: str, claim: str, source: str = "") -> None:
        self.name = name
        self.path = os.path.join(OUT, f"{name}.txt")
        os.makedirs(OUT, exist_ok=True)
        self._fh = open(self.path, "w", encoding="utf-8")
        self.checks: list[tuple[bool, str]] = []
        self.notes: list[str] = []
        self.t0 = time.time()
        now = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
        self.log(f"# {name}: {title}")
        self.log(f"run at {now} against Nidra {NIDRA} and gardend {GARDEND} graph {GRAPH}")
        self.log(f"claim: {claim}")
        if source:
            self.log(f"claim source: {source}")
        self.log("")

    def log(self, *parts: Any) -> None:
        text = " ".join(p if isinstance(p, str) else short(p) for p in parts)
        for line in text.splitlines() or [""]:
            line = _redact(line)
            print(line)
            self._fh.write(line + "\n")
        self._fh.flush()

    def section(self, title: str) -> None:
        self.log("")
        self.log(f"## {title}")

    def show(self, label: str, obj: Any, limit: int = 4000) -> None:
        self.log(f"{label}:")
        self.log(short(obj, limit))

    def check(self, ok: bool, what: str) -> bool:
        self.checks.append((bool(ok), what))
        self.log(f"CHECK {'PASS' if ok else 'FAIL'}: {what}")
        return bool(ok)

    def note(self, text: str) -> None:
        self.notes.append(text)
        self.log(f"NOTE: {text}")

    def finish(self, status: str | None = None, summary: str = "") -> None:
        passed = sum(1 for ok, _ in self.checks if ok)
        failed = [w for ok, w in self.checks if not ok]
        if status is None:
            status = "works" if not failed and self.checks else ("partial" if passed else "not yet")
        self.log("")
        self.log(f"checks: {passed}/{len(self.checks)} passed" + (f"; failed: {'; '.join(failed)}" if failed else ""))
        self.log(f"elapsed: {time.time() - self.t0:.1f}s")
        self.log(f"STATUS: {status}" + (f" — {summary}" if summary else ""))
        self._fh.close()


# -- the Nidra engine ----------------------------------------------------------

_http = httpx.Client(base_url=NIDRA, headers={"x-auto-ontology-user-id": USER}, timeout=120.0)


def nidra(method: str, path: str, **kw: Any) -> tuple[int, Any]:
    r = _http.request(method, path, **kw)
    ctype = r.headers.get("content-type", "")
    if "json" in ctype:
        try:
            return r.status_code, r.json()
        except ValueError:
            pass
    return r.status_code, r.text


SIDE = os.environ.get("SIDE_NIDRA_URL", "http://127.0.0.1:3011").rstrip("/")


def side_up() -> bool:
    try:
        return httpx.get(f"{SIDE}/api/health", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


def nidra_at(base: str, method: str, path: str, **kw: Any) -> tuple[int, Any]:
    with httpx.Client(base_url=base, headers={"x-auto-ontology-user-id": USER}, timeout=kw.pop("timeout", 120.0)) as c:
        r = c.request(method, path, **kw)
    if "json" in r.headers.get("content-type", ""):
        try:
            return r.status_code, r.json()
        except ValueError:
            pass
    return r.status_code, r.text


_ANSWERS = os.path.join(os.path.dirname(__file__), "..", "state", "answers")


def ask(question: str, conversation_id: str | None = None, timeout: float = 600.0, base: str | None = None) -> dict[str, Any]:
    """Ask, and keep the folded answer in demos/state/answers/. With
    DEMOS_REUSE_ANSWERS=1 a kept answer is returned instead of asking again
    (marked `reused_from`), unless it was a model failure; the default asks."""
    import hashlib

    key = hashlib.sha1(f"{base or NIDRA}|{question}".encode()).hexdigest()[:16]
    path = os.path.join(_ANSWERS, f"{key}.json")
    if os.environ.get("DEMOS_REUSE_ANSWERS") == "1" and os.path.exists(path) and not conversation_id:
        with open(path) as fh:
            kept = json.load(fh)
        if "LLM failed" not in str(kept.get("answer_text")):
            kept["reused_from"] = kept.get("asked_at")
            return kept
    out = _ask(question, conversation_id, timeout, base)
    out["asked_at"] = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    os.makedirs(_ANSWERS, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    return out


def _ask(question: str, conversation_id: str | None, timeout: float, base: str | None) -> dict[str, Any]:
    """POST /api/chat/completions and fold the SSE stream into one record."""
    body: dict[str, Any] = {"question": question}
    if conversation_id:
        body["conversation_id"] = conversation_id
    t0 = time.time()
    out: dict[str, Any] = {"question": question, "engine": base or NIDRA, "steps": [], "sql": [], "result": None, "error": None, "events": 0, "done": False}
    with httpx.Client(base_url=base or NIDRA, headers={"x-auto-ontology-user-id": USER}, timeout=timeout) as c:
        with c.stream("POST", "/api/chat/completions", json=body) as r:
            out["http_status"] = r.status_code
            if r.status_code != 200:
                out["error"] = r.read().decode("utf-8", "replace")[:2000]
                out["seconds"] = round(time.time() - t0, 1)
                return out
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    out["done"] = True
                    break
                try:
                    ev = json.loads(data)
                except ValueError:
                    continue
                out["events"] += 1
                kind = ev.get("type")
                if kind == "step" and ev.get("phase") == "end":
                    out["steps"].append({"node": ev.get("node"), "label": ev.get("label"), "thought": ev.get("thought")})
                elif kind == "sql":
                    out["sql"].append(ev.get("sql"))
                elif kind == "result":
                    out["result"] = ev.get("answer")
                elif kind == "error":
                    out["error"] = ev
    out["seconds"] = round(time.time() - t0, 1)
    res = out["result"] or {}
    rows: list[Any] = []
    for chunk in res.get("sql_response_from_db") or []:
        try:
            parsed = json.loads(chunk) if isinstance(chunk, str) else chunk
        except ValueError:
            parsed = chunk
        rows.extend(parsed if isinstance(parsed, list) else [parsed])
    out["rows"] = rows
    out["answer_text"] = res.get("response")
    out["final_sql"] = res.get("sql_code")
    return out


def show_answer(d: Demo, a: dict[str, Any], rows: int = 10) -> None:
    d.log(f"engine: {a['engine']}" + (f"  (answer REUSED from {a['reused_from']}, DEMOS_REUSE_ANSWERS=1)" if a.get("reused_from") else ""))
    d.log(f"question: {a['question']}")
    d.log(f"http {a.get('http_status')} · {a['events']} SSE events · [DONE] {'seen' if a['done'] else 'NOT seen'} · {a['seconds']}s")
    d.log("steps: " + " → ".join(s["node"] for s in a["steps"]))
    if a.get("final_sql"):
        d.log("sql: " + a["final_sql"])
    if a.get("rows"):
        d.show(f"rows ({len(a['rows'])}, first {rows})", a["rows"][:rows], 2500)
    if a.get("answer_text"):
        d.log("answer: " + a["answer_text"])
    if a.get("error"):
        d.show("error", a["error"], 2000)


# -- gardend MCP ---------------------------------------------------------------


class MCP:
    """A minimal streamable-HTTP MCP client for gardend's /mcp (bearer token)."""

    def __init__(self) -> None:
        token = os.environ.get("GARDEND_TOKEN", "")
        self._http = httpx.Client(base_url=GARDEND, headers={"authorization": f"Bearer {token}"}, timeout=300.0)
        self._id = 0
        self._sid: str | None = None
        self._init()

    def _headers(self) -> dict[str, str]:
        h = {"accept": "application/json, text/event-stream"}
        if self._sid:
            h["mcp-session-id"] = self._sid
        return h

    def _rpc(self, method: str, params: dict[str, Any] | None = None, notify: bool = False) -> Any:
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notify:
            self._id += 1
            body["id"] = self._id
        r = self._http.post("/mcp", json=body, headers=self._headers())
        if r.headers.get("mcp-session-id"):
            self._sid = r.headers["mcp-session-id"]
        if notify:
            return None
        payload = None
        for line in r.text.splitlines():
            line = line.removeprefix("data: ").strip()
            if line.startswith("{"):
                try:
                    payload = json.loads(line)
                except ValueError:
                    continue
        if payload is None:
            raise RuntimeError(f"mcp {method}: HTTP {r.status_code}: {r.text[:300]}")
        return payload

    def _init(self) -> None:
        self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "nidra-demos", "version": "1"}})
        self._rpc("notifications/initialized", notify=True)

    def tools(self) -> list[dict[str, Any]]:
        return self._rpc("tools/list", {})["result"]["tools"]

    def call(self, tool: str, args: dict[str, Any]) -> tuple[bool, Any]:
        """(ok, value): value is the structured result, or the error text."""
        p = self._rpc("tools/call", {"name": tool, "arguments": args})
        if "error" in p:
            return False, p["error"]
        res = p.get("result") or {}
        value: Any = res.get("structuredContent")
        if value is None:
            texts = [c.get("text", "") for c in res.get("content") or [] if c.get("type") == "text"]
            joined = "\n".join(texts)
            try:
                value = json.loads(joined)
            except ValueError:
                value = joined
        return (not res.get("isError")), value


def sparql(mcp: MCP, query: str, **kw: Any) -> list[dict[str, Any]]:
    ok, v = mcp.call("sparql_query", {"graphId": GRAPH, "query": query, **kw})
    if not ok:
        raise RuntimeError(f"sparql_query failed: {short(v, 800)}")
    return v.get("rows") or v.get("results") or v.get("bindings") or []


# -- ground truth --------------------------------------------------------------


def chinook(sql: str) -> list[tuple]:
    con = sqlite3.connect(f"file:{CHINOOK}?mode=ro", uri=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def pagila(sql: str) -> list[tuple]:
    import psycopg

    with psycopg.connect(
        host=os.environ.get("PAGILA_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        dbname="pagila",
        connect_timeout=5,
    ) as con:
        with con.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()


def new_uuid() -> str:
    return str(uuid.uuid4())


def values(rows: list[Any]) -> list[Any]:
    """Every scalar in a result set (rows are dicts or lists)."""
    out: list[Any] = []
    for r in rows:
        if isinstance(r, dict):
            out.extend(r.values())
        elif isinstance(r, (list, tuple)):
            out.extend(r)
        else:
            out.append(r)
    return out


def has_number(rows: list[Any], x: float, tol: float = 0.011) -> bool:
    for v in values(rows):
        try:
            if abs(float(v) - float(x)) <= tol:
                return True
        except (TypeError, ValueError):
            continue
    return False


def has_text(rows: list[Any], s: str) -> bool:
    return any(isinstance(v, str) and s.casefold() in v.casefold() for v in values(rows))


def upstream_readiness(status: Any, connections: Any, databases: Any) -> dict[str, Any]:
    """Upstream's own check_readiness fold (mcp/auto_ontology_mcp/readiness.py
    `_summarise`), imported as is; fastmcp is stubbed because the fold is pure."""
    import types

    if "fastmcp" not in sys.modules:
        fm = types.ModuleType("fastmcp")
        fm.FastMCP = object  # type: ignore[attr-defined]
        fe = types.ModuleType("fastmcp.exceptions")
        fe.ToolError = type("ToolError", (Exception,), {})  # type: ignore[attr-defined]
        sys.modules["fastmcp"] = fm
        sys.modules["fastmcp.exceptions"] = fe
    sys.path.insert(0, os.path.join(FORK, "mcp"))
    from auto_ontology_mcp import readiness  # noqa: PLC0415

    return readiness._summarise(status, connections, databases).model_dump()


def term(v: Any) -> Any:
    """An N-Triples term from sparql_query as a Python value."""
    from auto_ontology.rdf.client import parse_term

    return parse_term(v) if isinstance(v, str) else v

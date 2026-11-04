# SPDX-License-Identifier: Apache-2.0
"""The gardend HTTP client and the N-Triples term parser.

gardend answers ``POST /api/sparql/query`` synchronously with
``{"resultType": "solutions", "variables": [...], "rows": [{var: term}]}``
where every term is in N-Triples syntax (``<iri>``, ``"lit"^^<dt>``, ``_:b``).
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Iterable

import httpx

from auto_ontology.rdf.vocab import XSD

RDF_JSON = "http://www.w3.org/1999/02/22-rdf-syntax-ns#JSON"
_DEFAULT_URL = "http://127.0.0.1:7690"
_DEFAULT_GRAPH = "ao-catalog"


def wrap(body: str) -> str:
    """Wrap a pattern in ``GRAPH ?g { ... }``: unwrapped patterns match nothing."""
    return f"GRAPH ?g {{ {body} }}"


def iri(value: str) -> str:
    return f"<{value}>"


_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def lit(value: object) -> str:
    """Serialize a Python value as a SPARQL / N-Triples literal."""
    if isinstance(value, bool):
        return f'"{"true" if value else "false"}"^^<{XSD}boolean>'
    if isinstance(value, int):
        return f'"{value}"^^<{XSD}integer>'
    if isinstance(value, float):
        return f'"{value!r}"^^<{XSD}double>'
    if isinstance(value, datetime):
        return f'"{value.isoformat()}"^^<{XSD}dateTime>'
    if isinstance(value, (list, dict)):
        return f'"{_escape(json.dumps(value, ensure_ascii=False, sort_keys=False))}"^^<{RDF_JSON}>'
    return f'"{_escape(str(value))}"'


def _escape(s: str) -> str:
    return "".join(_ESCAPES.get(ch, ch) for ch in s)


def values(var: str, items: Iterable[str], as_iri: bool = True) -> str:
    terms = " ".join(iri(x) if as_iri else lit(x) for x in items)
    return f"VALUES ?{var} {{ {terms} }}"


_TERM_RE = re.compile(
    r'^(?:<(?P<iri>[^>]*)>|(?P<bnode>_:\S+)|"(?P<lex>(?:[^"\\]|\\.)*)"'
    r'(?:\^\^<(?P<dt>[^>]*)>|@(?P<lang>[A-Za-z0-9-]+))?)$'
)
_UNESC = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\", "b": "\b", "f": "\f"}


def _unescape(s: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        nxt = s[i + 1]
        if nxt == "u":
            out.append(chr(int(s[i + 2 : i + 6], 16)))
            i += 6
        elif nxt == "U":
            out.append(chr(int(s[i + 2 : i + 10], 16)))
            i += 10
        else:
            out.append(_UNESC.get(nxt, nxt))
            i += 2
    return "".join(out)


def parse_term(term: str | None) -> Any:
    """N-Triples term → Python: IRIs and blank nodes as str, literals typed."""
    if term is None:
        return None
    m = _TERM_RE.match(term)
    if not m:
        raise ValueError(f"not an N-Triples term: {term!r}")
    if m.group("iri") is not None:
        return m.group("iri")
    if m.group("bnode") is not None:
        return m.group("bnode")
    lex = _unescape(m.group("lex"))
    dt = m.group("dt")
    if dt is None:
        return lex
    if dt == f"{XSD}boolean":
        return lex == "true"
    if dt in (f"{XSD}integer", f"{XSD}int", f"{XSD}long", f"{XSD}short", f"{XSD}nonNegativeInteger"):
        return int(lex)
    if dt in (f"{XSD}double", f"{XSD}decimal", f"{XSD}float"):
        return float(lex)
    if dt == f"{XSD}dateTime":
        return datetime.fromisoformat(lex)
    if dt == RDF_JSON:
        return json.loads(lex)
    return lex


class GardendError(RuntimeError):
    pass


class GardendClient:
    """One graph on one gardend, reached with one bearer token."""

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        graph_id: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.url = (url or os.environ.get("GARDEND_URL") or _DEFAULT_URL).rstrip("/")
        self.token = token or os.environ.get("GARDEND_TOKEN") or ""
        self.graph_id = graph_id or os.environ.get("GARDEND_GRAPH_ID") or _DEFAULT_GRAPH
        self._http = httpx.Client(
            base_url=self.url,
            headers={"authorization": f"Bearer {self.token}"},
            timeout=timeout,
        )

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        response = self._http.post(path, json=body)
        try:
            payload = response.json()
        except ValueError:
            raise GardendError(f"{path}: HTTP {response.status_code}: {response.text[:300]}")
        if response.status_code >= 400 or payload.get("ok") is False:
            raise GardendError(f"{path}: HTTP {response.status_code}: {payload.get('error', payload)}")
        return payload

    def ensure_graph(self, title: str | None = None) -> None:
        try:
            self._post("/api/graphs", {"graphId": self.graph_id, "title": title or self.graph_id})
        except GardendError as exc:
            if "already" not in str(exc).lower() and "exists" not in str(exc).lower():
                raise

    def select(self, sparql: str) -> list[dict[str, Any]]:
        payload = self._post("/api/sparql/query", {"graphId": self.graph_id, "sparql": sparql})
        rows = payload.get("rows") or []
        return [{k: parse_term(v) for k, v in row.items()} for row in rows]

    def scalar(self, sparql: str, var: str) -> Any:
        rows = self.select(sparql)
        return rows[0].get(var) if rows else None

    def update(self, sparql: str) -> None:
        self._post("/api/sparql/update", {"graphId": self.graph_id, "sparql": sparql})

    def load(self, data: str, format: str = "ntriples") -> int:
        payload = self._post(
            "/api/rdf/load", {"graphId": self.graph_id, "format": format, "data": data}
        )
        return int(payload.get("quadCount") or 0)

    def load_lines(self, lines: Iterable[str], chunk: int = 20000) -> int:
        """Load N-Triples in chunks (each line is one triple, so any cut is safe)."""
        total = 0
        buf: list[str] = []
        for line in lines:
            buf.append(line)
            if len(buf) >= chunk:
                total += self.load("\n".join(buf) + "\n")
                buf = []
        if buf:
            total += self.load("\n".join(buf) + "\n")
        return total

    # -- MCP ------------------------------------------------------------------

    _mcp_session: str | None = None

    def mcp_call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call one of gardend's MCP tools (``find`` is the one the VDB uses)."""
        headers = {"accept": "application/json, text/event-stream"}
        if self._mcp_session is None:
            init = self._http.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "auto-ontology", "version": "0"}}},
                headers=headers,
            )
            self._mcp_session = init.headers.get("mcp-session-id") or ""
            if self._mcp_session:
                headers["mcp-session-id"] = self._mcp_session
            self._http.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers)
        if self._mcp_session:
            headers["mcp-session-id"] = self._mcp_session
        response = self._http.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": arguments}},
            headers=headers,
        )
        payload = None
        for line in response.text.splitlines():
            line = line.removeprefix("data: ").strip()
            if line.startswith("{"):
                try:
                    payload = json.loads(line)
                except ValueError:
                    continue
        if not payload or "error" in payload:
            raise GardendError(f"mcp {tool}: {payload.get('error') if payload else response.text[:300]}")
        result = payload.get("result") or {}
        if result.get("isError"):
            raise GardendError(f"mcp {tool}: {result}")
        structured = result.get("structuredContent")
        if structured is not None:
            return structured
        for item in result.get("content") or []:
            if item.get("type") == "text":
                try:
                    return json.loads(item["text"])
                except ValueError:
                    return {"text": item["text"]}
        return result

    def count(self) -> int:
        return int(self.scalar(f"SELECT (COUNT(*) AS ?n) WHERE {{ {wrap('?s ?p ?o')} }}", "n") or 0)


_client: GardendClient | None = None


def client() -> GardendClient:
    global _client
    if _client is None:
        _client = GardendClient()
    return _client

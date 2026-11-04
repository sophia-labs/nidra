# SPDX-License-Identifier: Apache-2.0
"""Grade a write port: the same write on Postgres and on gardend must leave
the catalog in the same state, as every DAL read sees it.

For one write (a dotted callable plus JSON arguments) this runs two fresh
copies of the fixture, applies the write on each backend in its own process,
captures every DAL read (``capture_dal_golden.capture_all``, the same
normalisation the golden uses) and diffs the two captures:

    uv run --no-sync python -m dev_tools.fixtures.write_diff \\
        auto_ontology.dal.terms:update_term --kwargs '{"term_id": "<term:Film>", "name": "Movie"}'

Arguments may name fixture ids by golden token (``<term:Film>``,
``<table:pagila.public.film>``, ``<column:pagila.public.film.title>``); they are
resolved on each backend before the call.

Postgres copy: ``CREATE DATABASE <name> TEMPLATE <fixture db>`` (seconds), then
``POSTGRES_DATABASE`` points at it. gardend copy: a new graph loaded from
``dev_tools/fixtures/sql/catalog_fixture.nt`` (the Direct-Mapping export of
the seeded fixture; refresh it with ``--refresh-fixture`` after reseeding).
Both copies are dropped afterwards unless ``--keep``.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

FIXTURE_NT = Path(__file__).resolve().parent / "sql" / "catalog_fixture.nt"


def _resolve_tokens(value: Any, id_map_inv: dict[str, str]) -> Any:
    if isinstance(value, str) and value.startswith("<") and value.endswith(">"):
        if value not in id_map_inv:
            raise SystemExit(f"unknown fixture token {value}")
        return id_map_inv[value]
    if isinstance(value, list):
        return [_resolve_tokens(v, id_map_inv) for v in value]
    if isinstance(value, dict):
        return {k: _resolve_tokens(v, id_map_inv) for k, v in value.items()}
    return value


def _child(target: str, kwargs: dict[str, Any], out: Path) -> None:
    """Runs in the child process, with the backend chosen by the environment."""
    import importlib

    from dev_tools.fixtures import capture_dal_golden

    module_name, fn_name = target.split(":")
    fn = getattr(importlib.import_module(module_name), fn_name)
    # Both runs share one Postgres copy (reads without a port fall through to it),
    # the postgres run first. The rdf run would then look the fixture up *after*
    # the write (a deleted row is "not found"), so the postgres run records the
    # pre-write ids and tokens and the rdf run reuses them.
    pre = out.with_name(out.name.rsplit("-", 1)[0] + ".pre")
    if os.environ.get("CATALOG_READ_BACKEND", "postgres") != "rdf" or not pre.is_file():
        ids_before = capture_dal_golden._fixture_ids()
        inv = {token: real for real, token in capture_dal_golden.build_id_map().items()}
        pre.write_text(json.dumps({"ids": ids_before, "inv": inv}, default=str))
    else:
        saved = json.loads(pre.read_text())
        ids_before, inv = saved["ids"], saved["inv"]
    result = fn(**_resolve_tokens(kwargs, inv))
    # The fixture ids are looked up by name; a write may rename or delete what
    # they name, so the capture keeps the pre-write ids. Tokens for rows the
    # write created come from the post-write id map, identically on both sides.
    capture_dal_golden._fixture_ids = lambda: ids_before
    try:
        cap = capture_dal_golden.capture_all()
    except Exception as exc:  # noqa: BLE001
        # WD_TOLERANT=1: a write that deletes what the capture itself looks up (a whole
        # database) makes capture_all raise; compare what the driver dumped instead.
        if not os.environ.get("WD_TOLERANT"):
            raise

        class cap:  # noqa: N801
            results: dict = {}
            errors = {"capture_all": type(exc).__name__}

    payload = {
        "backend": os.environ.get("CATALOG_READ_BACKEND", "postgres"),
        "returned": capture_dal_golden.normalise(result, capture_dal_golden.build_id_map()),
        "results": cap.results,
        "errors": cap.errors,
    }
    out.write_text(json.dumps(payload, indent=1, sort_keys=True, default=str))


def _pg(sql: str, dbname: str = "postgres") -> None:
    import psycopg

    with psycopg.connect(
        host=os.environ["POSTGRES_HOST"], port=os.environ["POSTGRES_PORT"], user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"], dbname=dbname, autocommit=True,
    ) as conn:
        conn.execute(sql)


def _copy_db(source: str, target: str) -> None:
    """``pg_dump | psql``: a template copy needs an idle source, a dump does not."""
    _pg(f'CREATE DATABASE "{target}"')
    env = {**os.environ, "PGPASSWORD": os.environ["POSTGRES_PASSWORD"]}
    conn = ["-h", os.environ["POSTGRES_HOST"], "-p", os.environ["POSTGRES_PORT"], "-U", os.environ["POSTGRES_USER"]]
    dump = subprocess.Popen(["pg_dump", *conn, "--no-owner", source], stdout=subprocess.PIPE, env=env)
    load = subprocess.run(["psql", *conn, "-q", "-v", "ON_ERROR_STOP=1", "-d", target], stdin=dump.stdout, env=env, capture_output=True, text=True)
    dump.wait()
    if dump.returncode or load.returncode:
        raise SystemExit(f"copy {source} -> {target} failed: {load.stderr[-800:]}")


def _gardend_copy(graph_id: str) -> None:
    from auto_ontology.rdf.client import GardendClient

    c = GardendClient(graph_id=graph_id)
    c.ensure_graph(f"write-diff {graph_id}")
    c.load_lines(FIXTURE_NT.read_text().splitlines())


def _refresh_fixture() -> None:
    from auto_ontology.rdf.export import export_all

    FIXTURE_NT.write_text("\n".join(export_all()) + "\n")
    print(f"wrote {FIXTURE_NT} ({sum(1 for _ in FIXTURE_NT.open())} triples)")


def _diff(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if a["returned"] != b["returned"]:
        lines.append(f"returned: postgres={json.dumps(a['returned'], default=str)[:300]} rdf={json.dumps(b['returned'], default=str)[:300]}")
    for name in sorted(set(a["results"]) | set(b["results"])):
        if a["results"].get(name) != b["results"].get(name):
            lines.append(f"read {name}: postgres={json.dumps(a['results'].get(name), default=str)[:300]} rdf={json.dumps(b['results'].get(name), default=str)[:300]}")
    for name in sorted(set(a["errors"]) | set(b["errors"])):
        if a["errors"].get(name) != b["errors"].get(name):
            lines.append(f"error {name}: postgres={a['errors'].get(name)} rdf={b['errors'].get(name)}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", nargs="?", help="module:function")
    parser.add_argument("--kwargs", default="{}", help="JSON keyword arguments; <token> values are fixture ids")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--refresh-fixture", action="store_true")
    parser.add_argument("--_child", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.refresh_fixture:
        _refresh_fixture()
        return 0
    if args._child:
        _child(args.target, json.loads(args.kwargs), Path(args._child))
        return 0
    if not args.target:
        parser.error("target required")
    if not FIXTURE_NT.is_file():
        raise SystemExit(f"{FIXTURE_NT} missing: run with --refresh-fixture on the seeded Postgres fixture first")
    tag = uuid.uuid4().hex[:8]
    pg_db = f"wd_{tag}"
    graph = f"wd-{tag}"
    fixture_db = os.environ["POSTGRES_DATABASE"]
    outs = {}
    try:
        _copy_db(fixture_db, pg_db)
        _gardend_copy(graph)
        for backend, env in (
            ("postgres", {"CATALOG_READ_BACKEND": "postgres", "POSTGRES_DATABASE": pg_db}),
            ("rdf", {"CATALOG_READ_BACKEND": "rdf", "AO_RDF_STRICT": os.environ.get("AO_RDF_STRICT", "0"), "POSTGRES_DATABASE": pg_db, "GARDEND_GRAPH_ID": graph}),
        ):
            out = Path(os.environ.get("TMPDIR", "/tmp")) / f"write-diff-{tag}-{backend}.json"
            outs[backend] = out
            proc = subprocess.run(
                [sys.executable, "-m", "dev_tools.fixtures.write_diff", args.target, "--kwargs", args.kwargs, "--_child", str(out)],
                env={**os.environ, **env},
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                print(proc.stdout[-2000:], proc.stderr[-4000:], sep="\n")
                raise SystemExit(f"{backend} run failed")
        a = json.loads(outs["postgres"].read_text())
        b = json.loads(outs["rdf"].read_text())
        diff = _diff(a, b)
        if diff:
            print(f"DIFFERENT ({len(diff)}):")
            for line in diff[:60]:
                print("  " + line)
            return 1
        print(f"SAME: {len(a['results'])} reads identical after {args.target}")
        return 0
    finally:
        if not args.keep:
            try:
                _pg(f'DROP DATABASE IF EXISTS "{pg_db}"')
            except Exception as exc:  # noqa: BLE001
                print(f"(could not drop {pg_db}: {exc})")
            # the gardend graph is small; left in place, named wd-<tag>


if __name__ == "__main__":
    raise SystemExit(main())

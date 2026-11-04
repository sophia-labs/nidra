"""Run natural-language questions and grade each answer against the source
database itself (SQLite for Chinook, Postgres for Pagila)."""
from __future__ import annotations

from typing import Any, Callable

from demo import NIDRA, SIDE, Demo, ask, chinook, pagila, show_answer, side_up


def run(d: Demo, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each item: {db, question, truth_sql, grade(rows, truth) -> (bool, why), engine?}.
    Chinook questions go to the live engine; Pagila questions to the side engine
    (the live one has no Pagila connection: demos 02 and 03)."""
    results = []
    for it in items:
        db = it["db"]
        base = it.get("engine") or (NIDRA if db == "chinook" else SIDE)
        d.section(f"{db}: {it['question']}")
        if base == SIDE and not side_up():
            d.check(False, f"side engine {SIDE} is not running (demos/lib/side-engine.sh start)")
            continue
        truth = (chinook if db == "chinook" else pagila)(it["truth_sql"])
        d.log(f"ground truth ({db}, run directly on the source): {it['truth_sql']}")
        d.log(f"  -> {[tuple(str(x) for x in r) for r in truth][:10]}")
        a = ask(it["question"], base=base)
        show_answer(d, a)
        grade: Callable[[list[Any], list[tuple]], tuple[bool, str]] = it["grade"]
        if it.get("expect") == "decline":
            ok = not a.get("rows") and not (a.get("final_sql") or "").strip()
            why = "declined (no SQL, no rows) instead of answering from the wrong source" if ok else "answered anyway"
        else:
            ok, why = grade(a.get("rows") or [], truth) if a.get("rows") else (False, "no rows came back")
        label = it.get("label", it["question"])
        d.check(ok, f"{db} [{'live' if base == NIDRA else 'side'} engine] {label}: {why}")
        a["graded"] = ok
        results.append(a)
    return results

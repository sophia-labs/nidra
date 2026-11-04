"""Beyond: memory. Remember an answer with its SQL and provenance, then recall
it from a paraphrase (in English and Spanish). LABELLED WRITE: one
sophia-memory-core record in ao-catalog's memory commons."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from answers import get_answer  # noqa: E402
from demo import GRAPH, MCP, Demo  # noqa: E402

d = Demo("25-memory", "remember an answer with its SQL; recall it from a paraphrase",
         "gardend keeps provenance-anchored memories and recalls them by meaning. Upstream's chat keeps the last five "
         "turns of one conversation for rewriting, and its 'similar questions from conversations' slot is never filled (demo 16).")
m = MCP()

d.section("the answer to remember")
a = get_answer()
d.log(f"source: {a.get('source')}" + (f"; fresh attempt said: {a['fresh_attempt']!r}" if a.get("fresh_attempt") else ""))
d.log(f"question: {a['question']}")
d.log(f"sql: {a['final_sql']}")
d.log(f"rows: {json.dumps(a['rows'])}")
d.check(bool(a.get("rows")), "an engine answer with SQL and rows is at hand")

content = (f"Q: {a['question']}\nA: {json.dumps(a['rows'])} (Chinook, via Nidra)\nSQL: {a['final_sql']}\n"
           f"Note: 'song' is the glossary synonym of Track; five minutes = Milliseconds > 300000.")
d.section("remember")
ok, r = m.call("remember", {"graphId": GRAPH, "content": content, "kind": "ClaimMemory", "scope": "graph",
                            "contentOrientation": "knowledge", "visibility": "shared", "status": "active",
                            "tags": ["nidra-demo-25", "answer", "chinook"],
                            "source_refs": [{"sourceKind": "ToolResult", "sourceLabel": f"Nidra POST /api/chat/completions ({a.get('source')})",
                                             "externalUri": f"{a.get('engine', 'http://127.0.0.1:3001')}/api/chat/completions"}]})
d.show("remember", r, 800)
subject = r.get("subject")
d.check(ok and bool(subject), "remembered, with a provenance anchor")

d.section("recall by substring (recall)")
ok, rc = m.call("recall", {"graphId": GRAPH, "query": "longer than five minutes", "limit": 5})
mems = rc.get("memories", [])
d.log(f"recall 'longer than five minutes': {len(mems)} hit(s)")
d.check(any(a["final_sql"] in (x.get("content") or "") for x in mems), "substring recall finds it")

import time  # noqa: E402

ok, probe = m.call("memory_semantic_recall", {"graphId": GRAPH, "query": "probe", "k": 1})
if not ok and "not prepared" in str(probe):
    d.section("the local embedding model is not prepared on this gardend: prepare_semantic_model (a one-time download)")
    d.log(f"memory_semantic_recall said: {probe.get('message') if isinstance(probe, dict) else probe}")
    ok, job = m.call("prepare_semantic_model", {"graphId": GRAPH})
    jid = job.get("job_id") or job.get("jobId")
    for _ in range(150):
        time.sleep(2)
        ok, st = m.call("get_job_status", {"graphId": GRAPH, "jobId": jid})
        if st.get("status") in ("succeeded", "failed", "cancelled"):
            break
    d.log(f"prepare_semantic_model {jid}: {st.get('status')} ({(st.get('detail') or {}).get('display_name')})")

for q in ("how many tracks run over 5 min?", "¿cuántas canciones duran más de cinco minutos?", "length of the longest invoice"):
    d.section(f"recall by meaning (memory_semantic_recall): {q}")
    ok, sr = m.call("memory_semantic_recall", {"graphId": GRAPH, "query": q, "k": 3})
    if not ok:
        d.log(f"  error: {sr}")
        sr = {}
    hits = sr.get("hits", [])
    for h in hits:
        d.log(f"  {h.get('score', 0):.3f} {str(h.get('content'))[:110]!r}")
    d.log(f"  model={sr.get('modelId')} dims={sr.get('dimensions')} candidates={sr.get('candidateCount')}")
    top = hits[0] if hits else {}
    if q.startswith("length of"):
        d.log("  (a control: an unrelated question; whatever comes back, the score should sit below the paraphrases')")
    else:
        d.check(top.get("subject") == subject, "the paraphrase recalls the remembered answer first")
d.log("The recall model on this gardend is BGE Small EN v1.5 (English); the Spanish paraphrase is a test of that, "
      "not of find's multilingual e5 index (demo 21).")
d.finish()

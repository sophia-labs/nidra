"""Beyond: wires and comments on catalog objects, and their survival through an
ingest sweep. LABELLED WRITE to ao-catalog: one whiteboard and one note
document (folder-less, titled 'nidra demo 24 …'), one wire from the glossary
term Customer to Chinook's Customer table, one wire and one comment on
Chinook's Track.Milliseconds column."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from demo import GRAPH, MCP, Demo, sparql, term  # noqa: E402
from ingest import trigger  # noqa: E402

d = Demo("24-wires-comments-ingest", "a wire term -> table, a comment on a column, then an ingest sweep",
         "Annotations live in the same graph as the catalog, on the catalog's own IRIs, and an ingest sweep leaves "
         "them standing. Upstream's glossary has no annotation layer beside its tables.")
m = MCP()


def one(q):
    r = sparql(m, q)
    return term(r[0]["s"]) if r else None


customer_term = one('SELECT ?s WHERE { GRAPH ?g { ?s a <urn:ao:term> ; <urn:ao:term#name> "Customer" } }')
chinook_customer = one('SELECT ?s WHERE { GRAPH ?g { ?s a <urn:ao:catalog_table> ; <urn:ao:catalog_table#name> "Customer" } }')
millis = one('SELECT ?s WHERE { GRAPH ?g { ?s <urn:ao:catalog_column#name> "Milliseconds" } }')
d.log(f"term Customer      {customer_term}  (represents pagila.public.customer today)")
d.log(f"table Customer     {chinook_customer}  (chinook.main.Customer)")
d.log(f"column Milliseconds {millis}  (chinook.main.Track)")


def find_doc(title):
    ok, v = m.call("search_documents", {"graphId": GRAPH, "query": title, "mode": "exact"})
    for x in v.get("documents") or v.get("results") or []:
        if x.get("title") == title:
            return x.get("documentId") or x.get("id")
    return None


d.section("a whiteboard to draw the term -> table wire on (an object-to-object wire needs a scene)")
board_title = "nidra demo 24: catalog annotations"
board = find_doc(board_title)
if not board:
    ok, b = m.call("whiteboard_create", {"graphId": GRAPH, "title": board_title})
    board = b.get("documentId")
d.log(f"board {board}")

d.section("wire: term Customer --should-also-represent--> chinook.main.Customer")
ok, wires = m.call("get_wires", {"graphId": GRAPH, "iri": customer_term, "direction": "outgoing"})
existing = [w for w in (wires.get("wires") or []) if w.get("targetIri") == chinook_customer]
if not existing:
    ok, cw = m.call("create_wires", {"graphId": GRAPH, "sourceIri": customer_term, "targetIri": chinook_customer,
                                     "predicate": "should-also-represent", "sceneDocumentId": board, "sceneGraphId": GRAPH})
    d.show("create_wires", cw, 1000)
    d.check(ok, "wire created")
else:
    d.log(f"wire already there from an earlier run: {existing[0].get('wireId') or existing[0].get('id')}")

d.section("a note document that embeds the column, a wire to it, and a comment on it")
note_title = "nidra demo 24: Track.Milliseconds"
note = find_doc(note_title)
if not note:
    ok, nd = m.call("create_document", {"graphId": GRAPH, "title": note_title})
    note = nd["documentId"]
    tiptap = {"type": "doc", "content": [
        {"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": note_title}]},
        {"type": "objectEmbed", "attrs": {"iri": millis, "slot": "card", "label": "Track.Milliseconds", "classLabel": "catalog column"}},
        {"type": "paragraph", "content": [{"type": "text", "text": "Milliseconds is the track length; five minutes is 300000. "
                                                                   "214 of the rows are MPEG-4 video, not music."}]}]}
    ok, wd = m.call("write_document", {"graphId": GRAPH, "documentId": note, "tiptapJson": tiptap, "title": note_title})
    d.check(ok, "note written with the column embedded as an object card")
    ok, cw2 = m.call("create_wires", {"graphId": GRAPH, "sourceDocumentId": note, "targetIri": millis, "predicate": "annotates"})
    d.check(ok, "wire note --annotates--> column")

import re  # noqa: E402

ok, rd = m.call("read_document", {"graphId": GRAPH, "documentId": note})
para = re.findall(r'<paragraph data-block-id="([^"]+)">[^<]*300000', rd.get("content", ""))
ok, cm = m.call("edit_comment", {"graphId": GRAPH, "documentId": note, "commentId": "nidra-demo-24-unit",
                                 "action": "set", "author": "nidra-demos", "blockId": para[0] if para else None, "find": "300000",
                                 "text": "Unit check: the column is milliseconds, not seconds. A question about minutes needs * 60000."})
d.show("edit_comment", {k: cm.get(k) for k in ("success", "anchored", "blockId", "quotedText")}, 800)
d.check(ok and (cm.get("anchored") or 0) >= 1, "comment anchored on '300000' in the paragraph about the column")
if not para:
    d.note("could not find the paragraph block to anchor the comment on")


def state():
    ok, w1 = m.call("get_wires", {"graphId": GRAPH, "iri": customer_term, "direction": "outgoing"})
    ok, w2 = m.call("get_wires", {"graphId": GRAPH, "iri": millis, "direction": "incoming"})
    ok, rd = m.call("read_document", {"graphId": GRAPH, "documentId": note})
    alive = {x: bool(sparql(m, f"SELECT ?p WHERE {{ GRAPH ?g {{ <{x}> ?p ?o }} }} LIMIT 1")) for x in (chinook_customer, millis)}
    return {"term_wires": [(w.get("predicate"), w.get("targetIri")) for w in (w1.get("wires") or [])],
            "column_wires_in": [(w.get("predicate"), w.get("sourceDocumentId")) for w in (w2.get("wires") or [])],
            "comments": [(c.get("id"), c.get("quotedText"), c.get("resolved")) for c in (rd.get("comments") or [])],
            "targets_exist": alive}


before = state()
d.show("before the sweep", before, 1500)

d.section("the ingest sweep: docker exec nidra-ingestion POST /ingest {database: chinook}")
res = trigger("chinook")
d.log(f"{res['reply']} -> {res['finished']}")
d.check(bool(res["finished"]) and "successfully" in res["finished"], "Chinook re-ingested")
after = state()
d.show("after the sweep", after, 1500)
d.check(after == before, "wires, comment and their catalog targets are unchanged after the sweep")
d.check(after["targets_exist"][chinook_customer] and after["targets_exist"][millis], "the IRIs the annotations point at still exist")
d.check(any(p == "should-also-represent" for p, _ in after["term_wires"]), "the term -> table wire survives")
d.check(any(c[0] == "nidra-demo-24-unit" for c in after["comments"]), "the comment survives")
d.log("Why it survives: the ingest is a diff keyed by the catalog's own ids (demo 13), so the rows keep their IRIs, "
      "and the annotations are separate workspace/document state that ingestion never touches.")
d.log(f"made by this demo: board {board}, note {note} (see demo 29 to remove them)")
d.finish()

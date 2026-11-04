"""Captures for the governance lane: the reads of dal.tags, dal.rules, dal.groups
(``__g_`` names) and the zone-target state, taken from whatever state the catalog
is in. On the plain fixture they record the empty and not-found paths; after a
write (``write_diff``) they enumerate every tag, rule and group that exists, so
a write is graded by everything a read can see."""
from __future__ import annotations

from typing import Any

MISSING = "00000000-0000-0000-0000-00000000dead"
_VOLATILE = ("modified", "tagged")


def _scrub(value: Any) -> Any:
    """Redact the stamps a write sets from the wall clock (kept as set / not set)."""
    if isinstance(value, dict):
        return {
            k: (None if v is None else "<stamp>") if k in _VOLATILE else _scrub(v) for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, set):
        return sorted(value)
    return value


def _fact(fn):
    """The value, or the exception type as a value (an erroring read is a fact,
    but ``Capture.errors`` must stay empty)."""
    try:
        return _scrub(fn())
    except Exception as exc:  # noqa: BLE001
        return {"raised": type(exc).__name__}


def capture(cap, ids: dict[str, Any]) -> None:
    from auto_ontology.dal import groups, rules, tags, zones

    run = cap.run
    id_map = cap._id_map

    all_tags = tags.list_tags()
    all_rules = rules.list_rules()
    try:
        all_groups = groups.list_groups()
    except Exception:  # noqa: BLE001 -- members need frontend."user", which the plain fixture lacks
        all_groups = []
    for tag in all_tags:
        id_map[tag["id"]] = f"<tag:{tag['name']}>"
    for rule in all_rules:
        id_map[rule["id"]] = f"<rule:{rule['name']}>"
    for group in all_groups:
        id_map[group["id"]] = f"<group:{group['name']}>"

    # -- tags ----------------------------------------------------------------
    run("tags.list_tags__g_all", lambda: _scrub(tags.list_tags()))
    run("tags.count_tags__g_all", lambda: tags.count_tags())
    for label, search in (("ci", "PII"), ("pct", "%"), ("under", "_"), ("blank", "   "), ("none", "zzzz"), ("sub", "a")):
        run(f"tags.list_tags__g_search_{label}", lambda s=search: _scrub(tags.list_tags(search=s)))
        run(f"tags.count_tags__g_search_{label}", lambda s=search: tags.count_tags(search=s))
    for label, kw in (("skip1", {"skip": 1}), ("limit1", {"limit": 1}), ("skip1_limit1", {"skip": 1, "limit": 1}), ("limit0", {"limit": 0})):
        run(f"tags.list_tags__g_page_{label}", lambda k=kw: _scrub(tags.list_tags(**k)))
    run("tags.get_tag__g_missing", lambda: tags.get_tag(MISSING))
    run("tags.get_tag_by_name__g_pii", lambda: _scrub(tags.get_tag_by_name("pii")))
    run("tags.get_tag_by_name__g_missing", lambda: tags.get_tag_by_name("no such tag"))
    run("tags.existing_tag_ids__g_empty", lambda: tags.existing_tag_ids([]))
    run("tags.existing_tag_ids__g_missing", lambda: tags.existing_tag_ids([MISSING]))
    run("tags.existing_tag_ids__g_all", lambda: tags.existing_tag_ids([t["id"] for t in all_tags] + [MISSING]))
    run("tags.list_tag_targets__g_missing", lambda: tags.list_tag_targets(MISSING))
    run("tags.count_tag_targets__g_missing", lambda: tags.count_tag_targets(MISSING))
    for tag in all_tags:
        label = tag["name"]
        run(f"tags.get_tag__g_{label}", lambda t=tag: _scrub(tags.get_tag(t["id"])))
        run(f"tags.list_tag_targets__g_{label}", lambda t=tag: _scrub(tags.list_tag_targets(t["id"])))
        run(f"tags.list_tag_targets__g_{label}_page", lambda t=tag: _scrub(tags.list_tag_targets(t["id"], skip=1, limit=2)))
        run(f"tags.count_tag_targets__g_{label}", lambda t=tag: tags.count_tag_targets(t["id"]))
    # What each fixture object carries (``fetch_tags_map`` is the scope lane's port).
    items = {
        "table": [ids["table_film"], ids["table_rental"], ids["table_customer"], ids["table_track"]],
        "term": [ids["term_Film"], ids["term_Customer"], ids["term_Payment"], ids["term_Track"]],
    }
    for kind, item_ids in items.items():
        run(f"tags.fetch_tags_map__g_{kind}", lambda k=kind, i=item_ids: _scrub(tags.fetch_tags_map(k, i)))

    # -- rules -----------------------------------------------------------------
    run("rules.list_rules__g_all", lambda: _scrub(rules.list_rules()))
    run("rules.count_rules__g_all", lambda: rules.count_rules())
    for label, search in (("blank", "  "), ("none", "zzzz"), ("a", "a"), ("pct", "%")):
        run(f"rules.list_rules__g_search_{label}", lambda s=search: _scrub(rules.list_rules(search=s)))
        run(f"rules.count_rules__g_search_{label}", lambda s=search: rules.count_rules(search=s))
    for label, kw in (("skip1", {"skip": 1}), ("limit1", {"limit": 1}), ("limit0", {"limit": 0})):
        run(f"rules.list_rules__g_page_{label}", lambda k=kw: _scrub(rules.list_rules(**k)))
    for rule in all_rules:
        for tag in rule["tags"]:
            run(f"rules.list_rules__g_tagsearch_{tag['name']}", lambda n=tag["name"]: _scrub(rules.list_rules(search=n)))

    # -- groups (account reads need frontend."user", which only a deployment has: the write drivers cover them) --
    run("groups.list_groups__g_all", lambda: _fact(groups.list_groups))
    run("groups.get_group__g_missing", lambda: _fact(lambda: groups.get_group(MISSING)))
    run("groups.groups_for_user__g_nobody", lambda: _fact(lambda: groups.groups_for_user("nobody")))
    for group in all_groups:
        run(f"groups.get_group__g_{group['name']}", lambda g=group: _fact(lambda: groups.get_group(g["id"])))
        for user in group["users"]:
            run(f"groups.groups_for_user__g_{user['id']}", lambda u=user: _fact(lambda: groups.groups_for_user(u["id"])))

    # -- zone targets, as the zone reads see them ------------------------------------
    # One entry for all: two zones may share a name (in different databases).
    run("zones.get_zone_by_id__g_all", lambda: [zones.get_zone_by_id(z["id"]) for z in zones.list_zones()])
    run("zones.get_zone_by_id__g_missing", lambda: zones.get_zone_by_id(MISSING))

"""write_diff drivers for the governance lane (tags, zones, rules, groups).

Each case runs a short scenario of DAL writes on one backend and returns what
the calls answered, with volatile stamps redacted and generated ids replaced by
the names they were created under. ``write_diff`` then diffs the whole catalog
as every DAL read (including ``golden_extra/gov.py``) sees it afterwards.

    uv run --no-sync python -m dev_tools.fixtures.write_diff \\
        dev_tools.fixtures.write_cases.writes_gov:tag_create
"""
from __future__ import annotations

import os
from typing import Any

_STAMPS = ("modified", "tagged", "created")
ACCOUNTS = [
    ("u-ada", "Ada Lovelace", "ada@example.com", "admin"),
    ("u-bob", "Bob Barker", "bob@example.com", "viewer"),
    ("u-cy", "cy", "cy@example.com", "viewer"),
]


def _clean(value: Any, names: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {k: (None if v is None else "<stamp>") if k in _STAMPS else _clean(v, names) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v, names) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_clean(v, names) for v in value)
    if isinstance(value, str):
        return names.get(value, value)
    return value


class _Run:
    """Collects ``step -> answer`` for a scenario; exceptions are answers too."""

    def __init__(self) -> None:
        self.names: dict[str, str] = {}
        self.out: dict[str, Any] = {}

    def name(self, kind: str, row: dict[str, Any]) -> dict[str, Any]:
        self.names[row["id"]] = f"<{kind}:{row['name']}>"
        return row

    def step(self, label: str, fn) -> Any:
        try:
            value = fn()
            self.out[label] = _clean(value, self.names)
            return value
        except Exception as exc:  # noqa: BLE001
            # A driver's own error text (SQL, parameters) is not part of the contract.
            message = "" if type(exc).__name__ in ("IntegrityError", "ProgrammingError") else _clean(str(exc), self.names)
            self.out[label] = {"raised": type(exc).__name__, "message": message}
            return None


def _sel(kind: str, ids: list[str]):
    """A ``SELECT id`` for these objects: what ``dal.search.matching_id_selects`` yields,
    and what upstream ``apply_labels_now_matched`` needs (it calls ``.subquery()``)."""
    from sqlalchemy import select

    from auto_ontology.dal import schema as s

    table = {"term": s.term, "table": s.catalog_table, "column": s.catalog_column}[kind]
    return select(table.c.id).where(table.c.id.in_(ids))


def _accounts() -> None:
    """Better Auth's ``frontend."user"``, which the fixture lacks, in this run's Postgres copy."""
    import psycopg

    with psycopg.connect(
        host=os.environ["POSTGRES_HOST"], port=os.environ["POSTGRES_PORT"], user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"], dbname=os.environ["POSTGRES_DATABASE"], autocommit=True,
    ) as conn:
        conn.execute('CREATE SCHEMA IF NOT EXISTS frontend')
        conn.execute('CREATE TABLE IF NOT EXISTS frontend."user" (id text primary key, name text, email text, role text)')
        for row in ACCOUNTS:
            conn.execute('INSERT INTO frontend."user" VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING', row)


# ---------------------------------------------------------------------------
# tags
# ---------------------------------------------------------------------------


def tag_create() -> dict:
    from auto_ontology.dal import tags

    r = _Run()
    r.step("create", lambda: r.name("tag", tags.create_tag(name="Sensitive", created_by="u-ada")))
    r.step("create_anonymous", lambda: r.name("tag", tags.create_tag(name="Finance")))
    r.step("create_spaced", lambda: r.name("tag", tags.create_tag(name="  Padded ")))
    r.step("get_or_create_existing", lambda: tags.get_or_create_tag(name=" sensitive "))
    r.step("get_or_create_new", lambda: r.name("tag", tags.get_or_create_tag(name="  Brand New ", created_by="u-bob")))
    return r.out


def tag_conflict() -> dict:
    from auto_ontology.dal import tags

    r = _Run()
    r.step("create", lambda: r.name("tag", tags.create_tag(name="Sensitive")))
    r.step("dup_exact", lambda: tags.create_tag(name="Sensitive"))
    r.step("dup_case_trim", lambda: tags.create_tag(name="  SENSITIVE "))
    other = r.step("create_other", lambda: r.name("tag", tags.create_tag(name="Other")))
    r.step("rename_onto_other", lambda: tags.update_tag(tag_id=other["id"], name="sensitive"))
    r.step("by_name", lambda: tags.get_tag_by_name("SENSITIVE"))
    return r.out


def tag_update() -> dict:
    from auto_ontology.dal import tags

    r = _Run()
    t = r.step("create", lambda: r.name("tag", tags.create_tag(name="Sensitive", created_by="u-ada")))
    r.step("rename", lambda: tags.update_tag(tag_id=t["id"], name="Restricted", modified_by="u-bob"))
    r.step("case_only", lambda: tags.update_tag(tag_id=t["id"], name="RESTRICTED", modified_by="u-bob"))
    r.step("clears_modified_by", lambda: tags.update_tag(tag_id=t["id"], name="RESTRICTED"))
    r.step("unknown", lambda: tags.update_tag(tag_id="no-such-tag", name="Whatever"))
    return r.out


def tag_update_noop() -> dict:
    from auto_ontology.dal import tags

    r = _Run()
    t = r.step("create", lambda: r.name("tag", tags.create_tag(name="Sensitive", created_by="u-ada")))
    r.step("same_name", lambda: tags.update_tag(tag_id=t["id"], name="Sensitive"))
    r.step("same_name_again", lambda: tags.update_tag(tag_id=t["id"], name="Sensitive", modified_by="u-ada"))
    return r.out


def tag_delete(table_id: str, column_id: str, term_id: str) -> dict:
    from auto_ontology.dal import tags

    r = _Run()
    a = r.step("a", lambda: r.name("tag", tags.create_tag(name="Alpha")))
    b = r.step("b", lambda: r.name("tag", tags.create_tag(name="Beta")))
    r.step("attach_table_a", lambda: tags.attach_tag(tag_id=a["id"], kind="table", item_id=table_id, tagged_by="u-ada"))
    r.step("attach_table_b", lambda: tags.attach_tag(tag_id=b["id"], kind="table", item_id=table_id))
    r.step("attach_column_a", lambda: tags.attach_tag(tag_id=a["id"], kind="column", item_id=column_id))
    r.step("attach_term_a", lambda: tags.attach_tag(tag_id=a["id"], kind="term", item_id=term_id))
    r.step("targets_before", lambda: tags.list_tag_targets(a["id"]))
    r.step("delete_a", lambda: tags.delete_tag(a["id"]))
    r.step("delete_a_again", lambda: tags.delete_tag(a["id"]))
    r.step("delete_unknown", lambda: tags.delete_tag("no-such-tag"))
    r.step("table_tags_after", lambda: tags.fetch_tags_map("table", [table_id]))
    return r.out


def tag_attach_detach(table_id: str, column_id: str, term_id: str, attribute_id: str, sql_attribute_id: str) -> dict:
    from auto_ontology.dal import tags

    r = _Run()
    a = r.step("a", lambda: r.name("tag", tags.create_tag(name="Alpha")))
    b = r.step("b", lambda: r.name("tag", tags.create_tag(name="Beta")))
    r.step("attach", lambda: tags.attach_tag(tag_id=a["id"], kind="table", item_id=table_id, tagged_by="u-ada"))
    r.step("attach_twice_other_user", lambda: tags.attach_tag(tag_id=a["id"], kind="table", item_id=table_id, tagged_by="u-bob"))
    r.step("attach_second_tag", lambda: tags.attach_tag(tag_id=b["id"], kind="table", item_id=table_id))
    r.step("attach_column", lambda: tags.attach_tag(tag_id=a["id"], kind="column", item_id=column_id))
    r.step("attach_term", lambda: tags.attach_tag(tag_id=b["id"], kind="term", item_id=term_id))
    r.step("attach_attribute", lambda: tags.attach_tag(tag_id=a["id"], kind="column_attribute", item_id=attribute_id))
    r.step("attach_sql_attribute", lambda: tags.attach_tag(tag_id=a["id"], kind="sql_attribute", item_id=sql_attribute_id))
    r.step("attach_unknown_tag", lambda: tags.attach_tag(tag_id="no-such-tag", kind="table", item_id=table_id))
    r.step("attach_unknown_item", lambda: tags.attach_tag(tag_id=a["id"], kind="table", item_id="no-such-table"))
    r.step("attach_unknown_kind", lambda: tags.attach_tag(tag_id=a["id"], kind="bogus", item_id=table_id))
    r.step("targets_a", lambda: tags.list_tag_targets(a["id"]))
    r.step("targets_a_count", lambda: tags.count_tag_targets(a["id"]))
    r.step("detach", lambda: tags.detach_tag(tag_id=a["id"], kind="table", item_id=table_id))
    r.step("detach_again", lambda: tags.detach_tag(tag_id=a["id"], kind="table", item_id=table_id))
    r.step("detach_unknown_tag", lambda: tags.detach_tag(tag_id="no-such-tag", kind="table", item_id=table_id))
    r.step("detach_unknown_kind", lambda: tags.detach_tag(tag_id=a["id"], kind="bogus", item_id=table_id))
    return r.out


def tag_rule_labels(table_a: str, table_b: str, column_id: str, term_id: str) -> dict:
    """attach_tags_by_rule / apply_labels_now_matched / remove_labels_no_longer_matched."""
    from auto_ontology.dal import rules, tags

    r = _Run()
    a = r.step("a", lambda: r.name("tag", tags.create_tag(name="Alpha")))
    b = r.step("b", lambda: r.name("tag", tags.create_tag(name="Beta")))
    rule_id = r.step(
        "rule",
        lambda: rules.create_rule(
            name="R1", search_term="x", text_match_option="contains", filters={"k": [1, 2]}, tags=[a["id"], b["id"]], created_by="u-ada"
        ),
    )
    r.names[rule_id] = "<rule:R1>"
    r.step("hand_label", lambda: tags.attach_tag(tag_id=a["id"], kind="table", item_id=table_a, tagged_by="u-bob"))
    r.step(
        "apply",
        lambda: tags.apply_labels_now_matched(
            rule_id=rule_id, tag_ids=[a["id"], b["id"], a["id"]],
            targets={"table": _sel("table", [table_a, table_b]), "column": _sel("column", [column_id]), "term": _sel("term", [term_id])},
        ),
    )
    r.step(
        "apply_again",
        lambda: tags.apply_labels_now_matched(
            rule_id=rule_id, tag_ids=[a["id"], b["id"]], targets={"table": _sel("table", [table_a, table_b]), "column": _sel("column", [column_id])}
        ),
    )
    r.step("targets_a", lambda: tags.list_tag_targets(a["id"]))
    r.step("remove_unmatched", lambda: tags.remove_labels_no_longer_matched(rule_id=rule_id, targets={"table": [table_a]}))
    r.step("remove_unmatched_again", lambda: tags.remove_labels_no_longer_matched(rule_id=rule_id, targets={"table": [table_a]}))
    r.step("targets_a_after", lambda: tags.list_tag_targets(a["id"]))
    r.step("remove_all", lambda: tags.remove_labels_no_longer_matched(rule_id=rule_id, targets={}))
    r.step("targets_b_after", lambda: tags.list_tag_targets(b["id"]))
    r.step("by_rule", lambda: tags.attach_tags_by_rule(
        rule_id=rule_id, tag_ids=[a["id"], b["id"]], targets=[("table", table_a), ("table", table_b), ("table", table_a), ("column", column_id)]))
    r.step("by_rule_again", lambda: tags.attach_tags_by_rule(
        rule_id=rule_id, tag_ids=[a["id"], b["id"]], targets=[("table", table_a), ("column", column_id)]))
    r.step("targets_a_final", lambda: tags.list_tag_targets(a["id"]))
    return r.out


def tag_rule_select(table_a: str, table_b: str) -> dict:
    """A rule that does not exist: the foreign key on ``tag_target.rule_id`` refuses the labels."""
    from sqlalchemy import select

    from auto_ontology.dal import schema as s
    from auto_ontology.dal import tags

    r = _Run()
    a = r.step("a", lambda: r.name("tag", tags.create_tag(name="Alpha")))
    sel = select(s.catalog_table.c.id).where(s.catalog_table.c.id.in_([table_a, table_b]))
    r.step("apply", lambda: tags.apply_labels_now_matched(rule_id="no-rule", tag_ids=[a["id"]], targets={"table": sel}))
    return r.out


def tag_empty(table_a: str) -> dict:
    from auto_ontology.dal import tags

    r = _Run()
    r.step("apply_no_tags", lambda: tags.apply_labels_now_matched(rule_id="r", tag_ids=[], targets={"table": _sel("table", [table_a])}))
    r.step("apply_no_targets", lambda: tags.apply_labels_now_matched(rule_id="r", tag_ids=["t"], targets={}))
    r.step("by_rule_no_tags", lambda: tags.attach_tags_by_rule(rule_id="r", tag_ids=[], targets=[("table", table_a)]))
    r.step("by_rule_no_targets", lambda: tags.attach_tags_by_rule(rule_id="r", tag_ids=["t"], targets=[]))
    r.step("remove_nothing", lambda: tags.remove_labels_no_longer_matched(rule_id="r", targets={}))
    r.step("existing_empty", lambda: tags.existing_tag_ids([]))
    r.step("list", lambda: tags.list_tags())
    r.step("count", lambda: tags.count_tags())
    return r.out


def tag_pii_rule_removal(column_id: str) -> dict:
    """Removing a rule's PII label on a column runs the attribute cascade."""
    from auto_ontology.dal import rules, tags

    r = _Run()
    pii = r.step("pii", lambda: r.name("tag", tags.create_tag(name="PII")))
    rid = r.step("rule", lambda: rules.create_rule(
        name="Find PII", search_term="email", text_match_option="contains", filters={}, tags=[pii["id"]], created_by="u-ada"))
    r.step("apply", lambda: tags.apply_labels_now_matched(rule_id=rid, tag_ids=[pii["id"]], targets={"column": _sel("column", [column_id])}))
    r.step("remove", lambda: tags.remove_labels_no_longer_matched(rule_id=rid, targets={"column": []}))
    r.step("targets", lambda: tags.list_tag_targets(pii["id"]))
    return r.out


# ---------------------------------------------------------------------------
# rules
# ---------------------------------------------------------------------------


def rule_create() -> dict:
    from auto_ontology.dal import rules, tags

    r = _Run()
    a = r.step("a", lambda: r.name("tag", tags.create_tag(name="Alpha")))
    b = r.step("b", lambda: r.name("tag", tags.create_tag(name="Beta")))
    rid = r.step("create", lambda: rules.create_rule(
        name="Rule One", search_term="email", text_match_option="contains",
        filters={"object_types": ["column"], "n": 3, "nested": {"x": None}}, tags=[b["id"], a["id"]], created_by="u-ada"))
    r.names[rid] = "<rule:Rule One>"
    rid2 = r.step("create_second", lambda: rules.create_rule(
        name="Another", search_term="", text_match_option="exact", filters={}, tags=[a["id"]], created_by="u-bob"))
    r.names[rid2] = "<rule:Another>"
    r.step("list", lambda: rules.list_rules())
    r.step("search_tag_name", lambda: rules.list_rules(search="beta"))
    r.step("search_wild", lambda: rules.list_rules(search="%"))
    r.step("count", lambda: rules.count_rules())
    r.step("page", lambda: rules.list_rules(skip=1, limit=1))
    return r.out


def rule_conflict() -> dict:
    from auto_ontology.dal import rules, tags

    r = _Run()
    a = r.step("a", lambda: r.name("tag", tags.create_tag(name="Alpha")))
    kw = dict(search_term="s", text_match_option="contains", filters={}, created_by="u-ada")
    r.step("create", lambda: rules.create_rule(name="Rule One", tags=[a["id"]], **kw))
    r.step("dup_name", lambda: rules.create_rule(name=" RULE one ", tags=[a["id"]], **kw))
    r.step("no_tags", lambda: rules.create_rule(name="Tagless", tags=[], **kw))
    r.step("unknown_tag", lambda: rules.create_rule(name="Ghost", tags=["no-such-tag"], **kw))
    r.step("duplicate_tags", lambda: rules.create_rule(name="Twice", tags=[a["id"], a["id"]], **kw))
    r.step("list", lambda: rules.list_rules())
    return r.out


def rule_update() -> dict:
    from auto_ontology.dal import rules, tags

    r = _Run()
    a = r.step("a", lambda: r.name("tag", tags.create_tag(name="Alpha")))
    kw = dict(search_term="s", text_match_option="contains", filters={}, created_by="u-ada")
    one = r.step("one", lambda: rules.create_rule(name="Rule One", tags=[a["id"]], **kw))
    two = r.step("two", lambda: rules.create_rule(name="Rule Two", tags=[a["id"]], **kw))
    r.names[one], r.names[two] = "<rule:Rule One>", "<rule:Rule Two>"
    r.step("rename", lambda: rules.update_rule(rule_id=one, name="First Rule", modified_by="u-bob"))
    r.step("rename_onto_other", lambda: rules.update_rule(rule_id=one, name="rule two"))
    r.step("case_only", lambda: rules.update_rule(rule_id=one, name="FIRST RULE", modified_by="u-bob"))
    r.step("unknown", lambda: rules.update_rule(rule_id="no-such-rule", name="Nope"))
    return r.out


def rule_update_noop() -> dict:
    from auto_ontology.dal import rules, tags

    r = _Run()
    a = r.step("a", lambda: r.name("tag", tags.create_tag(name="Alpha")))
    one = r.step("one", lambda: rules.create_rule(
        name="Rule One", search_term="s", text_match_option="contains", filters={}, tags=[a["id"]], created_by="u-ada"))
    r.names[one] = "<rule:Rule One>"
    r.step("same_name", lambda: rules.update_rule(rule_id=one, name="Rule One"))
    r.step("same_name_by", lambda: rules.update_rule(rule_id=one, name="Rule One", modified_by="u-ada"))
    return r.out


def rule_delete(table_a: str, table_b: str, column_id: str) -> dict:
    """The rule's labels go, a tag left labelling nothing goes, one still held stays."""
    from auto_ontology.dal import rules, tags

    r = _Run()
    solo = r.step("solo", lambda: r.name("tag", tags.create_tag(name="Solo")))
    shared = r.step("shared", lambda: r.name("tag", tags.create_tag(name="Shared")))
    hand = r.step("hand", lambda: r.name("tag", tags.create_tag(name="Hand")))
    pii = r.step("pii", lambda: r.name("tag", tags.create_tag(name="pii")))
    kw = dict(search_term="s", text_match_option="contains", filters={}, created_by="u-ada")
    one = r.step("one", lambda: rules.create_rule(name="One", tags=[solo["id"], shared["id"], hand["id"], pii["id"]], **kw))
    two = r.step("two", lambda: rules.create_rule(name="Two", tags=[shared["id"]], **kw))
    r.names[one], r.names[two] = "<rule:One>", "<rule:Two>"
    r.step("apply", lambda: tags.apply_labels_now_matched(
        rule_id=one, tag_ids=[solo["id"], shared["id"], hand["id"], pii["id"]], targets={"table": _sel("table", [table_a, table_b]), "column": _sel("column", [column_id])}))
    r.step("hand_label", lambda: tags.attach_tag(tag_id=hand["id"], kind="table", item_id=table_a, tagged_by="u-bob"))
    r.step("delete", lambda: rules.delete_rule(one))
    r.step("delete_again", lambda: rules.delete_rule(one))
    r.step("delete_unknown", lambda: rules.delete_rule("no-such-rule"))
    r.step("tags_after", lambda: tags.list_tags())
    r.step("rules_after", lambda: rules.list_rules())
    return r.out


def rule_delete_keep_tags(table_a: str, table_b: str) -> dict:
    from auto_ontology.dal import rules, tags

    r = _Run()
    solo = r.step("solo", lambda: r.name("tag", tags.create_tag(name="Solo")))
    one = r.step("one", lambda: rules.create_rule(
        name="One", search_term="s", text_match_option="contains", filters={}, tags=[solo["id"]], created_by="u-ada"))
    r.names[one] = "<rule:One>"
    r.step("apply", lambda: tags.apply_labels_now_matched(
        rule_id=one, tag_ids=[solo["id"]], targets={"table": _sel("table", [table_a, table_b])}))
    r.step("delete", lambda: rules.delete_rule(one, keep_tags=True))
    r.step("delete_unknown", lambda: rules.delete_rule("no-such-rule", keep_tags=True))
    r.step("targets", lambda: tags.list_tag_targets(solo["id"]))
    return r.out


def rule_empty() -> dict:
    from auto_ontology.dal import rules

    r = _Run()
    r.step("list", lambda: rules.list_rules())
    r.step("search", lambda: rules.list_rules(search="x"))
    r.step("count", lambda: rules.count_rules())
    r.step("delete", lambda: rules.delete_rule("no-such-rule"))
    r.step("update", lambda: rules.update_rule(rule_id="no-such-rule", name="x"))
    return r.out


# ---------------------------------------------------------------------------
# groups
# ---------------------------------------------------------------------------


def _group(r: _Run, **kw: Any) -> dict:
    return r.name("group", kw)


def group_create() -> dict:
    from auto_ontology.dal import groups

    _accounts()
    r = _Run()
    g = r.step("create", lambda: _group(r, **groups.create_group(name="Analysts", description="Reads", color="#fff", role="viewer", user_ids=["u-bob", "u-ada"])))
    r.step("create_no_members", lambda: _group(r, **groups.create_group(name="Empty", description=None, color="#000", role="admin", user_ids=[])))
    r.step("create_orphan_member_unknown", lambda: groups.create_group(name="Ghosts", description=None, color="#000", role="admin", user_ids=["nobody", "u-ada", "x"]))
    r.step("create_bad_role", lambda: groups.create_group(name="Bad", description=None, color="#000", role="owner", user_ids=[]))
    r.step("create_dup_members", lambda: groups.create_group(name="Dup", description=None, color="#000", role="viewer", user_ids=["u-ada", "u-ada"]))
    r.step("list", lambda: groups.list_groups())
    r.step("get", lambda: groups.get_group(g["id"]))
    r.step("for_user", lambda: groups.groups_for_user("u-ada"))
    r.step("users_and_groups", lambda: groups.list_users_and_groups())
    r.step("account", lambda: groups.get_account("u-cy"))
    return r.out


def group_conflict() -> dict:
    from auto_ontology.dal import groups

    _accounts()
    r = _Run()
    kw = dict(description="d", color="#fff", role="viewer", user_ids=["u-ada"])
    r.step("create", lambda: _group(r, **groups.create_group(name="Analysts", **kw)))
    r.step("dup", lambda: groups.create_group(name="  analysts ", **kw))
    other = r.step("other", lambda: _group(r, **groups.create_group(name="Other", **kw)))
    r.step("rename_onto", lambda: groups.update_group(group_id=other["id"], updates={"name": "ANALYSTS"}, user_ids=None))
    r.step("list", lambda: groups.list_groups())
    return r.out


def group_update() -> dict:
    from auto_ontology.dal import groups

    _accounts()
    r = _Run()
    g = r.step("create", lambda: _group(r, **groups.create_group(name="Analysts", description="Reads", color="#fff", role="viewer", user_ids=["u-ada", "u-bob"])))
    r.step("rename_role", lambda: groups.update_group(group_id=g["id"], updates={"name": "Seers", "role": "admin", "description": None}, user_ids=None))
    r.step("replace_members", lambda: groups.update_group(group_id=g["id"], updates={}, user_ids=["u-cy"]))
    r.step("clear_members", lambda: groups.update_group(group_id=g["id"], updates={}, user_ids=[]))
    r.step("members_and_color", lambda: groups.update_group(group_id=g["id"], updates={"color": "#123"}, user_ids=["u-bob", "u-ada"]))
    r.step("unknown_member", lambda: groups.update_group(group_id=g["id"], updates={}, user_ids=["nobody"]))
    r.step("bad_role", lambda: groups.update_group(group_id=g["id"], updates={"role": "root"}, user_ids=None))
    r.step("dup_members", lambda: groups.update_group(group_id=g["id"], updates={"name": "Changed"}, user_ids=["u-cy", "u-cy"]))
    r.step("unknown_group", lambda: groups.update_group(group_id="no-such-group", updates={"name": "x"}, user_ids=None))
    r.step("get", lambda: groups.get_group(g["id"]))
    return r.out


def group_update_noop() -> dict:
    from auto_ontology.dal import groups

    _accounts()
    r = _Run()
    g = r.step("create", lambda: _group(r, **groups.create_group(name="Analysts", description="Reads", color="#fff", role="viewer", user_ids=["u-ada"])))
    r.step("nothing", lambda: groups.update_group(group_id=g["id"], updates={}, user_ids=None))
    r.step("same_name", lambda: groups.update_group(group_id=g["id"], updates={"name": "Analysts"}, user_ids=None))
    r.step("same_members", lambda: groups.update_group(group_id=g["id"], updates={}, user_ids=["u-ada"]))
    return r.out


def group_delete() -> dict:
    from auto_ontology.dal import groups

    _accounts()
    r = _Run()
    a = r.step("a", lambda: _group(r, **groups.create_group(name="Analysts", description="d", color="#fff", role="viewer", user_ids=["u-ada", "u-bob"])))
    r.step("b", lambda: _group(r, **groups.create_group(name="Keepers", description="d", color="#fff", role="viewer", user_ids=["u-ada"])))
    r.step("delete", lambda: groups.delete_group(a["id"]))
    r.step("delete_again", lambda: groups.delete_group(a["id"]))
    r.step("delete_unknown", lambda: groups.delete_group("no-such-group"))
    r.step("for_user", lambda: groups.groups_for_user("u-ada"))
    r.step("orphan_user", lambda: groups.groups_for_user("u-bob"))
    return r.out


def group_empty() -> dict:
    from auto_ontology.dal import groups

    r = _Run()
    r.step("list", lambda: groups.list_groups())
    r.step("get", lambda: groups.get_group("no-such-group"))
    r.step("for_user", lambda: groups.groups_for_user("nobody"))
    r.step("delete", lambda: groups.delete_group("no-such-group"))
    r.step("update", lambda: groups.update_group(group_id="no-such-group", updates={}, user_ids=None))
    return r.out


# ---------------------------------------------------------------------------
# zones
# ---------------------------------------------------------------------------


def _zone(r: _Run, zone: dict | None) -> dict | None:
    if zone is not None:
        r.names[zone["id"]] = f"<zone:{zone['name']}>"
    return zone


def zone_create(table_id: str, schema_id: str, other_table_id: str) -> dict:
    from auto_ontology.dal import zones

    r = _Run()
    r.step("create", lambda: _zone(r, zones.create_zone(name="Sales", description="d", color="#abc", item_ids=[table_id, schema_id, "no-such-id", table_id])))
    r.step("create_no_items", lambda: _zone(r, zones.create_zone(name="Nothing", description=None, color="#000", item_ids=[])))
    r.step("create_other_db", lambda: _zone(r, zones.create_zone(name="Sales", description=None, color="#000", item_ids=[other_table_id])))
    r.step("create_unknown_items", lambda: _zone(r, zones.create_zone(name="Ghosts", description=None, color="#000", item_ids=["nope"])))
    r.step("list", lambda: zones.list_zones())
    return r.out


def zone_conflict(table_id: str, other_table_id: str) -> dict:
    from auto_ontology.dal import zones

    r = _Run()
    r.step("create", lambda: _zone(r, zones.create_zone(name="Sales", description="d", color="#abc", item_ids=[table_id])))
    r.step("dup_same_db", lambda: zones.create_zone(name="  SALES ", description=None, color="#000", item_ids=[table_id]))
    r.step("dup_existing_name_fixture", lambda: zones.create_zone(name="film domain", description=None, color="#000", item_ids=["<none>"]))
    r.step("same_name_other_db", lambda: _zone(r, zones.create_zone(name="Sales", description=None, color="#000", item_ids=[other_table_id])))
    r.step("dup_no_items", lambda: zones.create_zone(name="sales", description=None, color="#000", item_ids=[]))
    r.step("list", lambda: zones.list_zones())
    return r.out


def zone_update(zone_id: str, table_id: str, other_table_id: str) -> dict:
    from auto_ontology.dal import zones

    r = _Run()
    r.step("rename_fields", lambda: _zone(r, zones.update_zone(zone_id=zone_id, updates={"name": "Films", "description": None, "color": "#111"}, item_ids=None)))
    r.step("replace_items", lambda: _zone(r, zones.update_zone(zone_id=zone_id, updates={}, item_ids=[table_id, other_table_id])))
    r.step("clear_items", lambda: _zone(r, zones.update_zone(zone_id=zone_id, updates={}, item_ids=[])))
    r.step("ignored_keys", lambda: _zone(r, zones.update_zone(zone_id=zone_id, updates={"enabled": False, "bogus": 1}, item_ids=None)))
    r.step("unknown", lambda: zones.update_zone(zone_id="no-such-zone", updates={"name": "x"}, item_ids=None))
    return r.out


def zone_update_noop(zone_id: str) -> dict:
    from auto_ontology.dal import zones

    r = _Run()
    r.step("nothing", lambda: _zone(r, zones.update_zone(zone_id=zone_id, updates={}, item_ids=None)))
    r.step("same_name", lambda: _zone(r, zones.update_zone(zone_id=zone_id, updates={"name": "Film domain"}, item_ids=None)))
    return r.out


def zone_update_conflict(zone_id: str, cross_zone_id: str) -> dict:
    from auto_ontology.dal import zones

    r = _Run()
    r.step("rename_onto_sibling", lambda: zones.update_zone(zone_id=zone_id, updates={"name": "retired ZONE"}, item_ids=None))
    r.step("rename_onto_cross", lambda: zones.update_zone(zone_id=zone_id, updates={"name": "Cross database"}, item_ids=None))
    r.step("list", lambda: zones.list_zones())
    return r.out


def zone_delete(zone_id: str) -> dict:
    from auto_ontology.dal import zones

    r = _Run()
    r.step("delete", lambda: zones.delete_zone(zone_id))
    r.step("delete_again", lambda: zones.delete_zone(zone_id))
    r.step("delete_unknown", lambda: zones.delete_zone("no-such-zone"))
    r.step("list", lambda: zones.list_zones())
    return r.out


def zone_enabled(zone_id: str, retired_id: str) -> dict:
    from auto_ontology.dal import zones

    r = _Run()
    r.step("disable", lambda: _zone(r, zones.set_zone_enabled(zone_id, False)))
    r.step("disable_again", lambda: _zone(r, zones.set_zone_enabled(zone_id, False)))
    r.step("enable_retired", lambda: _zone(r, zones.set_zone_enabled(retired_id, True)))
    r.step("unknown", lambda: zones.set_zone_enabled("no-such-zone", True))
    return r.out

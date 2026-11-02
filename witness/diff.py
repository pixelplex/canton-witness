"""Compares two report sets and decides the gate.

Rows are matched on (template, creating exercise), then per role. Node ids are
positional and are never used to match; they are shown on both sides.
"""

import re
from collections import Counter

FIELD_SPLIT = re.compile(r";\s+(?=[A-Za-z_][\w.]* = )")


def _group_key(row):
    ex = row["creating_exercise"]
    by = f"{ex['template']}.{ex['choice']}" if ex else "command"
    return (row["template"], by)


def _refine_key(row):
    """Create-argument fields that hold no party and no contract id."""
    fields = FIELD_SPLIT.split(row["create_arguments"]) if row["create_arguments"] else []
    keep = [f for f in fields if "'" not in f and not re.search(r"#\d+:\d+", f)]
    return tuple(sorted(keep))


def _signature(row):
    return frozenset((role, c["code"]) for role, c in row["cells"].items())


def _side(row, role):
    if row is None:
        return None
    cell = row["cells"].get(role)
    out = {"contract": row["contract"], "code": cell["code"] if cell else None}
    if cell and cell.get("established_by"):
        out["established_by"] = cell["established_by"]
    return out


def _compare_rows(scenario, base, cand, additions, removals):
    template = (base or cand)["template"]
    roles = set(base["cells"] if base else ()) | set(cand["cells"] if cand else ())
    for role in sorted(roles):
        b = base["cells"].get(role, {}).get("code") if base else None
        c = cand["cells"].get(role, {}).get("code") if cand else None
        if b == c:
            continue
        entry = {"scenario": scenario, "template": template, "role": role,
                 "was": b, "now": c, "baseline": _side(base, role), "candidate": _side(cand, role)}
        if c is not None:
            additions.append(entry)
        else:
            removals.append(entry)


def _match_group(scenario, key, brows, crows, additions, removals, inconclusive):
    if len(brows) == 1 and len(crows) == 1:
        _compare_rows(scenario, brows[0], crows[0], additions, removals)
        return
    if not brows or not crows:
        for r in brows:
            _compare_rows(scenario, r, None, additions, removals)
        for r in crows:
            _compare_rows(scenario, None, r, additions, removals)
        return
    bk = [_refine_key(r) for r in brows]
    ck = [_refine_key(r) for r in crows]
    if len(set(bk)) == len(bk) and len(set(ck)) == len(ck):
        bmap, cmap = dict(zip(bk, brows)), dict(zip(ck, crows))
        for k in sorted(set(bmap) | set(cmap)):
            _compare_rows(scenario, bmap.get(k), cmap.get(k), additions, removals)
        return
    if Counter(map(_signature, brows)) == Counter(map(_signature, crows)):
        return  # every member has the same codes per role, so nothing changed
    inconclusive.append({
        "scenario": scenario, "template": key[0], "creating_exercise": key[1],
        "baseline_contracts": [r["contract"] for r in brows],
        "candidate_contracts": [r["contract"] for r in crows],
        "reason": "group of same-template contracts under one exercise cannot be "
                  "matched one-to-one by create arguments, and its codes differ",
    })


def diff(base_set, cand_set):
    bs = {r["header"]["scenario"]: r for r in base_set["reports"]}
    cs = {r["header"]["scenario"]: r for r in cand_set["reports"]}
    out = {"inputs_changed": [], "scenarios_added": sorted(set(cs) - set(bs)),
           "scenarios_removed": sorted(set(bs) - set(cs)),
           "additions": [], "removals": [], "inconclusive": []}
    for name in sorted(set(bs) & set(cs)):
        bh, ch = bs[name]["header"], cs[name]["header"]
        for field in ("scenario_digest", "role_resolution", "sdk_version", "tool"):
            if bh.get(field) != ch.get(field):
                out["inputs_changed"].append({"scenario": name, "field": field,
                                              "was": bh.get(field), "now": ch.get(field)})
        groups = {}
        for side, rep in ((0, bs[name]), (1, cs[name])):
            for row in rep["rows"]:
                groups.setdefault(_group_key(row), ([], []))[side].append(row)
        for key in sorted(groups):
            brows, crows = groups[key]
            _match_group(name, key, brows, crows,
                         out["additions"], out["removals"], out["inconclusive"])
    for name in out["scenarios_added"]:
        for row in cs[name]["rows"]:
            _compare_rows(name, None, row, out["additions"], out["removals"])
    return out


def gate_failures(d, allow_inconclusive=False):
    reasons = []
    for s in d["scenarios_added"]:
        reasons.append(f"scenario added: {s}")
    for s in d["scenarios_removed"]:
        reasons.append(f"scenario removed: {s}")
    for a in d["additions"]:
        where = a["candidate"]["established_by"] if a["candidate"] and a["candidate"].get("established_by") else None
        via = f" (established by {where['node']} {where['action']}" + (
            f" {where['choice']}" if where.get("choice") else "") + f" on {where['template']})" if where else ""
        reasons.append(f"addition: {a['scenario']} {a['template']} role {a['role']}: "
                       f"{a['was'] or '-'} -> {a['now']}{via}")
    if not allow_inconclusive:
        for i in d["inconclusive"]:
            reasons.append(f"inconclusive: {i['scenario']} {i['template']} under {i['creating_exercise']}")
    return reasons

"""Builds one report per scenario from the table and transaction HTML."""

import hashlib
import os
import re
import zipfile

from . import SCHEMA, SUPPORTED_SDK, TOOL
from .htmlread import read_table, read_transactions, sdk_version_from_html

NOT_MEASURED = [
    "Test runs on the IDE ledger only, not a running deployment.",
    "Per party only: co-hosted parties and participant operators are not modelled.",
    "Onward transmission of a witnessed createdEventBlob.",
    "Off-ledger disclosure by services.",
    "Anything the scenarios do not exercise.",
]

HINT_SUFFIX = re.compile(r"^(.+)-[0-9a-f]{8,}$")


class Refused(Exception):
    """The input cannot be turned into a report."""


def role_of(party):
    """'alice-d4d95138' -> 'alice'. A party allocated with an exact id
    (allocatePartyByHint) has no suffix and is its own role."""
    m = HINT_SUFFIX.match(party)
    return m.group(1) if m else party


def resolve_roles(parties):
    roles = {}
    for p in sorted(parties):
        r = role_of(p)
        if r in roles.values():
            other = next(k for k, v in roles.items() if v == r)
            raise Refused(f"parties '{other}' and '{p}' both resolve to role '{r}'")
        roles[p] = r
    return roles


def dar_info(path):
    """Name, version and main package id from a DAR's manifest."""
    with zipfile.ZipFile(path) as z:
        text = z.read("META-INF/MANIFEST.MF").decode()
    text = re.sub(r"\r?\n ", "", text)  # undo manifest line wrapping
    fields = dict(line.split(": ", 1) for line in text.splitlines() if ": " in line)
    main = fields["Main-Dalf"].rsplit("/", 1)[-1]
    m = re.match(r"^(.*)-(\d[^-]*)-([0-9a-f]{64})\.dalf$", main)
    return {"name": m.group(1), "version": m.group(2), "package_id": m.group(3),
            "sdk_version": fields.get("Sdk-Version")}


def digest_sources(paths):
    h = hashlib.sha256()
    for p in sorted(paths, key=os.path.basename):
        with open(p, "rb") as f:
            data = f.read()
        h.update(os.path.basename(p).encode() + b"\0" + data + b"\0")
    return "sha256:" + h.hexdigest()


def _name(template):
    """'Mod:T@<package id>' -> 'Mod:T'. The SDK adds the package id to
    templates from packages other than the one under test."""
    return template.split("@", 1)[0] if template else template


def _describe(node):
    if node is None:
        return None
    d = {"node": node["id"], "action": node["kind"], "template": _name(node["template"])}
    if node["kind"] == "exercise":
        d["choice"] = node["choice"]
    return d


def _creating_exercise(nodes, cid):
    """The exercise a create sits under, as (template, choice), or None for a
    create submitted as a command. No node ids: they are positional."""
    node = nodes.get(cid)
    if node is None or node["parent"] is None:
        return None
    parent = nodes[node["parent"]]
    return {"template": _name(parent["template"]), "choice": parent["choice"]}


def _provenance(nodes, order, cid, party, code):
    """The node that established `code` for `party` on contract `cid`."""
    create = nodes.get(cid)
    if create is None:
        return None
    if code in ("S", "O"):
        return {"established_by": _describe(create)}
    if code == "W":
        # nearest enclosing exercise that has the party as an informee
        pid = create["parent"]
        while pid is not None:
            p = nodes[pid]
            if party in p["disclosed"]:
                return {"established_by": _describe(p), "created_at": create["id"]}
            pid = p["parent"]
        return {"established_by": None, "created_at": create["id"]}
    if code == "D":
        since = create["divulged"].get(party)
        for nid in order:
            n = nodes[nid]
            if n["tx"] == since and n["target"] == cid and party in n["disclosed"]:
                return {"established_by": _describe(n),
                        "inside": _describe(nodes[n["parent"]]) if n["parent"] else None}
        return {"established_by": None, "since_tx": since}
    return None


PARTY_LIT = re.compile(r"'([^']*)'")


def _args_with_roles(args, roles):
    return PARTY_LIT.sub(lambda m: "'" + roles.get(m.group(1), m.group(1)) + "'", args)


def build_report(scenario, table_html, tx_html, sdk_version, dars=(), sources=()):
    table = read_table(table_html)
    nodes, order = read_transactions(tx_html)

    parties = set()
    for c in table.values():
        parties.update(c["codes"])
    for n in nodes.values():
        parties.update(n["disclosed"])
        parties.update(n["divulged"])
        parties.update(PARTY_LIT.findall(n["args"]))  # parties named only in arguments
    roles = resolve_roles(parties)

    rows = []
    for cid, c in table.items():
        node = nodes.get(cid, {})
        cells = {}
        for party, code in sorted(c["codes"].items(), key=lambda kv: roles[kv[0]]):
            cells[roles[party]] = {"code": code, **(_provenance(nodes, order, cid, party, code) or {})}
        row = {
            "contract": cid,
            "template": _name(c["template"]),
            "status": c["status"],
            "creating_exercise": _creating_exercise(nodes, cid),
            "create_arguments": _args_with_roles(node.get("args", ""), roles),
            "cells": cells,
        }
        if "@" in c["template"]:
            row["template_package"] = c["template"].split("@", 1)[1]
        rows.append(row)
    rows.sort(key=lambda r: tuple(int(x) for x in r["contract"][1:].split(":")))

    header = {
        "scenario": scenario,
        "sdk_version": sdk_version,
        "tool": TOOL,
        "dars": [dar_info(d) for d in dars],
        "roles": sorted(roles.values()),
        "role_resolution": "party-id hint",
    }
    if sources:
        header["scenario_digest"] = digest_sources(sources)
    return {"header": header, "not_measured": NOT_MEASURED, "rows": rows}


def build_report_set(tbl_dir, tx_dir, sdk_version=None, dars=(), sources=()):
    reports = []
    for name in sorted(os.listdir(tbl_dir)):
        m = re.match(r"^table-(.+)\.html$", name)
        if not m:
            continue
        scenario = m.group(1)
        tx_path = os.path.join(tx_dir, f"transaction-{scenario}.html")
        if not os.path.exists(tx_path):
            raise Refused(f"no transaction view for scenario '{scenario}'")
        with open(os.path.join(tbl_dir, name), encoding="utf-8") as f:
            table_html = f.read()
        with open(tx_path, encoding="utf-8") as f:
            tx_html = f.read()
        found = sdk_version_from_html(table_html)
        sdk = sdk_version or found
        if sdk is None:
            raise Refused(f"cannot tell the SDK version of '{name}'; pass --sdk-version")
        if sdk not in SUPPORTED_SDK:
            raise Refused(f"SDK {sdk} is not supported (supported: {', '.join(SUPPORTED_SDK)})")
        reports.append(build_report(scenario, table_html, tx_html, sdk, dars, sources))
    if not reports:
        raise Refused(f"no table-*.html files in {tbl_dir}")
    return {"schema": SCHEMA, "reports": reports}

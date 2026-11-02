"""Command line: report, diff, gate."""

import argparse
import json
import sys

from .diff import diff, gate_failures
from .report import Refused, build_report_set


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _dump(obj, out):
    text = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
    if out in (None, "-"):
        sys.stdout.write(text)
    else:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="witness", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("report", help="build reports from dpm test HTML output")
    r.add_argument("tbl_dir", help="directory given to --table-output")
    r.add_argument("tx_dir", help="directory given to --transactions-output")
    r.add_argument("-o", "--out", help="output file (default: stdout)")
    r.add_argument("--sdk-version", help="override the SDK version read from the HTML")
    r.add_argument("--dar", action="append", default=[], help="DAR to name in the header (repeatable)")
    r.add_argument("--source", action="append", default=[],
                   help="scenario source file to digest into the header (repeatable)")

    for name, text in (("diff", "compare two report sets, print JSON"),
                       ("gate", "exit 1 on any addition, inconclusive row or scenario change")):
        p = sub.add_parser(name, help=text)
        p.add_argument("baseline")
        p.add_argument("candidate")
        p.add_argument("-o", "--out", help="output file (default: stdout)")
        if name == "gate":
            p.add_argument("--allow-inconclusive", action="store_true", help="local use only")

    a = ap.parse_args(argv)
    if a.cmd == "report":
        try:
            rs = build_report_set(a.tbl_dir, a.tx_dir, a.sdk_version, a.dar, a.source)
        except Refused as e:
            print(f"witness: refused: {e}", file=sys.stderr)
            return 2
        _dump(rs, a.out)
        return 0

    d = diff(_load(a.baseline), _load(a.candidate))
    if a.cmd == "diff":
        _dump(d, a.out)
        return 0

    if a.out:
        _dump(d, a.out)
    for c in d["inputs_changed"]:
        print(f"inputs changed: {c['scenario']} {c['field']}: {c['was']} -> {c['now']}")
    for x in d["removals"]:
        print(f"removal (not failing): {x['scenario']} {x['template']} role {x['role']}: {x['was']} -> -")
    reasons = gate_failures(d, a.allow_inconclusive)
    for line in reasons:
        print(f"FAIL {line}")
    print("gate: " + ("FAILED" if reasons else "passed"))
    return 1 if reasons else 0

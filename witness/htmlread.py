"""Readers for the HTML that `dpm test --table-output/--transactions-output` writes.

Written against Daml SDK 3.5.2. The format is not a stable interface, so every
other SDK version is refused by the report builder.
"""

import html
import re
from html.parser import HTMLParser

SDK_IN_STYLE = re.compile(r"/damlc/(\d+\.\d+\.\d+)/")
PARTY_SINCE = re.compile(r"'([^']*)' \((\d+)\)")
NODE_ID = re.compile(r"#\d+:\d+")


def sdk_version_from_html(text):
    """The SDK writes the absolute path of its stylesheet into a <style> tag.
    The path contains the damlc version, which is the only version marker in
    the file."""
    m = SDK_IN_STYLE.search(text)
    return m.group(1) if m else None


class _TableParser(HTMLParser):
    """Collects, per template, the header cells and the body rows of the
    contract-by-party matrix."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []  # [{template, header:[(text, is_party)], rows:[[cell]]}]
        self._in_style = False
        self._in_h1 = False
        self._cell = None  # dict while inside th/td
        self._row = None
        self._span_depth = 0
        self._span_text = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "style":
            self._in_style = True
        elif tag == "h1":
            self._in_h1 = True
            self.tables.append({"template": "", "header": [], "rows": []})
        elif tag == "tr":
            self._row = []
        elif tag in ("th", "td"):
            self._cell = {"tag": tag, "text": "", "party": False, "code": None}
        elif tag == "div" and self._cell is not None and "observer" in (a.get("class") or "").split():
            self._cell["party"] = True
        elif tag == "span" and self._cell is not None:
            self._span_depth += 1
            self._span_text = "" if a.get("class") is None else None

    def handle_endtag(self, tag):
        if tag == "style":
            self._in_style = False
        elif tag == "h1":
            self._in_h1 = False
        elif tag == "span" and self._cell is not None:
            if self._span_text is not None and self._cell["code"] is None:
                self._cell["code"] = self._span_text.strip() or None
            self._span_text = None
            self._span_depth -= 1
        elif tag in ("th", "td") and self._cell is not None:
            self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None and self.tables:
            t = self.tables[-1]
            if self._row and self._row[0]["tag"] == "th":
                t["header"] = [(c["text"].strip(), c["party"]) for c in self._row]
            else:
                t["rows"].append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._in_style:
            return
        if self._in_h1:
            self.tables[-1]["template"] += data
        elif self._cell is not None:
            self._cell["text"] += data
            if self._span_text is not None:
                self._span_text += data


def read_table(text):
    """Returns {contract id: {template, status, codes: {party: code}}}."""
    p = _TableParser()
    p.feed(text)
    out = {}
    for t in p.tables:
        header = t["header"]
        party_cols = [(i, name) for i, (name, is_party) in enumerate(header) if is_party]
        for row in t["rows"]:
            cid = row[0]["text"].strip()
            status = row[1]["text"].strip() if len(row) > 1 else ""
            codes = {}
            for i, party in party_cols:
                # dpm writes "-" for a party with no disclosure
                code = row[i]["code"] if i < len(row) else None
                if code and code != "-":
                    codes[party] = code
            out[cid] = {"template": t["template"].strip(), "status": status, "codes": codes}
    return out


class _TextParser(HTMLParser):
    """Flattens the transaction view to text, with <br> as a line break."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._in_style = False

    def handle_starttag(self, tag, attrs):
        if tag == "style":
            self._in_style = True
        elif tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "style":
            self._in_style = False

    def handle_data(self, data):
        if not self._in_style:
            self.parts.append(data)


# Acting parties come first. One actor takes "creates/exercises/fetches",
# several take "create/exercise/fetch": "'alice' and 'bob' fetch #10:1 (...)".
ACTORS = r"((?:'[^']*'(?:, | and )?)+)"
ACTION_CREATE = re.compile(r"^└─> " + ACTORS + r" creates? (\S+)\s*$")
ACTION_EXERCISE = re.compile(r"^└─> " + ACTORS + r" exercises? (\S+) on (#\d+:\d+) \((\S+)\)\s*$")
ACTION_FETCH = re.compile(r"^└─> " + ACTORS + r" fetch(?:es)? (#\d+:\d+) \((\S+)\)\s*$")
ACTION_ANY = re.compile(r"^└─> (.*)$")


def _set_action(cur, text):
    if (m := ACTION_CREATE.match(text)):
        cur.update(kind="create", template=m.group(2))
    elif (m := ACTION_EXERCISE.match(text)):
        cur.update(kind="exercise", choice=m.group(2), target=m.group(3), template=m.group(4))
    elif (m := ACTION_FETCH.match(text)):
        cur.update(kind="fetch", target=m.group(2), template=m.group(3))
    else:
        cur.update(kind="other", choice=ACTION_ANY.match(text).group(1))


def read_transactions(text):
    """Returns (nodes, order). nodes maps a node id such as '#2:1' to a dict
    with tx, kind (create/exercise/fetch/other), template, choice, target,
    args, parent, disclosed {party: tx}, divulged {party: tx}.

    Long lines wrap in the view: party lists, the "referenced by" list and the
    actor list of an action continue on the next line without a marker."""
    p = _TextParser()
    p.feed(text)
    lines = html.unescape("".join(p.parts)).split("\n")

    nodes, order = {}, []
    stack = []  # [(indent, node id)] of open nodes
    cur = None
    tx = None
    state = None  # what a wrapped line continues: list name, "action", "args", "refs"
    node_may_start = False  # a node id follows a TX line, a blank line or "children:"
    action = ""
    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        m = re.match(r"^TX (\d+) ", stripped)
        if m:
            tx = int(m.group(1))
            stack, cur, state, node_may_start = [], None, None, True
            continue
        if node_may_start and re.fullmatch(r"#\d+:\d+", stripped):
            indent = len(line) - len(line.lstrip())
            while stack and stack[-1][0] >= indent:
                stack.pop()
            parent = None
            for _, nid in reversed(stack):
                if nodes[nid]["kind"] == "exercise":
                    parent = nid
                    break
            cur = {
                "id": stripped, "tx": tx, "kind": "other", "template": None,
                "choice": None, "target": None, "args": "", "parent": parent,
                "disclosed": {}, "divulged": {},
            }
            nodes[stripped] = cur
            order.append(stripped)
            stack.append((indent, stripped))
            state, node_may_start = None, False
            continue
        node_may_start = stripped == "" or stripped == "children:"
        if cur is None:
            continue
        body = stripped.lstrip("│").strip()
        if body == "" or body == "children:" or body.startswith("Active contracts:"):
            if state == "action":
                _set_action(cur, action)
            state = None
            if body.startswith("Active contracts:"):
                cur = None
            continue
        if state == "action":
            if body == "with":
                _set_action(cur, action)
                state = "args"
            else:
                action += " " + body
                if not action.rstrip().endswith((",", " and")):
                    _set_action(cur, action)
                    state = None
            continue
        if body.startswith("disclosed to (since):"):
            state = "disclosed"
        elif body.startswith("divulged to (since):"):
            state = "divulged"
        elif body.startswith("referenced by") or body.startswith("consumed by"):
            state = "refs"
            continue
        elif state in ("disclosed", "divulged") and PARTY_SINCE.match(body):
            pass  # wrapped party list
        elif state == "refs" and NODE_ID.match(body):
            continue  # wrapped "referenced by" list
        elif body.startswith("└─>"):
            action = body
            if action.rstrip().endswith((",", " and")):
                state = "action"  # actor list wraps
            else:
                _set_action(cur, action)
                state = None
            continue
        elif body == "with":
            state = "args"
            continue
        elif state == "args":
            cur["args"] = (cur["args"] + " " + body).strip()
            continue
        else:
            state = None
            continue
        cur[state].update({pty: int(n) for pty, n in PARTY_SINCE.findall(body)})
    return nodes, order

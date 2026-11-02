#!/usr/bin/env bash
# Optional: reports for the token-standard test scenarios of a Splice checkout.
#
#   examples/token-standard/run.sh [path to a splice checkout]
#
# Without a path the script clones https://github.com/canton-network/splice.
# SPLICE_REF is the tag (default 0.8.0, tested on Daml SDK 3.5.2). Sources come
# from that tag through git archive, so local changes in the checkout are
# ignored. The build goes to build/token-standard. The frozen v1 API DARs in
# daml/dars are used as they are, like the Splice build does.
# There is no expected report yet. The check is that every scenario gives a
# report and every cell has a provenance node.
set -euo pipefail
SPLICE_REF="${SPLICE_REF:-0.8.0}"
if [ -n "${1:-}" ]; then
  SPLICE=$(cd "$1" && pwd)
else
  SPLICE="$(mktemp -d)/splice"
  git clone --quiet --depth 1 --branch "$SPLICE_REF" https://github.com/canton-network/splice.git "$SPLICE"
fi
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
OUT=$ROOT/build/token-standard
TARGET=token-standard/splice-token-standard-v2-test

rm -rf "$OUT"
mkdir -p "$OUT/src"
git -C "$SPLICE" archive "$SPLICE_REF" daml token-standard | tar -C "$OUT/src" -xf -
find "$OUT/src/daml/dars" -name '*.dar' ! -name '*-1.0.0.dar' -delete

# Packages in dependency order, read from the data-dependencies of each daml.yaml.
order=$(python3 - "$OUT/src" "$TARGET" <<'EOF'
import os, re, sys
root, target = sys.argv[1], sys.argv[2]
seen = []
def deps(pkg):
    y = open(os.path.join(root, pkg, "daml.yaml")).read()
    out = []
    for d in re.findall(r"^\s*-\s*(\S+\.dar)\s*$", y, re.M):
        if "/.daml/" in d:
            out.append(os.path.normpath(os.path.join(pkg, d.split("/.daml/")[0])))
    return out
def visit(p):
    if p in seen:
        return
    for d in deps(p):
        visit(d)
    seen.append(p)
visit(target)
print("\n".join(seen))
EOF
)

for p in $order; do
  n=$(awk '/^name:/ {print $2}' "$OUT/src/$p/daml.yaml")
  dist=$OUT/src/$p/.daml/dist
  case $n in
    splice-api-token-*-v1)
      if [ -f "$OUT/src/daml/dars/$n-1.0.0.dar" ]; then
        mkdir -p "$dist"; cp "$OUT/src/daml/dars/$n-1.0.0.dar" "$dist/$n-current.dar"
        echo "frozen  $n"; continue
      fi;;
  esac
  echo "build   $n"
  (cd "$OUT/src/$p" && dpm build -o ".daml/dist/$n-current.dar") >"$OUT/build-$n.log" 2>&1 \
    || { echo "dpm build failed for $n, see build/token-standard/build-$n.log"; exit 1; }
done

echo "test    $(basename $TARGET)"
(cd "$OUT/src/$TARGET" && dpm test --table-output "$OUT/tbl" --transactions-output "$OUT/tx") >"$OUT/test.log" 2>&1 \
  || { echo "dpm test failed, see build/token-standard/test.log"; exit 1; }

cd "$ROOT"
python3 -m witness report "$OUT/tbl" "$OUT/tx" -o "$OUT/reports.json"
python3 - "$OUT/reports.json" <<'EOF'
import json, sys
rs = json.load(open(sys.argv[1]))["reports"]
cells = [c for r in rs for row in r["rows"] for c in row["cells"].values()]
missing = [c for c in cells if not c.get("established_by")]
codes = {}
for c in cells:
    codes[c["code"]] = codes.get(c["code"], 0) + 1
print(f"scenarios {len(rs)}, rows {sum(len(r['rows']) for r in rs)}, cells {len(cells)}, "
      f"codes {dict(sorted(codes.items()))}, without provenance {len(missing)}")
sys.exit(1 if missing or not rs else 0)
EOF

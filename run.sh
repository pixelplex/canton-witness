#!/usr/bin/env bash
# Builds the examples from clean, makes the reports, runs diff and gate and
# compares the output with examples/*/expected/. Exits 0 if all of it matches.
# Needs Daml SDK 3.5.2 through dpm, and python3.
#
#   ./run.sh            check
#   ./run.sh --update   overwrite the expected files with the new output
#   SPLICE_DIR=../splice ./run.sh   also run examples/token-standard
#                                   (tag SPLICE_REF, default 0.8.0)
set -uo pipefail
cd "$(dirname "$0")"
ROOT=$PWD
BUILD=$ROOT/build
UPDATE=0
[ "${1:-}" = "--update" ] && UPDATE=1
FAILS=0

fail() { echo "FAIL: $*"; FAILS=$((FAILS + 1)); }
ok() { echo "ok:   $*"; }
witness() { python3 -m witness "$@"; }

command -v dpm >/dev/null || { echo "dpm not found"; exit 2; }
command -v python3 >/dev/null || { echo "python3 not found"; exit 2; }

rm -rf "$BUILD"
mkdir -p "$BUILD"

# Copy without the .daml/ cache: a stale cache can change the result.
fresh() { # <src> <dst>
  mkdir -p "$2"
  cp "$1/daml.yaml" "$2/"
  cp -r "$1/daml" "$2/"
}

run_test() { # <src package> <out dir>
  fresh "$1" "$2/pkg"
  (cd "$2/pkg" && dpm test --table-output ../tbl --transactions-output ../tx) >"$2/test.log" 2>&1 \
    || { fail "dpm test in $1 (see ${2#$ROOT/}/test.log)"; return 1; }
}

compare() { # <generated> <expected>
  if [ $UPDATE = 1 ]; then
    mkdir -p "$(dirname "$2")"; cp "$1" "$2"; ok "updated ${2#$ROOT/}"; return
  fi
  if diff -u "$2" "$1" >"$1.diff"; then ok "${2#$ROOT/} matches"
  else fail "${2#$ROOT/} differs:"; cat "$1.diff"; fi
}

expect_exit() { # <expected code> <label> <stdout file> <cmd...>
  local want=$1 label=$2 out=$3; shift 3
  "$@" >"$out"; local got=$?
  if [ "$got" = "$want" ]; then ok "$label exits $got"; else fail "$label exits $got, expected $want"; fi
}

echo "== channels (Appendix A, part 2)"
E=examples/channels O=$BUILD/channels
if run_test $E $O; then
  witness report $O/tbl $O/tx --source $E/daml/Main.daml -o $O/report.json
  compare $O/report.json $E/expected/report.json
  expect_exit 2 "report with an unsupported SDK version (refused)" /dev/null \
    witness report $O/tbl $O/tx --sdk-version 3.4.0
fi

for EX in helper-change unmatched-group; do
  echo "== $EX"
  E=examples/$EX O=$BUILD/$EX
  for v in v1 v2; do
    fresh $E/$v $O/$v/pkg
    (cd $O/$v/pkg && dpm build -o ../../$v.dar) >$O/$v/build.log 2>&1 || fail "dpm build $E/$v"
    run_test $E/$v $O/$v || continue
    witness report $O/$v/tbl $O/$v/tx --dar $O/$v.dar --source $E/$v/daml/Scenario.daml -o $O/$v.json
    compare $O/$v.json $E/expected/$v.json
  done

  # The template text is the same in v1 and v2, so upgrade-check should stay silent.
  dpm upgrade-check --compiler $O/v1.dar $O/v2.dar >$O/upgrade-check.txt 2>&1; rc=$?
  if [ $rc = 0 ] && [ ! -s $O/upgrade-check.txt ]; then ok "upgrade-check v1 -> v2: silent, exit 0"
  else fail "upgrade-check v1 -> v2: exit $rc, output:"; cat $O/upgrade-check.txt; fi

  witness diff $O/v1.json $O/v2.json -o $O/diff-v1-v2.json
  compare $O/diff-v1-v2.json $E/expected/diff-v1-v2.json
  expect_exit 1 "gate v1 -> v2" $O/gate-v1-v2.txt witness gate $O/v1.json $O/v2.json
  compare $O/gate-v1-v2.txt $E/expected/gate-v1-v2.txt
  expect_exit 0 "gate v1 -> v1" /dev/null witness gate $O/v1.json $O/v1.json
  if [ $EX = helper-change ]; then
    expect_exit 0 "gate v2 -> v1 (removal only)" $O/gate-v2-v1.txt witness gate $O/v2.json $O/v1.json
  fi
done

if [ -n "${SPLICE_DIR:-}" ]; then
  echo "== token-standard (optional, SPLICE_DIR=${SPLICE_DIR}, tag ${SPLICE_REF:-0.8.0})"
  if examples/token-standard/run.sh "$SPLICE_DIR"; then ok "token-standard reports"
  else fail "token-standard reports"; fi
fi

echo "== committed files carry no local paths"
# "/tm""p/" is split so that this line does not match itself.
if grep -rIl --exclude-dir=build --exclude-dir=.daml --exclude-dir=.git \
     -e "$HOME" -e "/tm""p/" . ; then fail "local paths found in the files above"
else ok "no local paths"; fi

echo
if [ $FAILS = 0 ]; then echo "ALL OK"; exit 0; else echo "$FAILS FAILURE(S)"; exit 1; fi

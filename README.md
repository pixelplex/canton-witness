# canton-witness (prototype)

Prototype for the Canton Witness proposal to the Canton Development Fund,
[canton-foundation/canton-dev-fund#946](https://github.com/canton-foundation/canton-dev-fund/pull/946).

It shows who sees which contracts in a Daml Script scenario, and whether that changed between two versions of a package. It reads the HTML that `dpm test --table-output --transactions-output` writes and makes a JSON report per scenario. For each contract and each party the report gives a code and the transaction node it came from:

- `S` signatory
- `O` observer
- `D` divulged
- `W` witness

`diff` compares two reports. `gate` fails when a party gains a code or when a change cannot be matched.

## Run

Requires Daml SDK 3.5.2 through `dpm`, and `python3`.

```
./run.sh
```

It builds the examples, writes the reports, runs `diff` and `gate` and compares the output with the files in `examples/*/expected/`. It exits 0 if everything matches.

The tool on its own:

```
python3 -m witness report <tbl-dir> <tx-dir> -o report.json
python3 -m witness diff baseline.json candidate.json
python3 -m witness gate baseline.json candidate.json
```

## Examples

- `channels`: the scenario from Appendix A of the implementation draft. Bob gets `D` on `Secret` and `Bridge` and `W` on `Child`, although his queries return none of them.
- `helper-change`: two versions of a package that differ only in a helper function that computes observers. `dpm upgrade-check` sees no problem. `gate` catches that bob became an observer.
- `unmatched-group`: two contracts that the diff cannot tell apart. Their codes change, so the gate marks the group inconclusive and fails. `upgrade-check` sees no problem here either.
- `token-standard` (optional): runs the scenarios of the Splice 0.8.0 token-standard tests and checks that every scenario gives a report. Run it with `examples/token-standard/run.sh`. It downloads Splice. The result is 31 scenarios, 984 contracts and 2291 codes.

## Limits

- Reads the HTML of SDK 3.5.2 only. The HTML format is not a stable interface.
- Parties are named by their id hint, for example `alice-d4d95138` becomes `alice`. Two parties with the same hint are refused.
- The report does not follow the full schema of draft §4 yet.
- Contracts are matched between reports by template and the choice that created them. When that is not enough, the group is marked inconclusive, even if a better match exists.
- A party that never received a contract does not appear in the report.
- Only test runs are covered. The tool does not see data passed on outside the ledger or in scenarios that are not run.
- No accept file, no GitHub Action and no `dpm` component yet.

## License

Apache License 2.0. See `LICENSE`.

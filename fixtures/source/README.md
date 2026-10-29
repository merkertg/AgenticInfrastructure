# Source fixtures

Two invented states of the fictional Alpha Handbook source for the Stage 07
laboratory. `app.source_lab` serves one state at a time on `127.0.0.1`.

- `handbook-1.json`: four documents. `vendor-review` contains an
  instruction-like sentence; `office-access` also lists a group reader, which
  the connector ignores; `partner-note` is readable only by an unmapped account.
- `handbook-2.json`: `travel-desk` has a new revision, `u-fin` lost
  `office-access`, `vendor-review` was deleted and `budget-draft` contains a
  control character, so the sync withholds it.

The files contain no corporate data, accounts or credentials.

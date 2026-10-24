# Details check

`python tools/details-check/check.py` - a manual pre-release check of the
JavaScript the Python suite cannot run. It renders the real dashboard through
the app against a throwaway database, then clicks through it in headless
Chrome: the Result column hidden by default, a filtered column always shown
(and exported), the View toggle locking while filtered, filter-menu search
actually hiding what it filters out, and the Job Details panel for a reaped,
timed-out, old, already-installed and plain job - blank dashes, notes,
labels, Escape to close.

Needs **Chrome** (or set `CHROME`) and the server's Python packages - run it
with `server/venv`'s python. **Not a gate:** it is outside `t/`, and
`run_all.py` cannot reach it. Exit 0 clean, 1 findings, 2 could not run.
`--shots <folder>` also saves screenshots; `--server <folder>` checks a staged
copy instead of `server/`.

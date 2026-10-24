# Visual census

`node tools/visual-census/census.mjs` - a manual pre-release check of what the
Software Library page actually paints, in every state including the ones only
seen mid-task (reading an installer, a detected file, a picked package, both
dialogs, the duplicate banner). It reports light surfaces, light borders and
low-contrast text in dark mode, plus JavaScript errors.

Needs **Chrome** and **Node 24**. **Not a gate:** it is outside `t/`, and
`run_all.py` cannot reach it. Exit 0 clean, 1 findings, 2 could not run.
`--theme light` prints text contrast for information only.

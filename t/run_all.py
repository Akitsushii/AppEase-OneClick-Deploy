"""Run every AppEase test suite and summarise the result.

    python t\\run_all.py                 run everything
    python t\\run_all.py equipment       run only suites whose name matches
    python t\\run_all.py -v              print full output for every suite

Each suite runs in its OWN process: several chdir into a temp copy of
server/ and re-import app.py under different environment variables.
"""
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Per-suite ceiling. test_secret_key.py is the slow one - it starts app.py
# in a fresh subprocess a dozen times to check what happens at startup.
TIMEOUT_SECONDS = 900

# Two output formats live in this folder. Both are parsed: the counts are
# what matter, not their phrasing.
PATTERNS = [
    # PASSED: 116  /  FAILED: 0
    (re.compile(r"^PASSED:\s*(\d+)\s*$", re.M), re.compile(r"^FAILED:\s*(\d+)\s*$", re.M)),
    # 116 passed, 0 failed, 116 total
    (re.compile(r"^(\d+) passed,", re.M), re.compile(r"^\d+ passed, (\d+) failed", re.M)),
]

# Exit codes a suite uses to say "I could not run", as distinct from "I ran
# and something is wrong". Treating these as failures would cry wolf;
# treating them as passes would hide a suite that never ran at all.
SKIP_EXIT_CODES = {
    2: "prerequisite missing",
    3: "out of date or fixture missing",
}


def parse_counts(output):
    """Pull (passed, failed) out of a suite's output, or (None, None)."""
    for passed_re, failed_re in PATTERNS:
        passed = passed_re.search(output)
        failed = failed_re.search(output)
        if passed and failed:
            return int(passed.group(1)), int(failed.group(1))
    return None, None


def discover(filter_text):
    """test_*.py in this folder, sorted. test_ specifically: a helper script
    run as a suite would report a confusing pass."""
    names = sorted(
        name for name in os.listdir(HERE)
        if name.startswith("test_") and name.endswith(".py")
    )
    if filter_text:
        names = [n for n in names if filter_text.lower() in n.lower()]
    return names


def run_one(name):
    started = time.time()
    try:
        result = subprocess.run(
            [sys.executable, os.path.join("t", name)],
            cwd=ROOT, capture_output=True, text=True, timeout=TIMEOUT_SECONDS,
        )
        output = (result.stdout or "") + (result.stderr or "")
        code = result.returncode
    except subprocess.TimeoutExpired:
        return {
            "name": name, "state": "TIMEOUT", "passed": 0, "failed": 0,
            "seconds": time.time() - started,
            "output": f"No result after {TIMEOUT_SECONDS}s. Run it on its own to see where it hangs.",
        }

    passed, failed = parse_counts(output)
    seconds = time.time() - started

    if code in SKIP_EXIT_CODES:
        state = "SKIP"
    elif passed is None:
        # No counts at all almost always means it died before finishing -
        # an import error, a missing template. Never call that a pass.
        state = "ERROR"
        passed, failed = 0, 0
    elif code != 0 or failed:
        state = "FAIL"
    else:
        state = "PASS"

    return {"name": name, "state": state, "passed": passed or 0,
            "failed": failed or 0, "seconds": seconds, "output": output,
            "code": code}


def main():
    args = [a for a in sys.argv[1:]]
    verbose = "-v" in args or "--verbose" in args
    filter_text = next((a for a in args if not a.startswith("-")), None)

    names = discover(filter_text)
    if not names:
        print("No test files found" + (f" matching {filter_text!r}" if filter_text else "")
              + f" in {HERE}")
        return 1

    print(f"Running {len(names)} suite(s) from t\\\n")
    results = []
    for name in names:
        # Printed before the run, without a newline, so a slow suite shows
        # what is currently running rather than an idle cursor.
        print(f"  {name:<28}", end="", flush=True)
        outcome = run_one(name)
        results.append(outcome)

        if outcome["state"] == "PASS":
            detail = f"{outcome['passed']:>4} passed"
        elif outcome["state"] == "FAIL":
            detail = f"{outcome['passed']:>4} passed, {outcome['failed']} FAILED"
        elif outcome["state"] == "SKIP":
            detail = SKIP_EXIT_CODES.get(outcome.get("code"), "skipped")
        elif outcome["state"] == "TIMEOUT":
            detail = f"timed out after {TIMEOUT_SECONDS}s"
        else:
            detail = "did not finish"
        print(f"{outcome['state']:<8}{detail}  ({outcome['seconds']:.1f}s)")

    # --- detail for anything that was not a clean pass --------------------
    for outcome in results:
        if outcome["state"] == "PASS" and not verbose:
            continue
        print("\n" + "=" * 64)
        print(f"{outcome['state']}: {outcome['name']}")
        print("=" * 64)
        print(outcome["output"].rstrip())

    total_passed = sum(r["passed"] for r in results)
    total_failed = sum(r["failed"] for r in results)
    bad = [r for r in results if r["state"] in ("FAIL", "ERROR", "TIMEOUT")]
    skipped = [r for r in results if r["state"] == "SKIP"]

    print("\n" + "-" * 64)
    print(f"{len(results) - len(bad) - len(skipped)}/{len(results)} suites clean"
          f"   {total_passed} assertions passed"
          + (f", {total_failed} failed" if total_failed else "")
          + (f"   {len(skipped)} skipped" if skipped else ""))
    if bad:
        print("Needs attention: " + ", ".join(r["name"] for r in bad))
    print("-" * 64)

    # Non-zero if anything did not run cleanly, so this can be wired into
    # something else later without changing how it reports.
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

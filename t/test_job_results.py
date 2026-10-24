"""Job result reasons: what the agent reports, what the server keeps and
what the dashboard shows. A confidently wrong reason is worse than a blank
one, so most checks are of what must NOT be said. The Chocolatey fixtures
are real VM output from Chocolatey 2.7.3, verbatim.

    python t/test_job_results.py --agent C:\\Appease\\agent.py
"""
import html.parser
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

passed = 0
failed = []


def check(label, condition):
    global passed
    if condition:
        passed += 1
    else:
        failed.append(label)
        print(f"  FAIL: {label}")


def arg_path(name, default):
    for index, arg in enumerate(sys.argv):
        if arg == name and index + 1 < len(sys.argv):
            return os.path.abspath(sys.argv[index + 1])
        if arg.startswith(name + "="):
            return os.path.abspath(arg.split("=", 1)[1])
    return default


AGENT_PATH = arg_path("--agent", os.path.join(ROOT, "agent", "agent.py"))
SERVER_SRC = arg_path("--server", os.path.join(ROOT, "server"))
if not os.path.isfile(AGENT_PATH) or not os.path.isdir(SERVER_SRC):
    print("COULD NOT RUN - agent.py or server/ not found.")
    sys.exit(2)

work = tempfile.mkdtemp(prefix="appease_jr_")
server_dir = os.path.join(work, "server")
shutil.copytree(
    SERVER_SRC, server_dir,
    ignore=shutil.ignore_patterns(
        "appease.db*", "__pycache__", "*.pyc", ".env",
        "uploads", "staging", "report_photos", "avatars",
    ),
)
shutil.copy(AGENT_PATH, os.path.join(work, "agent.py"))
sys.path.insert(0, server_dir)
sys.path.insert(0, work)
os.chdir(server_dir)

import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
import app as appmod  # noqa: E402
import agent  # noqa: E402

needed = ("classify_install_result", "InstallResult", "CHOCO_EXE", "run_install_command")
if any(not hasattr(agent, n) for n in needed) or not hasattr(appmod, "JOB_RESULT_REASONS"):
    print("COULD NOT RUN - agent.py or app.py predates job result reasons.")
    sys.exit(3)

# Every result the agent produces in this file, for the contract in section 3.
produced = []


def classify(command, exit_code, output="", timed_out=False):
    result = agent.classify_install_result(command, exit_code, output, timed_out)
    produced.append(result)
    return result


# ---- the captured fixtures, verbatim ----------------------------------------
FIXTURE_A = r"""Chocolatey v2.7.3
Installing the following packages:
paint.net
By installing, you accept licenses for the packages.
paint.net v5.1.12 already installed.
 Use --force to reinstall, specify a version to install, or try upgrade.

Chocolatey installed 0/1 packages.
 See the log for details (C:\ProgramData\chocolatey\logs\chocolatey.log).

Warnings:
 - paint.net - paint.net v5.1.12 already installed.
 Use --force to reinstall, specify a version to install, or try upgrade.
"""

FIXTURE_B = r"""Chocolatey v2.7.3
Installing the following packages:
thispackagedoesnotexist
By installing, you accept licenses for the packages.
thispackagedoesnotexist not installed. The package was not found with the source(s) listed.
 Source(s): 'https://community.chocolatey.org/api/v2/'
 NOTE: When you specify explicit sources, it overrides default sources.
If the package version is a prerelease and you didn't specify `--pre`,
 the package may not be found.
Please see https://docs.chocolatey.org/en-us/troubleshooting for more
 assistance.

Chocolatey installed 0/1 packages. 1 packages failed.
 See the log for details (C:\ProgramData\chocolatey\logs\chocolatey.log).

Failures
 - thispackagedoesnotexist - thispackagedoesnotexist not installed. The package was not found with the source(s) listed.
 [same text repeated]
"""

# Fixture C - the block Chocolatey 2.7.3 printed ABOVE the normal output on
# the VM while a system reboot was pending, verbatim. It appears on every
# Chocolatey run in that state, successful installs included. Captured on a
# not-found run whose remaining output was identical to Fixture B, exit 1.
FIXTURE_C_VALIDATION = r"""Chocolatey v2.7.3
3 validations performed. 2 success(es), 1 warning(s), and 0 error(s).

Validation Warnings:
 - A pending system reboot request has been detected, however, this is
   being ignored due to the current Chocolatey configuration.  If you
   want to halt when this occurs, then either set the global feature
   using:
     choco feature enable --name="exitOnRebootDetected"
   or pass the option --exit-when-reboot-detected.
"""


# Fixture D - job 262 on the VM, a Paint.NET redeploy while a system reboot
# was pending, captured through the REAL agent and copied from its log
# verbatim. The agent logs install output with its outer whitespace stripped,
# so the fixture starts at the first line and ends at the last; nothing
# outside that is recoverable, and nothing here reads it.
#
# Two things it settled that the earlier fixtures could not:
#   - A blank line follows the validation block.
#   - "Chocolatey installed 0/1 packages. " ends in a SPACE. Fixture A,
#     from an earlier paste, does not. The parser strips each line before
#     matching, which is what makes both shapes read the same; the check
#     below keeps the space from being lost to an editor.
FIXTURE_D = r"""Chocolatey v2.7.3
3 validations performed. 2 success(es), 1 warning(s), and 0 error(s).

Validation Warnings:
 - A pending system reboot request has been detected, however, this is
   being ignored due to the current Chocolatey configuration.  If you
   want to halt when this occurs, then either set the global feature
   using:
     choco feature enable --name="exitOnRebootDetected"
   or pass the option --exit-when-reboot-detected.

Installing the following packages:
paint.net
By installing, you accept licenses for the packages.
paint.net v5.1.12 already installed.
 Use --force to reinstall, specify a version to install, or try upgrade.

Chocolatey installed 0/1 packages. 
 See the log for details (C:\ProgramData\chocolatey\logs\chocolatey.log).

Warnings:
 - paint.net - paint.net v5.1.12 already installed.
 Use --force to reinstall, specify a version to install, or try upgrade."""


def with_validation(fixture):
    """The captured validation block, a blank line, then a fixture minus its
    own version line - the join Fixture D shows real output uses."""
    return FIXTURE_C_VALIDATION + "\n" + fixture.split("\n", 1)[1]


CMD_A = "choco install paint.net -y"
CMD_B = "choco install thispackagedoesnotexist -y"
ALREADY = "choco_already_installed"
NOT_FOUND = "choco_package_not_found"


def variant(text, old, new):
    assert text.count(old) == 1, f"fixture variant anchor matched {text.count(old)}x: {old!r}"
    return text.replace(old, new)


print("\n=== 1. Chocolatey output: three signals, never one ===")
r = classify(CMD_A, 0, FIXTURE_A)
check("Fixture A is read as already installed", r.reason == ALREADY)
check("Fixture A stays a success, as decided", r.ok is True)
check("Fixture A keeps its exit code 0", r.exit_code == 0)

r = classify(CMD_B, 1, FIXTURE_B)
check("Fixture B is read as package not found", r.reason == NOT_FOUND)
check("Fixture B is a failure", r.ok is False)
check("Fixture B keeps its exit code (1, confirmed on the VM)", r.exit_code == 1)
check("Chocolatey's -1, as Windows delivers it, is also an error code for B",
      classify(CMD_B, 4294967295, FIXTURE_B).reason == NOT_FOUND)

# The two fixtures share the summary prefix. Each negative below removes or
# contradicts exactly ONE signal and must lose the specific reason.
check("A with a failure exit code is not 'already installed'",
      classify(CMD_A, 1, FIXTURE_A).reason != ALREADY)
check("A without its Warnings entry is not 'already installed'",
      classify(CMD_A, 0, variant(FIXTURE_A, "Warnings:\n - paint.net - paint.net v5.1.12 already installed.\n", "")).reason != ALREADY)
check("A whose summary says it installed the package is not 'already installed'",
      classify(CMD_A, 0, variant(FIXTURE_A, "Chocolatey installed 0/1 packages.", "Chocolatey installed 1/1 packages.")).reason != ALREADY)
check("A whose summary reports a failure is not 'already installed'",
      classify(CMD_A, 0, variant(FIXTURE_A, "Chocolatey installed 0/1 packages.", "Chocolatey installed 0/1 packages. 1 packages failed.")).reason != ALREADY)
check("A with no summary line at all is not 'already installed'",
      classify(CMD_A, 0, variant(FIXTURE_A, "Chocolatey installed 0/1 packages.\n", "")).reason != ALREADY)
check("A's entry under a Failures heading is not 'already installed'",
      classify(CMD_A, 0, variant(FIXTURE_A, "Warnings:", "Failures")).reason != ALREADY)
check("A's entry outside any heading is not 'already installed'",
      classify(CMD_A, 0, variant(FIXTURE_A, "Warnings:\n", "\n")).reason != ALREADY)
check("A for a different command's package is not 'already installed'",
      classify("choco install vlc -y", 0, FIXTURE_A).reason != ALREADY)
# Paint.NET's own dependency can be the thing reported already installed
# during a fresh install. The entry must name the package being deployed.
dependency_only = variant(
    FIXTURE_A, " - paint.net - paint.net v5.1.12 already installed.",
    " - chocolatey-core.extension - chocolatey-core.extension v1.3.3 already installed.")
check("a dependency's 'already installed' is not the deployed package's",
      classify(CMD_A, 0, dependency_only).reason != ALREADY)
check("the package id is matched literally, not as a pattern (paint.net vs paintXnet)",
      classify(CMD_A, 0, FIXTURE_A.replace("paint.net", "paintXnet")).reason != ALREADY)
check("the entry must name the package on BOTH sides",
      classify(CMD_A, 0, variant(FIXTURE_A, " - paint.net - paint.net v5.1.12", " - paint.net - vlc v5.1.12")).reason != ALREADY)
check("Chocolatey ids are case-insensitive: Paint.NET in the command still matches",
      classify("choco install Paint.NET -y", 0, FIXTURE_A).reason == ALREADY)
check("a command naming two packages gets no output-based reason",
      classify("choco install paint.net vlc -y", 0, FIXTURE_A).reason is None)
check("CRLF output reads the same as LF",
      classify(CMD_A, 0, FIXTURE_A.replace("\n", "\r\n")).reason == ALREADY)

check("B with exit 0 is not 'package not found'",
      classify(CMD_B, 0, FIXTURE_B).reason != NOT_FOUND)
check("B without 'packages failed' in its summary is not 'package not found'",
      classify(CMD_B, 1, variant(FIXTURE_B, " 1 packages failed.", "")).reason != NOT_FOUND)
check("B's entry under a Warnings heading is not 'package not found'",
      classify(CMD_B, 1, variant(FIXTURE_B, "Failures\n", "Warnings:\n")).reason != NOT_FOUND)
check("B for a different command's package is not 'package not found'",
      classify("choco install vlc -y", 1, FIXTURE_B).reason != NOT_FOUND)
check("B with no summary line is not 'package not found'",
      classify(CMD_B, 1, variant(FIXTURE_B, "Chocolatey installed 0/1 packages. 1 packages failed.\n", "")).reason != NOT_FOUND)
check("B that lost its specific reason still says Chocolatey failed, no more",
      classify(CMD_B, 1, variant(FIXTURE_B, "Failures\n", "Warnings:\n")).reason == "choco_error")


print("\n=== 1a. A pending reboot's Validation Warnings block (Fixtures C and D) ===")
check("Fixture D still carries the real trailing space after '0/1 packages.'",
      "Chocolatey installed 0/1 packages. \n" in FIXTURE_D)
check("the validation block is identical in both captures, with a blank line after it",
      FIXTURE_D.startswith(FIXTURE_C_VALIDATION + "\n"))

r = classify(CMD_A, 0, FIXTURE_D)
check("job 262's real output: already installed, a success, exit 0", r == (True, 0, ALREADY))

# Not captured whole: the validation block (captured) above Fixture B, which
# is what that run printed after it, joined the way Fixture D shows.
r = classify(CMD_B, 1, with_validation(FIXTURE_B))
check("pending reboot + not found: still package not found", r.reason == NOT_FOUND)
check("pending reboot + not found: never 'already installed'", r.reason != ALREADY)
check("pending reboot + not found: a failure, exit 1", (r.ok, r.exit_code) == (False, 1))

fresh = with_validation("Chocolatey v2.7.3\nChocolatey installed 1/1 packages.\n")
check("pending reboot + a real install: no reason invented", classify(CMD_A, 0, fresh) == (True, 0, None))

# Isolates the heading alone, on the real output. Everything else still
# says "already installed" - exit 0, a 0/1 summary with no failures, an
# entry naming the package - but the only heading above that entry is now
# "Validation Warnings:". Only an exact heading match turns this down.
moved = variant(FIXTURE_D, "\n\nWarnings:\n - paint.net - paint.net v5.1.12 already installed.\n"
                " Use --force to reinstall, specify a version to install, or try upgrade.", "\n")
moved = variant(moved, "Validation Warnings:\n",
                "Validation Warnings:\n - paint.net - paint.net v5.1.12 already installed.\n")
check("an entry under 'Validation Warnings:' is not under 'Warnings:'",
      classify(CMD_A, 0, moved).reason != ALREADY)


print("\n=== 2. Exit codes mean something only to the program that defines them ===")
MSI = 'msiexec /i "C:\\Temp\\appease_7_x.msi" /quiet /norestart'
EXE = '"C:\\Temp\\appease_7_setup.exe" /S'
CHOCO = "choco install vlc -y"

check("msiexec 1603 is Windows Installer's fatal error", classify(MSI, 1603).reason == "msi_fatal")
check("a Chocolatey 1603 is NOT given Windows Installer's meaning",
      classify(CHOCO, 1603).reason == "installer_exit_code")
check("an .exe 1603 is NOT given Windows Installer's meaning",
      classify(EXE, 1603).reason == "installer_exit_code")
check("Chocolatey's 1 is Chocolatey's error", classify(CHOCO, 1).reason == "choco_error")
check("an .exe's 1 is only a number", classify(EXE, 1).reason == "installer_exit_code")
check("msiexec's 1 is only a number", classify(MSI, 1).reason == "installer_exit_code")
check("msiexec 3010 is a restart, and a success", classify(MSI, 3010) == (True, 3010, "restart_needed"))
check("Chocolatey 3010 is a restart, and a success", classify(CHOCO, 3010) == (True, 3010, "restart_needed"))
# 3010 and 1641 are Windows' own general codes, worded in the system message
# table as a reboot needed and a reboot under way, not as installer messages -
# so both are trusted from any program, together. 1603 is in the same table
# but worded as an installer failure, and stays msiexec-only.
check("an .exe 3010 is Windows' own restart-needed code",
      classify(EXE, 3010) == (True, 3010, "restart_needed"))
check("msiexec 1641 is a restart already under way", classify(MSI, 1641).reason == "restart_started")
check("an .exe 1641 is Windows' own restart-under-way code",
      classify(EXE, 1641) == (True, 1641, "restart_started"))
check("Chocolatey 350 is a pending restart", classify(CHOCO, 350).reason == "choco_restart_pending")
check("Chocolatey 1604 is an incomplete install", classify(CHOCO, 1604).reason == "choco_install_incomplete")
check("msiexec 1604 is NOT Chocolatey's incomplete install", classify(MSI, 1604).reason == "installer_exit_code")
for code, reason in ((1602, "msi_cancelled"), (1618, "msi_busy"), (1619, "msi_unreadable"),
                     (1625, "msi_policy"), (1633, "msi_platform"), (1638, "msi_other_version")):
    check(f"msiexec {code} is {reason}", classify(MSI, code).reason == reason)
    check(f"an .exe {code} is not {reason}", classify(EXE, code).reason == "installer_exit_code")
check("MSIEXEC.EXE in capitals is still msiexec",
      classify('MSIEXEC.EXE /i "x.msi" /qn', 1603).reason == "msi_fatal")
check("an ordinary success carries no reason (exe)", classify(EXE, 0) == (True, 0, None))
check("an ordinary success carries no reason (msiexec)", classify(MSI, 0) == (True, 0, None))
check("an ordinary Chocolatey success carries no reason",
      classify(CHOCO, 0, "Chocolatey installed 1/1 packages.\n") == (True, 0, None))
check("a timeout reports NO exit code, whatever the kill left behind",
      classify(EXE, 1, "", timed_out=True) == (False, None, "timed_out"))

try:
    bool(agent.InstallResult(True, 0, None))
    truth_raises = False
except TypeError:
    truth_raises = True
check("an InstallResult refuses to be read as true or false (read .ok)", truth_raises)


print("\n=== 2a. The real install path: early exits give no exit code ===")
real_popen = agent.subprocess.Popen
real_timeout = agent.INSTALL_TIMEOUT_SECONDS
real_choco = agent.CHOCO_EXE


def closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


agent.SERVER_BASE_URL = f"http://127.0.0.1:{closed_port()}"
agent.SERVER_TIMEOUT_SECONDS = 2
r = agent.run_install_command('msiexec /i "uploads/jr_probe.msi" /qn', 9)
produced.append(r)
check("a failed download: failed, download_failed, no exit code", r == (False, None, "download_failed"))

agent.CHOCO_EXE = os.path.join(work, "no-such-choco.exe")
r = agent.run_install_command("choco install paint.net -y")
produced.append(r)
check("Chocolatey missing: failed, choco_missing, no exit code", r == (False, None, "choco_missing"))
agent.CHOCO_EXE = real_choco

r = agent.run_install_command("   ")
produced.append(r)
check("an empty command: failed, empty_command, no exit code", r == (False, None, "empty_command"))


def refuse_launch(*args, **kwargs):
    raise OSError("probe: cannot start")


agent.subprocess.Popen = refuse_launch
try:
    r = agent.run_install_command('"C:\\nope\\setup.exe" /S')
finally:
    agent.subprocess.Popen = real_popen
produced.append(r)
check("a launch failure: failed, launch_failed, no exit code", r == (False, None, "launch_failed"))

PY = f'"{sys.executable}"'
r = agent.run_install_command(f'{PY} -c "import sys; sys.exit(1603)"')
produced.append(r)
check("a real process exiting 1603 as an .exe: failed, the code, no MSI meaning",
      r == (False, 1603, "installer_exit_code"))
r = agent.run_install_command(f'{PY} -c "import sys; sys.exit(0)"')
check("a real process exiting 0: success, no reason", r == (True, 0, None))

agent.INSTALL_TIMEOUT_SECONDS = 1
try:
    r = agent.run_install_command(f'{PY} -c "import time; time.sleep(6)"')
finally:
    agent.INSTALL_TIMEOUT_SECONDS = real_timeout
produced.append(r)
check("a real timeout: failed, timed_out, and NOT the 1 the kill left behind",
      r == (False, None, "timed_out"))


print("\n=== 3. The report route keeps only what fits ===")
client = appmod.app.test_client()


def raw():
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


conn = raw()
SOFTWARE_ID = conn.execute(
    "INSERT INTO software (name, version, type, install_command) VALUES ('Probe App', '1.0', 'local', 'echo x')"
).lastrowid
conn.commit()
conn.close()


def make_job(host="LAB-JR", status="in_progress", started="now", retry=0, batch=None,
             exit_code=None, reason=None, completed=False):
    """started: "now", "old" (3 hours ago, stale) or None (never picked up)."""
    conn = raw()
    job_id = conn.execute(
        "INSERT INTO jobs (computer_hostname, software_id, status, started_at, retry_count,"
        " batch_id, software_name, exit_code, result_reason, completed_at)"
        " VALUES (?, ?, ?, CASE WHEN ? IS NULL THEN NULL ELSE DATETIME('now', ?) END, ?, ?, 'Probe App', ?, ?,"
        " CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END)",
        (host, SOFTWARE_ID, status, started, "-3 hours" if started == "old" else "+0 seconds",
         retry, batch, exit_code, reason, 1 if completed else 0),
    ).lastrowid
    conn.commit()
    conn.close()
    return job_id


def stored(job_id):
    conn = raw()
    row = conn.execute("SELECT status, exit_code, result_reason FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    return row


def report(body, host="LAB-JR"):
    job_id = make_job(host)
    payload = {"computer_hostname": host}
    payload.update(body)
    res = client.post(f"/api/agent/jobs/{job_id}/report", json=payload)
    return res, stored(job_id)


def last_notification():
    conn = raw()
    row = conn.execute("SELECT message FROM notifications ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return row["message"] if row else ""


res, row = report({"status": "completed", "exit_code": 0, "reason": ALREADY})
check("a fitting reason is accepted", res.status_code == 200)
check("...and stored with its code", (row["status"], row["exit_code"], row["result_reason"]) == ("completed", 0, ALREADY))
check("...and echoed back so the agent can log what was kept", res.get_json().get("reason") == ALREADY)

res, row = report({"status": "completed"})
check("an old agent's report (no fields) still succeeds", res.status_code == 200)
check("...and stores blanks, not guesses", row["exit_code"] is None and row["result_reason"] is None)

for label, body in (
    ("an unknown reason", {"status": "failed", "exit_code": 1, "reason": "disk_on_fire"}),
    ("reason text instead of a code", {"status": "failed", "exit_code": 1, "reason": "<b>Package not found</b>"}),
    ("a reason that is not a string", {"status": "failed", "exit_code": 1, "reason": ["choco_error"]}),
    ("a success reason on a failure", {"status": "failed", "exit_code": 3010, "reason": "restart_needed"}),
    ("a failure reason on a success", {"status": "completed", "exit_code": 1, "reason": "choco_error"}),
    ("Windows Installer's meaning on the wrong code", {"status": "failed", "exit_code": 1, "reason": "msi_fatal"}),
    ("'already installed' with a failure code", {"status": "completed", "exit_code": 1, "reason": ALREADY}),
    ("installer_exit_code with code 0", {"status": "completed", "exit_code": 0, "reason": "installer_exit_code"}),
    ("installer_exit_code with no code", {"status": "failed", "reason": "installer_exit_code"}),
    ("a reason only the server may set", {"status": "failed", "reason": "no_agent_report"}),
    ("a no-code reason on a success", {"status": "completed", "exit_code": 0, "reason": "timed_out"}),
):
    res, row = report(body)
    check(f"{label}: the report still succeeds", res.status_code == 200)
    check(f"{label}: the status still saves", row["status"] == body["status"])
    check(f"{label}: no reason is stored", row["result_reason"] is None)

res, row = report({"status": "failed", "exit_code": 1, "reason": "msi_fatal"})
check("a dropped reason does not take the exit code with it", row["exit_code"] == 1)
res, row = report({"status": "completed", "exit_code": 0, "reason": "timed_out"})
check("a no-code reason dropped for its status does not take the code either", row["exit_code"] == 0)

res, row = report({"status": "failed", "exit_code": 1, "reason": "timed_out"})
check("a timeout sent WITH a code keeps the reason", row["result_reason"] == "timed_out")
check("...and drops the code, which came from the kill", row["exit_code"] is None)

for label, value in (("True", True), ("a string", "1603"), ("a float", 1603.0),
                     ("too large", 2 ** 40), ("too negative", -(2 ** 40))):
    res, row = report({"status": "failed", "exit_code": value})
    check(f"exit code {label}: report succeeds and stores no code",
          res.status_code == 200 and row["exit_code"] is None)
res, row = report({"status": "failed", "exit_code": 4294967295, "reason": "choco_error"})
check("Chocolatey's unsigned -1 is stored intact", row["exit_code"] == 4294967295 and row["result_reason"] == "choco_error")
res, row = report({"status": "failed", "exit_code": 3221225477})
check("a crash code (0xC0000005) is stored intact", row["exit_code"] == 3221225477)

report({"status": "failed", "exit_code": 1, "reason": NOT_FOUND})
check("a failure notification carries the reason's words", last_notification().endswith("Package not found"))
report({"status": "failed", "exit_code": 1})
note = last_notification()
check("a failure with no reason keeps the plain notification", note.endswith("on LAB-JR"))

# The contract: every result this agent produced above, sent through the
# real route, is stored exactly as sent. A code the agent emits that the
# server does not know - or knows with different rules - fails here.
agent_reasons = {v for k, v in vars(agent).items() if k.startswith("REASON_")}
agent_reasons |= set(agent.MSI_EXIT_REASONS.values()) | set(agent.CHOCO_EXIT_REASONS.values())
# The Chocolatey check's own results need a Chocolatey tree and a registry to
# produce; t/test_choco_verify.py produces each one and runs the same
# contract over them.
agent_reasons -= set(getattr(agent, "CHOCO_CHECK_REASONS", ()))
# The uninstall verdicts are kept on uninstall jobs only, and every job here
# is an install; t/test_uninstall_verify.py runs the same contract over them.
agent_reasons -= set(getattr(agent, "UNINSTALL_VERDICT_REASONS", ()))
agent_reasons -= set(getattr(agent, "UNINSTALL_REFUSAL_REASONS", ()))
seen = {res.reason for res in produced if res.reason}
check(f"this suite exercised every reason the agent can send (missing: {sorted(agent_reasons - seen)})",
      agent_reasons <= seen)
mismatches = []
for result in {tuple(x) for x in produced}:
    ok, code, reason = result
    res, row = report({"status": "completed" if ok else "failed", "exit_code": code, "reason": reason})
    if res.status_code != 200 or (row["exit_code"], row["result_reason"]) != (code, reason):
        mismatches.append((result, (row["exit_code"], row["result_reason"])))
check(f"every agent result is stored exactly as sent ({len({tuple(x) for x in produced})} shapes; "
      f"mismatches: {mismatches[:3]})", not mismatches)

# The other direction: every reason the server accepts from an agent is one
# this agent sends. AWAITING_AGENT names, one by one and never by pattern,
# any the server learns before the agent sends it; it must come off the list
# once the agent does.
AWAITING_AGENT = set()
sends = {v for k, v in vars(agent).items() if k.startswith("REASON_")}
sends |= set(agent.MSI_EXIT_REASONS.values()) | set(agent.CHOCO_EXIT_REASONS.values())
accepts = {code for code, entry in appmod.JOB_RESULT_REASONS.items() if entry.get("source", "agent") == "agent"}
check(f"every reason the server accepts from an agent, the agent sends or is awaited "
      f"(unexpected: {sorted(accepts - sends - AWAITING_AGENT)})", not accepts - sends - AWAITING_AGENT)
check(f"every awaited reason is one the server accepts from an agent "
      f"(matched nothing: {sorted(AWAITING_AGENT - accepts)})", AWAITING_AGENT <= accepts)
check(f"the agent sends none of the awaited reasons yet - take these off the list: "
      f"{sorted(AWAITING_AGENT & sends)}", not AWAITING_AGENT & sends)


print("\n=== 4. The reaper says only what it knows ===")
# Seeded with a stale code on purpose. "No report from the agent" is a
# reason that has no exit code by definition, and the dashboard hides a
# reason whose code does not fit - so a leftover code would silently blank
# the one thing the reaper is there to say.
capped = make_job(host="LAB-REAP", started="old", retry=appmod.MAX_JOB_RETRIES, exit_code=7)
with appmod.app.app_context():
    db = appmod.get_db()
    appmod.reap_stale_jobs(db)
    db.commit()
row = stored(capped)
check("a job at the retry cap is failed", row["status"] == "failed")
check("...with 'no report from the agent'", row["result_reason"] == "no_agent_report")
check("...and no exit code", row["exit_code"] is None)
note = last_notification()
check("its notification does not claim a timeout", "timed out" not in note.lower())
check("its notification says what is known", "no report from the agent" in note.lower())
table_probe = appmod.job_result("failed", None, "no_agent_report", None)
check("the reaper's reason is shown on the dashboard",
      table_probe is not None and table_probe["label"] == "No report from the agent")


print("\n=== 5. The dashboard shows server words, or nothing ===")
conn = raw()
admin = conn.execute("SELECT id, username FROM admins LIMIT 1").fetchone()
conn.close()
admin_client = appmod.app.test_client()
with admin_client.session_transaction() as sess:
    sess["admin_id"] = admin["id"]
    sess["admin_role"] = "admin"
    sess["admin_username"] = admin["username"]


class JobsTable(html.parser.HTMLParser):
    """The jobs table: headers, cell text and tones, and each row's details."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.headers, self.rows, self.details, self.menus = [], {}, {}, {}
        # Per row: does its menu offer Delete, and does the row carry Cancel
        # and Retry (the menu builds those two from the <tr>'s attributes).
        self.menu_delete, self.row_cancel, self.row_retry = {}, {}, {}
        self.in_table = False
        self.row = self.cell = self.row_id = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table" and "data-dt-table" in a:
            self.in_table = True
        if not self.in_table:
            return
        if tag == "th":
            self.headers.append({"label": a.get("data-dt-label") or "", "facet": "data-dt-facet" in a,
                                 "hidden": "data-dt-hidden" in a})
        elif tag == "tr" and "data-dt-id" in a:
            self.row_id = a["data-dt-id"]
            self.row = self.rows.setdefault(self.row_id, [])
            self.row_cancel[self.row_id] = bool(a.get("data-dt-cancel-url"))
            self.row_retry[self.row_id] = bool(a.get("data-dt-retry-url"))
            try:
                self.details[self.row_id] = json.loads(a.get("data-job-details") or "")
            except ValueError:
                self.details[self.row_id] = None
        elif tag == "td" and self.row is not None:
            self.cell = {"text": "", "tones": [], "pill": None}
            self.row.append(self.cell)
        elif tag == "span" and self.cell is not None and "job-result" in (a.get("class") or ""):
            self.cell["tones"].append(a.get("data-tone"))
        elif tag == "span" and self.cell is not None and "dt-pill" in (a.get("class") or ""):
            self.cell["pill"] = a.get("data-tone")
        elif tag == "button" and self.row is not None and "data-dt-rowmenu" in a:
            self.menus[self.row_id] = "data-dt-details" in a
            self.menu_delete[self.row_id] = "data-dt-delete-url" in a

    def handle_endtag(self, tag):
        if tag == "table":
            self.in_table = False
        elif tag == "td":
            self.cell = None
        elif tag == "tr":
            self.row = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell["text"] += data


cases = {
    "restart": make_job(status="completed", exit_code=3010, reason="restart_needed", completed=True),
    "already": make_job(status="completed", exit_code=0, reason=ALREADY, completed=True),
    "notfound": make_job(status="failed", exit_code=1, reason=NOT_FOUND, completed=True),
    "unsigned": make_job(status="failed", exit_code=4294967295, reason="choco_error"),
    "old": make_job(status="completed"),
    "plain_ok": make_job(status="completed", exit_code=0),
    "bare_fail": make_job(status="failed", exit_code=1),
    "hostile": make_job(status="failed", exit_code=1, reason="<i>pwned</i>"),
    "misfit": make_job(status="failed", exit_code=1, reason="msi_fatal"),
    "status_misfit": make_job(status="failed", exit_code=3010, reason="restart_needed"),
    "timeout": make_job(status="failed", reason="timed_out", completed=True),
    "exe_ok": make_job(status="completed", exit_code=1641, reason="installer_exit_code"),
    "reaped": make_job(status="failed", reason="no_agent_report", retry=2, completed=True),
    "reaped_once": make_job(status="failed", reason="no_agent_report", retry=1, completed=True),
    "never_picked": make_job(status="pending", started=None),
}
page = admin_client.get("/")
body = page.get_data(as_text=True)
check("dashboard renders", page.status_code == 200)
table = JobsTable()
table.feed(body)
labels = [h["label"] for h in table.headers]
check("a Result column exists", "Result" in labels)
check("Result is filterable", any(h["label"] == "Result" and h["facet"] for h in table.headers))
check("Result starts hidden (empty on most rows; its filter is the point)",
      any(h["label"] == "Result" and h["hidden"] for h in table.headers))
check("no other column starts hidden", [h["label"] for h in table.headers if h["hidden"]] == ["Result"])
check("the exit code is no longer a table column - it is in Details", "Exit code" not in labels)
check("every job row has one cell per header",
      table.rows and all(len(cells) == len(table.headers) for cells in table.rows.values()))
check("every job row carries readable details", table.details and all(isinstance(d, dict) for d in table.details.values()))
check("every job row's menu offers Details", table.menus and all(table.menus.values()))
check("...and no job row's menu offers Delete",
      table.menu_delete and not any(table.menu_delete.values()))
check("a pending job's menu still offers Cancel", table.row_cancel.get(str(cases["never_picked"])) is True)
check("Cancel is offered only on pending jobs",
      not any(on for job_id, on in table.row_cancel.items() if job_id != str(cases["never_picked"])))
check("Retry is still offered on the rows the server marks retryable",
      any(table.row_retry.values()))
# The action bar keeps its Delete, posting to the same route behind the
# same confirm dialog; the row menu has none.
bar = re.search(r'<div class="dt-actionbar hidden" data-dt-actionbar.*?</div>', body, re.S)
check("the action bar still has Delete, posting to /jobs/bulk/delete",
      bar is not None and re.search(r'<form method="POST" action="/jobs/bulk/delete" data-dt-form>\s*'
                                    r'<button type="submit" class="dt-btn" data-tone="danger" data-dt-act="delete">Delete</button>',
                                    bar.group(0)) is not None)
check("...behind the same confirm dialog", 'id="dt-confirm-delete"' in body and 'data-dt-confirm="delete"' in body)
# A missing column fails the checks below by name instead of crashing the
# suite, so one broken header cannot hide every assertion after it.
S, R = (labels.index(n) if n in labels else None for n in ("Status", "Result"))


def cells(key):
    row = table.rows.get(str(cases[key]), [])
    if None in (S, R) or len(row) != len(labels):
        return None
    squash = lambda s: " ".join(s.split())
    return squash(row[S]["text"]), squash(row[R]["text"]), row[R]["tones"]


def details(key):
    return table.details.get(str(cases[key])) or {}


check("restart needed: words and warn tone in the table", cells("restart") == ("Completed", "Restart needed", ["warn"]))
check("restart needed: its code in Details", details("restart").get("exit") == "3010")
c = cells("already")
check("already installed: the Status cell still says only Completed", c and c[0] == "Completed")
check("already installed: the Result says nothing was installed, in warn",
      c and "nothing installed" in c[1] and c[2] == ["warn"])
check("already installed: Details shows exit 0, because a reason came with it", details("already").get("exit") == "0")
check("package not found: bad tone in the table", cells("notfound") == ("Failed", "Package not found", ["bad"]))
check("package not found: its code in Details", details("notfound").get("exit") == "1")
check("Chocolatey's unsigned -1 shows as hex in Details", details("unsigned").get("exit") == "0xFFFFFFFF")
check("a job with no result data: a blank cell", cells("old") == ("Completed", "", []))
check("...and blanks in Details, not guesses",
      details("old").get("result-label") == "" and details("old").get("exit") == "")
check("an ordinary success hides its exit code 0 in Details too", details("plain_ok").get("exit") == "")
check("a failure with a code but no reason: blank Result, the code in Details",
      cells("bare_fail") == ("Failed", "", []) and details("bare_fail").get("exit") == "1")
check("a stored reason the server does not know shows nothing",
      cells("hostile") == ("Failed", "", []) and details("hostile").get("result-label") == "")
check("...and its text never reaches the page, not even inside the details JSON", "pwned" not in body)
check("a stored reason that does not fit its row shows nothing, not a guess",
      cells("misfit") == ("Failed", "", []) and details("misfit").get("exit") == "1")
check("a stored success reason on a failed row shows nothing, only the code",
      cells("status_misfit") == ("Failed", "", []) and details("status_misfit").get("exit") == "3010")

# The timeout: the agent gave up; nobody saw the install fail.
check("a timeout reads as the agent giving up, not the install failing",
      cells("timeout") == ("Failed", "Agent stopped waiting for the install", ["bad"]))
check("...with no exit code", details("timeout").get("exit") == "")
check("...and a note that the install may still have finished",
      "may still have finished on the PC" in details("timeout").get("result-note", ""))
check("a reason without a note carries none", details("notfound").get("result-note") == "")

check("an .exe's unexplained success code is only a number, in a readable tone",
      cells("exe_ok") == ("Completed", "Installer exit code", ["info"]) and details("exe_ok").get("exit") == "1641")

# Timestamps are labelled by what was observed.
check("a reported job's time is 'Reported by the agent', not 'Finished at'",
      details("restart").get("finished-label") == "Reported by the agent")
check("...with no reaper note", details("restart").get("finished-note") == "")
check("a reaped job's time is 'Server stopped waiting'",
      details("reaped").get("finished-label") == "Server stopped waiting")
check("...with a note that it is not when the PC finished",
      "not when the PC finished" in details("reaped").get("finished-note", ""))
check("...its reason is the server's own", details("reaped").get("result-label") == "No report from the agent")
check("...and it says how many times it was re-queued", details("reaped").get("requeued") == "2 times")
check("one re-queue reads singular", details("reaped_once").get("requeued") == "1 time")
check("a job never re-queued says nothing about it", details("restart").get("requeued") == "")
check("a job never picked up has no pick-up time", details("never_picked").get("picked") == "")
check("...and no report time", details("never_picked").get("finished") == "")
check("a queued time is always given, in Manila time", details("never_picked").get("queued", "") not in ("", "-"))
check("software is named as the library listed it when queued", details("restart").get("software") == "Probe App 1.0")

# The panel's pill and the table's pill are the same pill.
mismatch = []
for row_id, row in table.rows.items():
    d = table.details.get(row_id) or {}
    cell = row[S] if S is not None and len(row) > S else {"text": "", "pill": None}
    if (d.get("status-label"), d.get("status-tone")) != (" ".join(cell["text"].split()), cell["pill"]):
        mismatch.append(row_id)
check(f"Details' status matches the table's pill on every row (mismatches: {mismatch[:3]})", not mismatch)
# Matching each other is not enough - both could be wrong together.
check("a failed job's pill is bad, in both places",
      details("notfound").get("status-tone") == "bad" and details("notfound").get("status-label") == "Failed")
check("a completed job's pill is good, in both places",
      details("restart").get("status-tone") == "good" and details("restart").get("status-label") == "Completed")
check("a pending job's pill is warn, in both places",
      details("never_picked").get("status-tone") == "warn" and details("never_picked").get("status-label") == "Pending")
values = " ".join(str(v) for d in table.details.values() if d for v in d.values()).lower()
check("no job's details claim an unknown anything", "unknown" not in values)
table_text = " ".join(cell["text"] for row in table.rows.values() for cell in row).lower()
check("no job row claims an unknown error", "unknown" not in table_text)


print("\n=== 6. The deploy progress JSON carries the same words ===")
batch = "jrbatch01"
b_rows = {
    "reasoned": make_job(host="LAB-B1", status="failed", exit_code=1, reason=NOT_FOUND, batch=batch),
    "bare": make_job(host="LAB-B2", status="completed", exit_code=0, batch=batch),
}
res = admin_client.get(f"/api/deployments/{batch}/progress")
data = res.get_json() or {}
jobs = {j["hostname"]: j for g in data.get("groups", []) for j in g["jobs"]}
check("progress JSON renders", res.status_code == 200 and len(jobs) == 2)
check("a reasoned job carries label, tone and code",
      jobs.get("LAB-B1", {}).get("result") == {"label": "Package not found", "tone": "bad", "exit_code": "1", "note": ""})
check("a plain success carries no result", jobs.get("LAB-B2", {}).get("result", "missing") is None)
check("the batch still counts as finished", data.get("finished") == 2)


print("\n=== 7. End to end: the real agent, a real server, the captured output ===")
if os.name != "nt":
    print("  SKIPPED - needs Windows to run the stand-in choco.bat.")
else:
    from werkzeug.serving import make_server  # noqa: E402

    live = make_server("127.0.0.1", 0, appmod.app, threaded=True)
    threading.Thread(target=live.serve_forever, daemon=True).start()
    agent.SERVER_BASE_URL = f"http://127.0.0.1:{live.server_port}"
    agent.SERVER_TIMEOUT_SECONDS = 20
    agent.COMPUTER_HOSTNAME = "LAB-JR-E2E"

    def fake_choco(fixture, code, tag):
        text_path = os.path.join(work, f"choco_out_{tag}.txt")
        with open(text_path, "w", encoding="utf-8", newline="\r\n") as handle:
            handle.write(fixture)
        # In bin\ of an otherwise empty folder: after "already installed" the
        # agent reads Chocolatey's records two levels up from choco, and an
        # empty root makes that check "can't verify" on purpose, on any PC.
        # t/test_choco_verify.py tests the check itself.
        os.makedirs(os.path.join(work, f"choco_root_{tag}", "bin"), exist_ok=True)
        bat = os.path.join(work, f"choco_root_{tag}", "bin", "choco.bat")
        with open(bat, "w", encoding="ascii", newline="\r\n") as handle:
            handle.write(f'@echo off\ntype "{text_path}"\nexit /b {code}\n')
        return bat

    def deploy(command):
        conn = raw()
        sid = conn.execute(
            "INSERT INTO software (name, version, type, install_command) VALUES ('E2E', '1', 'online', ?)",
            (command,)).lastrowid
        jid = conn.execute(
            "INSERT INTO jobs (computer_hostname, software_id, status, software_name) VALUES ('LAB-JR-E2E', ?, 'pending', 'E2E')",
            (sid,)).lastrowid
        conn.commit()
        conn.close()
        agent.poll_once()
        return jid

    agent.CHOCO_EXE = fake_choco(FIXTURE_A, 0, "a")
    jid = deploy(CMD_A)
    row = stored(jid)
    check("Fixture A through the whole pipeline: completed, 0, already installed",
          (row["status"], row["exit_code"], row["result_reason"]) == ("completed", 0, ALREADY))

    agent.CHOCO_EXE = fake_choco(FIXTURE_D, 0, "d")
    jid_d = deploy(CMD_A)
    row = stored(jid_d)
    check("Fixture D (job 262) through the whole pipeline: completed, 0, already installed",
          (row["status"], row["exit_code"], row["result_reason"]) == ("completed", 0, ALREADY))

    agent.CHOCO_EXE = fake_choco(FIXTURE_B, 1, "b")
    jid_b = deploy(CMD_B)
    row = stored(jid_b)
    check("Fixture B through the whole pipeline: failed, 1, package not found",
          (row["status"], row["exit_code"], row["result_reason"]) == ("failed", 1, NOT_FOUND))
    check("...and the admin is told why", last_notification().endswith("Package not found"))

    agent.CHOCO_EXE = real_choco
    jid_exe = deploy(f'{PY} -c "import sys; sys.exit(1603)"')
    row = stored(jid_exe)
    check("an .exe exiting 1603 through the pipeline: failed, 1603, no MSI meaning",
          (row["status"], row["exit_code"], row["result_reason"]) == ("failed", 1603, "installer_exit_code"))

    table = JobsTable()
    table.feed(admin_client.get("/").get_data(as_text=True))
    row_a = table.rows.get(str(jid), [])
    check("the dashboard shows the VM's bug instead of hiding it",
          None not in (S, R) and len(row_a) == len(labels) and "nothing installed" in row_a[R]["text"]
          and " ".join(row_a[S]["text"].split()) == "Completed")
    live.shutdown()


print("\n=== 8. Styling: tokens only, readable on every row state ===")
css = open(os.path.join(server_dir, "static", "css", "app.css"), encoding="utf-8").read()
tw = open(os.path.join(server_dir, "static", "css", "tailwind.css"), encoding="utf-8").read()
tones_used = set()
for entry in appmod.JOB_RESULT_REASONS.values():
    tone = entry["tone"]
    tones_used |= set(tone.values()) if isinstance(tone, dict) else {tone}
base_rule = re.search(r"\.job-result\s*\{([^}]*)\}", css)
check(".job-result's base rule exists and sets no display",
      base_rule is not None and "display" not in base_rule.group(1))
for tone in sorted(tones_used):
    rule = re.search(r'\.job-result\[data-tone="' + tone + r'"\]\s*\{([^}]*)\}', css)
    check(f".job-result has a {tone} tone", rule is not None)
    if rule:
        check(f".job-result {tone} reads the shared --tone-{tone}-fg", f"var(--tone-{tone}-fg)" in rule.group(1))
        check(f".job-result {tone} sets no display", "display" not in rule.group(1))


def hexes(name, text):
    """Every value of a colour token, #rgb expanded to #rrggbb."""
    out = []
    for value in re.findall(r"--" + name + r":\s*(#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b)", text):
        out.append(value if len(value) == 7 else "#" + "".join(ch * 2 for ch in value[1:]))
    return out


def lum(h):
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast(a, b):
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


grounds = {
    "light": {"card": hexes("color-card", tw)[0], "hovered row": hexes("color-subtle", tw)[0],
              "selected row": hexes("color-row-selected", css)[0], "Details panel": hexes("color-canvas", tw)[0]},
    "dark": {"card": hexes("color-card", css)[0], "hovered row": hexes("color-subtle", css)[0],
             "selected row": hexes("color-row-selected", css)[1], "Details panel": hexes("color-canvas", css)[0]},
}
for theme, index in (("light", 0), ("dark", 1)):
    for tone in sorted(tones_used):
        fg = hexes(f"tone-{tone}-fg", css)[index]
        for ground, bg in grounds[theme].items():
            ratio = contrast(fg, bg)
            check(f"{theme} {tone} reason text on a {ground}: {ratio:.2f}:1", ratio >= 4.5)

for theme, index, source in (("light", 0, tw), ("dark", 0, css)):
    muted = hexes("color-muted", source)[index]
    ratio = contrast(muted, grounds[theme]["Details panel"])
    check(f"{theme} Details notes and blank dashes on the panel: {ratio:.2f}:1", ratio >= 4.5)
for selector in (r"\.details-note\s*\{([^}]*)\}", r"\.details-blank\s*\{([^}]*)\}"):
    rule = re.search(selector, css)
    check(f"{selector[:18]}... is coloured by --color-muted, the token measured above",
          rule is not None and "var(--color-muted)" in rule.group(1))
for selector in (r"\.details-list\s*\{", r"\.details-note\s*\{", r"\.details-blank\s*\{"):
    check(f"app.css defines {selector.split(chr(92))[1][1:]}", re.search(selector, css) is not None)
for selector in (r"\.details-list\s*>\s*dt\s*\{([^}]*)\}", r"\.details-list\s*>\s*dd\s*\{([^}]*)\}",
                 r"\.details-note\s*\{([^}]*)\}", r"\.details-list\s*\{([^}]*)\}"):
    rule = re.search(selector, css)
    check(f"{selector[:24]}... sets no margin that would beat a Tailwind utility",
          rule is not None and not re.search(r"(^|[;\s])margin\s*:", rule.group(1)))

js = open(os.path.join(server_dir, "static", "js", "deploy-panel.js"), encoding="utf-8").read()
check("deploy-panel.js hands out .job-result and app.css defines it",
      "'job-result'" in js and ".job-result" in css)


print("\n=== 9. Migration ===")
legacy = os.path.join(work, "legacy.db")
conn = sqlite3.connect(legacy)
conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, computer_hostname TEXT NOT NULL,"
             " software_id INTEGER NOT NULL, status TEXT DEFAULT 'pending',"
             " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, completed_at TIMESTAMP)")
conn.execute("INSERT INTO jobs (computer_hostname, software_id, status) VALUES ('OLD-PC', 1, 'completed')")
conn.commit()
conn.close()
saved_path = database.DB_PATH
database.DB_PATH = legacy
try:
    database.init_db()
    database.init_db()
    migrated_twice = True
except Exception as exc:  # noqa: BLE001
    migrated_twice = exc
database.DB_PATH = saved_path
check(f"an existing database migrates, twice over ({migrated_twice})", migrated_twice is True)
conn = sqlite3.connect(legacy)
columns = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(jobs)")}
old_row = conn.execute("SELECT status, exit_code, result_reason FROM jobs WHERE computer_hostname = 'OLD-PC'").fetchone()
conn.close()
check("jobs gains exit_code INTEGER", columns.get("exit_code") == "INTEGER")
check("jobs gains result_reason TEXT", columns.get("result_reason") == "TEXT")
check("a job from before the migration keeps its status", old_row and old_row[0] == "completed")
check("...and gets blanks, not a guessed reason", old_row and old_row[1] is None and old_row[2] is None)


print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)

for handler in list(agent.logger.handlers):
    handler.close()
os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

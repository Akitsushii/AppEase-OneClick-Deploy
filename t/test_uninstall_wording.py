"""How installs and uninstalls are worded: every result reason reads as
agreed on each kind of job (WORDS below; a missing reason fails), and the
report route keeps a reason only for the kind of job it belongs to.
"""
import json
import os
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
from html import unescape

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


work = tempfile.mkdtemp(prefix="appease_unwording_")
server_dir = os.path.join(work, "server")
shutil.copytree(os.path.join(ROOT, "server"), server_dir, ignore=shutil.ignore_patterns(
    "appease.db*", "__pycache__", "*.pyc", ".env", "uploads", "staging", "report_photos", "avatars", "venv"))
sys.path.insert(0, server_dir)
os.chdir(server_dir)
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))

import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()
import app as appmod  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

if not hasattr(appmod, "job_reason_entry"):
    print("COULD NOT RUN - app.py predates uninstall wording.")
    sys.exit(3)
appmod.app.config["TESTING"] = True
PASSWORD = "Str0ng!Passw0rd"
UNINSTALL = appmod.JOB_ACTION_UNINSTALL


def write(sql, args=()):
    conn = sqlite3.connect(database.DB_PATH)
    try:
        cursor = conn.execute(sql, args)
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def rows(sql, args=()):
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


write("UPDATE admins SET password_hash = ?, must_change_password = 0 WHERE username = 'admin'",
      (generate_password_hash(PASSWORD),))
client = appmod.app.test_client()
client.post("/login", data={"username": "admin", "password": PASSWORD})
SOFTWARE = write("INSERT INTO software (name, version, type, install_command) "
                 "VALUES ('Probe', '1', 'online', 'choco install probe -y')")
PC = write("INSERT INTO computers (hostname, status) VALUES ('LAB-PC', 'online')")
ROW = write("INSERT INTO installations (computer_id, software_name, method, record_key, state) "
            "VALUES (?, 'Gone App', 'msi', 'msi:{X}', 'installed')", (PC,))


def job(status, action=None, reason=None, exit_code=None, batch=None, name=None):
    uninstall = action == UNINSTALL
    return write(
        "INSERT INTO jobs (computer_hostname, software_id, status, software_name, software_version, action, "
        "installation_id, result_reason, exit_code, batch_id, started_at) "
        "VALUES ('LAB-PC', ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
        (0 if uninstall else SOFTWARE, status, name or ("Gone App" if uninstall else "Probe"),
         "24.08" if uninstall else "1", action, ROW if uninstall else None, reason, exit_code, batch))


# ---------------------------------------------------------------------------
print("1. Every reason, worded for each kind of job")
# ---------------------------------------------------------------------------
# (install, uninstall). None: the reason never accompanies that kind of job.
WORDS = {
    "restart_needed": ("Restart needed", "Restart needed to finish removing"),
    "restart_started": ("Restarting to finish", "Restarting to finish removing"),
    "choco_already_installed": ("Already installed per Chocolatey, nothing installed", None),
    "choco_confirmed_installed": ("Already installed, Windows still lists it", None),
    "choco_reinstalled": ("Reinstalled, it had been removed outside Chocolatey", None),
    "choco_reinstall_unconfirmed": ("Reinstalled, but Windows does not confirm it", None),
    "choco_package_not_found": ("Package not found", None),
    "choco_error": ("Chocolatey reported an error", "Chocolatey reported an error"),
    "choco_restart_pending": ("Stopped by a pending restart", "Stopped by a pending restart"),
    "choco_install_incomplete": ("Install suspended before finishing", None),
    "msi_cancelled": ("Install was cancelled", "Uninstall was cancelled"),
    "msi_fatal": ("Windows Installer fatal error", "Windows Installer fatal error"),
    "msi_busy": ("Another install was already running", "Another install or uninstall was already running"),
    "msi_unreadable": ("Installer package could not be opened", None),
    "msi_policy": ("Blocked by system policy", "Blocked by system policy"),
    "msi_platform": ("Not supported on this Windows", None),
    "msi_other_version": ("A different version is already installed", None),
    "installer_exit_code": ("Installer exit code", "Uninstaller exit code"),
    "timed_out": ("Agent stopped waiting for the install", "Agent stopped waiting for the uninstall"),
    "download_failed": ("Could not download the installer", None),
    "choco_missing": ("Chocolatey is not installed on this PC", "Chocolatey is not installed on this PC"),
    "empty_command": ("No install command to run", None),
    "launch_failed": ("Could not start the installer", "Could not start the uninstaller"),
    "no_agent_report": ("No report from the agent", "No report from the agent"),
    "already_removed": (None, "Already gone, nothing to remove"),
    "removed_folder_left": (None, "Removed; its folder was left behind"),
    "still_listed": (None, "Uninstaller finished, but Windows still lists it"),
    "removal_unverified": (None, "Uninstaller finished, removal not confirmed"),
    "choco_not_managed": (None, "Chocolatey no longer manages it, left in place"),
    "choco_shared_dependency": (None, "Another Chocolatey package needs it, left in place"),
    "choco_records_unreadable": (None, "Chocolatey's records could not be read, so nothing was run"),
    "choco_own_entry_not_found": (None, "Not removed: couldn't tell which entry is the program"),
    "choco_own_entry_not_msi": (None, "Not removed: needs manual uninstall"),
    "uninstaller_unsafe_location": (None, "Its uninstaller is outside Program Files, so nothing was run"),
    "no_quiet_uninstall": (None, "No quiet uninstall is listed for it any more, so nothing was run"),
    "uninstaller_unsafe_user_location": (None, "Its uninstaller is outside its account's AppData folder, so nothing "
                                               "was run"),
    "shared_runtime": (None, "Shared runtime, left in place"),
    "pickup_expired": (None, "Computer went offline before the agent picked it up"),
    "agent_cannot_uninstall": (None, "This computer's agent cannot uninstall yet"),
    "removed_restart_needed": (None, "Removed; restart needed to finish"),
    "no_longer_installed": (None, "It was no longer installed when the agent asked for it"),
    "uninstall_job_invalid": (None, "The uninstall job was not understood, so nothing was run"),
    "identity_mismatch": (None, "This PC lists it differently, so nothing was run"),
    "registry_unreadable": (None, "Windows' program list could not be read, so nothing was run"),
    "protected_software": (None, "Protected, left in place"),
    "agent_error": (None, "The agent hit an error during the uninstall"),
}
# Notes, where one is shown. Anything not listed shows no note.
NOTES = {
    ("timed_out", None): "The install ran past the agent's time limit. It may still have finished on the PC.",
    ("timed_out", UNINSTALL): "The uninstall ran past the agent's time limit. It may still have finished on the PC.",
    ("choco_already_installed", None): "The agent could not check whether Windows still lists it.",
    ("choco_reinstall_unconfirmed", None): "Chocolatey reported the reinstall finished. Checked again afterwards, "
                                           "Windows did not confirm the software is installed.",
    ("removed_folder_left", UNINSTALL): "Windows no longer lists it, but its install folder is still on the PC.",
    ("still_listed", UNINSTALL): "The uninstaller reported success, but the software was still in Windows' "
                                 "program list when the agent stopped checking.",
    ("removal_unverified", UNINSTALL): "The agent could not read Windows' program list afterwards to check.",
    ("choco_not_managed", UNINSTALL): "Chocolatey has no record of the package, so AppEase did not touch it.",
    ("choco_shared_dependency", UNINSTALL): "Another package installed through Chocolatey depends on it, "
                                            "so AppEase did not remove it.",
    ("shared_runtime", UNINSTALL): "Other software on the PC may depend on it, so AppEase never removes it.",
    ("choco_own_entry_not_found", UNINSTALL): "AppEase couldn't tell which uninstall entry belongs to this "
                                              "program, so nothing was removed.",
    ("choco_own_entry_not_msi", UNINSTALL): "This program was installed together with a shared runtime and isn't "
                                            "an MSI program, so AppEase can't remove it safely. Remove it manually "
                                            "from the computer.",
    ("pickup_expired", UNINSTALL): "Uninstalls run only on computers that are online. "
                                   "Queue it again once this one is back.",
    ("agent_cannot_uninstall", UNINSTALL): "Update the agent on this computer, then queue the uninstall again.",
    ("removed_restart_needed", UNINSTALL): "Windows no longer lists it. Restart the PC to finish removing it.",
    ("identity_mismatch", UNINSTALL): "Windows lists it differently from what AppEase recorded, so AppEase did "
                                      "not touch it.",
    ("protected_software", UNINSTALL): "AppEase's own agent runs on it, so AppEase never removes it.",
    ("agent_error", UNINSTALL): "It may or may not have run. The agent's log on the PC says what happened.",
}
check(f"the list covers every reason the server knows (unlisted: "
      f"{sorted(set(appmod.JOB_RESULT_REASONS) - set(WORDS))})", set(appmod.JOB_RESULT_REASONS) == set(WORDS))


def fitting(code):
    """A status and exit code this reason may accompany, from its own rules."""
    entry = appmod.JOB_RESULT_REASONS[code]
    rule = entry["exit"]
    code_value = None if rule == appmod.JOB_EXIT_NONE else 7 if rule == appmod.JOB_EXIT_NONZERO else rule[0]
    return entry["statuses"][0], code_value


for code, (install_words, uninstall_words) in sorted(WORDS.items()):
    if code not in appmod.JOB_RESULT_REASONS:
        continue
    status, exit_code = fitting(code)
    for action, expected in ((None, install_words), (UNINSTALL, uninstall_words)):
        kind = "an uninstall" if action else "an install"
        shown = appmod.job_result(status, exit_code, code, action)
        if expected is None:
            check(f"{code} shows nothing on {kind}", shown is None or shown["label"] == "")
        else:
            check(f"{code} on {kind} reads {expected!r}", shown is not None and shown["label"] == expected)
            check(f"{code} on {kind} carries the agreed note",
                  shown is not None and shown["note"] == NOTES.get((code, action), ""))
unknown = appmod.job_result("failed", 1602, "msi_cancelled", "reinstall")
check("a reason on a kind of job nothing writes shows no words - only its exit code, like any unknown reason",
      unknown is not None and unknown["label"] == "" and unknown["exit_code"] == "1602")

# ---------------------------------------------------------------------------
print("2. The report route keeps a reason only on its own kind of job")
# ---------------------------------------------------------------------------


def report(action, status, exit_code, reason):
    job_id = job("in_progress", action)
    body = {"computer_hostname": "LAB-PC", "status": status, "reason": reason}
    if exit_code is not None:
        body["exit_code"] = exit_code
    res = client.post(f"/api/agent/jobs/{job_id}/report", json=body)
    row = rows("SELECT status, exit_code, result_reason FROM jobs WHERE id = ?", (job_id,))[0]
    return res.status_code, (row["status"], row["exit_code"], row["result_reason"])


for exit_code in (None, 0, 1605):
    check(f"an uninstall's already_removed with exit {exit_code} is kept",
          report(UNINSTALL, "completed", exit_code, "already_removed") == (200, ("completed", exit_code, "already_removed")))
check("...but not with an exit code that does not mean it (1)",
      report(UNINSTALL, "completed", 1, "already_removed") == (200, ("completed", 1, None)))
for reason, status, code in (("already_removed", "completed", 0), ("still_listed", "failed", 0),
                             ("removed_folder_left", "completed", 0), ("choco_not_managed", "failed", None)):
    check(f"{reason} on an install job is never kept", report(None, status, code, reason)[1][2] is None)
for reason, status, code in (("choco_reinstalled", "completed", 0), ("msi_unreadable", "failed", 1619),
                             ("empty_command", "failed", None), ("download_failed", "failed", None)):
    check(f"{reason} on an uninstall job is never kept", report(UNINSTALL, status, code, reason)[1][2] is None)
for reason, status, code in (("still_listed", "failed", 0), ("removal_unverified", "completed", 0),
                             ("msi_cancelled", "failed", 1602), ("restart_needed", "completed", 3010)):
    check(f"{reason} on an uninstall job is kept",
          report(UNINSTALL, status, code, reason) == (200, (status, code, reason)))
for reason in ("pickup_expired", "agent_cannot_uninstall"):
    check(f"{reason}, which only the server sets, is never kept from an agent",
          report(UNINSTALL, "failed", None, reason)[1][2] is None)
check("a timed-out uninstall sent with a code keeps the reason and drops the code",
      report(UNINSTALL, "failed", 1, "timed_out")[1] == ("failed", None, "timed_out"))

# ---------------------------------------------------------------------------
print("3. The dashboard, Job Details and deploy progress use the job's own words")
# ---------------------------------------------------------------------------
write("DELETE FROM jobs")
batch = "c" * 32
J_IN = job("failed", None, "msi_cancelled", 1602, batch=batch)
J_UN = job("failed", UNINSTALL, "msi_cancelled", 1602, batch=batch)
J_TIMEOUT = job("failed", UNINSTALL, "timed_out")
J_PICKUP = job("cancelled", UNINSTALL, "pickup_expired")
J_WRONG = job("completed", None, "already_removed", 0)
page = client.get("/").get_data(as_text=True)


def row_html(job_id):
    match = re.search(r'<tr data-dt-id="%d".*?</tr>' % job_id, page, re.S)
    return match.group(0) if match else ""


def details(job_id):
    match = re.search(r"data-job-details='([^']*)'", row_html(job_id))
    return json.loads(unescape(match.group(1))) if match else {}


def result_cell(job_id):
    match = re.search(r'<span class="job-result"[^>]*>([^<]*)</span>', row_html(job_id))
    return match.group(1) if match else ""


check("the table says Install was cancelled on the install", result_cell(J_IN) == "Install was cancelled")
check("...and Uninstall was cancelled on the uninstall", result_cell(J_UN) == "Uninstall was cancelled")
check("Job Details says Uninstall was cancelled", details(J_UN).get("result-label") == "Uninstall was cancelled")
check("a timed-out uninstall's Job Details carries the uninstall note",
      details(J_TIMEOUT).get("result-note") == NOTES[("timed_out", UNINSTALL)])
check("a cancelled uninstall shows why the server cancelled it, with its note",
      details(J_PICKUP).get("result-label") == WORDS["pickup_expired"][1]
      and details(J_PICKUP).get("result-note") == NOTES[("pickup_expired", UNINSTALL)])
check("an uninstall reason stored on an install shows nothing", result_cell(J_WRONG) == ""
      and details(J_WRONG).get("result-label") == "")
progress = client.get(f"/api/deployments/{batch}/progress").get_json()
labels = {g["label"]: [j["result"]["label"] for j in g["jobs"]] for g in progress["groups"]}
check("deploy progress words each job for its own kind",
      labels == {"Probe 1": ["Install was cancelled"], "Gone App 24.08": ["Uninstall was cancelled"]})

# ---------------------------------------------------------------------------
print("4. Notifications")
# ---------------------------------------------------------------------------


def notes():
    return [(r["message"], r["severity"]) for r in rows("SELECT message, severity FROM notifications ORDER BY id")]


def finish(job_id, status, exit_code=None, reason=None):
    body = {"computer_hostname": "LAB-PC", "status": status, "reason": reason}
    if exit_code is not None:
        body["exit_code"] = exit_code
    return client.post(f"/api/agent/jobs/{job_id}/report", json=body).status_code


write("DELETE FROM notifications")
finish(job("in_progress", UNINSTALL), "failed", 1602, "msi_cancelled")
finish(job("in_progress", None), "failed", 1602, "msi_cancelled")
finish(job("in_progress", UNINSTALL), "failed")
check("failure notifications say which kind of job failed, in its own words", [m for m, _ in notes()] == [
    "Uninstall failed: Gone App on LAB-PC — Uninstall was cancelled",
    "Install failed: Probe on LAB-PC — Install was cancelled",
    "Uninstall failed: Gone App on LAB-PC",
])

write("DELETE FROM notifications")
b1 = "d" * 32
first = job("in_progress", UNINSTALL, batch=b1)
second = job("in_progress", UNINSTALL, batch=b1)
third = job("in_progress", UNINSTALL, batch=b1)
finish(first, "completed", 0)
finish(second, "completed", 0, "removal_unverified")
finish(third, "failed", 0, "still_listed")
check("an uninstall batch counts what was removed - not an unconfirmed removal",
      notes()[-1] == ("Uninstall batch finished — 1 of 3 removed", "warning"))
write("DELETE FROM notifications")
b2 = "e" * 32
first = job("in_progress", UNINSTALL, batch=b2)
second = job("in_progress", UNINSTALL, batch=b2)
finish(first, "completed", 0, "removed_folder_left")
finish(second, "completed", None, "already_removed")
check("...and one where everything went reads as all removed",
      notes() == [("Uninstall batch finished — 2 of 2 removed", "info")])
write("DELETE FROM notifications")
b3 = "f" * 32
first = job("in_progress", None, batch=b3)
second = job("in_progress", None, batch=b3)
finish(first, "completed", 0)
finish(second, "failed", 1603, "msi_fatal")
check("an install batch is worded as before",
      notes()[-1] == ("Deployment batch finished — 1 of 2 succeeded", "warning"))

write("DELETE FROM notifications")
for action in (UNINSTALL, None):
    stale = job("in_progress", action)
    write("UPDATE jobs SET started_at = DATETIME('now', '-2 days'), retry_count = ? WHERE id = ?",
          (appmod.MAX_JOB_RETRIES, stale))
client.get("/")
check("the no-report notification says it was an uninstall, and installs read as before",
      sorted(m for m, _ in notes()) == ["No report from the agent: Probe on LAB-PC",
                                        "No report from the agent: uninstall of Gone App on LAB-PC"
                                        " — it may already have run"])

# ---------------------------------------------------------------------------
print("5. Retry says why each kind of job was skipped")
# ---------------------------------------------------------------------------
page = client.get("/").get_data(as_text=True)
dialog = re.search(r'<p id="dt-confirm-retry-desc">(.*?)</p>', page, re.S)
check("the retry dialog names both reasons a job is skipped", dialog is not None and dialog.group(1) == (
    "A new pending job is created; the original stays in the history. Installs whose software was removed "
    "from the library are skipped, and so are uninstalls of software no longer on that computer."))
write("DELETE FROM jobs")
write("UPDATE installations SET state = 'removed' WHERE id = ?", (ROW,))
gone = [job("failed", UNINSTALL), job("cancelled", UNINSTALL)]
reply = client.post("/jobs/bulk/retry", data={"job_ids": gone + [job("failed", None)]})
check("bulk retry says an uninstall was skipped because it is no longer installed",
      "2+skipped+(no+longer+installed)" in reply.headers.get("Location", ""))
DELETED = write("INSERT INTO software (name, version, type, install_command) VALUES ('D', '1', 'x', 'x')")
write("DELETE FROM software WHERE id = ?", (DELETED,))
lost = write("INSERT INTO jobs (computer_hostname, software_id, status, software_name) "
             "VALUES ('LAB-PC', ?, 'failed', 'D')", (DELETED,))
reply = client.post("/jobs/bulk/retry", data={"job_ids": [lost, gone[0], job("failed", None)]})
check("...and a mixed reason as not retryable", "2+skipped+(not+retryable)" in reply.headers.get("Location", ""))
reply = client.post("/jobs/bulk/retry", data={"job_ids": [lost, job("failed", None)]})
check("...while removed software alone still says so", "1+skipped+(removed+software)" in reply.headers.get("Location", ""))
write("UPDATE installations SET state = 'installed' WHERE id = ?", (ROW,))

# ---------------------------------------------------------------------------
print("6. The audit action")
# ---------------------------------------------------------------------------
check("uninstall_created is a destructive action", appmod.AUDIT_ACTION_TONES.get("uninstall_created") == "danger")
admin = rows("SELECT id FROM admins WHERE username = 'admin'")[0]["id"]
write("INSERT INTO audit_log (actor_admin_id, action_type, details, target_type, target_label) "
      "VALUES (?, 'uninstall_created', 'Uninstall probe', 'deployment', 'Gone App')", (admin,))
trail = client.get("/admins/audit-trail").get_data(as_text=True)
check("...and the Audit Trail shows it as a red pill",
      re.search(r'<span class="dt-pill" data-tone="bad">Uninstall created</span>', trail) is not None)

# ---------------------------------------------------------------------------
print("7. A removed row's history")
# ---------------------------------------------------------------------------
HISTORY = {"removed": "Removed by AppEase", "removed_folder_left": "Removed; its folder was left behind",
           "already_removed": "It was already gone",
           "removed_restart_needed": "Removed; restart needed to finish", None: "Removed"}
OTHER = write("INSERT INTO computers (hostname, status) VALUES ('LAB-PC2', 'online')")
for index, reason in enumerate(HISTORY):
    write("INSERT INTO installations (computer_id, software_name, method, record_key, state, state_reason, "
          "state_changed_at) VALUES (?, ?, 'msi', ?, 'removed', ?, '2026-10-02 01:00:00')",
          (OTHER, f"App {index}", f"msi:{{{index}}}", reason))
conn = database.get_db()
try:
    history = appmod.installations_by_computer(conn)[str(OTHER)]["history"]
finally:
    conn.close()
by_name = {item["name"]: item["history"] for item in history}
for index, (reason, words) in enumerate(HISTORY.items()):
    check(f"a row removed with {reason} reads {words!r}, then when",
          by_name.get(f"App {index}") == f"{words} · " + appmod.manila_time("2026-10-02 01:00:00"))
for reason in ("removed", "removed_folder_left", "already_removed", "removed_restart_needed"):
    try:
        appmod.clean_install_record({"method": "exe", "unavailable_reason": reason}, SOFTWARE)
        refused = False
    except ValueError:
        refused = True
    check(f"an agent cannot set {reason} on a record itself", refused)

# ---------------------------------------------------------------------------
print()
print(f"{passed} passed, {len(failed)} failed")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

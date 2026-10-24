"""The server's uninstall path: an uninstall is queued only for a row and
computer that can take it, saying why for every row it skips; the poll
hands it only to an agent that reported the method; and only a confirmed
removal changes the installations row.
"""
import os
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile

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


work = tempfile.mkdtemp(prefix="appease_unpath_")
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

if not hasattr(appmod, "uninstall_blocker"):
    print("COULD NOT RUN - app.py predates the uninstall path.")
    sys.exit(3)
appmod.app.config["TESTING"] = True
UNINSTALL = appmod.JOB_ACTION_UNINSTALL
BOTH = "uninstall:choco uninstall:msi"


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


def one(sql, args=()):
    found = rows(sql, args)
    return found[0] if found else None


write("UPDATE admins SET password_hash = ?, must_change_password = 0 WHERE username = 'admin'",
      (generate_password_hash("Str0ng!Passw0rd"),))
client = appmod.app.test_client()
client.post("/login", data={"username": "admin", "password": "Str0ng!Passw0rd"})


def computer(host, caps=BOTH, status="online", seen="-0 seconds", retired=False):
    return write(
        "INSERT INTO computers (hostname, status, last_seen, capabilities, asset_status) "
        "VALUES (?, ?, CASE WHEN ? IS NULL THEN NULL ELSE DATETIME('now', ?) END, ?, ?)",
        (host, status, seen, seen, caps, "Retired" if retired else None))


def software(name, command):
    return write("INSERT INTO software (name, version, type, install_command) VALUES (?, '1', 'x', ?)",
                 (name, command))


KEYS = iter(range(1, 10000))


def inst(pc, method, name, state="installed", reason=None, software_id=None, display=None, package=None,
         product_code=None, hive=None, quiet=None, sid=None):
    n = next(KEYS)
    if method == "msi" and product_code is None:
        product_code = "{%08X-0000-0000-0000-000000000000}" % n
    if method == "choco" and package is None:
        package = f"pkg{n}"
    return write(
        "INSERT INTO installations (computer_id, software_id, software_name, software_version, method, record_key, "
        "choco_package_id, msi_product_code, display_name, display_version, state, state_reason, state_changed_at, "
        "reg_hive, reg_view, reg_key_name, quiet_uninstall_string, reg_user_sid) "
        "VALUES (?, ?, ?, '1.0', ?, ?, ?, ?, ?, '1.0.5', ?, ?, '2026-01-01 00:00:00', ?, ?, ?, ?, ?)",
        (pc, software_id, name, method, f"{method}:{n}", package, product_code, display, state, reason,
         hive, "Registry64" if hive == "HKEY_LOCAL_MACHINE" else None, name if hive else None, quiet, sid))


def uninstall(*ids):
    res = client.post("/uninstall-batch", data={"installation_ids": [str(i) for i in ids]},
                      headers={"X-Requested-With": "XMLHttpRequest"})
    return res.status_code, res.get_json()


def skip_reason(reply, installation_id):
    for item in reply.get("skipped", []):
        if item["installation_id"] == installation_id:
            return item["reason"]
    return None


def jobs_for(installation_id):
    return rows("SELECT * FROM jobs WHERE installation_id = ? ORDER BY id", (installation_id,))


ON = computer("PC-ON")
MSI_ONLY = computer("PC-MSI", caps="uninstall:msi")
NONE = computer("PC-NONE", caps="")
NEVER = computer("PC-NEVER", caps=None)
OFF = computer("PC-OFF", status="offline", seen="-1 days")
STALE = computer("PC-STALE", seen="-5 minutes")
RETIRED = computer("PC-RET", retired=True)
S_7Z = software("7-Zip", 'msiexec /i "uploads/7z.msi" /qn')
S_VLC = software("VLC", "choco install vlc -y")
S_NPP = software("Notepad++", '"uploads/npp.exe" /S')

# ---------------------------------------------------------------------------
print("1. What may be uninstalled, and why not")
# ---------------------------------------------------------------------------
I_7Z = inst(ON, "msi", "7-Zip", software_id=S_7Z, display="7-Zip 24.08 (x64 edition)")
I_VLC = inst(ON, "choco", "VLC", software_id=S_VLC, package="vlc.install")
I_GONE = inst(ON, "msi", "Gone App")                      # library entry deleted: software_id NULL
REFUSED = {
    "unsafe_user_location": inst(ON, "exe", "Odd", hive="HKEY_CURRENT_USER", sid="S-1-5-21-1-1001",
                                 quiet='"C:\\Program Files\\Odd\\uninstall.exe" /S'),
    "unsafe_location": inst(ON, "exe", "Helper", hive="HKEY_LOCAL_MACHINE",
                            quiet='"C:\\ProgramData\\Helper\\uninstall.exe" /S'),
    "unavailable": inst(ON, "exe", "Audacity", state="unavailable", reason="no_silent_uninstall"),
    "protected": inst(ON, "choco", "Python", state="unavailable", reason="protected", package="python314"),
    "not_installed": inst(ON, "msi", "Old Tool", state="removed", reason="removed"),
    "shared_runtime": inst(ON, "exe", "vc_redist", display="Microsoft Visual C++ v14 Redistributable (x64)"),
    "offline": inst(OFF, "msi", "7-Zip", software_id=S_7Z),
    "retired": inst(RETIRED, "msi", "7-Zip", software_id=S_7Z),
    "no_capability": inst(NONE, "msi", "7-Zip", software_id=S_7Z),
}
REPLACED = inst(ON, "msi", "7-Zip old", state="replaced", reason="replaced")
STALE_ROW = inst(STALE, "msi", "7-Zip", software_id=S_7Z)
NEVER_ROW = inst(NEVER, "msi", "7-Zip", software_id=S_7Z)
CHOCO_ON_MSI_ONLY = inst(MSI_ONLY, "choco", "VLC", software_id=S_VLC)
MSI_ON_MSI_ONLY = inst(MSI_ONLY, "msi", "7-Zip", software_id=S_7Z)

status, reply = uninstall(*REFUSED.values(), REPLACED, STALE_ROW, NEVER_ROW, CHOCO_ON_MSI_ONLY, 99999)
check("a request with nothing acceptable is answered, not an error", status == 200 and reply["ok"] is False)
check("...and queues nothing", not rows("SELECT id FROM jobs"))
for expected, installation_id in REFUSED.items():
    check(f"refused as {expected}", skip_reason(reply, installation_id) == expected)
check("a replaced row is not installed", skip_reason(reply, REPLACED) == "not_installed")
check("a PC that stopped checking in reads offline, as on the dashboard", skip_reason(reply, STALE_ROW) == "offline")
check("a PC no agent has reported capabilities from cannot uninstall", skip_reason(reply, NEVER_ROW) == "no_capability")
check("uninstall:msi does not cover a Chocolatey row", skip_reason(reply, CHOCO_ON_MSI_ONLY) == "no_capability")
check("an id that does not exist", skip_reason(reply, 99999) == "not_found")
exe = next(item for item in reply["skipped"] if item["installation_id"] == REFUSED["unsafe_user_location"])
check("a per-user EXE with its uninstaller outside its AppData, posted directly, is refused, saying so",
      exe["reason_text"] == appmod.UNINSTALL_SKIP_REASONS["unsafe_user_location"]
      and exe["computer"] == "PC-ON" and exe["software"] == "Odd 1.0")
check("every skip carries the server's words for its reason",
      all(item["reason_text"] == appmod.UNINSTALL_SKIP_REASONS[item["reason"]] for item in reply["skipped"]))
status, reply = uninstall()
check("no ids at all is a bad request", status == 400 and reply["ok"] is False)
status, reply = uninstall("abc", "-1")
check("ids that are not numbers are ignored", status == 400)

before = len(rows("SELECT id FROM audit_log WHERE action_type = 'uninstall_created'"))
status, reply = uninstall(I_7Z, I_VLC, I_GONE, MSI_ON_MSI_ONLY, REFUSED["unsafe_user_location"], I_7Z)
check("acceptable rows are queued, each once", status == 200 and reply["ok"] is True
      and sorted(item["installation_id"] for item in reply["created"]) == sorted([I_7Z, I_VLC, I_GONE, MSI_ON_MSI_ONLY]))
check("...and the refused one is listed as skipped", [item["installation_id"] for item in reply["skipped"]]
      == [REFUSED["unsafe_user_location"]])
made = {row["installation_id"]: row for row in rows("SELECT * FROM jobs WHERE action IS NOT NULL")}
j = made.get(I_7Z, {})
check("a job is a pending uninstall of its row, on its computer",
      (j.get("status"), j.get("action"), j.get("computer_hostname"), j.get("software_id"))
      == ("pending", UNINSTALL, "PC-ON", S_7Z))
check("...named by the row's snapshot", (j.get("software_name"), j.get("software_version")) == ("7-Zip", "1.0"))
check("a row whose library entry is gone gets software_id 0",
      made.get(I_GONE, {}).get("software_id") == appmod.NO_LIBRARY_SOFTWARE_ID == 0)
check("one batch for the request, which the reply names",
      len({row["batch_id"] for row in made.values()}) == 1 and reply["batch_id"] == j.get("batch_id"))
check("each created entry names its job", all(made[item["installation_id"]]["id"] == item["job_id"]
                                              for item in reply["created"]))
audits = rows("SELECT * FROM audit_log WHERE action_type = 'uninstall_created' ORDER BY id")
check("one uninstall_created audit entry for the request", len(audits) == before + 1)
check("...saying what was queued where",
      audits[-1]["details"] == "Queued uninstall of 3 software items on 2 computer(s) (4 job(s) created)"
      and audits[-1]["target_type"] == "uninstall")

# ---------------------------------------------------------------------------
print("2. One at a time, both ways")
# ---------------------------------------------------------------------------
status, reply = uninstall(I_7Z)
check("a row with an uninstall pending is not queued again", skip_reason(reply, I_7Z) == "uninstall_active")
write("UPDATE jobs SET status = 'in_progress' WHERE installation_id = ?", (I_7Z,))
status, reply = uninstall(I_7Z)
check("...nor while it runs", skip_reason(reply, I_7Z) == "uninstall_active")
write("DELETE FROM jobs")
I_7Z_B = inst(ON, "msi", "7-Zip 64", software_id=S_7Z)
for state in ("pending", "in_progress"):
    write("DELETE FROM jobs")
    write("INSERT INTO jobs (computer_hostname, software_id, status) VALUES ('PC-ON', ?, ?)", (S_7Z, state))
    status, reply = uninstall(I_7Z_B)
    check(f"no uninstall while an install of that software is {state} on that PC",
          skip_reason(reply, I_7Z_B) == "install_active")
write("DELETE FROM jobs")
write("INSERT INTO jobs (computer_hostname, software_id, status) VALUES ('PC-MSI', ?, 'pending')", (S_7Z,))
status, reply = uninstall(I_7Z_B)
check("...an install of it on another PC does not block it", reply["ok"] and not reply["skipped"])
write("DELETE FROM jobs")
write("INSERT INTO jobs (computer_hostname, software_id, status) VALUES ('PC-ON', ?, 'completed')", (S_7Z,))
status, reply = uninstall(I_7Z_B)
check("...nor a finished one", reply["ok"] and not reply["skipped"])

write("DELETE FROM jobs")
uninstall(I_VLC)
res = client.post("/deploy-batch", data={"software_ids": [str(S_VLC), str(S_NPP)], "computer_hostnames": ["PC-ON"]},
                  headers={"X-Requested-With": "XMLHttpRequest"}).get_json()
installs = rows("SELECT software_id FROM jobs WHERE action IS NULL")
check("no install while an uninstall of that software is pending there; others still go",
      [r["software_id"] for r in installs] == [S_NPP] and res.get("uninstalling_count") == 1
      and "Skipped 1 where an uninstall is under way." in res.get("message", ""))
write("DELETE FROM jobs WHERE action IS NULL")
write("UPDATE jobs SET status = 'in_progress' WHERE action = ?", (UNINSTALL,))
reply = client.post("/deploy", data={"software_id": str(S_VLC), "computer_hostnames": ["PC-ON"]})
check("...nor while it runs, from the single deploy form either",
      not rows("SELECT id FROM jobs WHERE action IS NULL")
      and "where+an+uninstall+is+under+way" in reply.headers.get("Location", ""))
reply = client.post("/deploy-all", data={"software_id": str(S_VLC)})
check("...or Deploy to all online",
      not rows("SELECT id FROM jobs WHERE computer_hostname = 'PC-ON' AND action IS NULL")
      and "where+an+uninstall+is+under+way" in reply.headers.get("Location", ""))
write("UPDATE jobs SET status = 'completed' WHERE action = ?", (UNINSTALL,))
reply = client.post("/deploy-batch", data={"software_ids": [str(S_VLC)], "computer_hostnames": ["PC-ON"]},
                    headers={"X-Requested-With": "XMLHttpRequest"}).get_json()
check("once the uninstall has finished, the install goes", reply.get("created_count") == 1)
write("DELETE FROM jobs")

# ---------------------------------------------------------------------------
print("3. Retry passes the same checks")
# ---------------------------------------------------------------------------


def failed_uninstall(installation_id, host):
    row = one("SELECT * FROM installations WHERE id = ?", (installation_id,))
    return write("INSERT INTO jobs (computer_hostname, software_id, status, action, installation_id, software_name) "
                 "VALUES (?, ?, 'failed', ?, ?, ?)",
                 (host, row["software_id"] or 0, UNINSTALL, installation_id, row["software_name"]))


def retry(job_id):
    return client.post(f"/jobs/{job_id}/retry").headers.get("Location", "")


I_OFF = REFUSED["offline"]
I_NOCAP = REFUSED["no_capability"]
location = retry(failed_uninstall(I_OFF, "PC-OFF"))
check("an uninstall on an offline PC is not retried",
      "The+computer+is+offline" in location and not rows("SELECT id FROM jobs WHERE status = 'pending'"))
# Reads online, but has not checked in for 5 minutes: retry must apply the
# dashboard's rule itself, not trust the stored status.
write("UPDATE computers SET status = 'online' WHERE hostname = 'PC-STALE'")
location = retry(failed_uninstall(STALE_ROW, "PC-STALE"))
check("...nor on one that stopped checking in (the dashboard's online rule)",
      "The+computer+is+offline" in location and not rows("SELECT id FROM jobs WHERE status = 'pending'"))
write("UPDATE computers SET status = 'online' WHERE hostname = 'PC-STALE'")
location = retry(failed_uninstall(I_NOCAP, "PC-NONE"))
check("...nor on one whose agent cannot uninstall that method",
      "can't+uninstall+this+kind" in location and not rows("SELECT id FROM jobs WHERE status = 'pending'"))
first = failed_uninstall(I_7Z, "PC-ON")
retry(first)
check("an uninstall that passes is retried", len(rows("SELECT id FROM jobs WHERE status = 'pending'")) == 1)
location = retry(failed_uninstall(I_7Z, "PC-ON"))
check("...but not twice while the first is pending",
      "already+queued" in location and len(rows("SELECT id FROM jobs WHERE status = 'pending'")) == 1)
write("DELETE FROM jobs")
write("INSERT INTO jobs (computer_hostname, software_id, status) VALUES ('PC-ON', ?, 'in_progress')", (S_7Z,))
location = retry(failed_uninstall(I_7Z, "PC-ON"))
check("...nor while an install of that software runs there", "An+install+of+this+software" in location)
write("DELETE FROM jobs")
failed_install = write("INSERT INTO jobs (computer_hostname, software_id, status, software_name) "
                       "VALUES ('PC-ON', ?, 'failed', 'VLC')", (S_VLC,))
uninstall(I_VLC)
location = retry(failed_install)
check("an install is not retried while an uninstall of it is pending there",
      "An+uninstall+of+this+software" in location
      and not rows("SELECT id FROM jobs WHERE status = 'pending' AND action IS NULL"))
bulk = client.post("/jobs/bulk/retry", data={"job_ids": [str(failed_install)]}).headers.get("Location", "")
check("...from bulk retry either", "None+of+the+selected" in bulk)
write("DELETE FROM jobs")
gone = failed_uninstall(I_OFF, "PC-OFF")
bulk = client.post("/jobs/bulk/retry", data={"job_ids": [str(gone)]}).headers.get("Location", "")
check("bulk retry skips an uninstall on an offline PC", "None+of+the+selected" in bulk)
write("DELETE FROM jobs")

# ---------------------------------------------------------------------------
print("4. The poll")
# ---------------------------------------------------------------------------


def poll(host, caps=None):
    body = {"computer_hostname": host}
    if caps is not None:
        body["capabilities"] = caps
    return client.post("/api/agent/poll", json=body).get_json()["job"]


uninstall(I_7Z, I_VLC, I_GONE)
by_row = {row["installation_id"]: row["id"] for row in rows("SELECT * FROM jobs")}
write("UPDATE jobs SET created_at = DATETIME('now', '-3 seconds') WHERE id = ?", (by_row[I_7Z],))
write("UPDATE jobs SET created_at = DATETIME('now', '-2 seconds') WHERE id = ?", (by_row[I_VLC],))
write("UPDATE jobs SET created_at = DATETIME('now', '-1 seconds') WHERE id = ?", (by_row[I_GONE],))
got = poll("PC-ON", ["uninstall:msi", "uninstall:choco"])
pc7 = one("SELECT msi_product_code FROM installations WHERE id = ?", (I_7Z,))["msi_product_code"]
check("an MSI uninstall sends exactly its id, action, method and ProductCode",
      got == {"id": by_row[I_7Z], "action": "uninstall", "method": "msi", "product_code": pc7})
check("...and is now running", one("SELECT status FROM jobs WHERE id = ?", (by_row[I_7Z],))["status"] == "in_progress")
got = poll("PC-ON", ["uninstall:msi", "uninstall:choco"])
check("a Chocolatey uninstall sends exactly its id, action, method and package id",
      got == {"id": by_row[I_VLC], "action": "uninstall", "method": "choco", "package_id": "vlc.install"})
got = poll("PC-ON", ["uninstall:msi", "uninstall:choco"])
check("a software_id 0 uninstall goes out like any other", got is not None and got["id"] == by_row[I_GONE])
write("DELETE FROM jobs")

uninstall(I_7Z, I_VLC)
newer = write("INSERT INTO jobs (computer_hostname, software_id, status, software_name, created_at) "
              "VALUES ('PC-ON', ?, 'pending', 'Notepad++', DATETIME('now', '+1 seconds'))", (S_NPP,))
older = write("INSERT INTO jobs (computer_hostname, software_id, status, software_name, created_at) "
              "VALUES ('PC-ON', ?, 'pending', 'Notepad++', DATETIME('now', '-1 minutes'))", (S_NPP,))
got = poll("PC-ON", ["uninstall:msi"])
check("one queue, oldest first: an older install goes before the uninstalls",
      got is not None and got["id"] == older and "install_command" in got)
got = poll("PC-ON", ["uninstall:msi"])
check("then the MSI uninstall", got is not None and got.get("method") == "msi")
got = poll("PC-ON", ["uninstall:msi"])
vlc_job = jobs_for(I_VLC)[0]
check("a Chocolatey uninstall this agent cannot run is cancelled with agent_cannot_uninstall",
      (vlc_job["status"], vlc_job["result_reason"]) == ("cancelled", "agent_cannot_uninstall"))
check("...and the poll moves on to the next job", got is not None and got["id"] == newer)
write("DELETE FROM jobs")
write("UPDATE computers SET capabilities = ? WHERE hostname = 'PC-ON'", (BOTH,))   # the polls above narrowed it
uninstall(I_VLC)
uninstall(I_7Z)
write("UPDATE jobs SET created_at = DATETIME('now', '-1 minutes') WHERE installation_id = ?", (I_VLC,))
got = poll("PC-ON", ["uninstall:msi"])
check("...and when the next job is another uninstall, that one goes out",
      jobs_for(I_VLC)[0]["result_reason"] == "agent_cannot_uninstall"
      and got is not None and got["id"] == jobs_for(I_7Z)[0]["id"])
write("DELETE FROM jobs")
uninstall(I_7Z)
got = poll("PC-ON")
check("an old agent, sending no capabilities, is never handed an uninstall", got is None
      and jobs_for(I_7Z)[0]["result_reason"] == "agent_cannot_uninstall")
write("UPDATE computers SET capabilities = ? WHERE hostname = 'PC-ON'", (BOTH,))
write("DELETE FROM jobs")

# The row changes between queueing and pickup. 7-Zip 24.08's uninstall is
# queued while 7-Zip 26.03, from a DIFFERENT library entry, is installing:
# the reverse guard matches by library entry, so it lets both through, and
# the upgrade's report marks the 24.08 row replaced. The uninstall must not
# go out, and must say why.
write("DELETE FROM jobs")
S_7Z_NEW = software("7-Zip 26.03", 'msiexec /i "uploads/7z2603.msi" /qn')
OLD_CODE, NEW_CODE = "{24080000-0000-0000-0000-000000000000}", "{26030000-0000-0000-0000-000000000000}"
I_OLD = write("INSERT INTO installations (computer_id, software_id, software_name, software_version, method, "
              "record_key, msi_product_code, state) VALUES (?, ?, '7-Zip', '24.08', 'msi', ?, ?, 'installed')",
              (ON, S_7Z, database.installation_record_key("msi", product_code=OLD_CODE), OLD_CODE))
upgrade = write("INSERT INTO jobs (computer_hostname, software_id, status, software_name, started_at) "
                "VALUES ('PC-ON', ?, 'in_progress', '7-Zip 26.03', CURRENT_TIMESTAMP)", (S_7Z_NEW,))
status, reply = uninstall(I_OLD)
check("the uninstall of 24.08 is queued while 26.03 installs", reply["ok"] and not reply["skipped"])
report = client.post(f"/api/agent/jobs/{upgrade}/report", json={
    "computer_hostname": "PC-ON", "status": "completed", "exit_code": 0, "install_record": {
        "method": "msi", "product_code": NEW_CODE, "display_name": "7-Zip 26.03 (x64)", "display_version": "26.03",
        "replaced_entries": [{"product_code": OLD_CODE}]}}).get_json()
check("...and the upgrade marks the 24.08 row replaced",
      report.get("install_record") == "stored"
      and one("SELECT state FROM installations WHERE id = ?", (I_OLD,))["state"] == "replaced")
got = poll("PC-ON", ["uninstall:msi", "uninstall:choco"])
stale_job = jobs_for(I_OLD)[0]
check("the uninstall is not handed out", got is None)
check("...it is cancelled with no_longer_installed", (stale_job["status"], stale_job["result_reason"])
      == ("cancelled", "no_longer_installed"))
check("...which reads as agreed in Job Details",
      appmod.job_result("cancelled", None, "no_longer_installed", UNINSTALL)["label"]
      == "It was no longer installed when the agent asked for it")
write("UPDATE installations SET state = 'installed', msi_product_code = NULL WHERE id = ?", (I_OLD,))
uninstall_job = write("INSERT INTO jobs (computer_hostname, software_id, status, action, installation_id, software_name) "
                      "VALUES ('PC-ON', ?, 'pending', ?, ?, '7-Zip')", (S_7Z, UNINSTALL, I_OLD))
poll("PC-ON", ["uninstall:msi", "uninstall:choco"])
check("a row with nothing left to identify it by is cancelled with a reason too",
      one("SELECT result_reason FROM jobs WHERE id = ?", (uninstall_job,))["result_reason"] == "no_longer_installed")
check("no uninstall is ever cancelled without a reason",
      not rows("SELECT id FROM jobs WHERE action = ? AND status = 'cancelled' AND result_reason IS NULL", (UNINSTALL,)))
write("DELETE FROM jobs")
write("UPDATE computers SET capabilities = ? WHERE hostname = 'PC-ON'", (BOTH,))

# ---------------------------------------------------------------------------
print("5. Pickup expiry and the stale window")
# ---------------------------------------------------------------------------


def pending_uninstall(host, installation_id, age="-0 minutes"):
    return write("INSERT INTO jobs (computer_hostname, software_id, status, action, installation_id, software_name, "
                 "created_at) VALUES (?, 0, 'pending', ?, ?, 'X', DATETIME('now', ?))",
                 (host, UNINSTALL, installation_id, age))


def status_of(job_id):
    row = one("SELECT status, result_reason, retry_count FROM jobs WHERE id = ?", (job_id,))
    return row["status"], row["result_reason"]


computer("PC-QUIET-3", seen="-3 minutes")
computer("PC-QUIET-1", seen="-1 minutes")
quiet3 = pending_uninstall("PC-QUIET-3", I_7Z)
quiet1 = pending_uninstall("PC-QUIET-1", I_7Z)
unseen = pending_uninstall("PC-NEVER", I_7Z)
old46 = pending_uninstall("PC-ON", I_7Z, "-46 minutes")
old44 = pending_uninstall("PC-ON", I_7Z, "-44 minutes")
install_on_quiet = write("INSERT INTO jobs (computer_hostname, software_id, status) VALUES ('PC-QUIET-3', ?, 'pending')",
                         (S_7Z,))
write("UPDATE computers SET last_seen = NULL WHERE hostname = 'PC-NEVER'")
write("UPDATE computers SET last_seen = CURRENT_TIMESTAMP WHERE hostname = 'PC-ON'")
client.get("/")
check("a PC silent for 3 minutes: its pending uninstall is cancelled, pickup_expired",
      status_of(quiet3) == ("cancelled", "pickup_expired"))
check("...for 1 minute: still pending", status_of(quiet1) == ("pending", None))
check("a PC never heard from: cancelled", status_of(unseen) == ("cancelled", "pickup_expired"))
check("pending 46 minutes on a PC that is online: cancelled", status_of(old46) == ("cancelled", "pickup_expired"))
check("...44 minutes: still pending", status_of(old44) == ("pending", None))
check("an install on a silent PC is left waiting, as before", status_of(install_on_quiet) == ("pending", None))
write("DELETE FROM jobs")


def running(action, minutes, retries=0, host="PC-ON"):
    return write("INSERT INTO jobs (computer_hostname, software_id, status, action, installation_id, software_name, "
                 "started_at, retry_count) VALUES (?, ?, 'in_progress', ?, ?, 'Gone App', DATETIME('now', ?), ?)",
                 (host, S_7Z if action is None else 0, action, I_7Z if action else None, f"-{minutes} minutes",
                  retries))


u21 = running(UNINSTALL, 21)
u19 = running(UNINSTALL, 19)
i21 = running(None, 21)
write("DELETE FROM notifications")
cap = running(UNINSTALL, 21, appmod.MAX_JOB_RETRIES)
client.get("/")
check("an uninstall running 21 minutes with no report is re-queued", status_of(u21) == ("pending", None)
      and one("SELECT retry_count FROM jobs WHERE id = ?", (u21,))["retry_count"] == 1)
check("...19 minutes: left running", status_of(u19) == ("in_progress", None))
check("an install running 21 minutes is left running (its window is 45)", status_of(i21) == ("in_progress", None))
check("at the retry cap the uninstall fails with no_agent_report", status_of(cap) == ("failed", "no_agent_report"))
check("...and the notification says it may already have run",
      [r["message"] for r in rows("SELECT message FROM notifications WHERE event_type = 'job_failed'")]
      == ["No report from the agent: uninstall of Gone App on PC-ON — it may already have run"])
write("DELETE FROM jobs")

# ---------------------------------------------------------------------------
print("6. What a report does to the installations row")
# ---------------------------------------------------------------------------


def reported(status, reason=None, exit_code=None, record=None, state="installed"):
    row = inst(ON, "msi", "Probe", software_id=S_7Z)
    write("UPDATE installations SET state = ? WHERE id = ?", (state, row))
    job_id = write("INSERT INTO jobs (computer_hostname, software_id, status, action, installation_id, software_name, "
                   "started_at) VALUES ('PC-ON', ?, 'in_progress', ?, ?, 'Probe', CURRENT_TIMESTAMP)",
                   (S_7Z, UNINSTALL, row))
    body = {"computer_hostname": "PC-ON", "status": status, "reason": reason}
    if exit_code is not None:
        body["exit_code"] = exit_code
    if record is not None:
        body["install_record"] = record
    res = client.post(f"/api/agent/jobs/{job_id}/report", json=body)
    after = one("SELECT * FROM installations WHERE id = ?", (row,))
    return res, job_id, after


for reason, exit_code, expected in ((None, 0, "removed"), ("removed_folder_left", 0, "removed_folder_left"),
                                    ("removed_restart_needed", 3010, "removed_restart_needed"),
                                    ("already_removed", None, "already_removed"),
                                    ("already_removed", 1605, "already_removed")):
    res, job_id, after = reported("completed", reason, exit_code)
    check(f"completed with {reason or 'no reason'} (exit {exit_code}): the row is removed, {expected}",
          res.status_code == 200 and (after["state"], after["state_reason"], after["uninstall_job_id"])
          == ("removed", expected, job_id))
    check(f"...and its state_changed_at moved ({reason or 'no reason'})",
          after["state_changed_at"] != "2026-01-01 00:00:00")
for status, reason, exit_code in (("completed", "removal_unverified", 0), ("failed", "still_listed", 0),
                                  ("completed", "removed_restart_needed", 0),
                                  ("failed", "msi_fatal", 1603), ("failed", None, 1),
                                  ("completed", "restart_needed", 3010), ("failed", "timed_out", None),
                                  ("completed", "still_listed", 0)):
    res, job_id, after = reported(status, reason, exit_code)
    check(f"{status} with {reason} (exit {exit_code}): the row is unchanged",
          (after["state"], after["state_reason"], after["uninstall_job_id"], after["state_changed_at"])
          == ("installed", None, None, "2026-01-01 00:00:00"))
res, job_id, after = reported("completed", None, 0, state="replaced")
check("a confirmed removal does not touch a row that is no longer installed", after["state"] == "replaced")
count = len(rows("SELECT id FROM installations"))
res, job_id, after = reported("completed", None, 0, record={
    "method": "msi", "product_code": "{AAAAAAAA-0000-0000-0000-000000000000}", "display_name": "7-Zip"})
check("an install record sent with an uninstall report is ignored",
      res.get_json().get("install_record") == "ignored"
      and len(rows("SELECT id FROM installations")) == count + 1)     # + the row reported() made

# ---------------------------------------------------------------------------
print("7. Shared runtimes")
# ---------------------------------------------------------------------------
NAMES = {
    "vc_redist": True, "VC_redist.x64": True, "vcredist_x86": True,
    "Microsoft Visual C++ 2015-2022 Redistributable (x64) - 14.44.35211": True,
    "Microsoft Visual C++ v14 Redistributable (x64)": True,
    "Microsoft .NET Runtime - 8.0.11 (x64)": True, "Microsoft .NET Core Runtime - 3.1.32 (x64)": True,
    "Microsoft Windows Desktop Runtime - 8.0.11 (x64)": True, "Microsoft ASP.NET Core 8.0.11 - Shared Framework": True,
    "Microsoft .NET Framework 4.8.1": True, "Microsoft Edge WebView2 Runtime": True,
    "DirectX End-User Runtime": True,
    "7-Zip 24.08 (x64)": False, "VLC media player": False, "Notepad++ (64-bit x64)": False,
    "Python 3.14.2 (64-bit)": False, "Visual Studio Code": False, "Microsoft Edge": False,
}
for name, expected in NAMES.items():
    check(f"{name!r} is {'a shared runtime' if expected else 'not a shared runtime'}",
          appmod.is_shared_runtime({"software_name": name, "display_name": None}) is expected
          and appmod.is_shared_runtime({"software_name": "Library name", "display_name": name}) is expected)
RUNTIME_MSI = inst(ON, "msi", "Runtime pack", display="Microsoft Edge WebView2 Runtime")
status, reply = uninstall(RUNTIME_MSI)
check("an MSI whose DisplayName is a runtime is refused too", skip_reason(reply, RUNTIME_MSI) == "shared_runtime")
VC = REFUSED["shared_runtime"]
OLD_VC = inst(ON, "exe", "vc_redist 2019", state="removed", reason="removed")
conn = database.get_db()
try:
    drawer = appmod.installations_by_computer(conn)[str(ON)]
finally:
    conn.close()
live = {item["name"]: item for item in drawer["live"]}
vc = live.get("vc_redist", {})
check("the drawer shows the vc_redist row as Protected, with the agreed reason",
      (vc.get("pill"), vc.get("tone"), vc.get("reason"))
      == ("Protected", "info", "Shared runtime - other software on this PC may need it."))
check("...and the DisplayName-only one too", live.get("Runtime pack", {}).get("pill") == "Protected")
check("...in the Protected group, after Installed", [i["pill"] for i in drawer["live"]][-1] == "Protected"
      and drawer["live"].index(vc) > max(i for i, item in enumerate(drawer["live"]) if item["pill"] == "Installed"))
check("an ordinary row is still Installed", live.get("7-Zip", {}).get("pill") == "Installed")
old_vc = [item for item in drawer["history"] if item["name"] == "vc_redist 2019"]
check("a removed runtime stays in history, not Protected",
      len(old_vc) == 1 and "pill" not in old_vc[0] and old_vc[0]["history"].startswith("Removed by AppEase")
      and "vc_redist 2019" not in live)

# ---------------------------------------------------------------------------
print()
print(f"{passed} passed, {len(failed)} failed")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

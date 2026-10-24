"""Every admin change writes exactly ONE audit_log row per request, bulk
included, naming who and what changed but never a password, a note or a
report's text. A no-op writes nothing; destructive actions show red.
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


work = tempfile.mkdtemp(prefix="appease_audit_")
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

appmod.app.config["TESTING"] = True
PASSWORD = "Str0ng!Passw0rd"
NEW_PASSWORD = "N3w!Passw0rd-xyz"
SECRET_TEXT = "the projector smells of smoke " + secrets.token_hex(4)


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


def last_audit_id():
    found = rows("SELECT MAX(id) AS id FROM audit_log")
    return found[0]["id"] or 0


def audited(do):
    """Run do(); return the audit rows it wrote."""
    before = last_audit_id()
    response = do()
    return rows("SELECT * FROM audit_log WHERE id > ? ORDER BY id", (before,)), response


write("UPDATE admins SET password_hash = ?, must_change_password = 0 WHERE username = 'admin'",
      (generate_password_hash(PASSWORD),))
ADMIN = rows("SELECT id FROM admins WHERE username = 'admin'")[0]["id"]
FACULTY = write("INSERT INTO admins (username, password_hash, role, must_change_password, first_name, last_name) "
                "VALUES ('faculty1', ?, 'faculty', 0, 'Juan', 'Dela Cruz')", (generate_password_hash(PASSWORD),))


def signed_in(username):
    client = appmod.app.test_client()
    client.post("/login", data={"username": username, "password": PASSWORD})
    return client


admin = signed_in("admin")
faculty = signed_in("faculty1")


def report(host="LAB1-PC01", kind="hardware", status="open"):
    return write("INSERT INTO problem_reports (computer_hostname, problem_type, description, status) "
                 "VALUES (?, ?, ?, ?)", (host, kind, SECRET_TEXT, status))


def archived(host="LAB1-PC01", kind="hardware"):
    """A report as the delete routes require: finished and archived."""
    report_id = report(host, kind, "resolved")
    write("UPDATE problem_reports SET archived_at = '2026-10-06 00:00:00', archived_by = ? WHERE id = ?",
          (ADMIN, report_id))
    return report_id


def job(status="completed", host="LAB1-PC01"):
    return write("INSERT INTO jobs (computer_hostname, software_id, status, software_name, software_version) "
                 "VALUES (?, 0, ?, '7-Zip', '24.08')", (host, status))


def one_row(label, found, action, actor, target_type=None, target_label=None, target_admin=None):
    check(f"{label}: exactly one audit row ({[r['action_type'] for r in found]})", len(found) == 1)
    if len(found) != 1:
        return {}
    row = found[0]
    check(f"{label}: action {action}", row["action_type"] == action)
    check(f"{label}: done by the account that did it", row["actor_admin_id"] == actor)
    if target_type is not None:
        check(f"{label}: target {target_type}: {target_label}",
              (row["target_type"], row["target_label"]) == (target_type, target_label))
    if target_admin is not None:
        check(f"{label}: target is the account", row["target_admin_id"] == target_admin)
    check(f"{label}: no report text, note or password in it",
          not any(secret in " ".join(str(v) for v in row.values())
                  for secret in (SECRET_TEXT, PASSWORD, NEW_PASSWORD)))
    return row


# ---------------------------------------------------------------------------
print("1. Deleting problem reports")
# ---------------------------------------------------------------------------
R = archived("LAB1-PC07", "hardware")
found, response = audited(lambda: admin.post(f"/problem-reports/{R}/delete"))
row = one_row("one report deleted", found, "report_deleted", ADMIN, "report", f"#{R} (LAB1-PC07)")
check("...the report is gone", not rows("SELECT id FROM problem_reports WHERE id = ?", (R,)))
check("...the details name the report, its type and its computer",
      row.get("details") == f"Deleted report #{R} (hardware) from LAB1-PC07")
found, _ = audited(lambda: admin.post("/problem-reports/999999/delete"))
check("a report that does not exist: nothing deleted, nothing audited", found == [])

BULK = [archived(), archived("LAB1-PC02"), archived("LAB1-PC03")]
found, _ = audited(lambda: admin.post("/problem-reports/bulk-delete",
                                      data={"report_ids": [str(i) for i in BULK]}))
row = one_row("three reports deleted at once", found, "report_deleted", ADMIN, "report", "3 reports")
check("...one row for the request, naming every report",
      row.get("details") == "Deleted 3 reports: " + ", ".join(f"#{i}" for i in sorted(BULK)))
found, _ = audited(lambda: admin.post("/problem-reports/bulk-delete", data={"report_ids": ["999998"]}))
check("bulk delete of reports that do not exist: nothing audited", found == [])
one = archived()
found, _ = audited(lambda: admin.post("/problem-reports/bulk-delete", data={"report_ids": [str(one)]}))
one_row("a bulk delete of one report", found, "report_deleted", ADMIN, "report", "1 report")

# ---------------------------------------------------------------------------
print("2. Deleting finished jobs")
# ---------------------------------------------------------------------------
DONE = [job("completed"), job("failed")]
LIVE = job("pending")
found, _ = audited(lambda: admin.post("/jobs/bulk/delete", data={"job_ids": [str(i) for i in DONE + [LIVE]]}))
row = one_row("two finished jobs deleted (a pending one skipped)", found, "jobs_deleted", ADMIN, "job", "2 jobs")
check("...the details count only what was deleted",
      row.get("details") == "Deleted 2 finished jobs from the job history")
check("...the pending job is still there", rows("SELECT id FROM jobs WHERE id = ?", (LIVE,)) != [])
found, _ = audited(lambda: admin.post("/jobs/bulk/delete", data={"job_ids": [str(LIVE)]}))
check("only a pending job selected: nothing deleted, nothing audited", found == [])

# ---------------------------------------------------------------------------
print("3. Changing your own password")
# ---------------------------------------------------------------------------


def change_password(client, current):
    return lambda: client.post("/settings", data={"form_type": "change_password", "current_password": current,
                                                  "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD})


found, _ = audited(change_password(admin, PASSWORD))
one_row("an admin changes their own password", found, "password_changed", ADMIN, target_admin=ADMIN)
found, _ = audited(change_password(faculty, PASSWORD))
one_row("faculty change their own password - logged whatever the role", found, "password_changed", FACULTY,
        target_admin=FACULTY)
found, _ = audited(change_password(faculty, "Wr0ng!Password"))
check("the wrong current password: nothing changed, nothing audited", found == [])

# ---------------------------------------------------------------------------
print("4. Colours, the page, and the index")
# ---------------------------------------------------------------------------
for action in ("report_deleted", "jobs_deleted"):
    check(f"{action} shows red", appmod.audit_tone(action) == "bad")
check("password_changed shows amber, like password_reset", appmod.audit_tone("password_changed") == "warn")
admin.post("/login", data={"username": "admin", "password": NEW_PASSWORD})
page = admin.get("/admins/audit-trail").get_data(as_text=True)
check("the Audit Trail's subtitle names what it now covers",
      "Accounts, software, deployments, uninstalls, problem reports, jobs, computers and equipment." in page)
check("...not the old one", "Account, software and deployment changes." not in page)
check("a deleted report's pill is red on the Audit Trail",
      re.search(r'data-tone="bad">Report deleted<', page) is not None)
check("deleted jobs' pill is red too", re.search(r'data-tone="bad">Jobs deleted<', page) is not None)
indexes = {r["name"]: r["sql"] for r in rows("SELECT name, sql FROM sqlite_master WHERE type = 'index' "
                                             "AND tbl_name = 'audit_log'")}
check(f"audit_log has an index on created_at ({sorted(indexes)})",
      any(re.search(r"\(\s*created_at\s*\)", sql or "") for sql in indexes.values()))
database.init_db()
check("init_db() runs again over it without trouble",
      len(rows("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'audit_log'")) == len(indexes))

# ---------------------------------------------------------------------------
print("5. Bulk entries name at most ten ids")
# ---------------------------------------------------------------------------
check("150 ids: the first ten, then how many more",
      appmod.audit_id_list(range(150, 0, -1)) == ", ".join(f"#{i}" for i in range(1, 11)) + " and 140 more")
check("exactly ten: all of them, nothing more", appmod.audit_id_list(range(1, 11)).endswith("#10")
      and "more" not in appmod.audit_id_list(range(1, 11)))
check("eleven: ten and 1 more", appmod.audit_id_list(range(1, 12)).endswith("#10 and 1 more"))
MANY = [archived() for _ in range(15)]
found, _ = audited(lambda: admin.post("/problem-reports/bulk-delete", data={"report_ids": [str(i) for i in MANY]}))
row = one_row("fifteen reports deleted at once", found, "report_deleted", ADMIN, "report", "15 reports")
check(f"...the details name ten and count five ({row.get('details')})",
      row.get("details") == "Deleted 15 reports: " + ", ".join(f"#{i}" for i in sorted(MANY)[:10]) + " and 5 more")

# ---------------------------------------------------------------------------
print("6. Problem reports: status, priority, assignment, the drawer, notes")
# ---------------------------------------------------------------------------
TECH = write("INSERT INTO admins (username, password_hash, role, must_change_password, first_name, last_name) "
             "VALUES ('tech2', ?, 'admin', 0, 'Maria', 'Santos')", (generate_password_hash(PASSWORD),))
R = report("LAB1-PC09")
LABEL = f"#{R} (LAB1-PC09)"
found, _ = audited(lambda: admin.post(f"/problem-reports/{R}/status", data={"status": "resolved"}))
row = one_row("status changed", found, "report_status_changed", ADMIN, "report", LABEL)
check(f"...old -> new ({row.get('details')})", row.get("details") == "Status: Open -> Resolved")
found, _ = audited(lambda: admin.post(f"/problem-reports/{R}/status", data={"status": "resolved"}))
check("the same status again: nothing changed, nothing audited", found == [])
found, _ = audited(lambda: admin.post(f"/problem-reports/{R}/status", data={"status": "nonsense"}))
check("an invalid status: nothing audited", found == [])

found, _ = audited(lambda: admin.post(f"/problem-reports/{R}/priority", data={"priority": "high"}))
row = one_row("priority changed", found, "report_priority_changed", ADMIN, "report", LABEL)
check(f"...old -> new ({row.get('details')})", row.get("details") == "Priority: Medium -> High")
found, _ = audited(lambda: admin.post(f"/problem-reports/{R}/priority", data={"priority": "high"}))
check("the same priority again: nothing audited", found == [])

found, _ = audited(lambda: admin.post(f"/problem-reports/{R}/assign", data={"assigned_to": str(TECH)}))
row = one_row("assigned", found, "report_assigned", ADMIN, "report", LABEL)
check(f"...named by full name ({row.get('details')})", row.get("details") == "Assigned to: Unassigned -> Maria Santos")
found, _ = audited(lambda: admin.post(f"/problem-reports/{R}/assign", data={"assigned_to": ""}))
row = one_row("unassigned", found, "report_assigned", ADMIN, "report", LABEL)
check(f"...back to nobody ({row.get('details')})", row.get("details") == "Assigned to: Maria Santos -> Unassigned")

found, _ = audited(lambda: admin.post(f"/problem-reports/{R}/edit", data={
    "status": "in_progress", "priority": "low", "assigned_to": str(TECH)}))
row = one_row("the edit drawer changes three things", found, "report_updated", ADMIN, "report", LABEL)
check(f"...one entry naming all three ({row.get('details')})",
      row.get("details") == "Status: Resolved -> In progress; Priority: High -> Low; Assigned to: Unassigned -> Maria Santos")
found, _ = audited(lambda: admin.post(f"/problem-reports/{R}/edit", data={
    "status": "in_progress", "priority": "low", "assigned_to": str(TECH)}))
check("the drawer saved with nothing changed: nothing audited", found == [])

found, _ = audited(lambda: admin.post(f"/problem-reports/{R}/notes", data={"notes": SECRET_TEXT,
                                                                           "resolution_notes": ""}))
row = one_row("notes edited", found, "report_notes_edited", ADMIN, "report", LABEL)
check(f"...says that, never what ({row.get('details')})", row.get("details") == "Edited notes")
found, _ = audited(lambda: admin.post(f"/problem-reports/{R}/notes", data={"notes": SECRET_TEXT,
                                                                           "resolution_notes": SECRET_TEXT}))
row = one_row("resolution notes edited", found, "report_notes_edited", ADMIN, "report", LABEL)
check(f"...only the one that changed ({row.get('details')})", row.get("details") == "Edited resolution notes")
found, _ = audited(lambda: admin.post(f"/problem-reports/{R}/notes", data={"notes": SECRET_TEXT,
                                                                           "resolution_notes": SECRET_TEXT}))
check("notes saved unchanged: nothing audited", found == [])

# The dashboard's Tasks card: reports assigned to whoever is signed in.
MINE = [report(f"LAB2-PC0{i}") for i in range(1, 4)]
for i in MINE:
    write("UPDATE problem_reports SET assigned_to = ? WHERE id = ?", (ADMIN, i))
ids = [str(i) for i in MINE]
found, _ = audited(lambda: admin.post("/problem-reports/bulk/status", data={"report_ids": ids, "status": "resolved"}))
row = one_row("/bulk/status (dashboard)", found, "report_status_changed", ADMIN, "report", "3 reports")
check(f"...count and ids ({row.get('details')})",
      row.get("details") == "Status set to Resolved on 3 reports: " + appmod.audit_id_list(MINE))
found, _ = audited(lambda: admin.post("/problem-reports/bulk/priority", data={"report_ids": ids, "priority": "high"}))
row = one_row("/bulk/priority (dashboard)", found, "report_priority_changed", ADMIN, "report", "3 reports")
check(f"...count and ids ({row.get('details')})",
      row.get("details") == "Priority set to High on 3 reports: " + appmod.audit_id_list(MINE))

# The Problem Reports page's bar: every report.
PAGE = [report(f"LAB3-PC0{i}") for i in range(1, 5)]
ids = [str(i) for i in PAGE]
found, _ = audited(lambda: admin.post("/problem-reports/bulk-status", data={"report_ids": ids, "status": "resolved"}))
row = one_row("/bulk-status (Problem Reports page)", found, "report_status_changed", ADMIN, "report", "4 reports")
check(f"...count and ids ({row.get('details')})",
      row.get("details") == "Status set to Resolved on 4 reports: " + appmod.audit_id_list(PAGE))
found, _ = audited(lambda: admin.post("/problem-reports/bulk-status", data={"report_ids": ids, "status": "resolved"}))
check("/bulk-status with nothing to change: nothing audited", found == [])
found, _ = audited(lambda: admin.post("/problem-reports/bulk-priority",
                                      data={"report_ids": ids + ["999997"], "priority": "low"}))
row = one_row("/bulk-priority (Problem Reports page)", found, "report_priority_changed", ADMIN, "report", "4 reports")
check(f"...names only reports that exist ({row.get('details')})",
      row.get("details") == "Priority set to Low on 4 reports: " + appmod.audit_id_list(PAGE))
found, _ = audited(lambda: admin.post("/problem-reports/bulk-assign", data={"report_ids": ids,
                                                                            "assigned_to": str(TECH)}))
row = one_row("/bulk-assign", found, "report_assigned", ADMIN, "report", "4 reports")
check(f"...to whom ({row.get('details')})",
      row.get("details") == "Assigned to Maria Santos on 4 reports: " + appmod.audit_id_list(PAGE))
found, _ = audited(lambda: admin.post("/problem-reports/bulk-assign", data={"report_ids": ids,
                                                                            "assigned_to": "__none__"}))
row = one_row("/bulk-assign to nobody", found, "report_assigned", ADMIN, "report", "4 reports")
check(f"...unassigned ({row.get('details')})", row.get("details") == "Unassigned 4 reports: " + appmod.audit_id_list(PAGE))

# ---------------------------------------------------------------------------
print("7. Jobs: cancel and retry")
# ---------------------------------------------------------------------------
SW = write("INSERT INTO software (name, version, type, install_command) VALUES ('7-Zip', '24.08', 'offline', 'x')")


def library_job(status, host="LAB1-PC01"):
    return write("INSERT INTO jobs (computer_hostname, software_id, status, software_name, software_version) "
                 "VALUES (?, ?, ?, '7-Zip', '24.08')", (host, SW, status))


J = library_job("pending")
found, _ = audited(lambda: admin.post(f"/jobs/{J}/cancel"))
row = one_row("one job cancelled", found, "job_cancelled", ADMIN, "job", f"#{J} (LAB1-PC01)")
check(f"...what and where ({row.get('details')})", row.get("details") == "Cancelled the install of 7-Zip 24.08 on LAB1-PC01")
found, _ = audited(lambda: admin.post(f"/jobs/{J}/cancel"))
check("cancelling it again: refused, nothing audited", found == [])
found, _ = audited(lambda: admin.post(f"/jobs/{J}/retry"))
row = one_row("one job retried", found, "job_retried", ADMIN, "job", f"#{J} (LAB1-PC01)")
check(f"...what and where ({row.get('details')})", row.get("details") == "Retried the install of 7-Zip 24.08 on LAB1-PC01")
check("...and it really was re-queued", rows("SELECT id FROM jobs WHERE status = 'pending' AND software_id = ?", (SW,)))
write("DELETE FROM jobs WHERE status = 'pending'")
found, _ = audited(lambda: admin.post(f"/jobs/{library_job('completed')}/retry"))
check("retrying a completed job: refused, nothing audited", found == [])

PENDING = [library_job("pending", f"LAB4-PC0{i}") for i in range(1, 4)]
found, _ = audited(lambda: admin.post("/jobs/bulk/cancel", data={"job_ids": [str(i) for i in PENDING]
                                                                 + [str(library_job("completed"))]}))
row = one_row("bulk cancel (one completed job skipped)", found, "job_cancelled", ADMIN, "job", "3 jobs")
check(f"...count and ids ({row.get('details')})",
      row.get("details") == "Cancelled 3 pending jobs: " + appmod.audit_id_list(PENDING))
found, _ = audited(lambda: admin.post("/jobs/bulk/retry", data={"job_ids": [str(i) for i in PENDING]}))
row = one_row("bulk retry", found, "job_retried", ADMIN, "job", "3 jobs")
check(f"...count and ids ({row.get('details')})", row.get("details") == "Retried 3 jobs: " + appmod.audit_id_list(PENDING))
found, _ = audited(lambda: admin.post("/jobs/bulk/cancel", data={"job_ids": [str(library_job("completed"))]}))
check("bulk cancel with nothing pending: nothing audited", found == [])

# ---------------------------------------------------------------------------
print("8. Computers")
# ---------------------------------------------------------------------------
PC = write("INSERT INTO computers (hostname, status, location, device_type) VALUES ('LAB5-PC01', 'online', 'Lab 1', 'Desktop')")
found, _ = audited(lambda: admin.post(f"/computers/{PC}/location", data={"location": "Lab 2"}))
row = one_row("location changed", found, "computer_updated", ADMIN, "computer", "LAB5-PC01")
check(f"...old -> new ({row.get('details')})", row.get("details") == "Location: Lab 1 -> Lab 2")
found, _ = audited(lambda: admin.post(f"/computers/{PC}/location", data={"location": "Lab 2"}))
check("the same location again: nothing audited", found == [])
found, _ = audited(lambda: admin.post(f"/computers/{PC}/device-type", data={"device_type": "Laptop"}))
row = one_row("device type changed", found, "computer_updated", ADMIN, "computer", "LAB5-PC01")
check(f"...old -> new ({row.get('details')})", row.get("details") == "Device type: Desktop -> Laptop")


def edit_pc(**fields):
    data = {"location": "Lab 2", "device_type": "Laptop", "asset_status": "In use", "purchase_date": "",
            "vendor": "", "warranty_until": ""}
    data.update(fields)
    return lambda: admin.post(f"/computers/{PC}/edit", data=data)


found, _ = audited(edit_pc(location="Lab 3", vendor="Acme"))
row = one_row("the computer drawer", found, "computer_updated", ADMIN, "computer", "LAB5-PC01")
check(f"...one entry naming what moved ({row.get('details')})",
      row.get("details") == "Location: Lab 2 -> Lab 3; Purchase date, vendor or warranty updated")
found, _ = audited(edit_pc(location="Lab 3", vendor="Acme", asset_status="Retired"))
row = one_row("retired from the drawer", found, "computer_retired", ADMIN, "computer", "LAB5-PC01")
check(f"...says so ({row.get('details')})", row.get("details") == "Status: In use -> Retired")
found, _ = audited(edit_pc(location="Lab 3", vendor="Acme", asset_status="Retired"))
check("the drawer saved unchanged: nothing audited", found == [])
found, _ = audited(edit_pc(location="Lab 3", vendor="Acme", asset_status="In use"))
row = one_row("brought back into use", found, "computer_updated", ADMIN, "computer", "LAB5-PC01")
check(f"...an ordinary update ({row.get('details')})", row.get("details") == "Status: Retired -> In use")
FRESH = write("INSERT INTO computers (hostname, status, location, device_type) "
              "VALUES ('LAB5-PC02', 'online', 'Lab 1', 'Desktop')")
found, _ = audited(lambda: admin.post(f"/computers/{FRESH}/edit", data={
    "location": "Lab 1", "device_type": "Desktop", "asset_status": "In use", "purchase_date": "", "vendor": "",
    "warranty_until": ""}))
check(f"a never-edited computer (status NULL) saved as In use: the same state, nothing audited "
      f"({[r['details'] for r in found]})", found == [])

# ---------------------------------------------------------------------------
print("9. Equipment")
# ---------------------------------------------------------------------------


def item(category="Keyboard", location="Lab 1", quantity=5):
    return write("INSERT INTO equipment (location, category, quantity) VALUES (?, ?, ?)", (location, category, quantity))


E = item()


def edit_item(**fields):
    data = {"location": "Lab 1", "category": "Keyboard", "manufacturer": "", "model": "", "serial_number": "",
            "quantity": "5", "asset_status": "In use", "purchase_date": "", "vendor": "", "warranty_until": "",
            "notes": ""}
    data.update(fields)
    return lambda: admin.post(f"/equipment/{E}/edit", data=data)


found, _ = audited(edit_item(location="Lab 2", quantity="4", notes=SECRET_TEXT))
row = one_row("equipment edited", found, "equipment_updated", ADMIN, "equipment", "Keyboard")
check(f"...old -> new, and notes only as edited ({row.get('details')})",
      row.get("details") == "Location: Lab 1 -> Lab 2; Quantity: 5 -> 4; Notes edited")
found, _ = audited(edit_item(location="Lab 2", quantity="4", notes=SECRET_TEXT))
check("equipment saved unchanged: nothing audited", found == [])
SHELF = [item("Mouse"), item("Mouse"), E]
found, _ = audited(lambda: admin.post("/equipment/bulk-condition", data={"equipment_ids": [str(i) for i in SHELF],
                                                                         "asset_status": "In repair"}))
row = one_row("bulk condition", found, "equipment_updated", ADMIN, "equipment", "3 items")
check(f"...count and ids ({row.get('details')})",
      row.get("details") == "Condition set to In repair on 3 items: " + appmod.audit_id_list(SHELF))
found, _ = audited(lambda: admin.post("/equipment/bulk-location", data={"equipment_ids": [str(i) for i in SHELF],
                                                                        "location": "Lab 4"}))
row = one_row("bulk move", found, "equipment_updated", ADMIN, "equipment", "3 items")
check(f"...where to ({row.get('details')})", row.get("details") == "Moved 3 items to Lab 4: " + appmod.audit_id_list(SHELF))

# ---------------------------------------------------------------------------
print("10. Colours: red only for what destroys")
# ---------------------------------------------------------------------------
RED = {"account_deleted", "software_deleted", "equipment_deleted", "uninstall_created", "report_deleted",
       "report_purged", "jobs_deleted"}
NEW = ["report_status_changed", "report_priority_changed", "report_assigned", "report_updated", "report_notes_edited",
       "job_cancelled", "job_retried", "computer_updated", "computer_retired", "equipment_updated"]
check("none of this commit's actions is red", not [a for a in NEW if appmod.audit_tone(a) == "bad"])
check("the red ones are exactly the deletes and uninstalls",
      {a for a in appmod.AUDIT_ACTION_TONES if appmod.audit_tone(a) == "bad"} == RED)
check("a cancel and a retirement are amber, a retry blue",
      [appmod.audit_tone(a) for a in ("job_cancelled", "computer_retired", "job_retried")] == ["warn", "warn", "info"])

print()
print(f"{passed} passed, {len(failed)} failed")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

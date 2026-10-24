"""Archiving finished problem reports: only a FINISHED report archives; the
server, not just the buttons, keeps it read-only; Restore brings it back
as it was; and nothing that counts open work includes it.
"""
import json
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


work = tempfile.mkdtemp(prefix="appease_archive_")
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
from flask import template_rendered  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

if not hasattr(appmod, "archive_reports"):
    print("COULD NOT RUN - app.py predates archiving problem reports.")
    sys.exit(3)
appmod.app.config["TESTING"] = True
PASSWORD = "Str0ng!Passw0rd"
REFUSED = "is archived. Restore it to change it."


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


def one(report_id):
    found = rows("SELECT * FROM problem_reports WHERE id = ?", (report_id,))
    return found[0] if found else None


def audit_after(before):
    return rows("SELECT * FROM audit_log WHERE id > ? ORDER BY id", (before,))


def last_audit():
    return rows("SELECT MAX(id) AS id FROM audit_log")[0]["id"] or 0


write("UPDATE admins SET password_hash = ?, must_change_password = 0 WHERE username = 'admin'",
      (generate_password_hash(PASSWORD),))
ADMIN = rows("SELECT id FROM admins WHERE username = 'admin'")[0]["id"]
write("UPDATE admins SET first_name = 'Maria', last_name = 'Santos' WHERE id = ?", (ADMIN,))
write("INSERT INTO admins (username, password_hash, role, must_change_password) VALUES ('faculty1', ?, 'faculty', 0)",
      (generate_password_hash(PASSWORD),))


def signed_in(username):
    client = appmod.app.test_client()
    client.post("/login", data={"username": username, "password": PASSWORD})
    return client


admin = signed_in("admin")
faculty = signed_in("faculty1")
HOSTS = iter(range(1, 10000))


def report(status="resolved", priority="medium", assigned=None, notes="kept note", photos=None):
    return write("INSERT INTO problem_reports (computer_hostname, problem_type, description, status, priority, "
                 "assigned_to, notes, resolution_notes, photo_paths) VALUES (?, 'mouse', 'it broke', ?, ?, ?, ?, "
                 "'replaced it', ?)",
                 (f"LAB1-PC{next(HOSTS):02d}", status, priority, assigned, notes,
                  json.dumps(photos) if photos else None))


def toast(response):
    location = response.headers.get("Location", "")
    from urllib.parse import parse_qs, urlparse
    query = parse_qs(urlparse(location).query)
    return (query.get("report_success") or query.get("report_error") or [""])[0], query


def archive(client, ids):
    return client.post("/problem-reports/archive", data={"report_ids": [str(i) for i in ids]})


def restore(client, ids):
    return client.post("/problem-reports/restore", data={"report_ids": [str(i) for i in ids]})


# ---------------------------------------------------------------------------
print("1. Archiving: finished reports only")
# ---------------------------------------------------------------------------
cols = {r["name"] for r in rows("PRAGMA table_info(problem_reports)")}
check("problem_reports has archived_at and archived_by", {"archived_at", "archived_by"} <= cols)
database.init_db()
check("...and init_db() runs again over them", {"archived_at", "archived_by"} <= {
    r["name"] for r in rows("PRAGMA table_info(problem_reports)")})

R = report("resolved")
before = last_audit()
message, _ = toast(archive(admin, [R]))
row = one(R)
check(f"a resolved report is archived ({message})", row["archived_at"] is not None and row["archived_by"] == ADMIN)
check("...with a plain message", message == f"Report #{R} archived.")
found = audit_after(before)
check("...and exactly one audit row, report_archived",
      [(a["action_type"], a["target_type"], a["target_label"], a["details"]) for a in found]
      == [("report_archived", "report", "1 report", f"Archived 1 report: #{R}")])
NFF = report("no_fault_found")
archive(admin, [NFF])
check("No fault found is finished too", one(NFF)["archived_at"] is not None)

for status in ("open", "in_progress", "waiting_for_parts", "waiting_for_approval"):
    U = report(status)
    before = last_audit()
    message, _ = toast(archive(admin, [U]))
    check(f"{status}: not archived, saying why ({message})",
          one(U)["archived_at"] is None and message == f"Report #{U} isn't finished yet, so it can't be archived.")
    check(f"{status}: nothing audited", audit_after(before) == [])

DONE = [report("resolved"), report("no_fault_found"), report("resolved")]
OPEN = [report("open"), report("in_progress")]
before = last_audit()
message, _ = toast(archive(admin, DONE + OPEN))
check(f"a mixed selection archives the finished ones only ({message})",
      all(one(i)["archived_at"] for i in DONE) and not any(one(i)["archived_at"] for i in OPEN))
check("...and says so", message == "Archived 3 reports; 2 skipped because they aren't finished yet.")
found = audit_after(before)
check(f"...one audit row, naming them and the skip ({[a['details'] for a in found]})",
      [(a["action_type"], a["details"]) for a in found]
      == [("report_archived", "Archived 3 reports: " + appmod.audit_id_list(DONE)
           + "; 2 skipped because they aren't finished yet")])
message, _ = toast(archive(admin, [report("resolved"), report("open")]))
check(f"one skipped: singular ({message})", message == "Archived 1 report; 1 skipped because it isn't finished yet.")
message, _ = toast(archive(admin, OPEN))
check(f"only unfinished selected: nothing archived ({message})",
      message == "Nothing archived: 2 reports aren't finished yet." and not any(one(i)["archived_at"] for i in OPEN))
MANY = [report("resolved") for _ in range(12)]
before = last_audit()
archive(admin, MANY)
check("twelve at once: the audit row lists ten and counts two",
      audit_after(before)[0]["details"] == "Archived 12 reports: " + appmod.audit_id_list(MANY)
      and appmod.audit_id_list(MANY).endswith(" and 2 more"))
before = last_audit()
message, _ = toast(archive(admin, [R]))
check("archiving an archived report again: nothing new, nothing audited", audit_after(before) == [])

# ---------------------------------------------------------------------------
print("2. Restore")
# ---------------------------------------------------------------------------
KEEP = report("resolved", priority="high", assigned=ADMIN, notes="the note")
archive(admin, [KEEP])
snapshot = {k: v for k, v in one(KEEP).items() if k not in ("archived_at", "archived_by")}
before = last_audit()
message, query = toast(restore(admin, [KEEP]))
after = one(KEEP)
check(f"restored ({message})", after["archived_at"] is None and after["archived_by"] is None
      and message == f"Report #{KEEP} restored.")
check("...exactly as it was", {k: v for k, v in after.items() if k not in ("archived_at", "archived_by")} == snapshot)
check("...back on the Archived tab it was restored from", query.get("view") == ["archived"])
check("...one audit row, report_restored", [(a["action_type"], a["details"]) for a in audit_after(before)]
      == [("report_restored", f"Restored 1 report: #{KEEP}")])
before = last_audit()
message, _ = toast(restore(admin, DONE))
check(f"bulk restore ({message})", message == "Restored 3 reports." and not any(one(i)["archived_at"] for i in DONE))
check("...one audit row for the request", [a["action_type"] for a in audit_after(before)] == ["report_restored"])
before = last_audit()
message, _ = toast(restore(admin, OPEN))
check(f"restoring reports that are not archived: refused, nothing audited ({message})",
      message == "None of the selected reports are archived." and audit_after(before) == [])

# ---------------------------------------------------------------------------
print("3. An archived report is read-only - on the server")
# ---------------------------------------------------------------------------
A = report("resolved", priority="low", assigned=None, notes="frozen")
archive(admin, [A])
frozen = one(A)
ATTEMPTS = [
    ("status", lambda: admin.post(f"/problem-reports/{A}/status", data={"status": "open"})),
    ("status from the dashboard", lambda: admin.post(f"/problem-reports/{A}/status",
                                                     data={"status": "open", "return_to": "dashboard"})),
    ("priority", lambda: admin.post(f"/problem-reports/{A}/priority", data={"priority": "high"})),
    ("assignment", lambda: admin.post(f"/problem-reports/{A}/assign", data={"assigned_to": str(ADMIN)})),
    ("the edit drawer", lambda: admin.post(f"/problem-reports/{A}/edit", data={
        "status": "open", "priority": "high", "assigned_to": str(ADMIN)})),
    ("notes", lambda: admin.post(f"/problem-reports/{A}/notes", data={"notes": "changed", "resolution_notes": "x"})),
]
for label, attempt in ATTEMPTS:
    before = last_audit()
    message, _ = toast(attempt())
    check(f"{label}: refused, saying why ({message})", message == f"Report #{A} {REFUSED}")
    check(f"{label}: the report is unchanged", one(A) == frozen)
    check(f"{label}: nothing audited", audit_after(before) == [])
from urllib.parse import urlparse  # noqa: E402
check("the dashboard's refusal goes back to the dashboard (/)", urlparse(admin.post(
    f"/problem-reports/{A}/status", data={"status": "open", "return_to": "dashboard"}).headers.get("Location", "")).path
      == "/")

write("UPDATE problem_reports SET assigned_to = ? WHERE id = ?", (ADMIN, A))
frozen = one(A)
LIVE = report("open", assigned=ADMIN)
BULK = [
    # A status the archived report does NOT have, so only the archive guard
    # can keep it out - /bulk-status already skips rows already at the status.
    ("/bulk/status (dashboard)", "/problem-reports/bulk/status", {"status": "in_progress"}),
    ("/bulk/priority (dashboard)", "/problem-reports/bulk/priority", {"priority": "high"}),
    ("/bulk-status", "/problem-reports/bulk-status", {"status": "in_progress"}),
    ("/bulk-priority", "/problem-reports/bulk-priority", {"priority": "high"}),
    ("/bulk-assign", "/problem-reports/bulk-assign", {"assigned_to": "__none__"}),
]
for label, url, data in BULK:
    write("UPDATE problem_reports SET status = 'open', priority = 'medium', assigned_to = ? WHERE id = ?",
          (ADMIN, LIVE))
    before = last_audit()
    admin.post(url, data=dict(data, report_ids=[str(A), str(LIVE)]))
    check(f"{label}: the archived report is skipped", one(A) == frozen)
    check(f"{label}: the live one still changes", one(LIVE) != {**one(LIVE), "status": "open", "priority": "medium",
                                                                  "assigned_to": ADMIN})
    found = audit_after(before)
    check(f"{label}: the audit row names only the live report ({[a['details'] for a in found]})",
          len(found) == 1 and f"#{A}" not in found[0]["details"] and f"#{LIVE}" in found[0]["details"])
    before = last_audit()
    admin.post(url, data=dict(data, report_ids=[str(A)]))
    check(f"{label} with only the archived report: unchanged, nothing audited",
          one(A) == frozen and audit_after(before) == [])

# ---------------------------------------------------------------------------
print("4. Photos and Delete")
# ---------------------------------------------------------------------------
photo_dir = os.path.join(server_dir, "static", "report_photos")
os.makedirs(photo_dir, exist_ok=True)


def photo(name):
    open(os.path.join(photo_dir, name), "wb").close()
    return f"report_photos/{name}"


P = report("resolved", photos=[photo("a.jpg"), photo("b.jpg")])
archive(admin, [P])
check("archiving keeps the photos", os.path.exists(os.path.join(photo_dir, "a.jpg"))
      and os.path.exists(os.path.join(photo_dir, "b.jpg")))
response = admin.post(f"/problem-reports/{P}/delete")
check("deleting an archived report removes it", one(P) is None)
check("...and its photos", not os.path.exists(os.path.join(photo_dir, "a.jpg"))
      and not os.path.exists(os.path.join(photo_dir, "b.jpg")))
check("...and goes back to the Archived tab", "view=archived" in response.headers.get("Location", ""))
Q = [report("resolved", photos=[photo("c.jpg")]), report("resolved", photos=[photo("d.jpg")])]
archive(admin, Q)
response = admin.post("/problem-reports/bulk-delete", data={"report_ids": [str(i) for i in Q]})
check("bulk delete on the Archived tab: gone, photos too, back to that tab",
      not any(one(i) for i in Q) and not os.path.exists(os.path.join(photo_dir, "c.jpg"))
      and "view=archived" in response.headers.get("Location", ""))
M = report("resolved", photos=[photo("e.jpg")])
before = last_audit()
message, _ = toast(admin.post(f"/problem-reports/{M}/delete"))
check(f"a report on the main list cannot be deleted - only from the archive ({message})",
      one(M) is not None and message == f"Report #{M} isn't archived. Archive it, then delete it from the Archived tab.")
check("...its photo stays, and nothing is audited",
      os.path.exists(os.path.join(photo_dir, "e.jpg")) and audit_after(before) == [])

# ---------------------------------------------------------------------------
print("5. Two tabs, and the counts")
# ---------------------------------------------------------------------------
write("DELETE FROM problem_reports")
ACTIVE = [report("open", assigned=ADMIN), report("in_progress", priority="high"), report("resolved")]
GONE = [report("resolved"), report("no_fault_found")]
archive(admin, GONE)
# Planted: archived AND unfinished - a state the app never produces, here to
# prove the counts do not lean on that.
PLANTED = report("open", priority="high", assigned=ADMIN)
write("UPDATE problem_reports SET archived_at = '2026-10-01 00:00:00', archived_by = ? WHERE id = ?", (ADMIN, PLANTED))

captured = []


def record(sender, template, context, **extra):
    captured.append((template.name, context))


template_rendered.connect(record, appmod.app)


def context(client, url, name):
    captured.clear()
    client.get(url)
    found = [c for n, c in captured if n == name]
    return found[-1] if found else {}


main = context(admin, "/problem-reports", "problem_reports.html")
listed = {r["id"] for r in main.get("problem_reports", [])}
check(f"the main list holds only reports that are not archived ({sorted(listed)})", listed == set(ACTIVE))
summary = main.get("report_summary", {})
check(f"the summary cards count the main list only ({summary})",
      (summary.get("total"), summary.get("unresolved"), summary.get("high_priority"), summary.get("unassigned"))
      == (3, 2, 1, 1))
check("the Archived tab's count is its own", main.get("archived_count") == 3)
arch = context(admin, "/problem-reports?view=archived", "problem_reports.html")
check("?view=archived holds only archived reports", {r["id"] for r in arch.get("problem_reports", [])}
      == set(GONE) | {PLANTED})
write("UPDATE problem_reports SET archived_at = '2026-10-02 00:00:00' WHERE id = ?", (GONE[0],))
write("UPDATE problem_reports SET archived_at = '2026-10-03 00:00:00' WHERE id = ?", (GONE[1],))
order = [r["id"] for r in context(admin, "/problem-reports?view=archived", "problem_reports.html")["problem_reports"]]
check(f"...most recently archived first ({order})", order == [GONE[1], GONE[0], PLANTED])
dash = context(admin, "/", "dashboard.html")
check(f"dashboard Open Reports leaves out the planted archived one ({dash.get('open_reports_count')})",
      dash.get("open_reports_count") == 2)
check("dashboard Tasks card leaves it out", {t["id"] for t in dash.get("assigned_tasks", [])} == {ACTIVE[0]})
check("the reports sparkline still counts archived reports: it is a history of reports filed",
      sum(dash.get("kpi_sparklines", {}).get("reports", [])) == 6)
prof = context(admin, "/settings", "settings.html") or context(admin, "/settings", "profile.html")
check(f"Settings' Assigned to me leaves it out ({prof.get('assigned_open_count')})",
      {r["id"] for r in prof.get("assigned_reports", [])} == {ACTIVE[0]} and prof.get("assigned_open_count") == 1)

page = admin.get("/problem-reports").get_data(as_text=True)
check("the main tab is selected, with both counts",
      re.search(r'aria-selected="true">Reports \(3\)</a>', page) is not None and "Archived (3)" in page)
check("the main list shows the summary cards", "Not picked up" in page)
check("Archive is offered on the finished row only",
      page.count('data-dt-post-label="Archive"') == 1 and "data-dt-post-label=\"Restore\"" not in page)
check("the main bar has Archive and the edit controls", 'action="/problem-reports/archive"' in page
      and 'id="pr-bulk-status"' in page)
page = admin.get("/problem-reports?view=archived").get_data(as_text=True)
check("the Archived tab is selected", re.search(r'aria-selected="true">Archived \(3\)</a>', page) is not None)
check("...shows no summary cards", "Not picked up" not in page)
check("...has the Archived column", 'data-dt-label="Archived"' in page)
check("...offers Restore on every row and Edit on none",
      page.count('data-dt-post-label="Restore"') == 3 and "data-dt-edit " not in page and "data-dt-edit\n" not in page
      and "data-dt-edit\r\n" not in page)
check("...its bar is Restore, Export and Delete - no status, priority or assign",
      'action="/problem-reports/restore"' in page and 'id="pr-bulk-status"' not in page
      and 'id="pr-bulk-assign"' not in page and "bulk-delete" in page)
check("...Delete keeps its confirm dialog", 'data-dt-confirm="delete"' in page and 'data-dt-act="delete"' in page)
check("...Archive and Restore have none", 'data-dt-act="archive"' not in page and 'data-dt-act="restore"' not in page)
check("...the Details panel has Restore and read-only notes, no notes form",
      'id="details-restore-form"' in page and 'id="details-notes-readonly"' in page
      and 'id="details-notes-form"' not in page)
check("...and says who archived it, when", "by Maria Santos" in page)
deep = context(admin, f"/problem-reports?open={GONE[0]}", "problem_reports.html")
check("a link to an archived report opens the Archived tab", deep.get("report_view") == "archived")

# ---------------------------------------------------------------------------
print("6. Faculty: both tabs, read-only")
# ---------------------------------------------------------------------------
for url in ("/problem-reports", "/problem-reports?view=archived"):
    response = faculty.get(url)
    page = response.get_data(as_text=True)
    check(f"faculty open {url}", response.status_code == 200 and "Archived (3)" in page)
    check(f"...{url}: no Archive, Restore, Edit or Delete", "data-dt-post-url" not in page
          and "/problem-reports/archive" not in page and "/problem-reports/restore" not in page
          and "data-dt-delete-url" not in page and "bulk-delete" not in page)
before = last_audit()
faculty.post("/problem-reports/archive", data={"report_ids": [str(ACTIVE[2])]})
faculty.post("/problem-reports/restore", data={"report_ids": [str(GONE[0])]})
check("faculty posting Archive or Restore changes nothing",
      one(ACTIVE[2])["archived_at"] is None and one(GONE[0])["archived_at"] is not None and audit_after(before) == [])

# ---------------------------------------------------------------------------
print("7. Delete only from the archive, and Delete all archived")
# ---------------------------------------------------------------------------
write("DELETE FROM problem_reports")
LIVE2 = [report("open"), report("resolved")]
before = last_audit()
message, _ = toast(admin.post("/problem-reports/bulk-delete", data={"report_ids": [str(i) for i in LIVE2]}))
check(f"bulk delete of reports that aren't archived: refused ({message})",
      message == "Only archived reports can be deleted." and all(one(i) for i in LIVE2) and audit_after(before) == [])
ARCH = report("resolved")
archive(admin, [ARCH])
before = last_audit()
message, query = toast(admin.post("/problem-reports/bulk-delete",
                                  data={"report_ids": [str(ARCH), str(LIVE2[0])]}))
check(f"a hand-made mix: the archived one goes, the other is named as skipped ({message})",
      one(ARCH) is None and one(LIVE2[0]) is not None
      and message == "Deleted 1 report(s). 1 skipped because it isn't archived.")
check("...the audit row names only what was deleted",
      [a["details"] for a in audit_after(before)] == [f"Deleted 1 report: #{ARCH}"])

KEPT = [report("open", photos=[photo("live1.jpg")]), report("resolved", photos=[photo("live2.jpg")])]
PURGE = [report("resolved", photos=[photo(f"old{i}.jpg")]) for i in range(3)]
archive(admin, PURGE)


def purge(client, expected):
    return client.post("/problem-reports/purge-archived", data={"expected_count": str(expected)})


before = last_audit()
message, _ = toast(purge(faculty, 3))
check("faculty cannot purge", all(one(i) for i in PURGE) and audit_after(before) == [])
for wrong in (2, 4, ""):
    message, _ = toast(purge(admin, wrong))
    check(f"the archive changed since the page loaded (count {wrong!r}): nothing deleted ({message})",
          message == "The archive changed since this page loaded. Reload and try again."
          and all(one(i) for i in PURGE) and audit_after(before) == [])
message, query = toast(purge(admin, 3))
check(f"purge with the right count: every archived report gone ({message})",
      not any(one(i) for i in PURGE) and message == "Deleted 3 archived reports.")
check("...their photos too", not any(os.path.exists(os.path.join(photo_dir, f"old{i}.jpg")) for i in range(3)))
check("...nothing that wasn't archived, photos included",
      all(one(i) for i in KEPT) and os.path.exists(os.path.join(photo_dir, "live1.jpg"))
      and os.path.exists(os.path.join(photo_dir, "live2.jpg")))
check("...back on the Archived tab", query.get("view") == ["archived"])
found = audit_after(before)
check(f"...one red audit row ({[(a['action_type'], a['details']) for a in found]})",
      [(a["action_type"], a["target_label"], a["details"]) for a in found]
      == [("report_purged", "3 archived reports", "Deleted all 3 archived reports: " + appmod.audit_id_list(PURGE))]
      and appmod.audit_tone("report_purged") == "bad")
before = last_audit()
message, _ = toast(purge(admin, 0))
check(f"purge with an empty archive: nothing happens ({message})",
      message == "There are no archived reports." and all(one(i) for i in KEPT) and audit_after(before) == [])
BIG = [report("resolved") for _ in range(13)]
archive(admin, BIG)
before = last_audit()
purge(admin, 13)
check("thirteen purged: the audit row lists ten and counts three",
      audit_after(before)[0]["details"] == "Deleted all 13 archived reports: " + appmod.audit_id_list(BIG)
      and appmod.audit_id_list(BIG).endswith(" and 3 more"))
SOLO = report("resolved")
archive(admin, [SOLO])
page = admin.get("/problem-reports?view=archived").get_data(as_text=True)
check("one archived report: the dialog says so in the singular",
      "Permanently delete the 1 archived report and its photos? This cannot be undone." in page)
before = last_audit()
message, _ = toast(purge(admin, 1))
check(f"...and purging it says so too ({message})", message == "Deleted 1 archived report."
      and audit_after(before)[0]["details"] == f"Deleted the 1 archived report: #{SOLO}")

# The page: Delete on the Archived tab only; Delete all archived, admin only,
# and only with rows.
write("DELETE FROM problem_reports")
ROWS = [report("open"), report("resolved")] + [report("resolved") for _ in range(4)]
archive(admin, ROWS[2:])
main_page = admin.get("/problem-reports").get_data(as_text=True)
check("main tab: no row-menu Delete, no bulk Delete, no Details Delete",
      "data-dt-delete-url" not in main_page and "/problem-reports/bulk-delete" not in main_page
      and 'id="details-delete-form"' not in main_page)
check("main tab: no Delete all archived", 'type="button" data-pr-purge' not in main_page and 'id="pr-confirm-purge"' not in main_page)
arch_page = admin.get("/problem-reports?view=archived").get_data(as_text=True)
check("Archived tab: row-menu, bulk and Details Delete are all there",
      arch_page.count("data-dt-delete-url") == 4 and "/problem-reports/bulk-delete" in arch_page
      and 'id="details-delete-form"' in arch_page)
check("Archived tab: Delete all archived, posting the tab's count",
      'type="button" data-pr-purge' in arch_page and 'name="expected_count" value="4"' in arch_page)
check("...with its own red dialog and the exact count",
      'data-dt-confirm="purge"' in arch_page and 'data-variant="destructive"' in arch_page
      and "Permanently delete all 4 archived reports and their photos? This cannot be undone." in arch_page)
check("Details' Delete Report asks the table for the shared dialog; the old in-panel confirm is gone",
      'onclick="requestDetailsDelete()"' in arch_page and "dt:request-confirm" in arch_page
      and "details-delete-confirm-step" not in arch_page and "showDeleteConfirmStep" not in arch_page)
# Tied to closeDetailsModal(): base.html's shared overlay has the same
# 'Escape' test of its own, so a bare substring would pass without this one.
check("Esc closes the Details panel (source only - t/ cannot press a key; click-test it)",
      re.search(r"event\.key === 'Escape' && overlay\.classList\.contains\('modal-open'\)\)\s*\{\s*"
                r"closeDetailsModal\(\);", arch_page) is not None)
fac_page = faculty.get("/problem-reports?view=archived").get_data(as_text=True)
check("faculty: no Delete all archived and no Delete anywhere",
      'type="button" data-pr-purge' not in fac_page and 'id="pr-confirm-purge"' not in fac_page and "data-dt-delete-url" not in fac_page
      and 'id="details-delete-form"' not in fac_page)
write("UPDATE problem_reports SET archived_at = NULL, archived_by = NULL")
empty_page = admin.get("/problem-reports?view=archived").get_data(as_text=True)
check("an empty Archived tab: no Delete all archived", 'type="button" data-pr-purge' not in empty_page
      and "No archived reports." in empty_page)

# ---------------------------------------------------------------------------
print("8. Audit colours")
# ---------------------------------------------------------------------------
check("archiving and restoring are never red",
      appmod.audit_tone("report_archived") != "bad" and appmod.audit_tone("report_restored") != "bad")

print()
print(f"{passed} passed, {len(failed)} failed")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

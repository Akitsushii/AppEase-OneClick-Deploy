"""Faculty get row selection and Export on Problem Reports - nothing else.
Hiding a button is not a permission, so each admin-only route is checked
twice: turned away, AND the report byte-for-byte unchanged - a redirect
alone would pass a route that did the work first.

Not reachable here: the bar appearing on a tick, and the CSV itself; those
need a manual click-test on a faculty account.
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


def arg_path(name, default):
    for index, arg in enumerate(sys.argv):
        if arg == name and index + 1 < len(sys.argv):
            return os.path.abspath(sys.argv[index + 1])
        if arg.startswith(name + "="):
            return os.path.abspath(arg.split("=", 1)[1])
    return default


SERVER_SRC = arg_path("--server", os.path.join(ROOT, "server"))
work = tempfile.mkdtemp(prefix="appease_facbar_")
server_dir = os.path.join(work, "server")
shutil.copytree(
    SERVER_SRC, server_dir,
    ignore=shutil.ignore_patterns(
        "appease.db*", "__pycache__", "*.pyc", ".env",
        "uploads", "staging", "report_photos", "avatars", "venv",
    ),
)
sys.path.insert(0, server_dir)
os.chdir(server_dir)

import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
import app as appmod  # noqa: E402

appmod.app.config["TESTING"] = True

BULK_ROUTES = {
    "bulk-status": ("/problem-reports/bulk-status", {"status": "resolved"}),
    "bulk-priority": ("/problem-reports/bulk-priority", {"priority": "low"}),
    "bulk-assign": ("/problem-reports/bulk-assign", {"assigned_to": "__none__"}),
    "bulk-delete": ("/problem-reports/bulk-delete", {}),
}


def raw():
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


conn = raw()
conn.execute(
    "INSERT INTO admins (username, password_hash, role, first_name, last_name)"
    " VALUES (?, ?, ?, ?, ?)",
    ("faculty1", "x", "faculty", "Fac", "Ulty"))
conn.commit()
faculty = conn.execute("SELECT id, username FROM admins WHERE username = 'faculty1'").fetchone()
admin = conn.execute("SELECT id, username FROM admins WHERE role = 'admin' LIMIT 1").fetchone()
conn.close()


def seed_report():
    """One report in a known state. Returns its id."""
    conn = raw()
    conn.execute("DELETE FROM problem_reports")
    conn.execute(
        "INSERT INTO problem_reports (computer_hostname, problem_type, description,"
        " status, priority, assigned_to, reported_at, notes)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("LAB1-PC01", "hardware", "no power", "open", "high", admin["id"],
         "2026-09-01 02:00:00", "internal note"))
    conn.commit()
    report_id = conn.execute("SELECT id FROM problem_reports").fetchone()["id"]
    conn.close()
    return report_id


def snapshot(report_id):
    """Every column of the report, or None if it is gone."""
    conn = raw()
    row = conn.execute("SELECT * FROM problem_reports WHERE id = ?", (report_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def client_as(account, role):
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["admin_id"] = account["id"]
        sess["admin_role"] = role
        sess["admin_username"] = account["username"]
    return c


faculty_client = client_as(faculty, "faculty")
admin_client = client_as(admin, "admin")

seed_report()
faculty_page = faculty_client.get("/problem-reports").get_data(as_text=True)
admin_page = admin_client.get("/problem-reports").get_data(as_text=True)


def action_bar(page):
    """The action bar markup, or '' when the page renders none."""
    m = re.search(r'<div class="dt-actionbar[^"]*"[^>]*>(.*?)\n\s*</div>', page, re.S)
    return m.group(1) if m else ""


print("\n=== 1. Faculty can select rows ===")

check("faculty get a select column header",
      "data-dt-select" in faculty_page)
check("faculty get a master checkbox to select the page",
      "Select all rows on this page" in faculty_page)
check("faculty get a checkbox on the row",
      "data-dt-check" in faculty_page)
check("the checkbox carries the report id, so a selection names real rows",
      re.search(r'data-dt-check\s+value="\d+"', faculty_page) is not None)
check("admins still get the select column",
      "data-dt-select" in admin_page and "data-dt-check" in admin_page)

print("\n=== 2. The faculty action bar offers Export and nothing else ===")

bar = action_bar(faculty_page)
check("faculty get an action bar", bar != "")
check("the faculty bar offers Export", "data-dt-export" in bar)
check("the faculty bar offers a way to clear the selection", "data-dt-clear" in bar)
check("the faculty bar posts nothing at all", "<form" not in bar)
for name, (url, _) in BULK_ROUTES.items():
    check(f"the faculty bar has no {name} control", url not in faculty_page)
check("faculty are offered no bulk Delete button", 'data-dt-act="delete"' not in faculty_page)
check("faculty get no delete-confirmation dialog", "data-dt-confirm" not in faculty_page)
check("faculty rows carry no delete url for the row menu",
      "data-dt-delete-url" not in faculty_page)

# Asked of the ROW, not of the page. data-table.js builds the row menu from
# the <tr>'s own attributes, and the drawer's script mentions
# data-dt-edit-priority in a getAttribute() call that renders for everyone -
# a whole-page substring match reads that as a leak and is wrong.
faculty_rows = re.findall(r"<tr\b[^>]*>", faculty_page)
admin_rows = re.findall(r"<tr\b[^>]*>", admin_page)
check("faculty rows carry no edit hook, so the row menu offers no Edit",
      faculty_rows and not any("data-dt-edit" in row for row in faculty_rows))
check("admin rows still carry the edit hook",
      any("data-dt-edit" in row for row in admin_rows))
check("faculty still get Details on the row menu",
      "data-dt-details" in faculty_page)
check("the drawer markup itself stays admin-only",
      'id="pr-edit-drawer"' not in faculty_page and 'id="pr-edit-drawer"' in admin_page)

print("\n=== 3. The admin action bar is unchanged ===")

admin_bar = action_bar(admin_page)
# A report is deleted only from the Archived tab, so Delete is looked for
# there - the seeded report finished and archived - and the main list is
# checked for having none.
conn = raw()
conn.execute("UPDATE problem_reports SET status = 'resolved', archived_at = '2026-10-06 00:00:00'")
conn.commit()
conn.close()
admin_archived_page = admin_client.get("/problem-reports?view=archived").get_data(as_text=True)
archived_bar = action_bar(admin_archived_page)
for name, (url, _) in BULK_ROUTES.items():
    check(f"admins still get {name}", url in (archived_bar if name == "bulk-delete" else admin_bar))
check("admins still get Export", "data-dt-export" in admin_bar)
check("admins still get the delete confirmation dialog (Archived tab)",
      'data-dt-confirm="delete"' in admin_archived_page)
check("admin rows still carry the row-menu delete url (Archived tab)",
      "data-dt-delete-url" in admin_archived_page)
check("...and the main list offers no Delete at all",
      "data-dt-delete-url" not in admin_page and "bulk-delete" not in admin_bar)

print("\n=== 4. The server refuses the bulk routes, and nothing is written ===")

for name, (url, payload) in BULK_ROUTES.items():
    report_id = seed_report()
    before = snapshot(report_id)
    response = faculty_client.post(url, data=dict(payload, report_ids=[str(report_id)]))
    after = snapshot(report_id)

    # Weak on purpose, and kept only because it is cheap: these routes
    # redirect on SUCCESS too, so a status code cannot tell "refused" from
    # "done". Dropping @admin_required outright leaves this assertion green.
    check(f"{name}: a faculty POST is turned away",
          response.status_code != 200)
    # These two carry the whole guarantee. A route that wrote and then
    # redirected, or one that simply lost its decorator, sails through the
    # check above and is caught here.
    check(f"{name}: the report still exists afterwards", after is not None)
    check(f"{name}: not one column of the report changed", before == after)

print("\n=== 5. The single-report routes behind the row menu are gated too ===")

report_id = seed_report()
before = snapshot(report_id)
response = faculty_client.post(f"/problem-reports/{report_id}/delete",
                               data={"report_ids": [str(report_id)]})
after = snapshot(report_id)
check("a faculty POST to the single-report delete is turned away",
      response.status_code != 200)
check("the report survives a faculty delete", after is not None)
check("not one column changed", before == after)

response = faculty_client.post(f"/problem-reports/{report_id}/notes",
                               data={"notes": "WRITTEN-BY-FACULTY",
                                     "resolution_notes": "WRITTEN-BY-FACULTY"})
after = snapshot(report_id)
check("a faculty POST to the notes route is turned away",
      response.status_code != 200)
check("the notes are not rewritten by a faculty POST",
      after is not None and after["notes"] == before["notes"])
check("the resolution notes are not rewritten by a faculty POST",
      after is not None and after["resolution_notes"] == before["resolution_notes"])

print("\n=== 6. An admin can still do the work ===")

report_id = seed_report()
admin_client.post("/problem-reports/bulk-status",
                  data={"status": "resolved", "report_ids": [str(report_id)]})
check("an admin bulk status change still lands",
      snapshot(report_id)["status"] == "resolved")

admin_client.post("/problem-reports/bulk-priority",
                  data={"priority": "low", "report_ids": [str(report_id)]})
check("an admin bulk priority change still lands",
      snapshot(report_id)["priority"] == "low")

# Deleting happens only from the archive, so the (resolved) report is
# archived first - exactly what an admin does on the page.
admin_client.post("/problem-reports/archive", data={"report_ids": [str(report_id)]})
admin_client.post("/problem-reports/bulk-delete", data={"report_ids": [str(report_id)]})
check("an admin bulk delete still lands", snapshot(report_id) is None)

print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)
sys.exit(1 if failed else 0)

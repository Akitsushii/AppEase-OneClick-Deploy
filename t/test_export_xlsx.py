"""/export/xlsx is a pure transform, so faculty may use it (signing in is
still required). Checked, not argued: it runs no SELECT, its cells are
exactly what the caller posted, and Problem Reports' hidden fields stay out
even when a crafted POST names them.

Not reachable here: that the browser's fetch() offers the file for download.
"""
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
import zipfile

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
work = tempfile.mkdtemp(prefix="appease_xlsx_")
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

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Values chosen so that if any of them ever reaches a file, it is unmistakable
# and cannot have arrived by coincidence.
CANARIES = {
    "description": "CANARY-DESCRIPTION",
    "notes": "CANARY-INTERNALNOTES",
    "resolution_notes": "CANARY-RESOLUTION",
    "reporter_student_number": "CANARY-STUDENTNO",
    "reporter_program": "CANARY-PROGRAMME",
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
conn.execute(
    "INSERT INTO problem_reports (computer_hostname, problem_type, description,"
    " status, priority, reported_at, reporter_name, reporter_student_number,"
    " reporter_program, notes, resolution_notes)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    ("LAB1-PC01", "hardware", CANARIES["description"], "open", "high",
     "2026-09-01 02:00:00", "Student A", CANARIES["reporter_student_number"],
     CANARIES["reporter_program"], CANARIES["notes"], CANARIES["resolution_notes"]))
conn.execute(
    "INSERT INTO notifications (severity, event_type, message, audience, created_at)"
    " VALUES (?, ?, ?, ?, ?)",
    ("info", "public_evt", "VISIBLE-TO-ALL", "all", "2026-09-01 02:00:00"))
conn.execute(
    "INSERT INTO notifications (severity, event_type, message, audience, created_at)"
    " VALUES (?, ?, ?, ?, ?)",
    ("error", "admin_evt", "ADMIN-ONLY-ROW", "admin", "2026-09-01 02:00:00"))
conn.commit()
faculty = conn.execute("SELECT id, username FROM admins WHERE username = 'faculty1'").fetchone()
admin = conn.execute("SELECT id, username FROM admins WHERE role = 'admin' LIMIT 1").fetchone()
conn.close()


def client_as(account, role):
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["admin_id"] = account["id"]
        sess["admin_role"] = role
        sess["admin_username"] = account["username"]
    return c


faculty_client = client_as(faculty, "faculty")
admin_client = client_as(admin, "admin")


def post_grid(client, columns, rows, filename="export", title="Export"):
    body = json.dumps({"filename": filename, "title": title,
                       "columns": columns, "rows": rows})
    return client.post(
        "/export/xlsx",
        data={"payload": (io.BytesIO(body.encode("utf-8")), "grid.json")},
        content_type="multipart/form-data")


def workbook(response):
    """The returned workbook, or None when the response is not one: an HTML
    page instead must not end the run with a BadZipFile traceback before
    every later check has reported."""
    try:
        return zipfile.ZipFile(io.BytesIO(response.get_data()))
    except (zipfile.BadZipFile, ValueError):
        return None


def part_names(response):
    archive = workbook(response)
    return archive.namelist() if archive else []


def parts_text(response):
    """Every part of the returned workbook, as one string."""
    archive = workbook(response)
    if archive is None:
        # Fall back to the raw body: a check for "the stored value is absent"
        # must read whatever actually came back, or a leak inside an HTML
        # error page would pass unnoticed.
        return response.get_data(as_text=True)
    return "\n".join(archive.read(n).decode("utf-8", "replace") for n in archive.namelist())


def cells(response):
    archive = workbook(response)
    if archive is None or "xl/worksheets/sheet1.xml" not in archive.namelist():
        return None
    sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
    return re.findall(r'<t xml:space="preserve">([^<]*)</t>', sheet)


print("\n=== 1. A faculty user can export, and gets a real workbook ===")

response = post_grid(faculty_client, ["ID", "Computer"], [["1", "LAB1-PC01"]])
check("faculty export succeeds", response.status_code == 200)
check("faculty export is served as a spreadsheet, not a redirect to a page",
      response.headers.get("Content-Type", "").startswith(XLSX_MIME))
check("faculty export is a zip container, not an HTML page saved as .xlsx",
      response.get_data()[:2] == b"PK")
check("faculty export carries the worksheet part",
      "xl/worksheets/sheet1.xml" in part_names(response))
check("faculty export is offered as a download under the requested name",
      'filename="export.xlsx"' in response.headers.get("Content-Disposition", ""))
check("an admin export still works unchanged",
      post_grid(admin_client, ["ID"], [["1"]]).status_code == 200)

print("\n=== 2. Signing in is still required ===")

anon = appmod.app.test_client()
anon_response = post_grid(anon, ["ID"], [["1"]])
check("a signed-out caller cannot export", anon_response.status_code != 200)
check("a signed-out caller is sent to the login page",
      "/login" in (anon_response.headers.get("Location") or ""))

print("\n=== 3. The endpoint reads nothing from the database ===")

# Every statement run during the request is recorded. A SELECT here would mean
# the response could contain something the caller did not send, which is the
# whole basis for letting faculty in.
statements = []


class SpyConnection(sqlite3.Connection):
    def execute(self, sql, *args, **kwargs):
        statements.append(" ".join(str(sql).split()))
        return super().execute(sql, *args, **kwargs)


real_connect = sqlite3.connect


def spy_connect(*args, **kwargs):
    kwargs["factory"] = SpyConnection
    return real_connect(*args, **kwargs)


sqlite3.connect = spy_connect
try:
    statements.clear()
    spied = post_grid(faculty_client, ["ID", "Computer"], [["1", "LAB1-PC01"]])
    during_export = list(statements)
    statements.clear()
    faculty_client.get("/problem-reports")
    during_page = list(statements)
finally:
    sqlite3.connect = real_connect

selects = [s for s in during_export if s.lstrip().upper().startswith("SELECT")]
check("the export runs no SELECT, so it cannot read rows into the file",
      selects == [])
check("no statement during the export names problem_reports",
      not any("problem_reports" in s for s in during_export))
check("no statement during the export names notifications",
      not any("notifications" in s.lower() for s in during_export))
# Without this the "no SELECT" result above could just mean the spy never ran.
check("control: the same spy does record SELECTs on a page that queries",
      any(s.lstrip().upper().startswith("SELECT") for s in during_page))
check("control: the spy sees problem_reports when a page actually reads it",
      any("problem_reports" in s for s in during_page))
check("the export still succeeded while being watched", spied.status_code == 200)

print("\n=== 4. The file mirrors the request, and nothing else ===")

mirrored = post_grid(faculty_client, ["Col A", "Col B"],
                     [["sent-by-caller-1", "sent-by-caller-2"]])
check("every cell in the file was sent by the caller",
      cells(mirrored) == ["Col A", "Col B", "sent-by-caller-1", "sent-by-caller-2"])

# A hand-crafted POST from a faculty session, asking for the admin-only fields
# by name - as column headers AND as row values.
crafted = post_grid(
    faculty_client,
    ["ID", "description", "notes", "resolution_notes",
     "reporter_student_number", "reporter_program"],
    [["1", "description", "notes", "resolution_notes",
      "reporter_student_number", "reporter_program"]])
check("a hand-crafted faculty POST naming the admin-only fields is answered",
      crafted.status_code == 200)
crafted_text = parts_text(crafted)
for field, canary in CANARIES.items():
    check(f"a faculty POST asking for {field} does not receive its stored value",
          canary not in crafted_text)

# The same question asked in the other shapes a caller might reach for.
for probe, label in [
    (["*"], "a SELECT-star style column"),
    (["problem_reports.description"], "a table-qualified column name"),
    (["ID"], "a bare row id"),
]:
    probed = parts_text(post_grid(faculty_client, probe, [["1"]]))
    check(f"{label} returns no stored report data",
          not any(c in probed for c in CANARIES.values()))

print("\n=== 5. Notification History is row-scoped before the export sees it ===")

faculty_notifications = faculty_client.get("/notifications").get_data(as_text=True)
admin_notifications = admin_client.get("/notifications").get_data(as_text=True)
check("faculty see the rows addressed to everyone",
      "VISIBLE-TO-ALL" in faculty_notifications)
check("faculty do not see admin-only rows, so cannot export them",
      "ADMIN-ONLY-ROW" not in faculty_notifications)
check("admins do see admin-only rows", "ADMIN-ONLY-ROW" in admin_notifications)
check("faculty already had the checkbox column on Notification History",
      "data-dt-check" in faculty_notifications)
check("faculty already had an Export-only action bar on Notification History",
      "data-dt-export" in faculty_notifications)

print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)
sys.exit(1 if failed else 0)

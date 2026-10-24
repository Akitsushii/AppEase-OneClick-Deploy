"""Notifications name each event in words, not by its internal code. The
filter menu is built from the cell text, so this checks that text; the
menu itself is checked in Chrome.
"""
import html.parser
import os
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


work = tempfile.mkdtemp(prefix="appease_events_")
server_dir = os.path.join(work, "server")
# Never the real database or secrets - see test_agent_inventory.py.
shutil.copytree(
    os.path.join(ROOT, "server"), server_dir,
    ignore=shutil.ignore_patterns(
        "appease.db*", "__pycache__", "*.pyc", ".env",
        "uploads", "staging", "report_photos", "avatars",
    ),
)
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
conn = sqlite3.connect(database.DB_PATH)
conn.execute("UPDATE admins SET password_hash = ?, must_change_password = 0 WHERE username = 'admin'",
             (generate_password_hash(PASSWORD),))
conn.execute("INSERT INTO admins (username, password_hash, first_name, last_name, role, must_change_password) "
             "VALUES ('faculty.one', ?, 'Fac', 'Ulty', 'faculty', 0)", (generate_password_hash(PASSWORD),))
conn.commit()
conn.close()

EXPECTED = {
    "problem_report": "Problem report",
    "job_failed": "Install failed",
    "computer_status": "Computer status",
    "batch_done": "Deployment finished",
}
with appmod.app.test_request_context("/"):
    db = appmod.get_db()
    for code in EXPECTED:
        appmod.add_notification(db, code, f"Probe message for {code}", severity="info", link_url="/",
                                audience="all")
    # A code nobody has named yet must still read as words, never blank.
    appmod.add_notification(db, "agent_update_ready", "Probe message for a future event", audience="all")
    db.commit()


class Cells(html.parser.HTMLParser):
    """(header labels, rows of (text, attrs)) for the first data table."""

    def __init__(self):
        super().__init__()
        self.heads, self.rows, self.cell, self.in_tbody, self.in_th = [], [], None, False, None

    def handle_starttag(self, tag, attrs):
        if tag == "th" and not self.in_tbody:
            self.in_th = ""
        elif tag == "tbody":
            self.in_tbody = True
        elif tag == "tr" and self.in_tbody:
            self.rows.append([])
        elif tag == "td" and self.in_tbody and self.rows:
            self.cell = ["", dict(attrs)]

    def handle_endtag(self, tag):
        if tag == "th" and self.in_th is not None:
            self.heads.append(" ".join(self.in_th.split()))
            self.in_th = None
        elif tag == "td" and self.cell is not None:
            self.rows[-1].append((" ".join(self.cell[0].split()), self.cell[1]))
            self.cell = None
        elif tag == "tbody":
            self.in_tbody = False

    def handle_data(self, data):
        if self.in_th is not None:
            self.in_th += data
        if self.cell is not None:
            self.cell[0] += data


def event_cells(username):
    client = appmod.app.test_client()
    client.post("/login", data={"username": username, "password": PASSWORD})
    parser = Cells()
    parser.feed(client.get("/notifications").get_data(as_text=True))
    col = parser.heads.index("Event")
    msg = parser.heads.index("Message")
    return [(row[msg][0], row[col][0], row[col][1]) for row in parser.rows if len(row) > col]


for role, user in (("admin", "admin"), ("faculty", "faculty.one")):
    print(f"\n=== {role} ===")
    rows = event_cells(user)
    check(f"{role}: the probe notifications are listed", len([r for r in rows if "Probe message" in r[0]]) == 5)
    for code, label in EXPECTED.items():
        cell = next((r for r in rows if r[0] == f"Probe message for {code}"), None)
        check(f"{role}: {code} reads {label!r} (got {cell[1] if cell else None!r})", cell and cell[1] == label)
        check(f"{role}: {code} cell has no data-sort to put the code back in the filter",
              cell and "data-sort" not in cell[2])
        check(f"{role}: {code} is not in code font", cell and "font-mono" not in cell[2].get("class", ""))
    future = next((r for r in rows if r[0] == "Probe message for a future event"), None)
    check(f"{role}: an unnamed code still reads as words (got {future[1] if future else None!r})",
          future and future[1] == "Agent update ready")
    check(f"{role}: no raw code anywhere in the column", not any("_" in r[1] for r in rows))


print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)

os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

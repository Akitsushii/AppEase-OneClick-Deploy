"""Both public report pages are called "Report a Problem": the gate's tab
title and heading are checked against the form's own, side by side.
"""
import html.parser
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


work = tempfile.mkdtemp(prefix="appease_gatetitle_")
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
os.environ.pop("PUBLIC_REPORT_HOST", None)
os.environ.pop("DEMO_MODE_ENABLED", None)
os.environ["REPORT_QR_SECRET"] = "b" * 64

import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()
import app as appmod  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402
appmod.app.config["TESTING"] = True

PASSWORD = "Str0ng!Passw0rd"
conn = sqlite3.connect(database.DB_PATH)
conn.execute("UPDATE admins SET password_hash = ?, must_change_password = 0, middle_initial = 'L', "
             "email = 'admin@example.edu' WHERE username = 'admin'", (generate_password_hash(PASSWORD),))
conn.execute("INSERT INTO admins (username, password_hash, first_name, last_name, role, must_change_password) "
             "VALUES ('faculty.one', ?, 'Fac', 'Ulty', 'faculty', 0)", (generate_password_hash(PASSWORD),))
conn.execute("INSERT INTO computers (hostname, ip_address, status) VALUES ('LAB-PC-01', '10.0.0.1', 'online')")
conn.commit()
conn.close()


def signed_in(username):
    client = appmod.app.test_client()
    client.post("/login", data={"username": username, "password": PASSWORD})
    return client


class Visible(html.parser.HTMLParser):
    """Visible text, in order - script and style skipped."""

    def __init__(self):
        super().__init__()
        self.skip, self.bits = 0, []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.bits.append(" ".join(data.split()))


def visible(page):
    parser = Visible()
    parser.feed(page)
    return parser.bits


def title_and_heading(page):
    title = re.search(r"<title>([^<]*)</title>", page)
    heading = re.search(r"<h1[^>]*>([^<]*)</h1>", page)
    return (title.group(1).strip() if title else None, heading.group(1).strip() if heading else None)


gate = appmod.app.test_client().get("/report", environ_overrides={"REMOTE_ADDR": "10.9.9.9"}).get_data(as_text=True)
form = appmod.app.test_client().get(
    "/report?computer=LAB-PC-01&t=" + appmod.sign_computer("LAB-PC-01")).get_data(as_text=True)
check("the gate page is the code-entry page", 'name="qr_code"' in gate and 'name="problem_type"' not in gate)
check("the form page is the form", 'name="problem_type"' in form)
g, f = title_and_heading(gate), title_and_heading(form)
print("    gate:", g, " form:", f)
check("the gate's tab title reads Report a Problem - AppEase", g[0] == "Report a Problem - AppEase")
check("the gate's heading reads Report a Problem", g[1] == "Report a Problem")
check("the gate and the form share one tab title", g[0] == f[0])
check("the gate and the form share one heading", g[1] == f[1])


print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)

os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

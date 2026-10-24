"""An empty value on the Settings card reads "Not set"; a value that is set
is shown as itself.
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


work = tempfile.mkdtemp(prefix="appease_emptyvals_")
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


def card(page):
    """dt -> dd text, for the Settings card's definition lists."""
    pairs = re.findall(r"<dt>([^<]*)</dt>\s*<dd[^>]*>(.*?)</dd>", page, re.S)
    return {dt.strip(): " ".join(re.sub(r"<[^>]+>", " ", dd).split()) for dt, dd in pairs}


print("\n=== faculty: no middle initial, no email ===")
values = card(signed_in("faculty.one").get("/settings").get_data(as_text=True))
print("   ", values)
check("the card lists a middle initial row", "Middle initial" in values)
check("an empty middle initial reads Not set", values.get("Middle initial") == "Not set")
check("an empty email reads Not set", values.get("Email") == "Not set")
check("no None and no 'none on file' on the card",
      not any(v in ("None", "none on file") for v in values.values()))
check("the values that are set still show", values.get("First name") == "Fac" and values.get("Last name") == "Ulty")

print("\n=== admin: middle initial and email set ===")
values = card(signed_in("admin").get("/settings").get_data(as_text=True))
check("a set middle initial shows itself", values.get("Middle initial") == "L")
check("a set email shows itself", values.get("Email") == "admin@example.edu")


print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)

os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

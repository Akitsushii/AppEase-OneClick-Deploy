"""Sign-out is called "Log out" everywhere: every submit in a form posting
to /logout reads exactly that, and "Logout" appears nowhere.
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


work = tempfile.mkdtemp(prefix="appease_signout_")
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


class SignOut(html.parser.HTMLParser):
    """The text of every submit button inside a form posting to /logout."""

    def __init__(self):
        super().__init__()
        self.in_form = self.in_button = False
        self.labels, self.text = [], ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("action") == "/logout":
            self.in_form = True
        elif tag == "button" and self.in_form:
            self.in_button, self.text = True, ""

    def handle_endtag(self, tag):
        if tag == "button" and self.in_button:
            self.labels.append(" ".join(self.text.split()))
            self.in_button = False
        elif tag == "form":
            self.in_form = False

    def handle_data(self, data):
        if self.in_button:
            self.text += data


for role, user, pages in (("admin", "admin", ("/", "/computers", "/software", "/settings", "/admins")),
                          ("faculty", "faculty.one", ("/problem-reports", "/notifications", "/settings"))):
    client = signed_in(user)
    for path in pages:
        page = client.get(path).get_data(as_text=True)
        parser = SignOut()
        parser.feed(page)
        check(f"{role} {path}: both account menus offer sign-out", len(parser.labels) == 2)
        check(f"{role} {path}: every sign-out button reads 'Log out' (got {parser.labels})",
              parser.labels and all(label == "Log out" for label in parser.labels))
        check(f"{role} {path}: 'Logout' appears nowhere on the page",
              not any(re.search(r"\bLogout\b", bit) for bit in visible(page)))


print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)

os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

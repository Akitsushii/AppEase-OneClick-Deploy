"""The session cookie is SameSite=Lax: with no CSRF tokens, a page on
another site could otherwise make an admin's browser POST a deployment.
Lax, not Strict, so a link followed in from an email still works. The test
client ignores SameSite; a real browser needs the click-test.
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


work = tempfile.mkdtemp(prefix="appease_cookie_")
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
conn.commit()
conn.close()


def session_cookie_header(res):
    for header in res.headers.getlist("Set-Cookie"):
        if header.startswith("session="):
            return header
    return None


def attributes(header):
    """The cookie's attributes, lowercased, as {name: value}."""
    found = {}
    for part in header.split(";")[1:]:
        name, _, value = part.strip().partition("=")
        found[name.lower()] = value.lower()
    return found


# ---------------------------------------------------------------------------
print("\n=== 1. Sign-in sets a SameSite=Lax session cookie ===")
client = appmod.app.test_client()
res = client.post("/login", data={"username": "admin", "password": PASSWORD})
check("sign-in succeeds", res.status_code == 302 and "login" not in (res.headers.get("Location") or ""))
header = session_cookie_header(res)
check("sign-in sets the session cookie", header is not None)
attrs = attributes(header or "")
check("the session cookie is SameSite=Lax", attrs.get("samesite") == "lax")
check("and still HttpOnly", "httponly" in attrs)


# ---------------------------------------------------------------------------
print("\n=== 2. Signed in, pages and forms still work ===")
check("the dashboard loads", client.get("/").status_code == 200)
res = client.post("/settings", data={"form_type": "update_name", "first_name": "Lax",
                                     "middle_initial": "", "last_name": "Check"})
check("a signed-in form posts and succeeds", "success=" in (res.headers.get("Location") or ""))
conn = sqlite3.connect(database.DB_PATH)
name = conn.execute("SELECT first_name, last_name FROM admins WHERE username = 'admin'").fetchone()
conn.close()
check("and its change is stored", name == ("Lax", "Check"))
header = session_cookie_header(res)
check("a cookie rewritten mid-session keeps SameSite=Lax",
      header is None or attributes(header).get("samesite") == "lax")


# ---------------------------------------------------------------------------
print("\n=== 3. Sign-out still works ===")
res = client.post("/logout")
check("sign-out redirects to sign-in", "login" in (res.headers.get("Location") or ""))
res = client.get("/")
check("after sign-out the dashboard asks for sign-in again",
      res.status_code == 302 and "login" in (res.headers.get("Location") or ""))


# ---------------------------------------------------------------------------
print("\n=== 4. Every form and fetch() posts to its own site ===")
# Lax only withholds the cookie from requests another SITE starts. That can
# only break a form here if one of them posts somewhere else - so this is
# the condition under which the change is harmless, checked rather than
# assumed.
templates = os.path.join(server_dir, "templates")
offsite = []
for name in sorted(os.listdir(templates)):
    if not name.endswith(".html"):
        continue
    text = open(os.path.join(templates, name), encoding="utf-8").read()
    for tag in re.findall(r"<form\b[^>]*>", text, re.I | re.S):
        action = re.search(r'\baction\s*=\s*"([^"]*)"', tag, re.I)
        value = action.group(1).strip() if action else ""
        if value and not (value.startswith("{{") or value.startswith("/")):
            offsite.append(f"{name}: {value}")
scripts = [os.path.join(server_dir, "static", "js", n)
           for n in os.listdir(os.path.join(server_dir, "static", "js")) if n.endswith(".js")]
for path in scripts + [os.path.join(templates, n) for n in os.listdir(templates) if n.endswith(".html")]:
    text = open(path, encoding="utf-8").read()
    for target in re.findall(r"fetch\(\s*['\"]([^'\"]*)", text):
        if not target.startswith("/"):
            offsite.append(f"{os.path.basename(path)}: fetch {target}")
check("no form or fetch() targets another site", offsite == [])
for item in offsite:
    print("   ", item)


print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)

os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

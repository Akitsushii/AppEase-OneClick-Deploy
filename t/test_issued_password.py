"""A temporary password is shown once and never travels in a URL or the
session cookie (Flask's session is signed, NOT encrypted): only a random
one-time id does. A refresh or another admin sees nothing.
"""
import html
import os
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
from urllib.parse import urlparse

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


work = tempfile.mkdtemp(prefix="appease_issued_")
server_dir = os.path.join(work, "server")
shutil.copytree(os.path.join(ROOT, "server"), server_dir, ignore=shutil.ignore_patterns(
    "appease.db*", "__pycache__", "*.pyc", ".env", "uploads", "staging", "report_photos", "avatars", "venv"))
sys.path.insert(0, server_dir)
os.chdir(server_dir)
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
for name in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"):
    os.environ.pop(name, None)

import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()
import app as appmod  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

if not hasattr(appmod, "take_issued_password"):
    print("COULD NOT RUN - app.py predates the one-time password store.")
    sys.exit(3)
appmod.app.config["TESTING"] = True
appmod.run_in_background = lambda target: target()
PASSWORD = "Str0ng!Passw0rd"


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


write("UPDATE admins SET password_hash = ?, must_change_password = 0 WHERE username = 'admin'",
      (generate_password_hash(PASSWORD),))
write("INSERT INTO admins (username, password_hash, role, must_change_password) VALUES ('admin2', ?, 'admin', 0)",
      (generate_password_hash(PASSWORD),))


def signed_in(username):
    client = appmod.app.test_client()
    client.post("/login", data={"username": username, "password": PASSWORD})
    return client


admin = signed_in("admin")
other = signed_in("admin2")
serializer = appmod.app.session_interface.get_signing_serializer(appmod.app)
NAMES = iter(range(1, 100))


def session_cookie(response):
    """The session cookie this response sets, raw - or None."""
    for header in response.headers.getlist("Set-Cookie"):
        if header.startswith("session="):
            return header.split(";", 1)[0][len("session="):]
    return None


def dialog(response):
    """(title, password) from the one-time password dialog, or None."""
    page = response.get_data(as_text=True)
    value = re.search(r'id="adm-password-value">([^<]+)</code>', page)
    if not value:
        return None
    title = re.search(r'id="adm-password-title">(.*?)</h2>', page, re.S).group(1)
    return html.unescape(title), html.unescape(value.group(1))


def create(client=None):
    username = f"tech{next(NAMES):02d}"
    response = (client or admin).post("/admins", data={
        "username": username, "first_name": "Ana", "last_name": "Reyes", "role": "admin", "email": ""})
    return response, username


# ---------------------------------------------------------------------------
print("1. Never in the URL, never in the cookie")
# ---------------------------------------------------------------------------
response, username = create()
location = response.headers.get("Location", "")
check(f"the redirect goes to the Accounts page with nothing in the URL ({location})",
      urlparse(location).path == "/admins" and urlparse(location).query == "")
cookie = session_cookie(response)
held = serializer.loads(cookie) if cookie else {}
check("the session carries only a one-time id", isinstance(held.get("issued_id"), str) and len(held["issued_id"]) >= 20)
page = admin.get("/admins")
shown = dialog(page)
check(f"the Accounts page shows the password ({shown and shown[0]})", shown is not None and username in shown[0])
password = shown[1] if shown else "x" * 12
check("...and it was in neither the URL nor the cookie, raw or decoded",
      password not in location and password not in (cookie or "") and password not in repr(held))
check("...nor in any cookie the page sets", all(password not in h for h in page.headers.getlist("Set-Cookie")))
check("that page is not kept by the browser", page.headers.get("Cache-Control") == "no-store")
check("showing it took the id out of the session", "issued_id" not in serializer.loads(session_cookie(page) or cookie))

# ---------------------------------------------------------------------------
print("2. Once")
# ---------------------------------------------------------------------------
again = admin.get("/admins")
check("a refresh shows nothing", dialog(again) is None and password not in again.get_data(as_text=True))
check("...and is an ordinary, cacheable page again", again.headers.get("Cache-Control") != "no-store")
signer = appmod.app.test_client()
signer.post("/login", data={"username": username, "password": password})
with signer.session_transaction() as sess:
    check("the password shown is the one that signs in", sess.get("admin_username") == username)

response, username = create()
check("another admin's session never sees it", dialog(other.get("/admins")) is None)
check("...the admin who made it still does", dialog(admin.get("/admins")) is not None)

# ---------------------------------------------------------------------------
print("3. Unknown, expired, or forged")
# ---------------------------------------------------------------------------
with admin.session_transaction() as sess:
    sess["issued_id"] = "not-a-real-id"
check("an unknown id shows nothing", dialog(admin.get("/admins")) is None)
appmod.ISSUED_PASSWORD_SECONDS = 0
response, username = create()
check("an expired id shows nothing", dialog(admin.get("/admins")) is None)
# An expired entry nobody came back for must not sit in memory: the next
# hold drops it. (Reading one removes it anyway, so it is left unread here.)
create()
appmod.ISSUED_PASSWORD_SECONDS = 300
create()
check("an expired entry nobody read is dropped by the next hold", len(appmod._issued_passwords) == 1)
check("...and the fresh one still shows", dialog(admin.get("/admins")) is not None)
check("...after which the store is empty", len(appmod._issued_passwords) == 0)
forged = admin.get("/admins?issued_for=admin&issued_password=FORGED-PASSWORD&issued_note=x&issued_new=1")
check("query parameters cannot put a password dialog on the page",
      dialog(forged) is None and "FORGED-PASSWORD" not in forged.get_data(as_text=True))
check("the store keeps nothing for five minutes longer than it should", appmod.ISSUED_PASSWORD_SECONDS == 300)

# ---------------------------------------------------------------------------
print("4. Admin reset, and an email that went")
# ---------------------------------------------------------------------------
target = rows("SELECT id FROM admins WHERE username = 'admin2'")[0]["id"]
response = admin.post(f"/admins/{target}/reset-password")
check("reset: nothing in the URL", urlparse(response.headers.get("Location", "")).query == "")
shown = dialog(admin.get("/admins"))
check(f"reset: shown once, as a reset ({shown and shown[0]})", shown is not None and shown[0] == "Temporary password for admin2")
check("reset: gone on refresh", dialog(admin.get("/admins")) is None)

appmod.SMTP_HOST = "smtp.lab.example"
appmod.send_email = lambda to, subject, body: (True, None)
response = admin.post("/admins", data={"username": "tech90", "first_name": "Ana", "last_name": "Reyes",
                                       "role": "admin", "email": "tech90@lab.example"})
# Read through the client: an unchanged session sets no cookie at all.
with admin.session_transaction() as sess:
    check("email confirmed: nothing held in the session", "issued_id" not in sess)
check("...nothing held on the server", len(appmod._issued_passwords) == 0)
check("...and nothing shown", dialog(admin.get("/admins")) is None)
write("UPDATE admins SET email = 'admin2@lab.example' WHERE id = ?", (target,))
admin.post(f"/admins/{target}/reset-password")
check("a reset that was emailed shows nothing either", dialog(admin.get("/admins")) is None)

print()
print(f"{passed} passed, {len(failed)} failed")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

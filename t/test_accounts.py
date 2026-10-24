"""Tests for the Manage Admins status column and avatars."""
import os
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta

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


work = tempfile.mkdtemp(prefix="appease_accounts_")
server_dir = os.path.join(work, "server")
shutil.copytree(
    os.path.join(ROOT, "server"), server_dir,
    ignore=shutil.ignore_patterns("appease.db*", "__pycache__", "*.pyc", ".env",
                                  "uploads", "staging", "report_photos", "avatars"),
)
sys.path.insert(0, server_dir)
os.chdir(server_dir)

import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()

os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
import app as appmod  # noqa: E402
appmod.app.config["TESTING"] = True


def raw():
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def stamp(seconds_ago):
    return (datetime.utcnow() - timedelta(seconds=seconds_ago)).strftime("%Y-%m-%d %H:%M:%S")


print("\n=== 1. Schema ===")
conn = raw()
cols = {r["name"] for r in conn.execute("PRAGMA table_info(admins)").fetchall()}
conn.close()
check("admins.last_active_at exists", "last_active_at" in cols)
check("admins.avatar_path exists", "avatar_path" in cols)
check("last_login still exists and is separate", "last_login" in cols)
database.init_db()
conn = raw()
again = {r["name"] for r in conn.execute("PRAGMA table_info(admins)").fetchall()}
conn.close()
check("migration is idempotent", again == cols)


print("\n=== 2. Online window ===")
check("just now is online", appmod.admin_is_online(stamp(5)) is True)
check("one minute ago is online", appmod.admin_is_online(stamp(60)) is True)
check("just inside the window is online",
      appmod.admin_is_online(stamp(appmod.ADMIN_ONLINE_SECONDS - 10)) is True)
check("just outside the window is offline",
      appmod.admin_is_online(stamp(appmod.ADMIN_ONLINE_SECONDS + 10)) is False)
check("an hour ago is offline", appmod.admin_is_online(stamp(3600)) is False)
check("NULL is offline", appmod.admin_is_online(None) is False)
check("empty string is offline", appmod.admin_is_online("") is False)
# An unreadable timestamp is not evidence somebody is present.
check("garbage is offline, not online", appmod.admin_is_online("not-a-date") is False)
check("a partial date is offline", appmod.admin_is_online("2026-08-30") is False)
check("sub-second precision still parses",
      appmod.admin_is_online(stamp(5) + ".123456") is True)
check("the window is longer than the write interval, or rows would flicker",
      appmod.ADMIN_ONLINE_SECONDS > appmod.ADMIN_ACTIVITY_WRITE_SECONDS)


print("\n=== 3. The hook records activity ===")
conn = raw()
admin = conn.execute("SELECT id, username FROM admins LIMIT 1").fetchone()
conn.execute("UPDATE admins SET last_active_at = NULL WHERE id = ?", (admin["id"],))
conn.commit()
conn.close()

client = appmod.app.test_client()
with client.session_transaction() as sess:
    sess["admin_id"] = admin["id"]
    sess["admin_role"] = "admin"
    sess["admin_username"] = admin["username"]

client.get("/admins")
conn = raw()
after = conn.execute("SELECT last_active_at FROM admins WHERE id = ?", (admin["id"],)).fetchone()
conn.close()
check("a request stamps last_active_at", after["last_active_at"] is not None)
check("and that stamp reads as online", appmod.admin_is_online(after["last_active_at"]))

# Throttled: a second request inside the window must not rewrite it.
fresh = stamp(10)
conn = raw()
conn.execute("UPDATE admins SET last_active_at = ? WHERE id = ?", (fresh, admin["id"]))
conn.commit()
conn.close()
first = fresh
client.get("/admins")
conn = raw()
second = conn.execute("SELECT last_active_at FROM admins WHERE id = ?", (admin["id"],)).fetchone()
conn.close()
check("a second request does NOT rewrite a fresh stamp",
      second["last_active_at"] == first)

# A stale stamp must be refreshed.
conn = raw()
conn.execute("UPDATE admins SET last_active_at = ? WHERE id = ?",
             (stamp(appmod.ADMIN_ACTIVITY_WRITE_SECONDS + 30), admin["id"]))
conn.commit()
conn.close()
client.get("/admins")
conn = raw()
third = conn.execute("SELECT last_active_at FROM admins WHERE id = ?", (admin["id"],)).fetchone()
conn.close()
check("a stale stamp IS refreshed", appmod.admin_is_online(third["last_active_at"]))

# An anonymous request must not stamp anybody.
conn = raw()
before_anon = conn.execute("SELECT last_active_at FROM admins WHERE id = ?", (admin["id"],)).fetchone()[0]
conn.close()
appmod.app.test_client().get("/api/notifications")
conn = raw()
after_anon = conn.execute("SELECT last_active_at FROM admins WHERE id = ?", (admin["id"],)).fetchone()[0]
conn.close()
check("an anonymous request stamps nobody", after_anon == before_anon)


print("\n=== 4. The page renders ===")
conn = raw()
conn.execute(
    "INSERT INTO admins (username, password_hash, role, first_name, last_name, last_active_at, avatar_path) "
    "VALUES ('online.tech', 'x', 'admin', 'Ona', 'Line', ?, 'avatars/pic.png')", (stamp(30),))
conn.execute(
    "INSERT INTO admins (username, password_hash, role, first_name, last_name, last_active_at) "
    "VALUES ('away.tech', 'x', 'faculty', 'Away', 'Person', ?)", (stamp(7200),))
conn.execute(
    "INSERT INTO admins (username, password_hash, role, first_name, last_name) "
    "VALUES ('never.tech', 'x', 'faculty', 'Never', 'Here')")
conn.commit()
conn.close()

res = client.get("/admins")
html = res.get_data(as_text=True)
check("page renders", res.status_code == 200)
check("Status column present", 'data-dt-label="Status"' in html)
check("Last Seen column present", 'data-dt-label="Last Seen"' in html)
check("Date Created still present", 'data-dt-label="Date Created"' in html)
check("an online account shows Online", ">Online</span>" in html)
check("an idle account shows Offline", ">Offline</span>" in html)
check("an account never seen reads Never", ">Never</span>" in html)

check("the avatar component is used", 'class="avatar shrink-0"' in html)
check("a stored picture is rendered", "avatars/pic.png" in html)
check("initials are present as the fallback", ">OL</span>" in html)
check("a broken image falls back to initials", 'onerror="this.remove()"' in html)

head = len(re.findall(r"<th\b", html.split("</thead>")[0]))
first_row = html.split("<tbody>")[1].split("</tr>")[0]
cells = len(re.findall(r"<td\b", first_row))
check(f"header count == cell count ({head} vs {cells})", head == cells)

check("last seen sorts on the raw UTC value",
      'data-sort="' + stamp(7200)[:13] in html or 'data-sort=""' in html)
check("username still sorts on the bare name, not the avatar markup",
      'data-sort="online.tech"' in html)

anon = appmod.app.test_client()
check("the page still requires login", anon.get("/admins").status_code in (302, 401, 403))

print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)
os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

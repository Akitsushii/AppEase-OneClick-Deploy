"""The default admin's password is in the source, so a NEW install's first
sign-in must set another before anything else is reachable. An EXISTING
database is left exactly as it is.
"""
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


DEFAULT = "AppEase@2026"
CHOSEN = "N3w!Chosen-Passw0rd"

work = tempfile.mkdtemp(prefix="appease_seed_")
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
from werkzeug.security import check_password_hash, generate_password_hash  # noqa: E402
appmod.app.config["TESTING"] = True


def accounts(path=None):
    conn = sqlite3.connect(path or database.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT id, username, password_hash, must_change_password, role FROM admins ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def where(res):
    return res.headers.get("Location") or ""


# ---------------------------------------------------------------------------
print("\n=== 1. A new install's admin must choose a password first ===")
seeded = accounts()
check("a fresh database has exactly one account", len(seeded) == 1)
check("it is the admin account", seeded and seeded[0]["username"] == "admin" and seeded[0]["role"] == "admin")
check("flagged to change its password", seeded and seeded[0]["must_change_password"] == 1)

client = appmod.app.test_client()
res = client.post("/login", data={"username": "admin", "password": DEFAULT})
check("the default password signs in", res.status_code == 302 and "login" not in where(res))
check("straight to the set-password page", "set-password" in where(res))
for path in ("/", "/software", "/admins", "/computers"):
    res = client.get(path)
    check(f"{path} is held at set-password until a new one is chosen", "set-password" in where(res))

res = client.post("/set-password", data={"new_password": DEFAULT, "confirm_password": DEFAULT})
check("the default password cannot be chosen as the new one", "error=" in where(res))

res = client.post("/set-password", data={"new_password": CHOSEN, "confirm_password": CHOSEN})
check("a new password is accepted", "error=" not in where(res) and "set-password" not in where(res))
after = accounts()[0]
check("and stored", check_password_hash(after["password_hash"], CHOSEN))
check("the flag is cleared", after["must_change_password"] == 0)
check("the dashboard now loads", client.get("/").status_code == 200)


# ---------------------------------------------------------------------------
print("\n=== 2. Afterwards: the new password, and only the new password ===")
client.post("/logout")
fresh = appmod.app.test_client()
res = fresh.post("/login", data={"username": "admin", "password": CHOSEN})
check("the new password signs in", res.status_code == 302 and "login" not in where(res))
check("to the dashboard, not set-password", "set-password" not in where(res))
other = appmod.app.test_client()
res = other.post("/login", data={"username": "admin", "password": DEFAULT})
check("the default password no longer works", "set-password" not in where(res)
      and other.get("/").status_code == 302)


# ---------------------------------------------------------------------------
print("\n=== 3. An existing database is left exactly as it is ===")
before = [tuple(row) for row in accounts()]
database.init_db()
check("a restart changes no account", [tuple(row) for row in accounts()] == before)

# An existing install: its seeded admin has the flag at 0 and may still
# hold the default password.
old_db = os.path.join(work, "old.db")
real_path = database.DB_PATH
database.DB_PATH = old_db
try:
    database.init_db()
    conn = sqlite3.connect(old_db)
    conn.execute("UPDATE admins SET must_change_password = 0, password_hash = ? WHERE username = 'admin'",
                 (generate_password_hash(DEFAULT),))
    conn.execute("INSERT INTO admins (username, password_hash, role, must_change_password) "
                 "VALUES ('faculty.one', ?, 'faculty', 0)", (generate_password_hash(CHOSEN),))
    conn.commit()
    conn.close()
    existing = [tuple(row) for row in accounts(old_db)]
    database.init_db()
    database.init_db()
finally:
    database.DB_PATH = real_path
check("an older install's accounts are untouched by the migration",
      [tuple(row) for row in accounts(old_db)] == existing)
check("its admin is NOT newly forced to change password",
      [row["must_change_password"] for row in accounts(old_db)] == [0, 0])
check("and no second admin is seeded", len(accounts(old_db)) == 2)


print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)

os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

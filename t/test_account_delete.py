"""Deleting one account: with one admin a faculty account still deletes,
the last admin never does, and nobody deletes themselves. The last admin is
reached through an account demoted while signed in, which keeps its role.
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


work = tempfile.mkdtemp(prefix="appease_delete_")
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

DB = database.DB_PATH
PASSWORD = "Str0ng!Passw0rd"


def q(sql, args=()):
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, args).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def exists(account_id):
    return bool(q("SELECT 1 FROM admins WHERE id = ?", (account_id,)))


def add(username, role):
    q("INSERT INTO admins (username, password_hash, first_name, last_name, role, must_change_password) "
      "VALUES (?, ?, 'Test', 'User', ?, 0)", (username, generate_password_hash(PASSWORD), role))
    return q("SELECT id FROM admins WHERE username = ?", (username,))[0]["id"]


def signed_in(username):
    client = appmod.app.test_client()
    res = client.post("/login", data={"username": username, "password": PASSWORD})
    assert res.status_code == 302 and "login" not in res.headers.get("Location", ""), res.headers
    return client


def delete(client, account_id):
    res = client.post(f"/admins/{account_id}/delete")
    return res.headers.get("Location") or ""


# The seeded account becomes the one admin, with a known password.
q("UPDATE admins SET password_hash = ?, must_change_password = 0 WHERE username = 'admin'",
  (generate_password_hash(PASSWORD),))
ADMIN = q("SELECT id FROM admins WHERE username = 'admin'")[0]["id"]


# ---------------------------------------------------------------------------
print("\n=== 1. One admin deletes a faculty account ===")
check("exactly one admin to start with", len(q("SELECT id FROM admins WHERE role = 'admin'")) == 1)
faculty = add("faculty.one", "faculty")
admin = signed_in("admin")
where = delete(admin, faculty)
check("the faculty account is gone", not exists(faculty))
check("and the page says so", "success=" in where)
check("the deletion is in the audit trail",
      bool(q("SELECT 1 FROM audit_log WHERE action_type = 'account_deleted' AND target_admin_id = ?",
             (faculty,))))
check("the one admin is untouched", exists(ADMIN))


# ---------------------------------------------------------------------------
print("\n=== 2. Nobody deletes themselves ===")
# With a second admin present, so the last-admin guard cannot be the thing
# that refuses it.
second = add("admin.two", "admin")
where = delete(admin, ADMIN)
check("deleting your own account is refused", "error=" in where)
check("with the reason", "own+account" in where or "own%20account" in where)
check("and you still exist", exists(ADMIN))


# ---------------------------------------------------------------------------
print("\n=== 3. With two admins, either can delete the other ===")
where = delete(admin, second)
check("the other admin is deleted", not exists(second))
check("with a success message", "success=" in where)


# ---------------------------------------------------------------------------
print("\n=== 4. The last admin can never be deleted ===")
demoted = add("admin.three", "admin")
stale = signed_in("admin.three")
# Demoted in the database while signed in. The session still says admin,
# so this account can reach the delete route - and the account it targets
# is now the only admin there is.
q("UPDATE admins SET role = 'faculty' WHERE id = ?", (demoted,))
check("one admin left in the database", len(q("SELECT id FROM admins WHERE role = 'admin'")) == 1)
where = delete(stale, ADMIN)
check("deleting the last admin is refused", "error=" in where)
check("with the reason", "only+admin" in where or "only%20admin" in where)
check("and the last admin still exists", exists(ADMIN))


print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)

os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

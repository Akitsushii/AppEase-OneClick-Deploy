"""A page is called one thing: its tab title, breadcrumb, card heading and
print title match the sidebar entry highlighted on that page. "Current
Accounts" stays: it describes the list, not the page.
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


work = tempfile.mkdtemp(prefix="appease_names_")
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


def signed_in(username):
    client = appmod.app.test_client()
    client.post("/login", data={"username": username, "password": PASSWORD})
    return client


def names(page):
    active = re.findall(r'data-active="true"[^>]*>(?:(?!</a>).)*?<span class="sb-label">([^<]+)</span>', page, re.S)
    title = re.search(r"<title>([^<]*)</title>", page)
    crumb = re.search(r'<span aria-current="page">([^<]*)</span>', page)
    card = re.search(r'<h3 class="card-title">([^<]*)</h3>', page)
    printed = re.search(r'data-print-title="AppEase - ([^"]*)"', page)
    return {
        "sidebar": active[-1].strip() if active else None,
        "tab": re.sub(r"\s+-\s+AppEase$", "", title.group(1).strip()) if title else None,
        "crumb": crumb.group(1).strip() if crumb else None,
        "card": card.group(1).strip() if card else None,
        "print": printed.group(1).strip() if printed else None,
    }


for role, user, pages in (("admin", "admin", ("/notifications", "/admins/audit-trail")),
                          ("faculty", "faculty.one", ("/notifications",))):
    client = signed_in(user)
    for path in pages:
        print(f"\n=== {role} {path} ===")
        n = names(client.get(path).get_data(as_text=True))
        print("   ", n)
        check(f"{role} {path}: the sidebar highlights an entry", bool(n["sidebar"]))
        for where in ("tab", "crumb", "card", "print"):
            check(f"{role} {path}: {where} uses the sidebar's name {n['sidebar']!r} (got {n[where]!r})",
                  n[where] == n["sidebar"])
    if role == "admin":
        n = names(client.get("/notifications").get_data(as_text=True))
        check("admin /notifications: the sidebar's name is Notifications", n["sidebar"] == "Notifications")
        check("admin /notifications: History is gone from every name",
              not any("History" in (v or "") for v in n.values()))
        n = names(client.get("/admins").get_data(as_text=True))
        check("admin /admins: tab, crumb and sidebar say Accounts",
              n["tab"] == n["crumb"] == n["sidebar"] == "Accounts")
        check("admin /admins: its card still describes the list as Current Accounts", n["card"] == "Current Accounts")


print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)

os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

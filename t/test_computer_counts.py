"""How many computers the lab has. Retired machines count as neither
computers nor online and are counted on their own; the pill, its poll and
the dashboard card all read computer_counts(), so they cannot disagree.

Not reachable here: notifications.js rebuilding the pill's text, which must
match base.html's first paint (compared with Node by hand).
"""
import html
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


def arg_path(name, default):
    for index, arg in enumerate(sys.argv):
        if arg == name and index + 1 < len(sys.argv):
            return os.path.abspath(sys.argv[index + 1])
        if arg.startswith(name + "="):
            return os.path.abspath(arg.split("=", 1)[1])
    return default


SERVER_SRC = arg_path("--server", os.path.join(ROOT, "server"))
work = tempfile.mkdtemp(prefix="appease_counts_")
server_dir = os.path.join(work, "server")
shutil.copytree(
    SERVER_SRC, server_dir,
    ignore=shutil.ignore_patterns(
        "appease.db*", "__pycache__", "*.pyc", ".env",
        "uploads", "staging", "report_photos", "avatars", "venv",
    ),
)
sys.path.insert(0, server_dir)
os.chdir(server_dir)

import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
import app as appmod  # noqa: E402

if not hasattr(appmod, "computer_counts"):
    print("COULD NOT RUN - app.py predates computer_counts().")
    sys.exit(3)


def raw():
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def set_computers(rows):
    """rows: (hostname, status, asset_status). Replaces the whole table."""
    conn = raw()
    conn.execute("DELETE FROM computers")
    for hostname, status, asset in rows:
        conn.execute(
            "INSERT INTO computers (hostname, status, asset_status, last_seen) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (hostname, status, asset))
    conn.commit()
    conn.close()


def counts():
    with appmod.app.app_context():
        return appmod.computer_counts(appmod.get_db())


conn = raw()
admin = conn.execute("SELECT id, username FROM admins LIMIT 1").fetchone()
conn.close()


def client_as(role):
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["admin_id"] = admin["id"]
        sess["admin_role"] = role
        sess["admin_username"] = admin["username"]
    return c


admin_client = client_as("admin")


def text_of(fragment):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", fragment)).split())


def pill(body):
    m = re.search(r'<span id="agent-status-text">(.*?)</span>', body, re.S)
    return text_of(m.group(1)) if m else None


def card(body):
    """(number, footer text) from the Total Computers card."""
    m = re.search(r"Total Computers</p>\s*<h3[^>]*>(.*?)</h3>.*?<footer[^>]*>(.*?)</footer>", body, re.S)
    return (text_of(m.group(1)), text_of(m.group(2))) if m else None


# Four computers the lab has, and two it has retired, one still polling.
LAB = [
    ("LAB-A", "online", None),
    ("LAB-B", "online", "In repair"),
    ("LAB-C", "offline", "In use"),
    ("LAB-D", "offline", None),
    ("GONE-POLLING", "online", "Retired"),
    ("GONE-QUIET", "offline", "Retired"),
]


print("\n=== 1. The one count ===")
set_computers(LAB)
check("retired machines are not computers the lab has", counts()["total"] == 4)
check("a retired machine that still polls is not counted online", counts()["online"] == 2)
check("retired machines are counted on their own", counts()["retired"] == 2)
check("'In repair' is still a computer, and still online", counts() == {"total": 4, "online": 2, "retired": 2})

set_computers([])
check("an empty table counts zeros, not None", counts() == {"total": 0, "online": 0, "retired": 0})


print("\n=== 2. The dashboard card ===")
set_computers(LAB)
body = admin_client.get("/").get_data(as_text=True)
check("the card counts the computers the lab has", card(body) and card(body)[0] == "4")
check("the card says how many are online and how many are retired",
      card(body) and card(body)[1] == "2 online now · 2 retired")

set_computers([r for r in LAB if r[2] != "Retired"])
body = admin_client.get("/").get_data(as_text=True)
check("with nothing retired the card does not mention retired at all",
      card(body) and card(body)[1] == "2 online now")


print("\n=== 3. The topbar pill, first paint and poll ===")
set_computers(LAB)
body = admin_client.get("/").get_data(as_text=True)
check("the pill's first paint agrees with the card", pill(body) == "2 of 4 computers online · 2 retired")
body = admin_client.get("/computers").get_data(as_text=True)
check("...on every admin page, not just the dashboard", pill(body) == "2 of 4 computers online · 2 retired")
res = admin_client.get("/api/notifications")
check("the poll sends the same three numbers", res.get_json().get("agents") == {"total": 4, "online": 2, "retired": 2})

set_computers([("LAB-A", "online", None), ("GONE-QUIET", "offline", "Retired")])
body = admin_client.get("/").get_data(as_text=True)
check("one computer reads singular", pill(body) == "1 of 1 computer online · 1 retired")

set_computers([r for r in LAB if r[2] != "Retired"])
body = admin_client.get("/").get_data(as_text=True)
check("with nothing retired the pill does not say '0 retired'", pill(body) == "2 of 4 computers online")

set_computers([])
body = admin_client.get("/").get_data(as_text=True)
check("an empty table paints '0 of 0 computers online'", pill(body) == "0 of 0 computers online")

set_computers(LAB)
faculty = client_as("faculty")
# A page faculty can really open, proven by the bell base.html renders for
# them - otherwise a redirect would make "no pill" pass on an empty body.
faculty_page = None
for path in ("/problem-reports", "/software", "/settings", "/notifications"):
    res = faculty.get(path)
    if res.status_code == 200 and 'id="notif-bell"' in res.get_data(as_text=True):
        faculty_page = res.get_data(as_text=True)
        break
check("a faculty page renders the shared layout (so the next check means something)", faculty_page is not None)
check("faculty still get no pill", faculty_page is not None and pill(faculty_page) is None)
check("faculty still get no counts from the poll", faculty.get("/api/notifications").get_json().get("agents") is None)


print("\n=== 4. What retiring does NOT change ===")
set_computers(LAB)
body = admin_client.get("/computers").get_data(as_text=True)
check("Lab Computers still lists every row it has registered", "6 registered." in body)
res = appmod.app.test_client().post("/api/agent/poll", json={"computer_hostname": "GONE-POLLING"})
conn = raw()
row = conn.execute("SELECT status, asset_status FROM computers WHERE hostname = 'GONE-POLLING'").fetchone()
conn.close()
check("a retired machine's agent can still poll", res.status_code == 200)
check("...its row still reads online, which is true", row["status"] == "online")
check("...and polling cannot un-retire it", row["asset_status"] == "Retired")
check("...and it is still not counted", counts() == {"total": 4, "online": 2, "retired": 2})


print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)
os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

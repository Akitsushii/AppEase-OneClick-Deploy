"""The dashboard's three deployment numbers agree: the card, the donut's
centre and Success Rate's "of N" all count the same finished jobs, installs
and uninstalls alike; a pending, installing or cancelled job moves none.
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


work = tempfile.mkdtemp(prefix="appease_totals_")
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
DB = database.DB_PATH


def q(sql, args=()):
    conn = sqlite3.connect(DB)
    try:
        rows = conn.execute(sql, args).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


q("UPDATE admins SET password_hash = ?, must_change_password = 0 WHERE username = 'admin'",
  (generate_password_hash(PASSWORD),))
SOFTWARE = q("INSERT INTO software (name, version, type, install_command) "
             "VALUES ('Probe', '1', 'online', 'choco install probe -y') RETURNING id")[0][0]


def job(status, age_days=0, action=None):
    finished = status in ("completed", "failed")
    q("INSERT INTO jobs (computer_hostname, software_id, status, software_name, software_version, action, "
      "created_at, completed_at) VALUES ('LAB-PC', ?, ?, 'Probe', '1', ?, DATETIME('now', ?), "
      + ("DATETIME('now', ?))" if finished else "NULL)"),
      (SOFTWARE, status, action, f"-{age_days} days") + ((f"-{age_days} days",) if finished else ()))


client = appmod.app.test_client()
client.post("/login", data={"username": "admin", "password": PASSWORD})


def numbers():
    page = client.get("/").get_data(as_text=True)
    card = re.search(r'card-description">Total Deployments</p>\s*<h3[^>]*>\s*(\d+)\s*</h3>', page)
    donut = re.search(r'<div class="skin-value text-3xl tabular-nums text-ink" data-countup>\s*(\d+)\s*</div>', page)
    rate = re.search(r'(\d+) of (\d+) deployments', page)
    footer = re.search(r'Total Deployments</p>.*?<footer[^>]*>(.*?)</footer>', page, re.S)
    lines = [t.strip() for t in re.findall(r'<p[^>]*>([^<]*)</p>', footer.group(1))] if footer else []
    return {
        "card": int(card.group(1)) if card else None,
        "donut": int(donut.group(1)) if donut else None,
        "rate_of": int(rate.group(2)) if rate else None,
        "rate_ok": int(rate.group(1)) if rate else None,
        "breakdown": lines[0] if len(lines) == 2 else None,
        "subtitle": lines[-1] if lines else None,
    }


# ---------------------------------------------------------------------------
print("\n=== 1. Finished jobs only, all three the same ===")
for status in ("completed", "completed", "failed", "in_progress", "pending", "cancelled"):
    job(status)
n = numbers()
print("   ", n)
check("all three numbers were found on the page", None not in (n["card"], n["donut"], n["rate_of"]))
check("the card counts the finished jobs: 3", n["card"] == 3)
check("the donut's centre says 3", n["donut"] == 3)
check("Success Rate is out of 3", n["rate_of"] == 3 and n["rate_ok"] == 2)
check("card, donut and Success Rate agree", n["card"] == n["donut"] == n["rate_of"])

before = numbers()
job("in_progress")
job("pending")
job("cancelled")
after = numbers()
check("jobs pending, installing or cancelled move none of the three",
      (after["card"], after["donut"], after["rate_of"]) == (before["card"], before["donut"], before["rate_of"]))


# ---------------------------------------------------------------------------
print("\n=== 2. An old job outside the donut's range ===")
job("completed", age_days=30)
n = numbers()
print("   ", n)
check("the card counts it (all time)", n["card"] == 4)
check("Success Rate counts it (all time)", n["rate_of"] == 4)
check("the card and Success Rate still agree", n["card"] == n["rate_of"])
check("the donut shows only its 7-day range", n["donut"] == 3)


# ---------------------------------------------------------------------------
print("\n=== 3. The subtitle says what the card counts ===")
subtitle = (n["subtitle"] or "").lower()
check("it no longer claims only to exclude pending and cancelled", "excludes pending" not in subtitle)
check("it says finished: completed or failed", "completed" in subtitle and "failed" in subtitle)
check("it says all time", "all time" in subtitle)


print("\n=== 3b. Uninstalls are deployments too ===")
check(f"installs only so far: the card says so ({n['breakdown']})", n["breakdown"] == "4 installs &middot; 0 uninstalls")
before = numbers()
job("completed", action="uninstall")
job("failed", action="uninstall")
job("pending", action="uninstall")
job("in_progress", action="uninstall")
job("cancelled", action="uninstall")
n = numbers()
print("   ", n)
check("a finished uninstall counts in the card", n["card"] == before["card"] + 2)
check("...in the donut", n["donut"] == before["donut"] + 2)
check("...and in Success Rate, the failed one included", (n["rate_ok"], n["rate_of"])
      == (before["rate_ok"] + 1, before["rate_of"] + 2))
check("card and Success Rate still agree", n["card"] == n["rate_of"])
check(f"the card says how many of each ({n['breakdown']})", n["breakdown"] == "4 installs &middot; 2 uninstalls")
check("...above what it counts", n["subtitle"] == "Completed or failed, all time")
q("DELETE FROM jobs")
job("completed")
job("completed", action="uninstall")
check(f"one of each reads in the singular ({numbers()['breakdown']})",
      numbers()["breakdown"] == "1 install &middot; 1 uninstall")


print("\n=== 4. The cards' links to their full pages read \"More\" ===")
page = client.get("/").get_data(as_text=True)
for card, target in (("Tasks", "/problem-reports"), ("Recent Activity", "/admins/audit-trail")):
    header = re.search(r'card-title">' + card + r'</h3>.*?</header>', page, re.S)
    link = re.search(r'<a [^>]*>[^<]*</a>', header.group(0)) if header else None
    link = link.group(0) if link else ""
    check(f"{card}: the button says More", link.endswith(">More</a>"))
    check(f"{card}: same look and the same page as before",
          'class="btn" data-variant="outline" data-size="sm"' in link and f'href="{target}"' in link)
check("no card says View all any more", "View all" not in page)

print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)

os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

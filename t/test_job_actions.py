"""Installs and uninstalls in one jobs table: an existing job reads as an
install, a job whose software left the library (software_id 0) is named by
its snapshot everywhere, and a retry keeps an uninstall an uninstall.
"""
import json
import os
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
from html import unescape

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


work = tempfile.mkdtemp(prefix="appease_jobactions_")
server_dir = os.path.join(work, "server")
shutil.copytree(os.path.join(ROOT, "server"), server_dir, ignore=shutil.ignore_patterns(
    "appease.db*", "__pycache__", "*.pyc", ".env", "uploads", "staging", "report_photos", "avatars", "venv"))
sys.path.insert(0, server_dir)
os.chdir(server_dir)
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))

import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()
import app as appmod  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

if not hasattr(appmod, "JOB_ACTION_UNINSTALL"):
    print("COULD NOT RUN - app.py predates the job action.")
    sys.exit(3)
appmod.app.config["TESTING"] = True
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
client = appmod.app.test_client()
client.post("/login", data={"username": "admin", "password": PASSWORD})

# The dashboard's numbers, as handed to the template. Active Jobs and the
# sparklines are computed but not drawn, so the page alone cannot show them.
seen = {}
real_render = appmod.render_template


def capture(name, **context):
    if name == "dashboard.html":
        seen.clear()
        seen.update(context)
    return real_render(name, **context)


appmod.render_template = capture


def dashboard():
    return client.get("/").get_data(as_text=True)


def job(software_id, status, action=None, installation_id=None, name="Probe", version="1", host="LAB-PC",
        batch=None, age="-0 days"):
    finished = status in ("completed", "failed")
    return write(
        "INSERT INTO jobs (computer_hostname, software_id, status, software_name, software_version, action, "
        "installation_id, batch_id, created_at, completed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, DATETIME('now', ?), " + ("DATETIME('now', ?))" if finished else "NULL)"),
        (host, software_id, status, name, version, action, installation_id, batch, age)
        + ((age,) if finished else ()))


def installation(computer_id, name, state="installed", software_id=None, key=None):
    return write(
        "INSERT INTO installations (computer_id, software_id, software_name, software_version, method, "
        "record_key, msi_product_code, state) VALUES (?, ?, ?, '24.08', 'msi', ?, ?, ?)",
        # An installed MSI row always has its ProductCode; one without is
        # never handed out.
        (computer_id, software_id, name, key or f"msi:{{{name}}}",
         key or "{11111111-2222-3333-4444-555555555555}", state))


UNINSTALL = appmod.JOB_ACTION_UNINSTALL
NO_LIBRARY = appmod.NO_LIBRARY_SOFTWARE_ID

# ---------------------------------------------------------------------------
print("1. The columns, and software id 0")
# ---------------------------------------------------------------------------
columns = {r["name"] for r in rows("PRAGMA table_info(jobs)")}
check("jobs has action and installation_id", {"action", "installation_id"} <= columns)
check("NO_LIBRARY_SOFTWARE_ID is 0", NO_LIBRARY == 0)
old = job(999, "completed")
check("a job written without an action is an install (NULL), with no backfill needed",
      rows("SELECT action FROM jobs WHERE id = ?", (old,))[0]["action"] is None)
write("DELETE FROM jobs")


def refused(sql, args=()):
    try:
        write(sql, args)
    except sqlite3.IntegrityError as exc:
        return "software id 0 is reserved" in str(exc)
    return False


check("inserting a software row with id 0 is refused",
      refused("INSERT INTO software (id, name, type, install_command) VALUES (0, 'Zero', 'x', 'x')"))
SOFTWARE = write("INSERT INTO software (name, version, type, install_command) "
                 "VALUES ('Probe', '1', 'online', 'choco install probe -y')")
check("...an ordinary insert is not, and never gets id 0", SOFTWARE >= 1)
check("changing a software row's id to 0 is refused",
      refused("UPDATE software SET id = 0 WHERE id = ?", (SOFTWARE,)))
check("...and the row keeps its id", rows("SELECT id FROM software WHERE name = 'Probe'")[0]["id"] == SOFTWARE)
write("UPDATE software SET name = 'Probe' WHERE id = ?", (SOFTWARE,))
check("an update that leaves the id alone is not refused",
      rows("SELECT COUNT(*) AS n FROM software WHERE id = ?", (SOFTWARE,))[0]["n"] == 1)
# Online and able to uninstall MSI: an uninstall is queued, retried or kept
# pending only for a PC like that.
write("INSERT INTO computers (hostname, status, last_seen, capabilities) "
      "VALUES ('LAB-PC', 'online', CURRENT_TIMESTAMP, 'uninstall:msi')")
PC = rows("SELECT id FROM computers WHERE hostname = 'LAB-PC'")[0]["id"]

# ---------------------------------------------------------------------------
print("2. The dashboard counts installs and uninstalls (since 2026-10-08)")
# ---------------------------------------------------------------------------


def numbers(page):
    card = re.search(r'card-description">Total Deployments</p>\s*<h3[^>]*>\s*(\d+)\s*</h3>', page)
    donut = re.search(r'<div class="skin-value text-3xl tabular-nums text-ink" data-countup>\s*(\d+)\s*</div>', page)
    rate = re.search(r'(\d+) of (\d+) deployments', page)
    return (int(card.group(1)) if card else None, int(donut.group(1)) if donut else None,
            (int(rate.group(1)), int(rate.group(2))) if rate else None)


for status in ("completed", "completed", "failed", "pending"):
    job(SOFTWARE, status)
page = dashboard()
before = numbers(page)
before_active = seen.get("active_jobs_count")
before_spark = list(seen["kpi_sparklines"]["jobs"])
before_chart = [(e["completed"], e["failed"]) for e in seen["activity_chart"]]
check("installs alone: card 3, donut 3, Success Rate 2 of 3", before == (3, 3, (2, 3)))
check("...Active Jobs 1", before_active == 1)

GONE = installation(PC, "Gone App", state="installed")
job(NO_LIBRARY, "completed", UNINSTALL, GONE, name="Gone App")
job(NO_LIBRARY, "failed", UNINSTALL, GONE, name="Gone App")
job(SOFTWARE, "completed", UNINSTALL, GONE, name="Probe")
job(SOFTWARE, "pending", UNINSTALL, GONE, name="Probe")
job(NO_LIBRARY, "in_progress", UNINSTALL, GONE, name="Gone App")
page = dashboard()
# 3 finished installs (2 completed, 1 failed) + 3 finished uninstalls
# (2 completed, 1 failed); the pending and in-progress ones count nowhere.
check(f"finished uninstalls count in card, donut and Success Rate: 6, 6, 4 of 6 ({numbers(page)})",
      numbers(page) == (6, 6, (4, 6)))
after_chart = [(e["completed"], e["failed"]) for e in seen["activity_chart"]]
check("...and in the activity chart's bars: 2 more completed, 1 more failed",
      (sum(c for c, _ in after_chart), sum(f for _, f in after_chart))
      == (sum(c for c, _ in before_chart) + 2, sum(f for _, f in before_chart) + 1))
check("...and in the jobs sparkline: all 5 started today",
      seen["kpi_sparklines"]["jobs"][:-1] == before_spark[:-1]
      and seen["kpi_sparklines"]["jobs"][-1] == before_spark[-1] + 5)
check("the card says how many of each: 3 installs, 3 uninstalls",
      "3 installs &middot; 3 uninstalls" in page)
check("Active Jobs counts both: 1 install + 2 uninstalls", seen.get("active_jobs_count") == 3)
check("the jobs table lists every job, uninstalls included", len(seen["recent_jobs"]) == 9)
write("DELETE FROM jobs")
job(NO_LIBRARY, "completed", UNINSTALL, GONE, name="Gone App", age="-20 days")
job(SOFTWARE, "completed", age="-2 days")
dashboard()
check("the chart's earliest day is the oldest job, an older uninstall included",
      seen["chart_min_day"] == rows("SELECT date(DATETIME('now', '-20 days'), '+8 hours') AS d")[0]["d"])

# ---------------------------------------------------------------------------
print("3. The jobs table and Job Details")
# ---------------------------------------------------------------------------
write("DELETE FROM jobs")
DELETED = write("INSERT INTO software (name, version, type, install_command) VALUES ('Deleted', '2', 'x', 'x')")
write("DELETE FROM software WHERE id = ?", (DELETED,))
J_INSTALL = job(SOFTWARE, "failed", name="Probe")
J_INSTALL_GONE = job(DELETED, "failed", name="Deleted", version="2")
J_UNINSTALL = job(NO_LIBRARY, "failed", UNINSTALL, GONE, name="Gone App", version="24.08")
J_UNINSTALL_LIB = job(SOFTWARE, "cancelled", UNINSTALL, GONE, name="Probe")
page = dashboard()


def row_html(job_id):
    match = re.search(r'<tr data-dt-id="%d".*?</tr>' % job_id, page, re.S)
    return match.group(0) if match else ""


def cells(job_id):
    return [re.sub(r"<[^>]+>", " ", c).split() for c in re.findall(r"<td[^>]*>(.*?)</td>", row_html(job_id), re.S)]


def details(job_id):
    match = re.search(r"data-job-details='([^']*)'", row_html(job_id))
    return json.loads(unescape(match.group(1))) if match else {}


head = re.search(r"<thead>(.*?)</thead>", page, re.S).group(1)
labels = re.findall(r'data-dt-label="([^"]+)"', head)
check("the table has a Type column, beside Software, filterable",
      labels[labels.index("Software") + 1] == "Type"
      and re.search(r'<th data-dt-sort data-dt-facet data-dt-label="Type">Type</th>', head) is not None)
type_at = labels.index("Type") + 1  # the select column has no data-dt-label
check("an install's row says Install", cells(J_INSTALL)[type_at] == ["Install"])
check("an uninstall's row says Uninstall", cells(J_UNINSTALL)[type_at] == ["Uninstall"])
check("software_id 0 is named by its snapshot in the table",
      cells(J_UNINSTALL)[labels.index("Software") + 1] == ["Gone", "App"])
check("an install whose software was deleted keeps its Removed pill", ">Removed</span>" in row_html(J_INSTALL_GONE))
check("an uninstall never shows the library's Removed pill, software_id 0 or not",
      ">Removed</span>" not in row_html(J_UNINSTALL) and ">Removed</span>" not in row_html(J_UNINSTALL_LIB))
check("Job Details says Install for an install", details(J_INSTALL).get("action") == "Install")
check("Job Details says Uninstall for an uninstall", details(J_UNINSTALL).get("action") == "Uninstall")
check("Job Details names software_id 0 by its snapshot", details(J_UNINSTALL).get("software") == "Gone App 24.08")
check("an action nothing writes is shown as nothing, never as Install", appmod.job_action_label("reinstall") == "")
panel = re.search(r'<div id="job-details-overlay".*?</script>', page, re.S).group(0)
check("the panel has a Type row the script fills",
      re.search(r'<dt>Type</dt>\s*<dd data-field="action"></dd>', panel) is not None
      and "put('action', details.action);" in panel)

# ---------------------------------------------------------------------------
print("4. Retry keeps an uninstall an uninstall")
# ---------------------------------------------------------------------------
check("Retry is offered on an install whose software exists", "data-dt-retry-url" in row_html(J_INSTALL))
check("...not on one whose software was deleted", "data-dt-retry-url" not in row_html(J_INSTALL_GONE))
check("...and on an uninstall with software_id 0 while its row is installed",
      "data-dt-retry-url" in row_html(J_UNINSTALL))


def newest():
    return rows("SELECT * FROM jobs ORDER BY id DESC LIMIT 1")[0]


client.post(f"/jobs/{J_UNINSTALL}/retry")
again = newest()
check("retrying an uninstall queues a new pending uninstall",
      again["id"] > J_UNINSTALL and again["status"] == "pending" and again["action"] == UNINSTALL)
check("...for the same installations row and computer",
      again["installation_id"] == GONE and again["computer_hostname"] == "LAB-PC")
check("...keeping software_id 0 and its own snapshot",
      again["software_id"] == 0 and (again["software_name"], again["software_version"]) == ("Gone App", "24.08"))
# One uninstall of a row at a time: clear the queue between retries.
write("DELETE FROM jobs WHERE status = 'pending'")
write("UPDATE software SET name = 'Probe Renamed' WHERE id = ?", (SOFTWARE,))
client.post(f"/jobs/{J_UNINSTALL_LIB}/retry")
again = newest()
check("an uninstall whose software is in the library keeps its own snapshot, not the library's new name",
      again["action"] == UNINSTALL and again["software_name"] == "Probe" and again["software_id"] == SOFTWARE)
write("DELETE FROM jobs WHERE status = 'pending'")
client.post(f"/jobs/{J_INSTALL}/retry")
again = newest()
check("retrying an install still queues an install, under the library's name now",
      again["action"] is None and again["installation_id"] is None and again["software_name"] == "Probe Renamed")
count = len(rows("SELECT id FROM jobs"))
reply = client.post(f"/jobs/{J_INSTALL_GONE}/retry")
check("an install whose software was deleted is still refused",
      len(rows("SELECT id FROM jobs")) == count and "Software+no+longer+exists" in reply.headers.get("Location", ""))

write("UPDATE installations SET state = 'removed' WHERE id = ?", (GONE,))
page = dashboard()
check("no Retry on an uninstall once its row is no longer installed", "data-dt-retry-url" not in row_html(J_UNINSTALL))
reply = client.post(f"/jobs/{J_UNINSTALL}/retry")
check("...and the route refuses it, saying why",
      len(rows("SELECT id FROM jobs")) == count and "no+longer+installed" in reply.headers.get("Location", ""))
write("UPDATE installations SET state = 'installed' WHERE id = ?", (GONE,))

write("DELETE FROM jobs WHERE status = 'pending'")
count = len(rows("SELECT id FROM jobs"))
reply = client.post("/jobs/bulk/retry", data={"job_ids": [J_INSTALL, J_INSTALL_GONE, J_UNINSTALL]})
queued = rows("SELECT * FROM jobs WHERE status = 'pending' ORDER BY id")
check("bulk retry queues two and skips the deleted install",
      len(queued) == 2 and len(rows("SELECT id FROM jobs")) == count + 2
      and "2+jobs+re-queued.+1+skipped+(removed+software)" in reply.headers.get("Location", ""))
check("...the uninstall stays an uninstall of the same row, software_id 0 included",
      sorted((r["action"] or "", r["installation_id"] or 0, r["software_id"]) for r in queued)
      == sorted([("", 0, SOFTWARE), (UNINSTALL, GONE, 0)]))
write("DELETE FROM jobs WHERE status = 'pending'")
reply = client.post("/jobs/bulk/retry", data={"job_ids": [J_UNINSTALL, J_UNINSTALL_LIB]})
queued = rows("SELECT * FROM jobs WHERE status = 'pending'")
check("two selected attempts at one row's uninstall queue it once",
      len(queued) == 1 and queued[0]["action"] == UNINSTALL and queued[0]["installation_id"] == GONE
      and "1+job+re-queued.+1+skipped" in reply.headers.get("Location", ""))
write("UPDATE installations SET state = 'replaced' WHERE id = ?", (GONE,))
write("DELETE FROM jobs WHERE status = 'pending'")
reply = client.post("/jobs/bulk/retry", data={"job_ids": [J_UNINSTALL, J_UNINSTALL_LIB]})
check("bulk retry queues no uninstall once its row is no longer installed",
      not rows("SELECT id FROM jobs WHERE status = 'pending'")
      and "None+of+the+selected+jobs+could+be+retried" in reply.headers.get("Location", ""))
write("UPDATE installations SET state = 'installed' WHERE id = ?", (GONE,))

# ---------------------------------------------------------------------------
print("5. The install poll never hands out an uninstall as an install")
# ---------------------------------------------------------------------------
write("DELETE FROM jobs")
first = job(SOFTWARE, "pending", UNINSTALL, GONE, age="-10 minutes")
zero = job(NO_LIBRARY, "pending", UNINSTALL, GONE, name="Gone App", age="-9 minutes")
install = job(SOFTWARE, "pending", age="-1 minutes")
got = client.post("/api/agent/poll", json={"computer_hostname": "LAB-PC"}).get_json()["job"]
check("an older pending uninstall is skipped: the poll hands out the install",
      got is not None and got["id"] == install)
got = client.post("/api/agent/poll", json={"computer_hostname": "LAB-PC"}).get_json()["job"]
check("with only uninstalls pending, the poll hands out nothing", got is None)
# This poll sends no capabilities, so the uninstalls are cancelled on the
# way rather than left waiting.
check("...and both are cancelled as uninstalls this agent cannot run",
      [(r["status"], r["result_reason"]) for r in
       rows("SELECT status, result_reason FROM jobs WHERE id IN (?, ?) ORDER BY id", (first, zero))]
      == [("cancelled", "agent_cannot_uninstall")] * 2)

# ---------------------------------------------------------------------------
print("6. Notifications and progress name software_id 0 by its snapshot")
# ---------------------------------------------------------------------------
write("DELETE FROM jobs")
write("DELETE FROM notifications")
batch = "b" * 32
one = job(NO_LIBRARY, "in_progress", UNINSTALL, GONE, name="Gone App", version="24.08", batch=batch)
job(NO_LIBRARY, "completed", UNINSTALL, GONE, name="Gone App", version="24.08", batch=batch)
write("UPDATE jobs SET started_at = CURRENT_TIMESTAMP WHERE id = ?", (one,))
progress = client.get(f"/api/deployments/{batch}/progress").get_json()
check("deploy progress groups software_id 0 under its snapshot",
      [g["label"] for g in progress["groups"]] == ["Gone App 24.08"])
record = {"method": "msi", "product_code": "{11111111-2222-3333-4444-555555555555}", "display_name": "Gone App"}
res = client.post(f"/api/agent/jobs/{one}/report", json={
    "computer_hostname": "LAB-PC", "status": "failed", "exit_code": 1603, "install_record": record})
check("a failed report on it is accepted", res.status_code == 200)
messages = [r["message"] for r in rows("SELECT message FROM notifications ORDER BY id")]
check("the failure notification names it by its snapshot",
      any(m.startswith("Uninstall failed: Gone App on LAB-PC") for m in messages))
check("...and the batch-complete notification follows", any("finished" in m and "1 of 2" in m for m in messages))
check("an install record riding on a failed uninstall report is never written",
      res.get_json().get("install_record") == "ignored"
      and rows("SELECT COUNT(*) AS n FROM installations")[0]["n"] == 1)
# The same valid record, on a completed job whose software is in the library
# and whose command matches it: stored for an install, never for an uninstall.
PROBE = {"method": "choco", "package_id": "probe", "display_name": "Probe", "display_version": "1"}
for action, expected, label in ((UNINSTALL, ("ignored", 1), "never written for a completed uninstall either"),
                                (None, ("stored", 2), "...while on an install it is stored (the record is valid)")):
    running = job(SOFTWARE, "in_progress", action, GONE if action else None)
    reply = client.post(f"/api/agent/jobs/{running}/report", json={
        "computer_hostname": "LAB-PC", "status": "completed", "exit_code": 0, "install_record": PROBE}).get_json()
    check(label, (reply.get("install_record"), rows("SELECT COUNT(*) AS n FROM installations")[0]["n"]) == expected)

write("DELETE FROM notifications")
stale = job(NO_LIBRARY, "in_progress", UNINSTALL, GONE, name="Gone App")
write("UPDATE jobs SET started_at = DATETIME('now', '-2 days'), retry_count = ? WHERE id = ?",
      (appmod.MAX_JOB_RETRIES, stale))
dashboard()
check("the reaper fails a stale software_id 0 job",
      rows("SELECT status FROM jobs WHERE id = ?", (stale,))[0]["status"] == "failed")
check("...and its notification names it by its snapshot",
      [r["message"] for r in rows("SELECT message FROM notifications")]
      == ["No report from the agent: uninstall of Gone App on LAB-PC — it may already have run"])

# ---------------------------------------------------------------------------
print("7. Cancel and delete take a software_id 0 job like any other")
# ---------------------------------------------------------------------------
pending = job(NO_LIBRARY, "pending", UNINSTALL, GONE, name="Gone App")
client.post(f"/jobs/{pending}/cancel")
check("cancel", rows("SELECT status FROM jobs WHERE id = ?", (pending,))[0]["status"] == "cancelled")
client.post("/jobs/bulk/delete", data={"job_ids": [pending]})
check("delete", not rows("SELECT id FROM jobs WHERE id = ?", (pending,)))

# ---------------------------------------------------------------------------
print()
print(f"{passed} passed, {len(failed)} failed")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

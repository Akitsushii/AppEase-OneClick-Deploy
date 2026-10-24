"""The one email a reporter gets when their report is finished: from every
route that sets status, only when it MOVES, once ever; a bulk is sent in
full, waiting for a slot rather than dropping.
"""
import logging
import os
import secrets
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
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


work = tempfile.mkdtemp(prefix="appease_finished_")
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

if not hasattr(appmod, "queue_report_finished_emails"):
    print("COULD NOT RUN - app.py predates the resolved email.")
    sys.exit(3)
appmod.app.config["TESTING"] = True
PASSWORD = "Str0ng!Passw0rd"
TYPED = "typed-by-someone-" + secrets.token_hex(3)


class Captured(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())

    def take(self):
        out, self.lines = self.lines, []
        return out


logs = Captured()
appmod.app.logger.addHandler(logs)


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
ADMIN = rows("SELECT id FROM admins WHERE username = 'admin'")[0]["id"]
admin = appmod.app.test_client()
admin.post("/login", data={"username": "admin", "password": PASSWORD})

jobs = []
appmod.run_in_background = jobs.append
sent = []


def record_send(to, subject, body):
    sent.append((to, subject, body))
    return True, None


appmod.send_email = record_send


def run_jobs():
    while jobs:
        jobs.pop(0)()


def report(email="student@lab.example", status="open", assigned=None):
    return write("INSERT INTO problem_reports (computer_hostname, problem_type, description, status, notes, "
                 "resolution_notes, reporter_name, reporter_email, assigned_to) "
                 "VALUES ('LAB1-PC07', 'mouse', ?, ?, ?, ?, ?, ?, ?)",
                 (TYPED, status, TYPED, TYPED, "Juana " + TYPED, email, assigned))


def marked(report_id):
    return rows("SELECT resolved_notified_at FROM problem_reports WHERE id = ?", (report_id,))[0]["resolved_notified_at"]


def mail(on):
    appmod.SMTP_HOST = "smtp.lab.example" if on else ""


def reset():
    jobs.clear()
    sent.clear()


mail(True)

# ---------------------------------------------------------------------------
print("1. One route at a time, with the right words")
# ---------------------------------------------------------------------------
R = report()
admin.post(f"/problem-reports/{R}/status", data={"status": "resolved"})
check("the request queued one job and sent nothing itself", len(jobs) == 1 and sent == [] and marked(R) is None)
run_jobs()
check("single status -> one email to the reporter", [to for to, _, _ in sent] == ["student@lab.example"])
subject, body = sent[0][1], sent[0][2]
check(f"Resolved subject ({subject})", subject == f"Your AppEase report #{R} has been resolved")
when = body.split("Resolved: ", 1)[1].split(" (Philippine time)", 1)[0] if "Resolved: " in body else ""
check("Resolved body is exactly the fixed text", body == (
    "Report resolved\n\n"
    "Your report has been marked as resolved. Thank you for reporting.\n\n"
    f"Report number: #{R}\nComputer: LAB1-PC07\nProblem type: Mouse\n"
    f"Resolved: {when} (Philippine time)\n\n"
    "If the problem comes back, please send a new report.\n\n"
    "--\n"
    "BulSU Meneses Campus computer lab - this is an automatic message, replies are not read.\n"))
try:
    stamped = datetime.strptime(when, "%d %b %Y, %I:%M %p")
except ValueError:
    stamped = None
manila_now = datetime.utcnow() + timedelta(hours=8)
check(f"...its time is now, in Manila time ({when})",
      stamped is not None and abs((manila_now - stamped).total_seconds()) < 120)
check("it has an HTML part too", "<html" in getattr(body, "html", "") and f"#{R}" in body.html)
check("nothing typed by anyone is in it, in either part", TYPED not in subject + body + getattr(body, "html", ""))
check("the report is marked", marked(R) is not None)

reset()
N = report()
admin.post(f"/problem-reports/{N}/status", data={"status": "no_fault_found"})
run_jobs()
subject, body = (sent[0][1], sent[0][2]) if sent else ("", "")
when = body.split("Checked: ", 1)[1].split(" (Philippine time)", 1)[0] if "Checked: " in body else ""
check(f"No fault found subject ({subject})", subject == f"Your AppEase report #{N} has been checked")
check("No fault found body is exactly the fixed text", body == (
    "Report checked - no fault found\n\n"
    "A technician checked the computer in your report and found no fault. "
    "Thank you for reporting.\n\n"
    f"Report number: #{N}\nComputer: LAB1-PC07\nProblem type: Mouse\n"
    f"Checked: {when} (Philippine time)\n\n"
    "If the problem is still there, please send a new report.\n\n"
    "--\n"
    "BulSU Meneses Campus computer lab - this is an automatic message, replies are not read.\n"))
check("...and nothing typed in its HTML part either", TYPED not in getattr(body, "html", "") and "<html" in body.html)

reset()
D = report(assigned=ADMIN)
admin.post(f"/problem-reports/{D}/status", data={"status": "resolved", "return_to": "dashboard"})
run_jobs()
check("the dashboard's single status sends too", [to for to, _, _ in sent] == ["student@lab.example"])

reset()
E = report()
admin.post(f"/problem-reports/{E}/edit", data={"status": "resolved", "priority": "medium", "assigned_to": ""})
run_jobs()
check("the edit drawer sends too", len(sent) == 1 and f"#{E}" in sent[0][1])
reset()
E2 = report()
admin.post(f"/problem-reports/{E2}/edit", data={"status": "open", "priority": "high", "assigned_to": ""})
run_jobs()
check("the drawer without a status change sends nothing", sent == [] and jobs == [])
E3 = report(status="resolved")
admin.post(f"/problem-reports/{E3}/edit", data={"status": "resolved", "priority": "high", "assigned_to": ""})
run_jobs()
check("the drawer re-saving a Resolved report (priority only): nothing", sent == [] and marked(E3) is None)

reset()
PAGE = [report(), report(), report(email="other@lab.example")]
admin.post("/problem-reports/bulk-status", data={"report_ids": [str(i) for i in PAGE], "status": "resolved"})
check("/bulk-status: ONE job for the whole request", len(jobs) == 1)
run_jobs()
check("...three emails, in report order", [s[1] for s in sent] == [f"Your AppEase report #{i} has been resolved"
                                                                   for i in sorted(PAGE)])

reset()
DASH = [report(assigned=ADMIN), report(assigned=ADMIN), report(assigned=ADMIN, status="resolved")]
admin.post("/problem-reports/bulk/status", data={"report_ids": [str(i) for i in DASH], "status": "resolved"})
run_jobs()
check("/bulk/status (dashboard): emails only the reports whose status moved",
      [s[1] for s in sent] == [f"Your AppEase report #{i} has been resolved" for i in sorted(DASH[:2])])

# ---------------------------------------------------------------------------
print("2. Never twice; never without an address; never on an unchanged status")
# ---------------------------------------------------------------------------
reset()
admin.post(f"/problem-reports/{R}/status", data={"status": "in_progress"})
admin.post(f"/problem-reports/{R}/status", data={"status": "resolved"})
run_jobs()
check("Resolved -> In progress -> Resolved: no second email", sent == [])
admin.post(f"/problem-reports/{R}/status", data={"status": "no_fault_found"})
run_jobs()
check("Resolved -> No fault found: no second email either", sent == [])
X = report(email=None)
admin.post(f"/problem-reports/{X}/status", data={"status": "resolved"})
run_jobs()
check("a report with no address: nothing sent, nothing marked", sent == [] and marked(X) is None)
OLD = report(status="resolved")
admin.post(f"/problem-reports/{OLD}/status", data={"status": "resolved"})
run_jobs()
check("re-saving a report already Resolved (from before this feature): nothing", sent == [] and marked(OLD) is None)
admin.post(f"/problem-reports/{OLD}/status", data={"status": "waiting_for_parts"})
run_jobs()
check("a status that is not finished: nothing", sent == [] and jobs == [])

RACE = report()
appmod.queue_report_finished_emails([RACE], "resolved")
appmod.queue_report_finished_emails([RACE], "resolved")
run_jobs()
check("two jobs racing for one report: one email", len(sent) == 1)

# Marked by something else AFTER the batch read its list - the claim, not
# the list, is what stops a second email.
reset()
FIRST, SECOND = report(email="first@lab.example"), report(email="second@lab.example")


def mark_second_while_sending_first(to, subject, body):
    sent.append((to, subject, body))
    if to == "first@lab.example":
        write("UPDATE problem_reports SET resolved_notified_at = '2026-10-07 00:00:00' WHERE id = ?", (SECOND,))
    return True, None


appmod.send_email = mark_second_while_sending_first
appmod.queue_report_finished_emails([FIRST, SECOND], "resolved")
run_jobs()
appmod.send_email = record_send
check("a report marked by something else mid-batch is not emailed again",
      [to for to, _, _ in sent] == ["first@lab.example"])

# ---------------------------------------------------------------------------
print("3. Mail off")
# ---------------------------------------------------------------------------
reset()
mail(False)
M = report()
response = admin.post(f"/problem-reports/{M}/status", data={"status": "resolved"})
check("the status changes as today", rows("SELECT status FROM problem_reports WHERE id = ?", (M,))[0]["status"]
      == "resolved" and response.status_code == 302 and "error" not in response.headers.get("Location", ""))
check("nothing queued, nothing marked", jobs == [] and marked(M) is None)
mail(True)
admin.post(f"/problem-reports/{M}/status", data={"status": "in_progress"})
admin.post(f"/problem-reports/{M}/status", data={"status": "resolved"})
run_jobs()
check("with mail on later, finishing it again does send", len(sent) == 1 and f"#{M}" in sent[0][1])

# ---------------------------------------------------------------------------
print("4. A bulk of 20 - waiting for slots, never dropping")
# ---------------------------------------------------------------------------
reset()
BULK = [report(email=f"s{i}@lab.example") for i in range(20)]
held = [appmod._email_slots.acquire(blocking=False) for _ in range(appmod.EMAIL_MAX_IN_FLIGHT)]
check("every sending slot is taken before the job starts", all(held))
appmod.queue_report_finished_emails(BULK, "resolved")
worker = threading.Thread(target=jobs.pop(0))
worker.start()
time.sleep(0.4)
check("...and the job waits rather than dropping", sent == [] and worker.is_alive())
for _ in held:
    appmod._email_slots.release()
worker.join(10)
check(f"all 20 sent once a slot frees, one after another, in order ({len(sent)})",
      [to for to, _, _ in sent] == [f"s{i}@lab.example" for i in range(20)])
check("all 20 marked", all(marked(i) for i in BULK))

# ---------------------------------------------------------------------------
print("5. No slot for the whole wait: stop, log, leave unmarked")
# ---------------------------------------------------------------------------
reset()
logs.take()
STUCK = [report(email=f"stuck{i}@school.example") for i in range(3)]
DONE = report(email="done@school.example")
write("UPDATE problem_reports SET resolved_notified_at = '2026-10-07 00:00:00' WHERE id = ?", (DONE,))
held = [appmod._email_slots.acquire(blocking=False) for _ in range(appmod.EMAIL_MAX_IN_FLIGHT)]
appmod.STATUS_EMAIL_SLOT_WAIT_SECONDS = 0.2
appmod.queue_report_finished_emails(STUCK + [DONE], "resolved")
run_jobs()
lines = logs.take()
check("nothing sent", sent == [])
check("none marked - a later finish can still email them", not any(marked(i) for i in STUCK))
check(f"each report never tried is logged, by domain only - not one already emailed ({len(lines)})",
      [line for line in lines if "not sent: no sending slot free" in line]
      == [f"Report #{i} email to *@school.example not sent: no sending slot free for 0.2s" for i in STUCK])
for _ in held:
    appmod._email_slots.release()
appmod.STATUS_EMAIL_SLOT_WAIT_SECONDS = 120
appmod.queue_report_finished_emails(STUCK, "resolved")
run_jobs()
check("once mail flows again, they are sent", len(sent) == 3)

# ---------------------------------------------------------------------------
print("6. A crash mid-batch")
# ---------------------------------------------------------------------------
reset()


class Crash(BaseException):
    """Stands in for the process ending - not caught as an ordinary error."""


CRASH = [report(email=f"c{i}@lab.example") for i in range(4)]


def crash_on_second(to, subject, body):
    sent.append((to, subject, body))
    if len(sent) == 2:
        raise Crash()
    return True, None


appmod.send_email = crash_on_second
appmod.queue_report_finished_emails(CRASH, "resolved")
try:
    run_jobs()
except Crash:
    pass
check("the two it reached are marked", marked(CRASH[0]) is not None and marked(CRASH[1]) is not None)
check("the two it never reached stay unmarked", marked(CRASH[2]) is None and marked(CRASH[3]) is None)
check("...and the slot it held was given back",
      all(appmod._email_slots.acquire(blocking=False) for _ in range(appmod.EMAIL_MAX_IN_FLIGHT)))
for _ in range(appmod.EMAIL_MAX_IN_FLIGHT):
    appmod._email_slots.release()
check("...and the lane is free again", appmod._status_email_lane.acquire(blocking=False))
appmod._status_email_lane.release()
appmod.send_email = record_send
reset()
appmod.queue_report_finished_emails(CRASH, "resolved")
run_jobs()
check("the next finish sends only the two never reached", [to for to, _, _ in sent] == ["c2@lab.example", "c3@lab.example"])

# ---------------------------------------------------------------------------
print("7. One lane")
# ---------------------------------------------------------------------------
reset()
active = {"now": 0, "most": 0}
lock = threading.Lock()


def slow_send(to, subject, body):
    with lock:
        active["now"] += 1
        active["most"] = max(active["most"], active["now"])
    time.sleep(0.05)
    with lock:
        active["now"] -= 1
    sent.append(to)
    return True, None


appmod.send_email = slow_send
A = [report(email=f"a{i}@lab.example") for i in range(4)]
B = [report(email=f"b{i}@lab.example") for i in range(4)]
appmod.queue_report_finished_emails(A, "resolved")
appmod.queue_report_finished_emails(B, "resolved")
threads = [threading.Thread(target=job) for job in list(jobs)]
jobs.clear()
for thread in threads:
    thread.start()
for thread in threads:
    thread.join(10)
check(f"two batches at once: all 8 sent, never two status emails at the same moment ({active['most']})",
      len(sent) == 8 and active["most"] == 1)

print()
print(f"{passed} passed, {len(failed)} failed")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

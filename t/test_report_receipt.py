"""The reporter's email and the receipt sent to it: fixed text only, never
anything the reporter typed; queued in the background; at most 5 per
address per hour; and never in the audit log or notifications.
"""
import os
import html
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlparse

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


work = tempfile.mkdtemp(prefix="appease_receipt_")
server_dir = os.path.join(work, "server")
shutil.copytree(os.path.join(ROOT, "server"), server_dir, ignore=shutil.ignore_patterns(
    "appease.db*", "__pycache__", "*.pyc", ".env", "uploads", "staging", "report_photos", "avatars", "venv"))
sys.path.insert(0, server_dir)
os.chdir(server_dir)
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
# The sticker codes are signed with this; without it no code is accepted.
os.environ["REPORT_QR_SECRET"] = secrets.token_hex(16)
for name in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "DEMO_MODE_ENABLED"):
    os.environ.pop(name, None)

import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()
import app as appmod  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

if not hasattr(appmod, "send_report_receipt"):
    print("COULD NOT RUN - app.py predates the report receipt.")
    sys.exit(3)
appmod.app.config["TESTING"] = True
PASSWORD = "Str0ng!Passw0rd"
DESCRIPTION_SECRET = "projector smells of smoke " + secrets.token_hex(3)
NAME_SECRET = "Juana Secreta"


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


write("UPDATE admins SET password_hash = ?, must_change_password = 0, email = 'boss@lab.example' WHERE username = 'admin'",
      (generate_password_hash(PASSWORD),))
write("INSERT INTO admins (username, password_hash, role, must_change_password, email) "
      "VALUES ('noemail', ?, 'admin', 0, NULL)", (generate_password_hash(PASSWORD),))
write("INSERT INTO admins (username, password_hash, role, must_change_password) VALUES ('faculty1', ?, 'faculty', 0)",
      (generate_password_hash(PASSWORD),))
write("INSERT INTO computers (hostname, status, ip_address) VALUES ('LAB-PC-01', 'online', '10.0.0.5')")


def signed_in(username):
    client = appmod.app.test_client()
    client.post("/login", data={"username": username, "password": PASSWORD})
    return client


anon = appmod.app.test_client()
admin = signed_in("admin")
noemail = signed_in("noemail")
faculty = signed_in("faculty1")
LAB = {"REMOTE_ADDR": "10.0.0.5"}

jobs = []
appmod.run_in_background = jobs.append
sent = []
appmod.send_email = lambda to, subject, body: sent.append((to, subject, body)) or (True, None)


def run_jobs():
    while jobs:
        jobs.pop(0)()


def report(client=None, email="student@lab.example.com", **extra):
    data = {
        "computer_hostname": "LAB-PC-01", "problem_type": "keyboard",
        "description": f"The keyboard is broken, {DESCRIPTION_SECRET}.",
        "reporter_role": "student", "reporter_name": NAME_SECRET, "reporter_email": email,
        "reporter_student_number": "2021400001", "reporter_program": "BSIT", "reporter_section": "4A",
        "qr_code": appmod.computer_short_code("LAB-PC-01"),
        "form_started": appmod.sign_form_timestamp(time.time() - 30),
    }
    if email is None:
        data.pop("reporter_email")
    data.update(extra)
    with appmod._report_rate_lock:
        appmod._report_rate_hits.clear()
    return (client or anon).post("/report", data=data)


def toast(response):
    query = parse_qs(urlparse(response.headers.get("Location", "")).query)
    return (query.get("success") or [""])[0], (query.get("error") or [""])[0]


def field_error(response, name):
    """The message under one field of a report shown again after a refusal."""
    found = re.search(rf'id="{name}-error" class="text-xs text-danger">([^<]*)<', response.get_data(as_text=True))
    return html.unescape(found.group(1)) if found else ""


def count():
    return len(rows("SELECT id FROM problem_reports"))


def mail(on):
    appmod.SMTP_HOST = "smtp.lab.example" if on else ""


# ---------------------------------------------------------------------------
print("1. The field")
# ---------------------------------------------------------------------------
page = anon.get("/report", environ_overrides=LAB).get_data(as_text=True)
field = re.search(r'<input[^>]*id="reporter_email"[^>]*>', page)
field = field.group(0) if field else ""
check("the form has an email field", bool(field))
for attribute in ('type="email"', 'inputmode="email"', 'autocomplete="email"', "required", 'maxlength="120"',
                  'autocapitalize="off"', 'spellcheck="false"'):
    check(f"...{attribute}", attribute in field)
check("...empty for someone not signed in", 'value=""' in field)
check("...with the one line under it", "Used only to confirm and update you about this report." in page)
check("...and the same rule in the page's own check, so jols@g is refused before Send",
      "function emailError(val)" in page
      and "if (emailError(document.getElementById('reporter_email').value.trim())) return false;" in page)
admin_page = admin.get("/report", environ_overrides=LAB).get_data(as_text=True)
check("signed in with an address on the account: pre-filled",
      re.search(r'id="reporter_email"[^>]*value="boss@lab.example"', admin_page) is not None)
noemail_page = noemail.get("/report", environ_overrides=LAB).get_data(as_text=True)
check("signed in, no address on the account: empty, and still required",
      re.search(r'<input[^>]*id="reporter_email"[^>]*>', noemail_page) is not None
      and 'value=""' in re.search(r'<input[^>]*id="reporter_email"[^>]*>', noemail_page).group(0))

# ---------------------------------------------------------------------------
print("2. Required, and checked on the server")
# ---------------------------------------------------------------------------
mail(False)
before = count()
for label, email, message in (
        ("missing", None, "Your email address is required."),
        ("blank", "   ", "Your email address is required."),
        ("jols@g", "jols@g", appmod.REPORT_EMAIL_MESSAGE),
        ("no @", "jols.gmail.com", appmod.REPORT_EMAIL_MESSAGE),
        ("two @", "a@b@gmail.com", appmod.REPORT_EMAIL_MESSAGE),
        ("121 characters", "a" * 56 + "@" + "b" * 60 + ".com", appmod.REPORT_EMAIL_MESSAGE)):
    error = field_error(report(email=email), "reporter_email")
    check(f"{label}: refused under the email field ({error})", error == message)
check("...and nothing was saved", count() == before)
success, error = toast(report(email="  Student@Lab.Example.com  "))
saved = rows("SELECT reporter_email FROM problem_reports ORDER BY id DESC LIMIT 1")
check(f"a valid address: saved, trimmed ({error})", not error and saved and saved[0]["reporter_email"] == "Student@Lab.Example.com")
toast(report(client=admin, email="someone.else@lab.example.com"))
check("an address typed while signed in is saved on the report only - the account keeps its own",
      rows("SELECT email FROM admins WHERE username = 'admin'")[0]["email"] == "boss@lab.example"
      and rows("SELECT reporter_email FROM problem_reports ORDER BY id DESC LIMIT 1")[0]["reporter_email"]
      == "someone.else@lab.example.com")

# ---------------------------------------------------------------------------
print("3. Mail off: the report saves, nothing about email")
# ---------------------------------------------------------------------------
jobs.clear()
sent.clear()
before = count()
success, error = toast(report())
check(f"saved, no error ({success})", count() == before + 1 and not error)
check("the toast says only 'Report submitted.'", success == "Report submitted.")
check("nothing queued", jobs == [] and sent == [])

# ---------------------------------------------------------------------------
print("4. The receipt: fixed text, in the background")
# ---------------------------------------------------------------------------
mail(True)
appmod.REPORT_RECEIPT_LIMITER._hits.clear()
jobs.clear()
sent.clear()
success, error = toast(report(email="student@lab.example.com"))
report_id = rows("SELECT MAX(id) AS id FROM problem_reports")[0]["id"]
check("the request queued one job and sent nothing itself", len(jobs) == 1 and sent == [])
check("the toast promises nothing and names no address",
      success == ("Report submitted. A confirmation email is on its way to the address you gave - "
                  "if you don't see it, check your spam folder.") and "@" not in success)
run_jobs()
check("one receipt, to the address given", [to for to, _, _ in sent] == ["student@lab.example.com"])
to, subject, body = sent[0] if sent else ("", "", "")
check(f"subject names the report ({subject})", subject == f"AppEase received your report #{report_id}")
reported = rows("SELECT reported_at FROM problem_reports WHERE id = ?", (report_id,))[0]["reported_at"]
local = appmod._parse_stored_utc(reported) + appmod.MANILA_OFFSET
expected = ("Report received\n\n"
            "AppEase has received your report. Thank you for reporting.\n\n"
            f"Report number: #{report_id}\n"
            "Computer: LAB-PC-01\n"
            "Problem type: Keyboard\n"
            f"Submitted: {local.strftime('%d %b %Y, ')}{local.strftime('%I:%M %p').lstrip('0')} (Philippine time)\n\n"
            "--\n"
            "BulSU Meneses Campus computer lab - this is an automatic message, replies are not read.\n")
check("the body is exactly the fixed text", body == expected)
# The HTML part is a second copy of the same message, so it is held to the
# same rule: nothing the reporter typed, in either part.
html_part = getattr(body, "html", "")
check("it has an HTML part too", "<html" in html_part and f"#{report_id}" in html_part)
check("...never the description, the name or anything else typed, in either part",
      all(secret not in body + subject + html_part
          for secret in (DESCRIPTION_SECRET, NAME_SECRET, "2021400001", "BSIT")))

# ---------------------------------------------------------------------------
print("5. Five an hour per address")
# ---------------------------------------------------------------------------
appmod.REPORT_RECEIPT_LIMITER._hits.clear()
jobs.clear()
sent.clear()
before = count()
for email in ("same@lab.example.com", "SAME@lab.example.com", "same@lab.example.com", "Same@Lab.Example.com",
              "same@LAB.example.com", "same@lab.EXAMPLE.COM"):
    report(email=email)
run_jobs()
check("six reports, all saved", count() == before + 6)
check(f"five receipts, the sixth skipped - the address counted without case ({len(sent)})", len(sent) == 5)
check("the limit is five", appmod.REPORT_RECEIPTS_PER_HOUR == 5)
report(email="other@lab.example.com")
run_jobs()
check("another address still gets its own", sent[-1][0] == "other@lab.example.com")

# ---------------------------------------------------------------------------
print("6. What never sends")
# ---------------------------------------------------------------------------
appmod.REPORT_RECEIPT_LIMITER._hits.clear()
jobs.clear()
sent.clear()
appmod.DEMO_MODE_ENABLED = True
before = count()
success, _ = toast(report(demo="1"))
check(f"the demo preview: nothing saved, nothing queued ({success})", count() == before and jobs == [])
appmod.DEMO_MODE_ENABLED = False
success, _ = toast(report(website="http://spam.example"))
check("the honeypot: nothing saved or queued, and the same words a real report gets",
      count() == before and jobs == [] and success == appmod.report_submitted_message())
response = anon.post("/api/agent/problem-reports", json={
    "computer_hostname": "LAB-PC-01", "problem_type": "software", "description": "Agent filed this one here."})
agent_row = rows("SELECT reporter_email FROM problem_reports ORDER BY id DESC LIMIT 1")
check(f"the agent API: saved without an address, nothing queued ({response.status_code})",
      count() == before + 1 and agent_row[0]["reporter_email"] is None and jobs == [])

# ---------------------------------------------------------------------------
print("7. Who sees it, and where it never goes")
# ---------------------------------------------------------------------------
target = rows("SELECT id FROM problem_reports WHERE reporter_email = 'student@lab.example.com' ORDER BY id DESC LIMIT 1")[0]["id"]
admin_list = admin.get("/problem-reports").get_data(as_text=True)
check("admins: the row carries the address", 'data-reporter-email="student@lab.example.com"' in admin_list)
check("...and the Details panel has an Email line for it", 'id="details-reporter-email-row"' in admin_list)
faculty_list = faculty.get("/problem-reports").get_data(as_text=True)
# The element, not its id: the page's shared script names the id for everyone.
check("faculty: no address anywhere on the page, and no Email line",
      "@lab.example" not in faculty_list and 'id="details-reporter-email-row"' not in faculty_list
      and "data-reporter-email" not in faculty_list)
check("never in the audit log or the notifications",
      not any("@lab.example" in str(v) for r in rows("SELECT * FROM audit_log") + rows("SELECT * FROM notifications")
              for v in r.values()))
write("UPDATE problem_reports SET status = 'resolved' WHERE id = ?", (target,))
admin.post("/problem-reports/archive", data={"report_ids": [str(target)]})
admin.post(f"/problem-reports/{target}/delete")
check("deleting the report deletes its address", rows("SELECT id FROM problem_reports WHERE id = ?", (target,)) == [])
check("...and leaves it nowhere in the audit log", not any("student@lab.example.com" in str(v)
                                                           for r in rows("SELECT * FROM audit_log") for v in r.values()))

print()
print(f"{passed} passed, {len(failed)} failed")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

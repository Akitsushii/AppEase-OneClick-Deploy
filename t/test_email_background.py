"""Email off the request: forgot-password does the same work whatever the
address, so its timing reveals nothing; a failure logs only the domain and
the error's kind; account emails wait a bounded time, then show the
password on screen. SMTP is never reached.
"""
import html
import logging
import os
import re
import secrets
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
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


work = tempfile.mkdtemp(prefix="appease_email_")
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

if not hasattr(appmod, "send_email_within"):
    print("COULD NOT RUN - app.py predates sending email in the background.")
    sys.exit(3)
appmod.app.config["TESTING"] = True
PASSWORD = "Str0ng!Passw0rd"
SECRET = "body-secret-" + secrets.token_hex(4)


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


write("UPDATE admins SET password_hash = ?, must_change_password = 0, email = 'boss@lab.example' WHERE username = 'admin'",
      (generate_password_hash(PASSWORD),))
ADMIN = rows("SELECT id FROM admins WHERE username = 'admin'")[0]["id"]
write("INSERT INTO admins (username, password_hash, role, must_change_password, email) "
      "VALUES ('faculty1', ?, 'faculty', 0, 'fac@lab.example')", (generate_password_hash(PASSWORD),))


def signed_in(username):
    client = appmod.app.test_client()
    client.post("/login", data={"username": username, "password": PASSWORD})
    return client


admin = signed_in("admin")
faculty = signed_in("faculty1")
REAL_BACKGROUND = appmod.run_in_background
REAL_SEND = appmod.send_email


class FakeSend:
    """Stands in for send_email: records each send, takes `delay` seconds,
    and answers `result`."""

    def __init__(self, delay=0.0, result=(True, None)):
        self.delay, self.result, self.sent = delay, result, []
        self.done = threading.Event()

    def __call__(self, to_address, subject, body):
        time.sleep(self.delay)
        self.sent.append((to_address, subject, body))
        self.done.set()
        return self.result


def configured(on=True):
    appmod.SMTP_HOST = "smtp-host-" + SECRET if on else ""
    appmod.SMTP_USER = "smtp-user-" + SECRET if on else ""


def shown(client):
    """What the Accounts page's one-time password dialog shows: (title,
    password, description), or None. The password reaches the admin only
    here - never in the redirect URL."""
    page = client.get("/admins").get_data(as_text=True)
    value = re.search(r'id="adm-password-value">([^<]+)</code>', page)
    if not value:
        return None
    title = re.search(r'id="adm-password-title">(.*?)</h2>', page, re.S).group(1)
    description = re.search(r'id="adm-password-description">(.*?)</p>', page, re.S).group(1)
    return html.unescape(title), html.unescape(value.group(1)), html.unescape(description)


def query(response):
    return parse_qs(urlparse(response.headers.get("Location", "")).query)


# ---------------------------------------------------------------------------
print("1. Forgot password: the same work whatever the address")
# ---------------------------------------------------------------------------
configured(True)
jobs = []
appmod.run_in_background = jobs.append
fake = FakeSend()
appmod.send_email = fake
anon = appmod.app.test_client()
before = len(rows("SELECT id FROM password_resets"))
match_page = anon.post("/forgot-password", data={"email": "boss@lab.example"}).get_data(as_text=True)
check("a matching address: no token written by the request itself",
      len(rows("SELECT id FROM password_resets")) == before)
check("...and exactly one background job", len(jobs) == 1)
miss_page = anon.post("/forgot-password", data={"email": "nobody@lab.example"}).get_data(as_text=True)
check("an unknown address: one background job too - the same work", len(jobs) == 2)
check("the two pages are identical", match_page == miss_page)
jobs[0]()
check("the job writes the token", len(rows("SELECT id FROM password_resets")) == before + 1)
check("...and sends the link to the account's address",
      [(to, subject) for to, subject, _ in fake.sent] == [("boss@lab.example", "Reset your AppEase password")]
      and "/reset-password/" in fake.sent[0][2])
jobs[1]()
check("the unknown address's job writes and sends nothing",
      len(rows("SELECT id FROM password_resets")) == before + 1 and len(fake.sent) == 1)

appmod.run_in_background = REAL_BACKGROUND
slow = FakeSend(delay=2.0)
appmod.send_email = slow
timings = {}
for label, email in (("matching", "boss@lab.example"), ("unknown", "nobody2@lab.example")):
    started = time.monotonic()
    anon.post("/forgot-password", data={"email": email})
    timings[label] = time.monotonic() - started
check(f"with a mail server taking 2 s, neither request waits on it ({timings})",
      all(seconds < 0.5 for seconds in timings.values()))
check("...and the email still goes, in the background", slow.done.wait(5) and slow.sent[0][0] == "boss@lab.example")
check("the old timing-leak note is gone from the route",
      "THE RESPONSE TIME IS NOT IDENTICAL" not in open(os.path.join(server_dir, "app.py"), encoding="utf-8").read())

# ---------------------------------------------------------------------------
print("2. Not configured: nothing started, nothing logged")
# ---------------------------------------------------------------------------
configured(False)
jobs = []
appmod.run_in_background = jobs.append
logs.take()
appmod.send_email_later("someone@lab.example", "Subject", "Body")
check("send_email_later starts nothing", jobs == [])
started = time.monotonic()
result = appmod.send_email_within("someone@lab.example", "Subject", "Body")
check("send_email_within answers at once, as before",
      result == (False, "Email is not configured on this server.") and time.monotonic() - started < 0.1 and jobs == [])
check("...and logs nothing", logs.take() == [])
appmod.run_in_background = REAL_BACKGROUND

# ---------------------------------------------------------------------------
print("3. A failed send is logged: domain and kind only")
# ---------------------------------------------------------------------------
probe = socket.socket()
probe.bind(("127.0.0.1", 0))
closed_port = probe.getsockname()[1]
probe.close()
appmod.send_email = REAL_SEND
appmod.SMTP_HOST, appmod.SMTP_PORT, appmod.SMTP_USE_TLS = "127.0.0.1", closed_port, False
logs.take()
sent, error = appmod.send_email("private.person@school.example", "Subject " + SECRET, "Body " + SECRET)
lines = logs.take()
check(f"a real send to a closed port fails ({error})", sent is False and error.startswith("Could not send the email ("))
check(f"...and is logged with the domain and the error's kind ({lines})",
      len(lines) == 1 and lines[0].startswith("Email to *@school.example not sent: ") and "Error" in lines[0])
check("...never the address, the subject or the body",
      "private.person" not in lines[0] and SECRET not in lines[0])
appmod.SMTP_PORT, appmod.SMTP_USE_TLS = 587, True

# ---------------------------------------------------------------------------
print("4. A new account and an admin reset: a bounded wait")
# ---------------------------------------------------------------------------
configured(True)
REAL_BOUND = appmod.EMAIL_CONFIRM_SECONDS
# Shortened to keep the suite quick. Every message below reads "1 second",
# which also proves the number comes from the constant, not the text.
appmod.EMAIL_CONFIRM_SECONDS = 1
NAMES = iter(range(1, 100))


def create_account():
    n = next(NAMES)
    started = time.monotonic()
    response = admin.post("/admins", data={"username": f"tech{n:02d}", "first_name": "Ana", "last_name": "Reyes",
                                           "role": "admin", "email": f"tech{n:02d}@lab.example"})
    return response, time.monotonic() - started, f"tech{n:02d}"


hang = FakeSend(delay=3.0)
appmod.send_email = hang
logs.take()
response, took, username = create_account()
q = query(response)
check(f"new account, mail server hanging: back within the bound ({took:.1f}s)", 0.9 <= took < 2.5)
dialog = shown(admin)
check("...with the password on screen, and why", dialog is not None and username in dialog[0] and dialog[1]
      and dialog[2].startswith("The email could not be confirmed within 1 second."))
check("...the audit row says it was not emailed",
      rows("SELECT details FROM audit_log WHERE action_type = 'account_created' ORDER BY id DESC LIMIT 1")[0]["details"]
      .endswith("credentials not emailed"))
check("...and the server log says it was not confirmed, by domain only",
      any(line == "Email to *@lab.example not confirmed within 1s" for line in logs.take()))
check("...while the send carries on and can still deliver", hang.done.wait(5))

quick = FakeSend()
appmod.send_email = quick
response, took, username = create_account()
q = query(response)
check(f"new account, mail working: emailed, and the password NOT on screen ({took:.2f}s)",
      shown(admin) is None and q.get("success", [""])[0].startswith("Account created. Sign-in details were emailed")
      and took < 1.0)

TARGET = rows("SELECT id FROM admins WHERE username = ?", (username,))[0]["id"]
appmod.send_email = FakeSend(delay=3.0)
started = time.monotonic()
q = query(admin.post(f"/admins/{TARGET}/reset-password"))
took = time.monotonic() - started
check(f"admin reset, mail server hanging: password on screen within the bound ({took:.1f}s)",
      shown(admin) is not None and 0.9 <= took < 2.5)
appmod.send_email = FakeSend()
q = query(admin.post(f"/admins/{TARGET}/reset-password"))
check("admin reset, mail working: emailed, password NOT on screen",
      shown(admin) is None and q.get("success", [""])[0].startswith("Temporary password emailed"))
appmod.send_email = FakeSend(result=(False, "Could not send the email (SMTPAuthenticationError)."))
q = query(admin.post(f"/admins/{TARGET}/reset-password"))
check("admin reset, mail refused: password on screen with the reason",
      (shown(admin) or ("", "", ""))[2].startswith("Could not send the email (SMTPAuthenticationError)."))
configured(False)
q = query(admin.post(f"/admins/{TARGET}/reset-password"))
check("not configured: the password on screen at once, as before",
      (shown(admin) or ("", "", ""))[2].startswith("Email is not configured on this server."))
configured(True)
appmod.EMAIL_CONFIRM_SECONDS = REAL_BOUND
check("the real bound is 15 seconds - Gmail took longer than 6 from the lab", REAL_BOUND == 15)
check("...and no message spells a number of seconds out by hand",
      not re.search(r"within \d+ second", open(os.path.join(server_dir, "app.py"), encoding="utf-8").read()))

# ---------------------------------------------------------------------------
print("5. Settings' change notices never block")
# ---------------------------------------------------------------------------
# The route checks your current password first, which is slow on purpose,
# so the bound is "well short of the notice's 3 s", not a fixed fraction.
notice = FakeSend(delay=3.0)
appmod.send_email = notice
started = time.monotonic()
admin.post("/settings", data={"form_type": "change_account", "account_current_password": PASSWORD,
                              "username": "admin", "email": "boss2@lab.example"})
took = time.monotonic() - started
check(f"an email change returns without waiting on the 3 s notice ({took:.2f}s)", took < 2.0)
check("...and the notice still goes to the OLD address", notice.done.wait(5)
      and notice.sent[0][:2] == ("boss@lab.example", "Your AppEase email address was changed"))

# ---------------------------------------------------------------------------
print("6. At most a few sends at once")
# ---------------------------------------------------------------------------
gate = threading.Event()
entered = []


def blocked(to_address, subject, body):
    entered.append(to_address)
    gate.wait(10)
    return True, None


appmod.send_email = blocked
logs.take()
for i in range(appmod.EMAIL_MAX_IN_FLIGHT):
    appmod.send_email_later(f"user{i}@lab.example", "S", "B")
deadline = time.monotonic() + 5
while len(entered) < appmod.EMAIL_MAX_IN_FLIGHT and time.monotonic() < deadline:
    time.sleep(0.02)
appmod.send_email_later("extra@overflow.example", "S", "B")
time.sleep(0.3)
check(f"{appmod.EMAIL_MAX_IN_FLIGHT} in flight; the next is dropped, not queued",
      len(entered) == appmod.EMAIL_MAX_IN_FLIGHT and "extra@overflow.example" not in entered)
check("...and logged by domain", any(line == "Email to *@overflow.example not sent: too many sends already in flight"
                                     for line in logs.take()))
gate.set()
time.sleep(0.3)
appmod.send_email = FakeSend()
appmod.send_email_later("after@lab.example", "S", "B")
time.sleep(0.3)
check("once they finish, sending works again", appmod.send_email.sent and appmod.send_email.sent[0][0] == "after@lab.example")

# ---------------------------------------------------------------------------
print("7. The Admins page, and Send test email")
# ---------------------------------------------------------------------------
configured(True)
page = admin.get("/admins").get_data(as_text=True)
check("configured: says so", "Email: configured" in page and "Email: not configured" not in page)
check("...and shows no setting's value", SECRET not in page)
test_button = re.search(r"<button\b[^>]*>\s*Send test email\s*</button>", page)
check("...the test button is enabled (your account has an address)",
      test_button is not None and "disabled" not in test_button.group(0)
      and 'title="Sends a test email to your own address."' in test_button.group(0))
configured(False)
page = admin.get("/admins").get_data(as_text=True)
check("not configured: says so, and what happens instead",
      "Email: not configured" in page and "Sign-in details are shown on screen instead." in page)
check("...the test button is disabled, saying why",
      '<span title="Email is not configured on this server."><button type="submit" class="btn" data-variant="outline" '
      'data-size="sm" disabled>Send test email</button></span>' in page)
configured(True)
write("UPDATE admins SET email = NULL WHERE id = ?", (ADMIN,))
page = admin.get("/admins").get_data(as_text=True)
check("configured but no address on your account: disabled, saying why",
      '<span title="Your account has no email address. Add one in Settings first.">' in page
      and 'data-size="sm" disabled>Send test email' in page)
q = query(admin.post("/admins/test-email"))
check("...and the route refuses too", q.get("error") == ["Your account has no email address. Add one in Settings first."])
write("UPDATE admins SET email = 'boss@lab.example' WHERE id = ?", (ADMIN,))

tester = FakeSend()
appmod.send_email = tester
appmod.TEST_EMAIL_LIMITER._hits.clear()
q = query(admin.post("/admins/test-email", data={"to": "victim@elsewhere.example", "email": "victim@elsewhere.example"}))
check("a test email goes to your own address - a posted recipient is ignored",
      q.get("success") == ["Test email sent to boss@lab.example."]
      and [to for to, _, _ in tester.sent] == ["boss@lab.example"])
q = query(admin.post("/admins/test-email"))
check("a second within the minute is refused, nothing sent",
      q.get("error") == ["A test email was sent less than a minute ago. Try again shortly."] and len(tester.sent) == 1)
appmod.TEST_EMAIL_LIMITER._hits.clear()
appmod.send_email = FakeSend(result=(False, "Could not send the email (TimeoutError)."))
q = query(admin.post("/admins/test-email"))
check("a failed test email says why", q.get("error") == ["Test email not sent. Could not send the email (TimeoutError)."])
appmod.TEST_EMAIL_LIMITER._hits.clear()
configured(False)
q = query(admin.post("/admins/test-email"))
check("not configured: refused", q.get("error") == ["Email is not configured on this server."])
configured(True)
fac_tester = FakeSend()
appmod.send_email = fac_tester
faculty.post("/admins/test-email")
check("faculty cannot send one", fac_tester.sent == [])

print()
print(f"{passed} passed, {len(failed)} failed")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

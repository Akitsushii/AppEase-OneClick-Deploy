"""Demo mode, the survey-only path through /report: a demo submission must
leave NO trace, so every assertion that matters counts rows before and after.
"""
import os
import secrets
import shutil
import sqlite3
import sys
import tempfile
from urllib.parse import unquote_plus

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


work = tempfile.mkdtemp(prefix="appease_demo_")
server_dir = os.path.join(work, "server")
shutil.copytree(
    os.path.join(ROOT, "server"), server_dir,
    ignore=shutil.ignore_patterns("appease.db*", "__pycache__", "*.pyc", ".env"),
)
sys.path.insert(0, server_dir)
os.chdir(server_dir)
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
os.environ["REPORT_QR_SECRET"] = "d" * 64


def load(demo_enabled):
    """Import app.py fresh. DEMO_MODE_ENABLED is read at import time, so
    the two states need two imports - patching the constant afterwards
    would test a configuration that never occurs at startup."""
    for mod in ("app", "database"):
        sys.modules.pop(mod, None)
    if demo_enabled is None:
        os.environ.pop("DEMO_MODE_ENABLED", None)
    else:
        os.environ["DEMO_MODE_ENABLED"] = demo_enabled
    import database
    database.DB_PATH = os.path.join(server_dir, "appease.db")
    database.init_db()
    conn = sqlite3.connect(database.DB_PATH)
    conn.execute("INSERT OR IGNORE INTO computers (hostname, ip_address, status) "
                 "VALUES ('DEMO-PC', '10.0.0.9', 'online')")
    conn.commit()
    conn.close()
    import app as appmod
    appmod.app.config["TESTING"] = True
    return appmod, database


def counts(database):
    conn = sqlite3.connect(database.DB_PATH)
    reports = conn.execute("SELECT COUNT(*) FROM problem_reports").fetchone()[0]
    notes = conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
    conn.close()
    return reports, notes


FORM = {
    "computer_hostname": "DEMO-PC", "problem_type": "keyboard",
    "description": "The spacebar has stopped responding entirely.",
    "reporter_role": "student", "reporter_name": "Survey Person", "reporter_email": "student@lab.example.com",
    "reporter_student_number": "2021400001", "reporter_program": "BSIT",
    "reporter_section": "4A", "reporter_year_level": "4",
}


print("\n=== 1. Disabled by default ===")
appmod, database = load(None)
check("the switch is off with no env var", appmod.DEMO_MODE_ENABLED is False)
client = appmod.app.test_client()
html = client.get("/report?demo=1").get_data(as_text=True)
# Without the env check, ?demo=1 would be a way past the gate for anyone
# who guessed the parameter.
check("?demo=1 does NOT open the form when disabled", "Code on the sticker" in html)
check("no preview banner when disabled", "Preview only" not in html)

before = counts(database)
res = client.post("/report", data=dict(FORM, demo="1"))
check("a demo POST is refused when disabled", counts(database) == before)

for value in ("0", "false", "no", ""):
    appmod, database = load(value)
    check(f"DEMO_MODE_ENABLED={value!r} stays off", appmod.DEMO_MODE_ENABLED is False)


print("\n=== 2. Enabled ===")
for value in ("1", "true", "yes", "on", "TRUE"):
    appmod, database = load(value)
    check(f"DEMO_MODE_ENABLED={value!r} turns it on", appmod.DEMO_MODE_ENABLED is True)

appmod, database = load("1")
client = appmod.app.test_client()

html = client.get("/report?demo=1").get_data(as_text=True)
check("the real form is served", 'name="problem_type"' in html)
check("it is NOT the gate", "Code on the sticker" not in html)
check("the preview banner is shown", "Preview only" in html)
check("the banner says nothing is saved", "nothing you enter will be saved" in html.lower())
check("the banner is not dismissible", "data-dismiss" not in html)
check("the form carries the demo flag through the POST",
      'name="demo" value="1"' in html)
check("real computers are listed", "DEMO-PC" in html)

# A plain visit must still hit the gate even with demo mode ON.
plain = client.get("/report").get_data(as_text=True)
check("a visit WITHOUT ?demo=1 still hits the gate", "Code on the sticker" in plain)
check("no banner on the gated page", "Preview only" not in plain)


print("\n=== 3. A demo submission writes nothing ===")
before_reports, before_notes = counts(database)
res = client.post("/report", data=dict(FORM, demo="1"), follow_redirects=False)
after_reports, after_notes = counts(database)
check("the POST redirects like a real one", res.status_code == 302)
check("NO report row was created", after_reports == before_reports)
check("NO notification was created", after_notes == before_notes)

location = unquote_plus(res.headers.get("Location", ""))
check("it returns to the demo form", "demo=1" in location)
check("the message says it was not saved", "NOT saved" in location)

# Photos must not reach disk either.
photo_dir = os.path.join(server_dir, "static", "report_photos")
before_files = set(os.listdir(photo_dir)) if os.path.isdir(photo_dir) else set()
import io
res = client.post("/report", data=dict(
    FORM, demo="1",
    report_photos=(io.BytesIO(b"\xff\xd8\xff" + b"0" * 4000), "shot.jpg"),
), content_type="multipart/form-data")
after_files = set(os.listdir(photo_dir)) if os.path.isdir(photo_dir) else set()
check("no photo was written to disk", after_files == before_files)
check("still no report row", counts(database)[0] == before_reports)

# The rate limit must not apply - every tunnelled request arrives as
# 127.0.0.1, so a per-sender cap would lock out every respondent after the
# fifth and the survey would simply stop working halfway through.
appmod._report_rate_hits.clear()
blocked = 0
for i in range(12):
    r = client.post("/report", data=dict(FORM, demo="1"))
    if "error=" in (r.headers.get("Location") or ""):
        blocked += 1
check("twelve demo submissions in a row are all accepted", blocked == 0)
check("and none of them were stored", counts(database)[0] == before_reports)


# The ordering guarantee, proved directly. A demo POST carrying a VALID
# token would otherwise sail through the real path and be stored - so this
# is the case that shows the demo check runs FIRST, rather than the gate
# happening to catch demo traffic for unrelated reasons.
import time as _time
valid = appmod.sign_computer("DEMO-PC")
before_valid = counts(database)[0]
res = client.post("/report", data=dict(
    FORM, demo="1", qr_token=valid, qr_hostname="DEMO-PC",
    form_started=appmod.sign_form_timestamp(_time.time() - 30),
))
check("a demo POST with a VALID token is still discarded",
      counts(database)[0] == before_valid)
check("and it says so rather than reporting success",
      "NOT saved" in unquote_plus(res.headers.get("Location", "")))


print("\n=== 4. The real path is untouched ===")
token = appmod.sign_computer("DEMO-PC")
html = client.get(f"/report?computer=DEMO-PC&t={token}").get_data(as_text=True)
check("a signed link still opens the form", 'name="problem_type"' in html)
check("and shows NO demo banner", "Preview only" not in html)

before_reports = counts(database)[0]
res = client.post("/report", data=dict(
    FORM, qr_token=token, qr_hostname="DEMO-PC",
    form_started=appmod.sign_form_timestamp(__import__("time").time() - 30),
))
check("a genuine report IS still saved", counts(database)[0] == before_reports + 1)

# The demo flag must not turn a genuine submission into a discarded one
# when the switch is off.
appmod2, database2 = load(None)
client2 = appmod2.app.test_client()
before2 = counts(database2)[0]
res = client2.post("/report", data=dict(
    FORM, demo="1",
    qr_token=appmod2.sign_computer("DEMO-PC"), qr_hostname="DEMO-PC",
    form_started=appmod2.sign_form_timestamp(__import__("time").time() - 30),
))
check("with the switch off, a signed report carrying demo=1 is still SAVED",
      counts(database2)[0] == before2 + 1)

print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)
os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

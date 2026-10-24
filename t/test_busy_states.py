"""Requests that wait on an email show the shared busy state and disable
their button, so a second click cannot repeat them. t/ cannot run
JavaScript: this checks what the page is GIVEN; the click-test does the rest.
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


work = tempfile.mkdtemp(prefix="appease_busy_")
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

appmod.app.config["TESTING"] = True
conn = sqlite3.connect(database.DB_PATH)
conn.execute("UPDATE admins SET password_hash = ?, must_change_password = 0, email = 'boss@lab.example' "
             "WHERE username = 'admin'", (generate_password_hash("Str0ng!Passw0rd"),))
conn.execute("INSERT INTO admins (username, password_hash, role, must_change_password) VALUES ('tech2', 'x', 'admin', 0)")
conn.commit()
conn.close()
admin = appmod.app.test_client()
admin.post("/login", data={"username": "admin", "password": "Str0ng!Passw0rd"})


def button(page, text):
    """The opening tag of the <button> whose label is text."""
    found = re.search(r"<button\b[^>]*>\s*" + re.escape(text) + r"\s*</button>", page)
    return found.group(0) if found else ""


# ---------------------------------------------------------------------------
print("1. Each slow button says what it is waiting on")
# ---------------------------------------------------------------------------
appmod.SMTP_HOST = "smtp.lab.example"
page = admin.get("/admins").get_data(as_text=True)
check("mail on: Create account -> 'Sending sign-in details...'",
      'data-busy="Sending sign-in details..."' in button(page, "Create account"))
check("mail on: Reset password's confirm -> 'Sending sign-in details...'",
      'data-busy="Sending sign-in details..."' in button(page, "Reset password")
      and "data-dt-confirm-ok" in button(page, "Reset password"))
check("mail on: Send test email -> 'Sending test email...'",
      'data-busy="Sending test email..."' in button(page, "Send test email"))
appmod.SMTP_HOST = ""
page = admin.get("/admins").get_data(as_text=True)
check("mail off: nothing is sent, so Create account says what it is doing instead",
      'data-busy="Creating account..."' in button(page, "Create account"))
check("mail off: and Reset password likewise",
      'data-busy="Resetting password..."' in button(page, "Reset password"))
check("mail off: the disabled test button needs no busy state",
      "disabled" in button(page, "Send test email") and "data-busy" not in button(page, "Send test email"))
check("Create account is outside its form (form=...), which is why the submitter is used",
      'form="adm-add-form"' in button(page, "Create account"))
settings_page = admin.get("/settings").get_data(as_text=True)
check("Settings' Save Details, also outside its form, keeps its busy label",
      'data-busy="Saving..."' in button(settings_page, "Save Details") and 'form="' in button(settings_page, "Save Details"))

# ---------------------------------------------------------------------------
print("2. The script that acts on them (source only - click-test it)")
# ---------------------------------------------------------------------------
script = re.search(r"window\.markBusy = function.*?\}, true\);", page, re.S)
script = script.group(0) if script else ""
check("base.html defines one shared markBusy", "window.markBusy = function (busyButton)" in script)
check("...which disables the button", "busyButton.disabled = true;" in script)
check("the submit listener uses the button that submitted, so form=... buttons are found",
      "event.submitter && event.submitter.hasAttribute('data-busy')" in script)
check("a second submit while busy is stopped",
      re.search(r"getAttribute\('data-busy-on'\) === '1'\) \{\s*event\.preventDefault\(\);", script) is not None)
check("the busy look waits for the other listeners, and is skipped if one refused the submit",
      re.search(r"setTimeout\(function \(\) \{\s*if \(!event\.defaultPrevented\) \{\s*window\.markBusy\(busyButton\);",
                script) is not None)
table_js = open(os.path.join(server_dir, "static", "js", "data-table.js"), encoding="utf-8").read()
check("a confirm dialog's busy OK button uses the same markBusy",
      "ok.hasAttribute('data-busy') && typeof window.markBusy === 'function'" in table_js
      and "window.markBusy(ok);" in table_js)
check("...stays open, with Cancel disabled",
      re.search(r"window\.markBusy\(ok\);\s*dialog\.setAttribute\('data-dt-busy', ''\);", table_js) is not None
      and "b.disabled = true;" in table_js)
check("...and Esc does nothing while it is busy",
      re.search(r"addEventListener\('cancel', function \(event\) \{\s*if \(dialog\.hasAttribute\('data-dt-busy'\)\) "
                r"\{ event\.preventDefault\(\); \}", table_js) is not None)
check("a confirm without data-busy still closes at once (Delete is unchanged)",
      re.search(r"\} else \{\s*dialog\.close\(\);\s*\}", table_js) is not None)

print()
print(f"{passed} passed, {len(failed)} failed")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

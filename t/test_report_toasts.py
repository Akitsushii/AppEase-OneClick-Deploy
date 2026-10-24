"""The report pages show their confirmations as toasts, not inline banners."""
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


work = tempfile.mkdtemp(prefix="appease_toasts_")
server_dir = os.path.join(work, "server")
shutil.copytree(
    os.path.join(ROOT, "server"), server_dir,
    ignore=shutil.ignore_patterns("appease.db*", "__pycache__", "*.pyc", ".env"),
)
sys.path.insert(0, server_dir)
os.chdir(server_dir)
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
os.environ["REPORT_QR_SECRET"] = "t" * 64

import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()
conn = sqlite3.connect(database.DB_PATH)
conn.execute("INSERT OR IGNORE INTO computers (hostname, ip_address, status) "
             "VALUES ('TOAST-PC', '10.0.0.5', 'online')")
conn.commit()
conn.close()

import app as appmod  # noqa: E402
appmod.app.config["TESTING"] = True
client = appmod.app.test_client()
token = appmod.sign_computer("TOAST-PC")


def form_page(**args):
    query = "&".join(f"{k}={v}" for k, v in args.items())
    url = f"/report?computer=TOAST-PC&t={token}" + (("&" + query) if query else "")
    return client.get(url).get_data(as_text=True)


print("\n=== 1. Infrastructure ===")
html = form_page()
check("the form page has a toast container", 'id="toast-container"' in html)
check("it loads toasts.js", "js/toasts.js" in html)
check("app.css is still loaded (it carries the toast styles)", "css/app.css" in html)

css = open(os.path.join(server_dir, "static", "css", "app.css"), encoding="utf-8").read()
# The styles have to actually exist, or the toast renders as unstyled text.
for rule in ("#toast-container", ".toast", ".toast-show", ".toast-success", ".toast-error"):
    check(f"app.css defines {rule}", rule in css)

gate = client.get("/report").get_data(as_text=True)
check("the gate page has a toast container", 'id="toast-container"' in gate)
check("the gate loads toasts.js", "js/toasts.js" in gate)


print("\n=== 2. The old inline banners are gone ===")
html = form_page(success="Report+sent")
check("no green inline banner", "bg-green-50" not in html)
check("the success text is not rendered as page content",
      ">Report sent<" not in html)
check("it is handed to showToast instead", "showToast(" in html)
check("the message is in the script", "Report sent" in html)
check("as a success toast", "'success'" in html)

html = form_page(error="Something+went+wrong")
check("no red inline banner", "bg-red-50" not in html)
check("the error is handed to showToast", "Something went wrong" in html)
check("as an error toast", "'error'" in html)


print("\n=== 3. Only what was passed is announced ===")
html = form_page()
check("a plain page calls showToast for nothing",
      "window.showToast(" not in html.split('id="toast-container"')[1])
html = form_page(success="Only+this")
check("a success-only page does not also fire an error toast",
      html.count("window.showToast(") == 1)


print("\n=== 4. Quoting ===")
# These strings come from the server and can contain an apostrophe, which
# would end a bare-quoted JS string early and break the whole script.
html = form_page(success="It%27s+sent%2C+don%27t+worry")
script = html.split('id="toast-container"')[1]
# Inside a <script>, &#39; is NOT an entity - the JS parser sees those six
# literal characters, so a toast would read "It&#39;s sent". tojson escapes
# for a script context and produces \u0027 instead.
check("the apostrophe is escaped for JS, not for HTML", "&#39;" not in script)
check("it is escaped as a JS escape", "\\u0027" in script)
check("the script still parses as one statement",
      html.count("document.addEventListener('DOMContentLoaded'") >= 1)
html = form_page(success="%3C/script%3Ealert(1)")
check("a closing script tag cannot terminate the block early",
      "</script>alert(1)" not in html)


print("\n=== 5. The gate keeps its inline error ===")
# Errors there say "that code did not match", which belongs beside the box
# you just typed into rather than floating away after four seconds.
gate = client.get("/report?error=Wrong+code").get_data(as_text=True)
check("the gate error is still inline", "gate-msg error" in gate)
check("and reads on the page", "Wrong code" in gate)

gate = client.get("/report?success=Report+sent").get_data(as_text=True)
check("the gate success is NOT inline", "gate-msg ok" not in gate)
check("the gate success is a toast", "showToast(" in gate and "Report sent" in gate)


print("\n=== 6. Nothing else changed ===")
html = form_page()
check("the form still renders", 'name="problem_type"' in html)
check("the computer is still preselected", "TOAST-PC" in html)
check("the gate still gates", "Code on the sticker" in client.get("/report").get_data(as_text=True))

print("\n=== 7. The report pages are not cached ===")
# Pressing Back after sending must not re-show the FORM from cache: the
# gate would not run, a used link would look live, and on a shared lab PC
# the next student would see the last one's details.
res = client.get(f"/report?computer=TOAST-PC&t={token}")
cache = res.headers.get("Cache-Control", "")
check("the form says no-store", "no-store" in cache)
check("not merely no-cache, which still stores and revalidates",
      "no-store" in cache)
check("max-age is zero", "max-age=0" in cache)
check("legacy headers are set too for old proxies",
      res.headers.get("Pragma") == "no-cache" and res.headers.get("Expires") == "0")

res = client.get("/report")
check("the GATE is also no-store", "no-store" in res.headers.get("Cache-Control", ""))

res = client.post("/report/access", data={"qr_code": "XXXXXX"})
check("the code-entry POST response is no-store",
      "no-store" in res.headers.get("Cache-Control", ""))

# Scoped, not app-wide: admin pages sit behind a login on a LAN and there
# is nothing to gain by making them uncacheable.
res = client.get("/static/css/app.css")
check("static files are NOT forced no-store",
      "no-store" not in res.headers.get("Cache-Control", ""))
check("the endpoint set names both public report endpoints",
      appmod.NO_STORE_ENDPOINTS == {"student_problem_report_form", "report_access"})

# X-Robots-Tag must survive alongside Cache-Control.
res = client.get(f"/report?computer=TOAST-PC&t={token}")
check("the noindex header still applies",
      "noindex" in res.headers.get("X-Robots-Tag", ""))


print("\n=== 8. Required fields are marked as required ===")
html = form_page()
# All three student fields are ENFORCED - by validate_reporter_fields(),
# the browser validators and applyRoleBlock() - so the labels must say so.
for field, label in [("reporter_name", "Full name"),
                     ("reporter_role", "Who is reporting this?"),
                     ("reporter_department", "Department"),
                     ("reporter_student_number", "Student number"),
                     ("reporter_program", "Program"),
                     ("reporter_section", "Year &amp; Section")]:
    marker = f'<label for="{field}">{label} <span class="rep-req">*</span></label>'
    check(f"{label} is marked required", marker in html)

# And the server must actually refuse each one, or the marker lies.
for missing, expected in [("reporter_student_number", "Student number is required."),
                          ("reporter_program", "Please select your program."),
                          ("reporter_section", "Year and section is required.")]:
    fields = {"reporter_role": "student", "reporter_name": "Juan Dela Cruz",
              "reporter_student_number": "2023000000", "reporter_program": "BSIT",
              "reporter_section": "4A", "reporter_year_level": "4"}
    fields[missing] = ""
    values, error = appmod.validate_reporter_fields(fields)
    check(f"the server refuses a blank {missing}", values is None and error)
    check(f"and refuses it as MISSING, not as badly formatted", error == expected)


print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)
os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)
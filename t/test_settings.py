"""The Settings page: /profile lands here WITH its query string (a dropped
?error= reads as a save that worked), the sidebar entry is not admin-gated,
and there is exactly ONE tablist. The controller's behaviour needs a browser.
"""
import os
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile

from werkzeug.security import generate_password_hash

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


work = tempfile.mkdtemp(prefix="appease_settings_")
server_dir = os.path.join(work, "server")
shutil.copytree(
    os.path.join(ROOT, "server"), server_dir,
    ignore=shutil.ignore_patterns("appease.db*", "__pycache__", "*.pyc", ".env",
                                  "uploads", "staging", "report_photos", "avatars"),
)
sys.path.insert(0, server_dir)
os.chdir(server_dir)

import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()

os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
import app as appmod  # noqa: E402
appmod.app.config["TESTING"] = True

TEMPLATES = os.path.join(server_dir, "templates")
APP_CSS = os.path.join(server_dir, "static", "css", "app.css")


def raw():
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def make(username, role, password="Settings!2026"):
    conn = raw()
    conn.execute("DELETE FROM admins WHERE username = ?", (username,))
    conn.execute(
        "INSERT INTO admins (username, password_hash, role, must_change_password, email) "
        "VALUES (?, ?, ?, 0, ?)",
        (username, generate_password_hash(password), role, username + "@example.com"),
    )
    conn.commit()
    conn.close()


def signed_in(username, password="Settings!2026"):
    c = appmod.app.test_client()
    c.post("/login", data={"username": username, "password": password})
    return c


make("settings.admin", "admin")
make("settings.faculty", "faculty")
admin = signed_in("settings.admin")
faculty = signed_in("settings.faculty")


print("\n=== 1. The route ===")
r = admin.get("/settings")
check("/settings renders for an admin", r.status_code == 200)
admin_html = r.get_data(as_text=True)

r = faculty.get("/settings")
check("/settings renders for faculty too", r.status_code == 200)
faculty_html = r.get_data(as_text=True)

anon = appmod.app.test_client()
check("/settings still requires a session",
      anon.get("/settings").status_code in (302, 401, 403))

# Neither role is gated out - that is the whole reason the sidebar entry does
# not live in the admin-only System group.
src_app = open(os.path.join(server_dir, "app.py"), "rb").read().decode("utf-8")
route = re.search(r'@app\.route\("/settings".*?\ndef settings\(\):', src_app, re.S)
check("app.py declares the /settings route", route is not None)
if route:
    check("Settings is login_required, not admin_required",
          "@login_required" in route.group(0) and "@admin_required" not in route.group(0))
check("/settings accepts the form posts as well as GET",
      route is not None and 'methods=["GET", "POST"]' in route.group(0))


print("\n=== 2. /profile still lands, and carries its query string ===")
r = admin.get("/profile")
check("/profile redirects", r.status_code in (301, 302))
check("...to /settings", r.headers.get("Location", "").endswith("/settings"))

# The decisive one. A bare redirect drops these, and the page then renders as
# though the save had worked.
r = admin.get("/profile?error=Current+password+is+incorrect.&field=current_password")
loc = r.headers.get("Location", "")
check("/profile carries ?error= through the redirect", "error=" in loc)
check("/profile carries ?field= through the redirect", "field=" in loc)
check("the carried error survives verbatim", "Current" in loc and "incorrect" in loc)

r = admin.post("/profile", data={"form_type": "update_name"})
check("/profile is GET-only now, so a stale form post cannot half-work",
      r.status_code == 405)

# url_for kwargs are not inert - _external/_scheme/_anchor are url_for's own -
# so the redirect must not be built by splatting the query into url_for.
prof = re.search(r'@app\.route\("/profile".*?\n    return redirect\(target\)', src_app, re.S)
check("the /profile redirect is still present in app.py", prof is not None)
if prof:
    # Past the docstring, which names url_for(**request.args) precisely in
    # order to say it is not used.
    body = prof.group(0).split('"""')[-1]
    check("it builds the target by concatenation, not url_for(**request.args)",
          "request.query_string" in body and "**request.args" not in body)


print("\n=== 3. The forms post to /settings ===")
tpl = open(os.path.join(TEMPLATES, "settings.html"), "rb").read().decode("utf-8")
check("settings.html exists and profile.html is gone",
      not os.path.exists(os.path.join(TEMPLATES, "profile.html")))
check("no form still points at the old endpoint", "url_for('profile')" not in tpl)
check("all four forms post to settings", tpl.count("url_for('settings')") == 4)
check("the rendered page posts to /settings", 'action="/settings"' in admin_html)
check("the rendered page posts nowhere else",
      'action="/profile"' not in admin_html)

# A real round trip through each form_type.
r = admin.post("/settings", data={"form_type": "update_name", "first_name": "Ada",
                                  "middle_initial": "B", "last_name": "Lovelace"})
check("a name update posts and redirects", r.status_code in (301, 302))
check("it redirects back to settings, not profile",
      "/settings" in r.headers.get("Location", ""))
conn = raw()
row = conn.execute("SELECT first_name, last_name FROM admins WHERE username = 'settings.admin'").fetchone()
conn.close()
check("the name actually saved", row["first_name"] == "Ada" and row["last_name"] == "Lovelace")


print("\n=== 4. error_field still opens the right section and dialog ===")
r = admin.post("/settings", data={"form_type": "change_password",
                                  "current_password": "wrong-on-purpose",
                                  "new_password": "Whatever!2026",
                                  "confirm_password": "Whatever!2026"})
loc = r.headers.get("Location", "")
check("a wrong current password redirects rather than saving",
      r.status_code in (301, 302))
check("it names the field it failed on", "field=current_password" in loc)
check("and carries the message", "error=" in loc)

after = admin.get("/settings?error=Current+password+is+incorrect.&field=current_password")
after_html = after.get_data(as_text=True)
check("the page renders the failing field back to the controller",
      "current_password" in after_html)
# The map that turns that into an open section, and for the sign-in fields an
# open dialog. Source-level: a Python harness cannot run the controller.
check("the field -> section map survives",
      "'current_password': 'profile-tab-password'" in tpl)
for field in ["username", "email", "account_current_password"]:
    check(f"the field -> dialog map still covers {field}",
          f"'{field}': 'profile-signin-dialog'" in tpl)


print("\n=== 5. One tablist, rendered as a rail ===")

def markup_only(html):
    """The page without its inline <script>. The tab controller queries
    '[role="tablist"]' and '[role="tab"]' by selector, so counting roles over
    the raw page counts the code as well as the markup.
    """
    return re.sub(r"<script\b.*?</script>", "", html, flags=re.S)


admin_markup = markup_only(admin_html)
check("there is exactly one tablist on the page",
      admin_markup.count('role="tablist"') == 1)
check("it is the rail, not a nested set", admin_html.count("settings-rail") == 1)
# Scoped to the nav ELEMENT. Searching the whole page passes on the
# controller's own comment, which contains the same string.
rail_nav = re.search(r'<nav[^>]*class="settings-rail"[^>]*>', admin_markup)
check("the rail element is there", rail_nav is not None)
if rail_nav:
    check("the rail is vertical",
          'aria-orientation="vertical"' in rail_nav.group(0))
    check("the horizontal strip is gone",
          'aria-orientation="horizontal"' not in rail_nav.group(0))
# Asserted exactly, not as a floor: Settings has ONE flat set of sections,
# and a nested tablist would show up here as a jump.
check("its four sections are still tabs", admin_markup.count('role="tab"') == 4)
check("with four panels to match", admin_markup.count('role="tabpanel"') == 4)
check("Appearance is one of them",
      'id="profile-tab-appearance"' in admin_markup
      and 'id="profile-panel-appearance"' in admin_markup)
check("the shell is the grid hook", "settings-shell" in admin_html)

# aria-orientation="vertical" means Up/Down are the keys that must work.
check("the controller handles ArrowDown", "'ArrowDown'" in tpl)
check("the controller handles ArrowUp", "'ArrowUp'" in tpl)
check("Left/Right stay for the folded strip",
      "'ArrowRight'" in tpl and "'ArrowLeft'" in tpl)
check("Home/End still work", "'Home'" in tpl and "'End'" in tpl)


print("\n=== 6. The page title and the breadcrumb ===")
check("the browser title says Settings", "<title>Settings - AppEase</title>" in admin_html)
check("the breadcrumb says Settings, not My Profile",
      'aria-current="page">Settings<' in admin_html)
# ">My Profile<" still appears, and should: it is the first rail
# item's label. What must not survive is the CRUMB, pinned above.
check("the crumb is not the old page title",
      'aria-current=\"page\">My Profile<' not in admin_html)


print("\n=== 7. The sidebar ===")
base = open(os.path.join(TEMPLATES, "base.html"), "rb").read().decode("utf-8")
acct = re.search(r'<div role="group" aria-labelledby="sb-group-account">.*?</div>', base, re.S)
check("base.html has an Account group", acct is not None)

# The point of the whole placement: System is admin-only, so Settings must not
# be in it, and its own group must not be gated either.
# That the group is ungated is asserted BEHAVIOURALLY below - the faculty
# render either carries the entry or it does not, and that check fails the
# moment anyone wraps this block in a role test. Counting {% endif %} here
# would count every inline data-active conditional too.
check("the System group is still admin-only",
      re.search(r"\{% if session\.get\('admin_role'\) == 'admin' %\}\s*"
                r'<div role="group" aria-labelledby="sb-group-system">', base) is not None)

# The SIDEBAR span specifically. ">Settings</span>" alone also matches the
# breadcrumb, which says Settings on this page - so that check passed even
# with the sidebar entry removed entirely.
SIDEBAR_SETTINGS = '<span class="sb-label">Settings</span>'
check("an admin sees the Settings entry", SIDEBAR_SETTINGS in admin_html)
check("faculty see it too - the actual reason it is not under System",
      SIDEBAR_SETTINGS in faculty_html)
check("faculty still do not see the System group", "sb-group-system" not in faculty_html)
check("faculty still do not see Main", "sb-group-main" not in faculty_html)

# Notifications is a log, so it sits beside the other log-shaped page, not
# inside Settings.
# Sliced, not matched by regex. `.*?</div>\s*</div>` ran 8kB past the group
# and swallowed the System group with it, so anything asserted "inside
# Reports" was really asserted against half the sidebar.
r_start = base.index('aria-labelledby="sb-group-reports"')
r_end = base.index("{% if session.get('admin_role') == 'admin' %}", r_start)
reports = base[r_start:r_end]
check("base.html still has a Reports group", len(reports) > 0)
check("the Reports slice stops before the System group",
      "sb-group-system" not in reports)
# The LINK, not the bare endpoint name: the data-active conditional on the
# same element also mentions notifications_page, so a check for the bare name
# stays satisfied even if the href is repointed elsewhere.
check("Notifications sits under Reports", "url_for('notifications_page')" in reports)
check("beside Problem Reports", "problem_reports_page" in reports)
check("Notifications is NOT a section of Settings",
      "notifications" not in tpl.lower())
SIDEBAR_NOTIF = '<span class="sb-label">Notifications</span>'
check("both roles get the Notifications entry",
      SIDEBAR_NOTIF in admin_html and SIDEBAR_NOTIF in faculty_html)

# A new page that is not on the public-host block list ships untested against
# that host. Asserted here because deleting the entry there cannot make the
# guard suite itself fail.
guard_src = open(os.path.join(ROOT, "t", "test_public_guard.py"), "rb").read().decode("utf-8")
check("/settings is on the public-host block list", '"/settings"' in guard_src)

# Notifications keeps its own page.
check("/notifications is still its own page", admin.get("/notifications").status_code == 200)
check("faculty can still reach it", faculty.get("/notifications").status_code == 200)

check("no account menu still points at /profile", "url_for('profile')" not in base)
check("both account menus point at Settings", base.count("url_for('settings')") >= 3)


print("\n=== 8. The rail's CSS ===")
css = open(APP_CSS, "rb").read().decode("utf-8")
shell = re.search(r"\n\.settings-shell \{(.*?)\n\}", css, re.S)
check("app.css lays out the shell", shell is not None)
if shell:
    check("it is a two-track grid: rail then panel",
          "display: grid" in shell.group(1)
          and re.search(r"grid-template-columns:\s*232px", shell.group(1)) is not None)
rail = re.search(r"\n\.settings-rail \{(.*?)\n\}", css, re.S)
check("app.css styles the rail", rail is not None)
if rail:
    check("the rail stacks its items", "flex-direction: column" in rail.group(1))
    check("and follows a long panel down", "position: sticky" in rail.group(1))
# Keyed on the attribute the controller writes, so there is one source of
# truth for which section is open.
# The BASE rule, not just any occurrence: the same selector appears in
# the 1024px fold rule, so an unscoped search stays satisfied when the
# base one is rekeyed to a class. The base rule is the one that paints
# the background.
active = re.search(r'\.settings-rail > \[role="tab"\]\[aria-selected="true"\] \{(.*?)\n\}',
                   css, re.S)
check("the active marker keys on aria-selected",
      active is not None and 'background:' in active.group(1))
narrow = re.search(r"@media \(max-width: 1024px\) \{(.*?)\n\}\n", css, re.S)
check("app.css has the fold-back rule", narrow is not None)
if narrow:
    check("the rail folds to a horizontal strip below 1024",
          "flex-direction: row" in narrow.group(1))
    check("and stops being sticky when it does",
          "position: static" in narrow.group(1))

# Plain classes in app.css need no Tailwind rebuild - the documented escape
# hatch. Asserted so nobody moves them into a utility later.
tw = open(os.path.join(server_dir, "static", "css", "tailwind.css"), "rb").read().decode("utf-8")
for cls in ["settings-shell", "settings-rail"]:
    check(f".{cls} is a plain app.css class, so no rebuild is needed",
          ("." + cls) in css and ("." + cls) not in tw)


print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)
os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

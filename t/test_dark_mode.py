"""Dark mode. The theme is stamped SERVER-SIDE before the first <script>
(asserted on byte positions: a script-applied class flashes on every
navigation); both pre-paint lines follow the theme; one macro serves both
toggles; no dark: utility forces a rebuild; no localStorage; and the
out-of-scope pages stay light on purpose.

A Python harness cannot click: the toggle, the cookie round-trip in a real
browser and the icon swap need a manual click-test.
"""
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
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


# ---------------------------------------------------------------- staging --
# Excludes appease.db* and .env deliberately: suites that forgot this ran
# against the real populated database and produced false failures.
work = tempfile.mkdtemp(prefix="appease_dark_")
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

# app.py refuses to start without a secret and the staged copy carries no .env.
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
import app as appmod  # noqa: E402
appmod.app.config["TESTING"] = True

TEMPLATES = os.path.join(server_dir, "templates")
APP_CSS = os.path.join(server_dir, "static", "css", "app.css")
TAILWIND_CSS = os.path.join(server_dir, "static", "css", "tailwind.css")


def template(name):
    with open(os.path.join(TEMPLATES, name), "rb") as fh:
        return fh.read().decode("utf-8")


APP_CSS_SRC = open(APP_CSS, "rb").read().decode("utf-8")

SCHEME = re.compile(r'name="color-scheme"\s+content="(\w+)"')
PREPAINT = re.compile(r"html \{ background-color: (#\w+); \}")


def _hex6(v):
    v = v.strip().lower()
    return "#" + "".join(c * 2 for c in v[1:]) if len(v) == 4 else v


# The pre-paint literals stand in for --color-canvas until the stylesheets
# load, so each must equal the canvas its theme really paints: light from
# the compiled tailwind.css, dark from app.css's html.dark block.
LIGHT_CANVAS = _hex6(re.search(r"--color-canvas:\s*(#[0-9a-fA-F]+)",
                               open(TAILWIND_CSS, encoding="utf-8").read()).group(1))
_dark_block = re.search(r"html\.dark\s*\{(.*?)\n\}", APP_CSS_SRC, re.S).group(1)
DARK_CANVAS = _hex6(re.search(r"--color-canvas:\s*(#[0-9a-fA-F]+)", _dark_block).group(1))
# A real toggle BUTTON, not the controller's selector string or a comment.
TOGGLE_BUTTON = re.compile(r"<button[^>]*\bdata-theme-toggle\b", re.S)
HTML_TAG = re.compile(r"<html\b[^>]*>")
SCRIPT = re.compile(r"(?s)<script\b[^>]*>.*?</script>")


def html_tag(html):
    """The opening <html> tag itself: base.html's controller mentions the
    stamped class="dark" in a JS comment, present on a light page too."""
    m = HTML_TAG.search(html)
    return m.group(0) if m else ""


def is_dark(html):
    return "dark" in html_tag(html).split("class=")[-1] if "class=" in html_tag(html) else False


def markup_only(html):
    """Rendered HTML with <script> bodies removed: the tab controller holds
    role selectors as STRINGS, which would count as elements."""
    return SCRIPT.sub("", html)


def strip_js_comments(html):
    """Remove // and /* */ comments from <script> bodies: base.html explains
    in prose why localStorage is NOT used, which a plain substring search
    would match."""
    def scrub(m):
        body = m.group(0)
        body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
        body = re.sub(r"^\s*//.*$", "", body, flags=re.M)
        return body
    return re.sub(r"(?s)<script\b[^>]*>.*?</script>", scrub, html)


conn = sqlite3.connect(database.DB_PATH)
conn.execute("DELETE FROM admins WHERE username = ?", ("dark.admin",))
conn.execute(
    "INSERT INTO admins (username, password_hash, role, must_change_password, email) "
    "VALUES (?, ?, ?, 0, ?)",
    ("dark.admin", generate_password_hash("Dark!2026"), "admin", "dark@example.com"),
)
conn.commit()
conn.close()


def client(theme=None):
    c = appmod.app.test_client()
    c.post("/login", data={"username": "dark.admin", "password": "Dark!2026"})
    if theme is not None:
        c.set_cookie("theme", theme)
    return c


PAGES = ["/", "/computers", "/settings"]

light = client()
dark = client("dark")


print("\n=== 1. Server-side stamping ===")
for url in PAGES:
    lh = light.get(url).get_data(as_text=True)
    dh = dark.get(url).get_data(as_text=True)
    check(f"{url} renders in both themes",
          light.get(url).status_code == 200 and dark.get(url).status_code == 200)
    check(f"{url} light: <html> carries NO dark class",
          html_tag(lh) == '<html lang="en">')
    check(f"{url} dark: <html> carries class=\"dark\"",
          html_tag(dh) == '<html lang="en" class="dark">')
    check(f"{url} light: color-scheme is light", SCHEME.search(lh).group(1) == "light")
    check(f"{url} dark: color-scheme is dark", SCHEME.search(dh).group(1) == "dark")
    check(f"{url} light: pre-paint canvas matches --color-canvas",
          PREPAINT.search(lh).group(1) == LIGHT_CANVAS)
    check(f"{url} dark: pre-paint canvas matches the dark --color-canvas",
          PREPAINT.search(dh).group(1) == DARK_CANVAS)


print("\n=== 2. Nothing in the paint path is JavaScript ===")
# The anti-flash guarantee. Position, not presence: a class applied by a
# script - even an inline one in <head> - runs after the parser has seen
# <html>, which is a white flash.
for url in PAGES:
    dh = dark.get(url).get_data(as_text=True)
    first_script = dh.find("<script")
    check(f"{url}: the dark <html> tag precedes the first <script>",
          0 <= HTML_TAG.search(dh).start() < first_script and is_dark(dh))
    check(f"{url}: color-scheme meta precedes the first <script>",
          0 <= SCHEME.search(dh).start() < first_script)
    check(f"{url}: pre-paint <style> precedes the first <script>",
          0 <= PREPAINT.search(dh).start() < first_script)
    check(f"{url}: pre-paint precedes both stylesheet links",
          PREPAINT.search(dh).start() < dh.find('rel="stylesheet"'))


print("\n=== 3. A cookie that is not exactly 'dark' is light ===")
# Strict equality on purpose: a truncated, stale or hand-edited cookie should
# land on the theme every page is known to render correctly.
for value in ["DARK", "Dark", "nonsense", "", " dark", "dark ", "1", "true"]:
    c = client(value)
    h = c.get("/").get_data(as_text=True)
    check(f"theme={value!r} falls back to light",
          PREPAINT.search(h).group(1) == LIGHT_CANVAS
          and SCHEME.search(h).group(1) == "light"
          and not is_dark(h))

check("no cookie at all is light",
      PREPAINT.search(client().get("/").get_data(as_text=True)).group(1) == LIGHT_CANVAS)


print("\n=== 4. One mechanism, two entry points ===")
settings_dark = dark.get("/settings").get_data(as_text=True)
computers_dark = dark.get("/computers").get_data(as_text=True)

check("Settings renders exactly TWO toggle buttons",
      len(TOGGLE_BUTTON.findall(settings_dark)) == 2)
check("a non-Settings page renders exactly ONE toggle button",
      len(TOGGLE_BUTTON.findall(computers_dark)) == 1)

# Both buttons come from one macro, so the icon markup must be identical and
# must appear exactly twice per button pair. A hand-copied second button would
# satisfy "a toggle exists" and then drift on the next edit.
check("Settings carries two sun spans and two moon spans",
      settings_dark.count('class="theme-icon-sun"') == 2
      and settings_dark.count('class="theme-icon-moon"') == 2)
check("a non-Settings page carries one of each",
      computers_dark.count('class="theme-icon-sun"') == 1
      and computers_dark.count('class="theme-icon-moon"') == 1)

sun_paths = re.findall(r'class="theme-icon-sun".*?</span>', settings_dark, re.S)
check("both sun icons are byte-identical (proves one macro, not a copy)",
      len(sun_paths) == 2 and sun_paths[0] == sun_paths[1])

check("the toggle lives in exactly one template file",
      os.path.exists(os.path.join(TEMPLATES, "_theme_toggle.html")))
# Neither call site may carry its own copy of the glyphs.
check("base.html defines no sun/moon svg of its own",
      "theme-icon-sun" not in template("base.html"))
check("settings.html defines no sun/moon svg of its own",
      "theme-icon-sun" not in template("settings.html"))

check("both buttons report the dark state when dark",
      settings_dark.count('aria-pressed="true"') == 2)
check("both buttons report the light state when light",
      light.get("/settings").get_data(as_text=True).count('aria-pressed="false"') == 2)
check("the accessible name says where you are GOING (dark -> light)",
      'aria-label="Switch to light mode"' in settings_dark)
check("the accessible name says where you are GOING (light -> dark)",
      'aria-label="Switch to dark mode"' in light.get("/settings").get_data(as_text=True))


print("\n=== 5. The Settings entry point ===")
check("Appearance tab is in the rail", 'id="profile-tab-appearance"' in settings_dark)
check("Appearance panel exists", 'id="profile-panel-appearance"' in settings_dark)
check("the tab controls the panel",
      'aria-controls="profile-panel-appearance"' in settings_dark)
check("the panel is labelled by the tab",
      'aria-labelledby="profile-tab-appearance"' in settings_dark)
check("the panel starts hidden like the other non-default panels",
      re.search(r'id="profile-panel-appearance"[^>]*\shidden', settings_dark) is not None)
# The rail is still ONE tablist.
settings_markup = markup_only(settings_dark)
check("Settings still has exactly one tablist",
      settings_markup.count('role="tablist"') == 1)
check("the rail now has four tabs", settings_markup.count('role="tab"') == 4)
check("every tab has a panel", settings_markup.count('role="tabpanel"') == 4)
# No form: the theme is a cookie written in the browser, so a Save button
# would be a lie.
panel = re.search(r'id="profile-panel-appearance".*?(?=id="profile-panel-activity")',
                  settings_dark, re.S).group(0)
check("the Appearance panel posts no form", "<form" not in panel)


print("\n=== 6. No rebuild required ===")
# A dark: utility in a template means build-css.bat has to run, and until it
# does the page renders unstyled in dark.
compiled = open(TAILWIND_CSS, "rb").read().decode("utf-8")
check("the compiled tailwind.css still contains zero dark: utilities",
      len(re.findall(r"\.dark\\:", compiled)) == 0)
for name in sorted(os.listdir(TEMPLATES)):
    if not name.endswith(".html"):
        continue
    src = template(name)
    src_nc = re.sub(r"\{#.*?#\}", "", src, flags=re.S)
    src_nc = re.sub(r"<!--.*?-->", "", src_nc, flags=re.S)
    hits = re.findall(r'class="[^"]*?(?<![\w-])(dark:[\w:./\[\]-]+)', src_nc)
    check(f"{name} uses no dark: utility", not hits)

# Basecoat's canonical theme-switcher markup carries size-8, which is NOT in
# the compiled output - it would silently do nothing.
def without_jinja_comments(src_text):
    return re.sub(r"\{#.*?#\}", "", src_text, flags=re.S)


check("no template uses the uncompiled size-8 class",
      not any("size-8" in without_jinja_comments(template(n))
              for n in os.listdir(TEMPLATES) if n.endswith(".html")))
check("size-8 is indeed absent from the compiled CSS",
      not re.search(r"\.size-8\{", compiled))


print("\n=== 7. No web storage, no Basecoat theme helper ===")
for url in PAGES:
    code = strip_js_comments(dark.get(url).get_data(as_text=True))
    check(f"{url}: no localStorage in executable JS", "localStorage" not in code)
    check(f"{url}: no sessionStorage in executable JS", "sessionStorage" not in code)
    check(f"{url}: does not call basecoat.theme", "basecoat.theme" not in code)

controller = strip_js_comments(template("base.html"))
check("the controller binds EVERY toggle, not two known ids",
      "querySelectorAll('[data-theme-toggle]')" in controller)
check("the controller writes the theme cookie",
      re.search(r"document\.cookie\s*=\s*'theme='", controller) is not None)
check("the cookie carries path, a year and SameSite, like sidebar_collapsed",
      re.search(r"path=/; max-age=31536000; SameSite=Lax", controller) is not None)
check("the controller updates the stale color-scheme meta on toggle",
      'meta[name="color-scheme"]' in controller)
check("the controller keeps BOTH buttons in sync",
      "aria-pressed" in controller and "aria-label" in controller)


print("\n=== 8. Tokens in app.css ===")
check("app.css declares an html.dark block", "\nhtml.dark {" in APP_CSS_SRC)
dark_block = re.search(r"\nhtml\.dark \{(.*?)\n\}", APP_CSS_SRC, re.S)
check("the html.dark block parses", dark_block is not None)
body = dark_block.group(1) if dark_block else ""


def token(name, where=None):
    m = re.search(r"--color-" + re.escape(name) + r":\s*(#[0-9a-fA-F]{3,8});",
                  where if where is not None else body)
    return m.group(1).lower() if m else None


SUPPLIED = {
    "primary": "#4173d8",
    "background": "#121113", "foreground": "#c1c1c1",
    "card": "#121212", "card-foreground": "#c1c1c1",
    "popover": "#121113", "popover-foreground": "#c1c1c1",
    "accent": "#333333", "accent-foreground": "#c1c1c1",
    "muted-foreground": "#888888",
    "border": "#222222", "input": "#222222",
    "sidebar": "#121212", "sidebar-foreground": "#c1c1c1",
    "sidebar-accent": "#333333", "sidebar-border": "#222222",
}
for name, value in SUPPLIED.items():
    check(f"dark --color-{name} is the supplied {value}", token(name) == value)

DERIVED = {"destructive": "#f87171", "ring": "#7ea6f0", "secondary": "#2a2a2a"}
for name, value in DERIVED.items():
    check(f"dark --color-{name} is the derived {value}", token(name) == value)

# AppEase's OWN tokens are what the templates use, so these make the markup
# flip.
OWN = {"ink": "#c1c1c1", "body": "#c1c1c1", "label": "#c1c1c1", "muted": "#888888",
       "canvas": "#121113", "line": "#222222", "field": "#222222"}
for name, value in OWN.items():
    check(f"dark --color-{name} is {value}", token(name) == value)

check("the dark canvas token matches base.html's inlined pre-paint literal",
      token("canvas") == PREPAINT.search(dark.get("/").get_data(as_text=True)).group(1))

root_block = re.search(r"\n:root \{(.*?)\n\}", APP_CSS_SRC, re.S)
check("app.css declares a :root block", root_block is not None)
check("the LIGHT ring is no longer the TweakCN orange",
      token("ring", root_block.group(1) if root_block else "") != "#d87943")
check("the LIGHT ring is the primary blue",
      token("ring", root_block.group(1) if root_block else "") == "#4173d8")
# Outside comments only, so a comment may still name the old value.
APP_CSS_CODE = re.sub(r"/\*.*?\*/", "", APP_CSS_SRC, flags=re.S)
check("the orange #d87943 is gone from app.css's actual rules",
      "#d87943" not in APP_CSS_CODE)


print("\n=== 9. Contrast of the derived values ===")


def _lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_colour):
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(a, b):
    la, lb = luminance(a), luminance(b)
    if la < lb:
        la, lb = lb, la
    return (la + 0.05) / (lb + 0.05)


BASE, CARD, INPUT, ACCENT = "#121113", "#121212", "#222222", "#333333"

# Destructive was chosen over #ef4444 specifically because of the accent
# surface - that is where a Delete row sits when hovered. If a future edit
# walks it back down the ramp, this is the assertion that should stop it.
for surface, name in [(BASE, "base"), (CARD, "card"), (INPUT, "input"), (ACCENT, "accent")]:
    check(f"destructive #f87171 clears 4.5:1 on {name} ({contrast('#f87171', surface):.2f})",
          contrast("#f87171", surface) >= 4.5)
check("destructive beats the light-mode red it replaces on the dark base",
      contrast("#f87171", BASE) > contrast("#b91c1c", BASE))
check("the light-mode red would have FAILED on the dark base (why it changed)",
      contrast("#b91c1c", BASE) < 3.0)

# The ring's job is to be visible ON AN INPUT, which is why it is not the
# primary itself.
check(f"ring #7ea6f0 clears 4.5:1 on the input fill ({contrast('#7ea6f0', INPUT):.2f})",
      contrast("#7ea6f0", INPUT) >= 4.5)
check("ring beats straight primary on the input fill (the reason it is lightened)",
      contrast("#7ea6f0", INPUT) > contrast("#4173d8", INPUT))

check("secondary is not equal to primary (the collision that was flagged)",
      token("secondary") != token("primary"))
check("secondary is distinguishable from border, unlike the #1e2128 candidate",
      contrast("#2a2a2a", INPUT) > contrast("#1e2128", INPUT))

check(f"body text #c1c1c1 clears 4.5:1 on the canvas ({contrast('#c1c1c1', BASE):.2f})",
      contrast("#c1c1c1", BASE) >= 4.5)
check(f"muted text #888888 clears 4.5:1 on the canvas ({contrast('#888888', BASE):.2f})",
      contrast("#888888", BASE) >= 4.5)
# The supplied pairing, asserted at its real value rather than assumed to pass.
# DELIBERATELY NOT the supplied #121113. That palette came from a theme-editor
# screenshot rather than a contrast decision, and this token is the label on
# every primary button and on the sidebar's active pill.
check("primary-foreground is white, overriding the supplied #121113",
      token("primary-foreground") == "#ffffff")
check("sidebar-primary-foreground agrees with it",
      token("sidebar-primary-foreground") == "#ffffff")
check("white beats the supplied #121113 on the primary fill",
      contrast("#ffffff", "#4173d8") > contrast("#121113", "#4173d8"))
# Asserted at its REAL value, not rounded up. 4.4981 misses the 4.5 minimum by
# 0.002 - perceptually meaningless, but claiming it passes would be wrong. A
# one-step darkening of the primary (#4072d7) clears it at 4.5591; that is a
# brand decision, not a side effect of fixing the foreground.
check("the white pairing is recorded at 4.4981:1, just under the 4.5 minimum",
      4.49 <= contrast("#ffffff", "#4173d8") < 4.50)
check("a one-step darker primary would clear 4.5 (the documented option)",
      contrast("#ffffff", "#4072d7") >= 4.5)


print("\n=== 9b. The topbar is not a white band ===")
# One class, every page. bg-canvas rather than bg-white changes nothing in
# LIGHT mode: the compiled --color-canvas and --color-white are the same #fff.
base_src = template("base.html")
topbar = re.search(r'<header class="app-topbar[^"]*"', base_src)
check("the topbar element is still there", topbar is not None)
check("the topbar no longer carries bg-white",
      topbar is not None and "bg-white" not in topbar.group(0))
check("the topbar paints from the canvas token",
      topbar is not None and "bg-canvas" in topbar.group(0))
_compiled = open(TAILWIND_CSS, "rb").read().decode("utf-8")
_canvas = re.search(r"--color-canvas:\s*([^;]+);", _compiled).group(1).strip()
_white = re.search(r"--color-white:\s*([^;]+);", _compiled).group(1).strip()
check("bg-canvas and bg-white are identical in light mode (" + _canvas + ")",
      _canvas == _white)
check("no rendered page carries a bg-white topbar",
      all("bg-white" not in re.search(r'<header class="app-topbar[^"]*"',
                                      dark.get(u).get_data(as_text=True)).group(0)
          for u in PAGES))


print("\n=== 9c. Pass B: the .dt-* and .tt-* families are tokenised ===")
# These families carry NO bare colour literal, so they follow the theme.
# Walked rule by rule: they are interleaved with other families, so a plain
# grep would count the neighbours too.

def _family_literals(css, family):
    """Bare colour literals inside rules whose selector mentions `family`.
    var(--token, #fallback) is not one: every token here is defined in both
    themes, so the fallback never fires."""
    nocom = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    stack, pending, hits = [], "", []

    def collect(chunk):
        bare = re.sub(r"var\(\s*--[\w-]+\s*,\s*[^)]*\)", "V", chunk)
        return (re.findall(r"#[0-9a-fA-F]{3,8}\b", bare)
                + re.findall(r"rgba?\([^)]*\)", bare))

    for ln in nocom.split("\n"):
        s = ln.strip()
        one = re.match(r"^\s*([^{}]+?)\s*\{\s*(.+?)\s*\}\s*$", ln)
        if one and not s.startswith("@"):
            sel = (pending + " " + one.group(1)).strip()
            pending = ""
            if family in sel:
                hits += collect(one.group(2))
            continue
        if "{" in ln:
            sel = (pending + ln.split("{")[0]).strip()
            stack.append(sel)
            pending = ""
            # A declaration can sit on the SAME line as the opening brace
            # (`.x { color: #fff;` with the rule continuing below). Scanning
            # only the selector here would let a literal in that position slip
            # through entirely - found by mutation-testing this very check.
            tail = ln.split("{", 1)[1]
            if family in sel and tail.strip():
                hits += collect(tail)
            continue
        if "}" in ln:
            if stack:
                stack.pop()
            pending = ""
            continue
        # A multi-line value continues on lines ending in "," not ";" - that
        # is how rgba(9,12,20,0.35) hid inside .deploy-dialog's two-line
        # box-shadow while the family reported clean. Anything inside a rule
        # counts as declaration text.
        if s and not s.endswith(";") and not s.endswith(",") \
                and ":" not in s and not stack:
            pending += ln + " "
            continue
        cur = stack[-1] if stack else ""
        if family in cur and s:
            hits += collect(ln)
    return hits


# .tt-* is not listed: its CSS is gone, so the check would pass vacuously.
# Section 9d guards that instead.
#
# DELIBERATE literals, excluded by value with the reason they stay. A
# translucent white overlay on a coloured progress fill reads correctly in
# both themes, and a token holding one value in both would be noise.
DELIBERATE = {
    "rgba(255, 255, 255, 0.45)": ".deploy-bar-active stripe overlay",
}
SWEPT = [
    (".dt-", "data-table"), (".deploy-", "deploy panel"),
    (".profile-", "profile"), (".choco-", "chocolatey search"),
    (".activity-", "dashboard activity"), (".tl-", "profile timeline"),
    (".notif-", "notifications"),
    (".settings-rail", "settings rail"),
    (".rs-", "report status/priority selects"),
    (".task-", "dashboard task rows and bulk bar"),
    (".pw-", "password policy hints"),
]
for family, label in SWEPT:
    found = [f for f in _family_literals(APP_CSS_SRC, family)
             if f not in DELIBERATE]
    check(f"{family}* ({label}) carries no bare colour literal"
          + (f" - found {sorted(set(found))[:4]}" if found else ""),
          not found)

# The exclusions must stay real: if one stops appearing, it is dead weight in
# the allowlist and should come out rather than sit there granting cover.
for _lit, _why in DELIBERATE.items():
    check(f"deliberate literal {_lit} is still present ({_why})",
          _lit in APP_CSS_SRC)

# Every one of these tokens must exist in BOTH blocks: one defined only in
# :root keeps its light value in dark mode, and looks fine until someone
# opens the component in dark.
PASS_B_TOKENS = [
    "color-line-strong", "color-row-selected", "color-invert",
    "color-invert-foreground", "color-invert-hover", "color-ring-soft",
    "shadow-pop",
    "tone-good-bg", "tone-good-fg", "tone-info-bg", "tone-info-fg",
    "tone-warn-bg", "tone-warn-fg", "tone-warn-border",
    "tone-bad-bg", "tone-bad-fg", "tone-bad-border", "tone-bad-soft",
    "tone-neutral-bg", "tone-neutral-fg",
    # shared surfaces and shadows
    "color-ink-soft", "tone-warn-soft", "color-backdrop", "color-scrim",
    "shadow-dropdown", "shadow-dialog",
    # the soft step for good and info
    "tone-good-soft", "tone-info-soft",
    # the primary as TEXT rather than as a fill
    "color-primary-text",
    # the dragged task row's lift
    "shadow-drag",
    # the logo's three squares
    "logo-sq-1", "logo-sq-2", "logo-sq-3",
]

# Tokens that are the SAME in both themes on purpose. They still earn a token
# (the value is named once instead of being repeated in markup), but the
# "differs between themes" rule does not apply to them.
SAME_IN_BOTH = {
    "color-danger-solid": "a saturated red reads as destructive on either ground",
    "color-danger-solid-foreground": "its label is white in both themes",
    "color-warn-strong": "a saturated amber border reads on either ground",
}
_root = re.search(r"\n:root \{(.*?)\n\}", APP_CSS_SRC, re.S).group(1)
_dark = re.search(r"\nhtml\.dark \{(.*?)\n\}", APP_CSS_SRC, re.S).group(1)


def _tok(block, name):
    m = re.search(r"--" + re.escape(name) + r":\s*([^;]+);", block)
    return m.group(1).strip() if m else None


for name in PASS_B_TOKENS:
    light_v, dark_v = _tok(_root, name), _tok(_dark, name)
    check(f"--{name} is defined in both themes",
          light_v is not None and dark_v is not None)
    check(f"--{name} actually differs between themes", light_v != dark_v)

for name, why in SAME_IN_BOTH.items():
    light_v, dark_v = _tok(_root, name), _tok(_dark, name)
    check(f"--{name} is declared in both themes", light_v and dark_v)
    # Restated rather than inherited by omission, so the intent is visible
    # in the dark block instead of being an absence someone has to notice.
    check(f"--{name} is deliberately identical ({why})", light_v == dark_v)

# LIGHT-MODE EQUIVALENCE. Each of these tokens replaced an exact literal, so
# light mode must not have moved. The three near-duplicates that were
# deliberately collapsed are asserted separately, below, with their deltas.
UNCHANGED = {
    "color-row-selected": "#eff6ff", "color-line-strong": "#d1d5db",
    "color-invert": "#111827", "color-invert-foreground": "#ffffff",
    "color-invert-hover": "#1f2937",
    "tone-good-bg": "#dcfce7", "tone-good-fg": "#166534",
    "tone-info-bg": "#dbeafe", "tone-info-fg": "#1d4ed8",
    "tone-warn-bg": "#fef3c7", "tone-warn-fg": "#92400e",
    "tone-warn-border": "#fde68a",
    "tone-bad-bg": "#fee2e2", "tone-bad-fg": "#b91c1c",
    "tone-bad-border": "#fecaca", "tone-bad-soft": "#fef2f2",
    "tone-neutral-bg": "#f3f4f6", "tone-neutral-fg": "#6b7280",
    "color-ink-soft": "#4b5563", "tone-warn-soft": "#fffbeb",
    "tone-good-soft": "#f0fdf4", "tone-info-soft": "#eff6ff",
    "color-primary-text": "#4173d8",
}
for name, literal in UNCHANGED.items():
    check(f"--{name} still resolves to its original light value {literal}",
          (_tok(_root, name) or "").lower() == literal)

# The same for the non-hex tokens: retuning a backdrop or a shadow in LIGHT
# mode must not pass silently.
UNCHANGED_RAW = {
    "color-ring-soft": "rgba(65, 115, 216, 0.18)",
    "color-backdrop": "rgba(9, 12, 20, 0.55)",
    "color-scrim": "rgba(9, 12, 20, 0.45)",
    "shadow-pop": "0 10px 24px rgba(16, 24, 40, 0.12)",
    "shadow-dropdown": "0 6px 16px rgba(0, 0, 0, 0.1)",
}
for name, literal in UNCHANGED_RAW.items():
    got = " ".join((_tok(_root, name) or "").split())
    check(f"--{name} still resolves to its original light value",
          got == literal)
# The dialog shadow is two-part, so it is matched on both of its halves.
_dialog = " ".join((_tok(_root, "shadow-dialog") or "").split())
check("--shadow-dialog keeps both of its original light layers",
      "0 10px 38px -10px rgba(9, 12, 20, 0.35)" in _dialog
      and "0 10px 20px -15px rgba(9, 12, 20, 0.2)" in _dialog)

# Every dark tone pair has to clear 4.5:1 on its own fill. The light set does
# not - its neutral pair is 4.39 - so this is the stricter of the two, and
# that is deliberate rather than accidental.
for tone in ["good", "info", "warn", "bad", "neutral"]:
    bg, fg = _tok(_dark, f"tone-{tone}-bg"), _tok(_dark, f"tone-{tone}-fg")
    ratio = contrast(fg, bg)
    check(f"dark tone '{tone}' reads at {ratio:.2f}:1 on its own fill", ratio >= 4.5)

# Every token must still be USED: an unused one silently drifts out of step
# with the thing it was derived from.
_CONSUMERS = re.sub(r"/\*.*?\*/", "", APP_CSS_SRC, flags=re.S) + "".join(
    template(_n) for _n in os.listdir(TEMPLATES) if _n.endswith(".html"))
for _name in PASS_B_TOKENS:
    # app.css AND the templates: --logo-sq-* are read by an inline SVG fill=
    # in base.html, never by a CSS rule, so a stylesheet-only scan calls them
    # dead and the guard fires on a token that is very much in use.
    _used = len(re.findall(r"var\(\s*--" + re.escape(_name) + r"\s*[,)]", _CONSUMERS))
    check(f"--{_name} is actually referenced somewhere", _used > 0)


print("\n=== 9e. The avatar badge is legible in dark ===")
# A REAL BUG, not a literal sweep. .profile-avatar-badge filled with
# --color-ink and put #fff on it. In light that is white on #111827 (17.7:1);
# in dark --color-ink becomes #c1c1c1 and the pair collapses to 1.8:1 - a
# white glyph on a light-grey disc. --color-invert is the same #111827 in
# light and flips properly in dark, taking it to 10.46:1.
_badge = re.search(r"\.profile-avatar-badge \{(.*?)\n\}", APP_CSS_SRC, re.S)
check("the badge rule is still there", _badge is not None)
_body = _badge.group(1) if _badge else ""
check("the badge no longer fills with --color-ink",
      "var(--color-ink" not in _body)
check("the badge fills with --color-invert", "var(--color-invert)" in _body)
check("its glyph uses the matching foreground",
      "var(--color-invert-foreground)" in _body)
for _theme, _blk, _fill, _fg in [("light", _root, "#111827", "#ffffff"),
                                 ("dark", _dark, "#c1c1c1", "#121113")]:
    _r = contrast(_tok(_blk, "color-invert-foreground"), _tok(_blk, "color-invert"))
    check(f"badge glyph on badge fill clears 4.5:1 in {_theme} ({_r:.2f})", _r >= 4.5)
check("the OLD pairing would have failed in dark (why this changed)",
      contrast("#ffffff", "#c1c1c1") < 2.0)


print("\n=== 9d. table-tools.js stays retired ===")
# The file is gone, but the dashboard's range select still needs
# pad2/manilaToday/shiftDay, which live in dashboard.html. Both halves are
# guarded.
check("table-tools.js is deleted",
      not os.path.exists(os.path.join(server_dir, "static", "js", "table-tools.js")))
# A <script src=...> specifically: matching the bare name would assert
# nothing about whether it is actually loaded.
check("no template loads table-tools.js",
      not any(re.search(r'<script[^>]+src=[^>]*table-tools\.js', template(n))
              for n in os.listdir(TEMPLATES) if n.endswith(".html")))
check("its .tt-* CSS went with it",
      ".tt-" not in re.sub(r"/\*.*?\*/", "", APP_CSS_SRC, flags=re.S))
check("no template carries a data-tt-* attribute",
      not any(re.search(r"data-tt-[\w-]+",
                        re.sub(r"\{#.*?#\}", "", template(n), flags=re.S))
              for n in os.listdir(TEMPLATES) if n.endswith(".html")))

_dash = template("dashboard.html")
check("the dashboard no longer reaches for the deleted global",
      "AppEaseDateRange" not in _dash)
for _fn in ["pad2", "manilaToday", "shiftDay"]:
    # \b, not a substring: "function shiftDayX" contains "function shiftDay",
    # so a plain `in` check passes on a renamed-away helper.
    check(f"dashboard.html defines {_fn}() itself now",
          re.search(r"function " + _fn + r"\b", _dash) is not None)
check("the range select still computes both ends",
      "var to = manilaToday();" in _dash and "var from = shiftDay(to, -days);" in _dash)
check("the range select still navigates",
      "chart_from=" in _dash and "chart_to=" in _dash)

# THE BUG THIS ALMOST SHIPPED WITH. A whitespace-stripping Jinja comment
# ({#- ... -#}) eats the newline that terminates the `//` comment above it, so
# the first statement after the comment lands INSIDE that comment and silently
# stops existing - pad2 was defined in the template and undefined in the
# browser. Invisible in the template source; only the RENDERED output shows it.
#
# A Python harness cannot run node --check (standing decision: no Node tooling
# in t/), so this asserts the property that actually matters: every function
# the range select needs still begins a line of its own after Jinja has run,
# and nothing anywhere is glued onto the tail of a // comment.
_rendered = dark.get("/").get_data(as_text=True)
for _fn in ["pad2", "manilaToday", "shiftDay"]:
    check(f"{_fn}() survives Jinja rendering as a real statement",
          re.search(r"^\s*function " + _fn + r"\b", _rendered, re.M) is not None)
check("no statement is glued onto the end of a // comment in the rendered page",
      re.search(r"//[^\n]*\bfunction \w+\s*\(", _rendered) is None)


print("\n=== 9f. Pass C-1: base.html, which every page inherits ===")
# base.html is on every page, the shared confirm dialog included, so a
# baked colour here reaches every page.
_base = template("base.html")
_base_markup = re.sub(r"\{#.*?#\}", "", _base, flags=re.S)
_base_markup = re.sub(r"<!--.*?-->", "", _base_markup, flags=re.S)

_PAL = (r"(?:gray|red|amber|blue|green|slate|zinc|neutral|stone|orange|yellow"
        r"|lime|emerald|teal|cyan|sky|indigo|violet|purple|fuchsia|pink|rose)"
        r"-(?:50|100|200|300|400|500|600|700|800|900|950)")
_UTIL = re.compile(r"(?<![\w-])(?:(?:hover|focus|active|disabled):)*"
                   r"(?:bg|text|border|ring|divide|fill|stroke|placeholder)-(?:"
                   + _PAL + r"|white|black)(?![\w-])")
_found = _UTIL.findall(_base_markup)
# text-white is the ONE that stays: the confirm button swaps between a
# saturated red and the primary blue, and a white label is correct on both in
# either theme. Anything else is a baked colour.
_unexpected = [u for u in _found if u != "text-white"]
check("base.html carries no baked palette utility beyond text-white"
      + (f" - found {sorted(set(_unexpected))}" if _unexpected else ""),
      not _unexpected)
check("the one survivor is text-white on the confirm button",
      _found.count("text-white") == 1)

_hex = re.findall(r"#[0-9a-fA-F]{3,8}\b",
                  re.sub(r"var\(\s*--[\w-]+\s*,\s*[^)]*\)", "V", _base_markup))
# Only the pre-paint pair may remain, and only because a var() cannot be used
# in the rule that paints before app.css has loaded.
check(f"base.html's only bare hexes are the pre-paint pair (found {sorted(set(_hex))})",
      sorted(set(_hex)) == sorted({DARK_CANVAS, LIGHT_CANVAS}))

# 6 = <body>, the topbar, the sidebar popover, the notification panel, the
# profile panel and the dialog.
check("the popovers and the dialog paint from the canvas token",
      _base_markup.count("bg-canvas") == 6)
check("no popover or dialog is still bg-white", "bg-white" not in _base_markup)

# The three plain classes, and the cascade trap they could have walked into.
for _cls in ["tone-danger-solid", "badge-count", "menu-item-danger"]:
    check(f".{_cls} is defined in app.css", "." + _cls in APP_CSS_SRC)
    _rule = re.search(r"\.\s*" + _cls + r"[^{]*\{([^}]*)\}", APP_CSS_SRC)
    check(f".{_cls} sets no display (it would beat Tailwind's .hidden)",
          _rule is not None and "display" not in _rule.group(1))

# The confirm button's tone swap has to name the class the markup actually has.
check("the tone swap toggles .tone-danger-solid, not bg-red-600",
      "classList.add('tone-danger-solid')" in _base
      and "bg-red-600" not in _base_markup)
check("the other tone is still the compiled bg-primary",
      "classList.add('bg-primary')" in _base)

# The mark's ramp. Left as fixed hexes it inverts on the dark sidebar - the
# squares run 12.37 / 5.79 / 2.29 instead of 1.38 / 2.94 / 7.44, so the
# emphasis reverses and the third square nearly disappears.
_LIGHT_SIDEBAR, _DARK_SIDEBAR = "#f3f4f6", "#121212"
_light_ramp = [contrast(_tok(_root, f"logo-sq-{i}"), _LIGHT_SIDEBAR) for i in (1, 2, 3)]
_dark_ramp = [contrast(_tok(_dark, f"logo-sq-{i}"), _DARK_SIDEBAR) for i in (1, 2, 3)]
check(f"the mark's light ramp still ascends {[round(r, 2) for r in _light_ramp]}",
      _light_ramp[0] < _light_ramp[1] < _light_ramp[2])
check(f"the mark's dark ramp ascends the same way {[round(r, 2) for r in _dark_ramp]}",
      _dark_ramp[0] < _dark_ramp[1] < _dark_ramp[2])
for _i in (1, 2, 3):
    check(f"logo square {_i} keeps its light contrast within 0.15 in dark",
          abs(_light_ramp[_i - 1] - _dark_ramp[_i - 1]) < 0.15)
check("reusing the light values in dark would have inverted the ramp",
      contrast("#cfd2d8", _DARK_SIDEBAR) > contrast("#4a4f5c", _DARK_SIDEBAR))


print("\n=== 9g. Pass C-2: the JS layer and the shared surface ===")
# The JS layer: deploy-panel.js assembles class strings at runtime, which no
# template or stylesheet scan sees, so it is scanned for palette CLASSES.
_JS_DIR = os.path.join(server_dir, "static", "js")
_PAL_JS = (r"(?:gray|red|amber|blue|green|slate|zinc|neutral|stone|orange"
           r"|yellow|lime|emerald|teal|cyan|sky|indigo|violet|purple|fuchsia"
           r"|pink|rose)-(?:50|100|200|300|400|500|600|700|800|900|950)")
_UTIL_JS = re.compile(r"(?<![\w-])(?:(?:hover|focus|active|disabled):)*"
                      r"(?:bg|text|border|ring|divide|fill|stroke|placeholder)-(?:"
                      + _PAL_JS + r"|white|black)(?![\w-])")
for _name in sorted(os.listdir(_JS_DIR)):
    if not _name.endswith(".js"):
        continue
    _src = open(os.path.join(_JS_DIR, _name), "rb").read().decode("utf-8", "replace")
    _code = re.sub(r"/\*.*?\*/", "", _src, flags=re.S)
    _code = re.sub(r"^\s*//.*$", "", _code, flags=re.M)
    _found = _UTIL_JS.findall(_code)
    check(f"{_name} builds no baked palette class"
          + (f" - found {sorted(set(_found))[:4]}" if _found else ""),
          not _found)

# deploy-panel.js is LF like every .js here, and unlike the templates it is
# real JavaScript on disk - a syntax error in it is a syntax error, not a
# Jinja artefact. The suite cannot run node --check (no Node tooling in t/),
# so it checks the two things it can: the line endings, and that the class
# names the JS hands out actually exist in the stylesheet.
_dp = open(os.path.join(_JS_DIR, "deploy-panel.js"), "rb").read()
check("deploy-panel.js is LF, not CRLF", _dp.count(b"\r\n") == 0)
check("deploy-panel.js has no stray CR", not re.findall(rb"\r(?!\n)", _dp))
_dp_txt = _dp.decode("utf-8")
for _cls in ["pill-tone-good", "pill-tone-bad", "pill-tone-info",
             "pill-tone-warn", "pill-tone-neutral", "border-warn-strong"]:
    check(f"deploy-panel.js hands out .{_cls} and app.css defines it",
          _cls in _dp_txt and ("." + _cls) in APP_CSS_SRC)
# On the CODE, not the file: a comment there may name the classes the
# tokens replaced.
_dp_code = re.sub(r"/\*.*?\*/", "", _dp_txt, flags=re.S)
_dp_code = re.sub(r"^\s*//.*$", "", _dp_code, flags=re.M)
check("the status pills no longer name Tailwind palette classes",
      "bg-green-100" not in _dp_code and "bg-gray-100" not in _dp_code)
check("the warning border no longer names border-amber-500",
      "border-amber-500" not in _dp_code)
check("no bg-white survives in the code it builds",
      "bg-white" not in _dp_code)
# The other tone it swaps to is a compiled utility, so it must stay one.
check("the primary border is still the compiled bg/border-primary utility",
      "'border-primary'" in _dp_txt)

# The five pills read the SAME tokens as .dt-pill and .tl-chip - the whole
# point of unifying them - so a job status and an audit severity cannot
# disagree about what red means.
for _tone in ["good", "bad", "info", "warn", "neutral"]:
    _rule = re.search(r"\.pill-tone-" + _tone + r"\s*\{([^}]*)\}", APP_CSS_SRC)
    check(f".pill-tone-{_tone} is defined", _rule is not None)
    if _rule:
        check(f".pill-tone-{_tone} reads the shared tone pair",
              f"var(--tone-{_tone}-bg)" in _rule.group(1)
              and f"var(--tone-{_tone}-fg)" in _rule.group(1))
        check(f".pill-tone-{_tone} sets no display", "display" not in _rule.group(1))

# The shared bordered-surface transform: the "Back to Dashboard" / "Print"
# button pattern and the card panels, across every admin template.
_ADMIN = [n for n in os.listdir(TEMPLATES) if n.endswith(".html")
          and n not in ("report.html", "report_gate.html", "report_print.html",
                        "qr_labels.html", "auth_base.html", "login.html",
                        "forgot_password.html", "reset_password.html",
                        "set_password.html")]
for _n in sorted(_ADMIN):
    _m = re.sub(r"\{#.*?#\}", "", template(_n), flags=re.S)
    _m = re.sub(r"<!--.*?-->", "", _m, flags=re.S)
    check(f"{_n} has no bordered surface still painting bg-white",
          "border-field bg-white" not in _m
          and not re.search(r"border border-line bg-white p-6 shadow-", _m))

# The out-of-scope pages must NOT have been swept along with them.
check("report.html was left alone by the batch",
      "bg-white" in template("report.html"))


print("\n=== 9h. Pass C-3: one tone set, six consumers ===")
# --tone-* is the single source of severity colour. Every component that shows
# a severity or a type reads it; a seventh should join rather than start a
# second palette.
TONE_CONSUMERS = {
    ".dt-pill": "data-table status pills",
    ".tl-chip": "profile timeline",
    ".activity-avatar": "dashboard activity",
    ".notif-sev": "notification severity chips",
    ".rs-chip": "report status / priority (Tasks card, Problem Reports)",
    ".pill-tone-": "deploy-panel.js job pills and Deploy's type badges",
    ".job-result": "job result reasons (dashboard Result column, deploy rows)",
}
for _sel, _what in TONE_CONSUMERS.items():
    _uses = re.findall(re.escape(_sel) + r"[^{]*\{[^}]*\}", APP_CSS_SRC)
    _reads = any("var(--tone-" in _r for _r in _uses)
    check(f"{_sel} reads the shared tone set ({_what})", _reads)

# The soft step exists for all five tones; .rs-chip and Deploy's badges
# read it.
for _tone in ["good", "info", "warn", "bad"]:
    check(f"--tone-{_tone}-soft exists in both themes",
          _tok(_root, f"tone-{_tone}-soft") and _tok(_dark, f"tone-{_tone}-soft"))
    _r = contrast(_tok(_dark, f"tone-{_tone}-soft"), _tok(_dark, f"tone-{_tone}-fg"))
    check(f"dark tone '{_tone}' soft reads at {_r:.2f}:1 with its own fg", _r >= 4.5)

# .rs-chip carries no parallel set of foregrounds.
_rs = re.findall(r"\.rs-(?:btn|chip)\[data-(?:status|priority)=[^{]*\{([^}]*)\}", APP_CSS_SRC)
check(f"all {len(_rs)} .rs-* tone rules resolved to tokens", len(_rs) >= 7)
check("no .rs-* tone rule still names a literal",
      not any(re.search(r"#[0-9a-fA-F]{3,8}\b", _r) for _r in _rs))

# Deploy's type badges are template markup, not JS-built, despite the
# JS-driven picker around them.
_deploy = re.sub(r"\{#.*?#\}", "", template("deploy.html"), flags=re.S)
_deploy = re.sub(r"<!--.*?-->", "", _deploy, flags=re.S)
check("deploy.html carries no baked palette utility at all",
      not _UTIL.findall(_deploy))
for _cls in ["pill-tone-info-soft", "pill-tone-good-soft"]:
    check(f"the type badges use .{_cls}",
          _cls in _deploy and ("." + _cls) in APP_CSS_SRC)

# Software Library. Written from what the page must DO, not from what its
# source currently says: every surface a person sees while adding software
# must follow the
# theme. For a class list the text IS the fix, but it cannot prove a script
# branch runs or that nothing outranks a rule - the rendered colours need the
# census in tools/visual-census/, run by hand.
_sl = template("software_library.html")
_sl_markup = re.sub(r"<!--.*?-->", "", re.sub(r"\{#.*?#\}", "", _sl, flags=re.S), flags=re.S)


def _class_of(element_id):
    _m = re.search(r'<(?:div|span) id="' + element_id + r'" class="([^"]*)"', _sl_markup)
    return _m.group(1).split() if _m else None


# Opaque AND themed. Not merely "no bg-white": a card with no fill at all
# would pass that and float see-through over the modal backdrop.
_SURFACES = {
    "offline-fields": "the Local file container",
    "online-fields": "the Chocolatey container",
    "dropzone-detecting": "the reading-installer box",
    "dropzone-filled": "the detected-file panel",
    "choco-picked": "the selected-package panel",
    "duplicate-dialog-card": "the duplicate dialog",
    "detect-dialog-card": "the detection dialog",
}
for _id, _what in _SURFACES.items():
    _cls = _class_of(_id)
    check(f"#{_id} ({_what}) paints from the canvas token",
          _cls is not None and "bg-canvas" in _cls)
_loading = re.search(r'id="loading-overlay"[^>]*>\s*<div class="([^"]*)"', _sl_markup)
check("the loading overlay's card paints from the canvas token",
      _loading is not None and "bg-canvas" in _loading.group(1).split())

# The detection dialog's class list is REBUILT by the script on every open,
# so a template-only fix would undo itself the first time it appeared.
_card_lists = re.findall(r"card\.className = '([^']*)'", _sl_markup)
check(f"the script rebuilds the detection card ({len(_card_lists)} class lists found)",
      len(_card_lists) >= 2)
check("every class list the script rebuilds it with paints from the canvas token",
      bool(_card_lists) and all("bg-canvas" in _s.split() for _s in _card_lists))

# Switching the duplicate dialog between its tiers must not leave the other
# tier's border behind. .border-warn-strong is unlayered app.css, so a stale
# one silently beats .border-blue-500 - the card would stay amber.
_dup_start = _sl_markup.find("function openDuplicateDialog(")
_dup = _sl_markup[_dup_start:_sl_markup.find("\n    }\n", _dup_start)] if _dup_start != -1 else ""
_added = set(re.findall(r"card\.classList\.add\('([^']+)'\)", _dup))
_remove_call = re.search(r"card\.classList\.remove\(([^)]*)\)", _dup)
_removed = set(re.findall(r"'([^']+)'", _remove_call.group(1))) if _remove_call else set()
_initial = [c for c in (_class_of("duplicate-dialog-card") or []) if c.startswith("border-") and c != "border"]
check(f"the duplicate dialog's tiers add borders ({sorted(_added)})", len(_added) == 2)
check("switching tier removes every border either tier can add",
      bool(_added) and _added <= _removed)
check("... including the border the card is first rendered with",
      bool(_initial) and set(_initial) <= _removed)

# The one remainder, as an exact list. Any baked palette colour added to this
# page fails here, and so does removing one without updating the list.
#   text-white x6       - labels on the primary and amber buttons; correct in
#                         both themes, the same reason base.html keeps its one.
#   bg-amber-600 x2     - "Add anyway". 3.2:1 with its white label in BOTH
#                         themes; a known gap, not fixed here.
#   border-blue-500 x2  - the name_only tier's accent border. A saturated
#                         accent on a border, not a surface.
from collections import Counter  # noqa: E402
_REMAINDER = Counter({"text-white": 6, "bg-amber-600": 2, "border-blue-500": 2})
_sl_found = Counter(_UTIL.findall(_sl_markup))
check(f"software_library.html carries exactly the {sum(_REMAINDER.values())} intended baked utilities"
      + ("" if _sl_found == _REMAINDER else f" - found {dict(sorted(_sl_found.items()))}"),
      _sl_found == _REMAINDER)

# The Category listbox paints tokens only.
_ael_at = APP_CSS_SRC.find("/* --- Software Library: category listbox")
_ael_end = APP_CSS_SRC.find("/* --- Problem Reports: status menu")
_ael = re.sub(r"/\*.*?\*/", "", APP_CSS_SRC[_ael_at:_ael_end], flags=re.S) if _ael_at != -1 else ""
check("the listbox rules were found", ".ael-btn" in _ael and ".ael-menu" in _ael and ".ael-opt" in _ael)
check("no listbox rule names a literal colour",
      bool(_ael) and not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", _ael))
_caret = re.search(r"\.ael-caret\s*\{([^}]*)\}", APP_CSS_SRC)
check(".ael-caret reads a token", _caret is not None and "var(--color-" in _caret.group(1)
      and not re.search(r"#[0-9a-fA-F]{3,8}\b", _caret.group(1)))
for _label, _sel, _props in [
        (".ael-btn", r"\.ael-btn", ["background", "border", "color"]),
        (".ael-btn:hover", r"\.ael-btn:hover", ["background"]),
        (".ael-btn:focus", r"\.ael-btn:focus,\s*\.ael-btn:focus-visible", ["border-color", "box-shadow"]),
        (".ael-btn.ael-invalid", r"\.ael-btn\.ael-invalid", ["border-color"]),
        (".ael-placeholder", r"\.ael-placeholder", ["color"]),
        (".ael-menu", r"\.ael-menu", ["background", "border", "box-shadow"]),
        (".ael-opt", r"\.ael-opt", ["color"]),
        (".ael-opt:hover", r"\.ael-opt:hover,\s*\.ael-opt\.ael-active", ["background"])]:
    _rule = re.search(r"(?:^|\n)" + _sel + r"\s*\{([^}]*)\}", _ael)
    for _p in _props:
        _decl = re.search(r"(?:^|[;\s])" + re.escape(_p) + r"\s*:\s*([^;]+);", _rule.group(1)) if _rule else None
        check(f"{_label} {_p} comes from a token",
              _decl is not None and "var(--" in _decl.group(1))

# What the listbox's text lands on, in dark. (The light placeholder #9ca3af
# on white is 2.54:1 and is not asserted.)
_canvas_dark = _tok(_dark, "color-canvas")
for _name in ["color-ink", "color-faint", "color-muted", "tone-bad-fg"]:
    _r = contrast(_tok(_dark, _name), _canvas_dark)
    check(f"dark --{_name} reads at {_r:.2f}:1 on the canvas the panels now paint", _r >= 4.5)

# The dialog message text must read on the dark card.
_ink_soft = re.search(r"\.text-ink-soft\s*\{([^}]*)\}", APP_CSS_SRC)
check(".text-ink-soft is defined", _ink_soft is not None)
check(".text-ink-soft reads --color-ink-soft and sets no display",
      _ink_soft is not None and "var(--color-ink-soft)" in _ink_soft.group(1)
      and "display" not in _ink_soft.group(1))
for _block, _theme in [(_root, "light"), (_dark, "dark")]:
    _r = contrast(_tok(_block, "color-ink-soft"), _tok(_block, "color-canvas") or "#ffffff")
    check(f"{_theme} dialog message text reads at {_r:.2f}:1 on the canvas", _r >= 4.5)
for _id in ["duplicate-dialog-message", "detect-dialog-message"]:
    _m = re.search(r'<p id="' + _id + r'" class="([^"]*)"', _sl_markup)
    check(f"#{_id} uses it", _m is not None and "text-ink-soft" in _m.group(1).split())

_track = re.search(r"\.dropzone-track\s*\{([^}]*)\}", APP_CSS_SRC)
check(".dropzone-track reads --color-line and sets no display",
      _track is not None and "var(--color-line)" in _track.group(1) and "display" not in _track.group(1))
check("the reading bar's track uses it", 'class="dropzone-track ' in _sl_markup)

# The duplicate banner, and the info tone's border step.
check("--tone-info-border exists in both themes",
      bool(_tok(_root, "tone-info-border")) and bool(_tok(_dark, "tone-info-border")))
check("light --tone-info-border is blue-200's hex, like the rest of the tone set",
      (_tok(_root, "tone-info-border") or "").lower() == "#bfdbfe")
for _tone in ["warn", "info"]:
    _rule = re.search(r"\.banner-tone-" + _tone + r"\s*\{([^}]*)\}", APP_CSS_SRC)
    check(f".banner-tone-{_tone} is defined", _rule is not None)
    if _rule:
        check(f".banner-tone-{_tone} reads its tone's soft, border and fg",
              all(f"var(--tone-{_tone}-{_step})" in _rule.group(1) for _step in ("soft", "border", "fg")))
        check(f".banner-tone-{_tone} sets no display", "display" not in _rule.group(1))
    for _block, _theme in [(_root, "light"), (_dark, "dark")]:
        _r = contrast(_tok(_block, f"tone-{_tone}-fg"), _tok(_block, f"tone-{_tone}-soft"))
        check(f"{_theme} {_tone} banner text reads at {_r:.2f}:1", _r >= 4.5)

# Through the real route: a duplicate submission bounces back with the banner.
_c = sqlite3.connect(database.DB_PATH)
_c.execute("INSERT INTO software (name, version, type, install_command, category) "
           "VALUES ('Banner Probe', '1.0.0', 'online', 'choco install bannerprobe -y', 'Utility')")
_c.commit()
_c.close()
for _version, _tone in [("1.0.0", "warn"), ("2.0.0", "info")]:
    _res = dark.post("/software", data={"type": "online", "package_name": "bannerprobe",
                                        "name": "Banner Probe", "version": _version,
                                        "category": "Utility", "confirm_duplicate": ""})
    _page = _res.get_data(as_text=True)
    _banner = re.search(r'<div class="rounded-lg border ([^"]*) px-4 py-3 text-sm">', _page)
    check(f"a duplicate bounce renders the {_tone} banner with its tone class",
          _res.status_code == 200 and _banner is not None and _banner.group(1).strip() == f"banner-tone-{_tone}")


print("\n=== 9i. Pass C-4: foreground contrast ===")
# Text, not only backgrounds: text-red-700 resolves to (193,0,7) and reads
# 2.93:1 on #121113, and it was mostly inline validation messages - text that
# only appears when something is already wrong.
for _bad in ["text-red-700", "border-red-200", "hover:bg-red-50"]:
    _users = [n for n in os.listdir(TEMPLATES) if n.endswith(".html")
              and _bad in re.sub(r"\{#.*?#\}", "", template(n), flags=re.S)]
    check(f"no template still uses {_bad}" + (f" - {_users}" if _users else ""),
          not _users)

for _cls, _token in [("text-tone-bad", "tone-bad-fg"),
                     ("border-tone-bad", "tone-bad-border"),
                     ("hover-tone-bad-soft", "tone-bad-soft")]:
    _rule = re.search(r"\." + _cls + r"(?::hover)?\s*\{([^}]*)\}", APP_CSS_SRC)
    check(f".{_cls} is defined", _rule is not None)
    check(f".{_cls} reads --{_token}",
          _rule is not None and f"var(--{_token})" in _rule.group(1))

# The replacement has to clear 4.5:1 in BOTH themes - a validation message is
# small text, so it gets no large-text exemption.
for _theme, _blk, _ground in [("light", _root, "#ffffff"), ("dark", _dark, "#121113")]:
    _r = contrast(_tok(_blk, "tone-bad-fg"), _ground)
    check(f"--tone-bad-fg reads at {_r:.2f}:1 on the {_theme} canvas", _r >= 4.5)
check("the red it replaced would have failed in dark (why this changed)",
      contrast("#c10007", "#121113") < 3.0)


print("\n=== 9j. Dim text still has to be readable ===")
# --color-faint and --color-sidebar-label colour the sidebar group labels
# and the Tasks card's report ids, which are not disabled controls, so the
# WCAG exemption for a disabled state does not cover them.
_GROUNDS = {"canvas": "#121113", "sidebar": "#121212", "subtle": "#1a1a1a"}
for _name in ["color-faint", "color-sidebar-label"]:
    _v = _tok(_dark, _name)
    for _where, _bg in _GROUNDS.items():
        _r = contrast(_v, _bg)
        check(f"--{_name} reads at {_r:.2f}:1 on the dark {_where}", _r >= 4.5)
    # Still DIMMER than muted - that is the whole point of the role.
    check(f"--{_name} stays dimmer than --color-muted",
          contrast(_v, "#121113") < contrast(_tok(_dark, "color-muted"), "#121113"))
check("the two dim tokens agree, as they already do in light",
      _tok(_dark, "color-faint") == _tok(_dark, "color-sidebar-label"))
check("#666666 would have failed (why this changed)",
      contrast("#666666", "#121113") < 3.5)


print("\n=== 9k. The primary as text, not as a fill ===")
# #4173d8 works as a BUTTON FILL in dark (white on it is 4.50) but fails as
# TEXT on a dark surface: 4.19 on the canvas, 3.87 on the subtle fill. The
# selected Deploy tab and the active notification filter are both text.
#
# Its own token, not a second job for --color-ring: a focus ring and a text
# colour sharing one value drift the moment either needs to move alone.
check("--color-primary-text is a token of its own, not an alias of --color-ring",
      _tok(_dark, "color-primary-text") != _tok(_dark, "color-ring"))
check("in light it is exactly the primary, so nothing moves there",
      _tok(_root, "color-primary-text") == "#4173d8")
for _where, _bg in [("canvas", "#121113"), ("card", "#121212"), ("subtle", "#1a1a1a")]:
    _r = contrast(_tok(_dark, "color-primary-text"), _bg)
    check(f"--color-primary-text reads at {_r:.2f}:1 on the dark {_where}", _r >= 4.5)
check("the plain primary would have failed as text (why this exists)",
      contrast("#4173d8", "#1a1a1a") < 4.0)
# Nothing colours TEXT with the bare primary except the out-of-scope auth
# pages. (?<![\w-]) so "border-color: var(--color-primary...)" is not
# counted: it contains "color:" as a substring.
_text_primary = re.findall(r"(?<![\w-])color: var\(--color-primary,[^)]*\)", APP_CSS_SRC)
check(f"only the out-of-scope auth pages still colour text with the bare primary"
      f" (found {len(_text_primary)})", len(_text_primary) == 2)
check("the notification filter and the deploy tab read the new token",
      APP_CSS_SRC.count("var(--color-primary-text") >= 4)


print("\n=== 9l. The two families no census had rendered ===")
# .task-bulk-bar is the real one here: a white slab pinned to the bottom of
# the viewport that only exists once a task is selected. Every census run
# loaded pages without touching anything, so it was never on screen - the
# same way .pw-rule only appears once you start typing a password.
_bulk = re.search(r"(?m)^\.task-bulk-bar\s*\{([^}]*)\}", APP_CSS_SRC)
check("the bulk bar rule is still there", _bulk is not None)
check("the bulk bar paints from tokens, not a white literal",
      _bulk is not None and "#ffffff" not in _bulk.group(1)
      and "var(--color-canvas)" in _bulk.group(1))

# --shadow-drag is its own token rather than --shadow-raised: the first layer
# is 0.06 alpha, not 0.05, and rounding one onto the other is how two shadows
# become one that suits neither.
check("--shadow-drag keeps its own light value",
      "0.06" in (_tok(_root, "shadow-drag") or ""))
check("--shadow-drag is not silently aliased to --shadow-raised",
      _tok(_root, "shadow-drag") != _tok(_root, "shadow-raised"))
# Checked as an ABSENCE, not a presence: the shadow has two layers, so
# "rgba(0, 0, 0" is still true when only one of them has been changed back
# to the page colour.
check("--shadow-drag goes black in dark, like the other dark shadows",
      "16, 24, 40" not in (_tok(_dark, "shadow-drag") or ""))

# The password hints are read while correcting a password, so they are not
# exempt.
for _name, _ground, _where in [("color-muted", "#121212", "card"),
                               ("color-muted", "#121113", "canvas")]:
    _r = contrast(_tok(_dark, _name), _ground)
    check(f".pw-rule now reads at {_r:.2f}:1 on the dark {_where}", _r >= 4.5)
check(".pw-rule-ok uses the shared good tone",
      "var(--tone-good-fg)" in re.search(r"(?m)^\.pw-rule-ok\s*\{([^}]*)\}",
                                         APP_CSS_SRC).group(1))
# The toggle's hover has to stay DARKER than its resting colour, or hovering
# reads as the control going away.
check(".pw-toggle hover stays darker than its resting colour",
      contrast(_tok(_root, "color-ink-soft"), "#ffffff")
      > contrast("#6b7280", "#ffffff"))


print("\n=== 9m. The avatar initials were invisible in BOTH themes ===")
# Basecoat's vega pack styles `.avatar > span` with `bg-muted
# text-muted-foreground`. AppEase holds the SAME value in both of those keys
# (#6b7280 light, #888888 dark), so the initials sat on a disc of their own
# colour: 1.0:1. It survived because they are a FALLBACK - an onerror removes
# a broken <img> to reveal them - so they only show once the image failed.
#
# The --color-muted collision, fixed locally at the component, never by
# repointing the token that `text-muted` means in ~60 places.
_av = re.search(r"(?m)^\.avatar:not\(\.activity-avatar\) > span:not\(\.avatar-badge\)\s*\{([^}]*)\}",
                APP_CSS_SRC)
check("the generic avatar initials are patched locally", _av is not None)
_avbody = _av.group(1) if _av else ""
check("they no longer take their fill from --color-muted",
      "var(--color-muted)" not in _avbody)
check("they use the same pairing as the .activity-avatar precedent",
      "var(--color-subtle)" in _avbody and "var(--color-ink-soft)" in _avbody)
for _theme, _blk in [("light", _root), ("dark", _dark)]:
    _fill = _tok(_blk, "color-subtle") or "#f3f4f6"
    _r = contrast(_tok(_blk, "color-ink-soft"), _fill)
    check(f"the initials read at {_r:.2f}:1 in {_theme}", _r >= 4.5)
check("the old pairing was literally invisible (why this changed)",
      contrast(_tok(_dark, "color-muted"), _tok(_dark, "color-muted")) == 1.0)

# :not(.activity-avatar), NOT source order. Both selectors are (0,2,1), so
# whichever came last would win - and the generic rule sits later in the file,
# which would silently flatten the activity card's tones.
check("the generic rule excludes .activity-avatar explicitly",
      ":not(.activity-avatar)" in (_av.group(0) if _av else ""))
for _tone, _tok_name in [("danger", "bad"), ("warn", "warn"), ("info", "info")]:
    _r = re.search(r"\.activity-avatar\[data-tone=\"" + _tone + r"\"\] > span[^{]*\{([^}]*)\}",
                   APP_CSS_SRC)
    check(f"the activity '{_tone}' tone survived the generic rule", _r is not None)
    check(f"the activity '{_tone}' tone reads the shared tone set",
          _r is not None and f"var(--tone-{_tok_name}-bg)" in _r.group(1))



print("\n=== 9n. Every confirm dialog's icon sat on a grey slab ===")
# Basecoat fills `.alert-dialog > * > header > figure` with --color-muted.
# Here that is a text grey, so every confirm dialog's icon - Delete, Retry,
# Cancel, Reset password - sat on a dark grey tile in both themes: the
# --color-muted collision, fixed at the component like the avatar.
_fig = re.search(r"(?m)^\.alert-dialog > \* > header > figure\s*\{([^}]*)\}", APP_CSS_SRC)
check("the alert-dialog icon tile is patched at the component", _fig is not None)
_figbody = _fig.group(1) if _fig else ""
check("...no longer filled with --color-muted", "var(--color-muted)" not in _figbody)
check("...with the avatar's pairing", "var(--color-subtle)" in _figbody and "var(--color-ink-soft)" in _figbody)
for _theme, _blk in [("light", _root), ("dark", _dark)]:
    _r = contrast(_tok(_blk, "color-ink-soft"), _tok(_blk, "color-subtle") or "#f3f4f6")
    check(f"an icon reads at {_r:.2f}:1 on its tile in {_theme}", _r >= 4.5)
# Every dialog is the shape the rule matches - <dialog class="alert-dialog">,
# one wrapper, then header > figure - or the rule misses it silently.
_dialogs = []
for _n in sorted(os.listdir(TEMPLATES)):
    if _n.endswith(".html"):
        _src = without_jinja_comments(template(_n))
        for _m in re.finditer(r'<dialog[^>]*class="alert-dialog"[^>]*>(.*?)</dialog>', _src, re.S):
            _dialogs.append((_n, _m.group(1)))
check(f"there are alert-dialogs to check ({len(_dialogs)})", len(_dialogs) >= 9)
for _n, _body in _dialogs:
    check(f"{_n}: its icon is a header > figure inside one wrapper",
          re.match(r"\s*<div>\s*<header>\s*<figure[\s>]", _body) is not None)


def _specificity(selector):
    ids = selector.count("#")
    classes = len(re.findall(r"\.[\w-]+|\[[^\]]+\]|:(?!:)[\w-]+", selector))
    elements = len(re.findall(r"(?:^|[\s>+~])([a-z][\w-]*)", selector))
    return (ids, classes, elements)


_danger = re.search(r"(?m)^([^{}\n]*\.deploy-danger-figure)\s*\{([^}]*)\}", APP_CSS_SRC)
check("the uninstall confirm still tints its tile red",
      _danger is not None and "var(--tone-bad-bg)" in _danger.group(2))
check("...and out-ranks the neutral tile (both are unlayered)",
      _danger is not None and _fig is not None
      and _specificity(_danger.group(1)) > _specificity(".alert-dialog > * > header > figure"))


print("\n=== 10. The cascade trap ===")
# app.css is unlayered and beats Tailwind's .hidden at equal specificity, so a
# display rule on a shared class name would silently defeat it.
icon_rules = re.findall(r"([^\n{}]*\.theme-icon-(?:sun|moon)[^\n{}]*)\{([^}]*)\}", APP_CSS_SRC)
check("there are exactly four icon-swap rules", len(icon_rules) == 4)
check("every icon-swap rule is scoped under .theme-toggle",
      all(".theme-toggle" in sel for sel, _ in icon_rules))
check("no icon-swap rule targets a bare class at document scope",
      all(sel.strip().count(" ") >= 1 for sel, _ in icon_rules))
# The class names must be unique to this feature, or the display rules leak.
for cls in ["theme-icon-sun", "theme-icon-moon", "theme-toggle"]:
    # (?<!data-) so the controller's [data-theme-toggle] hook in base.html is
    # not mistaken for the class of the same name.
    pattern = re.compile(r"(?<!data-)\b" + re.escape(cls) + r"\b")
    users = [n for n in os.listdir(TEMPLATES)
             if n.endswith(".html") and pattern.search(without_jinja_comments(template(n)))]
    check(f".{cls} is used only by the toggle macro", users == ["_theme_toggle.html"])
# Nothing that carries Tailwind's .hidden may also carry these.
check("no element carries both an icon-swap class and .hidden",
      not re.search(r'class="[^"]*theme-icon-[^"]*\bhidden\b', template("_theme_toggle.html")))


print("\n=== 11. Out-of-scope pages stay light on purpose ===")
# Deleting one of these should be a deliberate act, not a surprise in a
# screenshot.
for name in ["report.html", "report_gate.html", "report_print.html", "qr_labels.html",
             "auth_base.html"]:
    src = template(name)
    check(f"{name} still hardcodes color-scheme: light",
          '<meta name="color-scheme" content="light">' in src)
    check(f"{name} carries no theme toggle", "data-theme-toggle" not in src)
    check(f"{name} does not stamp the dark class", 'class="dark"' not in src)

anon = appmod.app.test_client()
anon.set_cookie("theme", "dark")
login_html = anon.get("/login").get_data(as_text=True)
check("/login ignores the theme cookie entirely",
      SCHEME.search(login_html).group(1) == "light"
      and 'class="dark"' not in login_html)
check("/login keeps its own #1e2128 pre-paint",
      PREPAINT.search(login_html).group(1) == "#1e2128")


print("\n=== 12. Structure did not regress ===")
VOID = {"meta", "link", "img", "br", "hr", "input", "source", "path", "circle",
        "rect", "line", "polyline", "polygon", "use", "stop", "col"}


def tag_delta(html):
    """(open, bad) counts. These templates never balance absolutely - tags
    are built inside JS strings - so only the DELTA against the original
    matters."""
    html = re.sub(r"\{#.*?#\}", "", html, flags=re.S)
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    html = re.sub(r"(?s)<script\b[^>]*>.*?</script>", "", html)
    html = re.sub(r"(?s)<style\b[^>]*>.*?</style>", "", html)
    stack, bad = [], []
    for m in re.finditer(r"<\s*(/?)\s*([a-zA-Z][\w-]*)([^>]*?)(/?)\s*>", html):
        closing, tag, selfclose = m.group(1), m.group(2).lower(), m.group(4)
        if tag in VOID or selfclose == "/" or tag == "!doctype":
            continue
        if closing:
            if not stack or stack[-1] != tag:
                bad.append(tag)
            else:
                stack.pop()
        else:
            stack.append(tag)
    return len(stack), len(bad)


for name in ["base.html", "settings.html"]:
    try:
        original = subprocess.run(
            ["git", "show", f"HEAD:server/templates/{name}"],
            cwd=ROOT, capture_output=True, check=True,
        ).stdout.decode("utf-8")
    except Exception:
        print(f"  (skipped tag-balance delta for {name}: git unavailable)")
        continue
    check(f"{name}: tag balance is unchanged from HEAD",
          tag_delta(template(name)) == tag_delta(original))

import jinja2  # noqa: E402
for name in sorted(os.listdir(TEMPLATES)):
    if name.endswith(".html"):
        try:
            jinja2.Environment().parse(template(name))
            check(f"{name} parses as Jinja", True)
        except Exception as exc:
            check(f"{name} parses as Jinja ({exc})", False)


# ------------------------------------------------------------------ report --
os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)

print()
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
if failed:
    print()
    for label in failed:
        print(f"  - {label}")
sys.exit(1 if failed else 0)

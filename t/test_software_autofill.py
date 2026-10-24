"""The Add Software form must not carry values between sources: detection
may overwrite what the page filled, never what the admin typed. SOURCE
assertions only - the JavaScript never runs here - so it needs a manual check.
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


work = tempfile.mkdtemp(prefix="appease_autofill_")
server_dir = os.path.join(work, "server")
shutil.copytree(
    os.path.join(ROOT, "server"), server_dir,
    ignore=shutil.ignore_patterns("appease.db*", "__pycache__", "*.pyc", ".env",
                                  "uploads", "staging", "report_photos", "avatars"),
)
sys.path.insert(0, server_dir)
os.chdir(server_dir)
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))

import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()
import app as appmod  # noqa: E402
appmod.app.config["TESTING"] = True

src = open(os.path.join(server_dir, "templates", "software_library.html"),
           encoding="utf-8", newline="").read()


print("\n=== 1. The empty-only guard is gone ===")
# A field holding a Chocolatey pick is not "typed by the admin", so
# detection must not skip it.
check("detection no longer requires an empty name field",
      "if (result.name && !nameInput.value)" not in src)
check("detection no longer requires an empty version field",
      "if (result.version && !versionInput.value)" not in src)
check("detection no longer requires an empty silent flag",
      "if (result.silent_install && !silentInput.value)" not in src)


print("\n=== 2. Replaced by an ownership rule ===")
check("a shared mayAutofill helper exists", "function mayAutofill(" in src)
check("name uses it", "mayAutofill(nameInput, [detectFilledName, chocoFilledName])" in src)
check("version uses it",
      "mayAutofill(versionInput, [detectFilledVersion, chocoFilledVersion])" in src)
check("silent flag uses it", "mayAutofill(silentInput, [detectFilledSilent])" in src)
# Name and version can be overwritten by EITHER source's leftovers; the
# silent flag has no Chocolatey equivalent, so it only lists its own.
check("name may overwrite a Chocolatey leftover", "chocoFilledName]" in src)
check("the silent flag has no Chocolatey twin to consider",
      "detectFilledSilent]" in src and "chocoFilledSilent" not in src)

check("detection records what it wrote", "detectFilledName = result.name;" in src)
check("so a second file may replace the first",
      "detectFilledVersion = result.version;" in src)
check("and so may a replaced silent flag",
      "detectFilledSilent = result.silent_install;" in src)

# The rule must still protect hand-typed values, or the fix trades one bug
# for a worse one.
check("an empty field is always fillable", "if (!current) { return true; }" in src)
check("a value matching a previous autofill is fillable",
      "current === previousAutofills[i]" in src)
check("anything else is refused", "return false;" in src)


print("\n=== 3. Leaving a source drops its values ===")
check("a detect clearer exists", "function clearDetectAutofills(" in src)
check("a choco clearer exists", "function clearChocoAutofills(" in src)
check("selectType notices the previous source",
      "var previous = document.getElementById('type').value;" in src)
check("switching away from local clears detected values",
      "clearDetectAutofills();" in src.split("function selectType")[1][:900])
check("switching away from choco clears package values",
      "clearChocoAutofills();" in src.split("function selectType")[1][:900])
check("nothing is cleared when the source did not change",
      "previous !== value" in src)

check("replacing the installer clears detected values",
      "Drop only what DETECTION wrote" in src
      and "clearDetectAutofills();" in src.split("Drop only what DETECTION wrote")[1][:400])

# The choco reset button and the tab switch must share ONE implementation.
check("the choco clearing rule is defined once",
      src.count("if (chocoFilledName && nameField.value === chocoFilledName)") == 1)
check("the clearers only blank their own values",
      "field.value === pair[1]" in src)


print("\n=== 4. The page still renders ===")
conn = sqlite3.connect(database.DB_PATH)
admin = conn.execute("SELECT id, username FROM admins LIMIT 1").fetchone()
conn.close()
client = appmod.app.test_client()
with client.session_transaction() as sess:
    sess["admin_id"] = admin[0]
    sess["admin_role"] = "admin"
    sess["admin_username"] = admin[1]

res = client.get("/software")
html = res.get_data(as_text=True)
check("software library renders", res.status_code == 200)
check("the name field is present", 'id="name"' in html)
check("the version field is present", 'id="version"' in html)
check("the silent flag field is present", 'id="silent_flag"' in html)
check("both source tabs are present",
      'id="type-pill-offline"' in html and 'id="type-pill-online"' in html)

print("\n=== 5. Card redesign ===")
check("the card uses the shared card class", '<section class="card">' in html)
check("title uses card-title", 'class="card-title">Add New Software' in html)
check("a description was added", 'class="card-description"' in html)
check("the old bespoke wrapper is gone", 'rounded-xl border border-line bg-white p-6 shadow-card' not in html)

check("the source pills are a real tablist", 'role="tablist"' in html)
check("each pill is a tab", html.count('role="tab"') == 2)
check("selection is announced", 'aria-selected="true"' in html)
check("each tab names its panel",
      'aria-controls="offline-fields"' in html and 'aria-controls="online-fields"' in html)
# The ids and handlers must survive, or every piece of JS driving these breaks.
check("the offline handler is unchanged", "onclick=\"selectType('offline')\"" in html)
check("the online handler is unchanged", "onclick=\"selectType('online')\"" in html)
check("the offline id is unchanged", 'id="type-pill-offline"' in html)
check("the online id is unchanged", 'id="type-pill-online"' in html)

check("the detected row has a confidence badge", 'id="filled-tier"' in html)
check("the badge starts hidden", 'id="filled-tier" class="dt-pill hidden' in html)
check("filename and status share one block", 'id="detect-status-msg"' in html)
check("replace is still available", "discardDetectedFile()" in html)

check("the badge is filled from the submitted tier field",
      "document.getElementById('detected_confidence_tier').value" in src)
check("every tier the server can return is mapped",
      all(k in src for k in ("high:", "medium:", "low:", "unverified:")))
check("no tier means no badge, rather than an empty pill",
      "tierBadge.classList.add('hidden');" in src)
check("the badge only shows in the filled state", "state === 'filled' && found" in src)

# The form sits in the card's padded body slot: as a direct child of .card
# it gets no horizontal padding.
card_start = html.index('class="card-title">Add New Software')
after_header = html[card_start:].split("</header>", 1)[1]
check("the form is inside a card section",
      after_header.lstrip().startswith("<section>"))
check("and the form is inside that section",
      after_header.index("<section>") < after_header.index('id="add-software-form"'))
check("the form no longer needs its own top margin",
      'id="add-software-form"' in html and 'class="space-y-4"' in html)

# A <div> inside a <p> is invalid - the browser closes the paragraph early
# and leaves an empty one that still takes its bottom margin.
tabs_block = html.split('role="tablist"')[0][-400:]
check("the tablist is not wrapped in a paragraph", "<p>" not in tabs_block)

# Grey panels on a white card read as the fields being disabled, so both
# panels must sit on the SAME surface as the card around them.
#
# bg-canvas, not bg-white: the same #fff in light, and the panel still
# matches the card when it goes dark.
check("the local panel matches the card surface, not a grey tint",
      'id="offline-fields" class="rounded-lg border border-line bg-canvas p-4"' in html)
check("the chocolatey panel matches it too",
      'id="online-fields" class="hidden rounded-lg border border-line bg-canvas p-4"' in html)
check("neither panel is left on a hardcoded white",
      'id="offline-fields" class="rounded-lg border border-line bg-white p-4"' not in html
      and 'id="online-fields" class="hidden rounded-lg border border-line bg-white p-4"' not in html)
check("no grey panel remains in the card",
      "bg-subtle p-4" not in html)


def js_function(source, name):
    """The text of `function name(...) { ... }`, found by brace matching."""
    start = source.find("function " + name + "(")
    if start == -1:
        return ""
    depth = 0
    for i in range(source.index("{", start), len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
    return ""


def strip_js(code):
    """Blank comments and string literals, so prose and messages never read
    as code."""
    out, i, n = [], 0, len(code)
    while i < n:
        if code.startswith("//", i):
            j = code.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
        elif code.startswith("/*", i):
            j = code.index("*/", i) + 2
            out.append(re.sub(r"[^\n]", " ", code[i:j]))
            i = j
        elif code[i] in "'\"`":
            quote, j = code[i], i + 1
            while code[j] != quote:
                j += 2 if code[j] == "\\" else 1
            out.append(quote + " " * (j - i - 1) + quote)
            i = j + 1
        else:
            out.append(code[i])
            i += 1
    return "".join(out)


# A stale identifier. Every name that something is read FROM inside
# buildCategoryListbox() - `x.foo`, `x.foo()` - must be declared in that
# function (a var, a nested function, a parameter) or be a browser global.
#
# What this can and cannot see, stated plainly: it catches an undeclared
# RECEIVER of a property access in this one function, which is the shape of
# the `btn` bug. It does not catch a bare undeclared name (`foo(x)`, `a = b`),
# a variable that is declared but is the wrong one, or anything the code does
# at runtime - only a click-test proves the menu actually opens, moves focus
# and flips upward.
listbox_code = strip_js(js_function(src, "buildCategoryListbox"))
declared = set(re.findall(r"\bvar\s+([A-Za-z_$][\w$]*)", listbox_code))
declared |= set(re.findall(r"\bfunction\s+([A-Za-z_$][\w$]*)", listbox_code))
for params in re.findall(r"\bfunction\s*[\w$]*\s*\(([^)]*)\)", listbox_code):
    declared |= {p.strip() for p in params.split(",") if p.strip()}
browser_globals = {"document", "window", "Array", "Math", "Event", "JSON", "Object"}
receivers = set(re.findall(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\.", listbox_code))
# Guards the scan itself: if finding the function, stripping or matching broke,
# the receiver set would be empty and the real check would pass on nothing.
check("the scan sees the listbox's own receivers",
      {"button", "menu", "select", "optionButtons"} <= receivers)
undeclared = sorted(receivers - declared - browser_globals)
check("every receiver in buildCategoryListbox() is declared"
      + (f" - undeclared: {undeclared}" if undeclared else ""), not undeclared)

css = open(os.path.join(server_dir, "static", "css", "app.css"), encoding="utf-8").read()

# Every category, the last one included, must be reachable wherever the
# field sits. The add form's .card is overflow: hidden and clips an absolute
# list at its bottom edge (on the Chocolatey tab Category is the last field),
# so only a fixed menu can leave the clip.
#
# What these can and cannot see: they read source, so they prove the menu is
# fixed, sized to the room it has and re-placed on scroll - not that the
# numbers come out right. Only a click-test near the top and the bottom of
# the window proves that.
tailwind = open(os.path.join(server_dir, "static", "css", "tailwind.css"), encoding="utf-8").read()
card_clips = any("overflow:hidden" in rule for rule in re.findall(r"\.card\{[^}]*\}", tailwind))
check("the premise: the add form's card clips what overflows it",
      card_clips and '<section class="card">' in html.split('id="add-software-form"')[0][-1200:])
menu_rule = re.search(r"\n\.ael-menu\s*\{([^}]*)\}", css)
check("so the category menu is fixed, which that clip cannot reach",
      menu_rule is not None and re.search(r"position:\s*fixed;", menu_rule.group(1)) is not None)
check("and it scrolls inside itself rather than growing past the window",
      menu_rule is not None and "max-height:" in menu_rule.group(1)
      and re.search(r"overflow-y:\s*auto;", menu_rule.group(1)) is not None)

place_code = strip_js(js_function(src, "placeMenu"))
check("it is placed from the button's position on screen",
      "button.getBoundingClientRect()" in place_code
      and all(f"menu.style.{side} =" in place_code for side in ("top", "left", "width")))
check("its height is capped to the room on the side it opens",
      re.search(r"menu\.style\.maxHeight\s*=[^;]*\broom\b", place_code) is not None)
check("it opens upward when the room below is short and there is more above",
      re.search(r"below\s*<\s*natural\s*&&\s*above\s*>\s*below", place_code) is not None)
check("the room above stops at the sticky topbar, not the top of the window",
      "app-topbar" in js_function(src, "menuCeiling") and "menuCeiling()" in place_code)
check("it is placed every time it opens",
      "placeMenu();" in strip_js(js_function(src, "openMenu")))

follow_code = strip_js(js_function(src, "followButton"))
listbox_body = strip_js(js_function(src, "buildCategoryListbox"))
check("it follows the button when anything scrolls, in any container",
      "window.addEventListener('scroll', followButton, true);" in js_function(src, "buildCategoryListbox"))
check("and when the window is resized",
      "window.addEventListener('resize', followButton);" in js_function(src, "buildCategoryListbox"))
check("it closes once the button has scrolled out of view",
      "closeMenu(false)" in follow_code and "menuCeiling()" in follow_code
      and "window.innerHeight" in follow_code)
check("the list scrolling itself does not re-place it",
      "menu.contains(event.target)" in follow_code)
# The menu stays where it was in the document, so Tab moves on from the
# list to the next field exactly as before, and a click inside it still
# counts as inside the field.
check("the menu is still inside its field, not moved to the end of the page",
      "host.appendChild(menu);" in listbox_body and "document.body.appendChild(menu)" not in listbox_body)

check("the add button uses the shared btn class", 'class="btn disabled:opacity-40' in html)

# The relocation mechanism must be untouched: Name/Version/Category live in
# ONE node that JS moves between tabs, so the form never submits two sets.
check("the shared field node still exists", 'id="nvc-fields"' in html)
check("both anchors still exist",
      'id="local-nvc-anchor"' in html and 'id="online-nvc-anchor"' in html)
check("the relocation function is untouched", "onlineAnchor.appendChild(nvcFields);" in src)
check("name and version are still paired", "grid grid-cols-1 gap-4 md:grid-cols-2" in html)
check("the silent flag is still visible, not hidden behind an toggle",
      'id="silent_flag"' in html and "Advanced" not in html)


print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)
os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)
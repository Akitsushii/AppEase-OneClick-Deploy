"""Software categories kept in the database: seeded built-ins in order,
existing software keeps its category, the dropdowns read the table, and a
typed category is created only with the software that uses it.
"""
import html
import os
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
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


work = tempfile.mkdtemp(prefix="appease_categories_")
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

if not hasattr(appmod, "software_categories"):
    print("COULD NOT RUN - app.py predates categories in the database.")
    sys.exit(3)
appmod.app.config["TESTING"] = True
PASSWORD = "Str0ng!Passw0rd"
BUILTINS = ["Browser", "Development / IDE", "Multimedia", "Office / Productivity", "Utility",
            "Security / Antivirus", "Networking", "Design / Graphics", "Virtualization", "Runtime / Framework"]


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


def categories():
    return [r["name"] for r in rows("SELECT name FROM software_categories ORDER BY id")]


def dropdown():
    conn = database.get_db()
    try:
        return [c["name"] for c in appmod.software_categories(conn)]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
print("1. The table, the seed, and software already in the library")
# ---------------------------------------------------------------------------
check("the built-ins are seeded, in their order", categories() == BUILTINS)
check("...marked built-in", all(r["is_builtin"] == 1 for r in rows("SELECT is_builtin FROM software_categories")))
check("the seed list is the one in database.py", list(database.BUILTIN_SOFTWARE_CATEGORIES) == BUILTINS)
check("app.py keeps no hard-coded list of its own", not hasattr(appmod, "ALLOWED_CATEGORIES"))
# Software from before the table existed, with values the old list allowed
# and ones it did not.
write("DROP TABLE software_categories")
for name, category in (("Old Chrome", "browser"), ("Old Steam", "Games"), ("Old Blank", None), ("Old VLC", "Multimedia")):
    write("INSERT INTO software (name, version, type, install_command, category) VALUES (?, '1.0', 'online', 'x', ?)",
          (name, category))
database.init_db()
by_name = {r["name"]: r["category"] for r in rows("SELECT name, category FROM software")}
check("a case variant of a built-in takes its spelling", by_name["Old Chrome"] == "Browser")
check("an unknown value is kept as it is", by_name["Old Steam"] == "Games")
check("...and imported as an admin category", rows(
    "SELECT is_builtin FROM software_categories WHERE name = 'Games'") == [{"is_builtin": 0}])
check("no category stays no category", by_name["Old Blank"] is None)
before = rows("SELECT * FROM software_categories ORDER BY id")
database.init_db()
check("running it again changes nothing", rows("SELECT * FROM software_categories ORDER BY id") == before)
write("INSERT OR IGNORE INTO software_categories (name) VALUES ('BROWSER')")
check("the database itself refuses a second spelling",
      rows("SELECT COUNT(*) AS n FROM software_categories WHERE name = 'browser'")[0]["n"] == 1)

# ---------------------------------------------------------------------------
print("1b. Other is no longer a built-in")
# ---------------------------------------------------------------------------
check("Other is not seeded", "Other" not in categories())


def dump():
    conn = sqlite3.connect(database.DB_PATH)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def old_builtin_other():
    """The row every older database still has."""
    write("INSERT INTO software_categories (name, is_builtin, position) VALUES ('Other', 1, 10)")


old_builtin_other()
database.init_db()
check("an old built-in Other that no software uses is removed", "Other" not in categories())
before = dump()
database.init_db()
check("...and running it again changes nothing", dump() == before)

old_builtin_other()
write("INSERT INTO software_categories (name, is_builtin) VALUES ('Zebra Tools', 0)")
kept = [write("INSERT INTO software (name, version, type, install_command, category) VALUES (?, '1.0', 'online', 'x', ?)",
              (name, category)) for name, category in (("Kept A", "Other"), ("Kept B", "Other"))]
software_before = rows("SELECT * FROM software ORDER BY id")
database.init_db()
check("an old built-in Other still in use is kept, as an ordinary admin category",
      rows("SELECT is_builtin, position FROM software_categories WHERE name = 'Other'")
      == [{"is_builtin": 0, "position": None}])
check("...and the software filed under it is not moved or relabelled",
      rows("SELECT * FROM software WHERE id IN (?, ?) ORDER BY id", kept)
      == [r for r in software_before if r["id"] in kept])
check("...sorted A-Z with the other admin categories, no longer pinned last",
      dropdown() == BUILTINS + ["Games", "Other", "Zebra Tools"])
before = dump()
database.init_db()
check("...and running it again changes nothing", dump() == before)
write("DELETE FROM software WHERE id IN (?, ?)", kept)
database.init_db()
check("an Other that is an admin category is theirs: left alone even once unused",
      rows("SELECT is_builtin FROM software_categories WHERE name = 'Other'") == [{"is_builtin": 0}])
write("DELETE FROM software_categories WHERE name IN ('Other', 'Zebra Tools')")

# ---------------------------------------------------------------------------
print("2. Adding software with a category")
# ---------------------------------------------------------------------------
write("UPDATE admins SET password_hash = ?, must_change_password = 0 WHERE username = 'admin'",
      (generate_password_hash(PASSWORD),))
ADMIN = rows("SELECT id FROM admins WHERE username = 'admin'")[0]["id"]
write("INSERT INTO admins (username, password_hash, role, must_change_password) VALUES ('faculty1', ?, 'faculty', 0)",
      (generate_password_hash(PASSWORD),))
admin = appmod.app.test_client()
admin.post("/login", data={"username": "admin", "password": PASSWORD})
faculty = appmod.app.test_client()
faculty.post("/login", data={"username": "faculty1", "password": PASSWORD})
NAMES = iter(range(1, 100))


def add(category, new_category=None, client=None, name=None):
    data = {"name": name or f"Tool {next(NAMES)}", "version": "1.0", "category": category, "type": "online",
            "package_name": "sometool"}
    if new_category is not None:
        data["new_category"] = new_category
    response = (client or admin).post("/software", data=data)
    return response, data["name"]


def saved_category(name):
    found = rows("SELECT category FROM software WHERE name = ?", (name,))
    return found[0]["category"] if found else "NOT SAVED"


def audits(action):
    return [r["details"] for r in rows("SELECT details FROM audit_log WHERE action_type = ? ORDER BY id", (action,))]


_, name = add("Networking")
check("a built-in: saved as it is", saved_category(name) == "Networking")
_, name = add("Games")
check("an imported admin category can be chosen", saved_category(name) == "Games")
response, name = add("Nope")
check("a category that does not exist: refused", saved_category(name) == "NOT SAVED"
      and "Invalid category." in response.get_data(as_text=True))

before = categories()
_, name = add("__new__", "  Lab   Tools  ")
check("Choose to add...: the software is saved with the new category, cleaned", saved_category(name) == "Lab Tools")
added = rows("SELECT is_builtin, created_by FROM software_categories WHERE name = 'Lab Tools'")
check("...the category is created, as an admin category, by this admin", added == [{"is_builtin": 0, "created_by": ADMIN}])
check("...one category_added audit row", audits("category_added") == ["Added software category 'Lab Tools'"])
_, name = add("__new__", "browser")
check("typing 'browser' picks the existing Browser", saved_category(name) == "Browser")
_, name = add("__new__", "LAB TOOLS")
check("...and 'LAB TOOLS' the Lab Tools just added", saved_category(name) == "Lab Tools")
check("neither created anything or wrote an audit row",
      categories() == before + ["Lab Tools"] and len(audits("category_added")) == 1)

for label, typed, message in (
        ("empty", "   ", "Type a name for the new category."),
        ("one character", "X", "A category name must be 2 to 30 characters."),
        ("31 characters", "A" * 31, "A category name must be 2 to 30 characters."),
        ("a quote", "Lab's Tools", "A category name can only contain letters"),
        ("a < sign", "Lab <b>", "A category name can only contain letters"),
        ("starting with a symbol", "/Tools", "A category name can only contain letters")):
    response, name = add("__new__", typed)
    check(f"{label}: refused, saying why", saved_category(name) == "NOT SAVED"
          and message in response.get_data(as_text=True).replace("&#39;", "'"))
check("...and none of them created a category", categories() == before + ["Lab Tools"])
for allowed in ("C++ Tools", "Lab (Annex)", "R&D / Science", "Web-Apps 2.0"):
    _, name = add("__new__", allowed)
    check(f"'{allowed}' is allowed", saved_category(name) == allowed)
check("thirty characters is allowed", saved_category(add("__new__", "B" * 30)[1]) == "B" * 30)

before = categories()
response, _ = add("__new__", "Never Saved", name="Tool!!bad")
check("a new category is not created when the software is refused", "Never Saved" not in categories())
write("INSERT INTO software (name, version, type, install_command, category) "
      "VALUES ('Dup Tool', '1.0', 'online', 'x', 'Browser')")
response, _ = add("__new__", "Bounce Category", name="Dup Tool")
page = response.get_data(as_text=True)
check("the duplicate-name bounce creates nothing...", "Bounce Category" not in categories())
check("...and puts Choose to add... and the typed name back",
      re.search(r'<option value="__new__"\s+selected>Choose to add\.\.\.</option>', page) is not None
      and 'value="Bounce Category"' in page)
response, _ = add("__new__", "browser", name="Dup Tool")
page = response.get_data(as_text=True)
check("a bounce after typing 'browser' comes back with Browser itself selected",
      re.search(r'<option value="Browser"\s+selected>Browser</option>', page) is not None
      and re.search(r'<option value="__new__"\s+selected>', page) is None)

# Another admin adds the same category between the check and the insert.
write("INSERT INTO software_categories (name, is_builtin) VALUES ('Race Category', 0)")
with appmod.app.test_request_context():
    appmod.session["admin_id"] = ADMIN
    conn = database.get_db()
    try:
        result = appmod.add_software_category(conn, "race CATEGORY")
        conn.commit()
    finally:
        conn.close()
check(f"a category added by someone else a moment earlier: its spelling, and not counted as created ({result})",
      result == ("Race Category", False))

response, name = add("__new__", "Faculty Made", client=faculty)
check("faculty cannot add software or categories", saved_category(name) == "NOT SAVED"
      and "Faculty Made" not in categories())

# ---------------------------------------------------------------------------
print("3. The edit drawer")
# ---------------------------------------------------------------------------
target = rows("SELECT id, name FROM software WHERE category = 'Networking' LIMIT 1")[0]


def edit(category, new_category=None):
    data = {"name": target["name"], "version": "1.0", "category": category}
    if new_category is not None:
        data["new_category"] = new_category
    response = admin.post(f"/software/{target['id']}/edit", data=data)
    return response, parse_qs(urlparse(response.headers.get("Location", "")).query)


_, query = edit("__new__", "Classroom Apps")
check("edit with a new category: saved, created", saved_category(target["name"]) == "Classroom Apps"
      and "Classroom Apps" in categories())
check("...one category_added row for it", audits("category_added")[-1] == "Added software category 'Classroom Apps'")
count = len(audits("category_added"))
edit("__new__", "classroom APPS")
check("edit typing an existing one in another case: picks it, creates nothing",
      saved_category(target["name"]) == "Classroom Apps" and len(audits("category_added")) == count)
_, query = edit("__new__", "Bad'Name")
check("edit with an invalid name: refused, saying why",
      saved_category(target["name"]) == "Classroom Apps"
      and query.get("error", [""])[0].startswith("A category name can only contain letters"))
_, query = edit("Nope")
check("edit to a category that does not exist: refused", query.get("error") == ["Invalid category."])

# ---------------------------------------------------------------------------
print("4. The dropdowns")
# ---------------------------------------------------------------------------
order = dropdown()
added_names = sorted([c for c in order if c not in BUILTINS], key=str.lower)
check("built-ins first in their order, then admin-added A-Z",
      order == BUILTINS + added_names)
page = admin.get("/software").get_data(as_text=True)
add_select = re.search(r'<select id="category".*?</select>', page, re.S).group(0)
drawer_select = re.search(r'<select class="input" id="sw-edit-category".*?</select>', page, re.S).group(0)
for label, select in (("the add form", add_select), ("the edit drawer", drawer_select)):
    # Values as the browser reads them: "R&D / Science" is written R&amp;D.
    values = [html.unescape(v) for v in re.findall(r'<option value="([^"]*)"', select)]
    check(f"{label}: every category, in order, then Choose to add... last",
          values == [""] + order + ["__new__"] and "Choose to add...</option>" in select.split("<option")[-1])
    check(f"{label}: no Other when no software uses it", "Other" not in values)
check("the add form's name field is there, hidden until chosen",
      re.search(r'<span id="new-category-field" class="hidden mt-2 block">', page) is not None
      and 'name="new_category"' in page)
check("the drawer's name field is there, hidden until chosen",
      'id="sw-edit-new-category-field"' in page
      and re.search(r'class="field hidden" id="sw-edit-new-category-field"', page) is not None)
check("the page's rule matches the server's (source - click-test the behaviour)",
      "function newCategoryError(raw)" in page and "/^[A-Za-z0-9][A-Za-z0-9 \\/&\\-.()+]*$/" in page
      and appmod.CATEGORY_NAME_PATTERN.pattern == r"^[A-Za-z0-9][A-Za-z0-9 /&\-.()+]*$")
check("no category list is written into the page by hand any more",
      '<option value="Development / IDE">Development / IDE</option>\n                            <option value="Multimedia">'
      not in page)

# ---------------------------------------------------------------------------
print("5. Grey and the generic icon for an admin category")
# ---------------------------------------------------------------------------
# The deploy list only renders with at least one computer to deploy to.
write("INSERT INTO computers (hostname, status) VALUES ('LAB1-PC01', 'online')")
deploy = admin.get("/deploy").get_data(as_text=True)
check("the deploy page groups by the category saved", 'data-category="Lab Tools"' in deploy)
panel = open(os.path.join(server_dir, "static", "js", "deploy-panel.js"), encoding="utf-8").read()
check("deploy panel: an unknown category gets the generic icon, Uncategorized none",
      "return CATEGORY_ICONS[key] || (key ? 'other.png' : '');" in panel)
check("...and the neutral accent", "return CATEGORY_ACCENTS[key] || NEUTRAL_ACCENT;" in panel)
check("the library's icon list: an unknown category gets the generic icon",
      "var file = value ? (CATEGORY_ICONS[value] || 'other.png') : '';" in page)
check("the generic icon exists", os.path.isfile(os.path.join(server_dir, "static", "img", "other.png")))
check("category_added is not red", appmod.audit_tone("category_added") != "bad")

print()
print(f"{passed} passed, {len(failed)} failed")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

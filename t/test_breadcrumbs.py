"""The breadcrumb names the sidebar section the page is in, checked against
a literal table and against the sidebar rendered on the same page - which,
unlike the table, cannot be updated by mistake with the code.
"""
import html.parser
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


work = tempfile.mkdtemp(prefix="appease_crumbs_")
server_dir = os.path.join(work, "server")
# Never the real database or secrets - see test_agent_inventory.py.
shutil.copytree(
    os.path.join(ROOT, "server"), server_dir,
    ignore=shutil.ignore_patterns(
        "appease.db*", "__pycache__", "*.pyc", ".env",
        "uploads", "staging", "report_photos", "avatars",
    ),
)
sys.path.insert(0, server_dir)
os.chdir(server_dir)
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))

import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()
import app as appmod  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402
appmod.app.config["TESTING"] = True

PASSWORD = "Str0ng!Passw0rd"
conn = sqlite3.connect(database.DB_PATH)
conn.execute("UPDATE admins SET password_hash = ?, must_change_password = 0 WHERE username = 'admin'",
             (generate_password_hash(PASSWORD),))
conn.execute("INSERT INTO admins (username, password_hash, first_name, last_name, role, must_change_password) "
             "VALUES ('faculty.one', ?, 'Fac', 'Ulty', 'faculty', 0)", (generate_password_hash(PASSWORD),))
conn.commit()
conn.close()


class Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.children, self.text = tag, dict(attrs), parent, [], ""


VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr",
        "path", "circle", "rect", "line", "polyline", "polygon"}


class Tree(html.parser.HTMLParser):
    """Just enough of a DOM: tags, attributes, text, parents."""

    def __init__(self):
        super().__init__()
        self.root = Node("root", [], None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is not self.root:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def walk(node):
    for child in node.children:
        if isinstance(child, Node):
            yield child
            yield from walk(child)


def text(node):
    out = []
    for child in node.children:
        out.append(child if isinstance(child, str) else text(child))
    return " ".join(" ".join(out).split())


def parse(page):
    tree = Tree()
    tree.feed(page)
    return tree.root


def crumbs(root):
    nav = next((n for n in walk(root) if n.tag == "nav" and "breadcrumb" in n.attrs.get("class", "")), None)
    if nav is None:
        return None
    items = []
    for li in (n for n in walk(nav) if n.tag == "li"):
        if li.attrs.get("aria-hidden") == "true":
            continue
        link = next((n for n in walk(li) if n.tag == "a"), None)
        items.append((text(li), link.attrs.get("href") if link else None))
    return items


def sidebar_section(root):
    """The section the sidebar itself puts the highlighted page in."""
    aside = next(n for n in walk(root) if n.tag == "aside")
    active = [n for n in walk(aside) if n.tag == "a" and n.attrs.get("data-active") == "true"]
    if not active:
        return None
    page_link = active[-1]                      # the deepest: the page, not its parent
    n = page_link
    while n is not None:
        if n.tag == "ul" and n.attrs.get("id") == "dashboard-subnav":
            return "Dashboard"
        if n.attrs.get("role") == "group":
            heading = next((h for h in walk(n) if h.tag == "h3"), None)
            return text(heading) if heading else None
        n = n.parent
    return None


def sidebar_sections(root):
    aside = next(n for n in walk(root) if n.tag == "aside")
    return {text(h) for h in walk(aside) if h.tag == "h3"}


def title_name(root):
    title = next(n for n in walk(root) if n.tag == "title")
    return re.sub(r"\s+-\s+AppEase$", "", text(title))


def signed_in(username):
    client = appmod.app.test_client()
    res = client.post("/login", data={"username": username, "password": PASSWORD})
    assert res.status_code == 302 and "login" not in res.headers.get("Location", "")
    return client


ADMIN = {
    "/": ("Dashboard", "Overview"),
    "/computers": ("Dashboard", "Lab Computers"),
    "/equipment": ("Dashboard", "Equipment"),
    "/deploy": ("Main", "Deploy Software"),
    "/software": ("Main", "Software Library"),
    "/settings": ("Account", "Settings"),
    "/problem-reports": ("Reports", "Problem Reports"),
    "/notifications": ("Reports", None),        # its title is #5's business
    "/admins": ("System", "Accounts"),
    "/admins/audit-trail": ("System", "Audit Trail"),
}
FACULTY = {
    "/settings": ("Account", "Settings"),
    "/problem-reports": ("Reports", "Problem Reports"),
    "/notifications": ("Reports", None),
}


def audit(role, client, pages):
    for path, (section, page) in pages.items():
        res = client.get(path)
        check(f"{role} {path}: page loads", res.status_code == 200)
        root = parse(res.get_data(as_text=True))
        got = crumbs(root)
        check(f"{role} {path}: has a breadcrumb", bool(got))
        if not got:
            continue
        check(f"{role} {path}: two crumbs", len(got) == 2)
        check(f"{role} {path}: first crumb is {section!r} (got {got[0][0]!r})", got[0][0] == section)
        check(f"{role} {path}: first crumb matches the sidebar's own section",
              got[0][0] == sidebar_section(root))
        check(f"{role} {path}: first crumb is a section this account's sidebar shows",
              got[0][0] in sidebar_sections(root) or (got[0][0] == "Dashboard" and role == "admin"))
        if section == "Dashboard":
            check(f"{role} {path}: Dashboard crumb links to the dashboard", got[0][1] == "/")
        else:
            check(f"{role} {path}: a section that is not a page is not a link", got[0][1] is None)
        if page:
            check(f"{role} {path}: last crumb is {page!r} (got {got[-1][0]!r})", got[-1][0] == page)
        check(f"{role} {path}: no 'Main' crumb outside the Main section",
              section == "Main" or all(c[0] != "Main" for c in got))
        if path not in ("/",):
            check(f"{role} {path}: last crumb matches the tab title", got[-1][0] == title_name(root))


print("\n=== 1. Admin: every page ===")
audit("admin", signed_in("admin"), ADMIN)

print("\n=== 2. Faculty: every page, and never a section faculty lack ===")
faculty = signed_in("faculty.one")
audit("faculty", faculty, FACULTY)
for path in FACULTY:
    root = parse(faculty.get(path).get_data(as_text=True))
    first = crumbs(root)[0][0]
    check(f"faculty {path}: sidebar has no Main or System section", not {"Main", "System"} & sidebar_sections(root))
    check(f"faculty {path}: crumb is not Main, Dashboard or System", first not in ("Main", "Dashboard", "System"))

print("\n=== 3. The dashboard ===")
root = parse(signed_in("admin").get("/").get_data(as_text=True))
check("the dashboard's tab still says Dashboard", title_name(root) == "Dashboard")
check("its crumb names the highlighted sidebar item, Overview", crumbs(root)[-1][0] == "Overview")


print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)

os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

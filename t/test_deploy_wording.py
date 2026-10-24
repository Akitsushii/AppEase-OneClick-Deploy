"""Deploy Software calls software "software", as the rest of the app does,
on the page and in new Audit Trail rows; older rows keep their words.
deploy-panel.js cannot run here, so its visible strings are scanned too.
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


work = tempfile.mkdtemp(prefix="appease_deploywords_")
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
DB = database.DB_PATH


def q(sql, args=()):
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, args).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


q("UPDATE admins SET password_hash = ?, must_change_password = 0 WHERE username = 'admin'",
  (generate_password_hash(PASSWORD),))
for name in ("Google Chrome", "7-Zip", "VLC Media Player"):
    q("INSERT INTO software (name, version, type, install_command, category) "
      "VALUES (?, '1.0', 'online', 'choco install probe -y', 'Utility')", (name,))
for n in (1, 2, 3):
    q("INSERT INTO computers (hostname, ip_address, status, last_seen) "
      "VALUES (?, ?, 'online', CURRENT_TIMESTAMP)", (f"LAB-PC-0{n}", f"10.0.0.{n}"))
# An older entry, in the old words.
q("INSERT INTO audit_log (actor_admin_id, action_type, details, target_type, target_label) "
  "VALUES (1, 'deployment_created', 'Deployed 2 apps to 4 computer(s) (8 job(s) created)', 'deployment', '2 apps')")
OLD = [tuple(r) for r in q("SELECT id, details, target_label FROM audit_log")]

client = appmod.app.test_client()
client.post("/login", data={"username": "admin", "password": PASSWORD})


class Visible(html.parser.HTMLParser):
    """Text a person can see or hear: text nodes, placeholders, labels."""

    def __init__(self):
        super().__init__()
        self.skip, self.bits = 0, []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        for name, value in attrs:
            if name in ("placeholder", "aria-label", "title", "alt") and value:
                self.bits.append(value)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.bits.append(" ".join(data.split()))


print("\n=== 1. The Deploy page ===")
page = client.get("/deploy").get_data(as_text=True)
seen = Visible()
seen.feed(page)
apps = [b for b in seen.bits if re.search(r"\bapps?\b", b, re.I)]
check("no visible 'app' or 'apps' anywhere on the page", apps == [])
for bit in apps:
    print("    still says:", bit)
check("heading reads Deploy Software", '<h2 class="card-title">Deploy Software</h2>' in page)
check("the picker is labelled Software", "Software *" in seen.bits)
check("its search says software", "Search software..." in seen.bits)
check("the selection panel and tally say Software", seen.bits.count("Software") >= 2)
check("the screen-reader counter says software items", "0 software items × 0 computers = 0 jobs" in seen.bits)

print("\n=== 2. The counter and progress title (deploy-panel.js) ===")
js = open(os.path.join(server_dir, "static", "js", "deploy-panel.js"), encoding="utf-8").read()
js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
js = re.sub(r"^\s*//.*$", "", js, flags=re.M)      # whole-line comments
js = re.sub(r"\s//\s.*$", "", js, flags=re.M)       # trailing comments
strings = re.findall(r"'([^'\\]*(?:\\.[^'\\]*)*)'", js)
visible_apps = [s for s in strings if re.search(r"\bapps?\b", s, re.I) and " " in s]
check("no user-visible string in deploy-panel.js says app or apps", visible_apps == [])
for s in visible_apps:
    print("    still says:", s)

print("\n=== 3. New Audit Trail entries, old ones untouched ===")
ids = [str(r["id"]) for r in q("SELECT id FROM software ORDER BY id")]
res = client.post("/deploy-batch", data={"software_ids": ids[:2], "computer_hostnames": ["LAB-PC-01", "LAB-PC-02"]})
check("a two-software deployment is accepted", res.status_code in (200, 302))
new = q("SELECT details, target_label FROM audit_log WHERE action_type = 'deployment_created' ORDER BY id DESC LIMIT 1")[0]
print("    recorded:", dict(new))
check("it is recorded as 2 software items", new["target_label"] == "2 software items")
check("and its details say software items", "2 software items" in new["details"])
check("and never apps", "apps" not in new["details"] and "apps" not in new["target_label"])
res = client.post("/deploy-batch", data={"software_ids": ids[2:3], "computer_hostnames": ["LAB-PC-03"]})
single = q("SELECT target_label FROM audit_log WHERE action_type = 'deployment_created' ORDER BY id DESC LIMIT 1")[0]
# software_label(): name and version, as every audit target is written.
check("a single-software deployment still names the software", single["target_label"] == "VLC Media Player 1.0")
check("the row written before the change keeps its words",
      [tuple(r) for r in q("SELECT id, details, target_label FROM audit_log WHERE id = ?", (OLD[0][0],))] == OLD)
trail = client.get("/admins/audit-trail").get_data(as_text=True)
check("and the Audit Trail still shows it as written", "Deployed 2 apps to 4 computer(s)" in trail)


print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)

os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

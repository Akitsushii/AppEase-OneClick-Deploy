"""Agent capabilities: what an agent says it can do beyond installing. The
agent API has no login, so the server keeps only known names, and a
capability covers its own uninstall method only.
"""
import logging
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


work = tempfile.mkdtemp(prefix="appease_caps_")
server_dir = os.path.join(work, "server")
shutil.copytree(os.path.join(ROOT, "server"), server_dir, ignore=shutil.ignore_patterns(
    "appease.db*", "__pycache__", "*.pyc", ".env", "uploads", "staging", "report_photos", "avatars", "venv"))
agent_dir = os.path.join(work, "agent")
os.makedirs(agent_dir)
shutil.copy(os.path.join(ROOT, "agent", "agent.py"), agent_dir)
sys.path.insert(0, agent_dir)
sys.path.insert(0, server_dir)
os.chdir(server_dir)
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))

import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()
import app as appmod  # noqa: E402
import agent  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

if not hasattr(appmod, "clean_agent_capabilities") or not hasattr(agent, "agent_capabilities"):
    print("COULD NOT RUN - app.py or agent.py predates capabilities.")
    sys.exit(3)
appmod.app.config["TESTING"] = True
client = appmod.app.test_client()


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


def stored(host):
    conn = sqlite3.connect(database.DB_PATH)
    try:
        row = conn.execute("SELECT capabilities FROM computers WHERE hostname = ?", (host,)).fetchone()
        return row[0] if row else "no such computer"
    finally:
        conn.close()


MISSING = object()


def poll(host, capabilities=MISSING):
    body = {"computer_hostname": host}
    if capabilities is not MISSING:
        body["capabilities"] = capabilities
    return client.post("/api/agent/poll", json=body)


# ---------------------------------------------------------------------------
print("1. The server keeps known names only")
# ---------------------------------------------------------------------------
logs.take()
res = poll("PC-1", ["uninstall:msi"])
check("a known capability is kept", res.status_code == 200 and stored("PC-1") == "uninstall:msi")
check("...with nothing logged", logs.take() == [])
poll("PC-1", ["uninstall:msi", "uninstall:choco", "uninstall:msi"])
check("several are kept sorted, each once", stored("PC-1") == "uninstall:choco uninstall:msi")
poll("PC-1", [])
check("an empty list replaces them", stored("PC-1") == "")
poll("PC-1", ["uninstall:choco"])
poll("PC-1")
check("an agent that sends no list (from before capabilities) is an empty list, replacing the last one",
      stored("PC-1") == "")
check("...and is not logged as anything wrong", logs.take() == [])

for label, sent, expected_log in (
    ("a method that does not exist", ["uninstall:msix", "uninstall:msi"], "'uninstall:msix'"),
    ("a near miss in case", ["UNINSTALL:MSI"], "'UNINSTALL:MSI'"),
    ("a near miss in spacing", ["uninstall:msi "], "'uninstall:msi '"),
    ("a bare 'uninstall'", ["uninstall"], "'uninstall'"),
    ("a number", [1, "uninstall:msi"], "1"),
    ("null", [None], "None"),
    ("an object", [{"uninstall:msi": True}], "{'uninstall:msi': True}"),
):
    res = poll("PC-2", sent)
    lines = logs.take()
    kept = "uninstall:msi" if "uninstall:msi" in sent else ""
    check(f"{label}: ignored, the poll still works", res.status_code == 200 and stored("PC-2") == kept)
    check(f"{label}: logged once, naming what was ignored",
          len(lines) == 1 and "ignored" in lines[0] and expected_log in lines[0] and "PC-2" in lines[0])
for label, sent in (("a string instead of a list", "uninstall:msi"), ("an object instead of a list",
                                                                      {"uninstall:msi": True})):
    res = poll("PC-2", sent)
    lines = logs.take()
    check(f"{label}: nothing kept, the poll still works", res.status_code == 200 and stored("PC-2") == "")
    check(f"{label}: logged", len(lines) == 1 and "instead of a list" in lines[0])
cap = appmod.MAX_AGENT_CAPABILITIES
check("the cap is a small number", 1 <= cap <= 32)
poll("PC-3", ["junk"] * (cap - 1) + ["uninstall:msi"])
check(f"a known name within the first {cap} entries is kept", stored("PC-3") == "uninstall:msi")
logs.take()
poll("PC-3", ["junk"] * cap + ["uninstall:msi"])
lines = logs.take()
check(f"one past the first {cap} is not looked at", stored("PC-3") == "")
check("...and the log says entries were cut, in one line", len(lines) == 1 and f"1 more past the first {cap}" in lines[0])
poll("PC-4", ["x" * 5000, "a\nFAKE LOG LINE"])
lines = logs.take()
check("whatever arrives, the log gets one short line with no raw newline in it",
      len(lines) == 1 and "\n" not in lines[0] and len(lines[0]) < 400)

poll("PC-5", ["uninstall:msi"])
client.post("/api/agent/heartbeat", json={"computer_hostname": "PC-5"})
check("a heartbeat leaves the list alone", stored("PC-5") == "uninstall:msi")
write("INSERT INTO computers (hostname, status) VALUES ('PC-NEVER', 'offline')")
check("a computer no agent has polled from since has none recorded (NULL)", stored("PC-NEVER") is None)

# ---------------------------------------------------------------------------
print("2. A capability covers its own method only")
# ---------------------------------------------------------------------------
CASES = {
    (None, "msi"): False, ("", "msi"): False, ("uninstall:msi", "msi"): True,
    ("uninstall:msi", "choco"): False, ("uninstall:choco", "choco"): True, ("uninstall:choco", "msi"): False,
    ("uninstall:choco uninstall:msi", "msi"): True, ("uninstall:choco uninstall:msi", "choco"): True,
    ("uninstall:choco uninstall:msi", "exe"): False, ("uninstall:msi", None): False,
    ("uninstall:exe", "exe"): True, ("uninstall:exe", "msi"): False, ("uninstall:exe", "choco"): False,
    ("uninstall:choco uninstall:exe uninstall:msi", "exe"): True,
    ("uninstall:msi", "nonsense"): False, ("uninstall:msix", "msi"): False,
}
for (caps, method), expected in CASES.items():
    check(f"can_uninstall({caps!r}, {method!r}) is {expected}", appmod.can_uninstall(caps, method) is expected)

# ---------------------------------------------------------------------------
print("3. Computer Details shows what the PC reported")
# ---------------------------------------------------------------------------
write("UPDATE admins SET password_hash = ?, must_change_password = 0 WHERE username = 'admin'",
      (generate_password_hash("Str0ng!Passw0rd"),))
client.post("/login", data={"username": "admin", "password": "Str0ng!Passw0rd"})
poll("PC-BOTH", ["uninstall:choco", "uninstall:msi"])
poll("PC-MSI", ["uninstall:msi"])
poll("PC-NONE")
page = client.get("/computers").get_data(as_text=True)


def attribute(host):
    match = re.search(r'<tr[^>]*data-dt-edit-hostname="%s"[^>]*>' % host, page, re.S)
    found = match and re.search(r'data-asset-uninstall="([^"]*)"', match.group(0))
    return found.group(1) if found else "missing"


check("both methods", attribute("PC-BOTH") == "Windows Installer (MSI), Chocolatey")
check("MSI only", attribute("PC-MSI") == "Windows Installer (MSI)")
check("an agent that reported nothing: None", attribute("PC-NONE") == "None")
check("never polled: blank, which the drawer shows as Not recorded", attribute("PC-NEVER") == "")
hardware = page[page.index(">Hardware</p>"):page.index('id="comp-details-reported"')]
check("the drawer has an Uninstall support row in Hardware",
      re.search(r'<dt class="text-muted">Uninstall support</dt>\s*<dd id="comp-details-uninstall"', hardware)
      is not None)
check("...which the drawer's script fills from the row",
      "'data-asset-uninstall': 'comp-details-uninstall'," in page)

# ---------------------------------------------------------------------------
print("4. The contract with the agent")
# ---------------------------------------------------------------------------
# Every capability name the server knows, and the installation method each
# one lets a computer uninstall. Named, never matched by pattern.
KNOWN = {"uninstall:msi": "msi", "uninstall:choco": "choco", "uninstall:exe": "exe"}
# What this agent advertises, each with the suite that drives that
# uninstall end to end through the real code.
ADVERTISED = {"uninstall:msi": "test_uninstall_msi.py", "uninstall:choco": "test_uninstall_choco.py",
              "uninstall:exe": "test_uninstall_exe_run.py",
              "uninstall:exe-user": "test_uninstall_exe_user_agent.py"}

# Names that narrow a method rather than add one: uninstall:exe-user lets
# a computer that says uninstall:exe take per-user EXE rows too.
REFINES = {"uninstall:exe-user": "exe"}

check("the server knows exactly the agreed names, each for its own method",
      {v: k for k, v in appmod.UNINSTALL_CAPABILITY_BY_METHOD.items()} == KNOWN
      and appmod.KNOWN_AGENT_CAPABILITIES == set(KNOWN) | set(REFINES))
check("every installation method has exactly one capability (exe since Phase 3a)",
      sorted(KNOWN.values()) == sorted(database.INSTALLATION_METHODS))
advertised = agent.agent_capabilities()
check(f"the agent advertises exactly what is listed as built (it sends {advertised})",
      advertised == sorted(ADVERTISED))
check("everything the agent can advertise is a name the server knows",
      set(advertised) <= set(KNOWN) | set(REFINES))
for capability, suite in ADVERTISED.items():
    method = KNOWN.get(capability) or REFINES.get(capability)
    check(f"{capability} has code behind it: UNINSTALLERS[{method!r}]",
          callable(agent.UNINSTALLERS.get(method)))
    check(f"{capability} is driven end to end by {suite}", os.path.isfile(os.path.join(HERE, suite)))

# The list is DERIVED from UNINSTALLERS, so it cannot get ahead of the code.
saved = dict(agent.UNINSTALLERS)
try:
    agent.UNINSTALLERS.clear()
    check("with no uninstall code, the agent advertises nothing", agent.agent_capabilities() == [])
    agent.UNINSTALLERS["msi"] = lambda *args, **kwargs: None
    check("an uninstaller for a method is what makes the agent advertise it",
          agent.agent_capabilities() == ["uninstall:msi"])
finally:
    agent.UNINSTALLERS.clear()
    agent.UNINSTALLERS.update(saved)

# The real agent, polling the real server.
sent = []


def through_client(path, payload):
    sent.append((path, payload))
    return client.post(path, json=payload).get_json()


real_post = agent.post_json
agent.post_json = through_client
agent.COMPUTER_HOSTNAME = "PC-AGENT"
agent.AGENT_ID = None
try:
    agent.poll_once()
    check("the agent sends its list in the poll", sent and sent[0][0] == "/api/agent/poll"
          and sent[0][1].get("capabilities") == sorted(ADVERTISED))
    check("...and the server keeps it", stored("PC-AGENT") == " ".join(sorted(ADVERTISED)))
    agent.UNINSTALLERS["msix"] = lambda *args, **kwargs: None
    logs.take()
    agent.poll_once()
    check("an agent advertising a name the server does not know is ignored and logged",
          stored("PC-AGENT") == " ".join(sorted(ADVERTISED)) and any("uninstall:msix" in l for l in logs.take()))
finally:
    agent.UNINSTALLERS.pop("msix", None)
    agent.post_json = real_post

# ---------------------------------------------------------------------------
print()
print(f"{passed} passed, {len(failed)} failed")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

"""The "Installed by AppEase" section of the Lab Computers details drawer.
The drawer's script cannot run here: this checks the data it reads, the
markup it fills, and that it survives rendering. The clicks are manual.
"""
import json
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


def server_source():
    for index, arg in enumerate(sys.argv):
        if arg == "--server" and index + 1 < len(sys.argv):
            return os.path.abspath(sys.argv[index + 1])
    return os.path.join(ROOT, "server")


work = tempfile.mkdtemp(prefix="appease_drawer_")
server_dir = os.path.join(work, "server")
shutil.copytree(server_source(), server_dir, ignore=shutil.ignore_patterns(
    "appease.db*", "__pycache__", "*.pyc", ".env", "uploads", "staging", "report_photos", "avatars", "venv"))
sys.path.insert(0, server_dir)
os.chdir(server_dir)
import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
import app as appmod  # noqa: E402

if not hasattr(appmod, "installations_by_computer"):
    print("COULD NOT RUN - app.py predates the Installed by AppEase list.")
    sys.exit(3)
appmod.app.config["TESTING"] = True
DOT = "·"


def write(sql, args=()):
    conn = sqlite3.connect(database.DB_PATH)
    try:
        cursor = conn.execute(sql, args)
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def computer(name):
    return write("INSERT INTO computers (hostname, status) VALUES (?, 'online')", (name,))


def software(name, version="1.0"):
    return write("INSERT INTO software (name, version, type, install_command) VALUES (?, ?, 'x', 'x')",
                 (name, version))


QUIET = '"C:\\Program Files\\App\\uninstall.exe" /S'


def installation(computer_id, key, name, software_id=None, version=None, display_version=None, method="exe",
                 state="installed", reason=None, package=None, hive="HKEY_LOCAL_MACHINE", changed=None, quiet=QUIET,
                 sid=None):
    return write(
        "INSERT INTO installations (computer_id, software_id, software_name, software_version, method, record_key, "
        "choco_package_id, reg_hive, reg_user_sid, display_version, state, state_reason, state_changed_at, "
        "quiet_uninstall_string) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP), ?)",
        (computer_id, software_id, name, version, method, key, package, hive, sid, display_version, state, reason,
         changed, quiet if method == "exe" else None))


def the_map():
    conn = database.get_db()
    try:
        return appmod.installations_by_computer(conn)
    finally:
        conn.close()


PC = computer("LAB1-PC07")
OTHER = computer("LAB1-PC08")
EMPTY = computer("LAB1-PC09")
S = {n: software(n) for n in ("Notepad++", "VLC", "7-Zip", "Visual Studio Code", "Audacity", "Discord", "Python 3.14",
                              "7-Zip old", "Zoom", "aardvark tool")}

installation(PC, "exe:a", "Notepad++", S["Notepad++"], "8.7", "8.7.1")
installation(PC, "choco:vlc.install", "VLC", S["VLC"], "3.0", "3.0.23", method="choco", package="vlc.install")
installation(PC, "msi:{X}", "7-Zip", S["7-Zip"], "24.08", "24.08.00.0", method="msi")
installation(PC, "exe:HKCU:S-1:CODE", "Visual Studio Code", S["Visual Studio Code"], "1.94", "1.94.2",
             hive="HKEY_CURRENT_USER", sid="S-1-5-21-1-1001",
             quiet='"C:\\Users\\LabUser\\AppData\\Local\\Programs\\Microsoft VS Code\\unins000.exe" /SILENT')
installation(PC, "exe:b", "Audacity", S["Audacity"], "3.6", "3.6.4", state="unavailable", reason="no_silent_uninstall")
installation(PC, "none:software:6", "Discord", S["Discord"], "1.0.9", None, state="unavailable",
             reason="several_new_entries")
installation(PC, "choco:python314", "Python 3.14", S["Python 3.14"], "3.14.8", "3.14.8", method="choco",
             state="unavailable", reason="protected", package="python314")
installation(PC, "msi:{OLD}", "7-Zip old", S["7-Zip old"], "23.01", "23.01.00.0", method="msi", state="replaced",
             reason="replaced", changed="2026-10-01 07:14:00")
installation(PC, "exe:zoom", "Zoom", S["Zoom"], "6.0", None, state="removed", changed="2026-10-02 01:00:00")
installation(PC, "exe:gone", "Gone App", None, "2.0", None)
installation(PC, "exe:aardvark", "aardvark tool", S["aardvark tool"], None, None)
installation(OTHER, "exe:other", "Only On PC08", None, "1", "1")
THIRD = computer("LAB1-PC10")
installation(THIRD, "exe:helper", "Helper", None, "1", "1", quiet='"C:\\ProgramData\\Helper\\uninstall.exe" /S')
installation(THIRD, "exe:bare", "Bare", None, "1", "1", quiet=None)
installation(THIRD, "exe:winscp", "WinSCP", None, "6.5.7", "6.5.7",
             quiet='"C:\\Program Files (x86)\\WinSCP\\unins000.exe" /SILENT')

m = the_map()
pc = m.get(str(PC), {})
live = {i["name"]: i for i in pc.get("live", [])}
history = {i["name"]: i for i in pc.get("history", [])}


# ---------------------------------------------------------------------------
print("1. Each row reads as agreed")
# ---------------------------------------------------------------------------
check("keyed by computer id as a string", str(PC) in m and str(OTHER) in m)
check("a computer with nothing recorded is simply absent (the drawer shows its empty state)", str(EMPTY) not in m)
check("another computer's rows stay with it",
      [i["name"] for i in m[str(OTHER)]["live"]] == ["Only On PC08"] and "Only On PC08" not in live)

n = live.get("Notepad++", {})
check("installed: green 'Installed' pill, no reason",
      (n.get("pill"), n.get("tone"), n.get("reason")) == ("Installed", "good", ""))
check("installed: Windows' version, then the method", n.get("meta") == f"8.7.1 {DOT} Installer (EXE)")
check("a Chocolatey wrapper shows the package that owns it",
      live.get("VLC", {}).get("meta") == f"3.0.23 {DOT} Chocolatey {DOT} vlc.install")
check("an MSI", live.get("7-Zip", {}).get("meta") == f"24.08.00.0 {DOT} Windows Installer (MSI)")
check("a per-user EXE says so",
      live.get("Visual Studio Code", {}).get("meta") == f"1.94.2 {DOT} Installer (EXE) {DOT} for one user account")
v = live.get("Visual Studio Code", {})
check("...and, since 2026-10-07, that it is Installed like any other - per-user EXEs are uninstalled too",
      (v.get("pill"), v.get("reason")) == ("Installed", ""))
third = {i["name"]: i for i in m.get(str(THIRD), {}).get("live", [])}
check("an EXE whose uninstaller is outside Program Files: can't uninstall, saying why",
      (third.get("Helper", {}).get("pill"), third.get("Helper", {}).get("reason"))
      == ("Can't uninstall automatically", "Its uninstaller is outside Program Files, so AppEase never runs it."))
check("an EXE with no uninstall recorded: can't uninstall, in the approved 'unavailable' words",
      (third.get("Bare", {}).get("pill"), third.get("Bare", {}).get("reason"))
      == ("Can't uninstall automatically", "It can't be uninstalled automatically."))
check("an EXE inside Program Files (x86) stays Installed",
      (third.get("WinSCP", {}).get("pill"), third.get("WinSCP", {}).get("reason")) == ("Installed", ""))
a = live.get("Audacity", {})
check("no silent uninstall: amber 'Can't uninstall automatically', with its reason",
      (a.get("pill"), a.get("tone"), a.get("reason"))
      == ("Can't uninstall automatically", "warn", "This program has no quiet uninstall option."))
d = live.get("Discord", {})
check("unavailable with a reason, and the library version when Windows gave none",
      d.get("reason") == "The install added more than one program, so AppEase can't tell which to remove."
      and d.get("meta") == f"1.0.9 {DOT} Installer (EXE)")
p = live.get("Python 3.14", {})
check("protected: blue 'Protected', with its reason",
      (p.get("pill"), p.get("tone"), p.get("reason"))
      == ("Protected", "info", "AppEase's own agent runs on this, so it is never removed."))
check("no version anywhere: the method alone", live.get("aardvark tool", {}).get("meta") == "Installer (EXE)")
g = live.get("Gone App", {})
check("software deleted from the library: 'no longer in the Software Library'",
      g.get("library_note") == "no longer in the Software Library")
check("...and only then", all(i["library_note"] == "" for name, i in live.items() if name != "Gone App"))
check("no tone the drawer cannot draw at 4.5:1 (neutral is 4.39:1 in light mode)",
      {i["tone"] for i in live.values()} <= {"good", "warn", "info"})

APPROVED = {
    "no_new_entry": "Windows listed nothing new after the install.",
    "several_new_entries": "The install added more than one program, so AppEase can't tell which to remove.",
    "name_mismatch": "What Windows listed doesn't match this software's name.",
    "no_silent_uninstall": "This program has no quiet uninstall option.",
    "user_hive_unreadable": "It was installed for a user account AppEase couldn't check.",
    "product_code_unreadable": "AppEase couldn't read the installer's product ID.",
    "no_registry_entry": "Chocolatey installed it without adding it to Windows' program list.",
    "protected": "AppEase's own agent runs on this, so it is never removed.",
    "choco_shared_dependency": "Another Chocolatey package also needs it.",
    "choco_records_unreadable": "Chocolatey's records for it couldn't be read.",
    "replaced": "Replaced by a later install.",
    "removed": "Removed by AppEase.",
    "removed_folder_left": "Removed; its folder was left behind.",
    "already_removed": "It was already gone.",
    "removed_restart_needed": "Removed; restart needed to finish.",
}
check("every reason code reads exactly as approved, and no code is missing",
      {code: entry["label"] for code, entry in appmod.INSTALL_RECORD_REASONS.items()} == APPROVED)


# ---------------------------------------------------------------------------
print("2. The order, and history")
# ---------------------------------------------------------------------------
names = [i["name"] for i in pc.get("live", [])]
check("live rows: Installed, then Can't uninstall automatically, then Protected, alphabetical within each",
      names == ["7-Zip", "aardvark tool", "Gone App", "Notepad++", "Visual Studio Code", "VLC",
                "Audacity", "Discord", "Python 3.14"])
check("history holds only the replaced and removed rows", sorted(history) == ["7-Zip old", "Zoom"])
check("...and live holds none of them", not ({"7-Zip old", "Zoom"} & set(live)))
check("history is newest first", [i["name"] for i in pc["history"]] == ["Zoom", "7-Zip old"])
z, o = history.get("Zoom", {}), history.get("7-Zip old", {})
check("a replaced row: what happened, then when (Manila time)",
      o.get("history") == f"Replaced by a later install {DOT} " + appmod.manila_time("2026-10-01 07:14:00"))
check("a removed row says Removed", z.get("history", "").startswith(f"Removed {DOT} "))
check("history rows carry no pill", all("pill" not in i and "tone" not in i for i in pc["history"]))
check("a computer with no history has N = 0", m[str(OTHER)]["history"] == [])


# ---------------------------------------------------------------------------
print("3. The page, as an admin")
# ---------------------------------------------------------------------------
NASTY = 'Evil "quoted" </script><script>alert(\'x\')</script> & co'
installation(OTHER, "exe:nasty", NASTY, None, "1", "1")

conn = sqlite3.connect(database.DB_PATH)
admin = conn.execute("SELECT id, username FROM admins LIMIT 1").fetchone()
conn.execute("UPDATE admins SET must_change_password = 0")
conn.commit()
conn.close()


def client_as(role):
    client = appmod.app.test_client()
    with client.session_transaction() as sess:
        sess["admin_id"] = admin[0]
        sess["admin_role"] = role
        sess["admin_username"] = admin[1]
    return client


res = client_as("admin").get("/computers")
page = res.get_data(as_text=True)
check("the Lab Computers page renders for an admin", res.status_code == 200)
block = re.search(r'<script type="application/json" id="comp-installed-data">(.*?)</script>', page, re.S)
check("the map is in its own JSON block", block is not None)
try:
    data = json.loads(block.group(1)) if block else {}
except ValueError:
    data = {}
check("...holding exactly what the server built", data == the_map())
check("a name with a closing script tag and quotes comes back exactly as stored",
      any(i["name"] == NASTY for i in data.get(str(OTHER), {}).get("live", [])))
check("...and never appears raw in the page", "<script>alert(" not in page and "</script><script>" not in page)

# "Asset record" also heads a section of the EDIT drawer, earlier in the
# page; the details drawer's one is the first after this section begins.
start = page.find("Installed by AppEase")
end = page.find("Asset record", start)
section = page[start:end] if start != -1 and end != -1 else ""
check("the section sits after Hardware and before the asset record",
      -1 < page.find("Reported by the agent on this computer.") < start < end)
check("its subtitle", "Recorded by the agent when AppEase installed each one." in section)
check("the always-shown note on older installs",
      "Only software installed after this computer&#39;s agent started keeping records is listed." in section
      or "Only software installed after this computer's agent started keeping records is listed." in section)
check("the empty state's words", "Nothing installed by AppEase yet." in section)
history_tag = re.search(r'<details id="comp-details-history"[^>]*>', section)
check("the history toggle is a native <details>, hidden until the script finds history",
      history_tag is not None and re.search(r'class="[^"]*\bhidden\b', history_tag.group(0)) is not None)
check("...and closed: no 'open' attribute", history_tag is not None and " open" not in history_tag.group(0))
check("its label counts what is inside", 'Show history (<span id="comp-details-history-count">0</span>)' in section)

# The toggle must LOOK clickable. Basecoat makes every `details summary`
# inline-flex and hides the native marker, so without a marker of our own it
# reads as plain text (seen on the VM, both themes). A Python suite cannot
# see pixels; it checks the summary carries the marker class and that the
# stylesheet draws a marker closed and turns it open. Seeing it is a
# click-test.
summary_tag = re.search(r"<summary[^>]*>Show history", section)
check("the history summary carries the disclosure marker",
      summary_tag is not None and "disclosure-summary" in summary_tag.group(0))
css = open(os.path.join(ROOT, "server", "static", "css", "app.css"), encoding="utf-8").read()
marker = re.search(r"\.disclosure-summary::before\s*\{([^}]*)\}", css)
check("the marker is a triangle drawn before the summary text",
      marker is not None and '"\\25B8"' in marker.group(1))
check("...kept out of screen readers where that is supported",
      marker is not None and '"\\25B8" / ""' in marker.group(1))
check("...and turned to point down while the history is open",
      re.search(r"details\[open\]\s*>\s*\.disclosure-summary::before\s*\{[^}]*rotate\(90deg\)", css) is not None)
check("...without setting display on the summary itself (the .hidden cascade trap)",
      re.search(r"\.disclosure-summary\s*\{", css) is None)
check("no class that needs a Tailwind rebuild: only utilities already compiled",
      all(c in open(os.path.join(ROOT, "server", "static", "css", "tailwind.css"), encoding="utf-8").read()
          for c in ("space-y-2", "shrink-0", "min-w-0", "cursor-pointer", "select-none")))

script = page[page.find("function renderInstalled("):page.find("function renderEquipment(")]
check("the render function survives rendering (no comment swallowed it)",
      "function renderInstalled(" in page and all(
          "//" not in line.split("function renderInstalled(")[0]
          for line in page.splitlines() if "function renderInstalled(" in line))
check("...and is called when the drawer opens, with the row's computer id",
      "renderInstalled(String(event.detail.id || ''));" in page)
check("every value goes in with textContent, never innerHTML",
      "textContent" in script and "innerHTML" not in script and "insertAdjacentHTML" not in script)


# ---------------------------------------------------------------------------
print("4. Faculty")
# ---------------------------------------------------------------------------
res = client_as("faculty").get("/computers")
check("faculty cannot open Lab Computers at all - the same as Hardware",
      res.status_code in (301, 302) and "/problem-reports" in res.headers.get("Location", ""))
check("...so nothing of the list reaches them", "comp-installed-data" not in res.get_data(as_text=True))


print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for label in failed:
    print("  -", label)
print("=" * 56)
os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

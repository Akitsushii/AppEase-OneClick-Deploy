"""Per-user (HKCU) EXE uninstalls, the server's half: a row is removable
when it names its account's SID and every recorded command runs an .exe in
that account's AppData\\Local or \\Roaming; only an agent saying
uninstall:exe-user gets the job. Strings are the VM's per-user capture.
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


work = tempfile.mkdtemp(prefix="appease_unexeuser_")
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

if not hasattr(appmod, "is_user_appdata_path"):
    print("COULD NOT RUN - app.py predates per-user EXE uninstalls.")
    sys.exit(3)
appmod.app.config["TESTING"] = True
UNINSTALL = appmod.JOB_ACTION_UNINSTALL
HKLM, HKCU = "HKEY_LOCAL_MACHINE", "HKEY_CURRENT_USER"
SID = "S-1-5-21-4221116625-3915973442-1598432410-1001"

# The capture's strings.
VSCODE_US = '"C:\\Users\\LabUser\\AppData\\Local\\Programs\\Microsoft VS Code\\unins000.exe"'
VSCODE_QUS = VSCODE_US + " /SILENT"
OBSIDIAN_US = '"C:\\Users\\LabUser\\AppData\\Local\\Programs\\Obsidian\\Uninstall Obsidian.exe" /currentuser'
OBSIDIAN_QUS = OBSIDIAN_US + " /S"
DISCORD_US = "C:\\Users\\LabUser\\AppData\\Local\\Discord\\Update.exe --uninstall"
DISCORD_QUS = DISCORD_US + " -s"
NPP_QUS = '"C:\\Program Files\\Notepad++\\uninstall.exe" /S'
WINSCP_QUS = '"C:\\Program Files (x86)\\WinSCP\\unins000.exe" /SILENT'

# ---------------------------------------------------------------------------
print("1. Where a per-user uninstaller may be")
# ---------------------------------------------------------------------------
inside = appmod.is_user_appdata_path
INSIDE = [
    "C:\\Users\\LabUser\\AppData\\Local\\Programs\\Microsoft VS Code\\unins000.exe",
    "C:\\Users\\LabUser\\AppData\\Local\\Programs\\Obsidian\\Uninstall Obsidian.exe",
    "C:\\Users\\LabUser\\AppData\\Roaming\\Tool\\uninstall.exe",
    "c:\\users\\labuser\\appdata\\local\\app\\U.EXE",
    "C:\\Users\\LabUser\\AppData\\Local\\App\\Sub\\..\\uninstall.exe",
]
OUTSIDE = [
    "C:\\Program Files\\Notepad++\\uninstall.exe",                   # machine-wide's place, not this
    "C:\\ProgramData\\Helper\\uninstall.exe",
    "C:\\Users\\LabUser\\AppData\\LocalLow\\App\\uninstall.exe",     # neither Local nor Roaming
    "C:\\Users\\LabUser\\Desktop\\uninstall.exe",
    "C:\\Users\\LabUser\\Downloads\\uninstall.exe",
    "C:\\Users\\LabUser\\AppData\\uninstall.exe",                    # AppData itself
    "C:\\Users\\LabUser\\AppData\\Local\\..\\..\\Public\\x.exe",     # ".." out of AppData
    "C:\\Users\\Public\\uninstall.exe",
    "C:\\Users\\LABUSE~1\\AppData\\Local\\App\\uninstall.exe",       # 8.3: the server cannot resolve it
    "%LOCALAPPDATA%\\App\\uninstall.exe",                            # a variable
    "C:/Users/LabUser/AppData/Local/App/uninstall.exe",
    "\\\\server\\share\\Users\\x\\AppData\\Local\\a.exe",
    "Users\\LabUser\\AppData\\Local\\App\\uninstall.exe",
    "C:\\Users\\LabUser\\AppData\\Local\\App\\uninstall.cmd",
    "C:\\Users\\LabUser\\AppData\\Local\\",
    "",
    None,
]
for path in INSIDE:
    check(f"{path!r} is inside an account's AppData", inside(path) is True)
for path in OUTSIDE:
    check(f"{path!r} is not", not inside(path))

check("Squirrel is not singled out (2026-10-07: AppEase tries it)", not hasattr(appmod, "is_squirrel_command"))

# ---------------------------------------------------------------------------
print("2. The rule, on the capture's rows")
# ---------------------------------------------------------------------------


def row(hive, quiet=None, uninstall=None, sid=SID, state="installed", reason=None, switch=None):
    return {"method": "exe", "reg_hive": hive, "reg_user_sid": sid if hive in (HKCU, "HKCU") else None,
            "quiet_uninstall_string": quiet, "uninstall_string": uninstall, "software_name": "X",
            "display_name": "X", "state": state, "state_reason": reason, "library_switch": switch}


problem = appmod.exe_uninstall_problem
RULE = [
    ("VS Code (User) - Inno, per user", row(HKCU, VSCODE_QUS, VSCODE_US), None),
    ("Obsidian - per-user NSIS, vendor quiet string", row(HKCU, OBSIDIAN_QUS, OBSIDIAN_US), None),
    ("Discord - Squirrel, inside its account's AppData", row(HKCU, DISCORD_QUS, DISCORD_US), None),
    ("Squirrel in the UninstallString only", row(HKCU, None, DISCORD_US), None),
    ("per user, written short", row("HKCU", VSCODE_QUS), None),
    ("per user, but no SID recorded", row(HKCU, VSCODE_QUS, sid=None), "unavailable"),
    ("per user, a SID that is not one", row(HKCU, VSCODE_QUS, sid="LabUser"), "unavailable"),
    ("per user, nothing recorded", row(HKCU), "unavailable"),
    ("per user, uninstaller in Program Files", row(HKCU, NPP_QUS), "unsafe_user_location"),
    ("per user, uninstaller in ProgramData", row(HKCU, '"C:\\ProgramData\\Helper\\uninstall.exe" /S'),
     "unsafe_user_location"),
    ("per user, quiet string in AppData but uninstall string on the Desktop",
     row(HKCU, VSCODE_QUS, '"C:\\Users\\LabUser\\Desktop\\uninstall.exe"'), "unsafe_user_location"),
    ("per user, re-opened by a library switch", row(HKCU, None, '"C:\\Users\\LabUser\\AppData\\Local\\Programs'
                                                      '\\Tool\\uninstall.exe"', state="unavailable",
                                                      reason="no_silent_uninstall", switch="/S"), None),
    ("per user, no quiet uninstall and no switch",
     row(HKCU, None, '"C:\\Users\\LabUser\\AppData\\Local\\Programs\\Tool\\uninstall.exe"', state="unavailable",
         reason="no_silent_uninstall"), "unavailable"),
    # Machine-wide: exactly as before.
    ("HKLM WinSCP, Program Files", row(HKLM, WINSCP_QUS), None),
    ("HKLM, uninstaller in an account's AppData", row(HKLM, VSCODE_QUS), "unsafe_location"),
    ("HKLM, Squirrel-looking but in Program Files", row(HKLM, '"C:\\Program Files\\App\\Update.exe" --uninstall'),
     None),
]
for label, data, expected in RULE:
    check(f"{label}: {expected}", problem(data) == expected)

check("the server's SID shape is the agent's (agent.py USER_SID)",
      appmod.USER_SID.pattern == re.search(r'^USER_SID = re\.compile\(r"(.*)"\)$',
                                           open(os.path.join(ROOT, "agent", "agent.py"), encoding="utf-8").read(),
                                           re.M).group(1))
check("per_user is no longer a reason, and nor is squirrel",
      "per_user" not in appmod.UNINSTALL_SKIP_REASONS and "squirrel" not in appmod.UNINSTALL_SKIP_REASONS)
check("the new words are the approved ones",
      appmod.UNINSTALL_SKIP_REASONS["unsafe_user_location"]
      == "Its uninstaller is outside its account's AppData folder, so AppEase never runs it.")
check("Program Files words unchanged",
      appmod.UNINSTALL_SKIP_REASONS["unsafe_location"]
      == "Its uninstaller is outside Program Files, so AppEase never runs it.")

# ---------------------------------------------------------------------------
print("3. Picker, create, retry and drawer")
# ---------------------------------------------------------------------------


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


def one(sql, args=()):
    found = rows(sql, args)
    return found[0] if found else None


write("UPDATE admins SET password_hash = ?, must_change_password = 0 WHERE username = 'admin'",
      (generate_password_hash("Str0ng!Passw0rd"),))
client = appmod.app.test_client()
client.post("/login", data={"username": "admin", "password": "Str0ng!Passw0rd"})
NOW = "uninstall:choco uninstall:exe uninstall:exe-user uninstall:msi"
PHASE3 = "uninstall:choco uninstall:exe uninstall:msi"


def computer(host, caps):
    return write("INSERT INTO computers (hostname, status, last_seen, capabilities) "
                 "VALUES (?, 'online', DATETIME('now'), ?)", (host, caps))


def exe(pc, name, hive, key, quiet=None, uninstall=None, view="Registry64", sid=SID):
    return write(
        "INSERT INTO installations (computer_id, software_name, software_version, method, record_key, reg_hive, "
        "reg_user_sid, reg_view, reg_key_name, display_name, uninstall_string, quiet_uninstall_string, state) "
        "VALUES (?, ?, '1.0', 'exe', ?, ?, ?, ?, ?, ?, ?, ?, 'installed')",
        (pc, name, f"exe:{pc}:{key}", hive, sid if hive == HKCU else None, view, key, name, uninstall, quiet))


PC = computer("LAB1-PC01", NOW)
OLD = computer("LAB1-PC02", PHASE3)
VSCODE_KEY = "{771FD6B0-FA20-440A-A002-3B3BAC16DC50}_is1"
OBSIDIAN_KEY = "bd400747-f0c1-5638-a859-982036102edf"
R = {
    "vscode": (exe(PC, "Visual Studio Code", HKCU, VSCODE_KEY, VSCODE_QUS, VSCODE_US), None),
    "obsidian": (exe(PC, "Obsidian", HKCU, OBSIDIAN_KEY, OBSIDIAN_QUS, OBSIDIAN_US), None),
    "discord": (exe(PC, "Discord", HKCU, "Discord", DISCORD_QUS, DISCORD_US), None),
    "in_program_files": (exe(PC, "Odd", HKCU, "Odd", NPP_QUS), "unsafe_user_location"),
    "winscp": (exe(PC, "WinSCP", HKLM, "winscp3_is1", WINSCP_QUS, view="Registry32"), None),
    "on_3a_agent": (exe(OLD, "Visual Studio Code", HKCU, VSCODE_KEY, VSCODE_QUS, VSCODE_US), "no_capability"),
    "hklm_on_3a_agent": (exe(OLD, "WinSCP", HKLM, "winscp3_is1", WINSCP_QUS, view="Registry32"), None),
}
data = client.get("/api/uninstall-candidates").get_json()
listed = {r["id"]: r for r in data["installations"]}
for name, (row_id, expected) in R.items():
    got = listed.get(row_id, {})
    check(f"{name}: the picker says {expected}", got.get("reason") == expected
          and got.get("reason_text") == (appmod.UNINSTALL_SKIP_REASONS[expected] if expected else ""))

ids = [row_id for row_id, _ in R.values()]
reply = client.post("/uninstall-batch", data={"installation_ids": [str(i) for i in ids]},
                    headers={"X-Requested-With": "XMLHttpRequest"}).get_json()
decided = {item["installation_id"]: item["reason"] for item in reply["skipped"]}
decided.update({item["installation_id"]: None for item in reply["created"]})
check(f"/uninstall-batch decides exactly what the picker said ({decided})",
      all(decided.get(row_id, "missing") == expected for row_id, expected in R.values()))

conn = database.get_db()
try:
    drawer = appmod.installations_by_computer(conn)
finally:
    conn.close()
by_name = {i["name"]: i for i in drawer[str(PC)]["live"]}
check("the drawer: VS Code (User) is Installed, still marked for one user account",
      (by_name.get("Visual Studio Code", {}).get("pill"), by_name.get("Visual Studio Code", {}).get("reason"))
      == ("Installed", "") and "for one user account" in by_name.get("Visual Studio Code", {}).get("meta", ""))
check("the drawer: Discord (Squirrel) is Installed too",
      (by_name.get("Discord", {}).get("pill"), by_name.get("Discord", {}).get("reason")) == ("Installed", ""))
check("the drawer: a per-user uninstaller in Program Files says why, in the picker's words",
      (by_name.get("Odd", {}).get("pill"), by_name.get("Odd", {}).get("reason"))
      == ("Can't uninstall automatically", appmod.UNINSTALL_SKIP_REASONS["unsafe_user_location"]))

comps = {c["hostname"]: c for c in data["computers"]}
check("a PC whose agent removes everything, per-user EXEs included: no note",
      comps["LAB1-PC01"]["methods_note"] == "")
check("one whose agent predates per-user EXEs: its note says machine-wide EXE",
      comps["LAB1-PC02"]["methods_note"] == "MSI, Chocolatey and machine-wide EXE only")
NOTES = {
    "uninstall:exe": "machine-wide EXE only",
    "uninstall:exe uninstall:exe-user": "EXE only",
    "uninstall:msi uninstall:exe": "MSI and machine-wide EXE only",
    "uninstall:msi uninstall:choco": "MSI and Chocolatey only",
    "uninstall:msi": "MSI only",
    "uninstall:exe-user": "",                       # nothing removable without uninstall:exe itself
    "": "",
    None: "",
}
for caps, note in NOTES.items():
    check(f"{caps!r}: the picker's note is {note!r}", appmod.uninstall_methods_note(caps) == note)
LABELS = {
    NOW: "Windows Installer (MSI), Chocolatey, Installer (EXE)",
    PHASE3: "Windows Installer (MSI), Chocolatey, Installer (EXE, machine-wide only)",
    "uninstall:exe-user": "None",
    None: "",
}
for caps, label in LABELS.items():
    got = appmod.uninstall_support_label(caps)
    check(f"{caps!r}: Computer Details' Uninstall support reads {got!r}", got == label)

# ---------------------------------------------------------------------------
print("4. The job the agent gets")
# ---------------------------------------------------------------------------


def poll(host, caps):
    return client.post("/api/agent/poll", json={"computer_hostname": host, "capabilities": caps}).get_json()["job"]


jobs = {r["installation_id"]: r["id"] for r in rows("SELECT * FROM jobs WHERE action = ?", (UNINSTALL,))}
sent = []
for _ in range(5):
    got = poll("LAB1-PC01", NOW.split())
    if not got:
        break
    sent.append(got)
by_job = {job["id"]: job for job in sent}
check("a per-user job names the hive, the SID and the key - no view",
      by_job.get(jobs.get(R["vscode"][0])) == {"id": jobs.get(R["vscode"][0]), "action": "uninstall",
                                               "method": "exe", "hive": "HKCU", "sid": SID,
                                               "key_name": VSCODE_KEY})
check("...never a command or a path", all(not any("\\" in str(v) for v in job.values()) for job in sent))
check("a machine-wide job is exactly as before",
      by_job.get(jobs.get(R["winscp"][0])) == {"id": jobs.get(R["winscp"][0]), "action": "uninstall",
                                               "method": "exe", "view": "Registry32", "key_name": "winscp3_is1"})

# Queued for a current agent, then the PC's agent turned out older.
write("DELETE FROM jobs")
client.post("/uninstall-batch", data={"installation_ids": [str(R["obsidian"][0])]},
            headers={"X-Requested-With": "XMLHttpRequest"})
got = poll("LAB1-PC01", PHASE3.split())
job = one("SELECT status, result_reason FROM jobs")
check("an agent without uninstall:exe-user never gets a per-user job: agent_cannot_uninstall",
      got is None and (job["status"], job["result_reason"]) == ("cancelled", "agent_cannot_uninstall"))

# Queued while safe, then the record lost its SID: the last look cancels it.
write("DELETE FROM jobs")
poll("LAB1-PC01", NOW.split())
client.post("/uninstall-batch", data={"installation_ids": [str(R["obsidian"][0])]},
            headers={"X-Requested-With": "XMLHttpRequest"})
write("UPDATE installations SET reg_user_sid = NULL WHERE id = ?", (R["obsidian"][0],))
got = poll("LAB1-PC01", NOW.split())
job = one("SELECT status, result_reason FROM jobs")
check("a per-user row that lost its SID after it was queued is never sent",
      got is None and (job["status"], job["result_reason"]) == ("cancelled", "no_longer_installed"))
write("UPDATE installations SET reg_user_sid = ? WHERE id = ?", (SID, R["obsidian"][0]))

# ---------------------------------------------------------------------------
print("5. Retry passes the same rule")
# ---------------------------------------------------------------------------
write("DELETE FROM jobs")


def failed_uninstall(installation_id):
    return write("INSERT INTO jobs (computer_hostname, software_id, status, action, installation_id, software_name) "
                 "VALUES ('LAB1-PC01', 0, 'failed', ?, ?, 'X')", (UNINSTALL, installation_id))


location = client.post(f"/jobs/{failed_uninstall(R['vscode'][0])}/retry").headers.get("Location", "")
check("a failed per-user uninstall that still passes is retried",
      "Retry+job+created" in location and len(rows("SELECT id FROM jobs WHERE status = 'pending'")) == 1)
write("DELETE FROM jobs")
location = client.post(f"/jobs/{failed_uninstall(R['discord'][0])}/retry").headers.get("Location", "")
check("...a Squirrel one (Discord) is retried too",
      "Retry+job+created" in location and len(rows("SELECT id FROM jobs WHERE status = 'pending'")) == 1)
write("DELETE FROM jobs")
location = client.post(f"/jobs/{failed_uninstall(R['in_program_files'][0])}/retry").headers.get("Location", "")
check("...one whose uninstaller is outside its AppData is not, saying so",
      "outside+its+account" in location and not rows("SELECT id FROM jobs WHERE status = 'pending'"))

print()
print(f"{passed} passed, {len(failed)} failed")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

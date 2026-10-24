"""The server's EXE uninstall rules: a machine-wide row is removable only
when every recorded command runs an .exe inside Program Files; what the
server cannot resolve is refused; and the job names only the view and the
key, never a command or a path. Strings are the VM capture's.
"""
import os
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


work = tempfile.mkdtemp(prefix="appease_unexe_")
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

if not hasattr(appmod, "exe_uninstall_problem"):
    print("COULD NOT RUN - app.py predates the EXE uninstall rules.")
    sys.exit(3)
appmod.app.config["TESTING"] = True
UNINSTALL = appmod.JOB_ACTION_UNINSTALL
HKLM, HKCU = "HKEY_LOCAL_MACHINE", "HKEY_CURRENT_USER"

# The capture's strings. Long paths throughout: the PROGRA~2 below is the
# only short name, and the batch file made it on purpose.
WINSCP_US = '"C:\\Program Files (x86)\\WinSCP\\unins000.exe"'
WINSCP_QUS = '"C:\\Program Files (x86)\\WinSCP\\unins000.exe" /SILENT'
NPP_US = '"C:\\Program Files\\Notepad++\\uninstall.exe"'
NPP_QUS = '"C:\\Program Files\\Notepad++\\uninstall.exe" /S'
VC86_QUS = ('"C:\\ProgramData\\Package Cache\\{4373d0b5-4457-4a80-bad9-029de8df097b}\\VC_redist.x86.exe" '
            '/uninstall /quiet')
GREENSHOT_QUS = '"C:\\Users\\LabUser\\AppData\\Local\\Programs\\Greenshot\\unins000.exe" /SILENT'
PYTHON_QUS = ('"C:\\Users\\LabUser\\AppData\\Local\\Package Cache\\{7116a5c9-3a0e-4d9c-aa8b-c6f909dc9278}\\'
              'python-3.14.6-amd64.exe" /uninstall /quiet')
VSCODE_QUS = '"C:\\Users\\LabUser\\AppData\\Local\\Programs\\Microsoft VS Code\\unins000.exe" /SILENT'
DISCORD_QUS = "C:\\Users\\LabUser\\AppData\\Local\\Discord\\Update.exe --uninstall -s"
WINSCP_SHORT = "C:\\PROGRA~2\\WinSCP\\unins000.exe"

# ---------------------------------------------------------------------------
print("1. Which program a command runs")
# ---------------------------------------------------------------------------
program = appmod.uninstall_program_path
PROGRAMS = [
    (WINSCP_QUS, "C:\\Program Files (x86)\\WinSCP\\unins000.exe"),
    (WINSCP_US, "C:\\Program Files (x86)\\WinSCP\\unins000.exe"),
    (NPP_QUS, "C:\\Program Files\\Notepad++\\uninstall.exe"),
    (DISCORD_QUS, "C:\\Users\\LabUser\\AppData\\Local\\Discord\\Update.exe"),
    ("C:\\Program Files\\App\\uninstall.exe /S", "C:\\Program Files\\App\\uninstall.exe"),
    ("C:\\Program Files\\App\\UNINSTALL.EXE", "C:\\Program Files\\App\\UNINSTALL.EXE"),
    ("  " + NPP_QUS + "  ", "C:\\Program Files\\Notepad++\\uninstall.exe"),
    ("MsiExec.exe /X{23170F69-40C1-2702-2408-000001000000}", "MsiExec.exe"),
    ('"C:\\Program Files\\App\\uninstall.exe', None),
    ('""', None),
    ("", None),
    (None, None),
    ("C:\\Program Files\\App\\uninstall /S", None),
]
for command, expected in PROGRAMS:
    check(f"{command!r} runs {expected!r}", program(command) == expected)
check("an unquoted path is never cut at its first space (C:\\Program.exe)",
      program("C:\\Program Files\\App\\uninstall.exe /S") != "C:\\Program")

inside = appmod.is_program_files_path
INSIDE = ["C:\\Program Files (x86)\\WinSCP\\unins000.exe", "C:\\Program Files\\Notepad++\\uninstall.exe",
          "c:\\program files\\app\\U.EXE", "C:\\Program Files\\App\\Sub\\..\\uninstall.exe",
          "C:\\Program Files\\\\App\\uninstall.exe"]
OUTSIDE = [
    WINSCP_SHORT,                                                    # 8.3: the server cannot resolve it
    "%ProgramFiles%\\App\\uninstall.exe",                            # a variable
    "C:\\Program Files\\NOTEPA~1\\uninstall.exe",                     # 8.3, even inside Program Files
    "C:\\Program Files\\%App%\\uninstall.exe",                        # a variable inside Program Files
    "C:\\ProgramData\\Package Cache\\{4373d0b5}\\VC_redist.x86.exe",  # ProgramData: any user can write
    "C:\\ProgramData\\Helper\\uninstall.exe",
    "C:\\Users\\LabUser\\AppData\\Local\\Discord\\Update.exe",
    "C:\\Users\\Public\\uninstall.exe",
    "C:\\Program Files\\..\\Users\\LabUser\\x.exe",                  # ".." out of Program Files
    "C:\\Program Files (x86)evil\\x.exe",
    "C:\\Program FilesX\\x.exe",
    "C:\\Program Files.exe",
    "\\\\server\\share\\uninstall.exe",
    "\\\\?\\C:\\Program Files\\App\\uninstall.exe",
    "Program Files\\App\\uninstall.exe",
    "MsiExec.exe",
    "C:/Program Files/App/uninstall.exe",
    "C:\\Program Files/App/uninstall.exe",                           # mixed: only the "/" rule refuses it
    "D:\\Program Files\\App\\uninstall.exe",                         # another drive: not this rule's to judge
    "C:\\Program Files\\App\\uninstall.bat",
    "C:\\Program Files\\App\\uninstall.exe.",
    "C:\\Program Files\\",
    "",
    None,
]
for path in INSIDE:
    check(f"{path!r} is inside Program Files", inside(path) is True)
for path in OUTSIDE:
    check(f"{path!r} is not", not inside(path))

# ---------------------------------------------------------------------------
print("2. The rule, on every row the VM and the lab records hold")
# ---------------------------------------------------------------------------


def row(hive, quiet=None, uninstall=None, method="exe", name="X", state="installed", reason=None):
    return {"method": method, "reg_hive": hive, "quiet_uninstall_string": quiet, "uninstall_string": uninstall,
            "software_name": name, "display_name": name, "state": state, "state_reason": reason,
            "reg_user_sid": "S-1-5-21-1-1001" if hive in (HKCU, "HKCU") else None}


problem = appmod.exe_uninstall_problem
RULE = [
    ("WinSCP (Inno, HKLM, 32-bit view)", row(HKLM, WINSCP_QUS, WINSCP_US), None),
    ("Notepad++ (vendor quiet string, HKLM)", row(HKLM, NPP_QUS, NPP_US), None),
    ("an Inno entry recorded with only its UninstallString", row(HKLM, None, WINSCP_US), None),
    ("HKLM written short", row("HKLM", NPP_QUS, NPP_US), None),
    ("VC++ x86 (Package Cache)", row(HKLM, VC86_QUS, VC86_QUS.replace(" /quiet", "")), "unsafe_location"),
    ("a quiet string inside Program Files, but an uninstall string outside it",
     row(HKLM, NPP_QUS, '"C:\\ProgramData\\Helper\\uninstall.exe"'), "unsafe_location"),
    ("the uninstaller as an 8.3 short name", row(HKLM, None, '"' + WINSCP_SHORT + '"'), "unsafe_location"),
    ("an uninstaller that is not an .exe", row(HKLM, '"C:\\Program Files\\App\\remove.cmd"'), "unsafe_location"),
    ("Greenshot (Inno, per user)", row(HKCU, GREENSHOT_QUS), None),
    ("Python (per-user Package Cache)", row(HKCU, PYTHON_QUS), None),
    ("VS Code (User)", row(HKCU, VSCODE_QUS), None),
    ("Discord (Squirrel, per user)", row(HKCU, DISCORD_QUS), None),
    ("per user written short, uninstaller in Program Files", row("HKCU", NPP_QUS), "unsafe_user_location"),
    ("per user, uninstaller inside Program Files", row(HKCU, NPP_QUS), "unsafe_user_location"),
    ("HKLM with nothing recorded", row(HKLM), "unavailable"),
    ("HKLM with blank strings", row(HKLM, "  ", ""), "unavailable"),
    ("no hive recorded", row(None, NPP_QUS), "unavailable"),
    ("a hive that is neither", row("HKEY_USERS", NPP_QUS), "unavailable"),
    ("an MSI row", row(None, method="msi"), None),
    ("a Chocolatey row", row(None, method="choco"), None),
]
for label, data, expected in RULE:
    check(f"{label}: {expected}", problem(data) == expected)

# ---------------------------------------------------------------------------
print("3. Picker, create, retry and drawer give one answer")
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
ALL = "uninstall:choco uninstall:exe uninstall:msi"
PHASE2 = "uninstall:choco uninstall:msi"


def computer(host, caps):
    return write("INSERT INTO computers (hostname, status, last_seen, capabilities) "
                 "VALUES (?, 'online', DATETIME('now'), ?)", (host, caps))


def exe(pc, name, hive, view, key, quiet=None, uninstall=None):
    return write(
        "INSERT INTO installations (computer_id, software_name, software_version, method, record_key, reg_hive, "
        "reg_user_sid, reg_view, reg_key_name, display_name, uninstall_string, quiet_uninstall_string, state) "
        "VALUES (?, ?, '1.0', 'exe', ?, ?, ?, ?, ?, ?, ?, ?, 'installed')",
        (pc, name, f"exe:{pc}:{key}", hive, "S-1-5-21-1-1001" if hive == HKCU else None, view, key, name,
         uninstall, quiet))


PC = computer("LAB1-PC01", ALL)
OLD = computer("LAB1-PC02", PHASE2)
R = {
    "winscp": (exe(PC, "WinSCP", HKLM, "Registry32", "winscp3_is1", WINSCP_QUS, WINSCP_US), None),
    "notepadpp": (exe(PC, "Notepad++", HKLM, "Registry64", "Notepad++", NPP_QUS, NPP_US), None),
    # This PC's agent says uninstall:exe but not uninstall:exe-user.
    "greenshot": (exe(PC, "Greenshot", HKCU, None, "Greenshot_is1", GREENSHOT_QUS), "no_capability"),
    "discord": (exe(PC, "Discord", HKCU, None, "Discord", DISCORD_QUS), "no_capability"),
    "helper": (exe(PC, "Helper", HKLM, "Registry64", "Helper", '"C:\\ProgramData\\Helper\\uninstall.exe" /S'),
               "unsafe_location"),
    "vc_redist": (exe(PC, "Microsoft Visual C++ v14 Redistributable (x86)", HKLM, "Registry32",
                      "{4373d0b5-4457-4a80-bad9-029de8df097b}", VC86_QUS), "shared_runtime"),
    "bare": (exe(PC, "Bare", HKLM, "Registry64", "Bare"), "unavailable"),
    "on_phase2_pc": (exe(OLD, "Notepad++", HKLM, "Registry64", "Notepad++", NPP_QUS, NPP_US), "no_capability"),
}
data = client.get("/api/uninstall-candidates").get_json()
listed = {r["id"]: r for r in data["installations"]}
for name, (row_id, expected) in R.items():
    got = listed.get(row_id, {})
    check(f"{name}: the picker says {expected}", got.get("reason") == expected
          and got.get("reason_text") == (appmod.UNINSTALL_SKIP_REASONS[expected] if expected else ""))
check("the picker's words are the approved ones",
      appmod.UNINSTALL_SKIP_REASONS["unsafe_location"]
      == "Its uninstaller is outside Program Files, so AppEase never runs it.")
check("there is no 'EXE uninstall not available yet' any more", "exe" not in appmod.UNINSTALL_SKIP_REASONS)
comps = {c["hostname"]: c for c in data["computers"]}
check("a PC whose agent predates per-user EXEs says so (2026-10-07)",
      comps["LAB1-PC01"]["methods_note"] == "MSI, Chocolatey and machine-wide EXE only")
check("a Phase 2 agent says MSI and Chocolatey only", comps["LAB1-PC02"]["methods_note"] == "MSI and Chocolatey only")

ids = [row_id for row_id, _ in R.values()]
reply = client.post("/uninstall-batch", data={"installation_ids": [str(i) for i in ids]},
                    headers={"X-Requested-With": "XMLHttpRequest"}).get_json()
decided = {item["installation_id"]: item["reason"] for item in reply["skipped"]}
decided.update({item["installation_id"]: None for item in reply["created"]})
check(f"/uninstall-batch decides exactly what the picker said ({decided})",
      all(decided.get(row_id, "missing") == expected for row_id, expected in R.values()))
check("...queuing the two that pass, and nothing else",
      sorted(item["installation_id"] for item in reply["created"]) == sorted([R["winscp"][0], R["notepadpp"][0]]))

conn = database.get_db()
try:
    drawer = appmod.installations_by_computer(conn)
finally:
    conn.close()
by_name = {i["name"]: i for i in drawer[str(PC)]["live"]}
DRAWER = {"winscp": ("WinSCP", "Installed", ""),
          "notepadpp": ("Notepad++", "Installed", ""),
          "greenshot": ("Greenshot", "Installed", ""),
          "discord": ("Discord", "Installed", ""),
          "helper": ("Helper", "Can't uninstall automatically", appmod.UNINSTALL_SKIP_REASONS["unsafe_location"]),
          "vc_redist": ("Microsoft Visual C++ v14 Redistributable (x86)", "Protected", appmod.SHARED_RUNTIME_REASON),
          "bare": ("Bare", "Can't uninstall automatically", "It can't be uninstalled automatically.")}
for key, (name, pill, reason) in DRAWER.items():
    item = by_name.get(name, {})
    check(f"the drawer agrees on {key}: {pill}", (item.get("pill"), item.get("reason")) == (pill, reason))
check("nothing recorded: the picker and the drawer give the same approved words",
      listed.get(R["bare"][0], {}).get("reason_text") == by_name.get("Bare", {}).get("reason")
      == "It can't be uninstalled automatically.")

# ---------------------------------------------------------------------------
print("4. The job the agent gets")
# ---------------------------------------------------------------------------


def poll(host, caps):
    return client.post("/api/agent/poll", json={"computer_hostname": host, "capabilities": caps}).get_json()["job"]


jobs = {r["installation_id"]: r["id"] for r in rows("SELECT * FROM jobs WHERE action = ?", (UNINSTALL,))}
write("UPDATE jobs SET created_at = DATETIME('now', '-2 seconds') WHERE id = ?", (jobs[R["winscp"][0]],))
got = poll("LAB1-PC01", ALL.split())
check("an EXE uninstall sends exactly its id, action, method, view and key name",
      got == {"id": jobs[R["winscp"][0]], "action": "uninstall", "method": "exe", "view": "Registry32",
              "key_name": "winscp3_is1"})
check("...never a command or a path", got is not None and not any("\\" in str(v) or "exe\"" in str(v)
                                                                    for v in got.values()))
# Queued while safe, then the record changed: the last look cancels it.
write("UPDATE installations SET quiet_uninstall_string = ? WHERE id = ?",
      ('"C:\\ProgramData\\Evil\\x.exe" /S', R["notepadpp"][0]))
got = poll("LAB1-PC01", ALL.split())
job = one("SELECT status, result_reason FROM jobs WHERE id = ?", (jobs[R["notepadpp"][0]],))
check("an EXE row that turned unsafe after it was queued is never sent", got is None)
check("...it is cancelled with no_longer_installed", (job["status"], job["result_reason"])
      == ("cancelled", "no_longer_installed"))
write("UPDATE installations SET quiet_uninstall_string = ? WHERE id = ?", (NPP_QUS, R["notepadpp"][0]))

for label, column, value in (("its view is not one Windows has", "reg_view", "Registry128"),
                             ("its key name is blank", "reg_key_name", ""),
                             ("it became per-user", "reg_hive", HKCU)):
    write("DELETE FROM jobs")
    reply = client.post("/uninstall-batch", data={"installation_ids": [str(R["notepadpp"][0])]},
                        headers={"X-Requested-With": "XMLHttpRequest"}).get_json()
    write(f"UPDATE installations SET {column} = ? WHERE id = ?", (value, R["notepadpp"][0]))
    got = poll("LAB1-PC01", ALL.split())
    job = rows("SELECT status, result_reason FROM jobs")
    check(f"queued, then {label}: cancelled, nothing sent",
          reply["ok"] and got is None and [(j["status"], j["result_reason"]) for j in job]
          == [("cancelled", "no_longer_installed")])
    write("UPDATE installations SET reg_hive = ?, reg_view = 'Registry64', reg_key_name = 'Notepad++' WHERE id = ?",
          (HKLM, R["notepadpp"][0]))

write("DELETE FROM jobs")
client.post("/uninstall-batch", data={"installation_ids": [str(R["notepadpp"][0])]},
            headers={"X-Requested-With": "XMLHttpRequest"})
got = poll("LAB1-PC01", PHASE2.split())
job = one("SELECT status, result_reason FROM jobs")
check("an agent that does not advertise uninstall:exe never gets one: agent_cannot_uninstall",
      got is None and (job["status"], job["result_reason"]) == ("cancelled", "agent_cannot_uninstall"))

# ---------------------------------------------------------------------------
print("5. Retry passes the same rule")
# ---------------------------------------------------------------------------
write("DELETE FROM jobs")
# Section 4 last polled as an agent without uninstall:exe.
poll("LAB1-PC01", ALL.split())


def failed_uninstall(installation_id):
    return write("INSERT INTO jobs (computer_hostname, software_id, status, action, installation_id, software_name) "
                 "VALUES ('LAB1-PC01', 0, 'failed', ?, ?, 'X')", (UNINSTALL, installation_id))


location = client.post(f"/jobs/{failed_uninstall(R['winscp'][0])}/retry").headers.get("Location", "")
check("a failed EXE uninstall that still passes is retried",
      "Retry+job+created" in location and len(rows("SELECT id FROM jobs WHERE status = 'pending'")) == 1)
write("DELETE FROM jobs")
write("UPDATE installations SET reg_hive = ?, reg_user_sid = 'S-1-5-21-1-1001' WHERE id = ?", (HKCU, R["winscp"][0]))
location = client.post(f"/jobs/{failed_uninstall(R['winscp'][0])}/retry").headers.get("Location", "")
check("...but not once the row reads per-user with its uninstaller in Program Files, saying so",
      "outside+its+account" in location and not rows("SELECT id FROM jobs WHERE status = 'pending'"))
write("UPDATE installations SET reg_hive = ?, reg_user_sid = NULL WHERE id = ?", (HKLM, R["winscp"][0]))
write("DELETE FROM jobs")
bulk = client.post("/jobs/bulk/retry", data={"job_ids": [str(failed_uninstall(R["helper"][0]))]})
check("bulk retry refuses an EXE outside Program Files",
      "None+of+the+selected" in bulk.headers.get("Location", "")
      and not rows("SELECT id FROM jobs WHERE status = 'pending'"))
write("DELETE FROM jobs")

# ---------------------------------------------------------------------------
print("6. The agent's two refusals")
# ---------------------------------------------------------------------------
WORDS = {"uninstaller_unsafe_location": "Its uninstaller is outside Program Files, so nothing was run",
         "no_quiet_uninstall": "No quiet uninstall is listed for it any more, so nothing was run"}


def report(action, status, exit_code, reason):
    job_id = write("INSERT INTO jobs (computer_hostname, software_id, status, action, installation_id, started_at) "
                   "VALUES ('LAB1-PC01', 0, 'in_progress', ?, ?, CURRENT_TIMESTAMP)",
                   (action, R["winscp"][0] if action else None))
    body = {"computer_hostname": "LAB1-PC01", "status": status, "reason": reason}
    if exit_code is not None:
        body["exit_code"] = exit_code
    client.post(f"/api/agent/jobs/{job_id}/report", json=body)
    found = one("SELECT status, exit_code, result_reason FROM jobs WHERE id = ?", (job_id,))
    write("DELETE FROM jobs")
    return found["status"], found["exit_code"], found["result_reason"]


for reason, words in WORDS.items():
    check(f"{reason} reads as approved", appmod.job_result("failed", None, reason, UNINSTALL)["label"] == words)
    check(f"{reason} is kept on a failed uninstall with no exit code",
          report(UNINSTALL, "failed", None, reason) == ("failed", None, reason))
    check(f"...sent with an exit code, which nothing ran to give: the reason is kept, the code dropped",
          report(UNINSTALL, "failed", 1, reason) == ("failed", None, reason))
    check(f"...nor on an install job", report(None, "failed", None, reason)[2] is None)
    check(f"...nor on a completed one", report(UNINSTALL, "completed", None, reason)[2] is None)
row = one("SELECT state FROM installations WHERE id = ?", (R["winscp"][0],))
check("a refusal leaves the row installed", row["state"] == "installed")

print()
print(f"{passed} passed, {len(failed)} failed")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

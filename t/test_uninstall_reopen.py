"""Re-opening EXE rows with the library's Uninstall switch: one recorded as
no_silent_uninstall becomes uninstallable only when machine-wide, with an
allowed switch and its program inside Program Files - all decided in
exe_uninstall_problem(), so every view agrees. VLC strings from the VM.
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


work = tempfile.mkdtemp(prefix="appease_reopen_")
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

if not hasattr(appmod, "installation_library_switch"):
    print("COULD NOT RUN - app.py predates re-opening rows with the Uninstall switch.")
    sys.exit(3)
appmod.app.config["TESTING"] = True
UNINSTALL = appmod.JOB_ACTION_UNINSTALL
HKLM, HKCU = "HKEY_LOCAL_MACHINE", "HKEY_CURRENT_USER"
VLC_US = '"C:\\Program Files\\VideoLAN\\VLC\\uninstall.exe"'
NPP_QUS = '"C:\\Program Files\\Notepad++\\uninstall.exe" /S'
REOPENED = "Uninstalls with the saved switch "

# ---------------------------------------------------------------------------
print("1. The rule")
# ---------------------------------------------------------------------------
problem = appmod.exe_uninstall_problem


def row(state="unavailable", reason="no_silent_uninstall", switch="/S", hive=HKLM, uninstall=VLC_US, quiet=None,
        method="exe"):
    return {"method": method, "state": state, "state_reason": reason, "library_switch": switch, "reg_hive": hive,
            "uninstall_string": uninstall, "quiet_uninstall_string": quiet, "software_name": "VLC",
            "display_name": "VLC media player", "reg_user_sid": "S-1-5-21-1-1001" if hive == HKCU else None}


RULE = [
    ("VLC: HKLM, no quiet uninstall, library switch /S, uninstaller in Program Files", row(), None),
    ("the library switch written in another case", row(switch="/s"), None),
    ("a library switch with junk around an allowed one", row(switch="/S & del x"), None),
    ("no switch in the library", row(switch=None), "unavailable"),
    ("a switch that cleans to nothing", row(switch="/D=C:\\x"), "unavailable"),
    ("a blank switch", row(switch="  "), "unavailable"),
    ("per-user with a switch, but its uninstaller in Program Files", row(hive=HKCU), "unsafe_user_location"),
    ("its uninstaller in ProgramData", row(uninstall='"C:\\ProgramData\\Evil\\uninstall.exe"'), "unsafe_location"),
    ("its uninstaller as an 8.3 name", row(uninstall='"C:\\PROGRA~1\\VideoLAN\\VLC\\uninstall.exe"'),
     "unsafe_location"),
    ("no uninstall string recorded", row(uninstall=None), "unavailable"),
    ("unavailable for another reason", row(reason="several_new_entries"), "unavailable"),
    ("protected", row(reason="protected"), "unavailable"),
    ("replaced", row(state="replaced", reason="replaced"), "unavailable"),
    ("removed", row(state="removed", reason="removed"), "unavailable"),
    ("an installed EXE: the switch changes nothing", row(state="installed", reason=None, switch=None, quiet=NPP_QUS),
     None),
    ("an installed per-user EXE, uninstaller in Program Files", row(state="installed", reason=None, hive=HKCU),
     "unsafe_user_location"),
    ("an unavailable MSI row: not this rule's", row(method="msi"), None),
]
for label, data, expected in RULE:
    check(f"{label}: {expected}", problem(data) == expected)
check("a row read without the library's switch never re-opens",
      problem({k: v for k, v in row().items() if k != "library_switch"}) == "unavailable")

# ---------------------------------------------------------------------------
print("2. Picker, create, drawer and retry agree")
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


def computer(host, caps=ALL):
    return write("INSERT INTO computers (hostname, status, last_seen, capabilities) VALUES (?, 'online', DATETIME('now'), ?)",
                 (host, caps))


def software(name, switch):
    return write("INSERT INTO software (name, version, type, install_command, uninstall_switch) "
                 "VALUES (?, '3.0.24', 'offline', '\"uploads/vlc.exe\" /S', ?)", (name, switch))


KEYS = iter(range(1, 1000))


def inst(pc, name, software_id, state="unavailable", reason="no_silent_uninstall", hive=HKLM, uninstall=VLC_US,
         quiet=None):
    n = next(KEYS)
    return write(
        "INSERT INTO installations (computer_id, software_id, software_name, software_version, method, record_key, "
        "reg_hive, reg_user_sid, reg_view, reg_key_name, display_name, uninstall_string, quiet_uninstall_string, "
        "install_location, state, state_reason) "
        "VALUES (?, ?, ?, '3.0.24', 'exe', ?, ?, ?, 'Registry64', ?, ?, ?, ?, 'C:\\Program Files\\VideoLAN\\VLC', ?, ?)",
        (pc, software_id, name, f"exe:{n}", hive, "S-1-5-21-1-1001" if hive == HKCU else None,
         "VLC media player" if n == 1 else f"Key{n}", name, uninstall, quiet, state, reason))


PC = computer("LAB1-PC01")
OLD = computer("LAB1-PC02", "uninstall:choco uninstall:msi")
SW = software("VLC media player", "/S")
SW_NONE = software("VLC no switch", None)
SW_GONE = software("VLC deleted", "/S")
SW_TAMPERED = software("VLC tampered", "/S & del x")
SW_JUNK = software("VLC junk", "/D=C:\\x")
write("DELETE FROM software WHERE id = ?", (SW_GONE,))
R = {
    "vlc": (inst(PC, "VLC media player", SW), None),
    "no_switch": (inst(PC, "VLC no switch", SW_NONE), "unavailable"),
    "entry_deleted": (inst(PC, "VLC deleted", SW_GONE), "unavailable"),
    "no_entry": (inst(PC, "VLC no entry", None), "unavailable"),
    "per_user": (inst(PC, "VLC per user", SW, hive=HKCU), "unsafe_user_location"),
    "programdata": (inst(PC, "VLC programdata", SW, uninstall='"C:\\ProgramData\\Evil\\uninstall.exe"'),
                    "unsafe_location"),
    "tampered": (inst(PC, "VLC tampered", SW_TAMPERED), None),
    "junk": (inst(PC, "VLC junk", SW_JUNK), "unavailable"),
    "other_reason": (inst(PC, "VLC other", SW, reason="several_new_entries"), "unavailable"),
    "phase2_pc": (inst(OLD, "VLC media player", SW), "no_capability"),
    "npp_installed": (inst(PC, "Notepad++", None, state="installed", reason=None, uninstall=None, quiet=NPP_QUS), None),
}
data = client.get("/api/uninstall-candidates").get_json()
listed = {r["id"]: r for r in data["installations"]}
for name, (row_id, expected) in R.items():
    got = listed.get(row_id, {})
    check(f"{name}: the picker says {expected}", got.get("reason") == expected
          and got.get("reason_text") == (appmod.UNINSTALL_SKIP_REASONS[expected] if expected else ""))
check("nothing in the table changed to re-open them",
      [r["state"] for r in rows("SELECT state FROM installations WHERE id IN (?, ?)", (R["vlc"][0], R["tampered"][0]))]
      == ["unavailable", "unavailable"])

reply = client.post("/uninstall-batch", data={"installation_ids": [str(i) for i, _ in R.values()]},
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
items = {i["name"]: i for i in drawer[str(PC)]["live"]}
v = items.get("VLC media player", {})
check("the drawer shows a re-opened row as Installed",
      (v.get("pill"), v.get("tone")) == ("Installed", "good"))
check("...with one muted line naming the saved switch", v.get("reason") == "Uninstalls with the saved switch /S")
check("...the switch as stored after cleaning, not as typed", items.get("VLC tampered", {}).get("reason")
      == "Uninstalls with the saved switch /S")
n = items.get("VLC no switch", {})
check("no switch: still 'Can't uninstall automatically', with its recorded reason",
      (n.get("pill"), n.get("reason")) == ("Can't uninstall automatically", "This program has no quiet uninstall option."))
check("per-user with a switch, uninstaller in Program Files: the picker's words",
      items.get("VLC per user", {}).get("reason") == appmod.UNINSTALL_SKIP_REASONS["unsafe_user_location"]
      and items.get("VLC per user", {}).get("pill") == "Can't uninstall automatically")
check("outside Program Files with a switch: the picker's words",
      items.get("VLC programdata", {}).get("reason") == appmod.UNINSTALL_SKIP_REASONS["unsafe_location"])
disagree = []
for name, (row_id, expected) in R.items():
    if name == "phase2_pc":
        continue
    item = next((i for i in drawer[str(PC)]["live"] if i["name"] == one(
        "SELECT software_name FROM installations WHERE id = ?", (row_id,))["software_name"]), None)
    if item is None or (item["pill"] == "Installed") != (expected is None):
        disagree.append(name)
check(f"for every row, the drawer says Installed exactly when the picker would queue it ({disagree})", not disagree)

# ---------------------------------------------------------------------------
print("3. The job the agent gets")
# ---------------------------------------------------------------------------


def poll(host, caps=ALL):
    return client.post("/api/agent/poll", json={"computer_hostname": host, "capabilities": caps.split()}).get_json()["job"]


jobs = {r["installation_id"]: r["id"] for r in rows("SELECT * FROM jobs WHERE action = ?", (UNINSTALL,))}
ORDER = [R["vlc"][0], R["tampered"][0], R["npp_installed"][0]]
for i, installation_id in enumerate(ORDER):
    write("UPDATE jobs SET created_at = DATETIME('now', ?) WHERE id = ?", (f"-{10 - i} seconds", jobs[installation_id]))
got = [poll("LAB1-PC01") for _ in ORDER]
check("a re-opened row's job carries its switch, beside the view and the key",
      got[0] == {"id": jobs[R["vlc"][0]], "action": "uninstall", "method": "exe", "view": "Registry64",
                 "key_name": "VLC media player", "switch": "/S"})
check("...cleaned again at send time", got[1] is not None and got[1].get("switch") == "/S")
check("an installed EXE's job carries no switch - the registry's own uninstall is used",
      got[2] is not None and "switch" not in got[2])
write("DELETE FROM jobs")

for label, change in (("its switch cleans to nothing by then", ("UPDATE software SET uninstall_switch = '/D=x' WHERE id = ?", (SW,))),
                      ("its library entry was deleted by then", ("DELETE FROM software WHERE id = ?", (SW,)))):
    write("INSERT OR IGNORE INTO software (id, name, version, type, install_command, uninstall_switch) "
          "VALUES (?, 'VLC media player', '3.0.24', 'offline', 'x', '/S')", (SW,))
    write("UPDATE software SET uninstall_switch = '/S' WHERE id = ?", (SW,))
    reply = client.post("/uninstall-batch", data={"installation_ids": [str(R["vlc"][0])]},
                        headers={"X-Requested-With": "XMLHttpRequest"}).get_json()
    write(*change)
    got = poll("LAB1-PC01")
    job = one("SELECT status, result_reason FROM jobs")
    check(f"queued, then {label}: cancelled, nothing sent",
          reply["ok"] and got is None and (job["status"], job["result_reason"]) == ("cancelled", "no_longer_installed"))
    write("DELETE FROM jobs")
write("INSERT OR IGNORE INTO software (id, name, version, type, install_command, uninstall_switch) "
      "VALUES (?, 'VLC media player', '3.0.24', 'offline', 'x', '/S')", (SW,))
write("UPDATE software SET uninstall_switch = '/S' WHERE id = ?", (SW,))

# ---------------------------------------------------------------------------
print("4. Retry")
# ---------------------------------------------------------------------------


def failed_uninstall(installation_id):
    return write("INSERT INTO jobs (computer_hostname, software_id, status, action, installation_id, software_name) "
                 "VALUES ('LAB1-PC01', 0, 'failed', ?, ?, 'VLC')", (UNINSTALL, installation_id))


retryable = appmod.job_retryable
base = {"status": "failed", "action": UNINSTALL, "software_deleted": 0}
check("an uninstall of a row that is still live - installed or unavailable - may be retried",
      retryable(dict(base, installation_state="installed")) and retryable(dict(base, installation_state="unavailable")))
check("...but not once it is replaced or removed",
      not retryable(dict(base, installation_state="replaced")) and not retryable(dict(base, installation_state="removed")))
location = client.post(f"/jobs/{failed_uninstall(R['vlc'][0])}/retry").headers.get("Location", "")
check("a failed uninstall of a re-opened row is retried",
      "Retry+job+created" in location and len(rows("SELECT id FROM jobs WHERE status = 'pending'")) == 1)
write("DELETE FROM jobs")
location = client.post(f"/jobs/{failed_uninstall(R['no_switch'][0])}/retry").headers.get("Location", "")
check("...but one whose library has no switch is refused, saying why",
      "It+can't+be+uninstalled+automatically" in location.replace("%27", "'")
      and not rows("SELECT id FROM jobs WHERE status = 'pending'"))
write("DELETE FROM jobs")

# ---------------------------------------------------------------------------
print("5. Removed when the agent reports it gone")
# ---------------------------------------------------------------------------
client.post("/uninstall-batch", data={"installation_ids": [str(R["vlc"][0])]},
            headers={"X-Requested-With": "XMLHttpRequest"})
got = poll("LAB1-PC01")
client.post(f"/api/agent/jobs/{got['id']}/report", json={"computer_hostname": "LAB1-PC01", "status": "completed",
                                                         "exit_code": 0, "reason": None})
after = one("SELECT state, state_reason, uninstall_job_id FROM installations WHERE id = ?", (R["vlc"][0],))
check("a re-opened row the agent removed ends removed, by that job",
      (after["state"], after["state_reason"], after["uninstall_job_id"]) == ("removed", "removed", got["id"]))
client.post("/uninstall-batch", data={"installation_ids": [str(R["tampered"][0])]},
            headers={"X-Requested-With": "XMLHttpRequest"})
got = poll("LAB1-PC01")
client.post(f"/api/agent/jobs/{got['id']}/report", json={"computer_hostname": "LAB1-PC01", "status": "failed",
                                                         "exit_code": 0, "reason": "still_listed"})
after = one("SELECT state, state_reason FROM installations WHERE id = ?", (R["tampered"][0],))
check("...and one still listed afterwards is left exactly as it was",
      (after["state"], after["state_reason"]) == ("unavailable", "no_silent_uninstall"))

print()
print(f"{passed} passed, {len(failed)} failed")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

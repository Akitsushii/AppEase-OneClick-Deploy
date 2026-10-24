"""Closing a per-user app before its uninstaller runs: only a process whose
.exe is inside the app's own folder, running as the agent's account, and
never the agent; the uninstall goes ahead whatever closing did.
"""
import logging
import ntpath
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time

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


work = tempfile.mkdtemp(prefix="appease_closeapp_")
os.makedirs(os.path.join(work, "agent"))
shutil.copy(os.path.join(ROOT, "agent", "agent.py"), os.path.join(work, "agent"))
sys.path.insert(0, os.path.join(work, "agent"))
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))

import agent  # noqa: E402

if not hasattr(agent, "close_app_processes"):
    print("COULD NOT RUN - agent.py predates closing an app before its uninstall.")
    sys.exit(3)

SID = "S-1-5-21-4221116625-3915973442-1598432410-1001"
OTHER = "S-1-5-21-4221116625-3915973442-1598432410-1002"
LOCAL = "C:\\Users\\LabUser\\AppData\\Local"
ROAMING = "C:\\Users\\LabUser\\AppData\\Roaming"
ROOTS = [LOCAL, ROAMING]
VSCODE = LOCAL + "\\Programs\\Microsoft VS Code"
DISCORD = LOCAL + "\\Discord"


class Captured(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())

    def take(self):
        lines, self.lines = self.lines, []
        return lines


logs = Captured()
agent.logger.addHandler(logs)

# ---------------------------------------------------------------------------
print("1. Which folder is the app's")
# ---------------------------------------------------------------------------
folder = agent.app_folder
FOLDERS = [
    ("VS Code (User)", VSCODE + "\\unins000.exe", VSCODE),
    ("Obsidian", LOCAL + "\\Programs\\Obsidian\\Uninstall Obsidian.exe", LOCAL + "\\Programs\\Obsidian"),
    ("Discord (Squirrel)", DISCORD + "\\Update.exe", DISCORD),
    ("a Roaming app", ROAMING + "\\Tool\\bin\\uninstall.exe", ROAMING + "\\Tool\\bin"),
    ("written in another case", "c:\\users\\labuser\\appdata\\local\\Discord\\Update.exe", "c:\\users\\labuser\\appdata\\local\\Discord"),
    ("an uninstaller straight in AppData\\Local", LOCAL + "\\uninstall.exe", None),
    ("straight in AppData\\Local\\Programs, which many apps share", LOCAL + "\\Programs\\uninstall.exe", None),
    ("straight in AppData\\Local\\Microsoft", LOCAL + "\\Microsoft\\uninstall.exe", None),
    ("straight in AppData\\Local\\Temp", LOCAL + "\\Temp\\uninstall.exe", None),
    ("straight in AppData\\Local\\Packages", LOCAL + "\\Packages\\uninstall.exe", None),
    ("straight in AppData\\Roaming\\Microsoft", ROAMING + "\\Microsoft\\uninstall.exe", None),
    ("outside the account's AppData", "C:\\Program Files\\Tool\\uninstall.exe", None),
    ("a folder whose name only starts like AppData\\Local", "C:\\Users\\LabUser\\AppData\\LocalX\\Tool\\u.exe", None),
    ("nothing", None, None),
]
for label, program, expected in FOLDERS:
    check(f"{label}: {expected}", folder(program, ROOTS) == expected)
check("one app inside a shared folder is that app's own", folder(LOCAL + "\\Microsoft\\Teams\\Update.exe", ROOTS)
      == LOCAL + "\\Microsoft\\Teams")

# ---------------------------------------------------------------------------
print("2. Which processes are that app's")
# ---------------------------------------------------------------------------
AGENT_PYTHON = "C:\\Users\\LabUser\\AppData\\Local\\Programs\\Microsoft VS Code\\python.exe"
PROCESSES = [
    (101, VSCODE + "\\Code.exe", SID),
    (102, VSCODE + "\\Code.exe", SID.lower()),                          # the SID compared without case
    (103, VSCODE + "\\07f806f999\\helper.exe", SID),                    # inside, one folder down
    (104, VSCODE.upper() + "\\CODE.EXE", SID),                          # the path compared without case
    (201, VSCODE + "\\Code.exe", OTHER),                                # another account's copy
    (202, VSCODE + "\\Code.exe", None),                                 # an owner it could not read
    (203, VSCODE + " Insiders\\Code.exe", SID),                         # only the name starts the same
    (204, LOCAL + "\\Programs\\Obsidian\\Obsidian.exe", SID),           # another app
    (205, "C:\\Windows\\explorer.exe", SID),
    (206, None, SID),
    (207, AGENT_PYTHON, SID),                                           # the agent's own Python
    (208, VSCODE + "\\Code.exe", SID),                                  # the agent's own process id
]
chosen = agent.processes_to_close(PROCESSES, VSCODE, SID, never=[AGENT_PYTHON], own_pids={208})
check(f"only this account's copies running from the folder ({[pid for pid, _ in chosen]})",
      [pid for pid, _ in chosen] == [101, 102, 103, 104])
check("no folder: nothing", agent.processes_to_close(PROCESSES, None, SID) == [])
check("no account known: nothing", agent.processes_to_close(PROCESSES, VSCODE, None) == [])
check("the agent's own interpreter is on the never-close list",
      ntpath.normcase(sys.executable) in agent.agent_own_programs())

# ---------------------------------------------------------------------------
print("3. Closing, and saying what was closed")
# ---------------------------------------------------------------------------
ended = []
# The last two rows stand for the agent itself only when passed as such, as
# section 2 does; close_app_processes() excludes the real agent's own.
RUNNING = PROCESSES[:-2]


def listing():
    return RUNNING


def end_ok(pid, image):
    ended.append(pid)
    return agent.CLOSE_CLOSED


class Levels(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append((record.levelname, record.getMessage()))

    def take(self):
        records, self.records = self.records, []
        return records


levels = Levels()
agent.logger.addHandler(levels)
closed, gone, left = agent.close_app_processes(VSCODE, SID, listing, end_ok)
check("each one chosen was ended", sorted(ended) == [101, 102, 103, 104] and len(closed) == 4 and left == [])
lines = logs.take()
check(f"agent.log names what was closed ({lines})",
      any("closed 4 running process(es)" in l and "Code.exe (101)" in l and VSCODE in l for l in lines))
levels.take()

# What VS Code did in jobs 360 and 368: its main process ended, and its
# helpers went with it before they could be reached.
electron = {101: agent.CLOSE_CLOSED, 102: agent.CLOSE_GONE, 103: agent.CLOSE_GONE, 104: agent.CLOSE_IMAGE_CHANGED}
closed, gone, left = agent.close_app_processes(VSCODE, SID, listing, lambda pid, image: electron[pid])
records = levels.take()
logs.take()
check(f"main process closed, helpers already gone: one closed, three gone, none left ({closed}, {gone}, {left})",
      [pid for pid, _ in closed] == [101] and [pid for pid, _ in gone] == [102, 103, 104] and left == [])
check(f"...and no warning at all ({records})", all(level == "INFO" for level, _ in records))
check("...the log says they had already exited, and why each",
      any("3 more had already exited" in m and "helper.exe (103, already gone)" in m
          and "(104, image changed)" in m for _, m in records))

stuck = {101: agent.CLOSE_CLOSED, 102: agent.CLOSE_DENIED, 103: agent.CLOSE_STILL_RUNNING, 104: "Windows error 6"}
closed, gone, left = agent.close_app_processes(VSCODE, SID, listing, lambda pid, image: stuck[pid])
records = levels.take()
logs.take()
warnings = [m for level, m in records if level == "WARNING"]
check(f"only what is still running is a warning, each with its reason ({warnings})",
      [(pid, why) for pid, _, why in left] == [(102, "access denied"), (103, "still running"), (104, "Windows error 6")]
      and len(warnings) == 1 and "3 process(es)" in warnings[0] and "still running" in warnings[0]
      and "Code.exe (102, access denied)" in warnings[0] and "(104, Windows error 6)" in warnings[0])

closed, gone, left = agent.close_app_processes(LOCAL + "\\Programs\\Tool", SID, listing, end_ok)
records = levels.take()
logs.take()
check(f"nothing running from the app's folder: one INFO line says so ({records})",
      (closed, gone, left) == ([], [], [])
      and records == [("INFO", "Before uninstalling: nothing was running from " + LOCAL + "\\Programs\\Tool.")])
agent.logger.removeHandler(levels)


def boom(*args):
    raise OSError("access denied")


closed, gone, left = agent.close_app_processes(VSCODE, SID, boom, end_ok)
lines = logs.take()
check("the list cannot be taken: nothing closed, said so, and no exception",
      (closed, gone, left) == ([], [], []) and any("could not list running programs" in l for l in lines))
closed, gone, left = agent.close_app_processes(VSCODE, SID, listing, boom)
lines = logs.take()
check("ending one raises: still running, with the error as its reason, no exception",
      len(left) == 4 and closed == [] and all("access denied" in why for _, _, why in left))
asked = []
closed, gone, left = agent.close_app_processes(None, SID, lambda: asked.append(1) or RUNNING, end_ok)
lines = logs.take()
check("no app folder: the running programs are not even listed, and the log says why",
      (closed, gone, left, asked) == ([], [], [], []) and any("nothing was closed" in l for l in lines))

# ---------------------------------------------------------------------------
print("4. Where it happens: per-user only, just before the uninstaller")
# ---------------------------------------------------------------------------
HKLM, HKCU = agent.HKLM_NAME, agent.HKCU_NAME
PRESENT, ABSENT = agent.SIGNAL_PRESENT, agent.SIGNAL_ABSENT


def user(key, **values):
    return agent.uninstall_entry(HKCU, SID, "Registry64", key, {k.casefold(): v for k, v in values.items()})


def machine(key, **values):
    return agent.uninstall_entry(HKLM, None, "Registry64", key, {k.casefold(): v for k, v in values.items()})


class Registry:
    def __init__(self, *entries):
        self.keys = {(e["hive"], e["key_name"].upper()) for e in entries}

    def uninstall_key(self, hive, sid, name, view):
        return PRESENT if (hive, name.upper()) in self.keys else ABSENT

    def listed_display_names(self, sids):
        return set()

    def installer_dependents(self, product_code):
        return set()


def realpath(path):
    return ntpath.normpath(path)


VSC = user("{771FD6B0-FA20-440A-A002-3B3BAC16DC50}_is1", DisplayName="Microsoft Visual Studio Code (User)",
           UninstallString='"' + VSCODE + '\\unins000.exe"', InstallLocation=VSCODE + "\\",
           **{"Inno Setup: App Path": VSCODE})
ODD = user("Odd", DisplayName="Odd", QuietUninstallString='"C:\\Program Files\\Odd\\u.exe" /S')
NPP = machine("Notepad++", DisplayName="Notepad++", QuietUninstallString='"C:\\Program Files\\Notepad++\\u.exe" /S')
order = []


def run_job(entries, job, roots, own=SID):
    registry = Registry(*entries)

    def runner(argv):
        order.append(("run", argv[0]))
        registry.keys.clear()
        return 0, False, False, 1.0

    def close(app, sid):
        order.append(("close", app, sid))
        return [], []

    snapshot = {"entries": {agent.entry_identity(e): e for e in entries}, "user_readable": True}
    kwargs = dict(registry=registry, read=lambda sid: snapshot, user_sid=lambda: own, run=runner,
                  verify=lambda t, c: ("removed", 0.0), folders=[], roots=roots, realpath=realpath,
                  isfile=lambda p: True)
    if job.get("hive") == "HKCU":
        kwargs["close"] = close
    return agent.uninstall_exe_job(job, **kwargs)


order.clear()
outcome = run_job([VSC], {"id": 1, "hive": "HKCU", "sid": SID, "key_name": VSC["key_name"]}, ROOTS)
check(f"VS Code: its folder is closed first, as this account, then the uninstaller runs ({order})",
      order == [("close", VSCODE, SID), ("run", VSCODE + "\\unins000.exe")] and outcome.ok)
order.clear()
outcome = run_job([ODD], {"id": 2, "hive": "HKCU", "sid": SID, "key_name": "Odd"}, ROOTS)
check("a refused job (uninstaller outside AppData) closes nothing and runs nothing",
      order == [] and outcome.reason == "uninstaller_unsafe_user_location")
order.clear()
outcome = run_job([VSC], {"id": 3, "hive": "HKCU", "sid": "S-1-5-21-9", "key_name": VSC["key_name"]}, ROOTS)
check("another account's job closes nothing", order == [] and outcome.reason == "identity_mismatch")

spied = []
real_close = agent.close_app_processes
agent.close_app_processes = lambda *args, **kwargs: spied.append(args) or ([], [])
try:
    order.clear()
    outcome = run_job([NPP], {"id": 4, "method": "exe", "view": "Registry64", "key_name": "Notepad++"},
                      ["C:\\Program Files"])
finally:
    agent.close_app_processes = real_close
check("a machine-wide uninstall never closes anything", spied == [] and order == [("run", "C:\\Program Files\\Notepad++\\u.exe")]
      and outcome.ok)

# ---------------------------------------------------------------------------
print("5. On Windows, for real")
# ---------------------------------------------------------------------------
if getattr(__import__("ctypes"), "WinDLL", None) is None or not os.path.isfile(
        os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32", "PING.EXE")):
    print("  (skipped: not Windows)")
else:
    app_dir = os.path.join(work, "AppData", "Local", "PingApp")
    os.makedirs(app_dir)
    copy = os.path.join(app_dir, "pingapp.exe")
    shutil.copy(os.path.join(os.environ["SystemRoot"], "System32", "PING.EXE"), copy)
    inside = subprocess.Popen([copy, "-n", "60", "127.0.0.1"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    outside = subprocess.Popen(["ping", "-n", "60", "127.0.0.1"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1.0)
        own = agent.agent_user_sid()
        found = {pid: (image, owner) for pid, image, owner in agent.running_processes()}
        check("the list has the program running from the folder, with its path",
              inside.pid in found and ntpath.normcase(found[inside.pid][0]) == ntpath.normcase(copy))
        check(f"...and this account as its owner ({found.get(inside.pid, (None, None))[1]} vs {own})",
              own is not None and (found.get(inside.pid, (None, None))[1] or "").upper() == own.upper())
        check("ending it is refused when the path no longer matches (a reused process id): image changed",
              agent.end_process(inside.pid, os.path.join(app_dir, "other.exe")) == "image changed"
              and inside.poll() is None)
        roots = [os.path.join(work, "AppData", "Local")]
        app = agent.app_folder(copy, roots)
        closed, gone, left = agent.close_app_processes(app, own)
        inside.wait(timeout=10)
        check(f"the program running from the folder was closed ({closed}, {gone}, {left})",
              [pid for pid, _ in closed] == [inside.pid] and left == [] and inside.poll() is not None)
        check("the same program running from System32 was left alone", outside.poll() is None)
        check("ending it again, once it has exited: already gone", agent.end_process(inside.pid, copy) == "already gone")
        quick = subprocess.Popen([copy, "-n", "1", "127.0.0.1"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        quick.wait(timeout=10)
        check("a process that exited by itself (its handle still held open): already gone",
              agent.end_process(quick.pid, copy) == "already gone")
        check("a process id no process has: already gone",
              agent.end_process(4294967292, os.path.join(app_dir, "nothing.exe")) == "already gone")
        # The System process: Windows refuses to open it. The path given is
        # not System's, so even an open handle would end nothing.
        check("a process Windows will not let the agent open: access denied",
              agent.end_process(4, os.path.join(app_dir, "nothing.exe")) == "access denied")
    finally:
        for p in (inside, outside):
            if p.poll() is None:
                p.kill()
                p.wait()

agent.logger.removeHandler(logs)
print()
print(f"{passed} passed, {len(failed)} failed")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

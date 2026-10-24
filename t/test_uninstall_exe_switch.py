"""The agent using the library's Uninstall switch: the registry still wins
(an Inno key or a live QuietUninstallString); only a key with neither uses
its UninstallString's program plus the switch, re-checked on the PC
against the server's allowlist. VLC as captured on the VM.
"""
import functools
import logging
import ntpath
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


work = tempfile.mkdtemp(prefix="appease_exeswitch_")
server_dir = os.path.join(work, "server")
shutil.copytree(os.path.join(ROOT, "server"), server_dir, ignore=shutil.ignore_patterns(
    "appease.db*", "__pycache__", "*.pyc", ".env", "uploads", "staging", "report_photos", "avatars", "venv"))
os.makedirs(os.path.join(work, "agent"))
shutil.copy(os.path.join(ROOT, "agent", "agent.py"), os.path.join(work, "agent"))
sys.path.insert(0, os.path.join(work, "agent"))
sys.path.insert(0, server_dir)
os.chdir(server_dir)
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))

import agent  # noqa: E402

if not hasattr(agent, "clean_uninstall_switch"):
    print("COULD NOT RUN - agent.py predates using the Uninstall switch.")
    sys.exit(3)
import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()
import app as appmod  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

HKLM = agent.HKLM_NAME
PRESENT, ABSENT = agent.SIGNAL_PRESENT, agent.SIGNAL_ABSENT
SID = "S-1-5-21-1111111111-2222222222-3333333333-1001"
FOLDERS = [agent._folder_key("C:\\Appease")]
VM_ROOTS = ["C:\\Program Files", "C:\\Program Files (x86)"]
# The captures' strings (capture3a, capture3b).
VLC_US = '"C:\\Program Files\\VideoLAN\\VLC\\uninstall.exe"'
VLC_EXE = "C:\\Program Files\\VideoLAN\\VLC\\uninstall.exe"
VLC_DIR = "C:\\Program Files\\VideoLAN\\VLC\\"
WINSCP_US = '"C:\\Program Files (x86)\\WinSCP\\unins000.exe"'
WINSCP_QUS = '"C:\\Program Files (x86)\\WinSCP\\unins000.exe" /SILENT'
NPP_US = '"C:\\Program Files\\Notepad++\\uninstall.exe"'
NPP_QUS = '"C:\\Program Files\\Notepad++\\uninstall.exe" /S'


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
agent.logger.addHandler(logs)

# ---------------------------------------------------------------------------
print("1. The same allowlist as the server, cleaned the same way")
# ---------------------------------------------------------------------------
check("the agent's allowed switches are exactly the server's, in the same order",
      agent.UNINSTALL_SWITCH_ALLOWED == appmod.UNINSTALL_SWITCH_ALLOWED)
check("...and exactly the five agreed", agent.UNINSTALL_SWITCH_ALLOWED == ("/S", "/SILENT", "/VERYSILENT", "/qn",
                                                                           "/norestart"))
WANTED = {
    "/S": "/S", "/s": "/S", "/S /S": "/S", "/s /S /s": "/S", "/silent": "/SILENT", "/VerySilent": "/VERYSILENT",
    "/QN /NORESTART": "/qn /norestart", "/norestart /S": "/norestart /S", "  /S\t/norestart  ": "/S /norestart",
    "/S & del C:\\x": "/S", "/S && cmd.exe /c calc": "/S", "cmd.exe /c del C:\\x": None, "/D=C:\\x": None,
    "/S;calc": None, '/S"': None, "/S/norestart": None, "S": None, "-S": None, "/quiet": None, "": None,
    "   ": None, None: None,
}
for text, expected in WANTED.items():
    check(f"the agent keeps {expected!r} of {text!r}", agent.clean_uninstall_switch(text) == expected)
TABLE = list(WANTED) + ["/\u017f", "/\u212aN", "/\uff33\uff29\uff2c\uff25\uff2e\uff34", "\u00a0/S\u00a0", "/S\u200b", "/SILENT /S /qn /SILENT",
                        "/NoRestart /VERYSILENT /verysilent", "/S " * 50]
differ = [t for t in TABLE if agent.clean_uninstall_switch(t) != appmod.clean_uninstall_switch(t)[0]]
check(f"the agent and the server clean every switch in the shared table alike ({len(TABLE)}; differ: {differ})",
      not differ)
check("whatever the text, the agent only ever keeps allowed switches",
      all(set((agent.clean_uninstall_switch(t) or "").split()) <= set(agent.UNINSTALL_SWITCH_ALLOWED) for t in TABLE))

# ---------------------------------------------------------------------------
print("2. Which command: the registry wins")
# ---------------------------------------------------------------------------


class FakeRegistry:
    def installer_dependents(self, product_code):
        return set()


def live(key, view, **values):
    return agent.uninstall_entry(HKLM, None, view, key, {k.casefold(): v for k, v in values.items()})


def snapshot(*entries):
    return {"entries": {agent.entry_identity(e): e for e in entries}, "user_readable": True}


SHORT = {"c:\\progra~1\\videolan\\vlc\\uninstall.exe": VLC_EXE}


def vm_realpath(path):
    """os.path.realpath on a PC laid out like the VM."""
    path = ntpath.normpath(path)
    if path.casefold() in SHORT:
        return SHORT[path.casefold()]
    if path.casefold().startswith(("c:\\program files\\", "c:\\program files (x86)\\", "c:\\programdata\\",
                                   "c:\\users\\", "c:\\windows\\")):
        return path
    raise FileNotFoundError(path)


def vlc(**values):
    base = dict(DisplayName="VLC media player", UninstallString=VLC_US, Publisher="VideoLAN",
                InstallLocation=VLC_DIR)
    base.update(values)
    return live("VLC media player", "Registry64", **{k: v for k, v in base.items() if v is not None})


VLC = vlc()
WINSCP = live("winscp3_is1", "Registry32", DisplayName="WinSCP 6.5.7", UninstallString=WINSCP_US,
              QuietUninstallString=WINSCP_QUS, **{"Inno Setup: App Path": "C:\\Program Files (x86)\\WinSCP"})
NPP = live("Notepad++", "Registry64", DisplayName="Notepad++ (64-bit x64)", UninstallString=NPP_US,
           QuietUninstallString=NPP_QUS)
JOB = {"id": 9, "action": "uninstall", "method": "exe", "view": "Registry64", "key_name": "VLC media player"}


def choose(entry, key="VLC media player", view="Registry64", roots=VM_ROOTS, **job):
    return agent.exe_uninstall_command(dict({"id": 9, "view": view, "key_name": key}, **job), snapshot(entry), roots,
                                       FakeRegistry(), FOLDERS, vm_realpath, lambda p: True)


got = choose(VLC, switch="/S")
check("VLC (no quiet uninstall, not Inno) with the switch /S: uninstall.exe /S",
      (got.argv, got.reason) == ([VLC_EXE, "/S"], None))
check("...said to be the SIHQ switch", got.kind == "SIHQ switch")
check("the switch as the library spelled it, whatever case the job sent",
      choose(VLC, switch="/s").argv == [VLC_EXE, "/S"])
check("two switches: two arguments, in order",
      choose(VLC, switch="/S /norestart").argv == [VLC_EXE, "/S", "/norestart"])
check("an 8.3 UninstallString: resolved, and run by its long name",
      choose(vlc(UninstallString='"C:\\PROGRA~1\\VideoLAN\\VLC\\uninstall.exe"'), switch="/S").argv == [VLC_EXE, "/S"])

got = choose(WINSCP, "winscp3_is1", "Registry32", switch="/S")
check("an Inno key with a switch: Inno's own silent uninstall, the switch ignored",
      (got.argv, got.kind) == (["C:\\Program Files (x86)\\WinSCP\\unins000.exe", "/VERYSILENT", "/SUPPRESSMSGBOXES",
                                "/NORESTART"], "Inno command"))
got = choose(NPP, "Notepad++", switch="/SILENT")
check("a live QuietUninstallString with a switch: the quiet string as written, the switch ignored",
      (got.argv, got.kind) == (["C:\\Program Files\\Notepad++\\uninstall.exe", "/S"], "vendor command"))
logs.take()
choose(NPP, "Notepad++", switch="/SILENT")
check("...and agent.log says the switch was not used", any("switch is not used" in l for l in logs.take()))
got = choose(live("Notepad++", "Registry64", DisplayName="Notepad++", UninstallString=NPP_US,
                  QuietUninstallString='"C:\\ProgramData\\Evil\\x.exe" /S'), "Notepad++", switch="/S")
check("a QuietUninstallString tampered into ProgramData, with a switch: refused - never a fall back to the switch",
      got[::2] == (None, "uninstaller_unsafe_location"))
got = choose(live("evil_is1", "Registry64", DisplayName="Evil", UninstallString='"C:\\ProgramData\\Evil\\unins000.exe"',
                  **{"Inno Setup: App Path": "C:\\ProgramData\\Evil"}), "evil_is1", switch="/S")
check("an Inno key outside Program Files, with a switch: refused the same way", got.reason == "uninstaller_unsafe_location")
check("VLC with no switch in the job: no_quiet_uninstall, as in 3a", choose(VLC)[::2] == (None, "no_quiet_uninstall"))
check("...or a blank one", choose(VLC, switch="")[::2] == (None, "no_quiet_uninstall"))

HOSTILE = [("cmd.exe /c del C:\\x", "nothing allowed"), ("/D=C:\\x", "an installer path option"),
           ("/S;calc", "an allowed switch glued to more"), ("& calc", "a shell operator")]
for switch, label in HOSTILE:
    logs.take()
    got = choose(VLC, switch=switch)
    check(f"a switch that cleans to nothing ({label}): no_quiet_uninstall, nothing chosen",
          got[::2] == (None, "no_quiet_uninstall"))
check("...and agent.log says why", any("nothing allowed in it" in l for l in logs.take()))
got = choose(VLC, switch="/S && cmd.exe /c del C:\\x")
check("a hostile switch around an allowed one: only /S reaches the uninstaller", got.argv == [VLC_EXE, "/S"])
for label, value in (("a list", ["/S"]), ("a number", 7), ("an object", {"s": "/S"}), ("true", True)):
    check(f"a switch that is {label}: uninstall_job_invalid", choose(VLC, switch=value)[::2]
          == (None, "uninstall_job_invalid"))

UNSAFE = [("in ProgramData", '"C:\\ProgramData\\Evil\\uninstall.exe"'),
          ("cmd.exe", "C:\\Windows\\System32\\cmd.exe /c del C:\\x"),
          ("cmd.exe copied into Program Files", '"C:\\Program Files\\VideoLAN\\VLC\\cmd.exe"'),
          ("powershell in Program Files", '"C:\\Program Files\\PowerShell\\7\\pwsh.exe"'),
          ("msiexec", "MsiExec.exe /X{23170F69-40C1-2702-2408-000001000000}"),
          ("..\\ climbing out", '"C:\\Program Files\\VideoLAN\\..\\..\\Users\\LabUser\\x.exe"'),
          ("a %variable%", "%ProgramFiles%\\VideoLAN\\VLC\\uninstall.exe"),
          ("a network path", "\\\\server\\share\\uninstall.exe"), ("a relative path", "uninstall.exe"),
          ("not an .exe", '"C:\\Program Files\\VideoLAN\\VLC\\uninstall.cmd"')]
for label, uninstall in UNSAFE:
    check(f"the switch, with an UninstallString {label}: uninstaller_unsafe_location",
          choose(vlc(UninstallString=uninstall), switch="/S")[::2] == (None, "uninstaller_unsafe_location"))
check("the switch with an UninstallString's own arguments: those are dropped, only the switch is passed",
      choose(vlc(UninstallString=VLC_US + " /keepsettings"), switch="/S").argv == [VLC_EXE, "/S"])
check("the switch, and Windows' Program Files record unreadable: nothing is safe",
      choose(VLC, roots=[], switch="/S").reason == "uninstaller_unsafe_location")
check("the switch, and the key lists no UninstallString at all: no_quiet_uninstall, nothing chosen",
      choose(vlc(UninstallString=None), switch="/S")[::2] == (None, "no_quiet_uninstall"))
own = f"Python {sys.version_info[0]}.{sys.version_info[1]}.1 (64-bit)"
check("the switch never gets past protected software",
      choose(vlc(DisplayName=own), switch="/S")[::2] == (None, "protected_software"))
check("...or a shared runtime",
      choose(vlc(DisplayName="Microsoft Visual C++ 2015-2022 Redistributable (x64) - 14.44.35211",
                 Publisher="Microsoft Corporation"), switch="/S")[::2] == (None, "shared_runtime"))
check("the key gone: handed to the already-gone check, switch or not",
      agent.exe_uninstall_command(dict(JOB, switch="/S"), snapshot(), VM_ROOTS, FakeRegistry(), FOLDERS, vm_realpath,
                                  lambda p: True).reason == agent.EXE_KEY_GONE)

# ---------------------------------------------------------------------------
print("3. VLC as captured: the uninstaller exits, Un.exe finishes the job")
# ---------------------------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class VlcRegistry:
    """VLC's key, listed until `gone_at` seconds in (None: for ever)."""

    def __init__(self, clock, gone_at):
        self.clock, self.gone_at = clock, gone_at

    def present(self):
        return self.gone_at is None or self.clock.now < self.gone_at

    def uninstall_key(self, hive, sid, name, view):
        listed = hive == HKLM and (name.upper(), view) == ("VLC MEDIA PLAYER", "Registry64")
        return PRESENT if listed and self.present() else ABSENT

    def listed_display_names(self, sids):
        return {"VLC media player"} if self.present() else set()

    def installer_dependents(self, product_code):
        return set()


class Uninstall:
    """uninstall.exe, exiting at `exits_at` (None: waits for ever on a
    prompt), and Un.exe in the same Job Object until `un_until`. Records
    what Windows lists at the moment uninstall.exe exits."""

    def __init__(self, clock, registry, exits_at, un_until, in_job=True):
        self.clock, self.registry, self.exits_at, self.un_until, self.in_job = (
            clock, registry, exits_at, un_until, in_job)
        self.returncode, self.killed, self.terminated = None, False, False
        self.listed_at_exit = None

    # The process
    def poll(self):
        if self.killed:
            self.returncode = 1
        elif self.returncode is None and self.exits_at is not None and self.clock.now >= self.exits_at:
            self.returncode = 0
            self.listed_at_exit = self.registry.present()
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True

    # The Job Object
    def active_processes(self):
        if self.terminated:
            return 0
        helper = self.un_until is None or self.clock.now < self.un_until
        return int(self.poll() is None) + int(helper)

    def terminate(self):
        self.terminated = self.killed = True

    def close(self):
        pass


def vlc_run(exits_at, un_until, gone_at, folder_gone_at="same", in_job=True, switch="/S", code_of=None):
    clock = Clock()
    registry = VlcRegistry(clock, gone_at)
    folder_gone_at = gone_at if folder_gone_at == "same" else folder_gone_at
    process = Uninstall(clock, registry, exits_at, un_until)
    started, beats = [], []

    def start(argv, stdout, shell=True):
        started.append((argv, shell))
        return process, (process if in_job else None)

    def folder(path):
        gone = folder_gone_at is not None and clock.now >= folder_gone_at
        return ABSENT if gone else PRESENT

    job = dict(JOB, switch=switch) if switch is not None else dict(JOB)
    outcome = agent.uninstall_exe(
        job, registry=registry, read=lambda sid: snapshot(VLC) if registry.present() else snapshot(),
        user_sid=lambda: SID,
        run=lambda argv: agent.run_exe_uninstaller(argv, start, clock.time, clock.sleep, lambda: beats.append(clock.now)),
        verify=lambda t, c: agent.verify_exe_removal(t, c, registry, clock.time, clock.sleep,
                                                     lambda: beats.append(clock.now), folder),
        folders=FOLDERS, roots=VM_ROOTS, realpath=vm_realpath, isfile=lambda p: True)
    return outcome, started, process, clock, beats


def result(outcome):
    return outcome.ok, outcome.exit_code, outcome.reason


for run, exits_at in (("A", 2.0), ("B", 0.5)):
    outcome, started, process, clock, beats = vlc_run(exits_at, exits_at + 2.5, exits_at + 2.5)
    check(f"run {run}: uninstall.exe /S started with no shell", started == [([VLC_EXE, "/S"], False)])
    check(f"run {run}: uninstall.exe exits at {exits_at} s with VLC still listed, as captured",
          process.listed_at_exit is True)
    check(f"run {run}: Un.exe waited for - removed, exit code 0", result(outcome) == (True, 0, None))
    check(f"run {run}: ...verified at once, the key and folder already gone", outcome.verified == 0)
    check(f"run {run}: ...done within a second of Un.exe finishing, nowhere near the limit",
          exits_at + 2.5 <= clock.now < exits_at + 3.5)
outcome, _, process, clock, _ = vlc_run(2.0, 4.5, 4.5)
check("the outcome names the SIHQ switch", outcome.outcome.startswith("removed, SIHQ switch, helpers "))
outcome, _, process, clock, beats = vlc_run(2.0, 4.5, 4.5, in_job=False)
check("no Job Object to wait on: verification's own checks still see VLC go - removed",
      result(outcome) == (True, 0, None) and process.listed_at_exit is True and outcome.verified > 0)
outcome, _, _, _, _ = vlc_run(2.0, 4.5, 4.5, folder_gone_at=9.0)
check("the key gone with Un.exe, the folder a few seconds later: the backstop sees it - removed",
      result(outcome) == (True, 0, None) and 0 < outcome.verified <= agent.EXE_FOLDER_BACKSTOP_SECONDS)
outcome, _, _, _, _ = vlc_run(2.0, 4.5, 4.5, folder_gone_at=None)
check("the folder never goes: removed_folder_left", result(outcome) == (True, 0, "removed_folder_left"))
outcome, _, _, _, _ = vlc_run(2.0, 4.5, None)
check("uninstall.exe exits 0 but VLC is never removed: still_listed", result(outcome) == (False, 0, "still_listed"))
outcome, started, process, clock, beats = vlc_run(None, None, None, switch="/SILENT")
check("a wrong switch that leaves uninstall.exe on a prompt: timed out at the 10-minute limit, the tree ended",
      result(outcome) == (False, None, "timed_out") and process.terminated
      and agent.UNINSTALL_TIMEOUT_SECONDS < clock.now <= agent.UNINSTALL_TIMEOUT_SECONDS + 2)
check("...with heartbeats all the way, and the outcome naming the SIHQ switch",
      len(beats) >= 59 and outcome.outcome == "timed_out, SIHQ switch")
outcome, started, _, _, _ = vlc_run(2.0, 4.5, 4.5, switch="cmd.exe /c del C:\\x")
check("a switch that cleans to nothing: no_quiet_uninstall, nothing started",
      result(outcome) == (False, None, "no_quiet_uninstall") and started == [])
outcome, started, _, _, _ = vlc_run(2.0, 4.5, 4.5, switch="/S && cmd.exe /c del C:\\x")
check("a hostile switch around /S: only uninstall.exe /S was started",
      result(outcome) == (True, 0, None) and started == [([VLC_EXE, "/S"], False)])

# ---------------------------------------------------------------------------
print("4. The summary line")
# ---------------------------------------------------------------------------


class Ran:
    def __init__(self):
        self.argv = []


def bound(entries, registry, ran, waited=2.5):
    def run(argv):
        ran.argv.append(argv)
        registry.gone_at = 0
        return 0, False, False, waited
    return functools.partial(
        agent.uninstall_exe, registry=registry, read=lambda sid: snapshot(*entries), user_sid=lambda: SID, run=run,
        verify=lambda t, c: agent.verify_exe_removal(t, c, registry, registry.clock.time, registry.clock.sleep,
                                                     lambda: None, lambda f: ABSENT),
        folders=FOLDERS, roots=VM_ROOTS, realpath=vm_realpath, isfile=lambda p: True)


real_exe = agent.UNINSTALLERS["exe"]
try:
    ran = Ran()
    agent.UNINSTALLERS["exe"] = bound([VLC], VlcRegistry(Clock(), None), ran)
    logs.take()
    agent.run_uninstall_job(dict(JOB, switch="/S"))
    summary = [l for l in logs.take() if l.startswith("Uninstall summary:")]
    check(f"VLC's summary line says SIHQ switch ({summary})",
          len(summary) == 1 and re.fullmatch(r"Uninstall summary: job 9, exe VLC media player \(Registry64\): removed, "
                                             r"SIHQ switch, helpers \ds; exit code 0; verification 0s\.", summary[0]))
    agent.UNINSTALLERS["exe"] = bound([NPP], VlcRegistry(Clock(), None), Ran())
    agent.run_uninstall_job({"id": 8, "method": "exe", "view": "Registry64", "key_name": "Notepad++", "switch": "/S"})
    check("a vendor command with a switch in the job still says vendor command",
          any("exe Notepad++ (Registry64): removed, vendor command, helpers" in l for l in logs.take()))
finally:
    agent.UNINSTALLERS["exe"] = real_exe

# ---------------------------------------------------------------------------
print("5. End to end, through the real routes")
# ---------------------------------------------------------------------------
appmod.app.config["TESTING"] = True
client = appmod.app.test_client()


def db():
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


conn = db()
conn.execute("UPDATE admins SET password_hash = ?, must_change_password = 0 WHERE username = 'admin'",
             (generate_password_hash("Str0ng!Passw0rd"),))
conn.commit()
client.post("/login", data={"username": "admin", "password": "Str0ng!Passw0rd"})
agent.COMPUTER_HOSTNAME = "LAB-VLC"
agent.AGENT_ID = None
seen = []
real_post = agent.post_json


def through_client(path, payload):
    reply = client.post(path, json=payload).get_json()
    if path == "/api/agent/poll" and reply and reply.get("job"):
        seen.append(reply["job"])
    return reply


agent.post_json = through_client
try:
    agent.poll_once()
    pc = conn.execute("SELECT id FROM computers WHERE hostname = 'LAB-VLC'").fetchone()["id"]
    sw = conn.execute("INSERT INTO software (name, version, type, install_command, uninstall_switch) "
                      "VALUES ('VLC media player', '3.0.24', 'offline', '\"uploads/vlc.exe\" /S', '/S')").lastrowid
    ROW = conn.execute(
        "INSERT INTO installations (computer_id, software_id, software_name, software_version, method, record_key, "
        "reg_hive, reg_view, reg_key_name, display_name, uninstall_string, install_location, state, state_reason) "
        "VALUES (?, ?, 'VLC media player', '3.0.24', 'exe', ?, ?, 'Registry64', 'VLC media player', "
        "'VLC media player', ?, ?, 'unavailable', 'no_silent_uninstall')",
        (pc, sw, database.installation_record_key("exe", hive=HKLM, view="Registry64", key_name="VLC media player"),
         HKLM, VLC_US, VLC_DIR)).lastrowid
    conn.commit()
    reply = client.post("/uninstall-batch", data={"installation_ids": [str(ROW)]},
                        headers={"X-Requested-With": "XMLHttpRequest"}).get_json()
    check("the re-opened VLC row is queued", reply["ok"] and len(reply["created"]) == 1)
    ran = Ran()
    agent.UNINSTALLERS["exe"] = bound([VLC], VlcRegistry(Clock(), None), ran)
    logs.take()
    agent.poll_once()
    job = conn.execute("SELECT * FROM jobs WHERE installation_id = ?", (ROW,)).fetchone()
    row = conn.execute("SELECT * FROM installations WHERE id = ?", (ROW,)).fetchone()
    check("the agent got the view, the key and the switch",
          seen and {k: v for k, v in seen[-1].items() if k != "id"}
          == {"action": "uninstall", "method": "exe", "view": "Registry64", "key_name": "VLC media player",
              "switch": "/S"})
    check("...and ran uninstall.exe /S, nothing else", ran.argv == [[VLC_EXE, "/S"]])
    check("...agent.log's summary says SIHQ switch", any(l.startswith("Uninstall summary:") and "SIHQ switch" in l
                                                         for l in logs.take()))
    check("...the job completed, no reason (a plain removal)",
          (job["status"], job["exit_code"], job["result_reason"]) == ("completed", 0, None))
    check("...and the re-opened row is removed, by that job",
          (row["state"], row["state_reason"], row["uninstall_job_id"]) == ("removed", "removed", job["id"]))
    conn.close()
finally:
    agent.post_json = real_post
    agent.UNINSTALLERS["exe"] = real_exe

print()
print(f"{passed} passed, {len(failed)} failed")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

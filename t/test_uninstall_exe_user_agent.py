"""Per-user (HKCU) EXE uninstalls, the agent's half: only the account it
runs as (another SID is identity_mismatch), and an uninstaller resolving
inside that account's own AppData, found from Windows' profile list. Then
the same command choice, runner and verification as machine-wide.
"""
import ntpath
import os
import secrets
import shutil
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


work = tempfile.mkdtemp(prefix="appease_exeuser_agent_")
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

if not hasattr(agent, "uninstall_exe_user"):
    print("COULD NOT RUN - agent.py predates per-user EXE uninstalls.")
    sys.exit(3)
import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()
import app as appmod  # noqa: E402

HKLM, HKCU = agent.HKLM_NAME, agent.HKCU_NAME
PRESENT, ABSENT, UNKNOWN = agent.SIGNAL_PRESENT, agent.SIGNAL_ABSENT, agent.SIGNAL_UNKNOWN
SID = "S-1-5-21-4221116625-3915973442-1598432410-1001"
OTHER = "S-1-5-21-4221116625-3915973442-1598432410-1002"
FOLDERS = [agent._folder_key("C:\\Appease")]
PROFILE = "C:\\Users\\LabUser"
LOCAL = PROFILE + "\\AppData\\Local"
ROAMING = PROFILE + "\\AppData\\Roaming"
ROOTS = [LOCAL, ROAMING]

VSCODE_DIR = LOCAL + "\\Programs\\Microsoft VS Code\\"
VSCODE_US = '"' + LOCAL + '\\Programs\\Microsoft VS Code\\unins000.exe"'
VSCODE_QUS = VSCODE_US + " /SILENT"
OBSIDIAN_US = '"' + LOCAL + '\\Programs\\Obsidian\\Uninstall Obsidian.exe" /currentuser'
OBSIDIAN_QUS = OBSIDIAN_US + " /S"
DISCORD_US = LOCAL + "\\Discord\\Update.exe --uninstall"
DISCORD_QUS = DISCORD_US + " -s"


def user(key, sid=SID, **values):
    """One live per-user entry; values by registry value name."""
    return agent.uninstall_entry(HKCU, sid, "Registry64", key, {k.casefold(): v for k, v in values.items()})


def machine(key, view="Registry64", **values):
    return agent.uninstall_entry(HKLM, None, view, key, {k.casefold(): v for k, v in values.items()})


def snapshot(*entries, readable=True):
    return {"entries": {agent.entry_identity(e): e for e in entries}, "user_readable": readable}


VSCODE = user("{771FD6B0-FA20-440A-A002-3B3BAC16DC50}_is1", DisplayName="Microsoft Visual Studio Code (User)",
              UninstallString=VSCODE_US, QuietUninstallString=VSCODE_QUS, InstallLocation=VSCODE_DIR,
              Publisher="Microsoft Corporation", **{"Inno Setup: App Path": VSCODE_DIR.rstrip("\\")})
OBSIDIAN = user("bd400747-f0c1-5638-a859-982036102edf", DisplayName="Obsidian 1.14.4", UninstallString=OBSIDIAN_US,
                QuietUninstallString=OBSIDIAN_QUS, Publisher="Obsidian")
DISCORD = user("Discord", DisplayName="Discord", UninstallString=DISCORD_US, QuietUninstallString=DISCORD_QUS,
               InstallLocation=LOCAL + "\\Discord", Publisher="Discord Inc.")
TOOL = user("Tool", DisplayName="Tool", UninstallString='"' + LOCAL + '\\Programs\\Tool\\uninstall.exe"')


def vm_realpath(path):
    """os.path.realpath on a PC laid out like the VM. A junction in the
    account's AppData that points into Windows is followed, like the real
    one would be."""
    path = ntpath.normpath(path)
    folded = path.casefold()
    if folded.startswith(LOCAL.casefold() + "\\junction\\"):
        return "C:\\Windows\\System32\\" + path[len(LOCAL) + len("\\junction\\"):]
    if folded.startswith(("c:\\program files\\", "c:\\program files (x86)\\", "c:\\programdata\\", "c:\\users\\",
                          "c:\\windows\\")):
        return path
    raise FileNotFoundError(path)


class FakeRegistry:
    def installer_dependents(self, product_code):
        return set()


def choose(entries, key, sid=SID, own=SID, snap="default", roots=ROOTS, **job):
    found = snapshot(*entries) if snap == "default" else snap
    return agent.exe_user_uninstall_command(dict({"id": 7, "hive": "HKCU", "sid": sid, "key_name": key}, **job),
                                            own, found, roots, FakeRegistry(), FOLDERS, vm_realpath, lambda p: True)


# ---------------------------------------------------------------------------
print("1. The account's own AppData, from Windows' profile list")
# ---------------------------------------------------------------------------
same = lambda p: p  # noqa: E731
check("Local and Roaming under the profile Windows records for the SID",
      agent.user_appdata_roots(SID, lambda sid: PROFILE if sid == SID else None, same) == [LOCAL, ROAMING])
check("a profile Windows records on another drive", agent.user_appdata_roots(SID, lambda sid: "D:\\Users\\Lab", same)
      == ["D:\\Users\\Lab\\AppData\\Local", "D:\\Users\\Lab\\AppData\\Roaming"])
for label, value in (("no profile recorded", None), ("a variable left in it", "%SystemDrive%\\Users\\LabUser"),
                     ("a relative path", "Users\\LabUser"), ("a network path", "\\\\srv\\profiles\\LabUser"),
                     ("not text", 7)):
    check(f"{label}: no roots, so nothing can run", agent.user_appdata_roots(SID, lambda sid, v=value: v, same) == [])


def missing(path):
    raise FileNotFoundError(path)


check("a folder that does not exist is not a root", agent.user_appdata_roots(SID, lambda sid: PROFILE, missing) == [])
check("the profile is read from HKLM's ProfileList, not from the environment",
      agent.PROFILE_LIST_KEY == "SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\ProfileList"
      and agent._read_profile_folder("not a sid") is None)

check("Squirrel is not singled out (2026-10-07: AppEase tries it)",
      not hasattr(agent, "is_squirrel_uninstaller") and not hasattr(agent, "REASON_UNINSTALLER_SQUIRREL"))

# ---------------------------------------------------------------------------
print("3. The command, from the live per-user key")
# ---------------------------------------------------------------------------
got = choose([VSCODE], VSCODE["key_name"])
check("VS Code (User), Inno: unins000.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART",
      (got.argv, got.reason, got.kind) == ([LOCAL + "\\Programs\\Microsoft VS Code\\unins000.exe", "/VERYSILENT",
                                            "/SUPPRESSMSGBOXES", "/NORESTART"], None, agent.EXE_KIND_INNO))
got = choose([OBSIDIAN], OBSIDIAN["key_name"])
check("Obsidian: its own quiet string, arguments as written",
      (got.argv, got.reason, got.kind) == ([LOCAL + "\\Programs\\Obsidian\\Uninstall Obsidian.exe", "/currentuser",
                                            "/S"], None, agent.EXE_KIND_VENDOR))
got = choose([DISCORD], "Discord")
check("Discord (Squirrel): its own quiet string, Update.exe --uninstall -s",
      (got.argv, got.reason, got.kind) == ([LOCAL + "\\Discord\\Update.exe", "--uninstall", "-s"], None,
                                           agent.EXE_KIND_VENDOR))
got = choose([TOOL], "Tool", switch="/S")
check("no quiet string, the job's switch: the UninstallString's program with it",
      (got.argv, got.kind) == ([LOCAL + "\\Programs\\Tool\\uninstall.exe", "/S"], agent.EXE_KIND_SWITCH))
check("no quiet string and no switch: no_quiet_uninstall", choose([TOOL], "Tool")[::2] == (None, "no_quiet_uninstall"))
check("a switch outside the allowlist cleans to nothing: no_quiet_uninstall",
      choose([TOOL], "Tool", switch="/D=C:\\x")[::2] == (None, "no_quiet_uninstall"))
check("the key matched without case, as Windows matches it", choose([OBSIDIAN], OBSIDIAN["key_name"].upper()).argv
      == choose([OBSIDIAN], OBSIDIAN["key_name"]).argv)
check("the job's SID is compared with the agent's own without case",
      choose([OBSIDIAN], OBSIDIAN["key_name"], own=SID.lower()).reason is None)
check("...but a SID not written as Windows writes one (S-1-...) is not a SID",
      choose([OBSIDIAN], OBSIDIAN["key_name"], sid=SID.lower())[::2] == (None, "uninstall_job_invalid"))

check("a job naming another account: identity_mismatch, nothing chosen",
      choose([OBSIDIAN], OBSIDIAN["key_name"], sid=OTHER)[::2] == (None, "identity_mismatch"))
other_entry = user("Tool", sid=OTHER, DisplayName="Tool", QuietUninstallString='"' + LOCAL + '\\Tool\\u.exe" /S')
check("...even when that account's entry is in the snapshot",
      choose([other_entry], "Tool", sid=OTHER)[::2] == (None, "identity_mismatch"))
check("the agent's own account unknown: identity_mismatch", choose([OBSIDIAN], OBSIDIAN["key_name"], own=None)
      [::2] == (None, "identity_mismatch"))
check("a machine-wide entry of the same name is never taken for it",
      choose([machine("Tool", DisplayName="Tool", QuietUninstallString='"C:\\Program Files\\Tool\\u.exe" /S')],
             "Tool").reason == agent.EXE_KEY_GONE)
check("the account's list could not be read: registry_unreadable",
      choose([OBSIDIAN], OBSIDIAN["key_name"], snap=snapshot(OBSIDIAN, readable=False))[::2]
      == (None, "registry_unreadable"))
check("no snapshot at all: registry_unreadable", choose([], "Tool", snap=None)[::2] == (None, "registry_unreadable"))
check("the key is gone: handed to the already-gone check", choose([], "Tool").reason == agent.EXE_KEY_GONE)
for label, sid, key in (("no SID", None, "Tool"), ("a SID that is not one", "LabUser", "Tool"),
                        ("no key", SID, None), ("a key with a backslash", SID, "..\\Run"), ("a blank key", SID, ""),
                        ("a number", SID, 7)):
    check(f"the job names {label}: uninstall_job_invalid", choose([TOOL], key, sid=sid)[::2]
          == (None, "uninstall_job_invalid"))
check("a switch that is not text: uninstall_job_invalid", choose([TOOL], "Tool", switch=["/S"])[::2]
      == (None, "uninstall_job_invalid"))


def where(command):
    return user("Tool", DisplayName="Tool", QuietUninstallString=command)


for label, command in (
        ("Program Files", '"C:\\Program Files\\Tool\\uninstall.exe" /S'),
        ("ProgramData", '"C:\\ProgramData\\Tool\\uninstall.exe" /S'),
        ("the Desktop", '"' + PROFILE + '\\Desktop\\uninstall.exe" /S'),
        ("AppData\\LocalLow", '"' + PROFILE + '\\AppData\\LocalLow\\Tool\\uninstall.exe" /S'),
        ("another account's AppData", '"C:\\Users\\Other\\AppData\\Local\\Tool\\uninstall.exe" /S'),
        ("climbing out with ..", '"' + LOCAL + '\\..\\..\\..\\Public\\x.exe" /S'),
        ("a junction in AppData that points into Windows", '"' + LOCAL + '\\junction\\x.exe" /S'),
        ("a variable", '"%LOCALAPPDATA%\\Tool\\uninstall.exe" /S'),
        ("cmd.exe, even inside AppData", '"' + LOCAL + '\\Tool\\cmd.exe" /c del x'),
        ("powershell.exe, even inside AppData", '"' + LOCAL + '\\Tool\\powershell.exe" -c x'),
        ("not an .exe", '"' + LOCAL + '\\Tool\\uninstall.bat"')):
    check(f"uninstaller in {label}: uninstaller_unsafe_user_location, nothing run",
          choose([where(command)], "Tool")[::2] == (None, "uninstaller_unsafe_user_location"))
check("no roots (the profile could not be found): nothing runs",
      choose([OBSIDIAN], OBSIDIAN["key_name"], roots=[])[::2] == (None, "uninstaller_unsafe_user_location"))
python = user("Python", DisplayName="Python Launcher", QuietUninstallString='"' + LOCAL + '\\py\\u.exe" /S')
check("the Python Launcher is protected here too", choose([python], "Python")[::2] == (None, "protected_software"))
runtime = user("Runtime", DisplayName="Microsoft .NET Desktop Runtime - 8.0.10 (x64)",
               Publisher="Microsoft Corporation", QuietUninstallString='"' + LOCAL + '\\rt\\u.exe" /S')
check("a shared runtime is refused here too", choose([runtime], "Runtime")[::2] == (None, "shared_runtime"))

# The same entry content, machine-wide and per-user, chooses the same way.
PARITY = [
    ("Inno", {"DisplayName": "App", "UninstallString": '"{root}\\App\\unins000.exe"',
              "QuietUninstallString": '"{root}\\App\\unins000.exe" /SILENT', "Inno Setup: App Path": "{root}\\App"}, None),
    ("vendor", {"DisplayName": "App", "UninstallString": '"{root}\\App\\u.exe"',
                "QuietUninstallString": '"{root}\\App\\u.exe" /S /NCRC'}, None),
    ("switch", {"DisplayName": "App", "UninstallString": '"{root}\\App\\u.exe"'}, "/S"),
    ("switch with junk", {"DisplayName": "App", "UninstallString": '"{root}\\App\\u.exe"'}, "/S & del x"),
    ("nothing", {"DisplayName": "App", "UninstallString": '"{root}\\App\\u.exe"'}, None),
]
for label, values, switch in PARITY:
    hklm_entry = machine("App", **{k: v.replace("{root}", "C:\\Program Files") for k, v in values.items()})
    hkcu_entry = user("App", **{k: v.replace("{root}", LOCAL) for k, v in values.items()})
    job = {"switch": switch} if switch else {}
    a = agent.exe_uninstall_command(dict({"id": 1, "view": "Registry64", "key_name": "App"}, **job),
                                    snapshot(hklm_entry), ["C:\\Program Files"], FakeRegistry(), FOLDERS,
                                    vm_realpath, lambda p: True)
    b = choose([hkcu_entry], "App", **job)
    check(f"{label}: per-user chooses as machine-wide does ({b.kind}, {b.reason})",
          (a.kind, a.reason, (a.argv or [None])[1:]) == (b.kind, b.reason, (b.argv or [None])[1:]))

# ---------------------------------------------------------------------------
print("4. One job, start to verdict")
# ---------------------------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Registry:
    """Per-user uninstall keys of SID, and the names Windows lists."""

    def __init__(self, *entries, unknown=False):
        self.keys = {(e["sid"].upper(), e["key_name"].upper()) for e in entries if e["hive"] == HKCU}
        self.names = {e["display_name"] for e in entries if e["display_name"]}
        self.unknown = unknown
        self.asked = []

    def remove_all(self):
        self.keys.clear()
        self.names.clear()

    def uninstall_key(self, hive, sid, name, view):
        self.asked.append((hive, sid))
        if self.unknown:
            return UNKNOWN
        return PRESENT if hive == HKCU and ((sid or "").upper(), name.upper()) in self.keys else ABSENT

    def listed_display_names(self, sids):
        return None if self.unknown else set(self.names)

    def installer_dependents(self, product_code):
        return set()


class Uninstaller:
    def __init__(self, registry, clock, code=0, removes=True, timed_out=False, raises=None):
        self.registry, self.clock, self.code, self.removes = registry, clock, code, removes
        self.timed_out, self.raises = timed_out, raises
        self.argv = []

    def __call__(self, argv):
        self.argv.append(argv)
        if self.raises is not None:
            raise self.raises
        self.clock.now += 4
        if self.removes:
            self.registry.remove_all()
        return (None if self.timed_out else self.code), self.timed_out, self.timed_out, 2.0


def job(entry, sid=SID, **extra):
    return dict({"id": 9, "action": "uninstall", "method": "exe", "hive": "HKCU", "sid": sid,
                 "key_name": entry["key_name"]}, **extra)


def run(entries, the_job, code=0, removes=True, folder_present=False, registry=None, runner=None, own=SID, **over):
    clock = Clock()
    registry = registry if registry is not None else Registry(*entries)
    runner = runner or Uninstaller(registry, clock, code, removes)
    folder = (lambda path: PRESENT) if folder_present else (lambda path: ABSENT)
    verify = lambda t, c: agent.verify_exe_removal(t, c, registry, clock.time, clock.sleep, lambda: None,  # noqa
                                                   folder)
    kwargs = dict(registry=registry, read=lambda sid: snapshot(*entries), user_sid=lambda: own, run=runner,
                  verify=verify, folders=FOLDERS, roots=ROOTS, realpath=vm_realpath, isfile=lambda p: True)
    kwargs.update(over)
    if the_job.get("hive") == "HKCU":
        # Closing the app first is t/test_uninstall_close_app.py's; here it
        # must not look at this machine's real programs.
        kwargs.setdefault("close", lambda folder, sid: ([], []))
    return agent.uninstall_exe_job(the_job, **kwargs), runner, registry


def result(outcome):
    return outcome.ok, outcome.exit_code, outcome.reason


outcome, runner, registry = run([OBSIDIAN], job(OBSIDIAN))
check("Obsidian: removed", result(outcome) == (True, 0, None) and outcome.outcome.startswith("removed"))
check("...its own quiet string was what ran", runner.argv == [[LOCAL + "\\Programs\\Obsidian\\Uninstall Obsidian.exe",
                                                               "/currentuser", "/S"]])
check("...and the key it checked was the per-user one, under this account",
      all(asked == (HKCU, SID) for asked in registry.asked) and registry.asked)
outcome, runner, _ = run([VSCODE], job(VSCODE), folder_present=True)
check("VS Code with its folder still held open (the capture): removed, folder left behind",
      result(outcome) == (True, 0, "removed_folder_left") or outcome.outcome.startswith("removed_folder_left"))
outcome, runner, _ = run([DISCORD], job(DISCORD), folder_present=True)
check("Discord as the capture saw it - key gone, Update.exe and .dead left in its folder: removed, folder left",
      result(outcome) == (True, 0, "removed_folder_left") and runner.argv == [[LOCAL + "\\Discord\\Update.exe",
                                                                               "--uninstall", "-s"]])
clock = Clock()
reg = Registry(DISCORD)
outcome, _, _ = run([DISCORD], job(DISCORD), registry=reg, runner=Uninstaller(reg, clock, timed_out=True))
check("Discord whose own clean-up stops on a box nobody sees: ended at the time limit, timed_out",
      result(outcome) == (False, None, "timed_out"))
in_program_files = user("Odd", DisplayName="Odd", QuietUninstallString='"C:\\Program Files\\Odd\\uninstall.exe" /S')
outcome, runner, _ = run([in_program_files], job(in_program_files))
check("a per-user job whose uninstaller sits in Program Files: uninstaller_unsafe_user_location, nothing run",
      result(outcome) == (False, None, "uninstaller_unsafe_user_location") and runner.argv == [])
outcome, runner, _ = run([OBSIDIAN], job(OBSIDIAN, sid=OTHER))
check("another account's job: identity_mismatch, nothing run",
      result(outcome) == (False, None, "identity_mismatch") and runner.argv == [])
outcome, runner, _ = run([], job(OBSIDIAN), registry=Registry())
check("the key already gone, from the snapshot and the registry: already_removed, nothing run",
      result(outcome) == (True, None, "already_removed") and runner.argv == [])
outcome, runner, _ = run([], job(OBSIDIAN), registry=Registry(OBSIDIAN))
check("gone from the snapshot but there a moment later: registry_unreadable, nothing run",
      result(outcome) == (False, None, "registry_unreadable") and runner.argv == [])
clock = Clock()
reg = Registry(OBSIDIAN)
outcome, runner, _ = run([OBSIDIAN], job(OBSIDIAN), registry=reg, runner=Uninstaller(reg, clock, timed_out=True))
check("a prompt nobody sees, ended at the time limit: timed_out", result(outcome) == (False, None, "timed_out"))
outcome, _, _ = run([OBSIDIAN], job(OBSIDIAN), code=1)
check("its own failure: installer_exit_code 1", result(outcome) == (False, 1, "installer_exit_code"))
outcome, _, _ = run([OBSIDIAN], job(OBSIDIAN), code=0, removes=False)
check("exit 0 but still listed: not a removal", outcome.ok is False and outcome.reason == "still_listed")
reg = Registry(OBSIDIAN)
outcome, _, _ = run([OBSIDIAN], job(OBSIDIAN), registry=reg, runner=Uninstaller(reg, Clock(), raises=OSError(2, "x")))
check("could not start it: launch_failed", result(outcome) == (False, None, "launch_failed"))

# The same runs, machine-wide, read the same way.
HKLM_TOOL = machine("Tool", DisplayName="Tool", QuietUninstallString='"C:\\Program Files\\Tool\\u.exe" /S')
HKCU_TOOL = user("Tool", DisplayName="Tool", QuietUninstallString='"' + LOCAL + '\\Tool\\u.exe" /S')


class MachineRegistry(Registry):
    def __init__(self, *entries):
        super().__init__()
        self.keys = {("", e["key_name"].upper()) for e in entries}
        self.names = {e["display_name"] for e in entries}

    def uninstall_key(self, hive, sid, name, view):
        return PRESENT if hive == HKLM and ("", name.upper()) in self.keys else ABSENT


for label, code, removes, timed_out in (("removed", 0, True, False), ("still listed", 0, False, False),
                                        ("its own failure", 1603, True, False), ("restarting", 1641, True, False),
                                        ("timed out", 0, True, True)):
    outcomes = []
    for entry, the_job, reg in ((HKLM_TOOL, {"id": 1, "method": "exe", "view": "Registry64", "key_name": "Tool"},
                                 MachineRegistry(HKLM_TOOL)),
                                (HKCU_TOOL, job(HKCU_TOOL), Registry(HKCU_TOOL))):
        clock = Clock()
        runner = Uninstaller(reg, clock, code, removes, timed_out)
        outcome, _, _ = run([entry], the_job, registry=reg, runner=runner,
                            roots=["C:\\Program Files"] if entry is HKLM_TOOL else ROOTS)
        outcomes.append(result(outcome))
    check(f"{label}: per-user reports exactly what machine-wide does ({outcomes[1]})", outcomes[0] == outcomes[1])

# ---------------------------------------------------------------------------
print("5. Only HKCU jobs take this path; the agent says so")
# ---------------------------------------------------------------------------
calls = []
saved = (agent.uninstall_exe, agent.uninstall_exe_user)
try:
    agent.uninstall_exe = lambda job, **kw: calls.append(("machine", job.get("key_name")))
    agent.uninstall_exe_user = lambda job, **kw: calls.append(("user", job.get("key_name")))
    agent.uninstall_exe_job({"view": "Registry64", "key_name": "A"})
    agent.uninstall_exe_job({"hive": "HKCU", "sid": SID, "key_name": "B"})
    agent.uninstall_exe_job({"hive": "HKLM", "view": "Registry64", "key_name": "C"})
    agent.uninstall_exe_job({"hive": "hkcu", "sid": SID, "key_name": "D"})
finally:
    agent.uninstall_exe, agent.uninstall_exe_user = saved
check(f"a machine-wide job goes to uninstall_exe(), as before; only hive HKCU goes to the per-user path ({calls})",
      calls == [("machine", "A"), ("user", "B"), ("machine", "C"), ("machine", "D")])
check("the EXE entry in UNINSTALLERS is the dispatcher", agent.UNINSTALLERS["exe"] is agent.uninstall_exe_job)
check("the agent says uninstall:exe-user", "uninstall:exe-user" in agent.agent_capabilities())
check("...the name the server knows", agent.EXE_USER_CAPABILITY == appmod.EXE_USER_CAPABILITY
      and agent.EXE_USER_CAPABILITY in appmod.KNOWN_AGENT_CAPABILITIES)
saved = dict(agent.UNINSTALLERS)
try:
    agent.UNINSTALLERS["exe"] = agent.uninstall_exe
    check("...and stops saying it when the EXE entry is the machine-wide one alone",
          "uninstall:exe-user" not in agent.agent_capabilities() and "uninstall:exe" in agent.agent_capabilities())
finally:
    agent.UNINSTALLERS.clear()
    agent.UNINSTALLERS.update(saved)
check("the per-user log label names the key and the account",
      agent.uninstall_job_label({"hive": "HKCU", "sid": SID, "key_name": "Discord"}) == f"Discord (HKCU {SID})")
check("...and a machine-wide one reads exactly as before",
      agent.uninstall_job_label({"view": "Registry64", "key_name": "Notepad++"}) == "Notepad++ (Registry64)"
      and agent.uninstall_job_label({"product_code": "{X}"}) == "{X}")

# ---------------------------------------------------------------------------
print("6. The server reads the new refusal")
# ---------------------------------------------------------------------------
WORDS = {"uninstaller_unsafe_user_location": "Its uninstaller is outside its account's AppData folder, so nothing was run"}
for reason, words in WORDS.items():
    check(f"{reason} is one of the agent's refusals", reason in agent.UNINSTALL_REFUSAL_REASONS)
    check(f"{reason} reads as approved", appmod.job_result("failed", None, reason, appmod.JOB_ACTION_UNINSTALL)["label"]
          == words)

print()
print(f"{passed} passed, {len(failed)} failed")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

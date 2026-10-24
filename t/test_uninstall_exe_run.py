"""The agent running an EXE uninstall, on fakes timed like the VM capture:
it waits inside the Job Object until nothing it started is left (within the
10-minute limit, heartbeating), then verifies with the folder backstop. MSI
and Chocolatey uninstalls use neither wait.
"""
import functools
import logging
import ntpath
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


work = tempfile.mkdtemp(prefix="appease_exerun_")
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

if not hasattr(agent, "uninstall_exe"):
    print("COULD NOT RUN - agent.py predates running an EXE uninstall.")
    sys.exit(3)

HKLM = agent.HKLM_NAME
PRESENT, ABSENT, UNKNOWN = agent.SIGNAL_PRESENT, agent.SIGNAL_ABSENT, agent.SIGNAL_UNKNOWN
SID = "S-1-5-21-1111111111-2222222222-3333333333-1001"
FOLDERS = [agent._folder_key("C:\\Appease")]
VM_ROOTS = ["C:\\Program Files", "C:\\Program Files (x86)"]
WINSCP_US = '"C:\\Program Files (x86)\\WinSCP\\unins000.exe"'
WINSCP_QUS = '"C:\\Program Files (x86)\\WinSCP\\unins000.exe" /SILENT'
WINSCP_DIR = "C:\\Program Files (x86)\\WinSCP\\"
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


class Clock:
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


# ---------------------------------------------------------------------------
print("1. Running it: the uninstaller, then everything it started")
# ---------------------------------------------------------------------------


class FakeProcess:
    def __init__(self, clock, exits_at, code):
        self.clock, self.exits_at, self.code = clock, exits_at, code
        self.killed, self.returncode = False, None

    def poll(self):
        if self.killed or (self.exits_at is not None and self.clock.now >= self.exits_at):
            self.returncode = 1 if self.killed else self.code
            return self.returncode
        return None

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True


class FakeJob:
    """The Job Object: the uninstaller, and a helper copy that runs until
    helper_until (None: for ever)."""

    def __init__(self, clock, process, helper_until, fails=False):
        self.clock, self.process, self.helper_until, self.fails = clock, process, helper_until, fails
        self.terminated = self.closed = False
        self.asked = 0

    def active_processes(self):
        self.asked += 1
        if self.fails:
            raise OSError("QueryInformationJobObject failed")
        if self.terminated:
            return 0
        running = int(self.process.poll() is None)
        helper = int(self.helper_until is None or self.clock.now < self.helper_until)
        return running + helper

    def terminate(self):
        self.terminated = True
        self.process.killed = True

    def close(self):
        self.closed = True


def run_exe(exits_at, helper_until, code=0, with_job=True, fails=False):
    clock, beats, started = Clock(), [], []
    process = FakeProcess(clock, exits_at, code)
    job = FakeJob(clock, process, helper_until, fails) if with_job else None

    def start(argv, stdout, shell=True):
        started.append((argv, stdout, shell))
        return process, job

    out = agent.run_exe_uninstaller(["C:\\Program Files (x86)\\WinSCP\\unins000.exe", "/VERYSILENT"], start,
                                    clock.time, clock.sleep, lambda: beats.append(clock.now))
    return out, started, job, beats, clock


out, started, job, beats, clock = run_exe(2, 4)
check("WinSCP as captured: the uninstaller exits at 2 s, its helper at 4 s - waited for, exit code kept",
      out[:3] == (0, False, False) and out[3] == 2)
check("...started with no shell, nothing kept from its output",
      started == [(["C:\\Program Files (x86)\\WinSCP\\unins000.exe", "/VERYSILENT"], agent.subprocess.DEVNULL, False)])
check("...it returned when the job emptied, not before", clock.now == 4)
check("...and the job was closed", job.closed)
out, _, job, beats, clock = run_exe(30, 45)
check("a long one: heartbeats every 10 s through the uninstaller and the wait", beats == [10, 20, 30, 40])
out, _, job, beats, clock = run_exe(None, None)
check("an uninstaller that never ends: timed out at the 10-minute limit, the whole tree ended",
      out == (None, True, True, 0.0) and job.terminated and 600 < clock.now <= 602)
out, _, job, beats, clock = run_exe(2, None)
check("one that exits but leaves a helper running for ever: timed out at the same limit, the tree ended",
      out[:3] == (None, True, True) and job.terminated and 600 < clock.now <= 602)
check("...heartbeats all the way", len(beats) == 60)
out, _, _, _, clock = run_exe(2, 4, with_job=False)
check("no job at all: nothing to wait for, said so", out == (0, False, False, 0.0) and clock.now == 2
      and any("without a job" in line for line in logs.take()))
out, _, job, _, clock = run_exe(2, 4, fails=True)
check("the job cannot be asked: verify now rather than wait blind", out == (0, False, False, 0.0) and job.asked == 1)
check("the limit is the uninstall limit, 10 minutes", agent.UNINSTALL_TIMEOUT_SECONDS == 600)

# ---------------------------------------------------------------------------
print("2. MSI and Chocolatey use neither wait")
# ---------------------------------------------------------------------------
clock = Clock()
process = FakeProcess(clock, 2, 0)
lingering = FakeJob(clock, process, None)
msi_out = agent.run_uninstall_process(["msiexec", "/x", "{X}"], lambda argv, out, shell=True: (process, lingering),
                                      clock.time, clock.sleep, lambda: None)
check("the MSI/Chocolatey runner returns when msiexec exits, whatever is left in its job",
      msi_out == (0, False, False) and clock.now == 2 and lingering.asked == 0)


def trap(*args, **kwargs):
    raise AssertionError("an EXE-only wait was used")


class MsiRegistry:
    """One machine-wide key, listed until the uninstaller has run."""

    def __init__(self, name, view="Registry64", display="7-Zip"):
        self.name, self.view, self.display, self.listed = name.upper(), view, display, True

    def uninstall_key(self, hive, sid, name, view):
        return PRESENT if self.listed and hive == HKLM and (name.upper(), view) == (self.name, self.view) else ABSENT

    def listed_display_names(self, sids):
        return {self.display} if self.listed else set()

    def installer_dependents(self, code):
        return set()


GUID = "{23170F69-40C1-2702-2408-000001000000}"
PUTTY = "{3B0346D3-155D-43E5-9561-B9BE51CF2EB6}"
runs = []
real = (agent.run_exe_uninstaller, agent.verify_exe_removal, agent.run_uninstall_process)


def shared_runner(registry):
    """Stands in for run_uninstall_process, the runner MSI and Chocolatey
    share."""
    def run(argv, **kwargs):
        runs.append(argv[0])
        registry.listed = False
        return 0, False, False
    return run


agent.run_exe_uninstaller = agent.verify_exe_removal = trap
try:
    # MSI, with its own defaults for running and verifying.
    msi_registry = MsiRegistry(GUID)
    agent.run_uninstall_process = shared_runner(msi_registry)
    seven = agent.uninstall_entry(HKLM, None, "Registry64", GUID, {"displayname": "7-Zip", "windowsinstaller": 1})
    msi = agent.uninstall_msi({"id": 1, "method": "msi", "product_code": GUID}, registry=msi_registry,
                              read=lambda s: {"entries": {agent.entry_identity(seven): seven}}, user_sid=lambda: SID,
                              folders=FOLDERS)
    # Chocolatey, the same, on a real folder tree like the VM's.
    tree = os.path.join(work, "choco")
    os.makedirs(os.path.join(tree, "bin"))
    open(os.path.join(tree, "bin", "choco.exe"), "wb").close()
    os.makedirs(os.path.join(tree, "lib", "putty.install"))
    with open(os.path.join(tree, "lib", "putty.install", "putty.install.nuspec"), "w") as nuspec:
        nuspec.write('<?xml version="1.0"?><package><metadata><id>putty.install</id><version>0.85.0</version>'
                     '</metadata></package>')
    os.makedirs(os.path.join(tree, ".chocolatey", "putty.install.0.85.0"))
    shutil.copy(os.path.join(HERE, "fixtures", "choco-uninstall", "putty.install.0.85.0.registry"),
                os.path.join(tree, ".chocolatey", "putty.install.0.85.0", ".registry"))
    choco_registry = MsiRegistry(PUTTY, display="PuTTY release 0.85 (64-bit)")
    shared = shared_runner(choco_registry)

    def choco_runner(argv, **kwargs):
        shutil.rmtree(os.path.join(tree, "lib", "putty.install"), ignore_errors=True)
        return shared(argv, **kwargs)
    agent.run_uninstall_process = choco_runner
    choco = agent.uninstall_choco({"id": 2, "method": "choco", "package_id": "putty.install"}, registry=choco_registry,
                                  read=lambda s: {"entries": {}}, user_sid=lambda: SID, folders=FOLDERS,
                                  choco_exe=os.path.join(tree, "bin", "choco.exe"))
    trapped = False
except AssertionError:
    trapped, msi, choco = True, None, None
finally:
    agent.run_exe_uninstaller, agent.verify_exe_removal, agent.run_uninstall_process = real
check("an MSI uninstall, with its own defaults, runs through the shared runner and never an EXE-only wait",
      not trapped and msi is not None and (msi.ok, msi.reason) == (True, None) and "msiexec.exe" in runs[0].casefold())
check("a Chocolatey uninstall, with its own defaults, the same",
      not trapped and choco is not None and (choco.ok, choco.reason) == (True, None)
      and runs[-1].casefold().endswith("choco.exe"))

# And EXE uses both, by default.
calls = []
real = (agent.run_exe_uninstaller, agent.verify_exe_removal)
agent.run_exe_uninstaller = lambda argv: calls.append("run") or (0, False, False, 0.0)
agent.verify_exe_removal = lambda target, code, registry=None: calls.append("verify") or ("removed", 0.0)
try:
    entry = agent.uninstall_entry(HKLM, None, "Registry64", "Notepad++", {
        "displayname": "Notepad++", "quietuninstallstring": '"C:\\Program Files\\Notepad++\\uninstall.exe" /S'})
    agent.uninstall_exe({"id": 3, "method": "exe", "view": "Registry64", "key_name": "Notepad++"},
                        registry=MsiRegistry("Notepad++", display="Notepad++"),
                        read=lambda s: {"entries": {agent.entry_identity(entry): entry}}, user_sid=lambda: SID,
                        folders=FOLDERS, roots=["C:\\Program Files"], realpath=lambda p: p, isfile=lambda p: True)
finally:
    agent.run_exe_uninstaller, agent.verify_exe_removal = real
check("an EXE uninstall, with its own defaults, uses the EXE runner and the backstop verification",
      calls == ["run", "verify"])

# ---------------------------------------------------------------------------
print("3. Verification, and the folder backstop")
# ---------------------------------------------------------------------------


class Registry:
    """HKLM's uninstall keys, as (name, view), and the names Windows lists."""

    def __init__(self, *entries, unknown=False, dependents=None):
        self.keys = {(e["key_name"].upper(), e["view"]) for e in entries}
        self.names = {e["display_name"] for e in entries if e["display_name"]}
        self.unknown = unknown
        self.dependents = dependents or {}
        self.asked = 0

    def remove_all(self):
        self.keys.clear()
        self.names.clear()

    def uninstall_key(self, hive, sid, name, view):
        self.asked += 1
        if self.unknown:
            return UNKNOWN
        return PRESENT if hive == HKLM and (name.upper(), view) in self.keys else ABSENT

    def listed_display_names(self, sids):
        return None if self.unknown else set(self.names)

    def installer_dependents(self, product_code):
        return set(self.dependents.get(product_code.upper(), ()))


class Folder:
    """The install folder, gone from `gone_at` on (None: never)."""

    def __init__(self, clock, gone_at):
        self.clock, self.gone_at = clock, gone_at

    def signal(self, path):
        return ABSENT if self.gone_at is not None and self.clock.now >= self.gone_at else PRESENT


def target(name="winscp3_is1", display="WinSCP 6.5.7", folder=WINSCP_DIR):
    return agent.uninstall_target([{"hive": HKLM, "sid": None, "name": name}], [display], [folder])


for label, gone_at, expected, took in (("gone 3 s into the backstop", 3, "removed", 3),
                                       ("never gone", None, "removed_folder_left", 15)):
    clock = Clock()
    folder = Folder(clock, gone_at)
    verdict, waited = agent.verify_exe_removal(target(), 0, Registry(), clock.time, clock.sleep, lambda: None,
                                               folder.signal)
    check(f"keys gone, folder {label}: {expected}, after {took} s", (verdict, waited) == (expected, took))
clock = Clock()
folder = Folder(clock, None)
plain = agent.verify_removal(target(), 0, Registry(), clock.time, clock.sleep, lambda: None, folder.signal)
check("(without the backstop - MSI and Chocolatey - the same folder is left behind at once)",
      plain == ("removed_folder_left", 0))
clock = Clock()
verdict, waited = agent.verify_exe_removal(target(folder=None), 0, Registry(), clock.time, clock.sleep, lambda: None,
                                           Folder(clock, None).signal)
check("no folder recorded: removed at once, nothing to look for", (verdict, waited) == ("removed", 0))
clock = Clock()
verdict, _ = agent.verify_exe_removal(target(), 3010, Registry(), clock.time, clock.sleep, lambda: None,
                                      Folder(clock, None).signal)
check("3010 with the folder still there: restart needed, and no backstop (the restart finishes it)",
      verdict == "removed_restart_needed" and clock.now == 0)
check("the backstop is 15 seconds", agent.EXE_FOLDER_BACKSTOP_SECONDS == 15)

# ---------------------------------------------------------------------------
print("4. One job, start to verdict")
# ---------------------------------------------------------------------------


def live(key, view, **values):
    return agent.uninstall_entry(HKLM, None, view, key, {k.casefold(): v for k, v in values.items()})


WINSCP = live("winscp3_is1", "Registry32", DisplayName="WinSCP 6.5.7", UninstallString=WINSCP_US,
              QuietUninstallString=WINSCP_QUS, InstallLocation=WINSCP_DIR, Publisher="Martin Prikryl",
              **{"Inno Setup: App Path": "C:\\Program Files (x86)\\WinSCP"})
NPP = live("Notepad++", "Registry64", DisplayName="Notepad++ (64-bit x64)", UninstallString=NPP_US,
           QuietUninstallString=NPP_QUS, Publisher="Notepad++ Team")
VLC = live("VLC media player", "Registry64", DisplayName="VLC media player",
           UninstallString='"C:\\Program Files\\VideoLAN\\VLC\\uninstall.exe"')
JOBS = {"winscp": {"id": 7, "action": "uninstall", "method": "exe", "view": "Registry32", "key_name": "winscp3_is1"},
        "npp": {"id": 8, "action": "uninstall", "method": "exe", "view": "Registry64", "key_name": "Notepad++"},
        "vlc": {"id": 9, "action": "uninstall", "method": "exe", "view": "Registry64", "key_name": "VLC media player"}}


def vm_realpath(path):
    path = ntpath.normpath(path)
    if path.casefold().startswith(("c:\\program files\\", "c:\\program files (x86)\\")):
        return path
    raise FileNotFoundError(path)


class Uninstaller:
    """Stands in for run_exe_uninstaller: records the argv, and does what
    the capture saw - the key gone by the time the uninstaller exits, its
    helper waited for (2 s), the folder gone by then."""

    def __init__(self, registry, clock, code=0, removes=True, waited=2.0, timed_out=False, raises=None):
        self.registry, self.clock, self.code, self.removes = registry, clock, code, removes
        self.waited, self.timed_out, self.raises = waited, timed_out, raises
        self.argv = []

    def __call__(self, argv):
        self.argv.append(argv)
        if self.raises is not None:
            raise self.raises
        self.clock.now += 2 + self.waited
        if self.removes:
            self.registry.remove_all()
        return (None if self.timed_out else self.code), self.timed_out, self.timed_out, self.waited


produced = set()


def uninstall(entries, job, code=0, removes=True, folder_gone_at=4.0, registry=None, runner=None, **overrides):
    clock = Clock()
    registry = registry if registry is not None else Registry(*entries)
    runner = runner or Uninstaller(registry, clock, code, removes)
    folder = Folder(clock, folder_gone_at)
    verify = lambda t, c: agent.verify_exe_removal(t, c, registry, clock.time, clock.sleep, lambda: None,  # noqa
                                                   folder.signal)
    kwargs = dict(registry=registry, read=lambda sid: {"entries": {agent.entry_identity(e): e for e in entries}},
                  user_sid=lambda: SID, run=runner, verify=verify, folders=FOLDERS, roots=VM_ROOTS,
                  realpath=vm_realpath, isfile=lambda p: True)
    kwargs.update(overrides)
    outcome = agent.uninstall_exe(job, **kwargs)
    produced.add((outcome.ok, outcome.exit_code, outcome.reason))
    return outcome, runner, clock


def result(outcome):
    return outcome.ok, outcome.exit_code, outcome.reason


outcome, runner, clock = uninstall([WINSCP], JOBS["winscp"])
check("WinSCP: removed - its helper waited for, the folder gone with it", result(outcome) == (True, 0, None))
check("...run as Inno: unins000.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART",
      runner.argv == [["C:\\Program Files (x86)\\WinSCP\\unins000.exe", "/VERYSILENT", "/SUPPRESSMSGBOXES",
                       "/NORESTART"]])
check("...verified at once, no backstop needed", outcome.verified == 0)
outcome, runner, clock = uninstall([NPP], JOBS["npp"], folder_gone_at=None)
check("Notepad++: removed, its own quiet string as written; no folder recorded, so none looked for",
      result(outcome) == (True, 0, None) and runner.argv == [["C:\\Program Files\\Notepad++\\uninstall.exe", "/S"]])
outcome, runner, clock = uninstall([WINSCP], JOBS["winscp"], folder_gone_at=4 + 6)
check("a helper that slipped out of the job: the backstop sees the folder go 6 s later - removed",
      result(outcome) == (True, 0, None) and outcome.verified == 6)
outcome, runner, clock = uninstall([WINSCP], JOBS["winscp"], folder_gone_at=None)
check("the folder is still there after the backstop: removed_folder_left",
      result(outcome) == (True, 0, "removed_folder_left") and outcome.verified == 15)

CODES = [
    ("0, still listed", 0, False, (False, 0, "still_listed")),
    ("3010, gone", 3010, True, (True, 3010, "removed_restart_needed")),
    ("3010, still listed", 3010, False, (False, 3010, "still_listed")),
    ("1641", 1641, False, (True, 1641, "restart_started")),
    ("2, the uninstaller's own", 2, False, (False, 2, "installer_exit_code")),
    ("1605 - not msiexec, so just a number", 1605, True, (False, 1605, "installer_exit_code")),
    ("-1 as Windows hands it over", 4294967295, False, (False, 4294967295, "installer_exit_code")),
]
for label, code, removes, expected in CODES:
    outcome, runner, clock = uninstall([WINSCP], JOBS["winscp"], code=code, removes=removes)
    check(f"exit {label}: {expected}", result(outcome) == expected)
registry = Registry(WINSCP)
outcome, runner, clock = uninstall([WINSCP], JOBS["winscp"], code=1641, registry=registry)
check("1641 is reported as it is, without verifying (the restart is under way)", registry.asked <= 2)
outcome, runner, clock = uninstall([WINSCP], JOBS["winscp"], runner=None,
                                   run=lambda argv: (None, True, True, 0.0))
check("a timeout: timed_out, no exit code", result(outcome) == (False, None, "timed_out"))
outcome, runner, clock = uninstall([WINSCP], JOBS["winscp"], run=lambda argv: (1, True, True, 0.0))
check("...even when ending it left one behind (that 1 is the kill's, not the uninstaller's)",
      result(outcome) == (False, None, "timed_out"))


class KeyOnly(Uninstaller):
    """Removes the key, but Windows still lists the same DisplayName under
    another one - a reinstall racing it, or a second copy."""

    def __call__(self, argv):
        self.argv.append(argv)
        self.registry.keys.clear()
        return 0, False, False, 2.0


registry = Registry(WINSCP)
outcome, runner, clock = uninstall([WINSCP], JOBS["winscp"], registry=registry, runner=KeyOnly(registry, Clock()))
check("the key gone but its exact DisplayName still listed: still_listed - the name is part of the baseline",
      result(outcome) == (False, 0, "still_listed"))
outcome, runner, clock = uninstall([WINSCP], JOBS["winscp"], run=lambda argv: (_ for _ in ()).throw(OSError("no")))
check("the uninstaller cannot be started: launch_failed", result(outcome) == (False, None, "launch_failed"))

ran = []
outcome, runner, clock = uninstall([], JOBS["winscp"], run=lambda argv: ran.append(argv))
check("the key already gone, in both views: already_removed, nothing run",
      result(outcome) == (True, None, "already_removed") and ran == [])
other_view = Registry(live("winscp3_is1", "Registry64", DisplayName="WinSCP"))
outcome, runner, clock = uninstall([], JOBS["winscp"], registry=other_view, run=lambda argv: ran.append(argv))
check("gone from the recorded view but in the other one: not this entry - identity_mismatch, nothing run",
      result(outcome) == (False, None, "identity_mismatch") and ran == [])
outcome, runner, clock = uninstall([], JOBS["winscp"], registry=Registry(unknown=True), run=lambda argv: ran.append(argv))
check("gone from the recorded view, and the rest cannot be read: registry_unreadable, nothing run",
      result(outcome) == (False, None, "registry_unreadable") and ran == [])
outcome, runner, clock = uninstall([VLC], JOBS["vlc"], run=lambda argv: ran.append(argv))
check("no quiet uninstall (VLC): no_quiet_uninstall, nothing run",
      result(outcome) == (False, None, "no_quiet_uninstall") and ran == [])
evil = live("Notepad++", "Registry64", DisplayName="Notepad++", QuietUninstallString='"C:\\ProgramData\\Evil\\x.exe" /S')
outcome, runner, clock = uninstall([evil], JOBS["npp"], run=lambda argv: ran.append(argv))
check("a QuietUninstallString tampered into ProgramData: uninstaller_unsafe_location, nothing run",
      result(outcome) == (False, None, "uninstaller_unsafe_location") and ran == [])


def no_read(sid):
    raise AssertionError("the registry was read")


for label, job in (("no view", {"id": 1, "method": "exe", "key_name": "x"}),
                   ("a key with a backslash", {"id": 1, "method": "exe", "view": "Registry64", "key_name": "a\\b"})):
    outcome, runner, clock = uninstall([NPP], job, read=no_read, run=lambda argv: ran.append(argv))
    check(f"a job with {label}: uninstall_job_invalid before anything is read",
          result(outcome) == (False, None, "uninstall_job_invalid") and ran == [])

# ---------------------------------------------------------------------------
print("5. Errors: never a removal, never a crash; one summary line")
# ---------------------------------------------------------------------------


def boom(*args, **kwargs):
    raise RuntimeError("stage exploded")


def bound(entries=(WINSCP,), **overrides):
    clock = Clock()
    registry = Registry(*entries)
    defaults = dict(registry=registry, read=lambda sid: {"entries": {agent.entry_identity(e): e for e in entries}},
                    user_sid=lambda: SID, run=Uninstaller(registry, clock), folders=FOLDERS, roots=VM_ROOTS,
                    realpath=vm_realpath, isfile=lambda p: True,
                    verify=lambda t, c: agent.verify_exe_removal(t, c, registry, clock.time, clock.sleep, lambda: None,
                                                                 lambda f: ABSENT))
    defaults.update(overrides)
    return functools.partial(agent.uninstall_exe, **defaults)


real_exe = agent.UNINSTALLERS["exe"]
real_roots = agent.program_files_roots
try:
    for stage, overrides in (("asking which account the agent runs as", {"user_sid": boom}),
                             ("reading Windows' program list", {"read": boom}),
                             ("running the uninstaller", {"run": boom}),
                             ("verifying", {"verify": boom}),
                             ("checking protected folders", {"folders": object()}),
                             ("reading the Program Files folders", {"roots": None})):
        if overrides.get("roots", "x") is None:
            agent.program_files_roots = boom
        agent.UNINSTALLERS["exe"] = bound(**overrides)
        logs.take()
        outcome = agent.run_uninstall_job(JOBS["winscp"])
        agent.program_files_roots = real_roots
        lines = [l for l in logs.take() if l.startswith("Uninstall summary:")]
        check(f"an exception while {stage}: failed agent_error, one summary line saying so",
              (outcome.ok, outcome.reason, outcome.exit_code) == (False, "agent_error", None)
              and len(lines) == 1 and "agent_error" in lines[0])
    agent.UNINSTALLERS["exe"] = bound()
    logs.take()
    agent.run_uninstall_job(JOBS["winscp"])
    summary = [l for l in logs.take() if l.startswith("Uninstall summary:")]
    check("the summary line: key, view, Inno or vendor, verdict, exit code, the wait and verification times",
          summary == ["Uninstall summary: job 7, exe winscp3_is1 (Registry32): removed, Inno command, helpers 2s; "
                      "exit code 0; verification 0s."])
    agent.UNINSTALLERS["exe"] = bound(entries=(NPP,))
    agent.run_uninstall_job(JOBS["npp"])
    check("...and a vendor command says so", any("exe Notepad++ (Registry64): removed, vendor command" in l
                                                for l in logs.take()))
finally:
    agent.UNINSTALLERS["exe"] = real_exe
    agent.program_files_roots = real_roots

sent = []
real_post = agent.post_json
try:
    agent.post_json = lambda path, payload: sent.append((path, payload)) or {"reason": payload.get("reason")}
    agent.UNINSTALLERS["exe"] = bound(user_sid=boom)
    agent.handle_uninstall_job(JOBS["winscp"])
    check("the poll reports an agent_error as a failure, never a success",
          [(p, b["status"], b["reason"], b["exit_code"]) for p, b in sent]
          == [("/api/agent/jobs/7/report", "failed", "agent_error", None)])

    def unexpected(path, payload):
        raise TypeError("not a server error")
    agent.post_json = unexpected
    agent.UNINSTALLERS["exe"] = bound()
    try:
        agent.handle_uninstall_job(JOBS["winscp"])
        survived = True
    except Exception:  # noqa: BLE001
        survived = False
    check("even an unexpected error while reporting never escapes into the poll loop", survived)
finally:
    agent.post_json = real_post
    agent.UNINSTALLERS["exe"] = real_exe
check("the poll hands an EXE job to the dispatcher, which sends a machine-wide one to uninstall_exe",
      agent.UNINSTALLERS.get("exe") is agent.uninstall_exe_job)
check("the agent now advertises all three methods, and per-user EXEs (2026-10-07)",
      agent.agent_capabilities() == ["uninstall:choco", "uninstall:exe", "uninstall:exe-user", "uninstall:msi"])

# ---------------------------------------------------------------------------
print("6. The server: every result kept as sent, and end to end")
# ---------------------------------------------------------------------------
import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()
import app as appmod  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402
appmod.app.config["TESTING"] = True
client = appmod.app.test_client()


def db():
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


conn = db()
conn.execute("UPDATE admins SET password_hash = ?, must_change_password = 0 WHERE username = 'admin'",
             (generate_password_hash("Str0ng!Passw0rd"),))
pc = conn.execute("INSERT INTO computers (hostname, status) VALUES ('LAB-X', 'online')").lastrowid
conn.commit()
mismatches = []
for ok, exit_code, reason in sorted(produced, key=repr):
    row = conn.execute("INSERT INTO installations (computer_id, software_name, method, record_key, state) "
                       "VALUES (?, 'X', 'exe', ?, 'installed')", (pc, secrets.token_hex(8))).lastrowid
    job = conn.execute("INSERT INTO jobs (computer_hostname, software_id, status, action, installation_id, started_at) "
                       "VALUES ('LAB-X', 0, 'in_progress', 'uninstall', ?, CURRENT_TIMESTAMP)", (row,)).lastrowid
    conn.commit()
    body = {"computer_hostname": "LAB-X", "status": "completed" if ok else "failed", "exit_code": exit_code,
            "reason": reason}
    client.post(f"/api/agent/jobs/{job}/report", json=body)
    kept = tuple(conn.execute("SELECT status, exit_code, result_reason FROM jobs WHERE id = ?", (job,)).fetchone())
    if kept != (body["status"], exit_code, reason):
        mismatches.append(((ok, exit_code, reason), kept))
check(f"every result this suite produced is kept exactly as sent ({len(produced)} shapes; mismatches: {mismatches})",
      not mismatches and len(produced) >= 14)
conn.close()

client.post("/login", data={"username": "admin", "password": "Str0ng!Passw0rd"})
agent.COMPUTER_HOSTNAME = "LAB-XE2E"
agent.AGENT_ID = None
seen_jobs = []


def through_client(path, payload):
    reply = client.post(path, json=payload).get_json()
    if path == "/api/agent/poll" and reply and reply.get("job"):
        seen_jobs.append(reply["job"])
    return reply


agent.post_json = through_client
try:
    agent.poll_once()
    conn = db()
    pc_row = conn.execute("SELECT id, capabilities FROM computers WHERE hostname = 'LAB-XE2E'").fetchone()
    check("the real poll registers the PC as able to uninstall all three, and per-user EXEs",
          pc_row["capabilities"] == "uninstall:choco uninstall:exe uninstall:exe-user uninstall:msi")
    ROW = conn.execute(
        "INSERT INTO installations (computer_id, software_name, software_version, method, record_key, reg_hive, "
        "reg_view, reg_key_name, display_name, uninstall_string, quiet_uninstall_string, install_location, state) "
        "VALUES (?, 'WinSCP', '6.5.7', 'exe', ?, ?, 'Registry32', 'winscp3_is1', 'WinSCP 6.5.7', ?, ?, ?, 'installed')",
        (pc_row["id"], database.installation_record_key("exe", hive=HKLM, view="Registry32", key_name="winscp3_is1"),
         HKLM, WINSCP_US, WINSCP_QUS, WINSCP_DIR)).lastrowid
    conn.commit()
    reply = client.post("/uninstall-batch", data={"installation_ids": [str(ROW)]}).get_json()
    check("the create route queues it", reply["ok"] and len(reply["created"]) == 1)
    agent.UNINSTALLERS["exe"] = bound()
    agent.poll_once()
    job = conn.execute("SELECT * FROM jobs WHERE installation_id = ?", (ROW,)).fetchone()
    row = conn.execute("SELECT * FROM installations WHERE id = ?", (ROW,)).fetchone()
    check("end to end: the agent got only the view and the key",
          seen_jobs and {k: v for k, v in seen_jobs[-1].items() if k != "id"}
          == {"action": "uninstall", "method": "exe", "view": "Registry32", "key_name": "winscp3_is1"})
    check("...the job completed with no reason (a plain removal)",
          (job["status"], job["exit_code"], job["result_reason"]) == ("completed", 0, None))
    check("...and the installations row is removed by that job",
          (row["state"], row["state_reason"], row["uninstall_job_id"]) == ("removed", "removed", job["id"]))
    conn.close()
finally:
    agent.post_json = real_post
    agent.UNINSTALLERS["exe"] = real_exe

print()
print(f"{passed} passed, {len(failed)} failed")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

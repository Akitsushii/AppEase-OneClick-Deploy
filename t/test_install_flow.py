"""Install recording wired into the real install: the snapshot before, EXE
settling, the record after, and the job report that carries it. An error at
ANY stage leaves the install's result exactly as it was. Time is a stand-in
clock; section 7 runs a real process on Windows only.

    python t/test_install_flow.py --agent C:\\Appease\\agent.py
"""
import contextlib
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time as real_time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

passed = 0
failed = []
skipped = []


def check(label, condition):
    global passed
    if condition:
        passed += 1
    else:
        failed.append(label)
        print(f"  FAIL: {label}")


def skip(label, why):
    skipped.append(label)
    print(f"  SKIPPED: {label} - {why}")


def arg_path(name, default):
    for index, arg in enumerate(sys.argv):
        if arg == name and index + 1 < len(sys.argv):
            return os.path.abspath(sys.argv[index + 1])
    return default


AGENT_PATH = arg_path("--agent", os.path.join(ROOT, "agent", "agent.py"))
SERVER_SRC = arg_path("--server", os.path.join(ROOT, "server"))
if not os.path.isfile(AGENT_PATH):
    print("COULD NOT RUN - agent.py was not found.")
    sys.exit(2)

work = tempfile.mkdtemp(prefix="appease_flow_")
shutil.copy(AGENT_PATH, os.path.join(work, "agent.py"))
sys.path.insert(0, work)
import agent  # noqa: E402

if not hasattr(agent, "InstallRecorder"):
    print("COULD NOT RUN - agent.py predates install recording in the job report.")
    sys.exit(3)


class Captured(agent.logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append((record.levelno, record.getMessage()))

    def take(self, level=0):
        found = [m for lv, m in self.messages if lv >= level]
        self.messages = []
        return "\n".join(found)


for handler in list(agent.logger.handlers):
    if type(handler) is agent.logging.StreamHandler:
        agent.logger.removeHandler(handler)
logs = Captured()
agent.logger.addHandler(logs)

HKLM = agent.HKLM_NAME
SID = "S-1-5-21-1111111111-2222222222-3333333333-1001"
GUID = "{0AA2C178-BF99-3F72-BA87-F6123A1A95C8}"
GUID2 = "{E8B39B76-B02C-4A5B-B2C6-C015BD72366E}"


def entry(key, view="Registry64", **values):
    return agent.uninstall_entry(HKLM, None, view, key, {k.casefold(): v for k, v in values.items()})


def snap(*entries, readable=True):
    return {"entries": {agent.entry_identity(e): e for e in entries}, "user_readable": readable}


BASE = [entry("Old Tool", DisplayName="Old Tool", UninstallString="u")]
NPP = entry("Notepad++", DisplayName="Notepad++", DisplayVersion="8.7.1", UninstallString="u",
            QuietUninstallString="u /S")
NPP_HALF = entry("Notepad++", DisplayName="Notepad++", DisplayVersion="", UninstallString="u")
OTHER = entry("Other", DisplayName="Other App", UninstallString="u")
SEVEN = entry(GUID, DisplayName="7-Zip 24.08", DisplayVersion="24.08", WindowsInstaller=1, UninstallString="m")


class Clock:
    """Stands in for the time module: sleeping only moves the hands."""

    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        # Time never runs backwards: a negative wait is a bug in the caller,
        # and letting it through could make a loop wait for ever.
        if seconds < 0:
            raise ValueError(f"asked to sleep {seconds}s")
        self.slept.append(seconds)
        self.now += seconds


class Snapshots:
    """read_uninstall_snapshot() as a script: the before snapshot, then each
    later read in turn (the last one repeats)."""

    def __init__(self, *snapshots, none_from=None, raise_from=None):
        self.snapshots, self.none_from, self.raise_from, self.calls = list(snapshots), none_from, raise_from, 0

    def __call__(self, sid):
        index = self.calls
        self.calls += 1
        # A runaway loop fails here instead of hanging the suite.
        if index >= 50:
            raise RuntimeError("probe: the registry was read 50 times - a loop that never ends")
        if self.raise_from is not None and index >= self.raise_from:
            raise RuntimeError("probe: the registry reader broke")
        if self.none_from is not None and index >= self.none_from:
            return None
        return self.snapshots[min(index, len(self.snapshots) - 1)]


clock = Clock()
heartbeats = []
REAL = {name: getattr(agent, name) for name in (
    "read_uninstall_snapshot", "agent_user_sid", "read_msi_product_code", "send_install_heartbeat",
    "run_and_classify", "post_json", "time", "build_exe_record", "build_msi_record", "build_choco_record",
    "settle_exe", "CHOCO_EXE")}


def stand_ins(snapshots, msi_code=None):
    clock.now, clock.slept[:] = 1000.0, []
    heartbeats.clear()
    agent.time = types.SimpleNamespace(time=clock.time, sleep=clock.sleep)
    agent.read_uninstall_snapshot = snapshots
    agent.agent_user_sid = lambda: SID
    agent.send_install_heartbeat = lambda: heartbeats.append(clock.now)
    agent.read_msi_product_code = (msi_code if callable(msi_code) else (lambda path: msi_code))


def restore():
    for name, value in REAL.items():
        setattr(agent, name, value)


# ---------------------------------------------------------------------------
print("1. The settling decision")
# ---------------------------------------------------------------------------
one = agent.settle_signature(snap(*BASE), snap(*BASE, NPP))
check("a signature names the new visible entries, with name, version and uninstall string",
      len(one) == 1 and one[0][1:] == ("Notepad++", "8.7.1", "u"))
check("hidden new entries are not in it",
      agent.settle_signature(snap(*BASE), snap(*BASE, entry("H", DisplayName="H", SystemComponent=1))) == ())
check("two agreeing checks of one entry, nothing running: stop", agent.settled_early(one, one, 0))
check("...the first check alone never stops it", not agent.settled_early(None, one, 0))
check("...not while the install's processes still run", not agent.settled_early(one, one, 2))
check("...unknown process count does not hold it up", agent.settled_early(one, one, None))
check("...a changed version is not agreement",
      not agent.settled_early(agent.settle_signature(snap(*BASE), snap(*BASE, NPP_HALF)), one, 0))
two = agent.settle_signature(snap(*BASE), snap(*BASE, NPP, OTHER))
check("...two entries never stop it early", not agent.settled_early(two, two, 0))
check("...an empty diff never stops it early", not agent.settled_early((), (), 0))


def settle(snapshots, running=lambda: 0):
    stand_ins(snapshots)
    try:
        return agent.settle_exe(snap(*BASE), SID, running)
    except Exception as exc:  # noqa: BLE001 - raising IS the failure
        return f"raised {type(exc).__name__}", None, None
    finally:
        restore()


after, waited, early = settle(Snapshots(snap(*BASE, NPP)))
check("entry there at once and stable: stops at the second check, 10s", early and waited == 10 and after is not None)
check("...with one heartbeat between the checks", heartbeats == [1010.0])
after, waited, early = settle(Snapshots(snap(*BASE), snap(*BASE, NPP)))
check("entry appears at the second check: stops at the third, 20s", early and waited == 20)
after, waited, early = settle(Snapshots(snap(*BASE, NPP_HALF), snap(*BASE, NPP)))
check("entry still being written at the first check: stops when two agree, 20s", early and waited == 20)
after, waited, early = settle(Snapshots(snap(*BASE)))
check("empty diff: waits the full 60s", not early and waited == 60 and after is not None)
check("...checking every 10s, a heartbeat after each wait", clock.slept == [10] * 6 and len(heartbeats) == 6)
after, waited, early = settle(Snapshots(snap(*BASE, NPP, OTHER)))
check("two new entries: waits the full 60s", not early and waited == 60)
running = iter([3, 2, 1, 0, 0, 0, 0, 0])
after, waited, early = settle(Snapshots(snap(*BASE, NPP)), running=lambda: next(running))
check("a bootstrapper still running holds it until it ends, 30s", early and waited == 30)
after, waited, early = settle(Snapshots(snap(*BASE, NPP)), running=lambda: 1)
check("...and one that never ends is cut off at 60s", not early and waited == 60)
after, waited, early = settle(Snapshots(snap(*BASE, NPP), none_from=1))
check("a snapshot that cannot be read ends settling with nothing", after is None)


# ---------------------------------------------------------------------------
print("2. The MSI ProductCode fallback")
# ---------------------------------------------------------------------------
B = snap(*BASE)
check("one new Windows Installer product whose name matches: its ProductCode",
      agent.product_code_from_diff(B, snap(*BASE, SEVEN), "7-Zip") == GUID)
check("...a matching name is required", agent.product_code_from_diff(B, snap(*BASE, SEVEN), "Paint.NET") is None)
hidden_product = entry(GUID2, DisplayName="VC++ Runtime", WindowsInstaller=1, SystemComponent=1)
check("...a second new product counts even when hidden",
      agent.product_code_from_diff(B, snap(*BASE, SEVEN, hidden_product), "7-Zip") is None)
check("...a GUID key without WindowsInstaller=1 is not a product",
      agent.product_code_from_diff(B, snap(*BASE, entry(GUID, DisplayName="7-Zip 24.08", UninstallString="u")),
                                   "7-Zip") is None)
check("...nothing new: none", agent.product_code_from_diff(B, B, "7-Zip") is None)
check("...a product already there before is not new",
      agent.product_code_from_diff(snap(*BASE, SEVEN), snap(*BASE, SEVEN), "7-Zip") is None)


# ---------------------------------------------------------------------------
print("3. run_install_command, with the install itself stood in")
# ---------------------------------------------------------------------------
OK = agent.InstallResult(True, 0, None)
FAILED = agent.InstallResult(False, 1603, "msi_fatal")
TIMED_OUT = agent.InstallResult(False, None, agent.REASON_TIMED_OUT)
events = []


class Download:
    def __init__(self, url, timeout=None):
        events.append("download")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b"installer bytes"


def fake_run(result, running=lambda: 0, boom=False):
    def run(command, command_to_run, deadline, on_success=None):
        events.append("install")
        if on_success is not None and result.ok:
            if boom:
                # what the real run_and_classify does around the hook
                try:
                    on_success(running)
                except Exception:  # noqa: BLE001
                    events.append("hook raised")
            else:
                on_success(running)
        return result
    return run


real_urlopen = agent.request.urlopen
agent.request.urlopen = Download
choco_root = tempfile.mkdtemp(prefix="choco_", dir=work)
for sub in ("lib", ".chocolatey", "bin"):
    os.makedirs(os.path.join(choco_root, sub))
open(os.path.join(choco_root, "bin", "choco.exe"), "wb").close()


def install(command, name, snapshots, result=OK, msi_code=None, running=lambda: 0, boom=False, patches=None):
    events.clear()
    stand_ins(snapshots, msi_code)
    agent.CHOCO_EXE = os.path.join(choco_root, "bin", "choco.exe")
    agent.run_and_classify = fake_run(result, running, boom)
    for name_, value in (patches or {}).items():
        setattr(agent, name_, value)
    recorder = agent.InstallRecorder(name)
    logs.take()
    try:
        got = agent.run_install_command(command, 7, recorder=recorder)
    except Exception as exc:  # noqa: BLE001 - raising IS the failure
        got = f"raised {type(exc).__name__}: {exc}"
    finally:
        restore()
    return got, recorder


got, rec = install('"uploads/npp.exe" /S', "Notepad++", Snapshots(snap(*BASE), snap(*BASE, NPP)))
check("exe: the result is the install's own", got is OK)
check("exe: the snapshot comes after the download, right before the install",
      events == ["download", "install"] and rec.before is not None)
check("exe: settled, stopping early", rec.stopped_early is True and rec.settle_seconds == 10)
check("exe: the record names the entry", (rec.record or {}).get("key_name") == "Notepad++"
      and "unavailable_reason" not in rec.record)
check("exe: exactly one summary line in agent.log", logs.take().count("Install record: exe,") == 1)
OLD_NPP = entry("Notepad++ 7", DisplayName="Notepad++ 7", UninstallString="u")
got, rec2 = install('"uploads/npp.exe" /S', "Notepad++", Snapshots(snap(*BASE, OLD_NPP), snap(*BASE, NPP)))
check("exe upgrade: the summary counts the entry it replaced", rec2.summary().endswith("; 1 replaced."))
check("exe: summary text", rec.summary() ==
      "Install record: exe, recorded: HKLM Registry64 Notepad++ 8.7.1; settled in 10s (stopped early); 0 replaced.")

seen_paths = []


def code_reader(path):
    seen_paths.append((path, os.path.isfile(path)))
    return GUID


got, rec = install('msiexec /i "uploads/7z.msi" /qn', "7-Zip", Snapshots(snap(*BASE), snap(*BASE, SEVEN)),
                   msi_code=code_reader)
check("msi: the result is the install's own", got is OK)
check("msi: the ProductCode is read from the downloaded file while it still exists",
      len(seen_paths) == 1 and seen_paths[0][1] and seen_paths[0][0].endswith("7z.msi"))
check("msi: no settling", rec.settle_seconds is None)
check("msi: the record names the product", (rec.record or {}).get("product_code") == GUID)
check("msi: summary text", rec.summary() == "Install record: msi, recorded: " + GUID + " 24.08; no settling; 0 replaced.")
check("msi: the downloaded file is gone afterwards, as before", not os.path.exists(seen_paths[0][0]))

got, rec = install('msiexec /i "uploads/7z.msi" /qn', "7-Zip", Snapshots(snap(*BASE), snap(*BASE, SEVEN)),
                   msi_code=None)
check("msi fallback: COM failed, the one matching new product's code is used",
      (rec.record or {}).get("product_code") == GUID and "unavailable_reason" not in rec.record)
check("msi fallback: and the log says the registry stood in", "used instead" in logs.take())
got, rec = install('msiexec /i "uploads/7z.msi" /qn', "Paint.NET", Snapshots(snap(*BASE), snap(*BASE, SEVEN)),
                   msi_code=None)
check("msi fallback: name does not match, product_code_unreadable",
      (rec.record or {}).get("unavailable_reason") == agent.RECORD_REASON_PRODUCT_CODE_UNREADABLE)

VLC = ("vlc", "vlc.install")
os.makedirs(os.path.join(choco_root, "lib", "vlc"), exist_ok=True)
calls = []


def fake_choco_record(package, reason, before, after, root, folders):
    calls.append((package, reason, before is not None, after is not None, root))
    return {"method": "choco", "package_id": "vlc.install", "display_version": "3.0.23", "replaced_entries": []}


got, rec = install("choco install vlc -y", "VLC", Snapshots(snap(*BASE)),
                   patches={"build_choco_record": fake_choco_record})
check("choco: the result is the install's own", got is OK)
check("choco: the 3b-3 builder gets the package, the reason and both snapshots",
      calls and calls[-1][:4] == ("vlc", None, True, True) and calls[-1][4] == choco_root)
check("choco: no settling", rec.settle_seconds is None)
check("choco: summary text", rec.summary() == "Install record: choco, recorded: vlc.install 3.0.23; no settling; 0 replaced.")
got, rec = install("choco install vlc -y", "VLC", Snapshots(snap(*BASE)),
                   result=agent.InstallResult(True, 0, agent.REASON_CHOCO_CONFIRMED_INSTALLED),
                   patches={"confirm_choco_already_installed": lambda c, r, d: r})
check("choco 'already installed': no record, and the summary says why",
      rec.record is None and "Chocolatey reported it already installed" in rec.summary())

for label, result in (("failed", FAILED), ("timed out", TIMED_OUT)):
    got, rec = install('"uploads/npp.exe" /S', "Notepad++", Snapshots(snap(*BASE), snap(*BASE, NPP)), result=result)
    check(f"{label}: the result is the install's own", got is result)
    check(f"{label}: no settling and no record", rec.settle_seconds is None and rec.record is None)
    check(f"{label}: the summary says the install did not complete",
          f"none - the install did not complete ({result.reason})" in rec.summary())

for label, reason, snapshots, name in (
    ("no new entry", agent.RECORD_REASON_NO_NEW_ENTRY, Snapshots(snap(*BASE)), "Notepad++"),
    ("several", agent.RECORD_REASON_SEVERAL_NEW_ENTRIES, Snapshots(snap(*BASE), snap(*BASE, NPP, OTHER)), "Notepad++"),
    ("name mismatch", agent.RECORD_REASON_NAME_MISMATCH, Snapshots(snap(*BASE), snap(*BASE, NPP)), "VLC"),
    ("no silent uninstall", agent.RECORD_REASON_NO_SILENT_UNINSTALL,
     Snapshots(snap(*BASE), snap(*BASE, OTHER)), "Other App"),
    ("user hive unreadable", agent.RECORD_REASON_USER_HIVE_UNREADABLE,
     Snapshots(snap(*BASE, readable=False)), "Notepad++"),
    ("protected", agent.RECORD_REASON_PROTECTED, Snapshots(snap(*BASE), snap(*BASE, entry(
        "Py", DisplayName="Python {}.{}.1 (64-bit)".format(*sys.version_info[:2]), QuietUninstallString="q",
        UninstallString="u"))), "Python"),
):
    got, rec = install('"uploads/x.exe" /S', name, snapshots)
    check(f"exe {label}: the result is the install's own", got is OK)
    check(f"exe {label}: unavailable {reason}", (rec.record or {}).get("unavailable_reason") == reason)
    check(f"exe {label}: the summary says so", f"unavailable ({reason})" in rec.summary())
got, rec = install('msiexec /i "uploads/7z.msi" /qn', "7-Zip", Snapshots(snap(*BASE, SEVEN)), msi_code=GUID)
check("msi already there before: unavailable no_new_entry",
      (rec.record or {}).get("unavailable_reason") == agent.RECORD_REASON_NO_NEW_ENTRY)
got, rec = install('msiexec /i "uploads/7z.msi" /qn', "7-Zip", Snapshots(snap(*BASE)), msi_code=None)
got, rec3 = install('msiexec /i "uploads/7z.msi" /qn', "7-Zip", Snapshots(snap(*BASE), none_from=1), msi_code=GUID)
check("msi, keys unreadable after the install: no record, and the summary says why",
      rec3.record is None and "could not be read after the install" in rec3.summary())
check("msi with no code anywhere: unavailable product_code_unreadable",
      (rec.record or {}).get("unavailable_reason") == agent.RECORD_REASON_PRODUCT_CODE_UNREADABLE)

got, rec = install('"uploads/npp.exe" /S', "Notepad++", Snapshots(snap(*BASE), snap(*BASE, NPP)),
                   running=lambda: 1)
check("exe with its processes still running: the full wait", rec.settle_seconds == 60 and not rec.stopped_early)
check("...heartbeating throughout", len(heartbeats) == 6)


# ---------------------------------------------------------------------------
print("4. An error at every stage leaves the install's result alone")
# ---------------------------------------------------------------------------
def explode(*args, **kwargs):
    raise RuntimeError("probe: this stage broke")


# Each stage's breakage, built fresh for every run: a stateful stand-in
# reused across two runs would break at the wrong stage the second time.
stages = (
    ("the snapshot before", '"uploads/npp.exe" /S', lambda: {"read_uninstall_snapshot": explode}),
    ("the SID lookup", '"uploads/npp.exe" /S', lambda: {"agent_user_sid": explode}),
    ("settling", '"uploads/npp.exe" /S', lambda: {"settle_exe": explode}),
    ("a snapshot while settling", '"uploads/npp.exe" /S',
     lambda: {"read_uninstall_snapshot": Snapshots(snap(*BASE), raise_from=1)}),
    ("a heartbeat while settling", '"uploads/npp.exe" /S', lambda: {"send_install_heartbeat": explode}),
    ("the exe record", '"uploads/npp.exe" /S', lambda: {"build_exe_record": explode}),
    ("the ProductCode read", 'msiexec /i "uploads/7z.msi" /qn', lambda: {"read_msi_product_code": explode}),
    ("the msi record", 'msiexec /i "uploads/7z.msi" /qn', lambda: {"build_msi_record": explode}),
    ("the snapshot after", 'msiexec /i "uploads/7z.msi" /qn',
     lambda: {"read_uninstall_snapshot": Snapshots(snap(*BASE), raise_from=1)}),
    ("the choco record", "choco install vlc -y", lambda: {"build_choco_record": explode}),
)
for label, command, patches in stages:
    for result in (OK, FAILED):
        got, rec = install(command, "Notepad++", Snapshots(snap(*BASE), snap(*BASE, NPP)), result=result,
                           msi_code=GUID, patches=patches())
        check(f"{label} breaks, install {'ok' if result.ok else 'failed'}: the result is exactly the install's",
              got is result)
    check(f"{label} breaks: no record", rec.record is None)
got, rec = install('"uploads/npp.exe" /S', "Notepad++", Snapshots(snap(*BASE), snap(*BASE, NPP)), boom=True,
                   patches={"settle_exe": explode})
check("the settling hook raising inside the install is caught there, result unchanged", got is OK)

recorder = agent.InstallRecorder("x")
recorder.summary = explode
logs.take()
try:
    recorder.finish(OK)
    survived = True
except BaseException:  # noqa: BLE001 - that IS the failure being checked for
    survived = False
check("even the summary line failing raises nothing", survived and "summary line failed" in logs.take())
recorder = agent.InstallRecorder("x")
logs.take()
recorder.finish(FAILED)
recorder.finish(FAILED)
check("finishing twice still writes one summary line", logs.take().count("Install record: ") == 1)


# ---------------------------------------------------------------------------
print("5. The job report: only completed installs carry a record")
# ---------------------------------------------------------------------------
posts = []
reply = {}


def fake_post(path, payload):
    posts.append((path, payload))
    if path == "/api/agent/poll":
        return {"job": {"id": 41, "software_id": 7, "software_name": "Notepad++",
                        "install_command": '"uploads/npp.exe" /S'}}
    return dict(reply)


def poll(result, snapshots, server_reply):
    posts.clear()
    reply.clear()
    reply.update(server_reply)
    events.clear()
    stand_ins(snapshots)
    agent.run_and_classify = fake_run(result)
    agent.post_json = fake_post
    logs.take()
    try:
        agent.poll_once()
    finally:
        restore()
    report = [p for path, p in posts if path.endswith("/report")]
    return report[0] if report else None


NEW_SERVER = {"status": "completed", "reason": None, "install_record": "stored"}
OLD_SERVER = {"message": "job updated", "job_id": 41, "status": "completed", "reason": None, "exit_code": 0}
sent = poll(OK, Snapshots(snap(*BASE), snap(*BASE, NPP)), NEW_SERVER)
check("completed: the report carries the record", (sent or {}).get("install_record", {}).get("key_name") == "Notepad++")
check("...beside the same status, exit code and reason as before",
      sent and sent.get("status") == "completed" and sent.get("exit_code") == 0 and "reason" in sent
      and sent["reason"] is None)
log = logs.take()
check("...and agent.log has exactly one summary line for the job",
      log.count("Install record: exe,") == 1)
check("...and says what the server made of it", "install record sent, server says stored" in log)
for label, result in (("failed", FAILED), ("timed out", TIMED_OUT)):
    sent = poll(result, Snapshots(snap(*BASE), snap(*BASE, NPP)), NEW_SERVER)
    check(f"{label}: the report carries no record", sent is not None and "install_record" not in sent)
    check(f"{label}: ...and the same fields as before", sent.get("status") == "failed"
          and "exit_code" in sent and sent["exit_code"] == result.exit_code and sent.get("reason") == result.reason)
    check(f"{label}: one summary line still", logs.take().count("Install record: exe,") == 1)
sent = poll(OK, Snapshots(snap(*BASE), snap(*BASE, NPP)), OLD_SERVER)
log = logs.take()
check("a server older than install records: the report still goes out, nothing breaks",
      sent is not None and "Reported status: completed" in log)
check("...and the log says the server said nothing about the record", "server says nothing" in log)
sent = poll(OK, Snapshots(snap(*BASE), none_from=0), NEW_SERVER)
check("no snapshot possible: the report goes out without a record, unchanged",
      sent is not None and "install_record" not in sent and sent["status"] == "completed")
check("...and the summary says the keys could not be read before the install",
      "none - the Uninstall keys could not be read before the install" in logs.take())

real_run_install = agent.run_install_command


def wrongly_recorded(command, software_id=None, recorder=None):
    # As if a bug left a record behind on a recorder that has already
    # finished - so nothing but poll_once's own rule can keep it out.
    recorder.record = {"method": "exe", "unavailable_reason": "no_new_entry"}
    recorder.summarised = True
    return FAILED


posts.clear()
reply.clear()
agent.post_json = fake_post
agent.run_install_command = wrongly_recorded
try:
    agent.poll_once()
finally:
    agent.run_install_command = real_run_install
    restore()
report = [p for path, p in posts if path.endswith("/report")]
check("a failed install never sends a record, even one left on the recorder",
      report and "install_record" not in report[0])

real_install = agent.run_install_command


def stopped_early_post(path, payload):
    if path.endswith("poll"):
        return {"job": {"id": 9, "software_name": "X", "install_command": "x"}}
    return {}


agent.post_json = stopped_early_post
agent.run_install_command = lambda command, software_id=None, recorder=None: FAILED
logs.take()
try:
    agent.poll_once()
finally:
    agent.run_install_command = real_install
    restore()
check("a job that stopped before its install began still gets its one summary line",
      "Install record: unknown, none - the install did not complete" in logs.take())


# ---------------------------------------------------------------------------
print("6. The new agent and the real server, end to end")
# ---------------------------------------------------------------------------
server_dir = os.path.join(work, "server")
shutil.copytree(SERVER_SRC, server_dir, ignore=shutil.ignore_patterns(
    "appease.db*", "__pycache__", "*.pyc", ".env", "uploads", "staging", "report_photos", "avatars", "venv"))
sys.path.insert(0, server_dir)
here = os.getcwd()
os.chdir(server_dir)
try:
    import database  # noqa: E402
    database.DB_PATH = os.path.join(server_dir, "appease.db")
    database.init_db()
    os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
    import app as appmod  # noqa: E402
finally:
    os.chdir(here)
appmod.app.config["TESTING"] = True
client = appmod.app.test_client()


def via_client(path, payload):
    return client.post(path, json=payload).get_json() or {}


conn = sqlite3.connect(database.DB_PATH)
software_id = conn.execute("INSERT INTO software (name, version, type, install_command) VALUES "
                           "('Notepad++', '8.7.1', 'offline', '\"uploads/npp.exe\" /S')").lastrowid
conn.commit()
conn.close()
via_client("/api/agent/poll", agent.identity())          # registers this machine
conn = sqlite3.connect(database.DB_PATH)
conn.execute("INSERT INTO jobs (computer_hostname, software_id, status, software_name, software_version) "
             "VALUES (?, ?, 'pending', 'Notepad++', '8.7.1')", (agent.COMPUTER_HOSTNAME, software_id))
conn.commit()
conn.close()
stand_ins(Snapshots(snap(*BASE), snap(*BASE, NPP)))
agent.run_and_classify = fake_run(OK)
agent.post_json = via_client
logs.take()
try:
    agent.poll_once()
finally:
    restore()
conn = sqlite3.connect(database.DB_PATH)
conn.row_factory = sqlite3.Row
row = conn.execute("SELECT * FROM installations").fetchone()
job_row = conn.execute("SELECT status FROM jobs WHERE software_id = ?", (software_id,)).fetchone()
conn.close()
check("the real server stores the record the agent sent", row is not None and row["reg_key_name"] == "Notepad++"
      and row["display_version"] == "8.7.1" and row["state"] == "installed")
check("...and the job completed as it always would", job_row["status"] == "completed")
check("...and the agent logs that the server stored it", "server says stored" in logs.take())


# ---------------------------------------------------------------------------
print("7. Settling inside a real job object (Windows)")
# ---------------------------------------------------------------------------
agent.request.urlopen = real_urlopen
if os.name != "nt":
    skip("section 7, a real job object", "Windows only - Job Objects do not exist on this platform")
else:
    python = getattr(sys, "_base_executable", None) or sys.executable
    lingering = os.path.join(work, "linger.py")
    with open(lingering, "w", encoding="utf-8") as handle:
        handle.write("import subprocess, sys\n"
                     "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(4)'])\n")
    counts = []
    command = f'"{python}" "{lingering}"'
    result = agent.run_and_classify(command, command, real_time.time() + 60,
                                    on_success=lambda running: counts.append(running()))
    check("a real install: completed, and the hook was called once", result.ok and len(counts) == 1)
    check("...while its job object was still open, seeing the process the install left running",
          counts and isinstance(counts[0], int) and counts[0] >= 1)
    failing = f'"{python}" -c "import sys; sys.exit(1603)"'
    called = []
    result = agent.run_and_classify(failing, failing, real_time.time() + 60, on_success=lambda r: called.append(1))
    check("a failed install: the hook is never called", not result.ok and called == [])
    try:
        result = agent.run_and_classify(command, command, real_time.time() + 60, on_success=explode)
        kept = result.ok and result.exit_code == 0
    except Exception:  # noqa: BLE001 - raising IS the failure
        kept = False
    check("a hook that raises inside a real install leaves the result as it was", kept)


print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for label in failed:
    print("  -", label)
if skipped:
    print(f"SKIPPED: {len(skipped)}")
    for label in skipped:
        print("  -", label)
print("=" * 56)
for handler in list(agent.logger.handlers):
    handler.close()
os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

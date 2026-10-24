"""The agent's MSI uninstall, on fakes for the registry, msiexec and the
clock: nothing runs unless every check passes, msiexec /x runs by full path
with no shell, and an exception anywhere is agent_error, never a removal.
"""
import functools
import logging
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
skipped = []


def check(label, condition):
    global passed
    if condition:
        passed += 1
    else:
        failed.append(label)
        print(f"  FAIL: {label}")


work = tempfile.mkdtemp(prefix="appease_unmsi_")
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

if not hasattr(agent, "uninstall_msi"):
    print("COULD NOT RUN - agent.py predates the MSI uninstall.")
    sys.exit(3)

HKLM, HKCU = agent.HKLM_NAME, agent.HKCU_NAME
PRESENT, ABSENT, UNKNOWN = agent.SIGNAL_PRESENT, agent.SIGNAL_ABSENT, agent.SIGNAL_UNKNOWN
SID = "S-1-5-18"
GUID = "{23170F69-40C1-2702-2408-000001000000}"
FOLDERS = [agent._folder_key("C:\\Appease")]


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


class FakeRegistry:
    def __init__(self, keys=(), names=(), dependents=(), boom=None):
        self.keys = {(h, (s or "").upper() if h == HKCU else None, n.upper(), v) for h, s, n, v in keys}
        self.names = None if names is None else set(names)
        self.dependents = None if dependents is None else set(dependents)
        self.boom = boom or set()

    def remove_all(self):
        self.keys.clear()
        self.names = set()

    def uninstall_key(self, hive, sid, name, view):
        if "uninstall_key" in self.boom:
            raise RuntimeError("uninstall_key exploded")
        if hive == HKCU and (sid or "").upper() != SID:
            return UNKNOWN
        key = (hive, (sid or "").upper() if hive == HKCU else None, name.upper(), view)
        return PRESENT if key in self.keys else ABSENT

    def listed_display_names(self, required_sids):
        return None if self.names is None else set(self.names)

    def installer_dependents(self, product_code):
        if "installer_dependents" in self.boom:
            raise RuntimeError("installer_dependents exploded")
        return None if self.dependents is None else set(self.dependents)


def entry(display="7-Zip 24.08 (x64 edition)", location="C:\\Program Files\\7-Zip\\", publisher="Igor Pavlov",
          key=GUID, msi=True, hive=HKLM, view="Registry64", sid=None):
    values = {"displayname": display, "installlocation": location, "publisher": publisher}
    if msi:
        values["windowsinstaller"] = 1
    return agent.uninstall_entry(hive, sid, view, key, values)


def snapshot(*entries):
    return {"entries": {agent.entry_identity(e): e for e in entries}, "user_readable": True}


def installed(**kwargs):
    """A registry and a snapshot where 7-Zip 24.08 is installed
    machine-wide."""
    e = entry(**kwargs)
    registry = FakeRegistry(keys=[(e["hive"], e["sid"], e["key_name"], e["view"])], names=[e["display_name"]])
    return registry, snapshot(e)


class Runner:
    """Stands in for run_uninstall_process: records the argv, does what
    msiexec would to the fake registry, and returns an exit code."""

    def __init__(self, registry, code=0, removes=True, timed_out=False, whole=True, raises=None):
        self.registry, self.code, self.removes = registry, code, removes
        self.timed_out, self.whole, self.raises = timed_out, whole, raises
        self.argv = []

    def __call__(self, argv):
        self.argv.append(argv)
        if self.raises is not None:
            raise self.raises
        if self.removes:
            self.registry.remove_all()
        return (None if self.timed_out else self.code), self.timed_out, self.whole


class Clock:
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def uninstall(registry, snap, runner, product_code=GUID, folder_signal=None, sid=SID, read=None, **overrides):
    clock = Clock()
    signal = folder_signal or (lambda folder: agent.SIGNAL_ABSENT)

    def verify(target, code):
        return agent.verify_removal(target, code, registry, clock.time, clock.sleep, lambda: None, signal)

    kwargs = dict(registry=registry, read=read or (lambda s: snap), user_sid=lambda: sid, run=runner,
                  verify=verify, folders=FOLDERS)
    kwargs.update(overrides)
    return agent.uninstall_msi({"id": 7, "action": "uninstall", "method": "msi", "product_code": product_code},
                               **kwargs)


def result(outcome):
    return outcome.ok, outcome.exit_code, outcome.reason


# ---------------------------------------------------------------------------
print("1. Refusals: nothing runs")
# ---------------------------------------------------------------------------
for label, code in (("no braces", GUID.strip("{}")), ("a short group", "{23170F69-40C1-2702-2408-00000100000}"),
                    ("a command after it", GUID + " & del x"), ("not hex", "{23170F69-40C1-2702-2408-00000100000Z}"),
                    ("missing", None), ("a number", 7), ("blank", "")):
    registry, snap = installed()
    runner = Runner(registry)
    check(f"not a ProductCode ({label}): refused uninstall_job_invalid, nothing run",
          result(uninstall(registry, snap, runner, product_code=code)) == (False, None, "uninstall_job_invalid")
          and runner.argv == [])
registry, snap = installed()
runner = Runner(registry)
check("a ProductCode in lower case is still one", result(uninstall(registry, snap, runner, product_code=GUID.lower()))
      == (True, 0, None) and runner.argv[0][2] == GUID)

registry, snap = installed(msi=False)
runner = Runner(registry)
check("an entry under the ProductCode that is not a Windows Installer product: identity_mismatch",
      result(uninstall(registry, snap, runner)) == (False, None, "identity_mismatch") and runner.argv == [])
registry, snap = installed()
runner = Runner(registry)
check("Windows' program list cannot be read: registry_unreadable",
      result(uninstall(registry, None, runner, read=lambda s: None)) == (False, None, "registry_unreadable")
      and runner.argv == [])

own_python = f"Python {sys.version_info[0]}.{sys.version_info[1]}.0 (64-bit)"
for label, kwargs in (("the agent's own Python", {"display": own_python, "location": None}),
                      ("the Python Launcher", {"display": "Python Launcher", "location": None}),
                      ("anything in C:\\Appease", {"display": "Some Tool", "location": "C:\\Appease\\tool\\"})):
    registry, snap = installed(**kwargs)
    runner = Runner(registry)
    check(f"protected - {label}: refused protected_software, nothing run",
          result(uninstall(registry, snap, runner)) == (False, None, "protected_software") and runner.argv == [])

registry, snap = installed()
registry.dependents = {"{B0E5A7C1-1111-2222-3333-444444444444}"}
runner = Runner(registry)
check("another product depends on it (Dependents): shared_runtime, nothing run",
      result(uninstall(registry, snap, runner)) == (False, None, "shared_runtime") and runner.argv == [])
registry, snap = installed()
registry.dependents = None
runner = Runner(registry)
check("the dependency records cannot be read: registry_unreadable, nothing run",
      result(uninstall(registry, snap, runner)) == (False, None, "registry_unreadable") and runner.argv == [])
VC = "Microsoft Visual C++ 2015-2022 Redistributable (x64) - 14.44.35211"
registry, snap = installed(display=VC, publisher="Microsoft Corporation")
runner = Runner(registry)
check("publisher Microsoft Corporation and a runtime family name: shared_runtime, nothing run",
      result(uninstall(registry, snap, runner)) == (False, None, "shared_runtime") and runner.argv == [])
registry, snap = installed(display=VC, publisher="Some Repackager")
check("...the same name from another publisher is not refused by name",
      result(uninstall(registry, snap, Runner(registry))) == (True, 0, None))
registry, snap = installed(display="Microsoft Teams", publisher="Microsoft Corporation")
check("...nor Microsoft's own non-runtime software", result(uninstall(registry, snap, Runner(registry))) == (True, 0, None))

# ---------------------------------------------------------------------------
print("2. Already gone")
# ---------------------------------------------------------------------------
registry = FakeRegistry(names=["Other"])
runner = Runner(registry)
check("nothing under the ProductCode anywhere: already_removed, nothing run",
      result(uninstall(registry, snapshot(), runner)) == (True, None, "already_removed") and runner.argv == [])
registry = FakeRegistry(keys=[(HKCU, SID, GUID, "Registry64")], names=[])
runner = Runner(registry)
check("still in the agent account's own list: not gone, so it runs",
      result(uninstall(registry, snapshot(), runner)) == (True, 0, None) and len(runner.argv) == 1)

# ---------------------------------------------------------------------------
print("3. msiexec, and each exit code")
# ---------------------------------------------------------------------------
registry, snap = installed()
runner = Runner(registry)
uninstall(registry, snap, runner)
system_root = os.environ.get("SystemRoot") or "C:\\Windows"
check("msiexec /x {GUID} /qn /norestart, by its full path",
      runner.argv == [[os.path.join(system_root, "System32", "msiexec.exe"), "/x", GUID, "/qn", "/norestart"]])

CASES = [
    ("0, gone", 0, True, {}, (True, 0, None)),
    ("0, still listed after 120 s", 0, False, {}, (False, 0, "still_listed")),
    ("3010, gone", 3010, True, {}, (True, 3010, "removed_restart_needed")),
    ("3010, still listed", 3010, False, {}, (False, 3010, "still_listed")),
    ("1605, gone", 1605, True, {}, (True, 1605, "already_removed")),
    ("1605, but Windows still lists it", 1605, False, {}, (False, 1605, "still_listed")),
    ("0, gone but its folder left", 0, True, {"folder": True}, (True, 0, "removed_folder_left")),
    ("0, gone but the list cannot be read", 0, True, {"unreadable": True}, (True, 0, "removal_unverified")),
    ("1641", 1641, False, {}, (True, 1641, "restart_started")),
    ("1602", 1602, False, {}, (False, 1602, "msi_cancelled")),
    ("1603", 1603, False, {}, (False, 1603, "msi_fatal")),
    ("1618", 1618, False, {}, (False, 1618, "msi_busy")),
    ("1625", 1625, False, {}, (False, 1625, "msi_policy")),
    ("1 (undocumented)", 1, False, {}, (False, 1, "installer_exit_code")),
    ("1619 (an install's code, not an uninstall's)", 1619, False, {}, (False, 1619, "installer_exit_code")),
]
for label, code, removes, extra, expected in CASES:
    registry, snap = installed()
    runner = Runner(registry, code=code, removes=removes)
    if extra.get("unreadable"):
        original = runner.__call__

        def blinding(argv, original=original, registry=registry):
            out = original(argv)
            registry.names = None
            return out
        runner = blinding
    signal = (lambda folder: agent.SIGNAL_PRESENT) if extra.get("folder") else None
    check(f"exit {label}: {expected}", result(uninstall(registry, snap, runner, folder_signal=signal)) == expected)


class Watching(FakeRegistry):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.looked = 0

    def uninstall_key(self, *args):
        self.looked += 1
        return super().uninstall_key(*args)


for code in (1641, 1602, 1603):
    e = entry()
    registry = Watching(keys=[(HKLM, None, GUID, "Registry64")], names=[e["display_name"]])
    before_runs = registry.looked
    uninstall(registry, snapshot(e), Runner(registry, code=code, removes=False))
    check(f"exit {code} is reported as it is, without waiting to verify", registry.looked <= before_runs + 4)

registry, snap = installed()
check("a timeout: failed timed_out, no exit code",
      result(uninstall(registry, snap, Runner(registry, timed_out=True))) == (False, None, "timed_out"))
registry, snap = installed()
check("msiexec cannot be started: failed launch_failed",
      result(uninstall(registry, snap, Runner(registry, raises=OSError("no msiexec")))) == (False, None, "launch_failed"))

# ---------------------------------------------------------------------------
print("4. The process: no shell, its own job, the whole tree ended on timeout")
# ---------------------------------------------------------------------------


class FakeProcess:
    def __init__(self, finishes_at=None, code=0, clock=None):
        self.finishes_at, self.code, self.clock = finishes_at, code, clock
        self.killed = False
        self.returncode = None

    def poll(self):
        if self.killed or (self.finishes_at is not None and self.clock.now >= self.finishes_at):
            self.returncode = 1 if self.killed else self.code
            return self.returncode
        return None

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True


class FakeJob:
    def __init__(self, process):
        self.process, self.terminated, self.closed = process, False, False

    def terminate(self):
        self.terminated = True
        self.process.killed = True

    def close(self):
        self.closed = True


def run_process(finishes_at, with_job=True, code=0):
    clock, beats, started = Clock(), [], []
    process = FakeProcess(finishes_at, code, clock)
    job = FakeJob(process) if with_job else None

    def start(argv, stdout, shell=True):
        started.append((argv, stdout, shell))
        return process, job

    out = agent.run_uninstall_process(["msiexec", "/x", GUID], start, clock.time, clock.sleep,
                                      lambda: beats.append(clock.now))
    return out, started, job, beats, clock


out, started, job, beats, clock = run_process(25, code=0)
check("a normal run returns msiexec's own exit code", out == (0, False, False))
check("...started with no shell and no console output kept",
      started == [(["msiexec", "/x", GUID], agent.subprocess.DEVNULL, False)])
check("...its job closed afterwards", job.closed)
check("...with a heartbeat every 10 s while it runs", beats == [10, 20])
out, started, job, beats, clock = run_process(None)
check("past 10 minutes: timed out, and the whole job ended", out == (None, True, True) and job.terminated)
check("...at the 10-minute limit, not before", 600 < clock.now <= 602)
check("...heartbeats all the way", len(beats) == 60)
out, started, job, beats, clock = run_process(None, with_job=False)
check("with no job, only msiexec itself is ended, and it says so", out == (None, True, False))
check("the limit is 10 minutes", agent.UNINSTALL_TIMEOUT_SECONDS == 600)

# ---------------------------------------------------------------------------
print("5. Errors: never a removal, never a crash")
# ---------------------------------------------------------------------------


def boom(*args, **kwargs):
    raise RuntimeError("stage exploded")


def with_handler(**overrides):
    """UNINSTALLERS["msi"] bound to fakes, as the poll would call it."""
    registry, snap = installed()
    defaults = dict(registry=registry, read=lambda s: snap, user_sid=lambda: SID, run=Runner(registry),
                    folders=FOLDERS)
    defaults.update(overrides)
    if "verify" not in overrides:
        # Verification watches the registry the job uses, on a clock that
        # moves, so "never removed" ends at 120 s instead of looping.
        watched, clock = defaults["registry"], Clock()
        defaults["verify"] = lambda t, c: agent.verify_removal(t, c, watched, clock.time, clock.sleep,
                                                               lambda: None, lambda f: agent.SIGNAL_ABSENT)
    return functools.partial(agent.uninstall_msi, **defaults)


JOB = {"id": 7, "action": "uninstall", "method": "msi", "product_code": GUID}
real_msi = agent.UNINSTALLERS["msi"]
try:
    for stage, overrides in (
        ("asking which account the agent runs as", {"user_sid": boom}),
        ("reading Windows' program list", {"read": boom}),
        ("reading the dependency records", {"registry": FakeRegistry(boom={"installer_dependents"})}),
        ("running msiexec", {"run": Runner(FakeRegistry(), raises=RuntimeError("not an OSError"))}),
        ("verifying", {"verify": boom}),
        ("checking protected folders", {"folders": object()}),
    ):
        agent.UNINSTALLERS["msi"] = with_handler(**overrides)
        logs.take()
        outcome = agent.run_uninstall_job(JOB)
        lines = logs.take()
        check(f"an exception while {stage}: failed agent_error",
              (outcome.ok, outcome.reason, outcome.exit_code) == (False, "agent_error", None))
        check(f"...one summary line saying so ({stage})",
              len([l for l in lines if l.startswith("Uninstall summary:")]) == 1
              and any("agent_error" in l for l in lines if l.startswith("Uninstall summary:")))
    agent.UNINSTALLERS["msi"] = with_handler(registry=FakeRegistry(names=[], boom={"uninstall_key"}))
    outcome = agent.run_uninstall_job(JOB)
    check("a registry that raises while checking and verifying is never a removal",
          outcome.reason not in (None, "already_removed", "removed_folder_left", "removed_restart_needed"))
finally:
    agent.UNINSTALLERS["msi"] = real_msi
check("a method with no uninstaller: refused, nothing run",
      result(agent.run_uninstall_job(dict(JOB, method="msix"))) == (False, None, "uninstall_job_invalid"))
check("no method at all: refused", agent.run_uninstall_job({"id": 7, "action": "uninstall"}).reason
      == "uninstall_job_invalid")

sent = []
real_post = agent.post_json


def recording_post(path, payload):
    sent.append((path, payload))
    return {"reason": payload.get("reason")}


try:
    agent.post_json = recording_post
    agent.UNINSTALLERS["msi"] = with_handler(user_sid=boom)
    agent.handle_uninstall_job(JOB)
    check("the poll reports an agent_error as a failure",
          [(p, b["status"], b["reason"], b["exit_code"]) for p, b in sent] == [("/api/agent/jobs/7/report", "failed",
                                                                                "agent_error", None)])
    sent.clear()
    agent.handle_uninstall_job({"action": "uninstall", "method": "msi", "product_code": GUID})
    agent.handle_uninstall_job({"id": "7", "action": "uninstall", "method": "msi", "product_code": GUID})
    check("a job with no usable id is not run and not reported", sent == [])

    def unexpected(path, payload):
        raise TypeError("not a server error")       # ValueError is one: SERVER_ERRORS
    agent.post_json = unexpected
    agent.UNINSTALLERS["msi"] = with_handler()
    try:
        agent.handle_uninstall_job(JOB)
        survived = True
    except Exception:  # noqa: BLE001
        survived = False
    check("even an unexpected error while reporting never escapes into the poll loop", survived)

    agent.post_json = lambda path, payload: ({"job": JOB} if path == "/api/agent/poll" else recording_post(path, payload))
    sent.clear()
    agent.UNINSTALLERS["msi"] = with_handler()
    agent.poll_once()
    check("poll_once hands an uninstall job to the uninstall path and reports what it did",
          [(p, b["status"], b["exit_code"], b["reason"]) for p, b in sent] == [("/api/agent/jobs/7/report",
                                                                                "completed", 0, None)])
    logs.take()
    agent.post_json = lambda path, payload: ({"job": {"id": 9, "action": "uninstall"}} if path == "/api/agent/poll"
                                             else recording_post(path, payload))
    sent.clear()
    agent.poll_once()
    check("...an uninstall job with nothing in it is reported failed, never run",
          [(b["status"], b["reason"]) for _, b in sent] == [("failed", "uninstall_job_invalid")])
finally:
    agent.post_json = real_post
    agent.UNINSTALLERS["msi"] = real_msi

agent.UNINSTALLERS["msi"] = with_handler()
logs.take()
agent.run_uninstall_job(JOB)
summary = [l for l in logs.take() if l.startswith("Uninstall summary:")]
agent.UNINSTALLERS["msi"] = real_msi
check("one summary line: method, ProductCode, verdict, exit code, verification time",
      summary == [f"Uninstall summary: job 7, msi {GUID}: removed; exit code 0; verification 0s."])

# ---------------------------------------------------------------------------
print("6. The server: the codes, the capability, end to end, and the re-run after a restart")
# ---------------------------------------------------------------------------
import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()
import app as appmod  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402
appmod.app.config["TESTING"] = True
client = appmod.app.test_client()

check("the agent's runtime families are exactly the server's (pattern and flags)",
      (agent.SHARED_RUNTIME_NAME.pattern, agent.SHARED_RUNTIME_NAME.flags)
      == (appmod.SHARED_RUNTIME_NAME.pattern, appmod.SHARED_RUNTIME_NAME.flags))
check("the agent advertises uninstall:msi (beside choco since step 10, exe since Phase 3a, exe-user since 2026-10-07)",
      agent.agent_capabilities() == ["uninstall:choco", "uninstall:exe", "uninstall:exe-user", "uninstall:msi"])


def db():
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


conn = db()
conn.execute("UPDATE admins SET password_hash = ?, must_change_password = 0 WHERE username = 'admin'",
             (generate_password_hash("Str0ng!Passw0rd"),))
conn.commit()
pc = conn.execute("INSERT INTO computers (hostname, status) VALUES ('LAB-U', 'online')").lastrowid
conn.commit()
SHAPES = [((False, None, r), None) for r in agent.UNINSTALL_REFUSAL_REASONS]
SHAPES += [((False, None, "timed_out"), None), ((False, None, "launch_failed"), None),
           ((True, 1641, "restart_started"), 1641), ((False, 1602, "msi_cancelled"), 1602),
           ((False, 1603, "msi_fatal"), 1603), ((False, 1618, "msi_busy"), 1618), ((False, 1625, "msi_policy"), 1625),
           ((False, 1, "installer_exit_code"), 1), ((False, 1619, "installer_exit_code"), 1619),
           ((False, 1605, "still_listed"), 1605), ((False, 3010, "still_listed"), 3010),
           ((True, 1605, "removal_unverified"), 1605), ((True, 3010, "removal_unverified"), 3010),
           ((True, 1605, "already_removed"), 1605)]
mismatches = []
for (ok, exit_code, reason), _ in SHAPES:
    row = conn.execute("INSERT INTO installations (computer_id, software_name, method, record_key, state) "
                       "VALUES (?, 'X', 'msi', ?, 'installed')", (pc, secrets.token_hex(8))).lastrowid
    job = conn.execute("INSERT INTO jobs (computer_hostname, software_id, status, action, installation_id, started_at) "
                       "VALUES ('LAB-U', 0, 'in_progress', 'uninstall', ?, CURRENT_TIMESTAMP)", (row,)).lastrowid
    conn.commit()
    body = {"computer_hostname": "LAB-U", "status": "completed" if ok else "failed", "exit_code": exit_code,
            "reason": reason}
    client.post(f"/api/agent/jobs/{job}/report", json=body)
    kept = tuple(conn.execute("SELECT status, exit_code, result_reason FROM jobs WHERE id = ?", (job,)).fetchone())
    if kept != (body["status"], exit_code, reason):
        mismatches.append((reason, exit_code, kept))
check(f"every report this uninstall can send is kept exactly as sent (mismatches: {mismatches})", not mismatches)
conn.close()

client.post("/login", data={"username": "admin", "password": "Str0ng!Passw0rd"})
agent.COMPUTER_HOSTNAME = "LAB-E2E"
agent.AGENT_ID = None
agent.post_json = lambda path, payload: client.post(path, json=payload).get_json()
try:
    agent.poll_once()                                     # registers the PC and its capability
    conn = db()
    caps = conn.execute("SELECT id, capabilities FROM computers WHERE hostname = 'LAB-E2E'").fetchone()
    check("the real poll registers the PC as able to uninstall MSI",
          "uninstall:msi" in (caps["capabilities"] or "").split())


    def installation(code, name):
        row = conn.execute(
            "INSERT INTO installations (computer_id, software_name, software_version, method, record_key, "
            "msi_product_code, state) VALUES (?, ?, '24.08', 'msi', ?, ?, 'installed')",
            (caps["id"], name, database.installation_record_key("msi", product_code=code), code)).lastrowid
        conn.commit()
        return row

    ROW = installation(GUID, "7-Zip")
    reply = client.post("/uninstall-batch", data={"installation_ids": [str(ROW)]}).get_json()
    check("the create route queues it", reply["ok"] and len(reply["created"]) == 1)
    registry, snap = installed()
    runner = Runner(registry)
    agent.UNINSTALLERS["msi"] = with_handler(registry=registry, read=lambda s: snap, run=runner)
    agent.poll_once()
    job = conn.execute("SELECT * FROM jobs WHERE installation_id = ?", (ROW,)).fetchone()
    row = conn.execute("SELECT * FROM installations WHERE id = ?", (ROW,)).fetchone()
    check("end to end: msiexec ran once for the ProductCode the server sent", [a[2] for a in runner.argv] == [GUID])
    check("...the job completed with no reason (a plain removal)",
          (job["status"], job["exit_code"], job["result_reason"]) == ("completed", 0, None))
    check("...and the installations row is removed by that job",
          (row["state"], row["state_reason"], row["uninstall_job_id"]) == ("removed", "removed", job["id"]))

    # A restart interrupts the uninstall: the agent dies with no report. The
    # server re-queues it once stale; the PC, back up, finds it gone.
    CODE2 = "{11112222-3333-4444-5555-666677778888}"
    ROW2 = installation(CODE2, "Old Tool")
    client.post("/uninstall-batch", data={"installation_ids": [str(ROW2)]})

    class Restarted(BaseException):
        pass

    registry, snap = installed(key=CODE2, display="Old Tool")
    runner = Runner(registry, raises=Restarted())
    agent.UNINSTALLERS["msi"] = with_handler(registry=registry, read=lambda s: snap, run=runner)
    try:
        agent.poll_once()
        died = False
    except Restarted:
        died = True
    job = conn.execute("SELECT * FROM jobs WHERE installation_id = ?", (ROW2,)).fetchone()
    check("(the restart killed the agent mid-uninstall; the job is left in progress)",
          died and job["status"] == "in_progress")
    conn.execute("UPDATE jobs SET started_at = DATETIME('now', '-21 minutes') WHERE id = ?", (job["id"],))
    conn.commit()
    registry.remove_all()                                  # the uninstall finished during the restart
    rerun = Runner(registry)
    agent.UNINSTALLERS["msi"] = with_handler(registry=registry, read=lambda s: snapshot(), run=rerun)
    agent.poll_once()
    job = conn.execute("SELECT * FROM jobs WHERE installation_id = ?", (ROW2,)).fetchone()
    row = conn.execute("SELECT * FROM installations WHERE id = ?", (ROW2,)).fetchone()
    check("the re-run is the same job, re-queued once", job["retry_count"] == 1)
    check("...it reaches already gone and runs nothing", rerun.argv == [])
    check("...reports already_removed", (job["status"], job["exit_code"], job["result_reason"])
          == ("completed", None, "already_removed"))
    check("...and the row ends removed, saying it was already gone",
          (row["state"], row["state_reason"]) == ("removed", "already_removed"))
    conn.close()
finally:
    agent.post_json = real_post
    agent.UNINSTALLERS["msi"] = real_msi

# ---------------------------------------------------------------------------
print("7. The real thing (Windows only)")
# ---------------------------------------------------------------------------
if agent.winreg is None:
    skipped.append("real msiexec and dependency records: not Windows")
    print("  skipped: not Windows")
else:
    nobody = "{00000000-0000-0000-0000-0000000000AB}"
    code, timed_out, whole = agent.run_uninstall_process(agent.msi_uninstall_argv(nobody), heartbeat=lambda: None)
    check(f"the real msiexec, asked to remove a product nobody has, says 1605 (got {code})",
          (code, timed_out) == (1605, False))
    found = agent.WindowsRegistry().installer_dependents(nobody)
    check("the real dependency records can be read, and nobody depends on a product nobody has", found == set())

print()
if skipped:
    print("skipped:", "; ".join(skipped))
print(f"{passed} passed, {len(failed)} failed")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

"""The agent's uninstall verification - the half that decides what
happened, on a fake registry and clock. "Already gone" only when every key
is gone from both views; a failed read is removal_unverified, never removed.
"""
import os
import secrets
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures", "choco")

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


work = tempfile.mkdtemp(prefix="appease_unverify_")
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

if not hasattr(agent, "verify_removal"):
    print("COULD NOT RUN - agent.py predates uninstall verification.")
    sys.exit(3)

HKLM, HKCU = agent.HKLM_NAME, agent.HKCU_NAME
PRESENT, ABSENT, UNKNOWN = agent.SIGNAL_PRESENT, agent.SIGNAL_ABSENT, agent.SIGNAL_UNKNOWN
SID = "S-1-5-18"
GUID = "{23170F69-40C1-2702-2408-000001000000}"


class FakeRegistry:
    """keys: {(hive, SID or None, NAME, view)} that exist. unknown: the same
    tuples that cannot be read. names: every DisplayName listed, or None when
    the list cannot be read. hives: the user hives that are loaded."""

    def __init__(self, keys=(), unknown=(), names=(), hives=(SID,), boom=False):
        self.keys = {self._k(*k) for k in keys}
        self.unknown = {self._k(*k) for k in unknown}
        self.names = None if names is None else set(names)
        self.hives = {h.casefold() for h in hives}
        self.boom = boom
        self.asked = []

    @staticmethod
    def _k(hive, sid, name, view):
        return hive, (sid or "").upper() if hive == HKCU else None, name.upper(), view

    def uninstall_key(self, hive, sid, name, view):
        if self.boom:
            raise RuntimeError("the registry went away")
        if hive == HKCU and (not sid or sid.casefold() not in self.hives):
            return UNKNOWN
        key = self._k(hive, sid, name, view)
        if key in self.unknown:
            return UNKNOWN
        return PRESENT if key in self.keys else ABSENT

    def listed_display_names(self, required_sids):
        self.asked.append(list(required_sids))
        if self.names is None or any(s.casefold() not in self.hives for s in required_sids):
            return None
        return set(self.names)


def snapshot_entry(hive, view, key_name, display, location=None, sid=None):
    values = {"displayname": display}
    if location:
        values["installlocation"] = location
    return agent.uninstall_entry(hive, sid, view, key_name, values)


def snapshot_of(*entries):
    return {"entries": {agent.entry_identity(e): e for e in entries}, "user_readable": True}


# ---------------------------------------------------------------------------
print("1. The MSI target")
# ---------------------------------------------------------------------------
before = snapshot_of(
    snapshot_entry(HKLM, "Registry64", GUID, "7-Zip 24.08 (x64 edition)", "C:\\Program Files\\7-Zip\\"),
    snapshot_entry(HKLM, "Registry64", "{11111111-0000-0000-0000-000000000000}", "Other App", "C:\\Other\\"),
    snapshot_entry(HKCU, "Registry64", GUID.lower(), "7-Zip per user", "C:\\Users\\x\\7z\\", sid=SID),
    snapshot_entry(HKCU, "Registry64", GUID, "Someone else's 7-Zip", "C:\\Users\\y\\", sid="S-1-5-21-9"),
)
target = agent.msi_uninstall_target(GUID.lower().strip("{}"), SID, before)
check("a ProductCode with no braces, in any case, becomes its key name",
      [k["name"] for k in target["keys"]] == [GUID, GUID])
check("its key is looked for in HKLM and in the agent account's own per-user list",
      [(k["hive"], k["sid"]) for k in target["keys"]] == [(HKLM, None), (HKCU, SID)])
check("names and folders come from its own entries only - not another product, not another user",
      target["names"] == {"7-Zip 24.08 (x64 edition)", "7-Zip per user"}
      and target["folders"] == {"C:\\Program Files\\7-Zip\\", "C:\\Users\\x\\7z\\"})
check("with no snapshot, the keys alone decide", agent.msi_uninstall_target(GUID, SID, None)["names"] == set())
for bad in ("", None, "7-Zip", "{23170F69-40C1-2702-2408-00000100000}", "{" + GUID + "}", GUID + "; del x"):
    check(f"not a ProductCode: {bad!r}", agent.msi_uninstall_target(bad, SID, before) is None)

# ---------------------------------------------------------------------------
print("2. The Chocolatey target")
# ---------------------------------------------------------------------------


def choco_root(package, version, registry_file=None, nuspec_file=None):
    root = tempfile.mkdtemp(prefix="choco_", dir=work)
    os.makedirs(os.path.join(root, "lib", package))
    shutil.copy(os.path.join(FIXTURES, nuspec_file or package + ".nuspec"),
                os.path.join(root, "lib", package, package + ".nuspec"))
    folder = os.path.join(root, ".chocolatey", f"{package}.{version}")
    os.makedirs(folder)
    if registry_file is not None:
        if isinstance(registry_file, bytes):
            open(os.path.join(folder, ".registry"), "wb").write(registry_file)
        else:
            shutil.copy(os.path.join(FIXTURES, registry_file), os.path.join(folder, ".registry"))
    return root


vlc_root = choco_root("vlc.install", "3.0.23", "vlc.install.3.0.23.registry")
vlc = agent.choco_uninstall_target("vlc.install", vlc_root)
check("vlc.install's real snapshot: its one key, machine-wide",
      vlc is not None and vlc["keys"] == [{"hive": HKLM, "sid": None, "name": "VLC media player"}])
check("...with its recorded name and folder",
      vlc["names"] == {"VLC media player"} and vlc["folders"] == {"C:\\Program Files\\VideoLAN\\VLC"})
gimp_user, gimp_keys = agent.read_snapshot(os.path.join(FIXTURES, "gimp.3.0.6.1.registry"))
check("(the gimp fixture is a per-user install)", {k["hive"] for k in gimp_keys} == {HKCU})
gimp_root = choco_root("vlc.install", "3.0.23", "gimp.3.0.6.1.registry")
gimp = agent.choco_uninstall_target("vlc.install", gimp_root)
check("a per-user snapshot's keys carry the SID that recorded them",
      gimp is not None and all(k["hive"] == HKCU and k["sid"] == gimp_user for k in gimp["keys"]))
check("no .registry at all: nothing to check by",
      agent.choco_uninstall_target("vlc.install", choco_root("vlc.install", "3.0.23")) is None)
check("a .registry that cannot be read: nothing to check by",
      agent.choco_uninstall_target("vlc.install", choco_root("vlc.install", "3.0.23", b"garbage")) is None)
empty = (b'<?xml version="1.0" encoding="utf-8"?>\r\n<registrySnapshot>\r\n  <user>S-1-5-18</user>\r\n'
         b"  <keys>\r\n  </keys>\r\n</registrySnapshot>")
check("a snapshot that recorded no key: nothing to check by",
      agent.choco_uninstall_target("vlc.install", choco_root("vlc.install", "3.0.23", empty)) is None)
check("the snapshot of another version is not this one's",
      agent.choco_uninstall_target("vlc.install", choco_root("vlc.install", "3.0.22", "vlc.install.3.0.23.registry"))
      is None)
check("a package id that is not a plain one is never looked up",
      agent.choco_uninstall_target("..\\vlc.install", vlc_root) is None
      and agent.choco_uninstall_target("vlc.install;x", vlc_root) is None)
check("a nuspec naming another package is not this one's",
      agent.choco_uninstall_target("vlc.install", choco_root("vlc.install", "3.0.23", "vlc.install.3.0.23.registry",
                                                            nuspec_file="paint.net.nuspec")) is None)

# ---------------------------------------------------------------------------
print("3. Already gone?")
# ---------------------------------------------------------------------------
msi = agent.msi_uninstall_target(GUID, SID, before)
check("every key gone from both views: already gone", agent.already_gone(msi, FakeRegistry()) is True)
check("there in the 32-bit view only: not gone",
      agent.already_gone(msi, FakeRegistry(keys=[(HKLM, None, GUID, "Registry32")])) is False)
check("there in the agent account's own list only: not gone",
      agent.already_gone(msi, FakeRegistry(keys=[(HKCU, SID, GUID, "Registry64")])) is False)
check("one view cannot be read: cannot tell",
      agent.already_gone(msi, FakeRegistry(unknown=[(HKLM, None, GUID, "Registry64")])) is None)
check("the agent account's hive is not loaded: cannot tell", agent.already_gone(msi, FakeRegistry(hives=())) is None)
check("no SID for the agent account: cannot tell",
      agent.already_gone(agent.msi_uninstall_target(GUID, None, before), FakeRegistry()) is None)
check("one read raises: cannot tell", agent.already_gone(msi, FakeRegistry(boom=True)) is None)
check("no target: cannot tell", agent.already_gone(None, FakeRegistry()) is None)
check("a Chocolatey target is gone when its recorded key is", agent.already_gone(vlc, FakeRegistry()) is True
      and agent.already_gone(vlc, FakeRegistry(keys=[(HKLM, None, "vlc media player", "Registry64")])) is False)

# ---------------------------------------------------------------------------
print("4. The verdict")
# ---------------------------------------------------------------------------
NAMES_LEFT = ["Notepad++ (64-bit x64)", "7-Zip 26.03 (x64)"]
FOLDERS = {"C:\\Program Files\\7-Zip\\": PRESENT}


def verdict(registry, exit_code=0, folders=None, target=msi):
    lookup = folders or {}
    return agent.removal_verdict(target, exit_code, registry, lambda f: lookup.get(f, agent.SIGNAL_ABSENT))


gone = FakeRegistry(names=NAMES_LEFT)
check("nothing left: removed", verdict(gone) == agent.VERDICT_REMOVED)
check("...and the name list asked for with the agent account's hive",
      gone.asked == [[SID]])
check("a key still there: still_listed",
      verdict(FakeRegistry(keys=[(HKLM, None, GUID, "Registry32")], names=NAMES_LEFT)) == agent.REASON_STILL_LISTED)
check("keys gone, but an entry with an exact recorded name: still_listed",
      verdict(FakeRegistry(names=NAMES_LEFT + ["7-Zip 24.08 (x64 edition)"])) == agent.REASON_STILL_LISTED)
check("...ignoring case and outer spaces",
      verdict(FakeRegistry(names=["  7-ZIP 24.08 (X64 EDITION) "])) == agent.REASON_STILL_LISTED)
check("a similar name is not the same name (7-Zip 26.03 left behind is not 24.08)",
      verdict(FakeRegistry(names=["7-Zip 26.03 (x64 edition)", "7-Zip"])) == agent.VERDICT_REMOVED)
check("a key that cannot be read: removal_unverified",
      verdict(FakeRegistry(unknown=[(HKLM, None, GUID, "Registry64")], names=NAMES_LEFT))
      == agent.REASON_REMOVAL_UNVERIFIED)
check("...even when the name list says it is gone",
      verdict(FakeRegistry(unknown=[(HKLM, None, GUID, "Registry32")], names=[])) == agent.REASON_REMOVAL_UNVERIFIED)
check("the name list cannot be read: removal_unverified",
      verdict(FakeRegistry(names=None)) == agent.REASON_REMOVAL_UNVERIFIED)
check("a recorded user's hive is not loaded: removal_unverified",
      verdict(FakeRegistry(names=NAMES_LEFT, hives=())) == agent.REASON_REMOVAL_UNVERIFIED)
check("a read raises: removal_unverified, never removed",
      verdict(FakeRegistry(boom=True)) == agent.REASON_REMOVAL_UNVERIFIED)
check("no target: removal_unverified", verdict(gone, target=None) == agent.REASON_REMOVAL_UNVERIFIED)
check("removed, and the uninstaller said 3010: removed_restart_needed",
      verdict(gone, 3010) == agent.REASON_REMOVED_RESTART_NEEDED)
check("...still listed after a 3010 stays still_listed",
      verdict(FakeRegistry(keys=[(HKLM, None, GUID, "Registry64")], names=[]), 3010) == agent.REASON_STILL_LISTED)
check("removed, but its install folder is still there: removed_folder_left",
      verdict(gone, 0, FOLDERS) == agent.REASON_REMOVED_FOLDER_LEFT)
check("...a 3010 says more than the folder: removed_restart_needed",
      verdict(gone, 3010, FOLDERS) == agent.REASON_REMOVED_RESTART_NEEDED)
for signal in (agent.SIGNAL_ABSENT, agent.SIGNAL_UNKNOWN, agent.SIGNAL_ABSTAIN):
    check(f"an install folder that is {signal}: removed",
          verdict(gone, 0, {"C:\\Program Files\\7-Zip\\": signal}) == agent.VERDICT_REMOVED)
check("a 1641 is not 3010: removed", verdict(gone, 1641) == agent.VERDICT_REMOVED)
no_names = agent.uninstall_target(msi["keys"])
check("a target with no recorded names never asks for the list", verdict(FakeRegistry(names=None), target=no_names)
      == agent.VERDICT_REMOVED)
check("Chocolatey: the recorded key and name gone, the folder too: removed",
      verdict(FakeRegistry(names=NAMES_LEFT), target=vlc) == agent.VERDICT_REMOVED)
check("a per-user Chocolatey key: the name list is asked for with its SID",
      verdict(FakeRegistry(names=[], hives=(gimp_user,)), target=gimp) == agent.VERDICT_REMOVED
      and verdict(FakeRegistry(names=[], hives=(SID,)), target=gimp) == agent.REASON_REMOVAL_UNVERIFIED)

# ---------------------------------------------------------------------------
print("5. Checked at once, then every 5 s for up to 120 s")
# ---------------------------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        assert seconds > 0, seconds
        self.slept.append(seconds)
        self.now += seconds


class Changing(FakeRegistry):
    """Listed until the given check, then gone."""

    def __init__(self, gone_at, after=None, **kwargs):
        super().__init__(**kwargs)
        self.gone_at, self.after, self.checks = gone_at, after, 0

    def uninstall_key(self, hive, sid, name, view):
        if hive == HKLM and view == "Registry64":
            self.checks += 1
        if self.gone_at is not None and self.checks >= self.gone_at:
            return self.after or ABSENT
        return PRESENT


def run(registry, exit_code=0):
    clock, beats = Clock(), []
    result = agent.verify_removal(msi, exit_code, registry, clock.time, clock.sleep, lambda: beats.append(clock.now),
                                  lambda f: agent.SIGNAL_ABSENT)
    return result, clock.slept, beats


(result, waited), slept, beats = run(FakeRegistry(names=[]))
check("removed at the first check: no wait, no heartbeat", (result, waited, slept, beats)
      == (agent.VERDICT_REMOVED, 0, [], []))
(result, waited), slept, beats = run(Changing(3, names=[]))
check("removed at the third check: stops there", result == agent.VERDICT_REMOVED and slept == [5, 5] and waited == 10)
check("...with a heartbeat after every wait", beats == [1005.0, 1010.0])
(result, waited), slept, beats = run(Changing(None, names=[]))
check("never removed: still_listed after 120 s", result == agent.REASON_STILL_LISTED and waited == 120)
check("...checked 25 times, 5 s apart", slept == [5] * 24 and len(beats) == 24)
(result, waited), slept, beats = run(Changing(25, after=UNKNOWN, names=[]))
check("the last check could not read: removal_unverified, not the earlier still_listed",
      result == agent.REASON_REMOVAL_UNVERIFIED and waited == 120)
(result, waited), slept, beats = run(Changing(2, names=[]), exit_code=3010)
check("a restart verdict also ends the wait", result == agent.REASON_REMOVED_RESTART_NEEDED and slept == [5])
check("the window and the step are 120 s and 5 s",
      (agent.UNINSTALL_VERIFY_MAX_SECONDS, agent.UNINSTALL_VERIFY_CHECK_SECONDS) == (120, 5))


class SlowFirst(Clock):
    """The first check takes 2 s, so the checks land at 2, 7, ... 117 s and
    the last wait has only 3 s left to fill."""

    def __init__(self):
        super().__init__()
        self.calls = 0

    def time(self):
        self.calls += 1
        if self.calls == 2:
            self.now += 2
        return self.now


clock = SlowFirst()
result, waited = agent.verify_removal(msi, 0, Changing(None, names=[]), clock.time, clock.sleep, lambda: None,
                                      lambda f: agent.SIGNAL_ABSENT)
check("the last wait is cut to what is left, so the window ends at exactly 120 s",
      waited == 120 and clock.slept[-1] == 3 and clock.slept[:-1] == [5] * 23)

# ---------------------------------------------------------------------------
print("6. The report: every verdict kept exactly as sent")
# ---------------------------------------------------------------------------
REPORTS = {
    agent.VERDICT_REMOVED: (True, None),
    agent.REASON_REMOVED_RESTART_NEEDED: (True, "removed_restart_needed"),
    agent.REASON_REMOVED_FOLDER_LEFT: (True, "removed_folder_left"),
    agent.REASON_STILL_LISTED: (False, "still_listed"),
    agent.REASON_REMOVAL_UNVERIFIED: (True, "removal_unverified"),
}
for name, expected in REPORTS.items():
    check(f"{name} reports as {expected}", agent.verdict_report(name) == expected)
check("the verification's reason codes are exactly these, plus already_removed",
      set(agent.UNINSTALL_VERDICT_REASONS) == {r for _, r in REPORTS.values() if r} | {"already_removed"})

import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()
import app as appmod  # noqa: E402
appmod.app.config["TESTING"] = True
client = appmod.app.test_client()
conn = sqlite3.connect(database.DB_PATH)
pc = conn.execute("INSERT INTO computers (hostname, status) VALUES ('LAB-V', 'online')").lastrowid
conn.commit()
EXIT_FOR = {agent.REASON_REMOVED_RESTART_NEEDED: 3010}
shapes = [(agent.verdict_report(v), EXIT_FOR.get(v, 0)) for v in REPORTS]
shapes.append(((True, agent.REASON_ALREADY_REMOVED), None))       # nothing ran
shapes.append(((True, agent.REASON_ALREADY_REMOVED), 1605))      # msiexec: no such product
mismatches = []
for (ok, reason), exit_code in shapes:
    row = conn.execute("INSERT INTO installations (computer_id, software_name, method, record_key, state) "
                       "VALUES (?, 'X', 'msi', ?, 'installed')", (pc, secrets.token_hex(8))).lastrowid
    job = conn.execute("INSERT INTO jobs (computer_hostname, software_id, status, action, installation_id, started_at) "
                       "VALUES ('LAB-V', 0, 'in_progress', 'uninstall', ?, CURRENT_TIMESTAMP)", (row,)).lastrowid
    conn.commit()
    body = {"computer_hostname": "LAB-V", "status": "completed" if ok else "failed", "exit_code": exit_code,
            "reason": reason}
    res = client.post(f"/api/agent/jobs/{job}/report", json=body)
    kept = conn.execute("SELECT status, exit_code, result_reason FROM jobs WHERE id = ?", (job,)).fetchone()
    if res.status_code != 200 or kept != (body["status"], exit_code, reason):
        mismatches.append((reason, exit_code, kept))
check(f"every verdict, sent as the agent sends it, is kept exactly (mismatches: {mismatches})", not mismatches)
conn.close()

# ---------------------------------------------------------------------------
print("7. The real registry (Windows only)")
# ---------------------------------------------------------------------------
if agent.winreg is None:
    skipped.append("the real registry: not Windows")
    print("  skipped: not Windows")
else:
    real = agent.WindowsRegistry()
    exact = real.listed_display_names([])
    loose = real.display_names([])
    check("the real exact names are text, as written", exact is not None and all(isinstance(n, str) and n == n.strip()
                                                                               for n in exact))
    check("...and fold to exactly the loose names", loose == {k for k in map(agent.app_name_key, exact) if k})
    check("...not folded already: real names keep their capitals", any(n != n.casefold() for n in exact))
    nowhere = agent.uninstall_target([{"hive": HKLM, "sid": None, "name": "{00000000-0000-0000-0000-0000000000AB}"}])
    check("a ProductCode no product has is gone from both real views", agent.already_gone(nowhere, real) is True)

print()
if skipped:
    print("skipped:", "; ".join(skipped))
print(f"{passed} passed, {len(failed)} failed")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

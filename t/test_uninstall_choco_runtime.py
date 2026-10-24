"""A Chocolatey package whose records list a shared runtime beside its own
entry, as paint.net's held WebView2 on a lab PC (t/fixtures/choco-runtime):
only the program's own MSI is uninstalled, the runtime is never named in a
command, and a package of runtimes only is still refused.
"""
import functools
import hashlib
import logging
import os
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures", "choco-runtime")
CHOCO_FIXTURES = os.path.join(HERE, "fixtures", "choco")
UNINSTALL_FIXTURES = os.path.join(HERE, "fixtures", "choco-uninstall")

passed = 0
failed = []


def check(label, condition):
    global passed
    if condition:
        passed += 1
    else:
        failed.append(label)
        print(f"  FAIL: {label}")


work = tempfile.mkdtemp(prefix="appease_chocort_")
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

HKLM = agent.HKLM_NAME
PRESENT, ABSENT = agent.SIGNAL_PRESENT, agent.SIGNAL_ABSENT
SID = "S-1-5-21-1111111111-2222222222-3333333333-1001"
FOLDERS = [agent._folder_key("C:\\Appease")]
PAINT_GUID = "{C6A29A65-A91B-4F4A-A1B5-B904AC61255D}"
WEBVIEW_KEY = "Microsoft EdgeWebView"
NOT_RUN = object()


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


def fixture(name, folder=FIXTURES):
    with open(os.path.join(folder, name), "rb") as handle:
        return handle.read()


# ---------------------------------------------------------------------------
print("0. The lab PC's .registry, as Chocolatey wrote it")
# ---------------------------------------------------------------------------
LAB = fixture("paint.net.5.1.12.registry")
check("the fixture folder holds exactly the lab file and its README",
      sorted(os.listdir(FIXTURES)) == ["README.md", "paint.net.5.1.12.registry"])
check("the lab file is unchanged since it was copied",
      hashlib.sha256(LAB).hexdigest() == "4284e213f03cbdc876941761409125eed99a596a58f7a7a709f7de7997146ee5")
check("...it carries the redacted SID, not the lab account's",
      SID.encode() in LAB and b"4120420980" not in LAB)
check("...with Chocolatey's own BOM and CRLF line endings",
      LAB.startswith(b"\xef\xbb\xbf") and LAB.count(b"\r\n") == LAB.count(b"\n") > 0)
lab_path = os.path.join(work, "lab.registry")
with open(lab_path, "wb") as handle:
    handle.write(LAB)
user, lab_keys = agent.read_snapshot(lab_path)
check("the agent reads both keys: Paint.NET's MSI, then WebView2",
      user == SID and [(k["hive"], k["name"], k["view"], k["display"]) for k in lab_keys]
      == [(HKLM, PAINT_GUID, "Registry64", "Paint.NET"),
          (HKLM, WEBVIEW_KEY, "Registry32", "Microsoft Edge WebView2 Runtime")])

_KEY_BLOCK = re.compile(rb"\r\n    <key .*?</key>", re.DOTALL)
PAINT_BLOCK, WEBVIEW_BLOCK = _KEY_BLOCK.findall(LAB)
check("the file splits into its two key blocks", b"Paint.NET" in PAINT_BLOCK and b"EdgeWebView" in WEBVIEW_BLOCK)


def registry_with(*blocks):
    """The lab file with these key blocks, in this order, in place of its
    own."""
    head, rest = LAB.split(PAINT_BLOCK, 1)
    tail = rest.split(WEBVIEW_BLOCK, 1)[1]
    return head + b"".join(blocks) + tail


check("...and puts back together byte for byte", registry_with(PAINT_BLOCK, WEBVIEW_BLOCK) == LAB)
WEBVIEW_ONLY = registry_with(WEBVIEW_BLOCK)
EXE_OWN = registry_with(PAINT_BLOCK.replace(PAINT_GUID.encode(), b"Paint.NET")
                        .replace(b'installerType="Msi"', b'installerType="Nsis"'), WEBVIEW_BLOCK)
HELPER_GUID = "{11111111-2222-3333-4444-555555555555}"
TWO_OWN = registry_with(PAINT_BLOCK, PAINT_BLOCK.replace(PAINT_GUID.encode(), HELPER_GUID.encode())
                        .replace(b'displayName="Paint.NET"', b'displayName="Paint.NET Helper"'), WEBVIEW_BLOCK)

# ---------------------------------------------------------------------------
# Fakes: a Chocolatey folder, Windows' list, choco.exe and msiexec.
# ---------------------------------------------------------------------------


def nuspec(package_id, version, dependencies=()):
    deps = "".join(f'<dependency id="{d}" version="1.0" />' for d in dependencies)
    return (f'<?xml version="1.0" encoding="utf-8"?><package xmlns="http://schemas.microsoft.com/packaging/2015/06/'
            f'nuspec.xsd"><metadata><id>{package_id}</id><version>{version}</version><dependencies>{deps}'
            f'</dependencies></metadata></package>').encode("utf-8")


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)


def tree(registry_bytes=LAB):
    """A Chocolatey folder as the lab PC had it: choco.exe, Chocolatey's own
    package, paint.net 5.1.12 with that .registry, and the extension it
    depends on."""
    root = tempfile.mkdtemp(dir=work, prefix="choco_")
    write(os.path.join(root, "bin", "choco.exe"), b"")
    os.makedirs(os.path.join(root, "lib", "chocolatey"))
    with zipfile.ZipFile(os.path.join(root, "lib", "chocolatey", "chocolatey.nupkg"), "w") as archive:
        archive.writestr("chocolatey.nuspec", nuspec("chocolatey", "2.7.3"))
    for name, folder in (("paint.net", "paint.net.5.1.12"), ("chocolatey-core.extension", "chocolatey-core.extension.1.4.0"),
                         ("chocolatey-compatibility.extension", "chocolatey-compatibility.extension.1.0.0")):
        write(os.path.join(root, "lib", name, name + ".nuspec"), fixture(name + ".nuspec", CHOCO_FIXTURES))
        write(os.path.join(root, ".chocolatey", folder, ".files"), b"")
    write(os.path.join(root, ".chocolatey", "paint.net.5.1.12", ".registry"), registry_bytes)
    return root


def lib_has(root, name):
    return os.path.isdir(os.path.join(root, "lib", name))


def entry(name, display, publisher, view="Registry64", location=None, msi=False, system=False):
    return {"hive": HKLM, "sid": None, "name": name, "view": view, "display": display, "publisher": publisher,
            "location": location, "msi": msi, "system": system}


PAINT = entry(PAINT_GUID, "Paint.NET", "dotPDN LLC", msi=True)
WEBVIEW = entry(WEBVIEW_KEY, "Microsoft Edge WebView2 Runtime", "Microsoft Corporation", view="Registry32",
                location="C:\\Program Files (x86)\\Microsoft\\EdgeWebView\\Application", system=True)


class FakeRegistry:
    """Windows' list: a set of entries, both views of HKLM."""

    def __init__(self, *entries, dependents=()):
        self.entries = list(entries)
        self.dependents = {} if dependents == () else dependents      # None: the records cannot be read

    def lists(self, name):
        return any(e["name"].upper() == name.upper() for e in self.entries)

    def remove(self, name):
        self.entries = [e for e in self.entries if e["name"].upper() != name.upper()]

    def uninstall_key(self, hive, sid, name, view):
        for e in self.entries:
            if (e["hive"], e["name"].upper(), e["view"]) == (hive, name.upper(), view):
                return PRESENT
        return ABSENT

    def listed_display_names(self, required_sids):
        return {e["display"] for e in self.entries}

    def installer_dependents(self, product_code):
        if self.dependents is None:
            return None
        return set(self.dependents.get(product_code.upper(), ()))

    def snapshot(self):
        made = [agent.uninstall_entry(e["hive"], e["sid"], e["view"], e["name"],
                                      {"displayname": e["display"], "publisher": e["publisher"],
                                       "installlocation": e["location"], "windowsinstaller": 1 if e["msi"] else 0,
                                       "systemcomponent": 1 if e["system"] else 0}) for e in self.entries]
        return {"entries": {agent.entry_identity(m): m for m in made}, "user_readable": True}


C04_LINES = fixture("c04-cleanup.txt", UNINSTALL_FIXTURES).decode("utf-8").split("\r\n")
_ERRORLEVEL = max(i for i, line in enumerate(C04_LINES) if re.fullmatch(r"-?\d+ ?", line))
check("step 9's paint.net cleanup capture exited 0", C04_LINES[_ERRORLEVEL].strip() == "0")
C04_OUTPUT = "\r\n".join(C04_LINES[:_ERRORLEVEL])  # less cmd's `echo %ERRORLEVEL%` line


class Chocolatey:
    """Stands in for choco.exe, keeping every argv. The cleanup (-n
    --skip-autouninstaller) plays back the VM's capture; a plain uninstall
    also removes every key the auto-uninstaller reaches - the danger."""

    def __init__(self, root, registry, cleanup_code=0, plain_keys=(PAINT_GUID,)):
        self.root, self.registry, self.cleanup_code, self.plain_keys = root, registry, cleanup_code, plain_keys
        self.argv = []

    def __call__(self, argv):
        self.argv.append(list(argv))
        if argv[2] != "paint.net":
            return 99, False, False, "(no capture for this run)"
        if "-n" in argv:
            if self.cleanup_code == 0:
                shutil.rmtree(os.path.join(self.root, "lib", "paint.net"), ignore_errors=True)
            return self.cleanup_code, False, True, C04_OUTPUT
        shutil.rmtree(os.path.join(self.root, "lib", "paint.net"), ignore_errors=True)
        for name in self.plain_keys:
            self.registry.remove(name)
        return 0, False, True, "(made up) Chocolatey uninstalled 1/1 packages."


class Msiexec:
    """Stands in for run_uninstall_process. Every argv is kept; exit 0
    removes the product named, anything else leaves it."""

    def __init__(self, registry, code=0):
        self.registry, self.code = registry, code
        self.argv = []

    def __call__(self, argv):
        self.argv.append(list(argv))
        if self.code == 0:
            self.registry.remove(argv[2])
        return self.code, False, False


class Clock:
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Case:
    """One uninstall of paint.net against these fakes."""

    def __init__(self, registry_bytes=LAB, entries=(PAINT, WEBVIEW), dependents=(), msi_code=0, cleanup_code=0,
                 plain_keys=(PAINT_GUID,)):
        self.root = tree(registry_bytes)
        self.registry = FakeRegistry(*entries, dependents=dependents)
        self.choco = Chocolatey(self.root, self.registry, cleanup_code, plain_keys)
        self.msiexec = Msiexec(self.registry, msi_code)
        self.targets = []
        self.clock = Clock()

    def verify(self, target, code):
        self.targets.append(target)
        return agent.verify_removal(target, code, self.registry, self.clock.time, self.clock.sleep, lambda: None,
                                    lambda folder: ABSENT)

    def kwargs(self):
        return dict(registry=self.registry, read=lambda sid: self.registry.snapshot(), user_sid=lambda: SID,
                    run=self.choco, run_msi=self.msiexec, verify=self.verify, folders=FOLDERS,
                    choco_exe=os.path.join(self.root, "bin", "choco.exe"))

    def run(self):
        logs.take()
        self.outcome = agent.uninstall_choco({"id": 77, "action": "uninstall", "method": "choco",
                                              "package_id": "paint.net"}, **self.kwargs())
        self.log = logs.take()
        return self.outcome

    def result(self):
        return self.outcome.ok, self.outcome.exit_code, self.outcome.reason

    def nothing_ran(self):
        return self.choco.argv == [] and self.msiexec.argv == []

    def every_argv(self):
        return self.choco.argv + self.msiexec.argv


def mentions_webview(argv):
    text = " ".join(argv).casefold()
    return any(word in text for word in ("edgewebview", "msedgewebview", "webview2", "setup.exe"))


# ---------------------------------------------------------------------------
print("1. The lab case: paint.net with WebView2 recorded beside it")
# ---------------------------------------------------------------------------
case = Case()
case.run()
msiexec = os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32", "msiexec.exe")
check("the job completes, as a plain removal: exit 0, no reason",
      case.result() == (True, 0, None))
check("exactly one uninstaller ran besides Chocolatey: msiexec /x on Paint.NET's own ProductCode, quietly",
      case.msiexec.argv == [[msiexec, "/x", PAINT_GUID, "/qn", "/norestart"]])
check("choco.exe ran once, for paint.net only",
      [argv[1:3] for argv in case.choco.argv] == [["uninstall", "paint.net"]])
check("...and only with -n --skip-autouninstaller: Chocolatey's auto-uninstaller never ran",
      all("--skip-autouninstaller" in argv and "-n" in argv for argv in case.choco.argv)
      and not any("--fail-on-autouninstaller" in argv for argv in case.choco.argv))
check("...and never with -x: its dependencies are not removed",
      not any(flag in argv for argv in case.choco.argv for flag in ("-x", "--force-dependencies"))
      and lib_has(case.root, "chocolatey-core.extension"))
check("WebView2 is never named in any command run", not any(mentions_webview(argv) for argv in case.every_argv()))
check("WebView2 is still listed afterwards; Paint.NET is not",
      case.registry.lists(WEBVIEW_KEY) and not case.registry.lists(PAINT_GUID))
check("Chocolatey no longer has paint.net (lib\\paint.net is gone)", not lib_has(case.root, "paint.net"))
check("verification ran once", len(case.targets) == 1)
target = case.targets[0] if case.targets else {"keys": [{"name": "none"}], "names": set()}
check("...on Paint.NET's key alone, never WebView2's",
      {k["name"] for k in target["keys"]} == {PAINT_GUID}
      and not any(k["name"] == WEBVIEW_KEY for t in case.targets for k in t["keys"]))
check("...by Paint.NET's name alone", target["names"] == {"Paint.NET"})
check("agent.log says the runtime was left in place, and names it",
      any("Microsoft Edge WebView2 Runtime" in line and "left in place" in line for line in case.log))
check("no line says it refused", not any("nothing was run" in line for line in case.log))

# The summary line, as the poll writes it.
case = Case()
real_choco = agent.UNINSTALLERS["choco"]
agent.UNINSTALLERS["choco"] = functools.partial(agent.uninstall_choco, **case.kwargs())
try:
    logs.take()
    outcome = agent.run_uninstall_job({"id": 77, "action": "uninstall", "method": "choco", "package_id": "paint.net"})
    summary = [line for line in logs.take() if line.startswith("Uninstall summary:")]
finally:
    agent.UNINSTALLERS["choco"] = real_choco
check("the summary line: removed, Paint.NET's code only, the runtime left, files cleaned, exit 0, verified",
      summary == [f"Uninstall summary: job 77, choco paint.net: removed, {PAINT_GUID} only, shared runtime left "
                  f"in place; Chocolatey's files cleaned up; exit code 0; verification 0s."])

# ---------------------------------------------------------------------------
print("2. The rest of the lab case: gone already, msiexec failing, cleanup failing")
# ---------------------------------------------------------------------------
case = Case(entries=(WEBVIEW,))
case.run()
check("Paint.NET already gone, WebView2 listed: already_removed, msiexec never ran",
      case.result() == (True, None, "already_removed") and case.msiexec.argv == [])
check("...Chocolatey's files are still cleaned up, with the auto-uninstaller skipped",
      not lib_has(case.root, "paint.net")
      and [("-n" in a and "--skip-autouninstaller" in a) for a in case.choco.argv] == [True])
check("...and WebView2 is untouched", case.registry.lists(WEBVIEW_KEY)
      and not any(mentions_webview(argv) for argv in case.every_argv()))

case = Case(msi_code=1603)
case.run()
check("msiexec fails (1603): the job fails with Windows Installer's reason and exit code",
      case.result() == (False, 1603, "msi_fatal"))
check("...Chocolatey never ran, so paint.net is still its package; both stay listed",
      case.choco.argv == [] and lib_has(case.root, "paint.net")
      and case.registry.lists(PAINT_GUID) and case.registry.lists(WEBVIEW_KEY))

case = Case(cleanup_code=1)
case.run()
check("Chocolatey's cleanup fails after Paint.NET was removed: still completed, the program is gone",
      case.result() == (True, 0, None) and not case.registry.lists(PAINT_GUID))
check("...and agent.log says the cleanup did not finish",
      any("did not finish" in line for line in case.log))

# ---------------------------------------------------------------------------
print("3. The guard is as strict as before")
# ---------------------------------------------------------------------------
case = Case(WEBVIEW_ONLY, entries=(WEBVIEW,))
case.run()
check("a package whose only key is WebView2: shared_runtime, nothing run",
      case.result() == (False, None, "shared_runtime") and case.nothing_ran())
check("...with the same agent.log line as lab job 77",
      "Uninstall of paint.net: a shared runtime - a Microsoft runtime by name: Microsoft Edge WebView2 Runtime; "
      "nothing was run." in case.log)

case = Case(dependents={PAINT_GUID: {"{B0E5A7C1-1111-2222-3333-444444444444}"}})
case.run()
check("WebView2 beside it, and another product depends on Paint.NET's own MSI: shared_runtime, nothing run",
      case.result() == (False, None, "shared_runtime") and case.nothing_ran())
case = Case(dependents=None)
case.run()
check("...and with the dependency records unreadable: registry_unreadable, nothing run",
      case.result() == (False, None, "registry_unreadable") and case.nothing_ran())
case = Case(entries=(entry(PAINT_GUID, "Paint.NET", "dotPDN LLC", location="C:\\Appease\\pdn\\", msi=True), WEBVIEW))
case.run()
check("WebView2 beside it, and Paint.NET's entry in a protected folder: protected_software, nothing run",
      case.result() == (False, None, "protected_software") and case.nothing_ran())
VC = entry("{AAAAAAAA-1111-2222-3333-444444444444}", "Microsoft Visual C++ 2015-2022 Redistributable (x64)",
           "Microsoft Corporation", msi=True)
VC_BLOCK = (PAINT_BLOCK.replace(PAINT_GUID.encode(), VC["name"].encode())
            .replace(b'displayName="Paint.NET"', b'displayName="Microsoft Visual C++ 2015-2022 Redistributable (x64)"'))
case = Case(registry_with(VC_BLOCK, WEBVIEW_BLOCK), entries=(VC, WEBVIEW))
case.run()
check("two runtimes and nothing else (VC++ and WebView2): shared_runtime, nothing run",
      case.result() == (False, None, "shared_runtime") and case.nothing_ran())

# ---------------------------------------------------------------------------
print("4. No runtime among its keys: the old path, unchanged")
# ---------------------------------------------------------------------------
VM = fixture("paint.net.5.1.12.registry", CHOCO_FIXTURES)


def must_not_run(*args, **kwargs):
    raise AssertionError("the runtime path ran")


saved = agent.choco_runtime_split, agent.uninstall_choco_beside_runtime
agent.choco_runtime_split = agent.uninstall_choco_beside_runtime = must_not_run
try:
    case = Case(VM, entries=(PAINT, WEBVIEW))
    case.run()
finally:
    agent.choco_runtime_split, agent.uninstall_choco_beside_runtime = saved
check("the VM's .registry (Paint.NET only; WebView2 was already there): completed, as before",
      case.result() == (True, 0, None))
check("...by one plain choco uninstall, flags exactly as before, and the runtime path never entered",
      case.choco.argv == [[os.path.join(case.root, "bin", "choco.exe"), "uninstall", "paint.net", "-y",
                           "--fail-on-autouninstaller", "--no-progress"]] and case.msiexec.argv == [])
check("...verified on the keys Chocolatey recorded",
      len(case.targets) == 1 and {k["name"] for k in case.targets[0]["keys"]} == {PAINT_GUID})

# ---------------------------------------------------------------------------
print("5. A runtime beside it, and its own entry cannot be used")
# ---------------------------------------------------------------------------
case = Case(EXE_OWN, entries=(entry("Paint.NET", "Paint.NET", "dotPDN LLC"), WEBVIEW))
case.run()
check("its own entry is not a Windows Installer product: choco_own_entry_not_msi, nothing run",
      case.result() == (False, None, "choco_own_entry_not_msi") and case.nothing_ran())
check("...and lib\\paint.net and both entries are left as they were",
      lib_has(case.root, "paint.net") and case.registry.lists("Paint.NET") and case.registry.lists(WEBVIEW_KEY))
case = Case(entries=(entry(PAINT_GUID, "Paint.NET", "dotPDN LLC", msi=False), WEBVIEW))
case.run()
check("...nor a GUID key Windows does not list as a Windows Installer product",
      case.result() == (False, None, "choco_own_entry_not_msi") and case.nothing_ran())
case = Case(TWO_OWN, entries=(PAINT, entry(HELPER_GUID, "Paint.NET Helper", "dotPDN LLC", msi=True), WEBVIEW))
case.run()
check("two entries of its own beside the runtime: choco_own_entry_not_found, nothing run",
      case.result() == (False, None, "choco_own_entry_not_found") and case.nothing_ran())
check("...and all three entries are still listed",
      all(case.registry.lists(name) for name in (PAINT_GUID, HELPER_GUID, WEBVIEW_KEY)))

# ---------------------------------------------------------------------------
print("6. The install record never names the runtime")
# ---------------------------------------------------------------------------


def record_for(registry_bytes, after_entries):
    root = tree(registry_bytes)
    after = FakeRegistry(*after_entries).snapshot() if after_entries is not None else None
    return agent.build_choco_record("paint.net", None, None, after, root, FOLDERS)


for label, registry_bytes in (("in the lab file's order", LAB),
                              ("with WebView2 recorded first", registry_with(WEBVIEW_BLOCK, PAINT_BLOCK))):
    for seen, after_entries in (("Windows' list read", (PAINT, WEBVIEW)), ("no list after", None)):
        record = record_for(registry_bytes, after_entries)
        check(f"{label}, {seen}: the row names Paint.NET's own key",
              (record.get("package_id"), record.get("key_name"), record.get("display_name"), record.get("view"))
              == ("paint.net", PAINT_GUID, "Paint.NET", "Registry64"))
record = record_for(WEBVIEW_ONLY, (WEBVIEW,))
check("a package whose only key is WebView2: named as before, so the server shows it protected",
      (record.get("key_name"), record.get("display_name")) == (WEBVIEW_KEY, "Microsoft Edge WebView2 Runtime"))

# ---------------------------------------------------------------------------
print("7. The server: both new reasons, and the lab case end to end")
# ---------------------------------------------------------------------------
import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()
import app as appmod  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402
appmod.app.config["TESTING"] = True
client = appmod.app.test_client()

record = record_for(registry_with(WEBVIEW_BLOCK, PAINT_BLOCK), (PAINT, WEBVIEW))
check("the server would not mistake that record for a shared runtime",
      not appmod.is_shared_runtime({"software_name": "Paint.NET", "display_name": record.get("display_name")}))
check("both new reasons are among the agent's refusals",
      {"choco_own_entry_not_found", "choco_own_entry_not_msi"} <= set(agent.UNINSTALL_REFUSAL_REASONS))
WORDS = {
    "choco_own_entry_not_found": "AppEase couldn't tell which uninstall entry belongs to this program, so nothing "
                                 "was removed.",
    "choco_own_entry_not_msi": "This program was installed together with a shared runtime and isn't an MSI "
                               "program, so AppEase can't remove it safely. Remove it manually from the computer.",
}
for reason, note in WORDS.items():
    spec = appmod.JOB_RESULT_REASONS.get(reason, {})
    check(f"{reason}: a failed uninstall, no exit code, the agreed note",
          spec.get("statuses") == ("failed",) and spec.get("note") == note
          and spec.get("actions") == appmod.JOB_ACTIONS_UNINSTALL)


def db():
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


conn = db()
conn.execute("UPDATE admins SET password_hash = ?, must_change_password = 0 WHERE username = 'admin'",
             (generate_password_hash("Str0ng!Passw0rd"),))
pc = conn.execute("INSERT INTO computers (hostname, status) VALUES ('LAB-RT', 'online')").lastrowid
conn.commit()
for reason in WORDS:
    row = conn.execute("INSERT INTO installations (computer_id, software_name, method, record_key, state) "
                       "VALUES (?, 'Paint.NET', 'choco', ?, 'installed')", (pc, secrets.token_hex(8))).lastrowid
    job = conn.execute("INSERT INTO jobs (computer_hostname, software_id, status, action, installation_id, started_at) "
                       "VALUES ('LAB-RT', 0, 'in_progress', 'uninstall', ?, CURRENT_TIMESTAMP)", (row,)).lastrowid
    conn.commit()
    reply = client.post(f"/api/agent/jobs/{job}/report", json={"computer_hostname": "LAB-RT", "status": "failed",
                                                                 "exit_code": None, "reason": reason}).get_json()
    kept = tuple(conn.execute("SELECT status, exit_code, result_reason FROM jobs WHERE id = ?", (job,)).fetchone())
    state = conn.execute("SELECT state FROM installations WHERE id = ?", (row,)).fetchone()[0]
    check(f"{reason} is kept on the uninstall job as sent, and the row stays installed",
          kept == ("failed", None, reason) and (reply or {}).get("reason") == reason and state == "installed")
conn.close()

client.post("/login", data={"username": "admin", "password": "Str0ng!Passw0rd"})
agent.COMPUTER_HOSTNAME = "LAB-PDN"
agent.AGENT_ID = None
real_post = agent.post_json
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
    pc_row = conn.execute("SELECT id FROM computers WHERE hostname = 'LAB-PDN'").fetchone()
    # The lab PC's row, as the server held it (paintnet_installations.csv):
    # Paint.NET, its own key, installed - kept as recorded.
    ROW = conn.execute(
        "INSERT INTO installations (computer_id, software_name, software_version, method, record_key, "
        "choco_package_id, reg_hive, reg_view, reg_key_name, display_name, display_version, state) "
        "VALUES (?, 'Paint.NET', '5.1.12', 'choco', ?, 'paint.net', 'HKEY_LOCAL_MACHINE', 'Registry64', ?, "
        "'Paint.NET', '5.1.12', 'installed')",
        (pc_row["id"], database.installation_record_key("choco", package_id="paint.net"), PAINT_GUID)).lastrowid
    conn.commit()
    reply = client.post("/uninstall-batch", data={"installation_ids": [str(ROW)]}).get_json()
    check("end to end: the create route queues the uninstall of the lab row", reply["ok"] and len(reply["created"]) == 1)
    case = Case()
    agent.UNINSTALLERS["choco"] = functools.partial(agent.uninstall_choco, **case.kwargs())
    agent.poll_once()
    job = conn.execute("SELECT * FROM jobs WHERE installation_id = ?", (ROW,)).fetchone()
    row = conn.execute("SELECT * FROM installations WHERE id = ?", (ROW,)).fetchone()
    check("...the job the agent got names only the package",
          seen_jobs and seen_jobs[-1].get("package_id") == "paint.net")
    check("...only Paint.NET's MSI ran, and WebView2 is still listed",
          case.msiexec.argv == [[msiexec, "/x", PAINT_GUID, "/qn", "/norestart"]]
          and case.registry.lists(WEBVIEW_KEY))
    check("...the job ends completed, as a plain removal",
          (job["status"], job["exit_code"], job["result_reason"]) == ("completed", 0, None))
    check("...and the installations row is removed by that job",
          (row["state"], row["uninstall_job_id"]) == ("removed", job["id"]))
    conn.close()
finally:
    agent.post_json = real_post
    agent.UNINSTALLERS["choco"] = real_choco

print()
print(f"{passed} passed, {len(failed)} failed")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

"""The agent's Chocolatey uninstall, against what Chocolatey 2.7.3 really
printed on the VM (t/fixtures/choco-uninstall), played back on a fake
Chocolatey folder and registry. Its answers are read word for word, and
nothing runs unless every check passes.
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
FIXTURES = os.path.join(HERE, "fixtures", "choco-uninstall")
CHOCO_FIXTURES = os.path.join(HERE, "fixtures", "choco")

passed = 0
failed = []


def check(label, condition):
    global passed
    if condition:
        passed += 1
    else:
        failed.append(label)
        print(f"  FAIL: {label}")


work = tempfile.mkdtemp(prefix="appease_unchoco_")
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

if not hasattr(agent, "uninstall_choco"):
    print("COULD NOT RUN - agent.py predates the Chocolatey uninstall.")
    sys.exit(3)

HKLM, HKCU = agent.HKLM_NAME, agent.HKCU_NAME
PRESENT, ABSENT, UNKNOWN = agent.SIGNAL_PRESENT, agent.SIGNAL_ABSENT, agent.SIGNAL_UNKNOWN
SID = "S-1-5-21-1111111111-2222222222-3333333333-1001"
FOLDERS = [agent._folder_key("C:\\Appease")]
PUTTY_GUID = "{3B0346D3-155D-43E5-9561-B9BE51CF2EB6}"
UNSIGNED = 0xFFFFFFFF   # -1 as Windows hands it to Python


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
print("0. The captures are Chocolatey's own bytes")
# ---------------------------------------------------------------------------
FIXTURE_SHA256 = {
    "c01-real-id-not-installed.txt": "ca658a375aebea3b6044cbfc9c89e228de40ac5816f172f98711eed4600f86b6",
    "c01-unknown-id.txt": "7c87deb9368d6fd99544dd14b2fdfcdafc1f6590f38189ca6da21b038cb05a02",
    "c02-uninstall.txt": "9c2b97e5e599f68b71a45d658e5d885a1a905423b466f3a0ec2ea1fb93af1d90",
    "c03-uninstall.txt": "a8f769eecab3cbbbb2f35f0c436705a1cad51031b153749928149c5f808daafa",
    "c04-cleanup.txt": "e257596842d7802348952189e4f84326c0fb921dc74e9f30290c2fc3b67dacb2",
    "c05a-owner-first.txt": "fb2a8c0c9b958ff6404a4a001cccaa9863761244ab58c9768b5e98136da91e3d",
    "c05b-owner.txt": "4f85e8e82ba7512573f7822a0b155b0700f24393190f6b42e19971094b042d89",
    "c05b-wrapper.txt": "2702c9c6c512bd43827c5c5ad113a49e275d15c5b1f96c4a6a2bd13524defefb",
    "c07a-flag.txt": "2dcfd1a18705b2540ca1dd239ae4778542417d124b36c307b82d7da517379e61",
    "c07b-no-flag.txt": "2dcfd1a18705b2540ca1dd239ae4778542417d124b36c307b82d7da517379e61",
    "putty.install.0.85.0.registry": "b860027a9c63566eb4e97fe50f0cc231ae99d337cba26eb33da3b04804bd02fd",
}


def fixture(name, folder=FIXTURES):
    with open(os.path.join(folder, name), "rb") as handle:
        return handle.read()


on_disk = sorted(n for n in os.listdir(FIXTURES) if n != "README.md")
check(f"the fixture folder holds exactly the pinned files ({on_disk})", on_disk == sorted(FIXTURE_SHA256))
for name, digest in FIXTURE_SHA256.items():
    check(f"{name} is unchanged", hashlib.sha256(fixture(name)).hexdigest() == digest)
check("with VLC's uninstaller deleted, --fail-on-autouninstaller changed nothing: the two runs are byte-identical",
      fixture("c07a-flag.txt") == fixture("c07b-no-flag.txt"))
check("the PuTTY snapshot carries the redacted SID, not the VM's",
      SID.encode() in fixture("putty.install.0.85.0.registry") and b"4221116625" not in fixture(
          "putty.install.0.85.0.registry"))

_STAMP = re.compile(r"^\w{3} \d\d/\d\d/\d{4} \d")


def capture(name):
    """(what choco.exe printed, its exit code as Python sees it). The file is
    that output plus cmd's `echo %ERRORLEVEL%` line - and, in c02, a
    %DATE% %TIME% line either side - which are taken off."""
    lines = fixture(name).decode("utf-8").split("\r\n")
    index = max(i for i, line in enumerate(lines) if re.fullmatch(r"-?\d+ ?", line))
    code = int(lines[index])
    kept = [line for i, line in enumerate(lines) if i != index and not _STAMP.match(line)]
    return "\r\n".join(kept), code & UNSIGNED


check("the exit codes cmd printed are the ones the findings say",
      [capture(n)[1] for n in ("c01-unknown-id.txt", "c01-real-id-not-installed.txt", "c02-uninstall.txt",
                               "c03-uninstall.txt", "c04-cleanup.txt", "c05a-owner-first.txt",
                               "c05b-wrapper.txt", "c05b-owner.txt", "c07a-flag.txt")]
      == [1, 1, 0, 0, 0, 1, 0, 0, UNSIGNED])

# ---------------------------------------------------------------------------
print("1. Reading what Chocolatey said")
# ---------------------------------------------------------------------------
NOT_INSTALLED, DEPENDS = agent.CHOCO_FAILED_NOT_INSTALLED, agent.CHOCO_FAILED_DEPENDS
read_failure = agent.choco_uninstall_failure
text, code = capture("c01-unknown-id.txt")
check("an id that exists nowhere: \"is not installed\"", read_failure("appease-not-a-package", code, text)
      == NOT_INSTALLED)
text, code = capture("c01-real-id-not-installed.txt")
check("a real id that is not installed: the same words", read_failure("putty.install", code, text) == NOT_INSTALLED)
check("...ids compare without case, like Chocolatey's", read_failure("PuTTY.Install", code, text) == NOT_INSTALLED)
check("...but only for THIS package", read_failure("putty", code, text) is None)
check("...and only on exit 1 (not on 0)", read_failure("putty.install", 0, text) is None)
check("...nor on Chocolatey's other error code", read_failure("putty.install", UNSIGNED, text) is None)
check("...nor without the summary saying a package failed",
      read_failure("putty.install", 1, text.replace(" 1 packages failed.", "")) is None)
check("...nor with the words anywhere but under Failures",
      read_failure("putty.install", 1, text.replace("\r\nFailures\r\n", "\r\nNotes\r\n")) is None)
text, code = capture("c05a-owner-first.txt")
check("vlc.install while vlc is installed: \"depends on it\"", read_failure("vlc.install", code, text) == DEPENDS)
check("...about vlc.install, not about vlc", read_failure("vlc", code, text) is None)
for name, package in (("c02-uninstall.txt", "putty.install"), ("c05b-wrapper.txt", "vlc"),
                      ("c05b-owner.txt", "vlc.install"), ("c07a-flag.txt", "vlc.install"),
                      ("c04-cleanup.txt", "paint.net")):
    text, code = capture(name)
    check(f"{name}: neither", read_failure(package, code, text) is None)

# ---------------------------------------------------------------------------
print("2. Only lib\\<id> means Chocolatey has it")
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


def lib(root, package_id, version="1.0.0", dependencies=(), data=None):
    write(os.path.join(root, "lib", package_id, package_id + ".nuspec"),
          data if data is not None else nuspec(package_id, version, dependencies))


def records(root, package_id, version, registry=None):
    folder = os.path.join(root, ".chocolatey", f"{package_id}.{version}")
    os.makedirs(folder, exist_ok=True)
    if registry is not None:
        write(os.path.join(folder, ".registry"), registry)


def putty(root):
    lib(root, "putty.install", "0.85.0")
    records(root, "putty.install", "0.85.0", fixture("putty.install.0.85.0.registry"))


def vlc(root, wrapper=True):
    for name in ("vlc.install", "chocolatey-core.extension", "chocolatey-compatibility.extension"):
        lib(root, name, data=fixture(name + ".nuspec", CHOCO_FIXTURES))
    records(root, "vlc.install", "3.0.23", fixture("vlc.install.3.0.23.registry", CHOCO_FIXTURES))
    records(root, "chocolatey-core.extension", "1.4.0")
    records(root, "chocolatey-compatibility.extension", "1.0.0")
    if wrapper:
        lib(root, "vlc", data=fixture("vlc.nuspec", CHOCO_FIXTURES))
        records(root, "vlc", "3.0.23")


def tree(*packages):
    """A Chocolatey folder: bin\\choco.exe, Chocolatey's own package (only its
    .nupkg, as on every machine) and the packages asked for."""
    root = tempfile.mkdtemp(dir=work, prefix="choco_")
    write(os.path.join(root, "bin", "choco.exe"), b"")
    os.makedirs(os.path.join(root, ".chocolatey"), exist_ok=True)
    os.makedirs(os.path.join(root, "lib", "chocolatey"))
    with zipfile.ZipFile(os.path.join(root, "lib", "chocolatey", "chocolatey.nupkg"), "w") as archive:
        archive.writestr("chocolatey.nuspec", nuspec("chocolatey", "2.7.3"))
    for package in packages:
        package(root)
    return root


def exists(root, *parts):
    return os.path.exists(os.path.join(root, *parts))


root = tree(putty)
state, spec = agent.choco_lib_package(root, "putty.install")
check("a lib folder with its nuspec: Chocolatey has it, at that version",
      state == agent.CHOCO_LIB_PRESENT and (spec["id"], spec["version"]) == ("putty.install", "0.85.0"))
check("Chocolatey's own package is read from its .nupkg",
      agent.choco_lib_package(root, "chocolatey")[0] == agent.CHOCO_LIB_PRESENT)
shutil.rmtree(os.path.join(root, "lib", "putty.install"))
write(os.path.join(root, "lib-bad", "putty.install", "0.85.0", "putty.install.nuspec"), nuspec("putty.install", "0.85.0"))
check("no lib\\<id>: absent - though its record folder survives and lib-bad holds a copy",
      agent.choco_lib_package(root, "putty.install") == (agent.CHOCO_LIB_ABSENT, None)
      and exists(root, ".chocolatey", "putty.install.0.85.0", ".registry"))
lib(root, "putty.install", data=b"<not a nuspec")
check("a lib folder whose nuspec cannot be read: unreadable, never absent",
      agent.choco_lib_package(root, "putty.install")[0] == agent.CHOCO_LIB_UNREADABLE)
lib(root, "putty.install", data=nuspec("putty", "0.85.0"))
check("...nor one naming another package", agent.choco_lib_package(root, "putty.install")[0]
      == agent.CHOCO_LIB_UNREADABLE)

root = tree(vlc)
records(root, "vlc", "3.0.24")
records(root, "vlc.install", "3.0.22", fixture("vlc.install.3.0.23.registry", CHOCO_FIXTURES))
check("vlc's surviving records are vlc's alone - never vlc.install's",
      agent.choco_surviving_keys(root, "vlc") == [])
check("vlc.install's are every version's",
      [k["name"] for k in agent.choco_surviving_keys(root, "vlc.install")] == ["VLC media player"] * 2)
records(root, "vlc.install", "3.0.21", b"<garbage")
check("one record that cannot be read: can't tell", agent.choco_surviving_keys(root, "vlc.install") is None)

# ---------------------------------------------------------------------------
print("3. Refusals and not-managed: nothing runs")
# ---------------------------------------------------------------------------


def key(name, display, publisher, location=None, hive=HKLM, sid=None, view="Registry64"):
    return {"hive": hive, "sid": sid, "name": name, "view": view, "display": display, "publisher": publisher,
            "location": location}


PUTTY_KEY = key(PUTTY_GUID, "PuTTY release 0.85 (64-bit)", "Simon Tatham")
VLC_KEY = key("VLC media player", "VLC media player", "VideoLAN", "C:\\Program Files\\VideoLAN\\VLC")


class FakeRegistry:
    """Windows' list: a set of entries. unknown makes every answer "can't
    tell"."""

    def __init__(self, *entries, dependents=(), unknown=False):
        self.entries = list(entries)
        self.dependents = {} if dependents == () else dependents      # None: the records cannot be read
        self.unknown = unknown

    def remove(self, name):
        self.entries = [e for e in self.entries if e["name"].upper() != name.upper()]

    def uninstall_key(self, hive, sid, name, view):
        if self.unknown:
            return UNKNOWN
        if hive == HKCU and (sid or "").upper() != SID:
            return UNKNOWN
        for e in self.entries:
            if ((e["hive"], e["name"].upper(), e["view"]) == (hive, name.upper(), view)
                    and (hive != HKCU or (e["sid"] or "").upper() == (sid or "").upper())):
                return PRESENT
        return ABSENT

    def listed_display_names(self, required_sids):
        return None if self.unknown else {e["display"] for e in self.entries}

    def installer_dependents(self, product_code):
        if self.dependents is None:
            return None
        return set(self.dependents.get(product_code.upper(), ()))

    def snapshot(self):
        made = [agent.uninstall_entry(e["hive"], e["sid"], e["view"], e["name"],
                                      {"displayname": e["display"], "publisher": e["publisher"],
                                       "installlocation": e["location"]}) for e in self.entries]
        return {"entries": {agent.entry_identity(m): m for m in made}, "user_readable": True}


class Play:
    """One captured run, and what Chocolatey's log showed it did: which lib
    folders it removed and which uninstall keys went with them."""

    def __init__(self, name=None, output=None, code=None, libs=(), keys=(), timed_out=False, raises=None,
                 blinds=False):
        if name is not None:
            output, captured = capture(name)
            code = captured if code is None else code
        self.output, self.code, self.libs, self.keys = output or "", code, libs, keys
        self.timed_out, self.raises, self.blinds = timed_out, raises, blinds


class Chocolatey:
    """Stands in for run_choco_uninstall: records each argv and plays back
    the capture for (package, "uninstall" or "cleanup")."""

    def __init__(self, root, registry, plays):
        self.root, self.registry, self.plays = root, registry, plays
        self.argv = []

    def __call__(self, argv):
        self.argv.append(list(argv))
        play = self.plays.get((argv[2], "cleanup" if "-n" in argv else "uninstall"))
        if play is None:
            return 99, False, False, "(no capture for this run)"
        if play.raises is not None:
            raise play.raises
        for name in play.libs:
            shutil.rmtree(os.path.join(self.root, "lib", name), ignore_errors=True)
        for name in play.keys:
            self.registry.remove(name)
        if play.blinds:
            self.registry.unknown = True                  # Windows' list unreadable from here on
        return (None if play.timed_out else play.code), play.timed_out, True, play.output

    def packages(self):
        return [(a[2], "cleanup" if "-n" in a else "uninstall") for a in self.argv]


class Clock:
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


produced = set()


def uninstall(root, registry, runner, package="putty.install", folder_signal=None, choco_exe=None, **overrides):
    clock = Clock()
    signal = folder_signal or (lambda folder: agent.SIGNAL_ABSENT)

    def verify(target, code):
        return agent.verify_removal(target, code, registry, clock.time, clock.sleep, lambda: None, signal)

    kwargs = dict(registry=registry, read=lambda sid: registry.snapshot(), user_sid=lambda: SID, run=runner,
                  verify=verify, folders=FOLDERS, choco_exe=choco_exe or os.path.join(root, "bin", "choco.exe"))
    kwargs.update(overrides)
    outcome = agent.uninstall_choco({"id": 7, "action": "uninstall", "method": "choco", "package_id": package},
                                    **kwargs)
    produced.add((outcome.ok, outcome.exit_code, outcome.reason))
    return outcome


def result(outcome):
    return outcome.ok, outcome.exit_code, outcome.reason


def putty_case(*entries, **kwargs):
    root = tree(putty)
    registry = FakeRegistry(*(entries or (PUTTY_KEY,)), **kwargs)
    return root, registry


for label, package in (("a command after it", "putty.install & del x"), ("a path", "..\\putty.install"),
                       ("missing", None), ("a number", 7), ("blank", ""), ("a space inside", "putty install")):
    root, registry = putty_case()
    runner = Chocolatey(root, registry, {})
    check(f"not a plain package id ({label}): uninstall_job_invalid, nothing run",
          result(uninstall(root, registry, runner, package=package)) == (False, None, "uninstall_job_invalid")
          and runner.argv == [])
for package in ("chocolatey", "python314", "Python", "python3"):
    root, registry = putty_case()
    runner = Chocolatey(root, registry, {})
    check(f"{package}: protected_software, as at install, nothing run",
          result(uninstall(root, registry, runner, package=package)) == (False, None, "protected_software")
          and runner.argv == [])
root, registry = putty_case()
runner = Chocolatey(root, registry, {})
check("no choco.exe: choco_missing, nothing run",
      result(uninstall(root, registry, runner, choco_exe=os.path.join(root, "nowhere", "choco.exe")))
      == (False, None, "choco_missing") and runner.argv == [])

# Chocolatey does not have it: its surviving records decide, and nothing runs.
for label, keep_key, expected in (("Windows still lists a key its records name", True, (False, None, "choco_not_managed")),
                                  ("every key its records name is gone", False, (True, None, "already_removed"))):
    root, registry = putty_case(*([PUTTY_KEY] if keep_key else [key("{0}", "Other", "Other")]))
    shutil.rmtree(os.path.join(root, "lib", "putty.install"))
    runner = Chocolatey(root, registry, {})
    check(f"no lib\\putty.install and {label}: {expected[2]}, nothing run",
          result(uninstall(root, registry, runner)) == expected and runner.argv == [])
root, registry = putty_case()
shutil.rmtree(os.path.join(root, "lib", "putty.install"))
write(os.path.join(root, "lib-bad", "putty.install", "0.85.0", "putty.install.nuspec"), nuspec("putty.install", "0.85.0"))
check("...a lib-bad copy changes nothing", result(uninstall(root, registry, Chocolatey(root, registry, {})))
      == (False, None, "choco_not_managed"))
root, registry = putty_case(key("{0}", "Other", "Other"))
shutil.rmtree(os.path.join(root, "lib", "putty.install"))
shutil.rmtree(os.path.join(root, ".chocolatey", "putty.install.0.85.0"))
check("no lib folder and no record to check by: choco_not_managed",
      result(uninstall(root, registry, Chocolatey(root, registry, {}))) == (False, None, "choco_not_managed"))
records(root, "putty.install", "0.85.0", b"<garbage")
check("...nor with a record that cannot be read",
      result(uninstall(root, registry, Chocolatey(root, registry, {}))) == (False, None, "choco_not_managed"))
root, registry = putty_case(unknown=True)
shutil.rmtree(os.path.join(root, "lib", "putty.install"))
check("...nor when Windows' list cannot be read for its keys",
      result(uninstall(root, registry, Chocolatey(root, registry, {}))) == (False, None, "choco_not_managed"))
root = tree(vlc)
registry = FakeRegistry(key("{0}", "Other", "Other"))
shutil.rmtree(os.path.join(root, "lib", "vlc"))
check("no lib\\vlc: vlc's own records hold no key, so not managed - vlc.install's gone key is not vlc's",
      result(uninstall(root, registry, Chocolatey(root, registry, {}), package="vlc"))
      == (False, None, "choco_not_managed"))

root, registry = putty_case()
lib(root, "putty.install", data=b"<garbage")
runner = Chocolatey(root, registry, {})
check("a lib folder that cannot be read: choco_records_unreadable, nothing run",
      result(uninstall(root, registry, runner)) == (False, None, "choco_records_unreadable") and runner.argv == [])
root, registry = putty_case()
shutil.rmtree(os.path.join(root, ".chocolatey", "putty.install.0.85.0"))
runner = Chocolatey(root, registry, {})
check("no snapshot for the installed version: choco_records_unreadable, nothing run",
      result(uninstall(root, registry, runner)) == (False, None, "choco_records_unreadable") and runner.argv == [])
root, registry = putty_case()
runner = Chocolatey(root, registry, {})
check("Windows' program list cannot be read: registry_unreadable, nothing run",
      result(uninstall(root, registry, runner, read=lambda sid: None)) == (False, None, "registry_unreadable")
      and runner.argv == [])

for label, entry in (("installed in C:\\Appease", key(PUTTY_GUID, "PuTTY", "Simon Tatham", "C:\\Appease\\putty\\")),
                     ("named as the agent's own Python",
                      key(PUTTY_GUID, f"Python {sys.version_info[0]}.{sys.version_info[1]}.1 (64-bit)", "PSF"))):
    root, registry = putty_case(entry)
    runner = Chocolatey(root, registry, {})
    check(f"Windows lists it {label}: protected_software, nothing run",
          result(uninstall(root, registry, runner)) == (False, None, "protected_software") and runner.argv == [])
root, registry = putty_case(key("{0}", "Other", "Other"))
snapshot_path = os.path.join(root, ".chocolatey", "putty.install.0.85.0", ".registry")
write(snapshot_path, fixture("putty.install.0.85.0.registry").replace(b"PuTTY release 0.85 (64-bit)", b"Python Launcher"))
runner = Chocolatey(root, registry, {})
check("Chocolatey recorded it as the Python Launcher: protected_software, even with Windows no longer listing it",
      result(uninstall(root, registry, runner)) == (False, None, "protected_software") and runner.argv == [])

root, registry = putty_case(dependents={PUTTY_GUID: {"{B0E5A7C1-1111-2222-3333-444444444444}"}})
runner = Chocolatey(root, registry, {})
check("another product depends on its Windows Installer product: shared_runtime, nothing run",
      result(uninstall(root, registry, runner)) == (False, None, "shared_runtime") and runner.argv == [])
root, registry = putty_case(dependents=None)
runner = Chocolatey(root, registry, {})
check("those dependency records cannot be read: registry_unreadable, nothing run",
      result(uninstall(root, registry, runner)) == (False, None, "registry_unreadable") and runner.argv == [])
VC = "Microsoft Visual C++ 2015-2022 Redistributable (x64) - 14.44.35211"
root, registry = putty_case(key(PUTTY_GUID, VC, "Microsoft Corporation"))
runner = Chocolatey(root, registry, {})
check("Windows lists it as a Microsoft runtime: shared_runtime, nothing run",
      result(uninstall(root, registry, runner)) == (False, None, "shared_runtime") and runner.argv == [])

# ---------------------------------------------------------------------------
print("4. What depends on it: only a wrapper, and the wrapper goes first")
# ---------------------------------------------------------------------------
WRAPPER_PLAYS = {
    ("vlc", "uninstall"): Play("c05b-wrapper.txt", libs=["vlc"]),
    ("vlc.install", "uninstall"): Play("c05b-owner.txt", libs=["vlc.install"], keys=["VLC media player"]),
}


def vlc_case(**plays):
    root = tree(vlc)
    registry = FakeRegistry(VLC_KEY)
    return root, registry, Chocolatey(root, registry, dict(WRAPPER_PLAYS, **plays))


root, registry, runner = vlc_case()
outcome = uninstall(root, registry, runner, package="vlc.install")
check("vlc.install with its wrapper vlc installed: removed", result(outcome) == (True, 0, None))
check("...the wrapper first, on its own, then vlc.install",
      runner.packages() == [("vlc", "uninstall"), ("vlc.install", "uninstall")])
check("...never -x, the flag that would take dependencies with it",
      not any(flag in a for a in runner.argv for flag in ("-x", "--force-dependencies")))
check("...Chocolatey's \"uninstalled 1/2 packages\" for the wrapper was a success, not a failure",
      "Chocolatey uninstalled 1/2 packages." in WRAPPER_PLAYS[("vlc", "uninstall")].output
      and WRAPPER_PLAYS[("vlc", "uninstall")].code == 0)
check("...chocolatey-core.extension, its other dependency, is left alone",
      exists(root, "lib", "chocolatey-core.extension"))
check("...and the outcome says the wrapper went first", outcome.outcome == "removed, after its wrapper vlc")

root, registry, runner = vlc_case()
runner.plays[("vlc", "uninstall")] = Play("c05b-wrapper.txt", libs=[])
outcome = uninstall(root, registry, runner, package="vlc.install")
check("the wrapper exits 0 but lib\\vlc is still there: failed, and vlc.install never runs",
      (outcome.ok, runner.packages()) == (False, [("vlc", "uninstall")]))
root, registry, runner = vlc_case()
runner.plays[("vlc", "uninstall")] = Play("c07a-flag.txt")
outcome = uninstall(root, registry, runner, package="vlc.install")
check("the wrapper fails with Chocolatey's error: choco_error, and vlc.install never runs",
      result(outcome) == (False, UNSIGNED, "choco_error") and runner.packages() == [("vlc", "uninstall")])

root, registry, runner = vlc_case()
lib(root, "vlc-skins", dependencies=["vlc"])
check("something depends on the wrapper: choco_shared_dependency, nothing run",
      result(uninstall(root, registry, runner, package="vlc.install")) == (False, None, "choco_shared_dependency")
      and runner.argv == [])
root, registry, runner = vlc_case()
records(root, "vlc", "3.0.23", fixture("vlc.install.3.0.23.registry", CHOCO_FIXTURES))
check("the would-be wrapper recorded a key of its own: choco_shared_dependency, nothing run",
      result(uninstall(root, registry, runner, package="vlc.install")) == (False, None, "choco_shared_dependency")
      and runner.argv == [])
root, registry, runner = vlc_case()
records(root, "vlc", "3.0.23", b"<garbage")
check("the wrapper's records cannot be read: choco_records_unreadable, nothing run",
      result(uninstall(root, registry, runner, package="vlc.install")) == (False, None, "choco_records_unreadable")
      and runner.argv == [])
root, registry, runner = vlc_case()
lib(root, "kodi", dependencies=["vlc.install"])
check("a second package depends on it: choco_shared_dependency, nothing run",
      result(uninstall(root, registry, runner, package="vlc.install")) == (False, None, "choco_shared_dependency")
      and runner.argv == [])
root = tree(functools.partial(vlc, wrapper=False))
lib(root, "vlcplayer", dependencies=["vlc.install"])
registry = FakeRegistry(VLC_KEY)
runner = Chocolatey(root, registry, dict(WRAPPER_PLAYS))
check("a dependent that is not named for it (vlcplayer, not vlc): choco_shared_dependency",
      result(uninstall(root, registry, runner, package="vlc.install")) == (False, None, "choco_shared_dependency")
      and runner.argv == [])
root, registry, runner = vlc_case()
lib(root, "broken", data=b"<garbage")
check("an installed package that cannot be read might depend on it: choco_records_unreadable",
      result(uninstall(root, registry, runner, package="vlc.install")) == (False, None, "choco_records_unreadable")
      and runner.argv == [])
root = tree(functools.partial(vlc, wrapper=False))
registry = FakeRegistry(VLC_KEY)
runner = Chocolatey(root, registry, dict(WRAPPER_PLAYS))
check("vlc.install with no wrapper installed: only vlc.install runs",
      result(uninstall(root, registry, runner, package="vlc.install")) == (True, 0, None)
      and runner.packages() == [("vlc.install", "uninstall")])

# A .portable owner is a wrapper's too.
root = tree()
lib(root, "foo.portable", "2.0")
records(root, "foo.portable", "2.0", fixture("putty.install.0.85.0.registry"))
lib(root, "foo", "2.0", dependencies=["foo.portable"])
records(root, "foo", "2.0")
registry = FakeRegistry(PUTTY_KEY)
runner = Chocolatey(root, registry, {("foo", "uninstall"): Play("c05b-wrapper.txt", libs=["foo"]),
                                     ("foo.portable", "uninstall"): Play("c02-uninstall.txt", libs=["foo.portable"],
                                                                         keys=[PUTTY_GUID])})
check("foo.portable with its wrapper foo: the wrapper first, then removed",
      result(uninstall(root, registry, runner, package="foo.portable")) == (True, 0, None)
      and runner.packages() == [("foo", "uninstall"), ("foo.portable", "uninstall")])

# ---------------------------------------------------------------------------
print("5. Already gone: the cleanup")
# ---------------------------------------------------------------------------
CLEANUP = ["-y", "-n", "--skip-autouninstaller", "--no-progress"]
root, registry = putty_case(key("{0}", "Other", "Other"))
runner = Chocolatey(root, registry, {("putty.install", "cleanup"): Play("c04-cleanup.txt", libs=["putty.install"])})
outcome = uninstall(root, registry, runner)
check("removed through Settings: already_removed", result(outcome) == (True, 0, "already_removed"))
check("...by the cleanup alone, with -n --skip-autouninstaller, by choco.exe's full path",
      runner.argv == [[os.path.join(root, "bin", "choco.exe"), "uninstall", "putty.install"] + CLEANUP])
check("...and Chocolatey no longer has it", not exists(root, "lib", "putty.install"))

root, registry, runner = vlc_case()
registry.remove("VLC media player")
runner.plays.update({("vlc", "cleanup"): Play("c04-cleanup.txt", libs=["vlc"]),
                     ("vlc.install", "cleanup"): Play("c04-cleanup.txt", libs=["vlc.install"])})
check("vlc.install removed through Settings, its wrapper still installed: the cleanup, wrapper first",
      result(uninstall(root, registry, runner, package="vlc.install")) == (True, 0, "already_removed")
      and runner.packages() == [("vlc", "cleanup"), ("vlc.install", "cleanup")])

for label, play in (("Chocolatey's error", Play("c07a-flag.txt")),
                    ("exit 0, but lib\\putty.install still there", Play("c04-cleanup.txt")),
                    ("a timeout", Play(output="", timed_out=True)),
                    ("choco.exe would not start", Play(raises=OSError("no choco")))):
    root, registry = putty_case(key("{0}", "Other", "Other"))
    runner = Chocolatey(root, registry, {("putty.install", "cleanup"): play})
    logs.take()
    outcome = uninstall(root, registry, runner)
    lines = logs.take()
    check(f"the cleanup fails ({label}): still already_removed - the software is gone - with no exit code",
          result(outcome) == (True, None, "already_removed"))
    check(f"...and agent.log says the cleanup did not finish ({label})",
          any("did not finish" in line and "already_removed" in line for line in lines))

# ---------------------------------------------------------------------------
print("6. The uninstall, and each exit code")
# ---------------------------------------------------------------------------
UNINSTALL = ["-y", "--fail-on-autouninstaller", "--no-progress"]
NORMAL = Play("c02-uninstall.txt", libs=["putty.install"], keys=[PUTTY_GUID])


def putty_run(play, folder_signal=None):
    root, registry = putty_case()
    runner = Chocolatey(root, registry, {("putty.install", "uninstall"): play})
    return uninstall(root, registry, runner, folder_signal=folder_signal), runner, root


outcome, runner, root = putty_run(NORMAL)
check("PuTTY, as captured: removed", result(outcome) == (True, 0, None))
check("...choco.exe by its full path with the agent's flags, once",
      runner.argv == [[os.path.join(root, "bin", "choco.exe"), "uninstall", "putty.install"] + UNINSTALL])
check("...and its record folder survives, as on the VM - which is why it never means installed",
      exists(root, ".chocolatey", "putty.install.0.85.0", ".registry"))

root, registry = putty_case(unknown=True)
runner = Chocolatey(root, registry, {("putty.install", "uninstall"): Play("c03-uninstall.txt", libs=["putty.install"])})


def seeing(argv, runner=runner, registry=registry):
    out = Chocolatey.__call__(runner, argv)
    registry.unknown = False
    registry.remove(PUTTY_GUID)
    return out


check("whether it is gone cannot be told beforehand: Chocolatey runs, says \"uninstalled already by other "
      "means\", exits 0, and that verifies as removed", result(uninstall(root, registry, seeing)) == (True, 0, None))

# Made up: the VM capture never saw these codes. c02's output, not its code.
c02 = capture("c02-uninstall.txt")[0]
MADE_UP = [
    ("3010, gone", Play(output=c02, code=3010, libs=["putty.install"], keys=[PUTTY_GUID]), (True, 3010, "removed_restart_needed")),
    ("1605, gone", Play(output=c02, code=1605, libs=["putty.install"], keys=[PUTTY_GUID]), (True, 1605, "already_removed")),
    ("1614, gone", Play(output=c02, code=1614, libs=["putty.install"], keys=[PUTTY_GUID]), (True, 1614, "already_removed")),
    ("1614, still listed", Play(output=c02, code=1614, libs=["putty.install"]), (False, 1614, "still_listed")),
    ("0, still listed - --fail-on-autouninstaller is not the guard",
     Play(output=c02, code=0, libs=["putty.install"]), (False, 0, "still_listed")),
    ("1614, Windows' list unreadable afterwards",
     Play(output=c02, code=1614, libs=["putty.install"], keys=[PUTTY_GUID], blinds=True),
     (True, 1614, "removal_unverified")),
    ("1641", Play(output=c02, code=1641), (True, 1641, "restart_started")),
    ("350", Play(output=c02, code=350), (False, 350, "choco_restart_pending")),
    ("2, a package's own code", Play(output=c02, code=2), (False, 2, "installer_exit_code")),
]
for label, play, expected in MADE_UP:
    outcome, runner, root = putty_run(play)
    check(f"exit {label} (made-up output): {expected}", result(outcome) == expected)
outcome, runner, root = putty_run(Play(output=c02, code=0, libs=["putty.install"], keys=[PUTTY_GUID]),
                                  folder_signal=lambda folder: agent.SIGNAL_PRESENT)
check("PuTTY's snapshot records a blank install folder, which says nothing: removed, never folder_left",
      result(outcome) == (True, 0, None))
root, registry, runner = vlc_case()
outcome = uninstall(root, registry, runner, package="vlc.install", folder_signal=lambda folder: agent.SIGNAL_PRESENT)
check("vlc.install gone with C:\\Program Files\\VideoLAN\\VLC left behind: removed_folder_left",
      result(outcome) == (True, 0, "removed_folder_left"))

root = tree(functools.partial(vlc, wrapper=False))
registry = FakeRegistry(VLC_KEY)
runner = Chocolatey(root, registry, {("vlc.install", "uninstall"): Play("c07a-flag.txt")})
outcome = uninstall(root, registry, runner, package="vlc.install")
check("VLC's uninstaller deleted, as captured: choco_error with Chocolatey's -1",
      result(outcome) == (False, UNSIGNED, "choco_error"))
check("...and Chocolatey kept it", exists(root, "lib", "vlc.install"))

root = tree(functools.partial(vlc, wrapper=False))
registry = FakeRegistry(VLC_KEY)
runner = Chocolatey(root, registry, {("vlc.install", "uninstall"): Play("c05a-owner-first.txt")})
check("Chocolatey itself refuses, \"depends on it\" (a dependent appeared after the check): "
      "choco_shared_dependency with its exit 1",
      result(uninstall(root, registry, runner, package="vlc.install")) == (False, 1, "choco_shared_dependency"))

# Chocolatey says "is not installed": something removed it between the
# check and the run.
for label, keys, expected in (("Windows still lists it", [], (False, 1, "choco_not_managed")),
                              ("Windows no longer lists it", [PUTTY_GUID], (True, None, "already_removed"))):
    root, registry = putty_case()
    runner = Chocolatey(root, registry, {("putty.install", "uninstall"):
                                         Play("c01-real-id-not-installed.txt", libs=["putty.install"], keys=keys)})
    check(f"\"is not installed\" in between, and {label}: {expected}",
          result(uninstall(root, registry, runner)) == expected)
root, registry = putty_case()
runner = Chocolatey(root, registry, {("putty.install", "uninstall"): Play("c01-real-id-not-installed.txt")})
check("\"is not installed\" while lib\\putty.install is still there: choco_error, not a guess",
      result(uninstall(root, registry, runner)) == (False, 1, "choco_error"))

outcome, runner, root = putty_run(Play(output="", timed_out=True))
check("a timeout: timed_out, no exit code", result(outcome) == (False, None, "timed_out"))
outcome, runner, root = putty_run(Play(raises=OSError("no choco")))
check("choco.exe cannot be started: launch_failed", result(outcome) == (False, None, "launch_failed"))

# ---------------------------------------------------------------------------
print("7. The process: full path, no shell, stdin closed, output kept")
# ---------------------------------------------------------------------------
check("the agent's own command line, by Chocolatey's full path",
      agent.choco_uninstall_argv("vlc") == [agent.CHOCO_EXE, "uninstall", "vlc"] + UNINSTALL
      and agent.CHOCO_EXE == r"C:\ProgramData\chocolatey\bin\choco.exe")
check("...and the cleanup's", agent.choco_uninstall_argv("vlc", cleanup=True)
      == [agent.CHOCO_EXE, "uninstall", "vlc"] + CLEANUP)

popened = []


class FakePopen:
    def __init__(self, argv, **kwargs):
        popened.append((argv, kwargs))
        self.pid = 4242


def no_job():
    raise OSError("no jobs here")


agent.start_install_process(["choco.exe", "uninstall", "vlc"], "OUT", job_factory=no_job, popen=FakePopen, shell=False)
check("stdin is closed, so the wrapper's \"uninstall vlc.install as well?\" is answered at once",
      popened and popened[0][1]["stdin"] is agent.subprocess.DEVNULL)
check("...no shell, and the output goes where it is sent", popened[0][1]["shell"] is False
      and popened[0][1]["stdout"] == "OUT")

started = []
wrapper_text = capture("c05b-wrapper.txt")[0]


class Done:
    returncode = 0

    def poll(self):
        return 0


def fake_start(argv, stdout, shell=True):
    """Writes as choco.exe does: raw bytes to the file, not through Python."""
    started.append((argv, stdout, shell, getattr(stdout, "name", None)))
    if hasattr(stdout, "fileno"):
        stdout.flush()
        os.write(stdout.fileno(), wrapper_text.encode("utf-8"))
    return Done(), None


real_start = agent.start_install_process
agent.start_install_process = fake_start
try:
    code, timed_out, whole, output = agent.run_choco_uninstall(agent.choco_uninstall_argv("vlc"))
finally:
    agent.start_install_process = real_start
check("run_choco_uninstall runs the argv as given, with no shell",
      started and started[0][0] == agent.choco_uninstall_argv("vlc") and started[0][2] is False)
check("...writing to a file, never a pipe and never discarded",
      started and started[0][1] is not agent.subprocess.DEVNULL and started[0][3])
check("...and hands back exactly what choco.exe printed, line by line",
      (code, timed_out, output.splitlines()) == (0, False, wrapper_text.splitlines()))
check("...then removes the file", started and started[0][3] and not os.path.exists(started[0][3]))

# ---------------------------------------------------------------------------
print("8. Errors: never a removal, never a crash; one summary line")
# ---------------------------------------------------------------------------


def boom(*args, **kwargs):
    raise RuntimeError("stage exploded")


def bound(case=None, **overrides):
    """UNINSTALLERS["choco"] bound to one vlc case's fakes, as the poll would
    call it. Verification watches that case's registry."""
    root, registry, runner = case or vlc_case()
    clock = Clock()
    defaults = dict(registry=registry, read=lambda sid: registry.snapshot(), user_sid=lambda: SID, run=runner,
                    folders=FOLDERS, choco_exe=os.path.join(root, "bin", "choco.exe"),
                    verify=lambda t, c: agent.verify_removal(t, c, registry, clock.time, clock.sleep, lambda: None,
                                                             lambda f: agent.SIGNAL_ABSENT))
    defaults.update(overrides)
    return functools.partial(agent.uninstall_choco, **defaults)


JOB = {"id": 7, "action": "uninstall", "method": "choco", "package_id": "vlc.install"}
real_choco = agent.UNINSTALLERS.get("choco")


def restore_choco():
    agent.UNINSTALLERS.pop("choco", None)
    if real_choco is not None:
        agent.UNINSTALLERS["choco"] = real_choco


try:
    for stage, overrides in (("asking which account the agent runs as", {"user_sid": boom}),
                             ("reading Windows' program list", {"read": boom}),
                             ("running choco.exe", {"run": boom}),
                             ("verifying", {"verify": boom})):
        agent.UNINSTALLERS["choco"] = bound(**overrides)
        logs.take()
        outcome = agent.run_uninstall_job(JOB)
        lines = [l for l in logs.take() if l.startswith("Uninstall summary:")]
        check(f"an exception while {stage}: failed agent_error, one summary line saying so",
              (outcome.ok, outcome.reason, outcome.exit_code) == (False, "agent_error", None)
              and len(lines) == 1 and "agent_error" in lines[0])
    agent.UNINSTALLERS["choco"] = bound()
    logs.take()
    agent.run_uninstall_job(JOB)
    lines = logs.take()
    check("the summary line names the package, the wrapper step, the exit code and the verification",
          [l for l in lines if l.startswith("Uninstall summary:")]
          == ["Uninstall summary: job 7, choco vlc.install: removed, after its wrapper vlc; exit code 0; "
              "verification 0s."])
    check("...and what Chocolatey printed goes to agent.log, for both runs",
          sum(1 for l in lines if l.startswith("Chocolatey output for ")) == 2
          and any("Would you like to uninstall vlc.install as well?" in l for l in lines))
finally:
    restore_choco()
check("the poll hands a Chocolatey job to uninstall_choco", agent.UNINSTALLERS.get("choco") is agent.uninstall_choco)

# ---------------------------------------------------------------------------
print("9. The server: every result kept as sent, the capability, end to end")
# ---------------------------------------------------------------------------
import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()
import app as appmod  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402
appmod.app.config["TESTING"] = True
client = appmod.app.test_client()

check("the agent advertises Chocolatey, beside MSI and (since Phase 3a) EXE, per-user EXE too since 2026-10-07",
      agent.agent_capabilities() == ["uninstall:choco", "uninstall:exe", "uninstall:exe-user", "uninstall:msi"])
for reason in ("choco_not_managed", "choco_shared_dependency", "choco_records_unreadable", "already_removed",
               "choco_error", "choco_restart_pending", "restart_started", "installer_exit_code", "still_listed",
               "removed_restart_needed", "removed_folder_left", "removal_unverified", "timed_out", "launch_failed", "choco_missing",
               "uninstall_job_invalid", "protected_software", "shared_runtime", "registry_unreadable"):
    check(f"this suite produced {reason}", any(r == reason for _, _, r in produced))


def db():
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


conn = db()
conn.execute("UPDATE admins SET password_hash = ?, must_change_password = 0 WHERE username = 'admin'",
             (generate_password_hash("Str0ng!Passw0rd"),))
pc = conn.execute("INSERT INTO computers (hostname, status) VALUES ('LAB-C', 'online')").lastrowid
conn.commit()
mismatches = []
for ok, exit_code, reason in sorted(produced, key=repr):
    if ok and reason is None and exit_code == 0:
        continue                                       # the plain removal: section end to end below
    row = conn.execute("INSERT INTO installations (computer_id, software_name, method, record_key, state) "
                       "VALUES (?, 'X', 'choco', ?, 'installed')", (pc, secrets.token_hex(8))).lastrowid
    job = conn.execute("INSERT INTO jobs (computer_hostname, software_id, status, action, installation_id, started_at) "
                       "VALUES ('LAB-C', 0, 'in_progress', 'uninstall', ?, CURRENT_TIMESTAMP)", (row,)).lastrowid
    conn.commit()
    body = {"computer_hostname": "LAB-C", "status": "completed" if ok else "failed", "exit_code": exit_code,
            "reason": reason}
    client.post(f"/api/agent/jobs/{job}/report", json=body)
    kept = tuple(conn.execute("SELECT status, exit_code, result_reason FROM jobs WHERE id = ?", (job,)).fetchone())
    if kept != (body["status"], exit_code, reason):
        mismatches.append(((ok, exit_code, reason), kept))
check(f"every result this suite produced is kept exactly as sent ({len(produced)} shapes; mismatches: "
      f"{mismatches})", not mismatches)
for reason, status, code in (("choco_shared_dependency", "failed", 0), ("choco_not_managed", "failed", 2),
                             ("already_removed", "completed", 1)):
    row = conn.execute("INSERT INTO installations (computer_id, software_name, method, record_key, state) "
                       "VALUES (?, 'X', 'choco', ?, 'installed')", (pc, secrets.token_hex(8))).lastrowid
    job = conn.execute("INSERT INTO jobs (computer_hostname, software_id, status, action, installation_id, started_at) "
                       "VALUES ('LAB-C', 0, 'in_progress', 'uninstall', ?, CURRENT_TIMESTAMP)", (row,)).lastrowid
    conn.commit()
    client.post(f"/api/agent/jobs/{job}/report", json={"computer_hostname": "LAB-C", "status": status,
                                                        "exit_code": code, "reason": reason})
    kept = conn.execute("SELECT result_reason FROM jobs WHERE id = ?", (job,)).fetchone()[0]
    check(f"...but {reason} with exit {code}, which it never comes with, is not kept", kept is None)
conn.close()

client.post("/login", data={"username": "admin", "password": "Str0ng!Passw0rd"})
agent.COMPUTER_HOSTNAME = "LAB-CE2E"
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
    pc_row = conn.execute("SELECT id, capabilities FROM computers WHERE hostname = 'LAB-CE2E'").fetchone()
    check("the real poll registers the PC as able to uninstall Chocolatey",
          "uninstall:choco" in (pc_row["capabilities"] or "").split())
    ROW = conn.execute(
        "INSERT INTO installations (computer_id, software_name, software_version, method, record_key, "
        "choco_package_id, state) VALUES (?, 'VLC media player', '3.0.23', 'choco', ?, 'vlc.install', 'installed')",
        (pc_row["id"], database.installation_record_key("choco", package_id="vlc.install"))).lastrowid
    conn.commit()
    reply = client.post("/uninstall-batch", data={"installation_ids": [str(ROW)]}).get_json()
    check("the create route queues it", reply["ok"] and len(reply["created"]) == 1)
    root, registry, runner = case = vlc_case()
    agent.UNINSTALLERS["choco"] = bound(case)
    agent.poll_once()
    job = conn.execute("SELECT * FROM jobs WHERE installation_id = ?", (ROW,)).fetchone()
    row = conn.execute("SELECT * FROM installations WHERE id = ?", (ROW,)).fetchone()
    check("end to end: the job the agent got names only the package",
          seen_jobs and set(seen_jobs[-1]) == {"id", "action", "method", "package_id"}
          and seen_jobs[-1]["package_id"] == "vlc.install")
    check("...the wrapper, then vlc.install, ran", runner.packages() == [("vlc", "uninstall"),
                                                                         ("vlc.install", "uninstall")])
    check("...the job completed with no reason (a plain removal)",
          (job["status"], job["exit_code"], job["result_reason"]) == ("completed", 0, None))
    check("...and the installations row is removed by that job",
          (row["state"], row["state_reason"], row["uninstall_job_id"]) == ("removed", "removed", job["id"]))
    conn.close()
finally:
    agent.post_json = real_post
    restore_choco()

print()
print(f"{passed} passed, {len(failed)} failed")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

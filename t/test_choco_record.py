"""The install record of a Chocolatey job: which package owns what it put on
the PC, read from Chocolatey's own records and never guessed. Records are
built from the REAL ones in t/fixtures/choco (hash-pinned elsewhere).

    python t/test_choco_record.py --agent C:\\Appease\\agent.py
"""
import io
import os
import secrets
import shutil
import sys
import tempfile
import warnings
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures", "choco")

passed = 0
failed = []


def check(label, condition):
    global passed
    if condition:
        passed += 1
    else:
        failed.append(label)
        print(f"  FAIL: {label}")


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
if not os.path.isfile(os.path.join(FIXTURES, "vlc.install.3.0.23.registry")):
    print("COULD NOT RUN - t/fixtures/choco is missing.")
    sys.exit(3)

work = tempfile.mkdtemp(prefix="appease_chocorec_")
shutil.copy(AGENT_PATH, os.path.join(work, "agent.py"))
sys.path.insert(0, work)
import agent  # noqa: E402

if not hasattr(agent, "build_choco_record"):
    print("COULD NOT RUN - agent.py predates Chocolatey install records.")
    sys.exit(3)


class Captured(agent.logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())

    def take(self):
        found, self.messages = self.messages, []
        return "\n".join(found)


for handler in list(agent.logger.handlers):
    if type(handler) is agent.logging.StreamHandler:
        agent.logger.removeHandler(handler)
logs = Captured()
agent.logger.addHandler(logs)

HKLM, HKCU = agent.HKLM_NAME, agent.HKCU_NAME
UNINSTALL = agent.UNINSTALL_SUBKEY
PAINT_GUID = "{C6A29A65-A91B-4F4A-A1B5-B904AC61255D}"
REDACTED_SID = "S-1-5-21-1111111111-2222222222-3333333333-1001"
FOLDERS = [agent._folder_key(p) for p in ("C:\\Appease", "C:\\Program Files\\Python314")]
OWN_PYTHON = "Python {}.{}.2 (64-bit)".format(*sys.version_info[:2])

# The VM's own capture of "already installed" (test_job_results.py's
# Fixture A).
FIXTURE_A = r"""Chocolatey v2.7.3
Installing the following packages:
paint.net
By installing, you accept licenses for the packages.
paint.net v5.1.12 already installed.
 Use --force to reinstall, specify a version to install, or try upgrade.

Chocolatey installed 0/1 packages.
 See the log for details (C:\ProgramData\chocolatey\logs\chocolatey.log).

Warnings:
 - paint.net - paint.net v5.1.12 already installed.
 Use --force to reinstall, specify a version to install, or try upgrade.
"""


def fixture(name):
    with open(os.path.join(FIXTURES, name), "rb") as handle:
        return handle.read()


def record_bytes(text):
    """A synthetic record the way Chocolatey writes them: BOM and CRLF."""
    return b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8")


def nuspec(package_id, version, dependencies=()):
    deps = "".join(f'\n      <dependency id="{d}" version="1.0" />' for d in dependencies)
    return record_bytes(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://schemas.microsoft.com/packaging/2015/06/nuspec.xsd">\n'
        f"  <metadata>\n    <id>{package_id}</id>\n    <version>{version}</version>\n"
        f"    <dependencies>{deps}\n    </dependencies>\n  </metadata>\n</package>")


def snapshot_file(keys, user=REDACTED_SID):
    """keys: (hive, subkey name, view, display name, display version,
    install location)."""
    body = "".join(
        f'\n    <key installerType="Unknown" displayName="{display}" displayVersion="{version}">\n'
        f"      <RegistryView>{view}</RegistryView>\n"
        f"      <KeyPath>{hive}\\{UNINSTALL}\\{name}</KeyPath>\n"
        f"      <InstallLocation><![CDATA[{location}]]></InstallLocation>\n    </key>"
        for hive, name, view, display, version, location in keys)
    return record_bytes(
        '<?xml version="1.0" encoding="utf-8"?>\n<registrySnapshot>\n'
        f"  <user>{user}</user>\n  <keys>{body}\n  </keys>\n</registrySnapshot>")


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)


def new_root():
    root = tempfile.mkdtemp(prefix="choco_", dir=work)
    os.makedirs(os.path.join(root, "lib"))
    os.makedirs(os.path.join(root, ".chocolatey"))
    return root


def add(root, lib_name, spec, folder=None, registry=None):
    write(os.path.join(root, "lib", lib_name, lib_name + ".nuspec"), spec)
    if folder:
        write(os.path.join(root, ".chocolatey", folder, ".files"), b"")
        if registry is not None:
            write(os.path.join(root, ".chocolatey", folder, ".registry"), registry)


def add_helpers(root):
    add(root, "chocolatey-core.extension", fixture("chocolatey-core.extension.nuspec"),
        "chocolatey-core.extension.1.4.0")
    add(root, "chocolatey-compatibility.extension", fixture("chocolatey-compatibility.extension.nuspec"),
        "chocolatey-compatibility.extension.1.0.0")


def paint_tree(registry="fixture"):
    root = new_root()
    add(root, "paint.net", fixture("paint.net.nuspec"), "paint.net.5.1.12",
        fixture("paint.net.5.1.12.registry") if registry == "fixture" else registry)
    add_helpers(root)
    return root


def vlc_tree():
    root = new_root()
    add(root, "vlc", fixture("vlc.nuspec"), "vlc.3.0.23")
    add(root, "vlc.install", fixture("vlc.install.nuspec"), "vlc.install.3.0.23",
        fixture("vlc.install.3.0.23.registry"))
    add_helpers(root)
    return root


def build(package, root, reason=None, before=None, after=None):
    return agent.build_choco_record(package, reason, before, after, root, FOLDERS)


def names_no_package(rec):
    return not ({"package_id", "hive", "key_name", "sid", "product_code"} & rec.keys())


# ---------------------------------------------------------------------------
print("1. Chocolatey's records, as the agent reads them")
# ---------------------------------------------------------------------------
for name in ("paint.net.5.1.12.registry", "vlc.install.3.0.23.registry", "gimp.3.0.6.1.registry",
             "GoogleChrome.142.0.7444.176.registry", "Opera.124.0.5705.15.registry"):
    path = os.path.join(work, name)
    write(path, fixture(name))
    user, keys = agent.read_snapshot(path)
    check(f"{name}: every key keeps the DisplayVersion Chocolatey recorded",
          bool(keys) and all(k["version"] for k in keys))
check("the VM's Chocolatey 2.7.3 record (Paint.NET) is read like the dev machine's 2.5.1 ones",
      agent.read_snapshot(os.path.join(work, "paint.net.5.1.12.registry"))[1][0]["version"] == "5.1.12")


# ---------------------------------------------------------------------------
print("2. Which package owns the entry")
# ---------------------------------------------------------------------------
logs.take()
rec = build("paint.net", paint_tree())
check("a direct package names itself", rec.get("package_id") == "paint.net")
check("...with its entry's name, version and key from Chocolatey's record",
      rec.get("display_name") == "Paint.NET" and rec.get("display_version") == "5.1.12"
      and rec.get("key_name") == PAINT_GUID and rec.get("hive") == HKLM and rec.get("view") == "Registry64")
check("...nothing unavailable, no SID for a machine-wide entry", "unavailable_reason" not in rec and "sid" not in rec)
check("...and nothing worth a warning", logs.take() == "")
check("deployed in other capitals, it names the package as Chocolatey spells it",
      build("Paint.NET", paint_tree()).get("package_id") == "paint.net")

rec = build("vlc", vlc_tree())
check("a wrapper (vlc) names the package that owns the entry (vlc.install)", rec.get("package_id") == "vlc.install")
check("...with that package's entry", rec.get("display_name") == "VLC media player"
      and rec.get("display_version") == "3.0.23" and "unavailable_reason" not in rec)
check("deploying vlc.install directly names it too", build("vlc.install", vlc_tree()).get("package_id") == "vlc.install")

root = new_root()
add(root, "gimp", nuspec("gimp", "3.0.6.1"), "gimp.3.0.6.1", fixture("gimp.3.0.6.1.registry"))
rec = build("gimp", root)
check("a per-user entry carries the SID of the user Chocolatey recorded",
      rec.get("hive") == HKCU and rec.get("sid") == REDACTED_SID and "unavailable_reason" not in rec)

PAINT_LIVE = agent.uninstall_entry(HKLM, None, "Registry64", PAINT_GUID.lower(),
                                   {"displayname": "Paint.NET", "displayversion": "5.1.13", "windowsinstaller": 1})
after = {"entries": {agent.entry_identity(PAINT_LIVE): PAINT_LIVE}, "user_readable": True}
check("the version Windows lists now wins over the one Chocolatey recorded",
      build("paint.net", paint_tree(), after=after).get("display_version") == "5.1.13")

root = new_root()
add(root, "suite", nuspec("suite", "1.0", ["suite.core"]), "suite.1.0",
    snapshot_file([(HKLM, "Suite", "Registry64", "Suite", "1.0", "")]))
add(root, "suite.core", nuspec("suite.core", "1.0"), "suite.core.1.0",
    snapshot_file([(HKLM, "Suite Core", "Registry64", "Suite Core", "1.0", "")]))
check("a package with its own entry is the owner even when a dependency has one too",
      build("suite", root).get("package_id") == "suite")


# ---------------------------------------------------------------------------
print("3. A dependency another package also needs is never named")
# ---------------------------------------------------------------------------
root = vlc_tree()
add(root, "vlc-skins", nuspec("vlc-skins", "1.0", ["vlc.install"]), "vlc-skins.1.0")
logs.take()
rec = build("vlc", root)
check("vlc.install also needed by another installed package: not named",
      rec.get("unavailable_reason") == agent.RECORD_REASON_CHOCO_SHARED_DEPENDENCY and names_no_package(rec))
check("...and the log says which package needs it", "vlc-skins" in logs.take())
check("...but deploying vlc.install itself still names it - it is no one's dependency then",
      build("vlc.install", root).get("package_id") == "vlc.install")

root = vlc_tree()
write(os.path.join(root, "lib", "broken", "broken.nuspec"), b"not xml")
logs.take()
rec = build("vlc", root)
check("an installed package whose nuspec cannot be read might need it: not named",
      rec.get("unavailable_reason") == agent.RECORD_REASON_CHOCO_RECORDS_UNREADABLE and names_no_package(rec))
log = logs.take()
check("...and the log names the package that blocked the record",
      "installed package broken could not be read, which blocked the record" in log)

root = vlc_tree()
os.makedirs(os.path.join(root, "lib", ".chocolateyPending"))
write(os.path.join(root, "lib", "stray.txt"), b"x")
check("hidden folders and stray files in lib are not packages",
      build("vlc", root).get("package_id") == "vlc.install")

def packed(package_id, spec_bytes, member=None):
    """A .nupkg as Chocolatey keeps one: a zip with the nuspec inside."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(member or package_id + ".nuspec", spec_bytes)
        archive.writestr("tools/chocolateyInstall.ps1", "# nothing")
    return buffer.getvalue()


root = vlc_tree()
write(os.path.join(root, "lib", "chocolatey", "chocolatey.nupkg"), packed("chocolatey", nuspec("chocolatey", "2.5.1")))
check("a package kept only as its .nupkg (Chocolatey's own) is read from inside it",
      agent.choco_dependents(root, "vlc.install") == ({"vlc"}, None))
check("...so a wrapper is still named on a machine that has Chocolatey installed",
      build("vlc", root).get("package_id") == "vlc.install")
root = vlc_tree()
write(os.path.join(root, "lib", "skins", "skins.nupkg"), packed("skins", nuspec("skins", "1.0", ["vlc.install"])))
check("a .nupkg-only package that needs the dependency makes it shared",
      build("vlc", root).get("unavailable_reason") == agent.RECORD_REASON_CHOCO_SHARED_DEPENDENCY)
for label, data in (("a .nupkg that is not a zip", b"not a zip"),
                    ("a .nupkg without the nuspec", packed("odd", nuspec("odd", "1.0"), member="other.nuspec")),
                    ("a .nupkg whose nuspec is not XML", packed("odd", b"<not xml")),
                    ("neither .nuspec nor .nupkg", None)):
    root = vlc_tree()
    if data is None:
        os.makedirs(os.path.join(root, "lib", "odd"))
    else:
        write(os.path.join(root, "lib", "odd", "odd.nupkg"), data)
    logs.take()
    check(f"{label}: it might need it, so not named",
          build("vlc", root).get("unavailable_reason") == agent.RECORD_REASON_CHOCO_RECORDS_UNREADABLE)
    check(f"{label}: the log names it as what blocked the record",
          "installed package odd could not be read, which blocked the record" in logs.take())
    if data is not None:
        try:
            direct = agent.read_packed_nuspec(os.path.join(root, "lib", "odd", "odd.nupkg"), "odd")
        except Exception as exc:  # noqa: BLE001 - raising IS the failure
            direct = f"raised {type(exc).__name__}"
        check(f"{label}: read_packed_nuspec itself answers None, never raises", direct is None)
root = vlc_tree()
write(os.path.join(root, "lib", "skins", "skins.nupkg"), packed("skins", nuspec("skins", "1.0", ["vlc.install"]),
                                                                member="Skins.NUSPEC"))
check("the nuspec inside is found whatever its capitals",
      build("vlc", root).get("unavailable_reason") == agent.RECORD_REASON_CHOCO_SHARED_DEPENDENCY)

buffer = io.BytesIO()
with warnings.catch_warnings():
    warnings.simplefilter("ignore")  # zipfile warns about the duplicate name, which is the point
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("odd.nuspec", nuspec("odd", "1.0"))
        archive.writestr("odd.nuspec", nuspec("odd", "1.0", ["vlc.install"]))
root = vlc_tree()
write(os.path.join(root, "lib", "odd", "odd.nupkg"), buffer.getvalue())
check("a .nupkg holding two nuspecs is not understood: not named",
      build("vlc", root).get("unavailable_reason") == agent.RECORD_REASON_CHOCO_RECORDS_UNREADABLE)

padding = "<!-- " + "x" * agent.CHOCO_MAX_RECORD_BYTES + " -->"
big = nuspec("big", "1.0").decode("utf-8-sig").replace("<package ", padding + "<package ", 1).encode()
root = vlc_tree()
write(os.path.join(root, "lib", "big", "big.nupkg"), packed("big", big))
check("a nuspec larger than any real one is not read: not named",
      build("vlc", root).get("unavailable_reason") == agent.RECORD_REASON_CHOCO_RECORDS_UNREADABLE)

root = vlc_tree()
write(os.path.join(root, "lib", "skins", "skins.nupkg"), packed("skins", nuspec("skins", "1.0", ["vlc.install"])))
write(os.path.join(root, "lib", "skins", "skins.nuspec"), nuspec("skins", "1.0"))
check("a .nuspec file, when there is one, is what counts",
      build("vlc", root).get("package_id") == "vlc.install")

root = new_root()
add(root, "suite", nuspec("suite", "1.0", ["part.a", "part.b"]), "suite.1.0")
for part in ("part.a", "part.b"):
    add(root, part, nuspec(part, "1.0"), f"{part}.1.0",
        snapshot_file([(HKLM, part, "Registry64", part, "1.0", "")]))
rec = build("suite", root)
check("two dependencies with entries and none of its own: several, no package named",
      rec.get("unavailable_reason") == agent.RECORD_REASON_SEVERAL_NEW_ENTRIES and names_no_package(rec))


# ---------------------------------------------------------------------------
print("4. Missing or unreadable records: never a guess")
# ---------------------------------------------------------------------------
rec = build("paint.net", paint_tree(registry=None))
check("no .registry snapshot: no_registry_entry, no package named",
      rec.get("unavailable_reason") == agent.RECORD_REASON_NO_REGISTRY_ENTRY and names_no_package(rec))
logs.take()
rec = build("paint.net", paint_tree(registry=b"<registrySnapshot><keys><bogus/></keys></registrySnapshot>"))
check("a .registry not understood: choco_records_unreadable, no package named",
      rec.get("unavailable_reason") == agent.RECORD_REASON_CHOCO_RECORDS_UNREADABLE and names_no_package(rec))
log = logs.take()
check("...and the log names the package that blocked the record, and why",
      "package paint.net could not be read, which blocked the record" in log
      and ".registry not readable or not understood" in log)
logs.take()
check("a package Chocolatey has no lib folder for: unreadable, no package named",
      build("notinstalled", new_root()).get("unavailable_reason") == agent.RECORD_REASON_CHOCO_RECORDS_UNREADABLE)
check("...and the log names it as what blocked the record",
      "package notinstalled could not be read, which blocked the record" in logs.take())
root = vlc_tree()
shutil.rmtree(os.path.join(root, ".chocolatey", "vlc.install.3.0.23"))
logs.take()
build("vlc", root)
check("a dependency with no .chocolatey folder is named as the blocker, not the package deployed",
      "package vlc.install could not be read, which blocked the record" in logs.take())
for bad in ("vlc&whoami", "", None):
    rec = build(bad, vlc_tree())
    check(f"package id {bad!r}: unreadable, no package named",
          rec.get("unavailable_reason") == agent.RECORD_REASON_CHOCO_RECORDS_UNREADABLE and names_no_package(rec))

rec = build("python3&whoami", new_root())
check("an unsafe id that looks protected still names no package",
      rec.get("unavailable_reason") == agent.RECORD_REASON_CHOCO_RECORDS_UNREADABLE and names_no_package(rec))

root = new_root()
add(root, "tool", nuspec("tool", "1.0"), "tool.1.0", snapshot_file([
    (HKLM, "Tool-helper", "Registry64", "", "", ""),
    (HKLM, "Tool", "Registry64", "Tool", "2.0", ""),
]))
rec = build("tool", root)
check("of several recorded keys, the one with a name describes the package",
      rec.get("key_name") == "Tool" and rec.get("display_name") == "Tool" and rec.get("display_version") == "2.0")

real_walk = agent.walk_choco_package


def exploding_walk(*args):
    raise RuntimeError("probe: the walk broke")


agent.walk_choco_package = exploding_walk
try:
    logs.take()
    try:
        rec = build("vlc", vlc_tree())
        raised = False
    except BaseException:  # noqa: BLE001 - that IS the failure being checked for
        rec, raised = {}, True
finally:
    agent.walk_choco_package = real_walk
check("an unexpected error never escapes", not raised)
check("...it only downgrades the record to unreadable, naming no package",
      rec.get("unavailable_reason") == agent.RECORD_REASON_CHOCO_RECORDS_UNREADABLE and names_no_package(rec))
check("...and is logged", "reading Chocolatey's records failed" in logs.take())

real_guard = agent.is_protected_entry


def exploding_guard(*args, **kwargs):
    raise RuntimeError("probe: the guard broke")


agent.is_protected_entry = exploding_guard
try:
    rec = build("vlc", vlc_tree())
finally:
    agent.is_protected_entry = real_guard
check("an error after the owner was found still leaves no package named",
      rec.get("unavailable_reason") == agent.RECORD_REASON_CHOCO_RECORDS_UNREADABLE and names_no_package(rec))
logs.take()


# ---------------------------------------------------------------------------
print("5. Already installed, reinstalled, upgraded")
# ---------------------------------------------------------------------------
result = agent.classify_install_result("choco install paint.net -y", 0, FIXTURE_A)
check("(the VM's real 'already installed' output classifies as such)",
      result.reason == agent.REASON_CHOCO_ALREADY_INSTALLED)
check("'already installed' gives no record at all", build("paint.net", paint_tree(), result.reason) is None)
check("'already installed, Windows still lists it' gives no record at all",
      build("paint.net", paint_tree(), agent.REASON_CHOCO_CONFIRMED_INSTALLED) is None)
check("a forced reinstall is an install by AppEase: recorded",
      (build("paint.net", paint_tree(), agent.REASON_CHOCO_REINSTALLED) or {}).get("package_id") == "paint.net")
check("a reinstall Windows does not confirm is still one: recorded",
      (build("paint.net", paint_tree(), agent.REASON_CHOCO_REINSTALL_UNCONFIRMED) or {}).get("package_id")
      == "paint.net")
check("a restart-needed install is recorded",
      (build("paint.net", paint_tree(), agent.REASON_RESTART_NEEDED) or {}).get("package_id") == "paint.net")

old_opera = agent.uninstall_entry(HKLM, None, "Registry32", "Opera 123.0.5669.47",
                                  {"displayname": "Opera Stable 123.0.5669.47", "uninstallstring": "u"})
new_opera = agent.uninstall_entry(HKLM, None, "Registry32", "Opera 124.0.5705.15",
                                  {"displayname": "Opera Stable 124.0.5705.15", "uninstallstring": "u"})
root = new_root()
add(root, "opera", nuspec("opera", "124.0.5705.15"), "opera.124.0.5705.15", fixture("Opera.124.0.5705.15.registry"))
rec = build("opera", root,
            before={"entries": {agent.entry_identity(old_opera): old_opera}, "user_readable": True},
            after={"entries": {agent.entry_identity(new_opera): new_opera}, "user_readable": True})
check("an upgrade that re-keyed its entry reports the old entry as replaced",
      rec.get("replaced_entries") == [{"hive": HKLM, "view": "Registry32", "key_name": "Opera 123.0.5669.47"}])
check("...and still names the package", rec.get("package_id") == "opera")
check("no snapshots taken: an empty replaced list", build("paint.net", paint_tree()).get("replaced_entries") == [])
check("even a record that names no package reports what disappeared",
      build("paint.net", paint_tree(registry=None),
            before={"entries": {agent.entry_identity(old_opera): old_opera}, "user_readable": True},
            after={"entries": {}, "user_readable": True}).get("replaced_entries") != [])


# ---------------------------------------------------------------------------
print("6. Protected")
# ---------------------------------------------------------------------------
for package in ("chocolatey", "python", "python3", "Python314"):
    rec = build(package, new_root())
    check(f"{package}: protected, whatever Chocolatey recorded",
          rec.get("unavailable_reason") == agent.RECORD_REASON_PROTECTED and rec.get("package_id") == package)
root = new_root()
add(root, "mytools", nuspec("mytools", "1.0", ["python314"]), "mytools.1.0")
add(root, "python314", nuspec("python314", "3.14.8"), "python314.3.14.8",
    snapshot_file([(HKLM, "{X}", "Registry64", "Runtime for scripts", "3.14.8", "")]))
rec = build("mytools", root)
check("a wrapper whose owner is python*: protected, naming that owner",
      rec.get("unavailable_reason") == agent.RECORD_REASON_PROTECTED and rec.get("package_id") == "python314")
root = new_root()
add(root, "devkit", nuspec("devkit", "1.0"), "devkit.1.0",
    snapshot_file([(HKLM, "{Y}", "Registry64", OWN_PYTHON, "1", "")]))
check("an entry that is the agent's own Python, under any package name: protected",
      build("devkit", root).get("unavailable_reason") == agent.RECORD_REASON_PROTECTED)
root = new_root()
add(root, "tool", nuspec("tool", "1.0"), "tool.1.0",
    snapshot_file([(HKLM, "Tool", "Registry64", "Tool", "1", "C:\\Appease\\tool")]))
check("an entry installed inside C:\\Appease: protected", build("tool", root).get("unavailable_reason")
      == agent.RECORD_REASON_PROTECTED)
check("the python* rule is the server's, so it also covers a 'pythonista' - erring on the safe side",
      agent.PROTECTED_CHOCO_PACKAGE.match("pythonista") is not None)


# ---------------------------------------------------------------------------
print("7. The real server accepts every record, with the same rules")
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

check("every reason code the agent can send exists on the server, as one an agent may send",
      all(r in appmod.INSTALL_RECORD_REASONS and appmod.INSTALL_RECORD_REASONS[r].get("source", "agent") == "agent"
          for r in agent.RECORD_REASONS))
for sample in ("chocolatey", "Chocolatey", "python", "python3", "PYTHON314", "pythonista", "vlc", "chocolatey-core.extension",
               "ironpython", "py"):
    check(f"agent and server agree whether {sample!r} is protected",
          bool(agent.PROTECTED_CHOCO_PACKAGE.match(sample)) == bool(appmod.PROTECTED_CHOCO_PACKAGE.match(sample)))

root = vlc_tree()
add(root, "vlc-skins", nuspec("vlc-skins", "1.0", ["vlc.install"]), "vlc-skins.1.0")
samples = {
    "direct": (build("paint.net", paint_tree()), "choco:paint.net"),
    "wrapper": (build("vlc", vlc_tree()), "choco:vlc.install"),
    "per-user": (build("gimp", (lambda r: (add(r, "gimp", nuspec("gimp", "3.0.6.1"), "gimp.3.0.6.1",
                                                     fixture("gimp.3.0.6.1.registry")), r)[1])(new_root())),
                 "choco:gimp"),
    "shared dependency": (build("vlc", root), "none:software:7"),
    "no snapshot": (build("paint.net", paint_tree(registry=None)), "none:software:7"),
    "unreadable": (build("vlc&x", vlc_tree()), "none:software:7"),
    "protected": (build("python3", new_root()), "choco:python3"),
}
for label, (sample, key) in samples.items():
    try:
        cleaned = appmod.clean_install_record(sample, 7)
        accepted = cleaned["key"] == key and not cleaned["dropped"]
    except Exception as exc:  # noqa: BLE001 - a crash IS a refusal here
        accepted = False
        print(f"    server refused {label}: {type(exc).__name__}: {exc}")
    check(f"server accepts the {label} record, keyed {key}", accepted)
check("the server keeps a protected record protected", appmod.clean_install_record(samples["protected"][0], 7)["reason"]
      == "protected")


# ---------------------------------------------------------------------------
print("8. This machine's real Chocolatey, read-only")
# ---------------------------------------------------------------------------
REAL_ROOT = os.path.dirname(os.path.dirname(agent.CHOCO_EXE))
if os.name != "nt" or not os.path.isdir(os.path.join(REAL_ROOT, "lib")):
    print("  SKIPPED: section 8 - Windows with Chocolatey only")
else:
    names = [n for n in os.listdir(os.path.join(REAL_ROOT, "lib")) if not n.startswith(".")]
    check(f"every installed package's nuspec can be read here ({len(names)} packages)",
          all(agent.choco_dependents(REAL_ROOT, n)[0] is not None for n in names))
    if os.path.isfile(os.path.join(REAL_ROOT, "lib", "chocolatey", "chocolatey.nupkg")) and not \
            os.path.isfile(os.path.join(REAL_ROOT, "lib", "chocolatey", "chocolatey.nuspec")):
        check("Chocolatey's own package, kept only as its .nupkg, is read",
              agent.read_packed_nuspec(os.path.join(REAL_ROOT, "lib", "chocolatey", "chocolatey.nupkg"),
                                       "chocolatey") is not None)


print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for label in failed:
    print("  -", label)
print("=" * 56)
for handler in list(agent.logger.handlers):
    handler.close()
os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

"""The check agent.py runs after Chocolatey says "already installed". "Gone"
takes agreeing signals; anything missing, unreadable or not understood is
"can't verify" and forces nothing - a wrong forced reinstall is worse.

Fixtures in t/fixtures/choco/ are real records, redacted (see its README)
and hash-pinned below. Only the VM can show: a forced reinstall really
restoring Paint.NET; the S4U task's own registry reads, including whether
the installer's hive is loaded; Chocolatey 2.7.3's layout matching 2.5.1's.

    python t/test_choco_verify.py --agent C:\\Appease\\agent.py
"""
import hashlib
import itertools
import os
import secrets
import shutil
import sqlite3
import string
import subprocess
import sys
import tempfile
import threading

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


def skip(label, why):
    skipped.append(label)
    print(f"  SKIPPED: {label} - {why}")


def arg_path(name, default):
    for index, arg in enumerate(sys.argv):
        if arg == name and index + 1 < len(sys.argv):
            return os.path.abspath(sys.argv[index + 1])
        if arg.startswith(name + "="):
            return os.path.abspath(arg.split("=", 1)[1])
    return default


AGENT_PATH = arg_path("--agent", os.path.join(ROOT, "agent", "agent.py"))
SERVER_SRC = arg_path("--server", os.path.join(ROOT, "server"))
if not os.path.isfile(AGENT_PATH) or not os.path.isdir(SERVER_SRC):
    print("COULD NOT RUN - agent.py or server/ not found.")
    sys.exit(2)
if os.name != "nt":
    print("COULD NOT RUN - the registry and the stand-in choco.bat need Windows.")
    sys.exit(2)
if not os.path.isfile(os.path.join(FIXTURES, "paint.net.5.1.12.registry")):
    print("COULD NOT RUN - t/fixtures/choco is missing.")
    sys.exit(3)

work = tempfile.mkdtemp(prefix="appease_cv_")
server_dir = os.path.join(work, "server")
shutil.copytree(
    SERVER_SRC, server_dir,
    ignore=shutil.ignore_patterns(
        "appease.db*", "__pycache__", "*.pyc", ".env",
        "uploads", "staging", "report_photos", "avatars", "venv",
    ),
)
shutil.copy(AGENT_PATH, os.path.join(work, "agent.py"))
sys.path.insert(0, server_dir)
sys.path.insert(0, work)
os.chdir(server_dir)

import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
import app as appmod  # noqa: E402
import agent  # noqa: E402
import winreg  # noqa: E402

needed = ("confirm_choco_already_installed", "choco_package_state", "read_snapshot",
          "WindowsRegistry", "CHOCO_CHECK_REASONS")
if any(not hasattr(agent, n) for n in needed):
    print("COULD NOT RUN - agent.py predates the Chocolatey check.")
    sys.exit(3)

# The log file stays (section 5 reads it); the console copy would bury the
# results under every warning the check is supposed to write.
for handler in list(agent.logger.handlers):
    if type(handler) is agent.logging.StreamHandler:
        agent.logger.removeHandler(handler)

PRESENT, ABSENT, UNKNOWN, ABSTAIN = (agent.SIGNAL_PRESENT, agent.SIGNAL_ABSENT,
                                     agent.SIGNAL_UNKNOWN, agent.SIGNAL_ABSTAIN)
INSTALLED, REMOVED, UNVERIFIED = agent.CHOCO_INSTALLED, agent.CHOCO_REMOVED, agent.CHOCO_UNVERIFIED
HKLM, HKCU = "HKEY_LOCAL_MACHINE", "HKEY_CURRENT_USER"
UNINSTALL = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
PAINT_GUID = "{C6A29A65-A91B-4F4A-A1B5-B904AC61255D}"
CHROME_GUID = "{0AA2C178-BF99-3F72-BA87-F6123A1A95C8}"
# Every fixture's <user>, after redaction (see the fixture folder's README).
REDACTED_SID = "S-1-5-21-1111111111-2222222222-3333333333-1001"
VLC_LOCATION = r"C:\Program Files\VideoLAN\VLC"
OPERA_LOCATION = r"C:\Program Files\Opera"


# Exact bytes of every fixture, after redaction. Line endings are not
# uniform even within one real file - chocolatey-core.extension's nuspec
# mixes CRLF and LF - so
# the only honest guard against git or an editor "fixing" them is the hash.
FIXTURE_SHA256 = {
    "GoogleChrome.142.0.7444.176.registry": "61f081b386135a8c5ec83e807ad543cd1e2c9dca2bc8f0439ed6a01bc03161d1",
    "GoogleChrome.nuspec": "df7f2564618dd019e72da17c3e48160a8389fc5a713945ef3b8c2ef1648e33a8",
    "Opera.124.0.5705.15.registry": "6b492273844cc699171086a4422fba725f7ae75614926d758ea343b0efa6d789",
    "chocolatey-compatibility.extension.nuspec": "e16c82bd44a8abc7777f3c3003d3d58869d5111c020ba283b764f0a2f9de5dd8",
    "chocolatey-core.extension.nuspec": "d8b93903bb3ffe47ab5c081585d9767867bc1eaf10d2eb28ab879d3308e20f23",
    "gimp.3.0.6.1.registry": "7c436800cf328a7feb95e38e45ab95f2e0493261ccd45e809cb605ef48479a49",
    "paint.net.5.1.12.registry": "dfcf23dca94a15391aefab9cdb2ed8fb308373a8bb04bb4cf5e1508fb92e2e5e",
    "paint.net.nuspec": "702fb089cc6842f00f0e383f484657731418b43786681e6b9562812fe006d2d1",
    "vlc.install.3.0.23.registry": "cb45ec27dd3950b027b1c72672eeaf34ade49cc7909bef1a533d8d7c1d7a4252",
    "vlc.install.nuspec": "97533a260233ef245672b80321c8f0fac5e335c1e9f2cdb35eef108db59aab29",
    "vlc.nuspec": "1b1ef3db946b8c386b4614a236a0a4514692584c21f9694ca1c888744f86bcbf",
}


def fixture(name):
    with open(os.path.join(FIXTURES, name), "rb") as handle:
        return handle.read()


def record(text):
    """A synthetic record the way Chocolatey writes them: BOM and CRLF."""
    assert "\r" not in text
    return b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8")


def nuspec(package_id, version, dependencies=()):
    deps = "".join(f'\n      <dependency id="{d}" version="1.0" />' for d in dependencies)
    return record(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://schemas.microsoft.com/packaging/2015/06/nuspec.xsd">\n'
        f"  <metadata>\n    <id>{package_id}</id>\n    <version>{version}</version>\n"
        f"    <dependencies>{deps}\n    </dependencies>\n  </metadata>\n</package>")


def snapshot(keys, user=REDACTED_SID):
    """keys: (hive, subkey name, view, display name, install location)."""
    body = "".join(
        f'\n    <key installerType="Unknown" displayName="{display}" displayVersion="1.0">\n'
        f"      <RegistryView>{view}</RegistryView>\n"
        f"      <KeyPath>{hive}\\{UNINSTALL}\\{name}</KeyPath>\n"
        f"      <InstallLocation><![CDATA[{location}]]></InstallLocation>\n    </key>"
        for hive, name, view, display, location in keys)
    return record(
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
    """lib\\<lib_name>\\<lib_name>.nuspec, and optionally a .chocolatey
    folder."""
    write(os.path.join(root, "lib", lib_name, lib_name + ".nuspec"), spec)
    if folder:
        write(os.path.join(root, ".chocolatey", folder, ".files"), b"")
        if registry is not None:
            write(os.path.join(root, ".chocolatey", folder, ".registry"), registry)


def add_helpers(root):
    """The two helper libraries exactly as installed: real nuspecs, version
    folders, no snapshot."""
    add(root, "chocolatey-core.extension", fixture("chocolatey-core.extension.nuspec"),
        "chocolatey-core.extension.1.4.0")
    add(root, "chocolatey-compatibility.extension", fixture("chocolatey-compatibility.extension.nuspec"),
        "chocolatey-compatibility.extension.1.0.0")


def paint_tree(registry=None):
    root = new_root()
    add(root, "paint.net", fixture("paint.net.nuspec"), "paint.net.5.1.12",
        fixture("paint.net.5.1.12.registry") if registry is None else registry)
    add_helpers(root)
    return root


def vlc_tree():
    root = new_root()
    add(root, "vlc", fixture("vlc.nuspec"), "vlc.3.0.23")
    add(root, "vlc.install", fixture("vlc.install.nuspec"), "vlc.install.3.0.23",
        fixture("vlc.install.3.0.23.registry"))
    # Stale folders from the 3.0.21 install sit beside them on the real
    # machine; the older vlc.install one still holds a snapshot.
    write(os.path.join(root, ".chocolatey", "vlc.3.0.21", ".files"), b"")
    write(os.path.join(root, ".chocolatey", "vlc.install.3.0.21", ".registry"),
          fixture("vlc.install.3.0.23.registry"))
    add_helpers(root)
    return root


def chrome_tree():
    root = new_root()
    add(root, "GoogleChrome", fixture("GoogleChrome.nuspec"), "GoogleChrome.142.0.7444.176",
        fixture("GoogleChrome.142.0.7444.176.registry"))
    add_helpers(root)
    return root


class FakeRegistry:
    """What Windows lists, as a test decides it: keys map (hive, subkey,
    view) to a signal, and anything unlisted is absent. After a forced
    reinstall, keys_after and names_after apply instead."""

    def __init__(self, keys=None, names=(), names_unreadable=False,
                 keys_after=None, names_after=None, flip_file=None, explode=False):
        self.keys, self.names, self.names_unreadable = dict(keys or {}), list(names), names_unreadable
        self.keys_after, self.names_after, self.flip_file = keys_after, names_after, flip_file
        self.explode = explode
        self.key_calls, self.name_calls = [], []

    def _flipped(self):
        if not self.flip_file or not os.path.isfile(self.flip_file):
            return False
        with open(self.flip_file, encoding="utf-8", errors="replace") as handle:
            return "--force" in handle.read()

    def uninstall_key(self, hive, sid, name, view):
        self.key_calls.append((hive, sid, name, view))
        keys = self.keys_after if self._flipped() and self.keys_after is not None else self.keys
        return keys.get((hive, name, view), ABSENT)

    def display_names(self, required_sids):
        self.name_calls.append(list(required_sids))
        if self.explode:
            raise RuntimeError("probe: the registry reader broke")
        if self.names_unreadable:
            return None
        names = self.names_after if self._flipped() and self.names_after is not None else self.names
        return {agent.app_name_key(n) for n in names}


# Held here because section 7 replaces agent.install_location_signal with a
# locations() table; a table that looked the name up again would call itself.
REAL_LOCATION_SIGNAL = agent.install_location_signal


def locations(mapping):
    """An InstallLocation check that answers from a table, so a tree test
    does not depend on this PC. Anything unlisted is unknown, which cannot
    make a "gone" test pass by accident."""
    def signal(recorded):
        if not (recorded or "").strip():
            return REAL_LOCATION_SIGNAL(recorded)
        return mapping.get(recorded, UNKNOWN)
    return signal


def state(package, root, registry, where=None):
    return agent.choco_package_state(package, root, registry, locations(where or {}))


print("\n=== 1. Reading Chocolatey's records: real files, and anything else is not understood ===")
on_disk = sorted(n for n in os.listdir(FIXTURES) if n != "README.md")
check(f"the fixture folder holds exactly the pinned files ({on_disk})", on_disk and sorted(on_disk) == sorted(FIXTURE_SHA256))
for name, digest in FIXTURE_SHA256.items():
    check(f"fixture {name} is byte-for-byte what was copied (git has not normalised it)",
          hashlib.sha256(fixture(name)).hexdigest() == digest)
check("real .registry fixtures still start with their UTF-8 BOM",
      all(fixture(n).startswith(b"\xef\xbb\xbf") for n in os.listdir(FIXTURES) if n.endswith(".registry")))

chrome = agent.read_snapshot(os.path.join(FIXTURES, "GoogleChrome.142.0.7444.176.registry"))
check("Chrome's real snapshot: its recording user and exactly one key",
      chrome is not None and chrome is not agent.NO_SNAPSHOT and chrome[0] == REDACTED_SID and len(chrome[1]) == 1)
if chrome and chrome is not agent.NO_SNAPSHOT and chrome[1]:
    key = chrome[1][0]
    check("Chrome: HKLM, 64-bit view, the MSI product code as the key name",
          (key["hive"], key["view"], key["name"]) == (HKLM, "Registry64", CHROME_GUID))
    check("Chrome: display name 'Google Chrome', from the key's attribute", key["display"] == "Google Chrome")
    check("Chrome: the recorded InstallLocation is blank (CDATA[])", key["location"] == "")
opera = agent.read_snapshot(os.path.join(FIXTURES, "Opera.124.0.5705.15.registry"))
check("Opera's real snapshot: the 32-bit view, a versioned key name, a location",
      opera and opera[1] and (opera[1][0]["view"], opera[1][0]["name"], opera[1][0]["location"])
      == ("Registry32", "Opera 124.0.5705.15", OPERA_LOCATION))
gimp = agent.read_snapshot(os.path.join(FIXTURES, "gimp.3.0.6.1.registry"))
check("GIMP's real snapshot: a per-user key, with the installing user's SID",
      gimp and gimp[1] and gimp[1][0]["hive"] == HKCU and gimp[0] == REDACTED_SID
      and gimp[1][0]["name"] == "GIMP-3_is1")
paint = agent.read_snapshot(os.path.join(FIXTURES, "paint.net.5.1.12.registry"))
check("Paint.NET's real snapshot from the VM: one MSI key, 64-bit view, blank location",
      paint and paint[1] and (paint[1][0]["name"], paint[1][0]["view"], paint[1][0]["display"],
                              paint[1][0]["location"]) == (PAINT_GUID, "Registry64", "Paint.NET", ""))

check("a .registry that does not exist is 'no snapshot', not unreadable",
      agent.read_snapshot(os.path.join(work, "nope", ".registry")) is agent.NO_SNAPSHOT)
bad_dir = os.path.join(work, "bad")


def snapshot_of(data, name="probe.registry"):
    path = os.path.join(bad_dir, name)
    write(path, data)
    return agent.read_snapshot(path)


real_chrome = fixture("GoogleChrome.142.0.7444.176.registry")


def chrome_variant(old, new):
    assert real_chrome.count(old) == 1, old
    return real_chrome.replace(old, new)


check("an empty <keys /> is a snapshot with no keys",
      snapshot_of(chrome_variant(real_chrome[real_chrome.index(b"<keys>"):real_chrome.index(b"</keys>") + 7],
                                 b"<keys />")) == (REDACTED_SID, []))
for label, data in (
    ("a KeyPath through WOW6432Node", chrome_variant(b"SOFTWARE\\Microsoft", b"SOFTWARE\\WOW6432Node\\Microsoft")),
    ("a RegistryView of Default", chrome_variant(b"<RegistryView>Registry64<", b"<RegistryView>Default<")),
    ("a hive other than HKLM or HKCU", chrome_variant(b"<KeyPath>HKEY_LOCAL_MACHINE", b"<KeyPath>HKEY_USERS")),
    ("a key path outside Uninstall", chrome_variant(b"\\Uninstall\\{", b"\\Uninstall\\Sub\\{")),
    ("a key with no KeyPath", chrome_variant(b"<KeyPath>", b"<Other>").replace(b"</KeyPath>", b"</Other>")),
    ("a different root element", chrome_variant(b"<registrySnapshot ", b"<snapshot ").replace(b"</registrySnapshot>", b"</snapshot>")),
    ("no <keys> element at all", chrome_variant(b"<keys>", b"<items>").replace(b"</keys>", b"</items>")),
    ("something other than <key> inside <keys>", chrome_variant(b"<keys>", b"<keys>\r\n    <note />")),
    ("a file cut off halfway", real_chrome[:len(real_chrome) // 2]),
    ("an empty file", b""),
    ("text that is not XML", b"this is not a snapshot"),
    ("a file larger than the cap", real_chrome + b" " * agent.CHOCO_MAX_RECORD_BYTES),
):
    check(f"not understood -> None: {label}", snapshot_of(data) is None)
check("a key without the displayName attribute falls back to a DisplayName element",
      (snapshot_of(chrome_variant(b' displayName="Google Chrome"', b"").replace(
          b"<DefaultValue />", b"<DisplayName>Google Chrome</DisplayName>")) or (0, [{}]))[1][0].get("display")
      == "Google Chrome")

vlc_spec = agent.read_nuspec(os.path.join(FIXTURES, "vlc.nuspec"))
check("vlc's real nuspec: a wrapper depending on vlc.install",
      vlc_spec == {"id": "vlc", "version": "3.0.23", "dependencies": ["vlc.install"]})
core_spec = agent.read_nuspec(os.path.join(FIXTURES, "chocolatey-core.extension.nuspec"))
check("chocolatey-core.extension's real nuspec (2011/08 namespace, no BOM) reads too",
      core_spec == {"id": "chocolatey-core.extension", "version": "1.4.0",
                    "dependencies": ["chocolatey-compatibility.extension"]})
check("Paint.NET's real nuspec: a plain package depending on chocolatey-core.extension only",
      agent.read_nuspec(os.path.join(FIXTURES, "paint.net.nuspec"))
      == {"id": "paint.net", "version": "5.1.12", "dependencies": ["chocolatey-core.extension"]})
real_vlc = fixture("vlc.nuspec")


def nuspec_of(data):
    path = os.path.join(bad_dir, "probe.nuspec")
    write(path, data)
    return agent.read_nuspec(path)


for label, data in (
    ("no id", real_vlc.replace(b"<id>vlc</id>", b"")),
    ("no version", real_vlc.replace(b"<version>3.0.23</version>", b"")),
    ("a dependency with no id", real_vlc.replace(b'id="vlc.install"', b'name="vlc.install"')),
    ("two metadata blocks", real_vlc.replace(b"</metadata>", b"</metadata><metadata />")),
    ("not a package", real_vlc.replace(b"<package ", b"<pkg ").replace(b"</package>", b"</pkg>")),
    ("a file cut off halfway", real_vlc[:len(real_vlc) // 2]),
):
    check(f"nuspec not understood -> None: {label}", nuspec_of(data) is None)
check("a nuspec file that does not exist -> None", agent.read_nuspec(os.path.join(work, "nope.nuspec")) is None)


print("\n=== 2. The three signals ===")
for recorded, expected in (
    ("Google Chrome", "google chrome"),
    ("Opera Stable 124.0.5705.15", "opera stable"),
    ("GIMP 3.0.6-1", "gimp"),
    ("Paint.NET", "paint net"),
    ("Mozilla Firefox (x64 en-US)", "mozilla firefox"),
    ("VLC media player", "vlc media player"),
    ("", None),
    ("   ", None),
    ("3.0.23", None),
    (None, None),
):
    check(f"display name {recorded!r} compares as {expected!r}", agent.app_name_key(recorded) == expected)
check("names that differ only by version compare equal (Opera updating itself)",
      agent.app_name_key("Opera Stable 124.0.5705.15") == agent.app_name_key("Opera Stable 125.0.5729.21"))
check("different apps stay different", agent.app_name_key("Paint.NET") != agent.app_name_key("Google Chrome"))

existing = tempfile.mkdtemp(dir=work)
free_drive = next((f"{c}:\\" for c in reversed(string.ascii_uppercase) if not os.path.exists(f"{c}:\\")), None)
for label, recorded, expected in (
    ("blank abstains", "", ABSTAIN),
    ("whitespace abstains", "   ", ABSTAIN),
    ("an empty quoted path abstains", '""', ABSTAIN),
    ("a folder on disk is present", existing, PRESENT),
    ("a quoted folder on disk is present", f'"{existing}"', PRESENT),
    ("a folder with a trailing backslash is present", existing + "\\", PRESENT),
    ("a folder no longer on disk is absent", os.path.join(existing, "gone"), ABSENT),
    ("a path under a missing folder is absent", os.path.join(existing, "gone", "deeper"), ABSENT),
    ("an unexpanded variable is unknown", r"%ProgramFiles%\Paint.NET", UNKNOWN),
    ("a relative path is unknown", r"Program Files\Paint.NET", UNKNOWN),
    ("a network path is unknown", r"\\server\share\app", UNKNOWN),
):
    check(f"InstallLocation: {label}", agent.install_location_signal(recorded) == expected)
if free_drive:
    check(f"InstallLocation: a drive that is not there ({free_drive}) is unknown, not absent",
          agent.install_location_signal(free_drive + "Apps\\Thing") == UNKNOWN)
else:
    skip("InstallLocation on a missing drive", "every drive letter is in use")


print("\n=== 3. One key: the decision table, every combination ===")
KEY = {"path": f"{HKLM}\\{UNINSTALL}\\{PAINT_GUID}", "hive": HKLM, "name": PAINT_GUID,
       "view": "Registry64", "display": "Paint.NET", "location": "LOCATION"}
K_MODES = {
    "present": ({(HKLM, PAINT_GUID, "Registry64"): PRESENT}, PRESENT),
    "absent in both views": ({}, ABSENT),
    "only in the other view": ({(HKLM, PAINT_GUID, "Registry32"): PRESENT}, UNKNOWN),
    "unreadable": ({(HKLM, PAINT_GUID, "Registry64"): UNKNOWN}, UNKNOWN),
    "absent, other view unreadable": ({(HKLM, PAINT_GUID, "Registry32"): UNKNOWN}, UNKNOWN),
}
N_MODES = {PRESENT: ("Paint.NET", ["Paint.NET"], False), ABSENT: ("Paint.NET", ["Other App"], False),
           UNKNOWN: ("Paint.NET", [], True), ABSTAIN: ("", ["Paint.NET"], False)}
L_MODES = {PRESENT: "LOC", ABSENT: "LOC", UNKNOWN: "LOC", ABSTAIN: ""}


def rule(k, n, loc):
    """The approved rule, restated from the decision, not from the code."""
    if k == "present":
        return agent.KEY_PRESENT
    if k == "absent in both views" and {n, loc}.isdisjoint({PRESENT, UNKNOWN}) and ABSENT in (n, loc):
        return agent.KEY_GONE
    return agent.KEY_UNKNOWN


wrong = []
for k_mode, n_mode, l_mode in itertools.product(K_MODES, N_MODES, L_MODES):
    keys, k_expected = K_MODES[k_mode]
    display, names, unreadable = N_MODES[n_mode]
    key = dict(KEY, display=display, location=L_MODES[l_mode])
    got = agent.snapshot_key_verdict(key, None, FakeRegistry(keys, names, unreadable).display_names([]),
                                     FakeRegistry(keys), locations({"LOC": l_mode}))
    if got != (rule(k_mode, n_mode, l_mode), k_expected, n_mode, l_mode):
        wrong.append((k_mode, n_mode, l_mode, got))
check(f"all {len(K_MODES) * len(N_MODES) * len(L_MODES)} combinations follow the rule (wrong: {wrong[:3]})",
      not wrong)

# The named cases, so a failure says which real situation broke.
reg = FakeRegistry()
verdict = agent.snapshot_key_verdict(dict(KEY, location=""), None, set(), reg, locations({}))
check("Paint.NET on the VM: key absent, name absent, location blank -> gone",
      verdict == (agent.KEY_GONE, ABSENT, ABSENT, ABSTAIN))
check("...and the key was looked for in BOTH views",
      [c[3] for c in reg.key_calls] == ["Registry64", "Registry32"])
chrome_key = dict(KEY, name=CHROME_GUID, display="Google Chrome", location="")
check("Chrome after updating itself: key gone, 'Google Chrome' still listed -> unknown, never gone",
      agent.snapshot_key_verdict(chrome_key, None, {"google chrome"}, FakeRegistry(), locations({}))[0]
      == agent.KEY_UNKNOWN)
opera_key = dict(KEY, name="Opera 124.0.5705.15", view="Registry32", display="Opera Stable 124.0.5705.15",
                 location=OPERA_LOCATION)
check("Opera after updating itself: versioned key gone, folder still there -> unknown",
      agent.snapshot_key_verdict(opera_key, None, {"opera stable"}, FakeRegistry(),
                                 locations({OPERA_LOCATION: PRESENT}))[0] == agent.KEY_UNKNOWN)
check("Opera with only its folder left (name no longer listed) is still unknown",
      agent.snapshot_key_verdict(opera_key, None, set(), FakeRegistry(),
                                 locations({OPERA_LOCATION: PRESENT}))[0] == agent.KEY_UNKNOWN)
vlc_key = dict(KEY, name="VLC media player", display="VLC media player", location=VLC_LOCATION)
check("VLC recorded 64-bit but listed 32-bit: a contradiction -> unknown",
      agent.snapshot_key_verdict(vlc_key, None, set(), FakeRegistry({(HKLM, "VLC media player", "Registry32"): PRESENT}),
                                 locations({VLC_LOCATION: ABSENT}))[:2] == (agent.KEY_UNKNOWN, UNKNOWN))
check("a blank name and a blank location prove nothing, even with the key gone",
      agent.snapshot_key_verdict(dict(KEY, display="", location=""), None, set(), FakeRegistry(),
                                 locations({}))[0] == agent.KEY_UNKNOWN)


print("\n=== 4. The package and its dependencies ===")
PAINT = (HKLM, PAINT_GUID, "Registry64")
root = paint_tree()
reg = FakeRegistry()
check("Paint.NET removed through Windows -> removed: reinstall paint.net 5.1.12 only",
      state("paint.net", root, reg) == (REMOVED, [("paint.net", "5.1.12")]))
check("...the helper libraries were walked but never judged (no snapshot)",
      {c[2] for c in reg.key_calls} == {PAINT_GUID})
check("...the recording user's hive is required for the name list", reg.name_calls == [[REDACTED_SID]])
check("Paint.NET still installed -> installed", state("paint.net", root, FakeRegistry({PAINT: PRESENT})) == (INSTALLED, []))
check("Chocolatey ids are case-insensitive: Paint.NET finds lib\\paint.net",
      state("Paint.NET", root, FakeRegistry())[0] == REMOVED)
check("Paint.NET gone but the name list unreadable -> can't verify",
      state("paint.net", root, FakeRegistry(names_unreadable=True)) == (UNVERIFIED, []))
check("Paint.NET's key gone but a 'Paint.NET' entry still listed -> can't verify",
      state("paint.net", root, FakeRegistry(names=["Paint.NET"])) == (UNVERIFIED, []))

root = chrome_tree()
check("Chrome updated itself (real snapshot, 'Google Chrome' listed) -> can't verify, nothing forced",
      state("GoogleChrome", root, FakeRegistry(names=["Google Chrome"])) == (UNVERIFIED, []))

root = vlc_tree()
reg = FakeRegistry()
check("vlc's keys gone -> reinstall vlc.install, never the vlc wrapper",
      state("vlc", root, reg, {VLC_LOCATION: ABSENT}) == (REMOVED, [("vlc.install", "3.0.23")]))
check("...stale vlc.3.0.21 and vlc.install.3.0.21 folders were not read as vlc's",
      len(reg.key_calls) == 2)
check("vlc.install still listed -> installed",
      state("vlc", root, FakeRegistry({(HKLM, "VLC media player", "Registry64"): PRESENT}),
            {VLC_LOCATION: PRESENT}) == (INSTALLED, []))
check("vlc listed in the other view only -> can't verify",
      state("vlc", root, FakeRegistry({(HKLM, "VLC media player", "Registry32"): PRESENT}),
            {VLC_LOCATION: ABSENT}) == (UNVERIFIED, []))

# Synthetic trees for the shapes the real machine does not happen to have.
root = new_root()
add(root, "app", nuspec("app", "2.0", ["lib-a"]), "app.2.0", snapshot([(HKLM, "App", "Registry64", "App", "")]))
add(root, "lib-a", nuspec("lib-a", "1.0", ["lib-b"]), "lib-a.1.0", snapshot([(HKLM, "LibA", "Registry64", "Lib A", "")]))
add(root, "lib-b", nuspec("lib-b", "1.0"), "lib-b.1.0", snapshot([(HKLM, "LibB", "Registry64", "Lib B", "")]))
check("three owners all gone -> all three, dependencies first",
      state("app", root, FakeRegistry()) == (REMOVED, [("lib-b", "1.0"), ("lib-a", "1.0"), ("app", "2.0")]))
check("only a dependency gone, the app present -> reinstall just the dependency",
      state("app", root, FakeRegistry({(HKLM, "App", "Registry64"): PRESENT, (HKLM, "LibB", "Registry64"): PRESENT}))
      == (REMOVED, [("lib-a", "1.0")]))
check("one owner gone and another unknown -> can't verify, nothing forced",
      state("app", root, FakeRegistry({(HKLM, "LibA", "Registry64"): UNKNOWN})) == (UNVERIFIED, []))

root = new_root()
add(root, "two", nuspec("two", "1.0"), "two.1.0",
    snapshot([(HKLM, "TwoMain", "Registry64", "Two", ""), (HKLM, "TwoHelper", "Registry64", "Two Helper", "")]))
check("a snapshot with two keys, both there -> installed",
      state("two", root, FakeRegistry({(HKLM, "TwoMain", "Registry64"): PRESENT,
                                       (HKLM, "TwoHelper", "Registry64"): PRESENT})) == (INSTALLED, []))
check("two keys, both gone -> removed", state("two", root, FakeRegistry()) == (REMOVED, [("two", "1.0")]))
check("two keys, one there and one gone -> can't verify",
      state("two", root, FakeRegistry({(HKLM, "TwoMain", "Registry64"): PRESENT})) == (UNVERIFIED, []))

root = new_root()
add(root, "loop-a", nuspec("loop-a", "1.0", ["loop-b"]), "loop-a.1.0", snapshot([(HKLM, "LoopA", "Registry64", "Loop A", "")]))
add(root, "loop-b", nuspec("loop-b", "1.0", ["LOOP-A"]), "loop-b.1.0")
check("a dependency cycle ends, and is judged once", state("loop-a", root, FakeRegistry()) == (REMOVED, [("loop-a", "1.0")]))

root = new_root()
add(root, "KB2999226", nuspec("KB2999226", "1.0.20181019"), "KB2999226.1.0.20181019",
    snapshot([(HKLM, "KB", "Registry64", "Update", "")]))
add(root, "vcredist140", nuspec("vcredist140", "14.44.35211", ["kb2999226"]), "vcredist140.14.44.35211")
check("a dependency named in another case (kb2999226 -> KB2999226) is found, and reinstalled by its real id",
      state("vcredist140", root, FakeRegistry()) == (REMOVED, [("KB2999226", "1.0.20181019")]))

for label, build in (
    ("the deployed package has no lib folder", lambda r: None),
    ("a dependency has no lib folder",
     lambda r: add(r, "app", nuspec("app", "1.0", ["missing-dep"]), "app.1.0", snapshot([(HKLM, "App", "Registry64", "App", "")]))),
    ("the recorded version's .chocolatey folder is missing",
     lambda r: add(r, "app", nuspec("app", "1.0"), None)),
    ("only a different version's folder exists (never guessed)",
     lambda r: (add(r, "app", nuspec("app", "1.1"), None),
                write(os.path.join(r, ".chocolatey", "app.1.0", ".registry"), snapshot([(HKLM, "App", "Registry64", "App", "")])))),
    ("the recorded version has no snapshot but an older version's folder does",
     lambda r: (add(r, "app", nuspec("app", "1.1"), "app.1.1"),
                write(os.path.join(r, ".chocolatey", "app.1.0", ".registry"), snapshot([(HKLM, "App", "Registry64", "App", "")])))),
    ("an older version's snapshot cannot be read",
     lambda r: (add(r, "app", nuspec("app", "1.1"), "app.1.1"),
                write(os.path.join(r, ".chocolatey", "app.1.0", ".registry"), b"garbage"))),
    ("the .registry cannot be parsed", lambda r: add(r, "app", nuspec("app", "1.0"), "app.1.0", b"<registrySnapshot><keys>")),
    ("a key in it is not understood",
     lambda r: add(r, "app", nuspec("app", "1.0"), "app.1.0", snapshot([(HKLM, "App", "Default", "App", "")]))),
    ("no package records any key (a portable package)", lambda r: add(r, "app", nuspec("app", "1.0"), "app.1.0")),
    ("lib\\app records a different id", lambda r: add(r, "app", nuspec("other", "1.0"), "other.1.0",
                                                      snapshot([(HKLM, "App", "Registry64", "App", "")]))),
):
    r = new_root()
    build(r)
    reg = FakeRegistry()
    check(f"can't verify: {label}", state("app", r, reg) == (UNVERIFIED, []))
    check(f"...and the registry was never asked ({label})", not reg.key_calls and not reg.name_calls)

# A wrapper whose older version folder has no snapshot, next to a DIFFERENT
# package whose id starts the same way and does have one. Neither is this
# wrapper's snapshot, so its dependency still decides.
r = new_root()
add(r, "app", nuspec("app", "1.1", ["app.engine"]), "app.1.1")
write(os.path.join(r, ".chocolatey", "app.1.0", ".files"), b"")
add(r, "app.engine", nuspec("app.engine", "1.1"), "app.engine.1.1", snapshot([(HKLM, "Engine", "Registry64", "Engine", "")]))
write(os.path.join(r, ".chocolatey", "app.engine.1.0", ".registry"), snapshot([(HKLM, "Engine", "Registry64", "Engine", "")]))
check("an older snapshot-less folder, and app.engine's folders, do not block the app wrapper",
      state("app", r, FakeRegistry()) == (REMOVED, [("app.engine", "1.1")]))

# Each guard on its own. The app below has NO snapshot and depends on an
# owner whose key is gone, so without the guard the answer would be
# "removed: reinstall dep" - only the guard under test can stop it. (A case
# where nothing records a key is stopped by that rule instead, and would
# pass with the guard deleted.)
def guarded(app_version="1.0", app_folder="app.1.0", dep_id="dep", dep_version="1.0", dep_folder=None):
    r = new_root()
    add(r, "app", nuspec("app", app_version, [dep_id]), app_folder)
    add(r, dep_id, nuspec(dep_id, dep_version), dep_folder or f"{dep_id}.{dep_version}",
        snapshot([(HKLM, "Dep", "Registry64", "Dep", "")]))
    return r


r = guarded()
check("control for the guards below: an owner dependency gone -> removed",
      state("app", r, FakeRegistry()) == (REMOVED, [("dep", "1.0")]))
for label, build in (
    ("the app's recorded .chocolatey folder is missing", lambda: guarded(app_folder=None)),
    ("the app has no snapshot but an older version's folder does",
     lambda: (lambda r: (write(os.path.join(r, ".chocolatey", "app.0.9", ".registry"),
                               snapshot([(HKLM, "App", "Registry64", "App", "")])), r)[1])(guarded())),
    ("an older version's snapshot cannot be read",
     lambda: (lambda r: (write(os.path.join(r, ".chocolatey", "app.0.9", ".registry"), b"garbage"), r)[1])(guarded())),
    # &amp; is how a nuspec carries it: the parsed version is 1.0&calc.
    ("the app's recorded version would inject into a command",
     lambda: guarded(app_version="1.0&amp;calc", app_folder="app.1.0&calc")),
    ("an owner's recorded version would inject into its forced command",
     lambda: guarded(dep_version="1.0&amp;calc", dep_folder="dep.1.0&calc")),
    ("an owner's id would inject into its forced command",
     lambda: guarded(dep_id="dep --source=evil")),
):
    r = build()
    reg = FakeRegistry()
    check(f"can't verify: {label}", state("app", r, reg) == (UNVERIFIED, []))

r = new_root()
add(r, "app", nuspec("app", "1.0"), "app.1.0", snapshot([]))
check("a package whose only snapshot has no keys has recorded nothing -> can't verify",
      state("app", r, FakeRegistry()) == (UNVERIFIED, []))

# ZERO KEYS, on its own. all() over an empty list is True, so a snapshot with
# no keys, if it were judged, would pass "every recorded key is gone" with no
# evidence at all and be force-reinstalled. The dependency below is still
# installed, so anything but "installed" can only come from that mistake.
r = guarded()
write(os.path.join(r, ".chocolatey", "app.1.0", ".registry"), snapshot([]))
check("ZERO KEYS: a snapshot with no keys is never 'every key gone' (all() over nothing is True), "
      "so it is never reinstalled",
      state("app", r, FakeRegistry({(HKLM, "Dep", "Registry64"): PRESENT})) == (INSTALLED, []))
check("an empty snapshot on the app does not make it an owner: only the dependency is judged",
      state("app", r, FakeRegistry()) == (REMOVED, [("dep", "1.0")]))

r = new_root()
chain = [f"p{i}" for i in range(agent.CHOCO_MAX_PACKAGES + 5)]
for i, pid in enumerate(chain):
    add(r, pid, nuspec(pid, "1.0", chain[i + 1:i + 2]), f"{pid}.1.0", snapshot([(HKLM, pid, "Registry64", pid, "")]))
check(f"more than {agent.CHOCO_MAX_PACKAGES} packages -> can't verify", state("p0", r, FakeRegistry()) == (UNVERIFIED, []))

root = new_root()
add(root, "gimp", nuspec("gimp", "3.0.6.1"), "gimp.3.0.6.1", fixture("gimp.3.0.6.1.registry"))
reg = FakeRegistry()
gimp_location = r"C:\Users\LabUser\AppData\Local\Programs\GIMP 3" + "\\"
check("GIMP (real per-user snapshot) removed -> removed",
      state("gimp", root, reg, {gimp_location: ABSENT}) == (REMOVED, [("gimp", "3.0.6.1")]))
check("...its key was looked up with the SID the snapshot recorded",
      (HKCU, REDACTED_SID, "GIMP-3_is1", "Registry64") in reg.key_calls)


print("\n=== 5. The real registry, read the way the agent reads it ===")
windows = agent.WindowsRegistry()


def subkeys(view):
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, UNINSTALL, 0, winreg.KEY_READ | view) as parent:
            return {winreg.EnumKey(parent, i) for i in range(winreg.QueryInfoKey(parent)[0])}
    except OSError:
        return set()


keys64, keys32 = subkeys(winreg.KEY_WOW64_64KEY), subkeys(winreg.KEY_WOW64_32KEY)
made_up = "{00000000-APPE-ASE0-TEST-000000000000}"
check("a made-up key is absent in the 64-bit view", windows.uninstall_key(HKLM, None, made_up, "Registry64") == ABSENT)
check("a made-up key is absent in the 32-bit view", windows.uninstall_key(HKLM, None, made_up, "Registry32") == ABSENT)
check("a view Chocolatey did not record is unknown", windows.uninstall_key(HKLM, None, made_up, "Default") == UNKNOWN)
only64, only32 = sorted(keys64 - keys32), sorted(keys32 - keys64)
if only64:
    check(f"a 64-bit-only key ({only64[0]}) is present in Registry64", windows.uninstall_key(HKLM, None, only64[0], "Registry64") == PRESENT)
    check("...and absent in Registry32 - the flag, not Python's build, picks the view",
          windows.uninstall_key(HKLM, None, only64[0], "Registry32") == ABSENT)
else:
    skip("64-bit view flag", "no uninstall key exists only in the 64-bit view here")
if only32:
    check(f"a 32-bit-only key ({only32[0]}) is present in Registry32", windows.uninstall_key(HKLM, None, only32[0], "Registry32") == PRESENT)
    check("...and absent in Registry64", windows.uninstall_key(HKLM, None, only32[0], "Registry64") == ABSENT)
else:
    skip("32-bit view flag", "no uninstall key exists only in the 32-bit view here")

whoami = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True)
my_sid = whoami.stdout.strip().rsplit(",", 1)[-1].strip('"') if whoami.returncode == 0 else ""
check(f"this account's SID was found ({my_sid!r})", bool(agent.USER_SID.match(my_sid)))
check("the fixtures' redacted SID is not a hive loaded on this PC (the unloaded-hive checks rely on it)",
      REDACTED_SID.casefold() not in (windows.loaded_user_hives() or {REDACTED_SID.casefold()}))
check("a per-user key that does not exist, in a loaded hive, is absent",
      windows.uninstall_key(HKCU, my_sid, made_up, "Registry64") == ABSENT)
check("a per-user key for a user whose hive is not loaded is unknown, not absent",
      windows.uninstall_key(HKCU, REDACTED_SID, made_up, "Registry64") == UNKNOWN)
check("a per-user key with no recorded SID is unknown", windows.uninstall_key(HKCU, None, made_up, "Registry64") == UNKNOWN)
check("a per-user key with a SID that is not one is unknown",
      windows.uninstall_key(HKCU, my_sid + "\\..\\x", made_up, "Registry64") == UNKNOWN)
try:
    with winreg.OpenKey(winreg.HKEY_USERS, my_sid + "\\" + UNINSTALL) as parent:
        mine = [winreg.EnumKey(parent, i) for i in range(winreg.QueryInfoKey(parent)[0])]
except OSError:
    mine = []
if mine:
    check(f"a real per-user key ({mine[0]}) is present under HKEY_USERS\\<SID>",
          windows.uninstall_key(HKCU, my_sid, mine[0], "Registry64") == PRESENT)
else:
    skip("a real per-user key", "this account has no per-user uninstall entries")

names = windows.display_names([my_sid])
listed = None
for name in sorted(keys64):
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, UNINSTALL + "\\" + name, 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as entry:
            listed = agent.app_name_key(winreg.QueryValueEx(entry, "DisplayName")[0])
    except (OSError, TypeError):
        continue
    if listed:
        break
check("the name list reads real entries (it holds this PC's first named 64-bit entry)",
      names is not None and listed in names)
check("the name list is unknown when a recording user's hive is not loaded",
      windows.display_names([my_sid, REDACTED_SID]) is None)
saved_winreg = agent.winreg
agent.winreg = None
try:
    check("without winreg: every key is unknown", windows.uninstall_key(HKLM, None, made_up, "Registry64") == UNKNOWN)
    check("without winreg: the name list is unknown", windows.display_names([]) is None)
finally:
    agent.winreg = saved_winreg


print("\n=== 6. Your Paint.NET case, on this PC's real registry and disk ===")
paint_here = PAINT_GUID in keys64 or PAINT_GUID in keys32 or (names is not None and "paint net" in names)
if paint_here:
    skip("Paint.NET walkthrough on the real registry", "Paint.NET is installed on this PC")
else:
    # The VM's snapshot, as if this account had installed it - the case the
    # agent creates, since its own task account runs every choco install.
    root = paint_tree(fixture("paint.net.5.1.12.registry").replace(REDACTED_SID.encode(), my_sid.encode()))
    log_start = os.path.getsize(agent.LOG_PATH)
    check("recorded by this account: real registry, real disk -> removed, reinstall paint.net 5.1.12",
          agent.choco_package_state("paint.net", root, windows) == (REMOVED, [("paint.net", "5.1.12")]))
    log = open(agent.LOG_PATH, encoding="utf-8").read()[log_start:]
    check("...and the log shows why: key absent, name absent, location abstain -> gone",
          f"{PAINT_GUID} (Registry64): key absent, name absent, location abstain -> gone" in log)
    root = paint_tree()
    check("recorded by a user whose hive is NOT loaded -> can't verify (their own entries can't be listed)",
          agent.choco_package_state("paint.net", root, windows) == (UNVERIFIED, []))
if names is not None and "google chrome" in names:
    # Recorded by this account, as the name list needs; with the redacted SID
    # it would also come out "can't verify", but only because no such hive is
    # loaded - the right answer for the wrong reason. The log line pins the
    # reason: Windows still lists "Google Chrome".
    chrome = fixture("GoogleChrome.142.0.7444.176.registry").replace(REDACTED_SID.encode(), my_sid.encode())
    root = chrome_tree()
    write(os.path.join(root, ".chocolatey", "GoogleChrome.142.0.7444.176", ".registry"), chrome)
    log_start = os.path.getsize(agent.LOG_PATH)
    check("Chrome's real snapshot on this PC, where Chrome updated itself -> can't verify",
          agent.choco_package_state("GoogleChrome", root, windows) == (UNVERIFIED, []))
    log = open(agent.LOG_PATH, encoding="utf-8").read()[log_start:]
    check("...because Windows still lists the name, not because anything could not be read",
          f"{CHROME_GUID} (Registry64): key absent, name present, location abstain -> unknown" in log)
else:
    skip("Chrome on the real registry", "Google Chrome is not installed on this PC")


print("\n=== 7. The whole install path, with a stand-in choco ===")
produced = []

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
"""  # t/test_job_results.py's Fixture A, the VM capture, verbatim
INSTALLED_1_OF_1 = "Chocolatey v2.7.3\nChocolatey installed 1/1 packages.\n"
real_choco, real_registry_class = agent.CHOCO_EXE, agent.WindowsRegistry
real_timeout = agent.INSTALL_TIMEOUT_SECONDS


def stand_in(root, forced_code=0, forced_output=INSTALLED_1_OF_1, first_output=FIXTURE_A, forced_wait=0,
             first_wait=0):
    """bin\\choco.bat in the tree: logs every call's arguments, prints
    first_output unless the call has --force, and forced_output if it has."""
    bin_dir = os.path.join(root, "bin")
    os.makedirs(bin_dir, exist_ok=True)
    for name, text in (("first.txt", first_output), ("forced.txt", forced_output)):
        with open(os.path.join(bin_dir, name), "w", encoding="utf-8", newline="\r\n") as handle:
            handle.write(text)
    wait = f"ping -n {forced_wait + 1} 127.0.0.1 >nul\n" if forced_wait else ""
    first_pause = f"ping -n {first_wait + 1} 127.0.0.1 >nul\n" if first_wait else ""
    bat = os.path.join(bin_dir, "choco.bat")
    with open(bat, "w", encoding="ascii", newline="\r\n") as handle:
        handle.write('@echo off\n>>"%~dp0calls.txt" echo %*\necho %* | findstr /C:"--force" >nul\n'
                     'if errorlevel 1 goto first\n' + wait + 'type "%~dp0forced.txt"\n'
                     f"exit /b {forced_code}\n:first\n" + first_pause + 'type "%~dp0first.txt"\nexit /b 0\n')
    return bat


def calls(root):
    path = os.path.join(root, "bin", "calls.txt")
    return open(path, encoding="utf-8").read().splitlines() if os.path.isfile(path) else []


def deploy(root, registry, command="choco install paint.net -y", where=None, **choco):
    agent.CHOCO_EXE = stand_in(root, **choco)
    agent.WindowsRegistry = lambda: registry
    agent.install_location_signal = locations(where or {})
    try:
        result = agent.run_install_command(command)
    finally:
        agent.CHOCO_EXE, agent.WindowsRegistry = real_choco, real_registry_class
        agent.install_location_signal = REAL_LOCATION_SIGNAL
    produced.append(result)
    return result


FORCED = "install paint.net --version 5.1.12 --force -y --no-progress"
root = paint_tree()
flip = os.path.join(root, "bin", "calls.txt")
r = deploy(root, FakeRegistry(keys_after={PAINT: PRESENT}, names_after=["Paint.NET"], flip_file=flip))
check("removed, reinstalled, listed again: completed, 0, choco_reinstalled", r == (True, 0, "choco_reinstalled"))
check(f"...the forced command is pinned, forced, and has no -x (calls: {calls(root)})",
      calls(root) == ["install paint.net -y --no-progress", FORCED])
check("...never --ignore-dependencies or -x", not any(" -x" in c or "ignore-dependencies" in c for c in calls(root)))

root = paint_tree()
r = deploy(root, FakeRegistry())
check("reinstalled but still not listed: completed, 0, choco_reinstall_unconfirmed",
      r == (True, 0, "choco_reinstall_unconfirmed"))
check("...one forced run, not a loop", calls(root).count(FORCED) == 1 and len(calls(root)) == 2)

root = paint_tree()
flip = os.path.join(root, "bin", "calls.txt")
r = deploy(root, FakeRegistry(names_unreadable=False, keys_after={PAINT: UNKNOWN}, flip_file=flip))
check("reinstalled but the check afterwards can't decide: unconfirmed, never 'reinstalled'",
      r == (True, 0, "choco_reinstall_unconfirmed"))

root = paint_tree()
reg = FakeRegistry({PAINT: PRESENT})
r = deploy(root, reg)
check("really installed: completed, 0, choco_confirmed_installed", r == (True, 0, "choco_confirmed_installed"))
check("...and nothing was forced", calls(root) == ["install paint.net -y --no-progress"])

root = chrome_tree()
chrome_already = FIXTURE_A.replace("paint.net v5.1.12", "GoogleChrome v142.0.7444.176").replace("paint.net", "GoogleChrome")
r = deploy(root, FakeRegistry(names=["Google Chrome"]), "choco install GoogleChrome -y", first_output=chrome_already)
check("Chrome, can't verify: 'already installed' stays exactly as before", r == (True, 0, "choco_already_installed"))
check("...and nothing was forced", len(calls(root)) == 1)

root = paint_tree()
r = deploy(root, FakeRegistry(), forced_code=1, forced_output="Chocolatey installed 0/1 packages. 1 packages failed.\n")
check("the forced run fails: reported as that failure, classified like any install",
      r == (False, 1, "choco_error"))

root = paint_tree()
r = deploy(root, FakeRegistry(), forced_code=3010)
check("the forced run needs a restart: Restart needed wins", r == (True, 3010, "restart_needed"))
root = paint_tree()
r = deploy(root, FakeRegistry(), forced_code=1641)
check("the forced run starts a restart: Restarting to finish wins", r == (True, 1641, "restart_started"))

root = vlc_tree()
vlc_already = FIXTURE_A.replace("paint.net v5.1.12", "vlc v3.0.23").replace("paint.net", "vlc")
r = deploy(root, FakeRegistry(), "choco install vlc -y", where={VLC_LOCATION: ABSENT}, first_output=vlc_already)
check("vlc's keys gone: the forced run names vlc.install at its recorded version",
      calls(root)[1:] == ["install vlc.install --version 3.0.23 --force -y --no-progress"])

root = new_root()
add(root, "app", nuspec("app", "2.0", ["lib-a"]), "app.2.0", snapshot([(HKLM, "App", "Registry64", "App", "")]))
add(root, "lib-a", nuspec("lib-a", "1.0"), "lib-a.1.0", snapshot([(HKLM, "LibA", "Registry64", "Lib A", "")]))
app_already = FIXTURE_A.replace("paint.net v5.1.12", "app v2.0").replace("paint.net", "app")
r = deploy(root, FakeRegistry(), "choco install app -y", first_output=app_already)
check("two owners gone: dependency first, then the app",
      calls(root)[1:] == ["install lib-a --version 1.0 --force -y --no-progress",
                          "install app --version 2.0 --force -y --no-progress"])
root = new_root()
add(root, "app", nuspec("app", "2.0", ["lib-a"]), "app.2.0", snapshot([(HKLM, "App", "Registry64", "App", "")]))
add(root, "lib-a", nuspec("lib-a", "1.0"), "lib-a.1.0", snapshot([(HKLM, "LibA", "Registry64", "Lib A", "")]))
r = deploy(root, FakeRegistry(), "choco install app -y", first_output=app_already, forced_code=1,
           forced_output="Chocolatey installed 0/1 packages. 1 packages failed.\n")
check("the dependency's forced run fails: the app is not attempted", len(calls(root)) == 2 and r.ok is False)

root = new_root()
add(root, "app", nuspec("app", "2.0", ["lib-a"]), "app.2.0", snapshot([]))
add(root, "lib-a", nuspec("lib-a", "1.0"), "lib-a.1.0", snapshot([(HKLM, "LibA", "Registry64", "Lib A", "")]))
r = deploy(root, FakeRegistry({(HKLM, "LibA", "Registry64"): PRESENT}), "choco install app -y", first_output=app_already)
check("ZERO KEYS through the install path: nothing forced, and the installed dependency confirms it",
      r == (True, 0, "choco_confirmed_installed") and len(calls(root)) == 1)

root = paint_tree()
reg = FakeRegistry(explode=True)
r = deploy(root, reg)
check("the check itself throws: 'already installed' stands, nothing forced",
      r == (True, 0, "choco_already_installed") and len(calls(root)) == 1)

root = paint_tree()
reg = FakeRegistry()
r = deploy(root, reg, first_output=INSTALLED_1_OF_1)
check("a real fresh install never runs the check", r == (True, 0, None) and not reg.key_calls and not reg.name_calls)

root = new_root()
add(root, "app", nuspec("app", "1.0&amp;calc"), "app.1.0&calc", snapshot([(HKLM, "App", "Registry64", "App", "")]))
r = deploy(root, FakeRegistry(), "choco install app -y", first_output=app_already.replace("v2.0", "v1.0"))
check("an unsafe recorded version (1.0&calc): nothing is run with it",
      r == (True, 0, "choco_already_installed") and len(calls(root)) == 1)

# An 8-second limit, the first run taking ~5s and the forced run ~5s: each
# fits a limit of its own, only a SHARED one runs out during the forced run.
root = paint_tree()
agent.INSTALL_TIMEOUT_SECONDS = 8
try:
    r = deploy(root, FakeRegistry(), first_wait=5, forced_wait=5)
finally:
    agent.INSTALL_TIMEOUT_SECONDS = real_timeout
check("the forced run gets only what is left of the job's time limit: timed out, no exit code",
      r == (False, None, "timed_out"))

# The fail-safe swallows ANY exception in the check, so a broken harness (or
# a broken check) reads as a quiet "can't verify" and every test expecting
# that outcome still passes. Only the one probe above may have thrown.
with open(agent.LOG_PATH, encoding="utf-8", errors="replace") as handle:
    thrown = handle.read().count("can't verify - the check itself failed")
check(f"the check threw only where a test made it throw ({thrown} times)", thrown == 1)


print("\n=== 8. The server knows every result, with the same rules ===")
client = appmod.app.test_client()


def raw():
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


conn = raw()
SOFTWARE_ID = conn.execute(
    "INSERT INTO software (name, version, type, install_command) VALUES ('Paint.NET', '5.1.12', 'online', 'choco install paint.net -y')"
).lastrowid
conn.commit()
conn.close()


def report(body, host="LAB-CV"):
    conn = raw()
    job_id = conn.execute(
        "INSERT INTO jobs (computer_hostname, software_id, status, started_at, software_name)"
        " VALUES (?, ?, 'in_progress', CURRENT_TIMESTAMP, 'Paint.NET')", (host, SOFTWARE_ID)).lastrowid
    conn.commit()
    conn.close()
    payload = {"computer_hostname": host}
    payload.update(body)
    res = client.post(f"/api/agent/jobs/{job_id}/report", json=payload)
    conn = raw()
    row = conn.execute("SELECT status, exit_code, result_reason FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    return res, row


seen = {res.reason for res in produced if res.reason}
check(f"this suite produced every check reason the agent has (missing: {sorted(set(agent.CHOCO_CHECK_REASONS) - seen)})",
      set(agent.CHOCO_CHECK_REASONS) <= seen)
mismatches = []
for result in {tuple(x) for x in produced}:
    ok, code, reason = result
    res, row = report({"status": "completed" if ok else "failed", "exit_code": code, "reason": reason})
    if res.status_code != 200 or (row["exit_code"], row["result_reason"]) != (code, reason):
        mismatches.append((result, (row["exit_code"], row["result_reason"])))
check(f"every result above is stored exactly as the agent sent it (mismatches: {mismatches[:3]})", not mismatches)

for reason in agent.CHOCO_CHECK_REASONS:
    res, row = report({"status": "completed", "exit_code": 3010, "reason": reason})
    check(f"{reason} with a restart code is not stored", row["result_reason"] is None)
    res, row = report({"status": "failed", "exit_code": 0, "reason": reason})
    check(f"{reason} on a failed job is not stored", row["result_reason"] is None)

words = {reason: appmod.job_result("completed", 0, reason, None) for reason in
         ("choco_already_installed",) + tuple(agent.CHOCO_CHECK_REASONS)}
check("confirmed: says Windows still lists it, in a calm tone",
      words["choco_confirmed_installed"] and "Windows still lists it" in words["choco_confirmed_installed"]["label"]
      and words["choco_confirmed_installed"]["tone"] == "info")
check("reinstalled: says it was reinstalled", words["choco_reinstalled"] and
      words["choco_reinstalled"]["label"].startswith("Reinstalled") and words["choco_reinstalled"]["tone"] == "info")
unconfirmed = words["choco_reinstall_unconfirmed"] or {}
check("unconfirmed: a warning", unconfirmed.get("tone") == "warn")
check("unconfirmed: claims only that Windows did not CONFIRM it - true when the keys are gone "
      "and when the check could not decide", "not confirm" in unconfirmed.get("label", "")
      and "does not list" not in unconfirmed.get("label", "").lower())
check("unconfirmed: a note that Chocolatey reported success",
      "Chocolatey reported" in unconfirmed.get("note", ""))
check("already installed now notes that the agent could not check Windows",
      "could not check whether Windows still lists it" in (words["choco_already_installed"] or {}).get("note", ""))

from werkzeug.serving import make_server  # noqa: E402

live = make_server("127.0.0.1", 0, appmod.app, threaded=True)
threading.Thread(target=live.serve_forever, daemon=True).start()
agent.SERVER_BASE_URL = f"http://127.0.0.1:{live.server_port}"
agent.SERVER_TIMEOUT_SECONDS = 20
agent.COMPUTER_HOSTNAME = "LAB-CV-E2E"
root = paint_tree()
flip = os.path.join(root, "bin", "calls.txt")
agent.CHOCO_EXE = stand_in(root)
agent.WindowsRegistry = lambda: FakeRegistry(keys_after={PAINT: PRESENT}, names_after=["Paint.NET"], flip_file=flip)
conn = raw()
job_id = conn.execute("INSERT INTO jobs (computer_hostname, software_id, status, software_name)"
                      " VALUES ('LAB-CV-E2E', ?, 'pending', 'Paint.NET')", (SOFTWARE_ID,)).lastrowid
conn.commit()
conn.close()
try:
    agent.poll_once()
finally:
    agent.CHOCO_EXE, agent.WindowsRegistry = real_choco, real_registry_class
conn = raw()
row = conn.execute("SELECT status, exit_code, result_reason FROM jobs WHERE id = ?", (job_id,)).fetchone()
conn.close()
check("job 263's situation through poll_once and the real route: completed, 0, reinstalled",
      tuple(row) == ("completed", 0, "choco_reinstalled"))
live.shutdown()


print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
if skipped:
    print(f"SKIPPED: {len(skipped)} (not counted as passes)")
    for s in skipped:
        print("  -", s)
print("=" * 56)

for handler in list(agent.logger.handlers):
    handler.close()
os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

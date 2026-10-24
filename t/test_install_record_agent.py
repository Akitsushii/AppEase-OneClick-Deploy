"""How agent.py works out what an install left behind: an entry is new only
if its KEY is new, exactly one new visible entry is claimed or none, the
agent's own Python is never claimed, and every record is one the real
server accepts. Section 7 reads this machine's registry, read-only.

    python t/test_install_record_agent.py --agent C:\\Appease\\agent.py
"""
import base64
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
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

work = tempfile.mkdtemp(prefix="appease_recagent_")
shutil.copy(AGENT_PATH, os.path.join(work, "agent.py"))
sys.path.insert(0, work)
import agent  # noqa: E402

if not hasattr(agent, "build_exe_record"):
    print("COULD NOT RUN - agent.py predates install records.")
    sys.exit(3)

for handler in list(agent.logger.handlers):
    if type(handler) is agent.logging.StreamHandler:
        agent.logger.removeHandler(handler)

HKLM, HKCU = agent.HKLM_NAME, agent.HKCU_NAME
SID = "S-1-5-21-1111111111-2222222222-3333333333-1001"
GUID = "{0AA2C178-BF99-3F72-BA87-F6123A1A95C8}"
GUID2 = "{C6A29A65-A91B-4F4A-A1B5-B904AC61255D}"
FOLDERS = [agent._folder_key(p) for p in (
    "C:\\Appease", "C:\\Program Files\\Python314")]


def entry(key, hive=HKLM, view="Registry64", sid=None, **values):
    """An Uninstall key as Windows would hand it over: value names in any
    case, DWORDs as numbers."""
    return agent.uninstall_entry(hive, sid if hive == HKCU else None, view, key,
                                 {name.casefold(): value for name, value in values.items()})


def app(key, name, **values):
    values.setdefault("UninstallString", f'"C:\\Program Files\\{name}\\uninstall.exe"')
    return entry(key, DisplayName=name, **values)


def snap(*entries, user_readable=True):
    return {"entries": {agent.entry_identity(e): e for e in entries}, "user_readable": user_readable}


BASE = [app("Old Tool", "Old Tool"),
        entry("KB123", DisplayName="Update", SystemComponent=1),
        entry(GUID2, DisplayName="Paint.NET", WindowsInstaller=1)]


def record_for(added=(), removed=(), library="Notepad++", before_readable=True, after_readable=True):
    before = snap(*BASE, *removed, user_readable=before_readable)
    after = snap(*BASE, *added, user_readable=after_readable)
    return agent.build_exe_record(before, after, library, FOLDERS)


# ---------------------------------------------------------------------------
print("1. Which entries are new")
# ---------------------------------------------------------------------------
check("a key name in other capitals is the same entry",
      agent.entry_identity(entry("Notepad++")) == agent.entry_identity(entry("NOTEPAD++")))
check("...folded the way Windows folds names: 'ß' is not 'SS'",
      agent.entry_identity(entry("Straße")) != agent.entry_identity(entry("STRASSE")))
check("the 32- and 64-bit HKLM keys are different entries",
      agent.entry_identity(entry("App", view="Registry32")) != agent.entry_identity(entry("App")))
check("a per-user entry is the same whichever view it was read through",
      agent.entry_identity(entry("App", HKCU, "Registry32", SID))
      == agent.entry_identity(entry("App", HKCU, "Registry64", SID.lower())))
check("...and is not the machine-wide entry of the same name",
      agent.entry_identity(entry("App", HKCU, sid=SID)) != agent.entry_identity(entry("App")))

npp = app("Notepad++", "Notepad++", QuietUninstallString='"C:\\npp\\uninstall.exe" /S')
added, removed = agent.snapshot_diff(snap(*BASE), snap(*BASE, npp))
check("a new key is added", [e["key_name"] for e in added] == ["Notepad++"] and removed == [])
added, removed = agent.snapshot_diff(snap(*BASE, npp), snap(*BASE))
check("a key that went is removed", added == [] and [e["key_name"] for e in removed] == ["Notepad++"])
added, removed = agent.snapshot_diff(snap(*BASE, app("Old Tool", "Old Tool 1.0")),
                                     snap(*BASE, app("old TOOL", "Old Tool 2.0")))
check("new values under the same key (any capitals) are an upgrade in place, neither added nor removed",
      added == [] and removed == [])
per_user = entry("GIMP-3_is1", HKCU, sid=SID, DisplayName="GIMP 3.0.6", UninstallString="x")
added, _ = agent.snapshot_diff(snap(*BASE, user_readable=False), snap(*BASE, per_user, user_readable=True))
check("if the user's hive became readable mid-install, its entries are not counted as new", added == [])
added, removed = agent.snapshot_diff(snap(*BASE, per_user, user_readable=True), snap(*BASE, user_readable=False))
check("...nor as removed if it stopped being readable", removed == [] and added == [])
added, _ = agent.snapshot_diff(snap(*BASE), snap(*BASE, per_user))
check("with the hive readable both times, a new per-user entry IS new", len(added) == 1)


# ---------------------------------------------------------------------------
print("2. Which new entry is this install's")
# ---------------------------------------------------------------------------
check("visible: a DisplayName, not a system component, not a child",
      agent.is_visible_entry(npp)
      and not agent.is_visible_entry(entry("X", DisplayName="X", SystemComponent=1))
      and not agent.is_visible_entry(entry("X", DisplayName="X", SystemComponent="1"))
      and not agent.is_visible_entry(entry("X", DisplayName="X", ParentKeyName="Office"))
      and not agent.is_visible_entry(entry("X")))
check("SystemComponent=0 is still visible", agent.is_visible_entry(entry("X", DisplayName="X", SystemComponent=0)))

hidden = entry("Helper", DisplayName="Helper", SystemComponent=1)
child = entry("Child", DisplayName="Child", ParentKeyName="Notepad++")
picked, reason = agent.select_new_entry([npp, hidden, child])
check("exactly one visible new entry is picked, hidden ones beside it do not count",
      picked is npp and reason is None)
check("no visible new entry: no_new_entry",
      agent.select_new_entry([hidden]) == (None, agent.RECORD_REASON_NO_NEW_ENTRY)
      and agent.select_new_entry([]) == (None, agent.RECORD_REASON_NO_NEW_ENTRY))
other = app("Other", "Other App")
check("two visible new entries: several_new_entries",
      agent.select_new_entry([npp, other]) == (None, agent.RECORD_REASON_SEVERAL_NEW_ENTRIES))

bundle = app("{11111111-2222-3333-4444-555555555555}", "Tool Suite",
             BundleUpgradeCode=["{AAAAAAAA-2222-3333-4444-555555555555}"], QuietUninstallString="x /quiet")
msi_part = entry(GUID, DisplayName="Tool Suite Core", WindowsInstaller=1, UninstallString="MsiExec.exe /X" + GUID)
msi_part2 = entry(GUID2.replace("C6", "D7"), DisplayName="VC++ Runtime", WindowsInstaller=1, UninstallString="x")
check("a Burn bundle with visible MSI packages it chained: the bundle is the entry",
      agent.select_new_entry([msi_part, bundle, msi_part2]) == (bundle, None))
check("a Burn bundle alone is simply the one entry", agent.select_new_entry([bundle]) == (bundle, None))
check("a Burn bundle beside a visible NON-MSI entry: several",
      agent.select_new_entry([bundle, other])[1] == agent.RECORD_REASON_SEVERAL_NEW_ENTRIES)
bundle2 = app("{99999999-2222-3333-4444-555555555555}", "Second Suite", BundleProviderKey="p")
check("two Burn bundles: several", agent.select_new_entry([bundle, bundle2])[1]
      == agent.RECORD_REASON_SEVERAL_NEW_ENTRIES)
check("a bundle is recognised by BundleProviderKey as well as BundleUpgradeCode",
      bundle2["burn_bundle"] and not npp["burn_bundle"])


# ---------------------------------------------------------------------------
print("3. Name, protection, and a silent uninstall")
# ---------------------------------------------------------------------------
for library, display, expected in (
    ("VLC", "VLC media player", True),
    ("Notepad++", "Notepad++ (64-bit x64)", True),
    ("7-Zip", "7-Zip 24.08 (x64)", True),
    ("GIMP", "GIMP 3.0.6-1", True),
    ("Mozilla Firefox ESR", "Mozilla Firefox (x64 en-US)", True),
    ("visual studio code", "Microsoft Visual Studio Code (User)", True),
    ("Microsoft Office", "Microsoft Edge", False),
    ("Adobe Acrobat Reader", "Foxit PDF Reader", False),
    ("Notepad++", "", False),
    ("Notepad++", None, False),
    ("2024", "2024", False),
    # Installer noise in a library name. Every display name here has words
    # of its own, so only ignoring the noise can make these pass - the
    # either-way-round rule alone would not.
    ("vlc win64", "VLC media player", True),             # the VM run, job 270
    ("VLC x64 setup", "VLC media player", True),
    ("vlc 64-bit", "VLC media player", True),
    ("vlc 32 bit", "VLC media player", True),
    ("Audacity full installer win32", "Audacity Sound Editor", True),
    ("Krita Studio", "Krita x64", True),                  # noise on the Windows side too
    ("Win32 Disk Imager", "Win32 Disk Imager 1.0", True),
    ("Fullscreen Tool", "Fullscreen Tool Pro", True),
    # ...and what must still fail: a different program installing at the
    # same time, and a name that is nothing but noise.
    ("vlc win64", "Google Chrome", False),
    ("VLC x64 setup", "Notepad++ (64-bit x64)", False),
    ("Office 64-bit", "Microsoft Edge", False),
    ("Win32 Disk Imager", "Disk Cleanup", False),
    ("Fullscreen Tool", "Screen Tool Pro", False),
    ("setup", "VLC media player", False),
    ("x64 installer", "Setup Wizard x64", False),
    ("win64", "win64", False),
    ("64-bit", "Audacity", False),
):
    check(f"name: {library!r} vs {display!r} -> {expected}", agent.name_matches(library, display) is expected)
for noise in ("win64", "win32", "x64", "x86", "amd64", "arm64", "64bit", "32bit", "64-bit", "32-bit", "64 bit",
              "setup", "installer", "install", "offline", "full", "WIN64", "Setup", "64-Bit", "32 BIT"):
    check(f"noise word {noise!r} is ignored: 'vlc {noise}' is VLC media player",
          agent.name_matches(f"vlc {noise}", "VLC media player"))
    check(f"...and a name of only {noise!r} matches nothing", not agent.name_matches(noise, f"VLC {noise}"))
check("noise is cut as whole words only: 'Setupper' and 'Installation Manager' keep theirs",
      not agent.name_matches("Setupper", "Media player") and agent.name_matches("Setupper", "Setupper Pro")
      and not agent.name_matches("Installation Manager", "Manager Plus"))
check("the reinstall check's own name key is untouched: 'vlc win64' still keeps 'win64' there",
      agent.app_name_key("vlc win64") == "vlc win64")

for location, expected in (
    ("C:\\Program Files\\Python314", True),
    ('"C:\\Program Files\\Python314\\"', True),
    ("c:\\program files\\PYTHON314\\Lib", True),
    ("C:\\Program Files", True),
    ("C:\\Appease", True),
    ("C:\\Appease\\tools", True),
    ("C:\\", True),
    ("C:\\Program Files\\Python3140", False),
    ("C:\\Appease2", False),
    ("D:\\Program Files\\Python314", False),
    ("C:\\Program Files\\Notepad++", False),
    ("", False),
    (None, False),
):
    check(f"folder: {location!r} protected -> {expected}",
          agent.is_protected_entry(app("X", "Some App", InstallLocation=location), FOLDERS) is expected)
# As if the agent ran on Python 3.14: its own major.minor is protected by
# name, every other version is not.
for name, expected in (("Python 3.14.2 (64-bit)", True),
                       ("python 3.14.2 Core Interpreter (64-bit)", True),
                       ("Python 3.14.0a1 Development Libraries (32-bit)", True),
                       ("Python 3.13.7 (64-bit)", False),
                       ("Python 3.13.7 Core Interpreter (64-bit)", False),
                       ("Python 3.1.4", False),
                       ("Python 3.141", False),
                       ("Python 2.14.1", False),
                       ("Python Launcher", True), ("python launcher", True),
                       ("Pythonista", False), ("IDLE for Python 3.14", False), ("Python", False)):
    check(f"name, agent on 3.14: {name!r} protected -> {expected}",
          agent.is_protected_entry(app("X", name), FOLDERS, python_version=(3, 14)) is expected)
check("the running interpreter's own major.minor is the default",
      agent.is_protected_entry(app("X", "Python {}.{}.9 (64-bit)".format(*sys.version_info[:2])), FOLDERS)
      and not agent.is_protected_entry(app("X", "Python {}.{}.9 (64-bit)".format(
          sys.version_info[0], sys.version_info[1] + 1)), FOLDERS))
check("a version given as a list works the same as a tuple",
      agent.is_protected_entry(app("X", "Python 3.13.1"), FOLDERS, python_version=[3, 13]))
folders_now = agent.protected_folders()
check("the real protected folders include the agent's folder and its Python's",
      agent._folder_key(agent.AGENT_DIR) in folders_now
      and agent._folder_key(os.path.dirname(sys.executable)) in folders_now)

check("silent: a Windows Installer product named by its ProductCode",
      agent.has_silent_uninstall(entry(GUID, DisplayName="X", WindowsInstaller=1)))
check("...but not WindowsInstaller=1 on a key that is not a GUID",
      not agent.has_silent_uninstall(entry("Not a guid", DisplayName="X", WindowsInstaller=1)))
check("silent: Inno Setup, by its _is1 key AND its App Path value",
      agent.has_silent_uninstall(entry("GIMP-3_is1", DisplayName="X", **{"Inno Setup: App Path": "C:\\gimp"})))
check("...an _is1 key without the App Path value is not Inno",
      not agent.has_silent_uninstall(entry("GIMP-3_is1", DisplayName="X")))
check("silent: the vendor's QuietUninstallString", agent.has_silent_uninstall(npp))
check("not silent: only an UninstallString",
      not agent.has_silent_uninstall(app("VLC media player", "VLC media player")))


# ---------------------------------------------------------------------------
print("4. The record")
# ---------------------------------------------------------------------------
record = record_for(added=[npp])
check("one good entry: an exe record naming it, with nothing unavailable",
      record.get("method") == "exe" and record.get("key_name") == "Notepad++"
      and "unavailable_reason" not in record)
check("...with its registry identity and text as Windows gave them",
      record.get("hive") == HKLM and record.get("view") == "Registry64"
      and record.get("display_name") == "Notepad++"
      and record.get("quiet_uninstall_string") == '"C:\\npp\\uninstall.exe" /S')
check("...and no SID for a machine-wide entry, nor a ProductCode", "sid" not in record and "product_code" not in record)
check("...DisplayVersion included", record_for(added=[app("Notepad++", "Notepad++", DisplayVersion="8.7.1",
                                                          QuietUninstallString="q")]).get("display_version") == "8.7.1")

record = record_for(added=[entry("GIMP-3_is1", HKCU, sid=SID, DisplayName="GIMP 3.0.6", UninstallString="u",
                                 **{"Inno Setup: App Path": "C:\\gimp"})], library="GIMP")
check("a per-user entry carries its SID", record.get("hive") == HKCU and record.get("sid") == SID
      and "unavailable_reason" not in record)
msi = entry(GUID.lower(), DisplayName="Paint.NET 5.1", WindowsInstaller=1, DisplayVersion="5.1.12",
            UninstallString="MsiExec.exe /X" + GUID)
record = record_for(added=[msi], library="Paint.NET")
check("an EXE that installed a Windows Installer product reports its ProductCode",
      record.get("product_code") == GUID.lower() and "unavailable_reason" not in record)
record = record_for(added=[app("VLC media player", "VLC media player")], library="VLC")
check("ours but no silent uninstall: keeps its key, unavailable no_silent_uninstall",
      record.get("key_name") == "VLC media player"
      and record.get("unavailable_reason") == agent.RECORD_REASON_NO_SILENT_UNINSTALL)

for label, rec, reason in (
    ("nothing new", record_for(), agent.RECORD_REASON_NO_NEW_ENTRY),
    ("nothing new, user hive unreadable", record_for(after_readable=False, before_readable=False),
     agent.RECORD_REASON_USER_HIVE_UNREADABLE),
    ("nothing new, user hive readable only afterwards", record_for(before_readable=False),
     agent.RECORD_REASON_USER_HIVE_UNREADABLE),
    ("two new entries", record_for(added=[npp, other]), agent.RECORD_REASON_SEVERAL_NEW_ENTRIES),
    ("a name that does not match", record_for(added=[npp], library="VLC"), agent.RECORD_REASON_NAME_MISMATCH),
    ("a protected folder", record_for(added=[app("Notepad++", "Notepad++", QuietUninstallString="q",
                                                 InstallLocation="C:\\Appease")]), agent.RECORD_REASON_PROTECTED),
    ("the agent's own Python by name",
     record_for(added=[app("{X}", "Python {}.{}.6 (64-bit)".format(*sys.version_info[:2]), QuietUninstallString="q")],
                library="Python"), agent.RECORD_REASON_PROTECTED),
):
    check(f"{label}: unavailable {reason}", rec.get("unavailable_reason") == reason)
    check(f"{label}: names no entry", not ({"hive", "key_name", "product_code", "sid"} & rec.keys()))

other_python = "Python {}.{}.6 (64-bit)".format(sys.version_info[0], sys.version_info[1] - 1)
record = record_for(added=[app("{X}", other_python, QuietUninstallString="q")], library="Python")
check("another Python version deployed for a class is recorded like any app",
      record.get("key_name") == "{X}" and "unavailable_reason" not in record)
check("several new entries is not mistaken for a hive problem",
      record_for(added=[npp, other], before_readable=False, after_readable=False).get("unavailable_reason")
      == agent.RECORD_REASON_SEVERAL_NEW_ENTRIES)


# ---------------------------------------------------------------------------
print("5. What disappeared")
# ---------------------------------------------------------------------------
old_msi = entry(GUID, DisplayName="7-Zip 23", WindowsInstaller=1, UninstallString="u")
old_exe = app("Notepad++ 7", "Notepad++ 7")
old_user = entry("Tool", HKCU, sid=SID, DisplayName="Tool", UninstallString="u")
record = record_for(added=[npp], removed=[old_msi, old_exe, old_user])
replaced = record.get("replaced_entries")
check("a removed Windows Installer product is reported by its ProductCode",
      {"product_code": GUID} in replaced)
check("a removed exe entry by its hive, view and key", {"hive": HKLM, "view": "Registry64",
                                                         "key_name": "Notepad++ 7"} in replaced)
check("a removed per-user entry with its SID",
      {"hive": HKCU, "view": "Registry64", "key_name": "Tool", "sid": SID} in replaced)
check("replaced entries are reported even when the record names no entry",
      record_for(removed=[old_msi]).get("replaced_entries") == [{"product_code": GUID}])
many = [app(f"Gone {i:02d}", f"Gone {i:02d}") for i in range(25)]
many_hidden = [entry(f"Aaa hidden {i:02d}", DisplayName="h", SystemComponent=1) for i in range(5)]
listed = agent.replaced_entries(many_hidden + many)
check("cut at the server's limit", len(listed) == agent.MAX_REPLACED_ENTRIES == 20)
check("visible entries are kept before hidden ones", all(d["key_name"].startswith("Gone") for d in listed))
check("nothing removed: an empty list", record_for(added=[npp]).get("replaced_entries") == [])


# ---------------------------------------------------------------------------
print("6. The real server accepts every record the agent builds")
# ---------------------------------------------------------------------------
server_dir = os.path.join(work, "server")
shutil.copytree(SERVER_SRC, server_dir, ignore=shutil.ignore_patterns(
    "appease.db*", "__pycache__", "*.pyc", ".env", "uploads", "staging", "report_photos", "avatars", "venv"))
sys.path.insert(0, server_dir)
cwd = os.getcwd()
os.chdir(server_dir)
try:
    import database  # noqa: E402
    database.DB_PATH = os.path.join(server_dir, "appease.db")
    database.init_db()
    os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
    import app as appmod  # noqa: E402
finally:
    os.chdir(cwd)

check("every reason the agent can send is one the server accepts from an agent",
      all(r in appmod.INSTALL_RECORD_REASONS and appmod.INSTALL_RECORD_REASONS[r].get("source", "agent") == "agent"
          for r in agent.RECORD_REASONS))
check("the agent folds registry names exactly as the server does",
      all(agent.registry_fold(n) == database._registry_fold(n)
          for n in ("vlc", "Straße", "Éditeur", "GIMP-3_is1", "ǅ", "ﬃ", "İstanbul")))
check("the agent and the server agree on the replaced-entries limit",
      agent.MAX_REPLACED_ENTRIES == appmod.MAX_REPLACED_ENTRIES)
samples = {
    "good exe": record_for(added=[npp]),
    "per-user": record_for(added=[entry("GIMP-3_is1", HKCU, sid=SID, DisplayName="GIMP", UninstallString="u",
                                        **{"Inno Setup: App Path": "C:\\gimp"})], library="GIMP"),
    "EXE that installed an MSI": record_for(added=[msi], library="Paint.NET"),
    "no silent uninstall": record_for(added=[app("VLC media player", "VLC media player")], library="VLC"),
    "nothing new": record_for(),
    "several": record_for(added=[npp, other]),
    "with replaced entries": record_for(added=[npp], removed=[old_msi, old_exe, old_user]),
    "replaced at the limit": dict(record_for(added=[npp]), replaced_entries=listed),
}
for label, sample in samples.items():
    try:
        cleaned = appmod.clean_install_record(sample, 7)
        accepted = cleaned["method"] == "exe" and not cleaned["dropped"]
    except ValueError as exc:
        accepted = False
        print(f"    server refused {label}: {exc}")
    check(f"server accepts: {label}", accepted)
check("the server keys a per-user entry the way the agent identifies it",
      appmod.clean_install_record(samples["per-user"], 7)["key"] == f"exe:HKCU:{SID}:GIMP-3_IS1")
check("...and an EXE-installed MSI by its ProductCode",
      appmod.clean_install_record(samples["EXE that installed an MSI"], 7)["key"] == "msi:" + GUID)


# ---------------------------------------------------------------------------
print("7. This machine's real registry, read-only")
# ---------------------------------------------------------------------------
check("agent.py is plain ASCII: no control characters, no invisible ones "
      "(a backspace and a literal BOM once landed in it through tooling)",
      all(32 <= b < 127 or b in (9, 10, 13) for b in open(AGENT_PATH, "rb").read()))
check("whoami's SID is read from its CSV line",
      agent.parse_whoami_sid('"lab\\\\student","S-1-5-21-1-2-3-1001"\r\n') == "S-1-5-21-1-2-3-1001")
check("...and nothing is invented from other output", agent.parse_whoami_sid("ERROR: access denied") is None
      and agent.parse_whoami_sid(None) is None)

if os.name != "nt" or agent.winreg is None:
    skip("section 7, the real registry", "Windows only - there is no registry on this platform")
else:
    sid = agent.agent_user_sid()
    check("the agent's own SID is found", bool(sid) and bool(agent.USER_SID.match(sid)))
    first = agent.read_uninstall_snapshot(sid)
    second = agent.read_uninstall_snapshot(sid)
    check("the machine-wide Uninstall keys can be read", first is not None and len(first["entries"]) > 0)
    if first is not None:
        check("this account's own hive is loaded and read", first["user_readable"] is True)
        check("two snapshots in a row differ by nothing", agent.snapshot_diff(first, second) == ([], []))
        check("every entry is filed under its own identity",
              all(agent.entry_identity(e) == k for k, e in first["entries"].items()))
        views = {k[1] for k in first["entries"] if k[0] == "HKLM"}
        winreg = agent.winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, agent.UNINSTALL_SUBKEY, 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_32KEY) as wow:
                in_32_bit_view = winreg.QueryInfoKey(wow)[0]
        except OSError:
            in_32_bit_view = 0
        if in_32_bit_view:
            check(f"the 32-bit HKLM view is read ({in_32_bit_view} keys there)", "Registry32" in views)
        else:
            skip("the 32-bit HKLM view is read", "this machine has no 32-bit Uninstall keys")
        check("the 64-bit HKLM view is read", "Registry64" in views)
        check("an unknown SID's hive is reported unreadable, not an error",
              agent.read_uninstall_snapshot("S-1-5-21-0-0-0-424242")["user_readable"] is False)
        check("no SID at all: machine-wide entries only, hive unreadable",
              agent.read_uninstall_snapshot(None)["user_readable"] is False)
        own = "python {}.{}".format(*sys.version_info[:2])
        pythons = [e for e in first["entries"].values() if (e["display_name"] or "").lower().startswith("python ")]
        mine = [e for e in pythons if (e["display_name"] or "").lower().startswith(own + ".")]
        others = [e for e in pythons if e not in mine and "launcher" not in (e["display_name"] or "").lower()]
        if mine:
            check(f"this machine's {own} entries, the running Python's, are all protected ({len(mine)})",
                  all(agent.is_protected_entry(e, agent.protected_folders()) for e in mine))
        else:
            skip("this machine's own Python entries are protected", "the running Python is not registered here")
        if others:
            check(f"its other Python versions are not ({len(others)})",
                  not any(agent.is_protected_entry(e, agent.protected_folders()) for e in others))
        else:
            skip("other Python versions are not protected", "only one Python version is registered here")


# ---------------------------------------------------------------------------
print("8. The ProductCode of a downloaded .msi")
# ---------------------------------------------------------------------------
class Captured(agent.logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())

    def take(self):
        found, self.messages = self.messages, []
        return "\n".join(found)


logs = Captured()
agent.logger.addHandler(logs)


class Runner:
    """Stands in for subprocess.run, and remembers how it was called."""

    def __init__(self, stdout="", returncode=0, stderr="", raises=None):
        self.stdout, self.returncode, self.stderr, self.raises = stdout, returncode, stderr, raises
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.raises is not None:
            raise self.raises
        return types.SimpleNamespace(returncode=self.returncode, stdout=self.stdout, stderr=self.stderr)


HOSTILE = "C:\\Temp\\it's \"$(New-Item canary)\" `x; Remove-Item C:\\ -Recurse & (1) %PATH%.msi"
runner = Runner(stdout=GUID + "\r\n")
logs.take()
code = agent.read_msi_product_code(HOSTILE, run=runner)
argv, kw = runner.calls[0]
check("a good answer is the ProductCode", code == GUID)
check("the file name appears in no part of the command line",
      all(HOSTILE not in part and "canary" not in part and "Remove-Item" not in part for part in argv))
sent = (base64.b64decode(argv[argv.index("-EncodedCommand") + 1]).decode("utf-16-le")
        if "-EncodedCommand" in argv else "")
check("the script goes as -EncodedCommand, not as -Command text", "-EncodedCommand" in argv and "-Command" not in argv)
check("the script sent is exactly the fixed script", sent == agent._MSI_PRODUCT_CODE_SCRIPT)
check("...which reads the path from the environment variable",
      ("$env:" + agent.MSI_PATH_VARIABLE) in sent and "canary" not in sent)
check("the path goes in that variable, byte for byte", kw["env"][agent.MSI_PATH_VARIABLE] == HOSTILE)
check("the rest of the environment goes with it", kw["env"].get("PATH") == os.environ.get("PATH"))
check("the agent's own environment is not changed", agent.MSI_PATH_VARIABLE not in os.environ)
check("no shell, stdin closed", not kw.get("shell") and kw.get("stdin") is subprocess.DEVNULL)
check("a short timeout", 0 < kw.get("timeout", 0) <= 30)
check("the database is opened read-only (mode 0)", "@($env:" + agent.MSI_PATH_VARIABLE + ", 0)" in sent)
check("a good read logs nothing", logs.take() == "")

check("no path: nothing is run", agent.read_msi_product_code("", run=runner) is None and len(runner.calls) == 1)

for output, expected in (
    ("\ufeff" + GUID, GUID),
    ("  " + GUID.lower() + "\n", GUID.lower()),
    (GUID.strip("{}"), GUID.strip("{}")),
    (GUID[:-1], None),
    ("", None),
    ("Exception calling OpenDatabase", None),
    (GUID + GUID, None),
):
    got = agent.read_msi_product_code("x.msi", run=Runner(stdout=output))
    check(f"output {output[:24]!r}... -> {expected!r}", got == expected)
    if expected is None:
        check(f"output {output[:24]!r}...: logged", "did not give a ProductCode" in logs.take())
    logs.take()

CLIXML = ('#< CLIXML\r\n<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04">'
          '<S S="Error">Exception calling "InvokeMember" with "5" argument(s): "OpenDatabase,DatabasePath,'
          'OpenMode"_x000D__x000A_</S><S S="Error">At line:4 char:1_x000D__x000A_</S></Objs>')
got = agent.read_msi_product_code("x.msi", run=Runner(returncode=1, stderr=CLIXML))
text = logs.take()
check("a COM error: no ProductCode", got is None)
check("...logged in words, not XML", "Exception calling" in text and "<Objs" not in text and "_x000D_" not in text)
for raised, words in ((subprocess.TimeoutExpired("powershell", 20), "timed out"),
                      (FileNotFoundError("powershell"), "Could not read the ProductCode"),
                      (RuntimeError("anything at all"), "Could not read the ProductCode")):
    try:
        got = agent.read_msi_product_code("x.msi", run=Runner(raises=raised))
        threw = False
    except BaseException:  # noqa: BLE001 - that IS the failure being checked for
        got, threw = "raised", True
    check(f"{type(raised).__name__}: never raises, no ProductCode", not threw and got is None)
    check(f"{type(raised).__name__}: logged", words in logs.take())
check("plain stderr is kept, and cut short", agent.powershell_error_text("x" * 500) == "x" * 200
      and agent.powershell_error_text("plain failure\r\n") == "plain failure")

for sample in (GUID, GUID.lower(), GUID.strip("{}"), "{" + GUID.strip("{}"), GUID.strip("{}") + "}",
               "{0AA2C178-BF99-3F72-BA87-F6123A1A95C}", "{0AA2C178-BF99-3F72-BA87-F6123A1A95CG}", "", "  " + GUID):
    try:
        database.normalise_product_code(sample)
        server_says = True
    except ValueError:
        server_says = False
    check(f"agent and server agree on {sample!r}", (agent.product_code_or_none(sample) is not None) == server_says)

seven = entry(GUID, DisplayName="7-Zip 24.08 (x64 edition)", DisplayVersion="24.08.00.0", WindowsInstaller=1,
              UninstallString="MsiExec.exe /X" + GUID, InstallLocation="C:\\Program Files\\7-Zip\\")
GUID3 = "{E8B39B76-B02C-4A5B-B2C6-C015BD72366E}"  # not in BASE, unlike GUID2
old_seven = entry(GUID3, DisplayName="7-Zip 23.01", WindowsInstaller=1, UninstallString="MsiExec.exe /X" + GUID3)


def msi_record(code, added=(), removed=(), before_extra=(), before_readable=True, after_readable=True):
    before = snap(*BASE, *removed, *before_extra, user_readable=before_readable)
    after = snap(*BASE, *before_extra, *added, user_readable=after_readable)
    return agent.build_msi_record(code, before, after, FOLDERS)


record = msi_record(GUID, added=[seven])
check("a new product: an msi record naming it by its ProductCode",
      record.get("method") == "msi" and record.get("product_code") == GUID and "unavailable_reason" not in record)
check("...with DisplayName and DisplayVersion from the registry",
      record.get("display_name") == "7-Zip 24.08 (x64 edition)" and record.get("display_version") == "24.08.00.0")
check("...found whatever case the .msi and the registry use",
      msi_record(GUID.lower(), added=[seven]).get("display_version") == "24.08.00.0")
check("...and found when the .msi gave it without braces",
      msi_record(GUID.strip("{}"), added=[seven]).get("display_version") == "24.08.00.0")
flagless = entry(GUID, DisplayName="7-Zip 24.08", DisplayVersion="24.08", UninstallString="u")
check("the ProductCode is in the record even when the entry lacks the WindowsInstaller flag",
      msi_record(GUID, added=[flagless]).get("product_code") == GUID)
record = msi_record(GUID, added=[seven], removed=[old_seven])
check("a major upgrade reports the product it replaced", {"product_code": GUID3} in record["replaced_entries"])
for label, rec, reason in (
    ("the ProductCode could not be read", msi_record(None, added=[seven]), agent.RECORD_REASON_PRODUCT_CODE_UNREADABLE),
    ("the product was already there before the install", msi_record(GUID, before_extra=[seven]),
     agent.RECORD_REASON_NO_NEW_ENTRY),
    ("the product never appeared", msi_record(GUID), agent.RECORD_REASON_NO_NEW_ENTRY),
    ("the product never appeared, user hive unreadable", msi_record(GUID, after_readable=False),
     agent.RECORD_REASON_USER_HIVE_UNREADABLE),
    # Already listed machine-wide before the install: that, not the hive, is
    # why nothing new appeared - and the reason must say so.
    ("already there, user hive unreadable",
     msi_record(GUID, before_extra=[seven], before_readable=False, after_readable=False),
     agent.RECORD_REASON_NO_NEW_ENTRY),
    ("the product is the agent's own Python",
     msi_record(GUID, added=[entry(GUID, DisplayName="Python {}.{}.2 Core Interpreter (64-bit)".format(
         *sys.version_info[:2]), WindowsInstaller=1, UninstallString="u")]), agent.RECORD_REASON_PROTECTED),
):
    check(f"{label}: unavailable {reason}", rec.get("unavailable_reason") == reason)
    check(f"{label}: names no product", not ({"product_code", "key_name", "hive"} & rec.keys()))

for label, sample in (("a new product", msi_record(GUID, added=[seven])),
                      ("a major upgrade", msi_record(GUID, added=[seven], removed=[old_seven])),
                      ("no ProductCode", msi_record(None)),
                      ("already there", msi_record(GUID, before_extra=[seven]))):
    try:
        cleaned = appmod.clean_install_record(sample, 7)
        accepted = cleaned["method"] == "msi" and not cleaned["dropped"]
    except ValueError as exc:
        accepted = False
        print(f"    server refused {label}: {exc}")
    check(f"server accepts the msi record: {label}", accepted)
check("the server keys it by the ProductCode", appmod.clean_install_record(
    msi_record(GUID.lower(), added=[seven]), 7)["key"] == "msi:" + GUID)

if os.name != "nt":
    skip("section 8, a real .msi through COM", "Windows only - the Windows Installer is not on this platform")
else:
    # A real, tiny .msi - a Property table and nothing else - built in a temp
    # folder through the same COM object. It installs nothing and is never
    # run; it only has to be readable. Its name is as hostile as Windows
    # allows (no double quotes there), with two canaries that would create
    # files if any of it were ever executed.
    BUILD = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$installer = New-Object -ComObject WindowsInstaller.Installer
$db = $installer.GetType().InvokeMember('OpenDatabase', 'InvokeMethod', $null, $installer, @($env:APPEASE_TEST_MSI, 3))
$create = 'CREATE TABLE `Property` (`Property` CHAR(72) NOT NULL, `Value` CHAR(0) NOT NULL LOCALIZABLE PRIMARY KEY `Property`)'
$insert = 'INSERT INTO `Property` (`Property`, `Value`) VALUES (' + "'ProductCode', '" + $env:APPEASE_TEST_CODE + "')"
foreach ($sql in @($create, $insert)) {
    $view = $db.GetType().InvokeMember('OpenView', 'InvokeMethod', $null, $db, @($sql))
    $view.GetType().InvokeMember('Execute', 'InvokeMethod', $null, $view, $null) | Out-Null
    $view.GetType().InvokeMember('Close', 'InvokeMethod', $null, $view, $null) | Out-Null
}
$db.GetType().InvokeMember('Commit', 'InvokeMethod', $null, $db, $null) | Out-Null
"""
    folder = tempfile.mkdtemp(prefix="appease msi ")
    real_name = "it's $(New-Item canary1) `x; New-Item canary2; & (1) %PATH%.msi"
    real_path = os.path.join(folder, real_name)
    real_code = "{12345678-ABCD-4EF0-8123-456789ABCDEF}"
    here = os.getcwd()
    os.chdir(folder)
    try:
        built = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand",
             base64.b64encode(BUILD.encode("utf-16-le")).decode("ascii")],
            capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
            env=dict(os.environ, APPEASE_TEST_MSI=real_path, APPEASE_TEST_CODE=real_code))
        if built.returncode != 0 or not os.path.isfile(real_path):
            skip("section 8, a real .msi through COM",
                 "could not build a test .msi here: " + agent.powershell_error_text(built.stderr))
        else:
            logs.take()
            check("a real .msi with a hostile name: its ProductCode is read through COM",
                  agent.read_msi_product_code(real_path) == real_code)
            check("...and nothing in the name ran (no canary files)",
                  [n for n in os.listdir(folder) if n.lower().startswith("canary")] == [])
            check("...and nothing was logged", logs.take() == "")
            junk = os.path.join(folder, "not really.msi")
            with open(junk, "w") as handle:
                handle.write("not an msi")
            check("a file that is not an .msi: no ProductCode", agent.read_msi_product_code(junk) is None)
            check("...and the reason is logged", "Reading the ProductCode failed" in logs.take())
            check("a file that is not there: no ProductCode",
                  agent.read_msi_product_code(os.path.join(folder, "gone.msi")) is None and logs.take() != "")
    finally:
        os.chdir(here)
        shutil.rmtree(folder, ignore_errors=True)


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

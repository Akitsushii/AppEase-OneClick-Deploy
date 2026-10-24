"""The agent choosing an EXE uninstall command, as pure functions: only an
existing .exe inside this PC's Program Files after realpath, never a shell
or script host, and the live HKLM key decides. Command strings are the VM
capture's, with the lab account renamed LabUser.
"""
import ntpath
import os
import secrets
import shutil
import subprocess
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


work = tempfile.mkdtemp(prefix="appease_exe_")
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

if not hasattr(agent, "exe_uninstall_command"):
    print("COULD NOT RUN - agent.py predates choosing an EXE uninstall command.")
    sys.exit(3)
import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()
import app as appmod  # noqa: E402

HKLM = agent.HKLM_NAME
FOLDERS = [agent._folder_key("C:\\Appease")]

# The capture's strings, and a real install record's.
WINSCP_US = '"C:\\Program Files (x86)\\WinSCP\\unins000.exe"'
WINSCP_QUS = '"C:\\Program Files (x86)\\WinSCP\\unins000.exe" /SILENT'
NPP_US = '"C:\\Program Files\\Notepad++\\uninstall.exe"'
NPP_QUS = '"C:\\Program Files\\Notepad++\\uninstall.exe" /S'
VC86_QUS = ('"C:\\ProgramData\\Package Cache\\{4373d0b5-4457-4a80-bad9-029de8df097b}\\VC_redist.x86.exe" '
            '/uninstall /quiet')
DISCORD_QUS = "C:\\Users\\LabUser\\AppData\\Local\\Discord\\Update.exe --uninstall -s"
VLC_US = '"C:\\Program Files\\VideoLAN\\VLC\\uninstall.exe"'
WINSCP_SHORT = "C:\\PROGRA~2\\WinSCP\\unins000.exe"

# ---------------------------------------------------------------------------
print("1. Reading a command: the same as the server, and the arguments as written")
# ---------------------------------------------------------------------------
TABLE = [WINSCP_US, WINSCP_QUS, NPP_US, NPP_QUS, VC86_QUS, DISCORD_QUS, VLC_US,
         "C:\\Program Files\\App\\uninstall.exe /S", "C:\\Program Files\\App\\UNINSTALL.EXE",
         "  " + NPP_QUS + "  ", "MsiExec.exe /X{23170F69-40C1-2702-2408-000001000000}",
         '"C:\\Program Files\\App\\uninstall.exe', '""', "", None, "C:\\Program Files\\App\\uninstall /S",
         "cmd.exe /c del C:\\x", 'rundll32.exe "C:\\Program Files\\App\\x.dll",Uninstall',
         "%ProgramFiles%\\App\\uninstall.exe /S", "\\\\server\\share\\uninstall.exe /S",
         "C:/Program Files/App/uninstall.exe /S", "App\\uninstall.exe", '"C:\\a b\\c.exe"/S',
         "C:\\Program Files\\App\\un.exeinstall.exe /S", "C:\\Program Files\\A.EXE B\\u.exe /S"]
mismatch = [c for c in TABLE if agent.uninstall_program_path(c) != appmod.uninstall_program_path(c)]
check(f"the agent reads every command in the shared table as the server does ({len(TABLE)}; differ: {mismatch})",
      not mismatch)
check("...unquoted, the program ends at the first .exe followed by a space, never at the first space",
      agent.uninstall_program_path("C:\\Program Files\\App\\uninstall.exe /S") == "C:\\Program Files\\App\\uninstall.exe")
ARGS = {WINSCP_QUS: "/SILENT", NPP_QUS: "/S", DISCORD_QUS: "--uninstall -s", WINSCP_US: "",
        VC86_QUS: "/uninstall /quiet", "C:\\Program Files\\App\\uninstall.exe /S /NCRC": "/S /NCRC",
        '"C:\\Program Files\\App\\u.exe" /S /D="C:\\Program Files\\App"': '/S /D="C:\\Program Files\\App"'}
for command, expected in ARGS.items():
    check(f"the arguments of {command!r} are {expected!r}", agent.uninstall_arguments(command) == expected)

SPLITS = {
    "/S": ["/S"], "--uninstall -s": ["--uninstall", "-s"], "/uninstall /quiet": ["/uninstall", "/quiet"],
    "": [], "   ": [], '/D="C:\\Program Files\\App"': ["/D=C:\\Program Files\\App"],
    '"a b" c': ["a b", "c"], 'a\\\\"b c"': ["a\\b c"], 'a\\"b': ['a"b'], 'C:\\dir\\': ["C:\\dir\\"],
    '""': [""], '"a""b"': ['a"b'], "a\tb": ["a", "b"],
}
for text, expected in SPLITS.items():
    check(f"split {text!r} -> {expected}", agent.split_windows_arguments(text) == expected)
for text in ("/S", "/SILENT", "--uninstall -s", "/uninstall /quiet", "/S /NCRC"):
    check(f"{text!r} reaches the program byte for byte",
          subprocess.list2cmdline(agent.split_windows_arguments(text)) == text)
for text in SPLITS:
    again = agent.split_windows_arguments(subprocess.list2cmdline(agent.split_windows_arguments(text)))
    check(f"{text!r}: subprocess quotes back to the same arguments", again == agent.split_windows_arguments(text))

# ---------------------------------------------------------------------------
print("2. This PC's Program Files folders, from Windows' own record")
# ---------------------------------------------------------------------------
same = lambda p: p  # noqa: E731
check("both of Windows' Program Files folders are roots",
      agent.program_files_roots(lambda: ["C:\\Program Files", "C:\\Program Files (x86)"], same)
      == ["C:\\Program Files", "C:\\Program Files (x86)"])
check("...on whatever drive Windows put them", agent.program_files_roots(lambda: ["D:\\Program Files"], same)
      == ["D:\\Program Files"])
check("a value that is a variable, relative, or not text is never a root",
      agent.program_files_roots(lambda: ["%ProgramFiles%", "Program Files", None, 7, "\\\\srv\\pf"], same) == [])


def missing(path):
    raise FileNotFoundError(path)


check("a folder that does not exist is never a root", agent.program_files_roots(lambda: ["C:\\Nope"], missing) == [])
check("...as the real, strict realpath decides",
      agent.program_files_roots(lambda: ["C:\\AppEase-no-such-folder-" + secrets.token_hex(4)]) == [])
check("a value on a drive that still holds a %variable% is never a root",
      agent.program_files_roots(lambda: ["C:\\%ProgramFiles%"], same) == [])
check("an unreadable record gives no roots", agent.program_files_roots(lambda: [], same) == [])
if agent.winreg is None:
    skipped.append("Windows' real Program Files record: not Windows")
else:
    real = agent.program_files_roots()
    wanted = sorted({os.path.realpath(os.environ[v]) for v in ("ProgramW6432", "ProgramFiles(x86)") if v in os.environ})
    check(f"on this PC, the record names the real folders ({real})",
          [p.casefold() for p in real] == [p.casefold() for p in wanted] and len(real) == 2)

# ---------------------------------------------------------------------------
print("3. Only an .exe that is really inside Program Files")
# ---------------------------------------------------------------------------
tree = os.path.join(work, "fs")
PF = os.path.join(tree, "Program Files Fake")
OUT = os.path.join(tree, "Outside")
DATA = os.path.join(tree, "ProgramData")


def touch(*parts):
    path = os.path.join(*parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").close()
    return path


GOOD = touch(PF, "App", "uninstall.exe")
touch(PF, "App", "remove.bat")
touch(OUT, "evil.exe")
touch(DATA, "Helper", "uninstall.exe")
touch(tree, "Program Files Fake (x86)evil", "x.exe")
os.makedirs(os.path.join(PF, "Folder.exe"))
for name in agent.EXE_FORBIDDEN_PROGRAMS:
    touch(PF, "Tools", name)
roots = [os.path.realpath(PF)]
safe = agent.safe_uninstaller

check("an .exe inside Program Files: run, by its resolved path", safe(GOOD, roots) == os.path.realpath(GOOD))
check("..\\ climbing out of Program Files", safe(os.path.join(PF, "App", "..", "..", "Outside", "evil.exe"), roots) is None)
check("ProgramData", safe(os.path.join(DATA, "Helper", "uninstall.exe"), roots) is None)
check("outside every root", safe(os.path.join(OUT, "evil.exe"), roots) is None)
check("a folder whose name only starts like the root", safe(os.path.join(tree, "Program Files Fake (x86)evil", "x.exe"),
                                                           roots) is None)
check("not an .exe", safe(os.path.join(PF, "App", "remove.bat"), roots) is None)
check("an .exe that is not there", safe(os.path.join(PF, "App", "gone.exe"), roots) is None)
check("a folder named like an .exe", safe(os.path.join(PF, "Folder.exe"), roots) is None)
for name in sorted(agent.EXE_FORBIDDEN_PROGRAMS):
    check(f"{name}, even inside Program Files", safe(os.path.join(PF, "Tools", name), roots) is None)
check("the forbidden list is exactly the agreed nine", agent.EXE_FORBIDDEN_PROGRAMS == frozenset((
    "cmd.exe", "powershell.exe", "pwsh.exe", "rundll32.exe", "msiexec.exe", "wscript.exe", "cscript.exe",
    "mshta.exe", "reg.exe")))
check("a %variable%", safe("%ProgramFiles%\\App\\uninstall.exe", roots) is None)
check("forward slashes", safe(GOOD.replace("\\", "/"), roots) is None)
check("forward slashes after the drive, which realpath would quietly turn back",
      safe(GOOD[:3] + GOOD[3:].replace("\\", "/"), roots) is None)
check("a network path", safe("\\\\server\\share\\uninstall.exe", roots) is None)
check("a \\\\?\\ path", safe("\\\\?\\" + GOOD, roots) is None)
check("a relative path", safe("App\\uninstall.exe", roots) is None)
# Both really exist inside Program Files, so only the rule refuses them.
VARIABLE = touch(PF, "%App%", "uninstall.exe")
check("a %variable% that happens to name a real folder", safe(VARIABLE, roots) is None)
here = os.getcwd()
os.chdir(os.path.join(PF, "App"))
try:
    check("a relative path, even from a working folder inside Program Files", safe("uninstall.exe", roots) is None)
finally:
    os.chdir(here)
check("nothing", safe("", roots) is None and safe(None, roots) is None)
check("no roots (Windows' record unreadable): nothing is safe", safe(GOOD, []) is None)

try:
    import _winapi
    link = os.path.join(PF, "Link")
    _winapi.CreateJunction(OUT, link)
    made = os.path.isdir(link)
except (ImportError, AttributeError, OSError):
    made = False
if made:
    check("a junction inside Program Files pointing out of it: followed, and refused",
          safe(os.path.join(PF, "Link", "evil.exe"), roots) is None)
    check("...it really is one (the resolved path is outside)",
          os.path.realpath(os.path.join(PF, "Link", "evil.exe")).casefold() == os.path.realpath(
              os.path.join(OUT, "evil.exe")).casefold())
else:
    skipped.append("a real junction: this PC cannot make one")

try:
    import ctypes
    buffer = ctypes.create_unicode_buffer(1024)
    short = buffer.value if ctypes.windll.kernel32.GetShortPathNameW(GOOD, buffer, 1024) else None
except (AttributeError, OSError):
    short = None
if short and "~" in short:
    check(f"a real 8.3 name ({os.path.basename(os.path.dirname(os.path.dirname(short)))}...): followed, and allowed",
          safe(short, roots) == os.path.realpath(GOOD))
else:
    skipped.append("a real 8.3 name: this volume makes none")

# The capture's own sample, on a machine laid out like the VM.
LONG = {WINSCP_SHORT.casefold(): "C:\\Program Files (x86)\\WinSCP\\unins000.exe",
        "c:\\progra~1\\notepad++\\uninstall.exe": "C:\\Program Files\\Notepad++\\uninstall.exe",
        "c:\\progra~2\\..\\users\\labuser\\x.exe": "C:\\Users\\LabUser\\x.exe"}


def vm_realpath(path):
    """os.path.realpath on a PC laid out like the VM: ".." taken out, the
    capture's 8.3 names expanded, and anything else under C:\\ that the VM
    has left as it is. A path elsewhere does not exist."""
    path = ntpath.normpath(path)
    folded = path.casefold()
    if folded in LONG:
        return LONG[folded]
    if folded.startswith(("c:\\program files\\", "c:\\program files (x86)\\", "c:\\programdata\\", "c:\\users\\",
                          "c:\\windows\\")):
        return path
    raise FileNotFoundError(path)


VM_ROOTS = ["C:\\Program Files", "C:\\Program Files (x86)"]
vm_file = lambda p: True  # noqa: E731
check("the captured C:\\PROGRA~2\\WinSCP\\unins000.exe resolves to Program Files (x86), and is allowed",
      safe(WINSCP_SHORT, VM_ROOTS, vm_realpath, vm_file) == "C:\\Program Files (x86)\\WinSCP\\unins000.exe")
check("PROGRA~1 likewise", safe("C:\\PROGRA~1\\Notepad++\\uninstall.exe", VM_ROOTS, vm_realpath, vm_file)
      == "C:\\Program Files\\Notepad++\\uninstall.exe")
check("PROGRA~2\\..\\ climbing out is refused", safe("C:\\PROGRA~2\\..\\Users\\LabUser\\x.exe", VM_ROOTS,
                                                     vm_realpath, vm_file) is None)

# ---------------------------------------------------------------------------
print("4. The command, from the live key in the recorded view")
# ---------------------------------------------------------------------------


class FakeRegistry:
    def __init__(self, dependents=None):
        self.dependents = {} if dependents is None else dependents

    def installer_dependents(self, product_code):
        if self.dependents == "unreadable":
            return None
        return set(self.dependents.get(product_code.upper(), ()))


def live(key, view, **values):
    """One live HKLM entry; values by registry value name."""
    return agent.uninstall_entry(HKLM, None, view, key, {k.casefold(): v for k, v in values.items()})


def snapshot(*entries):
    return {"entries": {agent.entry_identity(e): e for e in entries}, "user_readable": True}


WINSCP = live("winscp3_is1", "Registry32", DisplayName="WinSCP 6.5.7", UninstallString=WINSCP_US,
              QuietUninstallString=WINSCP_QUS, InstallLocation="C:\\Program Files (x86)\\WinSCP\\",
              Publisher="Martin Prikryl", **{"Inno Setup: App Path": "C:\\Program Files (x86)\\WinSCP"})
NPP = live("Notepad++", "Registry64", DisplayName="Notepad++ (64-bit x64)", UninstallString=NPP_US,
           QuietUninstallString=NPP_QUS, Publisher="Notepad++ Team")
VLC = live("VLC media player", "Registry64", DisplayName="VLC media player", UninstallString=VLC_US,
           Publisher="VideoLAN")


def choose(entries, key, view, registry=None, roots=VM_ROOTS, realpath=vm_realpath, snap="default", **job):
    found = snapshot(*entries) if snap == "default" else snap
    return agent.exe_uninstall_command(dict({"id": 7, "view": view, "key_name": key}, **job), found, roots,
                                       registry or FakeRegistry(), FOLDERS, realpath, vm_file)


got = choose([WINSCP], "winscp3_is1", "Registry32")
check("WinSCP (Inno): unins000.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART",
      (got.argv, got.reason) == (["C:\\Program Files (x86)\\WinSCP\\unins000.exe", "/VERYSILENT",
                                  "/SUPPRESSMSGBOXES", "/NORESTART"], None))
check("...never Inno's own /SILENT", "/SILENT" not in got.argv)
got = choose([NPP], "Notepad++", "Registry64")
check("Notepad++ (vendor quiet string): its own arguments as written",
      (got.argv, got.reason) == (["C:\\Program Files\\Notepad++\\uninstall.exe", "/S"], None))
check("the key is matched without case, as Windows matches it",
      choose([NPP], "NOTEPAD++", "Registry64").argv == got.argv)
check("an argv list: the program first, nothing that is a shell",
      isinstance(got.argv, list) and not any(a.casefold().endswith(("cmd.exe", "powershell.exe")) for a in got.argv))

for label, job_view, job_key in (("a view Windows does not have", "Registry128", "Notepad++"),
                                 ("no view", None, "Notepad++"), ("no key", "Registry64", None),
                                 ("a key with a backslash", "Registry64", "..\\Run"), ("a blank key", "Registry64", ""),
                                 ("a key longer than Windows allows", "Registry64", "a" * 256),
                                 ("a control character", "Registry64", "Note\npad++"), ("a number", "Registry64", 7)):
    check(f"the job names {label}: uninstall_job_invalid, nothing chosen",
          choose([NPP], job_key, job_view)[::2] == (None, "uninstall_job_invalid"))
check("Windows' program list cannot be read: registry_unreadable",
      choose([NPP], "Notepad++", "Registry64", snap=None)[::2] == (None, "registry_unreadable"))
check("the key is gone: handed to the already-gone check",
      choose([], "Notepad++", "Registry64").reason == agent.EXE_KEY_GONE)
check("the key exists only in the other view: the recorded view decides - gone from it",
      choose([WINSCP], "winscp3_is1", "Registry64").reason == agent.EXE_KEY_GONE)
check("no Inno marker and no QuietUninstallString (VLC's NSIS): no_quiet_uninstall",
      choose([VLC], "VLC media player", "Registry64")[::2] == (None, "no_quiet_uninstall"))
not_inno = live("winscp3_is1", "Registry32", DisplayName="WinSCP", UninstallString=WINSCP_US)
check("an _is1 key without Inno's App Path value is not Inno: no_quiet_uninstall",
      choose([not_inno], "winscp3_is1", "Registry32").reason == "no_quiet_uninstall")


def changed(quiet, **extra):
    return live("Notepad++", "Registry64", DisplayName="Notepad++", UninstallString=NPP_US,
                QuietUninstallString=quiet, **extra)


CHANGED = [
    ("changed after install to ProgramData", '"C:\\ProgramData\\Evil\\x.exe" /S'),
    ("changed to cmd.exe /c", "C:\\Windows\\System32\\cmd.exe /c del /q C:\\x"),
    ("changed to cmd.exe copied into Program Files", '"C:\\Program Files\\Notepad++\\cmd.exe" /c del x'),
    ("rundll32", 'rundll32.exe "C:\\Program Files\\App\\x.dll",Uninstall'),
    ("msiexec", "MsiExec.exe /X{23170F69-40C1-2702-2408-000001000000}"),
    ("powershell in Program Files", '"C:\\Program Files\\PowerShell\\7\\pwsh.exe" -c x'),
    ("a %variable%", "%ProgramFiles%\\Notepad++\\uninstall.exe /S"),
    ("a network path", "\\\\server\\share\\uninstall.exe /S"),
    ("a relative path", "uninstall.exe /S"),
    ("forward slashes", "C:/Program Files/Notepad++/uninstall.exe /S"),
    ("..\\ climbing out", '"C:\\Program Files\\Notepad++\\..\\..\\Users\\LabUser\\x.exe" /S'),
    ("PROGRA~2\\..\\ climbing out", '"C:\\PROGRA~2\\..\\Users\\LabUser\\x.exe" /S'),
    ("the uninstaller is not there", '"C:\\Nowhere\\uninstall.exe" /S'),
    ("not an .exe", '"C:\\Program Files\\Notepad++\\uninstall.cmd" /S'),
]
for label, quiet in CHANGED:
    check(f"QuietUninstallString {label}: uninstaller_unsafe_location, nothing chosen",
          choose([changed(quiet)], "Notepad++", "Registry64")[::2] == (None, "uninstaller_unsafe_location"))
got = choose([changed('"C:\\Program Files\\Notepad++\\uninstall.exe" /S /NCRC')], "Notepad++", "Registry64")
check("changed after install, still inside Program Files: the live string is what runs",
      got.argv == ["C:\\Program Files\\Notepad++\\uninstall.exe", "/S", "/NCRC"])
got = choose([changed("C:\\Program Files\\Notepad++\\uninstall.exe /S")], "Notepad++", "Registry64")
check("unquoted with spaces: the whole program, never C:\\Program.exe",
      got.argv == ["C:\\Program Files\\Notepad++\\uninstall.exe", "/S"])
got = choose([changed('"C:\\PROGRA~1\\Notepad++\\uninstall.exe" /S')], "Notepad++", "Registry64")
check("an 8.3 short name: resolved, and run by its long name",
      got.argv == ["C:\\Program Files\\Notepad++\\uninstall.exe", "/S"])
check("Windows' Program Files record unreadable: nothing is safe",
      choose([NPP], "Notepad++", "Registry64", roots=[]).reason == "uninstaller_unsafe_location")
inno_quiet_only = live("app_is1", "Registry64", DisplayName="App", QuietUninstallString='"C:\\Program Files\\App\\unins000.exe" /SILENT',
                       **{"Inno Setup: App Path": "C:\\Program Files\\App"})
check("an Inno entry with only a quiet string: that program, with Inno's silent switches",
      choose([inno_quiet_only], "app_is1", "Registry64").argv
      == ["C:\\Program Files\\App\\unins000.exe", "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
inno_outside = live("evil_is1", "Registry64", DisplayName="Evil", UninstallString='"C:\\ProgramData\\Evil\\unins000.exe"',
                    **{"Inno Setup: App Path": "C:\\ProgramData\\Evil"})
check("an Inno entry whose uninstaller is outside Program Files: refused like any other",
      choose([inno_outside], "evil_is1", "Registry64").reason == "uninstaller_unsafe_location")

own = f"Python {sys.version_info[0]}.{sys.version_info[1]}.1 (64-bit)"
for label, entry in (("in C:\\Appease", changed(NPP_QUS, InstallLocation="C:\\Appease\\tool\\")),
                     ("the agent's own Python", live("Py", "Registry64", DisplayName=own, QuietUninstallString=NPP_QUS)),
                     ("the Python Launcher", live("Py", "Registry64", DisplayName="Python Launcher",
                                                  QuietUninstallString=NPP_QUS))):
    check(f"protected - {label}: protected_software",
          choose([entry], entry["key_name"], "Registry64")[::2] == (None, "protected_software"))
VC_KEY = "{4373d0b5-4457-4a80-bad9-029de8df097b}"
vc = live(VC_KEY, "Registry32", DisplayName="Microsoft Visual C++ v14 Redistributable (x86) - 14.44.35211",
          Publisher="Microsoft Corporation", QuietUninstallString=VC86_QUS)
check("a Microsoft runtime by publisher and name: shared_runtime",
      choose([vc], VC_KEY, "Registry32")[::2] == (None, "shared_runtime"))
other = live(VC_KEY, "Registry32", DisplayName="Some Bundle", Publisher="Someone", QuietUninstallString=NPP_QUS)
check("a GUID key another product depends on: shared_runtime",
      choose([other], VC_KEY, "Registry32", registry=FakeRegistry({VC_KEY.upper(): {"{B0E5A7C1-1111}"}})).reason
      == "shared_runtime")
check("...those records unreadable: registry_unreadable",
      choose([other], VC_KEY, "Registry32", registry=FakeRegistry("unreadable")).reason == "registry_unreadable")
check("...and nobody depends on it: on to the command", choose([other], VC_KEY, "Registry32").reason is None)

# ---------------------------------------------------------------------------
print("5. Nothing runs yet")
# ---------------------------------------------------------------------------


def no_process(*args, **kwargs):
    raise AssertionError("a process was started")


real_popen, real_run = agent.subprocess.Popen, agent.subprocess.run
agent.subprocess.Popen = agent.subprocess.run = no_process
try:
    choose([WINSCP], "winscp3_is1", "Registry32")
    choose([NPP], "Notepad++", "Registry64")
    choose([changed("C:\\Windows\\System32\\cmd.exe /c x")], "Notepad++", "Registry64")
    ran = False
except AssertionError:
    ran = True
finally:
    agent.subprocess.Popen, agent.subprocess.run = real_popen, real_run
check("choosing a command starts no process", not ran)
check("choosing is not running: the EXE uninstaller (step 3) is a separate function, reached through the "
      "dispatcher that also takes per-user jobs (2026-10-07)",
      agent.UNINSTALLERS.get("exe") is agent.uninstall_exe_job and agent.uninstall_exe is not agent.exe_uninstall_command)
check("its refusals are the server's codes",
      {agent.REASON_UNINSTALLER_UNSAFE_LOCATION, agent.REASON_NO_QUIET_UNINSTALL} <= set(appmod.JOB_RESULT_REASONS)
      and {agent.REASON_UNINSTALLER_UNSAFE_LOCATION, agent.REASON_NO_QUIET_UNINSTALL}
      <= set(agent.UNINSTALL_REFUSAL_REASONS))

print()
if skipped:
    print("skipped:", "; ".join(skipped))
print(f"{passed} passed, {len(failed)} failed")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

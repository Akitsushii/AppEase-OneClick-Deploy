import argparse
import base64
import collections
import ctypes
import http.client
import json
import logging
import ntpath
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ElementTree
import zipfile
from logging.handlers import RotatingFileHandler
from urllib import request, error
from urllib.parse import urlsplit

try:
    import winreg
except ImportError:
    # Not Windows: the Chocolatey check then always says "can't verify".
    winreg = None

# 8080 = app.py --prod (Waitress); 5000 (Flask dev) fails silently.
# Replace YOUR-SERVER-IP with the AppEase server's LAN IP before setup.bat.
SERVER_BASE_URL = "http://YOUR-SERVER-IP:8080"
POLL_INTERVAL_SECONDS = 10
COMPUTER_HOSTNAME = socket.gethostname()

# ProgramData: setup.bat overwrites the agent folder.
AGENT_ID_PATH = os.path.join(
    os.environ.get("ProgramData") or r"C:\ProgramData", "AppEase", "agent_id.txt"
)
AGENT_ID = None

# ---- Logging setup ----
# Rotates (~1 MB x 3) so a 24/7 poller never fills the disk.
AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(AGENT_DIR, "agent.log")

logger = logging.getLogger("appease_agent")
logger.setLevel(logging.INFO)

_formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

try:
    _file_handler = RotatingFileHandler(
        LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    _file_handler.setFormatter(_formatter)
    logger.addHandler(_file_handler)
except OSError:
    # A log file problem must never stop the agent.
    pass

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_formatter)
logger.addHandler(_console_handler)


# ---- Machine identity ----
# A random ID kept on disk tells same-named lab PCs apart, which hostname
# alone cannot. Random, not MachineGuid: cloned images share that one.
# Failures give None and a warning; the hostname is then used alone.
_AGENT_ID_PATTERN = re.compile(r"[0-9a-f]{32}")


def _create_agent_id_file(path):
    """Write a new ID to path, unless something already exists there."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # O_EXCL: if two agents start at once, only one writes the ID.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        return
    except OSError as exc:
        logger.warning(f"Could not create agent ID file {path} ({error_text(exc)}).")
        return
    try:
        try:
            os.write(fd, (secrets.token_hex(16) + "\n").encode("ascii"))
        finally:
            # Always closed: Windows can't delete an open file.
            os.close(fd)
    except OSError as exc:
        logger.warning(f"Could not write agent ID file {path} ({error_text(exc)}).")
        # A half-written file would read as damaged; next boot retries.
        try:
            os.remove(path)
        except OSError:
            pass


def load_agent_id(path=None):
    """This machine's ID, created on first run; None if unusable. Only an
    ID read back from disk is used and an invalid file is never replaced:
    either would make the server see a new machine."""
    path = path or AGENT_ID_PATH
    if not os.path.exists(path):
        _create_agent_id_file(path)
    try:
        with open(path, "r", encoding="ascii") as handle:
            text = handle.read(100).strip().lower()
    except (OSError, ValueError) as exc:
        logger.warning(
            f"Could not read agent ID from {path} ({error_text(exc)}); "
            f"identifying this machine by hostname only."
        )
        return None
    if not _AGENT_ID_PATTERN.fullmatch(text):
        logger.warning(
            f"{path} does not hold a valid agent ID; identifying this machine "
            f"by hostname only."
        )
        return None
    return text


def identity(**fields):
    """Request body naming this machine, plus fields; no agent_id if unset."""
    body = {"computer_hostname": COMPUTER_HOSTNAME}
    if AGENT_ID:
        body["agent_id"] = AGENT_ID
    body.update(fields)
    return body


# ---- Desktop shortcut ----
# A .url (INI text; a .lnk needs pywin32 or COM) for students without a
# scannable QR sticker. Deliberately NO token: the server knows the PC by
# its IP, and a token would put the server's secret on every lab PC.
SHORTCUT_FILENAME = "AppEase \u2014 Report a Problem.url"
SHORTCUT_RECHECK_SECONDS = 3600


def shortcut_body():
    # Windows expects CRLF; newline="" at the write site stops \r\r\n.
    return "[InternetShortcut]\r\nURL={0}/report\r\n".format(SERVER_BASE_URL)


def shortcut_folders():
    """Where to try putting it, best first. Only the Public desktop shows
    for every account; USERPROFILE is just the task account's, hence the
    caller's warning."""
    folders = []
    public_root = os.environ.get("PUBLIC")
    if public_root:
        folders.append((os.path.join(public_root, "Desktop"), True))
    else:
        folders.append((r"C:\Users\Public\Desktop", True))
    profile = os.environ.get("USERPROFILE")
    if profile:
        folders.append((os.path.join(profile, "Desktop"), False))
    return folders


def write_desktop_shortcut():
    """Create or refresh the shortcut; its path or None. Never raises:
    polling matters more than the icon."""
    body = shortcut_body()

    for folder, is_public in shortcut_folders():
        target = os.path.join(folder, SHORTCUT_FILENAME)
        try:
            if not os.path.isdir(folder):
                continue

            # A new SERVER_BASE_URL propagates; an intact file is untouched.
            if os.path.isfile(target):
                try:
                    with open(target, "r", encoding="utf-8", newline="") as existing:
                        if existing.read() == body:
                            return target
                except OSError:
                    # Unreadable: overwriting it is the repair.
                    pass

            with open(target, "w", encoding="utf-8", newline="") as handle:
                handle.write(body)

            if is_public:
                logger.info(f"Desktop shortcut written: {target}")
            else:
                logger.warning(
                    f"Public desktop unavailable; shortcut written to {target} "
                    f"instead. That is only the desktop of the account this "
                    f"agent runs as, so anyone signed in as a different "
                    f"account will not see it - check the permissions on the "
                    f"Public desktop folder."
                )
            return target
        except OSError as exc:
            logger.warning(f"Could not write shortcut to {folder}: {exc}")

    logger.error(
        "Could not write the report shortcut to any desktop folder. "
        "Deployments are unaffected; students on this PC will need the "
        "QR sticker instead."
    )
    return None


# ---- Hardware inventory ----
# One PowerShell process, not four wmic calls: one timeout, all-or-nothing.
# Get-CimInstance: Get-WmiObject is deprecated and its DCOM transport is
# what group policy restricts. No -ExecutionPolicy: -Command ignores it.
INVENTORY_RECHECK_SECONDS = 3600
INVENTORY_TIMEOUT_SECONDS = 30

# After a failed SEND only: the server may be back any moment, while
# missing PowerShell or WMI will not fix itself within the hour.
INVENTORY_RETRY_SECONDS = 60

# Strings, not a bool: callers must tell collect from send failures.
INVENTORY_SENT = "sent"
INVENTORY_SEND_FAILED = "send_failed"
INVENTORY_NO_FACTS = "no_facts"

HARDWARE_PS_COMMAND = (
    "$ErrorActionPreference='SilentlyContinue';"
    "$cs = Get-CimInstance Win32_ComputerSystem;"
    "$bios = Get-CimInstance Win32_BIOS;"
    "$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1;"
    "$os = Get-CimInstance Win32_OperatingSystem;"
    "$disk = Get-CimInstance Win32_DiskDrive | "
    "Where-Object { $_.MediaType -notlike '*Removable*' } | "
    "Sort-Object Size -Descending | Select-Object -First 1;"
    "[pscustomobject]@{"
    "manufacturer = $cs.Manufacturer;"
    "model = $cs.Model;"
    "serial_number = $bios.SerialNumber;"
    "cpu = $cpu.Name;"
    "ram_mb = [int]($cs.TotalPhysicalMemory / 1MB);"
    "storage_gb = [int]($disk.Size / 1GB);"
    "os_name = $os.Caption"
    "} | ConvertTo-Json -Compress"
)

HARDWARE_FIELDS = (
    "manufacturer", "model", "serial_number", "cpu",
    "ram_mb", "storage_gb", "os_name",
)


def parse_hardware_json(raw_text):
    """PowerShell output as a dict, or None. Never raises. Separate from the
    subprocess call so it can be tested off Windows."""
    if not raw_text:
        return None
    text = raw_text.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    # A list if ConvertTo-Json got several objects (guards -First 1 edits).
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        return None

    facts = {}
    for key in HARDWARE_FIELDS:
        value = data.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        facts[key] = value
    return facts or None


def collect_hardware_facts():
    """Run the WMI query; a dict or None. Fails soft so polling never stops."""
    try:
        # No console flash every hour; getattr for non-Windows test runs.
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        completed = subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive",
                "-Command", HARDWARE_PS_COMMAND,
            ],
            capture_output=True,
            text=True,
            timeout=INVENTORY_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
    except FileNotFoundError:
        logger.warning("PowerShell not found; skipping hardware inventory.")
        return None
    except subprocess.TimeoutExpired:
        logger.warning(
            f"Hardware inventory timed out after {INVENTORY_TIMEOUT_SECONDS}s; skipping."
        )
        return None
    except OSError as exc:
        logger.warning(f"Could not run hardware inventory: {exc}")
        return None

    if completed.returncode != 0:
        detail = (completed.stderr or "").strip()[:200]
        logger.warning(f"Hardware inventory command failed: {detail}")
        return None

    facts = parse_hardware_json(completed.stdout)
    if not facts:
        logger.warning("Hardware inventory returned nothing usable.")
    return facts


# Facts from a failed SEND, so the retry skips re-running PowerShell.
# Cleared on success, so the hourly pass re-reads (e.g. a RAM upgrade).
_pending_hardware_facts = None


def inventory_due_seconds(status):
    """Delay before the next inventory attempt, given the last status."""
    if status == INVENTORY_SEND_FAILED:
        return INVENTORY_RETRY_SECONDS
    return INVENTORY_RECHECK_SECONDS


def report_hardware_inventory():
    """Collect and send; one of INVENTORY_*. Sent hourly even if unchanged,
    so its reported-at date never looks stale."""
    global _pending_hardware_facts

    facts = _pending_hardware_facts or collect_hardware_facts()
    if not facts:
        _pending_hardware_facts = None
        return INVENTORY_NO_FACTS

    payload = identity(ip_address=get_local_ip())
    payload.update(facts)

    try:
        response = post_json("/api/agent/inventory", payload)
    except SERVER_ERRORS as exc:
        # Kept so the retry skips WMI; non-AppEase replies land here too.
        _pending_hardware_facts = facts
        logger.warning(
            f"Could not send hardware inventory to {describe_server_error(exc)}. "
            f"Retrying in {INVENTORY_RETRY_SECONDS}s."
        )
        return INVENTORY_SEND_FAILED

    _pending_hardware_facts = None
    recorded = response.get("recorded") or []
    logger.info(f"Hardware inventory sent ({len(recorded)} field(s) recorded).")
    return INVENTORY_SENT


# A constant so tests can shorten it.
SERVER_TIMEOUT_SECONDS = 20


class UnexpectedResponse(ValueError):
    """Something answered at SERVER_BASE_URL, but not with a JSON object
    (usually another web service)."""


# Any "no usable answer" failure; callers log and retry next pass. Not just
# URLError: urllib wraps only SEND errors, and reading a reply can raise
# ConnectionAbortedError, RemoteDisconnected, TimeoutError or
# IncompleteRead. ValueError is UnexpectedResponse.
SERVER_ERRORS = (OSError, http.client.HTTPException, ValueError)


def server_address():
    """Host:port this agent dials, port always explicit: it is what's most
    often wrong, and a bare URL hides that it means 80."""
    parts = urlsplit(SERVER_BASE_URL)
    try:
        port = parts.port
    except ValueError:
        return f"{parts.netloc} (unreadable port)"
    if port is None:
        port = 443 if parts.scheme == "https" else 80
    return f"{parts.hostname}:{port}"


def get_local_ip():
    """Local address on the adapter that reaches the server, or None. UDP
    connect() sends nothing, only picks the route; 8.8.8.8 would name the
    internet adapter instead (Wi-Fi on a dual-homed laptop)."""
    try:
        parts = urlsplit(SERVER_BASE_URL)
        host = parts.hostname
        if not host:
            return None
        # Any port picks the same route.
        port = parts.port or 80
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((host, port))
            return s.getsockname()[0]
    except (OSError, ValueError):
        # ValueError: .port raises it for an unreadable port.
        return None


def error_text(exc):
    """"TypeName: message" on one line; some carry raw server bytes with
    newlines (BadStatusLine)."""
    return " ".join(f"{type(exc).__name__}: {exc}".split())


def describe_server_error(exc):
    """Server address plus error, for one log line; urllib's messages never
    name the address."""
    return f"{server_address()} ({error_text(exc)})"


def post_json(path, payload):
    url = f"{SERVER_BASE_URL}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")

    with request.urlopen(req, timeout=SERVER_TIMEOUT_SECONDS) as response:
        body = response.read()

    # Callers use .get(): fail here clearly, not with AttributeError later.
    try:
        parsed = json.loads(body.decode("utf-8"))
    except ValueError:
        raise UnexpectedResponse(f"reply was not JSON: {body[:60]!r}") from None
    if not isinstance(parsed, dict):
        raise UnexpectedResponse(f"reply was JSON but not an object: {body[:60]!r}")
    return parsed


SUCCESS_EXIT_CODES = {0, 3010, 1641}  # 3010/1641 = reboot, not failure
INSTALL_HEARTBEAT_SECONDS = 10
INSTALL_TIMEOUT_SECONDS = 1800  # 30 min, for large installers (Blender)
CHOCO_EXE = r"C:\ProgramData\chocolatey\bin\choco.exe"


# ---- Why an install ended the way it did ----
# Results carry a reason CODE; the server owns the wording and must list
# each code in JOB_RESULT_REASONS (t/test_job_results.py checks). Only
# observed reasons: no exit code after a timeout kill, and exit codes read
# only for the program defining them (1603 is "fatal" only from msiexec;
# 1 is also cmd.exe's missing-path code).
REASON_TIMED_OUT = "timed_out"
REASON_DOWNLOAD_FAILED = "download_failed"
REASON_CHOCO_MISSING = "choco_missing"
REASON_EMPTY_COMMAND = "empty_command"
REASON_LAUNCH_FAILED = "launch_failed"
REASON_RESTART_NEEDED = "restart_needed"
REASON_RESTART_STARTED = "restart_started"
REASON_INSTALLER_EXIT_CODE = "installer_exit_code"
REASON_CHOCO_ALREADY_INSTALLED = "choco_already_installed"
REASON_CHOCO_PACKAGE_NOT_FOUND = "choco_package_not_found"
REASON_CHOCO_ERROR = "choco_error"
REASON_CHOCO_RESTART_PENDING = "choco_restart_pending"
REASON_CHOCO_INSTALL_INCOMPLETE = "choco_install_incomplete"
REASON_CHOCO_CONFIRMED_INSTALLED = "choco_confirmed_installed"
REASON_CHOCO_REINSTALLED = "choco_reinstalled"
REASON_CHOCO_REINSTALL_UNCONFIRMED = "choco_reinstall_unconfirmed"
CHOCO_CHECK_REASONS = (
    REASON_CHOCO_CONFIRMED_INSTALLED,
    REASON_CHOCO_REINSTALLED,
    REASON_CHOCO_REINSTALL_UNCONFIRMED,
)

# Choco's error is -1 or 1; -1 arrives unsigned, as 4294967295 (measured).
CHOCO_ERROR_CODES = {1, -1, 4294967295}

# Codes choco documents (2.5.1); others are a package's own, so ambiguous.
CHOCO_EXIT_REASONS = {
    1: REASON_CHOCO_ERROR,
    -1: REASON_CHOCO_ERROR,
    4294967295: REASON_CHOCO_ERROR,
    350: REASON_CHOCO_RESTART_PENDING,
    1604: REASON_CHOCO_INSTALL_INCOMPLETE,
    3010: REASON_RESTART_NEEDED,
    1641: REASON_RESTART_STARTED,
}

# Only msiexec defines these; from anything else they mean nothing.
MSI_EXIT_REASONS = {
    1602: "msi_cancelled",
    1603: "msi_fatal",
    1618: "msi_busy",
    1619: "msi_unreadable",
    1625: "msi_policy",
    1633: "msi_platform",
    1638: "msi_other_version",
    3010: REASON_RESTART_NEEDED,
    1641: REASON_RESTART_STARTED,
}


# Trusted from any program: Windows' message table words these as general
# results. As a pair, so "Restart needed" finds every rebooting PC. 1603
# and 1618 read as Windows Installer failures, so stay msiexec-only.
WINDOWS_EXIT_REASONS = {
    3010: REASON_RESTART_NEEDED,
    1641: REASON_RESTART_STARTED,
}


class InstallResult(collections.namedtuple("InstallResult", "ok exit_code reason")):
    """ok, exit code or None, reason or None. __bool__ raises: a tuple is
    always truthy and would read every install as a success."""

    __slots__ = ()

    def __bool__(self):
        raise TypeError("InstallResult has no truth value; read .ok")


def install_producer(command):
    """Who defines this command's exit codes: choco, msiexec or installer."""
    text = (command or "").strip()
    if text.startswith("choco "):
        return "choco"
    if re.match(r"msiexec(?:\.exe)?\s", text, re.IGNORECASE):
        return "msiexec"
    return "installer"


def choco_package_id(command):
    """The one package `choco install <id>` names, or None: a guess would
    aim the output checks at the wrong package."""
    tokens = (command or "").split()
    if len(tokens) < 3 or tokens[0] != "choco" or tokens[1] != "install":
        return None
    names = []
    for token in tokens[2:]:
        if token.startswith("-"):
            break
        names.append(token)
    return names[0] if len(names) == 1 else None


_CHOCO_SUMMARY = re.compile(
    r"^Chocolatey installed (\d+)/(\d+) packages\.(?: (\d+) packages failed\.)?$"
)


def _choco_summary(lines):
    """(installed, total, failed) from the last summary line, or None."""
    found = None
    for line in lines:
        match = _CHOCO_SUMMARY.match(line.strip())
        if match:
            found = (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))
    return found


def _choco_block(lines, heading):
    """Lines under a "Warnings:" or "Failures" heading, to a blank line."""
    for index, line in enumerate(lines):
        if line.strip() == heading:
            block = []
            for entry in lines[index + 1:]:
                if not entry.strip():
                    break
                block.append(entry.strip())
            return block
    return []


def choco_output_reason(package, exit_code, output):
    """A reason from Chocolatey's output, or None; each needs three agreeing
    signals. Entries must name THIS package (a fresh Paint.NET install can
    report its dependency already installed). Headings match whole lines:
    a pending reboot adds a "Validation Warnings:" block first."""
    if not package or not output:
        return None
    lines = output.splitlines()
    summary = _choco_summary(lines)
    if summary is None:
        return None
    installed, total, failed = summary
    name = re.escape(package)

    if exit_code == 0 and failed == 0 and installed < total:
        entry = re.compile(
            r"^-\s+" + name + r"\s+-\s+" + name + r"\s+v\S+\s+already installed\.$",
            re.IGNORECASE,
        )
        if any(entry.match(line) for line in _choco_block(lines, "Warnings:")):
            return REASON_CHOCO_ALREADY_INSTALLED

    if exit_code in CHOCO_ERROR_CODES and failed > 0:
        entry = re.compile(
            r"^-\s+" + name + r"\s+-\s+" + name
            + r"\s+not installed\. The package was not found with the source\(s\) listed\.$",
            re.IGNORECASE,
        )
        if any(entry.match(line) for line in _choco_block(lines, "Failures")):
            return REASON_CHOCO_PACKAGE_NOT_FOUND

    return None


def classify_install_result(command, exit_code, output="", timed_out=False):
    """A finished process as an InstallResult; separate so the rules can be
    tested against captured output."""
    if timed_out:
        return InstallResult(False, None, REASON_TIMED_OUT)

    ok = exit_code in SUCCESS_EXIT_CODES
    producer = install_producer(command)
    reason = None
    if producer == "choco":
        reason = choco_output_reason(choco_package_id(command), exit_code, output)
        if reason is None:
            reason = CHOCO_EXIT_REASONS.get(exit_code)
    elif producer == "msiexec":
        reason = MSI_EXIT_REASONS.get(exit_code)
    else:
        reason = WINDOWS_EXIT_REASONS.get(exit_code)

    if reason is None and exit_code != 0:
        reason = REASON_INSTALLER_EXIT_CODE
    return InstallResult(ok, exit_code, reason)


# ---- Is a package Chocolatey calls "already installed" really there? ----
# Chocolatey's records outlive the app: uninstall it through Windows and a
# redeploy prints "already installed" and installs nothing. So the agent
# checks Windows against .chocolatey\<id>.<ver>\.registry, the uninstall keys
# recorded at install time. Anything unreadable means "can't verify", which
# forces nothing.
#
# Each recorded key gets three signals (the key alone fails on self-updating
# apps such as Chrome and Opera, which change their key):
#   K  exact key: present in its view, absent from BOTH views, else unknown
#   N  display name, ignoring case, punctuation and versions; blank abstains
#   L  install folder on disk; blank abstains (Chrome and Paint.NET leave it
#      blank)
# present = K present. gone = K absent, at least one of N/L absent, and
# neither present nor unknown (two abstentions prove nothing). Else unknown.
# Packages with no recorded keys are never judged and never count as present.

CHOCO_INSTALLED = "installed"
CHOCO_REMOVED = "removed"
CHOCO_UNVERIFIED = "unverified"

SIGNAL_PRESENT = "present"
SIGNAL_ABSENT = "absent"
SIGNAL_UNKNOWN = "unknown"
SIGNAL_ABSTAIN = "abstain"

KEY_PRESENT = "present"
KEY_GONE = "gone"
KEY_UNKNOWN = "unknown"

# Anything spliced into a forced reinstall command must match this, and so
# must every id and version the check reads, or the answer is "can't verify".
CHOCO_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9._-]+$")
UNINSTALL_SUBKEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
# Chocolatey records the view separately and never writes WOW6432Node into
# the path, so a path that is not exactly this shape is not understood.
SNAPSHOT_KEY_PATH = re.compile(
    r"^(HKEY_LOCAL_MACHINE|HKEY_CURRENT_USER)\\" + re.escape(UNINSTALL_SUBKEY) + r"\\([^\\]+)$"
)
SNAPSHOT_VIEWS = ("Registry64", "Registry32")
INSTALLER_DEPENDENCIES_KEY = r"SOFTWARE\Classes\Installer\Dependencies"
USER_SID = re.compile(r"^S-1(?:-\d+)+$")
CHOCO_MAX_PACKAGES = 50
CHOCO_MAX_RECORD_BYTES = 1_000_000

# Missing (never judged) differs from unreadable (None: can't verify).
NO_SNAPSHOT = ("no snapshot",)


def app_name_key(name):
    """A display name as compared, or None. Loose on purpose: over-matching
    only means "can't verify", never a wrong reinstall. "GIMP 3.0.6-1"
    becomes "gimp"."""
    text = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", name or "").casefold()
    words = [w for w in re.split(r"[\W_]+", text) if w and not re.fullmatch(r"v?\d+", w)]
    return " ".join(words) or None


def install_location_signal(recorded):
    """L for one key: present, absent, unknown, or abstain when blank."""
    text = (recorded or "").strip().strip('"').strip()
    if not text:
        return SIGNAL_ABSTAIN
    # Plain local paths only; a missing drive says nothing about the app.
    if "%" in text or not re.match(r"^[A-Za-z]:\\", text):
        return SIGNAL_UNKNOWN
    try:
        os.stat(text[:3])
    except (OSError, ValueError):
        return SIGNAL_UNKNOWN
    try:
        os.stat(text)
    except (FileNotFoundError, NotADirectoryError):
        return SIGNAL_ABSENT
    except (OSError, ValueError):
        return SIGNAL_UNKNOWN
    return SIGNAL_PRESENT


class WindowsRegistry:
    """Read-only answers from the real registry. Tests pass their own."""

    def _view_flag(self, view):
        return {"Registry64": winreg.KEY_WOW64_64KEY, "Registry32": winreg.KEY_WOW64_32KEY}[view]

    def loaded_user_hives(self):
        """The names under HKEY_USERS, casefolded, or None if unreadable."""
        if winreg is None:
            return None
        try:
            count = winreg.QueryInfoKey(winreg.HKEY_USERS)[0]
            return {winreg.EnumKey(winreg.HKEY_USERS, i).casefold() for i in range(count)}
        except OSError:
            return None

    def uninstall_key(self, hive, sid, name, view):
        """K in one view. A per-user key is read from the recorded user's
        hive; an unloaded hive is unknown, not absent."""
        if winreg is None or view not in SNAPSHOT_VIEWS:
            return SIGNAL_UNKNOWN
        if hive == "HKEY_LOCAL_MACHINE":
            root, path = winreg.HKEY_LOCAL_MACHINE, UNINSTALL_SUBKEY + "\\" + name
        elif hive == "HKEY_CURRENT_USER":
            hives = self.loaded_user_hives()
            if not sid or not USER_SID.match(sid) or hives is None or sid.casefold() not in hives:
                return SIGNAL_UNKNOWN
            root, path = winreg.HKEY_USERS, sid + "\\" + UNINSTALL_SUBKEY + "\\" + name
        else:
            return SIGNAL_UNKNOWN
        try:
            winreg.CloseKey(winreg.OpenKey(root, path, 0, winreg.KEY_READ | self._view_flag(view)))
        except FileNotFoundError:
            return SIGNAL_ABSENT
        except OSError:
            return SIGNAL_UNKNOWN
        return SIGNAL_PRESENT

    def display_names(self, required_sids):
        """app_name_key() of every uninstall entry (both HKLM views, loaded
        user hives), or None if any is unreadable or a snapshot user's hive
        is not loaded."""
        listed = self.listed_display_names(required_sids)
        if listed is None:
            return None
        return {key for key in map(app_name_key, listed) if key}

    def installer_dependents(self, product_code):
        """Products depending on this ProductCode per the Windows Installer /
        Burn dependency records; empty set if none, None if unreadable."""
        if winreg is None:
            return None
        flag = winreg.KEY_READ | self._view_flag("Registry64")
        code = product_code.upper()
        try:
            root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, INSTALLER_DEPENDENCIES_KEY, 0, flag)
        except FileNotFoundError:
            return set()
        except OSError:
            return None
        found = set()
        with root:
            try:
                providers = [winreg.EnumKey(root, i) for i in range(winreg.QueryInfoKey(root)[0])]
            except OSError:
                return None
            for provider in providers:
                try:
                    with winreg.OpenKey(root, provider, 0, flag) as key:
                        try:
                            value, _ = winreg.QueryValueEx(key, "")
                        except FileNotFoundError:
                            value = None
                except OSError:
                    return None
                owner = value.strip().upper() if isinstance(value, str) else ""
                if code not in (provider.upper(), owner):
                    continue
                try:
                    with winreg.OpenKey(root, provider + "\\Dependents", 0, flag) as dependents:
                        names = [winreg.EnumKey(dependents, i) for i in range(winreg.QueryInfoKey(dependents)[0])]
                except FileNotFoundError:
                    continue
                except OSError:
                    return None
                found |= {name for name in names if name.upper() not in (code, provider.upper())}
        return found

    def listed_display_names(self, required_sids):
        """Every DisplayName Windows lists, as written, or None - read the
        same way, and None for the same reasons, as display_names()."""
        if winreg is None:
            return None
        hives = self.loaded_user_hives()
        if hives is None or any(sid.casefold() not in hives for sid in required_sids):
            return None
        roots = [(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_SUBKEY)]
        roots += [(winreg.HKEY_USERS, hive + "\\" + UNINSTALL_SUBKEY) for hive in sorted(hives)]
        names = set()
        for root, path in roots:
            for view in SNAPSHOT_VIEWS:
                flag = winreg.KEY_READ | self._view_flag(view)
                try:
                    parent = winreg.OpenKey(root, path, 0, flag)
                except FileNotFoundError:
                    continue
                except OSError:
                    return None
                with parent:
                    try:
                        count = winreg.QueryInfoKey(parent)[0]
                        subkeys = [winreg.EnumKey(parent, i) for i in range(count)]
                    except OSError:
                        return None
                    for subkey in subkeys:
                        try:
                            with winreg.OpenKey(parent, subkey, 0, flag) as entry:
                                value, kind = winreg.QueryValueEx(entry, "DisplayName")
                        except FileNotFoundError:
                            continue
                        except OSError:
                            return None
                        if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and isinstance(value, str):
                            if value.strip():
                                names.add(value.strip())
        return names


def _local_tag(element):
    return element.tag.rsplit("}", 1)[-1]


def _parse_record(path):
    """The XML root of a small Chocolatey record, NO_SNAPSHOT if the file
    does not exist, or None if it cannot be read or parsed."""
    try:
        if os.stat(path).st_size > CHOCO_MAX_RECORD_BYTES:
            return None
        return ElementTree.parse(path).getroot()
    except FileNotFoundError:
        return NO_SNAPSHOT
    except (OSError, ElementTree.ParseError, ValueError):
        return None


def read_nuspec(path):
    """{"id", "version", "dependencies"} from a nuspec, or None. Matched by
    local name: the namespace varies."""
    return _nuspec_fields(_parse_record(path))


def read_packed_nuspec(nupkg_path, package_id):
    """The same from inside a .nupkg, or None; Chocolatey's own package has
    no loose .nuspec."""
    try:
        with zipfile.ZipFile(nupkg_path) as archive:
            wanted = (package_id + ".nuspec").casefold()
            members = [i for i in archive.infolist() if i.filename.casefold() == wanted]
            if len(members) != 1 or members[0].file_size > CHOCO_MAX_RECORD_BYTES:
                return None
            data = archive.read(members[0])
        return _nuspec_fields(ElementTree.fromstring(data))
    except (OSError, zipfile.BadZipFile, ElementTree.ParseError, ValueError, KeyError, RuntimeError):
        return None


def _nuspec_fields(root):
    if root is None or root is NO_SNAPSHOT or _local_tag(root) != "package":
        return None
    metadata = [el for el in root if _local_tag(el) == "metadata"]
    if len(metadata) != 1:
        return None
    fields, dependencies = {}, []
    for element in metadata[0]:
        name = _local_tag(element)
        if name in ("id", "version"):
            fields[name] = (element.text or "").strip()
        elif name == "dependencies":
            for dependency in element.iter():
                if _local_tag(dependency) == "dependency":
                    dependency_id = (dependency.get("id") or "").strip()
                    if not dependency_id:
                        return None
                    dependencies.append(dependency_id)
    if not fields.get("id") or not fields.get("version"):
        return None
    return {"id": fields["id"], "version": fields["version"], "dependencies": dependencies}


def read_snapshot(path):
    """(user SID or None, [keys]) from a .registry; NO_SNAPSHOT if missing,
    None if unreadable or any key is not Chocolatey 2.5.1's shape."""
    root = _parse_record(path)
    if root is None or root is NO_SNAPSHOT:
        return root
    if root.tag != "registrySnapshot":
        return None
    keys_element = root.find("keys")
    if keys_element is None:
        return None
    user = (root.findtext("user") or "").strip() or None
    keys = []
    for element in keys_element:
        if element.tag != "key":
            return None
        key_path = (element.findtext("KeyPath") or "").strip()
        view = (element.findtext("RegistryView") or "").strip()
        match = SNAPSHOT_KEY_PATH.match(key_path)
        if match is None or view not in SNAPSHOT_VIEWS:
            return None
        display = element.get("displayName")
        if display is None:
            display = element.findtext("DisplayName")
        version = element.get("displayVersion")
        if version is None:
            version = element.findtext("DisplayVersion")
        keys.append({
            "path": key_path,
            "hive": match.group(1),
            "name": match.group(2),
            "view": view,
            "display": display or "",
            "version": version or "",
            "location": element.findtext("InstallLocation") or "",
        })
    return user, keys


def snapshot_key_verdict(key, user, names, registry, location_signal=None):
    """(verdict, K, N, L) for one recorded key."""
    other_view = "Registry32" if key["view"] == "Registry64" else "Registry64"
    k = registry.uninstall_key(key["hive"], user, key["name"], key["view"])
    if k == SIGNAL_ABSENT and registry.uninstall_key(key["hive"], user, key["name"], other_view) != SIGNAL_ABSENT:
        k = SIGNAL_UNKNOWN

    wanted = app_name_key(key["display"])
    if wanted is None:
        n = SIGNAL_ABSTAIN
    elif names is None:
        n = SIGNAL_UNKNOWN
    else:
        n = SIGNAL_PRESENT if wanted in names else SIGNAL_ABSENT

    location = (location_signal or install_location_signal)(key["location"])

    if k == SIGNAL_PRESENT:
        verdict = KEY_PRESENT
    elif (k == SIGNAL_ABSENT
          and not {n, location} & {SIGNAL_PRESENT, SIGNAL_UNKNOWN}
          and SIGNAL_ABSENT in (n, location)):
        verdict = KEY_GONE
    else:
        verdict = KEY_UNKNOWN
    return verdict, k, n, location


def _other_version_snapshot(choco_root, package_id, version):
    """True if another version's folder has a keyed or unreadable snapshot.
    For refusing only: what choco recorded is not what the check sees."""
    base = os.path.join(choco_root, ".chocolatey")
    prefix = package_id.casefold() + "."
    try:
        folders = os.listdir(base)
    except OSError:
        return True
    for folder in folders:
        rest = folder.casefold()[len(prefix):] if folder.casefold().startswith(prefix) else ""
        if not rest[:1].isdigit() or rest == version.casefold():
            continue
        snapshot = read_snapshot(os.path.join(base, folder, ".registry"))
        if snapshot is None or (snapshot is not NO_SNAPSHOT and snapshot[1]):
            return True
    return False


class ProblemText(str):
    """A Chocolatey-records problem: its text, and .package it is about.
    A str, so existing log lines are unchanged."""

    def __new__(cls, text, package=None):
        problem = super().__new__(cls, text)
        problem.package = package
        return problem


def walk_choco_package(package, choco_root):
    """(owners, problems, seen) for a package and its dependencies. owners:
    (id, version, SID, keys) of keyed packages, dependencies first;
    problems: ProblemTexts; seen: ids visited, casefolded."""
    problems, owners, seen = [], [], set()

    def visit(package_id):
        if package_id.casefold() in seen:
            return
        seen.add(package_id.casefold())
        if len(seen) > CHOCO_MAX_PACKAGES:
            problems.append(ProblemText(f"more than {CHOCO_MAX_PACKAGES} packages to check"))
            return
        if not CHOCO_SAFE_TOKEN.match(package_id):
            problems.append(ProblemText(f"package id {package_id!r} is not a plain id", package_id))
            return
        spec = read_nuspec(os.path.join(choco_root, "lib", package_id, package_id + ".nuspec"))
        if spec is None:
            problems.append(ProblemText(f"no readable lib\\{package_id}\\{package_id}.nuspec", package_id))
            return
        if (spec["id"].casefold() != package_id.casefold()
                or not CHOCO_SAFE_TOKEN.match(spec["id"]) or not CHOCO_SAFE_TOKEN.match(spec["version"])):
            problems.append(ProblemText(f"lib\\{package_id} records id {spec['id']!r} version {spec['version']!r}",
                                        package_id))
            return
        for dependency in spec["dependencies"]:
            visit(dependency)
        folder = os.path.join(choco_root, ".chocolatey", f"{spec['id']}.{spec['version']}")
        if not os.path.isdir(folder):
            problems.append(ProblemText(f"no .chocolatey\\{spec['id']}.{spec['version']} folder", spec["id"]))
            return
        snapshot = read_snapshot(os.path.join(folder, ".registry"))
        if snapshot is None:
            problems.append(ProblemText(f"{spec['id']} {spec['version']}: .registry not readable or not understood",
                                        spec["id"]))
            return
        if snapshot is NO_SNAPSHOT or not snapshot[1]:
            if _other_version_snapshot(choco_root, spec["id"], spec["version"]):
                problems.append(ProblemText(
                    f"{spec['id']} {spec['version']}: no snapshot, but another version's folder has one", spec["id"]))
            return
        owners.append((spec["id"], spec["version"], snapshot[0], snapshot[1]))

    visit(package)
    return owners, problems, seen


def choco_package_state(package, choco_root, registry, location_signal=None):
    """(state, [(id, version)] to reinstall) for a package choco calls
    installed. Findings are logged; the dashboard sees only the outcome."""
    owners, problems, _ = walk_choco_package(package, choco_root)
    if not problems and not owners:
        problems.append("no package in it recorded any uninstall key (a portable or zip package?)")
    if problems:
        for problem in problems:
            logger.warning(f"Chocolatey check for {package}: can't verify - {problem}.")
        return CHOCO_UNVERIFIED, []

    required = sorted({user for _, _, user, _ in owners if user and USER_SID.match(user)})
    names = registry.display_names(required)
    removed, undecided = [], False
    for owner_id, version, user, keys in owners:
        verdicts = []
        for key in keys:
            verdict, k, n, location = snapshot_key_verdict(key, user, names, registry, location_signal)
            verdicts.append(verdict)
            logger.info(
                f"Chocolatey check for {package}: {owner_id} {version} key {key['path']} "
                f"({key['view']}): key {k}, name {n}, location {location} -> {verdict}"
            )
        if all(v == KEY_GONE for v in verdicts):
            removed.append((owner_id, version))
        elif not all(v == KEY_PRESENT for v in verdicts):
            undecided = True
    if undecided:
        logger.warning(f"Chocolatey check for {package}: can't verify - not every recorded key agrees.")
        return CHOCO_UNVERIFIED, []
    if removed:
        return CHOCO_REMOVED, removed
    return CHOCO_INSTALLED, []


# ---- What an install left behind: the install record ----
# What an install added to the Uninstall keys, so the server can
# recognise (and later remove) it. For an .exe the only way is a
# before/after diff of both HKLM views and this account's HKCU. Any doubt
# gives "unavailable" with a reason, so the server never offers an undo.
#
# A Burn bundle plus the MSIs it chained counts as one entry: removing the
# bundle removes them. The name must match the library's, so an app that
# self-updated meanwhile is not claimed. Pythons of the agent's
# major.minor and the launcher are protected by NAME: Python's installer
# records no InstallLocation, so the folder check alone would miss them.
# Disappeared entries are reported too: that is how an MSI major upgrade
# retires the replaced row. Reasons must match the server's
# INSTALL_RECORD_REASONS (t/test_install_record_agent.py).
RECORD_REASON_NO_NEW_ENTRY = "no_new_entry"
RECORD_REASON_SEVERAL_NEW_ENTRIES = "several_new_entries"
RECORD_REASON_NAME_MISMATCH = "name_mismatch"
RECORD_REASON_NO_SILENT_UNINSTALL = "no_silent_uninstall"
RECORD_REASON_USER_HIVE_UNREADABLE = "user_hive_unreadable"
RECORD_REASON_PROTECTED = "protected"
RECORD_REASON_PRODUCT_CODE_UNREADABLE = "product_code_unreadable"
RECORD_REASON_NO_REGISTRY_ENTRY = "no_registry_entry"
RECORD_REASON_CHOCO_SHARED_DEPENDENCY = "choco_shared_dependency"
RECORD_REASON_CHOCO_RECORDS_UNREADABLE = "choco_records_unreadable"
RECORD_REASONS = (
    RECORD_REASON_NO_NEW_ENTRY, RECORD_REASON_SEVERAL_NEW_ENTRIES, RECORD_REASON_NAME_MISMATCH,
    RECORD_REASON_NO_SILENT_UNINSTALL, RECORD_REASON_USER_HIVE_UNREADABLE, RECORD_REASON_PROTECTED,
    RECORD_REASON_PRODUCT_CODE_UNREADABLE, RECORD_REASON_NO_REGISTRY_ENTRY,
    RECORD_REASON_CHOCO_SHARED_DEPENDENCY, RECORD_REASON_CHOCO_RECORDS_UNREADABLE,
)
# The server ignores a record listing more than this many.
MAX_REPLACED_ENTRIES = 20
HKLM_NAME, HKCU_NAME = "HKEY_LOCAL_MACHINE", "HKEY_CURRENT_USER"
_GUID_KEY_NAME = re.compile(r"^\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}$")
# Casefolded keys: Windows matches value names without case.
_ENTRY_TEXT_VALUES = {
    "displayname": "display_name",
    "displayversion": "display_version",
    "uninstallstring": "uninstall_string",
    "quietuninstallstring": "quiet_uninstall_string",
    "installlocation": "install_location",
    "parentkeyname": "parent_key_name",
    "publisher": "publisher",
}


def registry_fold(name):
    """A registry name as Windows compares it: simple one-to-one uppercase.
    The server folds the same way (database.installation_record_key)."""
    return "".join(c.upper() if len(c.upper()) == 1 else c for c in name)


def _value_text(value):
    if isinstance(value, str):
        return value.strip() or None
    return None


def _value_flag(value):
    """A DWORD that is 1, whether Windows stored it as a number or as text."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value == 1
    return isinstance(value, str) and value.strip() == "1"


def uninstall_entry(hive, sid, view, key_name, values):
    """One Uninstall key as the record logic sees it. values maps each value
    name, casefolded, to its data."""
    entry = {"hive": hive, "sid": sid if hive == HKCU_NAME else None, "view": view, "key_name": key_name}
    for value_name, field in _ENTRY_TEXT_VALUES.items():
        entry[field] = _value_text(values.get(value_name))
    entry["system_component"] = _value_flag(values.get("systemcomponent"))
    entry["windows_installer"] = _value_flag(values.get("windowsinstaller"))
    # Burn writes these on every bundle it registers, and nothing else does.
    entry["burn_bundle"] = any(values.get(name) not in (None, "", [], b"")
                               for name in ("bundleupgradecode", "bundleproviderkey"))
    entry["inno"] = (key_name.casefold().endswith("_is1")
                     and _value_text(values.get("inno setup: app path")) is not None)
    return entry


def entry_identity(entry):
    """Which entry this is, compared as Windows compares it. A per-user entry
    has no view: HKCU's Uninstall key is the same through both."""
    if entry["hive"] == HKCU_NAME:
        return ("HKCU", (entry["sid"] or "").upper(), registry_fold(entry["key_name"]))
    return ("HKLM", entry["view"], registry_fold(entry["key_name"]))


def snapshot_diff(before, after):
    """(added, removed) between two snapshots; an in-place upgrade is
    neither. If the agent's hive was readable for only one, per-user
    entries are dropped from both, or all would look added or removed."""
    old, new = before["entries"], after["entries"]
    if before["user_readable"] != after["user_readable"]:
        old = {k: v for k, v in old.items() if k[0] != "HKCU"}
        new = {k: v for k, v in new.items() if k[0] != "HKCU"}
    added = [new[k] for k in sorted(new.keys() - old.keys())]
    removed = [old[k] for k in sorted(old.keys() - new.keys())]
    return added, removed


def is_visible_entry(entry):
    return bool(entry["display_name"]) and not entry["system_component"] and not entry["parent_key_name"]


def select_new_entry(added):
    """(entry, None) for the one entry this install added, else (None, why)."""
    visible = [e for e in added if is_visible_entry(e)]
    if len(visible) == 1:
        return visible[0], None
    if not visible:
        return None, RECORD_REASON_NO_NEW_ENTRY
    bundles = [e for e in visible if e["burn_bundle"]]
    if len(bundles) == 1 and all(e["windows_installer"] for e in visible if e is not bundles[0]):
        return bundles[0], None
    return None, RECORD_REASON_SEVERAL_NEW_ENTRIES


# Ignored on both sides as whole words: "vlc win64" must match "VLC media
# player", and "Fullscreen" keeps its "full".
_NAME_NOISE_WORDS = frozenset({
    "win64", "win32", "x64", "x86", "amd64", "arm64", "64bit", "32bit",
    "setup", "installer", "install", "offline", "full",
})
# Removed before splitting, or "64-bit" becomes a version plus "bit".
_NAME_NOISE_BITS = re.compile(r"(?<![a-z0-9])(?:32|64)[\s_-]?bit(?![a-z0-9])", re.IGNORECASE)


def _name_words(name):
    key = app_name_key(_NAME_NOISE_BITS.sub(" ", name or ""))
    return {word for word in (key or "").split() if word not in _NAME_NOISE_WORDS}


def name_matches(library_name, display_name):
    """Does a DisplayName name this library software? All of one name's
    words (app_name_key, minus noise) must be in the other's. A miss only
    makes the record unavailable, never wrong."""
    wanted, found = _name_words(library_name), _name_words(display_name)
    if not wanted or not found:
        return False
    return wanted <= found or found <= wanted


def _folder_key(path):
    text = (path or "").strip().strip('"').strip()
    if not text:
        return None
    return ntpath.normcase(ntpath.normpath(text)).rstrip("\\")


def _folder_within(inner, outer):
    return inner == outer or inner.startswith(outer + "\\")


# "Python 3.14.2 (64-bit)" and its parts ("Python 3.14.2 Core Interpreter
# (64-bit)") name their version; the launcher names none and serves them all.
_PYTHON_DISPLAY_NAME = re.compile(r"^\s*python\s+(\d+)\.(\d+)", re.IGNORECASE)
_PYTHON_LAUNCHER_NAME = re.compile(r"^\s*python\s+launcher\b", re.IGNORECASE)


def protected_folders():
    """This agent's own folder, C:\\Appease, and the Python it runs on."""
    folders = [AGENT_DIR, (os.environ.get("SystemDrive") or "C:") + "\\Appease"]
    for executable in (sys.executable, getattr(sys, "_base_executable", None)):
        if executable:
            folders.append(os.path.dirname(executable))
    return sorted({key for key in map(_folder_key, folders) if key})


def is_protected_entry(entry, folders, python_version=None):
    """True for the Python Launcher or a Python of the agent's major.minor
    (by name), or an entry whose folder holds or is inside a protected
    one. A blank folder says nothing."""
    name = entry["display_name"] or ""
    if _PYTHON_LAUNCHER_NAME.match(name):
        return True
    version = _PYTHON_DISPLAY_NAME.match(name)
    own = tuple(python_version or sys.version_info[:2])
    if version and (int(version.group(1)), int(version.group(2))) == own:
        return True
    location = _folder_key(entry["install_location"])
    if location is None:
        return False
    return any(_folder_within(folder, location) or _folder_within(location, folder) for folder in folders)


def has_silent_uninstall(entry):
    """Is there an uninstall known to run silently? A Windows Installer
    product (msiexec /x), an Inno Setup entry (/VERYSILENT), or the vendor's
    own QuietUninstallString. Burn always writes the last."""
    if entry["windows_installer"] and _GUID_KEY_NAME.match(entry["key_name"]):
        return True
    return entry["inno"] or bool(entry["quiet_uninstall_string"])


def entry_record_fields(entry):
    """The record fields that identify an entry and describe it."""
    fields = {
        "hive": entry["hive"],
        "view": entry["view"],
        "key_name": entry["key_name"],
        "display_name": entry["display_name"],
        "display_version": entry["display_version"],
        "install_location": entry["install_location"],
        "uninstall_string": entry["uninstall_string"],
        "quiet_uninstall_string": entry["quiet_uninstall_string"],
    }
    if entry["hive"] == HKCU_NAME:
        fields["sid"] = entry["sid"]
    # The server knows an MSI product by its ProductCode, however installed.
    if entry["windows_installer"] and _GUID_KEY_NAME.match(entry["key_name"]):
        fields["product_code"] = entry["key_name"]
    return fields


def replaced_entries(removed):
    """What the server needs to find rows for entries that disappeared.
    Visible entries first, then cut at what the server accepts."""
    ordered = sorted(removed, key=lambda e: (not is_visible_entry(e), entry_identity(e)))
    descriptors = []
    for entry in ordered[:MAX_REPLACED_ENTRIES]:
        if entry["windows_installer"] and _GUID_KEY_NAME.match(entry["key_name"]):
            descriptors.append({"product_code": entry["key_name"]})
            continue
        descriptor = {"hive": entry["hive"], "view": entry["view"], "key_name": entry["key_name"]}
        if entry["hive"] == HKCU_NAME:
            descriptor["sid"] = entry["sid"]
        descriptors.append(descriptor)
    return descriptors


def build_exe_record(before, after, library_name, folders):
    """The install record for an .exe job, from the snapshots around it.
    Always a record: unclear means unavailable with a reason, never None."""
    added, removed = snapshot_diff(before, after)
    record = {"method": "exe", "replaced_entries": replaced_entries(removed)}
    entry, reason = select_new_entry(added)
    if entry is None:
        # Nothing new may mean it went to an unreadable per-user hive.
        if reason == RECORD_REASON_NO_NEW_ENTRY and not (before["user_readable"] and after["user_readable"]):
            reason = RECORD_REASON_USER_HIVE_UNREADABLE
        record["unavailable_reason"] = reason
        return record
    # Protected, or maybe not this install's: name no entry, only why.
    if is_protected_entry(entry, folders):
        record["unavailable_reason"] = RECORD_REASON_PROTECTED
        return record
    if not name_matches(library_name, entry["display_name"]):
        record["unavailable_reason"] = RECORD_REASON_NAME_MISMATCH
        return record
    record.update(entry_record_fields(entry))
    if not has_silent_uninstall(entry):
        record["unavailable_reason"] = RECORD_REASON_NO_SILENT_UNINSTALL
    return record


def parse_whoami_sid(text):
    """The SID from `whoami /user /fo csv /nh` output, or None."""
    match = re.search(r'"(S-1(?:-\d+)+)"', text or "")
    return match.group(1).upper() if match else None


_agent_sid_cache = []


def agent_user_sid():
    """The SID of the account this agent runs as, or None. Asked once."""
    if _agent_sid_cache:
        return _agent_sid_cache[0]
    try:
        completed = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        sid = parse_whoami_sid(completed.stdout) if completed.returncode == 0 else None
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(f"Could not ask Windows which account the agent runs as ({error_text(exc)}).")
        return None
    if sid:
        _agent_sid_cache.append(sid)
    return sid


def _read_uninstall_root(root, path, view, hive, sid, entries):
    """Add every entry under one Uninstall key to entries. False if the key
    exists but cannot be read; a key that does not exist is simply empty."""
    flag = winreg.KEY_READ | WindowsRegistry()._view_flag(view)
    try:
        parent = winreg.OpenKey(root, path, 0, flag)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    with parent:
        try:
            names = [winreg.EnumKey(parent, i) for i in range(winreg.QueryInfoKey(parent)[0])]
        except OSError:
            return False
        for name in names:
            try:
                with winreg.OpenKey(parent, name, 0, flag) as key:
                    count = winreg.QueryInfoKey(key)[1]
                    values = {}
                    for i in range(count):
                        value_name, value, _ = winreg.EnumValue(key, i)
                        values[value_name.casefold()] = value
            except OSError:
                # Skipped: unreadable before AND after, so never in a diff.
                continue
            entry = uninstall_entry(hive, sid, view, name, values)
            entries[entry_identity(entry)] = entry
    return True


def read_uninstall_snapshot(sid):
    """Every Uninstall entry for the machine and sid; None if machine-wide
    keys are unreadable. Per-user ones only if that hive is loaded."""
    if winreg is None:
        return None
    entries = {}
    for view in SNAPSHOT_VIEWS:
        if not _read_uninstall_root(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_SUBKEY, view, HKLM_NAME, None, entries):
            return None
    user_readable = False
    if sid and USER_SID.match(sid):
        hives = WindowsRegistry().loaded_user_hives()
        if hives is not None and sid.casefold() in hives:
            user_readable = _read_uninstall_root(
                winreg.HKEY_USERS, sid + "\\" + UNINSTALL_SUBKEY, "Registry64", HKCU_NAME, sid, entries)
    return {"entries": entries, "user_readable": user_readable}


# ---- The ProductCode of a downloaded .msi ----
# Read via Windows' WindowsInstaller.Installer COM object in PowerShell,
# read-only. The path goes in an ENVIRONMENT VARIABLE, never the script,
# so no file name can become command text; -EncodedCommand because
# -Command re-parses double quotes. Read before the download is deleted;
# failure only makes the record "product_code_unreadable".
MSI_PRODUCT_CODE_TIMEOUT_SECONDS = 20
MSI_PATH_VARIABLE = "APPEASE_MSI_PATH"
_MSI_PRODUCT_CODE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$installer = New-Object -ComObject WindowsInstaller.Installer
$database = $installer.GetType().InvokeMember('OpenDatabase', 'InvokeMethod', $null, $installer, @($env:APPEASE_MSI_PATH, 0))
$query = 'SELECT `Value` FROM `Property` WHERE `Property` = ' + "'ProductCode'"
$view = $database.GetType().InvokeMember('OpenView', 'InvokeMethod', $null, $database, @($query))
$view.GetType().InvokeMember('Execute', 'InvokeMethod', $null, $view, $null) | Out-Null
$record = $view.GetType().InvokeMember('Fetch', 'InvokeMethod', $null, $view, $null)
if ($record -eq $null) { exit 3 }
$value = $record.GetType().InvokeMember('StringData', 'GetProperty', $null, $record, 1)
$view.GetType().InvokeMember('Close', 'InvokeMethod', $null, $view, $null) | Out-Null
[Console]::Out.Write($value)
"""
# The server's own rule (database.normalise_product_code): a GUID, braces
# both or neither, any case.
_PRODUCT_CODE = re.compile(
    r"^(\{)?([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})(?(1)\})$"
)


def product_code_or_none(text):
    """text as a ProductCode by the server's rules, or None."""
    value = (text or "").strip().lstrip("\ufeff").strip()
    return value if _PRODUCT_CODE.match(value) else None


def powershell_error_text(stderr):
    """PowerShell's stderr as one readable line. Redirected, it is CLIXML:
    <S S="Error"> elements with _x000D__x000A_ line breaks."""
    text = stderr or ""
    parts = re.findall(r'<S S="Error">(.*?)</S>', text)
    if parts:
        text = " ".join(parts)
    text = re.sub(r"_x000[DA]_", " ", text)
    return " ".join(text.split())[:200]


def read_msi_product_code(path, run=None):
    """The ProductCode inside the .msi at path, or None. Never raises."""
    if not path:
        return None
    run = run or subprocess.run
    encoded = base64.b64encode(_MSI_PRODUCT_CODE_SCRIPT.encode("utf-16-le")).decode("ascii")
    environment = dict(os.environ)
    environment[MSI_PATH_VARIABLE] = path
    try:
        completed = run(
            ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True, text=True, timeout=MSI_PRODUCT_CODE_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL, env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        logger.warning(f"Reading the ProductCode timed out after {MSI_PRODUCT_CODE_TIMEOUT_SECONDS}s; "
                       f"no install record names this product.")
        return None
    except Exception as exc:  # noqa: BLE001 - never a reason for anything else to fail
        logger.warning(f"Could not read the ProductCode ({error_text(exc)}); no install record names this product.")
        return None
    if completed.returncode != 0:
        logger.warning(f"Reading the ProductCode failed (exit {completed.returncode}: "
                       f"{powershell_error_text(completed.stderr) or 'no detail'}).")
        return None
    code = product_code_or_none(completed.stdout)
    if code is None:
        logger.warning(f"The .msi did not give a ProductCode (got {(completed.stdout or '')[:60]!r}).")
    return code


def build_msi_record(product_code, before, after, folders):
    """The install record for an msiexec job. No name check (the code comes
    from the installed file), but the product must be NEW."""
    added, removed = snapshot_diff(before, after)
    record = {"method": "msi", "replaced_entries": replaced_entries(removed)}
    if product_code is None:
        record["unavailable_reason"] = RECORD_REASON_PRODUCT_CODE_UNREADABLE
        return record
    braced = product_code if product_code.startswith("{") else "{" + product_code + "}"
    wanted = registry_fold(braced)

    def named(entries):
        return [e for e in entries if registry_fold(e["key_name"]) == wanted]

    if named(before["entries"].values()):
        record["unavailable_reason"] = RECORD_REASON_NO_NEW_ENTRY
        return record
    found = named(added)
    if not found:
        readable = before["user_readable"] and after["user_readable"]
        record["unavailable_reason"] = (RECORD_REASON_NO_NEW_ENTRY if readable
                                        else RECORD_REASON_USER_HIVE_UNREADABLE)
        return record
    entry = sorted(found, key=lambda e: (not is_visible_entry(e), entry_identity(e)))[0]
    if is_protected_entry(entry, folders):
        record["unavailable_reason"] = RECORD_REASON_PROTECTED
        return record
    record.update(entry_record_fields(entry))
    # The .msi's own code is authoritative, flag or not.
    record["product_code"] = product_code
    return record


# ---- What a Chocolatey install put on the PC ----
# Choco already diffed the keys per package (.registry), so the record
# names the package that OWNS the entry: "vlc" wraps "vlc.install", which
# does. A dependency owner also needed outside the tree is never named
# (choco_shared_dependency): removing it would break that package. A
# record naming none leaves a "none:" row for a real one to replace.
# "Already installed" is no AppEase install and gets no record.
PROTECTED_CHOCO_PACKAGE = re.compile(r"^(?:chocolatey|python.*)$", re.IGNORECASE)
CHOCO_ALREADY_INSTALLED_REASONS = (REASON_CHOCO_ALREADY_INSTALLED, REASON_CHOCO_CONFIRMED_INSTALLED)


def choco_dependents(choco_root, package_id):
    """(ids, None): installed packages listing package_id as a dependency;
    (None, blocker) if one is unreadable, as it might be a dependent."""
    lib = os.path.join(choco_root, "lib")
    try:
        names = sorted(os.listdir(lib))
    except OSError:
        return None, "the lib folder itself"
    dependents = set()
    for name in names:
        if name.startswith(".") or not os.path.isdir(os.path.join(lib, name)):
            continue
        spec = None
        if CHOCO_SAFE_TOKEN.match(name):
            nuspec_path = os.path.join(lib, name, name + ".nuspec")
            if os.path.exists(nuspec_path):
                spec = read_nuspec(nuspec_path)
            else:
                spec = read_packed_nuspec(os.path.join(lib, name, name + ".nupkg"), name)
        if spec is None:
            return None, name
        if any(d.casefold() == package_id.casefold() for d in spec["dependencies"]):
            dependents.add(spec["id"].casefold())
    return dependents, None


def _choco_unavailable(record, reason):
    record["unavailable_reason"] = reason
    return record


def build_choco_record(package, result_reason, before, after, choco_root, folders):
    """The record for a `choco install` job, or None for "already
    installed". Never raises; on error the record names no package."""
    if result_reason in CHOCO_ALREADY_INSTALLED_REASONS:
        return None
    record = {"method": "choco", "replaced_entries": []}
    try:
        if before is not None and after is not None:
            record["replaced_entries"] = replaced_entries(snapshot_diff(before, after)[1])
        if not package or not CHOCO_SAFE_TOKEN.match(package):
            return _choco_unavailable(record, RECORD_REASON_CHOCO_RECORDS_UNREADABLE)
        if PROTECTED_CHOCO_PACKAGE.match(package):
            record["package_id"] = package
            return _choco_unavailable(record, RECORD_REASON_PROTECTED)

        owners, problems, seen = walk_choco_package(package, choco_root)
        if problems:
            for problem in problems:
                blocker = getattr(problem, "package", None)
                by = f"package {blocker} could not be read" if blocker else "Chocolatey's records could not be read"
                logger.warning(f"Install record for {package}: {by}, which blocked the record - {problem}.")
            return _choco_unavailable(record, RECORD_REASON_CHOCO_RECORDS_UNREADABLE)
        if not owners:
            return _choco_unavailable(record, RECORD_REASON_NO_REGISTRY_ENTRY)
        direct = [o for o in owners if o[0].casefold() == package.casefold()]
        if direct:
            owner = direct[0]
        elif len(owners) > 1:
            return _choco_unavailable(record, RECORD_REASON_SEVERAL_NEW_ENTRIES)
        else:
            owner = owners[0]
            dependents, blocker = choco_dependents(choco_root, owner[0])
            if dependents is None:
                logger.warning(f"Install record for {package}: installed package {blocker} could not be read, "
                               f"which blocked the record - it might also need {owner[0]}.")
                return _choco_unavailable(record, RECORD_REASON_CHOCO_RECORDS_UNREADABLE)
            if dependents - seen:
                logger.info(f"Install record for {package}: {owner[0]} is also needed by "
                            f"{', '.join(sorted(dependents - seen))}, so it is not named.")
                return _choco_unavailable(record, RECORD_REASON_CHOCO_SHARED_DEPENDENCY)

        owner_id, _, user, keys = owner
        record["package_id"] = owner_id
        if PROTECTED_CHOCO_PACKAGE.match(owner_id):
            return _choco_unavailable(record, RECORD_REASON_PROTECTED)
        if any(is_protected_entry({"display_name": k["display"] or None, "install_location": k["location"] or None},
                                  folders) for k in keys):
            return _choco_unavailable(record, RECORD_REASON_PROTECTED)

        # The first named key that is not a bundled shared runtime (WebView2
        # beside paint.net). Live values win: a self-updated app moved on.
        named = [k for k in keys if k["display"]]
        key = next((k for k in named if not choco_record_key_is_runtime(k, user, after)),
                   named[0] if named else keys[0])
        sid = user if key["hive"] == HKCU_NAME else None
        live = (after or {}).get("entries", {}).get(entry_identity(
            {"hive": key["hive"], "sid": sid, "view": key["view"], "key_name": key["name"]}))
        record.update({
            "hive": key["hive"],
            "view": key["view"],
            "key_name": key["name"],
            "display_name": (live or {}).get("display_name") or key["display"] or None,
            "display_version": (live or {}).get("display_version") or key["version"] or None,
            "install_location": key["location"] or None,
        })
        if sid:
            record["sid"] = sid
        return record
    except Exception:  # noqa: BLE001 - a record is never worth an exception
        logger.exception(f"Install record for {package}: reading Chocolatey's records failed.")
        record.pop("package_id", None)
        for field in ("hive", "view", "key_name", "display_name", "display_version", "install_location", "sid"):
            record.pop(field, None)
        return _choco_unavailable(record, RECORD_REASON_CHOCO_RECORDS_UNREADABLE)


def choco_record_key_is_runtime(key, user, after):
    """Is this recorded key a Microsoft runtime? By its live entry if any,
    else its recorded name (.registry has no publisher). For naming only;
    protection still checks every key."""
    sid = user if key["hive"] == HKCU_NAME else None
    live = (after or {}).get("entries", {}).get(entry_identity(
        {"hive": key["hive"], "sid": sid, "view": key["view"], "key_name": key["name"]}))
    if live is not None:
        return shared_runtime_by_name([live]) is not None
    return SHARED_RUNTIME_NAME.search(key["display"] or "") is not None


def send_install_heartbeat():
    try:
        post_json("/api/agent/heartbeat", identity(ip_address=get_local_ip()))
    except SERVER_ERRORS as exc:
        logger.warning(f"Heartbeat to {describe_server_error(exc)} failed; install continuing.")


# ---- Ending a timed-out install, and everything it started ----
# process.kill() ends only cmd.exe; the installer keeps running and can
# finish after the job is reported failed. So each install runs in its
# own Job Object, which children join, and ending the job ends the tree.
#
# A child started before its parent joins escapes, so cmd.exe starts
# SUSPENDED, is assigned, then resumed via a Toolhelp32 thread snapshot
# (Popen keeps no thread handle; NtResumeProcess is undocumented). A job
# failure never fails the install: it falls back, logged, to no job.
#
# BREAKAWAY_OK, or Windows refuses children that ask to leave and some
# installers fail. No kill-on-close, so what an installer launches
# survives a normal finish or an agent stop. Work inside a service (an
# MSI's real work) is out of reach.
CREATE_SUSPENDED = 0x00000004
JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
_JOB_BASIC_ACCOUNTING_INFORMATION = 1
# Not 2 (basic limits): Windows takes BREAKAWAY_OK only through the extended
# structure, and refuses it through the basic one with error 87.
_JOB_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100
_THREAD_SUSPEND_RESUME = 0x0002
_TH32CS_SNAPTHREAD = 0x00000004
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_RESUME_FAILED = 0xFFFFFFFF


class _JobBasicLimit(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _JobExtendedLimit(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobBasicLimit),
        ("IoInfo", ctypes.c_uint64 * 6),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _JobBasicAccounting(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", ctypes.c_uint32),
        ("TotalProcesses", ctypes.c_uint32),
        ("ActiveProcesses", ctypes.c_uint32),
        ("TotalTerminatedProcesses", ctypes.c_uint32),
    ]


class _ThreadEntry32(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("cntUsage", ctypes.c_uint32),
        ("th32ThreadID", ctypes.c_uint32),
        ("th32OwnerProcessID", ctypes.c_uint32),
        ("tpBasePri", ctypes.c_int32),
        ("tpDeltaPri", ctypes.c_int32),
        ("dwFlags", ctypes.c_uint32),
    ]


_kernel32_cache = []


def _kernel32():
    """kernel32 with every prototype declared, so a 64-bit HANDLE is not
    cut to an int. OSError off Windows."""
    if _kernel32_cache:
        return _kernel32_cache[0]
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        raise OSError("Job Objects need Windows")
    k = win_dll("kernel32", use_last_error=True)
    handle, dword, boolean = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int
    signatures = {
        "CreateJobObjectW": ([ctypes.c_void_p, ctypes.c_wchar_p], handle),
        "SetInformationJobObject": ([handle, ctypes.c_int, ctypes.c_void_p, dword], boolean),
        "QueryInformationJobObject": ([handle, ctypes.c_int, ctypes.c_void_p, dword, ctypes.c_void_p], boolean),
        "AssignProcessToJobObject": ([handle, handle], boolean),
        "TerminateJobObject": ([handle, ctypes.c_uint32], boolean),
        "OpenProcess": ([dword, boolean, dword], handle),
        "OpenThread": ([dword, boolean, dword], handle),
        "ResumeThread": ([handle], dword),
        "CreateToolhelp32Snapshot": ([dword, dword], handle),
        "Thread32First": ([handle, ctypes.POINTER(_ThreadEntry32)], boolean),
        "Thread32Next": ([handle, ctypes.POINTER(_ThreadEntry32)], boolean),
        "CloseHandle": ([handle], boolean),
    }
    for name, (argtypes, restype) in signatures.items():
        function = getattr(k, name)
        function.argtypes = argtypes
        function.restype = restype
    _kernel32_cache.append(k)
    return k


def _win_error(call):
    code = ctypes.get_last_error()
    return OSError(f"{call} failed (Windows error {code})")


class InstallJob:
    """One Windows Job Object for one install. Every method raises OSError
    when Windows refuses, except close(), which never raises."""

    def __init__(self):
        k = _kernel32()
        handle = k.CreateJobObjectW(None, None)
        if not handle:
            raise _win_error("CreateJobObjectW")
        limits = _JobExtendedLimit()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_BREAKAWAY_OK
        if not k.SetInformationJobObject(handle, _JOB_EXTENDED_LIMIT_INFORMATION,
                                         ctypes.byref(limits), ctypes.sizeof(limits)):
            error = _win_error("SetInformationJobObject")
            k.CloseHandle(handle)
            raise error
        self._k = k
        self._handle = handle

    def assign(self, pid):
        process = self._k.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        if not process:
            raise _win_error("OpenProcess")
        try:
            if not self._k.AssignProcessToJobObject(self._handle, process):
                raise _win_error("AssignProcessToJobObject")
        finally:
            self._k.CloseHandle(process)

    def terminate(self):
        if not self._k.TerminateJobObject(self._handle, 1):
            raise _win_error("TerminateJobObject")

    def active_processes(self):
        info = _JobBasicAccounting()
        if not self._k.QueryInformationJobObject(self._handle, _JOB_BASIC_ACCOUNTING_INFORMATION,
                                                 ctypes.byref(info), ctypes.sizeof(info), None):
            raise _win_error("QueryInformationJobObject")
        return info.ActiveProcesses

    def close(self):
        handle, self._handle = self._handle, None
        if handle:
            self._k.CloseHandle(handle)


def resume_suspended_process(pid):
    """Resume a process created with CREATE_SUSPENDED. Raises OSError unless
    every one of its threads was found and is now running."""
    k = _kernel32()
    snapshot = k.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
    if not snapshot or snapshot == _INVALID_HANDLE_VALUE:
        raise _win_error("CreateToolhelp32Snapshot")
    resumed = 0
    try:
        entry = _ThreadEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        more = k.Thread32First(snapshot, ctypes.byref(entry))
        while more:
            if entry.th32OwnerProcessID == pid:
                thread = k.OpenThread(_THREAD_SUSPEND_RESUME, False, entry.th32ThreadID)
                if not thread:
                    raise _win_error("OpenThread")
                try:
                    previous = k.ResumeThread(thread)
                finally:
                    k.CloseHandle(thread)
                if previous == _RESUME_FAILED:
                    raise _win_error("ResumeThread")
                # A suspend count above one means it is STILL suspended.
                if previous > 1:
                    raise OSError(f"thread {entry.th32ThreadID} was suspended {previous} times")
                resumed += 1
            entry.dwSize = ctypes.sizeof(entry)
            more = k.Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        k.CloseHandle(snapshot)
    if not resumed:
        raise OSError(f"no thread of process {pid} was found to resume")


def start_install_process(command_to_run, stdout, job_factory=None, resume=None, popen=None, shell=True):
    """Start an install command, in its own job when Windows allows:
    (process, job or None). shell=False runs an argv list. Raises OSError
    only if it cannot start; the keyword hooks are for tests."""
    job_factory = job_factory or InstallJob
    resume = resume or resume_suspended_process
    popen = popen or subprocess.Popen

    def start(creationflags=0):
        return popen(
            command_to_run,
            shell=shell,
            stdout=stdout,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            creationflags=creationflags,
        )

    without = "it runs without one, and a timeout ends only cmd.exe, not the installer"
    try:
        job = job_factory()
    except Exception as exc:  # noqa: BLE001 - never a reason for the install to fail
        logger.warning(f"Could not create a job for this install ({error_text(exc)}); {without}.")
        return start(), None

    try:
        process = start(CREATE_SUSPENDED)
    except OSError as exc:
        job.close()
        logger.warning(f"Could not start this install suspended ({error_text(exc)}); {without}.")
        return start(), None

    assigned = True
    try:
        job.assign(process.pid)
    except Exception as exc:  # noqa: BLE001
        assigned = False
        logger.warning(f"Could not put this install into its job ({error_text(exc)}); {without}.")

    try:
        resume(process.pid)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"Could not resume this install after starting it suspended ({error_text(exc)}); "
            f"ending it before it ran and starting it again without a job."
        )
        # End the job if it holds cmd.exe, and cmd.exe itself with a bounded
        # wait, or a suspended process is waited on forever.
        try:
            if assigned:
                job.terminate()
        except Exception:  # noqa: BLE001
            pass
        try:
            process.kill()
            process.wait(timeout=30)
        except Exception:  # noqa: BLE001
            logger.exception("Could not end the suspended install; it never ran.")
        job.close()
        return start(), None

    if not assigned:
        job.close()
        return process, None
    return process, job


def stop_install_process(process, job):
    """End a timed-out install. True if its whole job was ended, False if
    only cmd.exe was (no job, or ending it failed)."""
    if job is not None:
        try:
            job.terminate()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Could not end this install's job ({error_text(exc)}); ending cmd.exe only.")
    process.kill()
    return False


def run_and_classify(command, command_to_run, deadline, on_success=None):
    """Run one command to the end or the deadline and classify it. Producer
    and package id come from command; command_to_run is what runs.
    on_success gets a live-process counter while the job is open."""
    output_log_path = None
    install_job = None
    try:
        # A temp file, not a PIPE, which can fill and deadlock a chatty
        # installer. DEVNULL stdin makes a stray prompt fail fast.
        output_file_handle = tempfile.NamedTemporaryFile(
            mode="w+", suffix=".log", delete=False, encoding="utf-8", errors="replace"
        )
        output_log_path = output_file_handle.name
        process, install_job = start_install_process(command_to_run, output_file_handle)

        start_time = time.time()
        last_heartbeat = start_time
        last_progress = start_time
        timed_out = False
        while process.poll() is None:
            time.sleep(1)
            now = time.time()
            if now - last_heartbeat >= INSTALL_HEARTBEAT_SECONDS:
                send_install_heartbeat()
                last_heartbeat = now
            if now - last_progress >= INSTALL_HEARTBEAT_SECONDS:
                logger.info(f"Install still running... ({int(now - start_time)}s elapsed)")
                last_progress = now
            if now > deadline:
                whole_tree = stop_install_process(process, install_job)
                timed_out = True
                ended = ("the install and everything it started" if whole_tree
                         else "cmd.exe only - the installer may still be running")
                logger.error(
                    f"Install timed out: past this job's {INSTALL_TIMEOUT_SECONDS}s limit, "
                    f"ended {ended}: {command_to_run}"
                )
                break

        process.wait()
        return_code = process.returncode

        # Logged only: shows a bootstrapper that exited early.
        if install_job is not None and not timed_out:
            try:
                still_running = install_job.active_processes()
            except OSError:
                still_running = 0
            if still_running:
                logger.info(
                    f"cmd.exe has exited, but {still_running} process(es) the install "
                    f"started are still running."
                )

        try:
            output_file_handle.close()
        except OSError:
            pass
        stdout_output = ""
        try:
            with open(output_log_path, "r", encoding="utf-8", errors="replace") as f:
                stdout_output = f.read()
        except OSError:
            pass
        try:
            os.remove(output_log_path)
        except OSError:
            pass

        if stdout_output.strip():
            logger.info(f"Install output:\n{stdout_output.strip()}")

        # command, not command_to_run: before choco's path and flags.
        result = classify_install_result(command, return_code, stdout_output, timed_out)
        if result.ok:
            logger.info(
                f"Install finished (exit code {return_code}, "
                f"reason {result.reason or 'none'})."
            )
        elif not timed_out:
            logger.error(
                f"Install failed (exit code {return_code}, "
                f"reason {result.reason or 'none'}): {command_to_run}"
            )

        if on_success is not None and result.ok:
            def still_running():
                if install_job is None:
                    return None
                try:
                    return install_job.active_processes()
                except OSError:
                    return None
            try:
                on_success(still_running)
            except Exception:  # noqa: BLE001 - recording never changes an install's result
                logger.exception("Recording after the install failed; the install result stands.")

    except OSError as exc:
        logger.error(f"Failed to launch install command: {exc}")
        # The output file may already exist when the launch itself failed.
        if output_log_path:
            try:
                output_file_handle.close()
                os.remove(output_log_path)
            except OSError:
                pass
        result = InstallResult(False, None, REASON_LAUNCH_FAILED)
    finally:
        if install_job is not None:
            install_job.close()

    return result


def choco_command_line(command):
    """`choco ...` with choco's full path, fully non-interactive: package
    scripts can still prompt despite -y."""
    line = command.replace("choco ", f'"{CHOCO_EXE}" ', 1)
    if " --no-progress" not in line:
        line += " --no-progress"
    if " -y" not in line and " --yes" not in line:
        line += " --yes"
    return line


def confirm_choco_already_installed(command, result, deadline, registry=None):
    """The result to report once choco says "already installed". A restart
    code from a forced reinstall wins: someone has to act on it."""
    package = choco_package_id(command)
    registry = registry if registry is not None else WindowsRegistry()
    choco_root = os.path.dirname(os.path.dirname(CHOCO_EXE))
    try:
        state, removed = choco_package_state(package, choco_root, registry)
    except Exception:  # noqa: BLE001 - fail safe: an unexpected error is "can't verify"
        logger.exception(f"Chocolatey check for {package}: can't verify - the check itself failed.")
        return result

    if state == CHOCO_INSTALLED:
        logger.info(f"Chocolatey check for {package}: Windows still lists it; nothing to do.")
        return InstallResult(True, result.exit_code, REASON_CHOCO_CONFIRMED_INSTALLED)
    if state != CHOCO_REMOVED:
        return result

    restart = None
    for owner, version in removed:
        forced = f"choco install {owner} --version {version} --force -y"
        logger.warning(
            f"Chocolatey lists {owner} {version} as installed but Windows does not; reinstalling: {forced}"
        )
        forced_result = run_and_classify(forced, choco_command_line(forced), deadline)
        if not forced_result.ok:
            return forced_result
        if forced_result.reason == REASON_RESTART_STARTED or (
                forced_result.reason == REASON_RESTART_NEEDED and restart is None):
            restart = forced_result
    if restart is not None:
        return restart

    try:
        state, _ = choco_package_state(package, choco_root, registry)
    except Exception:  # noqa: BLE001
        logger.exception(f"Chocolatey check for {package}: the check after reinstalling failed.")
        state = CHOCO_UNVERIFIED
    if state == CHOCO_INSTALLED:
        logger.info(f"Chocolatey check for {package}: reinstalled, and Windows lists it again.")
        return InstallResult(True, 0, REASON_CHOCO_REINSTALLED)
    logger.warning(f"Chocolatey check for {package}: reinstalled, but Windows does not confirm it.")
    return InstallResult(True, 0, REASON_CHOCO_REINSTALL_UNCONFIRMED)


# ---- Recording an install, around the real install ----
# InstallRecorder snapshots the Uninstall keys before the install and,
# for a COMPLETED one only, builds the record. It never costs the
# install: errors only drop the record, and it spends only time.
#
# EXE settling: a bootstrapper can exit before its work finishes, so the
# agent rechecks every SETTLE_CHECK_SECONDS until two checks agree on ONE
# new visible entry (name, version and UninstallString) and the job is
# empty, or SETTLE_MAX_SECONDS pass, with heartbeats between. msiexec and
# choco wait for their own work. An .msi whose ProductCode can't be read
# may use the diff's one new MSI product, if its name matches.
SETTLE_MAX_SECONDS = 60
SETTLE_CHECK_SECONDS = 10


def settle_signature(before, snapshot):
    """What two settling checks compare: every new visible entry, by its
    identity and the three values an install still writing would change."""
    added, _ = snapshot_diff(before, snapshot)
    return tuple(sorted(
        (entry_identity(e), e["display_name"] or "", e["display_version"] or "", e["uninstall_string"] or "")
        for e in added if is_visible_entry(e)))


def settled_early(previous, current, still_running):
    """Two checks in a row agree on exactly one new visible entry, and none
    of the install's processes is still running."""
    return previous is not None and current == previous and len(current) == 1 and not still_running


def settle_exe(before, sid, running, read=None, clock=None, sleep=None, heartbeat=None):
    """Watch the Uninstall keys after an .exe install. Returns (last
    snapshot or None if unreadable, seconds waited, stopped early)."""
    read = read or read_uninstall_snapshot
    clock = clock or time.time
    sleep = sleep or time.sleep
    heartbeat = heartbeat or send_install_heartbeat
    start = clock()
    previous = None
    while True:
        snapshot = read(sid)
        elapsed = clock() - start
        if snapshot is None:
            return None, elapsed, False
        current = settle_signature(before, snapshot)
        if settled_early(previous, current, running()):
            return snapshot, elapsed, True
        if elapsed >= SETTLE_MAX_SECONDS:
            return snapshot, elapsed, False
        previous = current
        sleep(min(SETTLE_CHECK_SECONDS, SETTLE_MAX_SECONDS - elapsed))
        heartbeat()


def product_code_from_diff(before, after, library_name):
    """The ProductCode of the one new Windows Installer product, if there is
    exactly one - visible or hidden - and its name matches the library's."""
    added, _ = snapshot_diff(before, after)
    products = [e for e in added if e["windows_installer"] and _GUID_KEY_NAME.match(e["key_name"])]
    if len(products) != 1 or not name_matches(library_name, products[0]["display_name"]):
        return None
    return products[0]["key_name"]


def install_method_of(command):
    """choco, msi or exe - the kind of install a job's command performs."""
    return {"choco": "choco", "msiexec": "msi"}.get(install_producer(command), "exe")


class InstallRecorder:
    """Carries one job's recording: the library name in, the record out.
    None of its methods ever raises."""

    def __init__(self, library_name):
        self.library_name = library_name or ""
        self.method = None
        self.sid = None
        self.before = None
        self.after = None
        self.settle_seconds = None
        self.stopped_early = None
        self.record = None
        self.note = None
        self.summarised = False

    def start(self, method):
        """The "before" snapshot, right before the install starts."""
        self.method = method
        try:
            self.sid = agent_user_sid()
            self.before = read_uninstall_snapshot(self.sid)
            if self.before is None:
                self.note = "the Uninstall keys could not be read before the install"
                logger.warning(f"Install record: {self.note}; this job is reported without one.")
        except Exception:  # noqa: BLE001
            logger.exception("Install record: the snapshot before the install failed; the install runs as usual.")
            self.before = None
            self.note = "the snapshot before the install failed"

    def settle(self, running):
        """Called by run_and_classify() before the job closes - for an .exe
        only, which run_install_command() decides."""
        if self.before is None:
            return
        try:
            self.after, self.settle_seconds, self.stopped_early = settle_exe(self.before, self.sid, running)
            if self.after is None:
                self.note = "the Uninstall keys could not be read while settling"
        except Exception:  # noqa: BLE001
            logger.exception("Install record: settling failed; the job is reported without a record.")
            self.after = None
            self.note = "settling failed"

    def finish(self, result, package=None, msi_path=None):
        """Build the record for a completed install, then log the summary."""
        try:
            if not result.ok:
                self.record = None
                self.note = f"the install did not complete ({result.reason or 'failed'})"
            elif self.before is not None:
                self._build(result, package, msi_path)
        except Exception:  # noqa: BLE001
            logger.exception("Install record: building it failed; the job is reported without one.")
            self.record = None
            self.note = "building it failed"
        self.log_summary()

    def _build(self, result, package, msi_path):
        folders = protected_folders()
        if self.method == "exe":
            if self.after is not None:
                self.record = build_exe_record(self.before, self.after, self.library_name, folders)
            return
        after = read_uninstall_snapshot(self.sid)
        if after is None:
            self.note = "the Uninstall keys could not be read after the install"
            return
        if self.method == "msi":
            code = read_msi_product_code(msi_path)
            if code is None:
                code = product_code_from_diff(self.before, after, self.library_name)
                if code:
                    logger.info(f"Install record: the .msi gave no ProductCode; the registry shows {code}, used instead.")
            self.record = build_msi_record(code, self.before, after, folders)
        else:
            choco_root = os.path.dirname(os.path.dirname(CHOCO_EXE))
            self.record = build_choco_record(package, result.reason, self.before, after, choco_root, folders)
            if self.record is None:
                self.note = "Chocolatey reported it already installed"

    def summary(self):
        """One line: method, record (or why none), settling, replaced."""
        method = self.method or "unknown"
        if self.settle_seconds is not None:
            how = "stopped early" if self.stopped_early else "full wait"
            settled = f"settled in {self.settle_seconds:.0f}s ({how})"
        else:
            settled = "no settling"
        record = self.record
        if record is None:
            return f"Install record: {method}, none - {self.note or 'nothing to record'}; {settled}."
        if record.get("package_id"):
            what = record["package_id"]
        elif record.get("product_code"):
            what = record["product_code"]
        elif record.get("key_name"):
            where = record.get("sid") if record.get("hive") == HKCU_NAME else record.get("view")
            what = f"{'HKCU' if record.get('hive') == HKCU_NAME else 'HKLM'} {where} {record['key_name']}"
        else:
            what = "no entry named"
        if record.get("display_version"):
            what += f" {record['display_version']}"
        reason = record.get("unavailable_reason")
        state = f"unavailable ({reason})" if reason else "recorded"
        replaced = len(record.get("replaced_entries") or [])
        return f"Install record: {method}, {state}: {what}; {settled}; {replaced} replaced."

    def log_summary(self):
        if self.summarised:
            return
        self.summarised = True
        try:
            logger.info(self.summary())
        except Exception:  # noqa: BLE001
            logger.exception("Install record: the summary line failed.")


def run_install_command(command, software_id=None, recorder=None):
    """Run one job's install command; its InstallResult. The recorder never
    changes the result."""
    command_to_run = command

    uploads_match = re.search(
        r'(?P<quote>["\'])(?P<quoted_path>uploads/[^"\']+)(?P=quote)|(?P<bare_path>uploads/\S+)',
        command,
    )

    temp_path = None
    if uploads_match:
        relative_path = uploads_match.group("quoted_path") or uploads_match.group("bare_path")
        filename = relative_path.split("/", 1)[1]
        download_url = f"{SERVER_BASE_URL}/uploads/{filename}"

        # A stable path, not random: Windows Installer caches LastUsedSource
        # by path, and a new name makes reinstalls of the same ProductCode
        # fail (1603/1605/1612). software_id keeps shared filenames apart.
        stable_key = f"{software_id}_{filename}" if software_id is not None else filename
        temp_path = os.path.join(tempfile.gettempdir(), f"appease_{stable_key}")

        try:
            with request.urlopen(download_url, timeout=120) as response, open(temp_path, "wb") as output_file:
                output_file.write(response.read())
        except SERVER_ERRORS as exc:
            logger.error(f"Failed to download installer from {download_url}: {error_text(exc)}")
            try:
                os.remove(temp_path)
            except OSError:
                pass
            return InstallResult(False, None, REASON_DOWNLOAD_FAILED)

        if uploads_match.group("bare_path"):
            replacement = f'"{temp_path}"'
            command_to_run = command.replace(uploads_match.group("bare_path"), replacement, 1)
        else:
            command_to_run = command.replace(relative_path, temp_path, 1)

    is_choco = install_producer(command_to_run) == "choco"
    if is_choco:
        if not os.path.exists(CHOCO_EXE):
            logger.error(
                f"Chocolatey not found at {CHOCO_EXE}. Install Chocolatey on this "
                f"PC (or re-run setup.bat) before deploying Chocolatey packages."
            )
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            return InstallResult(False, None, REASON_CHOCO_MISSING)
        command_to_run = choco_command_line(command_to_run)

    # An empty command would exit 0 and falsely report "completed".
    if not command_to_run.strip():
        logger.error("Refusing to run empty install command.")
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        return InstallResult(False, None, REASON_EMPTY_COMMAND)

    method = install_method_of(command)
    if recorder is not None:
        recorder.start(method)

    # One limit for the whole job, forced reinstalls included: the server
    # stops waiting 45 minutes after the job started.
    deadline = time.time() + INSTALL_TIMEOUT_SECONDS
    settle = recorder.settle if recorder is not None and method == "exe" else None
    result = run_and_classify(command, command_to_run, deadline, on_success=settle)
    if result.reason == REASON_CHOCO_ALREADY_INSTALLED:
        result = confirm_choco_already_installed(command, result, deadline)

    # Before the downloaded .msi is deleted: its ProductCode is read here.
    if recorder is not None:
        recorder.finish(result, package=choco_package_id(command), msi_path=temp_path)

    if temp_path and os.path.exists(temp_path):
        try:
            os.remove(temp_path)
        except OSError:
            pass

    return result


def submit_problem_report(problem_type, description):
    try:
        response = post_json(
            "/api/agent/problem-reports",
            identity(problem_type=problem_type, description=description),
        )
        logger.info(f"Problem report submitted: {response.get('report_id')}")
    except SERVER_ERRORS as exc:
        logger.error(f"Failed to submit problem report to {describe_server_error(exc)}")


# ---- Verifying an uninstall ----
# Decided from Windows' program list, never an exit code alone, against a
# TARGET read BEFORE anything runs (the uninstall deletes its records):
# the recorded keys, exact names and folders. Exact names (case aside),
# so 7-Zip 24.08 is never taken for 26.03. Polled, because some
# uninstallers (NSIS) hand over to a copy of themselves and exit early.
# Never "removed" on a guess.

REASON_ALREADY_REMOVED = "already_removed"
REASON_REMOVED_RESTART_NEEDED = "removed_restart_needed"
REASON_REMOVED_FOLDER_LEFT = "removed_folder_left"
REASON_STILL_LISTED = "still_listed"
REASON_REMOVAL_UNVERIFIED = "removal_unverified"
# The ordinary verdict. Not a reason code: a removal is reported with none.
VERDICT_REMOVED = "removed"
REMOVED_VERDICTS = (VERDICT_REMOVED, REASON_REMOVED_RESTART_NEEDED, REASON_REMOVED_FOLDER_LEFT)
# Kept by the server on uninstall jobs only; t/test_uninstall_verify.py
# sends each through one.
UNINSTALL_VERDICT_REASONS = (REASON_ALREADY_REMOVED, REASON_REMOVED_RESTART_NEEDED,
                             REASON_REMOVED_FOLDER_LEFT, REASON_STILL_LISTED, REASON_REMOVAL_UNVERIFIED)
UNINSTALL_VERIFY_MAX_SECONDS = 120
UNINSTALL_VERIFY_CHECK_SECONDS = 5
UNINSTALL_RESTART_NEEDED_CODE = 3010


def uninstall_target(keys, names=(), folders=()):
    """A target: the keys to look for, and the exact names and the install
    folders they were recorded with. Each key is {"hive", "sid", "name"}."""
    return {
        "keys": list(keys),
        "names": {name.strip() for name in names if name and name.strip()},
        "folders": {folder for folder in folders if folder and folder.strip()},
    }


def msi_uninstall_target(product_code, sid, snapshot):
    """The MSI target, or None if product_code is not one; snapshot (taken
    before) supplies names and folders."""
    match = _PRODUCT_CODE.match((product_code or "").strip())
    if match is None:
        return None
    name = "{" + match.group(2).upper() + "}"
    keys = [{"hive": HKLM_NAME, "sid": None, "name": name}, {"hive": HKCU_NAME, "sid": sid, "name": name}]
    found = msi_entries(snapshot, name, sid) if snapshot is not None else []
    return uninstall_target(keys, [e["display_name"] for e in found], [e["install_location"] for e in found])


def msi_entries(snapshot, product_code, sid):
    """The entries in snapshot under this ProductCode's key: machine-wide in
    either view, and in sid's own per-user list - never another user's."""
    name = registry_fold(product_code)
    found = []
    for entry in snapshot["entries"].values():
        if registry_fold(entry["key_name"]) != name:
            continue
        if entry["hive"] == HKCU_NAME and (entry["sid"] or "").upper() != (sid or "").upper():
            continue
        found.append(entry)
    return found


def choco_uninstall_target(package_id, choco_root):
    """Every key the installed version's .registry recorded, read before the
    uninstall deletes the nuspec naming that version; None if nothing to
    check by. The snapshot survives uninstalls, so never means installed."""
    if not CHOCO_SAFE_TOKEN.match(package_id or ""):
        return None
    spec = read_nuspec(os.path.join(choco_root, "lib", package_id, package_id + ".nuspec"))
    if (spec is None or spec["id"].casefold() != package_id.casefold()
            or not CHOCO_SAFE_TOKEN.match(spec["id"]) or not CHOCO_SAFE_TOKEN.match(spec["version"])):
        return None
    snapshot = read_snapshot(os.path.join(choco_root, ".chocolatey", f"{spec['id']}.{spec['version']}", ".registry"))
    if snapshot is None or snapshot is NO_SNAPSHOT or not snapshot[1]:
        return None
    user, keys = snapshot
    return uninstall_target(
        [{"hive": k["hive"], "sid": user if k["hive"] == HKCU_NAME else None, "name": k["name"]} for k in keys],
        [k["display"] for k in keys],
        [k["location"] for k in keys],
    )


def keys_listed(keys, registry):
    """True if any key is there in either view, False if every one is gone
    from both, None if any could not be read and none was found."""
    unknown = False
    for key in keys:
        for view in SNAPSHOT_VIEWS:
            signal = registry.uninstall_key(key["hive"], key["sid"], key["name"], view)
            if signal == SIGNAL_PRESENT:
                return True
            if signal != SIGNAL_ABSENT:
                unknown = True
    return None if unknown else False


def already_gone(target, registry):
    """Before anything runs: True if every target key is gone from both
    views, False if one is there, None if unknown (the verdict decides)."""
    if target is None or not target["keys"]:
        return None
    try:
        listed = keys_listed(target["keys"], registry)
    except Exception as exc:  # noqa: BLE001 - a read that fails decides nothing
        logger.warning(f"Already-gone check could not read the registry ({error_text(exc)}).")
        return None
    return None if listed is None else not listed


def removal_verdict(target, exit_code, registry, folder_signal=None):
    """One check after the uninstaller ran: VERDICT_REMOVED or a reason."""
    if target is None or not target["keys"]:
        return REASON_REMOVAL_UNVERIFIED
    try:
        listed = keys_listed(target["keys"], registry)
        if listed is None:
            return REASON_REMOVAL_UNVERIFIED
        if listed:
            return REASON_STILL_LISTED
        if target["names"]:
            sids = sorted({k["sid"].upper() for k in target["keys"]
                           if k["hive"] == HKCU_NAME and k["sid"] and USER_SID.match(k["sid"])})
            shown = registry.listed_display_names(sids)
            if shown is None:
                return REASON_REMOVAL_UNVERIFIED
            wanted = {name.casefold() for name in target["names"]}
            if wanted & {name.strip().casefold() for name in shown}:
                return REASON_STILL_LISTED
    except Exception as exc:  # noqa: BLE001 - never "removed" on a failed read
        logger.warning(f"Uninstall check could not read the registry ({error_text(exc)}).")
        return REASON_REMOVAL_UNVERIFIED
    if exit_code == UNINSTALL_RESTART_NEEDED_CODE:
        return REASON_REMOVED_RESTART_NEEDED
    signal = folder_signal or install_location_signal
    if any(signal(folder) == SIGNAL_PRESENT for folder in sorted(target["folders"])):
        return REASON_REMOVED_FOLDER_LEFT
    return VERDICT_REMOVED


def verify_removal(target, exit_code, registry=None, clock=None, sleep=None, heartbeat=None, folder_signal=None):
    """(verdict, seconds waited), polled per UNINSTALL_VERIFY_*, stopping
    once removed, with heartbeats; the last verdict stands."""
    registry = registry or WindowsRegistry()
    clock = clock or time.time
    sleep = sleep or time.sleep
    heartbeat = heartbeat or send_install_heartbeat
    start = clock()
    while True:
        verdict = removal_verdict(target, exit_code, registry, folder_signal)
        elapsed = clock() - start
        if verdict in REMOVED_VERDICTS or elapsed >= UNINSTALL_VERIFY_MAX_SECONDS:
            return verdict, elapsed
        sleep(min(UNINSTALL_VERIFY_CHECK_SECONDS, UNINSTALL_VERIFY_MAX_SECONDS - elapsed))
        heartbeat()


def verdict_report(verdict):
    """(completed, reason) for the job report: still_listed is a failure,
    every other verdict a completed job, and a plain removal has no reason."""
    if verdict == REASON_STILL_LISTED:
        return False, REASON_STILL_LISTED
    return True, (None if verdict == VERDICT_REMOVED else verdict)


# ---- Uninstalling an MSI ----
# The server sends only {id, action, method, product_code}; everything
# else is read from Windows, and any failed guard means nothing runs. An
# already-gone product reports already_removed (also how a re-run after
# an interrupting restart ends). 1641 is unverified: the restart has
# begun. Any exception is agent_error, never a removal or a crash.

UNINSTALL_TIMEOUT_SECONDS = 600
MSI_UNINSTALL_NOT_INSTALLED = 1605
MSI_UNINSTALL_RESTARTING = 1641
MSI_UNINSTALL_VERIFIED_CODES = (0, MSI_UNINSTALL_NOT_INSTALLED, UNINSTALL_RESTART_NEEDED_CODE)
MSI_UNINSTALL_FAILURES = {code: MSI_EXIT_REASONS[code] for code in (1602, 1603, 1618, 1625)}
REASON_UNINSTALL_JOB_INVALID = "uninstall_job_invalid"
REASON_IDENTITY_MISMATCH = "identity_mismatch"
REASON_REGISTRY_UNREADABLE = "registry_unreadable"
REASON_PROTECTED_SOFTWARE = "protected_software"
REASON_SHARED_RUNTIME = "shared_runtime"
REASON_AGENT_ERROR = "agent_error"
# choco_shared_dependency reuses the install record's word as a job reason.
REASON_CHOCO_NOT_MANAGED = "choco_not_managed"
REASON_CHOCO_SHARED_DEPENDENCY = "choco_shared_dependency"
REASON_CHOCO_RECORDS_UNREADABLE = "choco_records_unreadable"
REASON_UNINSTALLER_UNSAFE_LOCATION = "uninstaller_unsafe_location"
REASON_NO_QUIET_UNINSTALL = "no_quiet_uninstall"
REASON_UNINSTALLER_UNSAFE_USER_LOCATION = "uninstaller_unsafe_user_location"
REASON_CHOCO_OWN_ENTRY_NOT_FOUND = "choco_own_entry_not_found"
REASON_CHOCO_OWN_ENTRY_NOT_MSI = "choco_own_entry_not_msi"
# Uninstall-only reasons beside the verdicts, likewise kept on uninstall
# jobs only; t/test_uninstall_msi.py sends each through the report route.
UNINSTALL_REFUSAL_REASONS = (REASON_UNINSTALL_JOB_INVALID, REASON_IDENTITY_MISMATCH, REASON_REGISTRY_UNREADABLE,
                             REASON_PROTECTED_SOFTWARE, REASON_SHARED_RUNTIME, REASON_AGENT_ERROR,
                             REASON_CHOCO_NOT_MANAGED, REASON_CHOCO_SHARED_DEPENDENCY,
                             REASON_CHOCO_RECORDS_UNREADABLE, REASON_UNINSTALLER_UNSAFE_LOCATION,
                             REASON_NO_QUIET_UNINSTALL, REASON_UNINSTALLER_UNSAFE_USER_LOCATION,
                             REASON_CHOCO_OWN_ENTRY_NOT_FOUND, REASON_CHOCO_OWN_ENTRY_NOT_MSI)
# The same families as the server's SHARED_RUNTIME_NAME (app.py); a test
# keeps the two identical.
SHARED_RUNTIME_NAME = re.compile(
    r"visual\s*c\+\+.*redistributable|vc_?redist"
    r"|\.net\s+(?:core\s+)?runtime|desktop\s+runtime|asp\.net\s+core|\.net\s+framework"
    r"|webview2|directx",
    re.IGNORECASE,
)
SHARED_RUNTIME_PUBLISHER = "Microsoft Corporation"

# outcome is for the log only, so it may say more than the reason sent.
UninstallOutcome = collections.namedtuple("UninstallOutcome", "ok exit_code reason outcome verified")


def _refused(reason):
    return UninstallOutcome(False, None, reason, f"refused: {reason}", None)


def msi_uninstall_argv(product_code):
    """msiexec by its full path - never whatever an msiexec on PATH is."""
    system_root = os.environ.get("SystemRoot") or r"C:\Windows"
    return [os.path.join(system_root, "System32", "msiexec.exe"), "/x", product_code, "/qn", "/norestart"]


def run_uninstall_process(argv, start=None, clock=None, sleep=None, heartbeat=None, stdout=None):
    """Run an uninstaller to its end or UNINSTALL_TIMEOUT_SECONDS: (exit
    code or None, timed out, whole tree ended). stdout is an optional file
    for its output. Raises OSError if it cannot start."""
    start = start or start_install_process
    clock = clock or time.time
    sleep = sleep or time.sleep
    heartbeat = heartbeat or send_install_heartbeat
    process, job = start(argv, subprocess.DEVNULL if stdout is None else stdout, shell=False)
    try:
        began = last_beat = clock()
        while process.poll() is None:
            sleep(1)
            now = clock()
            if now - last_beat >= INSTALL_HEARTBEAT_SECONDS:
                heartbeat()
                last_beat = now
            if now - began > UNINSTALL_TIMEOUT_SECONDS:
                whole_tree = stop_install_process(process, job)
                try:
                    process.wait(timeout=30)
                except Exception:  # noqa: BLE001 - it was ended; the result is a timeout either way
                    pass
                return None, True, whole_tree
        return process.returncode, False, False
    finally:
        if job is not None:
            job.close()


def shared_runtime_finding(product_code, entries, registry):
    """Why this product is a shared runtime, None if it is not, or
    REASON_REGISTRY_UNREADABLE when the dependency records cannot be read."""
    dependents = registry.installer_dependents(product_code)
    if dependents is None:
        return REASON_REGISTRY_UNREADABLE
    if dependents:
        return "other products depend on it: " + ", ".join(sorted(dependents)[:5])
    return shared_runtime_by_name(entries)


def shared_runtime_by_name(entries):
    """Why one of these entries is a Microsoft runtime by publisher and name,
    or None."""
    for entry in entries:
        if ((entry.get("publisher") or "") == SHARED_RUNTIME_PUBLISHER
                and SHARED_RUNTIME_NAME.search(entry["display_name"] or "")):
            return f"a Microsoft runtime by name: {entry['display_name']}"
    return None


def uninstall_msi(job, registry=None, read=None, user_sid=None, run=None, verify=None, folders=None):
    """One MSI uninstall job, start to verdict. Returns an UninstallOutcome.
    Keyword arguments are test hooks, defaulting to the real thing."""
    registry = registry or WindowsRegistry()
    read = read or read_uninstall_snapshot
    user_sid = user_sid or agent_user_sid
    run = run or run_uninstall_process
    verify = verify or (lambda target, code: verify_removal(target, code, registry))
    folders = protected_folders() if folders is None else folders

    product_code = job.get("product_code")
    if not isinstance(product_code, str) or not _GUID_KEY_NAME.match(product_code):
        logger.warning(f"Uninstall job {job.get('id')}: {product_code!r} is not a ProductCode.")
        return _refused(REASON_UNINSTALL_JOB_INVALID)
    product_code = product_code.upper()
    sid = user_sid()
    snapshot = read(sid)
    if snapshot is None:
        return _refused(REASON_REGISTRY_UNREADABLE)
    entries = msi_entries(snapshot, product_code, sid)
    if any(not entry["windows_installer"] for entry in entries):
        logger.warning(f"Uninstall of {product_code}: Windows lists an entry under it that is not a "
                       f"Windows Installer product; nothing was run.")
        return _refused(REASON_IDENTITY_MISMATCH)
    if any(is_protected_entry(entry, folders) for entry in entries):
        logger.warning(f"Uninstall of {product_code}: protected - AppEase's own agent runs on it.")
        return _refused(REASON_PROTECTED_SOFTWARE)
    shared = shared_runtime_finding(product_code, entries, registry)
    if shared == REASON_REGISTRY_UNREADABLE:
        return _refused(REASON_REGISTRY_UNREADABLE)
    if shared:
        logger.warning(f"Uninstall of {product_code}: a shared runtime - {shared}; nothing was run.")
        return _refused(REASON_SHARED_RUNTIME)

    target = msi_uninstall_target(product_code, sid, snapshot)
    if already_gone(target, registry):
        return UninstallOutcome(True, None, REASON_ALREADY_REMOVED, REASON_ALREADY_REMOVED, None)

    try:
        exit_code, timed_out, whole_tree = run(msi_uninstall_argv(product_code))
    except OSError as exc:
        logger.error(f"Could not start msiexec to uninstall {product_code} ({error_text(exc)}).")
        return UninstallOutcome(False, None, REASON_LAUNCH_FAILED, REASON_LAUNCH_FAILED, None)
    if timed_out:
        ended = "msiexec and everything it started" if whole_tree else "msiexec only"
        logger.error(f"Uninstall of {product_code} timed out after {UNINSTALL_TIMEOUT_SECONDS}s; ended {ended}.")
        return UninstallOutcome(False, None, REASON_TIMED_OUT, REASON_TIMED_OUT, None)
    if exit_code in MSI_UNINSTALL_VERIFIED_CODES:
        verdict, waited = verify(target, exit_code)
        if exit_code == MSI_UNINSTALL_NOT_INSTALLED and verdict in REMOVED_VERDICTS:
            verdict = REASON_ALREADY_REMOVED
        ok, reason = verdict_report(verdict)
        return UninstallOutcome(ok, exit_code, reason, verdict, waited)
    if exit_code == MSI_UNINSTALL_RESTARTING:
        return UninstallOutcome(True, exit_code, REASON_RESTART_STARTED, REASON_RESTART_STARTED, None)
    reason = MSI_UNINSTALL_FAILURES.get(exit_code, REASON_INSTALLER_EXIT_CODE)
    return UninstallOutcome(False, exit_code, reason, reason, None)


# ---- Uninstalling a Chocolatey package ----
# The server sends only {id, action, method, package_id}. Rules come from
# Chocolatey 2.7.3 runs captured on the VM (t/fixtures/choco-uninstall):
#   - Only lib\<id> means choco has a package: .chocolatey records outlive
#     an uninstall, and a failed one leaves a copy in lib-bad\.
#   - Choco refuses to remove a package another depends on. Removing the
#     wrapper vlc asks "uninstall vlc.install as well?" even with -y,
#     which closed stdin answers no, exiting 0. So a wrapper goes first,
#     alone, judged by exit 0 and lib\<wrapper> gone. Never -x.
#   - --fail-on-autouninstaller never fired (the package script fails
#     first); Windows' list afterwards is the real guard.
#   - For an app already removed outside choco, the cleanup uses -n
#     --skip-autouninstaller: an uninstall script can fail on a missing app.
# Order: identity and protection first, so nothing unsafe ever runs;
# dependents next, because choco refuses a package others need; the
# already-gone check before running anything, so a program removed
# outside choco only has choco's files cleaned up. A record it cannot
# read is a refusal, never a guess: a wrong uninstall cannot be undone.
# "is not installed" mid-run means something removed it meanwhile, so
# the surviving records decide.

CHOCO_UNINSTALL_FLAGS = ("-y", "--fail-on-autouninstaller", "--no-progress")
CHOCO_CLEANUP_FLAGS = ("-y", "-n", "--skip-autouninstaller", "--no-progress")
# Choco's documented "valid" codes bar 1641, each checked against Windows'
# list. 1605/1614 never appeared on the VM, so their tests fake output.
CHOCO_UNINSTALL_VERIFIED_CODES = (0, 1605, 1614, 3010)
CHOCO_UNINSTALL_GONE_CODES = (1605, 1614)
CHOCO_RESTART_PENDING_CODE = 350
CHOCO_UNINSTALL_RESTARTING = 1641
CHOCO_LIB_PRESENT = "present"
CHOCO_LIB_ABSENT = "absent"
CHOCO_LIB_UNREADABLE = "unreadable"
CHOCO_FAILED_DEPENDS = "depends"
CHOCO_FAILED_NOT_INSTALLED = "not installed"
_CHOCO_UNINSTALL_SUMMARY = re.compile(
    r"^Chocolatey uninstalled (\d+)/(\d+) packages\.(?: (\d+) packages failed\.)?$"
)
# What follows "<id>." in a record folder's name: a version, which starts
# with a digit - so the records of vlc never take in vlc.install's.
_CHOCO_RECORD_VERSION = re.compile(r"^\d+(?:\.\d+)*(?:-[0-9A-Za-z.-]+)?$")


def choco_uninstall_argv(package_id, cleanup=False, choco_exe=None):
    """choco.exe by its full path, never whatever a choco on PATH is."""
    flags = CHOCO_CLEANUP_FLAGS if cleanup else CHOCO_UNINSTALL_FLAGS
    return [choco_exe or CHOCO_EXE, "uninstall", package_id] + list(flags)


def run_choco_uninstall(argv):
    """run_uninstall_process for choco.exe, plus its output (via a file,
    never a pipe). Raises OSError if choco.exe cannot start."""
    handle = tempfile.NamedTemporaryFile(mode="w+", suffix=".log", delete=False, encoding="utf-8", errors="replace")
    path = handle.name
    try:
        try:
            exit_code, timed_out, whole_tree = run_uninstall_process(argv, stdout=handle)
        finally:
            try:
                handle.close()
            except OSError:
                pass
        output = ""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                output = f.read()
        except OSError:
            pass
        return exit_code, timed_out, whole_tree, output
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def choco_lib_package(choco_root, package_id):
    """(CHOCO_LIB_PRESENT, spec) if lib\\<id> has a nuspec (or .nupkg) for
    this id and a plain version; (CHOCO_LIB_ABSENT, None) with no
    lib\\<id> (lib-bad etc. do not count); else CHOCO_LIB_UNREADABLE."""
    folder = os.path.join(choco_root, "lib", package_id)
    try:
        os.listdir(folder)
    except FileNotFoundError:
        return CHOCO_LIB_ABSENT, None
    except OSError:
        return CHOCO_LIB_UNREADABLE, None
    nuspec = os.path.join(folder, package_id + ".nuspec")
    spec = read_nuspec(nuspec)
    if spec is None and not os.path.exists(nuspec):
        spec = read_packed_nuspec(os.path.join(folder, package_id + ".nupkg"), package_id)
    if (spec is None or spec["id"].casefold() != package_id.casefold()
            or not CHOCO_SAFE_TOKEN.match(spec["id"]) or not CHOCO_SAFE_TOKEN.match(spec["version"])):
        return CHOCO_LIB_UNREADABLE, None
    return CHOCO_LIB_PRESENT, spec


def choco_surviving_keys(choco_root, package_id):
    """Every key the package's surviving .chocolatey records name; [] if
    none, None if one is unreadable (it might name a listed key)."""
    base = os.path.join(choco_root, ".chocolatey")
    try:
        folders = sorted(os.listdir(base))
    except FileNotFoundError:
        return []
    except OSError:
        return None
    prefix = package_id.casefold() + "."
    keys = []
    for folder in folders:
        name = folder.casefold()
        if not name.startswith(prefix) or not _CHOCO_RECORD_VERSION.match(name[len(prefix):]):
            continue
        snapshot = read_snapshot(os.path.join(base, folder, ".registry"))
        if snapshot is None:
            return None
        if snapshot is NO_SNAPSHOT:
            continue
        user, found = snapshot
        keys.extend({"hive": k["hive"], "sid": user if k["hive"] == HKCU_NAME else None, "name": k["name"]}
                    for k in found)
    return keys


def choco_not_managed(package_id, choco_root, registry, exit_code=None):
    """Outcome when choco has no lib\\<id>; nothing runs. Surviving records
    decide: every key gone is already_removed, else choco_not_managed."""
    keys = choco_surviving_keys(choco_root, package_id)
    listed = None
    if keys:
        try:
            listed = keys_listed(keys, registry)
        except Exception as exc:  # noqa: BLE001 - a read that fails decides nothing
            logger.warning(f"Uninstall of {package_id}: could not read the registry ({error_text(exc)}).")
    if listed is False:
        logger.info(f"Uninstall of {package_id}: Chocolatey does not have it, and every key its records name "
                    f"is gone from Windows' list.")
        return UninstallOutcome(True, None, REASON_ALREADY_REMOVED, REASON_ALREADY_REMOVED, None)
    if keys is None:
        why = "its records could not be read"
    elif not keys:
        why = "no record of it is left to check Windows' list by"
    elif listed:
        why = "Windows still lists a key its records name"
    else:
        why = "Windows' list could not be read for the keys its records name"
    logger.warning(f"Uninstall of {package_id}: Chocolatey does not have it, and {why}; nothing was run.")
    return UninstallOutcome(False, exit_code, REASON_CHOCO_NOT_MANAGED, REASON_CHOCO_NOT_MANAGED, None)


def choco_live_entries(snapshot, keys):
    """Windows' own entries under these keys, from read_uninstall_snapshot():
    machine-wide in either view, and per-user under the key's own SID."""
    wanted = set()
    for key in keys:
        if key["hive"] == HKCU_NAME:
            wanted.add(("HKCU", (key["sid"] or "").upper(), registry_fold(key["name"])))
        else:
            wanted.update(("HKLM", view, registry_fold(key["name"])) for view in SNAPSHOT_VIEWS)
    return [entry for entry in snapshot["entries"].values() if entry_identity(entry) in wanted]


def choco_shared_runtime(keys, entries, registry):
    """shared_runtime_finding() for a package: each MSI key's dependency
    records, then publisher and name on its entries."""
    for code in sorted({registry_fold(k["name"]) for k in keys if _GUID_KEY_NAME.match(k["name"])}):
        finding = shared_runtime_finding(code, [], registry)
        if finding:
            return finding
    return shared_runtime_by_name(entries)


def choco_wrapper(choco_root, owner_id):
    """(wrapper id or None, refusal or None) for owner_id's dependents. Only
    a wrapper may depend on it: id + .install/.portable, no keys of its
    own, and nothing depending on it."""
    dependents, blocker = choco_dependents(choco_root, owner_id)
    if dependents is None:
        logger.warning(f"Uninstall of {owner_id}: installed package {blocker} could not be read; "
                       f"it might depend on {owner_id}, so nothing was run.")
        return None, REASON_CHOCO_RECORDS_UNREADABLE
    if not dependents:
        return None, None
    if len(dependents) == 1:
        candidate = next(iter(dependents))
        if owner_id.casefold() in (candidate + ".install", candidate + ".portable"):
            state, spec = choco_lib_package(choco_root, candidate)
            snapshot = None
            if state == CHOCO_LIB_PRESENT:
                snapshot = read_snapshot(os.path.join(choco_root, ".chocolatey",
                                                      f"{spec['id']}.{spec['version']}", ".registry"))
            if snapshot is None:
                logger.warning(f"Uninstall of {owner_id}: its wrapper {candidate}'s records could not be read; "
                               f"nothing was run.")
                return None, REASON_CHOCO_RECORDS_UNREADABLE
            if snapshot is NO_SNAPSHOT or not snapshot[1]:
                theirs, blocker = choco_dependents(choco_root, spec["id"])
                if theirs is None:
                    logger.warning(f"Uninstall of {owner_id}: installed package {blocker} could not be read; "
                                   f"it might depend on {spec['id']}, so nothing was run.")
                    return None, REASON_CHOCO_RECORDS_UNREADABLE
                if not theirs:
                    return spec["id"], None
    logger.warning(f"Uninstall of {owner_id}: {', '.join(sorted(dependents))} also need it; nothing was run.")
    return None, REASON_CHOCO_SHARED_DEPENDENCY


def choco_uninstall_failure(package_id, exit_code, output):
    """CHOCO_FAILED_DEPENDS / _NOT_INSTALLED for a refused uninstall, or
    None: exit 1, "packages failed", and a Failures entry for THIS package,
    worded as captured on the VM."""
    if exit_code != 1 or not output or not package_id:
        return None
    lines = output.splitlines()
    failed = 0
    for line in lines:
        match = _CHOCO_UNINSTALL_SUMMARY.match(line.strip())
        if match:
            failed = int(match.group(3) or 0)
    if not failed:
        return None
    name = re.escape(package_id)
    depends = re.compile(r"^-\s+" + name + r"\s+-\s+Unable to uninstall '" + name
                         + r"\.[^']+' because '[^']+' depends on it\.$", re.IGNORECASE)
    missing = re.compile(r"^-\s+" + name + r"\s+-\s+" + name
                         + r" is not installed\. Cannot uninstall a non-existent package\.$", re.IGNORECASE)
    block = _choco_block(lines, "Failures")
    if any(depends.match(line) for line in block):
        return CHOCO_FAILED_DEPENDS
    if any(missing.match(line) for line in block):
        return CHOCO_FAILED_NOT_INSTALLED
    return None


def _choco_run(run, argv, package_id):
    """One choco.exe run, its output to agent.log."""
    exit_code, timed_out, whole_tree, output = run(argv)
    if output and output.strip():
        logger.info(f"Chocolatey output for {package_id}:\n{output.strip()}")
    return exit_code, timed_out, whole_tree, output


def _choco_not_removed(package_id, exit_code, output, timed_out, whole_tree):
    """The outcome of a run that did not remove the package."""
    if timed_out:
        ended = "choco.exe and everything it started" if whole_tree else "choco.exe only"
        logger.error(f"Uninstall of {package_id} timed out after {UNINSTALL_TIMEOUT_SECONDS}s; ended {ended}.")
        return UninstallOutcome(False, None, REASON_TIMED_OUT, REASON_TIMED_OUT, None)
    if exit_code == CHOCO_UNINSTALL_RESTARTING:
        return UninstallOutcome(True, exit_code, REASON_RESTART_STARTED, REASON_RESTART_STARTED, None)
    if exit_code == CHOCO_RESTART_PENDING_CODE:
        return UninstallOutcome(False, exit_code, REASON_CHOCO_RESTART_PENDING, REASON_CHOCO_RESTART_PENDING, None)
    if choco_uninstall_failure(package_id, exit_code, output) == CHOCO_FAILED_DEPENDS:
        return UninstallOutcome(False, exit_code, REASON_CHOCO_SHARED_DEPENDENCY, REASON_CHOCO_SHARED_DEPENDENCY,
                                None)
    if exit_code in CHOCO_ERROR_CODES:
        return UninstallOutcome(False, exit_code, REASON_CHOCO_ERROR, REASON_CHOCO_ERROR, None)
    if not exit_code:
        # Exit 0 yet still in lib\ is undocumented: fail with no reason,
        # not a guessed one; agent.log has the output.
        return UninstallOutcome(False, exit_code, None, "not removed", None)
    return UninstallOutcome(False, exit_code, REASON_INSTALLER_EXIT_CODE, REASON_INSTALLER_EXIT_CODE, None)


def choco_cleanup(run, choco_exe, choco_root, owner_id, wrapper_id):
    """Program already gone: remove only choco's files, wrapper first.
    Always already_removed; exit 0 if the cleanup finished, else None."""
    cleaned = True
    for package_id in ([wrapper_id] if wrapper_id else []) + [owner_id]:
        try:
            exit_code, timed_out, _, _ = _choco_run(
                run, choco_uninstall_argv(package_id, cleanup=True, choco_exe=choco_exe), package_id)
        except OSError as exc:
            logger.warning(f"Could not start choco.exe to clean up {package_id} ({error_text(exc)}).")
            exit_code, timed_out = None, False
        if timed_out or exit_code != 0 or choco_lib_package(choco_root, package_id)[0] != CHOCO_LIB_ABSENT:
            cleaned = False
            logger.warning(f"Cleanup of {package_id}'s Chocolatey files did not finish (exit code "
                           f"{exit_code}{', timed out' if timed_out else ''}); the software itself was already "
                           f"gone, so the job reports already_removed.")
    note = "Chocolatey's files cleaned up" if cleaned else "cleanup did not finish"
    return UninstallOutcome(True, 0 if cleaned else None, REASON_ALREADY_REMOVED,
                            f"{REASON_ALREADY_REMOVED} ({note})", None)


# ---- A shared runtime among a package's keys ----
# A .registry holds every key that appeared during install: paint.net's
# held its own MSI and the WebView2 Runtime it brought along
# (t/fixtures/choco-runtime). Refusing the runtime must not block the
# program, and choco.exe cannot help: its auto-uninstaller runs EVERY
# key's uninstaller. So when runtimes sit beside other keys, the program's
# single own key must be an MSI, removed by uninstall_msi(); then only
# choco's files go (-n --skip-autouninstaller, never -x). Any verdict but
# removed leaves those files for a retry.
CHOCO_CLEANUP_AFTER = (None, REASON_ALREADY_REMOVED, REASON_REMOVED_RESTART_NEEDED, REASON_REMOVED_FOLDER_LEFT)


def choco_runtime_split(keys, snapshot):
    """(runtimes, own): each key whose live entries make it a Microsoft
    runtime by publisher and name, as (key, why), and every other key."""
    runtimes, own = [], []
    for key in keys:
        why = shared_runtime_by_name(choco_live_entries(snapshot, [key]))
        if why:
            runtimes.append((key, why))
        else:
            own.append(key)
    return runtimes, own


def choco_remove_own_files(run, choco_exe, choco_root, owner_id, wrapper_id):
    """Choco's own files for a package whose program is gone, wrapper
    first; True if all finished. Never changes the verdict."""
    cleaned = True
    for package_id in ([wrapper_id] if wrapper_id else []) + [owner_id]:
        try:
            exit_code, timed_out, _, _ = _choco_run(
                run, choco_uninstall_argv(package_id, cleanup=True, choco_exe=choco_exe), package_id)
        except OSError as exc:
            logger.warning(f"Could not start choco.exe to clean up {package_id} ({error_text(exc)}).")
            exit_code, timed_out = None, False
        if timed_out or exit_code != 0 or choco_lib_package(choco_root, package_id)[0] != CHOCO_LIB_ABSENT:
            cleaned = False
            logger.warning(f"Cleanup of {package_id}'s Chocolatey files did not finish (exit code "
                           f"{exit_code}{', timed out' if timed_out else ''}); the program itself is gone, so "
                           f"the job keeps that verdict, and Chocolatey still lists the package.")
    return cleaned


def uninstall_choco_beside_runtime(job, owner_id, runtimes, own, snapshot, registry, read, user_sid, run, run_msi,
                                   verify, folders, choco_exe, choco_root):
    """Remove the program's own MSI entry, never the runtimes beside it."""
    left = "; ".join(why for _, why in runtimes)
    entries = choco_live_entries(snapshot, own)
    shared = choco_shared_runtime(own, entries, registry)
    if shared == REASON_REGISTRY_UNREADABLE:
        return _refused(REASON_REGISTRY_UNREADABLE)
    if shared:
        logger.warning(f"Uninstall of {owner_id}: a shared runtime - {shared}; nothing was run.")
        return _refused(REASON_SHARED_RUNTIME)
    if len(own) != 1:
        logger.warning(f"Uninstall of {owner_id}: beside a shared runtime ({left}), Chocolatey recorded "
                       f"{len(own)} entries of its own, and nothing says which is the program; nothing was run.")
        return _refused(REASON_CHOCO_OWN_ENTRY_NOT_FOUND)
    key = own[0]
    if not _GUID_KEY_NAME.match(key["name"]) or not all(entry["windows_installer"] for entry in entries):
        logger.warning(f"Uninstall of {owner_id}: installed beside a shared runtime ({left}), and its own entry "
                       f"{key['name']} is not a Windows Installer product; nothing was run.")
        return _refused(REASON_CHOCO_OWN_ENTRY_NOT_MSI)
    wrapper_id, refusal = choco_wrapper(choco_root, owner_id)
    if refusal:
        return _refused(refusal)
    if wrapper_id and PROTECTED_CHOCO_PACKAGE.match(wrapper_id):
        return _refused(REASON_PROTECTED_SOFTWARE)

    product_code = key["name"].upper()
    logger.info(f"Uninstall of {owner_id}: its records also list a shared runtime ({left}), which is left in "
                f"place. Removing only its own entry {product_code}, with msiexec.")
    outcome = uninstall_msi({"id": job.get("id"), "product_code": product_code}, registry=registry, read=read,
                            user_sid=user_sid, run=run_msi, verify=verify, folders=folders)
    note = f", {product_code} only, shared runtime left in place"
    if not outcome.ok or outcome.reason not in CHOCO_CLEANUP_AFTER:
        if outcome.ok:
            logger.warning(f"Uninstall of {owner_id}: {outcome.outcome}, so Chocolatey's files for it were left "
                           f"for a retry to clean up.")
        return outcome._replace(outcome=outcome.outcome + note)
    cleaned = choco_remove_own_files(run, choco_exe, choco_root, owner_id, wrapper_id)
    files = "Chocolatey's files cleaned up" if cleaned else "cleanup did not finish"
    return outcome._replace(outcome=f"{outcome.outcome}{note}; {files}")


def uninstall_choco(job, registry=None, read=None, user_sid=None, run=None, verify=None, folders=None,
                    choco_exe=None, choco_root=None, run_msi=None):
    """One Chocolatey uninstall job, start to verdict; an UninstallOutcome.
    Keyword arguments are test hooks, defaulting to the real thing."""
    registry = registry or WindowsRegistry()
    read = read or read_uninstall_snapshot
    user_sid = user_sid or agent_user_sid
    run = run or run_choco_uninstall
    verify = verify or (lambda target, code: verify_removal(target, code, registry))
    folders = protected_folders() if folders is None else folders
    choco_exe = choco_exe or CHOCO_EXE
    choco_root = choco_root or os.path.dirname(os.path.dirname(choco_exe))

    package_id = job.get("package_id")
    if not isinstance(package_id, str) or not CHOCO_SAFE_TOKEN.match(package_id):
        logger.warning(f"Uninstall job {job.get('id')}: {package_id!r} is not a plain package id.")
        return _refused(REASON_UNINSTALL_JOB_INVALID)
    if PROTECTED_CHOCO_PACKAGE.match(package_id):
        logger.warning(f"Uninstall of {package_id}: protected - AppEase's own agent runs on it.")
        return _refused(REASON_PROTECTED_SOFTWARE)
    if not os.path.isfile(choco_exe):
        logger.error(f"Chocolatey not found at {choco_exe}; nothing was run.")
        return UninstallOutcome(False, None, REASON_CHOCO_MISSING, REASON_CHOCO_MISSING, None)

    state, spec = choco_lib_package(choco_root, package_id)
    if state == CHOCO_LIB_ABSENT:
        return choco_not_managed(package_id, choco_root, registry)
    if state != CHOCO_LIB_PRESENT:
        logger.warning(f"Uninstall of {package_id}: lib\\{package_id} could not be read; nothing was run.")
        return _refused(REASON_CHOCO_RECORDS_UNREADABLE)
    owner_id = spec["id"]
    target = choco_uninstall_target(owner_id, choco_root)
    if target is None:
        logger.warning(f"Uninstall of {owner_id}: no readable registry snapshot for {spec['version']}; "
                       f"nothing was run.")
        return _refused(REASON_CHOCO_RECORDS_UNREADABLE)

    snapshot = read(user_sid())
    if snapshot is None:
        return _refused(REASON_REGISTRY_UNREADABLE)
    entries = choco_live_entries(snapshot, target["keys"])
    recorded = ([{"display_name": name, "install_location": None} for name in sorted(target["names"])]
                + [{"display_name": None, "install_location": folder} for folder in sorted(target["folders"])])
    if any(is_protected_entry(entry, folders) for entry in entries + recorded):
        logger.warning(f"Uninstall of {owner_id}: protected - AppEase's own agent runs on it.")
        return _refused(REASON_PROTECTED_SOFTWARE)
    shared = choco_shared_runtime(target["keys"], entries, registry)
    if shared == REASON_REGISTRY_UNREADABLE:
        return _refused(REASON_REGISTRY_UNREADABLE)
    if shared:
        runtimes, own = choco_runtime_split(target["keys"], snapshot)
        if runtimes and own:
            return uninstall_choco_beside_runtime(job, owner_id, runtimes, own, snapshot, registry, read, user_sid,
                                                  run, run_msi, verify, folders, choco_exe, choco_root)
        logger.warning(f"Uninstall of {owner_id}: a shared runtime - {shared}; nothing was run.")
        return _refused(REASON_SHARED_RUNTIME)
    wrapper_id, refusal = choco_wrapper(choco_root, owner_id)
    if refusal:
        return _refused(refusal)
    if wrapper_id and PROTECTED_CHOCO_PACKAGE.match(wrapper_id):
        return _refused(REASON_PROTECTED_SOFTWARE)

    if already_gone(target, registry):
        return choco_cleanup(run, choco_exe, choco_root, owner_id, wrapper_id)

    if wrapper_id:
        try:
            exit_code, timed_out, whole_tree, output = _choco_run(
                run, choco_uninstall_argv(wrapper_id, choco_exe=choco_exe), wrapper_id)
        except OSError as exc:
            logger.error(f"Could not start choco.exe to uninstall {wrapper_id} ({error_text(exc)}).")
            return UninstallOutcome(False, None, REASON_LAUNCH_FAILED, REASON_LAUNCH_FAILED, None)
        if timed_out or exit_code != 0 or choco_lib_package(choco_root, wrapper_id)[0] != CHOCO_LIB_ABSENT:
            logger.error(f"Uninstall of {owner_id}: removing its wrapper {wrapper_id} first did not work "
                         f"(exit code {exit_code}); {owner_id} was not touched.")
            outcome = _choco_not_removed(wrapper_id, exit_code, output, timed_out, whole_tree)
            return outcome._replace(outcome=f"{outcome.outcome}, removing its wrapper {wrapper_id}")

    after = f", after its wrapper {wrapper_id}" if wrapper_id else ""
    try:
        exit_code, timed_out, whole_tree, output = _choco_run(
            run, choco_uninstall_argv(owner_id, choco_exe=choco_exe), owner_id)
    except OSError as exc:
        logger.error(f"Could not start choco.exe to uninstall {owner_id} ({error_text(exc)}).")
        return UninstallOutcome(False, None, REASON_LAUNCH_FAILED, REASON_LAUNCH_FAILED + after, None)
    if not timed_out and exit_code in CHOCO_UNINSTALL_VERIFIED_CODES:
        if choco_lib_package(choco_root, owner_id)[0] != CHOCO_LIB_ABSENT:
            logger.warning(f"Chocolatey exited {exit_code}, but lib\\{owner_id} is still there; "
                           f"Windows' own list decides.")
        verdict, waited = verify(target, exit_code)
        if exit_code in CHOCO_UNINSTALL_GONE_CODES and verdict in REMOVED_VERDICTS:
            verdict = REASON_ALREADY_REMOVED
        ok, reason = verdict_report(verdict)
        return UninstallOutcome(ok, exit_code, reason, verdict + after, waited)
    if not timed_out and choco_uninstall_failure(owner_id, exit_code, output) == CHOCO_FAILED_NOT_INSTALLED:
        if choco_lib_package(choco_root, owner_id)[0] == CHOCO_LIB_ABSENT:
            logger.warning(f"Chocolatey says {owner_id} is not installed: something removed it after the "
                           f"agent looked. Checking its records again.")
            outcome = choco_not_managed(owner_id, choco_root, registry, exit_code)
            return outcome._replace(outcome=outcome.outcome + after)
        logger.warning(f"Chocolatey says {owner_id} is not installed, but lib\\{owner_id} is still there.")
    outcome = _choco_not_removed(owner_id, exit_code, output, timed_out, whole_tree)
    return outcome._replace(outcome=outcome.outcome + after)


# ---- Uninstalling an EXE: choosing the command ----
# The server sends only {id, action, method, view, key_name} (an HKLM key)
# plus, for a re-opened row, its library switch. Everything else is read
# from the LIVE key, so a QuietUninstallString changed since install is
# judged as it is now. Inno Setup runs its UninstallString's program with
# /VERYSILENT /SUPPRESSMSGBOXES /NORESTART (its own quiet string's /SILENT
# still shows progress); else the vendor's QuietUninstallString; only a
# key with neither uses the job's switch. The program must resolve to an
# .exe inside Program Files, which Users can only read (anyone can write
# to ProgramData), and never one that runs other programs.

EXE_VIEWS = ("Registry64", "Registry32")
INNO_SILENT_ARGS = ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART")
# Never run, wherever it sits: each runs whatever it is told to.
EXE_FORBIDDEN_PROGRAMS = frozenset((
    "cmd.exe", "powershell.exe", "pwsh.exe", "rundll32.exe", "msiexec.exe",
    "wscript.exe", "cscript.exe", "mshta.exe", "reg.exe",
))
PROGRAM_FILES_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion"
PROGRAM_FILES_VALUES = ("ProgramFilesDir", "ProgramFilesDir (x86)")
# Not a reason: the key is gone, and the job goes to the "already gone" check.
EXE_KEY_GONE = "gone"
_UNQUOTED_PROGRAM = re.compile(r"^(.*?\.exe)(?=\s|$)", re.IGNORECASE)
_PLAIN_KEY_NAME = re.compile(r"^[^\\\x00-\x1f]{1,255}$")

# The Uninstall switches a job may carry - the same list as the server's
# UNINSTALL_SWITCH_ALLOWED (app.py); a test keeps the two identical.
UNINSTALL_SWITCH_ALLOWED = ("/S", "/SILENT", "/VERYSILENT", "/qn", "/norestart")
_UNINSTALL_SWITCHES = {switch.casefold(): switch for switch in UNINSTALL_SWITCH_ALLOWED}
# For the summary line only.
EXE_KIND_INNO = "Inno command"
EXE_KIND_VENDOR = "vendor command"
EXE_KIND_SWITCH = "SIHQ switch"

ExeCommand = collections.namedtuple("ExeCommand", "argv entry reason kind", defaults=(None,))


def clean_uninstall_switch(text):
    """Allowed switches in text, in allowlist spelling, each once, in order,
    or None: the server's cleaning, repeated on the PC."""
    kept = []
    for token in (text or "").split():
        switch = _UNINSTALL_SWITCHES.get(token.casefold())
        if switch is not None and switch not in kept:
            kept.append(switch)
    return " ".join(kept) or None


def uninstall_program_path(command):
    """The program an uninstall command runs, or None: quoted as written, or
    up to the first ".exe" + space/end, so an unquoted "C:\\Program Files"
    is not C:\\Program.exe. Matches the server's (a test checks)."""
    text = (command or "").strip()
    if text.startswith('"'):
        end = text.find('"', 1)
        return text[1:end] if end > 1 else None
    match = _UNQUOTED_PROGRAM.match(text)
    return match.group(1) if match else None


def uninstall_arguments(command):
    """What follows the program in an uninstall command, exactly as written."""
    text = (command or "").strip()
    if text.startswith('"'):
        end = text.find('"', 1)
        return text[end + 1:].strip() if end > 1 else ""
    match = _UNQUOTED_PROGRAM.match(text)
    return text[match.end():].strip() if match else ""


def split_windows_arguments(text):
    """Arguments split as the MS C runtime does, so subprocess passes exactly
    what the vendor wrote: 2n backslashes + quote are n and a toggle, 2n+1
    are n and a literal quote, and "" inside quotes is a quote."""
    args, current, has, quoted = [], [], False, False
    i, n = 0, len(text or "")
    while i < n:
        c = text[i]
        if c == "\\":
            j = i
            while j < n and text[j] == "\\":
                j += 1
            if j < n and text[j] == '"':
                current.append("\\" * ((j - i) // 2))
                if (j - i) % 2:
                    current.append('"')
                    j += 1
                i, has = j, True
                continue
            current.append(text[i:j])
            i, has = j, True
            continue
        if c == '"':
            if quoted and i + 1 < n and text[i + 1] == '"':
                current.append('"')
                i += 2
            else:
                quoted = not quoted
                i += 1
            has = True
            continue
        if c in " \t" and not quoted:
            if has:
                args.append("".join(current))
                current, has = [], False
            i += 1
            continue
        current.append(c)
        has = True
        i += 1
    if has:
        args.append("".join(current))
    return args


def _read_program_files_dirs():
    """Windows' own record of its Program Files folders, from the 64-bit
    view of HKLM, which only administrators can change. [] if unreadable."""
    if winreg is None:
        return []
    found = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, PROGRAM_FILES_KEY, 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            for name in PROGRAM_FILES_VALUES:
                try:
                    value, kind = winreg.QueryValueEx(key, name)
                except OSError:
                    continue
                if kind == winreg.REG_SZ and isinstance(value, str):
                    found.append(value)
    except OSError:
        return []
    return found


def program_files_roots(read=None, realpath=None):
    """This PC's Program Files folders, resolved like uninstaller paths; []
    if unreadable, and then nothing runs."""
    read = read or _read_program_files_dirs
    realpath = realpath or _strict_realpath
    roots = set()
    for value in read():
        if not isinstance(value, str) or "%" in value or not re.match(r"^[A-Za-z]:\\", value):
            continue
        try:
            roots.add(realpath(value))
        except (OSError, ValueError):
            continue
    return sorted(roots)


def _strict_realpath(path):
    """The final path, following links and 8.3 names; OSError if any part of
    it does not exist."""
    return os.path.realpath(path, strict=True)


def safe_uninstaller(path, roots, realpath=None, isfile=None):
    """The resolved program to run, or None if it is not a plain local path,
    missing, not an .exe, outside every root, or runs other programs."""
    realpath = realpath or _strict_realpath
    isfile = isfile or os.path.isfile
    if not path or any(c in path for c in "%/") or not re.match(r"^[A-Za-z]:\\", path):
        return None
    try:
        resolved = realpath(path)
    except (OSError, ValueError):
        return None
    folded = ntpath.normcase(resolved)
    if not folded.endswith(".exe") or ntpath.basename(folded) in EXE_FORBIDDEN_PROGRAMS:
        return None
    inside = any(folded.startswith(ntpath.normcase(root).rstrip("\\") + "\\") for root in roots)
    if not inside or not isfile(resolved):
        return None
    return resolved


def exe_uninstall_command(job, snapshot, roots, registry, folders, realpath=None, isfile=None):
    """An ExeCommand for one EXE job: what to run, or why not. Pure: reads
    only its arguments and runs nothing."""
    view, key_name = job.get("view"), job.get("key_name")
    if view not in EXE_VIEWS or not isinstance(key_name, str) or not _PLAIN_KEY_NAME.match(key_name):
        logger.warning(f"Uninstall job {job.get('id')}: {view!r} {key_name!r} is not a registry key it can use.")
        return ExeCommand(None, None, REASON_UNINSTALL_JOB_INVALID)
    switch_text = job.get("switch")
    if switch_text is not None and not isinstance(switch_text, str):
        logger.warning(f"Uninstall job {job.get('id')}: {switch_text!r} is not a switch it can use.")
        return ExeCommand(None, None, REASON_UNINSTALL_JOB_INVALID)
    if snapshot is None:
        return ExeCommand(None, None, REASON_REGISTRY_UNREADABLE)
    entry = snapshot["entries"].get(("HKLM", view, registry_fold(key_name)))
    if entry is None:
        return ExeCommand(None, None, EXE_KEY_GONE)
    if is_protected_entry(entry, folders):
        logger.warning(f"Uninstall of {key_name}: protected - AppEase's own agent runs on it.")
        return ExeCommand(None, entry, REASON_PROTECTED_SOFTWARE)
    if _GUID_KEY_NAME.match(key_name):
        shared = shared_runtime_finding(key_name.upper(), [entry], registry)
    else:
        shared = shared_runtime_by_name([entry])
    if shared == REASON_REGISTRY_UNREADABLE:
        return ExeCommand(None, entry, REASON_REGISTRY_UNREADABLE)
    if shared:
        logger.warning(f"Uninstall of {key_name}: a shared runtime - {shared}; nothing was run.")
        return ExeCommand(None, entry, REASON_SHARED_RUNTIME)

    switch = clean_uninstall_switch(switch_text)
    if entry["inno"]:
        kind = EXE_KIND_INNO
        command = entry["uninstall_string"] or entry["quiet_uninstall_string"]
        arguments = list(INNO_SILENT_ARGS)
    elif entry["quiet_uninstall_string"]:
        kind = EXE_KIND_VENDOR
        command = entry["quiet_uninstall_string"]
        arguments = split_windows_arguments(uninstall_arguments(command))
    elif switch and entry["uninstall_string"]:
        kind = EXE_KIND_SWITCH
        command = entry["uninstall_string"]
        arguments = switch.split()
    else:
        if switch:
            why = "and no UninstallString to use the job's switch with"
        elif switch_text:
            why = "and the job's switch has nothing allowed in it"
        else:
            why = "and the job carries no switch"
        logger.warning(f"Uninstall of {key_name}: Windows lists no quiet uninstall for it, {why}; nothing was run.")
        return ExeCommand(None, entry, REASON_NO_QUIET_UNINSTALL)
    if switch_text and kind != EXE_KIND_SWITCH:
        logger.info(f"Uninstall of {key_name}: Windows lists its own quiet uninstall, so the job's switch "
                    f"is not used.")
    program = safe_uninstaller(uninstall_program_path(command), roots, realpath, isfile)
    if program is None:
        logger.warning(f"Uninstall of {key_name}: its uninstaller ({command!r}) is not an .exe inside "
                       f"Program Files that may be run; nothing was run.")
        return ExeCommand(None, entry, REASON_UNINSTALLER_UNSAFE_LOCATION)
    return ExeCommand([program] + arguments, entry, None, kind)


# ---- Uninstalling an EXE: running it ----
# After the uninstaller exits, EXE jobs wait (same time limit) until
# nothing it started runs: WinSCP's and Notepad++'s exit with the key gone
# while a copy (_unins.tmp, Un.exe) still deletes the folder. A folder
# still there after verification is rechecked for
# EXE_FOLDER_BACKSTOP_SECONDS, for helpers that left the job. A key only
# in the other view is identity_mismatch. MSI and choco use neither wait
# (a test checks).

EXE_FOLDER_BACKSTOP_SECONDS = 15
EXE_VERIFIED_CODES = (0, UNINSTALL_RESTART_NEEDED_CODE)
EXE_RESTARTING = 1641


def run_exe_uninstaller(argv, start=None, clock=None, sleep=None, heartbeat=None):
    """Run an EXE uninstaller, then wait until nothing it started is left,
    within UNINSTALL_TIMEOUT_SECONDS: (exit code or None, timed out, whole
    tree ended, seconds waited after exit). OSError if it cannot start."""
    start = start or start_install_process
    clock = clock or time.time
    sleep = sleep or time.sleep
    heartbeat = heartbeat or send_install_heartbeat
    process, job = start(argv, subprocess.DEVNULL, shell=False)
    try:
        began = last_beat = clock()

        def tick():
            nonlocal last_beat
            sleep(1)
            now = clock()
            if now - last_beat >= INSTALL_HEARTBEAT_SECONDS:
                heartbeat()
                last_beat = now
            return now

        def stop(waited):
            whole_tree = stop_install_process(process, job)
            try:
                process.wait(timeout=30)
            except Exception:  # noqa: BLE001 - it was ended; the result is a timeout either way
                pass
            return None, True, whole_tree, waited

        while process.poll() is None:
            if tick() - began > UNINSTALL_TIMEOUT_SECONDS:
                return stop(0.0)
        exit_code = process.returncode
        exited = clock()
        if job is None:
            logger.warning("The uninstaller ran without a job, so what it started cannot be waited for; "
                           "the folder backstop is all there is.")
            return exit_code, False, False, 0.0
        while True:
            try:
                left = job.active_processes()
            except OSError as exc:
                logger.warning(f"Could not ask the uninstaller's job what is still running ({error_text(exc)}); "
                               f"verifying now.")
                break
            if not left:
                break
            if tick() - began > UNINSTALL_TIMEOUT_SECONDS:
                return stop(clock() - exited)
        return exit_code, False, False, clock() - exited
    finally:
        if job is not None:
            job.close()


def verify_exe_removal(target, exit_code, registry=None, clock=None, sleep=None, heartbeat=None,
                       folder_signal=None):
    """verify_removal() plus the folder backstop: (verdict, seconds taken)."""
    registry = registry or WindowsRegistry()
    clock = clock or time.time
    sleep = sleep or time.sleep
    verdict, waited = verify_removal(target, exit_code, registry, clock, sleep, heartbeat, folder_signal)
    if verdict != REASON_REMOVED_FOLDER_LEFT:
        return verdict, waited
    began = clock()
    while clock() - began < EXE_FOLDER_BACKSTOP_SECONDS:
        sleep(1)
        verdict = removal_verdict(target, exit_code, registry, folder_signal)
        if verdict != REASON_REMOVED_FOLDER_LEFT:
            break
    return verdict, waited + (clock() - began)


def exe_job_label(job):
    """How an EXE job is named in agent.log: its key and view."""
    return f"{job.get('key_name')} ({job.get('view')})"


def uninstall_exe(job, registry=None, read=None, user_sid=None, run=None, verify=None, folders=None, roots=None,
                  realpath=None, isfile=None):
    """One EXE uninstall job, start to verdict. Returns an UninstallOutcome.
    Keyword arguments are test hooks, defaulting to the real thing."""
    registry = registry or WindowsRegistry()
    read = read or read_uninstall_snapshot
    user_sid = user_sid or agent_user_sid
    run = run or run_exe_uninstaller
    verify = verify or (lambda target, code: verify_exe_removal(target, code, registry))
    folders = protected_folders() if folders is None else folders

    view, key_name = job.get("view"), job.get("key_name")
    if view not in EXE_VIEWS or not isinstance(key_name, str) or not _PLAIN_KEY_NAME.match(key_name):
        logger.warning(f"Uninstall job {job.get('id')}: {view!r} {key_name!r} is not a registry key it can use.")
        return _refused(REASON_UNINSTALL_JOB_INVALID)
    roots = program_files_roots() if roots is None else roots
    command = exe_uninstall_command(job, read(user_sid()), roots, registry, folders, realpath, isfile)

    if command.reason == EXE_KEY_GONE:
        gone = already_gone(uninstall_target([{"hive": HKLM_NAME, "sid": None, "name": key_name}]), registry)
        if gone:
            return UninstallOutcome(True, None, REASON_ALREADY_REMOVED, REASON_ALREADY_REMOVED, None)
        if gone is None:
            return _refused(REASON_REGISTRY_UNREADABLE)
        logger.warning(f"Uninstall of {exe_job_label(job)}: Windows lists {key_name} only in the other view - "
                       f"not the entry AppEase recorded; nothing was run.")
        return _refused(REASON_IDENTITY_MISMATCH)
    if command.reason:
        return _refused(command.reason)

    entry = command.entry
    kind = command.kind
    target = uninstall_target([{"hive": HKLM_NAME, "sid": None, "name": entry["key_name"]}],
                              [entry["display_name"]], [entry["install_location"]])
    try:
        exit_code, timed_out, whole_tree, waited = run(command.argv)
    except OSError as exc:
        logger.error(f"Could not start the uninstaller for {exe_job_label(job)} ({error_text(exc)}).")
        return UninstallOutcome(False, None, REASON_LAUNCH_FAILED, f"{REASON_LAUNCH_FAILED}, {kind}", None)
    if timed_out:
        ended = "the uninstaller and everything it started" if whole_tree else "the uninstaller only"
        logger.error(f"Uninstall of {exe_job_label(job)} timed out after {UNINSTALL_TIMEOUT_SECONDS}s; "
                     f"ended {ended}.")
        return UninstallOutcome(False, None, REASON_TIMED_OUT, f"{REASON_TIMED_OUT}, {kind}", None)
    how = f"{kind}, helpers {waited:.0f}s"
    if exit_code in EXE_VERIFIED_CODES:
        verdict, verified = verify(target, exit_code)
        ok, reason = verdict_report(verdict)
        return UninstallOutcome(ok, exit_code, reason, f"{verdict}, {how}", verified)
    if exit_code == EXE_RESTARTING:
        return UninstallOutcome(True, exit_code, REASON_RESTART_STARTED, f"{REASON_RESTART_STARTED}, {how}", None)
    return UninstallOutcome(False, exit_code, REASON_INSTALLER_EXIT_CODE, f"{REASON_INSTALLER_EXIT_CODE}, {how}",
                            None)


# ---- Uninstalling a per-user EXE ----
# HKCU jobs go only to agents advertising uninstall:exe-user and follow
# the machine-wide path, except: the entry must be in THIS account's
# per-user list (another SID is identity_mismatch - AppEase never reaches
# into other users' files), and the program must resolve inside this
# account's AppData\Local or \Roaming, found from HKLM's admin-only
# profile list, not environment variables. Uninstallers run elevated as
# this account, accepted because every lab account is an administrator.
# A Squirrel uninstaller stuck on an unseen dialog is ended by the time
# limit.

EXE_USER_CAPABILITY = "uninstall:exe-user"
PROFILE_LIST_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList"
USER_APPDATA_FOLDERS = ("AppData\\Local", "AppData\\Roaming")


def _read_profile_folder(sid):
    """sid's profile folder as Windows records it, in the 64-bit view of
    HKLM, which only administrators can change. None if unreadable."""
    if winreg is None or not isinstance(sid, str) or not USER_SID.match(sid):
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, PROFILE_LIST_KEY + "\\" + sid, 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            value, kind = winreg.QueryValueEx(key, "ProfileImagePath")
    except OSError:
        return None
    if not isinstance(value, str):
        return None
    if kind == winreg.REG_EXPAND_SZ:
        return winreg.ExpandEnvironmentStrings(value)
    return value if kind == winreg.REG_SZ else None


def user_appdata_roots(sid, read=None, realpath=None):
    """sid's AppData\\Local and \\Roaming, resolved like uninstaller paths;
    [] if the profile is not found, and then nothing runs."""
    read = read or _read_profile_folder
    realpath = realpath or _strict_realpath
    profile = read(sid)
    if not isinstance(profile, str) or "%" in profile or not re.match(r"^[A-Za-z]:\\", profile):
        return []
    roots = set()
    for folder in USER_APPDATA_FOLDERS:
        try:
            roots.add(realpath(ntpath.join(profile, folder)))
        except (OSError, ValueError):
            continue
    return sorted(roots)


def exe_user_uninstall_command(job, own_sid, snapshot, roots, registry, folders, realpath=None, isfile=None):
    """An ExeCommand for one per-user EXE job (own_sid: the agent's
    account). Pure: reads only its arguments and runs nothing."""
    sid, key_name = job.get("sid"), job.get("key_name")
    if (not isinstance(sid, str) or not USER_SID.match(sid) or not isinstance(key_name, str)
            or not _PLAIN_KEY_NAME.match(key_name)):
        logger.warning(f"Uninstall job {job.get('id')}: {sid!r} {key_name!r} is not a per-user key it can use.")
        return ExeCommand(None, None, REASON_UNINSTALL_JOB_INVALID)
    switch_text = job.get("switch")
    if switch_text is not None and not isinstance(switch_text, str):
        logger.warning(f"Uninstall job {job.get('id')}: {switch_text!r} is not a switch it can use.")
        return ExeCommand(None, None, REASON_UNINSTALL_JOB_INVALID)
    if not isinstance(own_sid, str) or sid.upper() != own_sid.upper():
        logger.warning(f"Uninstall of {key_name}: the job names another account ({sid}), not the one this agent "
                       f"runs as; nothing was run.")
        return ExeCommand(None, None, REASON_IDENTITY_MISMATCH)
    if snapshot is None or not snapshot["user_readable"]:
        return ExeCommand(None, None, REASON_REGISTRY_UNREADABLE)
    entry = snapshot["entries"].get(("HKCU", sid.upper(), registry_fold(key_name)))
    if entry is None:
        return ExeCommand(None, None, EXE_KEY_GONE)
    if is_protected_entry(entry, folders):
        logger.warning(f"Uninstall of {key_name}: protected - AppEase's own agent runs on it.")
        return ExeCommand(None, entry, REASON_PROTECTED_SOFTWARE)
    if _GUID_KEY_NAME.match(key_name):
        shared = shared_runtime_finding(key_name.upper(), [entry], registry)
    else:
        shared = shared_runtime_by_name([entry])
    if shared == REASON_REGISTRY_UNREADABLE:
        return ExeCommand(None, entry, REASON_REGISTRY_UNREADABLE)
    if shared:
        logger.warning(f"Uninstall of {key_name}: a shared runtime - {shared}; nothing was run.")
        return ExeCommand(None, entry, REASON_SHARED_RUNTIME)

    # The same choice, in the same order, as exe_uninstall_command(); a test
    # holds the two to the same answer for the same entry.
    switch = clean_uninstall_switch(switch_text)
    if entry["inno"]:
        kind = EXE_KIND_INNO
        command = entry["uninstall_string"] or entry["quiet_uninstall_string"]
        arguments = list(INNO_SILENT_ARGS)
    elif entry["quiet_uninstall_string"]:
        kind = EXE_KIND_VENDOR
        command = entry["quiet_uninstall_string"]
        arguments = split_windows_arguments(uninstall_arguments(command))
    elif switch and entry["uninstall_string"]:
        kind = EXE_KIND_SWITCH
        command = entry["uninstall_string"]
        arguments = switch.split()
    else:
        logger.warning(f"Uninstall of {key_name}: Windows lists no quiet uninstall for it, and the job carries "
                       f"no switch it can use; nothing was run.")
        return ExeCommand(None, entry, REASON_NO_QUIET_UNINSTALL)
    program = safe_uninstaller(uninstall_program_path(command), roots, realpath, isfile)
    if program is None:
        logger.warning(f"Uninstall of {key_name}: its uninstaller ({command!r}) is not an .exe inside this "
                       f"account's AppData that may be run; nothing was run.")
        return ExeCommand(None, entry, REASON_UNINSTALLER_UNSAFE_USER_LOCATION)
    return ExeCommand([program] + arguments, entry, None, kind)


def exe_user_job_label(job):
    """How a per-user EXE job is named in agent.log: its key and account."""
    return f"{job.get('key_name')} (HKCU {job.get('sid')})"


# ---- Closing a per-user app before its uninstaller runs ----
# A per-user app may still be running (VS Code reopens itself after setup
# in the agent's hidden session), and its uninstaller then exits 1 with
# files in use. So per-user uninstalls first end this account's processes
# running from the uninstaller's own folder - never an AppData root or a
# shared folder (Programs, Microsoft, Temp, Packages), never the agent or
# its Python. Each is re-checked through the handle that ends it, so a
# reused process id is safe. Unsaved work is lost, as with any uninstaller.
# Helpers that exit with their main process are "already gone", not
# failures; the uninstall goes ahead either way.

SHARED_APPDATA_FOLDERS = frozenset(("programs", "microsoft", "temp", "packages"))
TH32CS_SNAPPROCESS = 0x00000002
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_SYNCHRONIZE = 0x00100000
TOKEN_QUERY = 0x0008
TOKEN_USER_CLASS = 1
WAIT_OBJECT_0 = 0
CLOSE_WAIT_MILLISECONDS = 5000
# A process that is already on its way out refuses TerminateProcess with
# access denied; this long for it to finish before that answer is believed.
EXITING_WAIT_MILLISECONDS = 1000
ERROR_ACCESS_DENIED = 5
ERROR_INVALID_PARAMETER = 87
# The first three mean the app is not running, so only the rest warn.
CLOSE_CLOSED = "closed"
CLOSE_GONE = "already gone"
CLOSE_IMAGE_CHANGED = "image changed"
CLOSE_DENIED = "access denied"
CLOSE_STILL_RUNNING = "still running"
_INVALID_HANDLE = ctypes.c_void_p(-1).value


class _ProcessEntry32(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("cntUsage", ctypes.c_uint32),
        ("th32ProcessID", ctypes.c_uint32),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", ctypes.c_uint32),
        ("cntThreads", ctypes.c_uint32),
        ("th32ParentProcessID", ctypes.c_uint32),
        ("pcPriClassBase", ctypes.c_int32),
        ("dwFlags", ctypes.c_uint32),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


_process_api_cache = []


def _process_api():
    """(kernel32, advapi32) with prototypes declared - separate copies, so
    _kernel32() stays untouched. OSError off Windows."""
    if _process_api_cache:
        return _process_api_cache[0]
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        raise OSError("Closing an app needs Windows")
    k = win_dll("kernel32", use_last_error=True)
    a = win_dll("advapi32", use_last_error=True)
    handle, dword, boolean = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int
    signatures = {
        (k, "CreateToolhelp32Snapshot"): ([dword, dword], handle),
        (k, "Process32FirstW"): ([handle, ctypes.POINTER(_ProcessEntry32)], boolean),
        (k, "Process32NextW"): ([handle, ctypes.POINTER(_ProcessEntry32)], boolean),
        (k, "OpenProcess"): ([dword, boolean, dword], handle),
        (k, "QueryFullProcessImageNameW"): ([handle, dword, ctypes.c_wchar_p, ctypes.POINTER(dword)], boolean),
        (k, "TerminateProcess"): ([handle, ctypes.c_uint32], boolean),
        (k, "WaitForSingleObject"): ([handle, dword], dword),
        (k, "CloseHandle"): ([handle], boolean),
        (k, "LocalFree"): ([ctypes.c_void_p], ctypes.c_void_p),
        (a, "OpenProcessToken"): ([handle, dword, ctypes.POINTER(handle)], boolean),
        (a, "GetTokenInformation"): ([handle, ctypes.c_int, ctypes.c_void_p, dword, ctypes.POINTER(dword)], boolean),
        (a, "ConvertSidToStringSidW"): ([ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)], boolean),
    }
    for (dll, name), (argtypes, restype) in signatures.items():
        function = getattr(dll, name)
        function.argtypes = argtypes
        function.restype = restype
    _process_api_cache.append((k, a))
    return k, a


def _image_of(k, process):
    """The full path of a process's .exe, from an open handle, or None."""
    size = ctypes.c_uint32(32768)
    buffer = ctypes.create_unicode_buffer(size.value)
    if not k.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
        return None
    return buffer.value or None


def _owner_of(k, a, process):
    """The SID a process runs as, from an open handle, or None."""
    token = ctypes.c_void_p()
    if not a.OpenProcessToken(process, TOKEN_QUERY, ctypes.byref(token)):
        return None
    try:
        needed = ctypes.c_uint32(0)
        a.GetTokenInformation(token, TOKEN_USER_CLASS, None, 0, ctypes.byref(needed))
        if not needed.value:
            return None
        buffer = ctypes.create_string_buffer(needed.value)
        if not a.GetTokenInformation(token, TOKEN_USER_CLASS, buffer, needed, ctypes.byref(needed)):
            return None
        # TOKEN_USER starts with the SID's pointer.
        sid = ctypes.c_void_p.from_buffer(buffer).value
        text = ctypes.c_void_p()
        if not sid or not a.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            return None
        try:
            return ctypes.wstring_at(text.value)
        finally:
            k.LocalFree(text)
    finally:
        k.CloseHandle(token)


def running_processes():
    """[(pid, .exe path, owner SID)] for every process the agent can open;
    OSError if the list itself fails."""
    k, a = _process_api()
    snapshot = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == _INVALID_HANDLE:
        raise OSError(f"CreateToolhelp32Snapshot failed (Windows error {ctypes.get_last_error()})")
    found = []
    try:
        entry = _ProcessEntry32()
        entry.dwSize = ctypes.sizeof(_ProcessEntry32)
        more = k.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            pid = entry.th32ProcessID
            if pid not in (0, 4):
                process = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
                if process:
                    try:
                        image = _image_of(k, process)
                        if image:
                            found.append((pid, image, _owner_of(k, a, process)))
                    finally:
                        k.CloseHandle(process)
            more = k.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        k.CloseHandle(snapshot)
    return found


def end_process(pid, image):
    """End one process only if, through the handle that ends it, its .exe is
    still `image`. Returns a CLOSE_* or "Windows error N"; "image changed"
    means the pid now belongs to another program, which is left alone."""
    k, _ = _process_api()
    process = k.OpenProcess(PROCESS_TERMINATE | PROCESS_SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
    if not process:
        code = ctypes.get_last_error()
        if code == ERROR_INVALID_PARAMETER:
            return CLOSE_GONE
        return CLOSE_DENIED if code == ERROR_ACCESS_DENIED else f"Windows error {code}"
    try:
        if k.WaitForSingleObject(process, 0) == WAIT_OBJECT_0:
            return CLOSE_GONE
        now = _image_of(k, process)
        if not now:
            if k.WaitForSingleObject(process, EXITING_WAIT_MILLISECONDS) == WAIT_OBJECT_0:
                return CLOSE_GONE
            return f"Windows error {ctypes.get_last_error()}"
        if ntpath.normcase(now) != ntpath.normcase(image):
            return CLOSE_IMAGE_CHANGED
        if not k.TerminateProcess(process, 1):
            code = ctypes.get_last_error()
            if k.WaitForSingleObject(process, EXITING_WAIT_MILLISECONDS) == WAIT_OBJECT_0:
                return CLOSE_GONE
            return CLOSE_DENIED if code == ERROR_ACCESS_DENIED else f"Windows error {code}"
        if k.WaitForSingleObject(process, CLOSE_WAIT_MILLISECONDS) == WAIT_OBJECT_0:
            return CLOSE_CLOSED
        return CLOSE_STILL_RUNNING
    finally:
        k.CloseHandle(process)


def app_folder(program, roots):
    """The uninstaller's own folder if it is inside a root and not a shared
    folder; None (close nothing) otherwise."""
    folder = ntpath.dirname(program or "")
    folded = ntpath.normcase(folder).rstrip("\\")
    for root in roots:
        base = ntpath.normcase(root).rstrip("\\")
        if folded.startswith(base + "\\"):
            parts = folded[len(base) + 1:].split("\\")
            if len(parts) == 1 and parts[0] in SHARED_APPDATA_FOLDERS:
                return None
            return folder
    return None


def agent_own_programs():
    """The .exe files the agent itself runs as: never closed."""
    return {ntpath.normcase(p) for p in (sys.executable, getattr(sys, "_base_executable", None)) if p}


def processes_to_close(processes, folder, own_sid, never=(), own_pids=()):
    """(pid, path) of each process to end: its .exe inside folder, running as
    own_sid, and none of never (paths) or own_pids."""
    if not folder or not isinstance(own_sid, str):
        return []
    inside = ntpath.normcase(folder).rstrip("\\") + "\\"
    blocked = {ntpath.normcase(p) for p in never if p}
    chosen = []
    for pid, image, owner in processes:
        if not image or pid in own_pids:
            continue
        folded = ntpath.normcase(image)
        if not folded.startswith(inside) or folded in blocked:
            continue
        if not isinstance(owner, str) or owner.upper() != own_sid.upper():
            continue
        chosen.append((pid, image))
    return chosen


def close_app_processes(folder, own_sid, listing=None, end=None):
    """End what runs from a per-user app's folder: (closed, already gone,
    still running). Never raises; the uninstall goes ahead."""
    listing = listing or running_processes
    end = end or end_process
    if folder is None:
        logger.info("Before uninstalling: its uninstaller's folder is not one app's own, so nothing was closed.")
        return [], [], []
    try:
        chosen = processes_to_close(listing(), folder, own_sid, agent_own_programs(), {os.getpid(), os.getppid()})
    except Exception as exc:  # noqa: BLE001 - closing is a help, never a reason to stop
        logger.warning(f"Before uninstalling: could not list running programs ({error_text(exc)}); "
                       f"nothing was closed.")
        return [], [], []
    if not chosen:
        logger.info(f"Before uninstalling: nothing was running from {folder}.")
        return [], [], []
    closed, gone, left = [], [], []
    for pid, image in chosen:
        try:
            what = end(pid, image)
        except Exception as exc:  # noqa: BLE001
            what = error_text(exc)
        if what == CLOSE_CLOSED:
            closed.append((pid, image))
        elif what in (CLOSE_GONE, CLOSE_IMAGE_CHANGED):
            gone.append((pid, image, what))
        else:
            left.append((pid, image, what))

    def named(items):
        return ", ".join(f"{ntpath.basename(item[1])} ({item[0]})" for item in items)

    if closed:
        logger.info(f"Before uninstalling: closed {len(closed)} running process(es) from {folder}: {named(closed)}.")
    if gone:
        details = ", ".join(f"{ntpath.basename(image)} ({pid}, {what})" for pid, image, what in gone)
        logger.info(f"Before uninstalling: {len(gone)} more had already exited: {details}.")
    if left:
        details = ", ".join(f"{ntpath.basename(image)} ({pid}, {what})" for pid, image, what in left)
        logger.warning(f"Before uninstalling: {len(left)} process(es) from {folder} are still running: {details}.")
    return closed, [(pid, image) for pid, image, _ in gone], left


def uninstall_exe_user(job, registry=None, read=None, user_sid=None, run=None, verify=None, folders=None,
                       roots=None, realpath=None, isfile=None, close=None):
    """One per-user EXE uninstall job, start to verdict; an UninstallOutcome.
    Keyword arguments are test hooks, defaulting to the real thing."""
    registry = registry or WindowsRegistry()
    read = read or read_uninstall_snapshot
    user_sid = user_sid or agent_user_sid
    run = run or run_exe_uninstaller
    verify = verify or (lambda target, code: verify_exe_removal(target, code, registry))
    folders = protected_folders() if folders is None else folders
    close = close or close_app_processes

    own = user_sid()
    roots = user_appdata_roots(own) if roots is None else roots
    command = exe_user_uninstall_command(job, own, read(own), roots, registry, folders, realpath, isfile)
    if command.reason == EXE_KEY_GONE:
        key = {"hive": HKCU_NAME, "sid": job.get("sid"), "name": job.get("key_name")}
        gone = already_gone(uninstall_target([key]), registry)
        if gone:
            return UninstallOutcome(True, None, REASON_ALREADY_REMOVED, REASON_ALREADY_REMOVED, None)
        logger.warning(f"Uninstall of {exe_user_job_label(job)}: the per-user list changed while it was read; "
                       f"nothing was run.")
        return _refused(REASON_REGISTRY_UNREADABLE)
    if command.reason:
        return _refused(command.reason)

    close(app_folder(command.argv[0], roots), own)

    # Mirrors uninstall_exe() from here; keep the two in step.
    entry = command.entry
    kind = command.kind
    target = uninstall_target([{"hive": HKCU_NAME, "sid": entry["sid"], "name": entry["key_name"]}],
                              [entry["display_name"]], [entry["install_location"]])
    try:
        exit_code, timed_out, whole_tree, waited = run(command.argv)
    except OSError as exc:
        logger.error(f"Could not start the uninstaller for {exe_user_job_label(job)} ({error_text(exc)}).")
        return UninstallOutcome(False, None, REASON_LAUNCH_FAILED, f"{REASON_LAUNCH_FAILED}, {kind}", None)
    if timed_out:
        ended = "the uninstaller and everything it started" if whole_tree else "the uninstaller only"
        logger.error(f"Uninstall of {exe_user_job_label(job)} timed out after {UNINSTALL_TIMEOUT_SECONDS}s; "
                     f"ended {ended}.")
        return UninstallOutcome(False, None, REASON_TIMED_OUT, f"{REASON_TIMED_OUT}, {kind}", None)
    how = f"{kind}, helpers {waited:.0f}s"
    if exit_code in EXE_VERIFIED_CODES:
        verdict, verified = verify(target, exit_code)
        ok, reason = verdict_report(verdict)
        return UninstallOutcome(ok, exit_code, reason, f"{verdict}, {how}", verified)
    if exit_code == EXE_RESTARTING:
        return UninstallOutcome(True, exit_code, REASON_RESTART_STARTED, f"{REASON_RESTART_STARTED}, {how}", None)
    return UninstallOutcome(False, exit_code, REASON_INSTALLER_EXIT_CODE, f"{REASON_INSTALLER_EXIT_CODE}, {how}",
                            None)


def uninstall_exe_job(job, **kwargs):
    """UNINSTALLERS["exe"]: HKCU jobs to uninstall_exe_user(), the rest to
    uninstall_exe()."""
    if job.get("hive") == "HKCU":
        return uninstall_exe_user(job, **kwargs)
    return uninstall_exe(job, **kwargs)


def uninstall_job_label(job):
    """How any uninstall job is named in agent.log."""
    if job.get("product_code") or job.get("package_id"):
        return job.get("product_code") or job.get("package_id")
    return exe_user_job_label(job) if job.get("hive") == "HKCU" else exe_job_label(job)


def run_uninstall_job(job):
    """Run one uninstall job and return its UninstallOutcome. Never raises:
    anything that goes wrong is a failed agent_error, never a removal."""
    method = job.get("method")
    try:
        handler = UNINSTALLERS.get(method) if isinstance(method, str) else None
        if handler is None:
            outcome = _refused(REASON_UNINSTALL_JOB_INVALID)
        else:
            outcome = handler(job)
    except Exception:  # noqa: BLE001 - an uninstall job must never take the agent down
        logger.exception(f"Uninstall job {job.get('id')} hit an error; reporting it as failed.")
        outcome = UninstallOutcome(False, None, REASON_AGENT_ERROR, REASON_AGENT_ERROR, None)
    try:
        verified = "not run" if outcome.verified is None else f"{outcome.verified:.0f}s"
        logger.info(
            f"Uninstall summary: job {job.get('id')}, {method} "
            f"{uninstall_job_label(job)}: "
            f"{outcome.outcome}; exit code {outcome.exit_code if outcome.exit_code is not None else 'none'}; "
            f"verification {verified}."
        )
    except Exception:  # noqa: BLE001 - the summary never changes the outcome
        pass
    return outcome


def handle_uninstall_job(job):
    """Poll's uninstall branch: run the job and report it. Never raises."""
    try:
        job_id = job.get("id")
        if isinstance(job_id, bool) or not isinstance(job_id, int):
            logger.error(f"Received an uninstall job with no usable id ({job_id!r}); it cannot be reported.")
            return
        logger.info(f"Received uninstall job {job_id}: {job.get('method')} "
                    f"{uninstall_job_label(job)}")
        outcome = run_uninstall_job(job)
        fields = {"status": "completed" if outcome.ok else "failed",
                  "exit_code": outcome.exit_code, "reason": outcome.reason}
        try:
            reply = post_json(f"/api/agent/jobs/{job_id}/report", identity(**fields))
            logger.info(f"Reported uninstall {fields['status']}; reason sent {outcome.reason or 'none'}, "
                        f"kept {reply.get('reason') or 'none'}.")
        except SERVER_ERRORS as exc:
            # Left in_progress on the server, which re-queues it once stale;
            # the re-run finds the software already gone if it was removed.
            logger.error(f"Failed to report uninstall job {job_id} to {describe_server_error(exc)}")
    except Exception:  # noqa: BLE001
        logger.exception("Uninstall job handling failed; the agent carries on.")


UNINSTALLERS = {"msi": uninstall_msi, "choco": uninstall_choco, "exe": uninstall_exe_job}


def agent_capabilities():
    """What this agent can do beyond installing, derived from UNINSTALLERS
    so it never advertises a method it lacks. The server sends uninstalls
    only to agents advertising the method."""
    capabilities = ["uninstall:" + method for method in UNINSTALLERS]
    if UNINSTALLERS.get("exe") is uninstall_exe_job:
        capabilities.append(EXE_USER_CAPABILITY)
    return sorted(capabilities)


def poll_once():
    try:
        poll_response = post_json(
            "/api/agent/poll",
            identity(ip_address=get_local_ip(), capabilities=agent_capabilities()),
        )
    except SERVER_ERRORS as exc:
        logger.warning(
            f"Server unreachable at {describe_server_error(exc)}. "
            f"Retrying in {POLL_INTERVAL_SECONDS}s."
        )
        return

    job = poll_response.get("job")
    if not job:
        logger.debug("No pending job.")
        return
    if isinstance(job, dict) and job.get("action") == "uninstall":
        handle_uninstall_job(job)
        return

    logger.info(f"Received job {job['id']} for {job['software_name']}")
    recorder = InstallRecorder(job.get("software_name"))
    result = run_install_command(job["install_command"], job.get("software_id"), recorder=recorder)
    final_status = "completed" if result.ok else "failed"
    # A job that stopped before its install began still gets its one line.
    if not recorder.summarised:
        recorder.finish(result)

    fields = {"status": final_status, "exit_code": result.exit_code, "reason": result.reason}
    # Completed installs only: what a failed one left on the PC is unknown.
    if result.ok and recorder.record is not None:
        fields["install_record"] = recorder.record

    try:
        report_response = post_json(f"/api/agent/jobs/{job['id']}/report", identity(**fields))
        # "kept" is what the server stored: a dropped reason shows as none.
        # A server older than install records says nothing about one.
        sent = "install record sent" if "install_record" in fields else "no install record"
        logger.info(
            f"Reported status: {report_response.get('status')}; reason sent "
            f"{result.reason or 'none'}, kept {report_response.get('reason') or 'none'}; "
            f"{sent}, server says {report_response.get('install_record') or 'nothing'}."
        )
    except SERVER_ERRORS as exc:
        # Stays in_progress; the server re-queues it once stale.
        logger.error(
            f"Failed to report result for job {job['id']} to "
            f"{describe_server_error(exc)}"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-type", help="Problem type: hardware, software, network, or other")
    parser.add_argument("--report-description", help="Problem description text")
    args = parser.parse_args()

    # Here, not at import, so importing never creates an ID; before the
    # report branch, so CLI reports name the right row.
    global AGENT_ID
    AGENT_ID = load_agent_id()
    if AGENT_ID:
        logger.info(f"Agent ID {AGENT_ID} ({AGENT_ID_PATH}).")

    if args.report_type or args.report_description:
        if not args.report_type or not args.report_description:
            logger.error("Both --report-type and --report-description are required.")
            return
        submit_problem_report(args.report_type.lower().strip(), args.report_description.strip())
        return

    # At startup: there is no first-check-in hook, and the scheduled task
    # runs this at every boot.
    write_desktop_shortcut()
    last_shortcut_check = time.time()

    # Before the loop, for the same reason: a few seconds once, against a
    # loop that runs for weeks.
    inventory_status = report_hardware_inventory()
    last_inventory_check = time.time()

    while True:
        poll_once()

        # Hourly: lab icons get deleted. An intact file is not rewritten.
        if time.time() - last_shortcut_check >= SHORTCUT_RECHECK_SECONDS:
            write_desktop_shortcut()
            last_shortcut_check = time.time()

        # Hourly, or a minute after a failed SEND: a server down at boot is
        # the ordinary case, and the poll loop reconnects within seconds.
        if time.time() - last_inventory_check >= inventory_due_seconds(inventory_status):
            inventory_status = report_hardware_inventory()
            last_inventory_check = time.time()

        time.sleep(POLL_INTERVAL_SECONDS)


def run():
    """main() between start and exit log lines: the task discards stderr,
    so uncaught errors are logged here, and a killed run (which runs no
    Python) shows as a "starting" line with no "exiting" before it."""
    pid = os.getpid()
    logger.info(
        f"Agent starting (pid {pid}, hostname {COMPUTER_HOSTNAME}): "
        f"server {SERVER_BASE_URL}"
    )
    try:
        main()
    except KeyboardInterrupt:
        logger.warning(f"Agent exiting (pid {pid}): interrupted.")
        raise
    except SystemExit as exc:
        logger.warning(f"Agent exiting (pid {pid}): SystemExit, code {exc.code}.")
        raise
    except BaseException:
        # Re-raised so the process exits non-zero and the task's Last Run
        # Result shows a failure.
        logger.exception(f"Agent exiting (pid {pid}): unhandled error.")
        raise
    logger.info(f"Agent exiting (pid {pid}): finished normally.")


if __name__ == "__main__":
    run()
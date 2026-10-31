"""The easy-setup path: setup-server.bat, start-appease.bat, the server
address prompt in agent/setup.bat, and the Python they lean on
(server/server_setup.py and agent.py's --*-server options).

The .bat files cannot run here, so they get static checks: line endings,
labels, step numbering, and that every helper they call exists. The Python
behind them is run for real, on temp copies.
"""
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

passed = 0
failed = []


def check(label, condition):
    global passed
    if condition:
        passed += 1
    else:
        failed.append(label)
        print(f"  FAIL: {label}")


AGENT_PATH = os.path.join(ROOT, "agent", "agent.py")
SETUP_BAT = os.path.join(ROOT, "agent", "setup.bat")
SERVER_BAT = os.path.join(ROOT, "setup-server.bat")
START_BAT = os.path.join(ROOT, "start-appease.bat")
INSTALL_BAT = os.path.join(ROOT, "install-appease.bat")
HELPER_PATH = os.path.join(ROOT, "server", "server_setup.py")
EXAMPLE_PATH = os.path.join(ROOT, "server", ".env.example")
APP_PATH = os.path.join(ROOT, "server", "app.py")

for needed in (AGENT_PATH, SETUP_BAT, SERVER_BAT, START_BAT, INSTALL_BAT, HELPER_PATH, EXAMPLE_PATH, APP_PATH):
    if not os.path.isfile(needed):
        print(f"EASY SETUP CHECKS COULD NOT RUN - {os.path.relpath(needed, ROOT)} was not found.")
        sys.exit(3)

work = tempfile.mkdtemp(prefix="appease_easy_")
shutil.copy(AGENT_PATH, os.path.join(work, "agent.py"))
sys.path.insert(0, work)
import agent  # noqa: E402

sys.path.insert(0, os.path.dirname(HELPER_PATH))
import server_setup  # noqa: E402


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


# ---------------------------------------------------------------- addresses
print("Server address: normalise")
cases = {
    "192.168.1.10": "http://192.168.1.10:8080",
    "  192.168.1.10  ": "http://192.168.1.10:8080",
    "192.168.1.10:5000": "http://192.168.1.10:5000",
    "http://192.168.1.10:8080/": "http://192.168.1.10:8080",
    "HTTP://Lab-Server.local": "http://lab-server.local:8080",
    "https://appease.example.test": "https://appease.example.test:8080",
}
for typed, expected in cases.items():
    try:
        got = agent.normalize_server_url(typed)
    except ValueError as exc:
        got = f"ValueError: {exc}"
    check(f"{typed!r} -> {expected}", got == expected)

for typed in ("", "   ", "YOUR-SERVER-IP", "http://YOUR-SERVER-IP:8080", "ftp://10.0.0.1",
              "10.0.0.1/dashboard", "10.0.0.1:99999", "10.0.0.1:port", "a b", "user@10.0.0.1",
              "10.0.0.1?x=1", "-bad-.host", None):
    try:
        agent.normalize_server_url(typed)
        check(f"{typed!r} is refused", False)
    except ValueError as exc:
        check(f"{typed!r} is refused with a message", bool(str(exc).strip()))

print("Server address: read, default and write")
source = os.path.join(work, "agent.py")
original = read_bytes(source)
check("the repository copy still carries the placeholder", agent.read_server_url(source) == agent.SERVER_BASE_URL)
check("the placeholder is never offered as a default", agent.default_server_url(source) is None)
check("a missing file reads as None", agent.read_server_url(os.path.join(work, "missing.py")) is None)

installed = os.path.join(work, "installed_agent.py")
shutil.copy(source, installed)
saved = agent.write_server_url(installed, "10.20.30.40")
after = read_bytes(installed)
check("write returns the normalised address", saved == "http://10.20.30.40:8080")
check("write saves the address", agent.read_server_url(installed) == "http://10.20.30.40:8080")
before_lines = original.split(b"\r\n")
after_lines = after.split(b"\r\n")
changed = [i for i, (a, b) in enumerate(zip(before_lines, after_lines)) if a != b]
check("write keeps the line count", len(before_lines) == len(after_lines))
check("write changes exactly one line", len(changed) == 1)
check("the changed line is the SERVER_BASE_URL assignment",
      bool(changed) and after_lines[changed[0]] == b'SERVER_BASE_URL = "http://10.20.30.40:8080"')
check("write keeps CRLF line endings", after.count(b"\r\n") == original.count(b"\r\n")
      and b"\r" not in after.replace(b"\r\n", b""))
check("the source copy is untouched", read_bytes(source) == original)
check("default prefers the first real address", agent.default_server_url(source, installed) == "http://10.20.30.40:8080")
other = os.path.join(work, "other_agent.py")
shutil.copy(source, other)
agent.write_server_url(other, "10.9.9.9:9000")
check("default takes the first file when it is real", agent.default_server_url(other, installed) == "http://10.9.9.9:9000")

no_line = os.path.join(work, "no_line.py")
with open(no_line, "w", encoding="utf-8") as handle:
    handle.write("POLL_INTERVAL_SECONDS = 10\n")
try:
    agent.write_server_url(no_line, "10.0.0.1")
    check("write refuses a file without the line", False)
except ValueError:
    check("write refuses a file without the line", True)

print("Server address: command line")
check("ordinary agent options are not taken as server options",
      agent._server_cli(["--report-type", "hardware"]) is None and agent._server_cli([]) is None)


# The CLI runs from its own folder: importing agent above already put an
# agent.log in work/, and the point here is that the CLI itself writes none.
cli_dir = tempfile.mkdtemp(prefix="appease_cli_")
cli_source = os.path.join(cli_dir, "agent.py")
shutil.copy(AGENT_PATH, cli_source)


def run_agent(*args, path=cli_source):
    return subprocess.run([sys.executable, path, *args], capture_output=True, text=True, timeout=60, cwd=cli_dir)


result = run_agent("--check-server", "192.168.1.10")
check("--check-server prints the normalised address", result.returncode == 0 and result.stdout.strip() == "http://192.168.1.10:8080")
result = run_agent("--check-server", "YOUR-SERVER-IP")
check("--check-server exits 1 on a bad address", result.returncode == 1 and result.stdout.strip() == "")
check("--check-server says why on stderr", "YOUR-SERVER-IP" in result.stderr)
result = run_agent("--default-server", source, installed)
check("--default-server prints the installed address", result.stdout.strip() == "http://10.20.30.40:8080")
result = run_agent("--default-server", source)
check("--default-server prints nothing for a placeholder", result.returncode == 0 and result.stdout.strip() == "")

cli_copy = os.path.join(cli_dir, "cli_agent.py")
shutil.copy(AGENT_PATH, cli_copy)
result = run_agent("--set-server", "172.16.0.5", path=cli_copy)
check("--set-server with no path writes its own file", result.returncode == 0
      and agent.read_server_url(cli_copy) == "http://172.16.0.5:8080")
result = run_agent("--set-server", "not an address", path=cli_copy)
check("--set-server refuses a bad address and keeps the old one", result.returncode == 1
      and agent.read_server_url(cli_copy) == "http://172.16.0.5:8080")

listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
listener.bind(("127.0.0.1", 0))
listener.listen(1)
open_port = listener.getsockname()[1]
closed = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
closed.bind(("127.0.0.1", 0))
closed_port = closed.getsockname()[1]
closed.close()
result = run_agent("--probe-server", f"127.0.0.1:{open_port}")
check("--probe-server exits 0 when the port answers", result.returncode == 0)
result = run_agent("--probe-server", f"127.0.0.1:{closed_port}")
check("--probe-server exits 1 when nothing answers", result.returncode == 1 and "Nothing answered" in result.stdout)
listener.close()

check("the server options leave no agent.log behind", not os.path.exists(os.path.join(cli_dir, "agent.log")))
check("the server options create no agent ID", agent.AGENT_ID is None)

# ---------------------------------------------------------------- .env
print("server_setup: .env")
env_dir = tempfile.mkdtemp(prefix="appease_env_")
env_path = os.path.join(env_dir, ".env")
example_copy = os.path.join(env_dir, ".env.example")
shutil.copy(EXAMPLE_PATH, example_copy)


def env_values(path):
    values = {}
    with open(path, "r", encoding="utf-8", newline="") as handle:
        for line in handle.read().splitlines():
            key, value = server_setup._split_value(line)
            if key:
                values[key] = value
    return values


notes = server_setup.prepare_env(env_path, example_copy)
created = read_bytes(env_path)
values = env_values(env_path)
check(".env is created from the example", os.path.exists(env_path) and any("Created" in n for n in notes))
check("SECRET_KEY is 64 hex characters", re.fullmatch(r"[0-9a-f]{64}", values.get("SECRET_KEY", "")) is not None)
check("REPORT_QR_SECRET is 64 hex characters", re.fullmatch(r"[0-9a-f]{64}", values.get("REPORT_QR_SECRET", "")) is not None)
check("the two keys differ", values.get("SECRET_KEY") != values.get("REPORT_QR_SECRET"))
example_lines = read_bytes(example_copy).split(b"\r\n")
created_lines = created.split(b"\r\n")
diff = [i for i, (a, b) in enumerate(zip(example_lines, created_lines)) if a != b]
check("only the two key lines differ from the example", len(example_lines) == len(created_lines) and len(diff) == 2)
check(".env keeps the example's CRLF endings", b"\r" not in created.replace(b"\r\n", b""))
check("no secret is printed in the notes", not any(values["SECRET_KEY"] in n or values["REPORT_QR_SECRET"] in n for n in notes))

server_setup.prepare_env(env_path, example_copy)
check("running again changes nothing", read_bytes(env_path) == created)

with open(env_path, "w", encoding="utf-8", newline="") as handle:
    handle.write("SECRET_KEY=short\r\nREPORT_QR_SECRET=keep-this-printed-sticker-secret\r\nSMTP_HOST=mail.example.test\r\n")
server_setup.prepare_env(env_path, example_copy)
values = env_values(env_path)
check("a too-short SECRET_KEY is replaced", len(values["SECRET_KEY"]) == 64)
check("an existing REPORT_QR_SECRET is kept", values["REPORT_QR_SECRET"] == "keep-this-printed-sticker-secret")
check("other settings are kept", values.get("SMTP_HOST") == "mail.example.test")

with open(env_path, "w", encoding="utf-8", newline="") as handle:
    handle.write("SECRET_KEY=appease-bulsu-meneses-2026\nSMTP_HOST=x")
server_setup.prepare_env(env_path, example_copy)
text = read_bytes(env_path).decode("utf-8")
values = env_values(env_path)
check("the old public SECRET_KEY is replaced", values["SECRET_KEY"] != "appease-bulsu-meneses-2026" and len(values["SECRET_KEY"]) == 64)
check("a missing REPORT_QR_SECRET is added", len(values.get("REPORT_QR_SECRET", "")) == 64)
check("an appended line starts on its own line", "SMTP_HOST=x\nREPORT_QR_SECRET=" in text and "\r" not in text)

good = "a" * 40
with open(env_path, "w", encoding="utf-8", newline="") as handle:
    handle.write(f"SECRET_KEY={good}\r\nREPORT_QR_SECRET=\r\n")
server_setup.prepare_env(env_path, example_copy)
check("a usable SECRET_KEY is never replaced", env_values(env_path)["SECRET_KEY"] == good)

app_text = read_bytes(APP_PATH).decode("utf-8")
length = re.search(r"^MIN_SECRET_KEY_LENGTH = (\d+)", app_text, re.M)
compromised = re.search(r"^COMPROMISED_SECRET_KEYS = (\{[^}]*\})", app_text, re.M)
check("MIN_SECRET_KEY_LENGTH matches app.py", length is not None and int(length.group(1)) == server_setup.MIN_SECRET_KEY_LENGTH)
check("COMPROMISED_SECRET_KEYS matches app.py", compromised is not None and eval(compromised.group(1)) == server_setup.COMPROMISED_SECRET_KEYS)
check("app.py reads its keys from server/.env", "load_dotenv()" in app_text)
shutil.rmtree(env_dir, ignore_errors=True)

print("server_setup: addresses")
addresses = server_setup.lan_addresses()
check("no loopback or link-local address is offered",
      all(not a.startswith(("127.", "169.254.")) for a in addresses))
lines = server_setup.address_lines(["192.168.1.10", "10.0.0.5"])
check("localhost is always listed", lines[0].strip().endswith("http://localhost/"))
check("each address gets a browser line", sum("http://192.168.1.10/" in l or "http://10.0.0.5/" in l for l in lines) == 2)
check("agents are pointed at port 8080 on the first address", any("http://192.168.1.10:8080" in l for l in lines))
check("no network still says what to do", any("no network address" in l for l in server_setup.address_lines([])))
import contextlib  # noqa: E402
import io  # noqa: E402
with contextlib.redirect_stderr(io.StringIO()):
    check("an unknown command exits 2", server_setup.main(["bogus"]) == 2)

# ---------------------------------------------------------------- .bat files
print("Batch files")


def bat_lines(path):
    data = read_bytes(path)
    check(f"{os.path.basename(path)} uses CRLF only", b"\r\n" in data and b"\r" not in data.replace(b"\r\n", b"")
          and b"\n" not in data.replace(b"\r\n", b""))
    try:
        text = data.decode("ascii")
        check(f"{os.path.basename(path)} is plain ASCII", True)
    except UnicodeDecodeError:
        check(f"{os.path.basename(path)} is plain ASCII", False)
        text = data.decode("utf-8", "replace")
    return text.split("\r\n")


def labels_ok(path, lines):
    defined = {line.strip()[1:].split()[0].upper() for line in lines if re.match(r"^\s*:[A-Za-z_]", line)}
    used = set()
    for line in lines:
        if line.strip().upper().startswith("REM"):
            continue
        used.update(m.upper() for m in re.findall(r"\bgoto\s+:?([A-Za-z_]\w*)", line, re.I))
        used.update(m.upper() for m in re.findall(r"\bcall\s+:([A-Za-z_]\w*)", line, re.I))
    used.discard("EOF")
    check(f"{os.path.basename(path)}: every goto/call label exists ({sorted(used - defined)})", used <= defined)


def parens_ok(path, lines):
    depth, worst = 0, 0
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.upper().startswith("REM") or stripped.startswith("::"):
            continue
        line = line.replace("^(", "").replace("^)", "")
        line = re.sub(r'"[^"]*"', '""', line)
        if re.match(r"^\s*echo[ (.]", line, re.I) and "(" not in line.split("echo", 1)[0]:
            body = re.sub(r"^\s*echo\(?", "", line, flags=re.I)
            if not body.rstrip().endswith(")") or body.count("(") >= body.count(")"):
                continue
        depth += line.count("(") - line.count(")")
        worst = min(worst, depth)
    check(f"{os.path.basename(path)}: parentheses balance", depth == 0 and worst >= 0)


setup_lines = bat_lines(SETUP_BAT)
server_lines = bat_lines(SERVER_BAT)
start_lines = bat_lines(START_BAT)
install_lines = bat_lines(INSTALL_BAT)
for path, lines in ((SETUP_BAT, setup_lines), (SERVER_BAT, server_lines), (START_BAT, start_lines),
                    (INSTALL_BAT, install_lines)):
    labels_ok(path, lines)
    parens_ok(path, lines)
    check(f"{os.path.basename(path)} turns echo off first", lines[0].lower() == "@echo off")

setup_text = "\r\n".join(setup_lines)
steps = re.findall(r"\[(\d)/(\d)\]", setup_text)
check("setup.bat numbers its steps 1 to 6 of 6", [s for s, _ in steps] == ["1", "2", "3", "4", "5", "6"] and {t for _, t in steps} == {"6"})
used_options = set(re.findall(r"(--[a-z]+-server)\b", setup_text))
known_options = set(re.findall(r'"(--[a-z]+-server)"', read_bytes(AGENT_PATH).decode("ascii")))
check(f"setup.bat only uses server options agent.py has ({sorted(used_options)})", used_options and used_options <= known_options)
check("setup.bat writes the address into the INSTALLED copy",
      re.search(r'"%INSTALLDIR%\\agent\.py" --set-server', setup_text) is not None)
check("setup.bat asks before using an address that did not answer", "choice /c YN" in setup_text)
check("setup.bat no longer tells anyone to edit agent.py first", "edit it there before running this" not in setup_text)

server_text = "\r\n".join(server_lines)
steps = re.findall(r"\[(\d)/(\d)\]", server_text)
check("setup-server.bat numbers its steps 1 to 5 of 5", [s for s, _ in steps] == ["1", "2", "3", "4", "5"] and {t for _, t in steps} == {"5"})
check("setup-server.bat elevates itself", "-Verb RunAs" in server_text and "net session" in server_text)
check("setup-server.bat installs requirements.txt into server\\venv",
      'requirements.txt"' in server_text and "-m venv" in server_text and "venv\\Scripts\\python.exe" in server_text)
check("setup-server.bat prepares .env through server_setup.py", 'server_setup.py" env' in server_text)
check("setup-server.bat never writes .env itself", ".env" not in re.sub(r"(?im)^\s*(rem|echo).*$", "", server_text).replace("server_setup.py", ""))
check("setup-server.bat opens 8080 and 80", "for %%P in (8080 80)" in server_text and "localport=%%P" in server_text)
check("setup-server.bat removes its old rule before adding it", server_text.index("delete rule") < server_text.index("add rule"))
check("setup-server.bat points the shortcut at start-appease.bat", "'start-appease.bat'" in server_text)
check("the shortcut icon exists", os.path.isfile(os.path.join(ROOT, "server", "static", "img", "appease.ico")))

start_text = "\r\n".join(start_lines)
check("start-appease.bat runs app.py --prod from server\\", 'cd /d "%~dp0server"' in start_text and "app.py --prod" in start_text)
check("start-appease.bat refuses when setup has not run", "goto NOT_SET_UP" in start_text and "setup-server.bat" in start_text)
check("start-appease.bat uses the venv Python", '"%VENVPY%" app.py --prod' in start_text)
check("start-appease.bat keeps the window open when AppEase stops", start_text.count("pause") >= 2)

install_text = "\r\n".join(install_lines)
steps = re.findall(r"\[(\d)/(\d)\]", install_text)
check("install-appease.bat numbers its steps 1 to 3 of 3", [s for s, _ in steps] == ["1", "2", "3"] and {t for _, t in steps} == {"3"})
check("install-appease.bat elevates itself", "-Verb RunAs" in install_text and "net session" in install_text)
zip_url = re.search(r'set "REPO_ZIP_URL=([^"]+)"', install_text)
check("install-appease.bat downloads this repository's main branch",
      zip_url is not None and zip_url.group(1) == "https://github.com/Akitsushii/AppEase-OneClick-Deploy/archive/refs/heads/main.zip")
check("install-appease.bat installs into C:\\AppEase", 'set "INSTALLDIR=C:\\AppEase"' in install_text)
robocopy = re.search(r"^robocopy .*?(?=^if errorlevel 8)", install_text, re.M | re.S)
check("install-appease.bat copies with robocopy and treats 8+ as failure", robocopy is not None)
robocopy_text = robocopy.group(0) if robocopy else ""
check("install-appease.bat never deletes from C:\\AppEase",
      not re.search(r"/(MIR|PURGE)\b", robocopy_text, re.I) and not re.search(r'(?im)^\s*(rmdir|rd|del)\b[^\r\n]*INSTALLDIR', install_text))
for kept in (".env", "appease.db", "appease.db-wal", "appease.db-shm", "venv"):
    check(f"install-appease.bat never overwrites {kept}", re.search(r"/X[FD]\b[^/]*\s" + re.escape(kept) + r"(\s|\^|$)", robocopy_text) is not None)
check("install-appease.bat checks the download has setup-server.bat", 'exist "!SRC!\\setup-server.bat"' in install_text)
check("install-appease.bat refuses while AppEase is running", "-LocalPort 8080 -State Listen" in install_text)
check("install-appease.bat hands over to setup-server.bat", 'call "%INSTALLDIR%\\setup-server.bat"' in install_text)
check("install-appease.bat cleans up its download", install_text.count('rmdir /s /q "%WORK%"') >= 2)
attributes = read_bytes(os.path.join(ROOT, ".gitattributes")).decode("ascii")
check("install-appease.bat is stored byte for byte, so GitHub's raw download keeps CRLF",
      re.search(r"(?m)^install-appease\.bat\s+-text\s*$", attributes) is not None)

shutil.rmtree(work, ignore_errors=True)
shutil.rmtree(cli_dir, ignore_errors=True)
print()
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
sys.exit(1 if failed else 0)

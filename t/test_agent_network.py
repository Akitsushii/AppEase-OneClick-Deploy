"""agent.py when the server misbehaves, and the start and exit lines it
logs. REAL sockets only: the bugs live in which errors urllib wraps, so a
faked post_json would replace the thing under test.

Needs the hardware test: Task Scheduler itself; being killed (no Python
runs, so a kill shows as a "starting" line with no "exiting" before it);
real Wi-Fi, firewalls and a lab PC's configured address.

    python t/test_agent_network.py --agent C:\\Appease\\agent.py
"""
import http.server
import json
import logging
import os
import secrets
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import types

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


def agent_path():
    for index, arg in enumerate(sys.argv):
        if arg == "--agent" and index + 1 < len(sys.argv):
            return os.path.abspath(sys.argv[index + 1])
        if arg.startswith("--agent="):
            return os.path.abspath(arg.split("=", 1)[1])
    candidate = os.path.join(ROOT, "agent", "agent.py")
    return candidate if os.path.isfile(candidate) else None


AGENT_PATH = agent_path()
if not AGENT_PATH or not os.path.isfile(AGENT_PATH):
    print("AGENT CHECKS COULD NOT RUN - agent.py was not found.")
    print(r"    python t\test_agent_network.py --agent C:\Appease\agent.py")
    sys.exit(2)

work = tempfile.mkdtemp(prefix="appease_net_")
server_dir = os.path.join(work, "server")
# Never the real database or secrets - see test_agent_inventory.py.
shutil.copytree(
    os.path.join(ROOT, "server"), server_dir,
    ignore=shutil.ignore_patterns(
        "appease.db*", "__pycache__", "*.pyc", ".env",
        "uploads", "staging", "report_photos", "avatars",
    ),
)
# The copy is imported, not the original, so agent.log lands in the temp
# folder rather than beside the real agent.py.
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

REQUIRED_AGENT_SYMBOLS = ("run", "main", "poll_once", "send_install_heartbeat",
                          "report_hardware_inventory", "run_install_command",
                          "submit_problem_report", "SERVER_TIMEOUT_SECONDS",
                          "InstallResult")
missing = [name for name in REQUIRED_AGENT_SYMBOLS if not hasattr(agent, name)]
if missing:
    print("AGENT CHECKS COULD NOT RUN - agent.py is older than this test.")
    print("Loaded: %s" % AGENT_PATH)
    print("Missing: %s" % ", ".join(missing))
    shutil.rmtree(work, ignore_errors=True)
    sys.exit(3)

print(f"(using agent.py at {AGENT_PATH})")

# Short enough that the slow-server cases do not take minutes, long enough
# that a loaded test machine does not time out on the fast ones.
TIMEOUT = 2
agent.SERVER_TIMEOUT_SECONDS = TIMEOUT

# main() creates this machine's agent ID file. Never the real one in
# %ProgramData% - a test must not give the machine it runs on an identity.
agent.AGENT_ID_PATH = os.path.join(work, "programdata", "AppEase", "agent_id.txt")


# ---- log capture -----------------------------------------------------------
class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def clear(self):
        self.records = []

    def text(self, min_level=logging.DEBUG):
        return "\n".join(r.getMessage() for r in self.records if r.levelno >= min_level)


logs = Capture()
agent.logger.addHandler(logs)


# ---- a server that misbehaves on purpose ------------------------------------
# path prefix -> mode. Looked up per request so one server can answer the
# poll correctly and then fail the job report that follows it.
ROUTES = {}
JOB = {"id": 41, "software_id": 7, "software_name": "Probe", "install_command": "echo probe"}


class Quiet(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # the client hanging up mid-"hang" is the point, not a fault


class Misbehave(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def mode(self):
        for prefix, value in ROUTES.items():
            if self.path.startswith(prefix):
                return value
        return ROUTES.get("*", "refuse-json")

    def reply(self, status, body, content_type="application/json", length=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body) if length is None else length))
        self.end_headers()
        self.wfile.write(body)

    def respond(self):
        # Read the whole request first. Closing with unread bytes makes
        # Windows send a RESET, which is a different failure from the one a
        # case means to produce.
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        mode = self.mode()
        if mode == "drop":            # accepts, then hangs up with no reply
            return
        if mode == "hang":            # accepts, never answers in time
            time.sleep(TIMEOUT + 1.5)
            return
        if mode == "html":            # a router page on the wrong port
            self.reply(200, b"<html><body>Router login</body></html>", "text/html")
        elif mode == "json-list":     # valid JSON, but not an object
            self.reply(200, b"[]")
        elif mode == "truncated":     # promises 1000 bytes, sends 11
            self.reply(200, b'{"job": nul', length=1000)
        elif mode == "not-http":      # some other protocol on that port
            self.wfile.write(b"SSH-2.0-OpenSSH_9.6\r\n")
        elif mode == "job":
            self.reply(200, json.dumps({"job": JOB}).encode())
        elif mode == "ok":
            self.reply(200, b'{"message": "ok", "job": null}')
        else:
            self.reply(500, b"unexpected test mode")

    do_GET = respond
    do_POST = respond


bad = Quiet(("127.0.0.1", 0), Misbehave)
BAD_PORT = bad.server_address[1]
threading.Thread(target=bad.serve_forever, daemon=True).start()


def closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def attempt(fn, *args):
    """Call fn. Returns (value, exception) and never raises."""
    try:
        return fn(*args), None
    except BaseException as exc:  # noqa: BLE001 - a crash IS the finding
        return None, exc


FAILURES = ["drop", "hang", "html", "json-list", "truncated", "not-http"]
PS_OUTPUT = json.dumps({"manufacturer": "Dell Inc.", "model": "OptiPlex 3080",
                        "cpu": "Intel", "ram_mb": 8192})
real_run = agent.subprocess.run
agent.subprocess.run = lambda cmd, **kw: types.SimpleNamespace(returncode=0, stdout=PS_OUTPUT, stderr="")


print("\n=== 1. Polling survives every failure, and names the address ===")
refused = closed_port()
agent.SERVER_BASE_URL = f"http://127.0.0.1:{refused}"
logs.clear()
_, exc = attempt(agent.poll_once)
check("refused connection: poll does not raise", exc is None)
warning = logs.text(logging.WARNING)
# A machine on the wrong port must say WHICH port, not just
# "[WinError 10061]".
check("refused connection: warning names host and port", f"127.0.0.1:{refused}" in warning)

agent.SERVER_BASE_URL = f"http://127.0.0.1:{BAD_PORT}"
for mode in FAILURES:
    ROUTES.clear()
    ROUTES["*"] = mode
    logs.clear()
    _, exc = attempt(agent.poll_once)
    check(f"{mode}: poll does not raise (got {type(exc).__name__ if exc else None})", exc is None)
    check(f"{mode}: a warning is logged", logs.text(logging.WARNING) != "")
    check(f"{mode}: the warning names host and port", f"127.0.0.1:{BAD_PORT}" in logs.text(logging.WARNING))
    # A reply that is not HTTP carries the raw bytes the server sent, line
    # breaks included. One failure has to stay one line in agent.log, or
    # the next line reads as a separate, unexplained entry.
    check(f"{mode}: the warning is a single line", "\n" not in logs.text(logging.WARNING))


print("\n=== 2. The heartbeat during an install survives them too ===")
for mode in FAILURES:
    ROUTES.clear()
    ROUTES["*"] = mode
    logs.clear()
    _, exc = attempt(agent.send_install_heartbeat)
    check(f"{mode}: heartbeat does not raise (got {type(exc).__name__ if exc else None})", exc is None)
    check(f"{mode}: heartbeat failure names host and port", f"127.0.0.1:{BAD_PORT}" in logs.text(logging.WARNING))
    check(f"{mode}: heartbeat failure is a single line", "\n" not in logs.text(logging.WARNING))


print("\n=== 3. The startup inventory send ===")
for mode in FAILURES:
    ROUTES.clear()
    ROUTES["*"] = mode
    agent._pending_hardware_facts = None
    status, exc = attempt(agent.report_hardware_inventory)
    check(f"{mode}: inventory does not raise (got {type(exc).__name__ if exc else None})", exc is None)
    check(f"{mode}: counted as a failed send, so it retries in a minute",
          status == agent.INVENTORY_SEND_FAILED)
    check(f"{mode}: facts held for the retry", agent._pending_hardware_facts is not None)
agent._pending_hardware_facts = None


print("\n=== 4. Reporting a finished job ===")
# The poll works and hands over a job; the report afterwards fails. The
# install itself is stubbed - this section is about the request after it.
real_install = agent.run_install_command
installs = []
agent.run_install_command = (lambda command, software_id=None, recorder=None:
                             installs.append(command) or agent.InstallResult(True, 0, None))
for mode in FAILURES:
    ROUTES.clear()
    ROUTES["/api/agent/poll"] = "job"
    ROUTES["*"] = mode
    logs.clear()
    installs.clear()
    _, exc = attempt(agent.poll_once)
    check(f"{mode}: job report failure does not raise (got {type(exc).__name__ if exc else None})", exc is None)
    check(f"{mode}: the install did run first", installs == ["echo probe"])
    check(f"{mode}: the failed report names host and port",
          f"127.0.0.1:{BAD_PORT}" in logs.text(logging.ERROR))
agent.run_install_command = real_install


print("\n=== 5. Downloading an installer ===")
# Transport failures only. A 200 of the wrong content is a successful
# download as far as HTTP goes, and would go on to launch msiexec for real.
for mode in ("drop", "truncated", "not-http"):
    ROUTES.clear()
    ROUTES["*"] = mode
    logs.clear()
    result, exc = attempt(agent.run_install_command, 'msiexec /i "uploads/probe.msi" /qn', 7)
    check(f"{mode}: download does not raise (got {type(exc).__name__ if exc else None})", exc is None)
    check(f"{mode}: the install is reported as not done", result is not None and result.ok is False)
    check(f"{mode}: the download failure is logged on a single line",
          logs.text(logging.ERROR) != "" and "\n" not in logs.text(logging.ERROR))
leftover = os.path.join(tempfile.gettempdir(), "appease_7_probe.msi")
check("a failed download leaves no partial installer behind", not os.path.exists(leftover))


print("\n=== 6. Problem report from the command line ===")
for mode in ("drop", "html", "not-http"):
    ROUTES.clear()
    ROUTES["*"] = mode
    _, exc = attempt(agent.submit_problem_report, "other", "probe")
    check(f"{mode}: problem report does not raise (got {type(exc).__name__ if exc else None})", exc is None)


print("\n=== 7. Start line and exit line ===")
real_poll = agent.poll_once
real_shortcut = agent.write_desktop_shortcut
real_inventory = agent.report_hardware_inventory
real_sleep = agent.time.sleep
real_argv = sys.argv
# main() writes a real .url onto the Public desktop. Never from a test.
agent.write_desktop_shortcut = lambda: None
agent.report_hardware_inventory = lambda: agent.INVENTORY_NO_FACTS
agent.time.sleep = lambda seconds: None

agent.SERVER_BASE_URL = "http://10.99.88.77:5000"


def poll_then(exc):
    def poll():
        raise exc
    return poll


# A crash inside the polling loop.
sys.argv = ["agent.py"]
agent.poll_once = poll_then(RuntimeError("probe crash"))
logs.clear()
_, exc = attempt(agent.run)
records = list(logs.records)
check("an unhandled error still propagates (exit code stays non-zero)",
      isinstance(exc, RuntimeError))
check("the FIRST line logged is the start line", records and "start" in records[0].getMessage().lower())
check("the start line carries the full SERVER_BASE_URL",
      records and "http://10.99.88.77:5000" in records[0].getMessage())
check("the start line names this machine's hostname",
      records and agent.COMPUTER_HOSTNAME in records[0].getMessage())
last = records[-1] if records else None
check("a crash leaves a final log line", last is not None and last is not records[0])
check("that line is an error", last is not None and last.levelno >= logging.ERROR)
check("that line carries the traceback", last is not None and last.exc_info is not None
      and last.exc_info[0] is RuntimeError)

# Ctrl+C when run by hand.
agent.poll_once = poll_then(KeyboardInterrupt())
logs.clear()
_, exc = attempt(agent.run)
check("Ctrl+C still stops the agent", isinstance(exc, KeyboardInterrupt))
check("Ctrl+C leaves an exit line after the start line",
      len(logs.records) >= 2 and "exit" in logs.records[-1].getMessage().lower())

# argparse rejecting the command line.
sys.argv = ["agent.py", "--no-such-flag"]
logs.clear()
_stderr = sys.stderr
sys.stderr = open(os.devnull, "w")
try:
    _, exc = attempt(agent.run)
finally:
    sys.stderr.close()
    sys.stderr = _stderr
check("a bad command line still exits", isinstance(exc, SystemExit))
check("a bad command line leaves an exit line",
      len(logs.records) >= 2 and "exit" in logs.records[-1].getMessage().lower())

# The one path that returns normally: a problem report from the command line.
sys.argv = ["agent.py", "--report-type", "other", "--report-description", "probe"]
ROUTES.clear()
ROUTES["*"] = "ok"
agent.SERVER_BASE_URL = f"http://127.0.0.1:{BAD_PORT}"
logs.clear()
_, exc = attempt(agent.run)
check("a normal return does not raise", exc is None)
check("a normal return leaves an exit line",
      len(logs.records) >= 2 and "exit" in logs.records[-1].getMessage().lower())
check("a normal return is not logged as an error",
      bool(logs.records) and logs.records[-1].levelno < logging.WARNING)

sys.argv = real_argv
agent.poll_once = real_poll
agent.write_desktop_shortcut = real_shortcut
agent.report_hardware_inventory = real_inventory
agent.time.sleep = real_sleep


print("\n=== 8. Against the real server: nothing above broke the normal path ===")
# The error handling must not swallow a GOOD reply, and the reply check
# must accept every shape the real routes send.
from werkzeug.serving import make_server  # noqa: E402

real_server = make_server("127.0.0.1", 0, appmod.app, threaded=True)
threading.Thread(target=real_server.serve_forever, daemon=True).start()
agent.SERVER_BASE_URL = f"http://127.0.0.1:{real_server.server_port}"
agent.COMPUTER_HOSTNAME = "LAB-NET-E2E"

conn = sqlite3.connect(database.DB_PATH)
cur = conn.execute(
    "INSERT INTO software (name, version, type, install_command) VALUES (?, ?, ?, ?)",
    ("Echo probe", "1.0", "local", "echo appease-network-probe"))
software_id = cur.lastrowid
cur = conn.execute(
    "INSERT INTO jobs (computer_hostname, software_id, status) VALUES (?, ?, 'pending')",
    ("LAB-NET-E2E", software_id))
job_id = cur.lastrowid
conn.commit()
conn.close()

logs.clear()
_, exc = attempt(agent.poll_once)
check("real poll + install + report does not raise", exc is None)
check("no warning or error on the normal path", logs.text(logging.WARNING) == "")
conn = sqlite3.connect(database.DB_PATH)
conn.row_factory = sqlite3.Row
job = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
computer = conn.execute("SELECT hostname FROM computers WHERE hostname = 'LAB-NET-E2E'").fetchone()
conn.close()
check("the job reached the server as completed", job is not None and job["status"] == "completed")
check("the poll registered the machine", computer is not None)

logs.clear()
_, exc = attempt(agent.poll_once)
check("an empty poll ({job: null}) is accepted quietly",
      exc is None and logs.text(logging.WARNING) == "")

logs.clear()
agent.send_install_heartbeat()
check("a real heartbeat logs no failure", logs.text(logging.WARNING) == "")

agent._pending_hardware_facts = None
check("a real inventory send succeeds", agent.report_hardware_inventory() == agent.INVENTORY_SENT)

real_server.shutdown()
bad.shutdown()
agent.subprocess.run = real_run

print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)

for handler in list(agent.logger.handlers):
    handler.close()
os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

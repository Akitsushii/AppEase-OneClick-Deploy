"""`python app.py --prod` listens on 8080 for agents and 80 for browsers,
on real sockets through the real Waitress. If 80 cannot open, AppEase still
starts on 8080 and warns; if 8080 cannot, it does not start. The real ports
are never touched: socket.socket binds them to free ports instead.
"""
import http.client
import io
import logging
import os
import runpy
import secrets
import shutil
import socket
import sys
import tempfile
import threading
import time

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


work = tempfile.mkdtemp(prefix="appease_listen_")
server_dir = os.path.join(work, "server")
# Never the real database or secrets - see test_agent_inventory.py.
shutil.copytree(
    os.path.join(ROOT, "server"), server_dir,
    ignore=shutil.ignore_patterns(
        "appease.db*", "__pycache__", "*.pyc", ".env",
        "uploads", "staging", "report_photos", "avatars",
    ),
)
sys.path.insert(0, server_dir)
os.chdir(server_dir)
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
os.environ.pop("FLASK_DEBUG", None)

import waitress  # noqa: E402

REAL_SOCKET = socket.socket
PORTS = {}          # real port the app asks for -> free port used instead
REFUSE = set()      # real ports whose bind raises PermissionError


class Remapped(REAL_SOCKET):
    def bind(self, address):
        if isinstance(address, tuple) and address[1] in REFUSE:
            raise PermissionError(10013, "An attempt was made to access a socket in a way forbidden by its "
                                         "access permissions")
        if isinstance(address, tuple) and address[1] in PORTS:
            address = (address[0], PORTS[address[1]]) + tuple(address[2:])
        return super().bind(address)


socket.socket = Remapped


def free_port():
    probe = REAL_SOCKET(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


# What create_server was handed, and the address every request arrived from.
created = []
seen_addresses = []
REAL_CREATE = waitress.create_server


def recording_create(application, **kw):
    def recorder(environ, start_response):
        seen_addresses.append((environ.get("SERVER_PORT"), environ.get("REMOTE_ADDR")))
        return application(environ, start_response)
    created.append(kw)
    return REAL_CREATE(recorder, **kw)


waitress.create_server = recording_create


class Records(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.records = []

    def emit(self, record):
        self.records.append(record)


records = Records()
logging.getLogger().addHandler(records)


class Tee(io.StringIO):
    def __init__(self, real):
        super().__init__()
        self.real = real

    def write(self, text):
        self.real.write(text)
        return super().write(text)


def start(case):
    """Run app.py --prod in the background; return (started, error,
    printed)."""
    created.clear()
    seen_addresses.clear()
    records.records.clear()
    out = Tee(sys.stdout)
    outcome = {}

    def run():
        real_out, real_argv = sys.stdout, sys.argv
        sys.stdout, sys.argv = out, [os.path.join(server_dir, "app.py"), "--prod"]
        try:
            runpy.run_path(os.path.join(server_dir, "app.py"), run_name="__main__")
        except BaseException as exc:          # noqa: BLE001 - the outcome is the point
            outcome["error"] = exc
        finally:
            sys.stdout, sys.argv = real_out, real_argv

    thread = threading.Thread(target=run, name=case, daemon=True)
    thread.start()
    deadline = time.time() + 30
    while time.time() < deadline and not created and "error" not in outcome:
        time.sleep(0.05)
    time.sleep(0.5)
    return bool(created), outcome.get("error"), out.getvalue()


def get(port):
    """(status, None) for GET /login on 127.0.0.1:port, or (None, error)."""
    for _ in range(40):
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/login")
            status = conn.getresponse().status
            conn.close()
            return status, None
        except OSError as exc:
            last = exc
            time.sleep(0.1)
    return None, last


def first_port():
    """Port of the first socket Waitress was handed, or None."""
    socks = created[-1].get("sockets") if created else None
    return socks[0].getsockname()[1] if socks else None


def warnings_text():
    return " ".join(r.getMessage() for r in records.records if r.levelno >= logging.WARNING)


# ---------------------------------------------------------------------------
print("\n=== 1. Both ports open ===")
PORTS.clear(); REFUSE.clear()
PORTS.update({8080: free_port(), 80: free_port()})
started, error, printed = start("both")
check("AppEase starts", started and error is None)
check("Waitress is handed two listening sockets", created and len(created[-1].get("sockets", [])) == 2)
check("with 8 threads", created and created[-1].get("threads") == 8)
check("the agents' port answers", get(PORTS[8080])[0] == 200)
check("the browsers' port answers", get(PORTS[80])[0] == 200)
check("each request arrives from the visitor's own address, not a proxy's",
      seen_addresses and all(addr == "127.0.0.1" for _, addr in seen_addresses))
check("requests arrived on both ports", {str(PORTS[8080]), str(PORTS[80])} <= {p for p, _ in seen_addresses})
check("no warning is logged", warnings_text() == "")
check("the start line names both ports", "port 8080" in printed and "port 80" in printed)


# ---------------------------------------------------------------------------
print("\n=== 2. Port 80 already held by another program ===")
PORTS.clear(); REFUSE.clear()
PORTS.update({8080: free_port(), 80: free_port()})
# An ordinary listening socket, as another web server would hold it - no
# exclusive-use flag, which is what makes a shared bind possible on Windows.
holder = REAL_SOCKET(socket.AF_INET, socket.SOCK_STREAM)
holder.bind(("0.0.0.0", PORTS[80]))
holder.listen(1)
started, error, printed = start("held")
check("AppEase still starts", started and error is None)
check("Waitress is handed only the agents' socket", created and len(created[-1].get("sockets", [])) == 1)
check("that socket is the agents' port", first_port() == PORTS[8080])
check("the agents' port answers", get(PORTS[8080])[0] == 200)
text = warnings_text()
check("a warning is logged", text != "")
check("it names port 80 as the one that failed", "Port 80 could not be opened" in text)
check("and says AppEase runs on 8080", "port 8080 only" in text)
check("and tells browsers what address to use", ":8080" in text)
check("the start line says port 80 is unavailable", "port 80 unavailable" in printed)
holder.close()


# ---------------------------------------------------------------------------
print("\n=== 2b. Port 80 held by a server that allows sharing (another Waitress) ===")
# Waitress sets SO_REUSEADDR on its sockets. On Windows a second socket that
# also asks for SO_REUSEADDR binds the SAME port and the two split its
# traffic - so AppEase must ask for exclusive use, and fall back instead.
PORTS.clear(); REFUSE.clear()
PORTS.update({8080: free_port(), 80: free_port()})
sharer = REAL_SOCKET(socket.AF_INET, socket.SOCK_STREAM)
sharer.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sharer.bind(("0.0.0.0", PORTS[80]))
sharer.listen(1)
started, error, printed = start("shared")
check("AppEase still starts", started and error is None)
check("it does not share port 80 with the other server",
      created and len(created[-1].get("sockets", [])) == 1)
check("the agents' port answers", get(PORTS[8080])[0] == 200)
check("and it warns that port 80 could not be opened", "Port 80 could not be opened" in warnings_text())
sharer.close()


# ---------------------------------------------------------------------------
print("\n=== 3. Port 80 refused: no permission ===")
PORTS.clear(); REFUSE.clear()
PORTS.update({8080: free_port()})
REFUSE.add(80)
started, error, printed = start("refused")
check("AppEase still starts", started and error is None)
check("Waitress is handed only the agents' socket", created and len(created[-1].get("sockets", [])) == 1)
check("the agents' port answers", get(PORTS[8080])[0] == 200)
check("a warning is logged naming the permission error",
      "Port 80 could not be opened" in warnings_text() and "forbidden" in warnings_text())


# ---------------------------------------------------------------------------
print("\n=== 4. Port 8080 will not open: no start, as before ===")
PORTS.clear(); REFUSE.clear()
PORTS.update({8080: free_port(), 80: free_port()})
blocker = REAL_SOCKET(socket.AF_INET, socket.SOCK_STREAM)
blocker.bind(("0.0.0.0", PORTS[8080]))
blocker.listen(1)
started, error, printed = start("agents-blocked")
check("AppEase does not start: the start-up raises", error is not None)
check("it stops with the bind error", isinstance(error, OSError))
check("nothing is left bound on port 80", REAL_SOCKET(socket.AF_INET, socket.SOCK_STREAM).connect_ex(
    ("127.0.0.1", PORTS[80])) != 0)
blocker.close()


socket.socket = REAL_SOCKET
waitress.create_server = REAL_CREATE
print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)

os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

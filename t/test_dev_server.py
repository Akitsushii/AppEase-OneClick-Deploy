"""What `python app.py` starts. Without --prod: no debugger and no debug
mode, even with FLASK_DEBUG=1 - Werkzeug's console must never face the LAN.
With --prod: Waitress on 0.0.0.0:8080, 8 threads. The servers are replaced
by recorders, so the decision itself is checked, not the source.
"""
import os
import runpy
import secrets
import shutil
import socket
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


work = tempfile.mkdtemp(prefix="appease_devserver_")
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
# Set on purpose: an environment variable must not be able to switch the
# debugger back on.
os.environ["FLASK_DEBUG"] = "1"
os.environ.pop("FLASK_RUN_FROM_CLI", None)

import waitress  # noqa: E402
import werkzeug.serving  # noqa: E402

started = {}


def fake_run_simple(hostname, port, application, **options):
    started["dev"] = {"host": hostname, "port": port, "app": application, **options}


class FakeServer:
    def print_listen(self, format_str):
        pass

    def run(self):
        pass


def fake_create_server(application, **options):
    started["prod"] = {"app": application, **options}
    return FakeServer()


# --prod binds its own sockets before handing them to Waitress. The real
# ports 80 and 8080 are never touched - an AppEase already running here would
# make that fail - so both are bound to free ports instead.
REAL_SOCKET = socket.socket


def free_port():
    probe = REAL_SOCKET(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


PORTS = {8080: free_port(), 80: free_port()}


class Remapped(REAL_SOCKET):
    def bind(self, address):
        if isinstance(address, tuple) and address[1] in PORTS:
            address = (address[0], PORTS[address[1]]) + tuple(address[2:])
        return super().bind(address)


werkzeug.serving.run_simple = fake_run_simple
waitress.create_server = fake_create_server
socket.socket = Remapped


def start(*args):
    started.clear()
    real_argv = sys.argv
    sys.argv = [os.path.join(server_dir, "app.py"), *args]
    try:
        namespace = runpy.run_path(os.path.join(server_dir, "app.py"), run_name="__main__")
    finally:
        sys.argv = real_argv
    return namespace


# ---------------------------------------------------------------------------
print("\n=== 1. Without --prod: the dev server, with no debugger ===")
namespace = start()
dev = started.get("dev")
check("the dev server is started", dev is not None and "prod" not in started)
check("with the Werkzeug debugger OFF", dev is not None and dev.get("use_debugger") is False)
check("and the app itself not in debug mode", namespace["app"].debug is False)


# ---------------------------------------------------------------------------
print("\n=== 2. With --prod: Waitress, unchanged ===")
# In an ordinary environment. FLASK_DEBUG=1 above is there to attack the
# dev branch; Flask reads it into app.debug at import whichever branch runs.
os.environ.pop("FLASK_DEBUG", None)
namespace = start("--prod")
prod = started.get("prod")
listeners = (prod or {}).get("sockets") or []
check("Waitress is started", prod is not None and "dev" not in started)
check("on every interface", bool(listeners) and all(s.getsockname()[0] == "0.0.0.0" for s in listeners))
check("on port 8080, where the agents poll", bool(listeners) and listeners[0].getsockname()[1] == PORTS[8080])
check("with 8 threads", prod is not None and prod.get("threads") == 8)
check("and the app not in debug mode", namespace["app"].debug is False)
for s in listeners:
    s.close()
socket.socket = REAL_SOCKET


print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)

os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

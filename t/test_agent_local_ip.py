"""The address the agent reports: the one it would send FROM to reach the
server, not the adapter that reaches the internet. Each local address is
tried as the server's, and the answer must be the adapter on that side.

Needs the hardware test: a real second machine on a second network.
"""
import os
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


AGENT_PATH = os.path.join(ROOT, "agent", "agent.py")
if not os.path.isfile(AGENT_PATH):
    print("LOCAL IP CHECKS COULD NOT RUN - agent/agent.py was not found.")
    sys.exit(2)

work = tempfile.mkdtemp(prefix="appease_ip_")
# The copy is imported, not the original, so agent.log lands in the temp
# folder rather than beside the real agent.py.
shutil.copy(AGENT_PATH, os.path.join(work, "agent.py"))
sys.path.insert(0, work)
import agent  # noqa: E402

if not hasattr(agent, "get_local_ip"):
    print("LOCAL IP CHECKS COULD NOT RUN - agent.py is older than this test.")
    shutil.rmtree(work, ignore_errors=True)
    sys.exit(3)


def ip_for(url):
    """get_local_ip() with SERVER_BASE_URL set to url, and the exception, if
    any."""
    agent.SERVER_BASE_URL = url
    try:
        return agent.get_local_ip(), None
    except Exception as exc:    # noqa: BLE001 - any escape is the failure
        return None, exc


def own_addresses():
    """This machine's IPv4 addresses other than loopback, one per adapter."""
    found = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    return sorted(a for a in found if not a.startswith("127.") and not a.startswith("169.254."))


# ---------------------------------------------------------------------------
print("\n=== 1. The adapter that reaches the server ===")
got, exc = ip_for("http://127.0.0.1:8080")
check("a server on this machine is reached from loopback", got == "127.0.0.1" and exc is None)
got, exc = ip_for("http://localhost:8080")
check("a server named by hostname is resolved and reached", got == "127.0.0.1" and exc is None)
got, exc = ip_for("http://127.0.0.1")
check("a URL with no port still works", got == "127.0.0.1" and exc is None)

addresses = own_addresses()
print(f"  (this machine's adapters: {', '.join(addresses) or 'none up'})")
for address in addresses:
    got, exc = ip_for(f"http://{address}:8080")
    check(f"a server at {address} is reached from {address}", got == address and exc is None)
if len(addresses) < 2:
    print("  (fewer than two adapters up: the two-adapter case was not exercised here)")


# ---------------------------------------------------------------------------
print("\n=== 2. When it cannot tell: no address, never a crash ===")
for label, url in (
    ("no host at all", "http://"),
    ("not a URL", "not a url"),
    ("an unreadable port", "http://127.0.0.1:99999"),
    ("a port that is not a number", "http://127.0.0.1:eighty"),
    ("a host that does not resolve", "http://no-such-host.invalid:8080"),
    ("an empty setting", ""),
):
    got, exc = ip_for(url)
    check(f"{label}: does not raise", exc is None)
    check(f"{label}: reports no address", got is None)

real_socket = agent.socket.socket


class NoSockets:
    def __init__(self, *args, **kwargs):
        raise OSError(10050, "A socket operation encountered a dead network")


agent.socket.socket = NoSockets
try:
    got, exc = ip_for("http://127.0.0.1:8080")
finally:
    agent.socket.socket = real_socket
check("a network stack that refuses sockets: does not raise", exc is None)
check("a network stack that refuses sockets: reports no address", got is None)


# ---------------------------------------------------------------------------
print("\n=== 3. Every request that carries an address uses it ===")
sent = []


def capture(path, payload):
    sent.append((path, dict(payload)))
    return {"job": None}


real_post = agent.post_json
real_facts = agent.collect_hardware_facts
agent.post_json = capture
agent.collect_hardware_facts = lambda: {"cpu": "Probe CPU"}


def addresses_sent(url):
    agent.SERVER_BASE_URL = url
    sent.clear()
    agent.poll_once()
    agent.send_install_heartbeat()
    agent.report_hardware_inventory()
    return {path: body.get("ip_address", "MISSING") for path, body in sent}


targets = [("127.0.0.1", "http://127.0.0.1:8080")] + [(a, f"http://{a}:8080") for a in addresses]
for expected, url in targets:
    got = addresses_sent(url)
    check(f"server at {expected}: poll reports {expected}", got.get("/api/agent/poll") == expected)
    check(f"server at {expected}: heartbeat reports {expected}", got.get("/api/agent/heartbeat") == expected)
    check(f"server at {expected}: inventory reports {expected}", got.get("/api/agent/inventory") == expected)

try:
    got = addresses_sent("http://")
    raised = None
except Exception as exc:        # noqa: BLE001
    got, raised = {}, exc
check("an unusable setting does not stop the requests", raised is None and len(got) == 3)
check("and they go out with no address, as before", all(v is None for v in got.values()))

agent.post_json = real_post
agent.collect_hardware_facts = real_facts


print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)

shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

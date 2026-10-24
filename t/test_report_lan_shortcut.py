"""The lab-computer shortcut: a request from a registered computer's address
gets the form with no sticker. Agents claim addresses without credentials,
and every tunnelled request arrives from 127.0.0.1, so no computer row may
open the form to the internet. A real lab PC still gets straight in.
"""
import os
import secrets
import shutil
import sqlite3
import sys
import tempfile
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


PUBLIC = "report.example.test"
LAB_IP = "10.20.30.41"

work = tempfile.mkdtemp(prefix="appease_lan_")
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

# Both read at import, so both are set before it.
os.environ["PUBLIC_REPORT_HOST"] = PUBLIC
os.environ["REPORT_QR_SECRET"] = "f" * 64
os.environ.pop("DEMO_MODE_ENABLED", None)
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))

import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()
import app as appmod  # noqa: E402
appmod.app.config["TESTING"] = True

DB = database.DB_PATH


def q(sql, args=()):
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, args).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


q("INSERT INTO computers (hostname, ip_address, status) VALUES ('LAB-PC-01', ?, 'online')", (LAB_IP,))
q("INSERT INTO computers (hostname, ip_address, status) VALUES ('LAB-PC-02', '10.20.30.42', 'online')")

INTERNET = {"base_url": f"https://{PUBLIC}", "environ_overrides": {"REMOTE_ADDR": "127.0.0.1"},
            "headers": {"CF-Connecting-IP": "203.0.113.9"}}
LAB = {"base_url": "http://192.168.1.2:8080", "environ_overrides": {"REMOTE_ADDR": LAB_IP}}


def client():
    # A fresh client per visitor, and the rate limits cleared, so no case
    # here is refused by a limit instead of by the rule under test.
    with appmod._report_rate_lock:
        appmod._report_rate_hits.clear()
    return appmod.app.test_client()


def is_form(res):
    return 'name="problem_type"' in res.get_data(as_text=True)


def is_gate(res):
    text = res.get_data(as_text=True)
    return 'name="qr_code"' in text and 'name="problem_type"' not in text


def reports(description):
    return q("SELECT computer_hostname, report_source FROM problem_reports WHERE description = ?",
             (description,))


def report_body(description, hostname="LAB-PC-01", **extra):
    body = {
        "computer_hostname": hostname, "problem_type": "keyboard",
        "description": description, "reporter_role": "student",
        "reporter_name": "Test Student", "reporter_email": "student@lab.example.com", "reporter_student_number": "2021400001",
        "reporter_program": "BSIT", "reporter_section": "4A", "reporter_year_level": "4",
        "form_started": appmod.sign_form_timestamp(time.time() - 30),
    }
    body.update(extra)
    return body


def forge_heartbeat(hostname, address):
    """What anyone on the LAN can send: the agent API takes no credentials."""
    res = appmod.app.test_client().post(
        "/api/agent/heartbeat", base_url="http://192.168.1.2:8080",
        environ_overrides={"REMOTE_ADDR": "192.168.1.77"},
        json={"computer_hostname": hostname, "ip_address": address})
    return res.status_code


# ---------------------------------------------------------------------------
print("\n=== 1. A forged loopback address does not open the form to the internet ===")
check("the forged heartbeat is accepted, as any agent's would be",
      forge_heartbeat("LAB-FAKE", "127.0.0.1") == 200)
check("a computer row now claims 127.0.0.1",
      bool(q("SELECT 1 FROM computers WHERE ip_address = '127.0.0.1'")))

visitor = client()
res = visitor.get("/report", **INTERNET)
check("an internet visitor with no sticker gets the code gate", is_gate(res))
check("and not the form", not is_form(res))
check("and the forged computer is not preselected", "LAB-FAKE" not in res.get_data(as_text=True))

res = visitor.post("/report", data=report_body("Internet visitor with no proof at all.", "LAB-FAKE"), **INTERNET)
check("an internet report with no sticker or code is refused",
      "error=" in (res.headers.get("Location") or ""))
check("and asked for the sticker or the code",
      "code" in (res.headers.get("Location") or "").lower())
check("and nothing is stored", not reports("Internet visitor with no proof at all."))


# ---------------------------------------------------------------------------
print("\n=== 2. Internet visitors still report with the sticker or the code ===")
token = appmod.sign_computer("LAB-PC-01")
visitor = client()
res = visitor.get(f"/report?computer=LAB-PC-01&t={token}", **INTERNET)
check("a scanned sticker opens the form over the internet", is_form(res))
res = visitor.post("/report", data=report_body(
    "Sticker scanned from a phone on data.", qr_token=token, qr_hostname="LAB-PC-01"), **INTERNET)
check("a sticker report from the internet is accepted",
      "success=" in (res.headers.get("Location") or ""))
stored = reports("Sticker scanned from a phone on data.")
check("and stored as a sticker report", len(stored) == 1 and stored[0]["report_source"] == "sticker")

code = appmod.computer_short_code("LAB-PC-02")
visitor = client()
res = visitor.post("/report/access", data={"qr_code": code}, **INTERNET)
location = res.headers.get("Location") or ""
check("a valid typed code on the internet unlocks the form", res.status_code == 302 and "t=" in location)
res = visitor.get(location, **INTERNET)
check("the unlocked link serves the form", is_form(res))
check("with that computer preselected", '<option value="LAB-PC-02" selected>' in res.get_data(as_text=True))
res = visitor.post("/report", data=report_body(
    "Typed the code from the sticker.", "LAB-PC-02",
    qr_token=appmod.sign_computer("LAB-PC-02"), qr_hostname="LAB-PC-02", via_code="1"), **INTERNET)
stored = reports("Typed the code from the sticker.")
check("a report through the typed code is accepted and stored as 'code'",
      len(stored) == 1 and stored[0]["report_source"] == "code")

visitor = client()
res = visitor.post("/report", data=report_body(
    "Code typed straight into the report.", "LAB-PC-02", qr_code=code), **INTERNET)
stored = reports("Code typed straight into the report.")
check("a code posted with the report itself is accepted", len(stored) == 1
      and stored[0]["report_source"] == "code")

visitor = client()
res = visitor.post("/report/access", data={"qr_code": "ZZZZZZ"}, **INTERNET)
check("a wrong code on the internet is refused with the gate", is_gate(res))


# ---------------------------------------------------------------------------
print("\n=== 3. A real lab PC on the LAN still gets straight in ===")
pc = client()
res = pc.get("/report", **LAB)
check("a registered lab PC gets the form with no sticker or code", is_form(res))
check("with itself preselected", '<option value="LAB-PC-01" selected>' in res.get_data(as_text=True))
res = pc.post("/report", data=report_body("Reported from the lab PC shortcut."), **LAB)
check("its report is accepted", "success=" in (res.headers.get("Location") or ""))
stored = reports("Reported from the lab PC shortcut.")
check("and stored as a LAN report", len(stored) == 1 and stored[0]["report_source"] == "lan")

stranger = client()
res = stranger.get("/report", base_url="http://192.168.1.2:8080",
                   environ_overrides={"REMOTE_ADDR": "10.20.30.99"})
check("an unregistered LAN address still gets the gate", is_gate(res))


# ---------------------------------------------------------------------------
print("\n=== 4. The shortcut is withdrawn wherever the address cannot be the visitor's ===")
# The tunnel with PUBLIC_REPORT_HOST missing or wrong: the Host check cannot
# fire, but everything still arrives on loopback.
visitor = client()
res = visitor.get("/report", base_url="http://192.168.1.2:8080",
                  environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
check("loopback with no public host configured still gets the gate", is_gate(res))
res = visitor.post("/report", data=report_body("Loopback with the public host unset.", "LAB-FAKE"),
                   base_url="http://192.168.1.2:8080", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
check("and its report is refused", not reports("Loopback with the public host unset."))

forge_heartbeat("LAB-FAKE-6", "::1")
visitor = client()
res = visitor.get("/report", base_url="http://192.168.1.2:8080", environ_overrides={"REMOTE_ADDR": "::1"})
check("IPv6 loopback is loopback too", is_gate(res))

forge_heartbeat("LAB-FAKE-M", "::ffff:127.0.0.1")
visitor = client()
res = visitor.get("/report", base_url="http://192.168.1.2:8080",
                  environ_overrides={"REMOTE_ADDR": "::ffff:127.0.0.1"})
check("IPv4 loopback wrapped in IPv6 is loopback too", is_gate(res))

# cloudflared running on ANOTHER lab machine: tunnelled requests then arrive
# from that machine's LAN address, which may well be a registered computer.
visitor = client()
res = visitor.get("/report", base_url=f"https://{PUBLIC}", environ_overrides={"REMOTE_ADDR": LAB_IP})
check("on the public host even a registered lab address gets the gate", is_gate(res))
res = visitor.post("/report", data=report_body("Public host from a lab address."),
                   base_url=f"https://{PUBLIC}", environ_overrides={"REMOTE_ADDR": LAB_IP})
check("and its report without proof is refused", not reports("Public host from a lab address."))


print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)

os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

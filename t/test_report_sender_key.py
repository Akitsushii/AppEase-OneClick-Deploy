"""Whom the report form's limits count against: through the tunnel each
visitor by CF-Connecting-IP; on the LAN, where anyone can send that header,
one machine is one sender.
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

work = tempfile.mkdtemp(prefix="appease_sender_")
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
os.environ["PUBLIC_REPORT_HOST"] = PUBLIC
os.environ["REPORT_QR_SECRET"] = "d" * 64
os.environ.pop("DEMO_MODE_ENABLED", None)
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))

import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()
import app as appmod  # noqa: E402
appmod.app.config["TESTING"] = True

conn = sqlite3.connect(database.DB_PATH)
conn.execute("INSERT INTO computers (hostname, ip_address, status) VALUES ('LAB-PC-01', ?, 'online')", (LAB_IP,))
for n in range(2, 9):
    conn.execute("INSERT INTO computers (hostname, ip_address, status) VALUES (?, ?, 'online')",
                 (f"LAB-PC-{n:02d}", f"10.20.30.{40 + n}"))
conn.commit()
conn.close()

LAN = {"base_url": "http://192.168.1.2:8080", "environ_overrides": {"REMOTE_ADDR": LAB_IP}}
TUNNEL = {"base_url": f"https://{PUBLIC}", "environ_overrides": {"REMOTE_ADDR": "127.0.0.1"}}
LIMIT = appmod.REPORT_CODE_ATTEMPT_MAX


def fresh():
    with appmod._report_rate_lock:
        appmod._report_rate_hits.clear()
    return appmod.app.test_client()


def guess(client, where, sender=None):
    headers = {"CF-Connecting-IP": sender} if sender else {}
    return client.post("/report/access", data={"qr_code": "ZZZZZZ"}, headers=headers, **where).status_code


def reports_saved(prefix):
    conn = sqlite3.connect(database.DB_PATH)
    try:
        return conn.execute("SELECT COUNT(*) FROM problem_reports WHERE description LIKE ?",
                            (prefix + "%",)).fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
print("\n=== 1. On the LAN, the header cannot buy more code guesses ===")
client = fresh()
codes = [guess(client, LAN, f"198.51.100.{i}") for i in range(LIMIT + 3)]
check(f"the first {LIMIT} guesses are answered", all(c == 200 for c in codes[:LIMIT]))
check("a different header on every guess is still cut off after the limit",
      all(c == 429 for c in codes[LIMIT:]))

client = fresh()
codes = [guess(client, LAN) for i in range(LIMIT + 1)]
check("with no header, the LAN limit is unchanged", codes[-1] == 429 and codes[LIMIT - 1] == 200)


# ---------------------------------------------------------------------------
print("\n=== 2. On the LAN, the header cannot buy more reports ===")
fresh()
client = appmod.app.test_client()
for i in range(8):
    client.post("/report", headers={"CF-Connecting-IP": f"198.51.100.{i}"}, data={
        "computer_hostname": f"LAB-PC-0{(i % 6) + 1}", "problem_type": "keyboard",
        "description": f"Spread from one lab machine, number {i}.", "reporter_role": "student",
        "reporter_name": "Test Student", "reporter_email": "student@lab.example.com", "reporter_student_number": "2021400001",
        "reporter_program": "BSIT", "reporter_section": "4A", "reporter_year_level": "4",
        "form_started": appmod.sign_form_timestamp(time.time() - 30),
    }, **LAN)
check("one LAN machine is held to the per-sender report limit whatever header it sends",
      reports_saved("Spread from one lab machine") == appmod.REPORT_RATE_MAX_PER_IP)


# ---------------------------------------------------------------------------
print("\n=== 3. Through the tunnel, each visitor is still counted on their own ===")
client = fresh()
first = [guess(client, TUNNEL, "203.0.113.10") for _ in range(LIMIT)]
check(f"one visitor gets {LIMIT} guesses", all(c == 200 for c in first))
check("and is then cut off", guess(client, TUNNEL, "203.0.113.10") == 429)
check("while a different visitor is not", guess(client, TUNNEL, "203.0.113.20") == 200)
check("a proxy chain is counted by its first hop",
      guess(client, TUNNEL, "203.0.113.10, 70.41.3.18") == 429)

with appmod.app.test_request_context("/report", headers={"CF-Connecting-IP": "203.0.113.7"}, **TUNNEL):
    check("through the tunnel the sender is the header", appmod.report_client_key() == "203.0.113.7")
with appmod.app.test_request_context("/report", headers={"CF-Connecting-IP": "203.0.113.7"}, **LAN):
    check("on the LAN the sender is the real address, header or not", appmod.report_client_key() == LAB_IP)
with appmod.app.test_request_context("/report", **LAN):
    check("on the LAN with no header, the real address", appmod.report_client_key() == LAB_IP)


print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)

os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

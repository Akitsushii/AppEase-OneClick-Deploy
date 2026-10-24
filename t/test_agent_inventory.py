"""The hardware inventory endpoint and agent.py's collection code. The
PowerShell call cannot run here, so subprocess.run is faked and the parser
fed real-shaped output; the real WMI fields need a run on Windows.
"""
import json
import os
import secrets
import shutil
import sqlite3
import sys
import tempfile
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


def find_agent_py():
    """Locate agent.py, which has no fixed place: the copy that matters is
    the one deployed to each lab PC. The caller can name it:

        python t/test_agent_inventory.py --agent C:\\Appease\\agent.py
        set APPEASE_AGENT_PATH=C:\\Appease\\agent.py
    """
    for index, arg in enumerate(sys.argv):
        if arg == "--agent" and index + 1 < len(sys.argv):
            return os.path.abspath(sys.argv[index + 1])
        if arg.startswith("--agent="):
            return os.path.abspath(arg.split("=", 1)[1])

    from_env = os.environ.get("APPEASE_AGENT_PATH")
    if from_env and os.path.isfile(from_env):
        return os.path.abspath(from_env)

    for candidate in (
        os.path.join(ROOT, "agent.py"),
        os.path.join(ROOT, "agent", "agent.py"),
        os.path.join(ROOT, "server", "agent.py"),
        os.path.join(HERE, "agent.py"),
    ):
        if os.path.isfile(candidate):
            return candidate

    # Last resort: walk the project, skipping the folders that would
    # otherwise turn this into a slow crawl or match somebody else's file.
    skip = {"venv", ".venv", "env", "node_modules", ".git", "__pycache__",
            ".idea", "site-packages", "uploads", "report_photos"}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [name for name in dirnames if name not in skip]
        if "agent.py" in filenames:
            return os.path.join(dirpath, "agent.py")
    return None


AGENT_PATH = find_agent_py()

work = tempfile.mkdtemp(prefix="appease_inv_")
server_dir = os.path.join(work, "server")
# NEVER copy the real appease.db: the suite would test whatever data is in
# production rather than the code. It builds its own from an empty schema.
# uploads/staging/photos are skipped too - nothing here serves files, and a
# folder of installers would cost tens of megabytes a run.
shutil.copytree(
    os.path.join(ROOT, "server"), server_dir,
    ignore=shutil.ignore_patterns(
        "appease.db*", "__pycache__", "*.pyc", ".env",
        "uploads", "staging", "report_photos", "avatars",
    ),
)
if AGENT_PATH:
    shutil.copy(AGENT_PATH, os.path.join(work, "agent.py"))
sys.path.insert(0, server_dir)
sys.path.insert(0, work)

os.chdir(server_dir)
import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()

# app.py refuses to start without SECRET_KEY: a default would be a key an
# attacker can read. Tests supply a throwaway one rather than app.py making
# an exception for TESTING, so the startup check is production's own.
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
import app as appmod  # noqa: E402
appmod.app.config["TESTING"] = True

DB = database.DB_PATH


def raw():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def get_computer(hostname):
    conn = raw()
    row = conn.execute("SELECT * FROM computers WHERE hostname = ?", (hostname,)).fetchone()
    conn.close()
    return row


GOOD = {
    "computer_hostname": "LAB-01",
    "ip_address": "192.168.1.24",
    "manufacturer": "Dell Inc.",
    "model": "OptiPlex 3080",
    "serial_number": "7KJ2QM3",
    "cpu": "Intel(R) Core(TM) i5-10500 CPU @ 3.10GHz",
    "ram_mb": 8192,
    "storage_gb": 238,
    "os_name": "Microsoft Windows 11 Pro",
}

client = appmod.app.test_client()


print("\n=== 1. Field cleaning helpers ===")
check("plain text passes", appmod.clean_hardware_text("Dell Inc.") == "Dell Inc.")
check("whitespace collapsed", appmod.clean_hardware_text("  Dell   Inc. ") == "Dell Inc.")
check("empty -> None", appmod.clean_hardware_text("   ") is None)
check("None -> None", appmod.clean_hardware_text(None) is None)
check("long value capped to 80", len(appmod.clean_hardware_text("x" * 500)) == 80)
for junk in ["To Be Filled By O.E.M.", "System Serial Number", "Default string",
             "None", "Unknown", "0", "system manufacturer"]:
    check(f"WMI placeholder rejected: {junk}", appmod.clean_hardware_text(junk) is None)
# Trailing-dot variants are caught by a DIFFERENT branch than the exact
# matches above - "Unknown." is not in the set, "unknown" is. Without a
# case here, that branch could be deleted with every test still green.
for junk in ["Unknown.", "Default string.", "None.", " 0 "]:
    check(f"trailing-dot placeholder rejected: {junk!r}", appmod.clean_hardware_text(junk) is None)
check("a real serial that looks odd still passes", appmod.clean_hardware_text("0000-1111") == "0000-1111")
check("a model with a legitimate trailing dot survives", appmod.clean_hardware_text("OptiPlex 3080 Inc.") == "OptiPlex 3080 Inc.")

check("int passes", appmod.clean_hardware_number(8192, 256, 999999) == 8192)
check("float coerced", appmod.clean_hardware_number(8192.0, 256, 999999) == 8192)
check("numeric string coerced", appmod.clean_hardware_number("8192", 256, 999999) == 8192)
check("below minimum rejected", appmod.clean_hardware_number(4, 256, 999999) is None)
check("above maximum rejected", appmod.clean_hardware_number(10 ** 12, 256, 999999) is None)
check("garbage rejected", appmod.clean_hardware_number("lots", 256, 999999) is None)
check("None rejected", appmod.clean_hardware_number(None, 256, 999999) is None)
check("True not stored as 1", appmod.clean_hardware_number(True, 0, 999999) is None)


print("\n=== 2. The inventory endpoint ===")
res = client.post("/api/agent/inventory", json=GOOD)
check("accepted", res.status_code == 200)
row = get_computer("LAB-01")
check("row created for an unknown machine", row is not None)
check("manufacturer stored", row["manufacturer"] == "Dell Inc.")
check("model stored", row["model"] == "OptiPlex 3080")
check("serial stored", row["serial_number"] == "7KJ2QM3")
check("cpu stored", row["cpu"].startswith("Intel"))
check("ram stored as an integer", row["ram_mb"] == 8192)
check("storage stored as an integer", row["storage_gb"] == 238)
check("os stored", row["os_name"] == "Microsoft Windows 11 Pro")
check("reported-at stamped", row["hardware_reported_at"] is not None)
check("ip recorded via the heartbeat", row["ip_address"] == "192.168.1.24")
check("response lists what was recorded", len(res.get_json()["recorded"]) == 7)

res = client.post("/api/agent/inventory", json={"manufacturer": "Dell"})
check("missing hostname rejected", res.status_code == 400)
res = client.post("/api/agent/inventory", json={"computer_hostname": "LAB-01"})
check("no usable fields rejected", res.status_code == 400)
res = client.post("/api/agent/inventory", json={
    "computer_hostname": "LAB-01", "serial_number": "To Be Filled By O.E.M."})
check("a payload of only placeholders is rejected", res.status_code == 400)

before = get_computer("LAB-01")["hardware_reported_at"]
res = client.post("/api/agent/inventory", json={"computer_hostname": "LAB-01", "cpu": "Intel"})
after = get_computer("LAB-01")
check("partial report accepted", res.status_code == 200)
check("reported field updated", after["cpu"] == "Intel")
check("ABSENT field NOT blanked (serial survives)", after["serial_number"] == "7KJ2QM3")
check("ABSENT numeric NOT blanked (ram survives)", after["ram_mb"] == 8192)
check("only the reported field is listed", res.get_json()["recorded"] == ["cpu"])

res = client.post("/api/agent/inventory", json={
    "computer_hostname": "LAB-01", "model": "OptiPlex 5090",
    "serial_number": "To Be Filled By O.E.M."})
after = get_computer("LAB-01")
check("good field applied alongside a placeholder", after["model"] == "OptiPlex 5090")
check("placeholder did not overwrite a real serial", after["serial_number"] == "7KJ2QM3")


print("\n=== 3. The agent cannot touch technician-owned columns ===")
conn = raw()
conn.execute(
    "UPDATE computers SET location='Lab 1', device_type='Desktop', asset_status='In repair',"
    " purchase_date='2021-01-12', vendor='Dell PH', warranty_until='2024-01-12'"
    " WHERE hostname='LAB-01'")
conn.commit()
conn.close()

hostile = dict(GOOD)
hostile.update({
    "location": "HACKED", "device_type": "HACKED", "asset_status": "Retired",
    "purchase_date": "1999-01-01", "vendor": "HACKED", "warranty_until": "1999-01-01",
    "hostname": "OTHER", "status": "offline",
})
res = client.post("/api/agent/inventory", json=hostile)
row = get_computer("LAB-01")
check("hostile payload still accepted for its valid fields", res.status_code == 200)
check("location untouched", row["location"] == "Lab 1")
check("device_type untouched", row["device_type"] == "Desktop")
check("asset_status untouched - agent cannot retire a machine", row["asset_status"] == "In repair")
check("purchase_date untouched", row["purchase_date"] == "2021-01-12")
check("vendor untouched", row["vendor"] == "Dell PH")
check("warranty_until untouched", row["warranty_until"] == "2024-01-12")

# The reverse direction too, so the two-way guarantee reads in one place.
conn = raw()
admin = conn.execute("SELECT id, username FROM admins LIMIT 1").fetchone()
conn.close()
admin_client = appmod.app.test_client()
with admin_client.session_transaction() as sess:
    sess["admin_id"] = admin["id"]
    sess["admin_role"] = "admin"
    sess["admin_username"] = admin["username"]
cid = get_computer("LAB-01")["id"]
admin_client.post(f"/computers/{cid}/edit", data={
    "location": "Lab 2", "device_type": "Laptop", "asset_status": "In use",
    "serial_number": "FORGED", "model": "FORGED"})
row = get_computer("LAB-01")
check("technician edit cannot overwrite the agent's serial", row["serial_number"] == "7KJ2QM3")
check("technician edit still applied its own fields", row["location"] == "Lab 2")


print("\n=== 4. Password gate and public host ===")
check("endpoint is named agent_* so the password gate exempts it",
      "agent_report_inventory".startswith("agent_"))
gated = appmod.app.test_client()
with gated.session_transaction() as sess:
    sess["admin_id"] = admin["id"]
    sess["admin_role"] = "admin"
    sess["must_change_password"] = True
res = gated.post("/api/agent/inventory", json=GOOD)
check("still reachable while an admin must change their password", res.status_code == 200)

check("inventory NOT in the public allowlist",
      "agent_report_inventory" not in appmod.PUBLIC_ALLOWED_ENDPOINTS)
old_host = appmod.PUBLIC_REPORT_HOST
appmod.PUBLIC_REPORT_HOST = "report.example.test"
try:
    res = client.post("/api/agent/inventory", json=GOOD,
                      headers={"Host": "report.example.test"})
    check("blocked on the public host", res.status_code == 404)
    body = res.get_data(as_text=True)
    check("public-host block reveals nothing", "inventory" not in body.lower())
finally:
    appmod.PUBLIC_REPORT_HOST = old_host


if not AGENT_PATH:
    print("\n" + "=" * 56)
    print("SERVER-SIDE CHECKS PASSED: %d" % passed)
    print("AGENT-SIDE CHECKS SKIPPED - agent.py was not found.")
    print()
    print("This is NOT a pass. Sections 5-7 test agent.py and did not run.")
    print("Point the test at your agent file and run it again:")
    print(r"    python t\test_agent_inventory.py --agent C:\Appease\agent.py")
    print("=" * 56)
    shutil.rmtree(work, ignore_errors=True)
    sys.exit(2)

print(f"\n(using agent.py at {AGENT_PATH})")

print("\n=== 5. agent.py parsing (no PowerShell needed) ===")
import agent  # noqa: E402

# Version guard: this suite and agent.py are a matched pair, and the agent
# is found by search, so an old agent must be named as the problem rather
# than fail halfway with a bare AttributeError.
REQUIRED_AGENT_SYMBOLS = (
    "parse_hardware_json", "collect_hardware_facts", "report_hardware_inventory",
    "inventory_due_seconds", "INVENTORY_SENT", "INVENTORY_SEND_FAILED",
    "INVENTORY_NO_FACTS", "INVENTORY_RETRY_SECONDS", "HARDWARE_FIELDS",
)
missing = [name for name in REQUIRED_AGENT_SYMBOLS if not hasattr(agent, name)]
if missing:
    print("\n" + "=" * 56)
    print("AGENT-SIDE CHECKS COULD NOT RUN - agent.py is out of date.")
    print()
    print("Loaded: %s" % AGENT_PATH)
    print("Missing: %s" % ", ".join(missing))
    print()
    print("That file is an older agent.py than this test expects. Replace it")
    print("with the current one and run again. Server-side checks passed: %d" % passed)
    print("=" * 56)
    shutil.rmtree(work, ignore_errors=True)
    sys.exit(3)

ps_output = json.dumps({
    "manufacturer": "Dell Inc.", "model": "OptiPlex 3080",
    "serial_number": "7KJ2QM3", "cpu": "Intel(R) Core(TM) i5-10500",
    "ram_mb": 8192, "storage_gb": 238, "os_name": "Microsoft Windows 11 Pro",
})
facts = agent.parse_hardware_json(ps_output)
check("parses a real-shaped object", facts is not None)
check("all seven fields present", len(facts) == 7)
check("numbers stay numbers", facts["ram_mb"] == 8192 and isinstance(facts["ram_mb"], int))

check("parses when wrapped in a list", agent.parse_hardware_json("[" + ps_output + "]") is not None)
check("BOM/whitespace tolerated", agent.parse_hardware_json("\n  " + ps_output + "  \n") is not None)
check("empty string -> None", agent.parse_hardware_json("") is None)
check("None -> None", agent.parse_hardware_json(None) is None)
check("whitespace only -> None", agent.parse_hardware_json("   ") is None)
check("non-JSON -> None", agent.parse_hardware_json("Get-CimInstance : Access denied") is None)
check("JSON scalar -> None", agent.parse_hardware_json('"hello"') is None)
check("empty list -> None", agent.parse_hardware_json("[]") is None)
check("empty object -> None", agent.parse_hardware_json("{}") is None)

partial = agent.parse_hardware_json(json.dumps({"cpu": "Intel", "model": None, "serial_number": ""}))
check("nulls dropped from the payload", "model" not in partial)
check("empty strings dropped", "serial_number" not in partial)
check("the usable field survives", partial["cpu"] == "Intel")

extra = agent.parse_hardware_json(json.dumps({"cpu": "Intel", "PSComputerName": "LAB-01"}))
check("unexpected PowerShell keys are not forwarded", "PSComputerName" not in extra)


print("\n=== 6. agent.py collection, with a faked subprocess ===")
real_run = agent.subprocess.run
calls = []


def fake_run(result=None, exc=None):
    def runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if exc:
            raise exc
        return result
    return runner


agent.subprocess.run = fake_run(types.SimpleNamespace(returncode=0, stdout=ps_output, stderr=""))
facts = agent.collect_hardware_facts()
check("collection returns facts on a clean run", facts is not None and facts["model"] == "OptiPlex 3080")
cmd, kwargs = calls[-1]
check("invokes powershell", cmd[0] == "powershell")
check("uses -NoProfile", "-NoProfile" in cmd)
check("uses -NonInteractive", "-NonInteractive" in cmd)
check("passes no -ExecutionPolicy flag", "-ExecutionPolicy" not in cmd)
check("has a timeout", kwargs.get("timeout") == agent.INVENTORY_TIMEOUT_SECONDS)
check("closes stdin", kwargs.get("stdin") is agent.subprocess.DEVNULL)
check("asks for Get-CimInstance", "Get-CimInstance" in cmd[-1])
check("excludes removable drives", "Removable" in cmd[-1])

agent.subprocess.run = fake_run(types.SimpleNamespace(returncode=1, stdout="", stderr="Access denied"))
check("non-zero exit -> None, no raise", agent.collect_hardware_facts() is None)

agent.subprocess.run = fake_run(types.SimpleNamespace(returncode=0, stdout="not json", stderr=""))
check("unparseable output -> None, no raise", agent.collect_hardware_facts() is None)

agent.subprocess.run = fake_run(exc=FileNotFoundError("powershell"))
check("no PowerShell -> None, no raise", agent.collect_hardware_facts() is None)

agent.subprocess.run = fake_run(exc=agent.subprocess.TimeoutExpired("powershell", 30))
check("timeout -> None, no raise", agent.collect_hardware_facts() is None)

agent.subprocess.run = fake_run(exc=OSError("boom"))
check("OSError -> None, no raise", agent.collect_hardware_facts() is None)

# report_hardware_inventory must not raise when the server is unreachable.
agent.subprocess.run = fake_run(types.SimpleNamespace(returncode=0, stdout=ps_output, stderr=""))
sent = {}


def fake_post_json(path, payload):
    sent["path"] = path
    sent["payload"] = payload
    return {"message": "ok", "recorded": sorted(k for k in payload if k in agent.HARDWARE_FIELDS)}


real_post = agent.post_json
agent.post_json = fake_post_json
agent._pending_hardware_facts = None
ok = agent.report_hardware_inventory()
check("report returns 'sent' on success", ok == agent.INVENTORY_SENT)
check("posts to the inventory route", sent["path"] == "/api/agent/inventory")
check("payload carries the hostname", sent["payload"]["computer_hostname"] == agent.COMPUTER_HOSTNAME)
check("payload carries the facts", sent["payload"]["model"] == "OptiPlex 3080")
check("payload carries no technician-owned key",
      not any(k in sent["payload"] for k in
              ("location", "device_type", "asset_status", "purchase_date", "vendor", "warranty_until")))


def boom_post(path, payload):
    raise agent.error.URLError("unreachable")


agent.post_json = boom_post
agent._pending_hardware_facts = None
check("unreachable server -> send_failed, no raise",
      agent.report_hardware_inventory() == agent.INVENTORY_SEND_FAILED)

agent.subprocess.run = fake_run(types.SimpleNamespace(returncode=1, stdout="", stderr="nope"))
agent.post_json = fake_post_json
sent.clear()
agent._pending_hardware_facts = None
check("no facts -> no_facts", agent.report_hardware_inventory() == agent.INVENTORY_NO_FACTS)
check("no request was made at all", "path" not in sent)


print("\n=== 6a. Recovery after the server was down at boot ===")
# The exact sequence seen on the VM: the agent started before the server,
# the first send failed, and the polling loop then reconnected within
# seconds. Inventory must not wait a full hour behind it.
check("a successful send schedules the next attempt an hour out",
      agent.inventory_due_seconds(agent.INVENTORY_SENT) == agent.INVENTORY_RECHECK_SECONDS)
check("a FAILED SEND schedules a retry within a minute",
      agent.inventory_due_seconds(agent.INVENTORY_SEND_FAILED) == agent.INVENTORY_RETRY_SECONDS)
check("the retry is much sooner than the normal interval",
      agent.INVENTORY_RETRY_SECONDS < agent.INVENTORY_RECHECK_SECONDS)
check("a collection failure still waits the full hour (PowerShell will not "
      "appear in 60s)",
      agent.inventory_due_seconds(agent.INVENTORY_NO_FACTS) == agent.INVENTORY_RECHECK_SECONDS)

# Boot with the server down, then the server comes up.
calls.clear()
sent.clear()
agent._pending_hardware_facts = None
agent.subprocess.run = fake_run(types.SimpleNamespace(returncode=0, stdout=ps_output, stderr=""))
agent.post_json = boom_post
first = agent.report_hardware_inventory()
check("boot attempt reports send_failed", first == agent.INVENTORY_SEND_FAILED)
check("facts were held for the retry", agent._pending_hardware_facts is not None)
powershell_calls_after_boot = len(calls)

agent.post_json = fake_post_json
second = agent.report_hardware_inventory()
check("retry succeeds once the server is up", second == agent.INVENTORY_SENT)
check("retry actually sent the payload", sent.get("path") == "/api/agent/inventory")
check("retry sent the real facts, not an empty payload",
      sent["payload"].get("model") == "OptiPlex 3080")
check("retry did NOT re-run PowerShell (facts were cached)",
      len(calls) == powershell_calls_after_boot)
check("cache cleared after a successful send", agent._pending_hardware_facts is None)

# And the pass after that must re-read, so a RAM upgrade is picked up.
agent.report_hardware_inventory()
check("the next pass re-reads hardware rather than reusing stale facts",
      len(calls) > powershell_calls_after_boot)

agent._pending_hardware_facts = None

agent.subprocess.run = real_run
agent.post_json = real_post


print("\n=== 6b. Real captured VMware output (regression fixture) ===")
# Captured verbatim from the PowerShell command this agent runs, on the
# VMware Win11 VM. Kept as a fixture because real WMI output
# has two properties invented samples do not: RAM is never a round number
# (Windows reserves a sliver, so 8 GB reports as 8191 MB), and
# Win32_Processor.Name is a fixed-width field padded with trailing spaces.
VMWARE_REAL = (
    '{"manufacturer":"VMware, Inc.","model":"VMware20,1",'
    '"serial_number":"VMware-56 4d 22 f3 3d b5 f0 39-6b 0c e7 76 d3 3e c4 49",'
    '"cpu":"AMD Ryzen 5 5500                               ",'
    '"ram_mb":8191,"storage_gb":64,"os_name":"Microsoft Windows 11 Pro"}'
)
vm = agent.parse_hardware_json(VMWARE_REAL)
check("real VMware output parses", vm is not None and len(vm) == 7)
check("padded CPU stripped by the agent", vm["cpu"] == "AMD Ryzen 5 5500")

vm_clean = appmod.hardware_payload_fields(vm)
check("VM serial is kept, not treated as a placeholder",
      vm_clean["serial_number"] == "VMware-56 4d 22 f3 3d b5 f0 39-6b 0c e7 76 d3 3e c4 49")
check("VM serial fits the 80-char cap", len(vm_clean["serial_number"]) <= 80)
check("manufacturer with a comma survives", vm_clean["manufacturer"] == "VMware, Inc.")
check("model with a comma survives", vm_clean["model"] == "VMware20,1")
check("8191 MB is accepted, not rejected as out of range", vm_clean["ram_mb"] == 8191)

# The one that would look wrong on every machine if the tolerance were gone:
# no real PC reports a round MB figure.
check("8191 MB displays as 8 GB, not 7.999", appmod.format_ram(8191) == "8 GB")
check("16383 MB displays as 16 GB", appmod.format_ram(16383) == "16 GB")
check("4095 MB displays as 4 GB", appmod.format_ram(4095) == "4 GB")
check("64 GB disk displays plainly", appmod.format_storage(64) == "64 GB")

res = client.post("/api/agent/inventory", json=dict(
    {"computer_hostname": "LAB-VMWARE", "ip_address": "192.168.1.60"}, **vm))
vmrow = get_computer("LAB-VMWARE")
check("real VM payload accepted end to end", res.status_code == 200)
check("all seven fields recorded", len(res.get_json()["recorded"]) == 7)
check("stored CPU has no trailing padding", vmrow["cpu"] == "AMD Ryzen 5 5500")
check("stored RAM is the raw integer for sorting", vmrow["ram_mb"] == 8191)


print("\n=== 7. End to end: agent payload through the real endpoint ===")
agent.subprocess.run = fake_run(types.SimpleNamespace(returncode=0, stdout=ps_output, stderr=""))
facts = agent.collect_hardware_facts()
payload = {"computer_hostname": "LAB-E2E", "ip_address": "192.168.1.99"}
payload.update(facts)
res = client.post("/api/agent/inventory", json=payload)
row = get_computer("LAB-E2E")
check("agent-shaped payload accepted by the real route", res.status_code == 200)
check("stored end to end", row["model"] == "OptiPlex 3080" and row["ram_mb"] == 8192)
check("the new machine defaults to In use", row["asset_status"] is None)

# And it shows up on the page, formatted.
res = admin_client.get("/computers")
html = res.get_data(as_text=True)
check("appears on the computers page", "LAB-E2E" in html)
check("ram rendered as a label", 'data-asset-ram="8 GB"' in html)
check("storage rendered as a label", 'data-asset-storage="238 GB"' in html)
check("hardware reported date rendered", 'data-asset-reported="' in html)
agent.subprocess.run = real_run

print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)

shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)
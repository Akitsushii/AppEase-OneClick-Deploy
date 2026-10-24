"""Agent identity: two machines with one hostname stay two computers, and
neither can take the other's jobs. Every check reads the DATABASE: a poll
can answer "no job" while the job row has quietly gone in_progress.

Needs the hardware test: two real machines (here one process switches ID),
and the real %ProgramData% permissions on a lab PC.
"""
import os
import secrets
import shutil
import sqlite3
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
    print("IDENTITY CHECKS COULD NOT RUN - agent/agent.py was not found.")
    sys.exit(2)

work = tempfile.mkdtemp(prefix="appease_ident_")
server_dir = os.path.join(work, "server")
# Never the real database or secrets - see test_agent_inventory.py.
shutil.copytree(
    os.path.join(ROOT, "server"), server_dir,
    ignore=shutil.ignore_patterns(
        "appease.db*", "__pycache__", "*.pyc", ".env",
        "uploads", "staging", "report_photos", "avatars",
    ),
)
shutil.copy(AGENT_PATH, os.path.join(work, "agent.py"))
sys.path.insert(0, server_dir)
sys.path.insert(0, work)

os.chdir(server_dir)
import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()

os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
import app as appmod  # noqa: E402
appmod.app.config["TESTING"] = True
client = appmod.app.test_client()

import agent  # noqa: E402

DB = database.DB_PATH


def raw():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def rows(sql, args=()):
    conn = raw()
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def one(sql, args=()):
    found = rows(sql, args)
    return found[0] if found else None


def computer_by_id(agent_id):
    return one("SELECT * FROM computers WHERE agent_id = ?", (agent_id,))


def computer_named(name):
    return one("SELECT * FROM computers WHERE hostname = ?", (name,))


def job(job_id):
    return one("SELECT * FROM jobs WHERE id = ?", (job_id,))


def body(host, agent_id=None, **fields):
    data = {"computer_hostname": host}
    if agent_id is not None:
        data["agent_id"] = agent_id
    data.update(fields)
    return data


def poll(host, agent_id=None, ip=None):
    res = client.post("/api/agent/poll", json=body(host, agent_id, ip_address=ip))
    got = res.get_json() or {}
    return res.status_code, (got.get("job") or {}).get("id")


def report(job_id, host, agent_id=None, status="completed"):
    return client.post(
        f"/api/agent/jobs/{job_id}/report",
        json=body(host, agent_id, status=status, exit_code=0),
    ).status_code


conn = raw()
SOFTWARE_ID = conn.execute(
    "INSERT INTO software (name, version, type, install_command) "
    "VALUES ('Probe', '1.0', 'chocolatey', 'choco install probe -y')"
).lastrowid
conn.commit()
conn.close()


def queue(hostname, command=None):
    conn = raw()
    if command:
        software_id = conn.execute(
            "INSERT INTO software (name, version, type, install_command) "
            "VALUES ('Probe', '1.0', 'local', ?)", (command,)
        ).lastrowid
    else:
        software_id = SOFTWARE_ID
    job_id = conn.execute(
        "INSERT INTO jobs (computer_hostname, software_id, status, software_name, software_version) "
        "VALUES (?, ?, 'pending', 'Probe', '1.0')",
        (hostname, software_id),
    ).lastrowid
    conn.commit()
    conn.close()
    return job_id


def notifications_mentioning(text):
    return [r["message"] for r in rows("SELECT message FROM notifications") if text in r["message"]]


# ---------------------------------------------------------------------------
print("\n=== 1. Same hostname, different IDs: two computers ===")
A = secrets.token_hex(16)
B = secrets.token_hex(16)
check("first agent's poll accepted", poll("LAB-DUP", A, "10.0.0.1")[0] == 200)
check("second agent's poll accepted", poll("LAB-DUP", B, "10.0.0.2")[0] == 200)

dup_rows = rows("SELECT * FROM computers WHERE hostname LIKE 'LAB-DUP%'")
check("two rows, not one", len(dup_rows) == 2)
row_a, row_b = computer_by_id(A), computer_by_id(B)
check("each ID owns its own row", row_a is not None and row_b is not None and row_a["id"] != row_b["id"])
check("the first keeps the plain name", row_a is not None and row_a["hostname"] == "LAB-DUP")
check("the second is registered as LAB-DUP-2", row_b is not None and row_b["hostname"] == "LAB-DUP-2")
check("each row keeps its own address",
      row_a is not None and row_b is not None
      and row_a["ip_address"] == "10.0.0.1" and row_b["ip_address"] == "10.0.0.2")
check("the admin is told why the second one has a suffix",
      any("LAB-DUP" in m and "already uses" in m for m in notifications_mentioning("LAB-DUP-2")))

# Both keep polling, in both orders. Nothing merges, nothing renames.
for _ in range(2):
    poll("LAB-DUP", B, "10.0.0.2")
    poll("LAB-DUP", A, "10.0.0.1")
check("repeated polls still two rows", len(rows("SELECT id FROM computers WHERE hostname LIKE 'LAB-DUP%'")) == 2)
check("the suffixed row stays LAB-DUP-2", computer_by_id(B)["hostname"] == "LAB-DUP-2")
check("addresses never cross over",
      computer_by_id(A)["ip_address"] == "10.0.0.1" and computer_by_id(B)["ip_address"] == "10.0.0.2")
check("no rename was recorded for a machine that never changed its name",
      not notifications_mentioning("renamed"))


# ---------------------------------------------------------------------------
print("\n=== 2. A job queued for one is never claimed by the other ===")
job_b = queue("LAB-DUP-2")
job_a = queue("LAB-DUP")

status, got = poll("LAB-DUP", A)
check("A is handed A's job", got == job_a)
check("B's job is still pending in the database", job(job_b)["status"] == "pending")
check("B's job was never started", job(job_b)["started_at"] is None)
status, got = poll("LAB-DUP", A)
check("A, with its own job running, is handed nothing", got is None)
check("B's job is STILL pending after A polled again", job(job_b)["status"] == "pending")

status, got = poll("LAB-DUP", B)
check("B is handed B's job", got == job_b)
check("B's job is now in progress", job(job_b)["status"] == "in_progress")

check("A cannot report B's job", report(job_b, "LAB-DUP", A) == 403)
check("B's job untouched by A's report", job(job_b)["status"] == "in_progress")
check("B cannot report A's job", report(job_a, "LAB-DUP", B) == 403)
check("A's job untouched by B's report", job(job_a)["status"] == "in_progress")
check("B reports its own job", report(job_b, "LAB-DUP", B) == 200)
check("B's job stored completed", job(job_b)["status"] == "completed")
check("A reports its own job", report(job_a, "LAB-DUP", A) == 200)
check("A's job stored completed", job(job_a)["status"] == "completed")

# The other agent routes follow the ID too.
client.post("/api/agent/heartbeat", json=body("LAB-DUP", B, ip_address="10.0.0.22"))
check("B's heartbeat moves B's address", computer_by_id(B)["ip_address"] == "10.0.0.22")
check("and leaves A's alone", computer_by_id(A)["ip_address"] == "10.0.0.1")

res = client.post("/api/agent/inventory", json=body("LAB-DUP", B, serial_number="SER-B", cpu="Probe CPU"))
check("B's inventory accepted", res.status_code == 200)
check("B's inventory lands on B's row", computer_by_id(B)["serial_number"] == "SER-B")
check("and not on A's", computer_by_id(A)["serial_number"] is None)

res = client.post("/api/agent/problem-reports",
                  json=body("LAB-DUP", B, problem_type="other", description="probe from B"))
check("B's problem report accepted", res.status_code == 201)
filed = one("SELECT computer_hostname FROM problem_reports WHERE description = 'probe from B'")
check("B's problem report is filed against LAB-DUP-2", filed is not None and filed["computer_hostname"] == "LAB-DUP-2")


# ---------------------------------------------------------------------------
print("\n=== 3. An agent with no ID behaves as today ===")
check("poll without an ID accepted", poll("LAB-OLD", None, "10.0.1.1")[0] == 200)
old = computer_named("LAB-OLD")
check("row created under the reported name", old is not None)
check("with no ID recorded", old is not None and old["agent_id"] is None and old["agent_hostname"] is None)
poll("LAB-OLD", None, "10.0.1.2")
check("a second ID-less machine of the same name shares the row, as before",
      len(rows("SELECT id FROM computers WHERE hostname LIKE 'LAB-OLD%'")) == 1)
check("and the row carries the latest address, as before", computer_named("LAB-OLD")["ip_address"] == "10.0.1.2")

job_old = queue("LAB-OLD")
status, got = poll("LAB-OLD")
check("an ID-less agent is handed its hostname's job", got == job_old)
check("an ID-less agent reports it", report(job_old, "LAB-OLD") == 200)
check("stored completed", job(job_old)["status"] == "completed")
check("ID-less heartbeat accepted",
      client.post("/api/agent/heartbeat", json=body("LAB-OLD", ip_address="10.0.1.3")).status_code == 200)
check("ID-less heartbeat updates the row", computer_named("LAB-OLD")["ip_address"] == "10.0.1.3")
check("ID-less inventory accepted",
      client.post("/api/agent/inventory", json=body("LAB-OLD", cpu="Old CPU")).status_code == 200)
check("ID-less inventory stored", computer_named("LAB-OLD")["cpu"] == "Old CPU")
check("ID-less problem report accepted",
      client.post("/api/agent/problem-reports",
                  json=body("LAB-OLD", problem_type="other", description="probe old")).status_code == 201)

# A damaged ID is treated like no ID, not refused and not stored.
for bad in ("not-hex", "ABC", "g" * 32, 12345, "", "0" * 31):
    status, _ = poll("LAB-OLD", bad)
    check(f"malformed ID {bad!r} still polls", status == 200)
check("malformed IDs never split the row",
      len(rows("SELECT id FROM computers WHERE hostname LIKE 'LAB-OLD%'")) == 1)
check("malformed IDs are never stored", computer_named("LAB-OLD")["agent_id"] is None)


# ---------------------------------------------------------------------------
print("\n=== 4. The row two machines were already sharing ===")
# Two ID-less agents named LAB-MRG sharing one row and one pending job.
poll("LAB-MRG", None, "10.0.2.1")
poll("LAB-MRG", None, "10.0.2.2")
merged_id = computer_named("LAB-MRG")["id"]
job_merged = queue("LAB-MRG")
C = secrets.token_hex(16)
D = secrets.token_hex(16)

status, got = poll("LAB-MRG", C, "10.0.2.1")
check("the first updated agent adopts the existing row", computer_by_id(C) is not None
      and computer_by_id(C)["id"] == merged_id)
check("under its original name", computer_by_id(C)["hostname"] == "LAB-MRG")
check("and receives the job already queued on it", got == job_merged)

status, got = poll("LAB-MRG", D, "10.0.2.2")
check("the second updated agent gets a new row", computer_by_id(D) is not None
      and computer_by_id(D)["id"] != merged_id)
check("named LAB-MRG-2", computer_by_id(D)["hostname"] == "LAB-MRG-2")
check("and is handed nothing", got is None)
check("two rows now", len(rows("SELECT id FROM computers WHERE hostname LIKE 'LAB-MRG%'")) == 2)

job_c = queue("LAB-MRG")
job_d = queue("LAB-MRG-2")
_, got = poll("LAB-MRG", D)
check("D is handed D's job", got == job_d)
check("C's new job is still pending in the database", job(job_c)["status"] == "pending")
check("D cannot report the adopted job", report(job_merged, "LAB-MRG", D) == 403)
check("the adopted job is still C's, in progress", job(job_merged)["status"] == "in_progress")


# ---------------------------------------------------------------------------
print("\n=== 5. A renamed machine keeps its record and history ===")
E = secrets.token_hex(16)
poll("LAB-REN", E)
ren_id = computer_by_id(E)["id"]
done = queue("LAB-REN")
poll("LAB-REN", E)
report(done, "LAB-REN", E)
client.post("/api/agent/problem-reports",
            json=body("LAB-REN", E, problem_type="other", description="probe before rename"))
waiting = queue("LAB-REN")

_, got = poll("LAB-REN-NEW", E)
renamed = computer_by_id(E)
check("same row after the rename", renamed["id"] == ren_id)
check("row carries the new name", renamed["hostname"] == "LAB-REN-NEW")
check("nothing left under the old name", computer_named("LAB-REN") is None)
check("no second row made", len(rows("SELECT id FROM computers WHERE hostname LIKE 'LAB-REN%'")) == 1)
check("the finished job moved with it", job(done)["computer_hostname"] == "LAB-REN-NEW")
check("and is still completed", job(done)["status"] == "completed")
check("the waiting job moved with it", job(waiting)["computer_hostname"] == "LAB-REN-NEW")
check("and is handed to the renamed machine", got == waiting)
check("the renamed machine can report it", report(waiting, "LAB-REN-NEW", E) == 200)
moved = one("SELECT computer_hostname FROM problem_reports WHERE description = 'probe before rename'")
check("the problem report moved with it", moved is not None and moved["computer_hostname"] == "LAB-REN-NEW")
check("no jobs left orphaned under the old name",
      not rows("SELECT id FROM jobs WHERE computer_hostname = 'LAB-REN'"))
check("the admin is told about the rename",
      any("LAB-REN" in m and "LAB-REN-NEW" in m for m in notifications_mentioning("renamed")))

# Renamed onto a name another computer already holds: never merged into it.
poll("LAB-TAKEN", None, "10.0.3.9")
taken_id = computer_named("LAB-TAKEN")["id"]
poll("LAB-TAKEN", E)
check("renamed onto a taken name, it gets a suffix", computer_by_id(E)["hostname"] == "LAB-TAKEN-2")
check("still the same row", computer_by_id(E)["id"] == ren_id)
check("the other computer keeps its row and name",
      computer_named("LAB-TAKEN")["id"] == taken_id and computer_named("LAB-TAKEN")["agent_id"] is None)
check("the other computer's address untouched", computer_named("LAB-TAKEN")["ip_address"] == "10.0.3.9")
check("history followed to the suffixed name", job(done)["computer_hostname"] == "LAB-TAKEN-2")


# ---------------------------------------------------------------------------
print("\n=== 6. Suffixes skip names already in use ===")
poll("LAB-SKP-2", None)            # a real machine that happens to be called that
G = secrets.token_hex(16)
H = secrets.token_hex(16)
poll("LAB-SKP", G)
poll("LAB-SKP", H)
check("the next free suffix is used", computer_by_id(H)["hostname"] == "LAB-SKP-3")
check("the existing LAB-SKP-2 is untouched", computer_named("LAB-SKP-2")["agent_id"] is None)


# ---------------------------------------------------------------------------
print("\n=== 7. The agent's ID file ===")
id_dir = os.path.join(work, "programdata")
id_path = os.path.join(id_dir, "AppEase", "agent_id.txt")
first = agent.load_agent_id(id_path)
check("a first run creates an ID", isinstance(first, str) and len(first) == 32
      and all(c in "0123456789abcdef" for c in first))
check("and keeps it on disk", os.path.isfile(id_path) and open(id_path).read().strip() == first)
check("the next run reads the same ID back", agent.load_agent_id(id_path) == first)
other_path = os.path.join(id_dir, "Other", "agent_id.txt")
check("another machine gets a different ID", agent.load_agent_id(other_path) != first)

damaged = os.path.join(id_dir, "damaged.txt")
with open(damaged, "w") as handle:
    handle.write("not an id\n")
check("a damaged file gives no ID", agent.load_agent_id(damaged) is None)
check("and is not overwritten", open(damaged).read() == "not an id\n")

blocker = os.path.join(id_dir, "blocker")
with open(blocker, "w") as handle:
    handle.write("a file where a folder should be")
try:
    result = agent.load_agent_id(os.path.join(blocker, "AppEase", "agent_id.txt"))
    raised = None
except Exception as exc:        # noqa: BLE001 - any escape is the failure
    result, raised = None, exc
check("an uncreatable folder does not crash", raised is None)
check("and gives no ID", result is None)

folder_path = os.path.join(id_dir, "is-a-folder")
os.makedirs(folder_path)
try:
    result = agent.load_agent_id(folder_path)
    raised = None
except Exception as exc:        # noqa: BLE001
    result, raised = None, exc
check("an unreadable path does not crash", raised is None)
check("and gives no ID", result is None)

# The write itself failing after the file was created.
real_write = agent.os.write


def failing_write(*args, **kwargs):
    raise OSError(28, "No space left on device")


half = os.path.join(id_dir, "half", "agent_id.txt")
agent.os.write = failing_write
try:
    result = agent.load_agent_id(half)
    raised = None
except Exception as exc:        # noqa: BLE001
    result, raised = None, exc
finally:
    agent.os.write = real_write
check("a failed write does not crash", raised is None)
check("and gives no ID", result is None)
check("and leaves no half-written file to fail on every later boot", not os.path.exists(half))
check("so the next boot makes one normally", agent.load_agent_id(half) is not None)


# ---------------------------------------------------------------------------
print("\n=== 8. What the agent sends ===")
sent = []


class Refused(OSError):
    pass


def via_client(path, payload):
    """post_json, answered by the real app through the test client."""
    sent.append((path, dict(payload)))
    res = client.post(path, json=payload)
    if res.status_code >= 400:
        raise Refused(f"HTTP {res.status_code}")
    return res.get_json()


installs = []


def fake_install(command, software_id=None, recorder=None):
    installs.append((agent.AGENT_ID, command))
    return agent.InstallResult(True, 0, None)


real_post = agent.post_json
real_install = agent.run_install_command
real_facts = agent.collect_hardware_facts
agent.post_json = via_client
agent.run_install_command = fake_install
agent.collect_hardware_facts = lambda: {"cpu": "Probe CPU"}


def every_request(machine_id):
    """Drive all five agent requests once, returning what was sent."""
    agent.AGENT_ID = machine_id
    queue(agent.COMPUTER_HOSTNAME if machine_id is None else
          agent_registered(machine_id), "probe-all")
    sent.clear()
    agent.poll_once()                       # poll, then the job report
    agent.send_install_heartbeat()
    agent.report_hardware_inventory()
    agent.submit_problem_report("other", "probe payload")
    return list(sent)


def agent_registered(machine_id):
    row = computer_by_id(machine_id)
    return row["hostname"] if row else agent.COMPUTER_HOSTNAME


agent.COMPUTER_HOSTNAME = "LAB-WIRE"
bodies = every_request(None)
paths = [path for path, _ in bodies]
check("without an ID all five requests are made",
      len(paths) == 5 and "/api/agent/poll" in paths and "/api/agent/heartbeat" in paths
      and "/api/agent/inventory" in paths and "/api/agent/problem-reports" in paths
      and any(p.endswith("/report") for p in paths))
check("without an ID no request carries agent_id", all("agent_id" not in b for _, b in bodies))
by_path = {("/report" if p.endswith("/report") else p): set(b) for p, b in bodies}
check("poll body is exactly today's",
      by_path.get("/api/agent/poll") == {"computer_hostname", "ip_address", "capabilities"})
check("heartbeat body is exactly today's", by_path.get("/api/agent/heartbeat") == {"computer_hostname", "ip_address"})
check("inventory body is exactly today's",
      by_path.get("/api/agent/inventory") == {"computer_hostname", "ip_address", "cpu"})
check("job report body is exactly today's",
      by_path.get("/report") == {"computer_hostname", "status", "exit_code", "reason"})
check("problem report body is exactly today's",
      by_path.get("/api/agent/problem-reports") == {"computer_hostname", "problem_type", "description"})

W = secrets.token_hex(16)
bodies = every_request(W)
check("with an ID all five requests are made", len(bodies) == 5)
check("with an ID every request carries it", all(b.get("agent_id") == W for _, b in bodies))
check("and still names the machine", all(b.get("computer_hostname") == "LAB-WIRE" for _, b in bodies))

# Two agents, one name, end to end through poll_once itself.
agent.COMPUTER_HOSTNAME = "LAB-E2E"
P = secrets.token_hex(16)
Q = secrets.token_hex(16)
agent.AGENT_ID = P
agent.poll_once()
agent.AGENT_ID = Q
agent.poll_once()
check("two agents end to end: two rows",
      computer_by_id(P)["hostname"] == "LAB-E2E" and computer_by_id(Q)["hostname"] == "LAB-E2E-2")
installs.clear()
job_q = queue("LAB-E2E-2", "probe-q")
agent.AGENT_ID = P
agent.poll_once()
check("P never runs Q's install", installs == [])
check("Q's job still pending after P polled", job(job_q)["status"] == "pending")
agent.AGENT_ID = Q
agent.poll_once()
check("Q runs its own install", installs == [(Q, "probe-q")])
check("and Q's report is accepted and stored", job(job_q)["status"] == "completed")

# main() with an ID file it cannot create: runs, and sends the no-ID body.
real_argv = sys.argv
real_path = agent.AGENT_ID_PATH
agent.AGENT_ID = None
agent.AGENT_ID_PATH = os.path.join(blocker, "AppEase", "agent_id.txt")
sys.argv = ["agent.py", "--report-type", "other", "--report-description", "probe main"]
sent.clear()
try:
    agent.main()
    raised = None
except Exception as exc:        # noqa: BLE001
    raised = exc
check("main() with an unusable ID file does not crash", raised is None)
check("and still files the report", any(p == "/api/agent/problem-reports" for p, _ in sent))
check("without an agent_id", all("agent_id" not in b for _, b in sent))

agent.AGENT_ID_PATH = os.path.join(id_dir, "main", "agent_id.txt")
sent.clear()
agent.main()
stored_id = open(agent.AGENT_ID_PATH).read().strip()
check("main() with a usable path creates the ID", len(stored_id) == 32)
check("and sends it", any(b.get("agent_id") == stored_id for _, b in sent))

sys.argv = real_argv
agent.AGENT_ID_PATH = real_path
agent.AGENT_ID = None
agent.post_json = real_post
agent.run_install_command = real_install
agent.collect_hardware_facts = real_facts


# ---------------------------------------------------------------------------
print("\n=== 9. Migration on an existing database ===")
old_db = os.path.join(work, "old.db")
conn = sqlite3.connect(old_db)
conn.execute(
    "CREATE TABLE computers (id INTEGER PRIMARY KEY AUTOINCREMENT, hostname TEXT UNIQUE NOT NULL, "
    "ip_address TEXT, last_seen TIMESTAMP, status TEXT DEFAULT 'offline')"
)
conn.execute("INSERT INTO computers (hostname, ip_address) VALUES ('LAB-LEGACY', '10.9.9.9')")
conn.commit()
conn.close()
real_db = database.DB_PATH
database.DB_PATH = old_db
try:
    database.init_db()
    database.init_db()          # a restart must not fail on columns that exist
    raised = None
except Exception as exc:        # noqa: BLE001
    raised = exc
finally:
    database.DB_PATH = real_db
check("migration runs, twice", raised is None)
conn = sqlite3.connect(old_db)
conn.row_factory = sqlite3.Row
columns = {r["name"] for r in conn.execute("PRAGMA table_info(computers)")}
legacy = conn.execute("SELECT * FROM computers WHERE hostname = 'LAB-LEGACY'").fetchone()
check("agent_id column added", "agent_id" in columns)
check("agent_hostname column added", "agent_hostname" in columns)
check("existing row kept, with no ID", legacy is not None and legacy["agent_id"] is None
      and legacy["ip_address"] == "10.9.9.9")
conn.execute("INSERT INTO computers (hostname) VALUES ('LAB-N1')")
conn.execute("INSERT INTO computers (hostname) VALUES ('LAB-N2')")
check("any number of rows may have no ID", True)
conn.execute("INSERT INTO computers (hostname, agent_id) VALUES ('LAB-U1', 'aaaa')")
try:
    conn.execute("INSERT INTO computers (hostname, agent_id) VALUES ('LAB-U2', 'aaaa')")
    unique = False
except sqlite3.IntegrityError:
    unique = True
check("one ID can never own two rows", unique)
conn.close()


print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)

os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

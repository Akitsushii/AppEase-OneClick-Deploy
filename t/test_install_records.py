"""Install records: one is accepted ONLY inside an accepted report (an
in_progress job of the reporting computer); anything else is ignored and
logged, and never turns the report itself into an error.
"""
import logging
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


def server_source():
    for index, arg in enumerate(sys.argv):
        if arg == "--server" and index + 1 < len(sys.argv):
            return os.path.abspath(sys.argv[index + 1])
    return os.path.join(ROOT, "server")


SERVER_SRC = server_source()
work = tempfile.mkdtemp(prefix="appease_records_")
server_dir = os.path.join(work, "server")
shutil.copytree(
    SERVER_SRC, server_dir,
    ignore=shutil.ignore_patterns(
        "appease.db*", "__pycache__", "*.pyc", ".env",
        "uploads", "staging", "report_photos", "avatars", "venv",
    ),
)
sys.path.insert(0, server_dir)
os.chdir(server_dir)
import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
import app as appmod  # noqa: E402

if not hasattr(appmod, "store_install_record"):
    print("COULD NOT RUN - app.py predates install records.")
    sys.exit(3)

appmod.app.config["TESTING"] = True
client = appmod.app.test_client()
DB = database.DB_PATH


class Captured(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())

    def take(self):
        found, self.messages = self.messages, []
        return "\n".join(found)


logs = Captured()
appmod.app.logger.addHandler(logs)
appmod.app.logger.setLevel(logging.DEBUG)


def raw():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def rows(sql, args=()):
    conn = raw()
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def write(sql, args=()):
    conn = raw()
    try:
        cursor = conn.execute(sql, args)
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def all_installations():
    return rows("SELECT * FROM installations ORDER BY id")


def installs_on(host):
    return rows(
        "SELECT installations.* FROM installations JOIN computers ON computers.id = installations.computer_id "
        "WHERE computers.hostname = ? ORDER BY installations.id", (host,))


CHOCO_CMD = "choco install vlc -y"
MSI_CMD = 'msiexec /i "uploads/abc.msi" /qn'
EXE_CMD = '"uploads/def.exe" /S'


def software(name, command, version="1.0"):
    return write("INSERT INTO software (name, version, type, install_command) VALUES (?, ?, 'x', ?)",
                 (name, version, command))


def register(host, agent_id=None):
    body = {"computer_hostname": host}
    if agent_id:
        body["agent_id"] = agent_id
    client.post("/api/agent/poll", json=body)
    return rows("SELECT id FROM computers WHERE hostname = ?", (host,))[0]["id"]


def job(host, software_id, status="in_progress"):
    name, version = rows("SELECT name, version FROM software WHERE id = ?", (software_id,))[0].values()
    return write(
        "INSERT INTO jobs (computer_hostname, software_id, status, started_at, software_name, software_version) "
        "VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, ?)", (host, software_id, status, name, version))


def report(job_id, host, record=None, status="completed", exit_code=0, reason=None, agent_id=None,
           send_record=True):
    body = {"computer_hostname": host, "status": status, "exit_code": exit_code}
    if reason is not None:
        body["reason"] = reason
    if agent_id:
        body["agent_id"] = agent_id
    if send_record:
        body["install_record"] = record
    try:
        res = client.post(f"/api/agent/jobs/{job_id}/report", json=body)
    except Exception as exc:  # noqa: BLE001 - a report that blows up IS the failure
        return f"raised {type(exc).__name__}: {exc}", {}, ""
    return res.status_code, res.get_json() or {}, res.get_data(as_text=True)


def job_status(job_id):
    return rows("SELECT status FROM jobs WHERE id = ?", (job_id,))[0]["status"]


P1 = "{11111111-AAAA-4BBB-8CCC-000000000001}"
P2 = "{22222222-AAAA-4BBB-8CCC-000000000002}"
VLC = {"method": "choco", "package_id": "vlc.install", "display_name": "VLC media player",
       "display_version": "3.0.21"}
SEVENZIP = {"method": "msi", "product_code": P1, "display_name": "7-Zip", "display_version": "24.08"}
UNINSTALL = '"C:\\Program Files\\Notepad++\\uninstall.exe"'
NOTEPAD = {"method": "exe", "hive": "HKEY_LOCAL_MACHINE", "view": "Registry64", "key_name": "Notepad++",
           "display_name": "Notepad++", "display_version": "8.7.1", "uninstall_string": UNINSTALL,
           "quiet_uninstall_string": UNINSTALL + " /S", "install_location": "C:\\Program Files\\Notepad++"}

S_VLC = software("VLC", CHOCO_CMD, "3.0.21")
S_7Z = software("7-Zip", MSI_CMD, "24.08")
S_NPP = software("Notepad++", EXE_CMD, "8.7.1")


# ---------------------------------------------------------------------------
print("1. An old agent, sending no record")
# ---------------------------------------------------------------------------
register("PC-OLD")
j = job("PC-OLD", S_NPP)
logs.take()
code, reply, _ = report(j, "PC-OLD", send_record=False)
check("the report is accepted as always", code == 200 and reply.get("status") == "completed")
check("the job completes with its exit code", job_status(j) == "completed"
      and rows("SELECT exit_code FROM jobs WHERE id = ?", (j,))[0]["exit_code"] == 0)
check("no installations row is created", all_installations() == [])
check("the reply says no record came", reply.get("install_record") == "none")
check("nothing about it is logged", logs.take() == "")
j = job("PC-OLD", S_NPP)
code, reply, _ = report(j, "PC-OLD", record=None)
check("an explicit null record is the same as none", code == 200 and all_installations() == []
      and reply.get("install_record") == "none")


# ---------------------------------------------------------------------------
print("2. Records riding on a report that is refused")
# ---------------------------------------------------------------------------
register("PC-A")
register("PC-B")
j = job("PC-B", S_NPP)
logs.take()
code, _, _ = report(j, "PC-A", NOTEPAD)
check("another computer's job: refused exactly as before (403)", code == 403)
check("another computer's job: no row anywhere", all_installations() == [])
check("another computer's job: the job is untouched", job_status(j) == "in_progress")
check("another computer's job: the dropped record is logged", "belongs to another computer" in logs.take())

for state in ("pending", "completed", "failed"):
    j = job("PC-A", S_NPP, status=state)
    code, _, _ = report(j, "PC-A", NOTEPAD)
    check(f"a {state} job: refused exactly as before (409)", code == 409)
    check(f"a {state} job: no row", all_installations() == [])
    check(f"a {state} job: the dropped record is logged", "not in progress" in logs.take())

code, _, _ = report(999999, "PC-A", NOTEPAD)
check("no such job: 404 as before, no row, logged",
      code == 404 and all_installations() == [] and "no such job" in logs.take())


# ---------------------------------------------------------------------------
print("3. Records that do not parse are ignored whole, and the report still saves")
# ---------------------------------------------------------------------------
def bad(label, record, sw=S_NPP):
    j = job("PC-A", sw)
    before = all_installations()
    logs.take()
    code, reply, _ = report(j, "PC-A", record)
    check(f"{label}: report accepted, job completed", code == 200 and job_status(j) == "completed")
    check(f"{label}: no row stored", all_installations() == before)
    check(f"{label}: reply says ignored, and it is logged",
          reply.get("install_record") == "ignored" and "ignored" in logs.take())


bad("not an object", ["Notepad++"])
bad("unknown method", dict(NOTEPAD, method="zip"))
bad("a ProductCode that is not a GUID", dict(SEVENZIP, product_code="{1111-2222}"), S_7Z)
bad("a key name with a backslash", dict(NOTEPAD, key_name="Notepad++\\x"))
bad("a per-user entry without a SID", dict(NOTEPAD, hive="HKEY_CURRENT_USER"))
bad("an uninstall string over 2048 characters", dict(NOTEPAD, uninstall_string="x" * 2049))
bad("a control character in a display name", dict(NOTEPAD, display_name="Note\npad"))
bad("a number where text belongs", dict(NOTEPAD, display_version=871))
bad("a reason the server does not know", dict(NOTEPAD, unavailable_reason="because"))
bad("a reason only the server may set", dict(NOTEPAD, unavailable_reason="replaced"))
bad("an entry-less record that does not say why", {"method": "exe"})
bad("an msi record for a Chocolatey job", SEVENZIP, S_VLC)
bad("a choco record for an installer job", VLC, S_NPP)
bad("replaced_entries that is not a list", dict(NOTEPAD, replaced_entries="P1"))
bad("more replaced_entries than allowed", dict(NOTEPAD, replaced_entries=[{"product_code": P2}] * 21))

j = job("PC-A", S_NPP)
code, reply, _ = report(j, "PC-A", dict(NOTEPAD, uninstall_string="x" * 2048))
check("an uninstall string of exactly 2048 characters is accepted",
      reply.get("install_record") == "stored" and len(installs_on("PC-A")) == 1)
write("DELETE FROM installations")


# ---------------------------------------------------------------------------
print("4. Failed installs and Chocolatey's 'already installed' create nothing")
# ---------------------------------------------------------------------------
j = job("PC-A", S_NPP)
code, reply, _ = report(j, "PC-A", NOTEPAD, status="failed", exit_code=1603)
check("a failed install: no row", code == 200 and all_installations() == [] and reply["install_record"] == "ignored")
j = job("PC-A", S_NPP)
code, reply, _ = report(j, "PC-A", NOTEPAD, status="failed", exit_code=None, reason="timed_out")
check("a timed-out install: no row", code == 200 and all_installations() == [])

j = job("PC-A", S_NPP)
report(j, "PC-A", NOTEPAD)
kept = installs_on("PC-A")
j = job("PC-A", S_NPP)
report(j, "PC-A", dict(NOTEPAD, display_version="9.9"), status="failed", exit_code=2)
check("a failed reinstall leaves the existing row exactly as it was", installs_on("PC-A") == kept)
j = job("PC-A", S_NPP)
report(j, "PC-A", dict(NOTEPAD, display_version="9.9"), status="failed", exit_code=None, reason="timed_out")
check("so does a timed-out one", installs_on("PC-A") == kept)
write("DELETE FROM installations")

for reason in ("choco_already_installed", "choco_confirmed_installed"):
    j = job("PC-A", S_VLC)
    code, reply, _ = report(j, "PC-A", VLC, reason=reason)
    check(f"{reason}: the job completes, and no row is created",
          code == 200 and job_status(j) == "completed" and all_installations() == []
          and reply["install_record"] == "ignored")
j = job("PC-A", S_VLC)
code, reply, _ = report(j, "PC-A", VLC, exit_code=5, reason="choco_already_installed")
check("'already installed' with an exit code the server rejects still creates nothing",
      reply.get("reason") is None and all_installations() == [])
j = job("PC-A", S_VLC)
code, reply, _ = report(j, "PC-A", VLC, reason=["choco_already_installed"])
check("a reason that is not text cannot break the report",
      code == 200 and job_status(j) == "completed")
write("DELETE FROM installations")
j = job("PC-A", S_VLC)
code, reply, _ = report(j, "PC-A", VLC, reason="choco_reinstalled")
check("a Chocolatey REinstall did install it, so it is recorded",
      reply["install_record"] == "stored" and len(installs_on("PC-A")) == 1)
write("DELETE FROM installations")


# ---------------------------------------------------------------------------
print("5. What a stored row holds")
# ---------------------------------------------------------------------------
j = job("PC-A", S_NPP)
code, reply, _ = report(j, "PC-A", dict(NOTEPAD, some_future_field="ignored"))
row = installs_on("PC-A")[0] if installs_on("PC-A") else {}
check("a good record is stored", reply.get("install_record") == "stored" and row != {})
check("...on the reporting computer", row.get("computer_id") == register("PC-A"))
check("...with its key built by the server",
      row.get("record_key") == database.installation_record_key(
          "exe", hive="HKLM", view="Registry64", key_name="notepad++"))
check("...the registry text as Windows reported it",
      row.get("reg_key_name") == "Notepad++" and row.get("display_version") == "8.7.1"
      and row.get("quiet_uninstall_string") == UNINSTALL + " /S")
check("...the library entry, its snapshot name and version, and the job",
      row.get("software_id") == S_NPP and row.get("software_name") == "Notepad++"
      and row.get("software_version") == "8.7.1" and row.get("install_job_id") == j)
check("...installed, with no reason and no uninstall job",
      row.get("state") == "installed" and row.get("state_reason") is None and row.get("uninstall_job_id") is None)
write("DELETE FROM installations")

j = job("PC-A", S_NPP)
report(j, "PC-A", dict(NOTEPAD, quiet_uninstall_string=None, unavailable_reason="no_silent_uninstall"))
row = installs_on("PC-A")[0]
check("an entry with no silent uninstall is kept by its key, as unavailable with its reason",
      row["state"] == "unavailable" and row["state_reason"] == "no_silent_uninstall"
      and row["record_key"].startswith("exe:"))
write("DELETE FROM installations")

S_CHOCO_PY = software("Python", "choco install python314 -y")
S_CHOCO_CHOCO = software("Chocolatey", "choco install chocolatey -y")
j = job("PC-A", S_CHOCO_PY)
report(j, "PC-A", {"method": "choco", "package_id": "python314"})
j = job("PC-A", S_CHOCO_CHOCO)
report(j, "PC-A", {"method": "choco", "package_id": "Chocolatey"})
check("python* and chocolatey are always unavailable as protected, whatever the agent said",
      [(r["state"], r["state_reason"]) for r in installs_on("PC-A")] == [("unavailable", "protected")] * 2)
write("DELETE FROM installations")

j = job("PC-A", S_NPP)
_, _, text = report(j, "PC-A", NOTEPAD)
poll_text = client.post("/api/agent/poll", json={"computer_hostname": "PC-A"}).get_data(as_text=True)
check("the report's reply carries no command text",
      "uninstall.exe" not in text and "Notepad++" not in text)
check("nor does the next poll", "uninstall.exe" not in poll_text)
write("DELETE FROM installations")


# ---------------------------------------------------------------------------
print("6. A real entry always wins over 'none:'")
# ---------------------------------------------------------------------------
NONE = {"method": "exe", "unavailable_reason": "no_new_entry"}
none_key = database.installation_record_key("exe", software_id=S_NPP)

j = job("PC-A", S_NPP)
_, reply, _ = report(j, "PC-A", NONE)
found = installs_on("PC-A")
check("none first: stored as one unavailable row for that library entry",
      reply["install_record"] == "stored" and len(found) == 1 and found[0]["record_key"] == none_key
      and found[0]["state"] == "unavailable" and found[0]["state_reason"] == "no_new_entry")
j = job("PC-A", S_NPP)
report(j, "PC-A", NONE)
check("none again: still one row", len(installs_on("PC-A")) == 1)

S_OTHER = software("Other", '"uploads/other.exe" /S')
j = job("PC-A", S_OTHER)
report(j, "PC-A", NONE)
j = job("PC-A", S_NPP)
_, reply, _ = report(j, "PC-A", NOTEPAD)
keys = [r["record_key"] for r in installs_on("PC-A")]
check("a real record arriving removes that software's none: row", none_key not in keys)
check("...stores the real one", reply["install_record"] == "stored" and any(k.startswith("exe:HKLM") for k in keys))
check("...and leaves another software's none: row alone",
      database.installation_record_key("exe", software_id=S_OTHER) in keys)

before = installs_on("PC-A")
j = job("PC-A", S_NPP)
_, reply, _ = report(j, "PC-A", NONE)
check("none while a live real row exists: keeps the real one and adds nothing",
      reply["install_record"] == "kept_existing" and installs_on("PC-A") == before)

write("UPDATE installations SET state = 'removed' WHERE record_key LIKE 'exe:%'")
j = job("PC-A", S_NPP)
_, reply, _ = report(j, "PC-A", NONE)
check("none when the real row is no longer live (removed): the none: row is added",
      reply["install_record"] == "stored" and none_key in [r["record_key"] for r in installs_on("PC-A")])
write("DELETE FROM installations")


CHOCO_NONE = {"method": "choco", "unavailable_reason": "choco_shared_dependency"}
j = job("PC-A", S_VLC)
_, reply, _ = report(j, "PC-A", CHOCO_NONE)
found = installs_on("PC-A")
check("a Chocolatey record naming no package is stored as that software's none: row",
      reply.get("install_record") == "stored" and len(found) == 1
      and found[0]["record_key"] == database.installation_record_key("choco", software_id=S_VLC)
      and found[0]["state_reason"] == "choco_shared_dependency" and found[0]["choco_package_id"] is None)
j = job("PC-A", S_VLC)
_, reply, _ = report(j, "PC-A", VLC)
keys = [r["record_key"] for r in installs_on("PC-A")]
check("...and a later real Chocolatey record replaces it", keys == ["choco:vlc.install"])
for reason in ("choco_records_unreadable", "no_registry_entry"):
    write("DELETE FROM installations")
    j = job("PC-A", S_VLC)
    _, reply, _ = report(j, "PC-A", {"method": "choco", "unavailable_reason": reason})
    check(f"a Chocolatey none: record with reason {reason} is accepted", reply.get("install_record") == "stored")
write("DELETE FROM installations")


# ---------------------------------------------------------------------------
print("7. The same key reported again")
# ---------------------------------------------------------------------------
S_NPP2 = software("Notepad++ (new)", '"uploads/npp2.exe" /S', "8.8")
j1 = job("PC-A", S_NPP)
report(j1, "PC-A", NOTEPAD)
write("UPDATE installations SET state_changed_at = '2026-01-01 00:00:00', updated_at = '2026-01-01 00:00:00'")
j2 = job("PC-A", S_NPP2)
_, reply, _ = report(j2, "PC-A", dict(NOTEPAD, key_name="NOTEPAD++", display_version="8.8"))
found = installs_on("PC-A")
check("one row, not two, even in other capitals", len(found) == 1)
check("the reply says it was updated", reply["install_record"] == "updated")
check("the row now belongs to the later install, its library entry and its job",
      found[0]["software_id"] == S_NPP2 and found[0]["install_job_id"] == j2
      and found[0]["software_name"] == "Notepad++ (new)" and found[0]["display_version"] == "8.8")
check("updated_at moved; state_changed_at did not, as the state did not change",
      found[0]["updated_at"] != "2026-01-01 00:00:00" and found[0]["state_changed_at"] == "2026-01-01 00:00:00")

write("UPDATE installations SET state = 'replaced', state_reason = 'replaced', uninstall_job_id = 5")
j3 = job("PC-A", S_NPP)
report(j3, "PC-A", NOTEPAD)
found = installs_on("PC-A")[0]
check("a replaced row reported again is live again, with no reason or uninstall job",
      found["state"] == "installed" and found["state_reason"] is None and found["uninstall_job_id"] is None)
check("...and its state_changed_at moved", found["state_changed_at"] != "2026-01-01 00:00:00")
write("DELETE FROM installations")


# ---------------------------------------------------------------------------
print("8. 'replaced' is what the agent saw disappear")
# ---------------------------------------------------------------------------
S_7Z_NEW = software("7-Zip", 'msiexec /i "uploads/7z25.msi" /qn', "25.01")
register("PC-C")
j = job("PC-A", S_7Z)
report(j, "PC-A", SEVENZIP)
j = job("PC-C", S_7Z)
report(j, "PC-C", SEVENZIP)
j = job("PC-A", S_7Z_NEW)
logs.take()
_, reply, _ = report(j, "PC-A", dict(SEVENZIP, product_code=P2, display_version="25.01", replaced_entries=[
    {"product_code": P1.strip("{}").lower()},
    {"product_code": P2},
    {"key_name": "bad\\name", "hive": "HKLM", "view": "Registry64"},
]))
by_key = {r["record_key"]: r for r in installs_on("PC-A")}
old, new = by_key.get("msi:" + P1), by_key.get("msi:" + P2)
check("MSI major upgrade: the new ProductCode is stored as installed",
      reply["install_record"] == "stored" and new is not None and new["state"] == "installed")
check("...the old ProductCode's row, from a different library entry, is replaced",
      old is not None and old["state"] == "replaced" and old["state_reason"] == "replaced")
check("...matched however the agent capitalised it", old is not None and old["software_id"] == S_7Z)
check("...the same ProductCode on another PC is untouched",
      [r["state"] for r in installs_on("PC-C")] == ["installed"])
check("...listing the new key itself as replaced does not retire it", new is not None and new["state"] == "installed")
check("...a replaced entry that does not parse is dropped and logged, the record still stored",
      "a replaced entry was ignored" in logs.take())

j = job("PC-A", S_NPP)
report(j, "PC-A", NOTEPAD)
write("UPDATE installations SET state = 'removed' WHERE record_key LIKE 'exe:%'")
S_NPP3 = software("Notepad++ 9", '"uploads/npp9.exe" /S', "9")
j = job("PC-A", S_NPP3)
report(j, "PC-A", dict(NOTEPAD, key_name="Notepad++ 9", replaced_entries=[
    {"hive": "HKEY_LOCAL_MACHINE", "view": "Registry64", "key_name": "notepad++"}]))
states = {r["reg_key_name"]: r["state"] for r in installs_on("PC-A") if r["method"] == "exe"}
check("an exe entry named in replaced_entries is matched by its registry key",
      states.get("Notepad++ 9") == "installed")
check("...and a row already removed stays removed", states.get("Notepad++") == "removed")
write("DELETE FROM installations")


# ---------------------------------------------------------------------------
print("9. Deleted software and renamed computers")
# ---------------------------------------------------------------------------
conn = raw()
admin = conn.execute("SELECT id, username FROM admins LIMIT 1").fetchone()
conn.close()
admin_client = appmod.app.test_client()
with admin_client.session_transaction() as sess:
    sess["admin_id"] = admin["id"]
    sess["admin_role"] = "admin"
    sess["admin_username"] = admin["username"]

S_GONE = software("Gone App", '"uploads/gone.exe" /S', "2.0")
S_GONE2 = software("Gone Too", '"uploads/gone2.exe" /S', "3.0")
S_STAYS = software("Stays", '"uploads/stays.exe" /S', "1.0")
for sw, name in ((S_GONE, "Gone"), (S_GONE2, "Gone2"), (S_STAYS, "Stays")):
    j = job("PC-A", sw)
    report(j, "PC-A", dict(NOTEPAD, key_name=name))
admin_client.post(f"/software/{S_GONE}/delete")
admin_client.post("/software/bulk-delete", data={"software_ids": [str(S_GONE2)]})
by_name = {r["reg_key_name"]: r for r in installs_on("PC-A")}
check("software deleted from the library: its row stays",
      "Gone" in by_name and "Gone2" in by_name)
check("...with software_id NULL", by_name["Gone"]["software_id"] is None)
check("...and its snapshot name and version kept",
      by_name["Gone"]["software_name"] == "Gone App" and by_name["Gone"]["software_version"] == "2.0")
check("bulk delete does the same", by_name["Gone2"]["software_id"] is None
      and by_name["Gone2"]["software_name"] == "Gone Too")
check("another library entry's row is untouched", by_name["Stays"]["software_id"] == S_STAYS)
write("DELETE FROM installations")

AGENT = "0123456789abcdef0123456789abcdef"
cid = register("LAB-OLD", AGENT)
j = job("LAB-OLD", S_NPP)
report(j, "LAB-OLD", NOTEPAD, agent_id=AGENT)
register("LAB-NEW", AGENT)
renamed = rows("SELECT id, hostname FROM computers WHERE agent_id = ?", (AGENT,))[0]
found = rows("SELECT * FROM installations WHERE computer_id = ?", (cid,))
check("(the computer really was renamed, and kept its id)",
      renamed["hostname"] == "LAB-NEW" and renamed["id"] == cid)
check("a renamed computer keeps its rows, on the same computer_id", len(found) == 1)
check("...and installs_on() under the new name finds them", len(installs_on("LAB-NEW")) == 1)
j = job("LAB-NEW", S_NPP)
_, reply, _ = report(j, "LAB-NEW", NOTEPAD, agent_id=AGENT)
check("the same key reported after the rename updates that row, not a new one",
      reply["install_record"] == "updated" and len(installs_on("LAB-NEW")) == 1)
write("DELETE FROM installations")


# ---------------------------------------------------------------------------
print("10. A write that fails half-way leaves nothing behind")
# ---------------------------------------------------------------------------
real_write = appmod._write_install_record


def half_then_fail(db, job_row, computer_id, record):
    real_write(db, job_row, computer_id, record)
    raise sqlite3.OperationalError("disk I/O error")


appmod._write_install_record = half_then_fail
try:
    j = job("PC-A", S_NPP)
    logs.take()
    code, reply, _ = report(j, "PC-A", NOTEPAD)
finally:
    appmod._write_install_record = real_write
check("the report still succeeds and the job completes", code == 200 and job_status(j) == "completed")
check("the half-written row is rolled back", all_installations() == [])
check("the reply says ignored, and the log says why",
      reply.get("install_record") == "ignored" and "could not be written" in logs.take())

real_clean = appmod.clean_install_record


def broken_clean(raw, software_id):
    raise TypeError("probe: cleaning broke")


appmod.clean_install_record = broken_clean
try:
    j = job("PC-A", S_NPP)
    code, reply, _ = report(j, "PC-A", NOTEPAD)
finally:
    appmod.clean_install_record = real_clean
check("a bug while reading the record still costs nothing: report saved, job completed",
      code == 200 and job_status(j) == "completed")
check("...the record is ignored, and the log says so",
      reply.get("install_record") == "ignored" and "could not be read" in logs.take())


print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for label in failed:
    print("  -", label)
print("=" * 56)
os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

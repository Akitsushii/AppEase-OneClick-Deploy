"""Hardware asset tracking end to end, on a real test_client and a
file-backed database: every path under test writes.
"""
import os
import re
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


work = tempfile.mkdtemp(prefix="appease_asset_")
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
sys.path.insert(0, server_dir)

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
appmod.app.config["WTF_CSRF_ENABLED"] = False

DB = database.DB_PATH


def raw():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def seed_computer(hostname, **cols):
    conn = raw()
    conn.execute(
        "INSERT OR IGNORE INTO computers (hostname, ip_address, last_seen, status) "
        "VALUES (?, ?, CURRENT_TIMESTAMP, 'online')",
        (hostname, cols.pop("ip_address", "192.168.1.50")),
    )
    if cols:
        sets = ", ".join(f"{k} = ?" for k in cols)
        conn.execute(
            f"UPDATE computers SET {sets} WHERE hostname = ?",
            list(cols.values()) + [hostname],
        )
    conn.commit()
    row = conn.execute("SELECT * FROM computers WHERE hostname = ?", (hostname,)).fetchone()
    conn.close()
    return row


def seed_software(name="Firefox"):
    conn = raw()
    cur = conn.execute(
        "INSERT INTO software (name, version, type, install_command) VALUES (?, '1.0', 'choco', 'choco install firefox')",
        (name,),
    )
    conn.commit()
    sid = cur.lastrowid
    conn.close()
    return sid


def login(client):
    conn = raw()
    admin = conn.execute("SELECT id, username FROM admins LIMIT 1").fetchone()
    conn.close()
    with client.session_transaction() as sess:
        sess["admin_id"] = admin["id"]
        sess["admin_role"] = "admin"
        sess["admin_username"] = admin["username"]
    return admin


print("\n=== 1. Migration ===")
conn = raw()
cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(computers)").fetchall()}
conn.close()

AGENT_COLS = ["manufacturer", "model", "serial_number", "cpu", "ram_mb",
              "storage_gb", "os_name", "hardware_reported_at"]
TECH_COLS = ["asset_status", "purchase_date", "vendor", "warranty_until"]

for name in AGENT_COLS + TECH_COLS:
    check(f"column {name} exists", name in cols)

check("ram_mb is INTEGER", cols["ram_mb"]["type"] == "INTEGER")
check("storage_gb is INTEGER", cols["storage_gb"]["type"] == "INTEGER")
check("asset_status defaults to NULL", cols["asset_status"]["dflt_value"] is None)
check("asset_status is nullable", cols["asset_status"]["notnull"] == 0)

# Idempotency: init_db runs on every boot, so a second run must not raise
# and must not duplicate a column.
database.init_db()
conn = raw()
again = [r["name"] for r in conn.execute("PRAGMA table_info(computers)").fetchall()]
conn.close()
check("init_db is idempotent (no duplicate columns)", len(again) == len(set(again)))
check("column count stable after re-run", len(again) == len(cols))


print("\n=== 2. Pure helpers ===")
check("format_ram 8192 -> 8 GB", appmod.format_ram(8192) == "8 GB")
check("format_ram 4096 -> 4 GB", appmod.format_ram(4096) == "4 GB")
check("format_ram 12288 -> 12 GB", appmod.format_ram(12288) == "12 GB")
check("format_ram 6000 -> 5.9 GB", appmod.format_ram(6000) == "5.9 GB")
check("format_ram None -> None", appmod.format_ram(None) is None)
check("format_ram 0 -> None", appmod.format_ram(0) is None)
check("format_storage 238 -> 238 GB", appmod.format_storage(238) == "238 GB")
check("format_storage 1024 -> 1 TB", appmod.format_storage(1024) == "1 TB")
check("format_storage None -> None", appmod.format_storage(None) is None)

check("asset_status_label(None) -> In use", appmod.asset_status_label(None) == "In use")
check("asset_status_label('') -> In use", appmod.asset_status_label("") == "In use")
check("asset_status_label('Retired') -> Retired", appmod.asset_status_label("Retired") == "Retired")

v, e = appmod.normalise_asset_date("2021-01-12", "Purchase date")
check("valid date accepted", v == "2021-01-12" and e is None)
v, e = appmod.normalise_asset_date("", "Purchase date")
check("empty date clears the field", v is None and e is None)
v, e = appmod.normalise_asset_date("12/01/2021", "Purchase date")
check("slashed date rejected", v is None and e is not None)
v, e = appmod.normalise_asset_date("2021-13-01", "Purchase date")
check("month 13 rejected", v is None and e is not None)
v, e = appmod.normalise_asset_date("0001-01-01", "Purchase date")
check("year 1 rejected as a typo", v is None and e is not None)
v, e = appmod.normalise_asset_date("2101-01-01", "Purchase date")
check("year 2101 rejected", v is None and e is not None)

vals, err = appmod.validate_asset_fields({"asset_status": "Retired", "vendor": "  Dell   Inc.  "})
check("vendor whitespace collapsed", vals["vendor"] == "Dell Inc.")
check("status passed through", vals["asset_status"] == "Retired")
check("blank purchase date -> None", vals["purchase_date"] is None)
vals, err = appmod.validate_asset_fields({"asset_status": "Melted"})
check("unknown status rejected", vals is None and err == "Invalid asset status.")
vals, err = appmod.validate_asset_fields({"vendor": "x" * 41})
check("over-long vendor rejected", vals is None and "40 characters" in err)
vals, err = appmod.validate_asset_fields({"vendor": "<script>alert(1)</script>"})
check("markup-ish vendor rejected", vals is None and err is not None)
vals, err = appmod.validate_asset_fields({"vendor": "O'Brien & Sons, Ltd."})
check("real-world punctuation accepted", err is None and vals["vendor"] == "O'Brien & Sons, Ltd.")

rows = appmod.with_asset_labels([{"ram_mb": 8192, "storage_gb": 238, "asset_status": None}])
check("with_asset_labels builds ram label", rows[0]["ram_label"] == "8 GB")
check("with_asset_labels builds storage label", rows[0]["storage_label"] == "238 GB")
check("with_asset_labels defaults status", rows[0]["asset_status_label"] == "In use")


print("\n=== 3. Computers page rendering ===")
client = appmod.app.test_client()
login(client)

seed_computer("LAB-01", location="Lab 1", device_type="Desktop",
              manufacturer="Dell Inc.", model="OptiPlex 3080",
              serial_number="7KJ2QM3", cpu="Intel Core i5-10500",
              ram_mb=8192, storage_gb=238, os_name="Windows 11 Pro",
              hardware_reported_at="2026-08-29 01:14:02",
              purchase_date="2021-01-12", vendor="Dell Inc.",
              warranty_until="2024-01-12")
seed_computer("LAB-LEGACY")  # no asset data at all

res = client.get("/computers")
html = res.get_data(as_text=True)
check("computers page 200", res.status_code == 200)
check("Model header present", ">Model</th>" in html)
check("Condition header present", ">Condition</th>" in html)
check("Status header still present", 'data-dt-label="Status"' in html)
check("model value rendered", "OptiPlex 3080" in html)
check("serial in row attribute", 'data-asset-serial="7KJ2QM3"' in html)
check("ram rendered as label not raw mb", 'data-asset-ram="8 GB"' in html)
check("raw ram_mb not shown in a cell", ">8192<" not in html)
check("storage label present", 'data-asset-storage="238 GB"' in html)
check("legacy machine reads Not recorded", "Not recorded" in html)
check("legacy machine defaults to In use", 'data-asset-condition="In use"' in html)
check("details enabled on the row menu", "data-dt-details" in html)
check("details drawer present", 'id="comp-details-drawer"' in html)
check("asset status select present", 'id="comp-edit-asset-status"' in html)
check("purchase date input is type=date", 'id="comp-edit-purchase" name="purchase_date"' in html and 'type="date"' in html)

# Header/cell parity: a mismatch is the classic silently shifted column.
head_count = len(re.findall(r"<th\b", html.split("</thead>")[0]))
first_row = html.split("<tbody>")[1].split("</tr>")[0]
cell_count = len(re.findall(r"<td\b", first_row))
check(f"header count == cell count ({head_count} vs {cell_count})", head_count == cell_count)

# The agent-owned fields must have no editable twin in the drawer.
for name in ("manufacturer", "model", "serial_number", "cpu", "ram_mb", "storage_gb", "os_name"):
    check(f"no form input named {name}", f'name="{name}"' not in html)


print("\n=== 4. Editing the asset record ===")
row = seed_computer("LAB-01")
res = client.post(
    f"/computers/{row['id']}/edit",
    data={"location": "Lab 1", "device_type": "Desktop",
          "asset_status": "In repair", "purchase_date": "2021-01-12",
          "vendor": "Dell Inc.", "warranty_until": "2024-01-12"},
    follow_redirects=False,
)
check("edit redirects", res.status_code == 302)
conn = raw()
after = conn.execute("SELECT * FROM computers WHERE id = ?", (row["id"],)).fetchone()
conn.close()
check("status stored", after["asset_status"] == "In repair")
check("vendor stored", after["vendor"] == "Dell Inc.")
check("warranty stored", after["warranty_until"] == "2024-01-12")
check("agent-owned serial untouched by the edit", after["serial_number"] == "7KJ2QM3")
check("agent-owned model untouched by the edit", after["model"] == "OptiPlex 3080")

# A technician POSTing agent-owned fields directly must be ignored.
res = client.post(
    f"/computers/{row['id']}/edit",
    data={"location": "Lab 1", "device_type": "Desktop", "asset_status": "In repair",
          "serial_number": "FORGED", "model": "FORGED", "ram_mb": "999999"},
)
conn = raw()
after = conn.execute("SELECT * FROM computers WHERE id = ?", (row["id"],)).fetchone()
conn.close()
check("posted serial_number ignored", after["serial_number"] == "7KJ2QM3")
check("posted model ignored", after["model"] == "OptiPlex 3080")
check("posted ram_mb ignored", after["ram_mb"] == 8192)

# Invalid input must not half-apply.
res = client.post(
    f"/computers/{row['id']}/edit",
    data={"location": "Lab 2", "device_type": "Laptop",
          "asset_status": "In use", "warranty_until": "not-a-date"},
)
conn = raw()
after = conn.execute("SELECT * FROM computers WHERE id = ?", (row["id"],)).fetchone()
conn.close()
check("bad date rejected with an error redirect", "error=" in res.headers.get("Location", ""))
check("location NOT changed by the rejected post", after["location"] == "Lab 1")
check("device_type NOT changed by the rejected post", after["device_type"] == "Desktop")
check("asset_status NOT changed by the rejected post", after["asset_status"] == "In repair")

res = client.post(f"/computers/999999/edit", data={"location": "Lab 9"})
check("unknown computer id handled", res.status_code == 302 and "error=" in res.headers.get("Location", ""))


print("\n=== 5. Retired machines and deployment ===")
software_id = seed_software("Firefox")
seed_computer("LAB-RETIRED", asset_status="Retired", ip_address="192.168.1.77")
seed_computer("LAB-02", ip_address="192.168.1.52")

res = client.get("/deploy")
html = res.get_data(as_text=True)
check("deploy page 200", res.status_code == 200)
check("retired machine absent from picker", "LAB-RETIRED" not in html)
check("in-use machine present in picker", "LAB-02" in html)
check("in-repair machine still present in picker", "LAB-01" in html)

# The important one: the SERVER must refuse it, not just the template.
res = client.post("/deploy-batch", data={
    "software_ids": [str(software_id)],
    "computer_hostnames": ["LAB-02", "LAB-RETIRED"],
})
conn = raw()
jobs = conn.execute("SELECT computer_hostname FROM jobs WHERE software_id = ?", (software_id,)).fetchall()
conn.close()
hosts = [j["computer_hostname"] for j in jobs]
check("job created for the in-use machine", "LAB-02" in hosts)
check("NO job created for the retired machine", "LAB-RETIRED" not in hosts)
check("retired count surfaced in the message", "Skipped 1 retired" in res.headers.get("Location", "").replace("+", " ").replace("%20", " "))

# Everything retired -> a named error, not "no valid computers".
res = client.post("/deploy-batch", data={
    "software_ids": [str(software_id)],
    "computer_hostnames": ["LAB-RETIRED"],
}, headers={"X-Requested-With": "XMLHttpRequest"})
check("all-retired selection refused", res.status_code == 400)
check("all-retired message names the reason", "retired" in res.get_json().get("error", "").lower())

# all-online must skip retired too
conn = raw()
conn.execute("UPDATE computers SET status = 'online', last_seen = CURRENT_TIMESTAMP")
conn.commit()
conn.close()
software2 = seed_software("VLC")
res = client.post("/deploy-batch", data={
    "software_ids": [str(software2)],
    "target_all_online": "1",
}, headers={"X-Requested-With": "XMLHttpRequest"})
conn = raw()
hosts = [j["computer_hostname"] for j in
         conn.execute("SELECT computer_hostname FROM jobs WHERE software_id = ?", (software2,)).fetchall()]
conn.close()
check("all-online skipped the retired machine", "LAB-RETIRED" not in hosts)
check("all-online still reached a live machine", "LAB-02" in hosts)

software3 = seed_software("7zip")
conn = raw()
conn.execute("UPDATE computers SET status = 'online', last_seen = CURRENT_TIMESTAMP")
conn.commit()
conn.close()
res = client.post("/deploy-all", data={"software_id": str(software3)})
conn = raw()
hosts = [j["computer_hostname"] for j in
         conn.execute("SELECT computer_hostname FROM jobs WHERE software_id = ?", (software3,)).fetchall()]
conn.close()
check("/deploy-all skipped the retired machine", "LAB-RETIRED" not in hosts)
check("/deploy-all reached live machines", len(hosts) > 0)

# Un-retiring must put it back.
conn = raw()
rid = conn.execute("SELECT id FROM computers WHERE hostname = 'LAB-RETIRED'").fetchone()["id"]
conn.close()
client.post(f"/computers/{rid}/edit", data={"asset_status": "In use"})
res = client.get("/deploy")
check("un-retired machine returns to the picker", "LAB-RETIRED" in res.get_data(as_text=True))


print("\n=== 6. Access control ===")
anon = appmod.app.test_client()
res = anon.get("/computers")
check("computers page requires login", res.status_code in (302, 401, 403))
res = anon.post("/computers/1/edit", data={"asset_status": "Retired"})
check("edit route requires login", res.status_code in (302, 401, 403))

faculty = appmod.app.test_client()
with faculty.session_transaction() as sess:
    conn = raw()
    admin = conn.execute("SELECT id FROM admins LIMIT 1").fetchone()
    conn.close()
    sess["admin_id"] = admin["id"]
    sess["admin_role"] = "faculty"
res = faculty.get("/computers")
check("faculty cannot reach the computers page", res.status_code in (302, 403))


print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)

shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)
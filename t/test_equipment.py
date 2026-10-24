"""Technician-recorded equipment end to end, on a real test_client and a
file-backed database, rendered through the REAL base.html.
"""
import json
import os
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
# unquote_plus, not unquote: a query string encodes spaces as +, and
# unquote leaves them as a literal +, which breaks substring assertions
# against redirect messages.
from urllib.parse import unquote_plus

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


work = tempfile.mkdtemp(prefix="appease_equip_")
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
DB = database.DB_PATH


def raw():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def seed_computer(hostname, location=None, device_type=None):
    conn = raw()
    conn.execute(
        "INSERT OR IGNORE INTO computers (hostname, ip_address, last_seen, status, location, device_type) "
        "VALUES (?, '192.168.1.50', CURRENT_TIMESTAMP, 'online', ?, ?)",
        (hostname, location, device_type),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM computers WHERE hostname = ?", (hostname,)).fetchone()
    conn.close()
    return row


def equipment_rows():
    conn = raw()
    rows = conn.execute("SELECT * FROM equipment ORDER BY id").fetchall()
    conn.close()
    return rows


conn = raw()
admin = conn.execute("SELECT id, username FROM admins LIMIT 1").fetchone()
conn.close()

client = appmod.app.test_client()
with client.session_transaction() as sess:
    sess["admin_id"] = admin["id"]
    sess["admin_role"] = "admin"
    sess["admin_username"] = admin["username"]


print("\n=== 1. Schema ===")
conn = raw()
cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(equipment)").fetchall()}
idx = [r["name"] for r in conn.execute("PRAGMA index_list(equipment)").fetchall()]
foreign_keys = conn.execute("PRAGMA foreign_key_list(equipment)").fetchall()
conn.close()

for name in ("id", "location", "category", "manufacturer", "model", "serial_number",
             "quantity", "asset_status", "purchase_date", "vendor", "warranty_until",
             "notes", "created_at", "created_by"):
    check(f"equipment.{name} exists", name in cols)

# Who recorded the item. An item from before the column has nobody to name,
# so it must be allowed to say so - and to say it as NULL, not as admin 0.
created_by = cols.get("created_by")
check("created_by is an INTEGER, an admin's id", created_by is not None and created_by["type"] == "INTEGER")
check("created_by may be NULL", created_by is not None and created_by["notnull"] == 0)
check("created_by has no default to stand in for an admin",
      created_by is not None and created_by["dflt_value"] is None)
check("no foreign key is declared - a logical link, like software_categories.created_by",
      foreign_keys == [])

# An item belongs to a ROOM. A computer_id as well would be a second
# answer to "where is this", and the unmaintained one lies.
check("computer_id is GONE", "computer_id" not in cols)
check("location is NOT NULL", cols["location"]["notnull"] == 1)
check("category is NOT NULL", cols["category"]["notnull"] == 1)
check("quantity defaults to 1", cols["quantity"]["dflt_value"] == "1")
check("quantity is INTEGER", cols["quantity"]["type"] == "INTEGER")
check("location is indexed", any("location" in name for name in idx))

database.init_db()
conn = raw()
again = [r["name"] for r in conn.execute("PRAGMA table_info(equipment)").fetchall()]
conn.close()
check("init_db is idempotent", len(again) == len(cols))


print("\n=== 2. Location rules widened ===")
for name in ["Lab 1", "Computer Lab 1", "Computer Laboratory 1", "Lab 2 (Annex)", "I.T. Lab"]:
    value, error = appmod.normalize_location(name)
    check(f"location accepted: {name}", value == name and error is None)
value, error = appmod.normalize_location("x" * 31)
check("31 characters rejected", value is None and error is not None)
value, error = appmod.normalize_location("Lab <script>")
check("markup still rejected", value is None and error is not None)
value, error = appmod.normalize_location("Lab 1; DROP TABLE")
check("semicolon still rejected", value is None and error is not None)
value, error = appmod.normalize_location("  Lab   1  ")
check("whitespace still collapsed", value == "Lab 1")


print("\n=== 3. Field validation ===")
value, error = appmod.equipment_quantity("")
check("blank quantity means 1", value == 1 and error is None)
value, error = appmod.equipment_quantity("12")
check("numeric quantity accepted", value == 12)
value, error = appmod.equipment_quantity("0")
check("zero rejected", value is None and error is not None)
value, error = appmod.equipment_quantity("-3")
check("negative rejected", value is None and error is not None)
value, error = appmod.equipment_quantity("1000")
check("over the cap rejected", value is None and error is not None)
value, error = appmod.equipment_quantity("two")
check("words rejected", value is None and error is not None)
value, error = appmod.equipment_quantity("1.5")
check("decimal rejected", value is None and error is not None)

value, error = appmod.equipment_text("", "Category", 30, required=True)
check("required blank rejected", value is None and error == "Category is required.")
value, error = appmod.equipment_text("", "Model", 60)
check("optional blank allowed", value is None and error is None)
value, error = appmod.equipment_text("A4Tech KR-85 (refurb)", "Model", 60)
check("real model names accepted", value == "A4Tech KR-85 (refurb)")
value, error = appmod.equipment_text("<script>", "Model", 60)
check("markup rejected", value is None and error is not None)

check("equipment_label joins both", appmod.equipment_label("Dell", "P2419H") == "Dell P2419H")
check("equipment_label handles one", appmod.equipment_label("Dell", None) == "Dell")
check("equipment_label handles neither", appmod.equipment_label(None, None) == "")

# Location is required, and goes through the same normaliser computers use.
value, error = appmod.equipment_location("Lab 1")
check("location accepted", value == "Lab 1" and error is None)
value, error = appmod.equipment_location("  Lab   1 ")
check("location whitespace collapsed", value == "Lab 1")
value, error = appmod.equipment_location("")
check("blank location rejected", value is None and "required" in error)
value, error = appmod.equipment_location("   ")
check("whitespace-only location rejected", value is None and error is not None)
value, error = appmod.equipment_location("Lab <script>")
check("markup in location rejected", value is None and error is not None)

# Serial and quantity above one are mutually exclusive - a serial names ONE
# object, so it cannot sit on a row standing for forty.
values, error = appmod.validate_equipment_fields(
    {"location": "Lab 1", "category": "Cable", "quantity": "40", "serial_number": "SN1"})
check("serial refused on a multi-quantity row", values is None)
check("the refusal explains the reason", error and "identifies one item" in error)
values, error = appmod.validate_equipment_fields(
    {"location": "Lab 1", "category": "Monitor", "quantity": "1", "serial_number": "SN1"})
check("serial accepted on a quantity-1 row", values["serial_number"] == "SN1")
values, error = appmod.validate_equipment_fields(
    {"location": "Lab 1", "category": "Cable", "quantity": "40"})
check("a multi-quantity row with no serial is fine", values["quantity"] == 40)
values, error = appmod.validate_equipment_fields(
    {"location": "Lab 1", "category": "Keyboard", "warranty_until": "nonsense"})
check("procurement validators reused", values is None and error is not None)
values, error = appmod.validate_equipment_fields({"category": "Keyboard"})
check("validation requires a location", values is None and "Location" in error)


print("\n=== 4. Adding equipment ===")
seed_computer("LAB-01", "Lab 1", "Desktop")
seed_computer("LAB-02", "Lab 1", "Desktop")
seed_computer("LAB-03", "Lab 2", "Laptop")

res = client.post("/equipment/add", data={
    "location": "Lab 1", "category": "Ethernet cable", "manufacturer": "Beldin",
    "model": "Cat 8", "quantity": "40", "asset_status": "In use",
    "purchase_date": "2024-01-05", "vendor": "CD-R King",
})
check("add redirects", res.status_code == 302)
rows = equipment_rows()
check("ONE row created for forty cables", len(rows) == 1)
check("quantity stored as forty", rows[0]["quantity"] == 40)
check("location stored on the row itself", rows[0]["location"] == "Lab 1")
check("procurement stored", rows[0]["vendor"] == "CD-R King")
check("no computer_id column exists any more",
      "computer_id" not in dict(rows[0]).keys())

res = client.post("/equipment/add", data={
    "location": "Lab 1", "category": "Keyboard", "manufacturer": "A4Tech",
    "model": "KR-85", "quantity": "25",
})
check("second row added", len(equipment_rows()) == 2)

# A single tracked object keeps quantity 1 and may carry a serial.
res = client.post("/equipment/add", data={
    "location": "Lab 2", "category": "Monitor", "manufacturer": "Dell",
    "model": "P2419H", "quantity": "1", "serial_number": "MON-4471",
    "asset_status": "In repair",
})
mon = [r for r in equipment_rows() if r["category"] == "Monitor"][0]
check("a quantity-1 row may carry a serial", mon["serial_number"] == "MON-4471")
check("condition stored", mon["asset_status"] == "In repair")

# --- a serial means quantity 1 ---------------------------------------
# A serial identifies ONE object. "40 cables, serial SN-1" would claim all
# forty are the same cable, which is worse than no serial because it looks
# like data.
before = len(equipment_rows())
res = client.post("/equipment/add", data={
    "location": "Lab 1", "category": "Cable", "quantity": "40",
    "serial_number": "SN-1",
})
check("a serial on a multi-quantity row is refused", len(equipment_rows()) == before)
check("the refusal explains why",
      "serial" in unquote_plus(res.headers.get("Location", "")).lower())

# --- location is required and normalised ------------------------------
res = client.post("/equipment/add", data={"category": "Mouse", "quantity": "1"})
check("missing location is refused", "error=" in res.headers.get("Location", ""))
check("the refusal names location",
      "location" in unquote_plus(res.headers.get("Location", "")).lower())

res = client.post("/equipment/add", data={
    "location": "  Lab   1  ", "category": "Charger", "quantity": "3"})
charger = [r for r in equipment_rows() if r["category"] == "Charger"][0]
check("location whitespace is collapsed, so Lab 1 stays ONE room",
      charger["location"] == "Lab 1")

res = client.post("/equipment/add", data={
    "location": "Lab <script>", "category": "Mouse", "quantity": "1"})
check("markup in location is refused", "error=" in res.headers.get("Location", ""))

res = client.post("/equipment/add", data={"location": "Lab 1", "category": ""})
check("blank category is refused", "error=" in res.headers.get("Location", ""))

res = client.post("/equipment/add", data={
    "location": "Lab 1", "category": "Mouse", "quantity": "0"})
check("zero quantity is refused", "error=" in res.headers.get("Location", ""))

# A room nobody has a computer in is still a valid place to keep things.
res = client.post("/equipment/add", data={
    "location": "Storage room", "category": "Mouse", "quantity": "6"})
spare = [r for r in equipment_rows() if r["location"] == "Storage room"]
check("equipment can live in a room with no computers", len(spare) == 1)


print("\n=== 5. The page renders ===")
res = client.get("/equipment")
html = res.get_data(as_text=True)
check("equipment page 200", res.status_code == 200)
check("item shown", "Beldin Cat 8" in html)
check("location shown", "Lab 1" in html and "Lab 2" in html)
check("quantity shown", ">40<" in html)
check("NO Computer column", 'data-dt-label="Computer"' not in html)
check("Location column present", 'data-dt-label="Location"' in html)
check("add button present", 'id="equip-add-button"' in html)
check("location picker present", 'id="equip-add-location"' in html)
check("NO computer checkbox list", 'name="computer_ids"' not in html)

# The header must count OBJECTS, not rows - one row reading 40 cables is
# forty things owned, and forty is the number a reader takes away.
total = sum(r["quantity"] for r in equipment_rows())
check(f"header counts objects ({total}), not rows", f"{total} items" in html)
check("header also names the record count", f"{len(equipment_rows())} record" in html)

def card_header(page_html):
    """The card's <header>, not base.html's topbar one - the page contains
    both, and splitting on the first </header> catches the wrong element."""
    before = page_html.split('class="card-title"')[0]
    start = before.rindex("<header")
    return page_html[start:page_html.index("</header>", start)]


head = card_header(html)
check("header is a plain <header>", "<header>" in head)
check("title uses card-title", '<h3 class="card-title">Equipment</h3>' in head)
check("Add button sits in a card-action span", 'class="card-action"' in head)

header_row = html.split("</thead>")[0]
heads = len(re.findall(r"<th\b", header_row))
first_row = html.split("<tbody>")[1].split("</tr>")[0]
cells = len(re.findall(r"<td\b", first_row))
check(f"header count == cell count ({heads} vs {cells})", heads == cells)

check("details enabled on the row menu", "data-dt-details" in html)
check("details drawer present", 'id="equip-details-drawer"' in html)
check("a delete confirmation dialog exists", 'data-dt-confirm="delete"' in html)
check("the dialog is inside the table root",
      html.index('data-dt-root') < html.index('data-dt-confirm="delete"'))
check("details panel contains no input",
      "<input" not in html.split('id="equip-details-drawer"')[1].split("</dialog>")[0])


def delete_audit(template_name):
    """Every delete action in a template needs a confirmation dialog.

    Scans SOURCE, so it covers templates this suite never renders.
    Comments are stripped first: prose naming these attributes would
    otherwise count as a use of them.
    """
    path = os.path.join(server_dir, "templates", template_name)
    src = open(path, encoding="utf-8", newline="").read()
    src = re.sub(r"\{#.*?#\}", "", src, flags=re.S)
    src = re.sub(r"<!--.*?-->", "", src, flags=re.S)
    return (
        len(re.findall(r'\sdata-dt-delete-url=', src)),
        len(re.findall(r'\sdata-dt-act="delete"', src)),
        len(re.findall(r'\sdata-dt-confirm="delete"', src)),
    )


for template_name in sorted(os.listdir(os.path.join(server_dir, "templates"))):
    if not template_name.endswith(".html"):
        continue
    row_delete, bulk_delete, confirms = delete_audit(template_name)
    if row_delete or bulk_delete:
        check(f"{template_name} confirms before deleting", confirms >= 1)

check("Equipment nav link rendered", "sb-label\">Equipment" in html.replace("\r", ""))
check("subnav panel not hidden on the equipment page",
      "hidden" not in html.split('id="dashboard-subnav"')[1][:80].split(">")[0])
other = client.get("/problem-reports").get_data(as_text=True)
check("subnav collapsed on an unrelated page",
      "hidden" in other.split('id="dashboard-subnav"')[1][:80].split(">")[0])


print("\n=== 6. Editing and moving rooms ===")
cable = [r for r in equipment_rows() if r["category"] == "Ethernet cable"][0]
res = client.post(f"/equipment/{cable['id']}/edit", data={
    "location": "Lab 2", "category": "Ethernet cable", "manufacturer": "Beldin",
    "model": "Cat 8", "quantity": "38", "asset_status": "In use",
})
after = [r for r in equipment_rows() if r["id"] == cable["id"]][0]
check("quantity updated", after["quantity"] == 38)
check("MOVED to another room", after["location"] == "Lab 2")
check("the message names the move",
      "moved to Lab 2" in unquote_plus(res.headers.get("Location", "")))

res = client.post(f"/equipment/{cable['id']}/edit", data={
    "location": "Lab 2", "category": "Ethernet cable", "quantity": "38",
    "serial_number": "SN-9"})
after = [r for r in equipment_rows() if r["id"] == cable["id"]][0]
check("serial still refused on a multi-quantity row when editing",
      after["serial_number"] is None)

res = client.post(f"/equipment/{cable['id']}/edit", data={
    "location": "", "category": "Ethernet cable", "quantity": "38"})
after = [r for r in equipment_rows() if r["id"] == cable["id"]][0]
check("blank location on edit is refused", after["location"] == "Lab 2")

res = client.post("/equipment/999999/edit", data={
    "location": "Lab 1", "category": "Keyboard"})
check("editing an unknown item is handled", "error=" in res.headers.get("Location", ""))


print("\n=== 7. Deleting ===")
target = [r for r in equipment_rows() if r["category"] == "Charger"][0]
before = len(equipment_rows())
res = client.post(f"/equipment/{target['id']}/delete")
check("delete redirects", res.status_code == 302)
check("row removed", len(equipment_rows()) == before - 1)

conn = raw()
entry = conn.execute(
    "SELECT * FROM audit_log WHERE action_type = 'equipment_deleted' ORDER BY id DESC LIMIT 1"
).fetchone()
conn.close()
check("delete is audited", entry is not None)
check("audit entry names the room", entry and "Lab 1" in (entry["details"] or ""))
check("audit entry names the quantity", entry and "3 x Charger" in (entry["details"] or ""))
check("equipment_deleted has a danger tone",
      appmod.AUDIT_ACTION_TONES.get("equipment_deleted") == "danger")

res = client.post("/equipment/999999/delete")
check("deleting an unknown item is handled", "error=" in res.headers.get("Location", ""))


print("\n=== 7b. Bulk actions ===")
# Rebuild a known set so the counts below are not at the mercy of what
# earlier sections happened to leave behind.
conn = raw()
conn.execute("DELETE FROM equipment")
conn.commit()
conn.close()
for room, cat, qty in [("Lab 1", "Keyboard", 25), ("Lab 1", "Mouse", 25),
                       ("Lab 2", "Monitor", 10), ("Lab 2", "Cable", 40),
                       ("Storage room", "Charger", 8)]:
    client.post("/equipment/add", data={
        "location": room, "category": cat, "quantity": str(qty)})
rows = equipment_rows()
check("five rows seeded for the bulk tests", len(rows) == 5)
ids = [str(r["id"]) for r in rows]

res = client.get("/equipment")
html = res.get_data(as_text=True)
check("action bar has a Delete button", 'data-dt-act="delete"' in html)
check("action bar has a condition select", 'id="equip-bulk-condition"' in html)
check("action bar has a move select", 'id="equip-bulk-location"' in html)
check("bulk delete posts to its own route", "/equipment/bulk-delete" in html)
check("bulk condition posts to its own route", "/equipment/bulk-condition" in html)
check("bulk move posts to its own route", "/equipment/bulk-location" in html)
check("the id field matches what the routes read",
      'data-dt-idfield="equipment_ids"' in html)
# Move offers only rooms that already exist: a typo in a bulk move would
# scatter several rows into an invented room at once.
check("move select offers existing rooms", ">Lab 2</option>" in html)
check("move select is not a free text box",
      'id="equip-bulk-location" name="location"' in html and
      "<select" in html.split('id="equip-bulk-location"')[0][-200:])

# --- bulk condition ---------------------------------------------------
res = client.post("/equipment/bulk-condition", data={
    "equipment_ids": ids[:2], "asset_status": "In repair"})
after = {r["id"]: r for r in equipment_rows()}
check("bulk condition redirects", res.status_code == 302)
check("selected rows changed", all(after[int(i)]["asset_status"] == "In repair" for i in ids[:2]))
check("unselected rows untouched", after[int(ids[2])]["asset_status"] is None)
check("the message names the count and status",
      "2 record(s) marked In repair" in unquote_plus(res.headers.get("Location", "")))

res = client.post("/equipment/bulk-condition", data={
    "equipment_ids": ids[:2], "asset_status": "Melted"})
check("an invalid condition is refused", "error=" in res.headers.get("Location", ""))
res = client.post("/equipment/bulk-condition", data={"asset_status": "In use"})
check("no selection is refused", "error=" in res.headers.get("Location", ""))

# --- bulk move --------------------------------------------------------
res = client.post("/equipment/bulk-location", data={
    "equipment_ids": ids[:3], "location": "Lab 2"})
after = {r["id"]: r for r in equipment_rows()}
check("selected rows moved", all(after[int(i)]["location"] == "Lab 2" for i in ids[:3]))
check("unselected row stayed put", after[int(ids[4])]["location"] == "Storage room")
check("the message names the room",
      "moved to Lab 2" in unquote_plus(res.headers.get("Location", "")))

res = client.post("/equipment/bulk-location", data={
    "equipment_ids": ids[:2], "location": "  Lab   1 "})
after = {r["id"]: r for r in equipment_rows()}
check("a bulk move normalises the room like a single edit does",
      after[int(ids[0])]["location"] == "Lab 1")
res = client.post("/equipment/bulk-location", data={
    "equipment_ids": ids[:2], "location": ""})
check("a blank room is refused", "error=" in res.headers.get("Location", ""))
res = client.post("/equipment/bulk-location", data={
    "equipment_ids": ids[:2], "location": "Lab <script>"})
check("markup in a bulk room is refused", "error=" in res.headers.get("Location", ""))

# --- bulk delete ------------------------------------------------------
conn = raw()
before_audit = conn.execute(
    "SELECT COUNT(*) n FROM audit_log WHERE action_type = 'equipment_deleted'").fetchone()["n"]
conn.close()

res = client.post("/equipment/bulk-delete", data={"equipment_ids": ids[:3]})
remaining = equipment_rows()
check("bulk delete removed exactly the selected rows", len(remaining) == 2)
check("the rows that survived are the unselected ones",
      sorted(r["id"] for r in remaining) == sorted(int(i) for i in ids[3:]))
check("the message names the count",
      "Deleted 3 equipment record(s)" in unquote_plus(res.headers.get("Location", "")))

conn = raw()
after_audit = conn.execute(
    "SELECT COUNT(*) n FROM audit_log WHERE action_type = 'equipment_deleted'").fetchone()["n"]
conn.close()
# One entry PER ROW, not one summary line: the trail is read to answer
# "what happened to that monitor", which a single "deleted 3 items" cannot.
check("each deleted row is audited separately", after_audit - before_audit == 3)

res = client.post("/equipment/bulk-delete", data={})
check("bulk delete with no selection is refused", "error=" in res.headers.get("Location", ""))
res = client.post("/equipment/bulk-delete", data={"equipment_ids": ["999999"]})
check("bulk delete of unknown ids is handled", "error=" in res.headers.get("Location", ""))
check("nothing was removed by that", len(equipment_rows()) == 2)

# Access control on all three.
anon_bulk = appmod.app.test_client()
for path in ("/equipment/bulk-delete", "/equipment/bulk-condition", "/equipment/bulk-location"):
    r = anon_bulk.post(path, data={"equipment_ids": ids[:1]})
    check(f"{path} requires login", r.status_code in (302, 401, 403))


print("\n=== 8. Equipment on the computers page ===")
# Seeds its own rows rather than inheriting whatever section 7b left: a
# section that depends on another's leftovers breaks when that one changes.
client.post("/equipment/add", data={
    "location": "Lab 1", "category": "Keyboard", "manufacturer": "A4Tech",
    "model": "KR-85", "quantity": "25"})
client.post("/equipment/add", data={
    "location": "Lab 2", "category": "Monitor", "manufacturer": "Dell",
    "model": "P2419H", "quantity": "1", "serial_number": "MON-4471"})

res = client.get("/computers")
html = res.get_data(as_text=True)
check("computers page still 200", res.status_code == 200)
check("equipment JSON block rendered", 'id="comp-equipment-data"' in html)
check("section is about the ROOM, not the desk", "Equipment in this room" in html)
check("no 'Attached equipment' wording remains", "Attached equipment" not in html)

blob = html.split('id="comp-equipment-data">')[1].split("</script>")[0]
data = json.loads(blob)
check("JSON parses", isinstance(data, dict))
check("keyed by ROOM NAME, not computer id", "Lab 1" in data)
check("not keyed by id", "1" not in data)
items = data["Lab 1"]
check("carries the category", any(i["category"] == "Keyboard" for i in items))
check("carries a joined label", any(i["label"] == "A4Tech KR-85" for i in items))
check("carries quantity", any(i["quantity"] == 25 for i in items))

# A quote in a manufacturer must not break out of the script block.
client.post("/equipment/add", data={
    "location": "Lab 2", "category": "Printer",
    "manufacturer": "O'Brien & Co", "model": "X/1", "quantity": "1"})
html = client.get("/computers").get_data(as_text=True)
blob = html.split('id="comp-equipment-data">')[1].split("</script>")[0]
data = json.loads(blob)
check("awkward characters survive as JSON",
      any("O'Brien" in i.get("label", "") for i in data.get("Lab 2", [])))
check("no raw </script> injected", "</script>" not in blob)

# --- source checks on the drawer's JavaScript --------------------------
# These read the template TEXT rather than running it. That is genuinely
# weaker than executing the script - it proves the wiring is spelled
# correctly, not that it behaves - but with no JS runner in this project
# it is the difference between "untested" and "would catch a rename".
# A mutation swapping the room key back to a computer id passed every
# other assertion in this file, which is why they exist.
comp_src = open(os.path.join(server_dir, "templates", "computers.html"),
                encoding="utf-8", newline="").read()
check("drawer keys equipment by ROOM, not by computer id",
      "renderEquipment(row.getAttribute('data-asset-location')" in comp_src)
check("drawer does NOT key by event id",
      "renderEquipment(event.detail.id)" not in comp_src)
check("the renderer takes a location parameter",
      "function renderEquipment(location)" in comp_src)
check("an empty room falls through to nothing, not to a default group",
      "(location && EQUIPMENT[location]) || []" in comp_src)
check("equipment values are set with textContent, never innerHTML",
      ".innerHTML" not in comp_src.split("function renderEquipment")[1].split("}")[0] + "")

equip_src = open(os.path.join(server_dir, "templates", "equipment.html"),
                 encoding="utf-8", newline="").read()
check("add drawer posts a location", 'id="equip-add-location-value"' in equip_src)
check("add drawer has no computer picker", "computer_ids" not in equip_src)
check("edit drawer posts a location", 'id="equip-edit-location" name="location"' in equip_src)
check("the serial rule keys off quantity, not a computer count",
      "parseInt(quantity.value, 10)" in equip_src)

print("\n=== 8b. Audit pill tone and notification links ===")
# The Audit Trail reads app.py's severity map rather than keeping its own
# list, so one place decides what renders red.
check("equipment_deleted resolves to the bad pill tone",
      appmod.audit_tone("equipment_deleted") == "bad")
check("software_deleted still bad", appmod.audit_tone("software_deleted") == "bad")
check("account_deleted still bad", appmod.audit_tone("account_deleted") == "bad")
check("password_reset is warn", appmod.audit_tone("password_reset") == "warn")
check("deployment_created is info", appmod.audit_tone("deployment_created") == "info")
check("an unregistered action falls back to good",
      appmod.audit_tone("equipment_added") == "good")
check("an unknown action does not raise", appmod.audit_tone("nonsense_action") == "good")

# The severity map and the pill map must not drift apart: every severity
# named in one needs a translation in the other.
for severity in set(appmod.AUDIT_ACTION_TONES.values()) | {"neutral"}:
    check(f"severity '{severity}' has a pill tone",
          severity in appmod.AUDIT_PILL_TONES)

audit_src = open(os.path.join(server_dir, "templates", "audit_trail.html"),
                 encoding="utf-8", newline="").read()
check("audit_trail.html uses the filter", "| audit_tone" in audit_src)
check("audit_trail.html no longer keeps its own severity list",
      'action_type"] in ["account_deleted"' not in audit_src)

# Notification links must land where the thing lives: a stale link errors
# nowhere, it just lands you somewhere unrelated.
app_src = open(os.path.join(server_dir, "app.py"), encoding="utf-8", newline="").read()
new_computer = app_src.split('f"New computer registered')[1][:300]
check("the new-computer notification links to Lab Computers",
      'url_for("computers_page")' in new_computer)
offline = app_src.split('severity="warning",\r\n            link_url=')[1][:60] \
    if 'severity="warning",\r\n            link_url=' in app_src else ""
check("the went-offline notification links to Lab Computers",
      'url_for("computers_page")' in offline)
# The deployment ones are about jobs, and the jobs table IS on the
# dashboard, so "/" is correct there rather than an oversight.
check("deployment notifications still point at the dashboard",
      app_src.count('link_url="/"') == 2)


print("\n=== 8c. Notification read state ===")
# Read state is a single high-water mark on the admin row, not a flag per
# notification. That is why "open one link" cannot clear only that one -
# marking anything read necessarily marks everything older read too.
conn = raw()
conn.execute("DELETE FROM notifications")
for i in range(3):
    conn.execute(
        "INSERT INTO notifications (event_type, message, severity, link_url, audience) "
        "VALUES ('computer_status', ?, 'warning', '/computers', 'admin')",
        (f"Test notification {i}",))
conn.execute("UPDATE admins SET last_seen_notification_id = 0 WHERE id = ?", (admin["id"],))
conn.commit()
conn.close()

res = client.get("/api/notifications")
payload = res.get_json()
check("notifications API responds", res.status_code == 200)
check("three unread reported", payload["unread_count"] == 3)

# A beacon posts text/plain with an empty body. The route reads no body at
# all, so it must not care - but that is worth pinning, because a future
# request.get_json() there would break the unload path silently.
res = client.post("/api/notifications/mark-read",
                  data=b"", content_type="text/plain")
check("a beacon-shaped POST is accepted", res.status_code == 200)
check("it reports zero unread", res.get_json()["unread_count"] == 0)

res = client.get("/api/notifications")
check("the watermark really moved", res.get_json()["unread_count"] == 0)

conn = raw()
conn.execute(
    "INSERT INTO notifications (event_type, message, severity, audience) "
    "VALUES ('computer_status', 'Arrived after reading', 'info', 'admin')")
conn.commit()
conn.close()
res = client.get("/api/notifications")
check("a notification arriving after the mark is unread again",
      res.get_json()["unread_count"] == 1)

anon_n = appmod.app.test_client()
check("mark-read requires login",
      anon_n.post("/api/notifications/mark-read").status_code in (302, 401, 403))

# Source checks on the frontend, since there is no JS runner here.
notif_js = open(os.path.join(server_dir, "static", "js", "notifications.js"),
                encoding="utf-8", newline="").read()
check("the panel marks read when it CLOSES", "if (unreadRendered > 0) {" in notif_js)
check("mark-on-close sits inside closePanel",
      "markRead();" in notif_js.split("function closePanel")[1].split("function ")[0])
check("following a link marks read too", "markReadOnUnload();" in notif_js)
check("the unload path uses sendBeacon", "navigator.sendBeacon" in notif_js)
check("it falls back to keepalive fetch", "keepalive: true" in notif_js)
check("it does nothing when there is nothing unread",
      "if (unreadRendered === 0) {" in notif_js)
check("the unread count is tracked from the rendered rows",
      "unreadRendered = unread;" in notif_js)


print("\n=== 8d. Notification deep links ===")
seed_computer("NOTIF-PC", "Lab 1", "Desktop")
conn = raw()
conn.execute("DELETE FROM notifications")
conn.commit()
conn.close()

# A report filed through the agent API must produce a notification that
# lands on THAT report (?open=<id>), not on a table of thirty-six rows.
res = client.post("/api/agent/problem-reports", json={
    "computer_hostname": "NOTIF-PC", "problem_type": "keyboard",
    "description": "The spacebar has stopped responding entirely.",
})
check("agent report accepted", res.status_code in (200, 201))
report_id = res.get_json().get("report_id")
check("the API returns the new report id", isinstance(report_id, int))

conn = raw()
note = conn.execute(
    "SELECT * FROM notifications WHERE event_type = 'problem_report' ORDER BY id DESC LIMIT 1"
).fetchone()
conn.close()
check("a notification was created", note is not None)
check("its link names the specific report",
      note and note["link_url"] == f"/problem-reports?open={report_id}")
check("it is not the bare table link",
      note and note["link_url"] != "/problem-reports")

# And that link must actually work when followed.
res = client.get(note["link_url"])
check("following the deep link renders the page", res.status_code == 200)
page = res.get_data(as_text=True)
check("the page hands the id to its script",
      f"var opening = {report_id};" in page)
check("a plain visit with no ?open= hands it null",
      "var opening = null;" in client.get("/problem-reports").get_data(as_text=True))

# --- the backfill -----------------------------------------------------
# Stored rows keep an old link and land on the dashboard, which reads as
# the bug still being there, so the backfill rewrites them.
conn = raw()
conn.execute("DELETE FROM notifications")
conn.execute("INSERT INTO notifications (event_type, message, severity, link_url, audience) "
             "VALUES ('computer_status', 'LAB1-PC05 went offline', 'warning', '/', 'admin')")
conn.execute("INSERT INTO notifications (event_type, message, severity, link_url, audience) "
             "VALUES ('computer_status', 'New computer registered: X', 'info', '/', 'admin')")
# Must NOT be touched: a deployment notification belongs on the dashboard.
conn.execute("INSERT INTO notifications (event_type, message, severity, link_url, audience) "
             "VALUES ('deployment_batch', 'Deployment batch finished', 'warning', '/', 'admin')")
# Must NOT be touched: already correct.
conn.execute("INSERT INTO notifications (event_type, message, severity, link_url, audience) "
             "VALUES ('computer_status', 'Already fixed', 'info', '/computers', 'admin')")
conn.commit()
conn.close()

database.init_db()

conn = raw()
rows = {r["message"]: r["link_url"] for r in conn.execute("SELECT message, link_url FROM notifications")}
conn.close()
check("an offline notification is repointed", rows["LAB1-PC05 went offline"] == "/computers")
check("a new-computer notification is repointed",
      rows["New computer registered: X"] == "/computers")
check("a DEPLOYMENT notification is left alone - the jobs table is on the dashboard",
      rows["Deployment batch finished"] == "/")
check("an already-correct row is unchanged", rows["Already fixed"] == "/computers")

# Idempotent: after the first pass it matches nothing.
database.init_db()
conn = raw()
again = {r["message"]: r["link_url"] for r in conn.execute("SELECT message, link_url FROM notifications")}
conn.close()
check("the backfill is idempotent", again == rows)


print("\n=== 8e. Who recorded the item ===")
# A database from before created_by existed. The column is taken away from
# this throwaway copy, so the next init_db() meets the table as an existing
# install has it: without the column, and with items already in it.
conn = raw()
try:
    conn.execute("ALTER TABLE equipment DROP COLUMN created_by")
    conn.commit()
except sqlite3.OperationalError:
    pass  # No such column: section 1 has already failed on that.
before = [dict(r) for r in conn.execute("SELECT * FROM equipment ORDER BY id").fetchall()]
conn.close()
check("there are items from before the column to carry over",
      len(before) > 0 and "created_by" not in before[0])

database.init_db()
carried = [dict(r) for r in equipment_rows()]
check("an existing database gains created_by",
      len(carried) > 0 and all("created_by" in r for r in carried))
check("...and every item already there names nobody",
      len(carried) > 0 and all(r.get("created_by") is None for r in carried))
check("...and is otherwise exactly as it was",
      [{k: v for k, v in r.items() if k != "created_by"} for r in carried] == before)

# Two admins, each with their own session - one admin adding twice could not
# tell "the signed-in admin" from "some admin".
conn = raw()
second_id = conn.execute(
    "INSERT INTO admins (username, password_hash, role) VALUES ('equip_second', 'x', 'admin')"
).lastrowid
conn.commit()
conn.close()
second = appmod.app.test_client()
with second.session_transaction() as sess:
    sess["admin_id"] = second_id
    sess["admin_role"] = "admin"
    sess["admin_username"] = "equip_second"
check("the second admin is a different account", second_id != admin["id"])


def recorded_by(category):
    found = [r["created_by"] for r in equipment_rows() if r["category"] == category]
    return found[0] if len(found) == 1 else f"{len(found)} rows"


client.post("/equipment/add", data={"location": "Lab 1", "category": "Projector", "quantity": "1"})
second.post("/equipment/add", data={"location": "Lab 1", "category": "Webcam", "quantity": "2"})
check("an added item names the admin who was signed in", recorded_by("Projector") == admin["id"])
check("...whichever admin that was", recorded_by("Webcam") == second_id)

# Who RECORDED it, not who touched it last.
projector = [r for r in equipment_rows() if r["category"] == "Projector"][0]
second.post(f"/equipment/{projector['id']}/edit", data={
    "location": "Lab 2", "category": "Projector", "quantity": "1"})
after = [r for r in equipment_rows() if r["id"] == projector["id"]][0]
check("another admin's edit goes through", after["location"] == "Lab 2")
check("...and the item still names the admin who recorded it", after["created_by"] == admin["id"])

old = carried[0]
second.post(f"/equipment/{old['id']}/edit", data={
    "location": old["location"], "category": old["category"], "quantity": str(old["quantity"] + 1)})
after = [r for r in equipment_rows() if r["id"] == old["id"]][0]
check("an item from before the column can still be edited", after["quantity"] == old["quantity"] + 1)
check("...and still names nobody - the editor did not record it", after["created_by"] is None)

# Column only: nothing on the page is built from it, and the page must not
# mind the extra field on every row.
check("the equipment page still renders", client.get("/equipment").status_code == 200)

# A logical link, the same as software_categories.created_by: deleting the
# account leaves the id on the item.
client.post(f"/admins/{second_id}/delete")
conn = raw()
account_left = conn.execute("SELECT 1 FROM admins WHERE id = ?", (second_id,)).fetchone()
conn.close()
check("the second admin's account is deleted", account_left is None)
check("...and the item they recorded still carries their id", recorded_by("Webcam") == second_id)


print("\n=== 9. Access control ===")
anon = appmod.app.test_client()
for path, method in [("/equipment", "get"), ("/equipment/add", "post"),
                     (f"/equipment/{target['id']}/edit", "post"),
                     (f"/equipment/{target['id']}/delete", "post")]:
    res = getattr(anon, method)(path)
    check(f"{method.upper()} {path} requires login", res.status_code in (302, 401, 403))

faculty = appmod.app.test_client()
with faculty.session_transaction() as sess:
    sess["admin_id"] = admin["id"]
    sess["admin_role"] = "faculty"
res = faculty.get("/equipment")
check("faculty cannot reach the equipment page", res.status_code in (302, 403))

check("equipment routes NOT reachable on the public host",
      "equipment_page" not in appmod.PUBLIC_ALLOWED_ENDPOINTS
      and "add_equipment" not in appmod.PUBLIC_ALLOWED_ENDPOINTS)


print("\n=== 10. Deployment is unaffected ===")
res = client.get("/deploy")
check("deploy page still renders", res.status_code == 200)
res = client.get("/computers")
check("computers page still renders", res.status_code == 200)

print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)

shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)
"""The installations table: one row per thing on a PC, recognised by
record_key and unique per computer, with an idempotent migration. Runs
database.py alone on a file-backed database.
"""
import os
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


def database_path():
    for index, arg in enumerate(sys.argv):
        if arg == "--database" and index + 1 < len(sys.argv):
            return os.path.abspath(sys.argv[index + 1])
    return os.path.join(ROOT, "server", "database.py")


SOURCE = database_path()
if not os.path.isfile(SOURCE):
    print("COULD NOT RUN - server/database.py was not found.")
    sys.exit(2)

work = tempfile.mkdtemp(prefix="appease_inst_")
shutil.copy(SOURCE, os.path.join(work, "database.py"))
sys.path.insert(0, work)
import database  # noqa: E402

if not hasattr(database, "installation_record_key"):
    print("COULD NOT RUN - database.py predates the installations table.")
    sys.exit(3)

raw_key = database.installation_record_key
database.DB_PATH = os.path.join(work, "appease.db")


class Refused(str):
    """What key() returns when the builder refuses: equal to nothing, not
    even another refusal, so a check that expected a key fails by name
    instead of crashing the suite."""

    def __eq__(self, other):
        return False

    def __ne__(self, other):
        return True

    def __hash__(self):
        return id(self)


def key(*args, **kwargs):
    try:
        return raw_key(*args, **kwargs)
    except ValueError as exc:
        return Refused(f"<refused: {exc}>")


def raises(label, **fields):
    try:
        raw_key(**fields)
    except ValueError:
        check(label, True)
        return
    check(label, False)


# ---------------------------------------------------------------------------
print("1. record_key: one thing, one key, whatever the capitalisation")
# ---------------------------------------------------------------------------
GUID = "{0aa2c178-bf99-3f72-ba87-f6123a1a95c8}"
SID = "S-1-5-21-1111111111-2222222222-3333333333-1001"

check("choco: the package id, lowercased",
      key("choco", package_id="VLC.install") == "choco:vlc.install")
check("choco: any capitalisation of an id is the same key",
      key("choco", package_id="Paint.NET") == key("choco", package_id="paint.net"))
check("choco: a wrapper and the package that owns the entry are different keys",
      key("choco", package_id="vlc") != key("choco", package_id="vlc.install"))
check("choco: stays a Chocolatey key even when the package installed an MSI",
      key("choco", package_id="paint.net", product_code=GUID) == "choco:paint.net")

check("msi: braces kept, hex uppercased",
      key("msi", product_code=GUID) == "msi:{0AA2C178-BF99-3F72-BA87-F6123A1A95C8}")
check("msi: lower, upper and mixed case are one key",
      len({key("msi", product_code=g) for g in (GUID, GUID.upper(),
                                                 "{0Aa2C178-bF99-3f72-Ba87-f6123A1a95C8}")}) == 1)
check("msi: with or without braces is one key",
      key("msi", product_code=GUID.strip("{}")) == key("msi", product_code=GUID))
check("msi: surrounding spaces do not matter",
      key("msi", product_code="  " + GUID + " ") == key("msi", product_code=GUID))
check("msi: an EXE whose entry IS a Windows Installer product shares the MSI's key",
      key("exe", product_code=GUID.upper(), hive="HKLM", view="Registry64", key_name=GUID)
      == key("msi", product_code=GUID))
check("msi: different products are different keys",
      key("msi", product_code=GUID) != key("msi", product_code=GUID.replace("c8}", "c9}")))

hklm = dict(hive="HKEY_LOCAL_MACHINE", view="Registry64")
check("exe: HKLM keeps its view and folds the key name",
      key("exe", key_name="VLC media player", **hklm) == "exe:HKLM:Registry64:VLC MEDIA PLAYER")
check("exe: a key name in any capitalisation is the same key",
      key("exe", key_name="GIMP-3_is1", **hklm) == key("exe", key_name="gimp-3_IS1", **hklm))
check("exe: HKLM and HKEY_LOCAL_MACHINE, any case, are the same hive",
      key("exe", key_name="App", hive="hklm", view="registry64")
      == key("exe", key_name="App", **hklm))
check("exe: the 32- and 64-bit HKLM keys are DIFFERENT keys",
      key("exe", key_name="App", hive="HKLM", view="Registry32")
      != key("exe", key_name="App", hive="HKLM", view="Registry64"))
check("exe: per-user carries the SID, uppercased, and no view",
      key("exe", key_name="GIMP-3_is1", hive="HKEY_CURRENT_USER", sid=SID.lower(), view="Registry32")
      == f"exe:HKCU:{SID}:GIMP-3_IS1")
check("exe: one per-user entry read through either view is ONE key",
      key("exe", key_name="App", hive="HKCU", sid=SID, view="Registry32")
      == key("exe", key_name="App", hive="HKCU", sid=SID, view="Registry64"))
check("exe: the same per-user key for two users is two keys",
      key("exe", key_name="App", hive="HKCU", sid=SID)
      != key("exe", key_name="App", hive="HKCU", sid=SID.replace("1001", "1002")))
check("exe: per-user and machine-wide entries with one name are different keys",
      key("exe", key_name="App", hive="HKCU", sid=SID) != key("exe", key_name="App", **hklm))
check("exe: case folding is Windows' one-to-one kind - 'ß' is not 'SS'",
      key("exe", key_name="Straße", **hklm) != key("exe", key_name="STRASSE", **hklm))
check("exe: ...and non-ASCII still folds where Windows folds it",
      key("exe", key_name="Éditeur", **hklm) == key("exe", key_name="éditeur", **hklm))
check("exe: a GUID-named entry that is not an MSI product stays an exe key",
      key("exe", key_name=GUID + "_is1", **hklm).startswith("exe:HKLM:"))

check("no entry: one key per library row",
      key("exe", software_id=12) == "none:software:12" == key("msi", software_id=12))
check("no entry: a Chocolatey record naming no package is the same 'none' row",
      key("choco", software_id=12) == "none:software:12")

raises("rejects an unknown method", method="zip", package_id="x")
raises("rejects a choco record with no package id", method="choco")
raises("rejects a package id that is not a plain id", method="choco", package_id="vlc&whoami")
raises("rejects a malformed ProductCode", method="msi", product_code="{0AA2C178-BF99}")
raises("rejects one brace without the other", method="msi", product_code=GUID[:-1])
raises("rejects a key name with a backslash", method="exe", key_name="a\\b", **hklm)
raises("rejects an empty key name", method="exe", key_name="", **hklm)
raises("rejects a key name with a control character", method="exe", key_name="a\nb", **hklm)
raises("rejects a key name longer than Windows allows", method="exe", key_name="a" * 256, **hklm)
check("accepts a key name exactly as long as Windows allows",
      key("exe", key_name="a" * 255, **hklm).endswith("A" * 255))
raises("rejects an unknown hive", method="exe", key_name="App", hive="HKEY_USERS", view="Registry64")
raises("rejects HKLM without a view", method="exe", key_name="App", hive="HKLM")
raises("rejects HKLM with a user SID", method="exe", key_name="App", sid=SID, **hklm)
raises("rejects per-user with no SID", method="exe", key_name="App", hive="HKCU")
raises("rejects per-user with an unreadable SID", method="exe", key_name="App", hive="HKCU", sid="S-1-5-..\\x")
raises("rejects an msi record naming a registry key but no ProductCode",
       method="msi", key_name="App", **hklm)
raises("rejects a Chocolatey record naming a ProductCode but no package",
       method="choco", product_code=GUID, software_id=12)
raises("rejects 'no entry' without a software id", method="exe")
raises("rejects 'no entry' with a software id of True", method="exe", software_id=True)
raises("rejects 'no entry' with a software id of 0", method="exe", software_id=0)


# ---------------------------------------------------------------------------
print("2. The table and its unique rule, on a real database file")
# ---------------------------------------------------------------------------
database.init_db()
db = sqlite3.connect(database.DB_PATH)
db.row_factory = sqlite3.Row

columns = {row["name"]: row for row in db.execute("PRAGMA table_info(installations)")}
wanted = ("id computer_id software_id software_name software_version method record_key "
          "choco_package_id msi_product_code reg_hive reg_user_sid reg_view reg_key_name "
          "display_name display_version install_location uninstall_string "
          "quiet_uninstall_string install_job_id uninstall_job_id state state_reason "
          "created_at updated_at state_changed_at").split()
check("every column the design calls for exists", all(c in columns for c in wanted))
check("computer_id, method and record_key are required",
      all(columns[c]["notnull"] for c in ("computer_id", "method", "record_key")))
check("software_id and uninstall_job_id may be NULL",
      not columns["software_id"]["notnull"] and not columns["uninstall_job_id"]["notnull"])

indexes = {row["name"]: row for row in db.execute("PRAGMA index_list(installations)")}
unique = indexes.get("idx_installations_record")
check("the record index exists and is UNIQUE", unique is not None and unique["unique"] == 1)
check("...on exactly (computer_id, record_key), in that order",
      [r["name"] for r in db.execute("PRAGMA index_info(idx_installations_record)")]
      == ["computer_id", "record_key"])
check("no foreign keys are declared, as everywhere else in this schema",
      db.execute("PRAGMA foreign_key_list(installations)").fetchall() == [])


def add(computer_id, record_key, **extra):
    fields = {"computer_id": computer_id, "method": "exe", "record_key": record_key, **extra}
    names = ", ".join(fields)
    marks = ", ".join("?" * len(fields))
    try:
        db.execute(f"INSERT INTO installations ({names}) VALUES ({marks})", list(fields.values()))
        return True
    except sqlite3.IntegrityError:
        return False


check("a first row goes in",
      add(1, key("exe", key_name="VLC media player", **hklm), reg_key_name="VLC media player"))
row = db.execute("SELECT * FROM installations WHERE computer_id = 1").fetchone()
check("a new row defaults to installed", row["state"] == "installed")
check("a new row has its timestamps", all(row[c] for c in ("created_at", "updated_at", "state_changed_at")))
check("the registry name is kept as Windows wrote it", row["reg_key_name"] == "VLC media player")

check("the same entry reported in other capitals on the same PC is refused",
      not add(1, key("exe", key_name="vlc MEDIA player", **hklm)))
check("the same entry on another PC is its own row",
      add(2, key("exe", key_name="VLC media player", **hklm)))
check("the other view's entry on the same PC is its own row",
      add(1, key("exe", key_name="VLC media player", hive="HKLM", view="Registry32")))
check("an MSI reported as lowercase then uppercase is refused the second time",
      add(1, key("msi", product_code=GUID)) and not add(1, key("msi", product_code=GUID.upper())))
check("...and the same product via an EXE on that PC is refused too",
      not add(1, key("exe", product_code=GUID.strip("{}"), key_name=GUID, **hklm)))
check("a per-user entry read through the other view is refused",
      add(1, key("exe", key_name="App", hive="HKCU", sid=SID, view="Registry64"))
      and not add(1, key("exe", key_name="App", hive="HKCU", sid=SID.lower(), view="Registry32")))
check("choco ids in two capitalisations on one PC are refused",
      add(1, key("choco", package_id="paint.net"), method="choco")
      and not add(1, key("choco", package_id="Paint.NET"), method="choco"))
check("a second 'no entry' row for one library row on one PC is refused",
      add(3, key("exe", software_id=7), state="unavailable")
      and not add(3, key("exe", software_id=7), state="unavailable"))
db.commit()
db.close()


# ---------------------------------------------------------------------------
print("3. The migration runs twice and changes nothing")
# ---------------------------------------------------------------------------
def init_runs(label):
    try:
        database.init_db()
        check(label, True)
    except Exception as exc:  # noqa: BLE001 - a crashing migration IS the failure
        check(f"{label} ({type(exc).__name__}: {exc})", False)


def dump(path, skip_table=None):
    conn = sqlite3.connect(path)
    lines = [line for line in conn.iterdump()
             if not (skip_table and skip_table in line)]
    conn.close()
    return lines


before = dump(database.DB_PATH)
init_runs("the migration runs a second time without error")
check("a second run on a database WITH installations changes nothing at all",
      dump(database.DB_PATH) == before)
init_runs("...and a third")
check("...nor does a third", dump(database.DB_PATH) == before)

# A database from before this table existed: everything else populated,
# no installations table, no indexes for it.
old_path = os.path.join(work, "old.db")
database.DB_PATH = old_path
database.init_db()
conn = sqlite3.connect(old_path)
conn.executescript("""
    DROP INDEX idx_installations_record;
    DROP INDEX idx_installations_software;
    DROP TABLE installations;
    INSERT INTO computers (hostname, status) VALUES ('LAB1-PC01', 'online');
    INSERT INTO software (name, version, type, install_command) VALUES ('VLC', '3.0.21', 'online', 'choco install vlc -y');
    INSERT INTO jobs (computer_hostname, software_id, status, software_name) VALUES ('LAB1-PC01', 1, 'completed', 'VLC');
    INSERT INTO audit_log (action_type, details) VALUES ('deployment_created', 'Deployed VLC');
""")
conn.commit()
conn.close()
check("(the stand-in old database really has no installations table)",
      all("installations" not in line for line in dump(old_path)))
old_rest = dump(old_path)
init_runs("the migration runs on an old database")
upgraded = dump(old_path)
check("upgrading an old database creates the table",
      any('CREATE TABLE "installations"' in line or "CREATE TABLE installations" in line for line in upgraded))
check("...and its unique index", any("idx_installations_record" in line for line in upgraded))
check("...and leaves every other row and table exactly as it was",
      [line for line in upgraded if "installations" not in line] == old_rest)
init_runs("...and again on the upgraded one")
check("running it again on the upgraded database changes nothing", dump(old_path) == upgraded)


print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for label in failed:
    print("  -", label)
print("=" * 56)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

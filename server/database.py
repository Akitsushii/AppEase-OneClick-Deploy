import os
import re
import sqlite3
from werkzeug.security import generate_password_hash

# Beside this file, not the working directory: a relative path launched
# from elsewhere silently creates a new, empty appease.db.
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "appease.db")

def get_db():
    """Open a connection to appease.db. Every call opens a NEW one and
    nothing closes them, so WAL lets readers carry on during a write (a
    file property that persists), and busy_timeout queues a write rather
    than failing (sqlite3's timeout covers fewer paths). appease.db-wal and
    -shm are normal working files, not backups."""
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
    except sqlite3.Error:
        # A locked database at connect time must not take the whole app
        # down - the caller's own query will surface the problem instead.
        pass
    return conn

def create_default_admin(db):
    existing_admin = db.execute("SELECT id FROM admins LIMIT 1").fetchone()
    if existing_admin:
        return

    # Must satisfy the password policy the app now enforces - a seeded
    # default that violates its own rules is the first thing anyone looks
    # for. Only applies to a FRESH database: this function returns early if
    # any admin already exists, so existing installs keep their old password
    # and should change it from the Profile page.
    #
    # must_change_password = 1 because that password is in the source code:
    # the first sign-in goes straight to set-password, and nothing else is
    # reachable until a new one is chosen. Existing installs are unaffected
    # for the same reason as above - this never runs once any account exists.
    password_hash = generate_password_hash("AppEase@2026")
    db.execute(
        "INSERT INTO admins (username, password_hash, first_name, last_name, created_at, "
        "must_change_password) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, 1)",
        ("admin", password_hash, "System", "Administrator"),
    )
    print("Default admin created - username: admin password: AppEase@2026 "
          "(a new password is required at first sign-in)")


# --- What AppEase installed, and how to recognise it again -----------------
#
# One installations row per thing AppEase put on a computer, keyed on
# (computer_id, record_key). record_key names the THING on the PC, never the
# Software Library row: two library rows that install the same VLC point at
# one registry entry, and they must land on one row, not two.
#
# Everything that goes into the key is normalised here, so the unique index
# compares what Windows compares. Windows treats registry key names, MSI
# ProductCodes, SIDs and Chocolatey ids as case-insensitive, and reports them
# in whatever case the installer wrote - so the key folds case the way each
# one is compared, and the row keeps the original spelling for display.
#
#   choco        "choco:" + package id, lowercased (ids are plain ASCII)
#                - the package that OWNS the registry entry, e.g. vlc.install
#                  rather than the vlc wrapper (worked out by the agent).
#                Always this, even when the package installed an MSI: what
#                Chocolatey installed, Chocolatey must remove, or its records
#                and the PC disagree - the very drift the reinstall check
#                exists for. A product_code passed with it is ignored here.
#   msi          "msi:{GUID}" - braces optional on input, hex uppercased.
#                Also used for an EXE whose new entry IS a Windows Installer
#                product (pass its product_code): the product is the thing on
#                the PC, whichever library row installed it.
#   exe          "exe:HKLM:<view>:<KEY NAME>"
#                "exe:HKCU:<SID>:<KEY NAME>"
#                Key name folded by simple per-character uppercase, which is
#                how Windows compares registry names (full case folding would
#                make "Straße" equal "STRASSE"; Windows does not). The SID is
#                uppercased. HKCU carries no view: the per-user Uninstall key
#                is shared between the 32- and 64-bit views, so one entry read
#                through both must be one row. HKLM's two views are different
#                keys, so its view stays in.
#   no entry     "none:software:<id>" - an install that left nothing usable
#                to recognise it by (state unavailable). One per computer per
#                library row, so a redeploy updates it instead of adding more.
#                Chocolatey too, when the agent could not name the package
#                that owns the entry: it then sends no package id at all.
#
# Raises ValueError on anything not understood. Nothing here guesses.
INSTALLATION_METHODS = ("choco", "msi", "exe")

# The shipped categories, in dropdown order, each with its own colour and
# icon (deploy-panel.js, software_library.html); admin-added ones get the
# neutral grey and generic icon. No built-in "Other": admins add the
# category they mean.
BUILTIN_SOFTWARE_CATEGORIES = (
    "Browser", "Development / IDE", "Multimedia", "Office / Productivity",
    "Utility", "Security / Antivirus", "Networking", "Design / Graphics",
    "Virtualization", "Runtime / Framework",
)
INSTALLATION_STATES = ("installed", "removed", "replaced", "unavailable")

_CHOCO_ID = re.compile(r"^[A-Za-z0-9._-]+$")
_GUID = re.compile(
    r"^(\{)?([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})(?(1)\})$"
)
_SID = re.compile(r"^[Ss]-1(?:-\d+)+$")
_HIVES = {
    "HKEY_LOCAL_MACHINE": "HKLM", "HKLM": "HKLM",
    "HKEY_CURRENT_USER": "HKCU", "HKCU": "HKCU",
}
_VIEWS = {"registry64": "Registry64", "registry32": "Registry32"}
# A registry key name: at most 255 characters, no backslash (that would be a
# path, not a name) and no control characters.
_KEY_NAME_MAX = 255


def normalise_product_code(value):
    """"{GUID}" in uppercase, from any capitalisation, braces or none."""
    match = _GUID.match((value or "").strip())
    if not match:
        raise ValueError(f"not a ProductCode: {value!r}")
    return "{" + match.group(2).upper() + "}"


def _registry_fold(name):
    # Simple, one-to-one uppercase per character - what the registry does.
    # str.upper() alone would turn one ß into "SS".
    return "".join(c.upper() if len(c.upper()) == 1 else c for c in name)


def installation_record_key(method, package_id=None, product_code=None, hive=None,
                            sid=None, view=None, key_name=None, software_id=None):
    """The record_key for one installations row. See the table above."""
    if method not in INSTALLATION_METHODS:
        raise ValueError(f"unknown method: {method!r}")

    if method == "choco" and package_id is not None:
        if not _CHOCO_ID.match(package_id):
            raise ValueError(f"not a Chocolatey package id: {package_id!r}")
        return "choco:" + package_id.lower()

    if method != "choco" and product_code is not None:
        return "msi:" + normalise_product_code(product_code)

    if method == "exe" and key_name is not None:
        if (not key_name or len(key_name) > _KEY_NAME_MAX or "\\" in key_name
                or any(ord(c) < 32 for c in key_name)):
            raise ValueError(f"not a registry key name: {key_name!r}")
        canonical_hive = _HIVES.get((hive or "").upper())
        if canonical_hive == "HKLM":
            canonical_view = _VIEWS.get((view or "").lower())
            if canonical_view is None:
                raise ValueError(f"HKLM entry without a 32/64-bit view: {view!r}")
            if sid:
                raise ValueError("HKLM entry with a user SID")
            return f"exe:HKLM:{canonical_view}:{_registry_fold(key_name)}"
        if canonical_hive == "HKCU":
            if not sid or not _SID.match(sid):
                raise ValueError(f"per-user entry without a readable SID: {sid!r}")
            return f"exe:HKCU:{sid.upper()}:{_registry_fold(key_name)}"
        raise ValueError(f"unknown hive: {hive!r}")

    if package_id is None and key_name is None and sid is None and hive is None and product_code is None:
        if isinstance(software_id, bool) or not isinstance(software_id, int) or software_id < 1:
            raise ValueError("an install with no entry needs its software_id")
        return f"none:software:{software_id}"

    raise ValueError(f"{method} record with the wrong identifying fields")


def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS computers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hostname TEXT UNIQUE NOT NULL,
            ip_address TEXT,
            last_seen TIMESTAMP,
            status TEXT DEFAULT 'offline'
        );

        CREATE TABLE IF NOT EXISTS software (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            version TEXT,
            type TEXT NOT NULL,
            install_command TEXT NOT NULL,
            uninstall_command TEXT,
            category TEXT,
            date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            computer_hostname TEXT NOT NULL,
            software_id INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS problem_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            computer_hostname TEXT NOT NULL,
            problem_type TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'open',
            reported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'admin'
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            message TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'info',
            link_url TEXT,
            audience TEXT NOT NULL DEFAULT 'admin',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_notifications_id_audience ON notifications(id, audience);

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_admin_id INTEGER,
            target_admin_id INTEGER,
            action_type TEXT NOT NULL,
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    try:
        db.execute("ALTER TABLE admins ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE software ADD COLUMN uninstall_command TEXT")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE software ADD COLUMN confidence_tier TEXT")
    except Exception:
        pass
    # The switch an EXE's own uninstaller is run with when the program lists
    # no quiet uninstall of its own. NULL: none.
    try:
        db.execute("ALTER TABLE software ADD COLUMN uninstall_switch TEXT")
    except Exception:
        pass
    # Software categories, in the database rather than a list in the code, so
    # an admin can add one. COLLATE NOCASE on the name: "browser" IS
    # "Browser", and the database itself refuses a second spelling.
    db.execute("""
        CREATE TABLE IF NOT EXISTS software_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            is_builtin INTEGER NOT NULL DEFAULT 0,
            position INTEGER,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Looked up first rather than INSERT OR IGNORE: with AUTOINCREMENT an
    # ignored insert still advances sqlite_sequence, so every start would
    # change the database - and a re-run of these migrations must not.
    for position, name in enumerate(BUILTIN_SOFTWARE_CATEGORIES):
        if not db.execute("SELECT 1 FROM software_categories WHERE name = ?", (name,)).fetchone():
            db.execute(
                "INSERT INTO software_categories (name, is_builtin, position) VALUES (?, 1, ?)",
                (name, position),
            )
    # Existing software keeps its category: a case-insensitive match takes
    # the known spelling, anything else becomes an admin category, never
    # moved to Other. Idempotent.
    for (value,) in db.execute(
        "SELECT DISTINCT category FROM software WHERE category IS NOT NULL AND TRIM(category) <> ''"
    ).fetchall():
        known = db.execute("SELECT name FROM software_categories WHERE name = ?", (value,)).fetchone()
        if known is None:
            db.execute("INSERT INTO software_categories (name, is_builtin) VALUES (?, 0)", (value,))
        elif known[0] != value:
            db.execute("UPDATE software SET category = ? WHERE category = ?", (known[0], value))
    # An older database's built-in "Other" is dropped if unused, else kept
    # as an admin category, so its software is never moved. Only the
    # BUILT-IN row is touched, so an admin's own "Other" stays.
    if db.execute("SELECT 1 FROM software_categories WHERE name = 'Other' AND is_builtin = 1").fetchone():
        if db.execute("SELECT 1 FROM software WHERE category = 'Other' COLLATE NOCASE").fetchone():
            db.execute(
                "UPDATE software_categories SET is_builtin = 0, position = NULL WHERE name = 'Other' AND is_builtin = 1"
            )
        else:
            db.execute("DELETE FROM software_categories WHERE name = 'Other' AND is_builtin = 1")
    try:
        db.execute("ALTER TABLE jobs ADD COLUMN started_at TIMESTAMP")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE jobs ADD COLUMN retry_count INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE admins ADD COLUMN last_seen_notification_id INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE admins ADD COLUMN full_name TEXT")
    except Exception:
        pass
    # A path RELATIVE to static/, like photo_paths, so url_for('static')
    # serves it with no new endpoint. NULL means no picture.
    try:
        db.execute("ALTER TABLE admins ADD COLUMN avatar_path TEXT")
    except Exception:
        pass
    # full_name is superseded by the three columns below, but never dropped.
    try:
        db.execute("ALTER TABLE admins ADD COLUMN first_name TEXT")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE admins ADD COLUMN middle_initial TEXT")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE admins ADD COLUMN last_name TEXT")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE jobs ADD COLUMN batch_id TEXT")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE problem_reports ADD COLUMN priority TEXT NOT NULL DEFAULT 'medium'")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE problem_reports ADD COLUMN assigned_to INTEGER")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE problem_reports ADD COLUMN notes TEXT")
    except Exception:
        pass
    # Where a computer lives and what kind it is: set by hand, never by the
    # heartbeat, so they survive re-registration. Plain NULL, so existing
    # computers start Unassigned rather than mislabelled.
    try:
        db.execute("ALTER TABLE computers ADD COLUMN location TEXT")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE computers ADD COLUMN device_type TEXT")
    except Exception:
        pass
    # --- Hardware asset tracking -----------------------------------------
    #
    # Two owners, and the split is the design: the first eight are reported
    # BY the machine (only /api/agent/inventory writes them), the last four
    # are typed by a technician and no agent overwrites them. ram_mb and
    # storage_gb are INTEGER so they sort; hardware_reported_at says whether
    # any of it is still true.
    try:
        db.execute("ALTER TABLE computers ADD COLUMN manufacturer TEXT")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE computers ADD COLUMN model TEXT")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE computers ADD COLUMN serial_number TEXT")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE computers ADD COLUMN cpu TEXT")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE computers ADD COLUMN ram_mb INTEGER")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE computers ADD COLUMN storage_gb INTEGER")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE computers ADD COLUMN os_name TEXT")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE computers ADD COLUMN hardware_reported_at TIMESTAMP")
    except Exception:
        pass
    # Technician-owned. NULL asset_status reads as "In use", so the migration
    # can never retire a machine out of the deploy picker.
    try:
        db.execute("ALTER TABLE computers ADD COLUMN asset_status TEXT")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE computers ADD COLUMN purchase_date TEXT")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE computers ADD COLUMN vendor TEXT")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE computers ADD COLUMN warranty_until TEXT")
    except Exception:
        pass
    # --- Agent identity ----------------------------------------------------
    #
    # agent_id is a random ID each agent keeps on disk, so same-named PCs get
    # their own rows; agent_hostname is the name it last reported (it
    # differs once a second one is registered as LAB-PC-2). NULL for older
    # rows and agents. UNIQUE, so one ID never owns two rows (NULLs repeat).
    try:
        db.execute("ALTER TABLE computers ADD COLUMN agent_id TEXT")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE computers ADD COLUMN agent_hostname TEXT")
    except Exception:
        pass
    try:
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_computers_agent_id ON computers(agent_id)")
    except Exception:
        pass
    # Reporter identity: /report has no session, so the form asks who is
    # filing. Plain NULL, so older reports read "Not recorded".
    try:
        db.execute("ALTER TABLE problem_reports ADD COLUMN reporter_role TEXT")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE problem_reports ADD COLUMN reporter_name TEXT")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE problem_reports ADD COLUMN reporter_student_number TEXT")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE problem_reports ADD COLUMN reporter_section TEXT")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE problem_reports ADD COLUMN reporter_year_level TEXT")
    except Exception:
        pass
    # Program apart from section: inside free text, "BSED 2B" and "BSIT 2B"
    # could not be read back reliably. Plain NULL; older reports show a
    # section with no program.
    try:
        db.execute("ALTER TABLE problem_reports ADD COLUMN reporter_program TEXT")
    except Exception:
        pass
    # Faculty give a department, students a program: separate columns, so
    # no value needs the role to be read back. Plain NULL.
    try:
        db.execute("ALTER TABLE problem_reports ADD COLUMN reporter_department TEXT")
    except Exception:
        pass
    # How the report reached us, for triage rather than access control:
    #   sticker - arrived with a valid signed QR token (physical presence in
    #             the lab is proven, because the token cannot be forged)
    #   lan     - no token, but the requester's IP matched a known computer
    #   direct  - neither: someone opened the public URL and typed it in
    # Plain NULL default, so every pre-existing row reads as unknown rather
    # than being silently relabelled as trusted.
    try:
        db.execute("ALTER TABLE problem_reports ADD COLUMN report_source TEXT")
    except Exception:
        pass
    # Up to three static-relative photo paths as a JSON array: the count can
    # change without a migration, and with no foreign keys on, a child table
    # would have nothing enforcing the link. report_photo_list() fails soft.
    try:
        db.execute("ALTER TABLE problem_reports ADD COLUMN photo_paths TEXT")
    except Exception:
        pass
    # status_changed_at says how long a report has sat in its status, which
    # the status cannot. resolution_notes is what was DONE, apart from the
    # working notes: the fix is the useful record. NULL for older reports,
    # which must not claim a fix they never recorded.
    try:
        db.execute("ALTER TABLE problem_reports ADD COLUMN status_changed_at TIMESTAMP")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE problem_reports ADD COLUMN resolution_notes TEXT")
    except Exception:
        pass
    # Email + forced-change support for admin-issued passwords.
    #
    # email is plain NULL: every account that exists today has none, and the
    # reset flow has to keep working for those - it shows the new password on
    # screen once instead of sending it.
    #
    # must_change_password CAN take a default because 0 is a constant. It is 1
    # only while an admin-issued password is still in force; the person's own
    # change clears it. Existing accounts default to 0, so nobody is locked
    # into a set-password screen by the migration itself.
    try:
        db.execute("ALTER TABLE admins ADD COLUMN email TEXT")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE admins ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass
    # ADD COLUMN takes only a constant default, so both are set at write time
    # (created_at on INSERT, last_login on login); older accounts show "-".
    try:
        db.execute("ALTER TABLE admins ADD COLUMN created_at TIMESTAMP")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE admins ADD COLUMN last_login TIMESTAMP")
    except Exception:
        pass
    # last_login is when they signed in, not whether they are here now;
    # last_active_at is touched by every request, so it decays on its own.
    # NULL renders as "Never".
    try:
        db.execute("ALTER TABLE admins ADD COLUMN last_active_at TIMESTAMP")
    except Exception:
        pass
    # Let a row point at a non-account subject (software, a deployment)
    # without disturbing target_admin_id.
    try:
        db.execute("ALTER TABLE audit_log ADD COLUMN target_type TEXT")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE audit_log ADD COLUMN target_label TEXT")
    except Exception:
        pass
    # Archiving a finished report hides it from the main list and makes it
    # read-only, without deleting anything. Both NULL until it is archived;
    # timestamps here are added NULL and set at write time, like the rest.
    try:
        db.execute("ALTER TABLE problem_reports ADD COLUMN archived_at TIMESTAMP")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE problem_reports ADD COLUMN archived_by INTEGER")
    except Exception:
        pass
    # The address the person who filed the report gave, for their receipt.
    # On the report only - never written to an account - and gone with it
    # when the report is deleted. NULL on reports filed before it existed,
    # and on reports an agent files.
    try:
        db.execute("ALTER TABLE problem_reports ADD COLUMN reporter_email TEXT")
    except Exception:
        pass
    # When the one "your report is resolved / was checked" email was started.
    # Set just before that send, so a report is emailed once, ever; NULL
    # means it has not been - including when mail was off at the time.
    try:
        db.execute("ALTER TABLE problem_reports ADD COLUMN resolved_notified_at TIMESTAMP")
    except Exception:
        pass
    # The dashboard's Recent Activity reads the last eight days by created_at,
    # and audit_log is never pruned, so that read should not scan it whole.
    db.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log(created_at)")
    # The software name snapshotted at deploy time, so job history survives
    # the entry's deletion (a join would drop those jobs), like
    # audit_log.target_label.
    try:
        db.execute("ALTER TABLE jobs ADD COLUMN software_name TEXT")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE jobs ADD COLUMN software_version TEXT")
    except Exception:
        pass
    # One-time backfill for jobs created before the columns existed. Rows whose
    # software was ALREADY deleted stay NULL - that name is gone for good - and
    # render as "Removed software".
    try:
        db.execute(
            """
            UPDATE jobs
            SET software_name = (SELECT name FROM software WHERE software.id = jobs.software_id),
                software_version = (SELECT version FROM software WHERE software.id = jobs.software_id)
            WHERE software_name IS NULL
              AND EXISTS (SELECT 1 FROM software WHERE software.id = jobs.software_id)
            """
        )
    except Exception:
        pass
    try:
        db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_batch_id ON jobs(batch_id)")
    except Exception:
        pass
    # The installer's exit code and the agent's reason CODE (worded in
    # JOB_RESULT_REASONS). NULL when not sent: a blank Result, never a guess.
    try:
        db.execute("ALTER TABLE jobs ADD COLUMN exit_code INTEGER")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE jobs ADD COLUMN result_reason TEXT")
    except Exception:
        pass
    # action is NULL for every install, so "action IS NULL" asks for
    # installs; the other value, 'uninstall', is validated in code. An
    # uninstall also names the installations row it removes.
    try:
        db.execute("ALTER TABLE jobs ADD COLUMN action TEXT")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE jobs ADD COLUMN installation_id INTEGER")
    except Exception:
        pass
    # The agent's capabilities as of its last poll, space-separated, checked
    # by clean_agent_capabilities(). "" can do nothing more (or is too old to
    # say); NULL has not polled since this column existed.
    try:
        db.execute("ALTER TABLE computers ADD COLUMN capabilities TEXT")
    except Exception:
        pass
    # software.id is never 0, enforced here rather than assumed. An uninstall
    # whose software has left the library stores jobs.software_id = 0
    # (NO_LIBRARY_SOFTWARE_ID in app.py - the column is NOT NULL, which SQLite
    # cannot relax without rebuilding the table), so a real software row with
    # id 0 would claim every such job. AUTOINCREMENT never hands out 0 and no
    # code inserts an explicit id; these refuse a hand-written one too.
    db.executescript("""
        CREATE TRIGGER IF NOT EXISTS software_id_never_zero_insert
            BEFORE INSERT ON software WHEN NEW.id = 0
            BEGIN SELECT RAISE(ABORT, 'software id 0 is reserved'); END;
        CREATE TRIGGER IF NOT EXISTS software_id_never_zero_update
            BEFORE UPDATE OF id ON software WHEN NEW.id = 0
            BEGIN SELECT RAISE(ABORT, 'software id 0 is reserved'); END;
    """)
    # Password-reset tokens. Its own table rather than columns on admins: a
    # token has a lifecycle of its own (issued, expired, used) and more than
    # one can legitimately exist at a time if someone clicks the link twice.
    #
    # token_hash, never the token itself. The emailed value is the only copy;
    # a stolen database therefore yields nothing usable, exactly like
    # password_hash. used_at is a timestamp rather than a flag so the record
    # says WHEN it was spent, which is what you want if a reset is disputed.
    db.executescript("""
        CREATE TABLE IF NOT EXISTS password_resets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            used_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_password_resets_token ON password_resets(token_hash);
    """)
    # Equipment: peripherals a technician records by hand, owned by a ROOM
    # (a room is the honest owner of forty cables). Its own table, with real
    # defaults since it is a CREATE. quantity covers stock nobody
    # serial-numbers; category is free text, offered back as a dropdown.
    #
    # A table still in the per-computer shape is rebuilt per location
    # (SQLite cannot drop a NOT NULL column in place on older versions), in
    # ONE transaction, guarded on computer_id so it runs exactly once.
    columns = [row[1] for row in db.execute("PRAGMA table_info(equipment)").fetchall()]
    if "computer_id" in columns and "location" not in columns:
        db.executescript("""
            BEGIN;

            CREATE TABLE equipment_migrated (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                location TEXT NOT NULL,
                category TEXT NOT NULL,
                manufacturer TEXT,
                model TEXT,
                serial_number TEXT,
                quantity INTEGER NOT NULL DEFAULT 1,
                asset_status TEXT,
                purchase_date TEXT,
                vendor TEXT,
                warranty_until TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            -- Each item inherits the room its computer was in. A computer
            -- with no location, or a row whose computer has since gone,
            -- lands in 'Unassigned' rather than being dropped - losing a
            -- record silently is never the right migration.
            INSERT INTO equipment_migrated
                (id, location, category, manufacturer, model, serial_number,
                 quantity, asset_status, purchase_date, vendor, warranty_until,
                 notes, created_at)
            SELECT e.id,
                   COALESCE(NULLIF(TRIM(c.location), ''), 'Unassigned'),
                   e.category, e.manufacturer, e.model, e.serial_number,
                   e.quantity, e.asset_status, e.purchase_date, e.vendor,
                   e.warranty_until, e.notes, e.created_at
            FROM equipment e
            LEFT JOIN computers c ON c.id = e.computer_id;

            DROP TABLE equipment;
            ALTER TABLE equipment_migrated RENAME TO equipment;

            CREATE INDEX IF NOT EXISTS idx_equipment_location ON equipment(location);
            CREATE INDEX IF NOT EXISTS idx_equipment_category ON equipment(category);

            COMMIT;
        """)
        print("Equipment migrated from per-computer to per-location.")

    db.executescript("""
        CREATE TABLE IF NOT EXISTS equipment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            location TEXT NOT NULL,
            category TEXT NOT NULL,
            manufacturer TEXT,
            model TEXT,
            serial_number TEXT,
            quantity INTEGER NOT NULL DEFAULT 1,
            asset_status TEXT,
            purchase_date TEXT,
            vendor TEXT,
            warranty_until TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_equipment_location ON equipment(location);
        CREATE INDEX IF NOT EXISTS idx_equipment_category ON equipment(category);
    """)
    # Who recorded the item (admins.id, no declared foreign key, kept when
    # that admin is deleted); NULL for older rows. An ALTER, so it serves new
    # and existing databases alike.
    try:
        db.execute("ALTER TABLE equipment ADD COLUMN created_by INTEGER")
    except Exception:
        pass

    # What AppEase installed on each computer (see installation_record_key()).
    # Its own table: jobs are deletable history, this is a PC's current state.
    #   - ids are plain INTEGERs with no foreign key; a row outlives its
    #     library entry (software_id goes NULL) and shows its snapshot name.
    #   - method and state are validated in code, not CHECK constraints,
    #     which SQLite cannot change without a rebuild.
    #   - A CREATE, so real defaults are allowed; later ADDs are constant-only.
    # Registry columns keep Windows' original case; only record_key is
    # normalised.
    db.executescript("""
        CREATE TABLE IF NOT EXISTS installations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            computer_id INTEGER NOT NULL,
            software_id INTEGER,
            software_name TEXT,
            software_version TEXT,
            method TEXT NOT NULL,
            record_key TEXT NOT NULL,
            choco_package_id TEXT,
            msi_product_code TEXT,
            reg_hive TEXT,
            reg_user_sid TEXT,
            reg_view TEXT,
            reg_key_name TEXT,
            display_name TEXT,
            display_version TEXT,
            install_location TEXT,
            uninstall_string TEXT,
            quiet_uninstall_string TEXT,
            install_job_id INTEGER,
            uninstall_job_id INTEGER,
            state TEXT NOT NULL DEFAULT 'installed',
            state_reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            state_changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_installations_record
            ON installations(computer_id, record_key);
        CREATE INDEX IF NOT EXISTS idx_installations_software
            ON installations(software_id);
    """)

    # One-time backfill: older computer notifications link to "/" rather than
    # the computers page. A link is navigation, not record, so correcting it
    # rewrites nothing relied on. Scoped to one event type and one value;
    # idempotent.
    try:
        moved = db.execute(
            "UPDATE notifications SET link_url = '/computers' "
            "WHERE event_type = 'computer_status' AND link_url = '/'"
        ).rowcount
        if moved:
            print(f"Repointed {moved} computer notification(s) to the Lab Computers page.")
    except Exception:
        # A fresh database has no notifications table yet; a cleanup that
        # cannot run never stops startup.
        pass

    # One-time cleanup: profile-picture changes are cosmetic and not
    # audited; this clears rows recorded when they were. Nothing writes this
    # action_type, so after the first run it deletes nothing.
    # Scoped to exactly one action_type - it must never widen into a general
    # audit_log prune, which would let this quietly erase security history.
    try:
        removed = db.execute(
            "DELETE FROM audit_log WHERE action_type = 'profile_picture_updated'"
        ).rowcount
        if removed:
            print(f"Removed {removed} cosmetic profile-picture audit entr"
                  f"{'y' if removed == 1 else 'ies'}.")
    except Exception:
        # As above: a fresh database has no audit_log until the CREATE runs.
        pass

    create_default_admin(db)
    db.commit()
    print("Database initialized successfully.")

if __name__ == "__main__":
    init_db()
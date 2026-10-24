"""The Deploy page's Uninstall mode: the candidates API is admin-only and
read-only, every row's reason is uninstall_blocker()'s own, and names reach
the page only as data. The panel's JavaScript cannot run here; the clicks
are a manual test.
"""
import json
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


work = tempfile.mkdtemp(prefix="appease_unui_")
server_dir = os.path.join(work, "server")
shutil.copytree(os.path.join(ROOT, "server"), server_dir, ignore=shutil.ignore_patterns(
    "appease.db*", "__pycache__", "*.pyc", ".env", "uploads", "staging", "report_photos", "avatars", "venv"))
sys.path.insert(0, server_dir)
os.chdir(server_dir)
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))

import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()
import app as appmod  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

if not hasattr(appmod, "uninstall_candidates"):
    print("COULD NOT RUN - app.py predates the Uninstall mode.")
    sys.exit(3)
appmod.app.config["TESTING"] = True
UNINSTALL = appmod.JOB_ACTION_UNINSTALL
# An agent that can uninstall Chocolatey and MSI only.
BOTH = "uninstall:choco uninstall:msi"
ALL = "uninstall:choco uninstall:exe uninstall:msi"
STATIC = os.path.join(server_dir, "static")
PANEL_JS = open(os.path.join(STATIC, "js", "uninstall-panel.js"), encoding="utf-8").read()
DEPLOY_JS = open(os.path.join(STATIC, "js", "deploy-panel.js"), encoding="utf-8").read()
APP_CSS = open(os.path.join(STATIC, "css", "app.css"), encoding="utf-8").read()
TW_CSS = open(os.path.join(STATIC, "css", "tailwind.css"), encoding="utf-8").read()


def write(sql, args=()):
    conn = sqlite3.connect(database.DB_PATH)
    try:
        cursor = conn.execute(sql, args)
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def rows(sql, args=()):
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


write("UPDATE admins SET password_hash = ?, must_change_password = 0 WHERE username = 'admin'",
      (generate_password_hash("Str0ng!Passw0rd"),))


def computer(host, caps=ALL, status="online", seen="-0 seconds", retired=False, location="Lab 1"):
    return write(
        "INSERT INTO computers (hostname, status, last_seen, capabilities, asset_status, location) "
        "VALUES (?, ?, DATETIME('now', ?), ?, ?, ?)",
        (host, status, seen, caps, "Retired" if retired else None, location))


def software(name, command):
    return write("INSERT INTO software (name, version, type, install_command) VALUES (?, '1', 'x', ?)",
                 (name, command))


def inst(pc, method, name, key, state="installed", reason=None, software_id=None, display=None, package=None,
         product_code=None, version="1.0", hive=None, quiet=None, sid=None):
    return write(
        "INSERT INTO installations (computer_id, software_id, software_name, software_version, method, record_key, "
        "choco_package_id, msi_product_code, display_name, state, state_reason, reg_hive, reg_user_sid, "
        "quiet_uninstall_string) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (pc, software_id, name, version, method, key, package, product_code, display, state, reason, hive, sid,
         quiet))


def msi_key(code):
    return database.installation_record_key("msi", product_code=code)


ON = computer("LAB1-PC01")
ON2 = computer("LAB1-PC02")
MSI_ONLY = computer("LAB1-PC03", caps="uninstall:msi")
CHOCO_ONLY = computer("LAB1-PC06", caps="uninstall:choco")
PHASE2 = computer("LAB1-PC08", caps=BOTH)
OFF = computer("LAB1-PC04", status="offline", seen="-1 days")
STALE = computer("LAB1-PC07", seen="-5 minutes")
NEEDS_UPDATE = computer("LAB1-PC05", caps="")
RETIRED = computer("LAB2-PC07", retired=True, location="Lab 2")
S_7Z = software("7-Zip", 'msiexec /i "uploads/7z.msi" /qn')
S_VLC = software("VLC", "choco install vlc -y")
S_NPP = software("Notepad++", '"uploads/npp.exe" /S')
C_7Z = "{23170F69-40C1-2702-2408-000001000000}"
C_7Z_NEW = "{23170F69-40C1-2702-2603-000001000000}"
C_GONE = "{11111111-2222-3333-4444-555555555555}"
EVIL = 'Evil </script><script>alert("x")</script> \'App\' "&amp;"'

R = {
    "ok": inst(ON, "msi", "7-Zip", msi_key(C_7Z), software_id=S_7Z, product_code=C_7Z, version="24.08"),
    "ok_other_pc": inst(ON2, "msi", "7-Zip", msi_key(C_7Z), software_id=S_7Z, product_code=C_7Z, version="24.08"),
    "newer_7z": inst(ON2, "msi", "7-Zip", msi_key(C_7Z_NEW), software_id=S_7Z, product_code=C_7Z_NEW, version="26.03"),
    "vlc_on": inst(ON, "choco", "VLC", "choco:vlc.install", software_id=S_VLC, package="vlc.install", version="3.0.21"),
    "vlc_on2": inst(ON2, "choco", "VLC", "choco:vlc.install", software_id=S_VLC, package="vlc.install", version="3.0.23"),
    "exe": inst(ON, "exe", "Notepad++", "exe:HKLM:Registry64:NOTEPAD++", software_id=S_NPP,
                hive="HKEY_LOCAL_MACHINE", quiet='"C:\\Program Files\\Notepad++\\uninstall.exe" /S'),
    "exe_phase2_agent": inst(PHASE2, "exe", "Notepad++", "exe:HKLM:Registry64:NOTEPAD++", software_id=S_NPP,
                             hive="HKEY_LOCAL_MACHINE", quiet='"C:\\Program Files\\Notepad++\\uninstall.exe" /S'),
    # ON's agent says uninstall:exe but not uninstall:exe-user.
    "per_user": inst(ON, "exe", "Greenshot", "exe:HKCU:S-1-5-21-1-1001:GREENSHOT_IS1", hive="HKEY_CURRENT_USER",
                     sid="S-1-5-21-1-1001",
                     quiet='"C:\\Users\\LabUser\\AppData\\Local\\Programs\\Greenshot\\unins000.exe" /SILENT'),
    "per_user_squirrel": inst(ON, "exe", "Discord", "exe:HKCU:S-1-5-21-1-1001:DISCORD", hive="HKEY_CURRENT_USER",
                              sid="S-1-5-21-1-1001",
                              quiet="C:\\Users\\LabUser\\AppData\\Local\\Discord\\Update.exe --uninstall -s"),
    "per_user_unsafe": inst(ON, "exe", "Odd", "exe:HKCU:S-1-5-21-1-1001:ODD", hive="HKEY_CURRENT_USER",
                            sid="S-1-5-21-1-1001", quiet='"C:\\Program Files\\Odd\\uninstall.exe" /S'),
    "unsafe": inst(ON, "exe", "Helper", "exe:HKLM:Registry64:HELPER", hive="HKEY_LOCAL_MACHINE",
                   quiet='"C:\\ProgramData\\Helper\\uninstall.exe" /S'),
    "hkcu_a": inst(ON, "exe", "Code", "exe:HKCU:S-1-5-21-1-1001:{771FD6B0}", state="unavailable",
                   reason="no_silent_uninstall"),
    "hkcu_b": inst(ON2, "exe", "Code", "exe:HKCU:S-1-5-21-2-1002:{771FD6B0}", state="unavailable",
                   reason="no_silent_uninstall"),
    "unavailable": inst(ON, "exe", "Audacity", "exe:HKLM:Registry64:AUDACITY_IS1", state="unavailable",
                        reason="no_silent_uninstall"),
    "protected": inst(ON, "choco", "Python", "choco:python314", state="unavailable", reason="protected",
                      package="python314"),
    "shared": inst(ON, "exe", "vc_redist", "exe:HKLM:Registry64:{VC}"),
    "no_capability": inst(MSI_ONLY, "choco", "VLC", "choco:vlc.install", software_id=S_VLC, package="vlc.install"),
    "no_capability_msi": inst(CHOCO_ONLY, "msi", "7-Zip", msi_key(C_7Z), software_id=S_7Z, product_code=C_7Z),
    "offline": inst(OFF, "msi", "7-Zip", msi_key(C_7Z), software_id=S_7Z, product_code=C_7Z),
    "stale": inst(STALE, "msi", "7-Zip", msi_key(C_7Z), software_id=S_7Z, product_code=C_7Z),
    "needs_update": inst(NEEDS_UPDATE, "msi", "7-Zip", msi_key(C_7Z), software_id=S_7Z, product_code=C_7Z),
    "uninstall_active": inst(MSI_ONLY, "msi", "7-Zip", msi_key(C_7Z), software_id=S_7Z, product_code=C_7Z),
    "install_active": inst(ON2, "choco", "Paint.NET", "choco:paint.net", software_id=S_VLC, package="paint.net"),
    "evil": inst(ON, "msi", EVIL, msi_key(C_GONE), product_code=C_GONE),
    "retired": inst(RETIRED, "msi", "7-Zip", msi_key(C_7Z), software_id=S_7Z, product_code=C_7Z),
}
REMOVED_ROW = inst(ON, "msi", "Old", msi_key("{99999999-0000-0000-0000-000000000000}"), state="removed",
                   reason="removed", product_code="{99999999-0000-0000-0000-000000000000}")
write("INSERT INTO jobs (computer_hostname, software_id, status, action, installation_id) "
      "VALUES ('LAB1-PC03', ?, 'pending', ?, ?)", (S_7Z, UNINSTALL, R["uninstall_active"]))
write("INSERT INTO jobs (computer_hostname, software_id, status) VALUES ('LAB1-PC02', ?, 'in_progress')", (S_VLC,))

client = appmod.app.test_client()

# ---------------------------------------------------------------------------
print("1. Who may ask")
# ---------------------------------------------------------------------------
res = client.get("/api/uninstall-candidates")
check("signed out: no data, sent to sign in", res.status_code == 302 and "/login" in res.headers.get("Location", ""))
tech = write("INSERT INTO admins (username, password_hash, role) VALUES ('tech', ?, 'technician')",
             (generate_password_hash("Str0ng!Passw0rd"),))
write("UPDATE admins SET must_change_password = 0")
other = appmod.app.test_client()
other.post("/login", data={"username": "tech", "password": "Str0ng!Passw0rd"})
res = other.get("/api/uninstall-candidates")
check("a technician account: no data", res.status_code == 302 and res.get_json(silent=True) is None)
client.post("/login", data={"username": "admin", "password": "Str0ng!Passw0rd"})
jobs_before = rows("SELECT COUNT(*) AS n FROM jobs")[0]["n"]
res = client.get("/api/uninstall-candidates")
data = res.get_json()
check("an admin gets JSON", res.status_code == 200 and isinstance(data, dict))
check("...and asking changes nothing", rows("SELECT COUNT(*) AS n FROM jobs")[0]["n"] == jobs_before)

# ---------------------------------------------------------------------------
print("2. Computers")
# ---------------------------------------------------------------------------
comps = {c["hostname"]: c for c in data["computers"]}
check("retired computers are left out, as in Install mode", "LAB2-PC07" not in comps)
check("...and so are their programs", all(r["computer_id"] != RETIRED for r in data["installations"]))
check("an offline computer: Offline", (comps["LAB1-PC04"]["reason"], comps["LAB1-PC04"]["reason_text"])
      == ("offline", "Offline"))
check("one that stopped checking in reads offline too (the dashboard's rule)",
      comps["LAB1-PC07"]["reason"] == "offline" and comps["LAB1-PC07"]["online"] is False)
check("an agent that can uninstall nothing: Agent needs updating",
      (comps["LAB1-PC05"]["reason"], comps["LAB1-PC05"]["reason_text"]) == ("no_capability", "Agent needs updating"))
check("a capable online PC can be picked; its agent predates per-user EXEs, and the note says so (2026-10-07)",
      comps["LAB1-PC01"]["reason"] is None
      and comps["LAB1-PC01"]["methods_note"] == "MSI, Chocolatey and machine-wide EXE only")
check("one that can do only some methods says which",
      comps["LAB1-PC03"]["methods_note"] == "MSI only" and comps["LAB1-PC06"]["methods_note"] == "Chocolatey only"
      and comps["LAB1-PC03"]["reason"] is None)
check("an agent from Phase 2, not yet updated, says it cannot do EXE",
      comps["LAB1-PC08"]["methods_note"] == "MSI and Chocolatey only" and comps["LAB1-PC08"]["reason"] is None)
check("computers come in the Install picker's order (location, then name)",
      [c["hostname"] for c in data["computers"]] == sorted(c["hostname"] for c in data["computers"]))

# ---------------------------------------------------------------------------
print("3. The picker and /uninstall-batch agree on every reason")
# ---------------------------------------------------------------------------
listed = {r["id"]: r for r in data["installations"]}
check("only live rows are listed: a removed row is not", REMOVED_ROW not in listed)
EXPECTED = {"ok": None, "ok_other_pc": None, "newer_7z": None, "vlc_on": None, "evil": None,
            "exe": None, "exe_phase2_agent": "no_capability", "per_user": "no_capability",
            "per_user_squirrel": "no_capability", "per_user_unsafe": "unsafe_user_location",
            "unsafe": "unsafe_location",
            "unavailable": "unavailable", "hkcu_a": "unavailable", "protected": "protected",
            "shared": "shared_runtime", "no_capability": "no_capability", "no_capability_msi": "no_capability",
            "offline": "offline", "stale": "offline", "needs_update": "no_capability",
            "uninstall_active": "uninstall_active", "install_active": "install_active", "vlc_on2": "install_active"}
for name, reason in EXPECTED.items():
    row = listed.get(R[name], {})
    check(f"{name}: the picker says {reason}", row.get("reason") == reason
          and row.get("reason_text") == (appmod.UNINSTALL_SKIP_REASONS[reason] if reason else ""))
ids = [r["id"] for r in data["installations"]]
reply = client.post("/uninstall-batch", data={"installation_ids": [str(i) for i in ids]}).get_json()
server = {item["installation_id"]: item["reason"] for item in reply["skipped"]}
server.update({item["installation_id"]: None for item in reply["created"]})
disagree = [(i, listed[i]["reason"], server.get(i, "missing")) for i in ids if listed[i]["reason"] != server.get(i, "missing")]
check(f"for every listed row, /uninstall-batch decides exactly what the picker said (disagreements: {disagree})",
      not disagree and len(server) == len(ids))
check("...every reason the server can give a live row was covered",
      {r for r in EXPECTED.values() if r} == set(appmod.UNINSTALL_SKIP_REASONS) - {"not_found", "not_installed", "retired"})
write("DELETE FROM jobs WHERE batch_id = ?", (reply["batch_id"],))

# ---------------------------------------------------------------------------
print("4. What counts as the same program")
# ---------------------------------------------------------------------------
group = {name: listed[R[name]]["group"] for name in R if R[name] in listed}
check("one MSI on two PCs: one group, keyed by its ProductCode",
      group["ok"] == group["ok_other_pc"] == msi_key(C_7Z))
check("another version of it (another ProductCode): another group", group["newer_7z"] != group["ok"])
check("one Chocolatey package at two versions: one group", group["vlc_on"] == group["vlc_on2"] == "choco:vlc.install")
check("a per-user EXE on two PCs, two users: one group, without either SID",
      group["hkcu_a"] == group["hkcu_b"] == "exe:HKCU:{771FD6B0}")
check("a machine-wide EXE keeps its key", group["exe"] == "exe:HKLM:Registry64:NOTEPAD++")
check("the library entry is never part of a group key",
      all(f"software:{S_7Z}" not in g and f":{S_7Z}" not in g.split(":")[-1:] for g in group.values()))
check("each row carries its own name and version for the label",
      (listed[R["vlc_on"]]["name"], listed[R["vlc_on"]]["version"], listed[R["vlc_on2"]]["version"])
      == ("VLC", "3.0.21", "3.0.23"))
check("...and its method in short", (listed[R["ok"]]["method_label"], listed[R["vlc_on"]]["method_label"],
                                     listed[R["exe"]]["method_label"]) == ("MSI", "Chocolatey", "EXE"))

# ---------------------------------------------------------------------------
print("5. Names are data, never markup")
# ---------------------------------------------------------------------------
check("a name holding </script> and quotes comes back exactly", listed[R["evil"]]["name"] == EVIL)
page = client.get("/deploy").get_data(as_text=True)
check("...and is never rendered into the page itself", "Evil </script>" not in page and "alert(" not in page)
HTML_SINKS = ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "DOMParser", "createContextualFragment")
found = [w for w in HTML_SINKS if w in PANEL_JS]
check(f"uninstall-panel.js never parses text as HTML (found {found})", not found)
# deploy-panel.js has always cleared one list with innerHTML = '' - an empty
# string parses nothing. Anything else assigned to it, or any other sink, is.
code = re.sub(r"//[^\n]*|/\*.*?\*/", "", DEPLOY_JS, flags=re.S)
assigned = re.findall(r"innerHTML\s*=\s*([^;]*);", code)
others = [w for w in HTML_SINKS[1:] if w in code]
check(f"deploy-panel.js only ever clears with innerHTML = '' (found {assigned}, {others})",
      all(a.strip() == "''" for a in assigned) and not others)
check("uninstall-panel.js sets text with textContent", PANEL_JS.count(".textContent =") >= 15)

# ---------------------------------------------------------------------------
print("6. The page: Install mode first, and nothing remembered")
# ---------------------------------------------------------------------------
switch = re.search(r'<div class="deploy-mode[^"]*"[^>]*>(.*?)</div>', page, re.S)
check("there is an Install / Uninstall switch",
      switch is not None and 'data-deploy-mode="install"' in switch.group(1)
      and 'data-deploy-mode="uninstall"' in switch.group(1))
check("it opens on Install", switch is not None
      and re.search(r'data-deploy-mode="install" aria-pressed="true"', switch.group(1))
      and re.search(r'data-deploy-mode="uninstall" aria-pressed="false"', switch.group(1)))
panel_tag = re.search(r'<div id="uninstall-panel"[^>]*>', page)
form_tag = re.search(r'<form id="deploy-batch-form"[^>]*>', page, re.S)
check("the uninstall panel starts hidden, the install form shown",
      panel_tag is not None and re.search(r'class="hidden"', panel_tag.group(0))
      and form_tag is not None and "hidden" not in form_tag.group(0))
remembered = [w for w in ("localStorage", "sessionStorage", "document.cookie", "location.hash", "history.",
                          "URLSearchParams", "indexedDB") if w in PANEL_JS]
check(f"nothing remembers the mode: no storage, cookie or URL (found {remembered})", not remembered)
check("switching clears both modes' selections",
      "clearUninstall();" in PANEL_JS and "resetSelections()" in PANEL_JS
      and "function resetSelections" in DEPLOY_JS and "resetSelections: resetSelections" in DEPLOY_JS)
check("the scripts load in order: uninstall-panel.js after deploy-panel.js",
      0 < page.find("js/deploy-panel.js") < page.find("js/uninstall-panel.js"))
check("the panel knows its two routes",
      'data-candidates-url="/api/uninstall-candidates"' in page and 'data-submit-url="/uninstall-batch"' in page)

# ---------------------------------------------------------------------------
print("7. The confirmation")
# ---------------------------------------------------------------------------
dialog = re.search(r'<dialog id="uninstall-confirm".*?</dialog>', page, re.S)
dialog = dialog.group(0) if dialog else ""
ok_button = re.search(r'<button[^>]*id="uninstall-confirm-ok"[^>]*>([^<]*)</button>', dialog)
check("an alert-dialog", 'class="alert-dialog"' in dialog)
check("its confirm button says plain Uninstall", ok_button is not None and ok_button.group(1) == "Uninstall")
check("...in the solid destructive style", ok_button is not None and "deploy-uninstall-btn" in ok_button.group(0)
      and 'data-variant="destructive"' not in ok_button.group(0))
check("its icon is tinted destructive", '<figure class="deploy-danger-figure">' in dialog)
check("it warns about restarts and offline computers",
      "Some uninstallers may restart the PC." in dialog and "Offline computers are not queued" in dialog)


def token(name, dark):
    blocks = re.split(r"html\.dark\s*\{", APP_CSS, maxsplit=1)
    source = blocks[1] if dark and len(blocks) > 1 else blocks[0]
    found = re.search(r"--" + name + r":\s*(#[0-9a-fA-F]{6})\b", source)
    return found.group(1) if found else None


def luminance(colour):
    values = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    values = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in values]
    return 0.2126 * values[0] + 0.7152 * values[1] + 0.0722 * values[2]


def contrast(a, b):
    high, low = sorted([luminance(a), luminance(b)], reverse=True)
    return (high + 0.05) / (low + 0.05)


for dark in (False, True):
    theme = "dark" if dark else "light"
    button = (token("color-danger-solid-foreground", dark), token("color-danger-solid", dark))
    icon = (token("tone-bad-fg", dark), token("tone-bad-bg", dark))
    check(f"{theme}: the destructive button's text is at least 4.5:1",
          None not in button and contrast(*button) >= 4.5)
    check(f"{theme}: the tinted icon is at least 3:1 on its tile", None not in icon and contrast(*icon) >= 3)
rule = re.search(r"\.deploy-uninstall-btn\s*\{([^}]*)\}", APP_CSS)
figure = re.search(r"\.deploy-danger-figure\s*\{([^}]*)\}", APP_CSS)
check("the button reads those tokens", rule is not None and "var(--color-danger-solid)" in rule.group(1)
      and "var(--color-danger-solid-foreground)" in rule.group(1))
check("the icon tile reads its tokens, not --color-muted", figure is not None
      and "var(--tone-bad-bg)" in figure.group(1) and "var(--tone-bad-fg)" in figure.group(1))

# ---------------------------------------------------------------------------
print("8. The progress modal's numbers")
# ---------------------------------------------------------------------------
write("DELETE FROM jobs")
write("DELETE FROM notifications")
batch = "a" * 32
# Uneven on purpose: two unconfirmed, so counting the wrong reason as
# "unconfirmed" gives a different number.
for status, reason in (("completed", None), ("completed", "removal_unverified"), ("completed", "removal_unverified"),
                       ("failed", "still_listed"), ("completed", "already_removed")):
    write("INSERT INTO jobs (computer_hostname, software_id, status, action, installation_id, batch_id, "
          "result_reason, exit_code, software_name) VALUES ('LAB1-PC01', 0, ?, ?, ?, ?, ?, 0, 'X')",
          (status, UNINSTALL, R["ok"], batch, reason))
progress = client.get(f"/api/deployments/{batch}/progress").get_json()
check("an uninstall batch says so", progress["action"] == "uninstall")
check("removed counts completed uninstalls except an unconfirmed one", progress["counts"]["removed"] == 2)
flags = sorted(bool(j.get("removed")) for g in progress["groups"] for j in g["jobs"])
check("each job says whether it counts as removed",
      all("removed" in j for g in progress["groups"] for j in g["jobs"]) and flags == [False, False, False, True, True])
conn = database.get_db()
try:
    appmod.maybe_notify_batch_complete(conn, batch)
    conn.commit()
finally:
    conn.close()
note = rows("SELECT message FROM notifications ORDER BY id DESC LIMIT 1")
check("...exactly as the batch notification counts", note and note[0]["message"]
      == f"Uninstall batch finished — {progress['counts']['removed']} of 5 removed")
install_batch = "b" * 32
write("INSERT INTO jobs (computer_hostname, software_id, status, batch_id) VALUES ('LAB1-PC01', ?, 'completed', ?)",
      (S_7Z, install_batch))
check("an install batch says install", client.get(f"/api/deployments/{install_batch}/progress").get_json()["action"]
      == "install")

# ---------------------------------------------------------------------------
print("9. CSS: no display value, nothing to compile")
# ---------------------------------------------------------------------------
block = APP_CSS[APP_CSS.index("/* ---- Deploy page: Uninstall mode"):APP_CSS.index('/* The full "N apps x N computers')]
check("the new block sets no display value (the .hidden cascade trap)",
      not re.search(r"display\s*:", re.sub(r"/\*.*?\*/", "", block, flags=re.S)))
for name in ("deploy-mode", "deploy-row-off", "deploy-reason", "deploy-on", "deploy-subhead", "deploy-empty-box",
             "deploy-skip-list", "deploy-uninstall-btn", "deploy-danger-figure", "deploy-mode-note"):
    check(f".{name} is defined", re.search(r"\." + name + r"\b", block) is not None)
check("no colour literal in it, only tokens",
      not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(|oklch\(", re.sub(r"/\*.*?\*/", "", block, flags=re.S)))


def compiled(name):
    escaped = re.escape(name).replace(r"\:", r"\\:").replace(":", r"\\:")
    return (re.search(r"\." + re.escape(name.replace(":", "\\:").replace(".", "\\.")) + r"(?![\w-])", TW_CSS)
            or re.search(r"\." + re.escape(name) + r"(?![\w-])", APP_CSS)
            or re.search(r"\." + escaped + r"(?![\w-])", TW_CSS))


def between(start, end):
    i = page.index(start)
    return page[i:page.index(end, i)]


section = "".join((
    between('<div class="deploy-mode', "</div>"),
    between('<div id="uninstall-panel"', '<div id="batch-modal-overlay"'),
    between('<div id="deploy-progress-skipped"', "</ul>"),
    between('<dialog id="uninstall-confirm"', "</dialog>"),
))
used = set()
for classes in re.findall(r'class="([^"]*)"', section):
    used |= set(classes.split())
for classes in re.findall(r"el\('[a-z]+', '([^']*)'", PANEL_JS) + re.findall(r"className = '([^']*)'", PANEL_JS):
    used |= set(classes.split())
used |= {"pill-tone-info-soft", "pill-tone-neutral"}
HOOKS = {"deploy-tally", "alert-dialog"}           # styled elsewhere or hooks only
missing = sorted(c for c in used - HOOKS if not compiled(c))
check(f"every class the new markup and script use is already compiled (missing: {missing})", not missing)
check("(the check saw the new markup and script)", {"deploy-uninstall-btn", "deploy-row-off", "min-w-0"} <= used)

print()
print(f"{passed} passed, {len(failed)} failed")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

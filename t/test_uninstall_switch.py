"""The Software Library's Uninstall switch: only /S, /SILENT, /VERYSILENT,
/qn and /norestart are ever kept, whatever is typed or posted. The page's
JavaScript is source-checked only; its behaviour needs the click-test.
"""
import io
import os
import re
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


work = tempfile.mkdtemp(prefix="appease_switch_")
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

if not hasattr(appmod, "clean_uninstall_switch"):
    print("COULD NOT RUN - app.py predates the Uninstall switch.")
    sys.exit(3)
appmod.app.config["TESTING"] = True
os.makedirs(appmod.UPLOAD_FOLDER, exist_ok=True)
os.makedirs(appmod.STAGING_FOLDER, exist_ok=True)

# ---------------------------------------------------------------------------
print("1. Only the allowed switches are kept")
# ---------------------------------------------------------------------------
check("the allowlist is exactly the agreed five",
      appmod.UNINSTALL_SWITCH_ALLOWED == ("/S", "/SILENT", "/VERYSILENT", "/qn", "/norestart"))
clean = appmod.clean_uninstall_switch
KEPT = [
    ("/S", "/S"),
    ("/s", "/S"),
    ("/silent", "/SILENT"),
    ("/verysilent /norestart", "/VERYSILENT /norestart"),
    ("/QN", "/qn"),
    ("/NORESTART /S", "/norestart /S"),
    ("/S /S /s", "/S"),
    ("  /S\t/norestart  ", "/S /norestart"),
    ('"C:\\Program Files\\VideoLAN\\VLC\\uninstall.exe" /S', "/S"),
    ("/S & del C:\\x", "/S"),
    ("/S /D=C:\\Evil", "/S"),
    ("/S /x", "/S"),
]
for text, expected in KEPT:
    check(f"{text!r} keeps {expected!r}", clean(text)[0] == expected)
NOTHING = ["", "   ", None, "/D=C:\\x", "/x {23170F69-40C1-2702-2408-000001000000}", "-S", "S", "/S/norestart",
           "\uff0fS", "/\uff33", "/S\u200b", '"/S"', "/S;", "&& /S2", "/silent:yes", "/quiet"]
for text in NOTHING:
    check(f"{text!r} keeps nothing", clean(text)[0] is None)
check("what was dropped is reported, for the log", clean("/S & del x")[1] == ["&", "del", "x"])

# ---------------------------------------------------------------------------
print("2. Detection hands the form only the allowed switches")
# ---------------------------------------------------------------------------
# Silent Install HQ's real reply for vlc-3.0.24-win64.exe (capture3b),
# reduced by identify_installer() to what it returns.
VLC_REPLY = {"silent_install": "/S", "uninstall_command": "/S", "confidence_tier": "high",
             "product_name": "vlc 3.0.24 win64", "product_version": None}
real_identify = appmod.identify_installer


def detect(reply):
    appmod.identify_installer = lambda path: reply
    try:
        job_id = appmod.start_detection_job("unused", "staging/abc.exe")
        for _ in range(200):
            if appmod.DETECT_JOBS[job_id]["status"] != "pending":
                break
            time.sleep(0.02)
        return appmod.DETECT_JOBS[job_id]
    finally:
        appmod.identify_installer = real_identify


job = detect(VLC_REPLY)
check("VLC, as Silent Install HQ answered it: the switch is /S, and something was suggested",
      job["status"] == "done" and (job["result"]["uninstall_switch"], job["result"]["uninstall_suggested"])
      == ("/S", True))
job = detect(dict(VLC_REPLY, uninstall_command='"%ProgramFiles%\\VideoLAN\\VLC\\uninstall.exe" /S /D=C:\\x'))
check("a whole command: only its allowed switch reaches the form",
      (job["result"]["uninstall_switch"], job["result"]["uninstall_suggested"]) == ("/S", True))
job = detect(dict(VLC_REPLY, uninstall_command="/x {GUID} REBOOT=ReallySuppress"))
check("a suggestion with nothing allowed in it: no switch, but suggested - the form says so",
      (job["result"]["uninstall_switch"], job["result"]["uninstall_suggested"]) == (None, True))
job = detect(dict(VLC_REPLY, uninstall_command=""))
check("nothing suggested: no switch, nothing to say",
      (job["result"]["uninstall_switch"], job["result"]["uninstall_suggested"]) == (None, False))
job = detect(None)
check("detection failed: an error, as before, with nothing about a switch",
      job["status"] == "error" and "uninstall_switch" not in (job["result"] or {}))

# ---------------------------------------------------------------------------
print("3. Both save paths store only allowed switches")
# ---------------------------------------------------------------------------


def rows(sql, args=()):
    conn = sqlite3.connect(database.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


conn = sqlite3.connect(database.DB_PATH)
conn.execute("UPDATE admins SET password_hash = ?, must_change_password = 0 WHERE username = 'admin'",
             (generate_password_hash("Str0ng!Passw0rd"),))
conn.commit()
conn.close()
client = appmod.app.test_client()
client.post("/login", data={"username": "admin", "password": "Str0ng!Passw0rd"})
NAMES = iter(f"App {chr(65 + i // 26)}{chr(65 + i % 26)}" for i in range(400))


def staged(extension=".exe"):
    name = secrets.token_hex(8) + extension
    with open(os.path.join(appmod.STAGING_FOLDER, name), "wb") as handle:
        handle.write(b"MZ")
    return "staging/" + name


def saved(name):
    found = rows("SELECT * FROM software WHERE name = ?", (name,))
    return found[0] if found else None


def add_detected(switch, extension=".exe", **extra):
    """The JS path: detection ran, the file is in staging, the form posts."""
    name = next(NAMES)
    data = {"type": "offline", "name": name, "version": "1.0", "category": "Utility", "silent_flag": "/S",
            "detected_installer_path": staged(extension), "detected_confidence_tier": "high",
            "uninstall_switch": switch}
    data.update(extra)
    client.post("/software", data=data)
    return saved(name)


for posted, expected in (("/S", "/S"), ("/s /norestart", "/S /norestart"), ("/S /D=C:\\Evil & del x", "/S"),
                         ("", None), ("/D=x", None), ("/VERYSILENT /S", "/VERYSILENT /S")):
    row = add_detected(posted)
    check(f"JS path, {posted!r} posted: {expected!r} stored", row is not None and row["uninstall_switch"] == expected)
row = add_detected("/qn", extension=".msi")
check("an .msi never stores a switch (it uninstalls through msiexec)", row is not None and row["uninstall_switch"] is None)


def add_direct(switch, reply):
    """The direct post: the file comes with the form and is detected now."""
    name = next(NAMES)
    appmod.identify_installer = lambda path: reply
    try:
        client.post("/software", data={
            "type": "offline", "name": name, "version": "1.0", "category": "Utility", "silent_flag": "",
            "uninstall_switch": switch, "installer_file": (io.BytesIO(b"MZ"), "vlc-3.0.24-win64.exe")},
            content_type="multipart/form-data")
    finally:
        appmod.identify_installer = real_identify
    return saved(name)


row = add_direct("", VLC_REPLY)
check("direct post, nothing typed: Silent Install HQ's /S is stored", row is not None and row["uninstall_switch"] == "/S")
check("...and the old uninstall_command keeps Silent Install HQ's text, as before", row["uninstall_command"] == "/S")
row = add_direct("/VERYSILENT", VLC_REPLY)
check("direct post, a switch typed: what was typed wins", row["uninstall_switch"] == "/VERYSILENT")
row = add_direct("", dict(VLC_REPLY, uninstall_command='"C:\\Program Files\\App\\u.exe" /S & calc'))
check("direct post, a whole command from Silent Install HQ: only /S", row["uninstall_switch"] == "/S")
row = add_direct("", None)
check("direct post, detection failed: no switch", row is not None and row["uninstall_switch"] is None)
name = next(NAMES)
client.post("/software", data={"type": "online", "name": name, "version": "1.0", "category": "Utility",
                               "package_name": "vlc", "uninstall_switch": "/S"})
row = saved(name)
check("a Chocolatey package never stores a switch", row is not None and row["uninstall_switch"] is None)

# A duplicate bounces back to the form, with the switch that would be saved.
first = add_detected("/S")
bounce = client.post("/software", data={
    "type": "offline", "name": first["name"], "version": "1.0", "category": "Utility", "silent_flag": "/S",
    "detected_installer_path": staged(), "uninstall_switch": "/S /D=C:\\x"}).get_data(as_text=True)
check("a duplicate bounces back with only the allowed switch in the field",
      re.search(r'id="uninstall_switch" name="uninstall_switch" value="/S"', bounce) is not None)
bounce = client.post("/software", data={
    "type": "offline", "name": first["name"], "version": "1.0", "category": "Utility", "silent_flag": "/qn",
    "detected_installer_path": staged(".msi"), "uninstall_switch": ""}).get_data(as_text=True)
check("...and with the field hidden when the file is an .msi",
      re.search(r'<p class="mt-3 hidden" id="uninstall-switch-field">', bounce) is not None)

# ---------------------------------------------------------------------------
print("4. Never changed after it is added")
# ---------------------------------------------------------------------------
before = saved(first["name"])
client.post(f"/software/{before['id']}/edit", data={"name": before["name"], "version": "2.0", "category": "Utility",
                                                    "uninstall_switch": "/VERYSILENT"})
after = saved(before["name"])
check("the edit route saves what it edits", after["version"] == "2.0")
check("...and never the switch, whatever is posted", after["uninstall_switch"] == "/S")

# ---------------------------------------------------------------------------
print("5. The page")
# ---------------------------------------------------------------------------
conn = sqlite3.connect(database.DB_PATH)
conn.execute("DELETE FROM software")
conn.execute("INSERT INTO software (name, version, type, install_command, uninstall_switch, category) "
             "VALUES ('VLC media player', '3.0.24', 'offline', '\"uploads/aa.exe\" /S', '/S', 'Multimedia')")
conn.execute("INSERT INTO software (name, version, type, install_command, category) "
             "VALUES ('WinSCP', '6.5.7', 'offline', '\"uploads/bb.exe\" /VERYSILENT', 'Networking')")
conn.execute("INSERT INTO software (name, version, type, install_command, uninstall_switch, category) "
             "VALUES ('7-Zip', '24.08', 'offline', 'msiexec /i \"uploads/cc.msi\" /qn', NULL, 'Utility')")
conn.execute("INSERT INTO software (name, version, type, install_command, category) "
             "VALUES ('Firefox', '1.0', 'online', 'choco install firefox -y', 'Browser')")
conn.execute("INSERT INTO software (name, version, type, install_command, uninstall_switch, category) "
             "VALUES ('Evil', '1.0', 'offline', '\"uploads/dd.exe\" /S', '\"><script>alert(1)</script>', 'Other')")
conn.commit()
conn.close()
html = client.get("/software").get_data(as_text=True)

field = re.search(r'<p class="mt-3" id="uninstall-switch-field">(.*?)</p>', html, re.S)
body = field.group(1) if field else ""
check("the field is on the form, under Silent Install Flag",
      field is not None and html.index('id="silent_flag"') < html.index('id="uninstall-switch-field"'))
check("...labelled 'Uninstall switch'",
      re.search(r'<label for="uninstall_switch"[^>]*>Uninstall switch</label>', body) is not None)
check("...an editable text box posted as uninstall_switch",
      re.search(r'<input type="text" id="uninstall_switch" name="uninstall_switch" value=""', body) is not None
      and "readonly" not in body and "disabled" not in body)
check("...with the approved help line",
      "Pre-filled from Silent Install HQ. Allowed: /S, /SILENT, /VERYSILENT, /qn, /norestart. Used only if the "
      "program lists no quiet uninstall of its own. Fixed once the entry is added." in body)
check("...and the approved note, hidden until a suggestion had nothing allowed",
      re.search(r'<span id="uninstall-switch-note" class="mt-1 hidden block text-xs text-muted">Silent Install '
                r"HQ's suggestion isn't one of the allowed switches\. Type one if you know it, or leave it "
                r"empty\.</span>", body) is not None)
check("...and an error line, hidden until something refused is typed",
      re.search(r'<span id="uninstall-switch-error" class="[^"]*\bhidden\b[^"]*"></span>', body) is not None)
page_list = re.search(r"var UNINSTALL_SWITCHES = \[([^\]]*)\];", html)
check("the page's allowlist is the server's, in the same order",
      page_list is not None and tuple(re.findall(r"'([^']*)'", page_list.group(1))) == appmod.UNINSTALL_SWITCH_ALLOWED)
check("the field error names every allowed switch",
      "var UNINSTALL_SWITCH_ERROR = 'Only /S, /SILENT, /VERYSILENT, /qn and /norestart are allowed here.';" in html)


def row_of(name):
    found = re.search(r'<tr data-dt-id="\d+" data-dt-edit\s+data-dt-field-name="' + re.escape(name) + r'".*?</tr>',
                      html, re.S)
    return found.group(0) if found else ""


def cell(row_html):
    found = re.search(r'<td class="font-mono text-xs" data-sort="[^"]*">.*?</td>', row_html, re.S)
    return found.group(0) if found else ""


vlc, winscp, seven, firefox, evil = (row_of(n) for n in ("VLC media player", "WinSCP", "7-Zip", "Firefox", "Evil"))
check("a saved switch: a muted second line in the Install Command cell",
      '<span class="block text-muted">uninstall: /S</span>' in cell(vlc))
check("...and the cell still sorts and exports by the command alone",
      'data-sort="&#34;uploads/aa.exe&#34; /S"' in cell(vlc))
check("no switch: no second line at all", "uninstall:" not in cell(winscp) and "uninstall:" not in cell(seven)
      and "uninstall:" not in cell(firefox) and cell(winscp) and cell(seven))
check("the row tells the drawer its switch", 'data-dt-field-uninstall-switch="/S"' in vlc
      and 'data-dt-field-uninstall-switch=""' in winscp)
check("the drawer line is for an uploaded .exe only, not an .msi or a Chocolatey package",
      'data-dt-field-exe="1"' in vlc and 'data-dt-field-exe="1"' in winscp
      and 'data-dt-field-exe=""' in seven and 'data-dt-field-exe=""' in firefox)
check("whatever is in the column is text, never markup",
      "<script>alert(1)</script>" not in html and "&#34;&gt;&lt;script&gt;alert(1)&lt;/script&gt;" in evil)
drawer = re.search(r'<div role="group" class="field hidden" id="sw-edit-switch-group">(.*?)</div>', html, re.S)
check("the Edit drawer has a read-only Uninstall switch line, hidden until a row opens it",
      drawer is not None and "Uninstall switch" in drawer.group(1) and "<input" not in drawer.group(1)
      and "<select" not in drawer.group(1))
check("...inside the drawer's form, where it posts nothing (it has no name)",
      drawer is not None and html.index('id="sw-edit-form"') < drawer.start() < html.index("</form>", drawer.start())
      and "name=" not in drawer.group(1))
check("...worded as approved, with and without a switch",
      "savedSwitch + ' \\u00b7 can\\'t be changed'" in html
      and "'None - this program can be uninstalled only if it lists a quiet uninstall of its own.'" in html)

# Source checks on the page's script: they catch a revert or a rename, not
# a behaviour (the click-test does that).
check("source: Add stays disabled while the switch holds something refused",
      "!!categoryVal && !uninstallSwitchBlocks();" in html)
check("source: detection pre-fills it the way it pre-fills the silent flag",
      "mayAutofill(switchInput, [detectFilledUninstall])" in html and "result.uninstall_switch" in html)
check("source: an .msi hides it", "showUninstallSwitchField(ext === '.exe');" in html)
check("source: leaving a file takes its pre-filled switch with it",
      "['uninstall_switch', detectFilledUninstall]," in html)

print()
print(f"{passed} passed, {len(failed)} failed")
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

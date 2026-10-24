"""Details check - a manual pre-release check of the JavaScript the Python
suite cannot run: data-table.js's hidden and filtered columns, and the
dashboard's Job Details panel.

It stages server/ into a temp folder with a throwaway database (never the
real appease.db), renders the real dashboard through the app, then drives
it in headless Chrome - clicking the real toolbar, View menu, filter menus,
Excel export and row menus - and compares what the page did with what it
must do. Exit 0 clean, 1 findings, 2 could not run.

    python tools/details-check/check.py
    python tools/details-check/check.py --shots <folder>   save screenshots too
    python tools/details-check/check.py --server <folder>  check a staged copy
    python tools/details-check/check.py --json <file>      save what was seen

Needs Chrome (found in the usual install paths, or set CHROME) and the
server's Python packages - run it with server/venv's python. Not a gate:
it lives outside t/, and run_all.py only collects t/test_*.py.
"""
import argparse
import html
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument("--server", default=os.path.join(ROOT, "server"), help="server folder to stage")
parser.add_argument("--shots", help="folder to save screenshots into")
parser.add_argument("--json", help="file to write the observed values into")
args = parser.parse_args()


def find_chrome():
    candidates = [os.environ.get("CHROME"),
                  r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                  r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                  os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
                  "/usr/bin/google-chrome", "/usr/bin/chromium"]
    return next((c for c in candidates if c and os.path.isfile(c)), None)


CHROME = find_chrome()
if not CHROME or not os.path.isdir(args.server):
    print("COULD NOT RUN - " + ("Chrome not found (set CHROME)." if not CHROME else f"no server folder at {args.server}."))
    sys.exit(2)

work = tempfile.mkdtemp(prefix="appease_details_check_")
server_dir = os.path.join(work, "server")
shutil.copytree(args.server, server_dir,
                ignore=shutil.ignore_patterns("venv", "appease.db*", "__pycache__", "*.pyc", ".env",
                                              "uploads", "staging", "report_photos", "avatars"))
sys.path.insert(0, server_dir)
os.chdir(server_dir)
try:
    import database
    database.DB_PATH = os.path.join(server_dir, "appease.db")
    database.init_db()
    os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
    import app as appmod
except Exception as exc:  # noqa: BLE001
    print(f"COULD NOT RUN - the staged app did not import: {exc}")
    sys.exit(2)

# ---- a throwaway database with one job of every kind the panel must show ----
conn = sqlite3.connect(database.DB_PATH)
for host, ip in (("LAB1-PC05", "192.0.2.6"), ("LAB2-PC12", "192.0.2.8")):
    conn.execute("INSERT INTO computers (hostname, status, ip_address, last_seen) VALUES (?, 'online', ?, CURRENT_TIMESTAMP)", (host, ip))
software_id = conn.execute("INSERT INTO software (name, version, type, install_command)"
                           " VALUES ('Paint.NET', '5.1.12', 'online', 'choco install paint.net -y')").lastrowid
CASES = {}
for key, host, status, code, reason, retries, age in (
    ("old", "LAB1-PC05", "failed", None, None, 0, "-40 minutes"),
    ("already", "LAB1-PC05", "completed", 0, "choco_already_installed", 0, "-30 minutes"),
    ("restart", "LAB2-PC12", "completed", 3010, "restart_needed", 0, "-25 minutes"),
    ("restart2", "LAB1-PC05", "completed", 3010, "restart_needed", 0, "-20 minutes"),
    ("timeout", "LAB2-PC12", "failed", None, "timed_out", 0, "-60 minutes"),
    ("reaped", "LAB1-PC05", "failed", None, "no_agent_report", 2, "-50 minutes"),
    ("plain", "LAB1-PC05", "completed", 0, None, 0, "-10 minutes"),
):
    CASES[key] = conn.execute(
        "INSERT INTO jobs (computer_hostname, software_id, status, software_name, software_version, exit_code,"
        " result_reason, retry_count, started_at, completed_at, created_at)"
        " VALUES (?, ?, ?, 'Paint.NET', '5.1.12', ?, ?, ?, DATETIME('now', ?), CURRENT_TIMESTAMP, DATETIME('now', ?))",
        (host, software_id, status, code, reason, retries, age, age)).lastrowid
conn.commit()
admin = conn.execute("SELECT id, username FROM admins LIMIT 1").fetchone()
conn.close()


def render(theme):
    client = appmod.app.test_client()
    if theme == "dark":
        client.set_cookie("theme", "dark")
    with client.session_transaction() as sess:
        sess["admin_id"], sess["admin_role"], sess["admin_username"] = admin[0], "admin", admin[1]
    body = client.get("/").get_data(as_text=True)
    static = "file:///" + os.path.join(server_dir, "static").replace("\\", "/")
    return re.sub(r'(src|href)="/static/', lambda m: f'{m.group(1)}="{static}/', body)


# ---- what runs in the page: real clicks on real controls, values read back --
HARNESS = r"""
<script>
(function () {
  var CASES = %CASES%, MODE = %MODE%, captured = [];
  var RealBlob = window.Blob;
  // The Excel export posts its grid as a JSON blob; capture it, send nothing.
  window.Blob = function (parts, opts) { try { captured.push(parts.join('')); } catch (e) {} return new RealBlob(parts, opts); };
  window.fetch = function () { return new Promise(function () {}); };

  function th(label) {
    return Array.prototype.filter.call(document.querySelectorAll('[data-dt-table] thead th'),
      function (t) { return (t.getAttribute('data-dt-label') || '') === label; })[0];
  }
  function toolbarButton(label) {
    var b = document.querySelectorAll('[data-dt-toolbar] button');
    for (var i = 0; i < b.length; i++) { if (b[i].textContent.trim().indexOf(label) === 0) { return b[i]; } }
    return null;
  }
  function menuItem(text) {
    var items = document.querySelectorAll('.dt-menu-item');
    for (var i = items.length - 1; i >= 0; i--) {
      if ((items[i].getAttribute('data-value') || items[i].textContent.trim()) === text) { return items[i]; }
    }
    return null;
  }
  function escapeKey() { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); }
  function closeMenus() { escapeKey(); escapeKey(); escapeKey(); }
  function exportedColumns() {
    captured = [];
    toolbarButton('Export').click();
    var excel = Array.prototype.filter.call(document.querySelectorAll('.dt-menu-item'),
      function (n) { return n.textContent.trim().indexOf('Excel') === 0; }).pop();
    if (excel) { excel.click(); }
    closeMenus();
    var hit = captured.filter(function (p) { return p.indexOf('"columns"') !== -1; }).pop();
    return hit ? JSON.parse(hit).columns : null;
  }
  function viewItem(label) {
    toolbarButton('View').click();
    var item = menuItem(label);
    return { item: item, state: item ? { pressed: item.getAttribute('aria-pressed'), disabled: item.disabled } : null };
  }
  function openDetails(id) {
    var tr = document.querySelector('tr[data-dt-id="' + id + '"]');
    tr.querySelector('[data-dt-rowmenu]').click();
    var d = menuItem('Details');
    if (d) { d.click(); }
  }
  function panel() {
    var o = document.getElementById('job-details-overlay');
    function f(n) {
      var e = o.querySelector('[data-field="' + n + '"]');
      return e ? { text: e.textContent, hidden: e.classList.contains('hidden'), blank: !!e.querySelector('.details-blank') } : null;
    }
    return { open: !o.classList.contains('hidden'), status: f('status').text, result: f('result'), resultNote: f('result-note'),
             exit: f('exit'), finishedLabel: f('finished-label').text, finishedNote: f('finished-note'), requeued: f('requeued'),
             requeuedRowsHidden: Array.prototype.every.call(o.querySelectorAll('[data-row="requeued"]'),
               function (r) { return r.classList.contains('hidden'); }) };
  }

  function run() {
    if (MODE.show) {
      if (MODE.filter) { toolbarButton('Result').click(); menuItem('Restart needed').click(); closeMenus(); }
      if (MODE.show !== 'none') { openDetails(CASES[MODE.show]); }
      return;
    }
    var out = {};
    out.resultHiddenAtLoad = th('Result').classList.contains('hidden');
    out.exportAtLoad = exportedColumns();
    var v = viewItem('Result'); out.viewResultAtLoad = v.state; closeMenus();

    toolbarButton('Result').click(); menuItem('Restart needed').click(); closeMenus();
    out.resultHiddenWhileFiltered = th('Result').classList.contains('hidden');
    out.rowsShownWhileFiltered = document.querySelectorAll('[data-dt-table] tbody tr:not(.hidden)').length;
    out.exportWhileFiltered = exportedColumns();
    v = viewItem('Result'); out.viewResultWhileFiltered = v.state;
    if (v.item) { v.item.click(); }
    closeMenus();
    out.resultHiddenAfterViewClickWhileFiltered = th('Result').classList.contains('hidden');

    toolbarButton('Result').click();
    var clear = document.querySelector('.dt-menu-clear');
    if (clear) { clear.click(); }
    closeMenus();
    out.resultHiddenAfterClear = th('Result').classList.contains('hidden');
    out.exportAfterClear = exportedColumns();

    v = viewItem('Created'); if (v.item) { v.item.click(); } closeMenus();
    out.createdHiddenByView = th('Created').classList.contains('hidden');
    toolbarButton('Created').click();
    var preset = Array.prototype.filter.call(document.querySelectorAll('.dt-menu-item'),
      function (n) { return /^today$/i.test(n.textContent.trim()); })[0];
    if (preset) { preset.click(); }
    closeMenus();
    out.createdHiddenWhileDateFiltered = th('Created').classList.contains('hidden');

    // Search inside a filter menu must hide what it filters out (the
    // .dt-menu-item cascade bug: it never did).
    toolbarButton('Computer').click();
    var search = document.querySelector('.dt-menu-panel input');
    if (search) { search.value = 'lab1'; search.dispatchEvent(new Event('input', { bubbles: true })); }
    out.computerMenuItemsShown = Array.prototype.filter.call(document.querySelectorAll('.dt-menu-panel .dt-menu-item[data-value]'),
      function (n) { return n.offsetParent !== null; }).map(function (n) { return n.getAttribute('data-value'); });
    closeMenus();

    out.details = {};
    ['reaped', 'timeout', 'old', 'already', 'plain'].forEach(function (key) {
      openDetails(CASES[key]);
      out.details[key] = panel();
      escapeKey();
      out.details[key].closesOnEscape = document.getElementById('job-details-overlay').classList.contains('hidden');
    });
    document.getElementById('check-out').textContent = JSON.stringify(out);
  }
  window.addEventListener('load', function () { setTimeout(run, 50); });
})();
</script>
<pre id="check-out"></pre>
</body>"""


def page(theme, mode):
    body = render(theme)
    if body.count("</body>") != 1:
        print("COULD NOT RUN - the rendered dashboard has no single </body>.")
        sys.exit(2)
    return body.replace("</body>", HARNESS.replace("%CASES%", json.dumps(CASES)).replace("%MODE%", json.dumps(mode)))


def chrome(extra, url):
    return subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--allow-file-access-from-files",
                           f"--user-data-dir={os.path.join(work, 'chrome-profile')}", "--force-device-scale-factor=1",
                           "--force-prefers-reduced-motion", "--virtual-time-budget=6000"] + extra + [url],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def file_url(path):
    return "file:///" + path.replace("\\", "/")


check_page = os.path.join(work, "check.html")
open(check_page, "w", encoding="utf-8").write(page("light", {}))
dom = chrome(["--dump-dom"], file_url(check_page)).stdout
m = re.search(r'<pre id="check-out">(.*?)</pre>', dom, re.S)
if not m or not m.group(1).strip():
    print("COULD NOT RUN - the page did not report back (did data-table.js or the panel script throw?).")
    sys.exit(2)
seen = json.loads(html.unescape(m.group(1)))
if args.json:
    open(args.json, "w", encoding="utf-8").write(json.dumps(seen, indent=1))

# ---- what the page must have done -------------------------------------------
BASE = ["ID", "Computer", "IP Address", "Software", "Status", "Created", "Completed"]
WITH_RESULT = ["ID", "Computer", "IP Address", "Software", "Status", "Result", "Created", "Completed"]
d = seen.get("details", {})
EXPECT = [
    ("Result starts hidden", seen.get("resultHiddenAtLoad") is True),
    ("the Excel export leaves out the hidden Result column", seen.get("exportAtLoad") == BASE),
    ("View lists Result as off and changeable", seen.get("viewResultAtLoad") == {"pressed": "false", "disabled": False}),
    ("filtering on Result shows the column", seen.get("resultHiddenWhileFiltered") is False),
    ("the filter leaves only the two Restart needed rows", seen.get("rowsShownWhileFiltered") == 2),
    ("a filtered export includes the filter's own column", seen.get("exportWhileFiltered") == WITH_RESULT),
    ("View locks the Result toggle while it has a filter", seen.get("viewResultWhileFiltered") == {"pressed": "true", "disabled": True}),
    ("clicking the locked toggle does not hide the column", seen.get("resultHiddenAfterViewClickWhileFiltered") is False),
    ("clearing the filter hides Result again", seen.get("resultHiddenAfterClear") is True),
    ("...and the export leaves it out again", seen.get("exportAfterClear") == BASE),
    ("an ordinary column can still be hidden by hand", seen.get("createdHiddenByView") is True),
    ("a date filter shows the column it filters on", seen.get("createdHiddenWhileDateFiltered") is False),
    ("searching a filter menu hides the items that do not match", seen.get("computerMenuItemsShown") == ["LAB1-PC05"]),
]
for key in ("reaped", "timeout", "old", "already", "plain"):
    EXPECT.append((f"Details opens for the {key} job, and Escape closes it",
                   d.get(key, {}).get("open") is True and d.get(key, {}).get("closesOnEscape") is True))
r, t, o, a, p = (d.get(k, {}) for k in ("reaped", "timeout", "old", "already", "plain"))
EXPECT += [
    ("reaped: the time is 'Server stopped waiting'", r.get("finishedLabel") == "Server stopped waiting"),
    ("reaped: the note says it is not when the PC finished",
     "not when the PC finished" in (r.get("finishedNote") or {}).get("text", "") and not (r.get("finishedNote") or {}).get("hidden")),
    ("reaped: it says how many times it was re-queued",
     (r.get("requeued") or {}).get("text") == "2 times" and r.get("requeuedRowsHidden") is False),
    ("reaped: no exit code, shown as the blank dash", (r.get("exit") or {}).get("blank") is True),
    ("timeout: the agent stopped waiting, not the install failed",
     (t.get("result") or {}).get("text") == "Agent stopped waiting for the install"),
    ("timeout: the note says the install may still have finished",
     "may still have finished" in (t.get("resultNote") or {}).get("text", "") and not (t.get("resultNote") or {}).get("hidden")),
    ("timeout: no exit code, and never 0", (t.get("exit") or {}).get("blank") is True),
    ("timeout: a normal job's time is 'Reported by the agent'", t.get("finishedLabel") == "Reported by the agent"),
    ("timeout: no re-queued row when it never was", t.get("requeuedRowsHidden") is True),
    ("old: no reason is a blank dash, not a guess", (o.get("result") or {}).get("blank") is True),
    ("old: its notes stay hidden", (o.get("resultNote") or {}).get("hidden") is True and (o.get("finishedNote") or {}).get("hidden") is True),
    ("already installed: exit 0 is shown, because a reason came with it", (a.get("exit") or {}).get("text") == "0"),
    ("plain success: exit 0 with no reason is a blank dash", (p.get("exit") or {}).get("blank") is True),
    ("plain success: the panel's status is Completed", p.get("status") == "Completed"),
]

failures = [label for label, ok in EXPECT if not ok]
for label, ok in EXPECT:
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
print(f"\n{len(EXPECT) - len(failures)} of {len(EXPECT)} checks passed.")

if args.shots:
    os.makedirs(args.shots, exist_ok=True)
    for theme, show, name, filtered in (("light", "reaped", "details-reaped-light", False),
                                        ("dark", "timeout", "details-timeout-dark", False),
                                        ("light", "none", "result-filtered-light", True)):
        shot_page = os.path.join(work, name + ".html")
        open(shot_page, "w", encoding="utf-8").write(page(theme, {"show": show, "filter": filtered}))
        chrome(["--window-size=1920,1240", "--screenshot=" + os.path.join(args.shots, name + ".png")], file_url(shot_page))
    print(f"Screenshots in {args.shots}")

os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failures else 0)

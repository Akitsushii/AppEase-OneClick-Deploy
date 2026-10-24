"""The public-host guard: on the tunnelled hostname everything except
/report and a narrow static allowlist must 404. File-backed SQLite in a temp
copy of server/, never :memory:, which cannot reproduce write locking.
"""

import importlib
import os
import secrets
import shutil
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_DIR = os.path.join(PROJECT_ROOT, "server")

PUBLIC_HOST = "report.example.test"
LAN_HOST = "192.168.1.50:8080"

passed, failed = [], []


def check(label, condition, detail=""):
    (passed if condition else failed).append(label if condition else f"{label}  <-- {detail}")


def stage():
    """Copy server/ to a temp dir and plant decoy files in the sensitive
    folders: a 404 is weaker than proving their bytes never come back."""
    tmp = tempfile.mkdtemp(prefix="appease-guard-")
    dest = os.path.join(tmp, "server")
    shutil.copytree(
        SERVER_DIR, dest,
        ignore=shutil.ignore_patterns("appease.db*", "__pycache__", "*.pyc", ".env"),
    )
    for sub in ("static/avatars", "static/report_photos", "static/uploads", "uploads",
                "static/css", "static/js", "static/img", "static/fonts"):
        os.makedirs(os.path.join(dest, sub), exist_ok=True)
    with open(os.path.join(dest, "static/avatars/decoy-face.png"), "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    with open(os.path.join(dest, "static/report_photos/decoy-photo.jpg"), "wb") as fh:
        fh.write(b"\xff\xd8\xff" + b"0" * 32)
    with open(os.path.join(dest, "uploads/decoy-installer.exe"), "wb") as fh:
        fh.write(b"MZ" + b"0" * 64)
    return tmp, dest


def load_app(dest, public_host_env):
    """Import app.py fresh with PUBLIC_REPORT_HOST set or unset: it is read
    at import time, so patching it afterwards would test a startup that
    never happens."""
    for mod in ("app", "database"):
        sys.modules.pop(mod, None)
    if public_host_env is None:
        os.environ.pop("PUBLIC_REPORT_HOST", None)
    else:
        os.environ["PUBLIC_REPORT_HOST"] = public_host_env
    os.environ.setdefault("REPORT_QR_SECRET", "a" * 64)
    if dest not in sys.path:
        sys.path.insert(0, dest)
    os.chdir(dest)
    # app.py refuses to start without SECRET_KEY: a default would be a key
    # an attacker can read. server/ is copied WITHOUT its .env, so the key
    # comes from the environment; setdefault, so every load_app() call
    # reuses one key, as a real process would.
    os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
    appmod = importlib.import_module("app")
    import database
    database.init_db()
    conn = database.get_db()
    conn.execute(
        "INSERT OR IGNORE INTO computers (hostname, ip_address, status) VALUES (?, ?, ?)",
        ("GUARD-TEST-PC", "192.168.199.199", "online"),
    )
    conn.commit()
    conn.close()
    appmod.app.config["TESTING"] = True
    return appmod


def get(client, path, host):
    return client.get(path, headers={"Host": host}, follow_redirects=False)


tmpdir, dest = stage()
try:
    app = load_app(dest, PUBLIC_HOST)
    c = app.app.test_client()

    # --- the admin surface must be gone from the public host --------------
    for path in ["/", "/login", "/admins", "/admins/audit-trail", "/computers",
                 "/software", "/problem-reports", "/notifications", "/profile",
                 "/settings",
                 "/deploy", "/forgot-password", "/set-password", "/api/notifications"]:
        r = get(c, path, PUBLIC_HOST)
        check(f"public host blocks {path}", r.status_code == 404, f"got {r.status_code}")

    # --- installer downloads ----------------------------------------------
    r = get(c, "/uploads/decoy-installer.exe", PUBLIC_HOST)
    check("public host blocks /uploads/", r.status_code == 404, f"got {r.status_code}")
    check("no installer bytes in the blocked response", b"MZ" not in r.data, "file leaked")

    # --- the unauthenticated agent API ------------------------------------
    for path in ["/api/agent/poll", "/api/agent/heartbeat", "/api/agent/problem-reports"]:
        r = c.post(path, json={"computer_hostname": "GUARD-EVIL-PC"},
                   headers={"Host": PUBLIC_HOST})
        check(f"public host blocks POST {path}", r.status_code == 404, f"got {r.status_code}")

    import database as _db
    conn = _db.get_db()
    row = conn.execute("SELECT 1 FROM computers WHERE hostname = 'GUARD-EVIL-PC'").fetchone()
    conn.close()
    check("blocked agent POSTs created no computer row", row is None, "GUARD-EVIL-PC exists")

    # --- static: the four allowed prefixes --------------------------------
    for sub, name, body in [("css", "guard-probe.css", b"/*probe*/"),
                            ("js", "guard-probe.js", b"//probe"),
                            ("img", "guard-probe.png", b"\x89PNG\r\n\x1a\n"),
                            ("fonts", "guard-probe.woff2", b"wOF2")]:
        with open(os.path.join(dest, "static", sub, name), "wb") as fh:
            fh.write(body)
        r = get(c, f"/static/{sub}/{name}", PUBLIC_HOST)
        check(f"public host allows /static/{sub}/", r.status_code == 200, f"got {r.status_code}")

    # --- static: the folders that must stay private -----------------------
    r = get(c, "/static/avatars/decoy-face.png", PUBLIC_HOST)
    check("public host blocks /static/avatars/", r.status_code == 404, f"got {r.status_code}")
    check("no avatar bytes in the blocked response", b"\x89PNG" not in r.data, "image leaked")

    r = get(c, "/static/report_photos/decoy-photo.jpg", PUBLIC_HOST)
    check("public host blocks /static/report_photos/", r.status_code == 404, f"got {r.status_code}")
    check("no photo bytes in the blocked response", b"\xff\xd8\xff" not in r.data, "image leaked")

    r = get(c, "/static/uploads/anything.bin", PUBLIC_HOST)
    check("public host blocks /static/uploads/", r.status_code == 404, f"got {r.status_code}")

    # --- URLs that try to walk out of the allowlist -----------------------
    for path in ["/static/css/../avatars/decoy-face.png",
                 "/static/css/..%2favatars%2fdecoy-face.png",
                 "/report/../admins",
                 "/report/../../uploads/decoy-installer.exe",
                 "//admins", "/REPORT"]:
        r = get(c, path, PUBLIC_HOST)
        check(f"blocked: {path}", r.status_code == 404, f"got {r.status_code}")
        check(f"leaked nothing: {path}",
              b"\x89PNG" not in r.data and b"MZ" not in r.data, "bytes leaked")

    # --- /report itself is untouched --------------------------------------
    r = get(c, "/report", PUBLIC_HOST)
    check("public host allows GET /report", r.status_code == 200, f"got {r.status_code}")

    r = get(c, "/report?computer=GUARD-TEST-PC&t=deadbeef", PUBLIC_HOST)
    check("public host allows /report with a query string (QR links)",
          r.status_code == 200, f"got {r.status_code}")

    r = c.post("/report", data={
        "computer_hostname": "GUARD-TEST-PC", "problem_type": "keyboard",
        "description": "The spacebar has stopped responding entirely.",
        "reporter_role": "student", "reporter_name": "Guard Test", "reporter_email": "student@lab.example.com",
        "reporter_student_number": "2021400001", "reporter_program": "BSIT",
        "reporter_section": "4A", "reporter_year_level": "4",
    }, headers={"Host": PUBLIC_HOST}, follow_redirects=False)
    check("public host allows POST /report", r.status_code in (302, 303), f"got {r.status_code}")

    # --- the public-only body cap -----------------------------------------
    # Content-Length must go through environ_overrides: the test client
    # recomputes the header from the real body length, so setting it in
    # headers= is silently discarded and the guard never sees an oversized
    # request.
    cap = app.PUBLIC_MAX_CONTENT_BYTES
    r = c.post("/report", data=b"x" * 64,
               headers={"Host": PUBLIC_HOST, "Content-Type": "application/octet-stream"},
               environ_overrides={"CONTENT_LENGTH": str(cap + 1)}, follow_redirects=False)
    check("public host refuses an oversized body with 413", r.status_code == 413, f"got {r.status_code}")

    r = c.post("/report", data=b"x" * 64,
               headers={"Host": PUBLIC_HOST, "Content-Type": "application/octet-stream"},
               environ_overrides={"CONTENT_LENGTH": str(cap)}, follow_redirects=False)
    check("a body exactly at the cap is allowed", r.status_code != 413, f"got {r.status_code}")

    # --- Host header edge cases -------------------------------------------
    check("guard still applies when Host carries :443",
          get(c, "/", PUBLIC_HOST + ":443").status_code == 404, "port disabled the guard")
    check("guard is case-insensitive on Host",
          get(c, "/", PUBLIC_HOST.upper()).status_code == 404, "uppercase host slipped through")
    check("a lookalike hostname is not treated as public",
          get(c, "/", "evil-report.example.test").status_code != 404, "over-matched")

    # --- the LAN side must be completely unaffected -----------------------
    check("LAN / still redirects to login", get(c, "/", LAN_HOST).status_code == 302)
    check("LAN /uploads/ still serves installers",
          get(c, "/uploads/decoy-installer.exe", LAN_HOST).status_code == 200)
    check("LAN /static/avatars/ still serves",
          get(c, "/static/avatars/decoy-face.png", LAN_HOST).status_code == 200)
    check("LAN agent poll still works",
          c.post("/api/agent/poll", json={"computer_hostname": "GUARD-TEST-PC"},
                 headers={"Host": LAN_HOST}).status_code == 200)
    check("LAN is not subject to the body cap",
          c.post("/report", data=b"x" * 64,
                 headers={"Host": LAN_HOST, "Content-Type": "application/octet-stream"},
                 environ_overrides={"CONTENT_LENGTH": str(cap + 1)}).status_code != 413)

    # --- with the env var unset, nothing changes at all -------------------
    app2 = load_app(dest, None)
    c2 = app2.app.test_client()
    check("PUBLIC_REPORT_HOST unset leaves the constant empty",
          app2.PUBLIC_REPORT_HOST == "", repr(app2.PUBLIC_REPORT_HOST))
    for path, expect in [("/", 302), ("/uploads/decoy-installer.exe", 200),
                         ("/static/avatars/decoy-face.png", 200), ("/report", 200)]:
        r = get(c2, path, PUBLIC_HOST)
        check(f"guard off: {path} behaves exactly as before ({expect})",
              r.status_code == expect, f"got {r.status_code}")

    app3 = load_app(dest, "   ")
    check("whitespace-only PUBLIC_REPORT_HOST counts as unset",
          app3.PUBLIC_REPORT_HOST == "", repr(app3.PUBLIC_REPORT_HOST))
    check("whitespace-only env var blocks nothing",
          get(app3.app.test_client(), "/", PUBLIC_HOST).status_code == 302)

finally:
    os.chdir(PROJECT_ROOT)
    shutil.rmtree(tmpdir, ignore_errors=True)

print()
for line in passed:
    print(f"  PASS  {line}")
for line in failed:
    print(f"  FAIL  {line}")
print(f"\n{len(passed)} passed, {len(failed)} failed, {len(passed) + len(failed)} total")
sys.exit(1 if failed else 0)
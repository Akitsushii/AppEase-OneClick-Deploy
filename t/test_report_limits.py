"""Abuse control on the public report form: the honeypot, the fill-timing
check and both rate limits, keyed on CF-Connecting-IP rather than
remote_addr, since every tunnelled request arrives on loopback.
"""

import importlib
import os
import secrets
import shutil
import sys
import tempfile
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_DIR = os.path.join(PROJECT_ROOT, "server")

SECRET = "e" * 64
TUNNEL_ENV = {"REMOTE_ADDR": "127.0.0.1"}
# Tunnelled requests arrive on the public hostname, and CF-Connecting-IP is
# only believed there: on the LAN anyone can send it
# (test_report_sender_key.py).
PUBLIC_HOST = "report.example.test"
TUNNEL_URL = f"https://{PUBLIC_HOST}"

passed, failed = [], []


def check(label, condition, detail=""):
    (passed if condition else failed).append(label if condition else f"{label}  <-- {detail}")


def stage():
    tmp = tempfile.mkdtemp(prefix="appease-limits-")
    dest = os.path.join(tmp, "server")
    shutil.copytree(
        SERVER_DIR, dest,
        ignore=shutil.ignore_patterns("appease.db*", "__pycache__", "*.pyc", ".env"),
    )
    return tmp, dest


def load_app(dest):
    for mod in ("app", "database"):
        sys.modules.pop(mod, None)
    os.environ["REPORT_QR_SECRET"] = SECRET
    os.environ["PUBLIC_REPORT_HOST"] = PUBLIC_HOST
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
    for n in range(1, 9):
        conn.execute(
            "INSERT OR IGNORE INTO computers (hostname, location, ip_address, status) "
            "VALUES (?, 'Lab 1', ?, 'online')", (f"LAB-PC-{n:02d}", f"10.9.9.{n}"))
    conn.commit()
    conn.close()
    appmod.app.config["TESTING"] = True
    return appmod


def reset_limits(appmod):
    with appmod._report_rate_lock:
        appmod._report_rate_hits.clear()


def count_reports(prefix=""):
    import database
    conn = database.get_db()
    if prefix:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM problem_reports WHERE description LIKE ?",
            (prefix + "%",)).fetchone()["n"]
    else:
        n = conn.execute("SELECT COUNT(*) AS n FROM problem_reports").fetchone()["n"]
    conn.close()
    return n


tmpdir, dest = stage()
try:
    app = load_app(dest)
    client = app.app.test_client()

    def post(description, hostname="LAB-PC-01", started="fresh",
             honeypot=None, ip=None, **extra):
        data = {
            "computer_hostname": hostname, "problem_type": "keyboard",
            "description": description, "reporter_role": "student",
            "reporter_name": "Test Student", "reporter_email": "student@lab.example.com", "reporter_student_number": "2021400001",
            "reporter_program": "BSIT", "reporter_section": "4A",
            "reporter_year_level": "4",
        }
        if started == "fresh":
            data["form_started"] = app.sign_form_timestamp(time.time() - 30)
        elif started == "now":
            data["form_started"] = app.sign_form_timestamp()
        elif started is not None:
            data["form_started"] = started
        if honeypot is not None:
            data["website"] = honeypot
        # /report REFUSES a report that cannot show it came from the lab,
        # so every post here carries a valid token for the machine it names.
        # Without this the whole suite would fail on the gate instead of on
        # the limit it is actually testing.
        if "qr_token" not in extra:
            data["qr_token"] = app.sign_computer(hostname)
            data["qr_hostname"] = hostname
        data.update(extra)
        headers = {}
        if ip:
            headers["CF-Connecting-IP"] = ip
        return client.post("/report", data=data, headers=headers, base_url=TUNNEL_URL,
                           environ_overrides=dict(TUNNEL_ENV), follow_redirects=False)

    # --- the form serves what the checks need ------------------------------
    # A signed URL, because /report serves a code-entry gate to anyone who
    # cannot show they are in the lab - a bare GET returns that gate, which has
    # none of the fields being checked here.
    page = client.get(
        "/report?computer=LAB-PC-01&t=" + app.sign_computer("LAB-PC-01")
    ).get_data(as_text=True)
    check("the form carries a signed form_started value",
          'name="form_started"' in page and 'value=""' not in
          page.split('name="form_started"')[1][:40], "missing or empty")
    check("the form carries the honeypot field", 'name="website"' in page)
    check("the honeypot is positioned off-screen, not display:none",
          "left:-9999px" in page, "not off-screen")
    check("the honeypot is hidden from assistive tech",
          'aria-hidden="true"' in page and 'tabindex="-1"' in page)
    # The value the page actually serves must be one the server will accept -
    # a signed stamp that round-trips through its own verifier.
    served = page.split('name="form_started" value="')[1].split('"')[0]
    check("the served timestamp verifies against the server's own check",
          app.form_fill_seconds(served) is not None, served[:40])

    # --- honeypot ----------------------------------------------------------
    reset_limits(app)
    before = count_reports()
    r = post("Honeypot bait report here.", honeypot="http://spam.example")
    check("a filled honeypot returns the ordinary success redirect",
          r.status_code == 302 and "success" in (r.headers.get("Location") or ""),
          f"{r.status_code} {r.headers.get('Location')}")
    check("a filled honeypot saves NOTHING", count_reports() == before,
          f"{count_reports()} vs {before}")

    r = post("Empty honeypot should pass.", honeypot="")
    check("an empty honeypot is not treated as a bot",
          count_reports() == before + 1, f"{count_reports()} vs {before + 1}")

    # --- fill timing -------------------------------------------------------
    reset_limits(app)
    before = count_reports()
    r = post("Submitted far too quickly.", started="now")
    # Refused by showing the form again - this post carries a valid token -
    # with what was typed still in it, rather than sending the reporter back
    # to rescan the sticker.
    page = r.get_data(as_text=True)
    check("a form submitted instantly is refused, and shown the form again",
          r.status_code == 400 and 'name="problem_type"' in page, r.status_code)
    check("an instant submit saves nothing", count_reports() == before)
    check("the refusal is honest, not silent", "sent very quickly" in page)
    check("and what was typed is still there", "Submitted far too quickly." in page)

    reset_limits(app)
    before = count_reports()
    post("A normally paced report here.", started="fresh")
    check("a normally paced report goes through", count_reports() == before + 1)

    # Fail-open cases: none of these is abuse, and refusing a real report over
    # a missing or unreadable timestamp would be the worst trade available.
    reset_limits(app)
    before = count_reports()
    post("No timestamp at all here.", started=None)
    check("a missing timestamp does NOT block the report",
          count_reports() == before + 1, "report was refused")

    reset_limits(app)
    before = count_reports()
    post("Tampered timestamp report.", started="9999999999.deadbeefdeadbeef")
    check("an unverifiable timestamp does NOT block the report",
          count_reports() == before + 1, "report was refused")

    # A back-dated stamp must not verify, so it cannot be used to fake a slow
    # fill - it falls back to "cannot tell" rather than being trusted.
    check("a hand-written timestamp does not verify",
          app.form_fill_seconds(str(int(time.time()) - 600)) is None,
          "an unsigned value was trusted")
    check("a correctly signed timestamp does verify",
          app.form_fill_seconds(app.sign_form_timestamp(time.time() - 60)) is not None)

    # --- per-computer rate limit -------------------------------------------
    reset_limits(app)
    before = count_reports()
    results = [post(f"Same machine report number {i}.", hostname="LAB-PC-02")
               for i in range(5)]
    saved = count_reports() - before
    check("the per-computer limit caps reports for one machine",
          saved == app.REPORT_RATE_MAX_PER_COMPUTER,
          f"{saved} saved, limit is {app.REPORT_RATE_MAX_PER_COMPUTER}")
    blocked_page = results[-1].get_data(as_text=True)
    check("the blocked attempt says why, on the form it can be sent again from",
          results[-1].status_code == 429 and "wait a few minutes" in blocked_page
          and "Same machine report number 4." in blocked_page, results[-1].status_code)

    # A DIFFERENT machine is unaffected by that cap.
    before = count_reports()
    post("A different machine entirely.", hostname="LAB-PC-03")
    check("another computer is not blocked by the first one's limit",
          count_reports() == before + 1, "wrongly blocked")

    # --- per-IP rate limit --------------------------------------------------
    reset_limits(app)
    before = count_reports()
    for i in range(8):
        post(f"Spread across machines number {i}.", hostname=f"LAB-PC-0{(i % 6) + 1}",
             ip="203.0.113.55")
    saved = count_reports() - before
    check("the per-IP limit caps one sender across different machines",
          saved == app.REPORT_RATE_MAX_PER_IP,
          f"{saved} saved, limit is {app.REPORT_RATE_MAX_PER_IP}")

    # --- THE tunnel detail --------------------------------------------------
    # Every request above arrived with REMOTE_ADDR 127.0.0.1. If the limit were
    # keyed on remote_addr, the sender below would already be locked out by the
    # previous block - one visitor standing in for the whole internet.
    before = count_reports()
    r = post("A different person entirely.", hostname="LAB-PC-07",
             ip="198.51.100.9")
    check("a DIFFERENT public IP is counted separately, not lumped onto loopback",
          count_reports() == before + 1,
          "keyed on remote_addr - all tunnel visitors share one bucket")

    with app.app.test_request_context(
            "/report", headers={"CF-Connecting-IP": "203.0.113.7"}, base_url=TUNNEL_URL,
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"}):
        check("the client key is the header value, not 127.0.0.1",
              app.report_client_key() == "203.0.113.7", app.report_client_key())
    with app.app.test_request_context(
            "/report", headers={"CF-Connecting-IP": "203.0.113.7, 70.41.3.18"}, base_url=TUNNEL_URL,
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"}):
        check("a proxy chain takes the first hop",
              app.report_client_key() == "203.0.113.7", app.report_client_key())
    with app.app.test_request_context(
            "/report", environ_overrides={"REMOTE_ADDR": "192.168.1.44"}):
        check("on the LAN, with no header, the real address is used",
              app.report_client_key() == "192.168.1.44", app.report_client_key())

    # --- a rejected attempt must not burn the quota ------------------------
    reset_limits(app)
    for i in range(6):
        # Too short: refused by the description validator, never saved.
        post("bad", hostname="LAB-PC-08", ip="203.0.113.99")
    before = count_reports()
    post("A perfectly good report follows.", hostname="LAB-PC-08", ip="203.0.113.99")
    check("failed validation attempts do not count against the limit",
          count_reports() == before + 1,
          "a student who mistyped six times was locked out")

    # --- the window really is a window -------------------------------------
    reset_limits(app)
    with app._report_rate_lock:
        old = time.time() - app.REPORT_RATE_WINDOW_SECONDS - 5
        app._report_rate_hits["ip:203.0.113.200"] = [old] * 20
        app._report_rate_hits["pc:lab-pc-04"] = [old] * 20
    before = count_reports()
    post("Old hits should have expired.", hostname="LAB-PC-04", ip="203.0.113.200")
    check("hits older than the window stop counting",
          count_reports() == before + 1, "stale hits still blocking")
    with app._report_rate_lock:
        check("expired keys are pruned rather than growing forever",
              len(app._report_rate_hits.get("ip:203.0.113.200", [])) == 1,
              str(len(app._report_rate_hits.get("ip:203.0.113.200", []))))

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
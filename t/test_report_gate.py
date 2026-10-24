"""The presence gate and typed sticker codes: /report refuses a report that
cannot show it came from the lab (a scanned token, a typed code, or a lab
computer). A student always has one; someone with only the URL has none.
"""

import importlib
import os
import secrets
import shutil
import sys
import tempfile
import time
# unquote_plus, not unquote: a query string encodes spaces as "+", and
# unquote leaves those alone - so every "does the message say X" assertion
# would compare against a string full of plus signs and fail on wording that
# is actually correct.
from urllib.parse import unquote_plus

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_DIR = os.path.join(PROJECT_ROOT, "server")

SECRET = "f" * 64
LAB_IP = "10.20.30.40"

passed, failed = [], []


def check(label, condition, detail=""):
    (passed if condition else failed).append(label if condition else f"{label}  <-- {detail}")


def stage():
    tmp = tempfile.mkdtemp(prefix="appease-gate-")
    dest = os.path.join(tmp, "server")
    shutil.copytree(
        SERVER_DIR, dest,
        ignore=shutil.ignore_patterns("appease.db*", "__pycache__", "*.pyc", ".env"),
    )
    return tmp, dest


def load_app(dest, secret=SECRET):
    for mod in ("app", "database"):
        sys.modules.pop(mod, None)
    if secret is None:
        os.environ.pop("REPORT_QR_SECRET", None)
    else:
        os.environ["REPORT_QR_SECRET"] = secret
    os.environ.pop("PUBLIC_REPORT_HOST", None)
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
        "INSERT OR IGNORE INTO computers (hostname, location, ip_address, status) "
        "VALUES ('LAB-PC-01', 'Lab 1', ?, 'online')", (LAB_IP,))
    conn.execute(
        "INSERT OR IGNORE INTO computers (hostname, location, ip_address, status) "
        "VALUES ('LAB-PC-02', 'Lab 1', '10.20.30.41', 'online')")
    conn.commit()
    conn.close()
    appmod.app.config["TESTING"] = True
    return appmod


def count_reports():
    import database
    conn = database.get_db()
    total = conn.execute("SELECT COUNT(*) AS n FROM problem_reports").fetchone()["n"]
    conn.close()
    return total


def last_source():
    import database
    conn = database.get_db()
    row = conn.execute(
        "SELECT report_source FROM problem_reports ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return row["report_source"] if row else None


tmpdir, dest = stage()
try:
    app = load_app(dest)
    client = app.app.test_client()
    counter = {"n": 0}

    def post(hostname="LAB-PC-01", from_ip="203.0.113.5", **extra):
        counter["n"] += 1
        data = {
            "computer_hostname": hostname, "problem_type": "keyboard",
            "description": f"Gate test report number {counter['n']} here.",
            "reporter_role": "student", "reporter_name": "Test Student", "reporter_email": "student@lab.example.com",
            "reporter_student_number": "2021400001", "reporter_program": "BSIT",
            "reporter_section": "4A",
            "form_started": app.sign_form_timestamp(time.time() - 30),
        }
        data.update(extra)
        with app._report_rate_lock:
            app._report_rate_hits.clear()
        return client.post("/report", data=data, follow_redirects=False,
                           environ_overrides={"REMOTE_ADDR": from_ip})

    def refused(response):
        location = unquote_plus(response.headers.get("Location") or "")
        return "error=" in location or "error" in location

    # --- the code itself ---------------------------------------------------
    code = app.computer_short_code("LAB-PC-01")
    check("a short code is generated", bool(code), str(code))
    check("the code is six characters", len(code or "") == 6, code)
    check("the code uses only the printable alphabet",
          all(ch in app.SHORT_CODE_ALPHABET for ch in code or ""), code)
    check("the code omits the characters that get misread",
          not any(ch in (code or "") for ch in "ILOU"), code)
    check("the code is stable across calls",
          app.computer_short_code("LAB-PC-01") == code)
    check("the code is case-insensitive on the hostname",
          app.computer_short_code("lab-pc-01") == code)
    check("a different computer gets a different code",
          app.computer_short_code("LAB-PC-02") != code)
    check("the code is NOT a slice of the QR token",
          code not in (app.sign_computer("LAB-PC-01") or "").upper(), code)

    check("typing it with spaces still resolves",
          app.resolve_short_code(__import__("database").get_db(),
                                 " ".join(code)) == "LAB-PC-01")
    check("typing it with hyphens still resolves",
          app.resolve_short_code(__import__("database").get_db(),
                                 "-".join(code)) == "LAB-PC-01")
    check("typing it in lower case still resolves",
          app.resolve_short_code(__import__("database").get_db(),
                                 code.lower()) == "LAB-PC-01")
    lookalike = code.replace("0", "O").replace("1", "I")
    check("O for zero and I for one are read as intended",
          app.resolve_short_code(__import__("database").get_db(),
                                 lookalike) == "LAB-PC-01", lookalike)
    check("a wrong code resolves to nothing",
          app.resolve_short_code(__import__("database").get_db(), "ZZZZZZ") is None)
    check("a truncated code resolves to nothing",
          app.resolve_short_code(__import__("database").get_db(), code[:4]) is None)
    check("an empty code resolves to nothing",
          app.resolve_short_code(__import__("database").get_db(), "") is None)

    # --- the gate ----------------------------------------------------------
    before = count_reports()
    r = post()
    check("a report with NO proof of presence is refused", refused(r), r.headers.get("Location"))
    check("a refused report saves nothing", count_reports() == before)

    location = unquote_plus(r.headers.get("Location") or "")
    check("the refusal names the QR sticker", "QR sticker" in location, location)
    check("the refusal names the typed code", "code printed under it" in location, location)
    check("the refusal names the desktop shortcut", "shortcut" in location, location)

    # --- way in 1: the scanned token ---------------------------------------
    before = count_reports()
    post(qr_token=app.sign_computer("LAB-PC-01"), qr_hostname="LAB-PC-01")
    check("a scanned token gets through", count_reports() == before + 1)
    check("and is recorded as 'sticker'", last_source() == "sticker", str(last_source()))

    before = count_reports()
    r = post(qr_token="0000000000000000", qr_hostname="LAB-PC-01")
    check("a FORGED token is refused", refused(r) and count_reports() == before)

    before = count_reports()
    r = post(qr_token=app.sign_computer("LAB-PC-02"), qr_hostname="LAB-PC-01")
    check("a token for a different machine is refused",
          refused(r) and count_reports() == before)

    # --- way in 2: the typed code ------------------------------------------
    before = count_reports()
    post(qr_code=code)
    check("a typed code gets through", count_reports() == before + 1)
    check("and is recorded as 'code'", last_source() == "code", str(last_source()))

    before = count_reports()
    post(qr_code=code.lower())
    check("a lower-case typed code gets through", count_reports() == before + 1)

    before = count_reports()
    r = post(qr_code="ZZZZZZ")
    check("a wrong typed code is refused", refused(r) and count_reports() == before)

    # A code for LAB-PC-02 while reporting LAB-PC-01 is legitimate: it proves
    # lab presence, and reporting the machine beside the one you read the
    # sticker off is exactly the case the editable dropdown exists for.
    before = count_reports()
    post(hostname="LAB-PC-01", qr_code=app.computer_short_code("LAB-PC-02"))
    check("a code from the machine NEXT to the broken one still gets through",
          count_reports() == before + 1)

    # --- way in 3: a registered lab computer --------------------------------
    before = count_reports()
    post(from_ip=LAB_IP)
    check("a request from a registered lab PC gets through",
          count_reports() == before + 1)
    check("and is recorded as 'lan'", last_source() == "lan", str(last_source()))

    before = count_reports()
    r = post(from_ip="10.20.30.99")
    check("an unregistered address on the same subnet is refused",
          refused(r) and count_reports() == before)

    # --- the tunnel case ----------------------------------------------------
    # Every tunnelled request arrives on loopback. If 127.0.0.1 ever matched a
    # computer row, the entire internet would be inside the gate.
    before = count_reports()
    r = post(from_ip="127.0.0.1")
    check("a loopback request with no token is refused",
          refused(r) and count_reports() == before,
          "the tunnel would let the whole internet through")

    before = count_reports()
    post(from_ip="127.0.0.1", qr_token=app.sign_computer("LAB-PC-01"),
         qr_hostname="LAB-PC-01")
    check("a tunnelled request WITH a valid token still gets through",
          count_reports() == before + 1, "phones on cellular would be locked out")

    # --- precedence ---------------------------------------------------------
    before = count_reports()
    post(from_ip=LAB_IP, qr_token=app.sign_computer("LAB-PC-01"),
         qr_hostname="LAB-PC-01")
    check("token beats LAN when both are present",
          last_source() == "sticker", str(last_source()))

    post(from_ip=LAB_IP, qr_code=code)
    check("a typed code beats LAN when both are present",
          last_source() == "code", str(last_source()))

    # --- the PAGE is gated, not just the submit -----------------------------
    # Gating only the submit would still serve the whole form, computer
    # list included, to anybody typing the address into a phone.
    page = client.get("/report").get_data(as_text=True)
    check("typing the address alone does NOT show the report form",
          'name="problem_type"' not in page, "the form was served unprompted")
    check("it shows the code-entry gate instead",
          'name="qr_code"' in page and "Code on the sticker" in page)
    check("the gate does not leak the list of computers",
          "LAB-PC-01" not in page, "hostnames exposed to an anonymous visitor")
    check("the gate says what to do without a sticker",
          "shortcut" in page.lower())

    scanned = client.get(
        f"/report?computer=LAB-PC-01&t={app.sign_computer('LAB-PC-01')}"
    ).get_data(as_text=True)
    check("a scanned link opens the real form",
          'name="problem_type"' in scanned, "form not served after a valid scan")
    check("the scanned form has no code box on it",
          'name="qr_code"' not in scanned, "redundant field shown after a scan")

    forged = client.get("/report?computer=LAB-PC-01&t=0000000000000000").get_data(as_text=True)
    check("a forged link gets the gate, not the form",
          'name="problem_type"' not in forged)

    lan_page = client.get("/report", environ_overrides={"REMOTE_ADDR": LAB_IP}).get_data(as_text=True)
    check("a lab computer opens the real form with no token at all",
          'name="problem_type"' in lan_page)

    # --- the code-entry page ------------------------------------------------
    with app._report_rate_lock:
        app._report_rate_hits.clear()
    unlocked = client.post("/report/access", data={"qr_code": code}, follow_redirects=False)
    target = unquote_plus(unlocked.headers.get("Location") or "")
    check("a correct code redirects into the form",
          unlocked.status_code == 302 and "/report?" in target, target)
    check("the redirect carries a real signed token",
          f"t={app.sign_computer('LAB-PC-01')}" in target, target)
    check("the redirect names the machine the code belongs to",
          "computer=LAB-PC-01" in target, target)
    check("the redirect marks it as arrived-by-code", "c=1" in target, target)

    followed = client.get(target.replace("/report?", "/report?")).get_data(as_text=True)
    check("following that redirect really does open the form",
          'name="problem_type"' in followed)

    with app._report_rate_lock:
        app._report_rate_hits.clear()
    wrong = client.post("/report/access", data={"qr_code": "ZZZZZZ"})
    check("a wrong code stays on the gate",
          b'name="qr_code"' in wrong.data and b'name="problem_type"' not in wrong.data)
    check("a wrong code does not say whether it exists",
          b"not recognised" in wrong.data, wrong.data[:200])

    # Guessing must be expensive, or a six-character code is a weekend's work.
    with app._report_rate_lock:
        app._report_rate_hits.clear()
    statuses = [client.post("/report/access", data={"qr_code": "ZZZZZZ"}).status_code
                for _ in range(app.REPORT_CODE_ATTEMPT_MAX + 2)]
    check("repeated wrong codes are eventually refused",
          429 in statuses, str(statuses))
    check("the limit does not bite immediately",
          statuses[0] != 429, str(statuses[:3]))

    # --- search engines -----------------------------------------------------
    robots = client.get("/robots.txt")
    check("robots.txt exists", robots.status_code == 200, str(robots.status_code))
    check("robots.txt disallows everything",
          b"Disallow: /" in robots.data, robots.data)
    check("every response carries a noindex header",
          "noindex" in (robots.headers.get("X-Robots-Tag") or ""),
          robots.headers.get("X-Robots-Tag"))
    check("the gate page carries a noindex meta tag",
          'name="robots"' in page and "noindex" in page)
    check("the form carries a noindex meta tag too",
          'name="robots"' in scanned and "noindex" in scanned)

    # --- the label prints it ------------------------------------------------
    admin = app.app.test_client()
    with admin.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_role"] = "admin"
    sheet = admin.get("/computers/qr-labels").get_data(as_text=True)
    check("the printed label carries the code", code in sheet, "code missing from label")
    check("the printed label carries the address to type it into",
          "/report" in sheet)

    # --- no secret configured -----------------------------------------------
    # Every proof of presence rests on the secret. Without one nothing can be
    # signed, so the gate must fall back to the LAN check alone rather than
    # refusing every report ever.
    app2 = load_app(dest, secret=None)
    check("no secret means no short code",
          app2.computer_short_code("LAB-PC-01") is None)
    client2 = app2.app.test_client()
    counter["n"] += 1
    unsigned = client2.post("/report", data={
        "computer_hostname": "LAB-PC-01", "problem_type": "keyboard",
        "description": "Report with no secret configured at all.",
        "reporter_role": "student", "reporter_name": "Test Student", "reporter_email": "student@lab.example.com",
        "reporter_student_number": "2021400001", "reporter_program": "BSIT",
        "reporter_section": "4A",
    }, environ_overrides={"REMOTE_ADDR": LAB_IP}, follow_redirects=False)
    check("with no secret, a lab computer can still report",
          "success" in unquote_plus(unsigned.headers.get("Location") or ""),
          unsigned.headers.get("Location"))

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
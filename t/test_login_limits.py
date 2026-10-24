"""Rate limits on /login and /forgot-password. A cooldown refusal is
BYTE-identical for real or made-up usernames and right or wrong passwords,
so it reveals neither; every path runs one full-cost password check
(asserted structurally, not timed); and every count comes from the
limiter's own numbers, so nothing passes without reaching a cooldown.
"""

import importlib
import logging
import os
import re
import secrets
import shutil
import sys
import tempfile
import threading

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_DIR = os.path.join(PROJECT_ROOT, "server")

USERNAME = "limit.admin"
PASSWORD = "Limit!2026"
EMAIL = "limit@example.com"
OTHER_USERNAME = "other.admin"
OTHER_PASSWORD = "Other!2026"
# The reset tests change this account's password, so they get an account of
# their own rather than changing the one every other test signs in with.
RESET_USERNAME = "reset.admin"
RESET_PASSWORD = "Reset!2026"
RESET_EMAIL = "reset@example.com"

passed, failed = [], []


def check(label, condition, detail=""):
    (passed if condition else failed).append(label if condition else f"{label}  <-- {detail}")


def stage():
    tmp = tempfile.mkdtemp(prefix="appease-login-limits-")
    dest = os.path.join(tmp, "server")
    shutil.copytree(
        SERVER_DIR, dest,
        ignore=shutil.ignore_patterns(
            "appease.db*", "__pycache__", "*.pyc", ".env", "uploads", "staging",
            "report_photos", "avatars"),
    )
    return tmp, dest


def load_app(dest):
    for mod in ("app", "database"):
        sys.modules.pop(mod, None)
    # Mail unset, so nothing here can reach a real server. send_email is
    # replaced below anyway, to count what WOULD have been sent.
    for name in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "PUBLIC_REPORT_HOST"):
        os.environ.pop(name, None)
    if dest not in sys.path:
        sys.path.insert(0, dest)
    os.chdir(dest)
    os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
    appmod = importlib.import_module("app")
    import database
    database.init_db()
    from werkzeug.security import generate_password_hash
    conn = database.get_db()
    for username, password, email in ((USERNAME, PASSWORD, EMAIL),
                                      (OTHER_USERNAME, OTHER_PASSWORD, "other@example.com"),
                                      (RESET_USERNAME, RESET_PASSWORD, RESET_EMAIL)):
        conn.execute(
            "INSERT INTO admins (username, password_hash, email, role, created_at) "
            "VALUES (?, ?, ?, 'admin', CURRENT_TIMESTAMP)",
            (username, generate_password_hash(password), email))
    conn.commit()
    conn.close()
    appmod.app.config["TESTING"] = True
    return appmod


class FakeClock:
    def __init__(self):
        self.now = 10_000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        message = record.getMessage()
        if "[rate limit]" in message:
            self.lines.append(message)


def reset(limiter):
    with limiter._lock:
        limiter._hits.clear()
        limiter._full = False


def method_of(password_hash):
    """'scrypt:32768:8:1' - the method AND its cost parameters."""
    return (password_hash or "").split("$", 1)[0]


def headers_of(response):
    return sorted((k, v) for k, v in response.headers.items())


tmpdir, dest = stage()
try:
    app = load_app(dest)
    import database

    LOGIN = app.LOGIN_LIMITER
    FORGOT = app.FORGOT_PASSWORD_LIMITER
    clock = FakeClock()
    LOGIN.clock = clock
    FORGOT.clock = clock

    # Every password check the app makes, with the hash it was checked against.
    real_check = app.check_password_hash
    hash_calls = []

    def counting_check(password_hash, password):
        hash_calls.append(password_hash)
        return real_check(password_hash, password)

    app.check_password_hash = counting_check

    # Every email the app would have sent, and what it said - the reset tests
    # take their link out of the body, the way a person would.
    sent = []
    sent_bodies = []

    def fake_send(to_address, subject, body):
        sent.append(to_address)
        sent_bodies.append(body)
        return True, None

    app.send_email = fake_send
    # Sends and reset tokens happen in a background thread; run that job at
    # once instead, so what was sent can be read back without a race.
    app.run_in_background = lambda target: target()

    log = ListHandler()
    app.app.logger.addHandler(log)

    conn = database.get_db()
    stored_hash = conn.execute(
        "SELECT password_hash FROM admins WHERE username = ?", (USERNAME,)).fetchone()[0]
    conn.close()

    def login(username, password, client=None):
        c = client or app.app.test_client()
        return c.post("/login", data={"username": username, "password": password})

    def forgot(email):
        return app.app.test_client().post("/forgot-password", data={"email": email})

    def count_rows(table):
        conn = database.get_db()
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        conn.close()
        return n

    # Every count and offset below comes from these two, so the tests follow
    # the limiter's numbers instead of restating them.
    MAX = LOGIN.max_hits
    WINDOW = LOGIN.window_seconds

    from markupsafe import escape
    COOLDOWN = str(escape(app.LOGIN_COOLDOWN_MESSAGE)).encode("utf-8")
    INVALID = b"Invalid username or password"

    def is_cooldown(response):
        """Refused BY THE COOLDOWN - not merely a 200 with some error on it.

        Every test that calls a response limited goes through this. A status
        of 200 alone is also what a wrong password returns, which is how a
        test can pass without ever reaching a cooldown.
        """
        return (response.status_code == 200
                and b'<p class="auth-error">' + COOLDOWN + b"</p>" in response.data
                and INVALID not in response.data
                and not response.headers.getlist("Set-Cookie"))

    def same(a, b):
        return (a.status_code == b.status_code and a.data == b.data
                and headers_of(a) == headers_of(b))

    # --- the numbers are the agreed ones ----------------------------------
    check("login: 10 failures", LOGIN.max_hits == 10, LOGIN.max_hits)
    check("login: 30-minute window", LOGIN.window_seconds == 1800, LOGIN.window_seconds)
    check("login: key cap 64", LOGIN.max_key_length == 64, LOGIN.max_key_length)
    check("login: 50,000 tracked keys", LOGIN.max_keys == 50000, LOGIN.max_keys)
    check("login: evicts when full", LOGIN.when_full == app.SlidingWindowLimiter.EVICT,
          LOGIN.when_full)
    check("forgot-password: 5 requests", FORGOT.max_hits == 5, FORGOT.max_hits)
    check("forgot-password: 30-minute window", FORGOT.window_seconds == 1800,
          FORGOT.window_seconds)
    check("forgot-password: key cap 254", FORGOT.max_key_length == 254, FORGOT.max_key_length)
    check("forgot-password: 10,000 tracked keys", FORGOT.max_keys == 10000, FORGOT.max_keys)
    check("forgot-password: refuses when full",
          FORGOT.when_full == app.SlidingWindowLimiter.REFUSE, FORGOT.when_full)

    # The two facts the message has to carry: the real duration and the way
    # out. "A few minutes" undersold a window that is close to thirty after a
    # burst of failures.
    check("the cooldown message gives the real duration",
          "about half an hour" in app.LOGIN_COOLDOWN_MESSAGE, app.LOGIN_COOLDOWN_MESSAGE)
    check("... and the way out", "reset your password" in app.LOGIN_COOLDOWN_MESSAGE,
          app.LOGIN_COOLDOWN_MESSAGE)
    # "By email", because email is optional on every account: the message
    # cannot vary by account, so it names the precondition instead of sending
    # somebody with no address into a reset that silently never arrives.
    check("... and names the precondition for it", "by email" in app.LOGIN_COOLDOWN_MESSAGE,
          app.LOGIN_COOLDOWN_MESSAGE)
    check("... and does not undersell it", "few minutes" not in app.LOGIN_COOLDOWN_MESSAGE,
          app.LOGIN_COOLDOWN_MESSAGE)
    check("... and gives no countdown", not re.search(r"\d", app.LOGIN_COOLDOWN_MESSAGE),
          app.LOGIN_COOLDOWN_MESSAGE)

    # --- 1. outside a cooldown: a made-up username is a wrong password ----
    reset(LOGIN)
    wrong = login(USERNAME, "Wrong!2026")
    check("the reference response really is the invalid-credentials page",
          wrong.status_code == 200 and INVALID in wrong.data and COOLDOWN not in wrong.data,
          f"{wrong.status_code}")
    check("a wrong password sets no cookie",
          not wrong.headers.getlist("Set-Cookie"), wrong.headers.getlist("Set-Cookie"))

    unknown = login("no.such.user", "Wrong!2026")
    check("an unknown username is byte-identical to a wrong password",
          same(unknown, wrong), "differs")

    # --- 2. in a cooldown: ONE page for every username and every password -
    # The acceptance criterion. Real or made-up username, right or wrong
    # password: four refusals, one set of bytes. Equality is transitive, so
    # comparing each against one reference covers every pair.
    reset(LOGIN)
    for _ in range(MAX):
        login(USERNAME, "Wrong!2026")
        login("ghost.user", "Wrong!2026")
    right_client = app.app.test_client()
    matrix = [
        ("real username, wrong password", login(USERNAME, "Wrong!2026")),
        ("real username, CORRECT password", login(USERNAME, PASSWORD, right_client)),
        ("made-up username, wrong password", login("ghost.user", "Wrong!2026")),
        ("made-up username, a real account's password", login("ghost.user", PASSWORD)),
    ]
    ref_label, reference = matrix[0]
    for label, response in matrix:
        check(f"cooldown [{label}]: refused with the cooldown message",
              is_cooldown(response), f"{response.status_code}")
    for label, response in matrix[1:]:
        check(f"cooldown [{label}]: byte-identical to [{ref_label}]",
              same(response, reference), "differs")
    with right_client.session_transaction() as sess:
        check("a refused CORRECT password signs nobody in", "admin_id" not in sess, dict(sess))

    # --- 3. the cooldown is its own page ----------------------------------
    check("the cooldown page is not the invalid-credentials page",
          reference.data != wrong.data and INVALID not in reference.data, "same page")
    check("... and carries the message in the page's error slot",
          b'<p class="auth-error">' + COOLDOWN + b"</p>" in reference.data, "not found")

    # The cooldown starts on the attempt AFTER the last counted failure.
    reset(LOGIN)
    for _ in range(MAX - 1):
        login(USERNAME, "Wrong!2026")
    last_counted = login(USERNAME, "Wrong!2026")
    check(f"failure {MAX} is still reported as a wrong password",
          same(last_counted, wrong), "differs")
    check(f"attempt {MAX + 1} is the first one refused by the cooldown",
          is_cooldown(login(USERNAME, "Wrong!2026")), "not refused")

    other = login(OTHER_USERNAME, OTHER_PASSWORD)
    check("another username is unaffected by one account's cooldown",
          other.status_code == 302, f"got {other.status_code}")

    # --- exactly one password check, at full cost, on every path ----------
    def calls_for(username, password):
        del hash_calls[:]
        response = login(username, password)
        return list(hash_calls), response

    reset(LOGIN)
    calls, _ = calls_for("no.such.user", "Wrong!2026")
    check("an unknown username makes exactly one password check",
          len(calls) == 1, f"{len(calls)} calls")
    check("... against the dummy hash", calls[:1] == [app.DUMMY_PASSWORD_HASH], calls[:1])
    check("... whose method and cost match a real account's",
          method_of(calls[0] if calls else "") == method_of(stored_hash),
          f"{method_of(calls[0] if calls else '')} vs {method_of(stored_hash)}")
    check("the dummy hash's cost is not a weak default",
          method_of(app.DUMMY_PASSWORD_HASH) == method_of(
              app.generate_password_hash("anything")),
          method_of(app.DUMMY_PASSWORD_HASH))

    calls, _ = calls_for(USERNAME, "Wrong!2026")
    check("a real account's wrong password makes exactly one check, against its hash",
          calls == [stored_hash], f"{len(calls)} calls")

    calls, _ = calls_for("", "")
    check("an empty username still makes exactly one check",
          len(calls) == 1, f"{len(calls)} calls")

    reset(LOGIN)
    for _ in range(MAX):
        login(USERNAME, "Wrong!2026")
    calls, response = calls_for(USERNAME, PASSWORD)
    check("(that real account really was refused by the cooldown)", is_cooldown(response))
    check("a LIMITED real account still makes one check against its real hash",
          calls == [stored_hash], f"{len(calls)} calls")
    for _ in range(MAX):
        login("ghost.user", "Wrong!2026")
    calls, response = calls_for("ghost.user", "Wrong!2026")
    check("(that unknown username really was refused by the cooldown)", is_cooldown(response))
    check("a LIMITED unknown username still makes one check against the dummy",
          calls == [app.DUMMY_PASSWORD_HASH], f"{len(calls)} calls")

    # --- unknown usernames count exactly the same -------------------------
    check("failures against an unknown username are counted",
          len(LOGIN._hits.get("ghost.user", [])) == MAX, LOGIN._hits.get("ghost.user"))
    check("... and put it into cooldown like a real one", LOGIN.hit("ghost.user") is False)

    reset(LOGIN)
    for _ in range(MAX):
        login("  LIMIT.Admin ", "Wrong!2026")
    check("the key is the stripped, lowercased username",
          is_cooldown(login(USERNAME, PASSWORD)),
          "case-variant failures did not count against the account")

    # --- a successful sign-in clears the counter --------------------------
    reset(LOGIN)
    for _ in range(MAX - 1):
        login(USERNAME, "Wrong!2026")
    ok = login(USERNAME, PASSWORD)
    check("a sign-in under the limit succeeds", ok.status_code == 302, f"got {ok.status_code}")
    check("... and removes the username from the table", USERNAME not in LOGIN._hits,
          LOGIN._hits.get(USERNAME))
    for _ in range(MAX - 1):
        login(USERNAME, "Wrong!2026")
    again = login(USERNAME, PASSWORD)
    check(f"after that success, a fresh {MAX - 1} failures still allow a sign-in",
          again.status_code == 302,
          f"got {again.status_code} - old failures were not cleared")

    # --- the window expires on its own ------------------------------------
    reset(LOGIN)
    for _ in range(MAX):
        login(USERNAME, "Wrong!2026")
    # Retrying once a minute through the cooldown must not extend it.
    elapsed = 0
    every_retry_refused = True
    while elapsed + 60 < WINDOW:
        clock.advance(60)
        elapsed += 60
        refused = is_cooldown(login(USERNAME, "Wrong!2026"))
        every_retry_refused = every_retry_refused and refused
    check("every retry through the cooldown was refused by it", every_retry_refused)
    check("refused retries are not recorded - the list never passes the limit",
          len(LOGIN._hits.get(USERNAME, [])) == MAX, len(LOGIN._hits.get(USERNAME, [])))
    clock.advance(WINDOW - 1 - elapsed)  # one second before the window ends
    check("still locked one second before the window ends",
          is_cooldown(login(USERNAME, PASSWORD)), "unlocked early")
    clock.advance(2)                     # one second after
    unlocked = login(USERNAME, PASSWORD)
    check("unlocked on its own once the window has passed, despite the retries",
          unlocked.status_code == 302, f"got {unlocked.status_code}")

    # Rolling, not fixed: failures age out one at a time. All but one early
    # and one late - once the early ones have expired the key is still in the
    # table (its newest failure is live), but it holds one failure, not MAX.
    reset(LOGIN)
    for _ in range(MAX - 1):
        login(USERNAME, "Wrong!2026")
    late_at = WINDOW * 2 // 3
    clock.advance(late_at)
    login(USERNAME, "Wrong!2026")
    check("(the key really is at its limit before the early failures expire)",
          is_cooldown(login(USERNAME, PASSWORD)))
    clock.advance(WINDOW - late_at + 1)
    rolling = login(USERNAME, PASSWORD)
    check("old failures age out one by one while a newer one is still live",
          rolling.status_code == 302, f"got {rolling.status_code}")

    # --- the key cap holds ------------------------------------------------
    reset(LOGIN)
    login("u" * 10000, "Wrong!2026")
    keys = list(LOGIN._hits)
    check("a huge username is stored cut to 64 characters",
          len(keys) == 1 and len(keys[0]) == 64, [len(k) for k in keys])
    reset(FORGOT)
    forgot("e" * 5000 + "@example.com")
    keys = list(FORGOT._hits)
    check("a huge email is stored cut to 254 characters",
          len(keys) == 1 and len(keys[0]) == 254, [len(k) for k in keys])

    # --- pruning holds ----------------------------------------------------
    reset(LOGIN)
    for i in range(2000):
        LOGIN.hit(f"flood-{i}")
    clock.advance(WINDOW + 1)
    LOGIN.hit("after-the-flood")
    check("expired keys are pruned, not left to accumulate",
          list(LOGIN._hits) == ["after-the-flood"], len(LOGIN._hits))

    # A key hit again later must survive while the keys around it expire,
    # and the ones that DID expire must still go - the ordering is what
    # lets pruning stop early, so it has to be kept up to date.
    reset(LOGIN)
    LOGIN.hit("early-then-late")
    clock.advance(1)
    LOGIN.hit("early-only")
    clock.advance(WINDOW - 100)
    LOGIN.hit("early-then-late")
    clock.advance(100)  # early-only is now a whole window old; early-then-late's newest is 100 s
    LOGIN.hit("trigger")
    check("a key re-hit later survives while an older key is pruned",
          sorted(LOGIN._hits) == ["early-then-late", "trigger"], sorted(LOGIN._hits))

    # --- the ceiling holds: login evicts ----------------------------------
    reset(LOGIN)
    for _ in range(MAX):
        LOGIN.hit("victim")
    for i in range(LOGIN.max_keys - 1):
        LOGIN.hit(f"fill-{i}")
    check("login table filled to exactly its ceiling", len(LOGIN._hits) == LOGIN.max_keys,
          len(LOGIN._hits))
    newcomer = LOGIN.hit("newcomer")
    check("a new username on a full login table is still allowed", newcomer is True)
    check("... the table does not grow past the ceiling", len(LOGIN._hits) == LOGIN.max_keys,
          len(LOGIN._hits))
    check("... and the least recently used key was the one evicted",
          "victim" not in LOGIN._hits and "newcomer" in LOGIN._hits, "wrong key evicted")
    check("a full login table logged exactly once",
          sum("login: table full" in line for line in log.lines) == 1, log.lines)
    reset(LOGIN)
    del log.lines[:]

    # --- the ceiling holds: forgot-password refuses -----------------------
    # The page an address with no account gets when NOTHING is limited - the
    # yardstick. Comparing two refused pages with each other would pass even
    # if every refusal rendered something different from the ordinary page.
    reset(FORGOT)
    ordinary_nomatch = forgot("nobody-at-all@example.com")

    reset(FORGOT)
    del sent[:]
    # Before the table fills: the full transition happens DURING the fill,
    # so a count taken afterwards would already include anything it wrote.
    audit_before = count_rows("audit_log")
    fill_started = clock.now
    # Fifty keys a second apart, so that later the clock can expire them ONE
    # AT A TIME - the only way to put the table at "one slot free" the way a
    # real flood does.
    for i in range(50):
        FORGOT.hit(f"staggered-{i}@example.com")
        clock.advance(1)
    for _ in range(2):
        FORGOT.hit("tracked@example.com")
    for i in range(FORGOT.max_keys - 51):
        FORGOT.hit(f"fill-{i}@example.com")
    check("forgot-password table filled to exactly its ceiling",
          len(FORGOT._hits) == FORGOT.max_keys, len(FORGOT._hits))
    check("an address not already tracked is refused on a full table",
          FORGOT.hit("new@example.com") is False)
    check("... and the table does not grow", len(FORGOT._hits) == FORGOT.max_keys,
          len(FORGOT._hits))
    check("an address already tracked and under its limit is still allowed",
          FORGOT.hit("tracked@example.com") is True)

    resets_before = count_rows("password_resets")
    full_page = forgot(EMAIL)
    check("a real account's reset on a full table returns the identical page",
          full_page.status_code == ordinary_nomatch.status_code
          and full_page.data == ordinary_nomatch.data
          and headers_of(full_page) == headers_of(ordinary_nomatch), "differs")
    check("... and sends nothing", sent == [], sent)
    check("... and issues no token", count_rows("password_resets") == resets_before,
          count_rows("password_resets"))

    for i in range(300):
        FORGOT.hit(f"more-flood-{i}@example.com")
    full_lines = [line for line in log.lines if "forgot-password: table full" in line]
    check("a full forgot-password table logs ONCE, not once per refused request",
          len(full_lines) == 1, f"{len(full_lines)} lines")
    check("... and that line says new keys are refused",
          bool(full_lines) and "REFUSED" in full_lines[0], full_lines)

    # Sitting at the edge must not flip the state back and forth. The way a
    # real flood does it: one key EXPIRES, a request for an address already
    # tracked sees the table one short, and the next new address fills the
    # slot again. The staggered keys expire one per second from here, while
    # everything else in the table is still live.
    clock.now = fill_started + 1800
    for i in range(50):
        FORGOT.hit("tracked@example.com")
        FORGOT.hit(f"edge-{i}@example.com")
        clock.advance(1)
    check("hovering at the ceiling does not log again",
          sum("table full" in line for line in log.lines) == 1
          and not any("drained" in line for line in log.lines), log.lines)

    clock.advance(1800 + 1)
    FORGOT.hit("first-after-the-window@example.com")
    FORGOT.hit("second-after-the-window@example.com")
    drained = [line for line in log.lines if "forgot-password: table drained" in line]
    check("the table draining is logged once", len(drained) == 1, f"{len(drained)} lines")
    check("the flood wrote nothing to the audit log",
          count_rows("audit_log") == audit_before, count_rows("audit_log"))

    # --- a limited forgot-password submission -----------------------------
    reset(FORGOT)
    del sent[:]
    for _ in range(5):
        forgot(EMAIL)
    check("five reset requests inside the window all send", len(sent) == 5, len(sent))
    resets_before = count_rows("password_resets")
    limited = forgot(" LIMIT@Example.com ")
    nomatch = forgot("nobody-at-all@example.com")
    check("the sixth - case and spaces aside - sends nothing", len(sent) == 5, len(sent))
    check("... issues no token", count_rows("password_resets") == resets_before,
          count_rows("password_resets"))
    check("... and returns the page byte-identical to an address with no account",
          limited.status_code == nomatch.status_code and limited.data == nomatch.data
          and headers_of(limited) == headers_of(nomatch), "differs")
    check("... which is the ordinary 'we've sent a link' page",
          limited.status_code == 200
          and b"We&#39;ve sent a reset link" in limited.data, limited.status_code)

    reset(FORGOT)
    for _ in range(5):
        forgot("nobody-at-all@example.com")
    check("submissions for an address with no account are counted too",
          len(FORGOT._hits.get("nobody-at-all@example.com", [])) == 5,
          FORGOT._hits.get("nobody-at-all@example.com"))

    reset(FORGOT)
    forgot("")
    check("an empty submission is not counted", len(FORGOT._hits) == 0, list(FORGOT._hits))

    reset(FORGOT)
    del sent[:]
    for _ in range(5):
        forgot(EMAIL)
    clock.advance(1800 + 1)
    forgot(EMAIL)
    check("forgot-password unlocks on its own after thirty minutes", len(sent) == 6, len(sent))

    # --- a completed password reset clears the cooldown -------------------
    # End to end: the token comes out of the captured email exactly as a
    # person would get it, not from a row the test wrote. The lockout uses a
    # different case and stray spaces, so clearing has to reach the same
    # normalised key the failures were counted under.
    def request_reset_link():
        reset(FORGOT)
        del sent_bodies[:]
        forgot(RESET_EMAIL)
        found = re.search(r"/reset-password/([A-Za-z0-9_-]+)", sent_bodies[-1]) \
            if sent_bodies else None
        return found.group(1) if found else None

    def lock_out(password):
        reset(LOGIN)
        for _ in range(MAX):
            login("  RESET.Admin ", "Wrong!2026")
        return is_cooldown(login(RESET_USERNAME, password))

    def still_locked(password):
        return (len(LOGIN._hits.get(RESET_USERNAME, [])) == MAX
                and is_cooldown(login(RESET_USERNAME, password)))

    def post_reset(token, password, confirm=None):
        return app.app.test_client().post(
            f"/reset-password/{token}",
            data={"password": password, "confirm_password": password if confirm is None else confirm})

    NEW_PASSWORD = "Renewed!Pass2026"
    NEWER_PASSWORD = "Renewed!Again2026"
    DEAD_LINK = b"has expired or has already been used"
    CHANGED = b"Your password has been changed"

    token = request_reset_link()
    check("reset: a link came out of the captured email", token is not None, sent_bodies)
    token = token or "missing"
    check("reset: the account is locked out before any of this", lock_out(RESET_PASSWORD))

    opened = app.app.test_client().get(f"/reset-password/{token}")
    check("reset: opening the link shows the form", opened.status_code == 200
          and b'name="confirm_password"' in opened.data, opened.status_code)
    check("reset: ... and does NOT clear the cooldown", still_locked(RESET_PASSWORD))

    mismatch = post_reset(token, NEW_PASSWORD, confirm=NEWER_PASSWORD)
    check("reset: mismatched passwords are rejected",
          b"The two passwords do not match." in mismatch.data, mismatch.status_code)
    check("reset: ... and do NOT clear the cooldown", still_locked(RESET_PASSWORD))

    weak = post_reset(token, "short")
    check("reset: a too-weak password is rejected",
          b"at least" in weak.data and CHANGED not in weak.data, weak.status_code)
    check("reset: ... and does NOT clear the cooldown", still_locked(RESET_PASSWORD))

    # Expired: the link is real and otherwise valid, only out of date.
    expired_token = request_reset_link()
    check("reset: a second link came out for the expiry test", expired_token is not None)
    expired_token = expired_token or "missing"
    check("reset: ... which spent the first one",
          DEAD_LINK in post_reset(token, NEW_PASSWORD).data)
    conn = database.get_db()
    moved = conn.execute(
        "UPDATE password_resets SET expires_at = '2000-01-01 00:00:00' WHERE token_hash = ?",
        (app._hash_reset_token(expired_token),)).rowcount
    conn.commit()
    conn.close()
    check("reset: the expiry test moved exactly one token into the past", moved == 1, moved)
    check("reset: the account is locked out again", lock_out(RESET_PASSWORD))
    expired = post_reset(expired_token, NEW_PASSWORD)
    check("reset: an expired token changes nothing",
          DEAD_LINK in expired.data and CHANGED not in expired.data, expired.status_code)
    check("reset: ... and does NOT clear the cooldown", still_locked(RESET_PASSWORD))

    # Completed.
    good_token = request_reset_link()
    check("reset: a third link came out for the completed reset", good_token is not None)
    good_token = good_token or "missing"
    check("reset: the account is locked out again", lock_out(RESET_PASSWORD))
    done = post_reset(good_token, NEW_PASSWORD)
    check("reset: a completed reset reports the change", CHANGED in done.data, done.status_code)
    check("reset: ... and CLEARS the cooldown", RESET_USERNAME not in LOGIN._hits,
          LOGIN._hits.get(RESET_USERNAME))
    signed_in = login(RESET_USERNAME, NEW_PASSWORD)
    check("reset: ... so the new password signs in straight away",
          signed_in.status_code == 302, f"got {signed_in.status_code}")

    # Reused: the same link a second time, after locking out again.
    check("reset: locked out again after the reset", lock_out(NEW_PASSWORD))
    reused = post_reset(good_token, NEWER_PASSWORD)
    check("reset: a reused token changes nothing",
          DEAD_LINK in reused.data and CHANGED not in reused.data, reused.status_code)
    check("reset: ... and does NOT clear the cooldown", still_locked(NEW_PASSWORD))
    reset(LOGIN)
    check("reset: ... and the password is still the one from the completed reset",
          login(RESET_USERNAME, NEW_PASSWORD).status_code == 302)

    # --- check-and-record is one step -------------------------------------
    reset(LOGIN)
    racers = MAX * 2
    barrier = threading.Barrier(racers)
    results = []
    results_lock = threading.Lock()

    def race():
        barrier.wait()
        outcome = LOGIN.hit("contended")
        with results_lock:
            results.append(outcome)

    threads = [threading.Thread(target=race) for _ in range(racers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check(f"{racers} simultaneous attempts on one key: exactly {MAX} get through",
          results.count(True) == MAX, f"{results.count(True)} got through")

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

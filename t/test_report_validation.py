"""Problem report validation. A refused report shows the form AGAIN with
what was typed, the message under its field and the same sticker link. The
page's own patterns, taken from the rendered page, run against the server's
inputs to catch drift; the order of the checks lives in the page's
JavaScript and needs a click-test.
"""

import html
import importlib
import io
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import time
from urllib.parse import unquote_plus

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_DIR = os.path.join(PROJECT_ROOT, "server")
LAB_IP = "10.20.30.40"

passed, failed = [], []


def check(label, condition, detail=""):
    # ascii(): a Windows console prints in cp1252, and these labels carry
    # emoji and accented letters.
    label = label.encode("ascii", "backslashreplace").decode("ascii")
    detail = ascii(detail) if not isinstance(detail, str) else detail.encode("ascii", "backslashreplace").decode()
    (passed if condition else failed).append(label if condition else f"{label}  <-- {detail}")


tmpdir = tempfile.mkdtemp(prefix="appease-validation-")
dest = os.path.join(tmpdir, "server")
shutil.copytree(SERVER_DIR, dest, ignore=shutil.ignore_patterns(
    "appease.db*", "__pycache__", "*.pyc", ".env", "uploads", "staging", "report_photos", "avatars"))
os.makedirs(os.path.join(dest, "static", "report_photos"), exist_ok=True)

os.environ["REPORT_QR_SECRET"] = "f" * 64
os.environ["DEMO_MODE_ENABLED"] = "1"
os.environ.pop("PUBLIC_REPORT_HOST", None)
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
sys.path.insert(0, dest)
os.chdir(dest)

try:
    app = importlib.import_module("app")
    import database
    database.init_db()
    conn = database.get_db()
    for host, ip in (("LAB-PC-01", LAB_IP), ("LAB-PC-02", "10.20.30.41")):
        conn.execute("INSERT INTO computers (hostname, location, ip_address, status) "
                     "VALUES (?, 'Lab 1', ?, 'online')", (host, ip))
    conn.commit()
    conn.close()
    app.app.config["TESTING"] = True
    client = app.app.test_client()

    TOKEN = app.sign_computer("LAB-PC-01")
    GOOD = {
        "reporter_role": "student", "reporter_name": "Juan Dela Cruz",
        "reporter_email": "juan@gmail.com", "reporter_student_number": "2023000000",
        "reporter_program": "BSIT", "reporter_section": "4C",
        "computer_hostname": "LAB-PC-02", "problem_type": "mouse",
        "description": "The mouse does not click at all.",
    }

    def q(sql, args=()):
        db = database.get_db()
        try:
            return db.execute(sql, args).fetchall()
        finally:
            db.close()

    def count():
        return q("SELECT COUNT(*) AS n FROM problem_reports")[0]["n"]

    def post(proof="sticker", photos=(), ip="203.0.113.5", **fields):
        data = dict(GOOD)
        data["form_started"] = app.sign_form_timestamp(time.time() - 30)
        if proof == "sticker":
            data.update(qr_token=TOKEN, qr_hostname="LAB-PC-01")
        elif proof == "code":
            data.update(qr_token=TOKEN, qr_hostname="LAB-PC-01", via_code="1")
        elif proof == "lan":
            ip = LAB_IP
        data.update(fields)
        data = {k: v for k, v in data.items() if v is not None}
        if photos:
            data["report_photos"] = [(io.BytesIO(body), name) for name, body in photos]
        with app._report_rate_lock:
            app._report_rate_hits.clear()
        return client.post("/report", data=data, content_type="multipart/form-data",
                           environ_overrides={"REMOTE_ADDR": ip}, follow_redirects=False)

    def text(res):
        return res.get_data(as_text=True)

    def is_form(page):
        return 'name="problem_type"' in page

    def field_error(page, name):
        """The message SHOWING under a field - a hidden span does not match."""
        found = re.search(rf'id="{name}-error" class="text-xs text-danger">([^<]*)<', page)
        return html.unescape(found.group(1)) if found else None

    def shown_errors(page):
        return re.findall(r'id="([a-z_]+)-error" class="text-xs text-danger">', page)

    def hidden(page, name):
        found = re.search(rf'name="{name}" value="([^"]*)"', page)
        return html.unescape(found.group(1)) if found else None

    def input_value(page, field_id):
        found = re.search(rf'<input[^>]*id="{field_id}"[^>]*>', page)
        value = re.search(r'value="([^"]*)"', found.group(0)) if found else None
        return html.unescape(value.group(1)) if value else None

    def textarea(page):
        found = re.search(r'<textarea[^>]*id="description"[^>]*>(.*?)</textarea>', page, re.S)
        return html.unescape(found.group(1)) if found else None

    def location(res):
        return unquote_plus(res.headers.get("Location") or "")

    JPEG = b"\xff\xd8\xff" + b"0" * 2000

    # =======================================================================
    print("1. A refused report comes back with what was typed")
    # =======================================================================
    before = count()
    res = post(description="The mouse \U0001F600 is broken here.")
    page = text(res)
    check("a refused report is shown the FORM again, not the code page",
          res.status_code == 400 and is_form(page) and 'name="qr_code"' not in page, res.status_code)
    check("nothing was saved", count() == before)
    check("the message is under the description",
          field_error(page, "description") == "Description cannot contain emojis.", shown_errors(page))
    check("and under no other field", shown_errors(page) == ["description"], shown_errors(page))
    check("the description is still there, as typed", textarea(page) == "The mouse \U0001F600 is broken here.")
    check("the name is still there", input_value(page, "reporter_name") == "Juan Dela Cruz")
    check("the email is still there", input_value(page, "reporter_email") == "juan@gmail.com")
    check("the student number, program and section are still there",
          (input_value(page, "reporter_student_number"), input_value(page, "reporter_section"))
          == ("2023000000", "4C") and '<option value="BSIT" title=' in page
          and re.search(r'<option value="BSIT" title="[^"]*" selected>BSIT</option>', page) is not None)
    check("Student is still chosen", '<option value="student" selected>' in page)
    check("the problem type is still chosen", '<option value="mouse" selected>' in page)
    check("the computer the reporter CHOSE is still chosen",
          '<option value="LAB-PC-02" selected>' in page and '<option value="LAB-PC-01" selected>' not in page)
    check("'(this computer)' stays on the machine the sticker named",
          re.search(r'<option value="LAB-PC-01">LAB-PC-01 \(online\) \(this computer\)</option>', page)
          is not None)
    check("the same signed sticker link is carried",
          hidden(page, "qr_token") == TOKEN and hidden(page, "qr_hostname") == "LAB-PC-01")
    check("the page still says not to keep it (no-store)",
          "no-store" in (res.headers.get("Cache-Control") or ""))

    # Send the page's own hidden fields back with the description fixed: it
    # must be accepted as a sticker report - the reporter never rescans.
    fixed = {name: hidden(page, name) for name in ("qr_token", "qr_hostname", "via_code")}
    # Back-dated rather than the page's fresh stamp, which is under the
    # three-second floor this soon after it was rendered.
    fixed["form_started"] = app.sign_form_timestamp(time.time() - 30)
    with app._report_rate_lock:
        app._report_rate_hits.clear()
    resend = client.post("/report", data=dict(GOOD, **fixed), environ_overrides={"REMOTE_ADDR": "203.0.113.5"})
    saved = q("SELECT report_source FROM problem_reports ORDER BY id DESC LIMIT 1")
    check("sent again from that page, it is accepted as a sticker report",
          "success=" in location(resend) and saved and saved[0]["report_source"] == "sticker",
          location(resend))

    page = text(post(proof="code", reporter_email="juan@gmail"))
    check("a reporter who typed the sticker code keeps that too",
          hidden(page, "via_code") == "1" and hidden(page, "qr_token") == TOKEN)
    check("the email message is under the email field",
          field_error(page, "reporter_email") == "Enter a valid email address, like name@gmail.com.")

    code = app.computer_short_code("LAB-PC-02")
    page = text(post(proof=None, qr_code=code, description="short"))
    check("a code posted with the report comes back as that machine's signed link",
          hidden(page, "qr_token") == app.sign_computer("LAB-PC-02") and hidden(page, "qr_hostname") == "LAB-PC-02"
          and hidden(page, "via_code") == "1")

    res = post(proof="lan", problem_type="printer")
    page = text(res)
    check("a lab PC is shown the form again too",
          res.status_code == 400 and is_form(page)
          and field_error(page, "problem_type") == "Please select a valid problem type.")

    res = post(proof=None, description="short")
    check("with NO proof of presence there is still no form - the code page, with the message",
          res.status_code == 302 and "error=" in location(res) and "/report" in location(res))
    check("...and that message is the one telling them how to get in",
          "QR sticker" in location(res), location(res))

    res = post(proof=None, demo="1", form_started=app.sign_form_timestamp())
    page = text(res)
    check("a demo respondent refused for speed stays in the demo",
          is_form(page) and 'name="demo" value="1"' in page and "Preview only" in page)
    check("...and is told why", "sent very quickly" in page)

    page = text(post(photos=[("a.jpg", JPEG)], reporter_name="J"))
    check("a reporter who attached a photo is asked to attach it again",
          field_error(page, "report_photos") == "Please attach your photo again.", shown_errors(page))
    check("...as well as being told what was wrong",
          field_error(page, "reporter_name") == "Name must be between 2 and 60 characters.")
    page = text(post(photos=[("a.jpg", JPEG), ("b.jpg", JPEG)], reporter_name="J"))
    check("two photos: 'photos'", field_error(page, "report_photos") == "Please attach your photos again.")
    page = text(post(reporter_name="J"))
    check("no photos attached: nothing said about photos", field_error(page, "report_photos") is None)
    page = text(post(photos=[("a.jpg", b"not an image at all")]))
    check("a bad photo: its own message under the photos",
          field_error(page, "report_photos") == "One of the files is not a valid image.")

    page = text(post(reporter_role="faculty", reporter_department=""))
    check("a faculty reporter keeps Faculty chosen, with the message under Department",
          '<option value="faculty" selected>' in page
          and field_error(page, "reporter_department") == "Department is required.")

    page = text(post(description='The mouse <b onclick="x">is</b> broken'))
    check("what was typed is escaped when it comes back",
          "<b onclick" not in page and "&lt;b onclick=" in page)

    now = time.time()
    with app._report_rate_lock:
        app._report_rate_hits["pc:lab-pc-02"] = [now] * app.REPORT_RATE_MAX_PER_COMPUTER
    data = dict(GOOD, qr_token=TOKEN, qr_hostname="LAB-PC-01",
                form_started=app.sign_form_timestamp(time.time() - 30))
    res = client.post("/report", data=data, environ_overrides={"REMOTE_ADDR": "203.0.113.5"})
    page = text(res)
    check("a rate-limited reporter keeps what they typed, and is told to wait",
          res.status_code == 429 and is_form(page) and "wait a few minutes" in page
          and textarea(page) == GOOD["description"])

    # =======================================================================
    print("2. Line breaks count once")
    # =======================================================================
    # 500 characters on the page: ten lines of 49 letters and nine line
    # breaks, plus one more letter. The browser sends each break as CR+LF.
    lines = [("Line %d " % i + "word " * 9)[:49] for i in range(10)]
    on_page = "\n".join(lines) + "x"
    check("(the test description is 500 characters as the page counts them)", len(on_page) == 500, len(on_page))
    sent = on_page.replace("\n", "\r\n")
    before = count()
    res = post(description=sent)
    check("a 500-character description with line breaks is accepted",
          count() == before + 1, field_error(text(res), "description"))
    stored = q("SELECT description FROM problem_reports ORDER BY id DESC LIMIT 1")[0]["description"]
    check("it is saved with one character per line break", stored == on_page and "\r" not in stored)
    res = post(description=(on_page + "y").replace("\n", "\r\n"))
    check("501 as the page counts them is refused",
          field_error(text(res), "description") == "Description must be 500 characters or fewer.")

    # =======================================================================
    print("3. The description rules")
    # =======================================================================
    TOO_MANY = "Description must be 500 characters or fewer."
    NOT_REAL = "That doesn't look like a real description. Please explain the problem in your own words."
    CHARS = "Description can only contain letters, numbers, and basic punctuation."
    REPEAT = "Description cannot repeat the same character 5 times in a row."
    EMOJI = "Description cannot contain emojis."
    cases = [
        # Real reports, each of which both sides must accept.
        ("IP 192.168.1.45 no internet", None),
        ("Error 0x80070643 code 1603", None),
        ("PC #12: no display at all", None),
        ("BSOD 0x0000007E at boot", None),
        ("Mouse wont click!!!!", None),
        ("Keyboard ok but rhythm keys stuck", None),
        ("Monitor flickers 50% of the time & beeps", None),
        ("Email me@school re: under_score * plus+", None),
        ("Ang keyboard ay sira, \u00f1 key", None),
        ("two  spaces  are  fine  now", None),
        ("line one\nline two\nline three", None),
        # Refused.
        ("", "Please describe the problem."),
        ("   \n  ", "Please describe the problem."),
        ("too short", "Description must be at least 10 characters."),
        ("a" * 501, TOO_MANY),
        ("Mouse wont click!!!!!", REPEAT),
        ("The screen goes aaaaaaargh", REPEAT),
        ("The screen \U0001F600 is broken", EMOJI),
        ("The \u2603 snowman on screen", EMOJI),
        ("<script>alert(1)</script>", CHARS),
        ("The price was 5\u00d73 pesos", CHARS),
        ("1 2 3 4 5 6 7 8 9", NOT_REAL),
        ("12345678901 abc", NOT_REAL),
        ("qwrtpsdfgh is broken", NOT_REAL),
        ("?!?!?!?!?!", NOT_REAL),
        ("..........", REPEAT),
    ]
    for value, expected in cases:
        got = app.description_error(app.clean_description(value))
        check(f"description {value[:32]!r}: {'accepted' if expected is None else 'refused'}",
              got == expected, got)
    check("trimmed before counting: 10 characters with spaces round them is enough",
          app.description_error(app.clean_description("  keyboards!  \n")) is None)

    # The page's own patterns, from the rendered page, against the server's.
    form_page = text(client.get(f"/report?computer=LAB-PC-01&t={TOKEN}"))

    def page_pattern(name):
        found = re.search(rf"var {name} = /(.*)/([a-z]*);\r?\n", form_page)
        if not found:
            return None
        source, flags = found.group(1), found.group(2)
        # The one place JS and Python regexes differ for these patterns:
        # Python's $ also matches before a final newline.
        source = re.sub(r"\$$", r"\\Z", source)
        return re.compile(source, re.IGNORECASE if "i" in flags else 0)

    corpus = [app.clean_description(value) for value, _ in cases] + [
        "\u00c0\u00d6\u00d7\u00d8\u00f6\u00f7\u00f8\u024f\u0250", "tab\there", "under_score", "a\u00a0b", "x#y@z",
        "yyyyyy", "rhythms", "bcdfgh", "BCDFGH", "00000", "aaaaa", "     ", "\u27bf\u2600\u25ff\u2800"]
    for js_name, server_pattern, how in (
            ("DESC_LETTER", app._DESCRIPTION_LETTER, "search"),
            ("DESC_ALLOWED", app._DESCRIPTION_ALLOWED, "fullmatch"),
            ("DESC_REPEAT", app._DESCRIPTION_REPEAT, "search"),
            ("DESC_CONSONANTS", app._DESCRIPTION_CONSONANTS, "search")):
        mine = page_pattern(js_name)
        differ = [v for v in corpus if mine is None
                  or bool(mine.search(v)) != bool(getattr(server_pattern, how)(v))]
        check(f"the page's {js_name} agrees with the server on every test input", not differ, differ[:3])
    mine = page_pattern("DESC_LETTER_OR_SPACE")
    differ = [v for v in corpus if mine is None
              or len(mine.findall(v)) != len(app._DESCRIPTION_LETTER_OR_SPACE.findall(v))]
    check("the page counts letters and spaces as the server does", not differ, differ[:3])
    mine = page_pattern("DESC_EMOJI")

    def as_page_sees_it(value):
        """JavaScript strings are UTF-16: a character outside the basic plane
        is two surrogate halves, which is what the page's pattern looks for."""
        out = ""
        for ch in value:
            if ord(ch) > 0xFFFF:
                offset = ord(ch) - 0x10000
                out += chr(0xD800 + (offset >> 10)) + chr(0xDC00 + (offset & 0x3FF))
            else:
                out += ch
        return out

    differ = [v for v in corpus if mine is None
              or bool(mine.search(as_page_sees_it(v))) != bool(app._DESCRIPTION_EMOJI.search(v))]
    check("the page and the server find the same emoji", not differ, differ[:3])

    # =======================================================================
    print("4. The email rule")
    # =======================================================================
    FORMAT = "Enter a valid email address, like name@gmail.com."
    ENDING = "Please use an email ending in .com or .ph."
    emails = [
        ("juan@gmail.com", None), ("juan@yahoo.com.ph", None), ("2021123456@bulsu.edu.ph", None),
        ("JUAN@GMAIL.COM", None), ("juan@school.PH", None), ("Juan.Dela-Cruz+lab@Outlook.com", None),
        ("o'brien@gmail.com", None), ("a@xn--80ak6aa92e.com", None), ("a" * 64 + "@gmail.com", None),
        # 120 characters exactly, with every part of it valid.
        ("a" * 56 + "@" + "b" * 59 + ".com", None),
        # A slip the rules cannot see: accepted here, and left to the page's
        # "Did you mean" suggestion.
        ("name@gmial.com", None),
        # Valid addresses, but not .com or .ph.
        ("juan@gmail.net", ENDING), ("juan@school.org", ENDING), ("juan@gmail.co", ENDING),
        ("juan@gmail.cw", ENDING), ("juan@gmail.c", ENDING), ("juan@yahoo.cm", ENDING),
        ("juan@bulsu.edu", ENDING), ("student@lab.example", ENDING), ("a@gmail.c0m", ENDING),
        ("a@1.2.3.4", ENDING), ("user@[127.0.0.1]", ENDING),
        # Not addresses at all.
        ("abc", FORMAT), ("abc@", FORMAT), ("@gmail.com", FORMAT), ("a@g", FORMAT),
        ("a@b@gmail.com", FORMAT), ("<x>@gmail.com", FORMAT), ("<x>@gmail.net", FORMAT),
        ("a..b@gmail.com", FORMAT), (".a@gmail.com", FORMAT), ("a.@gmail.com", FORMAT),
        ("a@-gmail.com", FORMAT), ("a@gmail-.com", FORMAT), ("a@gm_ail.com", FORMAT), ("a@.ph", FORMAT),
        ("a b@gmail.com", FORMAT), ("a\tb@gmail.com", FORMAT), ("x\r\nBcc:@evil.com", FORMAT),
        ("a@gmail.com.", FORMAT), ("a@gmail..com", FORMAT), ("\u00f1@gmail.com", FORMAT),
        ("a" * 65 + "@gmail.com", FORMAT),
        # 121 characters with every part valid: only the whole-address limit
        # refuses it.
        ("a" * 56 + "@" + "b" * 60 + ".com", FORMAT), ("a@" + "b" * 64 + ".com", FORMAT),
    ]
    for value, expected in emails:
        got = app.report_email_error(app.trim_like_browser(value))
        check(f"email {value[:30]!r}: {'accepted' if expected is None else 'refused'}", got == expected, got)
    check("blank: asked for", app.report_email_error("") == "Your email address is required.")
    check("trimmed the way the page trims (a no-break space at the end goes)",
          app.trim_like_browser("a@gmail.com\u00a0") == "a@gmail.com"
          and app.trim_like_browser("\ufeffa@gmail.com") == "a@gmail.com")

    local_js, domain_js = page_pattern("EMAIL_LOCAL"), page_pattern("EMAIL_DOMAIN")
    ending_js = page_pattern("EMAIL_ENDING")
    differ = []
    for value, _ in emails:
        local, _, domain = value.partition("@")
        ending = domain.rpartition(".")[2]
        if local_js is None or bool(local_js.search(local)) != bool(app._EMAIL_LOCAL.fullmatch(local)):
            differ.append(local)
        if domain_js is None or bool(domain_js.search(domain)) != bool(app._EMAIL_DOMAIN.fullmatch(domain)):
            differ.append(domain)
        if ending_js is None or bool(ending_js.search(ending)) != bool(app._EMAIL_ENDING.fullmatch(ending)):
            differ.append(ending)
    for ending in ("com", "COM", "Com", "ph", "PH", "pH", "co", "cm", "c", "cw", "net", "org", "comm", "phh", "xcom"):
        if ending_js is None or bool(ending_js.search(ending)) != bool(app._EMAIL_ENDING.fullmatch(ending)):
            differ.append(ending)
    check("the page's email patterns agree with the server's on every test address", not differ, differ[:3])
    check("only .com and .ph endings pass the server, in any case",
          [e for e in ("com", "COM", "ph", "Ph", "co", "cm", "net", "comm", "phh")
           if app._EMAIL_ENDING.fullmatch(e)] == ["com", "COM", "ph", "Ph"])
    check("the page's refusal message for an ending is the server's",
          "'" + ENDING + "'" in form_page)

    # =======================================================================
    print("5. Notes and What was done")
    # =======================================================================
    admin = app.app.test_client()
    with admin.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_role"] = "admin"
    db = database.get_db()
    report_id = db.execute(
        "INSERT INTO problem_reports (computer_hostname, problem_type, description, status) "
        "VALUES ('LAB-PC-01', 'mouse', 'Notes test report here.', 'open')").lastrowid
    db.commit()
    db.close()

    def save_notes(notes, resolution=""):
        return admin.post(f"/problem-reports/{report_id}/notes",
                          data={"notes": notes, "resolution_notes": resolution})

    def stored_notes():
        row = q("SELECT notes, resolution_notes FROM problem_reports WHERE id = ?", (report_id,))[0]
        return row["notes"], row["resolution_notes"]

    res = save_notes("n" * 1000, "r" * 1000)
    check("1000 characters each are saved", stored_notes() == ("n" * 1000, "r" * 1000))
    res = save_notes("x" * 1001)
    check("1001 characters of notes are refused, with the toast",
          "Notes must be 1000 characters or fewer." in location(res) and stored_notes()[0] == "n" * 1000,
          location(res))
    res = save_notes("ok", "y" * 1001)
    check("1001 characters of What was done are refused, and nothing is saved",
          "What was done must be 1000 characters or fewer." in location(res)
          and stored_notes() == ("n" * 1000, "r" * 1000), location(res))
    with_breaks = "\n".join(["a" * 99] * 10) + "b"
    check("(1000 characters as the textarea counts them)", len(with_breaks) == 1000)
    save_notes(with_breaks.replace("\n", "\r\n"), with_breaks.replace("\n", "\r\n"))
    check("line breaks count once, and are saved that way",
          stored_notes() == (with_breaks, with_breaks))
    admin_page = admin.get("/problem-reports").get_data(as_text=True)
    for field_id in ("details-notes", "details-resolution"):
        tag = re.search(rf'<textarea id="{field_id}"[^>]*>', admin_page)
        check(f"the {field_id} box stops at 1000 characters",
              tag is not None and 'maxlength="1000"' in tag.group(0))

    # =======================================================================
    print("6. An oversized request is refused before the form is read")
    # =======================================================================
    before = count()
    big = b"\xff\xd8\xff" + b"0" * (app.REPORT_MAX_REQUEST_BYTES + 1024)
    # A filled honeypot would get the quiet success page IF the form were
    # read. Getting the size message instead proves it never was.
    res = post(proof="lan", photos=[("big.jpg", big)], website="spam")
    check("refused with the size message",
          res.status_code == 302 and "The photos are too large. Attach up to 3, 5 MB each." in location(res),
          location(res))
    check("...before the form was read (the honeypot never saw it)", "success=" not in location(res))
    check("...and nothing was saved", count() == before)
    photo = b"\xff\xd8\xff" + b"0" * (5 * 1024 * 1024 - 100)
    res = post(proof="lan", photos=[("a.jpg", photo), ("b.jpg", photo), ("c.jpg", photo)])
    check("three photos at the 5 MB limit still go through",
          count() == before + 1 and "success=" in location(res), location(res))

    # =======================================================================
    print("7. The code page keeps a mistyped code")
    # =======================================================================
    with app._report_rate_lock:
        app._report_rate_hits.clear()
    gate = client.post("/report/access", data={"qr_code": "4K7P2X"}).get_data(as_text=True)
    check("a wrong code is shown back in the box", input_value(gate, "qr_code") == "4K7P2X")
    gate = client.post("/report/access", data={"qr_code": '"><b>4K7P2MZZZZZZ'}).get_data(as_text=True)
    check("escaped, and cut to the box's 10 characters",
          input_value(gate, "qr_code") == '"><b>4K7P2' and "<b>4K7P2" not in gate)
    gate = client.get("/report").get_data(as_text=True)
    check("a fresh visit starts with an empty box", input_value(gate, "qr_code") == "")

    # =======================================================================
    print("8. The agent API caps a description")
    # =======================================================================
    def agent(description):
        return client.post("/api/agent/problem-reports", json={
            "computer_hostname": "LAB-PC-01", "problem_type": "software", "description": description})

    res = agent("  " + "d" * 500 + "  ")
    check("500 characters (after trimming) are accepted", res.status_code == 201, res.status_code)
    stored = q("SELECT description FROM problem_reports ORDER BY id DESC LIMIT 1")[0]["description"]
    check("...and saved trimmed", stored == "d" * 500)
    before = count()
    res = agent("d" * 501)
    check("501 are refused with a JSON 400",
          res.status_code == 400 and "500 characters or fewer" in (res.get_json() or {}).get("error", ""),
          res.status_code)
    check("...and nothing is saved", count() == before)

    # =======================================================================
    print("9. Reporter fields")
    # =======================================================================
    STUDENT = {"reporter_role": "student", "reporter_name": "Juan Dela Cruz",
               "reporter_student_number": "2023000000", "reporter_program": "BSIT", "reporter_section": "4C"}
    FACULTY = {"reporter_role": "faculty", "reporter_name": "Maria Santos", "reporter_department": "CICT"}

    def reporter(base, **change):
        values, field, message = app.reporter_fields_error(dict(base, **change))
        return values, field, message

    NUMBER = "Enter your 10-digit student number, like 2023000000."
    for typed, saved in (("2023000000", "2023000000"), ("2023-000000", "2023000000"),
                         ("2023 000000", "2023000000"), (" 2023 - 000 - 000 ", "2023000000")):
        values, field, message = reporter(STUDENT, reporter_student_number=typed)
        check(f"student number {typed!r}: accepted, saved as {saved}",
              message is None and values["student_number"] == saved, message)
    for typed in ("123", "20230000009", "2023ab0000", "202300000", "2023\u066000000"):
        _, field, message = reporter(STUDENT, reporter_student_number=typed)
        check(f"student number {typed!r}: refused",
              field == "reporter_student_number" and message == NUMBER, message)
    _, field, message = reporter(STUDENT, reporter_student_number="  ")
    check("a blank student number is asked for", message == "Student number is required.", message)

    for field_name, base, cases in (
            ("reporter_name", STUDENT, [("Juan Dela Cruz", None), ("Ab", None), ("Jos\u00e9 Ri\u00f1a", None),
                                        ("--", "Enter your name using letters."),
                                        (". .", "Enter your name using letters."),
                                        ("A.", "Enter your name using letters."),
                                        ("J", "Name must be between 2 and 60 characters.")]),
            ("reporter_department", FACULTY, [("CICT", None), ("A&B", None),
                                              ("C", "Enter your department name."),
                                              ("--", "Enter your department name."),
                                              ("12", "Enter your department name.")])):
        for typed, expected in cases:
            _, field, message = reporter(base, **{field_name: typed})
            check(f"{field_name} {typed!r}: {'accepted' if expected is None else 'refused'}",
                  message == expected and (expected is None or field == field_name), message)

    # Through the form: saved as the ten digits; refused under the field.
    before = count()
    post(reporter_student_number="2023-000000", description="Student number with a hyphen typed.")
    saved = q("SELECT reporter_student_number FROM problem_reports ORDER BY id DESC LIMIT 1")
    check("2023-000000 sent through the form is saved as 2023000000",
          count() == before + 1 and saved[0]["reporter_student_number"] == "2023000000")
    post(reporter_student_number="2023 000000", description="Student number with a space typed.")
    saved = q("SELECT reporter_student_number FROM problem_reports ORDER BY id DESC LIMIT 1")
    check("2023 000000 is saved as 2023000000", saved[0]["reporter_student_number"] == "2023000000")
    page = text(post(reporter_student_number="2023ab0000"))
    check("2023ab0000 is refused under the student number, with what was typed kept",
          field_error(page, "reporter_student_number") == NUMBER
          and input_value(page, "reporter_student_number") == "2023ab0000")
    page = text(post(reporter_email="juan@gmail.net"))
    check("a .net address is refused under the email field",
          field_error(page, "reporter_email") == ENDING)

    # =======================================================================
    print("10. Did you mean...? (what Python can check)")
    # =======================================================================
    # The suggestion itself is the page's JavaScript; it needs the click-test.
    # What is checked here: the controls exist and start hidden, the list is
    # the approved one, and every domain it can suggest is one the form takes.
    suggest = re.search(r'<p id="reporter_email-suggest" class="([^"]*)"', form_page)
    check("the suggestion line is on the page, hidden until needed",
          suggest is not None and "hidden" in suggest.group(1).split())
    check("it offers Use this and Keep mine",
          'id="reporter_email-use"' in form_page and ">Use this</button>" in form_page
          and 'id="reporter_email-keep"' in form_page and ">Keep mine</button>" in form_page)
    found = re.search(r"var EMAIL_DOMAINS = \[(.*?)\];", form_page, re.S)
    listed = re.findall(r"'([^']+)'", found.group(1)) if found else []
    for domain in ("gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com", "bulsu.edu.ph"):
        check(f"{domain} is in the suggestion list", domain in listed, listed)
    refused = [d for d in listed if app.report_email_error("name@" + d) is not None]
    check("every domain it can suggest is one the form accepts", listed and not refused, refused)
    check("the Send dialog has a place for the email address",
          re.search(r'Confirmation email to: <strong id="report-confirm-email"', form_page) is not None)

    # =======================================================================
    print("11. Program, year and section, department")
    # =======================================================================
    # Written out here from the approved list, not read from app.py, so a
    # change to the list on either side fails this.
    PROGRAMS = [
        ("BIT-CT", "Bachelor in Industrial Technology Major in Computer"),
        ("BIT-ELX", "Bachelor in Industrial Technology Major in Electronics"),
        ("BIT-FSM", "Bachelor in Industrial Technology Major in Food and Service Management"),
        ("BEEd", "Bachelor of Elementary Education"),
        ("BPEd", "Bachelor of Physical Education"),
        ("BSBA-FM", "Bachelor of Science in Business Administration Major in Financial Management"),
        ("BSCpE", "Bachelor of Science in Computer Engineering"),
        ("BSHM", "Bachelor of Science in Hospitality Management"),
        ("BSIT", "Bachelor of Science in Information Technology"),
        ("BSEd-English", "Bachelor of Secondary Education Major in English"),
        ("BSEd-Filipino", "Bachelor of Secondary Education Major in Filipino"),
        ("BSEd-Math", "Bachelor of Secondary Education Major in Mathematics"),
        ("BSEd-Science", "Bachelor of Secondary Education Major in Science"),
        ("BTVTEd-FSM", "Bachelor of Technical-Vocational Teacher Education Major in Food and Service Management"),
    ]
    start = form_page.index('<select class="input" id="reporter_program"')
    dropdown = form_page[start:form_page.index("</select>", start)]
    options = re.findall(r'<option value="([^"]*)"(?: title="([^"]*)")?>([^<]*)</option>', dropdown)
    check("Program is a dropdown, not a text box",
          bool(options) and not re.search(r'<input[^>]*name="reporter_program"', form_page))
    check("it starts with the 'Select your program' placeholder, which has no value",
          options[:1] == [("", "", "Select your program")], options[:1])
    check("the listed programs, in order, showing the code with the full name as the tooltip",
          [(code, html.unescape(title)) for code, title, text in options[1:-1] if text == code] == PROGRAMS,
          options[1:3])
    check("and Other... last", options[-1:] == [(app.REPORT_PROGRAM_OTHER, "", "Other...")], options[-1:])
    check("the server accepts the same list (one list, not two copies)", list(app.REPORT_PROGRAMS) == PROGRAMS)
    other_box = re.search(r'<div id="program-other-field" class="([^"]*)">', form_page)
    check("the Other box starts hidden", other_box is not None and "hidden" in other_box.group(1).split())
    other_input = re.search(r'<input[^>]*id="reporter_program_other"[^>]*>', form_page)
    check("the Other box takes up to 60 characters",
          other_input is not None and 'maxlength="60"' in other_input.group(0))
    section_input = re.search(r'<input[^>]*id="reporter_section"[^>]*>', form_page)
    check("year and section takes up to 5 characters - room for '4 - C'",
          section_input is not None and 'maxlength="5"' in section_input.group(0))

    def program(choice, typed=""):
        return reporter(STUDENT, reporter_program=choice, reporter_program_other=typed)

    for code, _ in PROGRAMS:
        values, field, message = program(code)
        check(f"program {code}: accepted and saved as the code",
              message is None and values["program"] == code, message)
    values, field, message = program("BSIT", "Leftover typed text")
    check("a listed program ignores anything in the Other box", message is None and values["program"] == "BSIT")
    OTHER = app.REPORT_PROGRAM_OTHER
    TYPE_NAME = "Type your program's name, 2 to 60 characters."
    TYPE_CHARS = "Program can only contain letters, numbers, spaces and . & - characters."
    for typed, saved in (("BS Agricultural Engineering", "BS Agricultural Engineering"),
                         ("Pe\u00f1a Studies", "Pe\u00f1a Studies"), ("B.S.I.T.", "B.S.I.T."),
                         ("BS Ed & Arts", "BS Ed & Arts"), ("AB", "AB"), ("  BS   Nursing  ", "BS Nursing"),
                         ("x" * 60, "x" * 60)):
        values, field, message = program(OTHER, typed)
        check(f"Other... {typed[:28]!r}: accepted, saved as typed", message is None and values["program"] == saved,
              message)
    for typed, expected in (("", "Type your program."), ("   ", "Type your program."), ("A", TYPE_NAME),
                            ("12", TYPE_NAME), ("x" * 61, TYPE_NAME), ("BS<b>", TYPE_CHARS), ("BS_Ed", TYPE_CHARS)):
        _, field, message = program(OTHER, typed)
        check(f"Other... {typed[:28]!r}: refused under the Other box",
              field == "reporter_program_other" and message == expected, message)
    for choice, expected in (("", "Please select your program."), ("BSXX", "Please select your program from the list."),
                             ("Other...", "Please select your program from the list."),
                             ("bsit", "Please select your program from the list.")):
        _, field, message = program(choice)
        check(f"program {choice!r}: refused", field == "reporter_program" and message == expected, message)

    for typed in ("4C", "4c", "4 c", "4-C", " 4 - c ", "4--C"):
        values, field, message = reporter(STUDENT, reporter_section=typed)
        check(f"section {typed!r}: accepted, saved as 4C", message is None and values["section"] == "4C", message)
    for typed, saved in (("1a", "1A"), ("5Z", "5Z")):
        values, field, message = reporter(STUDENT, reporter_section=typed)
        check(f"section {typed!r}: saved as {saved}", message is None and values["section"] == saved, message)
    for typed in ("C4", "44", "4CC", "0C", "6C", "4", "4\u00f1", "IV-C", "BSIT 3A"):
        _, field, message = reporter(STUDENT, reporter_section=typed)
        check(f"section {typed!r}: refused", field == "reporter_section"
              and message == "Enter your year and section, like 4C.", message)
    _, field, message = reporter(STUDENT, reporter_section="")
    check("a blank section is asked for", message == "Year and section is required.", message)

    for typed in ("Pe\u00f1a", "Ni\u00f1o Hall", "Jos\u00e9 Rizal Hall", "College of Engineering & Technology"):
        values, field, message = reporter(FACULTY, reporter_department=typed)
        check(f"department {typed!r}: accepted", message is None and values["department"] == typed, message)
    _, field, message = reporter(FACULTY, reporter_department="Dept <x>")
    check("a department with < > is refused, with a message that does not call n-tilde a non-letter",
          message == "Department can only contain letters, numbers, spaces and . & - characters.", message)
    _, field, message = reporter(FACULTY, reporter_department="x" * 61)
    check("61 characters: the length message, on the server as on the page",
          message == "Department must be 60 characters or fewer.", message)

    # The multiplication and division signs sit inside the accented-letter
    # block but are not letters: refused in a name, a department and a typed
    # program, while real accented letters stay accepted.
    _, field, message = reporter(STUDENT, reporter_name="Juan \u00d7 Cruz")
    check("'Juan x Cruz' (multiplication sign) is refused as a name",
          field == "reporter_name" and message == "Name can only contain letters, spaces, and . ' - characters.",
          message)
    _, field, message = reporter(FACULTY, reporter_department="Dept \u00f7 IT")
    check("'Dept / IT' (division sign) is refused as a department",
          field == "reporter_department"
          and message == "Department can only contain letters, numbers, spaces and . & - characters.", message)
    _, field, message = program(OTHER, "BS \u00d7 IT")
    check("'BS x IT' (multiplication sign) is refused as a typed program",
          field == "reporter_program_other" and message == TYPE_CHARS, message)
    for typed in ("Pe\u00f1a", "Ni\u00f1o Hall", "Jos\u00e9 Rizal",
                  "\u00d1o\u00f1o M\u00fcller"):
        values, field, message = reporter(STUDENT, reporter_name=typed)
        check(f"name {typed!r}: still accepted", message is None, message)
        values, field, message = reporter(FACULTY, reporter_department=typed)
        check(f"department {typed!r}: still accepted", message is None, message)
        values, field, message = program(OTHER, typed)
        check(f"typed program {typed!r}: still accepted", message is None, message)

    # The page's own patterns against the server's.
    text_js, section_js = page_pattern("REPORTER_TEXT_ALLOWED"), page_pattern("SECTION_PATTERN")
    name_js = page_pattern("NAME_ALLOWED")
    corpus = ["Pe\u00f1a", "Ni\u00f1o Hall", "B.S.I.T.", "BS Ed & Arts", "BS<b>", "BS_Ed", "\u00c0\u024f\u0250",
              "a\u00a0b", "x/y", "4C", "4c", "44", "C4", "4CC", "0C", "6C", "1A", "5z", "4\u00f1", "4", ""]
    differ = [v for v in corpus if text_js is None
              or bool(text_js.search(v)) != bool(app._REPORTER_TEXT_ALLOWED.fullmatch(v))]
    check("the page's program/department letters agree with the server's", not differ, differ[:3])
    signs = ["Juan \u00d7 Cruz", "Dept \u00f7 IT", "\u00d7", "\u00f7",
             "\u00d6\u00d8\u00f6\u00f8", "Pe\u00f1a",
             "O'Brien-Cruz Jr.", "Ma. Cristina"]
    differ = [v for v in corpus + signs if text_js is None
              or bool(text_js.search(v)) != bool(app._REPORTER_TEXT_ALLOWED.fullmatch(v))]
    check("...including around the multiplication and division signs", not differ, differ[:3])
    differ = [v for v in corpus + signs if name_js is None
              or bool(name_js.search(v)) != bool(app._NAME_ALLOWED.fullmatch(v))]
    check("the page's name letters agree with the server's", not differ, differ[:3])
    check("neither side takes the multiplication or division sign in a name, department or program",
          not any(p.search(s) for p in (app._NAME_ALLOWED, app._REPORTER_TEXT_ALLOWED) + tuple(
              x for x in (text_js, name_js) if x is not None) for s in ("\u00d7", "\u00f7")))
    differ = [v for v in corpus if section_js is None
              or bool(section_js.search(v)) != bool(app._SECTION.fullmatch(v))]
    check("the page's year-and-section pattern agrees with the server's", not differ, differ[:3])

    # Through the form: a refusal brings the dropdown and the Other box back.
    page = text(post(reporter_program=OTHER, reporter_program_other="BS Agricultural Engineering",
                     reporter_section="4-c", description="short"))
    check("refused: Other... is still chosen",
          f'<option value="{OTHER}" selected>Other...</option>' in page)
    check("...the typed program is still in the Other box",
          input_value(page, "reporter_program_other") == "BS Agricultural Engineering")
    check("...and the section is as it was typed", input_value(page, "reporter_section") == "4-c")
    page = text(post(reporter_program="BSEd-Math", description="short"))
    check("refused: a listed program is still chosen",
          re.search(r'<option value="BSEd-Math" title="[^"]*" selected>BSEd-Math</option>', page) is not None)
    page = text(post(reporter_program=OTHER, reporter_program_other="A"))
    check("a bad typed program is refused under the Other box",
          field_error(page, "reporter_program_other") == TYPE_NAME)
    before = count()
    post(reporter_program=OTHER, reporter_program_other="BS Agricultural Engineering", reporter_section="4-c",
         description="Saved with a typed program here.")
    saved = q("SELECT reporter_program, reporter_section FROM problem_reports ORDER BY id DESC LIMIT 1")
    check("sent through the form: the typed program and 4C are saved",
          count() == before + 1 and (saved[0]["reporter_program"], saved[0]["reporter_section"])
          == ("BS Agricultural Engineering", "4C"))

    # Older reports keep their values, on screen and on paper.
    db = database.get_db()
    old_id = db.execute(
        "INSERT INTO problem_reports (computer_hostname, problem_type, description, status, reporter_role, "
        "reporter_name, reporter_student_number, reporter_program, reporter_section) "
        "VALUES ('LAB-PC-01', 'mouse', 'Filed before the dropdown.', 'open', 'student', 'Old Student', "
        "'2021-00001', 'B.S.I.T.', 'BSIT 3A')").lastrowid
    db.commit()
    db.close()
    listing = admin.get("/problem-reports").get_data(as_text=True)
    check("an old report still shows its program and section as saved",
          'data-reporter-program="B.S.I.T."' in listing and 'data-reporter-section="BSIT 3A"' in listing)
    sheet = admin.get(f"/problem-reports/{old_id}/print").get_data(as_text=True)
    check("...and prints them as saved", "B.S.I.T." in sheet and "BSIT 3A" in sheet)
    check("...and nothing rewrote them",
          q("SELECT reporter_program, reporter_section FROM problem_reports WHERE id = ?", (old_id,))[0]
          ["reporter_program"] == "B.S.I.T.")

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

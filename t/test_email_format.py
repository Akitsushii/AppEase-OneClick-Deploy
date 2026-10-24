"""Every email AppEase sends: one shared, mail-client-safe design, and
nothing that reads as spam. No link to a raw IP address except the
forgot-password link, kept on purpose. How a client actually draws the
HTML needs the preview files and a real test email.
"""
import html
import ipaddress
import logging
import os
import re
import secrets
import shutil
import sys
import tempfile
from email import message_from_bytes, policy
from email.utils import parseaddr, parsedate_to_datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
passed, failed = [], []


def check(label, condition):
    if condition:
        passed.append(label)
        print(f"  ok  {label}")
    else:
        failed.append(label)
        print(f"  FAIL: {label}")


work = tempfile.mkdtemp(prefix="appease_email_format_")
server_dir = os.path.join(work, "server")
shutil.copytree(os.path.join(ROOT, "server"), server_dir, ignore=shutil.ignore_patterns(
    "appease.db*", "__pycache__", "*.pyc", ".env", "uploads", "staging", "report_photos", "avatars", "venv"))
sys.path.insert(0, server_dir)
os.chdir(server_dir)
for name in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"):
    os.environ.pop(name, None)
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))

import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()
import app as appmod  # noqa: E402

if not hasattr(appmod, "render_email"):
    print("COULD NOT RUN - app.py predates the shared email template.")
    sys.exit(3)

messages = []


class FakeSMTP:
    fail = False

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        pass

    def login(self, *args):
        pass

    def send_message(self, message):
        if FakeSMTP.fail:
            raise OSError("refused")
        messages.append(message_from_bytes(message.as_bytes(), policy=policy.default))


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


log = Capture()
appmod.app.logger.addHandler(log)
appmod.smtplib.SMTP = FakeSMTP
appmod.SMTP_HOST = "smtp.example.test"
appmod.SMTP_USER = "lab.sender@example.test"
# A different From address configured: it must not be used.
appmod.SMTP_FROM = "someone.else@other.test"
# The Message-ID must never wait on a DNS lookup of this machine's name.
appmod.socket.getfqdn = lambda *a: (_ for _ in ()).throw(AssertionError("getfqdn called"))

PASSWORD = "Tq7#Lp2!xR9wKe"
HOSTILE = "<b>mica</b>&\"x\""
RESET_LINK = "http://192.168.1.2:8080/reset-password/abcDEF123_token"
finished = {s: appmod.report_finished_email(7, "LAB1-PC07", "mouse", s, "08 Oct 2026, 9:10 AM")
            for s in ("resolved", "no_fault_found")}
EMAILS = {
    "account created": ("Your new AppEase account",
                        appmod.new_account_email_body(HOSTILE, PASSWORD, "Faculty", "admin")),
    "admin password reset": ("Your AppEase password was reset",
                             appmod.temporary_password_email_body(HOSTILE, PASSWORD, "admin")),
    "forgot-password link": ("Reset your AppEase password", appmod.password_reset_email_body(HOSTILE, RESET_LINK)),
    "email changed": ("Your AppEase email address was changed",
                      appmod.email_changed_email_body(HOSTILE, "new.address@example.test")),
    "username changed": ("Your AppEase username was changed",
                         appmod.username_changed_email_body("old.name", HOSTILE)),
    "test email": ("AppEase test email", appmod.test_email_body(HOSTILE)),
    "report receipt": ("AppEase received your report #7",
                       appmod.report_receipt_body(7, "LAB1-PC07", "mouse", "07 Oct 2026, 2:35 PM")),
    "resolved": finished["resolved"],
    "no fault found": finished["no_fault_found"],
}
LINK_EXEMPT = {"forgot-password link": "kept on purpose - a known, accepted gap"}

# ---------------------------------------------------------------------------
print("1. Both parts, the headers, and who it is from")
# ---------------------------------------------------------------------------
sent = {}
for name, (subject, body) in EMAILS.items():
    before = len(messages)
    ok, error = appmod.send_email("reader@example.test", subject, body)
    sent[name] = messages[-1] if ok and len(messages) == before + 1 else None
check("all nine were sent through the real send_email()", all(sent.values()))
ids = set()
LOGO_FILE = os.path.join(server_dir, "static", "img", "appease-email-logo.png")
LOGO = open(LOGO_FILE, "rb").read() if os.path.isfile(LOGO_FILE) else b""
for name, message in sent.items():
    if message is None:
        continue
    parts = [part.get_content_type() for part in message.iter_parts()] if message.is_multipart() else []
    check(f"{name}: multipart/alternative - plain text, then the HTML with its logo",
          message.get_content_type() == "multipart/alternative" and parts == ["text/plain", "multipart/related"])
    related = list(message.iter_parts())[1] if len(parts) == 2 else None
    inner = [part.get_content_type() for part in related.iter_parts()] if related is not None else []
    image = list(related.iter_parts())[1] if inner == ["text/html", "image/png"] else None
    check(f"{name}: the related part is the HTML and one PNG", image is not None)
    check(f"{name}: the PNG is the committed logo, inline, under its Content-ID",
          image is not None and image.get_content() == LOGO and image["Content-ID"] == f"<{appmod.EMAIL_LOGO_CID}>"
          and image.get_content_disposition() == "inline")
    markup = message.get_body(("html",)).get_content()
    check(f"{name}: the HTML shows it by that cid, sized, with alt text",
          re.search(rf'<img src="cid:{re.escape(appmod.EMAIL_LOGO_CID)}" width="96" height="32" alt="AppEase"',
                    markup) is not None)
    check(f"{name}: every text part UTF-8",
          all(part.get_content_charset() == "utf-8" for part in message.walk()
              if part.get_content_maintype() == "text"))
    try:
        dated = parsedate_to_datetime(message["Date"]) is not None
    except (TypeError, ValueError):
        dated = False
    check(f"{name}: Date, Message-ID and MIME-Version",
          dated and re.fullmatch(r"<[^<>@\s]+@[^<>@\s]+>", message["Message-ID"] or "") is not None
          and message["MIME-Version"] == "1.0")
    ids.add(message["Message-ID"])
    display, address = parseaddr(message["From"])
    check(f"{name}: From the signed-in account, with a name ({message['From']})",
          address == "lab.sender@example.test" and display == "AppEase - BulSU Lab")
check("every Message-ID is different", len(ids) == len(EMAILS))

# A body that is not one of the built emails still goes out with both parts.
appmod.send_email("reader@example.test", "AppEase notice", "Plain words only.")
loose = messages[-1]
check("even a plain-text body goes out as text + HTML",
      loose.get_content_type() == "multipart/alternative"
      and "Plain words only." in loose.get_body(("html",)).get_content())


def visible(markup):
    """The words a reader sees in the HTML part."""
    markup = re.sub(r"<(style|title)\b.*?</\1>", " ", markup, flags=re.S)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", markup)).split())


# ---------------------------------------------------------------------------
print("2. The two parts say the same thing, in the shared design")
# ---------------------------------------------------------------------------
for name, message in sent.items():
    if message is None:
        continue
    text = message.get_body(("plain",)).get_content()
    markup = message.get_body(("html",)).get_content()
    seen = visible(markup)
    missing = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line == "--":
            continue
        if ": " in line:
            # A details row: label and value are separate cells in the HTML.
            label, value = line.split(": ", 1)
            if label not in seen or value not in seen:
                missing.append(line)
        elif line.endswith(":"):
            continue    # the plain-text lead-in to a link; the HTML has a button
        elif " ".join(line.split()) not in seen:
            missing.append(line)
    check(f"{name}: every line of the text part is in the HTML part" + (f" - missing {missing}" if missing else ""),
          not missing)
    check(f"{name}: the same footer in both",
          appmod.EMAIL_FOOTER in text and appmod.EMAIL_FOOTER in seen)
    check(f"{name}: the header is the lockup - logo, then AppEase over the campus",
          re.search(r'alt="AppEase"[^>]*></td><td[^>]*><p[^>]*>AppEase</p><p[^>]*>BulSU Meneses Campus</p>',
                    markup) is not None)
    check(f"{name}: a brand-blue accent along the top of the card",
          re.search(r'class="ae-card" style="[^"]*border-top:3px solid #4173d8;', markup) is not None)
    lowered = markup.lower()
    sources = re.findall(r'src="([^"]*)"', markup)
    check(f"{name}: nothing fetched - its one image is inside the message, never a URL or data: URI",
          sources == [f"cid:{appmod.EMAIL_LOGO_CID}"]
          and not any(bad in lowered for bad in ("<link", "<script", "url(", "@import", "@font-face", "data:")))
    check(f"{name}: laid out with tables, at most 600px wide",
          'role="presentation"' in markup and "max-width:600px" in markup and "<div" not in lowered)

PILLS = {"report receipt": ("Received", "#dbeafe", "#1d4ed8"),
         "resolved": ("Resolved", "#dcfce7", "#166534"),
         "no fault found": ("No fault found", "#f3f4f6", "#6b7280")}
for name, message in sent.items():
    markup = message.get_body(("html",)).get_content() if message else ""
    pills = re.findall(r'<td class="ae-pill-\w+" style="[^"]*background-color:(#\w+);[^"]*color:(#\w+);">([^<]*)</td>',
                       markup)
    if name in PILLS:
        label, background, colour = PILLS[name]
        check(f"{name}: a '{label}' pill in the app's colours", pills == [(background, colour, label)])
    else:
        check(f"{name}: no status pill - it is not a report email", pills == [])

for name in ("account created", "admin password reset"):
    markup = sent[name].get_body(("html",)).get_content() if sent[name] else ""
    box = re.search(r'<td class="ae-code" style="([^"]*)">([^<]*)</td>', markup)
    check(f"{name}: the temporary password sits in its own monospace box",
          box is not None and "monospace" in box.group(1) and html.unescape(box.group(2)) == PASSWORD)

# ---------------------------------------------------------------------------
print("3. Links")
# ---------------------------------------------------------------------------
URL = re.compile(r"https?://[^\s\"'<>]+")


def host_is_ip(url):
    host = re.sub(r"^https?://", "", url).split("/")[0].rsplit(":", 1)[0].strip("[]")
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


with_links = []
for name, message in sent.items():
    if message is None:
        continue
    urls = URL.findall(message.get_body(("plain",)).get_content() + message.get_body(("html",)).get_content())
    if urls:
        with_links.append(name)
    if name in LINK_EXEMPT:
        check(f"{name}: links to the raw IP it was asked from - exempt, {LINK_EXEMPT[name]}",
              urls and all(u == RESET_LINK or html.unescape(u) == RESET_LINK for u in urls))
        continue
    check(f"{name}: no link to a raw IP address", not any(host_is_ip(u) for u in urls))
check(f"only the forgot-password email carries a link ({with_links})", with_links == ["forgot-password link"])

# ---------------------------------------------------------------------------
print("4. Typed text is escaped, never markup")
# ---------------------------------------------------------------------------
for name in ("account created", "test email", "username changed"):
    markup = sent[name].get_body(("html",)).get_content() if sent[name] else ""
    check(f"{name}: a username with markup in it is shown, not run",
          "<b>mica</b>" not in markup and "&lt;b&gt;mica&lt;/b&gt;" in markup)

# ---------------------------------------------------------------------------
print("5. Subjects")
# ---------------------------------------------------------------------------
ALARM = re.compile(r"\b(urgent|immediately|action required|warning|alert|important|final notice|verify now)\b", re.I)
# The subjects app.py really uses: the literal each send call is given, and
# the two report_finished_email() returns - not ones written into this test.
app_source = open(os.path.join(server_dir, "app.py"), encoding="utf-8").read()
subjects = re.findall(r"(?:send_email|send_email_later|send_email_within|_send_capped)\(\s*[^,()]+(?:\[[^\]]*\])?,\s*\n?\s*"
                      r"f?\"([^\"]+)\"", app_source)
subjects += [subject for subject, _ in finished.values()]
check(f"every subject was found ({len(subjects)})", len(subjects) >= 9)
for subject in subjects:
    shouting = [w for w in re.findall(r"[A-Za-z]{3,}", subject) if w.isupper()]
    check(f"'{subject}' - specific, calm, no capitals shouted",
          "AppEase" in subject and not shouting and "!" not in subject and not ALARM.search(subject))

# ---------------------------------------------------------------------------
print("6. Logs and the From warning")
# ---------------------------------------------------------------------------
log.lines.clear()
FakeSMTP.fail = True
ok, error = appmod.send_email("reader@example.test", *EMAILS["account created"])
FakeSMTP.fail = False
check("a failed send logs only the domain - no password, no address, no subject",
      not ok and log.lines and all(PASSWORD not in line and "reader@" not in line
                                   and "Your new AppEase" not in line for line in log.lines))

log.lines.clear()
appmod.warn_if_sender_mismatch()
check("a different SMTP_FROM is warned about once at startup, by setting name",
      len(log.lines) == 1 and "SMTP_FROM" in log.lines[0] and "SMTP_USER" in log.lines[0])
check("...and the warning holds neither address",
      log.lines and "lab.sender" not in log.lines[0] and "someone.else" not in log.lines[0]
      and "@" not in log.lines[0])
log.lines.clear()
appmod.SMTP_FROM = "LAB.SENDER@example.test"
appmod.warn_if_sender_mismatch()
appmod.SMTP_FROM = ""
appmod.warn_if_sender_mismatch()
check("no warning when SMTP_FROM is the same address, or unset", log.lines == [])
appmod.SMTP_USER = "relay-login"
appmod.SMTP_FROM = "lab.sender@example.test"
appmod.send_email("reader@example.test", *EMAILS["test email"])
check("a relay that signs in with a plain username still sends From a real address",
      parseaddr(messages[-1]["From"])[1] == "lab.sender@example.test")

# ---------------------------------------------------------------------------
print("7. The logo file")
# ---------------------------------------------------------------------------
import struct  # noqa: E402
import zlib  # noqa: E402
check(f"committed, small ({len(LOGO)} bytes, under 10 KB)", 0 < len(LOGO) < 10 * 1024)
header = struct.unpack(">IIBBBBB", LOGO[16:29]) if LOGO[:8] == b"\x89PNG\r\n\x1a\n" else None
check("a PNG, 192x64 - 96x32 shown at 2x - with an alpha channel",
      header is not None and header[:2] == (192, 64) and header[2:4] == (8, 6))


def first_pixel_alpha(data):
    """Alpha of the top-left pixel: the corner of the lockup is empty."""
    idat, pos = b"", 8
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        if kind == b"IDAT":
            idat += data[pos + 8:pos + 8 + length]
        pos += 12 + length
    row = zlib.decompress(idat)[:1 + 4]
    # The filter byte, then R G B A. Nothing lies above or to the left of
    # the first pixel of the first row, so every PNG filter stores it as is.
    return row[4]


check("transparent, so it sits on a light or a dark background", LOGO and first_pixel_alpha(LOGO) == 0)

# ---------------------------------------------------------------------------
print("8. Greetings use the first name")
# ---------------------------------------------------------------------------
from werkzeug.security import generate_password_hash  # noqa: E402
import sqlite3  # noqa: E402
appmod.SMTP_USER = "lab.sender@example.test"
appmod.SMTP_FROM = ""
appmod.run_in_background = lambda target: target()
conn = sqlite3.connect(database.DB_PATH)
for username, first_name, email in (("named.admin", "Mica", "named@example.test"),
                                    ("plain.admin", "", "plain@example.test")):
    conn.execute("INSERT INTO admins (username, password_hash, role, first_name, email, must_change_password) "
                 "VALUES (?, ?, 'admin', ?, ?, 0)",
                 (username, generate_password_hash("Str0ng!Passw0rd"), first_name, email))
conn.commit()
conn.close()


def greeting_of(message):
    match = re.search(r"Hello ([^,]+),", message.get_body(("plain",)).get_content()) if message else None
    return match.group(1) if match else None


for username, expected in (("named.admin", "Mica"), ("plain.admin", "plain.admin")):
    client = appmod.app.test_client()
    client.post("/login", data={"username": username, "password": "Str0ng!Passw0rd"})
    before = len(messages)
    client.post("/admins/test-email")
    got = greeting_of(messages[-1]) if len(messages) == before + 1 else None
    check(f"test email to {username}: 'Hello {expected},' ({got})", got == expected)
    before = len(messages)
    appmod.app.test_client().post("/forgot-password", data={"email": f"{username.split('.')[0]}@example.test"})
    got = greeting_of(messages[-1]) if len(messages) == before + 1 else None
    check(f"forgot-password to {username}: 'Hello {expected},' ({got})", got == expected)

shutil.rmtree(work, ignore_errors=True)
print()
print(f"{len(passed)} passed, {len(failed)} failed")
for label in failed:
    print(f"  - {label}")
sys.exit(1 if failed else 0)

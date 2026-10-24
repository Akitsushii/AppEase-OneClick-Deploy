import sys
import base64
import hashlib
import hmac
import io
import ipaddress
import json
import ntpath
import os
import re
import stat as stat_module
import secrets
import smtplib
import socket
import zipfile
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from html import escape as html_escape
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from collections import OrderedDict
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import quote, urlencode

import logging  # DEBUG-REMOVE
import requests as http_requests
from dotenv import load_dotenv

from flask import Flask, Response, jsonify, render_template, request, redirect, send_from_directory, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from database import INSTALLATION_METHODS, get_db, init_db, installation_record_key

app = Flask(__name__)
app.logger.setLevel(logging.INFO)  # DEBUG-REMOVE

# Must run BEFORE the secret key is read: load_dotenv() is what puts the
# contents of .env into os.environ, and reading the key above this line
# would only ever see whatever the shell happened to export.
load_dotenv()

# --- Session signing key -------------------------------------------------
#
# Flask signs the session cookie with this. The cookie is what carries
# admin_id and admin_role, and every @login_required / @admin_required
# check reads the role back out of it - so anyone who knows this string
# can forge a cookie naming themselves an administrator and sign in with
# no password at all, using nothing but the itsdangerous package that
# ships with Flask.
#
# It used to be a literal on this line, in a file committed to a shared
# repository, which published the key alongside the lock. That value is
# permanently burned: it is still in the git history, and editing this
# file does not remove it from past commits. Only a genuinely NEW value
# fixes it, which is why the old one is refused outright below rather
# than merely discouraged.
#
# There is deliberately NO fallback. A default here would mean an install
# that forgot to set the key runs anyway, quietly, with a key an attacker
# can read - which is the exact situation this change exists to end. A
# refusal at startup is loud, immediate, and impossible to ship past.
SECRET_KEY_ENV = "SECRET_KEY"
# The literal that leaked. Kept only so it can be rejected: someone
# copying the old line into .env would otherwise change nothing at all.
COMPROMISED_SECRET_KEYS = {"appease-bulsu-meneses-2026"}
MIN_SECRET_KEY_LENGTH = 32


def _load_secret_key():
    """Read and sanity-check the session signing key, or refuse to start."""
    value = (os.getenv(SECRET_KEY_ENV) or "").strip()
    if not value:
        raise RuntimeError(
            f"{SECRET_KEY_ENV} is not set. AppEase signs its admin session "
            f"cookies with it, so it will not start without one.\n"
            f"Generate a value and put it in server/.env as a single line:\n"
            f'    {SECRET_KEY_ENV}=<64 random hex characters>\n'
            f"You can generate one with:\n"
            f'    python -c "import secrets; print(secrets.token_hex(32))"'
        )
    if value in COMPROMISED_SECRET_KEYS:
        raise RuntimeError(
            f"{SECRET_KEY_ENV} is set to the old hardcoded value, which is "
            f"public in this repository's git history and can be used to "
            f"forge an administrator session. Generate a NEW one:\n"
            f'    python -c "import secrets; print(secrets.token_hex(32))"'
        )
    if len(value) < MIN_SECRET_KEY_LENGTH:
        raise RuntimeError(
            f"{SECRET_KEY_ENV} is only {len(value)} characters. Use at least "
            f"{MIN_SECRET_KEY_LENGTH}; a short key can be brute-forced offline "
            f"by anyone holding one signed cookie."
        )
    return value


app.secret_key = _load_secret_key()

SILENTINSTALLHQ_API_KEY = os.getenv("SILENTINSTALLHQ_API_KEY", "")

# Outgoing mail, all optional: with SMTP_HOST unset every message path
# shows the password on screen instead - the expected mode for a lab with
# no outbound mail.
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587") or 587)
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
# Only read to warn about it - see email_sender_address().
SMTP_FROM = os.getenv("SMTP_FROM", "")
SMTP_USE_TLS = (os.getenv("SMTP_USE_TLS", "1") or "1").strip().lower() not in ("0", "false", "no")
# Per network step, inside whichever thread is sending. A lab network that
# blocks outbound SMTP fails by HANGING rather than refusing, so without a
# timeout a send would sit there for the OS default - minutes.
SMTP_TIMEOUT_SECONDS = 10
# How long password-issuing routes wait to hear whether the email went;
# past it the password is shown on screen. Bounds the whole send, DNS
# included, unlike SMTP_TIMEOUT_SECONDS. 15, not 6: Gmail took over 6 s
# from the lab network. Dialog text and log both read this value.
EMAIL_CONFIRM_SECONDS = 15
# Sends allowed in flight at once. On a network that hangs, each one holds a
# thread for up to a minute; past this many, a send is dropped and logged
# rather than piling up threads.
EMAIL_MAX_IN_FLIGHT = 4

UPLOAD_FOLDER = os.path.join(app.root_path, "uploads")
ALLOWED_OFFLINE_EXTENSIONS = {".exe", ".msi"}
# An ORDERED tuple: a lifecycle, filling the per-row dropdown in order.
# The waiting/no-fault values answer "why is this not fixed yet", which
# "in progress" cannot.
ALLOWED_REPORT_STATUSES = (
    "open",
    "in_progress",
    "waiting_for_parts",
    "waiting_for_approval",
    "resolved",
    "no_fault_found",
)

# Both count as done, or the dashboard nags forever about a machine that
# was fine. Every "still outstanding" query reads this, never a literal.
FINISHED_REPORT_STATUSES = ("resolved", "no_fault_found")

# Written out: capitalize() on the raw value gets these right only by luck.
REPORT_STATUS_LABELS = {
    "open": "Open",
    "in_progress": "In progress",
    "waiting_for_parts": "Waiting for parts",
    "waiting_for_approval": "Waiting for approval",
    "resolved": "Resolved",
    "no_fault_found": "No fault found",
}
# Ordered, not a set: sorted() would put Low in the middle.
ALLOWED_PRIORITIES = ("high", "medium", "low")
ALLOWED_PROBLEM_TYPES = {"keyboard", "mouse", "monitor", "network", "software", "other"}
# Not "staff": refused on submit, though stored rows still display via
# role_label()'s fallback.
ALLOWED_REPORTER_ROLES = {"student", "faculty"}
# One list for the route's check and the page's dropdown, so they cannot
# drift. Anything else is typed under "Other..."; stored values are never
# rewritten.
REPORT_PROGRAMS = (
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
)
REPORT_PROGRAM_CODES = frozenset(code for code, _ in REPORT_PROGRAMS)
# The "Other..." option's value. Never a program code, so it can never be
# saved as one; what is saved for it is the program the student typed.
REPORT_PROGRAM_OTHER = "__other__"
# A closed list: a machine really is one of these. Location stays free
# text, since room names are a local decision.
ALLOWED_DEVICE_TYPES = ["Desktop", "Laptop", "Other"]
# Fits real room names ("Computer Laboratory 1" is 21) while keeping a
# group header from wrapping.
MAX_LOCATION_LENGTH = 30
LOCATION_PATTERN = r"^[A-Za-z0-9.()\- ]+$"
# A closed list, so the deploy filter never sees "retired"/"Retired"
# variants. NULL means "In use": older rows have it, and reading it
# otherwise would drop them from the deploy picker.
ASSET_STATUS_IN_USE = "In use"
ASSET_STATUS_RETIRED = "Retired"
ALLOWED_ASSET_STATUSES = [ASSET_STATUS_IN_USE, "In repair", ASSET_STATUS_RETIRED]
MAX_VENDOR_LENGTH = 40
# Every deploy path filters on this, and it is written once so the three
# of them cannot drift apart. NULL passes because NULL means In use.
#
# It is applied in the SQL of the deploy ROUTES, not only in the picker
# template: hiding a retired machine in the UI while the POST still accepts
# it would be decoration, and a stale tab or a hand-made request would
# deploy to it anyway.
NOT_RETIRED_CLAUSE = "(asset_status IS NULL OR asset_status <> ?)"

# --- Equipment (technician-recorded peripherals) -------------------------
#
# category is free text: technicians know what the labs own better than a
# fixed list, and a wrong one means a migration or a drawer of "Other".
MAX_EQUIPMENT_CATEGORY_LENGTH = 30
MAX_EQUIPMENT_FIELD_LENGTH = 60
MAX_EQUIPMENT_NOTES_LENGTH = 200
# A typo guard, not a security limit: 1000 keyboards on one PC is a typo.
MAX_EQUIPMENT_QUANTITY = 999
EQUIPMENT_TEXT_PATTERN = r"^[A-Za-z0-9.,'&()/\- ]+$"
# Only for displaying values on older reports; the section ("BSIT 3A")
# carries the year now.
ALLOWED_YEAR_LEVELS = {"1st Year", "2nd Year", "3rd Year", "4th Year"}
# Report photos live under static/ so they are served by Flask's own static
# route - no new endpoint, and nothing in that folder is ever executed.
REPORT_PHOTO_FOLDER = os.path.join(app.root_path, "static", "report_photos")
ALLOWED_PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_REPORT_PHOTOS = 3
MAX_REPORT_PHOTO_BYTES = 5 * 1024 * 1024
# Under static/ like report photos. 2 MB, not 5: an avatar renders at
# 80px, and profile.html tells the user the same number.
AVATAR_FOLDER = os.path.join(app.root_path, "static", "avatars")

# Cached hard: uncached, every navigation re-requests CSS, font and JS,
# and even a 304 blocks first paint. Safe only because
# add_static_cache_buster() stamps each URL with the file's mtime, so a
# rebuild is a new URL.
STATIC_CACHE_SECONDS = 60 * 60 * 24 * 30
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = STATIC_CACHE_SECONDS

# The browser leaves the session cookie off any POST another site starts.
# There are no CSRF tokens, so without this a page elsewhere
# could post a Software Library entry and a deployment as whoever is signed
# in - and the install command runs on every lab PC. Every form and fetch()
# here posts to its own site, so none of them is affected. Lax rather than
# Strict: Strict would also sign people out of every link into AppEase from
# somewhere else, such as an email.
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


@app.url_defaults
def add_static_cache_buster(endpoint, values):
    """Append ?v=<mtime> to each static file URL so a rebuild is a new URL.
    Directories are skipped: two pages concatenate icon names onto the
    'img/' URL in JS, and a stamp there would 404 every icon."""
    if endpoint != "static":
        return
    filename = values.get("filename")
    if not filename:
        return
    # Fonts are NOT stamped: app.css reaches the font via a relative url()
    # that cannot carry a version, so a stamped preload would fetch it
    # twice. Safe because fonts are replaced under new names, never in place.
    if filename.replace("\\", "/").startswith("fonts/"):
        return
    # One stat, not isfile() then stat(): the file can vanish in between.
    try:
        info = os.stat(os.path.join(app.static_folder, filename))
    except (OSError, TypeError, ValueError):
        # Missing, unreadable, or a filename os.path.join will not take. Leave
        # the URL alone - this must never turn a page render into a 500.
        return
    if not stat_module.S_ISREG(info.st_mode):
        return
    values["v"] = int(info.st_mtime)
MAX_AVATAR_BYTES = 2 * 1024 * 1024
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(REPORT_PHOTO_FOLDER, exist_ok=True)
os.makedirs(AVATAR_FOLDER, exist_ok=True)

STAGING_FOLDER = os.path.join(app.root_path, "staging")
os.makedirs(STAGING_FOLDER, exist_ok=True)

# --- Chocolatey Community Repository lookup ------------------------------
#
# Only helps fill in the add-software form; deployment never depends on
# it. Chocolatey do not support third-party API queries, so every call is
# best-effort and a failure degrades the field to plain text.
#
# Two undocumented details, found by probing the live API:
#   1. Spaces MUST encode as %20: the server does not decode "+", and a
#      "+"-joined $filter returns 200 with an empty feed. Hence
#      urlencode(..., quote_via=quote) in _chocolatey_url.
#   2. The package id is the ATOM <title> (d:Title is the display name);
#      the <id> URL carries a fallback copy.
CHOCOLATEY_API_BASE = "https://community.chocolatey.org/api/v2"
# Fetched wider than shown, so re-ranking has candidates.
CHOCOLATEY_SEARCH_FETCH = 20
CHOCOLATEY_SEARCH_RESULTS = 8
# 6s: a down repository hangs rather than failing, and each hang holds a
# Waitress thread. It bounds the gap between bytes, so a slow but moving
# response is fine.
CHOCOLATEY_TIMEOUT = 6
CHOCOLATEY_CACHE_TTL_SECONDS = 600
CHOCOLATEY_MIN_TERM_LENGTH = 3
# Then a cooldown: outages last hours, and retrying per keystroke only
# queues more hangs.
CHOCOLATEY_FAILURE_THRESHOLD = 3
CHOCOLATEY_COOLDOWN_SECONDS = 60

ATOM_NS = "{http://www.w3.org/2005/Atom}"
ODATA_NS = "{http://schemas.microsoft.com/ado/2007/08/dataservices}"
ODATA_META_NS = "{http://schemas.microsoft.com/ado/2007/08/dataservices/metadata}"
CHOCOLATEY_ID_FROM_URL = re.compile(r"Packages\(Id='([^']+)'")

# Everything outside this set is stripped before the term goes into an
# OData string literal. The single quote is the one that matters: it would
# otherwise close the literal early and let the rest of the input be read
# as query syntax.
CHOCOLATEY_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9 .+\-_]")

# "search:"/"id:" keys share one store, lock and cap; responses are ~120KB
# and repeat while typing. Only successes (empty ones included) are cached,
# so a blip is never frozen in; the cooldown handles failures.
_chocolatey_cache = {}
_chocolatey_cache_lock = threading.Lock()

_chocolatey_failures = {"count": 0, "retry_at": 0.0}


# The fixed-width SVG fits this many bars before labels collide; 92 fits
# the "Last 3 months" preset.
CHART_MAX_DAYS = 92

# Beyond this, weekly bars: daily ones get ~6px wide on a projector.
ACTIVITY_DAILY_MAX_DAYS = 35


# In static/img/, not static/avatars/: that folder holds uploads and is
# swept on replace. Read from disk by this exact name.
DEFAULT_AVATAR_FILE = "img/Default_profile.jpg"


@app.template_global()
def avatar_url_for(stored_path):
    """Static URL for a stored avatar_path, or the shared default. Also a
    template global, so the Accounts table needs no copy of this logic."""
    return url_for("static", filename=stored_path or DEFAULT_AVATAR_FILE)


# ---- Dark mode -------------------------------------------------------------
#
# A COOKIE, resolved SERVER-SIDE. Not a column: auth_base.html picks a
# theme before anyone signs in, and these are shared lab desktops, so the
# setting belongs to the machine. Not a pre-paint script: this is an MPA,
# and a script runs after <html> is parsed, flashing white on every click.
THEME_COOKIE = "theme"
THEME_DARK = "dark"
THEME_LIGHT = "light"
# One year, like sidebar_collapsed. Written only by base.html's toggle;
# there is no theme route.
THEME_COOKIE_MAX_AGE = 31536000


def resolve_theme():
    """"dark" or "light" for this request. Only the exact string "dark" is
    dark: a damaged cookie must land on the known-good light theme."""
    try:
        if request.cookies.get(THEME_COOKIE) == THEME_DARK:
            return THEME_DARK
    except Exception:
        # No request context (CLI, shell) - the light default is still right.
        pass
    return THEME_LIGHT


@app.context_processor
def inject_theme():
    """Theme for <html class>, the color-scheme meta and the pre-paint rule.
    A context processor, so no route can forget it (that page would flash)."""
    theme = resolve_theme()
    return {"theme": theme, "theme_dark": theme == THEME_DARK}


@app.context_processor
def inject_current_avatar():
    """Current account's picture for the sidebar and topbar; None signed
    out. Broad except, as in inject_agent_counts: an unreadable database
    gives initials, never a 500."""
    if not session.get("admin_id"):
        return {"current_avatar_url": None}
    try:
        row = get_db().execute(
            "SELECT avatar_path FROM admins WHERE id = ?", (session["admin_id"],)
        ).fetchone()
    except Exception:
        return {"current_avatar_url": None}
    return {
        "current_avatar_url": avatar_url_for(row["avatar_path"] if row else None)
    }


@app.context_processor
def inject_agent_counts():
    """Seed the topbar agent pill for the first paint, which otherwise reads
    0 of 0 until the first poll. Skipped for non-admins (no pill)."""
    if session.get("admin_role") != "admin":
        return {"agents_online": 0, "agents_total": 0, "agents_retired": 0}
    try:
        counts = computer_counts(get_db())
    except Exception:
        # Deliberately broad (sqlite3 is not imported here). This runs on
        # EVERY render, error pages included: anything escaping turns one bad
        # query into a blank site.
        return {"agents_online": 0, "agents_total": 0, "agents_retired": 0}
    return {
        "agents_online": counts["online"],
        "agents_total": counts["total"],
        "agents_retired": counts["retired"],
    }


def computer_counts(db):
    """Total, online and retired counts: the one definition for the topbar
    pill, its poll and the dashboard card. Retired rows count only as
    retired, so retiring a PC visibly removes it."""
    row = db.execute(
        f"""
        SELECT
            SUM(CASE WHEN {NOT_RETIRED_CLAUSE} THEN 1 ELSE 0 END) AS total,
            SUM(CASE WHEN {NOT_RETIRED_CLAUSE} AND status = 'online' THEN 1 ELSE 0 END) AS online,
            SUM(CASE WHEN {NOT_RETIRED_CLAUSE} THEN 0 ELSE 1 END) AS retired
        FROM computers
        """,
        (ASSET_STATUS_RETIRED, ASSET_STATUS_RETIRED, ASSET_STATUS_RETIRED),
    ).fetchone()
    # SUM over zero rows is NULL, not 0.
    return {
        "total": row["total"] or 0,
        "online": row["online"] or 0,
        "retired": row["retired"] or 0,
    }


# Manila is UTC+8 with no daylight saving, so a fixed offset is correct and
# needs no timezone database on the lab machines.
MANILA_OFFSET = timedelta(hours=8)


@app.template_filter("manila")
def manila_time(value):
    """A stored UTC timestamp, shown in Manila time (UTC+8). Storage stays
    UTC."""
    if not value:
        return "-"
    text = str(value)
    parsed = _parse_stored_utc(text)
    if parsed is None:
        return text  # unknown format: show as-is rather than crash
    # Display only: sortable cells carry the raw value in data-sort.
    # Zero-padded so the column keeps a fixed width.
    return (parsed + MANILA_OFFSET).strftime("%d %b, %Y %I:%M:%S %p")


# Display labels only; the raw roles gate access throughout this file.
ROLE_LABELS = {"admin": "Computer Technician", "faculty": "Faculty"}


# Password policy - classic composition rules (upper + lower + digit +
# symbol), minimum 8. Defined once and called from all three write paths
# (account creation, admin reset, self-service change) so they can never
# drift apart. The rule ORDER here is mirrored by the live checklist in
# static/js/password-policy.js - keep the two in sync if either changes.
#
# Stored passwords are hashes and cannot be re-tested against a new policy,
# so this governs new passwords only; existing accounts keep working until
# their password is next changed. There is deliberately no forced rotation.
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128


def validate_password_strength(password):
    """Return an error message, or None when every rule is satisfied."""
    if not password:
        return "Password is required."
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Password must be at least {PASSWORD_MIN_LENGTH} characters."
    if len(password) > PASSWORD_MAX_LENGTH:
        return f"Password must be {PASSWORD_MAX_LENGTH} characters or fewer."
    if not re.search(r"[A-Z]", password):
        return "Password must include at least one uppercase letter."
    if not re.search(r"[a-z]", password):
        return "Password must include at least one lowercase letter."
    if not re.search(r"[0-9]", password):
        return "Password must include at least one number."
    if not re.search(r"[^A-Za-z0-9]", password):
        return "Password must include at least one symbol."
    return None


# Characters a temporary password is built from. Ambiguous glyphs are left
# out on purpose - this password gets read off a screen or out of an email and
# typed by hand, and 0/O and 1/l/I are where that goes wrong. The symbol set
# is small and keyboard-obvious for the same reason.
TEMP_PASSWORD_UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"
TEMP_PASSWORD_LOWER = "abcdefghijkmnpqrstuvwxyz"
TEMP_PASSWORD_DIGITS = "23456789"
TEMP_PASSWORD_SYMBOLS = "!@#$%&*?"
TEMP_PASSWORD_LENGTH = 14


# Mirrors validateUsername() in admins.html. One server-side rule for
# both create and rename, so neither door lets in what the other refuses.
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 30


def validate_username(username):
    """Returns an error string, or None when the username is acceptable."""
    if not username:
        return "Username is required."
    if len(username) < USERNAME_MIN_LENGTH:
        return f"Username must be at least {USERNAME_MIN_LENGTH} characters."
    if len(username) > USERNAME_MAX_LENGTH:
        return f"Username must be {USERNAME_MAX_LENGTH} characters or fewer."
    if not USERNAME_PATTERN.match(username):
        return "Use only letters, numbers, dot, underscore, or hyphen (no spaces)."
    return None


def generate_temporary_password():
    """A random password guaranteed to pass validate_password_strength().
    One of each class, random fill, then shuffle: a re-roll loop could spin
    forever under a tighter policy. secrets, not random: random is seeded
    predictably enough to guess a password from when it was issued."""
    alphabet = TEMP_PASSWORD_UPPER + TEMP_PASSWORD_LOWER + TEMP_PASSWORD_DIGITS + TEMP_PASSWORD_SYMBOLS
    characters = [
        secrets.choice(TEMP_PASSWORD_UPPER),
        secrets.choice(TEMP_PASSWORD_LOWER),
        secrets.choice(TEMP_PASSWORD_DIGITS),
        secrets.choice(TEMP_PASSWORD_SYMBOLS),
    ]
    characters += [secrets.choice(alphabet) for _ in range(TEMP_PASSWORD_LENGTH - len(characters))]
    # secrets has no shuffle; sorting by a random key is the stdlib-only way to
    # do it without falling back to the random module.
    characters.sort(key=lambda _c: secrets.randbelow(1 << 32))
    return "".join(characters)


# ---- What every email looks like -------------------------------------------
#
# One template, built as BOTH a plain-text and an HTML part so they never
# disagree. The HTML targets mail clients: nested tables, inline styles,
# max 600px, nothing fetched. The one <style> block only ADDS dark-mode
# colours; Gmail strips it, so everything needed is inline.
EMAIL_FROM_NAME = "AppEase - BulSU Lab"
EMAIL_FOOTER = "BulSU Meneses Campus computer lab - this is an automatic message, replies are not read."
_EMAIL_FONT = "font-family:Arial,Helvetica,sans-serif;"
_EMAIL_MONO = "font-family:Consolas,'Courier New',Courier,monospace;"
# --color-primary, so mail matches the app.
_EMAIL_ACCENT = "#4173d8"
# Status pills on the emails to a reporter: the app's own --tone-* pairs,
# light inline and dark in _EMAIL_DARK_CSS.
_EMAIL_TONES = {
    "info": ("#dbeafe", "#1d4ed8"),
    "good": ("#dcfce7", "#166534"),
    "neutral": ("#f3f4f6", "#6b7280"),
}
_EMAIL_DARK_CSS = (
    "@media (prefers-color-scheme: dark) {"
    " .ae-page { background-color:#121113 !important; }"
    " .ae-card { background-color:#1c1b1e !important; border-left-color:#2f2e33 !important;"
    " border-right-color:#2f2e33 !important; border-bottom-color:#2f2e33 !important; }"
    " .ae-ink { color:#ececec !important; }"
    " .ae-soft { color:#a8a8a8 !important; }"
    " .ae-rule { border-color:#2f2e33 !important; }"
    " .ae-code { background-color:#121113 !important; border-color:#45444a !important; color:#f5f5f5 !important; }"
    " .ae-pill-info { background-color:#14243f !important; color:#7ea6f0 !important; }"
    " .ae-pill-good { background-color:#10291c !important; color:#4ade80 !important; }"
    " .ae-pill-neutral { background-color:#26252a !important; color:#a8a8a8 !important; }"
    " }"
)

# The sidebar logo as a 2x PNG inside each message (cid:): clients do not
# draw SVG, the LAN-only server is unreachable from a phone, and Gmail
# blocks data: URIs. Its colours read on light (4.5:1) and on Gmail's dark
# ground (3.3:1) alike.
EMAIL_LOGO_FILE = os.path.join(app.root_path, "static", "img", "appease-email-logo.png")
EMAIL_LOGO_CID = "appease-logo@appease.email"
try:
    with open(EMAIL_LOGO_FILE, "rb") as _logo:
        EMAIL_LOGO_PNG = _logo.read()
except OSError:
    # Mail still goes; the header shows the image's alt text instead.
    EMAIL_LOGO_PNG = b""


class EmailBody(str):
    """One email; the str IS the plain-text part, so anything reading text
    keeps working. The HTML part rides on .html."""

    html = ""


def render_email(title, lead, rows=(), secret=None, link=None, closing=(), status=None):
    """One email's text and HTML parts. rows: (label, value) pairs; secret:
    the monospace (label, value); link: (text, url); status: (label, tone),
    HTML only. Every value is escaped in the HTML part."""
    text = [title, "", lead, ""]
    if rows:
        text += [f"{label}: {value}" for label, value in rows] + [""]
    if secret:
        text += [f"{secret[0]}: {secret[1]}", ""]
    if link:
        text += [f"{link[0]}:", link[1], ""]
    for paragraph in closing:
        text += [paragraph, ""]
    text += ["--", EMAIL_FOOTER]
    body = EmailBody("\n".join(text) + "\n")
    body.html = _email_html(title, lead, rows, secret, link, closing, status)
    return body


def _email_html(title, lead, rows, secret, link, closing, status=None):
    def esc(value):
        return html_escape(str(value), quote=True)

    def paragraph(value, colour="#374151", css="ae-soft"):
        return (f'<p class="{css}" style="margin:0 0 16px 0;{_EMAIL_FONT}font-size:15px;line-height:22px;'
                f'color:{colour};">{esc(value)}</p>')

    parts = [
        f'<h1 class="ae-ink" style="margin:0 0 12px 0;{_EMAIL_FONT}font-size:20px;line-height:28px;'
        f'font-weight:bold;color:#111827;">{esc(title)}</h1>',
    ]
    if status:
        label, tone = status
        background, colour = _EMAIL_TONES[tone]
        parts.append(
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 14px 0;"><tr>'
            f'<td class="ae-pill-{tone}" style="padding:3px 10px;border-radius:999px;background-color:{background};'
            f'{_EMAIL_FONT}font-size:12px;line-height:18px;font-weight:bold;color:{colour};">{esc(label)}</td>'
            '</tr></table>'
        )
    parts.append(paragraph(lead))
    if rows:
        cells = []
        for label, value in rows:
            cells.append(
                '<tr>'
                f'<td class="ae-soft ae-rule" style="padding:8px 12px 8px 0;border-top:1px solid #e5e7eb;'
                f'{_EMAIL_FONT}font-size:14px;line-height:20px;color:#6b7280;width:38%;vertical-align:top;">{esc(label)}</td>'
                f'<td class="ae-ink ae-rule" style="padding:8px 0;border-top:1px solid #e5e7eb;'
                f'{_EMAIL_FONT}font-size:14px;line-height:20px;color:#111827;vertical-align:top;'
                f'word-break:break-word;">{esc(value)}</td>'
                '</tr>'
            )
        parts.append('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
                     'style="border-collapse:collapse;margin:0 0 16px 0;">' + "".join(cells) + '</table>')
    if secret:
        parts.append(
            f'<p class="ae-soft" style="margin:0 0 6px 0;{_EMAIL_FONT}font-size:13px;line-height:18px;'
            f'color:#6b7280;">{esc(secret[0])}</p>'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="border-collapse:separate;margin:0 0 16px 0;"><tr>'
            f'<td class="ae-code" style="padding:14px 16px;background-color:#f3f4f6;border:1px solid #d1d5db;'
            f'border-radius:6px;{_EMAIL_MONO}font-size:20px;line-height:26px;letter-spacing:1px;'
            f'color:#111827;word-break:break-all;">{esc(secret[1])}</td>'
            '</tr></table>'
        )
    if link:
        parts.append(
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 12px 0;"><tr>'
            f'<td style="border-radius:6px;background-color:#2f5fc4;">'
            f'<a href="{esc(link[1])}" style="display:inline-block;padding:12px 20px;{_EMAIL_FONT}font-size:15px;'
            f'font-weight:bold;color:#ffffff;text-decoration:none;border-radius:6px;">{esc(link[0])}</a>'
            '</td></tr></table>'
            f'<p class="ae-soft" style="margin:0 0 16px 0;{_EMAIL_FONT}font-size:13px;line-height:18px;'
            f'color:#6b7280;">If the button does not work, copy this address into your browser:<br>'
            f'<span class="ae-ink" style="{_EMAIL_MONO}font-size:13px;color:#111827;word-break:break-all;">'
            f'{esc(link[1])}</span></p>'
        )
    parts += [paragraph(line) for line in closing]
    return (
        '<!DOCTYPE html>'
        '<html lang="en"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        '<meta name="supported-color-schemes" content="light dark">'
        f'<title>{esc(title)}</title>'
        f'<style>{_EMAIL_DARK_CSS}</style>'
        '</head>'
        '<body class="ae-page" style="margin:0;padding:0;background-color:#f3f4f6;">'
        '<table role="presentation" class="ae-page" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="background-color:#f3f4f6;"><tr><td align="center" style="padding:24px 12px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="max-width:600px;width:100%;">'
        '<tr><td style="padding:0 4px 14px 4px;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
        '<td style="padding:0 12px 0 0;vertical-align:middle;">'
        f'<img src="cid:{EMAIL_LOGO_CID}" width="96" height="32" alt="AppEase" '
        'style="display:block;width:96px;height:32px;border:0;outline:none;text-decoration:none;"></td>'
        '<td style="vertical-align:middle;">'
        f'<p class="ae-ink" style="margin:0;{_EMAIL_FONT}font-size:16px;line-height:20px;font-weight:bold;'
        f'color:#111827;">AppEase</p>'
        f'<p class="ae-soft" style="margin:0;{_EMAIL_FONT}font-size:12px;line-height:16px;color:#6b7280;">'
        'BulSU Meneses Campus</p>'
        '</td></tr></table>'
        '</td></tr>'
        '<tr><td class="ae-card" style="background-color:#ffffff;border:1px solid #e5e7eb;'
        f'border-top:3px solid {_EMAIL_ACCENT};border-radius:8px;padding:28px 24px 12px 24px;">'
        + "".join(parts) +
        '</td></tr>'
        f'<tr><td class="ae-soft" style="padding:16px 4px 0 4px;{_EMAIL_FONT}font-size:12px;line-height:18px;'
        f'color:#6b7280;">{esc(EMAIL_FOOTER)}</td></tr>'
        '</table>'
        '</td></tr></table>'
        '</body></html>'
    )


def email_sender_address():
    """The From address: the account the server signs in as, since Gmail
    sends any other From to spam. Only a relay without an "@" login falls
    back to SMTP_FROM."""
    if "@" in SMTP_USER:
        return SMTP_USER
    return SMTP_FROM or SMTP_USER


def warn_if_sender_mismatch():
    """Once, at startup. Names the settings, never their values."""
    if SMTP_HOST and SMTP_FROM and "@" in SMTP_USER and SMTP_FROM.strip().lower() != SMTP_USER.strip().lower():
        app.logger.warning(
            "SMTP_FROM is set to a different address from SMTP_USER. Email is sent From the "
            "SMTP_USER address, because a From that is not the signed-in account fails "
            "Gmail's sender checks; SMTP_FROM is not used."
        )


warn_if_sender_mismatch()


def send_email(to_address, subject, body):
    """Send one message (text + HTML): (sent, error_text). NEVER raises:
    callers have already committed a password change, and an exception
    would make the admin reissue, invalidating the password just sent."""
    if not SMTP_HOST:
        return False, "Email is not configured on this server."
    if not to_address:
        return False, "No email address on file for that account."
    try:
        if not isinstance(body, EmailBody):
            body = render_email(subject, str(body))
        sender = email_sender_address()
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = formataddr((EMAIL_FROM_NAME, sender))
        message["To"] = to_address
        message["Date"] = formatdate(localtime=True)
        # The domain is given, never looked up: without it make_msgid()
        # asks for the machine's FQDN, a DNS lookup that can stall on a lab
        # network - inside a send that is meant to be bounded.
        message["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[1] if "@" in sender else "appease.local")
        message.set_content(str(body), charset="utf-8")
        message.add_alternative(body.html, subtype="html", charset="utf-8")
        if EMAIL_LOGO_PNG:
            # Related, so the HTML part carries the logo its cid: names.
            message.get_payload()[1].add_related(
                EMAIL_LOGO_PNG, maintype="image", subtype="png", cid=f"<{EMAIL_LOGO_CID}>",
                disposition="inline", filename="appease-logo.png",
            )
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
            if SMTP_USE_TLS:
                smtp.starttls()
            if SMTP_USER:
                smtp.login(SMTP_USER, SMTP_PASSWORD)
            smtp.send_message(message)
        return True, None
    except Exception as exc:
        # Server log: the recipient's domain and the kind of failure, never
        # the address, the subject or the body - a body can hold a password.
        app.logger.warning(f"Email to {email_domain(to_address)} not sent: {exc.__class__.__name__}")
        # The message is shown to an admin, so it says what to do next rather
        # than only what broke.
        return False, f"Could not send the email ({exc.__class__.__name__})."


def email_domain(address):
    """"*@example.com" - how a recipient is named in the server log."""
    if address and "@" in address:
        return "*@" + address.rsplit("@", 1)[1]
    return "*"


def run_in_background(target):
    """Run target() on a daemon thread; one function so tests can run it
    inline instead of racing a thread."""
    threading.Thread(target=target, daemon=True).start()


_email_slots = threading.BoundedSemaphore(EMAIL_MAX_IN_FLIGHT)


def _send_capped(to_address, subject, body):
    """send_email(), unless EMAIL_MAX_IN_FLIGHT sends are already running.
    Called from a background thread, never from a request."""
    if not _email_slots.acquire(blocking=False):
        app.logger.warning(f"Email to {email_domain(to_address)} not sent: too many sends already in flight")
        return False, "Too many emails are being sent right now."
    try:
        return send_email(to_address, subject, body)
    finally:
        _email_slots.release()


def send_email_later(to_address, subject, body):
    """Send in the background and return at once. Without mail or an
    address it does nothing at all."""
    if not SMTP_HOST or not to_address:
        return
    run_in_background(lambda: _send_capped(to_address, subject, body))


def send_email_within(to_address, subject, body, seconds=None):
    """Send in the background, waiting at most `seconds`: (sent, error).
    A timeout counts as not sent (the password is shown) while the send
    carries on, so the email may still arrive."""
    if not SMTP_HOST:
        return False, "Email is not configured on this server."
    if not to_address:
        return False, "No email address on file for that account."
    seconds = EMAIL_CONFIRM_SECONDS if seconds is None else seconds
    outcome = {}
    finished = threading.Event()

    def attempt():
        try:
            outcome["result"] = _send_capped(to_address, subject, body)
        finally:
            finished.set()

    run_in_background(attempt)
    if finished.wait(seconds) and "result" in outcome:
        return outcome["result"]
    app.logger.warning(f"Email to {email_domain(to_address)} not confirmed within {seconds}s")
    return False, f"The email could not be confirmed within {seconds} second{'' if seconds == 1 else 's'}."


# Temporary passwords waiting to be shown once, on the Accounts page, after
# a new account or an admin reset whose email was not confirmed. They used
# to ride in the redirect URL (?issued_password=...), which put them in the
# address bar and the browser's history; the session is no better, because
# Flask's session is a signed - not encrypted - cookie. Here only a random
# one-time id goes in the session, and the password stays on the server
# until the page that shows it is rendered.
#
# A plain dict works because production is ONE process: Waitress
# (create_server(..., threads=8), at the bottom of this file) serves every
# request from threads of a single Python process. Run AppEase under several
# worker processes and the redirect could land on one that never saw the
# password - this would then need a shared store. A restart loses what is
# waiting; the admin simply resets again.
ISSUED_PASSWORD_SECONDS = 300
_issued_passwords = {}
_issued_lock = threading.Lock()


def hold_issued_password(details):
    """Keep details ({"for", "password", "note", "new"}) for one showing.
    Returns the one-time id, which is all the session ever carries."""
    now = time.monotonic()
    key = secrets.token_urlsafe(24)
    with _issued_lock:
        for stale in [k for k, (expires, _) in _issued_passwords.items() if expires <= now]:
            del _issued_passwords[stale]
        _issued_passwords[key] = (now + ISSUED_PASSWORD_SECONDS, details)
    return key


def take_issued_password(key):
    """The details held under key, removed as they are read - so a refresh
    shows nothing. None for no key, an unknown key or an expired one."""
    if not key:
        return None
    with _issued_lock:
        entry = _issued_passwords.pop(key, None)
    if entry is None or entry[0] <= time.monotonic():
        return None
    return entry[1]


NOT_YOU_LINE = "If you did not make this change, tell the lab administrator straight away."


def greeting_name(first_name, username):
    """"Mica" when the account has a first name, else its username."""
    return (first_name or "").strip() or username
NOT_EXPECTED_LINE = "If you were not expecting this, tell the lab administrator straight away."


def email_changed_email_body(username, new_email):
    """Sent to the OLD address, which is the whole point: if the change was not
    theirs, the old inbox is the only place they would still see it."""
    return render_email(
        "Your email address was changed",
        "The email address on your AppEase account was changed.",
        rows=[("Username", username), ("New address", new_email)],
        closing=["This message went to your previous address. Future AppEase mail, "
                 "including password resets, goes to the new one.", NOT_YOU_LINE],
    )


def username_changed_email_body(old_username, new_username):
    return render_email(
        "Your username was changed",
        "The username on your AppEase account was changed.",
        rows=[("Previous username", old_username), ("New username", new_username)],
        closing=["Sign in with the new one from now on. Your password has not changed.", NOT_YOU_LINE],
    )


def new_account_email_body(username, temporary_password, role_name, actor_name):
    return render_email(
        "Your AppEase account is ready",
        "An AppEase account has been created for you.",
        rows=[("Username", username), ("Role", role_name), ("Account created by", actor_name)],
        secret=("Temporary password", temporary_password),
        closing=["You will be asked to choose your own password the first time you sign in. "
                 "This temporary password stops working as soon as you do.", NOT_EXPECTED_LINE],
    )


def temporary_password_email_body(username, temporary_password, actor_name):
    return render_email(
        "Your password was reset",
        "Your AppEase password was reset.",
        rows=[("Username", username), ("Reset by", actor_name)],
        secret=("Temporary password", temporary_password),
        closing=["You will be asked to choose a new password the next time you sign in. "
                 "This temporary password stops working as soon as you do.", NOT_EXPECTED_LINE],
    )


@app.template_filter("spark_path")
def spark_path(series):
    """Daily counts as an SVG path across a 100x30 box. A filter, so the
    line exists before any script runs; a flat series draws a midline
    instead of dividing by zero."""
    points = [float(value or 0) for value in (series or [])]
    if len(points) < 2:
        return "M0,15 L100,15"
    low, high = min(points), max(points)
    span = high - low
    step = 100.0 / (len(points) - 1)
    coords = []
    for index, value in enumerate(points):
        x = index * step
        # 26 down to 4, so the stroke never clips against the box edge.
        y = 15.0 if span == 0 else 26.0 - ((value - low) / span) * 22.0
        coords.append(f"{x:.1f},{y:.1f}")
    return "M" + " L".join(coords)


@app.template_filter("role_label")
def role_label(value):
    return ROLE_LABELS.get(value, (value or "").capitalize())


def format_full_name(first_name, middle_initial, last_name):
    """"First MiddleInitial. Last" for display, or "" if both names are
    blank. The middle initial is used in full; format rules are client-side."""
    first_name = (first_name or "").strip()
    middle_initial = (middle_initial or "").strip()
    last_name = (last_name or "").strip()
    if not first_name and not last_name:
        return ""
    middle_part = (middle_initial + ".") if middle_initial else ""
    return " ".join(part for part in (first_name, middle_part, last_name) if part)


@app.template_filter("format_name")
def format_name_filter(first_name, middle_initial, last_name):
    return format_full_name(first_name, middle_initial, last_name)


def software_label(name, version):
    """"VLC 3.0.20", or just "VLC" when no version is recorded."""
    parts = [(name or "").strip(), (version or "").strip()]
    return " ".join(part for part in parts if part)


# Matches the Audit Trail's badges, so both agree on what "red" means;
# unlisted actions are neutral.
AUDIT_ACTION_TONES = {
    "account_deleted": "danger",
    "software_deleted": "danger",
    "equipment_deleted": "danger",
    "password_reset": "warn",
    "deployment_created": "info",
    # It removes software from lab PCs - as destructive as deleting a record.
    "uninstall_created": "danger",
    "report_deleted": "danger",
    "report_purged": "danger",
    "jobs_deleted": "danger",
    # The same weight as password_reset: a credential changed, by its owner.
    "password_changed": "warn",
    # Neither destroys anything, so neither is red: a cancel stops work that
    # was asked for, and a retirement takes a PC out of every deploy.
    "job_cancelled": "warn",
    "computer_retired": "warn",
    # A retry queues work, like a deployment.
    "job_retried": "info",
}

# Translates to the Audit Trail pill's tone names, so severity is still
# decided only in AUDIT_ACTION_TONES.
AUDIT_PILL_TONES = {"danger": "bad", "warn": "warn", "info": "info", "neutral": "good"}


@app.template_filter("audit_tone")
def audit_tone(action_type):
    """dt-pill tone for an audit action type."""
    return AUDIT_PILL_TONES.get(AUDIT_ACTION_TONES.get(action_type, "neutral"), "good")


def group_activity_by_day(rows):
    """Audit rows -> [{"day_label", "entries"}], keyed on the Manila date
    (23:30 UTC is the next morning here). Keeps the caller's ORDER BY; an
    unparseable date groups under its raw text rather than vanishing."""
    days = []
    by_key = {}
    for row in rows:
        parsed = _parse_stored_utc(row["created_at"])
        if parsed is None:
            raw = str(row["created_at"] or "")
            key = raw or "unknown"
            day_label = raw or "Date not recorded"
            time_label = raw or "-"
        else:
            local = parsed + MANILA_OFFSET
            key = local.strftime("%Y-%m-%d")
            day_label = local.strftime("%d %b, %Y")
            # Unpadded: unlike the manila filter, no fixed-width column here.
            time_label = local.strftime("%I:%M %p").lstrip("0")
        if key not in by_key:
            by_key[key] = {"day_label": day_label, "entries": []}
            days.append(by_key[key])
        by_key[key]["entries"].append({
            "action_type": row["action_type"],
            "details": row["details"],
            "time_label": time_label,
            # Same map the dashboard chip and the Audit Trail pill read, so a
            # deletion is red in all three places and severity is decided once.
            "tone": AUDIT_ACTION_TONES.get(row["action_type"], "neutral"),
        })
    return days


def _name_initials(name):
    """"Jane Doe" -> "JD", else one letter, else a dash. First and last
    only: three letters do not fit the avatar circle."""
    parts = [part for part in (name or "").replace(".", " ").split() if part]
    if not parts:
        return "-"
    if len(parts) == 1:
        return parts[0][0].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _parse_stored_utc(value):
    """Parse a stored timestamp in any format the DB holds; rows with and
    without fractional seconds both exist."""
    if not value:
        return None
    text = str(value)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def log_audit_event(actor_admin_id, target_admin_id, action_type, details=None,
                    target_type=None, target_label=None):
    """Record a sensitive action. details keeps a readable snapshot, since
    the target row may later be gone; target_type/target_label name
    non-account subjects (software, deployments)."""
    db = get_db()
    db.execute(
        "INSERT INTO audit_log "
        "(actor_admin_id, target_admin_id, action_type, details, target_type, target_label) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (actor_admin_id, target_admin_id, action_type, details, target_type, target_label),
    )
    db.commit()


# A bulk entry names at most this many ids and counts the rest, so an entry
# for 150 reports stays one readable line.
AUDIT_ID_LIST_LIMIT = 10


def audit_id_list(ids):
    """"#3, #7, #9" for a bulk entry: sorted, at most AUDIT_ID_LIST_LIMIT of
    them, then "and N more"."""
    ids = sorted(ids)
    shown = ", ".join(f"#{i}" for i in ids[:AUDIT_ID_LIST_LIMIT])
    rest = len(ids) - AUDIT_ID_LIST_LIMIT
    return f"{shown} and {rest} more" if rest > 0 else shown


def audit_count(count, noun):
    """"1 report", "5 reports"."""
    return f"{count} {noun}" + ("" if count == 1 else "s")


def audit_change(label, old, new):
    """One "Label: old -> new" piece of an entry's details."""
    return f"{label}: {old} -> {new}"


def audit_admin_name(db, admin_id):
    """An assignee as an entry names them: full name, else username, and
    "Unassigned" for nobody."""
    if admin_id is None:
        return "Unassigned"
    row = db.execute(
        "SELECT username, first_name, middle_initial, last_name FROM admins WHERE id = ?", (admin_id,)
    ).fetchone()
    if row is None:
        return f"Account #{admin_id}"
    return format_full_name(row["first_name"], row["middle_initial"], row["last_name"]) or row["username"]


def report_audit_label(report_id, hostname):
    """A single report as a target: "#12 (LAB1-PC07)"."""
    return f"#{report_id} ({hostname})"


def archived_report_refusal(report_id, endpoint="problem_reports_page"):
    """An archived report is read-only: every route that changes a report
    sends this back instead, so hiding the controls is not the only guard."""
    return redirect(url_for(endpoint, report_error=f"Report #{report_id} is archived. Restore it to change it."))


def priority_label(value):
    return (value or "").capitalize() or "Not set"


DETECT_JOBS = {}


# A 32-character lowercase hex string, as agent.py writes it. Anything
# else is treated exactly like no ID at all rather than refused, so a
# damaged ID file costs a machine its identity, not its connection.
AGENT_ID_PATTERN = re.compile(r"[0-9a-f]{32}")


def clean_agent_id(value):
    """The agent ID from a request body, or None if absent or malformed."""
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    return value if AGENT_ID_PATTERN.fullmatch(value) else None


def free_computer_name(db, wanted, keep_id=None):
    """wanted if free, else wanted-2, wanted-3...; keep_id keeps its own."""
    candidate = wanted
    suffix = 1
    while True:
        row = db.execute(
            "SELECT id FROM computers WHERE hostname = ?", (candidate,)
        ).fetchone()
        if row is None or row["id"] == keep_id:
            return candidate
        suffix += 1
        candidate = f"{wanted}-{suffix}"


def rename_computer(db, old_name, new_name):
    """Move a computer and its history to a new name: jobs and
    problem_reports store the hostname, not computers.id."""
    db.execute("UPDATE computers SET hostname = ? WHERE hostname = ?", (new_name, old_name))
    db.execute("UPDATE jobs SET computer_hostname = ? WHERE computer_hostname = ?", (new_name, old_name))
    db.execute(
        "UPDATE problem_reports SET computer_hostname = ? WHERE computer_hostname = ?",
        (new_name, old_name),
    )


def agent_computer_name(db, hostname, agent_id):
    """The name an agent's computer is registered under, without writing:
    the job report compares against it (LAB-PC-2, not LAB-PC)."""
    if agent_id:
        row = db.execute(
            "SELECT hostname FROM computers WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if row:
            return row["hostname"]
    return hostname


def upsert_computer_heartbeat(db, hostname, ip_address=None, agent_id=None):
    """Record a check-in; returns the registered name, which callers must
    use from then on. Without an agent_id the row is found by hostname
    alone; with one, the ID decides, adopting an ID-less row of that name."""
    if agent_id:
        return _upsert_identified_computer(db, hostname, ip_address, agent_id)

    existing = db.execute(
        "SELECT id FROM computers WHERE hostname = ?",
        (hostname,),
    ).fetchone()

    if existing:
        db.execute(
            """
            UPDATE computers
            SET ip_address = COALESCE(?, ip_address),
                last_seen = CURRENT_TIMESTAMP,
                status = 'online'
            WHERE hostname = ?
            """,
            (ip_address, hostname),
        )
    else:
        db.execute(
            """
            INSERT INTO computers (hostname, ip_address, last_seen, status)
            VALUES (?, ?, CURRENT_TIMESTAMP, 'online')
            """,
            (hostname, ip_address),
        )
        add_notification(
            db,
            "computer_status",
            f"New computer registered: {hostname}",
            severity="info",
            link_url=url_for("computers_page"),
            audience="admin",
        )
    return hostname


def _upsert_identified_computer(db, hostname, ip_address, agent_id):
    row = db.execute(
        "SELECT id, hostname, agent_hostname FROM computers WHERE agent_id = ?",
        (agent_id,),
    ).fetchone()

    if row is None:
        unclaimed = db.execute(
            "SELECT id FROM computers WHERE hostname = ? AND agent_id IS NULL",
            (hostname,),
        ).fetchone()
        if unclaimed:
            db.execute(
                """
                UPDATE computers
                SET agent_id = ?, agent_hostname = ?,
                    ip_address = COALESCE(?, ip_address),
                    last_seen = CURRENT_TIMESTAMP,
                    status = 'online'
                WHERE id = ?
                """,
                (agent_id, hostname, ip_address, unclaimed["id"]),
            )
            return hostname

        name = free_computer_name(db, hostname)
        db.execute(
            """
            INSERT INTO computers (hostname, agent_id, agent_hostname, ip_address, last_seen, status)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, 'online')
            """,
            (name, agent_id, hostname, ip_address),
        )
        message = f"New computer registered: {name}"
        if name != hostname:
            message += f" (it calls itself {hostname}, which another computer already uses)"
        add_notification(
            db,
            "computer_status",
            message,
            severity="info",
            link_url=url_for("computers_page"),
            audience="admin",
        )
        return name

    name = row["hostname"]
    if row["agent_hostname"] != hostname:
        # The machine was renamed in Windows. Its row, and every job and
        # report filed against it, follow the ID to the new name.
        new_name = free_computer_name(db, hostname, keep_id=row["id"])
        if new_name != name:
            rename_computer(db, name, new_name)
            add_notification(
                db,
                "computer_status",
                f"Computer renamed: {name} is now {new_name}",
                severity="info",
                link_url=url_for("computers_page"),
                audience="admin",
            )
            name = new_name

    db.execute(
        """
        UPDATE computers
        SET agent_hostname = ?,
            ip_address = COALESCE(?, ip_address),
            last_seen = CURRENT_TIMESTAMP,
            status = 'online'
        WHERE id = ?
        """,
        (hostname, ip_address, row["id"]),
    )
    return name


NOTIFICATION_RETENTION = 500
NOTIFICATION_MAX_AGE_DAYS = 14
ALLOWED_NOTIFICATION_SEVERITIES = {"info", "warning", "error"}
STALE_COMPUTER_SECONDS = 60
OFFLINE_NAMES_IN_MESSAGE = 5


def add_notification(db, event_type, message, severity="info", link_url=None, audience="admin"):
    """Insert a notification row. The CALLER is responsible for db.commit()."""
    db.execute(
        """
        INSERT INTO notifications (event_type, message, severity, link_url, audience)
        VALUES (?, ?, ?, ?, ?)
        """,
        (event_type, message, severity, link_url, audience),
    )
    db.execute(
        """
        DELETE FROM notifications
        WHERE id <= (SELECT MAX(id) FROM notifications) - ?
        """,
        (NOTIFICATION_RETENTION,),
    )
    db.execute(
        """
        DELETE FROM notifications
        WHERE created_at < DATETIME('now', ?)
        """,
        (f"-{NOTIFICATION_MAX_AGE_DAYS} days",),
    )


def maybe_notify_batch_complete(db, batch_id):
    """One notification when a multi-job batch's last job finishes."""
    if not batch_id:
        return
    remaining = db.execute(
        "SELECT COUNT(*) AS total FROM jobs WHERE batch_id = ? AND status IN ('pending', 'in_progress')",
        (batch_id,),
    ).fetchone()
    if remaining["total"] > 0:
        return
    # "removed" counts completed uninstalls except removal_unverified: the
    # uninstaller finished there, but nothing confirmed the software went.
    stats = db.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS succeeded,
               SUM(CASE WHEN action = ? THEN 1 ELSE 0 END) AS uninstalls,
               SUM(CASE WHEN status = 'completed' AND result_reason IS NOT ?
                        THEN 1 ELSE 0 END) AS removed
        FROM jobs WHERE batch_id = ?
        """,
        (JOB_ACTION_UNINSTALL, REMOVAL_UNCONFIRMED_REASON, batch_id),
    ).fetchone()
    total = stats["total"] or 0
    if total < 2:
        return
    if stats["uninstalls"] == total:
        done = stats["removed"] or 0
        message = f"Uninstall batch finished — {done} of {total} removed"
    else:
        done = stats["succeeded"] or 0
        message = f"Deployment batch finished — {done} of {total} succeeded"
    add_notification(
        db,
        "batch_done",
        message,
        severity="info" if done == total else "warning",
        link_url="/",
        audience="admin",
    )


def mark_stale_computers_offline(db):
    candidates = db.execute(
        """
        SELECT hostname
        FROM computers
        WHERE status = 'online'
          AND last_seen < DATETIME('now', ?)
        ORDER BY hostname
        """,
        (f"-{STALE_COMPUTER_SECONDS} seconds",),
    ).fetchall()

    if not candidates:
        db.commit()
        return

    hostnames = [row["hostname"] for row in candidates]

    placeholders = ",".join(["?"] * len(hostnames))
    cursor = db.execute(
        f"UPDATE computers SET status = 'offline' WHERE status = 'online' AND hostname IN ({placeholders})",
        hostnames,
    )

    if cursor.rowcount > 0:
        if len(hostnames) == 1:
            message = f"{hostnames[0]} went offline"
        elif len(hostnames) <= OFFLINE_NAMES_IN_MESSAGE:
            message = f"{len(hostnames)} computers went offline: {', '.join(hostnames)}"
        else:
            shown = ", ".join(hostnames[:OFFLINE_NAMES_IN_MESSAGE])
            remaining = len(hostnames) - OFFLINE_NAMES_IN_MESSAGE
            message = f"{len(hostnames)} computers went offline: {shown} and {remaining} more"

        add_notification(
            db,
            "computer_status",
            message,
            severity="warning",
            link_url=url_for("computers_page"),
            audience="admin",
        )

    db.commit()


# The agent's 30-min install ceiling plus slack.
STALE_JOB_MINUTES = 45
MAX_JOB_RETRIES = 2
# Uninstalls: the agent's ceiling is 10 minutes, so 20 is that plus slack.
STALE_UNINSTALL_JOB_MINUTES = 20
# Uninstalls are for online computers only, never queued for later. A pending
# one is cancelled (pickup_expired) once its PC has not been heard from for
# this long, or once it has waited this long whatever the PC is doing.
UNINSTALL_PICKUP_OFFLINE_MINUTES = 2
UNINSTALL_PICKUP_MAX_MINUTES = 45

# jobs.action is NULL for every install, so "action IS NULL" asks for
# installs. Deployment totals count installs and uninstalls alike.
JOB_ACTION_UNINSTALL = "uninstall"
# A completed uninstall that is not counted as removed: the uninstaller
# finished, but nothing confirmed the software went.
REMOVAL_UNCONFIRMED_REASON = "removal_unverified"
JOB_ACTION_LABELS = {None: "Install", JOB_ACTION_UNINSTALL: "Uninstall"}

# software_id for an uninstall whose software left the library: the
# column is NOT NULL (SQLite cannot relax that without a rebuild), and no
# software row can be 0 (AUTOINCREMENT starts at 1; triggers refuse 0).
NO_LIBRARY_SOFTWARE_ID = 0


@app.template_global()
def job_action_label(action):
    """"Install" or "Uninstall", for the jobs table and Job Details. A value
    nothing writes shows as nothing - never guessed to be an install."""
    return JOB_ACTION_LABELS.get(action, "")


@app.template_global()
def job_retryable(job):
    """Whether a job can be queued again; the one rule behind Retry. An
    install needs its software in the library; an uninstall needs its
    installations row still live, whatever the library holds."""
    if job["status"] not in ("failed", "cancelled"):
        return False
    if job["action"] is None:
        return not job["software_deleted"]
    if job["action"] == JOB_ACTION_UNINSTALL:
        return job["installation_state"] in LIVE_INSTALLATION_STATES
    return False


# Shared by both retry routes, so they judge a job identically.
RETRY_JOB_SELECT = """
    SELECT jobs.id, jobs.computer_hostname, jobs.software_id, jobs.status, jobs.action,
           jobs.installation_id, jobs.software_name, jobs.software_version,
           software.name AS library_name, software.version AS library_version,
           CASE WHEN software.id IS NULL THEN 1 ELSE 0 END AS software_deleted,
           installations.state AS installation_state
    FROM jobs
    LEFT JOIN software ON software.id = jobs.software_id
    LEFT JOIN installations ON installations.id = jobs.installation_id
"""


def requeue_job(db, job):
    """Queue a job again as a new pending row; the original stays in the
    history. Installs take the library's current name and version; an
    uninstall keeps its own snapshot."""
    if job["action"] is None:
        name, version = job["library_name"], job["library_version"]
    else:
        name, version = job["software_name"], job["software_version"]
    db.execute(
        "INSERT INTO jobs (computer_hostname, software_id, status, software_name, software_version, "
        "action, installation_id) VALUES (?, ?, 'pending', ?, ?, ?, ?)",
        (job["computer_hostname"], job["software_id"], name, version, job["action"], job["installation_id"]),
    )


# --- Why a job ended the way it did ------------------------------------------
#
# The agent sends a reason CODE; the words live only here, so no text from
# a lab PC reaches the dashboard. A reason that breaks its entry's status,
# exit-code or action rule is not stored: a wrong reason is worse than
# none. JOB_EXIT_NONE means no code is expected (one sent is dropped, the
# reason kept). "server" reasons are ignored from agents. An "uninstall"
# dict keeps an uninstall's label and note together.
# t/test_job_results.py keeps agent.py's codes in step with these.
JOB_EXIT_NONE = "none"
JOB_EXIT_NONZERO = "nonzero"
# Chocolatey's documented error is -1 or 1; on Windows -1 arrives unsigned.
CHOCOLATEY_ERROR_EXIT_CODES = (1, -1, 4294967295)
NO_AGENT_REPORT_REASON = "no_agent_report"
JOB_ACTIONS_INSTALL = (None,)
JOB_ACTIONS_UNINSTALL = (JOB_ACTION_UNINSTALL,)
JOB_ACTIONS_BOTH = (None, JOB_ACTION_UNINSTALL)

JOB_RESULT_REASONS = {
    "restart_needed": {
        "label": "Restart needed", "tone": "warn",
        "statuses": ("completed",), "exit": (3010,), "actions": JOB_ACTIONS_BOTH,
        "uninstall": {"label": "Restart needed to finish removing"},
    },
    "restart_started": {
        "label": "Restarting to finish", "tone": "warn",
        "statuses": ("completed",), "exit": (1641,), "actions": JOB_ACTIONS_BOTH,
        "uninstall": {"label": "Restarting to finish removing"},
    },
    # Worded as what Chocolatey CLAIMED: its records outlive the app. Reaching
    # the server means the agent's own check could not decide.
    "choco_already_installed": {
        "label": "Already installed per Chocolatey, nothing installed", "tone": "warn",
        "statuses": ("completed",), "exit": (0,), "actions": JOB_ACTIONS_INSTALL,
        "note": "The agent could not check whether Windows still lists it.",
    },
    # The next three follow a Chocolatey "already installed", once the agent
    # has checked the uninstall keys Chocolatey recorded against Windows.
    "choco_confirmed_installed": {
        "label": "Already installed, Windows still lists it", "tone": "info",
        "statuses": ("completed",), "exit": (0,), "actions": JOB_ACTIONS_INSTALL,
    },
    "choco_reinstalled": {
        "label": "Reinstalled, it had been removed outside Chocolatey", "tone": "info",
        "statuses": ("completed",), "exit": (0,), "actions": JOB_ACTIONS_INSTALL,
    },
    # "Does not confirm", not "does not list": after the reinstall the check
    # may find the keys gone OR be unable to decide, and the label has to be
    # true of both.
    "choco_reinstall_unconfirmed": {
        "label": "Reinstalled, but Windows does not confirm it", "tone": "warn",
        "statuses": ("completed",), "exit": (0,), "actions": JOB_ACTIONS_INSTALL,
        "note": "Chocolatey reported the reinstall finished. Checked again afterwards, "
                "Windows did not confirm the software is installed.",
    },
    "choco_package_not_found": {
        "label": "Package not found", "tone": "bad",
        "statuses": ("failed",), "exit": CHOCOLATEY_ERROR_EXIT_CODES, "actions": JOB_ACTIONS_INSTALL,
    },
    "choco_error": {
        "label": "Chocolatey reported an error", "tone": "bad",
        "statuses": ("failed",), "exit": CHOCOLATEY_ERROR_EXIT_CODES, "actions": JOB_ACTIONS_BOTH,
    },
    "choco_restart_pending": {
        "label": "Stopped by a pending restart", "tone": "bad",
        "statuses": ("failed",), "exit": (350,), "actions": JOB_ACTIONS_BOTH,
    },
    "choco_install_incomplete": {
        "label": "Install suspended before finishing", "tone": "bad",
        "statuses": ("failed",), "exit": (1604,), "actions": JOB_ACTIONS_INSTALL,
    },
    "msi_cancelled": {
        "label": "Install was cancelled", "tone": "bad",
        "statuses": ("failed",), "exit": (1602,), "actions": JOB_ACTIONS_BOTH,
        "uninstall": {"label": "Uninstall was cancelled"},
    },
    "msi_fatal": {
        "label": "Windows Installer fatal error", "tone": "bad",
        "statuses": ("failed",), "exit": (1603,), "actions": JOB_ACTIONS_BOTH,
    },
    # 1618 is any Windows Installer operation already in progress, which for
    # an uninstall is as likely to be another removal.
    "msi_busy": {
        "label": "Another install was already running", "tone": "bad",
        "statuses": ("failed",), "exit": (1618,), "actions": JOB_ACTIONS_BOTH,
        "uninstall": {"label": "Another install or uninstall was already running"},
    },
    "msi_unreadable": {
        "label": "Installer package could not be opened", "tone": "bad",
        "statuses": ("failed",), "exit": (1619,), "actions": JOB_ACTIONS_INSTALL,
    },
    "msi_policy": {
        "label": "Blocked by system policy", "tone": "bad",
        "statuses": ("failed",), "exit": (1625,), "actions": JOB_ACTIONS_BOTH,
    },
    "msi_platform": {
        "label": "Not supported on this Windows", "tone": "bad",
        "statuses": ("failed",), "exit": (1633,), "actions": JOB_ACTIONS_INSTALL,
    },
    "msi_other_version": {
        "label": "A different version is already installed", "tone": "bad",
        "statuses": ("failed",), "exit": (1638,), "actions": JOB_ACTIONS_INSTALL,
    },
    # An .exe's code, or a package's own code passed through Chocolatey:
    # nobody defines what it means, so the label claims nothing beyond the
    # number shown beside it. Info, not neutral, on success: neutral grey
    # text is 4.39:1 on a hovered table row in light mode.
    "installer_exit_code": {
        "label": "Installer exit code", "tone": {"completed": "info", "failed": "bad"},
        "statuses": ("completed", "failed"), "exit": JOB_EXIT_NONZERO, "actions": JOB_ACTIONS_BOTH,
        "uninstall": {"label": "Uninstaller exit code"},
    },
    # Worded as the agent GIVING UP, not as the install failing. On the
    # timeout the agent ends the installer's whole process tree when it can,
    # but an MSI's work in the Windows Installer service - or an install that
    # had to run without a job - can still finish afterwards.
    # Nobody observed it fail. The note says so wherever there is room for it.
    "timed_out": {
        "label": "Agent stopped waiting for the install", "tone": "bad",
        "statuses": ("failed",), "exit": JOB_EXIT_NONE, "actions": JOB_ACTIONS_BOTH,
        "note": "The install ran past the agent's time limit. It may still have finished on the PC.",
        "uninstall": {
            "label": "Agent stopped waiting for the uninstall",
            "note": "The uninstall ran past the agent's time limit. It may still have finished on the PC.",
        },
    },
    "download_failed": {
        "label": "Could not download the installer", "tone": "bad",
        "statuses": ("failed",), "exit": JOB_EXIT_NONE, "actions": JOB_ACTIONS_INSTALL,
    },
    "choco_missing": {
        "label": "Chocolatey is not installed on this PC", "tone": "bad",
        "statuses": ("failed",), "exit": JOB_EXIT_NONE, "actions": JOB_ACTIONS_BOTH,
    },
    "empty_command": {
        "label": "No install command to run", "tone": "bad",
        "statuses": ("failed",), "exit": JOB_EXIT_NONE, "actions": JOB_ACTIONS_INSTALL,
    },
    "launch_failed": {
        "label": "Could not start the installer", "tone": "bad",
        "statuses": ("failed",), "exit": JOB_EXIT_NONE, "actions": JOB_ACTIONS_BOTH,
        "uninstall": {"label": "Could not start the uninstaller"},
    },
    # Set by reap_stale_jobs(). All the server knows is that no report came:
    # the install may have hung, the PC may be off, the report may be lost.
    # So it says exactly that, and never "timed out".
    NO_AGENT_REPORT_REASON: {
        "label": "No report from the agent", "tone": "bad",
        "statuses": ("failed",), "exit": JOB_EXIT_NONE, "source": "server", "actions": JOB_ACTIONS_BOTH,
    },
    # --- Uninstalls only: the agent's verdict from Windows' list after the
    # uninstaller ran; an ordinary removal has no reason.
    #
    # Nothing was left to remove: gone before anything ran, after a "no
    # such product" code, or after a choco cleanup.
    "already_removed": {
        "label": "Already gone, nothing to remove", "tone": "info",
        "statuses": ("completed",), "exit": (None, 0, 1605, 1614), "actions": JOB_ACTIONS_UNINSTALL,
    },
    # Windows no longer lists it, and the uninstaller said 3010: removed, with
    # a restart to finish. (1641 needs no verdict of its own - the restart has
    # begun, and the re-run after it finds the software already gone.)
    "removed_restart_needed": {
        "label": "Removed; restart needed to finish", "tone": "warn",
        "statuses": ("completed",), "exit": (3010,), "actions": JOB_ACTIONS_UNINSTALL,
        "note": "Windows no longer lists it. Restart the PC to finish removing it.",
    },
    "removed_folder_left": {
        "label": "Removed; its folder was left behind", "tone": "warn",
        "statuses": ("completed",), "exit": (0,), "actions": JOB_ACTIONS_UNINSTALL,
        "note": "Windows no longer lists it, but its install folder is still on the PC.",
    },
    "still_listed": {
        "label": "Uninstaller finished, but Windows still lists it", "tone": "bad",
        "statuses": ("failed",), "exit": (0, 1605, 1614, 3010), "actions": JOB_ACTIONS_UNINSTALL,
        "note": "The uninstaller reported success, but the software was still in Windows' "
                "program list when the agent stopped checking.",
    },
    "removal_unverified": {
        "label": "Uninstaller finished, removal not confirmed", "tone": "warn",
        "statuses": ("completed",), "exit": (0, 1605, 1614, 3010), "actions": JOB_ACTIONS_UNINSTALL,
        "note": "The agent could not read Windows' program list afterwards to check.",
    },
    # Chocolatey has no lib\<id>, and Windows still lists a key its records
    # name - or nothing is left to check by. Usually found before anything
    # ran; exit 1 when it was Chocolatey's own "is not installed" (something
    # removed it after the agent looked).
    "choco_not_managed": {
        "label": "Chocolatey no longer manages it, left in place", "tone": "bad",
        "statuses": ("failed",), "exit": (None, 1), "actions": JOB_ACTIONS_UNINSTALL,
        "note": "Chocolatey has no record of the package, so AppEase did not touch it.",
    },
    # Another installed package depends on it - found by the agent before
    # anything ran, or refused by Chocolatey itself (exit 1). A wrapper such
    # as vlc is not one: the agent removes it first.
    "choco_shared_dependency": {
        "label": "Another Chocolatey package needs it, left in place", "tone": "bad",
        "statuses": ("failed",), "exit": (None, 1), "actions": JOB_ACTIONS_UNINSTALL,
        "note": "Another package installed through Chocolatey depends on it, so AppEase did not remove it.",
    },
    "choco_records_unreadable": {
        "label": "Chocolatey's records could not be read, so nothing was run", "tone": "bad",
        "statuses": ("failed",), "exit": JOB_EXIT_NONE, "actions": JOB_ACTIONS_UNINSTALL,
    },
    # A shared runtime sits beside the program's own entry, and the agent
    # cannot tell which entry is the program's, or it is not an MSI.
    # Nothing ran.
    "choco_own_entry_not_found": {
        "label": "Not removed: couldn't tell which entry is the program", "tone": "bad",
        "statuses": ("failed",), "exit": JOB_EXIT_NONE, "actions": JOB_ACTIONS_UNINSTALL,
        "note": "AppEase couldn't tell which uninstall entry belongs to this program, so nothing was removed.",
    },
    "choco_own_entry_not_msi": {
        "label": "Not removed: needs manual uninstall", "tone": "bad",
        "statuses": ("failed",), "exit": JOB_EXIT_NONE, "actions": JOB_ACTIONS_UNINSTALL,
        "note": "This program was installed together with a shared runtime and isn't an MSI program, so "
                "AppEase can't remove it safely. Remove it manually from the computer.",
    },
    # From the live key. The server refuses such rows earlier, from
    # install-time records; these mean the PC found one anyway.
    "uninstaller_unsafe_location": {
        "label": "Its uninstaller is outside Program Files, so nothing was run", "tone": "bad",
        "statuses": ("failed",), "exit": JOB_EXIT_NONE, "actions": JOB_ACTIONS_UNINSTALL,
    },
    "no_quiet_uninstall": {
        "label": "No quiet uninstall is listed for it any more, so nothing was run", "tone": "bad",
        "statuses": ("failed",), "exit": JOB_EXIT_NONE, "actions": JOB_ACTIONS_UNINSTALL,
    },
    # The per-user equivalent, from the live per-user key.
    "uninstaller_unsafe_user_location": {
        "label": "Its uninstaller is outside its account's AppData folder, so nothing was run", "tone": "bad",
        "statuses": ("failed",), "exit": JOB_EXIT_NONE, "actions": JOB_ACTIONS_UNINSTALL,
    },
    # The agent's own check, before anything runs. The server refuses these
    # rows earlier still; this is what a PC says when it finds one anyway.
    "shared_runtime": {
        "label": "Shared runtime, left in place", "tone": "bad",
        "statuses": ("failed",), "exit": JOB_EXIT_NONE, "actions": JOB_ACTIONS_UNINSTALL,
        "note": "Other software on the PC may depend on it, so AppEase never removes it.",
    },
    # The agent's own refusals, before anything runs, and its error. Nothing
    # was run for any of the first four, so they carry no exit code.
    "uninstall_job_invalid": {
        "label": "The uninstall job was not understood, so nothing was run", "tone": "bad",
        "statuses": ("failed",), "exit": JOB_EXIT_NONE, "actions": JOB_ACTIONS_UNINSTALL,
    },
    "identity_mismatch": {
        "label": "This PC lists it differently, so nothing was run", "tone": "bad",
        "statuses": ("failed",), "exit": JOB_EXIT_NONE, "actions": JOB_ACTIONS_UNINSTALL,
        "note": "Windows lists it differently from what AppEase recorded, so AppEase did not touch it.",
    },
    "registry_unreadable": {
        "label": "Windows' program list could not be read, so nothing was run", "tone": "bad",
        "statuses": ("failed",), "exit": JOB_EXIT_NONE, "actions": JOB_ACTIONS_UNINSTALL,
    },
    "protected_software": {
        "label": "Protected, left in place", "tone": "bad",
        "statuses": ("failed",), "exit": JOB_EXIT_NONE, "actions": JOB_ACTIONS_UNINSTALL,
        "note": "AppEase's own agent runs on it, so AppEase never removes it.",
    },
    "agent_error": {
        "label": "The agent hit an error during the uninstall", "tone": "bad",
        "statuses": ("failed",), "exit": JOB_EXIT_NONE, "actions": JOB_ACTIONS_UNINSTALL,
        "note": "It may or may not have run. The agent's log on the PC says what happened.",
    },
    # Set by the server on a pending uninstall it will not hand out. The job
    # is cancelled, not failed: nothing ran. If the agent had picked it up
    # before and gone quiet, Job Details also shows it was re-queued.
    "pickup_expired": {
        "label": "Computer went offline before the agent picked it up", "tone": "warn",
        "statuses": ("cancelled",), "exit": JOB_EXIT_NONE, "source": "server",
        "actions": JOB_ACTIONS_UNINSTALL,
        "note": "Uninstalls run only on computers that are online. Queue it again once this one is back.",
    },
    "agent_cannot_uninstall": {
        "label": "This computer's agent cannot uninstall yet", "tone": "warn",
        "statuses": ("cancelled",), "exit": JOB_EXIT_NONE, "source": "server",
        "actions": JOB_ACTIONS_UNINSTALL,
        "note": "Update the agent on this computer, then queue the uninstall again.",
    },
    # Set by the server when the row changed between queueing and pickup -
    # replaced by an upgrade from another library entry, which the reverse
    # guard cannot see because it matches by library entry.
    "no_longer_installed": {
        "label": "It was no longer installed when the agent asked for it", "tone": "warn",
        "statuses": ("cancelled",), "exit": JOB_EXIT_NONE, "source": "server",
        "actions": JOB_ACTIONS_UNINSTALL,
    },
}


def job_reason_entry(reason, action):
    """A reason's entry worded for one kind of job, or None.

    None when the reason is unknown or never accompanies that action - an
    install reason on an uninstall, or the reverse. An uninstall reads its
    own label and note, together, where the entry has them."""
    entry = JOB_RESULT_REASONS.get(reason) if isinstance(reason, str) else None
    if entry is None or action not in entry["actions"]:
        return None
    if action == JOB_ACTION_UNINSTALL and "uninstall" in entry:
        words = entry["uninstall"]
        return dict(entry, label=words["label"], note=words.get("note", ""))
    return entry


def clean_exit_code(value):
    """An exit code worth storing, or None.

    Accepts the unsigned range Windows really returns (a crash reads as
    3221225477) and the signed range, and nothing that is not a plain int -
    True is an int in Python and would otherwise store as 1.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < -(2 ** 31) or value > 2 ** 32 - 1:
        return None
    return value


def _reason_fits(entry, status, exit_code):
    if status not in entry["statuses"]:
        return False
    rule = entry["exit"]
    if rule == JOB_EXIT_NONE:
        return exit_code is None
    if rule == JOB_EXIT_NONZERO:
        return exit_code is not None and exit_code != 0
    return exit_code in rule


def clean_job_result(status, exit_code, reason, action):
    """(exit_code, reason) safe to store for an agent-reported status. Never
    rejects: a 400 would leave the job to be re-queued and installed again,
    so anything that does not fit is stored as NULL."""
    exit_code = clean_exit_code(exit_code)
    entry = job_reason_entry(reason, action)
    if entry is None or entry.get("source", "agent") != "agent":
        return exit_code, None
    if status not in entry["statuses"]:
        return exit_code, None
    if entry["exit"] == JOB_EXIT_NONE:
        # The code is what is wrong here, not the reason: a timed-out
        # install's 1 came from killing the shell, not from the installer.
        return None, reason
    if not _reason_fits(entry, status, exit_code):
        return exit_code, None
    return exit_code, reason


def format_exit_code(exit_code):
    """Exit code as shown: decimal, or hex once it is too large to read.

    Windows codes are unsigned, so Chocolatey's -1 arrives as 4294967295 and
    a crash as 3221225477; 0xFFFFFFFF and 0xC0000005 are how anyone would
    look those up.
    """
    if exit_code is None:
        return ""
    if exit_code >= 2 ** 31:
        return f"0x{exit_code:08X}"
    return str(exit_code)


@app.template_global()
def job_result(status, exit_code, reason, action):
    """{"label", "tone", "exit_code"} for a job's result cells, or None: the
    one place a reason becomes words. Unknown or misfit reasons show as
    nothing, never raw text or a guess."""
    entry = job_reason_entry(reason, action)
    if entry is not None and not _reason_fits(entry, status, exit_code):
        entry = None
    code = ""
    if exit_code is not None and (entry is not None or exit_code != 0):
        code = format_exit_code(exit_code)
    if entry is None and not code:
        return None
    tone = "neutral"
    if entry is not None:
        tone = entry["tone"]
        if isinstance(tone, dict):
            tone = tone.get(status, "neutral")
    return {
        "label": entry["label"] if entry is not None else "",
        "tone": tone,
        "exit_code": code,
        "note": entry.get("note", "") if entry is not None else "",
    }


# The table's status pill and the Details panel's, from one place.
JOB_STATUS_PILLS = {
    "completed": ("good", "Completed"),
    "in_progress": ("info", "In progress"),
    "pending": ("warn", "Pending"),
}


@app.template_global()
def job_status_pill(status):
    """(tone, label) for a job status; anything unlisted is bad, labelled
    by its own name."""
    return JOB_STATUS_PILLS.get(status, ("bad", (status or "").capitalize()))


NO_AGENT_REPORT_NOTE = (
    "No report arrived from the agent. This is when the server gave up, "
    "not when the PC finished."
)


@app.template_global()
def job_details(job):
    """Every string the Job Details panel shows; its script decides nothing.
    completed_at reads "Reported by the agent" (when the report arrived) or
    "Server stopped waiting" if reaped, never a "Finished at" nobody saw."""
    result = job_result(job["status"], job["exit_code"], job["result_reason"], job["action"])
    reaped = result is not None and job["result_reason"] == NO_AGENT_REPORT_REASON
    tone, label = job_status_pill(job["status"])
    retries = job["retry_count"] or 0
    return {
        "status-label": label,
        "status-tone": tone,
        "computer": job["computer_hostname"] or "",
        # The machine's IP now, not when the job ran - hence "Current IP".
        "ip": job["computer_ip"] or "",
        "software": software_label(job["software_name"], job["software_version"]),
        "action": job_action_label(job["action"]),
        "result-label": result["label"] if result else "",
        "result-tone": result["tone"] if result else "",
        "result-note": result["note"] if result else "",
        "exit": result["exit_code"] if result else "",
        "queued": manila_time(job["created_at"]) if job["created_at"] else "",
        "picked": manila_time(job["started_at"]) if job["started_at"] else "",
        "finished-label": "Server stopped waiting" if reaped else "Reported by the agent",
        "finished": manila_time(job["completed_at"]) if job["completed_at"] else "",
        "finished-note": NO_AGENT_REPORT_NOTE if reaped else "",
        # retry_count only ever goes up in reap_stale_jobs().
        "requeued": f"{retries} time{'' if retries == 1 else 's'}" if retries else "",
    }


def reap_stale_jobs(db):
    """Recover jobs stuck in_progress because an agent never reported back
    (VM froze/restarted/crashed). Under the retry cap -> reset to pending so
    the agent picks it up again; at/over the cap -> mark failed."""
    stale_jobs = db.execute(
        """
        SELECT id, retry_count, batch_id
        FROM jobs
        WHERE status = 'in_progress'
          AND started_at IS NOT NULL
          AND started_at < DATETIME('now', CASE WHEN action = ? THEN ? ELSE ? END)
        """,
        (JOB_ACTION_UNINSTALL, f"-{STALE_UNINSTALL_JOB_MINUTES} minutes", f"-{STALE_JOB_MINUTES} minutes"),
    ).fetchall()

    for job in stale_jobs:
        current_retries = job["retry_count"] or 0
        if current_retries < MAX_JOB_RETRIES:
            db.execute(
                """
                UPDATE jobs
                SET status = 'pending',
                    started_at = NULL,
                    retry_count = ?
                WHERE id = ?
                """,
                (current_retries + 1, job["id"]),
            )
        else:
            job_details = db.execute(
                """
                SELECT jobs.computer_hostname AS hostname, jobs.action,
                       COALESCE(jobs.software_name, software.name, 'Removed software') AS software_name
                FROM jobs
                LEFT JOIN software ON software.id = jobs.software_id
                WHERE jobs.id = ?
                """,
                (job["id"],),
            ).fetchone()

            db.execute(
                """
                UPDATE jobs
                SET status = 'failed',
                    completed_at = CURRENT_TIMESTAMP,
                    exit_code = NULL,
                    result_reason = ?
                WHERE id = ?
                """,
                (NO_AGENT_REPORT_REASON, job["id"]),
            )
            if job_details:
                # Not "timed out": the server only knows no report arrived.
                # An uninstall the agent started may well have finished.
                message = f"No report from the agent: {job_details['software_name']} on {job_details['hostname']}"
                if job_details["action"] == JOB_ACTION_UNINSTALL:
                    message = (f"No report from the agent: uninstall of {job_details['software_name']} "
                               f"on {job_details['hostname']} — it may already have run")
                add_notification(
                    db,
                    "job_failed",
                    message,
                    severity="error",
                    link_url="/",
                    audience="admin",
                )

            # After the failure that completes the batch, so the newest-first
            # dropdown shows the summary above it.
            maybe_notify_batch_complete(db, job["batch_id"])

    # After the loop above, so an uninstall it just re-queued for a silent
    # PC is cancelled too (Job Details still shows the re-queue, the sign it
    # may have run). created_at is the clock, so a re-queue buys no time.
    expired = db.execute(
        """
        SELECT jobs.id, jobs.batch_id
        FROM jobs
        LEFT JOIN computers ON computers.hostname = jobs.computer_hostname
        WHERE jobs.status = 'pending' AND jobs.action = ?
          AND (computers.last_seen IS NULL
               OR computers.last_seen < DATETIME('now', ?)
               OR jobs.created_at < DATETIME('now', ?))
        """,
        (JOB_ACTION_UNINSTALL, f"-{UNINSTALL_PICKUP_OFFLINE_MINUTES} minutes",
         f"-{UNINSTALL_PICKUP_MAX_MINUTES} minutes"),
    ).fetchall()
    for job in expired:
        db.execute(
            "UPDATE jobs SET status = 'cancelled', result_reason = 'pickup_expired' WHERE id = ?",
            (job["id"],),
        )
        maybe_notify_batch_complete(db, job["batch_id"])
    return len(stale_jobs)

with app.app_context():
    init_db()


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "admin_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "admin_id" not in session:
            return redirect(url_for("login"))
        if session.get("admin_role") != "admin":
            return redirect(url_for("problem_reports_page"))
        return f(*args, **kwargs)
    return decorated_function


# --- Public (tunnelled) host guard ------------------------------------
#
# AppEase was written as a LAN application and the code still assumes it.
# The agent API takes no credentials, /uploads serves installer files with
# no login, and Flask's static endpoint serves the whole static folder -
# which holds student problem photos and staff profile pictures next to the
# stylesheets. Every one of those is a reasonable call behind a campus
# firewall. None of them is a reasonable call on the open internet, and the
# Cloudflare tunnel put the app on the open internet.
#
# Two independent layers close it. cloudflared's ingress rules in config.yml
# refuse everything except /report at the EDGE, so a blocked request never
# reaches Python at all. This hook is the second layer, and it exists
# because ingress rules are configuration: configuration gets copied to a
# new machine half-edited, or widened for one afternoon of debugging and
# never narrowed again. Either layer alone closes the hole. Both would have
# to be wrong at the same time for anything to leak.
#
# A before_request hook rather than decorators, for the same reason the
# password gate below is one: the routes that need guarding most are
# precisely the ones carrying no decorator today, so a decorator-based
# scheme would have to be applied route by route and any route missed is a
# hole. One hook is one place to audit.
#
# Unset by default. With PUBLIC_REPORT_HOST empty this returns None on
# every request and the app behaves exactly as it did before, so a lab
# running with no tunnel is never asked to configure anything.
PUBLIC_REPORT_HOST = (os.getenv("PUBLIC_REPORT_HOST", "") or "").strip().lower()

# ENDPOINT names, not URL paths. request.endpoint is the rule Flask
# actually matched, so it cannot be talked around by "/report/../", a
# doubled slash, a trailing dot or percent-encoding - all of which a
# comparison against request.path would have to defend against by hand and
# would eventually lose to.
PUBLIC_ALLOWED_ENDPOINTS = {
    "student_problem_report_form",
    # Where the code-entry page posts. Without this the gate would be
    # reachable and its form would 404 on submit.
    "report_access",
    # Must be reachable or crawlers assume everything is permitted: a
    # 404 for robots.txt is read as "no rules", not as "stay out".
    "robots_txt",
}

# The static endpoint is allowed only under these prefixes. It is ONE
# endpoint serving the entire static folder, so allowing it wholesale would
# publish static/report_photos/ and static/avatars/ along with the CSS.
PUBLIC_ALLOWED_STATIC_PREFIXES = (
    "css/",
    "js/",
    "img/",
    "fonts/",
)

# A body cap for the public host only. MAX_CONTENT_LENGTH stays unset
# app-wide on purpose - admin installer uploads run to tens of megabytes -
# but nothing arriving from the internet has any business being that big.
# The real worst case is three photos at the 5 MB per-photo limit that
# save_report_photos already enforces, so 20 MB clears a legitimate
# submission comfortably while refusing anything absurd.
PUBLIC_MAX_CONTENT_BYTES = 20 * 1024 * 1024


def request_is_public_host():
    """True when this request arrived on the tunnelled public hostname.

    cloudflared forwards the original Host header to the origin, which is
    what makes this distinguishable at all - by the time a tunnelled request
    reaches Flask its remote address is always 127.0.0.1, so the IP tells us
    nothing.
    """
    if not PUBLIC_REPORT_HOST:
        return False
    # Split the port off before comparing. Host omits the port on 443 so the
    # tunnel never sends one, but a stray ":443" would otherwise silently
    # switch the entire guard off - and failing OPEN is the one direction
    # this must never fail in.
    host = (request.host or "").strip().lower()
    return bool(host) and host.split(":", 1)[0] == PUBLIC_REPORT_HOST


def public_host_not_found():
    """404 rather than 403 for anything blocked on the public host.

    A 403 answers the question "does /admins exist here?" with yes, which is
    exactly what someone probing the tunnel wants to know. A 404 says
    nothing, and is what an unrouted path would have returned anyway.
    """
    return ("Not Found", 404)


@app.before_request
def guard_public_host():
    if not request_is_public_host():
        return None

    # Size first, before any routing. Content-Length is absent on a chunked
    # request, in which case this check cannot fire - that is why it is a
    # second line of defence rather than the only one, with
    # save_report_photos' per-file seek check doing the real work.
    if request.content_length is not None and request.content_length > PUBLIC_MAX_CONTENT_BYTES:
        return ("Payload too large", 413)

    endpoint = request.endpoint or ""

    if endpoint == "static":
        filename = str((request.view_args or {}).get("filename", ""))
        # Normalise separators before matching. A backslash is a path
        # separator on the machine this runs on, so "css\\..\\avatars\\x.png"
        # must not be allowed to pass a startswith("css/") test.
        normalised = filename.replace("\\", "/").lstrip("/")
        if ".." in normalised.split("/"):
            return public_host_not_found()
        if normalised.startswith(PUBLIC_ALLOWED_STATIC_PREFIXES):
            return None
        return public_host_not_found()

    if endpoint in PUBLIC_ALLOWED_ENDPOINTS:
        return None

    # Covers unrouted paths too: endpoint is None when nothing matched, so
    # those fall through to the same 404 they would have got anyway.
    return public_host_not_found()


# Endpoints reachable while a password change is still pending. Everything
# else redirects.
#
# A before_request hook rather than a decorator: login_required and
# admin_required are not on every route - /login, /report and the agent API
# deliberately skip them - so a decorator-based gate would have to be added
# route by route, and any route missed is a hole. One hook is one place to
# get right and one place to audit.
#
# The agent endpoints are exempt because agents do not sign in as a person at
# all; without this, one admin with a pending password change would stop every
# lab machine polling for jobs.
PASSWORD_GATE_EXEMPT_ENDPOINTS = {
    "set_password_page",
    "set_password",
    "logout",
    "login",
    "static",
    "student_problem_report_form",
    "serve_uploaded_file",
    # Reached by someone who is not signed in at all, so the gate can never
    # apply - but listed rather than relied upon, since the gate is the one
    # place this project audits for holes.
    "forgot_password",
    "reset_password",
}


# Five minutes: notifications.js stops polling in a hidden tab, and a
# false "online" (a wasted walk) beats a false "offline" (a needless call).
ADMIN_ONLINE_SECONDS = 300

# The hook runs on every authenticated request (~8/min per tab); this
# stops it rewriting a value that is already true.
ADMIN_ACTIVITY_WRITE_SECONDS = 60


@app.before_request
def touch_admin_activity():
    """Record that the signed-in account is still here, in one UPDATE whose
    WHERE skips fresh values (no read-then-write race). Never breaks a
    request: it only drives a status dot."""
    if not session.get("admin_id"):
        return
    if request.endpoint in (None, "static"):
        return
    try:
        db = get_db()
        db.execute(
            "UPDATE admins SET last_active_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND (last_active_at IS NULL "
            "                  OR last_active_at < DATETIME('now', ?))",
            (session["admin_id"], f"-{ADMIN_ACTIVITY_WRITE_SECONDS} seconds"),
        )
        db.commit()
    except Exception:
        pass


@app.before_request
def require_password_change():
    if "admin_id" not in session:
        return None
    if not session.get("must_change_password"):
        return None
    endpoint = request.endpoint or ""
    # The agent endpoints are matched by PREFIX rather than listed: they are
    # all named agent_*, and a list would silently miss any added later.
    if endpoint in PASSWORD_GATE_EXEMPT_ENDPOINTS or endpoint.startswith("agent_"):
        return None
    # Read from the session, not the database: this runs on EVERY request, and
    # a query here would double the connection count of the whole app for a
    # flag that only ever changes on a path that rewrites the session anyway.
    return redirect(url_for("set_password_page"))


@app.route("/set-password", methods=["GET"])
@login_required
def set_password_page():
    if not session.get("must_change_password"):
        # Nothing pending - going here by hand should not offer to change a
        # password without asking for the current one, which is what the real
        # Change Password page is for.
        return redirect(url_for("settings"))
    return render_template(
        "set_password.html",
        error_message=request.args.get("error"),
        username=session.get("admin_username"),
    )


@app.route("/set-password", methods=["POST"])
@login_required
def set_password():
    if not session.get("must_change_password"):
        return redirect(url_for("settings"))

    # Not stripped, same as every other password path here.
    new_password = request.form.get("new_password") or ""
    confirm_password = request.form.get("confirm_password") or ""

    password_error = validate_password_strength(new_password)
    if password_error:
        return redirect(url_for("set_password_page", error=password_error))
    if new_password != confirm_password:
        return redirect(url_for("set_password_page", error="Passwords do not match."))

    db = get_db()
    admin = db.execute(
        "SELECT id, username, password_hash FROM admins WHERE id = ?",
        (session.get("admin_id"),),
    ).fetchone()
    if not admin:
        session.clear()
        return redirect(url_for("login"))

    # Reusing the temporary password would leave the emailed copy live
    # indefinitely, which is the one thing this whole flow exists to prevent.
    if check_password_hash(admin["password_hash"], new_password):
        return redirect(url_for("set_password_page", error="Choose a password different from the temporary one."))

    db.execute(
        "UPDATE admins SET password_hash = ?, must_change_password = 0 WHERE id = ?",
        (generate_password_hash(new_password), admin["id"]),
    )
    db.commit()
    session["must_change_password"] = False

    log_audit_event(
        actor_admin_id=admin["id"],
        target_admin_id=admin["id"],
        action_type="password_reset",
        details=f"Temporary password replaced by account '{admin['username']}'",
    )

    if session.get("admin_role") == "admin":
        return redirect(url_for("dashboard"))
    return redirect(url_for("problem_reports_page"))


# ----------------------------------------------------------------------
# Rate limits for sign-in and password-reset requests.
#
# In memory and wiped on restart, deliberately: a restart is the emergency
# reset for an account somebody has locked out on purpose, and nothing
# reachable from a lab PC can cause one. Waitress serves this app as
# THREADS in one process, so one limiter object really is shared by every
# request - which would stop being true if the server were ever switched
# to several processes.
#
# A separate helper from the /report limiter (report_rate_limit_error),
# on purpose for now. That one is left exactly as it is; this one is
# written so it could be moved onto later.
# ----------------------------------------------------------------------
class SlidingWindowLimiter:
    """At most max_hits per key in any rolling window_seconds.

    hit() checks the count and records the attempt in ONE lock
    acquisition. Checking under one acquisition and recording under
    another would let eight requests arriving together all read a count of
    four and all go through.

    A refused attempt is NOT recorded, and a key's list never grows past
    max_hits. Recording refusals would do two bad things: a flood against
    one key would grow its list without limit, and a person retrying
    during their own cooldown would keep pushing their unlock time back -
    which is not a cooldown that clears itself.

    Keys are stripped, lowercased and cut to max_key_length before use.

    The table holds at most max_keys keys. What happens when it is full is
    a security decision that differs per caller, so it is a parameter:
    EVICT drops the least recently hit key, REFUSE refuses any key not
    already in the table until room frees up.
    """

    EVICT = "evict"
    REFUSE = "refuse"

    def __init__(self, name, max_hits, window_seconds, max_key_length,
                 max_keys, when_full, clock=time.monotonic):
        if when_full not in (self.EVICT, self.REFUSE):
            raise ValueError(f"when_full must be 'evict' or 'refuse', not {when_full!r}")
        self.name = name
        self.max_hits = max_hits
        self.window_seconds = window_seconds
        self.max_key_length = max_key_length
        self.max_keys = max_keys
        self.when_full = when_full
        # monotonic, not time.time(): a wall clock corrected by the OS can
        # jump backwards and hold a lockout open for however far it moved.
        # An attribute rather than a direct call so a test can move time
        # forward instead of sleeping through a thirty-minute window.
        self.clock = clock
        self._lock = threading.Lock()
        # key -> its timestamps, oldest first. The dict itself is kept in
        # order of each key's NEWEST timestamp, which is what lets _prune()
        # stop at the first live key. Scanning every key on every request,
        # the way the /report limiter does, is fine at a few dozen keys and
        # about 20 ms under the lock at fifty thousand, measured.
        self._hits = OrderedDict()
        self._full = False

    def key_for(self, raw):
        return (raw or "").strip().lower()[:self.max_key_length]

    def _prune(self, now):
        """Drop keys whose newest hit has left the window. Caller holds the
        lock."""
        while self._hits:
            oldest_key = next(iter(self._hits))
            if now - self._hits[oldest_key][-1] < self.window_seconds:
                break
            del self._hits[oldest_key]

    def _hit_locked(self, key, now):
        stamps = self._hits.get(key)
        if stamps is not None:
            # Still here after _prune(), so its newest hit is live - but
            # the older ones in the list may not be.
            live = [seen for seen in stamps if now - seen < self.window_seconds]
            if len(live) >= self.max_hits:
                # Stored trimmed but NOT moved: the newest timestamp did
                # not change, so neither does the key's place in the order.
                self._hits[key] = live
                return False
            live.append(now)
            self._hits[key] = live
            self._hits.move_to_end(key)
            return True
        if len(self._hits) >= self.max_keys:
            if self.when_full == self.REFUSE:
                return False
            self._hits.popitem(last=False)
        self._hits[key] = [now]
        return True

    def _full_transition(self):
        """Returns "full", "drained", or None when nothing changed. Caller
        holds the lock.

        Drained means back down to HALF, not merely one under the ceiling.
        Under a flood the table sits right at the edge - one key expires,
        the next request takes its slot - and a single threshold would flip
        on nearly every request, which is the log flood this exists to
        prevent.
        """
        size = len(self._hits)
        if not self._full and size >= self.max_keys:
            self._full = True
            return "full"
        if self._full and size <= self.max_keys // 2:
            self._full = False
            return "drained"
        return None

    def hit(self, raw):
        """True when this attempt may go ahead and has been counted."""
        key = self.key_for(raw)
        now = self.clock()
        with self._lock:
            self._prune(now)
            allowed = self._hit_locked(key, now)
            transition = self._full_transition()
        # One line per change of state, never per request, and written
        # after the lock is released. Server log only - this is an
        # operational signal, not something an admin did, so it has no
        # business in audit_log.
        if transition == "full":
            if self.when_full == self.REFUSE:
                consequence = "keys not already tracked are now REFUSED"
            else:
                consequence = "the least recently used keys are now evicted"
            app.logger.warning(
                f"[rate limit] {self.name}: table full at {self.max_keys} keys; "
                f"{consequence} until it drains to {self.max_keys // 2}"
            )
        elif transition == "drained":
            app.logger.warning(
                f"[rate limit] {self.name}: table drained to {self.max_keys // 2} "
                f"keys or fewer; normal limiting resumed"
            )
        return allowed

    def clear(self, raw):
        """Forget a key entirely."""
        key = self.key_for(raw)
        with self._lock:
            self._hits.pop(key, None)


# Ten failed sign-ins per username in any thirty minutes. Keyed on the
# username and NOT the address: twenty lab PCs reach this server through
# one NAT'd address, so limiting by IP would lock out a whole room at once.
#
# Ten, not five: five is harsh on somebody who genuinely cannot remember
# which password they used here. The window doubled with the count, so the
# sustained rate a guesser gets is unchanged - twenty an hour per username
# either way. What changed is that one burst locks an account for thirty
# minutes rather than fifteen.
LOGIN_MAX_FAILURES = 10
LOGIN_WINDOW_SECONDS = 30 * 60
# The username rule allows 30 characters. The headroom is for any account
# created before that rule existed; anything longer cannot be a username.
LOGIN_KEY_MAX_LENGTH = 64
# Every login attempt, refused or not, costs a full password check (about
# 130 ms, measured), and that alone slows a flood. ESTIMATED, not measured:
# 8 Waitress threads at 130 ms each is about 60 attempts a second, roughly
# 110,000 distinct usernames per thirty-minute window - faster hardware
# raises that. The ceiling is well under that, so a flood CAN push one
# account out of the table: it takes about 14 minutes with the server
# fully busy, which shortens that account's cooldown to about half. That
# roughly doubles guesses against one username, from 20 to about 40 an
# hour, and only while every thread is pinned - agents included. Memory at
# the ceiling is about 22 MB, measured.
LOGIN_MAX_TRACKED_KEYS = 50000

# Five reset requests per email address in any thirty minutes. Not three:
# asking, finding it in spam, asking again, and then finding the first link
# already expired (RESET_TOKEN_MINUTES) is three ordinary actions, and the
# cap must not be spent by them. Five still holds an inbox to ten an hour.
FORGOT_PASSWORD_MAX_REQUESTS = 5
FORGOT_PASSWORD_WINDOW_SECONDS = 30 * 60
# The longest an email address can legally be.
FORGOT_PASSWORD_KEY_MAX_LENGTH = 254
# About 5 MB at worst, measured.
FORGOT_PASSWORD_MAX_TRACKED_KEYS = 10000

# EVICT on login. REFUSE would lock out every username not already in the
# table - including the administrator's - for the rest of the window, just
# because somebody filled it.
LOGIN_LIMITER = SlidingWindowLimiter(
    "login", LOGIN_MAX_FAILURES, LOGIN_WINDOW_SECONDS,
    LOGIN_KEY_MAX_LENGTH, LOGIN_MAX_TRACKED_KEYS, SlidingWindowLimiter.EVICT,
)
# REFUSE on forgot-password. A request here costs about a millisecond, so
# filling the table takes seconds, and eviction would let somebody flush one
# victim's counter and go on filling their inbox. The cost of refusing is
# that genuine resets quietly stop sending during a flood - which is why a
# full table writes a line to the server log.
FORGOT_PASSWORD_LIMITER = SlidingWindowLimiter(
    "forgot-password", FORGOT_PASSWORD_MAX_REQUESTS, FORGOT_PASSWORD_WINDOW_SECONDS,
    FORGOT_PASSWORD_KEY_MAX_LENGTH, FORGOT_PASSWORD_MAX_TRACKED_KEYS,
    SlidingWindowLimiter.REFUSE,
)

# What a sign-in for a username that does not exist is checked against.
#
# Without it that path skipped the password check entirely and answered in
# about 1 ms, against about 130 ms for a real account with a wrong password
# - so anyone timing the login page could tell which usernames exist, even
# though the page itself was identical.
#
# Made by the same call that hashes real passwords, so it carries the same
# method and cost parameters and costs the same to check. The password
# behind it is random and never kept, so nothing can match it.
DUMMY_PASSWORD_HASH = generate_password_hash(secrets.token_urlsafe(32))


# ----------------------------------------------------------------------
# Password reset by email.
#
# Fifteen minutes, single use, and the token is only ever stored hashed -
# the emailed link is the sole copy, so a stolen database yields nothing
# usable. Same reasoning as password_hash.
# ----------------------------------------------------------------------
RESET_TOKEN_MINUTES = 15


def _hash_reset_token(token):
    """sha256, not generate_password_hash.

    The token is 32 bytes of secrets.token_urlsafe - already full entropy -
    so the slow salted hash password_hash needs (to survive a weak human
    password) buys nothing here, and a salted hash cannot be looked up by
    value at all: verifying one would mean scanning every outstanding row.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _consume_reset_token(db, token):
    """Return the admin row for a VALID token, or None.

    Valid means: it exists, it has not been used, and it has not expired.
    Nothing is written here - the caller marks it used only once the new
    password is actually stored, so a GET of the link does not spend it.
    """
    if not token:
        return None
    row = db.execute(
        "SELECT id, admin_id, expires_at, used_at FROM password_resets WHERE token_hash = ?",
        (_hash_reset_token(token),),
    ).fetchone()
    if not row or row["used_at"]:
        return None
    try:
        expires = datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None
    if datetime.utcnow() > expires:
        return None
    admin = db.execute(
        "SELECT id, username, email FROM admins WHERE id = ?", (row["admin_id"],)
    ).fetchone()
    if not admin:
        return None
    return {"reset_id": row["id"], "admin": admin}


def password_reset_email_body(name, link):
    # The only email with a link. It is the address the request came in on -
    # on the lab network, the server's IP - which is a spam signal; kept on
    # purpose.
    return render_email(
        "Reset your password",
        f"Hello {name}, someone asked to reset the password for your AppEase account. "
        f"Use the link below within {RESET_TOKEN_MINUTES} minutes to choose a new one.",
        link=("Choose a new password", link),
        closing=["The link works once and then stops working. If you did not ask for "
                 "this, you can ignore this email - your password has not changed."],
    )


# Deliberately identical whether or not the address matched an account.
# Saying "no such email" would turn this form into a way to discover which
# addresses have accounts, which is worth more to an attacker than the reset
# itself.
#
# The response time does not depend on the address either: the token and
# email are handled in the background, so every request does the same work.
RESET_REQUESTED_MESSAGE = (
    "We've sent a reset link to the email provided (if an account exists). "
    "Check your spam folder if it doesn't arrive shortly."
)


def _issue_password_reset(admin, token, link):
    """The background half of forgot-password: spend any outstanding token,
    store this one, and email the link. admin is None when the address
    matched nothing or the limiter said no; the job then does nothing, so
    the request does the same work either way. Never raises."""
    if admin is None:
        return
    try:
        db = get_db()
        try:
            now = datetime.utcnow()
            # Any outstanding token for this account is spent first, so asking
            # twice does not leave two working links behind.
            db.execute(
                "UPDATE password_resets SET used_at = ? WHERE admin_id = ? AND used_at IS NULL",
                (now.strftime("%Y-%m-%d %H:%M:%S"), admin["id"]),
            )
            db.execute(
                "INSERT INTO password_resets (admin_id, token_hash, expires_at) VALUES (?, ?, ?)",
                (admin["id"], _hash_reset_token(token),
                 (now + timedelta(minutes=RESET_TOKEN_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")),
            )
            db.commit()
        finally:
            db.close()
        _send_capped(admin["email"], "Reset your AppEase password",
                     password_reset_email_body(greeting_name(admin.get("first_name"), admin["username"]), link))
    except Exception as exc:
        app.logger.warning(f"Password reset for {email_domain(admin.get('email'))} not issued: "
                           f"{exc.__class__.__name__}")


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if "admin_id" in session:
        return redirect(url_for("dashboard"))

    if request.method != "POST":
        return render_template("forgot_password.html")

    email = (request.form.get("email") or "").strip()
    if not email:
        return render_template(
            "forgot_password.html", error="Enter the email address on your account."
        )

    # Counted on EVERY submission, before the lookup and whether or not the
    # address matches anything. A limited submission still returns the same
    # page below; it simply sends nothing.
    allowed = FORGOT_PASSWORD_LIMITER.hit(email)

    db = get_db()
    # COLLATE NOCASE: people type their own address with whatever
    # capitalisation they feel like, and it is stored as they first gave it.
    admin = db.execute(
        "SELECT id, username, first_name, email FROM admins WHERE email IS NOT NULL "
        "AND email <> '' AND email = ? COLLATE NOCASE",
        (email,),
    ).fetchone()

    # The token is written and the email sent in the background, and the
    # request does the same work whatever the address: the limiter above,
    # the lookup, one token, one background job. It used to write the token
    # and then wait on the mail server here - about 950 ms for a match
    # against 1 ms for no match - so timing the form told anyone which
    # addresses had accounts. The job does nothing for no match.
    token = secrets.token_urlsafe(32)
    link = url_for("reset_password", token=token, _external=True)
    target = dict(admin) if (admin and allowed) else None
    run_in_background(lambda: _issue_password_reset(target, token, link))

    # Same PAGE either way - matched, unmatched or rate-limited - and never
    # branched on whether the send succeeded, which the request no longer
    # knows anyway.
    return render_template("forgot_password.html", sent_message=RESET_REQUESTED_MESSAGE)


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    db = get_db()
    found = _consume_reset_token(db, token)
    if not found:
        # One message for expired, already used and never existed. Telling
        # them apart would confirm that a token was once real.
        return render_template(
            "reset_password.html",
            invalid="This reset link has expired or has already been used. Request a new one.",
        )

    if request.method != "POST":
        return render_template("reset_password.html", token=token)

    password = request.form.get("password") or ""
    confirm = request.form.get("confirm_password") or ""
    strength_error = validate_password_strength(password)
    if strength_error:
        return render_template("reset_password.html", token=token, error=strength_error)
    if password != confirm:
        return render_template(
            "reset_password.html", token=token, error="The two passwords do not match."
        )

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        "UPDATE admins SET password_hash = ?, must_change_password = 0 WHERE id = ?",
        (generate_password_hash(password), found["admin"]["id"]),
    )
    # Marked used only now, so opening the link without finishing does not
    # burn it. Every other outstanding token for the account goes too.
    db.execute(
        "UPDATE password_resets SET used_at = ? WHERE admin_id = ? AND used_at IS NULL",
        (now, found["admin"]["id"]),
    )
    db.commit()
    # A completed reset clears the sign-in cooldown too, so the new password
    # works straight away - the cooldown message tells people exactly that.
    # Only here, once the new hash is stored: opening the link, a rejected
    # password and a dead token all return above without touching it.
    # Finishing a reset proves control of the account's email, a stronger
    # claim than knowing its password, and the token is 32 random bytes that
    # last RESET_TOKEN_MINUTES and work once.
    LOGIN_LIMITER.clear(found["admin"]["username"])
    log_audit_event(
        actor_admin_id=found["admin"]["id"],
        target_admin_id=found["admin"]["id"],
        action_type="password_reset",
        details=f"'{found['admin']['username']}' reset their own password via email link",
    )
    return render_template(
        "reset_password.html",
        done="Your password has been changed. You can sign in with it now.",
    )


# What a sign-in refused by the cooldown is told. It used to get the
# ordinary invalid-credentials error, which told somebody who had just
# remembered the right password that it was wrong - and sent them off to
# reset a password that was fine.
#
# The real duration and the way out, and no countdown: an exact time hands
# over the window length and buys the user nothing over "half an hour".
# The way out is real because reset_password() clears the cooldown.
LOGIN_COOLDOWN_MESSAGE = (
    "Too many sign-in attempts. Try again in about half an hour, "
    "or reset your password by email to sign in now."
)


@app.route("/login", methods=["GET", "POST"])
def login():
    if "admin_id" in session:
        if session.get("admin_role") == "faculty":
            return redirect(url_for("problem_reports_page"))
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        # Counted BEFORE the password is checked, in the same lock that reads
        # the count. The check takes about 130 ms and cannot run under the
        # lock without making every sign-in in the app wait in line, so the
        # attempt is reserved first and a success clears it again below - the
        # net effect is that only failures count. Usernames that do not exist
        # are counted exactly the same way.
        allowed = LOGIN_LIMITER.hit(username)

        db = get_db()
        admin = db.execute(
            "SELECT id, username, password_hash, role, first_name, middle_initial, last_name, "
            "must_change_password FROM admins WHERE username = ?",
            (username,),
        ).fetchone()

        # Exactly ONE password check on every path: real account or not,
        # limited or not, no exceptions. A cooldown refusal says so openly
        # now, so this is no longer about hiding the refusal. It is about a
        # refusal costing the same whether or not the username exists: a
        # path that skipped the check only where there is no account would
        # answer faster, and the speed alone would say "this account
        # exists". One check everywhere is the rule that cannot be
        # half-implemented.
        password_matches = check_password_hash(
            admin["password_hash"] if admin else DUMMY_PASSWORD_HASH, password
        )

        if allowed and admin and password_matches:
            LOGIN_LIMITER.clear(username)
            # UTC, like every stored timestamp; the manila filter converts.
            db.execute(
                "UPDATE admins SET last_login = CURRENT_TIMESTAMP WHERE id = ?",
                (admin["id"],),
            )
            db.commit()
            session["admin_id"] = admin["id"]
            session["admin_username"] = admin["username"]
            session["admin_role"] = admin["role"]
            session["admin_full_name"] = format_full_name(
                admin["first_name"], admin["middle_initial"], admin["last_name"]
            )
            # Carried in the session so the before_request gate does not have
            # to query on every single request. Written here and cleared only
            # by set_password(), the one path that can satisfy it.
            session["must_change_password"] = bool(admin["must_change_password"])
            if session["must_change_password"]:
                return redirect(url_for("set_password_page"))
            if admin["role"] == "faculty":
                return redirect(url_for("problem_reports_page"))
            return redirect(url_for("dashboard"))

        # A username in cooldown gets its own message, decided by `allowed`
        # ALONE. Two things this must never consult:
        #   - admin: a cooldown message only for real accounts is username
        #     enumeration again, through the wording this time.
        #   - password_matches: refused attempts are not recorded, so a
        #     message that differed when the password was right would let
        #     anyone keep guessing through the cooldown and be told when a
        #     guess was right.
        # Failures against made-up usernames are counted exactly like real
        # ones, so this page is identical for both. What it does give away
        # is that a name is in cooldown right now - to anyone, for any name,
        # real or not.
        if not allowed:
            return render_template("login.html", error=LOGIN_COOLDOWN_MESSAGE)

        # One response for a wrong password and a username that does not
        # exist. Telling those apart would say which usernames are real.
        return render_template("login.html", error="Invalid username or password")

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/admins", methods=["GET", "POST"])
@admin_required
def manage_admins():
    db = get_db()

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        first_name = (request.form.get("first_name") or "").strip()
        middle_initial = (request.form.get("middle_initial") or "").strip()
        last_name = (request.form.get("last_name") or "").strip()
        role = request.form.get("role", "admin").strip().lower()
        email = (request.form.get("email") or "").strip()
        if role not in {"admin", "faculty"}:
            role = "admin"

        username_error = validate_username(username)
        if username_error:
            return redirect(url_for("manage_admins", add="1", error=username_error))
        if not first_name:
            return redirect(url_for("manage_admins", add="1", error="First name is required"))
        if not last_name:
            return redirect(url_for("manage_admins", add="1", error="Last name is required"))
        if middle_initial and not re.fullmatch(r"[A-Za-z]{1,2}", middle_initial):
            return redirect(url_for("manage_admins", add="1", error="Middle initial must be 1 or 2 letters."))
        # Validated like every other path that accepts an address.
        email_error = validate_admin_email(email)
        if email_error:
            return redirect(url_for("manage_admins", add="1", error=email_error))

        existing_admin = db.execute(
            "SELECT id FROM admins WHERE username = ?",
            (username,),
        ).fetchone()
        if existing_admin:
            return redirect(url_for("manage_admins", add="1", error="Username already exists"))

        # The creating admin no longer types the new account's password. It is
        # generated here for the same reason a reset is: a password one person
        # picks for another tends to be guessable, gets reused across accounts,
        # and leaves the picker knowing a working credential for someone else's
        # account. must_change_password is set, so whatever is issued below
        # lasts exactly until the person's first sign-in.
        temporary_password = generate_temporary_password()
        cursor = db.execute(
            "INSERT INTO admins (username, password_hash, role, first_name, middle_initial, last_name,"
            " email, must_change_password, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)",
            (username, generate_password_hash(temporary_password), role, first_name, middle_initial,
             last_name, email or None),
        )
        db.commit()
        new_admin_id = cursor.lastrowid

        # Same ordering rule as the reset route: the account EXISTS before the
        # email is attempted, and a send failure is reported as a send failure
        # rather than as a failed creation. Creating it twice would fail on the
        # unique username anyway, leaving an account nobody has the password
        # to. Waits at most EMAIL_CONFIRM_SECONDS: past that, the password is
        # shown on screen as if the send had failed.
        sent, send_error = send_email_within(
            email,
            "Your new AppEase account",
            new_account_email_body(
                username, temporary_password, role_label(role),
                session.get("admin_username") or "an administrator",
            ),
        )

        log_audit_event(
            actor_admin_id=session.get("admin_id"),
            target_admin_id=new_admin_id,
            action_type="account_created",
            details=(
                f"Created account '{username}' ({role_label(role)}) - credentials emailed"
                if sent else
                f"Created account '{username}' ({role_label(role)}) - credentials not emailed"
            ),
        )

        if sent:
            return redirect(url_for(
                "manage_admins",
                success=f"Account created. Sign-in details were emailed to {email}.",
            ))
        session["issued_id"] = hold_issued_password({
            "for": username, "password": temporary_password, "note": send_error or "", "new": True,
        })
        return redirect(url_for("manage_admins"))

    # created_at feeds Date Created; accounts older than its migration hold
    # NULL (ADD COLUMN takes only a constant default) and show "-".
    admins = db.execute(
        "SELECT id, username, role, first_name, middle_initial, last_name, created_at,"
        " email, must_change_password, avatar_path, last_active_at"
        " FROM admins ORDER BY id ASC"
    ).fetchall()
    # Popped from the session and from the store together: shown on this one
    # response and never again. Query parameters are no longer read for it,
    # so a crafted link cannot put a fake password dialog on this page.
    issued = take_issued_password(session.pop("issued_id", None)) or {}
    page = render_template(
        "admins.html",
        admins=with_online_status(admins),
        error_message=request.args.get("error"),
        success_message=request.args.get("success"),
        # Only set when a password could not be emailed. Shown once, on this
        # one response, and never recoverable afterwards - only the hash is
        # stored.
        issued_for=issued.get("for"),
        issued_password=issued.get("password"),
        issued_note=issued.get("note"),
        # Distinguishes "a new account's first password" from "a reset", which
        # need different wording on the same panel.
        issued_new="1" if issued.get("new") else None,
        email_configured=bool(SMTP_HOST),
        # For the Send test email button: it emails only your own address.
        own_email_on_file=any(row["id"] == session.get("admin_id") and row["email"] for row in admins),
    )
    response = app.make_response(page)
    if issued:
        # Not kept by the browser either, so Back cannot bring it back.
        response.headers["Cache-Control"] = "no-store"
    return response


# Caps how often an admin can make the server send mail.
TEST_EMAIL_LIMITER = SlidingWindowLimiter("test-email", 1, 60, 32, 256, SlidingWindowLimiter.EVICT)


def test_email_body(name):
    return render_email(
        "Test email",
        f"Hello {name}, this is a test email from AppEase. "
        "If you are reading it, the server can send email.",
        closing=["Nothing needs doing."],
    )


@app.route("/admins/test-email", methods=["POST"])
@admin_required
def send_test_email():
    """Email the signed-in admin's OWN address, to prove mail works. There is
    no recipient field, so this cannot be pointed anywhere else."""
    if not SMTP_HOST:
        return redirect(url_for("manage_admins", error="Email is not configured on this server."))
    me = get_db().execute(
        "SELECT username, first_name, email FROM admins WHERE id = ?", (session.get("admin_id"),)
    ).fetchone()
    if not me or not me["email"]:
        return redirect(url_for("manage_admins", error="Your account has no email address. Add one in Settings first."))
    if not TEST_EMAIL_LIMITER.hit(str(session.get("admin_id"))):
        return redirect(url_for("manage_admins", error="A test email was sent less than a minute ago. Try again shortly."))
    sent, send_error = send_email_within(me["email"], "AppEase test email",
                                         test_email_body(greeting_name(me["first_name"], me["username"])))
    if sent:
        return redirect(url_for("manage_admins", success=f"Test email sent to {me['email']}."))
    return redirect(url_for("manage_admins", error=f"Test email not sent. {send_error}"))


@app.route("/admins/<int:admin_id>/reset-password", methods=["POST"])
@admin_required
def reset_admin_password(admin_id):
    """Issue a generated temporary password and email it, forcing a change at
    next sign-in.

    ORDER MATTERS. The password change is committed BEFORE the email is
    attempted, and the two outcomes are reported differently. If the send were
    inside the same try/except as the change, a mail failure would read as a
    failed reset - and the admin would reset again, generating a second
    password and silently invalidating the one already on its way.
    """
    if admin_id == session.get("admin_id"):
        return redirect(url_for(
            "manage_admins",
            error="Use Change Password to set your own password.",
        ))

    db = get_db()
    target = db.execute(
        "SELECT id, username, email FROM admins WHERE id = ?", (admin_id,)
    ).fetchone()
    if not target:
        return redirect(url_for("manage_admins", error="Account not found."))

    temporary_password = generate_temporary_password()
    db.execute(
        "UPDATE admins SET password_hash = ?, must_change_password = 1 WHERE id = ?",
        (generate_password_hash(temporary_password), admin_id),
    )
    db.commit()

    # At most EMAIL_CONFIRM_SECONDS, as for a new account.
    sent, send_error = send_email_within(
        target["email"],
        "Your AppEase password was reset",
        temporary_password_email_body(
            target["username"], temporary_password, session.get("admin_username") or "an administrator"
        ),
    )

    # The password itself is NEVER written to the audit log. The log records
    # that a reset happened and whether it was delivered; the password exists
    # only in the hash and, briefly, in the message or on the admin's screen.
    log_audit_event(
        actor_admin_id=session.get("admin_id"),
        target_admin_id=target["id"],
        action_type="password_reset",
        details=(
            f"Temporary password issued for '{target['username']}' and emailed"
            if sent else
            f"Temporary password issued for '{target['username']}' - not emailed"
        ),
    )

    if sent:
        return redirect(url_for(
            "manage_admins",
            success=f"Temporary password emailed to {target['username']}. They must change it at next sign-in.",
        ))

    # Shown once and never recoverable: only the hash is stored, so there is
    # nothing to look up later. Said plainly so nobody navigates away expecting
    # to find it again.
    session["issued_id"] = hold_issued_password({
        "for": target["username"], "password": temporary_password, "note": send_error or "", "new": False,
    })
    return redirect(url_for("manage_admins"))


@app.route("/admins/<int:admin_id>/email", methods=["POST"])
@admin_required
def update_admin_email(admin_id):
    email = (request.form.get("email") or "").strip()
    # One validator for every path that accepts an address. Blank clears it.
    email_error = validate_admin_email(email)
    if email_error:
        return redirect(url_for("manage_admins", error=email_error))

    db = get_db()
    target = db.execute("SELECT id, username FROM admins WHERE id = ?", (admin_id,)).fetchone()
    if not target:
        return redirect(url_for("manage_admins", error="Account not found."))

    db.execute("UPDATE admins SET email = ? WHERE id = ?", (email or None, admin_id))
    db.commit()

    log_audit_event(
        actor_admin_id=session.get("admin_id"),
        target_admin_id=target["id"],
        action_type="account_updated",
        details=(f"Email set for '{target['username']}'" if email
                 else f"Email cleared for '{target['username']}'"),
    )
    return redirect(url_for("manage_admins", success="Email address saved."))


EMAIL_INCOMPLETE_MESSAGE = (
    "Enter the full email address, including the part after the dot "
    "(for example name@gmail.com)."
)


def validate_admin_email(email):
    """An optional address's error message, or None. Lax on the local part
    (real addresses vary), strict on the domain, where a truncated "jols@g"
    hides: blank shows the password, but a typo silently loses a reset."""
    if not email:
        return None
    if len(email) > 120 or " " in email or email.count("@") != 1:
        return "That does not look like an email address."
    local, _, domain = email.partition("@")
    if not local or not domain:
        return "That does not look like an email address."
    labels = domain.split(".")
    if len(labels) < 2 or not all(labels):
        return EMAIL_INCOMPLETE_MESSAGE
    if not re.fullmatch(r"[A-Za-z]{2,}", labels[-1]):
        return EMAIL_INCOMPLETE_MESSAGE
    return None


@app.route("/admins/<int:admin_id>/edit", methods=["POST"])
@admin_required
def update_admin_fields(admin_id):
    """Set role and email from the accounts drawer; nothing else of another
    account is editable here. Demoting the LAST admin is refused, as
    deleting it is: nobody could administer the system."""
    db = get_db()
    target = db.execute(
        "SELECT id, username, role, email FROM admins WHERE id = ?", (admin_id,)
    ).fetchone()
    if not target:
        return redirect(url_for("manage_admins", error="Account not found."))

    role = (request.form.get("role") or "").strip().lower()
    if role not in {"admin", "faculty"}:
        return redirect(url_for("manage_admins", error="Invalid role selected."))

    email = (request.form.get("email") or "").strip()
    email_error = validate_admin_email(email)
    if email_error:
        return redirect(url_for("manage_admins", error=email_error))

    if target["role"] == "admin" and role != "admin":
        remaining = db.execute(
            "SELECT COUNT(*) AS total FROM admins WHERE role = 'admin' AND id != ?",
            (admin_id,),
        ).fetchone()
        if not remaining or remaining["total"] < 1:
            return redirect(url_for(
                "manage_admins",
                error="Cannot change the role of the only admin account.",
            ))
        # Demoting YOURSELF is refused separately: the guard above passes as
        # soon as one other admin exists, but you would still lose access to
        # this page mid-action with no warning.
        if session.get("admin_id") == admin_id:
            return redirect(url_for(
                "manage_admins",
                error="You cannot change your own role.",
            ))

    role_changed = role != (target["role"] or "")
    email_changed = (email or None) != (target["email"] or None)
    if not role_changed and not email_changed:
        return redirect(url_for("manage_admins"))

    db.execute(
        "UPDATE admins SET role = ?, email = ? WHERE id = ?",
        (role, email or None, admin_id),
    )
    db.commit()

    changed = []
    if role_changed:
        changed.append(f"role to {role_label(role)}")
    if email_changed:
        changed.append("email set" if email else "email cleared")
    log_audit_event(
        actor_admin_id=session.get("admin_id"),
        target_admin_id=target["id"],
        action_type="account_updated",
        details=f"Updated '{target['username']}': {', '.join(changed)}",
    )
    return redirect(url_for("manage_admins", success=f"'{target['username']}' updated."))


@app.route("/admins/bulk-delete", methods=["POST"])
@admin_required
def bulk_delete_admins():
    """Delete several accounts at once.

    PARTIAL, like the other bulk routes, and it re-checks both guards the
    single delete makes rather than trusting the UI to have hidden them:
    your own account is skipped, and the deletion stops before it would
    remove the last remaining admin. That last count is recomputed as it
    goes, so a batch containing every admin deletes all but one instead of
    either wiping them or refusing outright.
    """
    ids = _bulk_ids("admin_ids")
    if not ids:
        return redirect(url_for("manage_admins", error="No accounts selected."))

    db = get_db()
    placeholders = ",".join("?" * len(ids))
    rows = db.execute(
        f"SELECT id, username, role, first_name, middle_initial, last_name "
        f"FROM admins WHERE id IN ({placeholders})",
        ids,
    ).fetchall()

    admin_total = db.execute(
        "SELECT COUNT(*) AS total FROM admins WHERE role = 'admin'"
    ).fetchone()["total"]

    deleted, skipped_self, skipped_last, entries = 0, 0, 0, []
    for target in rows:
        if target["id"] == session.get("admin_id"):
            skipped_self += 1
            continue
        if target["role"] == "admin" and admin_total <= 1:
            skipped_last += 1
            continue

        db.execute("DELETE FROM admins WHERE id = ?", (target["id"],))
        if target["role"] == "admin":
            admin_total -= 1
        deleted += 1
        display_name = format_full_name(
            target["first_name"], target["middle_initial"], target["last_name"]
        )
        entries.append((
            target["id"],
            f"Deleted account '{target['username']}'" + (f" ({display_name})" if display_name else ""),
        ))

    db.commit()
    for target_id, details in entries:
        log_audit_event(
            actor_admin_id=session.get("admin_id"),
            target_admin_id=target_id,
            action_type="account_deleted",
            details=details,
        )

    if not deleted:
        return redirect(url_for("manage_admins", error="Nothing was deleted."))
    message = f"Deleted {deleted} account(s)."
    if skipped_self:
        message += " Your own account was skipped."
    if skipped_last:
        message += " The last admin account was kept."
    return redirect(url_for("manage_admins", success=message))


@app.route("/admins/<int:admin_id>/delete", methods=["POST"])
@admin_required
def delete_admin(admin_id):
    if session.get("admin_id") == admin_id:
        return redirect(url_for("manage_admins", error="You cannot delete your own account"))

    db = get_db()
    target = db.execute(
        "SELECT username, role, first_name, middle_initial, last_name FROM admins WHERE id = ?",
        (admin_id,),
    ).fetchone()

    # Only an admin target can be the last admin, or every faculty deletion
    # would be refused. bulk_delete_admins() already asked.
    admin_count_row = db.execute("SELECT COUNT(*) AS total FROM admins WHERE role = 'admin'").fetchone()
    admin_count = admin_count_row["total"] if admin_count_row else 0
    if target and target["role"] == "admin" and admin_count <= 1:
        return redirect(url_for("manage_admins", error="Cannot delete the only admin account"))

    db.execute("DELETE FROM admins WHERE id = ?", (admin_id,))
    db.commit()

    if target:
        display_name = format_full_name(target["first_name"], target["middle_initial"], target["last_name"])
        details = f"Deleted account '{target['username']}'" + (f" ({display_name})" if display_name else "")
    else:
        details = f"Deleted account (id {admin_id})"

    log_audit_event(
        actor_admin_id=session.get("admin_id"),
        target_admin_id=admin_id,
        action_type="account_deleted",
        details=details,
    )

    return redirect(url_for("manage_admins", success="Admin deleted successfully."))


@app.route("/admins/change-password", methods=["GET"])
@admin_required
def change_password_page():
    """Retired: your own password is on Profile, anyone else's is Reset
    password. A redirect so bookmarks land somewhere useful."""
    return redirect(url_for("settings"))


@app.route("/admins/audit-trail", methods=["GET"])
@admin_required
def audit_trail_page():
    db = get_db()

    # Server-side filters: audit_log only grows, so the server returns the
    # filtered set and table-tools.js pages through it.
    action_filter = (request.args.get("action") or "").strip()
    actor_filter = (request.args.get("actor") or "").strip()

    # Options come from the UNFILTERED table, or choosing one would remove
    # the way back; only values that occur are offered.
    action_options = [
        row["action_type"]
        for row in db.execute(
            "SELECT DISTINCT action_type FROM audit_log ORDER BY action_type ASC"
        ).fetchall()
    ]
    actor_options = db.execute(
        """
        SELECT DISTINCT admins.id, admins.username, admins.first_name,
               admins.middle_initial, admins.last_name
        FROM audit_log
        JOIN admins ON admins.id = audit_log.actor_admin_id
        ORDER BY admins.first_name ASC, admins.username ASC
        """
    ).fetchall()
    # Anything not in the offered options is discarded rather than queried -
    # a hand-edited query string can't reach past the dropdowns.
    if action_filter not in action_options:
        action_filter = ""
    if actor_filter not in [str(row["id"]) for row in actor_options]:
        actor_filter = ""

    where_clauses = []
    params = []
    if action_filter:
        where_clauses.append("audit_log.action_type = ?")
        params.append(action_filter)
    if actor_filter:
        where_clauses.append("audit_log.actor_admin_id = ?")
        params.append(int(actor_filter))
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    audit_entries = db.execute(
        f"""
        SELECT audit_log.id, audit_log.action_type, audit_log.details, audit_log.created_at,
               audit_log.target_type, audit_log.target_label,
               actor.username AS actor_username,
               actor.first_name AS actor_first_name,
               actor.middle_initial AS actor_middle_initial,
               actor.last_name AS actor_last_name,
               target.username AS target_username,
               target.first_name AS target_first_name,
               target.middle_initial AS target_middle_initial,
               target.last_name AS target_last_name
        FROM audit_log
        LEFT JOIN admins AS actor ON actor.id = audit_log.actor_admin_id
        LEFT JOIN admins AS target ON target.id = audit_log.target_admin_id
        {where_sql}
        ORDER BY audit_log.created_at DESC
        """,
        params,
    ).fetchall()

    return render_template(
        "audit_trail.html",
        audit_entries=audit_entries,
        action_options=action_options,
        actor_options=actor_options,
        action_filter=action_filter,
        actor_filter=actor_filter,
    )


@app.route("/profile", methods=["GET"])
@login_required
def profile():
    """Now /settings; a redirect so bookmarks still work. The query string
    is CARRIED, or ?error=/?field= would be lost and a failure would look
    like a save. Concatenated, not url_for(**request.args): its _external,
    _scheme and _anchor kwargs would let a crafted query steer the redirect."""
    target = url_for("settings")
    if request.query_string:
        target += "?" + request.query_string.decode("utf-8", "replace")
    return redirect(target)


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    db = get_db()
    admin_id = session.get("admin_id")

    if request.method == "POST":
        form_type = request.form.get("form_type")

        if form_type == "update_name":
            first_name = (request.form.get("first_name") or "").strip()
            middle_initial = (request.form.get("middle_initial") or "").strip()
            last_name = (request.form.get("last_name") or "").strip()
            if not first_name:
                return redirect(url_for("settings", error="First name is required."))
            if not last_name:
                return redirect(url_for("settings", error="Last name is required."))
            if middle_initial and not re.fullmatch(r"[A-Za-z]{1,2}", middle_initial):
                return redirect(url_for("settings", error="Middle initial must be 1 or 2 letters."))

            db.execute(
                "UPDATE admins SET first_name = ?, middle_initial = ?, last_name = ? WHERE id = ?",
                (first_name, middle_initial, last_name, admin_id),
            )
            db.commit()
            session["admin_full_name"] = format_full_name(first_name, middle_initial, last_name)
            return redirect(url_for("settings", success="Profile updated successfully."))

        elif form_type == "change_account":
            # Username and email in ONE form, behind ONE current-password
            # check.
            #
            # The password is not ceremony here. Whoever controls the address
            # controls where a future reset lands, so an unattended session
            # would otherwise be enough to point recovery somewhere else. The
            # username is what you sign in with, so the same bar applies.
            current_password = request.form.get("account_current_password") or ""
            new_username = (request.form.get("username") or "").strip()
            new_email = (request.form.get("email") or "").strip()

            account_row = db.execute(
                "SELECT id, username, email, password_hash FROM admins WHERE id = ?", (admin_id,)
            ).fetchone()
            if not account_row:
                session.clear()
                return redirect(url_for("login"))

            if not check_password_hash(account_row["password_hash"], current_password):
                return redirect(url_for(
                    "profile",
                    error="Current password is incorrect.",
                    field="account_current_password",
                ))

            username_error = validate_username(new_username)
            if username_error:
                return redirect(url_for("settings", error=username_error, field="username"))

            # Same loose check as the admin-side email field: a strict address
            # pattern rejects real addresses, and a wrong one costs the email,
            # not the account.
            if new_email and (len(new_email) > 120 or " " in new_email or new_email.count("@") != 1
                              or new_email.startswith("@") or new_email.endswith("@")
                              or "." not in new_email.split("@")[1]):
                return redirect(url_for(
                    "profile", error="That does not look like an email address.", field="email"
                ))

            old_username = account_row["username"]
            old_email = account_row["email"]
            username_changed = new_username != old_username
            email_changed = (new_email or None) != (old_email or None)

            if not username_changed and not email_changed:
                return redirect(url_for("settings", success="Nothing to change."))

            if username_changed:
                # Checked first, or UNIQUE raises an IntegrityError instead.
                taken = db.execute(
                    "SELECT id FROM admins WHERE username = ? AND id != ?", (new_username, admin_id)
                ).fetchone()
                if taken:
                    return redirect(url_for(
                        "profile", error="That username is already taken.", field="username"
                    ))

            db.execute(
                "UPDATE admins SET username = ?, email = ? WHERE id = ?",
                (new_username, new_email or None, admin_id),
            )
            db.commit()
            # The topbar and the sidebar tile both render this, so a stale
            # session would keep showing the old name until the next sign-in.
            session["admin_username"] = new_username

            # Audit entries name the account through target_admin_id, which the
            # Audit Trail resolves to the CURRENT name - so past rows follow a
            # rename on their own. These details record the transition itself,
            # which is the one thing an id cannot express.
            if username_changed:
                log_audit_event(
                    actor_admin_id=admin_id,
                    target_admin_id=admin_id,
                    action_type="account_updated",
                    details=f"Username changed from '{old_username}' to '{new_username}'",
                )
            if email_changed:
                log_audit_event(
                    actor_admin_id=admin_id,
                    target_admin_id=admin_id,
                    action_type="account_updated",
                    details=("Email address changed" if new_email else "Email address removed"),
                )

            # Notifications go to the OLD address on an email change: if the
            # change was not theirs, the old inbox is the only place they would
            # still see it. A username change goes to the current address,
            # since that one has not moved.
            if email_changed and old_email:
                send_email_later(
                    old_email,
                    "Your AppEase email address was changed",
                    email_changed_email_body(new_username, new_email or "none"),
                )
            if username_changed and (new_email or old_email):
                send_email_later(
                    new_email or old_email,
                    "Your AppEase username was changed",
                    username_changed_email_body(old_username, new_username),
                )

            return redirect(url_for("settings", success="Account details updated."))

        elif form_type == "update_avatar":
            new_path, avatar_error = save_avatar(request.files.get("avatar"))
            if avatar_error:
                return redirect(url_for("settings", error=avatar_error, field="avatar"))

            # Read the old path BEFORE overwriting it, so the previous file
            # can be removed and the folder does not grow by one orphan per
            # change. Removed only AFTER the row commits: losing the file
            # while the row still points at it would show a broken image.
            previous_row = db.execute(
                "SELECT avatar_path FROM admins WHERE id = ?", (admin_id,)
            ).fetchone()
            previous_path = previous_row["avatar_path"] if previous_row else None

            db.execute(
                "UPDATE admins SET avatar_path = ? WHERE id = ?",
                (new_path, admin_id),
            )
            db.commit()

            if previous_path and previous_path != new_path:
                delete_avatar_file(previous_path)

            # Not audit-logged: audit_log records security-relevant actions,
            # and a cosmetic photo change would bury them in noise.
            return redirect(url_for("settings", success="Profile picture updated."))

        elif form_type == "change_password":
            current_password = request.form.get("current_password") or ""
            # Not stripped - see the note in change_password().
            new_password = request.form.get("new_password") or ""
            confirm_password = request.form.get("confirm_password") or ""

            account = db.execute(
                "SELECT password_hash FROM admins WHERE id = ?",
                (admin_id,),
            ).fetchone()

            if not account or not check_password_hash(account["password_hash"], current_password):
                # field= names the input for an inline error: the form clears
                # either way, so a missed toast would look like a success.
                return redirect(url_for(
                    "profile",
                    error="Current password is incorrect.",
                    field="current_password",
                ))
            password_error = validate_password_strength(new_password)
            if password_error:
                return redirect(url_for("settings", error=password_error))
            if new_password != confirm_password:
                return redirect(url_for("settings", error="Passwords do not match."))

            db.execute(
                "UPDATE admins SET password_hash = ? WHERE id = ?",
                (generate_password_hash(new_password), admin_id),
            )
            db.commit()
            log_audit_event(
                actor_admin_id=admin_id,
                target_admin_id=admin_id,
                action_type="password_changed",
                details="Changed their own password in Settings",
            )
            return redirect(url_for("settings", success="Password changed successfully."))

        return redirect(url_for("settings"))

    account = db.execute(
        "SELECT id, username, role, first_name, middle_initial, last_name, created_at, last_login,"
        " email, avatar_path "
        "FROM admins WHERE id = ?",
        (admin_id,),
    ).fetchone()

    # Admin-only: assigned_to only points at technicians. The list is capped,
    # the count is not, for "showing N of M".
    assigned_reports = []
    assigned_open_count = 0
    if session.get("admin_role") == "admin":
        # Not finished, not just not resolved: a No fault found report would
        # otherwise sit on this list forever.
        finished_placeholders = ",".join("?" * len(FINISHED_REPORT_STATUSES))
        assigned_reports = db.execute(
            f"""
            SELECT id, computer_hostname, problem_type, priority, status, reported_at
            FROM problem_reports
            WHERE assigned_to = ? AND status NOT IN ({finished_placeholders})
              AND archived_at IS NULL
            ORDER BY reported_at DESC
            LIMIT 5
            """,
            (admin_id,) + FINISHED_REPORT_STATUSES,
        ).fetchall()
        count_row = db.execute(
            "SELECT COUNT(*) AS total FROM problem_reports "
            f"WHERE assigned_to = ? AND status NOT IN ({finished_placeholders}) AND archived_at IS NULL",
            (admin_id,) + FINISHED_REPORT_STATUSES,
        ).fetchone()
        assigned_open_count = count_row["total"] if count_row else 0

    # Only this account's own actions; a faculty profile may show none.
    recent_activity = db.execute(
        "SELECT action_type, details, created_at, target_type, target_label FROM audit_log "
        "WHERE actor_admin_id = ? ORDER BY created_at DESC LIMIT 5",
        (admin_id,),
    ).fetchall()
    activity_days = group_activity_by_day(recent_activity)

    # Built here so profile.html need not know the column is a static path.
    avatar_url = avatar_url_for(account["avatar_path"] if account else None)

    return render_template(
        "settings.html",
        account=account,
        avatar_url=avatar_url,
        assigned_reports=assigned_reports,
        assigned_open_count=assigned_open_count,
        activity_days=activity_days,
        error_message=request.args.get("error"),
        success_message=request.args.get("success"),
        error_field=request.args.get("field"),
    )


@app.route("/")
@admin_required
def dashboard():
    db = get_db()
    mark_stale_computers_offline(db)
    reap_stale_jobs(db)
    db.commit()

    counts = computer_counts(db)

    software_count_row = db.execute(
        "SELECT COUNT(*) AS total FROM software"
    ).fetchone()
    software_count = software_count_row["total"] if software_count_row else 0

    active_jobs_count_row = db.execute(
        "SELECT COUNT(*) AS total FROM jobs WHERE status IN ('pending', 'in_progress')"
    ).fetchone()
    active_jobs_count = active_jobs_count_row["total"] if active_jobs_count_row else 0

    # Every unfinished report, not only untouched ones: picked-up work counts.
    open_reports_count_row = db.execute(
        "SELECT COUNT(*) AS total FROM problem_reports WHERE archived_at IS NULL AND status NOT IN (%s)"
        % ",".join("?" * len(FINISHED_REPORT_STATUSES)),
        FINISHED_REPORT_STATUSES,
    ).fetchone()
    open_reports_count = open_reports_count_row["total"] if open_reports_count_row else 0

    # FINISHED jobs (completed or failed), installs and uninstalls: the set
    # the donut draws and Success Rate divides by, so they agree.
    total_deployments_row = db.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN action IS NULL THEN 1 ELSE 0 END) AS installs, "
        "SUM(CASE WHEN action = ? THEN 1 ELSE 0 END) AS uninstalls "
        "FROM jobs WHERE status IN ('completed', 'failed')",
        (JOB_ACTION_UNINSTALL,),
    ).fetchone()
    total_deployments = total_deployments_row["total"] if total_deployments_row else 0
    deployment_installs = (total_deployments_row["installs"] or 0) if total_deployments_row else 0
    deployment_uninstalls = (total_deployments_row["uninstalls"] or 0) if total_deployments_row else 0

    recent_jobs = db.execute(
        """
        SELECT jobs.id, jobs.computer_hostname, jobs.status, jobs.created_at, jobs.completed_at,
               jobs.started_at, jobs.retry_count, jobs.exit_code, jobs.result_reason, jobs.action,
               COALESCE(jobs.software_name, software.name, 'Removed software') AS software_name,
               COALESCE(jobs.software_version, software.version) AS software_version,
               CASE WHEN software.id IS NULL THEN 1 ELSE 0 END AS software_deleted,
               installations.state AS installation_state,
               computers.ip_address AS computer_ip
        FROM jobs
        LEFT JOIN software ON software.id = jobs.software_id
        LEFT JOIN installations ON installations.id = jobs.installation_id
        LEFT JOIN computers ON computers.hostname = jobs.computer_hostname
        ORDER BY jobs.created_at DESC, jobs.id DESC
        LIMIT 500
        """
    ).fetchall()
    # ------------------------------------------------------------------
    # Dashboard charts. Each card owns its range (chart_*, fail_*). Days are
    # Manila days: grouping on UTC dates would misfile 16:00-23:59 Manila.
    # ------------------------------------------------------------------
    manila_now = datetime.utcnow() + timedelta(hours=8)
    manila_today = manila_now.date()

    oldest_row = db.execute(
        "SELECT MIN(date(created_at, '+8 hours')) AS oldest FROM jobs"
    ).fetchone()
    chart_min_day = (oldest_row["oldest"] if oldest_row else None) or manila_today.strftime("%Y-%m-%d")

    def _parse_day(raw):
        # Anything malformed falls back to the default window rather than
        # erroring - a bad query string should never take the dashboard down.
        if not raw:
            return None
        try:
            return datetime.strptime(raw.strip(), "%Y-%m-%d").date()
        except (ValueError, AttributeError):
            return None

    def _chart_window(prefix, default_days):
        """Resolve one card's range into (start, end, explicit, clamped)."""
        start_day = _parse_day(request.args.get(prefix + "_from"))
        end_day = _parse_day(request.args.get(prefix + "_to"))
        if start_day and end_day and start_day > end_day:
            start_day, end_day = end_day, start_day
        # Never let a range run past today - future buckets are always empty.
        if end_day and end_day > manila_today:
            end_day = manila_today
        if start_day and start_day > manila_today:
            start_day = manila_today

        if not (start_day and end_day):
            return (manila_today - timedelta(days=default_days - 1), manila_today, False, False)

        span = (end_day - start_day).days + 1
        clamped = span > CHART_MAX_DAYS
        if clamped:
            start_day = end_day - timedelta(days=CHART_MAX_DAYS - 1)
        return (start_day, end_day, True, clamped)

    def _utc_bounds(start_day, end_day):
        """Manila day range -> the UTC strings the created_at column stores."""
        lo = (datetime(start_day.year, start_day.month, start_day.day)
              - timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
        after = end_day + timedelta(days=1)
        hi = (datetime(after.year, after.month, after.day)
              - timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
        return lo, hi

    # ---- Recent Activity (the Audit Trail, windowed for a card) ----
    # Same query shape as audit_trail_page(), so both describe an event alike.
    # 7 days and 40 rows: the widest tab is "This week". Bucketed in Manila
    # time, not on raw UTC dates.
    activity_since = (datetime.utcnow() - timedelta(days=8)).strftime("%Y-%m-%d %H:%M:%S")
    activity_rows = db.execute(
        """
        SELECT audit_log.id, audit_log.action_type, audit_log.details,
               audit_log.created_at, audit_log.target_type, audit_log.target_label,
               actor.username AS actor_username,
               actor.first_name AS actor_first_name,
               actor.middle_initial AS actor_middle_initial,
               actor.last_name AS actor_last_name,
               target.username AS target_username,
               target.first_name AS target_first_name,
               target.middle_initial AS target_middle_initial,
               target.last_name AS target_last_name,
               actor.avatar_path AS actor_avatar_path
        FROM audit_log
        LEFT JOIN admins AS actor ON actor.id = audit_log.actor_admin_id
        LEFT JOIN admins AS target ON target.id = audit_log.target_admin_id
        WHERE audit_log.created_at >= ?
        ORDER BY audit_log.created_at DESC
        LIMIT 40
        """,
        (activity_since,),
    ).fetchall()

    manila_now = datetime.utcnow() + timedelta(hours=8)
    manila_today_date = manila_now.date()
    recent_activity_feed = []
    activity_today_count = 0
    for row in activity_rows:
        stamp = _parse_stored_utc(row["created_at"])
        if stamp is None:
            # Unparseable: still shown, under the widest tab.
            bucket, when = "week", "-"
        else:
            local = stamp + timedelta(hours=8)
            delta_days = (manila_today_date - local.date()).days
            if delta_days <= 0:
                bucket = "today"
            elif delta_days == 1:
                bucket = "yesterday"
            elif delta_days <= 6:
                bucket = "week"
            else:
                continue
            # Today and yesterday only need a clock time; anything older
            # needs the date too or "10:30 AM" is ambiguous across days.
            when = local.strftime("%I:%M %p").lstrip("0")
            if bucket == "week":
                when = local.strftime("%d %b") + ", " + when
        if bucket == "today":
            activity_today_count += 1

        actor = format_full_name(
            row["actor_first_name"], row["actor_middle_initial"], row["actor_last_name"]
        ) or row["actor_username"] or "Unknown"
        # Same precedence the Audit Trail's Target column uses: a named
        # account first, then a non-account subject, then nothing.
        target = format_full_name(
            row["target_first_name"], row["target_middle_initial"], row["target_last_name"]
        ) or row["target_username"] or ""
        if not target and row["target_label"]:
            target = f'{(row["target_type"] or "").capitalize()}: {row["target_label"]}'.lstrip(": ")

        recent_activity_feed.append({
            "id": row["id"],
            "action_type": row["action_type"],
            "action_label": (row["action_type"] or "").replace("_", " ").capitalize(),
            "tone": AUDIT_ACTION_TONES.get(row["action_type"], "neutral"),
            "actor": actor,
            "initials": _name_initials(actor),
            # The default also covers a deleted actor (LEFT JOIN gives NULL),
            # who has no name left for initials.
            "avatar_url": avatar_url_for(row["actor_avatar_path"]),
            "target": target,
            "details": row["details"] or "",
            "when": when,
            "bucket": bucket,
        })

    # ---- Tasks (open problem reports assigned to the signed-in admin) ----
    # Same outstanding-work rule as the profile panel, so they never
    # disagree. No LIMIT: the card scrolls. An explicit CASE, since text
    # order gives "high" < "low" < "medium"; unknown priorities sort last.
    task_finished_placeholders = ",".join("?" * len(FINISHED_REPORT_STATUSES))
    assigned_tasks = db.execute(
        f"""
        SELECT id, computer_hostname, problem_type, description, notes,
               priority, status, reported_at,
               reporter_name, reporter_role
        FROM problem_reports
        WHERE assigned_to = ? AND status NOT IN ({task_finished_placeholders})
          AND archived_at IS NULL
        ORDER BY CASE priority
                     WHEN 'high' THEN 0
                     WHEN 'medium' THEN 1
                     WHEN 'low' THEN 2
                     ELSE 3
                 END ASC,
                 reported_at DESC
        """,
        (session.get("admin_id"),) + FINISHED_REPORT_STATUSES,
    ).fetchall()
    assigned_task_count = len(assigned_tasks)

    # String keys: JS looks them up by data-attribute value. Labels resolved
    # here so the dialog cannot drift from REPORT_STATUS_LABELS.
    task_details = {
        str(row["id"]): {
            "id": row["id"],
            "problem_type": row["problem_type"],
            "computer_hostname": row["computer_hostname"],
            "description": row["description"],
            "notes": row["notes"],
            "priority": row["priority"],
            "priority_label": (row["priority"] or "").capitalize(),
            "status": row["status"],
            "status_label": report_status_label(row["status"]),
            "reported_at": manila_time(row["reported_at"]),
            "reporter": (
                f'{row["reporter_name"]} ({row["reporter_role"]})'
                if row["reporter_name"] and row["reporter_role"]
                else (row["reporter_name"] or "")
            ),
        }
        for row in assigned_tasks
    }

    # ---- Deployment Activity ----
    act_start, act_end, act_explicit, activity_clamped = _chart_window("chart", 7)

    # Matched on the range as REQUESTED, not as clamped, so the select shows
    # the right option. Anything else (hand-typed, an old bookmark) falls
    # through to the default label rather than erroring.
    def _match_preset(start_day, end_day):
        req_start = _parse_day(request.args.get("chart_from")) or start_day
        req_end = _parse_day(request.args.get("chart_to")) or end_day
        if req_start > req_end:
            req_start, req_end = req_end, req_start
        if req_end > manila_today:
            req_end = manila_today
        if req_start > manila_today:
            req_start = manila_today
        if req_end != manila_today:
            return None
        span = (req_end - req_start).days
        if span == 6:
            return "Last 7 days"
        if span == 29:
            return "Last 30 days"
        if span == 90:
            return "Last 3 months"
        # The JS floors the requested start at the oldest job it knows about,
        # so on a young database a longer preset arrives as a shorter span.
        # Match it to the shortest preset that would have been clamped to it,
        # otherwise a fresh install can never show anything but the default.
        oldest = _parse_day(chart_min_day)
        if oldest and req_start <= oldest:
            if span > 29:
                return "Last 3 months"
            if span > 6:
                return "Last 30 days"
        return None

    chart_range_preset = _match_preset(act_start, act_end) or "Last 7 days"

    act_lo, act_hi = _utc_bounds(act_start, act_end)
    window_days = [act_start + timedelta(days=i) for i in range((act_end - act_start).days + 1)]

    activity_rows = db.execute(
        """
        SELECT date(created_at, '+8 hours') AS day,
               SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
               SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
        FROM jobs
        WHERE created_at >= ? AND created_at < ?
        GROUP BY day
        """,
        (act_lo, act_hi),
    ).fetchall()
    activity_by_day = {row["day"]: row for row in activity_rows}

    # From the day list, so empty days still get a slot. Weekly buckets are
    # anchored to the window's END, so the last ends on the chosen date.
    activity_by_week = len(window_days) > ACTIVITY_DAILY_MAX_DAYS
    bucket_size = 7 if activity_by_week else 1
    buckets = []
    cut = len(window_days)
    while cut > 0:
        start_i = max(0, cut - bucket_size)
        buckets.append(window_days[start_i:cut])
        cut = start_i
    buckets.reverse()

    activity_chart = []
    # More buckets than the axis can label, so only every Nth one gets a label;
    # the first and last always do.
    label_step = max(1, -(-len(buckets) // 10))
    for index, bucket in enumerate(buckets):
        first_day = bucket[0]
        last_day = bucket[-1]
        completed = 0
        failed = 0
        for day in bucket:
            row = activity_by_day.get(day.strftime("%Y-%m-%d"))
            if row:
                completed += row["completed"] or 0
                failed += row["failed"] or 0
        if len(bucket) == 1:
            # Axis label. A bare weekday ("Fri") cannot identify a day once the
            # range runs longer than a week, so this carries the month and day.
            label = first_day.strftime("%b ") + str(first_day.day)
            tip_label = first_day.strftime("%a, %b ") + str(first_day.day) + first_day.strftime(", %Y")
        else:
            label = first_day.strftime("%b ") + str(first_day.day)
            tip_label = (first_day.strftime("%b ") + str(first_day.day) + " - "
                         + last_day.strftime("%b ") + str(last_day.day)
                         + last_day.strftime(", %Y"))
        activity_chart.append({
            "label": label,
            # Tooltip / screen-reader label: room for the full date here.
            "tip_label": tip_label,
            "show_label": (index % label_step == 0) or (index == len(buckets) - 1),
            # Bounds for the jobs table's date-range filter, so a week bucket
            # is as clickable as a day.
            "from": first_day.strftime("%Y-%m-%d"),
            "to": last_day.strftime("%Y-%m-%d"),
            "completed": completed,
            "failed": failed,
            "total": completed + failed,
        })
    activity_max = max([entry["total"] for entry in activity_chart] + [0])
    activity_completed_total = sum(entry["completed"] for entry in activity_chart)
    activity_failed_total = sum(entry["failed"] for entry in activity_chart)
    # completed+failed only, matching the donut's two segments.
    activity_total = activity_completed_total + activity_failed_total

    # Scale top rounded UP to a step, so gridlines land on round numbers.
    activity_ticks = []
    activity_scale_max = 0
    if activity_max > 0:
        tick_step = 1
        for candidate in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000):
            tick_step = candidate
            if activity_max / float(candidate) <= 4:
                break
        activity_scale_max = int((activity_max + tick_step - 1) // tick_step * tick_step)
        activity_ticks = list(range(0, activity_scale_max + 1, tick_step))

    # All-time, completed/failed only: pending has not resolved, and
    # cancelled was never attempted.
    success_row = db.execute(
        """
        SELECT
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
        FROM jobs
        WHERE status IN ('completed', 'failed')
        """
    ).fetchone()
    success_completed = (success_row["completed"] or 0) if success_row else 0
    success_failed = (success_row["failed"] or 0) if success_row else 0
    success_total = success_completed + success_failed
    # None, not 0: a new install at "0%" would read as everything failing.
    success_rate = round(success_completed * 100.0 / success_total) if success_total > 0 else None

    # No sparkline for Total Computers: last_seen is overwritten each
    # heartbeat, so there is no history to draw. Grouped on Manila days.
    spark_days = [(manila_today - timedelta(days=offset)).strftime("%Y-%m-%d")
                  for offset in range(6, -1, -1)]

    def spark_series(table, column, only="1"):
        rows = db.execute(
            f"SELECT date({column}, '+8 hours') AS day, COUNT(*) AS total"
            f" FROM {table} WHERE date({column}, '+8 hours') >= ? AND {only}"
            f" GROUP BY day",
            (spark_days[0],),
        ).fetchall()
        by_day = {row["day"]: row["total"] for row in rows}
        return [by_day.get(day, 0) for day in spark_days]

    # Table, column and condition are literals here, never request input -
    # the f string above would be an injection otherwise. Jobs are installs
    # and uninstalls, like the cards.
    kpi_sparklines = {
        "software": spark_series("software", "date_added"),
        "jobs": spark_series("jobs", "created_at"),
        "reports": spark_series("problem_reports", "reported_at"),
    }

    return render_template(
        "dashboard.html",
        kpi_sparklines=kpi_sparklines,
        recent_jobs=recent_jobs,
        deploy_error=request.args.get("deploy_error"),
        deploy_success=request.args.get("deploy_success"),
        computer_count=counts["total"],
        online_count=counts["online"],
        retired_count=counts["retired"],
        software_count=software_count,
        active_jobs_count=active_jobs_count,
        open_reports_count=open_reports_count,
        total_deployments=total_deployments,
        deployment_installs=deployment_installs,
        deployment_uninstalls=deployment_uninstalls,
        success_rate=success_rate,
        success_completed=success_completed,
        success_total=success_total,
        activity_chart=activity_chart,
        activity_max=activity_max,
        activity_scale_max=activity_scale_max,
        activity_ticks=activity_ticks,
        chart_from=act_start.strftime("%Y-%m-%d") if act_explicit else "",
        chart_to=act_end.strftime("%Y-%m-%d") if act_explicit else "",
        chart_range_preset=chart_range_preset,
        activity_clamped=activity_clamped,
        activity_by_week=activity_by_week,
        chart_min_day=chart_min_day,
        chart_max_day=manila_today.strftime("%Y-%m-%d"),
        chart_max_days=CHART_MAX_DAYS,
        activity_completed_total=activity_completed_total,
        activity_failed_total=activity_failed_total,
        activity_total=activity_total,
        recent_activity_feed=recent_activity_feed,
        activity_today_count=activity_today_count,
        assigned_tasks=assigned_tasks,
        assigned_task_count=assigned_task_count,
        # Set by the bulk routes above, which redirect back here.
        task_details=task_details,
        # The same tuples every status and priority control reads, so adding
        # a status stays a one-line change.
        allowed_report_statuses=ALLOWED_REPORT_STATUSES,
        allowed_priorities=ALLOWED_PRIORITIES,
        task_success=request.args.get("report_success"),
        task_error=request.args.get("report_error"),
    )


def report_status_label(value):
    """Stored status -> words. One place, so the dropdown, the pill, the
    toast and the print stand-in can never disagree with each other."""
    return REPORT_STATUS_LABELS.get(
        (value or "").strip().lower(),
        (value or "").replace("_", " ").capitalize() or "Unknown",
    )


@app.template_filter("report_status")
def report_status_filter(value):
    return report_status_label(value)


@app.route("/problem-reports/<int:report_id>/print", methods=["GET"])
@login_required
def problem_report_print(report_id):
    """A one-page repair sheet. Not a print stylesheet: @media print hides
    forms, which would drop Notes and What was done. login_required, not
    admin_required: faculty can already see this report."""
    db = get_db()
    report = db.execute(
        """
        SELECT problem_reports.*,
               computers.ip_address AS computer_ip,
               computers.location AS computer_location,
               computers.device_type AS computer_device_type,
               assigned_admin.first_name AS assigned_first_name,
               assigned_admin.middle_initial AS assigned_middle_initial,
               assigned_admin.last_name AS assigned_last_name
        FROM problem_reports
        LEFT JOIN computers ON computers.hostname = problem_reports.computer_hostname
        LEFT JOIN admins AS assigned_admin ON assigned_admin.id = problem_reports.assigned_to
        WHERE problem_reports.id = ?
        """,
        (report_id,),
    ).fetchone()
    if not report:
        return redirect(url_for("problem_reports_page", report_error="Problem report not found."))

    # Same helper the list page uses, so the photo paths cannot be parsed one
    # way here and another way there. It takes a list of rows.
    report = with_report_photos([report])[0]

    return render_template(
        "report_print.html",
        report=report,
        # Printed at, not reported at - it dates the piece of paper.
        printed_at=(datetime.utcnow() + timedelta(hours=8)).strftime("%d %b, %Y %I:%M %p"),
    )


@app.route("/problem-reports/<int:report_id>/status", methods=["POST"])
@admin_required
def update_problem_report_status(report_id):
    # Anything not on the allow-list falls back to the reports page, so a
    # hand-edited value can never redirect off-site.
    return_to = (request.form.get("return_to") or "").strip()
    if return_to not in ("dashboard",):
        return_to = "problem_reports_page"

    new_status = (request.form.get("status") or "").strip().lower()
    if new_status not in ALLOWED_REPORT_STATUSES:
        return redirect(url_for(return_to, report_error="Invalid problem report status selected."))

    db = get_db()
    report = db.execute(
        "SELECT id, status, computer_hostname, archived_at FROM problem_reports WHERE id = ?",
        (report_id,),
    ).fetchone()
    if not report:
        return redirect(url_for(return_to, report_error="Problem report not found."))
    if report["archived_at"]:
        return archived_report_refusal(report_id, return_to)

    # Stamped at write time: SQLite refuses CURRENT_TIMESTAMP as an ALTER
    # TABLE default.
    db.execute(
        "UPDATE problem_reports SET status = ?, status_changed_at = ? WHERE id = ?",
        (new_status, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), report_id),
    )
    db.commit()
    if new_status != (report["status"] or ""):
        log_audit_event(
            actor_admin_id=session.get("admin_id"),
            target_admin_id=None,
            action_type="report_status_changed",
            details=audit_change("Status", report_status_label(report["status"]), report_status_label(new_status)),
            target_type="report",
            target_label=report_audit_label(report_id, report["computer_hostname"]),
        )
        queue_report_finished_emails([report_id], new_status)

    return redirect(url_for(
        return_to,
        report_success=f"Report #{report_id} updated to {report_status_label(new_status)}.",
    ))


def _bulk_report_ids():
    """Selected report ids from a bulk form, restricted to this admin's own.

    The dashboard card only ever lists reports assigned to whoever is signed
    in, but the endpoint is reachable with any id an admin cares to post, so
    ownership is re-checked here rather than trusted from the form. Returns
    the ids that both parse as integers AND are actually assigned to the
    caller; everything else is dropped silently.
    """
    raw_ids = request.form.getlist("report_ids")
    wanted = []
    for raw in raw_ids:
        try:
            wanted.append(int(str(raw).strip()))
        except (TypeError, ValueError):
            continue
    if not wanted:
        return []
    db = get_db()
    placeholders = ",".join("?" * len(wanted))
    rows = db.execute(
        f"SELECT id FROM problem_reports WHERE id IN ({placeholders}) AND assigned_to = ? "
        "AND archived_at IS NULL",
        wanted + [session.get("admin_id")],
    ).fetchall()
    return [row["id"] for row in rows]


@app.route("/problem-reports/bulk/status", methods=["POST"])
@admin_required
def bulk_update_problem_report_status():
    new_status = (request.form.get("status") or "").strip().lower()
    if new_status not in ALLOWED_REPORT_STATUSES:
        return redirect(url_for("dashboard", report_error="Invalid problem report status selected."))

    report_ids = _bulk_report_ids()
    if not report_ids:
        return redirect(url_for("dashboard", report_error="No assigned reports were selected."))

    db = get_db()
    placeholders = ",".join("?" * len(report_ids))
    # Read first: only a report whose status actually moves can "reach" a
    # finished one, and so be emailed.
    moved = [row["id"] for row in db.execute(
        f"SELECT id FROM problem_reports WHERE id IN ({placeholders}) AND status IS NOT ?",
        report_ids + [new_status],
    ).fetchall()]
    # Stamped by hand, as in the single-report route.
    db.execute(
        f"UPDATE problem_reports SET status = ?, status_changed_at = ? WHERE id IN ({placeholders})",
        [new_status, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")] + report_ids,
    )
    db.commit()
    queue_report_finished_emails(moved, new_status)
    log_audit_event(
        actor_admin_id=session.get("admin_id"),
        target_admin_id=None,
        action_type="report_status_changed",
        details=(f"Status set to {report_status_label(new_status)} on "
                 f"{audit_count(len(report_ids), 'report')}: {audit_id_list(report_ids)}"),
        target_type="report",
        target_label=audit_count(len(report_ids), "report"),
    )

    noun = "report" if len(report_ids) == 1 else "reports"
    return redirect(url_for(
        "dashboard",
        report_success=f"{len(report_ids)} {noun} updated to {report_status_label(new_status)}.",
    ))


@app.route("/problem-reports/bulk/priority", methods=["POST"])
@admin_required
def bulk_update_problem_report_priority():
    new_priority = (request.form.get("priority") or "").strip().lower()
    if new_priority not in ALLOWED_PRIORITIES:
        return redirect(url_for("dashboard", report_error="Invalid problem report priority selected."))

    report_ids = _bulk_report_ids()
    if not report_ids:
        return redirect(url_for("dashboard", report_error="No assigned reports were selected."))

    db = get_db()
    placeholders = ",".join("?" * len(report_ids))
    db.execute(
        f"UPDATE problem_reports SET priority = ? WHERE id IN ({placeholders})",
        [new_priority] + report_ids,
    )
    db.commit()
    log_audit_event(
        actor_admin_id=session.get("admin_id"),
        target_admin_id=None,
        action_type="report_priority_changed",
        details=(f"Priority set to {priority_label(new_priority)} on "
                 f"{audit_count(len(report_ids), 'report')}: {audit_id_list(report_ids)}"),
        target_type="report",
        target_label=audit_count(len(report_ids), "report"),
    )

    noun = "report" if len(report_ids) == 1 else "reports"
    return redirect(url_for(
        "dashboard",
        report_success=f"{len(report_ids)} {noun} set to {new_priority.capitalize()} priority.",
    ))


@app.route("/problem-reports/<int:report_id>/priority", methods=["POST"])
@admin_required
def update_problem_report_priority(report_id):
    new_priority = (request.form.get("priority") or "").strip().lower()
    if new_priority not in ALLOWED_PRIORITIES:
        return redirect(url_for("problem_reports_page", report_error="Invalid priority selected."))

    db = get_db()
    report = db.execute(
        "SELECT id, priority, computer_hostname, archived_at FROM problem_reports WHERE id = ?", (report_id,)
    ).fetchone()
    if not report:
        return redirect(url_for("problem_reports_page", report_error="Problem report not found."))
    if report["archived_at"]:
        return archived_report_refusal(report_id)

    db.execute(
        "UPDATE problem_reports SET priority = ? WHERE id = ?",
        (new_priority, report_id),
    )
    db.commit()
    if new_priority != (report["priority"] or ""):
        log_audit_event(
            actor_admin_id=session.get("admin_id"),
            target_admin_id=None,
            action_type="report_priority_changed",
            details=audit_change("Priority", priority_label(report["priority"]), priority_label(new_priority)),
            target_type="report",
            target_label=report_audit_label(report_id, report["computer_hostname"]),
        )

    return redirect(url_for("problem_reports_page", report_success=f"Report #{report_id} priority updated to {new_priority}."))


@app.route("/problem-reports/<int:report_id>/assign", methods=["POST"])
@admin_required
def update_problem_report_assignment(report_id):
    db = get_db()
    assigned_to_raw = (request.form.get("assigned_to") or "").strip()
    assigned_to = None
    if assigned_to_raw:
        try:
            assigned_to = int(assigned_to_raw)
        except ValueError:
            return redirect(url_for("problem_reports_page", report_error="Invalid technician selected."))
        technician = db.execute(
            "SELECT id FROM admins WHERE id = ? AND role = 'admin'",
            (assigned_to,),
        ).fetchone()
        if not technician:
            return redirect(url_for("problem_reports_page", report_error="Invalid technician selected."))

    report = db.execute(
        "SELECT id, assigned_to, computer_hostname, archived_at FROM problem_reports WHERE id = ?", (report_id,)
    ).fetchone()
    if not report:
        return redirect(url_for("problem_reports_page", report_error="Problem report not found."))
    if report["archived_at"]:
        return archived_report_refusal(report_id)

    db.execute(
        "UPDATE problem_reports SET assigned_to = ? WHERE id = ?",
        (assigned_to, report_id),
    )
    db.commit()
    if assigned_to != report["assigned_to"]:
        log_audit_event(
            actor_admin_id=session.get("admin_id"),
            target_admin_id=None,
            action_type="report_assigned",
            details=audit_change("Assigned to", audit_admin_name(db, report["assigned_to"]),
                                 audit_admin_name(db, assigned_to)),
            target_type="report",
            target_label=report_audit_label(report_id, report["computer_hostname"]),
        )

    return redirect(url_for("problem_reports_page", report_success=f"Report #{report_id} assignment updated."))


@app.route("/problem-reports/<int:report_id>/edit", methods=["POST"])
@admin_required
def update_problem_report_fields(report_id):
    """Set priority, status and assignment in one write, so the drawer
    never half-applies. Enforces every single-field route rule, stamping
    status_changed_at only when the status moved."""
    db = get_db()
    report = db.execute(
        "SELECT id, status, priority, assigned_to, computer_hostname, archived_at "
        "FROM problem_reports WHERE id = ?",
        (report_id,),
    ).fetchone()
    if not report:
        return redirect(url_for("problem_reports_page", report_error="Problem report not found."))
    if report["archived_at"]:
        return archived_report_refusal(report_id)

    new_status = (request.form.get("status") or "").strip().lower()
    if new_status not in ALLOWED_REPORT_STATUSES:
        return redirect(url_for("problem_reports_page", report_error="Invalid problem report status selected."))

    new_priority = (request.form.get("priority") or "").strip().lower()
    if new_priority not in ALLOWED_PRIORITIES:
        return redirect(url_for("problem_reports_page", report_error="Invalid priority selected."))

    assigned_to_raw = (request.form.get("assigned_to") or "").strip()
    assigned_to = None
    if assigned_to_raw:
        try:
            assigned_to = int(assigned_to_raw)
        except ValueError:
            return redirect(url_for("problem_reports_page", report_error="Invalid technician selected."))
        technician = db.execute(
            "SELECT id FROM admins WHERE id = ? AND role = 'admin'",
            (assigned_to,),
        ).fetchone()
        if not technician:
            return redirect(url_for("problem_reports_page", report_error="Invalid technician selected."))

    status_moved = new_status != (report["status"] or "")
    if status_moved:
        db.execute(
            "UPDATE problem_reports SET status = ?, status_changed_at = ?, priority = ?, assigned_to = ? "
            "WHERE id = ?",
            (new_status, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
             new_priority, assigned_to, report_id),
        )
    else:
        db.execute(
            "UPDATE problem_reports SET priority = ?, assigned_to = ? WHERE id = ?",
            (new_priority, assigned_to, report_id),
        )
    db.commit()

    # One message naming what actually moved, rather than three toasts or a
    # vague "updated" that hides a no-op save.
    changed = []
    if status_moved:
        changed.append("status to " + report_status_label(new_status))
    if new_priority != (report["priority"] or ""):
        changed.append("priority to " + new_priority)
    if assigned_to != report["assigned_to"]:
        changed.append("assignment")
    if not changed:
        return redirect(url_for("problem_reports_page"))
    parts = []
    if status_moved:
        parts.append(audit_change("Status", report_status_label(report["status"]), report_status_label(new_status)))
    if new_priority != (report["priority"] or ""):
        parts.append(audit_change("Priority", priority_label(report["priority"]), priority_label(new_priority)))
    if assigned_to != report["assigned_to"]:
        parts.append(audit_change("Assigned to", audit_admin_name(db, report["assigned_to"]),
                                  audit_admin_name(db, assigned_to)))
    log_audit_event(
        actor_admin_id=session.get("admin_id"),
        target_admin_id=None,
        action_type="report_updated",
        details="; ".join(parts),
        target_type="report",
        target_label=report_audit_label(report_id, report["computer_hostname"]),
    )
    if status_moved:
        queue_report_finished_emails([report_id], new_status)
    return redirect(url_for(
        "problem_reports_page",
        report_success=f"Report #{report_id}: updated {', '.join(changed)}.",
    ))


# Per field. The textareas carry the same maxlength, which counts a line
# break once - and the browser SENDS each one as CR+LF, so they are turned
# back into one character before counting, and saved that way.
NOTES_MAX_LENGTH = 1000


@app.route("/problem-reports/<int:report_id>/notes", methods=["POST"])
@admin_required
def update_problem_report_notes(report_id):
    notes = normalise_line_breaks(request.form.get("notes")).strip()

    db = get_db()
    # The old notes are read only to tell whether they changed. The audit
    # entry says THAT they were edited, never what they say.
    report = db.execute(
        "SELECT id, notes, resolution_notes, computer_hostname, archived_at FROM problem_reports WHERE id = ?",
        (report_id,),
    ).fetchone()
    if not report:
        return redirect(url_for("problem_reports_page", report_error="Problem report not found."))
    if report["archived_at"]:
        return archived_report_refusal(report_id)

    # Empty string, not NULL, once touched, matching notes.
    resolution_notes = normalise_line_breaks(request.form.get("resolution_notes")).strip()
    if len(notes) > NOTES_MAX_LENGTH:
        return redirect(url_for("problem_reports_page",
                                report_error=f"Notes must be {NOTES_MAX_LENGTH} characters or fewer."))
    if len(resolution_notes) > NOTES_MAX_LENGTH:
        return redirect(url_for("problem_reports_page",
                                report_error=f"What was done must be {NOTES_MAX_LENGTH} characters or fewer."))
    db.execute(
        "UPDATE problem_reports SET notes = ?, resolution_notes = ? WHERE id = ?",
        (notes, resolution_notes, report_id),
    )
    db.commit()
    edited = [name for name, old, new in (("notes", report["notes"], notes),
                                           ("resolution notes", report["resolution_notes"], resolution_notes))
              if (old or "") != new]
    if edited:
        log_audit_event(
            actor_admin_id=session.get("admin_id"),
            target_admin_id=None,
            action_type="report_notes_edited",
            details="Edited " + " and ".join(edited),
            target_type="report",
            target_label=report_audit_label(report_id, report["computer_hostname"]),
        )

    return redirect(url_for("problem_reports_page", report_success=f"Report #{report_id} notes saved."))


@app.route("/problem-reports", methods=["GET"])
@login_required
def problem_reports_page():
    db = get_db()

    # Server-side filters, independent of table-tools.js's own paging.
    status_filter = (request.args.get("status") or "").strip().lower()
    priority_filter = (request.args.get("priority") or "").strip().lower()
    if status_filter not in ALLOWED_REPORT_STATUSES:
        status_filter = ""
    if priority_filter not in ALLOWED_PRIORITIES:
        priority_filter = ""

    # Two views of one table, chosen here rather than by a script, so search,
    # sort, paging, print and export all work on exactly the rows shown, and
    # the counts above the table can say which is which.
    report_view = "archived" if request.args.get("view") == "archived" else "active"
    open_report_id = request.args.get("open", type=int)
    locate_report_id = request.args.get("locate", type=int)
    # A notification or a profile link to a report archived since lands on
    # the tab that has it, instead of "not in the current list".
    linked_id = open_report_id or locate_report_id
    if linked_id and report_view == "active":
        linked = db.execute("SELECT archived_at FROM problem_reports WHERE id = ?", (linked_id,)).fetchone()
        if linked and linked["archived_at"]:
            report_view = "archived"

    where_clauses = ["problem_reports.archived_at IS NOT NULL" if report_view == "archived"
                     else "problem_reports.archived_at IS NULL"]
    params = []
    if status_filter:
        where_clauses.append("problem_reports.status = ?")
        params.append(status_filter)
    if priority_filter:
        where_clauses.append("problem_reports.priority = ?")
        params.append(priority_filter)
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    problem_reports = db.execute(
        f"""
        SELECT problem_reports.id, problem_reports.computer_hostname,
               problem_reports.problem_type, problem_reports.description,
               problem_reports.status, problem_reports.reported_at,
               problem_reports.priority, problem_reports.assigned_to, problem_reports.notes,
               problem_reports.reporter_role, problem_reports.reporter_name,
               problem_reports.reporter_student_number, problem_reports.reporter_program,
               problem_reports.reporter_section, problem_reports.reporter_department,
               problem_reports.reporter_year_level, problem_reports.photo_paths,
               problem_reports.status_changed_at, problem_reports.resolution_notes,
               problem_reports.report_source, problem_reports.archived_at,
               problem_reports.reporter_email,
               computers.ip_address AS computer_ip,
               assigned_admin.first_name AS assigned_first_name,
               assigned_admin.middle_initial AS assigned_middle_initial,
               assigned_admin.last_name AS assigned_last_name,
               archiver.username AS archived_username,
               archiver.first_name AS archived_first_name,
               archiver.middle_initial AS archived_middle_initial,
               archiver.last_name AS archived_last_name
        FROM problem_reports
        LEFT JOIN computers ON computers.hostname = problem_reports.computer_hostname
        LEFT JOIN admins AS assigned_admin ON assigned_admin.id = problem_reports.assigned_to
        LEFT JOIN admins AS archiver ON archiver.id = problem_reports.archived_by
        {where_sql}
        ORDER BY {"problem_reports.archived_at DESC, " if report_view == "archived" else ""}problem_reports.reported_at DESC
        """,
        params,
    ).fetchall()
    problem_reports = with_report_photos(problem_reports)

    # Over the WHOLE table: a card that changes with the filter only echoes
    # it. Unresolved derives from FINISHED_REPORT_STATUSES; one query.
    finished_placeholders = ",".join("?" * len(FINISHED_REPORT_STATUSES))
    summary_row = db.execute(
        f"""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status NOT IN ({finished_placeholders}) THEN 1 ELSE 0 END) AS unresolved,
               SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS unopened,
               SUM(CASE WHEN priority = 'high' AND status NOT IN ({finished_placeholders}) THEN 1 ELSE 0 END) AS high_priority,
               SUM(CASE WHEN assigned_to IS NULL AND status NOT IN ({finished_placeholders}) THEN 1 ELSE 0 END) AS unassigned
        FROM problem_reports
        WHERE archived_at IS NULL
        """,
        FINISHED_REPORT_STATUSES * 3,
    ).fetchone()
    # SUM() over zero rows is NULL, not 0 - an empty table would print "None".
    report_summary = {
        "total": (summary_row["total"] or 0) if summary_row else 0,
        "unresolved": (summary_row["unresolved"] or 0) if summary_row else 0,
        "unopened": (summary_row["unopened"] or 0) if summary_row else 0,
        "high_priority": (summary_row["high_priority"] or 0) if summary_row else 0,
        "unassigned": (summary_row["unassigned"] or 0) if summary_row else 0,
    }
    # Subtracted, so Total always equals the two added up.
    report_summary["finished"] = report_summary["total"] - report_summary["unresolved"]
    # The Archived tab's own count. The cards above describe the main list
    # only - archived reports are finished, and kept for the record.
    archived_count = db.execute(
        "SELECT COUNT(*) AS total FROM problem_reports WHERE archived_at IS NOT NULL"
    ).fetchone()["total"]

    technicians = db.execute(
        "SELECT id, first_name, middle_initial, last_name FROM admins WHERE role = 'admin' ORDER BY first_name ASC"
    ).fetchall()
    return render_template(
        "problem_reports.html",
        problem_reports=problem_reports,
        technicians=technicians,
        # NOT sorted - the tuple is already in lifecycle order.
        report_summary=report_summary,
        allowed_report_statuses=ALLOWED_REPORT_STATUSES,
        # ?open=<id> opens that report's Details on arrival, so no other page
        # needs a copy of the panel. Unvalidated: the template only matches
        # existing buttons, so a stale id finds nothing.
        open_report_id=open_report_id,
        # "Take me to this row" without reopening Details - see the deep
        # link comment in problem_reports.html.
        locate_report_id=locate_report_id,
        report_view=report_view,
        archived_count=archived_count,
        allowed_priorities=ALLOWED_PRIORITIES,
        status_filter=status_filter,
        priority_filter=priority_filter,
        report_error=request.args.get("report_error"),
        report_success=request.args.get("report_success"),
    )


# --- Report form text rules ----------------------------------------------
#
# Each rule here has a twin in report.html's script, and the two must give
# the same answer: a value the page lets through and this file refuses costs
# the reporter a round trip, and one this file would take but the page
# refuses can never be sent at all. t/test_report_validation.py runs the
# page's own patterns against these.

# Exactly what JavaScript's String.prototype.trim() removes, because that is
# how the page trims. Python's str.strip() is close but not the same: it also
# strips four ASCII separator controls and U+0085, and keeps U+FEFF.
BROWSER_TRIM_CHARS = ("\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
                      "\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff")


def trim_like_browser(text):
    return (text or "").strip(BROWSER_TRIM_CHARS)


def normalise_line_breaks(text):
    """One character per line break: browsers send CR+LF but their script
    counts one, so counting CR+LF would refuse what the page allowed."""
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


REPORT_EMAIL_MESSAGE = "Enter a valid email address, like name@gmail.com."
# The local part takes the characters <input type="email"> takes, so the
# browser's own check and this one never disagree about a symbol - plus what
# that check does not look at: no dot first, last, or twice in a row.
_EMAIL_LOCAL = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*")
# Two or more labels of letters, digits and inner hyphens, ending in two or
# more letters - so "name@gmail.c" and "name@gm_ail.com" are refused.
_EMAIL_DOMAIN = re.compile(r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}")
# AppEase is used only in the Philippines, so a reporter's address ends in
# .com or .ph, in any case. Checked against the part after the LAST dot, and
# before the domain pattern, so a slip in the ending - gmail.c, gmail.co -
# is told what to do rather than given the general message.
REPORT_EMAIL_ENDING_MESSAGE = "Please use an email ending in .com or .ph."
_EMAIL_ENDING = re.compile(r"com|ph", re.IGNORECASE)


def report_email_error(email):
    """The reporter's trimmed address -> a message, or None. Stricter than
    validate_admin_email(), so receipts can be delivered; .com and .ph only."""
    if not email:
        return "Your email address is required."
    if len(email) > 120 or email.count("@") != 1:
        return REPORT_EMAIL_MESSAGE
    local, _, domain = email.partition("@")
    if len(local) > 64 or not _EMAIL_LOCAL.fullmatch(local):
        return REPORT_EMAIL_MESSAGE
    ending = domain.rpartition(".")[2] if "." in domain else ""
    if ending and not _EMAIL_ENDING.fullmatch(ending):
        return REPORT_EMAIL_ENDING_MESSAGE
    if not _EMAIL_DOMAIN.fullmatch(domain):
        return REPORT_EMAIL_MESSAGE
    return None


DESCRIPTION_MIN_LENGTH = 10
DESCRIPTION_MAX_LENGTH = 500
# Letters include the accented ones (n with a tilde, e with an acute), minus
# the two symbols that sit inside that block: the multiplication and
# division signs.
_LETTERS = "A-Za-z\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u024f"
_DESCRIPTION_LETTER = re.compile(f"[{_LETTERS}]")
_DESCRIPTION_LETTER_OR_SPACE = re.compile(f"[{_LETTERS} \t\n]")
_DESCRIPTION_ALLOWED = re.compile(f"[{_LETTERS}0-9 \t\n.,!?'\"()\\-:;/#&%+@_*]+")
# The misc-symbols block, and anything outside the basic plane - which is
# where emoji live, and what the page sees as a surrogate pair.
_DESCRIPTION_EMOJI = re.compile("[\u2600-\u27bf\U00010000-\U0010ffff]")
# Digits are exempt: the five zeros in 0x0000007E are an error code worth
# reporting, not keymash.
_DESCRIPTION_REPEAT = re.compile(r"([^0-9])\1{4,}")
# Keymash is a long run with no vowel. Y is left out of the run because it
# is a vowel in "rhythm" and "glyphs", both refused while it counted.
_DESCRIPTION_CONSONANTS = re.compile(r"[bcdfghjklmnpqrstvwxz]{6,}", re.IGNORECASE)


def clean_description(raw):
    """What is checked and saved: one character per line break, trimmed as
    the page trims."""
    return trim_like_browser(normalise_line_breaks(raw))


def description_error(text):
    """A cleaned description -> its message, or None: the same checks,
    order and messages as descriptionError() in report.html. Catches keymash
    and symbol spam without refusing short real reports."""
    if not text:
        return "Please describe the problem."
    if len(text) < DESCRIPTION_MIN_LENGTH:
        return f"Description must be at least {DESCRIPTION_MIN_LENGTH} characters."
    if len(text) > DESCRIPTION_MAX_LENGTH:
        return f"Description must be {DESCRIPTION_MAX_LENGTH} characters or fewer."
    if _DESCRIPTION_EMOJI.search(text):
        return "Description cannot contain emojis."
    if _DESCRIPTION_REPEAT.search(text):
        return "Description cannot repeat the same character 5 times in a row."
    if not _DESCRIPTION_ALLOWED.fullmatch(text):
        return "Description can only contain letters, numbers, and basic punctuation."
    if (not _DESCRIPTION_LETTER.search(text)
            or len(_DESCRIPTION_LETTER_OR_SPACE.findall(text)) < 0.4 * len(text)
            or _DESCRIPTION_CONSONANTS.search(text)):
        return "That doesn't look like a real description. Please explain the problem in your own words."
    return None


@app.route("/problem-reports/<int:report_id>/delete", methods=["POST"])
@admin_required
def delete_problem_report(report_id):
    db = get_db()
    # photo_paths so the files go with the row (or the images leak);
    # hostname and type so the audit entry can name the report. Never its
    # description.
    report = db.execute(
        "SELECT id, photo_paths, computer_hostname, problem_type, archived_at FROM problem_reports WHERE id = ?",
        (report_id,),
    ).fetchone()
    if not report:
        return redirect(url_for("problem_reports_page", report_error="Problem report not found."))
    # Deleting happens only from the archive, so nothing leaves the record
    # without first being finished and archived - a junk report goes No
    # fault found, then Archive, then Delete. The buttons are only on the
    # Archived tab; this is the guard.
    if not report["archived_at"]:
        return redirect(url_for(
            "problem_reports_page",
            report_error=f"Report #{report_id} isn't archived. Archive it, then delete it from the Archived tab.",
        ))

    db.execute("DELETE FROM problem_reports WHERE id = ?", (report_id,))
    delete_report_photos(report["photo_paths"])
    db.commit()
    log_audit_event(
        actor_admin_id=session.get("admin_id"),
        target_admin_id=None,
        action_type="report_deleted",
        details=f"Deleted report #{report_id} ({report['problem_type']}) from {report['computer_hostname']}",
        target_type="report",
        target_label=f"#{report_id} ({report['computer_hostname']})",
    )
    return redirect(url_for("problem_reports_page", view="archived",
                            report_success=f"Report #{report_id} deleted."))


# --- Computer location and device type -----------------------------------
#
# Normalised so "Lab 1" and " Lab  1 " never become two rooms. Blank gives
# None: that is how a computer goes back to Unassigned.
def normalize_location(raw_value):
    """Returns (value_or_None, error_message)."""
    value = " ".join((raw_value or "").split())
    if not value:
        return None, None
    if len(value) > MAX_LOCATION_LENGTH:
        return None, f"Location must be {MAX_LOCATION_LENGTH} characters or fewer."
    if not re.match(LOCATION_PATTERN, value):
        return None, "Location can only contain letters, numbers, spaces, brackets, full stops and hyphens."
    return value, None


def existing_computer_locations(db):
    """Every location already in use, for the dropdown. DISTINCT so the second
    computer in a room is a pick rather than a re-type."""
    rows = db.execute(
        """
        SELECT DISTINCT location
        FROM computers
        WHERE location IS NOT NULL AND TRIM(location) <> ''
        ORDER BY LOWER(location) ASC
        """
    ).fetchall()
    return [row["location"] for row in rows]


def normalise_asset_date(raw_value, field_label):
    """A technician's ISO date: (value, error). YYYY-MM-DD text, so it sorts
    as text (SQLite has no date type); blank clears it to "Not recorded"."""
    value = (raw_value or "").strip()
    if not value:
        return None, None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None, f"{field_label} must be a date in YYYY-MM-DD form."
    # A four-digit year is not enough on its own: strptime happily accepts
    # 0001-01-01, which is a typo rather than a purchase date.
    if parsed.year < 1990 or parsed.year > 2100:
        return None, f"{field_label} must be a year between 1990 and 2100."
    return value, None


def validate_asset_fields(form):
    """(values, error) for the technician-owned fields. Agent-reported facts
    (manufacturer, serial, cpu...) are never taken from a form, or the
    database would disagree with the machine."""
    status = (form.get("asset_status") or "").strip()
    if status and status not in ALLOWED_ASSET_STATUSES:
        return None, "Invalid asset status."

    purchase_date, error = normalise_asset_date(form.get("purchase_date"), "Purchase date")
    if error:
        return None, error

    warranty_until, error = normalise_asset_date(form.get("warranty_until"), "Warranty expiry")
    if error:
        return None, error

    vendor = " ".join((form.get("vendor") or "").split())
    if len(vendor) > MAX_VENDOR_LENGTH:
        return None, f"Vendor must be {MAX_VENDOR_LENGTH} characters or fewer."
    if vendor and not re.match(r"^[A-Za-z0-9.,'&\- ]+$", vendor):
        return None, "Vendor can only contain letters, numbers, spaces and . , ' & -"

    return (
        {
            "asset_status": status or None,
            "purchase_date": purchase_date,
            "vendor": vendor or None,
            "warranty_until": warranty_until,
        },
        None,
    )


def format_ram(ram_mb):
    """8192 -> "8 GB". Whole GB when it divides evenly, one decimal when it
    does not - a 12,288 MB stick is 12 GB, but 6,144 is 6 GB and 3,072 is
    3 GB, while an odd 6,000 should not claim to be 5.9."""
    if not ram_mb:
        return None
    gb = ram_mb / 1024.0
    if abs(gb - round(gb)) < 0.05:
        return f"{int(round(gb))} GB"
    return f"{gb:.1f} GB"


def format_storage(storage_gb):
    """238 -> "238 GB", 1024 -> "1 TB". Drives are sold in TB past a point
    and a four-digit GB figure is harder to read at a glance."""
    if not storage_gb:
        return None
    if storage_gb >= 1000:
        tb = storage_gb / 1024.0
        return f"{tb:.1f} TB".replace(".0 TB", " TB")
    return f"{storage_gb} GB"


def asset_status_label(value):
    """NULL means In use - see ALLOWED_ASSET_STATUSES. Every read path goes
    through here so the default lives in exactly one place."""
    return value or ASSET_STATUS_IN_USE


def admin_is_online(last_active_at):
    """True if the account made a request within ADMIN_ONLINE_SECONDS.
    Unparseable counts as offline: it is no evidence of presence."""
    if not last_active_at:
        return False
    value = str(last_active_at).strip()
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            seen = datetime.strptime(value, pattern)
        except ValueError:
            continue
        # CURRENT_TIMESTAMP is UTC, so this compares against utcnow() - the
        # manila filter is for DISPLAY and must not be used for arithmetic.
        return (datetime.utcnow() - seen).total_seconds() <= ADMIN_ONLINE_SECONDS
    return False


def with_online_status(rows):
    """Turn read-only sqlite3.Row objects into dicts carrying is_online."""
    out = []
    for row in rows:
        item = dict(row)
        item["is_online"] = admin_is_online(item.get("last_active_at"))
        out.append(item)
    return out


def with_asset_labels(rows):
    """Rows as dicts carrying display strings: a Row cannot take new keys,
    and templates should not do arithmetic."""
    out = []
    for row in rows:
        item = dict(row)
        item["ram_label"] = format_ram(item.get("ram_mb"))
        item["storage_label"] = format_storage(item.get("storage_gb"))
        item["asset_status_label"] = asset_status_label(item.get("asset_status"))
        item["uninstall_label"] = uninstall_support_label(item.get("capabilities"))
        out.append(item)
    return out


def equipment_text(raw_value, label, max_length, required=False):
    """One free-text equipment field: (value, error). Same character set
    as locations, since the same kinds of names appear."""
    value = " ".join((raw_value or "").split())
    if not value:
        if required:
            return None, f"{label} is required."
        return None, None
    if len(value) > max_length:
        return None, f"{label} must be {max_length} characters or fewer."
    if not re.match(EQUIPMENT_TEXT_PATTERN, value):
        return None, f"{label} contains characters that are not allowed."
    return value, None


def equipment_quantity(raw_value):
    """(quantity, error); blank means one, the common case."""
    raw = (raw_value or "").strip()
    if not raw:
        return 1, None
    if not raw.isdigit():
        return None, "Quantity must be a whole number."
    value = int(raw)
    if value < 1:
        return None, "Quantity must be at least 1."
    if value > MAX_EQUIPMENT_QUANTITY:
        return None, f"Quantity must be {MAX_EQUIPMENT_QUANTITY} or fewer."
    return value, None


def equipment_location(raw_value):
    """The item's room: (value, error). Normalised like a computer's, so
    the two match, and REQUIRED: an item with no room cannot be found."""
    value, error = normalize_location(raw_value)
    if error:
        return None, error
    if not value:
        return None, "Location is required."
    return value, None


def validate_equipment_fields(form):
    """(values, error) for one equipment row. A serial names ONE object,
    so it cannot go on a row with quantity above one."""
    location, error = equipment_location(form.get("location"))
    if error:
        return None, error

    category, error = equipment_text(
        form.get("category"), "Category", MAX_EQUIPMENT_CATEGORY_LENGTH, required=True)
    if error:
        return None, error

    manufacturer, error = equipment_text(
        form.get("manufacturer"), "Manufacturer", MAX_EQUIPMENT_FIELD_LENGTH)
    if error:
        return None, error

    model, error = equipment_text(form.get("model"), "Model", MAX_EQUIPMENT_FIELD_LENGTH)
    if error:
        return None, error

    quantity, error = equipment_quantity(form.get("quantity"))
    if error:
        return None, error

    serial_number, error = equipment_text(
        form.get("serial_number"), "Serial number", MAX_EQUIPMENT_FIELD_LENGTH)
    if error:
        return None, error
    if serial_number and quantity > 1:
        return None, ("A serial number identifies one item, so it cannot be set "
                      "on a row with a quantity above 1.")

    notes, error = equipment_text(form.get("notes"), "Notes", MAX_EQUIPMENT_NOTES_LENGTH)
    if error:
        return None, error

    # The computer asset validators, so dates are checked alike.
    asset, error = validate_asset_fields(form)
    if error:
        return None, error

    return (
        {
            "location": location,
            "category": category,
            "manufacturer": manufacturer,
            "model": model,
            "serial_number": serial_number,
            "quantity": quantity,
            "notes": notes,
            "asset_status": asset["asset_status"],
            "purchase_date": asset["purchase_date"],
            "vendor": asset["vendor"],
            "warranty_until": asset["warranty_until"],
        },
        None,
    )


def equipment_label(manufacturer, model):
    """"Dell P2419H", or one part alone. Not software_label(): that name
    would misread here, though the join is identical."""
    parts = [(manufacturer or "").strip(), (model or "").strip()]
    return " ".join(part for part in parts if part)


def existing_equipment_categories(db):
    """Every category already in use, for the dropdown - so the second
    keyboard is a pick, not a re-type that invents "Keyboards"."""
    rows = db.execute(
        """
        SELECT DISTINCT category
        FROM equipment
        WHERE category IS NOT NULL AND TRIM(category) <> ''
        ORDER BY LOWER(category) ASC
        """
    ).fetchall()
    return [row["category"] for row in rows]


def known_locations(db):
    """Every room named by either table, so the first item in a room is a
    pick, not a re-typed "lab 2"."""
    rows = db.execute(
        """
        SELECT location FROM (
            SELECT location FROM computers
            WHERE location IS NOT NULL AND TRIM(location) <> ''
            UNION
            SELECT location FROM equipment
            WHERE location IS NOT NULL AND TRIM(location) <> ''
        )
        ORDER BY LOWER(location) ASC
        """
    ).fetchall()
    return [row["location"] for row in rows]


def equipment_rows(db, location=None):
    """Equipment by room, then category. No join: the row's location IS
    the location, with no copy to drift from a computer that moved."""
    sql = "SELECT * FROM equipment"
    params = []
    if location is not None:
        sql += " WHERE location = ?"
        params.append(location)
    sql += """
        ORDER BY LOWER(location) ASC,
                 LOWER(category) ASC,
                 LOWER(COALESCE(manufacturer, '')) ASC,
                 id ASC
    """
    out = []
    for row in db.execute(sql, params).fetchall():
        item = dict(row)
        item["asset_status_label"] = asset_status_label(item.get("asset_status"))
        out.append(item)
    return out


def equipment_total_quantity(items):
    """How many objects, not rows: "40 cables" is forty things."""
    return sum(int(item.get("quantity") or 1) for item in items)


def equipment_by_location(db):
    """Grouped by room, for the Lab Computers drawer. By room, not desk:
    per-desk records are only useful if kept current, and are not."""
    grouped = {}
    for item in equipment_rows(db):
        grouped.setdefault(item["location"], []).append({
            "category": item["category"],
            "label": equipment_label(item.get("manufacturer"), item.get("model")),
            "quantity": item.get("quantity") or 1,
            "condition": item["asset_status_label"],
        })
    return grouped


# --- "Installed by AppEase", in the Lab Computers details drawer -----------
#
# Every word, tone and order is decided here; the drawer's script only
# copies strings with textContent, so wording lives in one place and no
# agent text is ever markup. Replaced and removed rows are history, behind
# "Show history".
INSTALL_METHOD_LABELS = {
    "exe": "Installer (EXE)",
    "msi": "Windows Installer (MSI)",
    "choco": "Chocolatey",
}
# (group order, pill, tone). Tones are the shared --tone-* set; neutral is
# not used because its pill text is 4.39:1 in light mode.
INSTALLATION_PILLS = {
    "installed": (0, "Installed", "good"),
    "unavailable": (1, "Can't uninstall automatically", "warn"),
    "protected": (2, "Protected", "info"),
}
INSTALLATION_HISTORY_STATES = ("replaced", "removed")


def installation_item(row):
    """One installations row as the drawer shows it."""
    name = row["software_name"] or row["display_name"] or "Unknown software"
    version = row["display_version"] or row["software_version"] or ""
    meta = [version] if version else []
    meta.append(INSTALL_METHOD_LABELS.get(row["method"], row["method"] or "Unknown method"))
    if row["method"] == "choco" and row["choco_package_id"]:
        meta.append(row["choco_package_id"])
    if (row["reg_hive"] or "").upper() in ("HKEY_CURRENT_USER", "HKCU"):
        meta.append("for one user account")
    reason = INSTALL_RECORD_REASONS.get(row["state_reason"] or "", {}).get("label", "")
    item = {
        "name": name,
        "library_note": "no longer in the Software Library" if row["software_id"] is None else "",
        "meta": " \u00b7 ".join(meta),
    }
    if row["state"] in INSTALLATION_HISTORY_STATES:
        when = manila_time(row["state_changed_at"]) if row["state_changed_at"] else ""
        what = (reason or row["state"].capitalize()).rstrip(".")
        item["history"] = " \u00b7 ".join(part for part in (what, when) if part)
        return item
    group = "protected" if row["state_reason"] == "protected" else row["state"]
    # A shared runtime is never offered for removal (uninstall_blocker), so
    # the drawer says so, whatever else the row says.
    if group != "protected" and is_shared_runtime(row):
        group, reason = "protected", SHARED_RUNTIME_REASON
    # Worded as the picker: both read exe_uninstall_problem().
    elif row["method"] == "exe" and group in LIVE_INSTALLATION_STATES:
        problem = exe_uninstall_problem(row)
        if group == "unavailable" and problem is None:
            group, reason = "installed", "Uninstalls with the saved switch " + installation_library_switch(row)
        elif group == "installed" and problem:
            group, reason = "unavailable", UNINSTALL_SKIP_REASONS[problem]
        elif problem not in (None, "unavailable"):
            reason = UNINSTALL_SKIP_REASONS[problem]
    order, pill, tone = INSTALLATION_PILLS.get(group, INSTALLATION_PILLS["unavailable"])
    item.update({"pill": pill, "tone": tone, "reason": reason})
    item["_order"] = order
    return item


def installations_by_computer(db):
    """{computer id string: {"live", "history"}}: string keys, as JS looks
    them up by data-dt-id. Computers with no rows are absent."""
    grouped = {}
    for row in db.execute(INSTALLATION_ROW_SELECT + "ORDER BY installations.id").fetchall():
        entry = grouped.setdefault(str(row["computer_id"]), {"live": [], "history": [], "_when": []})
        item = installation_item(row)
        if "history" in item:
            entry["history"].append(item)
            entry["_when"].append((row["state_changed_at"] or "", row["id"]))
        else:
            entry["live"].append(item)
    for entry in grouped.values():
        entry["live"].sort(key=lambda item: (item["_order"], item["name"].casefold()))
        for item in entry["live"]:
            del item["_order"]
        newest_first = sorted(zip(entry.pop("_when"), entry["history"]), key=lambda pair: pair[0], reverse=True)
        entry["history"] = [item for _, item in newest_first]
    return grouped


@app.route("/computers")
@admin_required
def computers_page():
    """The Lab Computers table (search, sort, filter, print, CSV)."""
    db = get_db()
    # Same first step the dashboard takes: a computer that stopped checking in
    # must read offline here too, or the two pages disagree.
    mark_stale_computers_offline(db)
    db.commit()
    computers = db.execute(
        """
        SELECT * FROM computers
        ORDER BY (location IS NULL OR TRIM(location) = '') ASC,
                 LOWER(location) ASC,
                 LOWER(hostname) ASC
        """
    ).fetchall()
    return render_template(
        "computers.html",
        computers=with_asset_labels(computers),
        computer_locations=existing_computer_locations(db),
        allowed_device_types=ALLOWED_DEVICE_TYPES,
        allowed_asset_statuses=ALLOWED_ASSET_STATUSES,
        equipment_by_location=equipment_by_location(db),
        installations_by_computer=installations_by_computer(db),
        computer_error=request.args.get("error"),
        computer_success=request.args.get("success"),
    )


@app.route("/computers/<int:computer_id>/location", methods=["POST"])
@admin_required
def update_computer_location(computer_id):
    db = get_db()
    computer = db.execute(
        "SELECT id, hostname, location FROM computers WHERE id = ?", (computer_id,)
    ).fetchone()
    if not computer:
        return redirect(url_for("computers_page", error="Computer not found."))

    location, error = normalize_location(request.form.get("location"))
    if error:
        return redirect(url_for("computers_page", error=error))

    db.execute("UPDATE computers SET location = ? WHERE id = ?", (location, computer_id))
    db.commit()
    if (location or None) != (computer["location"] or None):
        log_audit_event(
            actor_admin_id=session.get("admin_id"),
            target_admin_id=None,
            action_type="computer_updated",
            details=audit_change("Location", computer["location"] or "Unassigned", location or "Unassigned"),
            target_type="computer",
            target_label=computer["hostname"],
        )
    if location:
        message = f"{computer['hostname']} moved to {location}."
    else:
        message = f"{computer['hostname']} is now unassigned."
    return redirect(url_for("computers_page", success=message))


@app.route("/computers/<int:computer_id>/edit", methods=["POST"])
@admin_required
def update_computer_fields(computer_id):
    """Set the technician-owned fields from the edit drawer. Hostname, IP,
    status and last-seen come from the agent, so are never editable."""
    db = get_db()
    computer = db.execute(
        """
        SELECT id, hostname, location, device_type,
               asset_status, purchase_date, vendor, warranty_until
        FROM computers WHERE id = ?
        """,
        (computer_id,),
    ).fetchone()
    if not computer:
        return redirect(url_for("computers_page", error="Computer not found."))

    location, error = normalize_location(request.form.get("location"))
    if error:
        return redirect(url_for("computers_page", error=error))

    device_type = (request.form.get("device_type") or "").strip()
    if device_type and device_type not in ALLOWED_DEVICE_TYPES:
        return redirect(url_for("computers_page", error="Invalid device type."))

    # The asset half. Validated as a block before anything is written, so a
    # bad warranty date cannot leave a half-applied row behind.
    asset, error = validate_asset_fields(request.form)
    if error:
        return redirect(url_for("computers_page", error=error))

    location_changed = (location or None) != (computer["location"] or None)
    device_changed = (device_type or None) != (computer["device_type"] or None)
    asset_changed = {
        field: asset[field] != (computer[field] or None)
        for field in ("asset_status", "purchase_date", "vendor", "warranty_until")
    }
    if not location_changed and not device_changed and not any(asset_changed.values()):
        return redirect(url_for("computers_page"))

    db.execute(
        """
        UPDATE computers
        SET location = ?, device_type = ?,
            asset_status = ?, purchase_date = ?, vendor = ?, warranty_until = ?
        WHERE id = ?
        """,
        (
            location,
            device_type or None,
            asset["asset_status"],
            asset["purchase_date"],
            asset["vendor"],
            asset["warranty_until"],
            computer_id,
        ),
    )
    db.commit()

    changed = []
    if location_changed:
        changed.append(f"moved to {location}" if location else "unassigned")
    if device_changed:
        changed.append(f"set to {device_type}" if device_type else "device type cleared")
    # Retiring a machine takes it out of the deploy picker, so it is called
    # out by name rather than folded into a generic "asset details updated".
    if asset_changed["asset_status"]:
        changed.append(f"marked {asset_status_label(asset['asset_status'])}")
    detail_fields = ("purchase_date", "vendor", "warranty_until")
    if any(asset_changed[field] for field in detail_fields):
        changed.append("asset details updated")

    parts = []
    if location_changed:
        parts.append(audit_change("Location", computer["location"] or "Unassigned", location or "Unassigned"))
    if device_changed:
        parts.append(audit_change("Device type", computer["device_type"] or "Not set", device_type or "Not set"))
    # Compared as words: NULL and "In use" are the same state (see
    # ALLOWED_ASSET_STATUSES), so posting one over the other changed nothing.
    old_status = asset_status_label(computer["asset_status"])
    new_status = asset_status_label(asset["asset_status"])
    if old_status != new_status:
        parts.append(audit_change("Status", old_status, new_status))
    if any(asset_changed[field] for field in detail_fields):
        parts.append("Purchase date, vendor or warranty updated")
    if parts:
        retired = old_status != new_status and new_status == ASSET_STATUS_RETIRED
        log_audit_event(
            actor_admin_id=session.get("admin_id"),
            target_admin_id=None,
            action_type="computer_retired" if retired else "computer_updated",
            details="; ".join(parts),
            target_type="computer",
            target_label=computer["hostname"],
        )
    return redirect(url_for(
        "computers_page",
        success=f"{computer['hostname']}: {', '.join(changed)}.",
    ))


@app.route("/computers/<int:computer_id>/device-type", methods=["POST"])
@admin_required
def update_computer_device_type(computer_id):
    db = get_db()
    computer = db.execute(
        "SELECT id, hostname, device_type FROM computers WHERE id = ?", (computer_id,)
    ).fetchone()
    if not computer:
        return redirect(url_for("computers_page", error="Computer not found."))

    device_type = (request.form.get("device_type") or "").strip()
    # Empty is legitimate - it clears the field back to "Not set".
    if device_type and device_type not in ALLOWED_DEVICE_TYPES:
        return redirect(url_for("computers_page", error="Invalid device type."))

    db.execute(
        "UPDATE computers SET device_type = ? WHERE id = ?",
        (device_type or None, computer_id),
    )
    db.commit()
    if (device_type or None) != (computer["device_type"] or None):
        log_audit_event(
            actor_admin_id=session.get("admin_id"),
            target_admin_id=None,
            action_type="computer_updated",
            details=audit_change("Device type", computer["device_type"] or "Not set", device_type or "Not set"),
            target_type="computer",
            target_label=computer["hostname"],
        )
    label = device_type or "not set"
    return redirect(url_for("computers_page", success=f"{computer['hostname']} is now {label}."))


@app.route("/equipment")
@admin_required
def equipment_page():
    """Equipment, by room: an item belongs to a LOCATION, so forty cables
    in Lab 1 is one row whose quantity means exactly that."""
    db = get_db()
    items = equipment_rows(db)
    return render_template(
        "equipment.html",
        equipment=items,
        equipment_total=equipment_total_quantity(items),
        locations=known_locations(db),
        equipment_categories=existing_equipment_categories(db),
        allowed_asset_statuses=ALLOWED_ASSET_STATUSES,
        max_location_length=MAX_LOCATION_LENGTH,
        equipment_error=request.args.get("error"),
        equipment_success=request.args.get("success"),
    )


@app.route("/equipment/add", methods=["POST"])
@admin_required
def add_equipment():
    """Record one row: one room, one category, one quantity."""
    db = get_db()
    values, error = validate_equipment_fields(request.form)
    if error:
        return redirect(url_for("equipment_page", error=error))

    db.execute(
        """
        INSERT INTO equipment
            (location, category, manufacturer, model, serial_number,
             quantity, asset_status, purchase_date, vendor, warranty_until, notes,
             created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            values["location"], values["category"], values["manufacturer"],
            values["model"], values["serial_number"], values["quantity"],
            values["asset_status"], values["purchase_date"], values["vendor"],
            values["warranty_until"], values["notes"], session.get("admin_id"),
        ),
    )
    db.commit()

    log_audit_event(
        actor_admin_id=session.get("admin_id"),
        target_admin_id=None,
        action_type="equipment_added",
        details=f"Added {values['quantity']} x {values['category']} to {values['location']}",
        target_type="equipment",
        target_label=values["category"],
    )
    count = values["quantity"]
    noun = values["category"] if count == 1 else f"{count} x {values['category']}"
    return redirect(url_for(
        "equipment_page",
        success=f"{noun} added to {values['location']}.",
    ))


@app.route("/equipment/<int:equipment_id>/edit", methods=["POST"])
@admin_required
def update_equipment(equipment_id):
    db = get_db()
    item = db.execute("SELECT * FROM equipment WHERE id = ?", (equipment_id,)).fetchone()
    if not item:
        return redirect(url_for("equipment_page", error="That item no longer exists."))

    values, error = validate_equipment_fields(request.form)
    if error:
        return redirect(url_for("equipment_page", error=error))

    db.execute(
        """
        UPDATE equipment
        SET location = ?, category = ?, manufacturer = ?, model = ?,
            serial_number = ?, quantity = ?, asset_status = ?,
            purchase_date = ?, vendor = ?, warranty_until = ?, notes = ?
        WHERE id = ?
        """,
        (
            values["location"], values["category"], values["manufacturer"],
            values["model"], values["serial_number"], values["quantity"],
            values["asset_status"], values["purchase_date"], values["vendor"],
            values["warranty_until"], values["notes"], equipment_id,
        ),
    )
    db.commit()

    def moved(*fields):
        return any((values[field] or None) != (item[field] or None) for field in fields)

    parts = []
    if moved("location"):
        parts.append(audit_change("Location", item["location"], values["location"]))
    if moved("category"):
        parts.append(audit_change("Category", item["category"], values["category"]))
    if moved("quantity"):
        parts.append(audit_change("Quantity", item["quantity"], values["quantity"]))
    # As words, as for a computer: NULL and "In use" are the same condition.
    if asset_status_label(item["asset_status"]) != asset_status_label(values["asset_status"]):
        parts.append(audit_change("Condition", asset_status_label(item["asset_status"]),
                                  asset_status_label(values["asset_status"])))
    if moved("manufacturer", "model", "serial_number"):
        parts.append("Manufacturer, model or serial number updated")
    if moved("purchase_date", "vendor", "warranty_until"):
        parts.append("Purchase date, vendor or warranty updated")
    if moved("notes"):
        parts.append("Notes edited")
    if parts:
        log_audit_event(
            actor_admin_id=session.get("admin_id"),
            target_admin_id=None,
            action_type="equipment_updated",
            details="; ".join(parts),
            target_type="equipment",
            target_label=values["category"],
        )

    # Moving a room is worth naming in the message: it is the one edit that
    # changes where somebody would physically go to find the thing.
    if values["location"] != item["location"]:
        message = f"{values['category']} moved to {values['location']}."
    else:
        message = f"{values['category']} updated."
    return redirect(url_for("equipment_page", success=message))


@app.route("/equipment/<int:equipment_id>/delete", methods=["POST"])
@admin_required
def delete_equipment(equipment_id):
    """Audited: a record that vanishes with no trace cannot be told apart
    from one that was never entered."""
    db = get_db()
    item = db.execute("SELECT * FROM equipment WHERE id = ?", (equipment_id,)).fetchone()
    if not item:
        return redirect(url_for("equipment_page", error="That item no longer exists."))

    db.execute("DELETE FROM equipment WHERE id = ?", (equipment_id,))
    db.commit()

    label = equipment_label(item["manufacturer"], item["model"]) or item["category"]
    log_audit_event(
        actor_admin_id=session.get("admin_id"),
        target_admin_id=None,
        action_type="equipment_deleted",
        details=f"Deleted {item['quantity']} x {item['category']} ({label}) from {item['location']}",
        target_type="equipment",
        target_label=item["category"],
    )
    return redirect(url_for(
        "equipment_page",
        success=f"{item['category']} removed from {item['location']}.",
    ))


# --- QR sticker labels ---------------------------------------------------
#
# On the case, not the screen: the machine that will not boot is the one
# most worth reporting. Generated server-side, not by a CDN library: the
# admin LAN may have no internet, and empty boxes would only show on paper.
try:
    import qrcode as qrcode_lib
except ImportError:  # pragma: no cover - depends on the install
    # Same degradation stance as _report_token_secret: the feature goes
    # quiet and says why, rather than taking a route down with an import
    # error at startup.
    qrcode_lib = None

# Not a security limit: 200 labels is 17 pages, and thousands of inline
# PNGs would stall the request.
QR_LABELS_MAX = 200


def public_report_origin():
    """Origin for printed QR codes: PUBLIC_REPORT_HOST when tunnelled
    (phones are often off the LAN), else this request's. https is fixed:
    Cloudflare terminates TLS and .app is HSTS-preloaded."""
    if PUBLIC_REPORT_HOST:
        return "https://" + PUBLIC_REPORT_HOST
    return request.url_root.rstrip("/")


def computer_report_url(hostname):
    """The /report link for one machine, signed when signing is configured.

    The token is omitted rather than faked when REPORT_QR_SECRET is unset:
    a sticker with no token still opens the right form and still preselects
    nothing, which is the same outcome as a hand-typed URL. Printing a
    placeholder would produce stickers that look signed and are not.
    """
    params = {"computer": hostname}
    token = sign_computer(hostname)
    if token:
        params["t"] = token
    return public_report_origin() + "/report?" + urlencode(params)


def qr_png_data_uri(payload):
    """One QR code as an inline PNG data URI: an image route could let
    print run before every code arrived. Error correction Q, since
    stickers in a public lab get scuffed."""
    code = qrcode_lib.QRCode(
        version=None,
        error_correction=qrcode_lib.constants.ERROR_CORRECT_Q,
        box_size=10,
        border=2,
    )
    code.add_data(payload)
    code.make(fit=True)
    image = code.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


@app.route("/computers/qr-labels")
@admin_required
def computer_qr_labels():
    """A printable sheet of QR stickers: ?ids=1,2,3, else every machine,
    matching Export."""
    if qrcode_lib is None:
        return redirect(url_for(
            "computers_page",
            error="QR labels need the qrcode and pillow packages installed.",
        ))

    db = get_db()

    # Parsed digit by digit rather than trusted: anything that is not a
    # plain integer is dropped instead of reaching the query.
    requested = []
    for chunk in (request.args.get("ids") or "").split(","):
        chunk = chunk.strip()
        if chunk.isdigit():
            requested.append(int(chunk))
    requested = requested[:QR_LABELS_MAX]

    order = (
        "ORDER BY (location IS NULL OR TRIM(location) = '') ASC, "
        "LOWER(location) ASC, LOWER(hostname) ASC"
    )
    if requested:
        # The placeholders are built from the COUNT of ids, never from the
        # ids themselves - the values still go through parameter binding.
        marks = ",".join("?" * len(requested))
        rows = db.execute(
            f"SELECT id, hostname, location FROM computers WHERE id IN ({marks}) {order}",
            requested,
        ).fetchall()
    else:
        rows = db.execute(
            f"SELECT id, hostname, location FROM computers {order}"
        ).fetchall()

    if not rows:
        return redirect(url_for(
            "computers_page", error="No computers to print labels for."
        ))

    labels = [{
        "hostname": row["hostname"],
        "location": row["location"],
        "qr": qr_png_data_uri(computer_report_url(row["hostname"])),
        # Printed beside the QR so a camera is not the only way in. The
        # address goes on the label too - a code with nowhere to type it
        # is no use to somebody whose phone will not scan.
        "code": computer_short_code(row["hostname"]),
    } for row in rows]

    return render_template(
        "qr_labels.html",
        labels=labels,
        origin=public_report_origin(),
        # Surfaced so the sheet can warn ON SCREEN before anyone spends a
        # sheet of sticker paper. Unsigned labels still work as links, they
        # just cannot prove the scan happened in the lab, so every report
        # they produce would be tagged 'direct'.
        signing_enabled=sign_computer(rows[0]["hostname"]) is not None,
        auto_print=request.args.get("print") == "1",
        truncated=len(requested) == QR_LABELS_MAX,
    )


# --- Report photos and reporter identity ---------------------------------
#
# /report takes uploads from anyone on the LAN with no login, so a photo is
# only accepted if it passes BOTH an extension check and a magic-byte check -
# a .png that is not really a PNG is rejected rather than stored. Size is
# checked per file by seeking the stream: MAX_CONTENT_LENGTH is deliberately
# NOT set app-wide, because installer uploads on the Software Library side are
# routinely tens of megabytes and a global cap would break them.
PHOTO_SIGNATURES = (
    (b"\xff\xd8\xff", None),          # JPEG
    (b"\x89PNG\r\n\x1a\n", None),    # PNG
    (b"RIFF", b"WEBP"),                # WEBP: RIFF....WEBP
)


def photo_bytes_look_like_image(header):
    """True when the first bytes of the upload match a format we accept."""
    for prefix, marker in PHOTO_SIGNATURES:
        if not header.startswith(prefix):
            continue
        if marker is None or header[8:12] == marker:
            return True
    return False


def report_photo_list(raw_value):
    """photo_paths is stored as a JSON array. Returns [] for NULL, empty or
    malformed values - a report with an unreadable photo field should still
    open, just without photos."""
    if not raw_value:
        return []
    try:
        parsed = json.loads(raw_value)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, str) and item]


def with_report_photos(rows):
    """Rows as dicts carrying photo_urls (a Row is read-only)."""
    items = []
    for row in rows:
        item = dict(row)
        item["photo_urls"] = [
            url_for("static", filename=path)
            for path in report_photo_list(item.get("photo_paths"))
        ]
        items.append(item)
    return items


def save_report_photos(files):
    """Validate and store up to MAX_REPORT_PHOTOS uploads.

    Returns (paths, error_message). On ANY failure nothing is kept: files
    already written during this call are removed, so a half-saved set can
    never end up attached to a report that was rejected.
    """
    candidates = [item for item in files if item and item.filename]
    if not candidates:
        return [], None
    if len(candidates) > MAX_REPORT_PHOTOS:
        return [], f"Please attach at most {MAX_REPORT_PHOTOS} photos."

    saved_paths = []
    saved_full_paths = []

    def discard(message):
        for full_path in saved_full_paths:
            try:
                os.remove(full_path)
            except OSError:
                pass
        return [], message

    for upload in candidates:
        _, extension = os.path.splitext(secure_filename(upload.filename))
        extension = extension.lower()
        if extension not in ALLOWED_PHOTO_EXTENSIONS:
            return discard("Photos must be JPG, PNG or WEBP files.")

        upload.stream.seek(0, os.SEEK_END)
        size = upload.stream.tell()
        upload.stream.seek(0)
        if size == 0:
            return discard("One of the photos was empty.")
        if size > MAX_REPORT_PHOTO_BYTES:
            return discard("Each photo must be 5 MB or smaller.")

        header = upload.stream.read(12)
        upload.stream.seek(0)
        if not photo_bytes_look_like_image(header):
            return discard("One of the files is not a valid image.")

        generated_name = f"{uuid.uuid4().hex}{extension}"
        full_path = os.path.join(REPORT_PHOTO_FOLDER, generated_name)
        try:
            upload.save(full_path)
        except OSError:
            return discard("A photo could not be saved. Please try again.")
        saved_full_paths.append(full_path)
        saved_paths.append(f"report_photos/{generated_name}")

    return saved_paths, None


# --- QR sticker tokens ---------------------------------------------------
# Each lab computer gets a printed sticker encoding
#   /report?computer=<hostname>&t=<token>
# where the token is an HMAC of the hostname under a server-side secret. The
# point is NOT authentication - there are no student accounts - it is proof of
# physical presence: you cannot produce a valid token without having stood in
# front of a real sticker, and you cannot forge one without the secret.
#
# Deliberately NOT a gate. A report with no token is still accepted, just
# recorded with a different report_source. Rejecting would mean a genuinely
# broken PC goes unreported because someone forwarded the link to a classmate
# who is standing right next to it.
REPORT_TOKEN_LENGTH = 16


def _report_token_secret():
    """The signing secret, or None when unset.

    Returning None rather than raising is deliberate: a missing secret must
    degrade the sticker path to the same behaviour as no sticker at all, not
    take the whole report form down. A form nobody can reach is a worse
    outcome than a report tagged 'direct'.
    """
    secret = os.environ.get("REPORT_QR_SECRET", "").strip()
    return secret or None


def sign_computer(hostname):
    """Token for a hostname, or None if signing is not configured.

    Truncated to 16 hex chars - 64 bits. Full-length would make the QR denser
    to scan for no benefit: forging one still needs the secret, and an online
    guessing attack against a rate-limited form is not a realistic path.
    """
    secret = _report_token_secret()
    if not secret or not hostname:
        return None
    digest = hmac.new(
        secret.encode("utf-8"),
        hostname.strip().lower().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest[:REPORT_TOKEN_LENGTH]


def verify_computer_token(hostname, token):
    """True when token is the valid signature for hostname.

    compare_digest, not ==, so the comparison does not leak how many leading
    characters were correct through its timing.
    """
    if not hostname or not token:
        return False
    expected = sign_computer(hostname)
    if not expected:
        return False
    return hmac.compare_digest(expected, token.strip().lower())


# --- Typed sticker codes -------------------------------------------------
#
# The same proof of presence as the QR token, in a form a person can read
# off a sticker and type. It exists because a camera is not always
# available or willing: a cracked lens, a locked-down phone, a scanner app
# that will not focus on a scuffed label. Without this the gate below
# would turn every one of those into "you cannot report this at all".
#
# SIX characters from a 32-symbol alphabet is about a billion codes. That
# is not cryptographic, and it does not need to be: guessing one online
# means going through the rate limiter at five attempts per ten minutes.
# What it needs to be is short enough that somebody actually types it.
#
# I, L, O and U are left out. The first three are the pairs that get
# misread as 1 and 0 on a printed label, and U is dropped so the alphabet
# cannot spell short words by accident.
SHORT_CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
SHORT_CODE_LENGTH = 6

# What a person might type instead of what is printed. Applied before
# comparing, so "o" for zero and "l" for one are read as intended rather
# than rejected as a wrong code.
SHORT_CODE_LOOKALIKES = {"O": "0", "I": "1", "L": "1", "U": "V"}


def computer_short_code(hostname):
    """The printed code for a hostname, or None when signing is unset.

    Derived from a DIFFERENT message than sign_computer's, so the code and
    the token are independent outputs rather than two truncations of the
    same digest - knowing one must not shorten the search for the other.
    """
    secret = _report_token_secret()
    if not secret or not hostname:
        return None
    digest = hmac.new(
        secret.encode("utf-8"),
        (hostname.strip().lower() + "|code").encode("utf-8"),
        hashlib.sha256,
    ).digest()
    # Straight from the raw bytes rather than from hex: hex would only
    # ever use 16 of the 32 symbols, throwing away half the alphabet and a
    # third of the code space for nothing.
    value = int.from_bytes(digest[:8], "big")
    code = ""
    for _ in range(SHORT_CODE_LENGTH):
        code = SHORT_CODE_ALPHABET[value % len(SHORT_CODE_ALPHABET)] + code
        value //= len(SHORT_CODE_ALPHABET)
    return code


def normalise_short_code(raw):
    """What the person typed, reduced to what could have been printed:
    spaces, hyphens and case are discarded rather than refused."""
    text = (raw or "").strip().upper()
    cleaned = ""
    for character in text:
        if not character.isalnum():
            continue
        cleaned += SHORT_CODE_LOOKALIKES.get(character, character)
    return cleaned[:SHORT_CODE_LENGTH]


def resolve_short_code(db, raw):
    """The hostname a typed code belongs to, or None.

    Scans the computers table rather than reversing the code - an HMAC
    cannot be reversed, which is the point of using one. A few hundred
    machines is a few hundred HMACs, which is microseconds, and it means
    the student never has to know the hostname: the code identifies the
    machine on its own.
    """
    typed = normalise_short_code(raw)
    if len(typed) < SHORT_CODE_LENGTH:
        return None
    for row in db.execute("SELECT hostname FROM computers").fetchall():
        expected = computer_short_code(row["hostname"])
        # compare_digest on every row, and no early exit on a match, so
        # the loop takes the same time whichever machine it belongs to.
        if expected and hmac.compare_digest(expected, typed):
            return row["hostname"]
    return None


# --- Abuse controls for the public report form ---------------------------
#
# /report is now on the open internet, takes no login, and its address is
# printed on stickers. Nothing about that is wrong, but it does mean the
# only thing between this table and a shell script is what is written
# here. Three cheap layers, none of which can refuse a real report.
REPORT_RATE_WINDOW_SECONDS = 600
REPORT_RATE_MAX_PER_IP = 5
# Lower than the per-IP limit on purpose. One leaked photo of a sticker is
# the realistic abuse route, and it can only ever target ONE machine - so
# capping per computer bounds that damage in a way per-IP cannot.
REPORT_RATE_MAX_PER_COMPUTER = 3
# Three seconds is unreachable for a genuine fill: a computer has to be
# chosen, a problem type picked, and at least ten characters typed.
REPORT_MIN_FILL_SECONDS = 3

# Guesses at the six-character code, per sender per window. The code is
# one in a billion, so this is not really about exhausting it - it is
# about making a script that grinds through the space cost real time.
# Higher than the report limit because typing a code wrong is an
# ordinary thing a person does, and locking someone out after two
# typos would be its own denial of service.
REPORT_CODE_ATTEMPT_MAX = 10

# The whole /report request, photos included. Three photos at the 5 MB limit
# and the text fields fit under it with room to spare, and the page shrinks
# photos far below that before sending - so only a request that went round
# the page's own checks ever meets this.
REPORT_MAX_REQUEST_BYTES = 16 * 1024 * 1024
REPORT_TOO_LARGE_MESSAGE = "The photos are too large. Attach up to 3, 5 MB each."

# In memory, guarded by a lock. Waitress serves this app with THREADS in
# one process, so a module-level dict really is shared across requests -
# which would not hold if the server were ever switched to a multi-process
# model. The counts reset on restart; that is acceptable for a limit whose
# window is ten minutes, and it keeps a hot path off the database.
_report_rate_lock = threading.Lock()
_report_rate_hits = {}


def report_client_key():
    """Who to count this request against.

    CF-Connecting-IP FIRST, and this is the part that must not be got
    wrong: every tunnelled request reaches Flask over loopback, so
    remote_addr is 127.0.0.1 for the entire internet. Keying on it would
    make all public visitors one visitor - five reports from anywhere at
    all would then lock out everybody.

    The header is only trustworthy on the public host, where Cloudflare
    sets it. The origin also answers on the LAN, and there anyone can send
    the header themselves - believing it there let one machine reset every
    limit by changing it on each request. On the LAN remote_addr is the
    real client, so that is what counts there, header or not.
    """
    if request_is_public_host():
        forwarded = (request.headers.get("CF-Connecting-IP") or "").strip()
        if forwarded:
            # A proxy chain would give a comma-separated list; take the first.
            return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _prune_report_hits(now):
    """Drop timestamps that have fallen out of the window.

    Called on both read and write so the dict cannot grow without bound -
    an unpruned key per attacking IP is itself a slow memory leak.
    Caller must hold the lock.
    """
    for key in list(_report_rate_hits):
        fresh = [seen for seen in _report_rate_hits[key]
                 if now - seen < REPORT_RATE_WINDOW_SECONDS]
        if fresh:
            _report_rate_hits[key] = fresh
        else:
            del _report_rate_hits[key]


def report_rate_limit_error(client_key, hostname):
    """An honest message when a limit is reached, else None.

    Honest rather than silent because a student with three genuinely
    broken machines is a real person who needs to know why the third one
    would not send. Someone quietly discovering their reports vanish is a
    far worse outcome than a spammer learning the threshold.
    """
    now = time.time()
    with _report_rate_lock:
        _prune_report_hits(now)
        by_ip = len(_report_rate_hits.get("ip:" + client_key, []))
        by_pc = len(_report_rate_hits.get("pc:" + (hostname or "").strip().lower(), []))
    if by_ip >= REPORT_RATE_MAX_PER_IP:
        return ("You have sent several reports already. Please wait a few "
                "minutes before sending another one.")
    if by_pc >= REPORT_RATE_MAX_PER_COMPUTER:
        return ("This computer has already been reported a few times just "
                "now. Please wait a few minutes, or tell a lab technician "
                "in person.")
    return None


def record_report_submission(client_key, hostname):
    """Count one ACCEPTED report.

    Recorded on success only, never on a rejected attempt. A student who
    trips the description validator four times has not sent anything, and
    burning their quota for it would punish exactly the person least able
    to work out what went wrong.
    """
    now = time.time()
    with _report_rate_lock:
        _report_rate_hits.setdefault("ip:" + client_key, []).append(now)
        _report_rate_hits.setdefault(
            "pc:" + (hostname or "").strip().lower(), []).append(now)
        _prune_report_hits(now)


def sign_form_timestamp(issued_at=None):
    """A stamped, signed 'this form was rendered at' value.

    Signed with the same secret as the QR tokens so the elapsed time
    cannot simply be back-dated by editing the field. Falls back to a bare
    timestamp when no secret is configured, which is weaker but still
    catches an unsophisticated submitter - and matches how the rest of
    this feature degrades rather than failing.
    """
    stamp = str(int(time.time() if issued_at is None else issued_at))
    secret = _report_token_secret()
    if not secret:
        return stamp
    digest = hmac.new(
        secret.encode("utf-8"), stamp.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:REPORT_TOKEN_LENGTH]
    return stamp + "." + digest


def form_fill_seconds(value):
    """Seconds since the form was rendered, or None if that is unknowable.

    None means 'cannot tell', and every caller treats that as fine. This
    fails OPEN deliberately: a stale cached page, a browser that dropped
    the field, a restored tab - none of those are abuse, and refusing a
    real report over a missing timestamp would be the worst trade in this
    whole file.
    """
    raw = (value or "").strip()
    if not raw:
        return None
    secret = _report_token_secret()
    if secret:
        stamp, separator, digest = raw.partition(".")
        if not separator:
            return None
        expected = hmac.new(
            secret.encode("utf-8"), stamp.encode("utf-8"), hashlib.sha256
        ).hexdigest()[:REPORT_TOKEN_LENGTH]
        if not hmac.compare_digest(expected, digest.strip().lower()):
            return None
    else:
        stamp = raw
    try:
        issued = int(stamp)
    except ValueError:
        return None
    elapsed = time.time() - issued
    # A negative value means a clock skew, not a fast human. Unknowable.
    return elapsed if elapsed >= 0 else None


def save_avatar(upload):
    """Validate and store one profile picture.

    Returns (relative_path, error_message) - exactly one is ever non-None.
    The path returned is relative to static/, matching photo_paths.

    The extension check alone is not a boundary: the browser's accept=
    attribute and the JS size check in profile.html are conveniences that a
    crafted POST simply ignores, so size and magic bytes are re-checked here.
    """
    if not upload or not upload.filename:
        return None, "Choose an image first."

    _, extension = os.path.splitext(secure_filename(upload.filename))
    extension = extension.lower()
    if extension not in ALLOWED_PHOTO_EXTENSIONS:
        return None, "Profile picture must be a JPG, PNG or WEBP file."

    # Size by seeking the stream, the same way save_report_photos does.
    # MAX_CONTENT_LENGTH stays unset app-wide on purpose - installer uploads
    # are routinely tens of megabytes and a global cap would break them.
    upload.stream.seek(0, os.SEEK_END)
    size = upload.stream.tell()
    upload.stream.seek(0)
    if size == 0:
        return None, "That image file was empty."
    if size > MAX_AVATAR_BYTES:
        return None, "Profile picture must be 2 MB or smaller."

    header = upload.stream.read(12)
    upload.stream.seek(0)
    if not photo_bytes_look_like_image(header):
        return None, "That file is not a valid image."

    generated_name = f"{uuid.uuid4().hex}{extension}"
    try:
        upload.save(os.path.join(AVATAR_FOLDER, generated_name))
    except OSError:
        return None, "The picture could not be saved. Please try again."

    return f"avatars/{generated_name}", None


def delete_avatar_file(relative_path):
    """Remove a stored avatar. Best-effort and path-guarded: the resolved
    path must sit inside AVATAR_FOLDER, so a tampered database value cannot
    make this delete something elsewhere on disk."""
    if not relative_path:
        return
    base = os.path.abspath(AVATAR_FOLDER)
    candidate = os.path.abspath(
        os.path.join(app.root_path, "static", relative_path)
    )
    if not candidate.startswith(base + os.sep):
        return
    try:
        os.remove(candidate)
    except OSError:
        pass


def delete_report_photos(raw_value):
    """Best-effort file cleanup when a report is deleted. Containment-guarded
    the same way the installer paths are, so a tampered value cannot reach
    outside static/report_photos."""
    base = os.path.abspath(REPORT_PHOTO_FOLDER)
    for path in report_photo_list(raw_value):
        full_path = os.path.abspath(os.path.join(app.root_path, "static", path))
        if not full_path.startswith(base + os.sep):
            continue
        try:
            os.remove(full_path)
        except OSError:
            pass


# A name: letters - accented ones and n with a tilde included - spaces and
# . ' -. The accented block is split around U+00D7 and U+00F7, the
# multiplication and division signs, which sit inside it and are not letters.
_NAME_ALLOWED = re.compile(r"[A-Za-z\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u024f.'\- ]+")
# A typed program and a department: the same letters, plus digits, spaces
# and . & -.
_REPORTER_TEXT_ALLOWED = re.compile(r"[A-Za-z\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u024f0-9.&\- ]+")
# Year and section: one year from 1 to 5 and one section letter, once spaces
# and hyphens are gone - "4c", "4 c" and "4-C" are all 4C.
_SECTION = re.compile(r"[1-5][A-Za-z]")


def reporter_fields_error(form):
    """(values, field, error); field is the input the message belongs
    under. Students give a student number and section, faculty a name.
    Year level is not asked: "BSIT 3A" already carries it."""
    role = (form.get("reporter_role") or "").strip().lower()
    name = " ".join((form.get("reporter_name") or "").split())
    student_number = " ".join((form.get("reporter_student_number") or "").split())
    program = " ".join((form.get("reporter_program") or "").split())
    program_other = " ".join((form.get("reporter_program_other") or "").split())
    section = " ".join((form.get("reporter_section") or "").split())
    department = " ".join((form.get("reporter_department") or "").split())

    if role not in ALLOWED_REPORTER_ROLES:
        return None, "reporter_role", "Please select whether you are a student or faculty."
    if not name:
        return None, "reporter_name", "Your name is required."
    if len(name) < 2 or len(name) > 60:
        return None, "reporter_name", "Name must be between 2 and 60 characters."
    if not _NAME_ALLOWED.fullmatch(name):
        return None, "reporter_name", "Name can only contain letters, spaces, and . ' - characters."
    # "--" and ". ." passed the line above: every character allowed, no name.
    if len(_DESCRIPTION_LETTER.findall(name)) < 2:
        return None, "reporter_name", "Enter your name using letters."

    if role != "student":
        # Faculty answer a different set entirely: name and department, no
        # student number, program or section. Those fields are not merely
        # optional for faculty - they are not asked, so anything that
        # arrived in them is discarded rather than stored.
        if not department:
            return None, "reporter_department", "Department is required."
        if len(department) > 60:
            return None, "reporter_department", "Department must be 60 characters or fewer."
        if not _REPORTER_TEXT_ALLOWED.fullmatch(department):
            return None, "reporter_department", ("Department can only contain letters, numbers, "
                                                 "spaces and . & - characters.")
        if len(department) < 2 or not _DESCRIPTION_LETTER.search(department):
            return None, "reporter_department", "Enter your department name."
        return {
            "role": role, "name": name, "program": None, "department": department,
            "student_number": None, "section": None, "year_level": None,
        }, None, None

    if not student_number:
        return None, "reporter_student_number", "Student number is required."
    # A BulSU student number is ten digits, 2023000000. Typed with spaces or
    # a hyphen after the year it is still that number, so those are removed
    # first - and only the ten digits are saved, so one student is one value.
    # [0-9], not \d: \d also matches digits from other scripts.
    student_number = student_number.replace(" ", "").replace("-", "")
    if not re.fullmatch(r"[0-9]{10}", student_number):
        return None, "reporter_student_number", "Enter your 10-digit student number, like 2023000000."
    if not program:
        return None, "reporter_program", "Please select your program."
    if program == REPORT_PROGRAM_OTHER:
        # Typed under "Other...": a programme NAME, so it takes dots and
        # ampersands (B.S.I.T., BS Ed & Arts) as well as accented letters.
        if not program_other:
            return None, "reporter_program_other", "Type your program."
        if not _REPORTER_TEXT_ALLOWED.fullmatch(program_other):
            return None, "reporter_program_other", ("Program can only contain letters, numbers, "
                                                    "spaces and . & - characters.")
        if not 2 <= len(program_other) <= 60 or not _DESCRIPTION_LETTER.search(program_other):
            return None, "reporter_program_other", "Type your program's name, 2 to 60 characters."
        program = program_other
    elif program not in REPORT_PROGRAM_CODES:
        return None, "reporter_program", "Please select your program from the list."
    if not section:
        return None, "reporter_section", "Year and section is required."
    section = section.replace(" ", "").replace("-", "")
    if not _SECTION.fullmatch(section):
        return None, "reporter_section", "Enter your year and section, like 4C."
    section = section.upper()
    return {
        "role": role, "name": name, "program": program, "department": None,
        "student_number": student_number, "section": section, "year_level": None,
    }, None, None


def validate_reporter_fields(form):
    """(values_dict, error_message), for a caller that does not need to know
    which field the message is about."""
    values, _, error = reporter_fields_error(form)
    return values, error


# --- Demo mode (survey only) ---------------------------------------------
#
# Exists for one purpose: a Google Form survey where respondents open the
# public report page from their own phones to see how it works. They have
# no sticker, no code, and are not on the lab network, so the ordinary
# gate correctly refuses them and they would only ever see the code box.
#
# ?demo=1 lets them through to the REAL form - same fields, same
# validation, same photo picker - and then throws the submission away.
# Nothing is written: no report, no photo on disk, no notification.
#
# Off unless DEMO_MODE_ENABLED is set, so it is a switch you turn on for
# the survey and off afterwards without touching code. That also means the
# ordinary deployed system has no such path at all, which is the honest
# thing to be able to say about a page that was deliberately gated.
DEMO_MODE_ENABLED = (os.getenv("DEMO_MODE_ENABLED", "").strip().lower()
                     in ("1", "true", "yes", "on"))


def demo_mode_requested():
    """True when this request asked for the demo AND demo mode is enabled.

    Both halves matter. Without the env check, ?demo=1 would be a way past
    the gate for anyone who guessed the parameter.
    """
    if not DEMO_MODE_ENABLED:
        return False
    return (request.args.get("demo") == "1"
            or request.form.get("demo") == "1")


def lan_report_address():
    """The address to try the lab-computer shortcut with, or None.

    The shortcut lets a request in when its address matches a registered
    computer's. That address is whatever an agent last reported, and the
    agent API takes no credentials - so it is only safe where the address
    really is the visitor's own, which rules out two cases:

      - the public host. Everything tunnelled in is an internet visitor,
        and none of them is a lab computer, whatever address it arrives
        from.
      - loopback. Every tunnelled request reaches Flask from 127.0.0.1, so
        one forged heartbeat claiming 127.0.0.1 used to wave the entire
        internet through - and this still holds with PUBLIC_REPORT_HOST
        unset, which is why it is checked separately.

    Internet visitors keep the sticker and the typed code; only this
    shortcut is withdrawn from them.
    """
    if request_is_public_host():
        return None
    address = (request.remote_addr or "").strip()
    if not address:
        return None
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return None
    # ::ffff:127.0.0.1 is loopback too. Python 3.13+ says so on its own;
    # older versions only once it is unwrapped.
    if (getattr(parsed, "ipv4_mapped", None) or parsed).is_loopback:
        return None
    return address


def report_access_state(db):
    """How (or whether) this request has proven it is in the lab.

    Returns a dict describing what the caller is allowed to see. The
    same three doors the POST accepts, checked here so the FORM ITSELF
    is not served to somebody who simply typed the address - which was
    the hole: gating the submit still showed everyone the page.
    """
    hostname = (request.args.get("computer") or "").strip()
    supplied = (request.args.get("t") or "").strip()

    # A valid signature grants access even when the hostname is no
    # longer in the computers table. A sticker outlives the machine it
    # was printed for, and somebody holding a genuinely signed link has
    # proven exactly what the token exists to prove - they just cannot
    # have a computer preselected for them.
    signed = bool(hostname) and verify_computer_token(hostname, supplied)

    known = None
    if signed:
        known = db.execute(
            "SELECT hostname FROM computers WHERE hostname = ? LIMIT 1",
            (hostname,),
        ).fetchone()

    lan = None
    lan_address = lan_report_address()
    if not signed and lan_address:
        lan = db.execute(
            "SELECT hostname FROM computers WHERE ip_address = ? LIMIT 1",
            (lan_address,),
        ).fetchone()

    return {
        "allowed": bool(signed or lan),
        "token": supplied if signed else "",
        "token_hostname": hostname if signed else "",
        "preselect": (known["hostname"] if known else
                      (lan["hostname"] if lan else None)),
        # Only affects how the report is labelled afterwards, never
        # whether it is accepted, so there is nothing to gain by faking
        # it - both values mean the same thing to the gate.
        "via_code": request.args.get("c") == "1",
    }


@app.route("/report/access", methods=["POST"])
def report_access():
    """Turn a typed sticker code into the link the QR would have given.

    No session is involved, deliberately. Redirecting to the ordinary
    signed /report URL means typing the code and scanning it end up on
    exactly the same code path, already built and already tested - and
    it means access does not depend on a signed cookie.
    """
    db = get_db()
    typed = request.form.get("qr_code")
    # Put back in the box after any refusal, so one wrong character is fixed
    # rather than the whole code typed again. Cut to the box's own maxlength.
    typed_back = (typed or "")[:10]

    # Attempts are counted whether or not they succeed - unlike report
    # submissions, where only accepted ones count. A wrong guess IS the
    # thing being limited here, so not counting it would limit nothing.
    client_key = report_client_key()
    now = time.time()
    with _report_rate_lock:
        _prune_report_hits(now)
        attempts = len(_report_rate_hits.get("code:" + client_key, []))
        _report_rate_hits.setdefault("code:" + client_key, []).append(now)
    if attempts >= REPORT_CODE_ATTEMPT_MAX:
        return render_template(
            "report_gate.html",
            typed_code=typed_back,
            error_message=("Too many attempts. Please wait a few minutes, "
                           "or ask a lab technician for help."),
        ), 429

    if not normalise_short_code(typed):
        return render_template(
            "report_gate.html",
            typed_code=typed_back,
            error_message="Enter the code printed on the computer.",
        )

    matched = resolve_short_code(db, typed)
    if not matched:
        # One message for a wrong code and for a code belonging to no
        # machine. Telling them apart would turn this box into a way to
        # find out which codes are real.
        return render_template(
            "report_gate.html",
            typed_code=typed_back,
            error_message=("That code was not recognised. Check the "
                           "sticker on the computer and try again."),
        )

    return redirect(url_for(
        "student_problem_report_form",
        computer=matched,
        t=sign_computer(matched),
        c="1",
    ))


@app.route("/robots.txt")
def robots_txt():
    """Keep the whole site out of search results.

    The report form is meant to be found by scanning a sticker in a
    computer lab, not by searching for it. One link from anywhere - a
    classmate posting the address, a QR scanner app that logs where it
    sent you - is enough for a crawler to find and index it otherwise.

    Disallow is a REQUEST, not a control: it is obeyed by the crawlers
    that matter and ignored by anything malicious. The X-Robots-Tag
    header and the meta tag on the pages themselves say the same thing
    a second and third time, and the access gate is what actually keeps
    anybody out.
    """
    return Response(
        "User-agent: *\nDisallow: /\n",
        mimetype="text/plain",
    )


@app.after_request
def discourage_indexing(response):
    """Say "do not index" on every response, in a header.

    A header rather than only a meta tag because a meta tag lives inside
    HTML - it does nothing for a photo under /static, which is precisely
    the sort of URL that gets indexed and then shows up in an image
    search. Applied app-wide: nothing here should ever be in a search
    result, admin pages least of all.
    """
    response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
    return response


# Pages whose HTML is specific to one visit and must not be kept by the
# browser. Named by ENDPOINT rather than path, the same way
# guard_public_host() matches, so no encoding trick can miss the check.
NO_STORE_ENDPOINTS = {"student_problem_report_form", "report_access"}


@app.after_request
def no_store_report_pages(response):
    """Stop the browser keeping the report form.

    Two problems, one cause. The form is rendered for a signed link, and a
    browser is entitled to cache it - so after sending a report, pressing
    Back re-showed the FORM from cache with no request to the server,
    which meant the gate never ran and a used link looked like a live one.

    The worse half is a shared lab computer. Without this, the next
    student to press Back sees the previous student's form still filled
    in: their name, student number, section and year level. That is not a
    security bypass - they had proven presence to get there - it is a
    privacy leak between two people at the same desk, which is the reason
    this is worth a header rather than a shrug.

    no-store rather than no-cache: no-cache still stores the page and
    revalidates, and Safari's back-forward cache honours it inconsistently.
    no-store is the one that means "do not keep this".

    Scoped to the two public report endpoints. Applying it app-wide would
    make every admin page uncacheable for no gain, and those are behind a
    login on a LAN.
    """
    if request.endpoint in NO_STORE_ENDPOINTS:
        response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
        # Named for the same reason X-Robots-Tag is a header: an old proxy
        # or an HTTP/1.0 client will ignore Cache-Control and honour these.
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# Receipts to whoever filed a report. Fixed text only: the report number,
# the computer, the problem type and when it was sent - never the
# description, the name or anything else typed into the form, so the form
# cannot be used to put someone's words in another person's inbox.
#
# A few per address per hour. Past that the report is still saved; only
# the receipt is skipped. A student reporting a row of broken machines gets
# the first few, and somebody typing another person's address can cause at
# most this many fixed-text emails an hour.
REPORT_RECEIPTS_PER_HOUR = 5
REPORT_RECEIPT_LIMITER = SlidingWindowLimiter(
    "report-receipt", REPORT_RECEIPTS_PER_HOUR, 60 * 60, 120, 5000, SlidingWindowLimiter.REFUSE,
)


def report_submitted_message():
    """The toast after a report is sent. It never names the address - that
    would put it in the redirect URL, in the history of a shared lab PC -
    never promises delivery, and reads the same whether or not this
    particular receipt was rate-limited."""
    if SMTP_HOST:
        return ("Report submitted. A confirmation email is on its way to the address you gave - "
                "if you don't see it, check your spam folder.")
    return "Report submitted."


def report_details(report_id, hostname, problem_type):
    """The rows every email to a reporter carries. All three are AppEase's
    own values - the id, a registered computer's hostname and one of the
    fixed problem types - never anything the reporter typed."""
    return [("Report number", f"#{report_id}"), ("Computer", hostname),
            ("Problem type", problem_type.capitalize())]


def report_receipt_body(report_id, hostname, problem_type, submitted):
    return render_email(
        "Report received",
        "AppEase has received your report. Thank you for reporting.",
        rows=report_details(report_id, hostname, problem_type)
        + [("Submitted", f"{submitted} (Philippine time)")],
        status=("Received", "info"),
    )


def send_report_receipt(email, report_id, hostname, problem_type, reported_at):
    """Queue the receipt for a report that is ALREADY saved. Never waits on
    the mail server and never raises; with mail not configured it does
    nothing at all."""
    if not SMTP_HOST or not email:
        return
    if not REPORT_RECEIPT_LIMITER.hit(email):
        app.logger.warning(f"Report receipt to {email_domain(email)} skipped: "
                           f"{REPORT_RECEIPTS_PER_HOUR} an hour already sent")
        return
    send_email_later(email, f"AppEase received your report #{report_id}",
                     report_receipt_body(report_id, hostname, problem_type, report_email_time(reported_at)))


def report_email_time(stored_utc):
    """"07 Oct 2026, 2:35 PM" in Manila time, for the emails to a reporter.
    stored_utc is a stored timestamp; None means now."""
    stamp = (_parse_stored_utc(str(stored_utc)) if stored_utc else None) or datetime.utcnow()
    local = stamp + MANILA_OFFSET
    return local.strftime("%d %b %Y, ") + local.strftime("%I:%M %p").lstrip("0")


# ---- One email when a report is finished ------------------------------------
#
# One email, ever, when a report first reaches Resolved or No fault found.
# Fixed text only, like the receipt. A bulk finish runs as ONE background
# job, in a lane, each send WAITING for a slot rather than being dropped by
# EMAIL_MAX_IN_FLIGHT. resolved_notified_at is claimed atomically just
# before each send, so no report is emailed twice, and one never reached
# stays unmarked for a later finish.
STATUS_EMAIL_SLOT_WAIT_SECONDS = 120
_status_email_lane = threading.Lock()


def report_finished_email(report_id, hostname, problem_type, status, when):
    """(subject, body) for a report that reached a finished status."""
    details = report_details(report_id, hostname, problem_type)
    if status == "no_fault_found":
        return (
            f"Your AppEase report #{report_id} has been checked",
            render_email(
                "Report checked - no fault found",
                "A technician checked the computer in your report and found no fault. "
                "Thank you for reporting.",
                rows=details + [("Checked", f"{when} (Philippine time)")],
                closing=["If the problem is still there, please send a new report."],
                status=("No fault found", "neutral"),
            ),
        )
    return (
        f"Your AppEase report #{report_id} has been resolved",
        render_email(
            "Report resolved",
            "Your report has been marked as resolved. Thank you for reporting.",
            rows=details + [("Resolved", f"{when} (Philippine time)")],
            closing=["If the problem comes back, please send a new report."],
            status=("Resolved", "good"),
        ),
    )


def queue_report_finished_emails(report_ids, status):
    """Called after a commit that moved these reports INTO status. Queues one
    background job for all of them; with mail not configured it does
    nothing at all, and nothing is marked."""
    if not SMTP_HOST or not report_ids or status not in FINISHED_REPORT_STATUSES:
        return
    ids = sorted(set(report_ids))
    when = report_email_time(None)
    run_in_background(lambda: _send_report_finished_emails(ids, status, when))


def _send_report_finished_emails(report_ids, status, when):
    """The job: one email after another, in the one lane."""
    with _status_email_lane:
        db = get_db()
        try:
            marks = ",".join("?" * len(report_ids))
            pending = [row for row in db.execute(
                f"SELECT id, computer_hostname, problem_type, reporter_email FROM problem_reports "
                f"WHERE id IN ({marks}) AND reporter_email IS NOT NULL AND reporter_email <> '' "
                f"AND resolved_notified_at IS NULL ORDER BY id",
                report_ids,
            ).fetchall()]
            for index, row in enumerate(pending):
                if not _email_slots.acquire(timeout=STATUS_EMAIL_SLOT_WAIT_SECONDS):
                    # Every slot busy for this long: mail is jammed. Stop, and
                    # say which reports were never tried - they stay unmarked.
                    for skipped in pending[index:]:
                        app.logger.warning(
                            f"Report #{skipped['id']} email to {email_domain(skipped['reporter_email'])} "
                            f"not sent: no sending slot free for {STATUS_EMAIL_SLOT_WAIT_SECONDS}s"
                        )
                    return
                try:
                    claimed = db.execute(
                        "UPDATE problem_reports SET resolved_notified_at = ? "
                        "WHERE id = ? AND resolved_notified_at IS NULL",
                        (datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), row["id"]),
                    ).rowcount
                    db.commit()
                    if claimed:
                        subject, body = report_finished_email(
                            row["id"], row["computer_hostname"], row["problem_type"], status, when)
                        send_email(row["reporter_email"], subject, body)
                finally:
                    _email_slots.release()
        except Exception as exc:
            app.logger.warning(f"Report emails stopped: {exc.__class__.__name__}")
        finally:
            db.close()


# The fields a refused report is shown again with. Photos are not among them:
# no page can fill a file picker, so the reporter is asked to attach them
# again instead.
REPORT_FORM_FIELDS = (
    "reporter_role", "reporter_name", "reporter_email", "reporter_department",
    "reporter_student_number", "reporter_program", "reporter_program_other", "reporter_section",
    "computer_hostname", "problem_type", "description",
)
# Longer than any field's own limit, so a value refused for its length comes
# back whole - while a hand-made request cannot make the page echo megabytes.
REPORT_ECHO_MAX_LENGTH = 2000


def posted_report_values():
    return {name: normalise_line_breaks(request.form.get(name))[:REPORT_ECHO_MAX_LENGTH]
            for name in REPORT_FORM_FIELDS}


def report_post_access(db):
    """How a POST to /report proved it is in the lab.

    The same dict report_access_state() gives a GET - so a refused report is
    shown the form again carrying the proof it arrived with - plus "source",
    which is what the report is recorded as. Three ways in, and a student
    always has at least one of them:

      sticker - scanned the QR code. Works from a phone on cellular
                data, and works when the machine will not boot.
      code    - typed the six characters printed under the QR. The
                way through when a camera will not cooperate.
      lan     - came from a registered lab computer, which is what
                the desktop shortcut does. The way through when the
                sticker is gone entirely.

    The sticker covers a dead PC; the LAN covers a missing sticker. Each one
    covers the other's failure, which is what makes refusing the fourth case,
    "direct", reasonable rather than cruel.
    """
    posted_token = (request.form.get("qr_token") or "").strip()
    posted_token_host = (request.form.get("qr_hostname") or "").strip()
    typed_code = request.form.get("qr_code")
    access = {"token": "", "token_hostname": "", "preselect": None}

    # Checked against the hostname the token was ISSUED for, not the one
    # finally submitted: the dropdown stays editable on purpose, and a student
    # who scans the sticker on a dead PC and reports the one beside it has
    # still proven they are in the lab.
    if posted_token and verify_computer_token(posted_token_host, posted_token):
        # "code" when the token came from the code-entry page rather than a
        # camera. Both are equally trusted - the marker only records which
        # one the person actually used.
        source = "code" if request.form.get("via_code") == "1" else "sticker"
        known = db.execute("SELECT hostname FROM computers WHERE hostname = ? LIMIT 1",
                           (posted_token_host,)).fetchone()
        access.update(token=posted_token, token_hostname=posted_token_host,
                      preselect=known["hostname"] if known else None)
    else:
        coded = resolve_short_code(db, typed_code) if normalise_short_code(typed_code) else None
        lan_address = None if coded else lan_report_address()
        lan = db.execute(
            "SELECT hostname FROM computers WHERE ip_address = ? LIMIT 1", (lan_address,),
        ).fetchone() if lan_address else None
        if coded:
            # A code posted with the report itself. Shown again, the form
            # carries the signed link that code would have opened.
            source = "code"
            access.update(token=sign_computer(coded) or "", token_hostname=coded, preselect=coded)
        elif lan:
            source = "lan"
            access["preselect"] = lan["hostname"]
        else:
            source = "direct"
    access.update(source=source, allowed=source != "direct", via_code=source == "code")
    return access


def render_report_form(db, access, demo, values=None, error_field=None, error_text=None,
                       error_message=None, success_message=None, photos_note=None, status=200):
    """The report form, for a visit (values None) or a refused report shown
    again with what was typed. access carries the proof of presence
    forward; error_field/error_text place a message, error_message toasts."""
    computers = db.execute(
        "SELECT hostname, status FROM computers ORDER BY hostname ASC"
    ).fetchall()

    if values is not None:
        reporter_email_prefill = values["reporter_email"]
    else:
        # Someone signed in to AppEase reaches this page from Report a
        # Problem; their account's address is filled in for them. Editable,
        # empty when the account has none, and saved only on the report -
        # never written back to the account.
        reporter_email_prefill = ""
        if session.get("admin_id"):
            account = db.execute("SELECT email FROM admins WHERE id = ?", (session["admin_id"],)).fetchone()
            reporter_email_prefill = (account["email"] or "") if account else ""

    return render_template(
        "report.html",
        reporter_email_prefill=reporter_email_prefill,
        computers=computers,
        pre_selected_hostname=access["preselect"],
        qr_token=access["token"],
        # The hostname the token was ISSUED for, which is not always the
        # one preselected - a sticker whose machine has been
        # decommissioned signs a name no longer in the table. The POST
        # verifies against this, so it has to be the signed one.
        qr_hostname=access["token_hostname"],
        arrived_by_code=access["via_code"],
        success_message=success_message,
        error_message=error_message,
        form_started=sign_form_timestamp(),
        demo_mode=demo,
        values=values or {},
        error_field=error_field,
        error_text=error_text,
        photos_note=photos_note,
        report_programs=REPORT_PROGRAMS,
        report_program_other=REPORT_PROGRAM_OTHER,
    ), status


@app.route("/report", methods=["GET", "POST"])
def student_problem_report_form():
    db = get_db()

    if request.method == "POST":
        # Before anything reads the form: request.form parses the whole
        # multipart body, photos and all, so a cap checked after it would
        # already have paid for what it refuses. The public host has its own
        # cap in guard_public_host(); this is the one the lab network meets.
        # Nothing typed can be shown again - the form was never read - but
        # the page's own photo checks keep a real report far below it.
        if (request.content_length or 0) > REPORT_MAX_REQUEST_BYTES:
            return redirect(url_for("student_problem_report_form", error=REPORT_TOO_LARGE_MESSAGE))

        # Honeypot, checked before anything else. The field is positioned
        # off-screen and hidden from assistive technology, so no person
        # can put anything in it - a value here means an automated
        # submitter that filled every input it found.
        #
        # Accepted and DISCARDED rather than refused: the caller gets the
        # ordinary success page and no signal about what gave it away. A
        # visible rejection would just tell whoever wrote the script which
        # field to skip next time.
        if (request.form.get("website") or "").strip():
            return redirect(url_for(
                "student_problem_report_form",
                success=report_submitted_message(),
            ))

        computer_hostname = (request.form.get("computer_hostname") or "").strip()
        problem_type = (request.form.get("problem_type") or "").strip().lower()
        description = clean_description(request.form.get("description"))

        # Every refusal below shows the form AGAIN: what was typed still in
        # it, the message under the field it is about, and the same proof of
        # presence it arrived with, so nobody rescans a sticker over a typo.
        # A request that has proven no presence has no form to go back to,
        # and goes to the gate with the message, as before.
        #
        # Where it came from is worked out on first use rather than here, so
        # an accepted demo submission still touches nothing below the demo
        # check.
        access = None

        def post_access():
            nonlocal access
            if access is None:
                access = report_post_access(db)
            return access

        def refuse(message, field=None, status=400):
            demo = demo_mode_requested()
            if not demo and not post_access()["allowed"]:
                return redirect(url_for("student_problem_report_form", error=message))
            photos = [item for item in request.files.getlist("report_photos") if item and item.filename]
            photos_note = None
            if photos and field != "report_photos":
                photos_note = ("Please attach your photo again." if len(photos) == 1
                               else "Please attach your photos again.")
            return render_report_form(
                db, post_access(), demo, values=posted_report_values(),
                error_field=field, error_text=message if field else None,
                error_message=None if field else message,
                photos_note=photos_note, status=status,
            )

        # How long the form was open. Unlike the honeypot this one is told
        # to the person, because it is the only check here that could
        # conceivably fire on a human - and a student who believes they
        # reported a broken machine that nobody will ever look at is a far
        # worse failure than a spammer learning a threshold exists.
        elapsed = form_fill_seconds(request.form.get("form_started"))
        if elapsed is not None and elapsed < REPORT_MIN_FILL_SECONDS:
            return refuse("That was sent very quickly. Please check your details and send it again.")

        # The demo short-circuits here: before the rate limit, before the
        # validators, and long before save_report_photos() would write a
        # file. Placed this early ON PURPOSE - anything below this line
        # either touches the database, the disk, or the shared rate-limit
        # dictionary, and a survey respondent must touch none of them.
        #
        # The rate limit in particular would break the survey outright:
        # every tunnelled request arrives as 127.0.0.1, so the per-sender
        # cap would lock out every respondent after the fifth. Skipping it
        # is safe precisely because nothing is being written.
        #
        # Returns the same success page a real report gets, so what a
        # respondent sees end to end is what a student sees.
        if demo_mode_requested():
            return redirect(url_for(
                "student_problem_report_form",
                demo="1",
                success=("Demo only - this report was NOT saved."),
            ))

        # Before the validators, so a flood costs one dict lookup rather
        # than a gibberish scan and three database round trips.
        client_key = report_client_key()
        rate_error = report_rate_limit_error(client_key, computer_hostname)
        if rate_error:
            return refuse(rate_error, status=429)

        # Where this came from, worked out BEFORE the validators and long
        # before any photo is written to disk: it decides whether the report
        # is accepted at all. report_post_access() describes the three ways in.
        report_source = post_access()["source"]

        if report_source == "direct":
            # Says what to do, not just that this was refused. Someone
            # reading it is standing next to a broken computer and needs a
            # route through, and all three are named because which one
            # works depends on what is broken.
            return redirect(url_for(
                "student_problem_report_form",
                error=("Scan the QR sticker on the computer to send a report. "
                       "If it will not scan, type the code printed under it. "
                       "You can also use the AppEase shortcut on any working "
                       "lab computer."),
            ))

        reporter, reporter_field, reporter_error = reporter_fields_error(request.form)
        if reporter_error:
            return refuse(reporter_error, reporter_field)

        # Required, and checked by report_email_error() - the twin of the
        # page's own check - so "jols@g" is refused rather than silently
        # undeliverable. Trimmed exactly as the page trims it.
        reporter_email = trim_like_browser(request.form.get("reporter_email"))
        email_error = report_email_error(reporter_email)
        if email_error:
            return refuse(email_error, "reporter_email")

        if not computer_hostname:
            return refuse("Please select a computer.", "computer_hostname")

        if problem_type not in ALLOWED_PROBLEM_TYPES:
            return refuse("Please select a valid problem type.", "problem_type")

        description_problem = description_error(description)
        if description_problem:
            return refuse(description_problem, "description")

        computer = db.execute(
            "SELECT hostname FROM computers WHERE hostname = ?",
            (computer_hostname,),
        ).fetchone()
        if not computer:
            return refuse("Selected computer was not found.", "computer_hostname")

        # Photos LAST: everything above can reject the submission, and there is
        # no point writing files for a report that is not going to exist.
        photo_paths, photo_error = save_report_photos(request.files.getlist("report_photos"))
        if photo_error:
            return refuse(photo_error, "report_photos")

        # report_source was decided at the top of this branch, where it
        # also acted as the gate - a request that got this far has already
        # proven it came from the lab.
        cursor = db.execute(
            """
            INSERT INTO problem_reports (computer_hostname, problem_type, description, status,
                                         reporter_role, reporter_name, reporter_student_number,
                                         reporter_program, reporter_section, reporter_year_level,
                                         reporter_department, photo_paths, report_source, reporter_email)
            VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (computer_hostname, problem_type, description,
             reporter["role"], reporter["name"], reporter["student_number"],
             reporter["program"], reporter["section"], reporter["year_level"],
             reporter["department"],
             json.dumps(photo_paths) if photo_paths else None,
             report_source, reporter_email),
        )
        add_notification(
            db,
            "problem_report",
            f"New {problem_type} report from {computer_hostname} ({reporter['name']})",
            severity="warning",
            # ?open=<id> lands on the report with its Details panel up.
            link_url=url_for("problem_reports_page", open=cursor.lastrowid),
            audience="all",
        )
        db.commit()
        # After the commit, so a report that failed to save never counts
        # against the person who tried to send it.
        record_report_submission(client_key, computer_hostname)
        # The receipt, after the commit for the same reason, and in the
        # background: the report is saved whatever the mail server does.
        report_id = cursor.lastrowid
        saved = db.execute("SELECT reported_at FROM problem_reports WHERE id = ?", (report_id,)).fetchone()
        send_report_receipt(reporter_email, report_id, computer_hostname, problem_type,
                            saved["reported_at"] if saved else None)
        return redirect(url_for("student_problem_report_form", success=report_submitted_message()))

    # THE PAGE, not just the submit. Someone who types the address into a
    # phone has proven nothing, and until now they were shown the whole
    # form and only refused at the end - which is a worse experience than
    # being asked up front, and puts the computer list in front of
    # somebody who has no business seeing it.
    access = report_access_state(db)
    # The demo is a fourth door, and the ONLY one that writes nothing. It
    # is checked here rather than inside report_access_state() so the three
    # real proofs stay exactly as they were - this cannot weaken them,
    # because it is a separate branch that leads somewhere else.
    demo = demo_mode_requested()
    if not access["allowed"] and not demo:
        return render_template(
            "report_gate.html",
            error_message=request.args.get("error"),
            success_message=request.args.get("success"),
        )

    return render_report_form(
        db, access, demo,
        success_message=request.args.get("success"),
        error_message=request.args.get("error"),
    )


@app.route("/deploy", methods=["POST"])
@admin_required
def create_deployment_jobs():
    db = get_db()

    software_id_raw = request.form.get("software_id", "").strip()
    selected_hostnames = [host.strip() for host in request.form.getlist("computer_hostnames") if host.strip()]

    if not software_id_raw.isdigit():
        return redirect(url_for("dashboard", deploy_error="Please select a software item."))

    if not selected_hostnames:
        return redirect(url_for("dashboard", deploy_error="Please select at least one computer."))

    software_id = int(software_id_raw)
    software_exists = db.execute(
        "SELECT id, name, version FROM software WHERE id = ?", (software_id,)
    ).fetchone()
    if not software_exists:
        return redirect(url_for("dashboard", deploy_error="Selected software no longer exists."))

    placeholders = ",".join(["?"] * len(selected_hostnames))
    valid_rows = db.execute(
        f"SELECT hostname FROM computers WHERE hostname IN ({placeholders})",
        selected_hostnames,
    ).fetchall()
    valid_hostnames = [row["hostname"] for row in valid_rows]

    if not valid_hostnames:
        return redirect(url_for("dashboard", deploy_error="No valid computers were selected."))

    batch_id = uuid.uuid4().hex
    created_count = 0
    skipped_count = 0
    uninstalling_count = 0
    for hostname in valid_hostnames:
        skip = install_skip_reason(db, hostname, software_id)
        if skip == "duplicate":
            skipped_count += 1
            continue
        if skip == "uninstalling":
            uninstalling_count += 1
            continue

        db.execute(
            "INSERT INTO jobs (computer_hostname, software_id, status, batch_id, software_name, software_version) "
            "VALUES (?, ?, 'pending', ?, ?, ?)",
            (hostname, software_id, batch_id, software_exists["name"], software_exists["version"]),
        )
        created_count += 1

    db.commit()

    success_message = f"Created {created_count} job(s)."
    if skipped_count:
        success_message += f" Skipped {skipped_count} duplicate pending job(s)."
    if uninstalling_count:
        success_message += f" Skipped {uninstalling_count} where an uninstall is under way."

    # One entry per deploy action, not per job - see the note at the top of
    # log_audit_event. Nothing is logged when every job was a duplicate.
    if created_count:
        label = software_label(software_exists["name"], software_exists["version"])
        log_audit_event(
            actor_admin_id=session.get("admin_id"),
            target_admin_id=None,
            action_type="deployment_created",
            details=f"Deployed '{label}' to {created_count} computer(s)",
            target_type="deployment",
            target_label=label,
        )

    return redirect(url_for("dashboard", deploy_success=success_message))


@app.route("/deploy-all", methods=["POST"])
@admin_required
def deploy_all():
    db = get_db()

    software_id_raw = request.form.get("software_id", "").strip()
    if not software_id_raw.isdigit():
        return redirect(url_for("dashboard", deploy_error="Please select a software item."))

    software_id = int(software_id_raw)
    software_exists = db.execute(
        "SELECT id, name, version FROM software WHERE id = ?", (software_id,)
    ).fetchone()
    if not software_exists:
        return redirect(url_for("dashboard", deploy_error="Selected software no longer exists."))

    online_rows = db.execute(
        f"SELECT hostname FROM computers WHERE status = 'online' AND {NOT_RETIRED_CLAUSE}",
        (ASSET_STATUS_RETIRED,),
    ).fetchall()
    if not online_rows:
        return redirect(url_for("dashboard", deploy_error="No computers are currently online."))

    batch_id = uuid.uuid4().hex
    created_count = 0
    skipped_count = 0
    uninstalling_count = 0
    for row in online_rows:
        hostname = row["hostname"]
        skip = install_skip_reason(db, hostname, software_id)
        if skip == "duplicate":
            skipped_count += 1
            continue
        if skip == "uninstalling":
            uninstalling_count += 1
            continue

        db.execute(
            "INSERT INTO jobs (computer_hostname, software_id, status, batch_id, software_name, software_version) "
            "VALUES (?, ?, 'pending', ?, ?, ?)",
            (hostname, software_id, batch_id, software_exists["name"], software_exists["version"]),
        )
        created_count += 1

    db.commit()

    success_message = f"Deployed to {created_count} computer(s)."
    if skipped_count:
        success_message += f" Skipped {skipped_count} duplicate pending job(s)."
    if uninstalling_count:
        success_message += f" Skipped {uninstalling_count} where an uninstall is under way."

    if created_count:
        label = software_label(software_exists["name"], software_exists["version"])
        log_audit_event(
            actor_admin_id=session.get("admin_id"),
            target_admin_id=None,
            action_type="deployment_created",
            details=f"Deployed '{label}' to all {created_count} online computer(s)",
            target_type="deployment",
            target_label=label,
        )

    return redirect(url_for("dashboard", deploy_success=success_message))


@app.route("/deploy", methods=["GET"])
@admin_required
def deploy_page():
    """The Deploy Software page."""
    db = get_db()
    # Retired machines are left out of the picker entirely rather than
    # shown greyed out. A retired PC is not in the lab any more, so it is
    # not a choice a technician should have to skip past every time.
    computers = db.execute(
        f"""
        SELECT * FROM computers
        WHERE {NOT_RETIRED_CLAUSE}
        ORDER BY (location IS NULL OR TRIM(location) = '') ASC,
                 LOWER(location) ASC,
                 LOWER(hostname) ASC
        """,
        (ASSET_STATUS_RETIRED,),
    ).fetchall()
    software_items = db.execute(
        "SELECT id, name, version, type, category FROM software ORDER BY LOWER(name) ASC"
    ).fetchall()
    return render_template(
        "deploy.html",
        computers=computers,
        software_items=software_items,
        deploy_error=request.args.get("deploy_error"),
    )


@app.route("/deploy-batch", methods=["POST"])
@admin_required
def create_batch_deployment_jobs():
    db = get_db()

    software_ids = [sid.strip() for sid in request.form.getlist("software_ids") if sid.strip().isdigit()]
    selected_hostnames = [host.strip() for host in request.form.getlist("computer_hostnames") if host.strip()]
    target_all_online = request.form.get("target_all_online", "") == "1"

    # Posted with fetch() so the progress modal survives, so every exit has
    # a JSON and a redirect shape; the redirect is the fallback if fetch()
    # fails, and where an expired session lands.
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    def _fail(message):
        if wants_json:
            return jsonify({"ok": False, "error": message}), 400
        # Back to Deploy, not the dashboard: the form the user needs to fix
        # is there. Success still lands on the dashboard, where the queued
        # jobs are visible in Recent Deployment Jobs.
        return redirect(url_for("deploy_page", deploy_error=message))

    if not software_ids:
        return _fail("Please select at least one software item.")

    if not target_all_online and not selected_hostnames:
        return _fail("Please select at least one computer.")

    software_placeholders = ",".join(["?"] * len(software_ids))
    valid_software_rows = db.execute(
        f"SELECT id, name, version FROM software WHERE id IN ({software_placeholders})",
        software_ids,
    ).fetchall()
    valid_software_ids = [row["id"] for row in valid_software_rows]
    valid_software_labels = [software_label(row["name"], row["version"]) for row in valid_software_rows]
    # Snapshotted onto each job row so deployment history keeps the name even
    # after the software entry is deleted.
    software_meta = {row["id"]: (row["name"], row["version"]) for row in valid_software_rows}

    if not valid_software_ids:
        return _fail("Selected software no longer exists.")

    offline_count = 0
    retired_count = 0

    if target_all_online:
        online_rows = db.execute(
            f"SELECT hostname FROM computers WHERE status = 'online' AND {NOT_RETIRED_CLAUSE}",
            (ASSET_STATUS_RETIRED,),
        ).fetchall()
        target_hostnames = [row["hostname"] for row in online_rows]

        if not target_hostnames:
            return _fail("No computers are currently online.")
    else:
        placeholders = ",".join(["?"] * len(selected_hostnames))
        valid_rows = db.execute(
            f"SELECT hostname, status, asset_status FROM computers WHERE hostname IN ({placeholders})",
            selected_hostnames,
        ).fetchall()
        # Retired machines are dropped here rather than by a WHERE clause so
        # they can be COUNTED. Silently deploying to nine of the ten
        # computers someone ticked, under a message that says ten, is worse
        # than telling them one was retired.
        retired_count = sum(1 for row in valid_rows if row["asset_status"] == ASSET_STATUS_RETIRED)
        deployable_rows = [row for row in valid_rows if row["asset_status"] != ASSET_STATUS_RETIRED]
        target_hostnames = [row["hostname"] for row in deployable_rows]
        offline_count = sum(1 for row in deployable_rows if row["status"] != "online")

        if not target_hostnames:
            if retired_count:
                return _fail("Every computer you selected is retired.")
            return _fail("No valid computers were selected.")

    batch_id = uuid.uuid4().hex
    created_count = 0
    skipped_count = 0
    uninstalling_count = 0
    for software_id in valid_software_ids:
        for hostname in target_hostnames:
            skip = install_skip_reason(db, hostname, software_id)
            if skip == "duplicate":
                skipped_count += 1
                continue
            if skip == "uninstalling":
                uninstalling_count += 1
                continue

            snapshot_name, snapshot_version = software_meta.get(software_id, (None, None))
            db.execute(
                "INSERT INTO jobs (computer_hostname, software_id, status, batch_id, software_name, software_version) "
                "VALUES (?, ?, 'pending', ?, ?, ?)",
                (hostname, software_id, batch_id, snapshot_name, snapshot_version),
            )
            created_count += 1

    db.commit()

    success_message = f"Created {created_count} job(s) across {len(target_hostnames)} computer(s)."
    if skipped_count:
        success_message += f" Skipped {skipped_count} duplicate pending job(s)."
    if uninstalling_count:
        success_message += f" Skipped {uninstalling_count} where an uninstall is under way."
    if not target_all_online and offline_count:
        success_message += f" {offline_count} target computer(s) are offline — their jobs will run when they come online."
    if retired_count:
        success_message += f" Skipped {retired_count} retired computer(s)."

    # One entry per batch, naming the software when there is only one.
    if created_count:
        if len(valid_software_labels) == 1:
            label = valid_software_labels[0]
        else:
            label = f"{len(valid_software_labels)} software items"
        log_audit_event(
            actor_admin_id=session.get("admin_id"),
            target_admin_id=None,
            action_type="deployment_created",
            details=(
                f"Deployed {label} to {len(target_hostnames)} computer(s)"
                f" ({created_count} job(s) created)"
            ),
            target_type="deployment",
            target_label=label,
        )

    if wants_json:
        # The modal polls on batch_id.
        return jsonify({
            "ok": True,
            "batch_id": batch_id,
            "created_count": created_count,
            "skipped_count": skipped_count,
            "uninstalling_count": uninstalling_count,
            "offline_count": 0 if target_all_online else offline_count,
            "computer_count": len(target_hostnames),
            "app_count": len(valid_software_ids),
            "message": success_message,
        }), 200

    return redirect(url_for("dashboard", deploy_success=success_message))


@app.route("/api/deployments/<batch_id>/progress", methods=["GET"])
@admin_required
def deployment_batch_progress(batch_id):
    """Live job status for one batch, for the progress modal. Read-only: it
    is polled every few seconds, and the stale-job writers emit
    notifications, so a modal left open overnight would spam the feed."""
    batch_id = (batch_id or "").strip()
    if not batch_id or len(batch_id) > 64 or not batch_id.isalnum():
        return jsonify({"error": "invalid batch id"}), 400

    db = get_db()
    rows = db.execute(
        """
        SELECT jobs.id, jobs.computer_hostname, jobs.status, jobs.retry_count,
               jobs.exit_code, jobs.result_reason, jobs.action,
               COALESCE(jobs.software_name, software.name, 'Removed software') AS software_name,
               COALESCE(jobs.software_version, software.version) AS software_version
        FROM jobs
        LEFT JOIN software ON software.id = jobs.software_id
        WHERE jobs.batch_id = ?
        ORDER BY LOWER(COALESCE(jobs.software_name, software.name, '')) ASC,
                 CASE jobs.status
                     WHEN 'failed' THEN 0
                     WHEN 'in_progress' THEN 1
                     WHEN 'pending' THEN 2
                     WHEN 'completed' THEN 3
                     ELSE 4
                 END ASC,
                 jobs.computer_hostname ASC
        """,
        (batch_id,),
    ).fetchall()

    counts = {"pending": 0, "in_progress": 0, "completed": 0, "failed": 0, "cancelled": 0, "removed": 0}
    groups = []
    group_index = {}
    for row in rows:
        status = row["status"]
        if status in counts:
            counts[status] += 1
        # Counted as the batch notification counts it, so the modal and the
        # bell never disagree on "N of M removed".
        if status == "completed" and row["result_reason"] != REMOVAL_UNCONFIRMED_REASON:
            counts["removed"] += 1
        # Grouped by app, not by computer: a batch is a few apps across many
        # machines, so by-computer would be ~30 headers wrapping 1-3 rows.
        label = software_label(row["software_name"], row["software_version"])
        if label not in group_index:
            group_index[label] = len(groups)
            groups.append({"label": label, "jobs": []})
        groups[group_index[label]]["jobs"].append({
            "hostname": row["computer_hostname"],
            "status": status,
            "retry_count": row["retry_count"] or 0,
            "result": job_result(status, row["exit_code"], row["result_reason"], row["action"]),
            # The modal's "Removed" pill: completed, and confirmed.
            "removed": status == "completed" and row["result_reason"] != REMOVAL_UNCONFIRMED_REASON,
        })

    total = len(rows)
    finished = counts["completed"] + counts["failed"] + counts["cancelled"]
    # A batch is one kind: /uninstall-batch queues only uninstalls, the
    # deploy routes only installs. The modal words itself from this.
    action = JOB_ACTION_UNINSTALL if rows and all(r["action"] == JOB_ACTION_UNINSTALL for r in rows) else "install"
    return jsonify({
        "batch_id": batch_id,
        "action": action,
        "total": total,
        "finished": finished,
        "counts": counts,
        "done": total > 0 and finished == total,
        "groups": groups,
    }), 200


def _bulk_job_ids():
    """Selected job ids from a bulk form, as integers, deduplicated.

    Unlike problem reports there is no per-admin ownership on jobs - any
    admin may act on any job, which is what the single-job routes already
    allow - so this only sanitises. Non-numeric values are dropped rather
    than erroring: the ids come from checkbox values, and a junk one means
    a tampered form, not a case worth reporting back to the user.
    """
    seen = []
    for raw in request.form.getlist("job_ids"):
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            continue
        if value not in seen:
            seen.append(value)
    return seen


@app.route("/jobs/bulk/cancel", methods=["POST"])
@admin_required
def bulk_cancel_jobs():
    """Cancel every selected job still pending. Partial by design (the WHERE
    is the guard). Not in_progress: the agent has started, so a cancelled
    record would lie about what the PC ends up with."""
    job_ids = _bulk_job_ids()
    if not job_ids:
        return redirect(url_for("dashboard", deploy_error="No jobs were selected."))

    db = get_db()
    placeholders = ",".join("?" * len(job_ids))
    affected = db.execute(
        f"SELECT id, batch_id FROM jobs WHERE id IN ({placeholders}) AND status = 'pending'",
        job_ids,
    ).fetchall()
    if not affected:
        return redirect(url_for("dashboard", deploy_error="None of the selected jobs were still pending."))

    ids = [row["id"] for row in affected]
    ids_sql = ",".join("?" * len(ids))
    db.execute(f"UPDATE jobs SET status = 'cancelled' WHERE id IN ({ids_sql})", ids)
    # Each batch checked once, as the single-job route checks it.
    for batch_id in {row["batch_id"] for row in affected if row["batch_id"]}:
        maybe_notify_batch_complete(db, batch_id)
    db.commit()
    log_audit_event(
        actor_admin_id=session.get("admin_id"),
        target_admin_id=None,
        action_type="job_cancelled",
        details=f"Cancelled {audit_count(len(ids), 'pending job')}: {audit_id_list(ids)}",
        target_type="job",
        target_label=audit_count(len(ids), "job"),
    )

    skipped = len(job_ids) - len(ids)
    noun = "job" if len(ids) == 1 else "jobs"
    message = f"{len(ids)} {noun} cancelled."
    if skipped:
        message += f" {skipped} skipped (not pending)."
    return redirect(url_for("dashboard", deploy_success=message))


@app.route("/jobs/bulk/retry", methods=["POST"])
@admin_required
def bulk_retry_jobs():
    """Re-queue every selected failed or cancelled job as a new row (the
    original stays in history), by job_retryable(), like retry_job()."""
    job_ids = _bulk_job_ids()
    if not job_ids:
        return redirect(url_for("dashboard", deploy_error="No jobs were selected."))

    db = get_db()
    placeholders = ",".join("?" * len(job_ids))
    candidates = db.execute(
        RETRY_JOB_SELECT + f"WHERE jobs.id IN ({placeholders})",
        job_ids,
    ).fetchall()

    # Same online rule as the dashboard, for the uninstalls' online check.
    mark_stale_computers_offline(db)
    requeued = 0
    requeued_ids = []
    missing_software = 0
    not_installed = 0
    for row in candidates:
        if not job_retryable(row):
            if row["status"] in ("failed", "cancelled"):
                if row["action"] is None:
                    missing_software += 1
                elif row["action"] == JOB_ACTION_UNINSTALL:
                    not_installed += 1
            continue
        # Checked one by one against what is already queued, the jobs this
        # loop re-queued included, so two selected attempts at one uninstall
        # queue it once.
        if retry_blocker(db, row):
            continue
        requeue_job(db, row)
        requeued += 1
        requeued_ids.append(row["id"])
    db.commit()

    if not requeued:
        return redirect(url_for("dashboard", deploy_error="None of the selected jobs could be retried."))
    log_audit_event(
        actor_admin_id=session.get("admin_id"),
        target_admin_id=None,
        action_type="job_retried",
        details=f"Retried {audit_count(requeued, 'job')}: {audit_id_list(requeued_ids)}",
        target_type="job",
        target_label=audit_count(requeued, "job"),
    )

    noun = "job" if requeued == 1 else "jobs"
    message = f"{requeued} {noun} re-queued."
    skipped = len(job_ids) - requeued
    if skipped:
        message += f" {skipped} skipped"
        if missing_software == skipped:
            message += " (removed software)."
        elif not_installed == skipped:
            message += " (no longer installed)."
        else:
            message += " (not retryable)."
    return redirect(url_for("dashboard", deploy_success=message))


@app.route("/jobs/bulk/delete", methods=["POST"])
@admin_required
def bulk_delete_jobs():
    """Remove selected FINISHED jobs from the history. Pending and running
    jobs are live instructions an agent is acting on, so they are skipped."""
    job_ids = _bulk_job_ids()
    if not job_ids:
        return redirect(url_for("dashboard", deploy_error="No jobs were selected."))

    db = get_db()
    placeholders = ",".join("?" * len(job_ids))
    deletable = db.execute(
        f"SELECT id FROM jobs WHERE id IN ({placeholders}) "
        "AND status NOT IN ('pending', 'in_progress')",
        job_ids,
    ).fetchall()
    if not deletable:
        return redirect(url_for(
            "dashboard",
            deploy_error="Pending and in-progress jobs cannot be deleted. Cancel them first.",
        ))

    ids = [row["id"] for row in deletable]
    ids_sql = ",".join("?" * len(ids))
    db.execute(f"DELETE FROM jobs WHERE id IN ({ids_sql})", ids)
    db.commit()

    skipped = len(job_ids) - len(ids)
    noun = "job" if len(ids) == 1 else "jobs"
    log_audit_event(
        actor_admin_id=session.get("admin_id"),
        target_admin_id=None,
        action_type="jobs_deleted",
        details=f"Deleted {len(ids)} finished {noun} from the job history",
        target_type="job",
        target_label=f"{len(ids)} {noun}",
    )
    message = f"{len(ids)} {noun} deleted."
    if skipped:
        message += f" {skipped} skipped (still running or queued)."
    return redirect(url_for("dashboard", deploy_success=message))


# ----------------------------------------------------------------------
# XLSX export.
#
# Server-side: an .xlsx is a ZIP of XML, and stdlib zipfile writes one with
# no new dependency. The CLIENT decides what to export (filters, columns
# and ticks live in data-table.js) and posts the finished grid as a FILE
# part: Werkzeug caps non-file form values (~500KB), which wide tables
# crossed. The payload is escaped into XML, never read back or used in SQL.
# ----------------------------------------------------------------------
XLSX_MAX_ROWS = 5000
XLSX_MAX_COLS = 64
XLSX_MAX_CELL = 500


def _xl_escape(value):
    """Escape a cell value for XML and strip characters XML cannot carry."""
    text = "" if value is None else str(value)
    text = text[:XLSX_MAX_CELL]
    # Control characters are illegal in XML 1.0 and make Excel refuse the
    # whole file, so they go before anything else.
    text = "".join(ch for ch in text if ch in "\t\n\r" or ord(ch) >= 32)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _xl_col(index):
    """0 -> A, 25 -> Z, 26 -> AA."""
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _build_xlsx(title, columns, rows):
    """.xlsx bytes for a header row plus data rows. inlineStr cells, so
    there is no sharedStrings table to keep in sync."""
    sheet = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        "<sheetData>",
    ]

    def write_row(number, values, style):
        cells = ['<row r="%d">' % number]
        for position, value in enumerate(values):
            cells.append(
                '<c r="%s%d" t="inlineStr" s="%d"><is><t xml:space="preserve">%s</t></is></c>'
                % (_xl_col(position), number, style, _xl_escape(value))
            )
        cells.append("</row>")
        sheet.append("".join(cells))

    write_row(1, columns, 1)
    for offset, row in enumerate(rows):
        write_row(offset + 2, row, 0)
    sheet.append("</sheetData></worksheet>")

    # Column widths sized off the content so nothing opens as ####.
    widths = []
    for position in range(len(columns)):
        longest = len(str(columns[position]))
        for row in rows:
            if position < len(row):
                longest = max(longest, len(str(row[position])))
        widths.append(
            '<col min="%d" max="%d" width="%d" customWidth="1"/>'
            % (position + 1, position + 1, min(max(longest + 2, 10), 60))
        )
    if widths:
        sheet.insert(2, "<cols>%s</cols>" % "".join(widths))

    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
        "</cellXfs></styleSheet>"
    )

    # Excel shows this as the tab name; it rejects []:*?/\\ and >31 chars.
    tab = re.sub(r"[\[\]:*?/\\]", " ", title or "Export").strip()[:31] or "Export"

    parts = {
        "[Content_Types].xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            "</Types>",
        "_rels/.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        "xl/workbook.xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="%s" sheetId="1" r:id="rId1"/></sheets></workbook>' % _xl_escape(tab),
        "xl/_rels/workbook.xml.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            "</Relationships>",
        "xl/styles.xml": styles,
        "xl/worksheets/sheet1.xml": "".join(sheet),
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in parts.items():
            archive.writestr(name, text)
    return buffer.getvalue()


@app.route("/export/xlsx", methods=["POST"])
@login_required
def export_xlsx():
    """A grid posted by data-table.js, as a downloadable .xlsx.
    login_required, NOT admin_required: a pure transform that never
    queries, so it only echoes rows the caller already had (faculty tables
    are row-scoped; t/test_export_xlsx.py). Errors are JSON, not
    redirects: fetch() would follow one and save the page as .xlsx."""
    upload = request.files.get("payload")
    if upload is None:
        return jsonify({"error": "No table data was received."}), 400

    try:
        payload = json.loads(upload.read().decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return jsonify({"error": "The table data could not be read."}), 400

    if not isinstance(payload, dict):
        return jsonify({"error": "The table data could not be read."}), 400

    columns = payload.get("columns")
    rows = payload.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list) or not columns:
        return jsonify({"error": "There is nothing to export."}), 400

    columns = [str(value) for value in columns[:XLSX_MAX_COLS]]
    trimmed = []
    for row in rows[:XLSX_MAX_ROWS]:
        if not isinstance(row, list):
            continue
        trimmed.append([str(value) for value in row[:XLSX_MAX_COLS]])

    title = str(payload.get("title") or "Export").strip()[:80]
    # secure_filename so a crafted name cannot steer the Content-Disposition
    # header; it also strips the characters Windows refuses in a filename.
    name = secure_filename(str(payload.get("filename") or "export").strip()) or "export"

    payload = _build_xlsx(title, columns, trimmed)
    return Response(
        payload,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="%s.xlsx"' % name,
            "Content-Length": str(len(payload)),
        },
    )


@app.route("/jobs/<int:job_id>/cancel", methods=["POST"])
@admin_required
def cancel_job(job_id):
    db = get_db()

    job = db.execute(
        "SELECT id, status, batch_id, action, computer_hostname, software_name, software_version "
        "FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if not job:
        return redirect(url_for("dashboard", deploy_error="Job not found."))

    # pending only - see bulk_cancel_jobs() for why in_progress is excluded.
    if job["status"] != "pending":
        return redirect(url_for("dashboard", deploy_error="Only pending jobs can be cancelled."))

    db.execute(
        "UPDATE jobs SET status = 'cancelled' WHERE id = ?",
        (job_id,),
    )
    maybe_notify_batch_complete(db, job["batch_id"])
    db.commit()
    kind = "uninstall" if job["action"] == JOB_ACTION_UNINSTALL else "install"
    log_audit_event(
        actor_admin_id=session.get("admin_id"),
        target_admin_id=None,
        action_type="job_cancelled",
        details=(f"Cancelled the {kind} of "
                 f"{software_label(job['software_name'], job['software_version']) or 'removed software'} "
                 f"on {job['computer_hostname']}"),
        target_type="job",
        target_label=f"#{job_id} ({job['computer_hostname']})",
    )

    return redirect(url_for("dashboard", deploy_success=f"Job #{job_id} has been cancelled."))


@app.route("/jobs/<int:job_id>/retry", methods=["POST"])
@admin_required
def retry_job(job_id):
    db = get_db()

    job = db.execute(RETRY_JOB_SELECT + "WHERE jobs.id = ?", (job_id,)).fetchone()
    if not job:
        return redirect(url_for("dashboard", deploy_error="Job not found."))

    # Cancelled (before pickup) is as retryable as failed; pending is
    # already queued and in_progress already running.
    if job["status"] not in ("failed", "cancelled"):
        return redirect(url_for("dashboard", deploy_error="Only failed or cancelled jobs can be retried."))

    if not job_retryable(job):
        if job["action"] is None:
            return redirect(url_for("dashboard", deploy_error="Software no longer exists, cannot retry."))
        return redirect(url_for(
            "dashboard", deploy_error="It is no longer installed on that computer, so there is nothing to uninstall."
        ))

    # Same online rule as the dashboard, for an uninstall's online check.
    mark_stale_computers_offline(db)
    blocker = retry_blocker(db, job)
    if blocker:
        return redirect(url_for("dashboard", deploy_error=f"Cannot retry: {blocker}"))

    requeue_job(db, job)
    db.commit()
    kind = "uninstall" if job["action"] == JOB_ACTION_UNINSTALL else "install"
    name = software_label(job["software_name"] or job["library_name"],
                          job["software_version"] or job["library_version"]) or "removed software"
    log_audit_event(
        actor_admin_id=session.get("admin_id"),
        target_admin_id=None,
        action_type="job_retried",
        details=f"Retried the {kind} of {name} on {job['computer_hostname']}",
        target_type="job",
        target_label=f"#{job_id} ({job['computer_hostname']})",
    )

    return redirect(url_for("dashboard", deploy_success=f"Retry job created for {job['computer_hostname']}."))


# --- What an agent can do beyond installing --------------------------------
#
# Each poll carries the agent's capabilities, and the computer row keeps the
# latest list. They are per installation method, and an agent lists one only
# once it has the code for it (agent_capabilities() in agent.py). Every
# uninstall check - the picker, creating the job, handing it out - matches
# the capability to the installation's method through can_uninstall(), never
# to "can uninstall" in general. An EXE row also has to pass
# exe_uninstall_problem() first.
#
# The agent API has no login, so the list is checked like any other input:
# known names only, at most MAX_AGENT_CAPABILITIES entries looked at, and
# anything else ignored and logged. A list that is missing - an agent from
# before capabilities - is exactly an empty one.
UNINSTALL_CAPABILITY_BY_METHOD = {"msi": "uninstall:msi", "choco": "uninstall:choco", "exe": "uninstall:exe"}
# What an agent that can also remove per-user EXEs says, beside
# uninstall:exe. An older agent never sends it, so it is never handed one -
# see can_uninstall_row().
EXE_USER_CAPABILITY = "uninstall:exe-user"
KNOWN_AGENT_CAPABILITIES = frozenset(UNINSTALL_CAPABILITY_BY_METHOD.values()) | {EXE_USER_CAPABILITY}
MAX_AGENT_CAPABILITIES = 16


def clean_agent_capabilities(value):
    """(kept, ignored): the known names sent, sorted and deduplicated, and a
    short description of everything else, for the log. Never raises."""
    if value is None:
        return [], []
    if not isinstance(value, list):
        return [], [f"a {type(value).__name__} instead of a list"]
    ignored = []
    if len(value) > MAX_AGENT_CAPABILITIES:
        ignored.append(f"{len(value) - MAX_AGENT_CAPABILITIES} more past the first {MAX_AGENT_CAPABILITIES}")
        value = value[:MAX_AGENT_CAPABILITIES]
    kept = set()
    for item in value:
        if isinstance(item, str) and item in KNOWN_AGENT_CAPABILITIES:
            kept.add(item)
        else:
            # repr, cut short: whatever arrived, the log gets one safe line.
            ignored.append(repr(item)[:60])
    return sorted(kept), ignored


def store_agent_capabilities(db, hostname, value):
    """Keep what this poll said the agent can do, replacing the last list."""
    kept, ignored = clean_agent_capabilities(value)
    if ignored:
        app.logger.warning(f"Capabilities from {hostname!r}: ignored {'; '.join(ignored[:5])}.")
    db.execute("UPDATE computers SET capabilities = ? WHERE hostname = ?", (" ".join(kept), hostname))


def can_uninstall(capabilities, method):
    """Whether a computer with these stored capabilities can remove something
    installed by method (msi, choco or exe). NULL and "": never."""
    wanted = UNINSTALL_CAPABILITY_BY_METHOD.get(method)
    return wanted is not None and wanted in (capabilities or "").split()


# Shown wherever uninstall support is, so a PC whose agent removes only
# machine-wide EXEs reads differently from an updated one.
EXE_MACHINE_ONLY_LABEL = "Installer (EXE, machine-wide only)"


def exe_machine_wide_only(capabilities):
    """Whether this computer's agent removes EXEs, but not per-user ones."""
    said = (capabilities or "").split()
    return UNINSTALL_CAPABILITY_BY_METHOD["exe"] in said and EXE_USER_CAPABILITY not in said


def uninstall_support_label(capabilities):
    """Computer Details' "Uninstall support": the methods this computer's
    agent can remove, "None", or "" (Not recorded) before any agent polled."""
    if capabilities is None:
        return ""
    methods = [EXE_MACHINE_ONLY_LABEL if method == "exe" and exe_machine_wide_only(capabilities)
               else INSTALL_METHOD_LABELS[method]
               for method in UNINSTALL_CAPABILITY_BY_METHOD if can_uninstall(capabilities, method)]
    return ", ".join(methods) or "None"


# --- Uninstalling what AppEase installed -------------------------------------
#
# MSI, Chocolatey, and EXE rows passing exe_uninstall_problem(), on
# computers online now whose agent reported that method - never queued for
# later. uninstall_blocker() alone decides, for create, retry and picker.

SHARED_RUNTIME_REASON = "Shared runtime - other software on this PC may need it."
# Why a row is not uninstalled now. Codes for the JSON, words for people.
UNINSTALL_SKIP_REASONS = {
    "not_found": "No such record.",
    "protected": "Protected - AppEase's own agent runs on it.",
    "not_installed": "It is no longer installed.",
    "shared_runtime": SHARED_RUNTIME_REASON,
    "unavailable": "It can't be uninstalled automatically.",
    "unsafe_location": "Its uninstaller is outside Program Files, so AppEase never runs it.",
    "unsafe_user_location": "Its uninstaller is outside its account's AppData folder, so AppEase never runs it.",
    "retired": "The computer is retired.",
    "offline": "The computer is offline.",
    "no_capability": "This computer's agent can't uninstall this kind of software yet.",
    "uninstall_active": "An uninstall of it is already queued or running.",
    "install_active": "An install of this software is queued or running on this computer.",
}
UNINSTALL_UNDER_WAY = "An uninstall of this software is queued or running on that computer."
MAX_UNINSTALL_SELECTION = 500

# Microsoft runtime families that other software needs without saying so.
# Matched on the library name and on Windows' DisplayName, and read every
# time rather than stored: rows recorded before this rule (a lab PC's
# vc_redist) are covered, and the rule can change with no migration. A name
# match only ever protects a row - it never makes one removable. The agent
# checks again, by Windows' own dependency records first.
SHARED_RUNTIME_NAME = re.compile(
    r"visual\s*c\+\+.*redistributable|vc_?redist"
    r"|\.net\s+(?:core\s+)?runtime|desktop\s+runtime|asp\.net\s+core|\.net\s+framework"
    r"|webview2|directx",
    re.IGNORECASE,
)


def is_shared_runtime(row):
    """Whether an installations row is a SHARED_RUNTIME_NAME family."""
    return any(SHARED_RUNTIME_NAME.search(row[field] or "") for field in ("software_name", "display_name"))


# An EXE row is removable when every uninstall command recorded for it runs
# a program where its kind of entry belongs:
#   - machine-wide (HKLM): inside Program Files. Outside it - ProgramData
#     included, which any user can write to (the 3a capture's icacls) -
#     anyone could have put the program there.
#   - per-user (HKCU): inside that account's own
#     AppData\Local or AppData\Roaming, and the row must name the account.
#     The account can change those files and the agent runs them elevated;
#     that is accepted because every lab account is already an
#     administrator.
# Read from what the agent recorded at install time, and computed on read,
# like shared runtimes. It is a filter for the picker and the drawer, and
# deliberately stricter than the agent: the server cannot resolve the PC's
# links or 8.3 names, or know an account's profile folder, so it refuses any
# path that needs them. The agent checks the live registry key again, and
# that is the guard.
EXE_UNINSTALL_ROOTS = ("C:\\Program Files\\", "C:\\Program Files (x86)\\")
# A per-user uninstaller's place, by shape only: some account's AppData.
# Which account's is the agent's to check.
_USER_APPDATA_PROGRAM = re.compile(r"^[A-Za-z]:\\Users\\[^\\]+\\AppData\\(?:Local|Roaming)\\.+\.exe$",
                                   re.IGNORECASE)
# The agent's own SID shape (agent.py USER_SID); a test keeps the two equal.
USER_SID = re.compile(r"^S-1(?:-\d+)+$")
# Rows with their library entry's current Uninstall switch (library_switch,
# NULL if none). Every exe_uninstall_problem() caller reads through this, so
# all see the same switch.
INSTALLATION_ROW_SELECT = ("SELECT installations.*, software.uninstall_switch AS library_switch "
                           "FROM installations LEFT JOIN software ON software.id = installations.software_id ")
PER_USER_HIVES = ("HKEY_CURRENT_USER", "HKCU")
MACHINE_HIVES = ("HKEY_LOCAL_MACHINE", "HKLM")
_UNQUOTED_PROGRAM = re.compile(r"^(.*?\.exe)(?=\s|$)", re.IGNORECASE)


def uninstall_program_path(command):
    """The program an uninstall command runs, or None. A quoted program is
    taken as written; an unquoted one ends at the first ".exe" followed by a
    space or the end - never at a space, so "C:\\Program Files\\..." without
    quotes cannot be read as C:\\Program.exe."""
    text = (command or "").strip()
    if text.startswith('"'):
        end = text.find('"', 1)
        return text[1:end] if end > 1 else None
    match = _UNQUOTED_PROGRAM.match(text)
    return match.group(1) if match else None


def is_program_files_path(path):
    """Whether path is an .exe inside Program Files or Program Files (x86),
    with ".." taken out first. Never a short 8.3 name, a variable, a network
    or relative path, or one written with forward slashes."""
    if not path or any(c in path for c in "~%/") or not re.match(r"^[A-Za-z]:\\", path):
        return False
    folded = ntpath.normpath(path).casefold()
    return folded.endswith(".exe") and any(folded.startswith(root.casefold()) for root in EXE_UNINSTALL_ROOTS)


def is_user_appdata_path(path):
    """Whether path is an .exe inside some account's AppData\\Local or
    AppData\\Roaming, with ".." taken out first. Never a short 8.3 name, a
    variable, a network or relative path, or one written with forward
    slashes - the same refusals as is_program_files_path()."""
    if not path or any(c in path for c in "~%/") or not re.match(r"^[A-Za-z]:\\", path):
        return False
    return _USER_APPDATA_PROGRAM.match(ntpath.normpath(path)) is not None


def is_per_user_row(row):
    """Whether an installations row is a per-user (HKCU) entry."""
    return (row["reg_hive"] or "").upper() in PER_USER_HIVES


def installation_library_switch(row):
    """The Uninstall switch a row's library entry has now, cleaned again, or
    None - also for a row read without INSTALLATION_ROW_SELECT."""
    try:
        text = row["library_switch"]
    except (IndexError, KeyError):
        return None
    return clean_uninstall_switch(text)[0]


def exe_uninstall_problem(row):
    """Why this EXE row cannot be uninstalled ("unsafe_location",
    "unsafe_user_location", "unavailable"), or None: the one rule for the
    picker, create, retry, the job and the drawer. A no_silent_uninstall
    row re-opens, checked the same way, while its library entry has an
    Uninstall switch."""
    if row["method"] != "exe":
        return None
    if row["state"] != "installed":
        if (row["state"] != "unavailable" or row["state_reason"] != "no_silent_uninstall"
                or installation_library_switch(row) is None):
            return "unavailable"
    hive = (row["reg_hive"] or "").upper()
    per_user = hive in PER_USER_HIVES
    if not per_user and hive not in MACHINE_HIVES:
        return "unavailable"
    if per_user and not USER_SID.match(row["reg_user_sid"] or ""):
        return "unavailable"
    commands = [c for c in (row["quiet_uninstall_string"], row["uninstall_string"]) if c and c.strip()]
    if not commands:
        return "unavailable"
    if not per_user:
        if not all(is_program_files_path(uninstall_program_path(c)) for c in commands):
            return "unsafe_location"
        return None
    if not all(is_user_appdata_path(uninstall_program_path(c)) for c in commands):
        return "unsafe_user_location"
    return None


def can_uninstall_row(capabilities, row):
    """can_uninstall() for this row's method - and, for a per-user EXE, an
    agent that also said EXE_USER_CAPABILITY."""
    if not can_uninstall(capabilities, row["method"]):
        return False
    if row["method"] == "exe" and is_per_user_row(row):
        return EXE_USER_CAPABILITY in (capabilities or "").split()
    return True


def _active_job(db, sql, args):
    return db.execute(
        f"SELECT 1 FROM jobs WHERE status IN ('pending', 'in_progress') AND {sql} LIMIT 1", args
    ).fetchone() is not None


def uninstall_blocker(db, installation, computer):
    """Why this installations row cannot be uninstalled now - a code from
    UNINSTALL_SKIP_REASONS - or None. computer is its computers row.

    The caller runs mark_stale_computers_offline() first, so "online" means
    exactly what it means on the dashboard."""
    if installation is None:
        return "not_found"
    if installation["state_reason"] == "protected":
        return "protected"
    if installation["state"] not in LIVE_INSTALLATION_STATES:
        return "not_installed"
    if is_shared_runtime(installation):
        return "shared_runtime"
    if installation["method"] == "exe":
        problem = exe_uninstall_problem(installation)
        if problem:
            return problem
    elif installation["state"] != "installed":
        return "unavailable"
    if computer is None or computer["asset_status"] == ASSET_STATUS_RETIRED:
        return "retired" if computer is not None else "not_found"
    if computer["status"] != "online":
        return "offline"
    if not can_uninstall_row(computer["capabilities"], installation):
        return "no_capability"
    if _active_job(db, "action = ? AND installation_id = ?", (JOB_ACTION_UNINSTALL, installation["id"])):
        return "uninstall_active"
    if installation["software_id"] is not None and _active_job(
        db, "action IS NULL AND computer_hostname = ? AND software_id = ?",
        (computer["hostname"], installation["software_id"]),
    ):
        return "install_active"
    return None


def install_skip_reason(db, hostname, software_id):
    """Why an install is not queued: "duplicate" (one is already pending) or
    "uninstalling" (an uninstall of that software is pending or running on
    that computer), or None."""
    if _active_job(db, "action IS NULL AND status = 'pending' AND computer_hostname = ? AND software_id = ?",
                   (hostname, software_id)):
        return "duplicate"
    if _active_job(db, "action = ? AND computer_hostname = ? AND software_id = ?",
                   (JOB_ACTION_UNINSTALL, hostname, software_id)):
        return "uninstalling"
    return None


def retry_blocker(db, job):
    """Words for why a job job_retryable() allows cannot be queued again
    right now, or None. An uninstall goes through every check a new one
    does; an install only through the uninstall guard."""
    if job["action"] is None:
        if _active_job(db, "action = ? AND computer_hostname = ? AND software_id = ?",
                       (JOB_ACTION_UNINSTALL, job["computer_hostname"], job["software_id"])):
            return UNINSTALL_UNDER_WAY
        return None
    installation = db.execute(INSTALLATION_ROW_SELECT + "WHERE installations.id = ?",
                              (job["installation_id"],)).fetchone()
    computer = db.execute("SELECT * FROM computers WHERE hostname = ?", (job["computer_hostname"],)).fetchone()
    reason = uninstall_blocker(db, installation, computer)
    return UNINSTALL_SKIP_REASONS[reason] if reason else None


@app.route("/uninstall-batch", methods=["POST"])
@admin_required
def create_uninstall_jobs():
    """Queue uninstalls of installations rows by id; JSON only (fetch(), no
    form fallback). Each row goes through uninstall_blocker(); one batch,
    one audit entry, and the reply gives every skip's reason."""
    installation_ids = []
    for raw in request.form.getlist("installation_ids"):
        raw = raw.strip()
        if raw.isdigit() and int(raw) not in installation_ids:
            installation_ids.append(int(raw))
    if not installation_ids:
        return jsonify({"ok": False, "error": "Please select at least one program to uninstall."}), 400
    if len(installation_ids) > MAX_UNINSTALL_SELECTION:
        return jsonify({"ok": False, "error": f"Select at most {MAX_UNINSTALL_SELECTION} at a time."}), 400

    db = get_db()
    # The dashboard's own first step: a PC that stopped checking in must
    # read offline here too.
    mark_stale_computers_offline(db)
    batch_id = uuid.uuid4().hex
    created, skipped = [], []
    for installation_id in installation_ids:
        installation = db.execute(INSTALLATION_ROW_SELECT + "WHERE installations.id = ?",
                                  (installation_id,)).fetchone()
        computer = None
        entry = {"installation_id": installation_id, "computer": "", "software": ""}
        if installation is not None:
            computer = db.execute("SELECT * FROM computers WHERE id = ?", (installation["computer_id"],)).fetchone()
            name = installation["software_name"] or installation["display_name"] or "Unknown software"
            version = installation["software_version"] or installation["display_version"]
            entry.update(computer=computer["hostname"] if computer else "", software=software_label(name, version))
        reason = uninstall_blocker(db, installation, computer)
        if reason:
            skipped.append(dict(entry, reason=reason, reason_text=UNINSTALL_SKIP_REASONS[reason]))
            continue
        # jobs.software_id is NOT NULL. A row whose library entry was deleted
        # has software_id NULL (both delete routes clear it), so its job keeps
        # NO_LIBRARY_SOFTWARE_ID instead - 0, which no software row can have -
        # and is named by the snapshot written beside it.
        software_id = installation["software_id"]
        if software_id is None:
            software_id = NO_LIBRARY_SOFTWARE_ID
        job_id = db.execute(
            "INSERT INTO jobs (computer_hostname, software_id, status, batch_id, software_name, software_version, "
            "action, installation_id) VALUES (?, ?, 'pending', ?, ?, ?, ?, ?)",
            (computer["hostname"], software_id, batch_id, name, version, JOB_ACTION_UNINSTALL, installation["id"]),
        ).lastrowid
        created.append(dict(entry, job_id=job_id))
    db.commit()

    message = f"Queued {len(created)} uninstall(s)."
    if skipped:
        message += f" Skipped {len(skipped)}."
    if created:
        labels = sorted({item["software"] for item in created})
        label = labels[0] if len(labels) == 1 else f"{len(labels)} software items"
        computers = len({item["computer"] for item in created})
        log_audit_event(
            actor_admin_id=session.get("admin_id"),
            target_admin_id=None,
            action_type="uninstall_created",
            details=f"Queued uninstall of {label} on {computers} computer(s) ({len(created)} job(s) created)",
            target_type="uninstall",
            target_label=label,
        )
    return jsonify({
        "ok": bool(created),
        "batch_id": batch_id if created else None,
        "created": created,
        "skipped": skipped,
        "message": message,
    }), 200


# What the Deploy page's Uninstall mode can offer. The page is only a view:
# every reason here comes from uninstall_blocker() itself, and
# /uninstall-batch checks every row again at submit, so the picker and the
# server cannot disagree.
COMPUTER_UNINSTALL_LABELS = {"offline": "Offline", "no_capability": "Agent needs updating"}
UNINSTALL_METHOD_SHORT = {"msi": "MSI", "choco": "Chocolatey", "exe": "EXE"}


def uninstall_methods_note(capabilities):
    """The picker's note beside a computer whose agent removes only some of
    what AppEase installs: "MSI and Chocolatey only", "MSI, Chocolatey and
    machine-wide EXE only" - or "" for one that removes everything."""
    methods = [m for m in UNINSTALL_CAPABILITY_BY_METHOD if can_uninstall(capabilities, m)]
    machine_only = exe_machine_wide_only(capabilities)
    if not methods or (len(methods) == len(UNINSTALL_CAPABILITY_BY_METHOD) and not machine_only):
        return ""
    words = ["machine-wide EXE" if m == "exe" and machine_only else UNINSTALL_METHOD_SHORT[m] for m in methods]
    joined = words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]
    return joined + " only"


def uninstall_group_key(row):
    """Which rows on different computers are "the same program": the record's
    identity with the computer taken out. An MSI's ProductCode, so 7-Zip
    24.08 and 26.03 are two programs; a Chocolatey package, whatever version
    each PC has; an EXE's key name, without the SID of a per-user entry.
    Never the library entry, which two programs can share and a deleted one
    no longer has, and never a name."""
    key = row["record_key"]
    if key.startswith("exe:HKCU:"):
        parts = key.split(":", 3)
        if len(parts) == 4:
            return "exe:HKCU:" + parts[3]
    return key


def computer_uninstall_reason(computer):
    """Why a computer cannot be picked in Uninstall mode at all, or None:
    offline, or an agent that can uninstall nothing. One that can do some
    methods is pickable; its other rows say why in the programs column."""
    if computer["status"] != "online":
        return "offline"
    if not any(can_uninstall(computer["capabilities"], method) for method in UNINSTALL_CAPABILITY_BY_METHOD):
        return "no_capability"
    return None


@app.route("/api/uninstall-candidates", methods=["GET"])
@admin_required
def uninstall_candidates():
    """Every computer Uninstall mode lists, and every live installations row
    on them, each with uninstall_blocker()'s own reason. Retired computers
    are left out, as the Install picker leaves them out. Read-only: it runs
    the dashboard's offline check first, and writes nothing else."""
    db = get_db()
    mark_stale_computers_offline(db)
    computers = db.execute(
        f"""
        SELECT * FROM computers
        WHERE {NOT_RETIRED_CLAUSE}
        ORDER BY (location IS NULL OR TRIM(location) = '') ASC,
                 LOWER(location) ASC,
                 LOWER(hostname) ASC
        """,
        (ASSET_STATUS_RETIRED,),
    ).fetchall()
    by_id = {}
    listed = []
    for computer in computers:
        by_id[computer["id"]] = computer
        reason = computer_uninstall_reason(computer)
        listed.append({
            "id": computer["id"],
            "hostname": computer["hostname"],
            "location": computer["location"] or "",
            "online": computer["status"] == "online",
            "reason": reason,
            "reason_text": COMPUTER_UNINSTALL_LABELS.get(reason, ""),
            "methods_note": uninstall_methods_note(computer["capabilities"]),
        })
    installations = []
    rows = db.execute(
        INSTALLATION_ROW_SELECT + "WHERE installations.state IN (?, ?) ORDER BY installations.id",
        LIVE_INSTALLATION_STATES
    ).fetchall()
    for row in rows:
        computer = by_id.get(row["computer_id"])
        if computer is None:
            continue
        reason = uninstall_blocker(db, row, computer)
        installations.append({
            "id": row["id"],
            "computer_id": row["computer_id"],
            "group": uninstall_group_key(row),
            "name": row["software_name"] or row["display_name"] or "Unknown software",
            "version": row["software_version"] or row["display_version"] or "",
            "method_label": UNINSTALL_METHOD_SHORT.get(row["method"], row["method"]),
            "reason": reason,
            "reason_text": UNINSTALL_SKIP_REASONS[reason] if reason else "",
        })
    return jsonify({"computers": listed, "installations": installations}), 200


def next_install_job(db, hostname):
    # Installs only. This sends an install command, and the JOIN alone would
    # pass an uninstall whose software is still in the library off to the
    # agent as an install of it.
    return db.execute(
        """
        SELECT jobs.id, jobs.created_at, jobs.software_id, software.name AS software_name,
               software.install_command
        FROM jobs
        JOIN software ON software.id = jobs.software_id
        WHERE jobs.computer_hostname = ? AND jobs.status = 'pending' AND jobs.action IS NULL
        ORDER BY jobs.created_at ASC, jobs.id ASC
        LIMIT 1
        """,
        (hostname,),
    ).fetchone()


def next_uninstall_job(db, hostname):
    # Never joins software: an uninstall needs nothing from the library.
    return db.execute(
        """
        SELECT id, created_at, installation_id, batch_id
        FROM jobs
        WHERE computer_hostname = ? AND status = 'pending' AND action = ?
        ORDER BY created_at ASC, id ASC
        LIMIT 1
        """,
        (hostname, JOB_ACTION_UNINSTALL),
    ).fetchone()


def uninstall_job_payload(db, job, capabilities):
    """What an uninstall job sends the agent, or None after cancelling it.

    Only the job's id, the action, the method and the identity of the thing
    to remove - a Chocolatey package id, an MSI ProductCode, or an EXE's
    registry view and key name (HKLM) or its account's SID and key name
    (HKCU, hive "HKCU"), plus, for an EXE re-opened by its library entry's
    Uninstall switch, that switch - read from the installations
    row. Never a command and never a path: the agent
    re-reads its own records, or the live registry key, for the rest.
    Cancelled, saying why, when it must not go out: the row is no longer one
    it could remove (no_longer_installed) - a switch that cleans to nothing
    included - or the PC's agent did not report this method
    (agent_cannot_uninstall)."""
    row = db.execute(INSTALLATION_ROW_SELECT + "WHERE installations.id = ?", (job["installation_id"],)).fetchone()
    identity = None
    if row is not None and row["method"] in UNINSTALL_CAPABILITY_BY_METHOD:
        if row["method"] == "choco" and row["state"] == "installed":
            identity = {"package_id": row["choco_package_id"]}
        elif row["method"] == "msi" and row["state"] == "installed":
            identity = {"product_code": row["msi_product_code"]}
        elif row["method"] == "exe" and exe_uninstall_problem(row) is None:
            # Creation and retry refuse every other EXE row; this is the
            # last look before the job leaves the server. The switch is
            # cleaned again here, and only a re-opened row carries one.
            if is_per_user_row(row):
                identity = {"hive": "HKCU", "sid": row["reg_user_sid"], "key_name": row["reg_key_name"]}
            elif row["reg_view"] in ("Registry64", "Registry32"):
                identity = {"view": row["reg_view"], "key_name": row["reg_key_name"]}
            if identity is not None and row["state"] != "installed":
                identity["switch"] = installation_library_switch(row)
    if identity is None or not all(identity.values()):
        # Changed since it was queued - an upgrade from another library entry
        # marked it replaced, say. Creation can only check what was true then.
        reason = "no_longer_installed"
    elif not can_uninstall_row(capabilities, row):
        reason = "agent_cannot_uninstall"
    else:
        return {"id": job["id"], "action": JOB_ACTION_UNINSTALL, "method": row["method"], **identity}
    db.execute("UPDATE jobs SET status = 'cancelled', result_reason = ? WHERE id = ?", (reason, job["id"]))
    maybe_notify_batch_complete(db, job["batch_id"])
    return None


def record_uninstall_outcome(db, job, status, sent_reason, kept_reason):
    """A completed uninstall the agent confirmed: its installations row
    becomes removed, saying how. still_listed, removal_unverified, a restart
    still to come and every failure leave the row exactly as it was.

    A live row, installed or unavailable: an EXE recorded with no quiet
    uninstall stays 'unavailable' in the table while its library entry's
    Uninstall switch re-opens it, and must still end removed.

    Only a reason the server KEPT counts, and only when it is the one the
    agent sent: a verdict dropped for breaking its rules must not read as a
    plain removal."""
    sent_reason = sent_reason if isinstance(sent_reason, str) else None
    if status != "completed" or sent_reason != kept_reason or kept_reason not in UNINSTALL_REMOVED_REASONS:
        return
    db.execute(
        "UPDATE installations SET state = 'removed', state_reason = ?, uninstall_job_id = ?, "
        "state_changed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP "
        "WHERE id = ? AND state IN (?, ?)",
        (UNINSTALL_REMOVED_REASONS[kept_reason], job["id"], job["installation_id"], *LIVE_INSTALLATION_STATES),
    )


# A completed uninstall's kept reason -> the removed row's state_reason. No
# reason at all is the ordinary, verified removal.
UNINSTALL_REMOVED_REASONS = {
    None: "removed",
    "removed_restart_needed": "removed_restart_needed",
    "removed_folder_left": "removed_folder_left",
    "already_removed": "already_removed",
}


@app.route("/api/agent/poll", methods=["POST"])
def agent_poll_job():
    payload = request.get_json(silent=True) or {}
    hostname = (payload.get("computer_hostname") or "").strip()
    ip_address = (payload.get("ip_address") or "").strip() or None

    if not hostname:
        return jsonify({"error": "computer_hostname is required"}), 400

    db = get_db()
    hostname = upsert_computer_heartbeat(
        db, hostname, ip_address, clean_agent_id(payload.get("agent_id"))
    )
    store_agent_capabilities(db, hostname, payload.get("capabilities"))
    reap_stale_jobs(db)
    capabilities = db.execute("SELECT capabilities FROM computers WHERE hostname = ?", (hostname,)).fetchone()
    capabilities = capabilities["capabilities"] if capabilities else ""

    # Oldest first, installs and uninstalls in one queue. An uninstall this
    # agent cannot run is cancelled on the way, and the next job considered.
    while True:
        job = next_install_job(db, hostname)
        uninstall = next_uninstall_job(db, hostname)
        if uninstall is None or (job is not None and
                                 (job["created_at"], job["id"]) < (uninstall["created_at"], uninstall["id"])):
            break
        sent = uninstall_job_payload(db, uninstall, capabilities)
        if sent is not None:
            db.execute(
                "UPDATE jobs SET status = 'in_progress', started_at = CURRENT_TIMESTAMP WHERE id = ?",
                (uninstall["id"],),
            )
            db.commit()
            return jsonify({"job": sent})

    if not job:
        db.commit()
        return jsonify({"job": None}), 200

    db.execute(
        "UPDATE jobs SET status = 'in_progress', started_at = CURRENT_TIMESTAMP WHERE id = ?",
        (job["id"],),
    )
    db.commit()

    return jsonify(
        {
            "job": {
                "id": job["id"],
                "software_id": job["software_id"],
                "software_name": job["software_name"],
                "install_command": job["install_command"],
            }
        }
    )


@app.route("/api/agent/heartbeat", methods=["POST"])
def agent_heartbeat():
    payload = request.get_json(silent=True) or {}
    hostname = (payload.get("computer_hostname") or "").strip()
    ip_address = (payload.get("ip_address") or "").strip() or None

    if not hostname:
        return jsonify({"error": "computer_hostname is required"}), 400

    db = get_db()
    upsert_computer_heartbeat(
        db, hostname, ip_address, clean_agent_id(payload.get("agent_id"))
    )
    db.commit()
    return jsonify({"message": "ok"}), 200


# --- Hardware inventory reported by the agent ----------------------------
#
# Length cap on every string field. These arrive from WMI, which is not a
# hostile source, but it is an UNBOUNDED one - a motherboard whose model
# string is 4 KB of vendor marketing would otherwise be stored and then
# rendered into a table cell.
MAX_HARDWARE_FIELD_LENGTH = 80

# WMI placeholders that mean "the factory never filled this in". They are
# not serial numbers, and storing them would put a value in a column whose
# whole purpose is to identify one physical machine - two PCs both reading
# "To Be Filled By O.E.M." is worse than two both reading "Not recorded",
# because the first looks like data.
HARDWARE_PLACEHOLDER_VALUES = {
    "to be filled by o.e.m.",
    "to be filled by oem",
    "system serial number",
    "default string",
    "none",
    "not applicable",
    "not specified",
    "unknown",
    "0",
    "o.e.m.",
    "invalid",
    "system manufacturer",
    "system product name",
    "chassis serial number",
}

# Sanity bounds. A machine reporting 900 TB of RAM has a unit-conversion
# bug somewhere, and storing the number would make the column useless for
# sorting. Out-of-range values are dropped rather than clamped: a wrong
# number that looks plausible is worse than no number.
MIN_RAM_MB = 256
MAX_RAM_MB = 4 * 1024 * 1024
MIN_STORAGE_GB = 1
MAX_STORAGE_GB = 1024 * 1024


def clean_hardware_text(value):
    """Trim, cap, and discard WMI placeholder strings. Returns None if the
    value carries no information."""
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    if text.strip(". ").lower() in HARDWARE_PLACEHOLDER_VALUES:
        return None
    if text.lower() in HARDWARE_PLACEHOLDER_VALUES:
        return None
    return text[:MAX_HARDWARE_FIELD_LENGTH]


def clean_hardware_number(value, minimum, maximum):
    """Coerce to int and range-check. Returns None on anything unusable.

    Accepts a float because PowerShell's ConvertTo-Json will happily emit
    8192.0, and rejects bools because in Python True is an int and would
    otherwise store as 1.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    if number < minimum or number > maximum:
        return None
    return number


def hardware_payload_fields(payload):
    """Pull the seven agent-owned facts out of a JSON body, cleaned.

    Everything absent or unusable comes back as None, and None means "leave
    this column alone" at the write site - an agent that can read the CPU
    but not the serial number must not blank a serial number recorded on a
    previous boot.
    """
    return {
        "manufacturer": clean_hardware_text(payload.get("manufacturer")),
        "model": clean_hardware_text(payload.get("model")),
        "serial_number": clean_hardware_text(payload.get("serial_number")),
        "cpu": clean_hardware_text(payload.get("cpu")),
        "os_name": clean_hardware_text(payload.get("os_name")),
        "ram_mb": clean_hardware_number(payload.get("ram_mb"), MIN_RAM_MB, MAX_RAM_MB),
        "storage_gb": clean_hardware_number(payload.get("storage_gb"), MIN_STORAGE_GB, MAX_STORAGE_GB),
    }


@app.route("/api/agent/inventory", methods=["POST"])
def agent_report_inventory():
    """Hardware facts reported by the machine itself. Named agent_* on
    purpose: require_password_change() exempts agent endpoints by PREFIX,
    or one admin's pending password change would stop every lab PC. Apart
    from the 10 s heartbeat, as these facts change almost never. Writes
    only agent-owned columns, never technician-typed ones."""
    payload = request.get_json(silent=True) or {}
    hostname = (payload.get("computer_hostname") or "").strip()
    if not hostname:
        return jsonify({"error": "computer_hostname is required"}), 400

    fields = hardware_payload_fields(payload)

    # Every field unusable means there is nothing to record. Stamping
    # hardware_reported_at anyway would claim a successful inventory that
    # produced no inventory.
    if not any(value is not None for value in fields.values()):
        return jsonify({"error": "no usable hardware fields"}), 400

    db = get_db()
    # Upsert first so this works whether inventory or the first poll arrives
    # first. Without it, a machine whose very first request was this one
    # would have its facts silently dropped for want of a row.
    hostname = upsert_computer_heartbeat(
        db, hostname, (payload.get("ip_address") or "").strip() or None,
        clean_agent_id(payload.get("agent_id")),
    )

    # COALESCE(?, column): a NULL parameter leaves the stored value alone.
    # A partial reading must never erase a fact an earlier boot recorded.
    db.execute(
        """
        UPDATE computers
        SET manufacturer = COALESCE(?, manufacturer),
            model = COALESCE(?, model),
            serial_number = COALESCE(?, serial_number),
            cpu = COALESCE(?, cpu),
            ram_mb = COALESCE(?, ram_mb),
            storage_gb = COALESCE(?, storage_gb),
            os_name = COALESCE(?, os_name),
            hardware_reported_at = CURRENT_TIMESTAMP
        WHERE hostname = ?
        """,
        (
            fields["manufacturer"], fields["model"], fields["serial_number"],
            fields["cpu"], fields["ram_mb"], fields["storage_gb"],
            fields["os_name"], hostname,
        ),
    )
    db.commit()
    return jsonify({
        "message": "ok",
        "recorded": sorted(k for k, v in fields.items() if v is not None),
    }), 200


@app.route("/api/agent/problem-reports", methods=["POST"])
def agent_submit_problem_report():
    payload = request.get_json(silent=True) or {}
    hostname = (payload.get("computer_hostname") or "").strip()
    ip_address = (payload.get("ip_address") or "").strip() or None
    problem_type = (payload.get("problem_type") or "").strip().lower()
    description = (payload.get("description") or "").strip()

    if not hostname:
        return jsonify({"error": "computer_hostname is required"}), 400
    if problem_type not in ALLOWED_PROBLEM_TYPES:
        return jsonify({"error": f"problem_type must be one of: {', '.join(sorted(ALLOWED_PROBLEM_TYPES))}"}), 400
    if not description:
        return jsonify({"error": "description is required"}), 400
    # The same ceiling the report form has. The agent API takes no
    # credentials, so without one any machine on the LAN could store a
    # description of any size on every page that lists reports.
    if len(description) > DESCRIPTION_MAX_LENGTH:
        return jsonify({"error": f"description must be {DESCRIPTION_MAX_LENGTH} characters or fewer"}), 400

    db = get_db()
    hostname = upsert_computer_heartbeat(
        db, hostname, ip_address, clean_agent_id(payload.get("agent_id"))
    )
    cursor = db.execute(
        """
        INSERT INTO problem_reports (computer_hostname, problem_type, description, status)
        VALUES (?, ?, ?, 'open')
        """,
        (hostname, problem_type, description),
    )
    add_notification(
        db,
        "problem_report",
        f"New {problem_type} report from {hostname}",
        severity="warning",
        link_url=url_for("problem_reports_page", open=cursor.lastrowid),
        audience="all",
    )
    db.commit()

    return jsonify({"message": "problem report submitted", "report_id": cursor.lastrowid}), 201


# --- What an install left on the PC ------------------------------------------
#
# A job report may carry an install_record: what the install added to
# Windows' installed-programs list, so it can be recognised again later. It
# is stored in installations (database.py, one row per thing on a PC), and
# ONLY from inside a report this route has already accepted - an in_progress
# job that belongs to the reporting computer. Nothing else writes it.
#
# Never a reason to refuse the report, the same posture as clean_job_result():
# a record that is missing, malformed or not allowed is ignored and logged,
# and the job's status still saves. Old agents send none, and nothing about
# their reports changes.
#
#   - Completed jobs only. A failed or timed-out install may have installed
#     part, all or none of the software, and nothing it reports can say
#     which. It neither creates nor changes a row; an existing one stays
#     exactly as it was.
#   - Chocolatey's "already installed", in either form, creates nothing:
#     AppEase did not install it. Checked against the reason the agent SENT
#     as well as the one kept, so a reason the server dropped (say, with the
#     wrong exit code) cannot let a record through.
#   - The record's method must match the job's own command: choco, msiexec,
#     or an installer (exe).
#   - Every field is checked for type, length and control characters, and
#     the key is built by installation_record_key(). Anything that does not
#     parse is ignored as a WHOLE - never truncated, because a truncated
#     uninstall command is a different command.
#   - Why a row is unavailable is a CODE from INSTALL_RECORD_REASONS, worded
#     here, like JOB_RESULT_REASONS. Registry text (DisplayName and so on) is
#     stored as Windows reported it, and is data, shown escaped.
#
# Real entries against "none:" rows (an install that left nothing usable):
#   - a real record always wins. Storing one deletes that software's none:
#     row on that computer.
#   - a none: result where a LIVE real row (installed or unavailable) exists
#     for that software on that computer adds nothing - typically a redeploy
#     of something already there, whose install added no new entry because
#     the entry already existed.
#
# "replaced" is OBSERVED, never inferred. The agent lists the entries that
# disappeared while this install ran (replaced_entries), and any live row on
# THIS computer with one of those keys becomes replaced. That is how an MSI
# major upgrade - old ProductCode removed, new one added - retires the old
# row, even when a different library entry installed it. Names, versions
# and software ids are never used to guess it.
#
# The same key reported again by a later install updates that one row: it
# now belongs to the latest install, its library entry and its job, and is
# live again (installed, or unavailable with its new reason), whatever state
# it was in before.
INSTALL_RECORD_REASONS = {
    "no_new_entry": {"label": "Windows listed nothing new after the install."},
    "several_new_entries": {"label": "The install added more than one program, so AppEase can't tell which to remove."},
    "name_mismatch": {"label": "What Windows listed doesn't match this software's name."},
    "no_silent_uninstall": {"label": "This program has no quiet uninstall option."},
    "user_hive_unreadable": {"label": "It was installed for a user account AppEase couldn't check."},
    "product_code_unreadable": {"label": "AppEase couldn't read the installer's product ID."},
    "no_registry_entry": {"label": "Chocolatey installed it without adding it to Windows' program list."},
    "protected": {"label": "AppEase's own agent runs on this, so it is never removed."},
    "choco_shared_dependency": {"label": "Another Chocolatey package also needs it."},
    "choco_records_unreadable": {"label": "Chocolatey's records for it couldn't be read."},
    # Set here, from replaced_entries. An agent sending it is ignored.
    "replaced": {"label": "Replaced by a later install.", "source": "server"},
    # Set by the server from an uninstall job's verdict, on a removed row.
    # An agent sending any of them as a record's reason is ignored.
    "removed": {"label": "Removed by AppEase.", "source": "server"},
    "removed_restart_needed": {"label": "Removed; restart needed to finish.", "source": "server"},
    "removed_folder_left": {"label": "Removed; its folder was left behind.", "source": "server"},
    "already_removed": {"label": "It was already gone.", "source": "server"},
}
ALREADY_INSTALLED_REASONS = frozenset({"choco_already_installed", "choco_confirmed_installed"})
LIVE_INSTALLATION_STATES = ("installed", "unavailable")
# Characters, per field. Registry key names are capped at 255 by
# installation_record_key() itself; this cap only has to let them reach it.
INSTALL_RECORD_TEXT_LIMITS = {
    "package_id": 100,
    "product_code": 64,
    "hive": 32,
    "sid": 200,
    "view": 16,
    "key_name": 300,
    "display_name": 256,
    "display_version": 64,
    "install_location": 1024,
    "uninstall_string": 2048,
    "quiet_uninstall_string": 2048,
    "unavailable_reason": 64,
}
MAX_REPLACED_ENTRIES = 20
# Never offered for removal, whatever the agent says: the agent needs both.
#
# Every python* package, not only the agent's own version - unlike an .exe
# or .msi entry, whose DisplayName says "Python 3.14.2" and lets the agent
# protect just its own major.minor. A Chocolatey id cannot say it reliably:
# "python" and "python3" are meta-packages that follow the newest release
# (3.14 today, through a dependency on python314), so the same id installs a
# different Python next year. And this server does not know which Python
# each agent runs on. So for Chocolatey the whole family stays protected.
PROTECTED_CHOCO_PACKAGE = re.compile(r"^(?:chocolatey|python.*)$", re.IGNORECASE)


def install_method_of(command):
    """choco, msi or exe - which kind of install a job's command performs."""
    text = (command or "").strip()
    if text.startswith("choco "):
        return "choco"
    if re.match(r"msiexec(?:\.exe)?\s", text, re.IGNORECASE):
        return "msi"
    return "exe"


def _record_text(record, field):
    value = record.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} is not text")
    if not value.strip():
        return None
    if len(value) > INSTALL_RECORD_TEXT_LIMITS[field]:
        raise ValueError(f"{field} is longer than {INSTALL_RECORD_TEXT_LIMITS[field]} characters")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"{field} holds a control character")
    return value


def _entry_key(method, fields, software_id=None):
    return installation_record_key(
        method,
        package_id=fields.get("package_id"),
        product_code=fields.get("product_code"),
        hive=fields.get("hive"),
        sid=fields.get("sid"),
        view=fields.get("view"),
        key_name=fields.get("key_name"),
        software_id=software_id,
    )


def clean_install_record(raw, software_id):
    """A record fit to store, or ValueError saying why it is not.

    Returns {"method", "key", "fields", "state", "reason", "replaced"}, with
    "replaced" the keys of entries the install removed, each checked on its
    own: one that does not parse is dropped (and logged by the caller)
    without costing the record itself.
    """
    if not isinstance(raw, dict):
        raise ValueError("not an object")
    method = raw.get("method")
    if method not in INSTALLATION_METHODS:
        raise ValueError(f"unknown method {str(method)[:20]!r}")
    fields = {name: _record_text(raw, name) for name in INSTALL_RECORD_TEXT_LIMITS}

    reason = fields.pop("unavailable_reason")
    if reason is not None:
        entry = INSTALL_RECORD_REASONS.get(reason)
        if entry is None or entry.get("source", "agent") != "agent":
            raise ValueError(f"unknown reason {reason[:40]!r}")

    key = _entry_key(method, fields, software_id)
    if key.startswith("none:") and reason is None:
        raise ValueError("a record with no entry must say why")
    if method == "choco" and fields["package_id"] and PROTECTED_CHOCO_PACKAGE.match(fields["package_id"]):
        reason = "protected"

    replaced, dropped = [], []
    entries = raw.get("replaced_entries") or []
    if not isinstance(entries, list) or len(entries) > MAX_REPLACED_ENTRIES:
        raise ValueError("replaced_entries is not a short list")
    for entry in entries:
        try:
            if not isinstance(entry, dict):
                raise ValueError("not an object")
            entry_fields = {name: _record_text(entry, name)
                            for name in ("product_code", "hive", "sid", "view", "key_name")}
            entry_method = "msi" if entry_fields["product_code"] else "exe"
            # No software_id: an entry with nothing to identify it raises.
            entry_key = _entry_key(entry_method, entry_fields)
        except ValueError as exc:
            dropped.append(str(exc))
            continue
        if entry_key != key:
            replaced.append(entry_key)

    return {
        "method": method,
        "key": key,
        "fields": fields,
        "state": "unavailable" if reason else "installed",
        "reason": reason,
        "replaced": replaced,
        "dropped": dropped,
    }


def _write_install_record(db, job, computer_id, record):
    """Store one cleaned record: "stored", "updated" or "kept_existing"."""
    key = record["key"]
    software_id = job["software_id"]

    if key.startswith("none:"):
        real = db.execute(
            "SELECT id FROM installations WHERE computer_id = ? AND software_id = ? "
            "AND record_key NOT LIKE 'none:%' AND state IN (?, ?) LIMIT 1",
            (computer_id, software_id) + LIVE_INSTALLATION_STATES,
        ).fetchone()
        if real:
            return "kept_existing"

    fields = record["fields"]
    values = {
        "software_id": software_id,
        "software_name": job["software_name"],
        "software_version": job["software_version"],
        "method": record["method"],
        "record_key": key,
        "choco_package_id": fields["package_id"],
        "msi_product_code": fields["product_code"],
        "reg_hive": fields["hive"],
        "reg_user_sid": fields["sid"],
        "reg_view": fields["view"],
        "reg_key_name": fields["key_name"],
        "display_name": fields["display_name"],
        "display_version": fields["display_version"],
        "install_location": fields["install_location"],
        "uninstall_string": fields["uninstall_string"],
        "quiet_uninstall_string": fields["quiet_uninstall_string"],
        "install_job_id": job["id"],
        "uninstall_job_id": None,
        "state": record["state"],
        "state_reason": record["reason"],
    }
    existing = db.execute(
        "SELECT id FROM installations WHERE computer_id = ? AND record_key = ?",
        (computer_id, key),
    ).fetchone()
    if existing:
        # Every right-hand side reads the OLD row, so state_changed_at moves
        # only when the state really changes.
        assignments = ", ".join(f"{name} = ?" for name in values)
        db.execute(
            f"UPDATE installations SET "
            f"state_changed_at = CASE WHEN state = ? THEN state_changed_at ELSE CURRENT_TIMESTAMP END, "
            f"{assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            [record["state"]] + list(values.values()) + [existing["id"]],
        )
        outcome = "updated"
    else:
        names = ", ".join(["computer_id"] + list(values))
        marks = ", ".join("?" * (len(values) + 1))
        db.execute(
            f"INSERT INTO installations ({names}) VALUES ({marks})",
            [computer_id] + list(values.values()),
        )
        outcome = "stored"

    if not key.startswith("none:"):
        db.execute(
            "DELETE FROM installations WHERE computer_id = ? AND record_key = ?",
            (computer_id, installation_record_key("exe", software_id=software_id)),
        )
        for replaced_key in record["replaced"]:
            db.execute(
                "UPDATE installations SET state = 'replaced', state_reason = 'replaced', "
                "state_changed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP "
                "WHERE computer_id = ? AND record_key = ? AND state IN (?, ?)",
                (computer_id, replaced_key) + LIVE_INSTALLATION_STATES,
            )
    return outcome


def store_install_record(db, job, raw_record, status, sent_reason, kept_reason):
    """Handle a report's install_record. Returns what happened, for the reply:
    none, stored, updated, kept_existing or ignored. Never raises."""
    if raw_record is None:
        return "none"

    def ignore(why):
        app.logger.warning(f"Install record for job {job['id']} ignored: {why[:300]}.")
        return "ignored"

    if job["action"] is not None:
        return ignore("it came with an uninstall job, which installs nothing")
    if status != "completed":
        return ignore(f"the job {status}, so what the install left is unknown")
    # sent_reason is whatever JSON the agent sent; only text can name a reason.
    sent_reason = sent_reason if isinstance(sent_reason, str) else None
    if {sent_reason, kept_reason} & ALREADY_INSTALLED_REASONS:
        return ignore("Chocolatey reported it already installed, so AppEase did not install it")

    software = db.execute(
        "SELECT install_command FROM software WHERE id = ?", (job["software_id"],)
    ).fetchone()
    computer = db.execute(
        "SELECT id FROM computers WHERE hostname = ?", (job["computer_hostname"],)
    ).fetchone()
    if software is None or computer is None:
        return ignore("the job's software or computer is no longer recorded")

    try:
        record = clean_install_record(raw_record, job["software_id"])
    except ValueError as exc:
        return ignore(str(exc))
    except Exception as exc:  # noqa: BLE001 - a bug here must not cost the report either
        return ignore(f"it could not be read ({type(exc).__name__})")
    for why in record["dropped"]:
        app.logger.warning(f"Install record for job {job['id']}: a replaced entry was ignored: {why[:200]}.")
    expected = install_method_of(software["install_command"])
    if record["method"] != expected:
        return ignore(f"the record says {record['method']}, the job's command is {expected}")

    # A savepoint, so a record that fails half-way leaves no half-written
    # rows, and the job's own status - already updated above - still saves.
    db.execute("SAVEPOINT install_record")
    try:
        outcome = _write_install_record(db, job, computer["id"], record)
    except Exception as exc:  # noqa: BLE001 - nothing here may cost the report itself
        db.execute("ROLLBACK TO install_record")
        db.execute("RELEASE install_record")
        return ignore(f"it could not be written ({exc})")
    db.execute("RELEASE install_record")
    return outcome


@app.route("/api/agent/jobs/<int:job_id>/report", methods=["POST"])
def agent_report_job_result(job_id):
    payload = request.get_json(silent=True) or {}
    hostname = (payload.get("computer_hostname") or "").strip()
    job_status = (payload.get("status") or "").strip().lower()

    if not hostname:
        return jsonify({"error": "computer_hostname is required"}), 400
    if job_status not in {"completed", "failed"}:
        return jsonify({"error": "status must be completed or failed"}), 400

    db = get_db()
    hostname = agent_computer_name(db, hostname, clean_agent_id(payload.get("agent_id")))
    job = db.execute(
        "SELECT id, computer_hostname, status, batch_id, software_id, software_name, software_version, action, "
        "installation_id FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    # A record riding on a report that is refused goes nowhere. The refusal
    # itself is unchanged; this only says, in the log, what was dropped.
    has_record = payload.get("install_record") is not None
    if not job:
        if has_record:
            app.logger.warning(f"Install record for job {job_id} ignored: no such job.")
        return jsonify({"error": "job not found"}), 404

    if job["computer_hostname"] != hostname:
        if has_record:
            app.logger.warning(f"Install record for job {job_id} ignored: the job belongs to another computer.")
        return jsonify({"error": "job does not belong to this computer"}), 403

    if job["status"] != "in_progress":
        if has_record:
            app.logger.warning(f"Install record for job {job_id} ignored: the job is not in progress.")
        return jsonify({"error": "job is not in progress"}), 409

    # Optional, never a reason to refuse the report (see clean_job_result()).
    exit_code, result_reason = clean_job_result(
        job_status, payload.get("exit_code"), payload.get("reason"), job["action"]
    )

    if job_status == "failed":
        job_details = db.execute(
            """
            SELECT COALESCE(jobs.software_name, software.name, 'Removed software') AS software_name
            FROM jobs
            LEFT JOIN software ON software.id = jobs.software_id
            WHERE jobs.id = ?
            """,
            (job_id,),
        ).fetchone()

    db.execute(
        """
        UPDATE jobs
        SET status = ?, completed_at = CURRENT_TIMESTAMP,
            exit_code = ?, result_reason = ?
        WHERE id = ?
        """,
        (job_status, exit_code, result_reason, job_id),
    )
    db.execute(
        """
        UPDATE computers
        SET last_seen = CURRENT_TIMESTAMP,
            status = 'online'
        WHERE hostname = ?
        """,
        (hostname,),
    )

    install_record = store_install_record(
        db, job, payload.get("install_record"), job_status, payload.get("reason"), result_reason
    )
    if job["action"] == JOB_ACTION_UNINSTALL:
        record_uninstall_outcome(db, job, job_status, payload.get("reason"), result_reason)

    if job_status == "failed" and job_details:
        verb = "Uninstall" if job["action"] == JOB_ACTION_UNINSTALL else "Install"
        message = f"{verb} failed: {job_details['software_name']} on {hostname}"
        if result_reason:
            message += f" — {job_reason_entry(result_reason, job['action'])['label']}"
        add_notification(
            db,
            "job_failed",
            message,
            severity="error",
            link_url=url_for("dashboard"),
            audience="admin",
        )

    maybe_notify_batch_complete(db, job["batch_id"])

    db.commit()

    return jsonify({
        "message": "job updated",
        "job_id": job_id,
        "status": job_status,
        # Echoed so the agent can log what was actually kept.
        "reason": result_reason,
        "exit_code": exit_code,
        # What became of the install record - a word, never anything stored.
        "install_record": install_record,
    }), 200


@app.route("/api/notifications", methods=["GET"])
@login_required
def get_notifications():
    db = get_db()
    mark_stale_computers_offline(db)
    role = session.get("admin_role")
    admin_id = session.get("admin_id")

    severity_filter = request.args.get("severity")
    if severity_filter not in ALLOWED_NOTIFICATION_SEVERITIES:
        severity_filter = None

    admin_row = db.execute(
        "SELECT last_seen_notification_id FROM admins WHERE id = ?",
        (admin_id,),
    ).fetchone()
    last_seen_id = admin_row["last_seen_notification_id"] if admin_row and admin_row["last_seen_notification_id"] is not None else 0

    if role == "admin":
        if severity_filter is not None:
            visible_rows = db.execute(
                """
                SELECT id, event_type, message, severity, link_url, created_at
                FROM notifications
                WHERE severity = ?
                ORDER BY id DESC
                LIMIT 20
                """,
                (severity_filter,),
            ).fetchall()
        else:
            visible_rows = db.execute(
                """
                SELECT id, event_type, message, severity, link_url, created_at
                FROM notifications
                ORDER BY id DESC
                LIMIT 20
                """
            ).fetchall()
        unread_count_row = db.execute(
            "SELECT COUNT(*) AS total FROM notifications WHERE id > ?",
            (last_seen_id,),
        ).fetchone()
    else:
        if severity_filter is not None:
            visible_rows = db.execute(
                """
                SELECT id, event_type, message, severity, link_url, created_at
                FROM notifications
                WHERE audience = 'all' AND severity = ?
                ORDER BY id DESC
                LIMIT 20
                """,
                (severity_filter,),
            ).fetchall()
        else:
            visible_rows = db.execute(
                """
                SELECT id, event_type, message, severity, link_url, created_at
                FROM notifications
                WHERE audience = 'all'
                ORDER BY id DESC
                LIMIT 20
                """
            ).fetchall()
        unread_count_row = db.execute(
            "SELECT COUNT(*) AS total FROM notifications WHERE audience = 'all' AND id > ?",
            (last_seen_id,),
        ).fetchone()

    unread_count = unread_count_row["total"] if unread_count_row else 0

    items = [
        {
            "id": row["id"],
            "event_type": row["event_type"],
            "message": row["message"],
            "severity": row["severity"],
            "link_url": row["link_url"],
            "created_at_display": manila_time(row["created_at"]),
        }
        for row in visible_rows
    ]

    # Agent counts ride on this poll: notifications.js already runs every 8s
    # and this route just swept stale computers, so a second timer would
    # double requests for nothing. Null for faculty, who have no pill.
    agents = None
    if session.get("admin_role") == "admin":
        agents = computer_counts(db)

    return jsonify({"unread_count": unread_count, "severity": severity_filter, "items": items, "last_seen_id": last_seen_id, "agents": agents})


@app.route("/api/notifications/mark-read", methods=["POST"])
@login_required
def mark_notifications_read():
    db = get_db()
    admin_id = session.get("admin_id")

    max_id_row = db.execute("SELECT MAX(id) AS max_id FROM notifications").fetchone()
    max_id = max_id_row["max_id"] if max_id_row and max_id_row["max_id"] is not None else 0

    db.execute(
        "UPDATE admins SET last_seen_notification_id = ? WHERE id = ?",
        (max_id, admin_id),
    )
    db.commit()

    return jsonify({"ok": True, "unread_count": 0})


@app.route("/notifications", methods=["GET"])
@login_required
def notifications_page():
    """Full notification history (the bell shows only the newest 20).
    Read-only, with no stale-computer sweep. The role branch MUST mirror
    /api/notifications, or non-admins see admin-only rows."""
    db = get_db()
    role = session.get("admin_role")
    admin_id = session.get("admin_id")

    severity_filter = (request.args.get("severity") or "").strip()
    event_filter = (request.args.get("event") or "").strip()

    # Faculty only ever see audience='all' rows, so the option lists must be
    # built from THEIR visible set - otherwise a faculty user is offered a
    # filter that can only ever return nothing. This clause is reused for the
    # options and for the listing below so the two can never disagree.
    role_sql = "" if role == "admin" else "audience = 'all'"
    role_params = []

    # Options from the UNFILTERED (role-scoped) table, as in the Audit Trail.
    options_where = ("WHERE " + role_sql) if role_sql else ""
    severity_options = [
        row["severity"]
        for row in db.execute(
            f"""
            SELECT DISTINCT severity FROM notifications
            {options_where}
            ORDER BY CASE severity
                         WHEN 'error' THEN 0
                         WHEN 'warning' THEN 1
                         WHEN 'info' THEN 2
                         ELSE 3
                     END ASC
            """,
            role_params,
        ).fetchall()
    ]
    event_options = [
        row["event_type"]
        for row in db.execute(
            f"""
            SELECT DISTINCT event_type FROM notifications
            {options_where}
            ORDER BY event_type ASC
            """,
            role_params,
        ).fetchall()
    ]

    # Anything not in the offered options is discarded rather than queried, so
    # a hand-edited query string cannot reach past the dropdowns.
    if severity_filter not in severity_options:
        severity_filter = ""
    if event_filter not in event_options:
        event_filter = ""

    # Display only: this never advances last_seen_notification_id.
    admin_row = db.execute(
        "SELECT last_seen_notification_id FROM admins WHERE id = ?",
        (admin_id,),
    ).fetchone()
    last_seen_id = admin_row["last_seen_notification_id"] if admin_row and admin_row["last_seen_notification_id"] is not None else 0

    where_clauses = []
    params = []
    if role_sql:
        where_clauses.append(role_sql)
        params.extend(role_params)
    if severity_filter:
        where_clauses.append("severity = ?")
        params.append(severity_filter)
    if event_filter:
        where_clauses.append("event_type = ?")
        params.append(event_filter)
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    # No LIMIT: NOTIFICATION_RETENTION already bounds the table at 500 rows.
    notifications = db.execute(
        f"""
        SELECT id, event_type, message, severity, link_url, created_at
        FROM notifications
        {where_sql}
        ORDER BY id DESC
        """,
        params,
    ).fetchall()

    return render_template(
        "notifications.html",
        notifications=notifications,
        severity_options=severity_options,
        event_options=event_options,
        severity_filter=severity_filter,
        event_filter=event_filter,
        last_seen_id=last_seen_id,
    )


SILENTINSTALLHQ_BASE_URL = "https://app.silentinstallhq.com"
SILENTINSTALLHQ_SIZE_LIMIT = 50 * 1024 * 1024  # the API's sync limit
SILENTINSTALLHQ_POLL_INTERVAL = 10
SILENTINSTALLHQ_MAX_POLL_ATTEMPTS = 60  # x 10s: at least a 10-minute cap
SILENTINSTALLHQ_POLL_TIMEOUT = 60
SILENTINSTALLHQ_SYNC_MIN_CONFIDENCE = 30.0  # 0-100; below it, tagged low


def _strip_filename_prefix(silent):
    """A SilentInstallHQ command line reduced to its switches ("setup.exe
    /S" -> "/S", "MsiExec.exe /i x.msi /qn" -> "/qn"): the command builder
    re-adds the executable and "/i <uploads path>" itself."""
    silent = (silent or "").strip()
    if not silent:
        return ""

    tokens = silent.split()

    if tokens and tokens[0].lower().endswith((".exe", ".msi")):
        tokens = tokens[1:]

    # A verb (/i, /x, /f...) followed by a *.msi token goes too: the app
    # supplies its own "/i <uploads path>".
    if len(tokens) >= 2:
        verb = tokens[0].lower()
        is_msi_verb = verb.startswith("/i") or verb.startswith("/x") \
            or verb.startswith("/f") or verb.startswith("/a") or verb.startswith("/j")
        if is_msi_verb and tokens[1].lower().endswith(".msi"):
            tokens = tokens[2:]

    return " ".join(tokens)


def identify_installer(file_path):
    """Silent Install HQ's detection (silent_install, uninstall_command), or
    None. Sync /identify up to the API's 50MB limit, else upload and poll."""
    if not SILENTINSTALLHQ_API_KEY:
        return None
    try:
        size = os.path.getsize(file_path)
    except OSError as e:
        app.logger.warning(f"[identify_installer] could not stat file: {e}")
        return None

    app.logger.info(f"[SIHQ] file={os.path.basename(file_path)} size={size} bytes -> "
                     f"{'async' if size > SILENTINSTALLHQ_SIZE_LIMIT else 'sync'} path")  # DEBUG-REMOVE

    if size <= SILENTINSTALLHQ_SIZE_LIMIT:
        return _identify_sync(file_path)
    return _identify_async(file_path)


def _identify_sync(file_path):
    """The synchronous /identify flow, for files up to 50MB."""
    try:
        with open(file_path, "rb") as f:
            response = http_requests.post(
                f"{SILENTINSTALLHQ_BASE_URL}/identify",
                headers={"X-API-Key": SILENTINSTALLHQ_API_KEY},
                files={"file": f},
                timeout=90,
            )
        if response.status_code == 200:
            data = response.json()
            app.logger.info(f"[SIHQ sync] raw response: {json.dumps(data, indent=2)}")  # DEBUG-REMOVE
            # snake_case fields; confidence is 0-100.
            app.logger.info(f"[SIHQ sync] confidence={data.get('confidence')} "
                             f"silent_install={data.get('silent_install')!r}")  # DEBUG-REMOVE
            confidence = data.get("confidence")
            confidence = confidence if confidence is not None else 0.0
            silent = _strip_filename_prefix(data.get("silent_install", ""))
            product_name = data.get("product_name") or None
            product_version = data.get("product_version") or None
            if not silent:
                return {
                    "silent_install": None,
                    "uninstall_command": data.get("silent_uninstall", "") or "",
                    "confidence_tier": None,
                    "product_name": product_name,
                    "product_version": product_version,
                }
            if confidence < SILENTINSTALLHQ_SYNC_MIN_CONFIDENCE:
                app.logger.info(f"[SIHQ sync] low confidence: {confidence} < {SILENTINSTALLHQ_SYNC_MIN_CONFIDENCE}, keeping flag, tagged low")  # DEBUG-REMOVE
                confidence_tier = "low"
            else:
                confidence_tier = "high"
            return {
                "silent_install": silent,
                "uninstall_command": data.get("silent_uninstall", "") or "",
                "confidence_tier": confidence_tier,
                "product_name": product_name,
                "product_version": product_version,
            }
    except Exception as e:
        app.logger.warning(f"[identify_installer] sync API error: {e}")
    return None


def _identify_async(file_path):
    """The upload-and-poll flow, for files over the 50MB sync limit."""
    try:
        filename = os.path.basename(file_path)

        upload_url_response = http_requests.post(
            f"{SILENTINSTALLHQ_BASE_URL}/identify/upload-url",
            headers={"X-API-Key": SILENTINSTALLHQ_API_KEY},
            json={"filename": filename},
            timeout=90,
        )
        app.logger.info(f"[SIHQ async] upload-url status={upload_url_response.status_code}")  # DEBUG-REMOVE
        if upload_url_response.status_code != 200:
            return None
        upload_data = upload_url_response.json()
        upload_url = upload_data["upload_url"]
        job_id = upload_data["job_id"]

        # No X-API-Key on the pre-signed storage URL.
        with open(file_path, "rb") as f:
            put_response = http_requests.put(
                upload_url,
                headers={"Content-Type": "application/octet-stream"},
                data=f,
                timeout=600,
            )
        app.logger.info(f"[SIHQ async] PUT status={put_response.status_code}")  # DEBUG-REMOVE
        if put_response.status_code not in (200, 201, 204):
            return None

        submit_response = http_requests.post(
            f"{SILENTINSTALLHQ_BASE_URL}/identify/submit",
            headers={"X-API-Key": SILENTINSTALLHQ_API_KEY},
            json={"job_id": job_id},
            timeout=90,
        )
        app.logger.info(f"[SIHQ async] submit status={submit_response.status_code}")  # DEBUG-REMOVE
        if submit_response.status_code != 202:
            return None

        for attempt in range(1, SILENTINSTALLHQ_MAX_POLL_ATTEMPTS + 1):
            time.sleep(SILENTINSTALLHQ_POLL_INTERVAL)
            try:
                poll_response = http_requests.get(
                    f"{SILENTINSTALLHQ_BASE_URL}/jobs/{job_id}",
                    headers={"X-API-Key": SILENTINSTALLHQ_API_KEY},
                    timeout=SILENTINSTALLHQ_POLL_TIMEOUT,
                )
            except http_requests.exceptions.RequestException as e:
                app.logger.warning(f"[SIHQ async] poll attempt {attempt} failed, retrying: {e}")
                continue  # transient; retry until the cap

            if poll_response.status_code != 200:
                continue  # transient; retry until the cap

            poll_data = poll_response.json()
            status = poll_data.get("status")
            app.logger.info(f"[SIHQ async] poll status={status}")  # DEBUG-REMOVE

            if status == "complete":
                app.logger.info(f"[SIHQ async] raw poll_data: {json.dumps(poll_data, indent=2)}")  # DEBUG-REMOVE
                profile = poll_data.get("profile") or {}
                # Same snake_case fields and 0-100 scale as the sync endpoint.
                app.logger.info(f"[SIHQ async] confidence={profile.get('confidence')} "
                                 f"silent_install={profile.get('silent_install')!r}")  # DEBUG-REMOVE
                confidence = profile.get("confidence")
                confidence = confidence if confidence is not None else 0.0
                silent = _strip_filename_prefix(profile.get("silent_install", ""))
                product_name = profile.get("product_name") or None
                product_version = profile.get("product_version") or None
                if not silent:
                    return {
                        "silent_install": None,
                        "uninstall_command": profile.get("silent_uninstall", "") or "",
                        "confidence_tier": None,
                        "product_name": product_name,
                        "product_version": product_version,
                    }
                if confidence < SILENTINSTALLHQ_SYNC_MIN_CONFIDENCE:
                    app.logger.info(f"[SIHQ async] low confidence: {confidence} < {SILENTINSTALLHQ_SYNC_MIN_CONFIDENCE}, keeping flag, tagged low")  # DEBUG-REMOVE
                    confidence_tier = "low"
                else:
                    confidence_tier = "high"
                return {
                    "silent_install": silent,
                    "uninstall_command": profile.get("silent_uninstall", "") or "",
                    "confidence_tier": confidence_tier,
                    "product_name": product_name,
                    "product_version": product_version,
                }
            if status == "failed":
                return None

        app.logger.warning(f"[identify_installer] async poll timed out for job {job_id}")
    except Exception as e:
        app.logger.warning(f"[identify_installer] async API error: {e}")
    return None


@app.route("/software/detect", methods=["POST"])
@admin_required
def software_detect():
    installer_file = request.files.get("installer_file")
    if not installer_file or not installer_file.filename:
        return jsonify({"error": "No installer file provided."}), 400

    original_name = secure_filename(installer_file.filename)
    _, extension = os.path.splitext(original_name)
    extension = extension.lower()
    if extension not in ALLOWED_OFFLINE_EXTENSIONS:
        return jsonify({"error": "Only .exe and .msi files are allowed for offline software."}), 400

    generated_name = f"{uuid.uuid4().hex}{extension}"
    saved_path = os.path.join(STAGING_FOLDER, generated_name)
    installer_file.save(saved_path)
    installer_relative_path = f"staging/{generated_name}"

    return jsonify({"job_id": start_detection_job(saved_path, installer_relative_path)}), 202


def start_detection_job(saved_path, installer_relative_path):
    """Start a background detection; returns its job id. Shared by upload
    and retry so both report alike. A failed CALL is "error" (worth a
    retry); an answer with no flag is "done" (a retry would not change it).
    installer_path rides on errors so a retry reuses the staged copy."""
    job_id = uuid.uuid4().hex
    DETECT_JOBS[job_id] = {"status": "pending", "result": None}

    def _run_detection():
        with app.app_context():
            try:
                detected = identify_installer(saved_path)
                if detected is None:
                    DETECT_JOBS[job_id] = {"status": "error", "result": {
                        "installer_path": installer_relative_path,
                    }}
                    return
                suggested = detected.get("uninstall_command") or ""
                DETECT_JOBS[job_id] = {"status": "done", "result": {
                    "name": detected.get("product_name"),
                    "version": detected.get("product_version"),
                    "silent_install": detected.get("silent_install"),
                    "confidence_tier": detected.get("confidence_tier"),
                    "installer_path": installer_relative_path,
                    # Only the allowed switches in Silent Install HQ's
                    # silent_uninstall. uninstall_suggested says it offered
                    # something, so the form can say none of it was allowed.
                    "uninstall_switch": clean_uninstall_switch(suggested)[0],
                    "uninstall_suggested": bool(suggested.strip()),
                }}
            except Exception as e:
                app.logger.warning(f"[detect] job {job_id} failed: {e}")
                DETECT_JOBS[job_id] = {"status": "error", "result": {
                    "installer_path": installer_relative_path,
                }}

    threading.Thread(target=_run_detection, daemon=True).start()
    return job_id


@app.route("/software/detect/status/<job_id>", methods=["GET"])
@admin_required
def software_detect_status(job_id):
    job = DETECT_JOBS.get(job_id)
    if not job:
        return jsonify({"status": "error"}), 404
    return jsonify(job)


@app.route("/software/detect/retry", methods=["POST"])
@admin_required
def software_detect_retry():
    """Re-run detection on a staged installer, only after a FAILED one: an
    "unknown" answer would not change."""
    path = (request.form.get("path") or "").strip()

    # Same validation as software_detect_discard(), deliberately - the two
    # must agree on what a legal staging path is.
    if not path or not re.match(r"^staging/[A-Za-z0-9]+\.(exe|msi)$", path):
        return jsonify({"error": "invalid path"}), 400

    resolved_path = os.path.abspath(os.path.join(app.root_path, path))
    if not resolved_path.startswith(os.path.abspath(STAGING_FOLDER) + os.sep):
        return jsonify({"error": "invalid path"}), 400

    # The staged file can legitimately be gone - the admin may have hit
    # the replace button, which discards it. Say so instead of starting a
    # job that is guaranteed to fail.
    if not os.path.exists(resolved_path):
        return jsonify({"error": "The uploaded file is no longer available. Please choose it again."}), 404

    return jsonify({"job_id": start_detection_job(resolved_path, path)}), 202


@app.route("/software/detect/discard", methods=["POST"])
@admin_required
def software_detect_discard():
    path = (request.form.get("path") or "").strip()

    if not path or not re.match(r"^staging/[A-Za-z0-9]+\.(exe|msi)$", path):
        return jsonify({"error": "invalid path"}), 400

    resolved_path = os.path.abspath(os.path.join(app.root_path, path))
    if not resolved_path.startswith(os.path.abspath(STAGING_FOLDER) + os.sep):
        return jsonify({"error": "invalid path"}), 400

    try:
        if os.path.exists(resolved_path):
            os.remove(resolved_path)
    except OSError:
        pass

    return jsonify({"ok": True}), 200


def find_duplicate_software(db, name, version):
    """{"match": "exact" | "name_only" | "none"} for name/version, trimmed
    and case-insensitive; one rule for the check endpoint and the guard."""
    name = (name or "").strip()
    version = (version or "").strip()

    if not name:
        return {"match": "none"}

    exact = db.execute(
        """
        SELECT id FROM software
        WHERE LOWER(TRIM(name)) = LOWER(TRIM(?)) AND LOWER(TRIM(version)) = LOWER(TRIM(?))
        LIMIT 1
        """,
        (name, version),
    ).fetchone()
    if exact:
        return {"match": "exact"}

    if version:
        name_only = db.execute(
            "SELECT version FROM software WHERE LOWER(TRIM(name)) = LOWER(TRIM(?)) LIMIT 1",
            (name,),
        ).fetchone()
        if name_only:
            return {"match": "name_only", "existing_version": name_only["version"]}

    return {"match": "none"}


def _chocolatey_url(path, params):
    """quote_via=quote gives %20 for spaces. See note 1 at the top of this
    file - the default "+" encoding silently breaks every query."""
    return CHOCOLATEY_API_BASE + "/" + path + "?" + urlencode(params, quote_via=quote)


def _chocolatey_in_cooldown():
    with _chocolatey_cache_lock:
        return time.time() < _chocolatey_failures["retry_at"]


def _chocolatey_note_failure():
    with _chocolatey_cache_lock:
        _chocolatey_failures["count"] += 1
        if _chocolatey_failures["count"] >= CHOCOLATEY_FAILURE_THRESHOLD:
            _chocolatey_failures["retry_at"] = time.time() + CHOCOLATEY_COOLDOWN_SECONDS
            _chocolatey_failures["count"] = 0
            app.logger.warning(
                f"[chocolatey] repository unreachable, pausing lookups for "
                f"{CHOCOLATEY_COOLDOWN_SECONDS}s")


def _chocolatey_note_success():
    with _chocolatey_cache_lock:
        _chocolatey_failures["count"] = 0
        _chocolatey_failures["retry_at"] = 0.0


def _chocolatey_fetch(url, label):
    """One outbound call, with the failure memory applied. Returns the
    response body, or None if the call could not be completed."""
    if _chocolatey_in_cooldown():
        return None

    try:
        response = http_requests.get(url, timeout=CHOCOLATEY_TIMEOUT)
    except Exception as e:
        app.logger.warning(f"[chocolatey] {label} request failed: {e}")
        _chocolatey_note_failure()
        return None

    if response.status_code != 200:
        app.logger.warning(f"[chocolatey] {label} returned HTTP {response.status_code}")
        # 406 means OUR query was malformed - their docs are explicit that
        # unsupported filters and sort fields answer that way. That is a bug
        # on this side, not the service being down, so it must not put
        # everyone else into a cooldown. Only real unavailability counts.
        if response.status_code >= 500 or response.status_code == 429:
            _chocolatey_note_failure()
        return None

    _chocolatey_note_success()
    return response.text


def _chocolatey_cache_get(key):
    with _chocolatey_cache_lock:
        entry = _chocolatey_cache.get(key)
        if entry and time.time() - entry[0] < CHOCOLATEY_CACHE_TTL_SECONDS:
            return entry
    return None


def _chocolatey_cache_put(key, value):
    with _chocolatey_cache_lock:
        # Capped and cleared when full, so a long-running server cannot leak.
        if len(_chocolatey_cache) > 200:
            _chocolatey_cache.clear()
        _chocolatey_cache[key] = (time.time(), value)


def _chocolatey_clean_name(title):
    """A Chocolatey title made to pass the add-software name rules, or ""
    rather than something mangled ("Intel(R) Driver & Support Assistant"
    has two characters those rules refuse)."""
    if not title:
        return ""
    cleaned = re.sub(r"[^a-zA-Z0-9 .+\-_()\[\]]", " ", title)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()[:50].strip()
    if len(cleaned) < 2 or re.search(r"(.)\1{3,}", cleaned):
        return ""
    return cleaned


def _chocolatey_clean_version(version):
    """Same idea for the version rules (2-30 chars, limited set). Chocolatey
    versions are normally plain dotted numbers and pass untouched."""
    cleaned = (version or "").strip()
    if len(cleaned) < 2 or len(cleaned) > 30:
        return ""
    if "  " in cleaned or re.search(r"(.)\1{3,}", cleaned):
        return ""
    if not re.match(r"^[a-zA-Z0-9 .+\-]+$", cleaned):
        return ""
    return cleaned


def _chocolatey_parse_feed(xml_text):
    """Turn an ATOM feed into package dicts. See note 2 at the top of this
    file for why the id comes out of the ATOM envelope and not the
    properties block."""
    root = ET.fromstring(xml_text)
    packages = []

    for entry in root.findall(ATOM_NS + "entry"):
        title_element = entry.find(ATOM_NS + "title")
        package_id = (title_element.text or "").strip() if title_element is not None else ""

        if not package_id:
            id_element = entry.find(ATOM_NS + "id")
            id_url = (id_element.text or "") if id_element is not None else ""
            match = CHOCOLATEY_ID_FROM_URL.search(id_url)
            package_id = match.group(1) if match else ""

        if not package_id:
            continue

        # Properties are normally a direct <m:properties> child; the iter()
        # fallback covers feeds that nest them under <content>.
        properties = entry.find(ODATA_META_NS + "properties")
        source = properties if properties is not None else entry
        values = {}
        for element in source.iter():
            if element.tag.startswith(ODATA_NS):
                values[element.tag[len(ODATA_NS):]] = (element.text or "").strip()

        try:
            downloads = int(values.get("DownloadCount") or 0)
        except ValueError:
            downloads = 0

        display_title = values.get("Title") or package_id

        packages.append({
            "id": package_id,
            "title": display_title,
            "version": values.get("Version", ""),
            "downloads": downloads,
            # Retired packages still say "Approved"; only the title marks
            # them, anywhere, in any case ("[Deprecated] ...").
            "deprecated": "deprecated" in display_title.lower(),
        })

    return packages


def _chocolatey_relevance(package, term):
    """Sort bucket, lower first. ONE bucket for "id contains the term":
    separate prefix and contains buckets ranked chromedriver above
    GoogleChrome for "chrome". Popularity decides within a bucket."""
    package_id = package["id"].lower()
    title = package["title"].lower()
    term = term.lower()

    if package_id == term:
        return 0
    if term in package_id:
        return 1
    if term in title:
        return 2
    return 3


def search_chocolatey_packages(term):
    """Best-effort search: a list (maybe empty) if the repository answered,
    None if not - "no matches" and "unavailable" mean different things."""
    term = CHOCOLATEY_UNSAFE_CHARS.sub("", term or "").strip()[:50]
    if len(term) < CHOCOLATEY_MIN_TERM_LENGTH:
        return []

    # Cache BEFORE the cooldown check, so anything already looked up keeps
    # working normally right through an outage.
    cache_key = "search:" + term.lower()
    cached = _chocolatey_cache_get(cache_key)
    if cached:
        return cached[1]

    # Search() is the endpoint Chocolatey CLI itself uses. Hand-rolled
    # substringof filters on Packages() are the DIY path their docs warn
    # about, so this stays as close to sanctioned behaviour as possible.
    url = _chocolatey_url("Search()", {
        "searchTerm": "'" + term + "'",
        "targetFramework": "''",
        "includePrerelease": "false",
        "$filter": "IsLatestVersion",
        "$orderby": "DownloadCount desc",
        "$top": str(CHOCOLATEY_SEARCH_FETCH),
        "semVerLevel": "2.0.0",
    })

    body = _chocolatey_fetch(url, "search")
    if body is None:
        return None

    try:
        packages = _chocolatey_parse_feed(body)
    except ET.ParseError as e:
        app.logger.warning(f"[chocolatey] search response was not parseable: {e}")
        return None

    # Popularity alone is not enough: sorted purely by downloads, a search
    # for "notepad" leads with chocolatey-core.extension (100M downloads,
    # matches only on description text). Re-ranking on how well the ID
    # matches, with downloads breaking ties, is what fixes that.
    packages.sort(key=lambda p: (
        _chocolatey_relevance(p, term),
        p["deprecated"],
        -p["downloads"],
    ))

    results = []
    for package in packages[:CHOCOLATEY_SEARCH_RESULTS]:
        results.append({
            "id": package["id"],
            "title": package["title"],
            "name": _chocolatey_clean_name(package["title"]),
            "version": _chocolatey_clean_version(package["version"]),
            "downloads": package["downloads"],
            "deprecated": package["deprecated"],
        })

    _chocolatey_cache_put(cache_key, results)
    return results


def lookup_chocolatey_package(package_id):
    """Exact existence check: a dict if found, {} if the repository has no
    such package (a miss is an empty feed), None if unreachable."""
    package_id = CHOCOLATEY_UNSAFE_CHARS.sub("", package_id or "").strip()[:100]
    if not package_id:
        return {}

    # Cached like search, misses included: a typo will not become a real
    # package within ten minutes.
    cache_key = "id:" + package_id.lower()
    cached = _chocolatey_cache_get(cache_key)
    if cached:
        return cached[1]

    url = _chocolatey_url("Packages()", {
        "$filter": "(tolower(Id) eq '" + package_id.lower() + "') and IsLatestVersion",
        "semVerLevel": "2.0.0",
    })

    body = _chocolatey_fetch(url, "lookup")
    if body is None:
        return None

    try:
        packages = _chocolatey_parse_feed(body)
    except ET.ParseError as e:
        app.logger.warning(f"[chocolatey] lookup response was not parseable: {e}")
        return None

    if not packages:
        _chocolatey_cache_put(cache_key, {})
        return {}

    package = packages[0]
    result = {
        "id": package["id"],
        "title": package["title"],
        "name": _chocolatey_clean_name(package["title"]),
        "version": _chocolatey_clean_version(package["version"]),
        "downloads": package["downloads"],
        "deprecated": package["deprecated"],
    }
    _chocolatey_cache_put(cache_key, result)
    return result


@app.route("/software/chocolatey/search", methods=["GET"])
@admin_required
def chocolatey_search():
    """Type-ahead for the Chocolatey package field. Never errors at the
    HTTP level - an unreachable repository comes back as ok:false so the
    browser can say so quietly and leave the field usable."""
    term = (request.args.get("q") or "").strip()
    results = search_chocolatey_packages(term)

    if results is None:
        return jsonify({"ok": False, "reason": "unavailable", "results": []})

    return jsonify({"ok": True, "results": results})


@app.route("/software/chocolatey/verify", methods=["GET"])
@admin_required
def chocolatey_verify():
    """Existence check for a package id typed by hand rather than picked
    from the suggestions. Advisory only: nothing here blocks the form."""
    package_id = (request.args.get("id") or "").strip()

    if not package_id:
        return jsonify({"ok": True, "found": False, "package": None})

    package = lookup_chocolatey_package(package_id)

    if package is None:
        return jsonify({"ok": False, "reason": "unavailable", "found": False, "package": None})

    if not package:
        return jsonify({"ok": True, "found": False, "package": None})

    return jsonify({"ok": True, "found": True, "package": package})


@app.route("/software/check-duplicate", methods=["GET"])
@admin_required
def check_duplicate_software():
    name = (request.args.get("name") or "").strip()
    version = (request.args.get("version") or "").strip()

    if not name:
        return jsonify({"match": "none"})

    db = get_db()
    return jsonify(find_duplicate_software(db, name, version))


# --- Installer file size -------------------------------------------------
#
# Read from disk at render rather than stored: no column or backfill, and a
# deleted installer shows "-" instead of a stale number. One stat() per row
# is cheap on a short table.
INSTALLER_PATH_PATTERN = re.compile(r"uploads/[A-Za-z0-9]+\.(?:exe|msi)")


def installer_file_size(install_command):
    """Bytes on disk for a local installer, or None (online entry, path not
    recognised, file missing). Same containment guard as the detect routes:
    the resolved path must sit inside UPLOAD_FOLDER."""
    if not install_command:
        return None
    match = INSTALLER_PATH_PATTERN.search(install_command)
    if not match:
        return None
    resolved = os.path.abspath(os.path.join(app.root_path, match.group(0)))
    if not resolved.startswith(os.path.abspath(UPLOAD_FOLDER) + os.sep):
        return None
    try:
        return os.path.getsize(resolved)
    except OSError:
        return None


def format_file_size(size_bytes):
    """Short human label - 812 KB, 47.3 MB, 1.2 GB. Returns None for None so
    the template can decide what a missing size looks like."""
    if size_bytes is None:
        return None
    if size_bytes < 1024:
        return "%d B" % size_bytes
    kilobytes = size_bytes / 1024.0
    if kilobytes < 1024:
        return "%d KB" % round(kilobytes)
    megabytes = kilobytes / 1024.0
    if megabytes < 1024:
        return "%.1f MB" % megabytes
    return "%.1f GB" % (megabytes / 1024.0)


# The Uninstall switch: Silent Install HQ's silent_uninstall, or
# what the admin typed at upload, reduced to these switches and nothing else -
# no path, no "=", no other text. It is only ever handed to an uninstaller
# the agent has already checked (inside Program Files, no shell), and only
# for a program that lists no quiet uninstall of its own. Fixed once the
# entry is added: the edit route never writes it.
UNINSTALL_SWITCH_ALLOWED = ("/S", "/SILENT", "/VERYSILENT", "/qn", "/norestart")
_UNINSTALL_SWITCHES = {switch.casefold(): switch for switch in UNINSTALL_SWITCH_ALLOWED}


def clean_uninstall_switch(text):
    """(switch or None, tokens dropped): the allowed switches in text, in any
    case, written the allowlist's way, each once, in the order given. Every
    other token is dropped. This is the guard, whatever was typed or posted;
    the form only refuses earlier, so the admin sees why."""
    kept, dropped = [], []
    for token in (text or "").split():
        switch = _UNINSTALL_SWITCHES.get(token.casefold())
        if switch is None:
            dropped.append(token)
        elif switch not in kept:
            kept.append(switch)
    return (" ".join(kept) or None), dropped


def with_installer_sizes(rows):
    """Rows as dicts with file_size_bytes (for sorting) and file_size_label."""
    items = []
    for row in rows:
        item = dict(row)
        size = installer_file_size(item.get("install_command"))
        item["file_size_bytes"] = size
        item["file_size_label"] = format_file_size(size)
        items.append(item)
    return items


# The dropdown's last option. Choosing it posts this value with the name
# typed under it in new_category.
NEW_CATEGORY_VALUE = "__new__"
CATEGORY_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 /&\-.()+]*$")


def software_categories(db):
    """Every category, in dropdown order: the built-ins in their own order,
    then the ones admins added, A-Z."""
    rows = db.execute("SELECT name, is_builtin, position FROM software_categories").fetchall()
    builtins = sorted((r for r in rows if r["is_builtin"]), key=lambda r: r["position"])
    added = sorted((r for r in rows if not r["is_builtin"]), key=lambda r: r["name"].lower())
    return [{"name": r["name"], "builtin": bool(r["is_builtin"])} for r in builtins + added]


def clean_category_name(text):
    """(name, error) for a category typed in: trimmed, inner spaces
    collapsed, 2-30 characters of letters, numbers, spaces and / & - . ( ) +,
    starting with a letter or number."""
    name = " ".join((text or "").split())
    if not name:
        return None, "Type a name for the new category."
    if len(name) < 2 or len(name) > 30:
        return None, "A category name must be 2 to 30 characters."
    if not CATEGORY_NAME_PATTERN.match(name):
        return None, ("A category name can only contain letters, numbers, spaces and / & - . ( ) +, "
                      "and must start with a letter or number.")
    return name, None


def resolve_posted_category(db, form):
    """(category, new_name, error) for a posted add or edit form.

    category is the name to save. new_name is set only when it does not
    exist yet and must be created along with the software row - never
    before, so a save that fails leaves no category behind. A typed name
    that matches one already there in all but case picks that one."""
    posted = (form.get("category") or "").strip()
    if posted == NEW_CATEGORY_VALUE:
        name, error = clean_category_name(form.get("new_category"))
        if error:
            return None, None, error
        existing = db.execute("SELECT name FROM software_categories WHERE name = ?", (name,)).fetchone()
        if existing:
            return existing["name"], None, None
        return name, name, None
    if not posted:
        return None, None, None
    existing = db.execute("SELECT name FROM software_categories WHERE name = ?", (posted,)).fetchone()
    if not existing:
        return None, None, "Invalid category."
    return existing["name"], None, None


def add_software_category(db, name):
    """Create an admin category inside the caller's transaction. Returns the
    name as stored - another admin may have added it a moment earlier, in
    another case - and whether this call created it."""
    created = db.execute(
        "INSERT OR IGNORE INTO software_categories (name, is_builtin, created_by) VALUES (?, 0, ?)",
        (name, session.get("admin_id")),
    ).rowcount == 1
    stored = db.execute("SELECT name FROM software_categories WHERE name = ?", (name,)).fetchone()
    return stored["name"], created


def audit_category_added(name):
    log_audit_event(
        actor_admin_id=session.get("admin_id"),
        target_admin_id=None,
        action_type="category_added",
        details=f"Added software category '{name}'",
        target_type="category",
        target_label=name,
    )


def validate_software_identity(name, version, category, category_error=None):
    """The error for name, version or category, or None: shared by Add and
    Edit so both enforce the same rules."""
    if not name:
        return "Name is required."
    if len(name) < 2:
        return "Name must be at least 2 characters."
    if len(name) > 50:
        return "Name must be 50 characters or fewer."
    if "  " in name:
        return "Name cannot contain consecutive spaces."
    if re.search(r"(.)\1{3,}", name):
        return "Name cannot have more than 3 consecutive identical characters."
    if re.search(r"[\U0001F300-\U0001F9FF\U00002600-\U000027BF]", name):
        return "Name cannot contain emojis."
    if not re.match(r"^[a-zA-Z0-9 .+\-_()\[\]]+$", name):
        return "Name can only contain letters, numbers, spaces, and . + - _ ( ) [ ] characters."
    if not version:
        return "Version is required."
    if len(version) < 2:
        return "Version must be at least 2 characters."
    if len(version) > 30:
        return "Version must be 30 characters or fewer."
    if "  " in version:
        return "Version cannot contain consecutive spaces."
    if re.search(r"(.)\1{3,}", version):
        return "Version cannot have more than 3 consecutive identical characters."
    if re.search(r"[\U0001F300-\U0001F9FF\U00002600-\U000027BF]", version):
        return "Version cannot contain emojis."
    if not re.match(r"^[a-zA-Z0-9 .+\-]+$", version):
        return "Version can only contain letters, numbers, dots, hyphens, plus signs, and spaces."
    # The category was resolved against software_categories before this ran
    # (resolve_posted_category); its own error, if any, comes in here so the
    # messages keep their order: name, then version, then category.
    if category_error:
        return category_error
    if not category:
        return "Category is required."
    return None


@app.route("/software", methods=["GET", "POST"])
@admin_required
def software_library():
    db = get_db()
    error_message = request.args.get("error")
    success_message = request.args.get("success")

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        version = request.form.get("version", "").strip()
        category, new_category, category_error = resolve_posted_category(db, request.form)
        software_type = request.form.get("type", "").strip().lower()
        package_name = request.form.get("package_name", "").strip()
        silent_flag = request.form.get("silent_flag", "").strip()
        posted_switch = request.form.get("uninstall_switch", "")
        installer_file = request.files.get("installer_file")
        detected_installer_path = request.form.get("detected_installer_path", "").strip()
        detected_confidence_tier = request.form.get("detected_confidence_tier", "").strip()
        confirm_duplicate = request.form.get("confirm_duplicate", "") == "1"

        install_command = None
        uninstall_command = None
        uninstall_switch = None
        confidence_tier = None
        installer_relative_path = ""
        staged_promote_from = None
        staged_promote_to = None

        # Shared with Edit; the fields only Add has follow.
        identity_error = validate_software_identity(name, version, category, category_error)
        if identity_error:
            error_message = identity_error
        elif software_type not in {"offline", "online"}:
            error_message = "Type must be offline or online."
        elif software_type == "offline":
            # Reuse a file /software/detect already staged and analysed;
            # without the hidden field (JS off) it is saved and identified
            # here.
            reused_installer_path = None
            if detected_installer_path and re.match(r"^staging/[A-Za-z0-9]+\.(exe|msi)$", detected_installer_path):
                staged_full_path = os.path.abspath(os.path.join(app.root_path, detected_installer_path))
                # containment guard: must resolve inside STAGING_FOLDER
                if (staged_full_path.startswith(os.path.abspath(STAGING_FOLDER) + os.sep)
                        and os.path.exists(staged_full_path)):
                    # Not moved yet: a duplicate confirmation may bounce this
                    # request before the INSERT. Only the uploads/ path is
                    # predicted here; the move happens just before the INSERT.
                    staged_filename = os.path.basename(staged_full_path)
                    reused_installer_path = f"uploads/{staged_filename}"
                    staged_promote_from = staged_full_path
                    staged_promote_to = os.path.join(UPLOAD_FOLDER, staged_filename)

            if reused_installer_path:
                installer_relative_path = reused_installer_path
                _, extension = os.path.splitext(installer_relative_path)
                extension = extension.lower()
                confidence_tier = detected_confidence_tier or None

                if not silent_flag:
                    if extension == ".exe":
                        silent_flag = "/S"
                    else:
                        silent_flag = "/quiet /norestart"
                    confidence_tier = "fallback"

                if extension == ".exe":
                    install_command = f'"{installer_relative_path}" {silent_flag}'
                    # Detection already pre-filled the field; what was
                    # posted is the admin's choice, and only its allowed
                    # switches stay.
                    uninstall_switch = clean_uninstall_switch(posted_switch)[0]
                else:
                    install_command = f'msiexec /i "{installer_relative_path}" {silent_flag}'
            elif not installer_file or not installer_file.filename:
                error_message = "Offline software requires an .exe or .msi installer file."
            else:
                original_name = secure_filename(installer_file.filename)
                _, extension = os.path.splitext(original_name)
                extension = extension.lower()

                if extension not in ALLOWED_OFFLINE_EXTENSIONS:
                    error_message = "Only .exe and .msi files are allowed for offline software."
                else:
                    generated_name = f"{uuid.uuid4().hex}{extension}"
                    saved_file_path = os.path.join(UPLOAD_FOLDER, generated_name)
                    installer_file.save(saved_file_path)
                    installer_relative_path = f"uploads/{generated_name}"

                    detected = identify_installer(saved_file_path)
                    if detected and detected["silent_install"] and not silent_flag:
                        silent_flag = detected["silent_install"]
                        confidence_tier = detected["confidence_tier"]
                    uninstall_command = detected["uninstall_command"] if detected else None

                    if not silent_flag:
                        if extension == ".exe":
                            silent_flag = "/S"
                        else:
                            silent_flag = "/quiet /norestart"
                        confidence_tier = "fallback"

                    if extension == ".exe":
                        install_command = f'"{installer_relative_path}" {silent_flag}'
                        # No detection ran before this post, so nothing could
                        # be pre-filled: a typed switch wins, else Silent
                        # Install HQ's, and only its allowed switches stay.
                        uninstall_switch = clean_uninstall_switch(
                            posted_switch if posted_switch.strip() else uninstall_command)[0]
                    else:
                        install_command = f'msiexec /i "{installer_relative_path}" {silent_flag}'
        else:
            if not package_name:
                error_message = "Online software requires a Chocolatey package name."
            else:
                install_command = f"choco install {package_name} -y"
                uninstall_command = None

        duplicate_match = None
        duplicate_existing_version = None

        if not error_message and install_command and not confirm_duplicate:
            duplicate = find_duplicate_software(db, name, version)
            if duplicate["match"] in ("exact", "name_only"):
                duplicate_match = duplicate["match"]
                duplicate_existing_version = duplicate.get("existing_version")

        if duplicate_match:
            # Back to the confirmation dialog. Nothing has left staging/ yet,
            # so a reused file threads its ORIGINAL staging/ path back, which
            # the resubmit's validation and promotion expect.
            software_items = with_installer_sizes(db.execute(
                "SELECT id, name, version, type, install_command, uninstall_switch, category, date_added, confidence_tier "
                "FROM software ORDER BY date_added DESC"
            ).fetchall())
            return render_template(
                "software_library.html",
                software_items=software_items,
                error_message=None,
                success_message=None,
                duplicate_match=duplicate_match,
                duplicate_existing_version=duplicate_existing_version,
                form_name=name,
                form_version=version,
                # A new category is not saved until the software is, so the
                # bounce puts "Choose to add..." and the typed name back.
                form_category=NEW_CATEGORY_VALUE if new_category else category,
                form_new_category=new_category or "",
                categories=software_categories(db),
                form_type=software_type,
                form_silent_flag=silent_flag,
                form_uninstall_switch=uninstall_switch or "",
                form_package_name=package_name,
                form_detected_installer_path=(detected_installer_path if staged_promote_from else installer_relative_path),
                form_detected_confidence_tier=confidence_tier or "",
            )

        if not error_message and install_command:
            if staged_promote_from and staged_promote_to:
                # The only move into uploads/, just before the INSERT. A file
                # gone since (discarded, cleaned up) fails soft rather than
                # inserting a row that points at nothing.
                try:
                    os.replace(staged_promote_from, staged_promote_to)
                except OSError:
                    error_message = "The staged installer file could not be found. Please re-select it and try again."

        if not error_message and install_command:
            # Created in the same transaction as the software, so it exists
            # exactly when the entry that uses it does.
            category_created = False
            if new_category:
                category, category_created = add_software_category(db, new_category)
            db.execute(
                """
                INSERT INTO software (name, version, type, install_command, uninstall_command, uninstall_switch,
                                      category, confidence_tier)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (name, version or None, software_type, install_command, uninstall_command or None, uninstall_switch,
                 category or None, confidence_tier),
            )
            db.commit()
            label = software_label(name, version)
            log_audit_event(
                actor_admin_id=session.get("admin_id"),
                target_admin_id=None,
                action_type="software_added",
                details=f"Added software '{label}'",
                target_type="software",
                target_label=label,
            )
            if category_created:
                audit_category_added(category)
            return redirect(url_for("software_library"))

    software_items = with_installer_sizes(db.execute(
        "SELECT id, name, version, type, install_command, uninstall_switch, category, date_added, confidence_tier "
        "FROM software ORDER BY date_added DESC"
    ).fetchall())
    return render_template(
        "software_library.html",
        software_items=software_items,
        categories=software_categories(db),
        error_message=error_message,
        success_message=success_message,
    )


@app.route("/software/<int:software_id>/edit", methods=["POST"])
@admin_required
def update_software(software_id):
    """Rename, re-version or re-categorise an entry. Not type,
    install_command or confidence_tier: they describe what was uploaded or
    verified. Past jobs keep their own snapshot name."""
    db = get_db()
    software = db.execute(
        "SELECT id, name, version, category FROM software WHERE id = ?",
        (software_id,),
    ).fetchone()
    if not software:
        return redirect(url_for("software_library", error="Software not found."))

    name = request.form.get("name", "").strip()
    version = request.form.get("version", "").strip()
    category, new_category, category_error = resolve_posted_category(db, request.form)

    error = validate_software_identity(name, version, category, category_error)
    if error:
        return redirect(url_for("software_library", error=error))

    # Same-name-and-version guard as Add, minus this row itself - otherwise
    # saving a drawer without changing anything would report a duplicate.
    clash = db.execute(
        "SELECT id FROM software WHERE id != ? AND LOWER(name) = LOWER(?) "
        "AND LOWER(COALESCE(version, '')) = LOWER(?)",
        (software_id, name, version),
    ).fetchone()
    if clash:
        return redirect(url_for(
            "software_library",
            error=f"Another entry is already called '{software_label(name, version)}'.",
        ))

    unchanged = (
        (software["name"] or "") == name
        and (software["version"] or "") == version
        and (software["category"] or "") == category
    )
    if unchanged:
        return redirect(url_for("software_library"))

    # Committed BEFORE the audit entry: log_audit_event() opens its own
    # connection, which a pending write lock here would block.
    category_created = False
    if new_category:
        category, category_created = add_software_category(db, new_category)
    db.execute(
        "UPDATE software SET name = ?, version = ?, category = ? WHERE id = ?",
        (name, version or None, category or None, software_id),
    )
    db.commit()
    if category_created:
        audit_category_added(category)

    before = software_label(software["name"], software["version"])
    after = software_label(name, version)
    details = f"Edited software '{before}'"
    if before != after:
        details = f"Renamed software '{before}' to '{after}'"
    if (software["category"] or "") != category:
        details += f" (category {software['category'] or 'none'} -> {category})"
    log_audit_event(
        actor_admin_id=session.get("admin_id"),
        target_admin_id=None,
        # Its own type: the Audit Trail derives labels and filters from
        # action_type, so it needs no template change.
        action_type="software_edited",
        details=details,
        target_type="software",
        target_label=after,
    )
    return redirect(url_for("software_library", success=f"'{after}' updated."))


@app.route("/software/<int:software_id>/delete", methods=["POST"])
@admin_required
def delete_software(software_id):
    db = get_db()
    # name/version are selected purely so the audit entry can say WHAT was
    # deleted - they must be captured before the DELETE below removes the row.
    software = db.execute(
        "SELECT id, name, version, type, install_command FROM software WHERE id = ?",
        (software_id,),
    ).fetchone()

    if not software:
        return redirect(url_for("software_library", error="Software not found"))

    active_job = db.execute(
        """
        SELECT id FROM jobs
        WHERE software_id = ? AND status IN ('pending', 'in_progress')
        LIMIT 1
        """,
        (software_id,),
    ).fetchone()
    if active_job:
        return redirect(url_for("software_library", error="Cannot delete software with active jobs"))

    if software["type"] == "offline":
        path_match = re.search(r"uploads/[^\"\s]+\.(?:exe|msi)", software["install_command"] or "", re.IGNORECASE)
        if path_match:
            installer_filename = os.path.basename(path_match.group(0))
            installer_full_path = os.path.join(UPLOAD_FOLDER, installer_filename)
            try:
                if os.path.exists(installer_full_path):
                    os.remove(installer_full_path)
            except OSError:
                pass

    label = software_label(software["name"], software["version"])
    db.execute("DELETE FROM software WHERE id = ?", (software_id,))
    # What it installed is still on the PCs. The rows stay, shown by their
    # snapshot name, and stop pointing at an entry that no longer exists.
    db.execute("UPDATE installations SET software_id = NULL WHERE software_id = ?", (software_id,))
    db.commit()
    log_audit_event(
        actor_admin_id=session.get("admin_id"),
        target_admin_id=None,
        action_type="software_deleted",
        details=f"Deleted software '{label}'",
        target_type="software",
        target_label=label,
    )
    return redirect(url_for("software_library", success="Software deleted successfully"))


def _bulk_ids(field):
    """Integer ids from a bulk form field, deduplicated in ticked order.
    Junk is dropped silently: a hand-edited form should do nothing."""
    seen = []
    for raw in request.form.getlist(field):
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value not in seen:
            seen.append(value)
    return seen


@app.route("/software/bulk-delete", methods=["POST"])
@admin_required
def bulk_delete_software():
    """Delete several entries. Partial, like the job bulk routes; per entry
    exactly what delete_software() does, so the audit trail and the disk
    agree with the library."""
    ids = _bulk_ids("software_ids")
    if not ids:
        return redirect(url_for("software_library", error="No software selected."))

    db = get_db()
    placeholders = ",".join("?" * len(ids))
    rows = db.execute(
        f"SELECT id, name, version, type, install_command FROM software WHERE id IN ({placeholders})",
        ids,
    ).fetchall()

    deleted, blocked, labels = 0, 0, []
    for software in rows:
        active_job = db.execute(
            "SELECT id FROM jobs WHERE software_id = ? AND status IN ('pending', 'in_progress') LIMIT 1",
            (software["id"],),
        ).fetchone()
        if active_job:
            blocked += 1
            continue

        if software["type"] == "offline":
            path_match = re.search(
                r"uploads/[^\"\s]+\.(?:exe|msi)", software["install_command"] or "", re.IGNORECASE
            )
            if path_match:
                installer_full_path = os.path.join(UPLOAD_FOLDER, os.path.basename(path_match.group(0)))
                try:
                    if os.path.exists(installer_full_path):
                        os.remove(installer_full_path)
                except OSError:
                    pass

        label = software_label(software["name"], software["version"])
        db.execute("DELETE FROM software WHERE id = ?", (software["id"],))
        # Same as delete_software(): the installations stay, unlinked.
        db.execute("UPDATE installations SET software_id = NULL WHERE software_id = ?", (software["id"],))
        deleted += 1
        labels.append(label)

    db.commit()
    for label in labels:
        log_audit_event(
            actor_admin_id=session.get("admin_id"),
            target_admin_id=None,
            action_type="software_deleted",
            details=f"Deleted software '{label}'",
            target_type="software",
            target_label=label,
        )

    if not deleted:
        return redirect(url_for(
            "software_library",
            error="Nothing was deleted - every selected entry has a queued or running job.",
        ))
    message = f"Deleted {deleted} entr{'y' if deleted == 1 else 'ies'}."
    if blocked:
        message += f" Skipped {blocked} with active jobs."
    return redirect(url_for("software_library", success=message))


@app.route("/problem-reports/bulk-status", methods=["POST"])
@admin_required
def bulk_update_reports_status():
    """Set the status on several reports (Problem Reports page). Unlike the
    Tasks card's route, not narrowed to the caller's own: this page lists
    every report, and narrowing would skip ticked rows."""
    new_status = (request.form.get("status") or "").strip().lower()
    if new_status not in ALLOWED_REPORT_STATUSES:
        return redirect(url_for("problem_reports_page", report_error="Invalid problem report status selected."))

    ids = _bulk_ids("report_ids")
    if not ids:
        return redirect(url_for("problem_reports_page", report_error="No reports selected."))

    db = get_db()
    placeholders = ",".join("?" * len(ids))
    # status_changed_at moves only on rows whose status actually changes, so
    # re-applying a status a report already has does not reset its clock.
    changed = db.execute(
        f"SELECT id FROM problem_reports WHERE id IN ({placeholders}) AND status IS NOT ? "
        "AND archived_at IS NULL",
        ids + [new_status],
    ).fetchall()
    changed_ids = [row["id"] for row in changed]
    if changed_ids:
        marks = ",".join("?" * len(changed_ids))
        db.execute(
            f"UPDATE problem_reports SET status = ?, status_changed_at = ? WHERE id IN ({marks})",
            [new_status, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")] + changed_ids,
        )
        db.commit()

    if not changed_ids:
        return redirect(url_for("problem_reports_page"))
    queue_report_finished_emails(changed_ids, new_status)
    log_audit_event(
        actor_admin_id=session.get("admin_id"),
        target_admin_id=None,
        action_type="report_status_changed",
        details=(f"Status set to {report_status_label(new_status)} on "
                 f"{audit_count(len(changed_ids), 'report')}: {audit_id_list(changed_ids)}"),
        target_type="report",
        target_label=audit_count(len(changed_ids), "report"),
    )
    return redirect(url_for(
        "problem_reports_page",
        report_success=f"{len(changed_ids)} report(s) set to {report_status_label(new_status)}.",
    ))


@app.route("/problem-reports/bulk-priority", methods=["POST"])
@admin_required
def bulk_update_reports_priority():
    new_priority = (request.form.get("priority") or "").strip().lower()
    if new_priority not in ALLOWED_PRIORITIES:
        return redirect(url_for("problem_reports_page", report_error="Invalid priority selected."))

    ids = _bulk_ids("report_ids")
    if not ids:
        return redirect(url_for("problem_reports_page", report_error="No reports selected."))

    db = get_db()
    placeholders = ",".join("?" * len(ids))
    # Read first, so the write and the audit entry cover reports that exist
    # and are not archived - an archived report is read-only - rather than
    # whatever ids the form carried.
    found_ids = [row["id"] for row in db.execute(
        f"SELECT id FROM problem_reports WHERE id IN ({placeholders}) AND archived_at IS NULL", ids).fetchall()]
    if not found_ids:
        return redirect(url_for("problem_reports_page", report_error="None of the selected reports can be changed."))
    marks = ",".join("?" * len(found_ids))
    db.execute(
        f"UPDATE problem_reports SET priority = ? WHERE id IN ({marks})",
        [new_priority] + found_ids,
    )
    db.commit()
    if found_ids:
        log_audit_event(
            actor_admin_id=session.get("admin_id"),
            target_admin_id=None,
            action_type="report_priority_changed",
            details=(f"Priority set to {priority_label(new_priority)} on "
                     f"{audit_count(len(found_ids), 'report')}: {audit_id_list(found_ids)}"),
            target_type="report",
            target_label=audit_count(len(found_ids), "report"),
        )
    return redirect(url_for(
        "problem_reports_page",
        report_success=f"{len(found_ids)} report(s) set to {new_priority} priority.",
    ))


@app.route("/problem-reports/bulk-assign", methods=["POST"])
@admin_required
def bulk_assign_reports():
    ids = _bulk_ids("report_ids")
    if not ids:
        return redirect(url_for("problem_reports_page", report_error="No reports selected."))

    db = get_db()
    assigned_to_raw = (request.form.get("assigned_to") or "").strip()
    # The bulk bar's "Unassigned" option posts __none__, not "" - an empty
    # value there is its placeholder ("Assign to...") and must not be
    # mistaken for a deliberate unassign.
    if assigned_to_raw == "__none__":
        assigned_to_raw = ""
    assigned_to = None
    if assigned_to_raw:
        try:
            assigned_to = int(assigned_to_raw)
        except ValueError:
            return redirect(url_for("problem_reports_page", report_error="Invalid technician selected."))
        # Same check the single-report routes make: the target must exist AND
        # still be an admin, so a demoted account cannot be assigned work.
        technician = db.execute(
            "SELECT id FROM admins WHERE id = ? AND role = 'admin'",
            (assigned_to,),
        ).fetchone()
        if not technician:
            return redirect(url_for("problem_reports_page", report_error="Invalid technician selected."))

    placeholders = ",".join("?" * len(ids))
    found_ids = [row["id"] for row in db.execute(
        f"SELECT id FROM problem_reports WHERE id IN ({placeholders}) AND archived_at IS NULL", ids).fetchall()]
    if not found_ids:
        return redirect(url_for("problem_reports_page", report_error="None of the selected reports can be changed."))
    marks = ",".join("?" * len(found_ids))
    db.execute(
        f"UPDATE problem_reports SET assigned_to = ? WHERE id IN ({marks})",
        [assigned_to] + found_ids,
    )
    db.commit()
    if found_ids:
        reports = f"{audit_count(len(found_ids), 'report')}: {audit_id_list(found_ids)}"
        log_audit_event(
            actor_admin_id=session.get("admin_id"),
            target_admin_id=None,
            action_type="report_assigned",
            details=(f"Unassigned {reports}" if assigned_to is None
                     else f"Assigned to {audit_admin_name(db, assigned_to)} on {reports}"),
            target_type="report",
            target_label=audit_count(len(found_ids), "report"),
        )
    who = "unassigned" if assigned_to is None else "reassigned"
    return redirect(url_for("problem_reports_page", report_success=f"{len(found_ids)} report(s) {who}."))


@app.route("/problem-reports/bulk-delete", methods=["POST"])
@admin_required
def bulk_delete_reports():
    """Delete several reports and their photos, via the single-report
    helper, so orphaned uploads never accumulate."""
    ids = _bulk_ids("report_ids")
    if not ids:
        return redirect(url_for("problem_reports_page", report_error="No reports selected."))

    db = get_db()
    placeholders = ",".join("?" * len(ids))
    rows = db.execute(
        f"SELECT id, photo_paths, archived_at FROM problem_reports WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    if not rows:
        return redirect(url_for("problem_reports_page", report_error="Problem report not found."))
    # Archived reports only, as for a single delete. The Archived tab is the
    # only bar with Delete, so a mix here means a hand-made request: the
    # archived ones go, the rest are named as skipped.
    skipped = sum(1 for row in rows if not row["archived_at"])
    rows = [row for row in rows if row["archived_at"]]
    if not rows:
        return redirect(url_for("problem_reports_page", report_error="Only archived reports can be deleted."))

    for row in rows:
        delete_report_photos(row["photo_paths"])

    found_ids = [row["id"] for row in rows]
    marks = ",".join("?" * len(found_ids))
    db.execute(f"DELETE FROM problem_reports WHERE id IN ({marks})", found_ids)
    db.commit()
    # One entry for the whole request, like a batch deploy: the count, and
    # which reports, so the trail still says what went.
    count = len(found_ids)
    noun = "report" if count == 1 else "reports"
    log_audit_event(
        actor_admin_id=session.get("admin_id"),
        target_admin_id=None,
        action_type="report_deleted",
        details=f"Deleted {count} {noun}: {audit_id_list(found_ids)}",
        target_type="report",
        target_label=f"{count} {noun}",
    )
    message = f"Deleted {len(found_ids)} report(s)."
    if skipped:
        message += f" {skipped} skipped because {'it isn' if skipped == 1 else 'they aren'}'t archived."
    return redirect(url_for("problem_reports_page", view="archived", report_success=message))


@app.route("/problem-reports/purge-archived", methods=["POST"])
@admin_required
def purge_archived_reports():
    """Delete every archived report, and its photos, in one request.

    The confirm dialog names a count, and the form posts that count back:
    if the archive has changed since the page was drawn - someone archived
    or restored meanwhile - nothing is deleted, rather than a different
    number of reports from the one that was agreed to."""
    db = get_db()
    rows = db.execute(
        "SELECT id, photo_paths FROM problem_reports WHERE archived_at IS NOT NULL ORDER BY id"
    ).fetchall()
    if not rows:
        return redirect(url_for("problem_reports_page", view="archived",
                                report_error="There are no archived reports."))
    if request.form.get("expected_count", type=int) != len(rows):
        return redirect(url_for("problem_reports_page", view="archived",
                                report_error="The archive changed since this page loaded. Reload and try again."))

    ids = [row["id"] for row in rows]
    marks = ",".join("?" * len(ids))
    db.execute(f"DELETE FROM problem_reports WHERE id IN ({marks}) AND archived_at IS NOT NULL", ids)
    db.commit()
    for row in rows:
        delete_report_photos(row["photo_paths"])
    reports = audit_count(len(ids), "archived report")
    log_audit_event(
        actor_admin_id=session.get("admin_id"),
        target_admin_id=None,
        action_type="report_purged",
        details=(f"Deleted the {reports}" if len(ids) == 1 else f"Deleted all {reports}")
        + f": {audit_id_list(ids)}",
        target_type="report",
        target_label=reports,
    )
    return redirect(url_for("problem_reports_page", view="archived", report_success=f"Deleted {reports}."))


def _not_finished_note(count):
    """The skipped half of an archive message: "2 skipped because they
    aren't finished yet"."""
    if count == 1:
        return "1 skipped because it isn't finished yet"
    return f"{count} skipped because they aren't finished yet"


@app.route("/problem-reports/archive", methods=["POST"])
@admin_required
def archive_reports():
    """Move finished reports out of the main list, for one id or many; a
    mixed selection archives the finished ones and counts the skips. Not
    confirmed first: Restore undoes it exactly."""
    ids = _bulk_ids("report_ids")
    if not ids:
        return redirect(url_for("problem_reports_page", report_error="No reports selected."))
    db = get_db()
    placeholders = ",".join("?" * len(ids))
    rows = db.execute(
        f"SELECT id, status FROM problem_reports WHERE id IN ({placeholders}) AND archived_at IS NULL",
        ids,
    ).fetchall()
    finished = [row["id"] for row in rows if row["status"] in FINISHED_REPORT_STATUSES]
    skipped = len(rows) - len(finished)
    if not finished:
        if len(rows) == 1:
            error = f"Report #{rows[0]['id']} isn't finished yet, so it can't be archived."
        elif rows:
            error = f"Nothing archived: {len(rows)} reports aren't finished yet."
        else:
            error = "Problem report not found."
        return redirect(url_for("problem_reports_page", report_error=error))

    marks = ",".join("?" * len(finished))
    db.execute(
        f"UPDATE problem_reports SET archived_at = ?, archived_by = ? WHERE id IN ({marks})",
        [datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), session.get("admin_id")] + finished,
    )
    db.commit()
    archived = f"{audit_count(len(finished), 'report')}: {audit_id_list(finished)}"
    log_audit_event(
        actor_admin_id=session.get("admin_id"),
        target_admin_id=None,
        action_type="report_archived",
        details=f"Archived {archived}" + (f"; {_not_finished_note(skipped)}" if skipped else ""),
        target_type="report",
        target_label=audit_count(len(finished), "report"),
    )
    if len(finished) == 1 and not skipped:
        message = f"Report #{finished[0]} archived."
    else:
        message = f"Archived {audit_count(len(finished), 'report')}"
        message += f"; {_not_finished_note(skipped)}." if skipped else "."
    return redirect(url_for("problem_reports_page", report_success=message))


@app.route("/problem-reports/restore", methods=["POST"])
@admin_required
def restore_reports():
    """Put archived reports back in the main list, unchanged - only the two
    archive columns are cleared."""
    ids = _bulk_ids("report_ids")
    if not ids:
        return redirect(url_for("problem_reports_page", view="archived", report_error="No reports selected."))
    db = get_db()
    placeholders = ",".join("?" * len(ids))
    restored = [row["id"] for row in db.execute(
        f"SELECT id FROM problem_reports WHERE id IN ({placeholders}) AND archived_at IS NOT NULL", ids,
    ).fetchall()]
    if not restored:
        return redirect(url_for("problem_reports_page", view="archived",
                                report_error="None of the selected reports are archived."))
    marks = ",".join("?" * len(restored))
    db.execute(
        f"UPDATE problem_reports SET archived_at = NULL, archived_by = NULL WHERE id IN ({marks})",
        restored,
    )
    db.commit()
    log_audit_event(
        actor_admin_id=session.get("admin_id"),
        target_admin_id=None,
        action_type="report_restored",
        details=f"Restored {audit_count(len(restored), 'report')}: {audit_id_list(restored)}",
        target_type="report",
        target_label=audit_count(len(restored), "report"),
    )
    message = (f"Report #{restored[0]} restored." if len(restored) == 1
               else f"Restored {audit_count(len(restored), 'report')}.")
    return redirect(url_for("problem_reports_page", view="archived", report_success=message))


@app.route("/equipment/bulk-delete", methods=["POST"])
@admin_required
def bulk_delete_equipment():
    """Delete several equipment rows at once.

    Audited per row rather than as one summary line: the audit trail is
    read to answer "what happened to that monitor", and a single entry
    saying "deleted 6 items" cannot answer it.
    """
    ids = _bulk_ids("equipment_ids")
    if not ids:
        return redirect(url_for("equipment_page", error="No equipment selected."))

    db = get_db()
    placeholders = ",".join("?" * len(ids))
    rows = db.execute(
        f"SELECT * FROM equipment WHERE id IN ({placeholders})", ids
    ).fetchall()
    if not rows:
        return redirect(url_for("equipment_page", error="That equipment no longer exists."))

    found = [row["id"] for row in rows]
    marks = ",".join("?" * len(found))
    db.execute(f"DELETE FROM equipment WHERE id IN ({marks})", found)
    db.commit()

    for row in rows:
        label = equipment_label(row["manufacturer"], row["model"]) or row["category"]
        log_audit_event(
            actor_admin_id=session.get("admin_id"),
            target_admin_id=None,
            action_type="equipment_deleted",
            details=f"Deleted {row['quantity']} x {row['category']} ({label}) from {row['location']}",
            target_type="equipment",
            target_label=row["category"],
        )
    return redirect(url_for(
        "equipment_page",
        success=f"Deleted {len(found)} equipment record(s).",
    ))


@app.route("/equipment/bulk-condition", methods=["POST"])
@admin_required
def bulk_update_equipment_condition():
    """Set the condition on several rows - the "a shelf of keyboards just
    came back from repair" case."""
    status = (request.form.get("asset_status") or "").strip()
    if status not in ALLOWED_ASSET_STATUSES:
        return redirect(url_for("equipment_page", error="Invalid condition."))

    ids = _bulk_ids("equipment_ids")
    if not ids:
        return redirect(url_for("equipment_page", error="No equipment selected."))

    db = get_db()
    placeholders = ",".join("?" * len(ids))
    found_ids = [row["id"] for row in db.execute(
        f"SELECT id FROM equipment WHERE id IN ({placeholders})", ids).fetchall()]
    updated = db.execute(
        f"UPDATE equipment SET asset_status = ? WHERE id IN ({placeholders})",
        [status] + ids,
    ).rowcount
    db.commit()
    if not updated:
        return redirect(url_for("equipment_page", error="That equipment no longer exists."))
    log_audit_event(
        actor_admin_id=session.get("admin_id"),
        target_admin_id=None,
        action_type="equipment_updated",
        details=f"Condition set to {status} on {audit_count(len(found_ids), 'item')}: {audit_id_list(found_ids)}",
        target_type="equipment",
        target_label=audit_count(len(found_ids), "item"),
    )
    return redirect(url_for(
        "equipment_page",
        success=f"{updated} record(s) marked {status}.",
    ))


@app.route("/equipment/bulk-location", methods=["POST"])
@admin_required
def bulk_move_equipment():
    """Move several rows to another room, normalised as a single edit is."""
    location, error = equipment_location(request.form.get("location"))
    if error:
        return redirect(url_for("equipment_page", error=error))

    ids = _bulk_ids("equipment_ids")
    if not ids:
        return redirect(url_for("equipment_page", error="No equipment selected."))

    db = get_db()
    placeholders = ",".join("?" * len(ids))
    found_ids = [row["id"] for row in db.execute(
        f"SELECT id FROM equipment WHERE id IN ({placeholders})", ids).fetchall()]
    updated = db.execute(
        f"UPDATE equipment SET location = ? WHERE id IN ({placeholders})",
        [location] + ids,
    ).rowcount
    db.commit()
    if not updated:
        return redirect(url_for("equipment_page", error="That equipment no longer exists."))
    log_audit_event(
        actor_admin_id=session.get("admin_id"),
        target_admin_id=None,
        action_type="equipment_updated",
        details=f"Moved {audit_count(len(found_ids), 'item')} to {location}: {audit_id_list(found_ids)}",
        target_type="equipment",
        target_label=audit_count(len(found_ids), "item"),
    )
    return redirect(url_for(
        "equipment_page",
        success=f"{updated} record(s) moved to {location}.",
    ))


@app.route("/uploads/<path:filename>", methods=["GET"])
def serve_uploaded_file(filename):
    # Not cached like static files: agents fetch these with no version
    # query, and a replaced installer must never be served stale. max_age=0
    # keeps the conditional request.
    return send_from_directory(UPLOAD_FOLDER, filename, max_age=0)

# --prod listens on two ports. 8080 is where every agent polls, so it must
# open or nothing starts. 80 is for browsers, so the lab's address needs no
# port - and if it cannot open, only that convenience is lost, never the
# agents. Both are served by the one Waitress process with no proxy in
# between, so every request still carries the visitor's real address.
AGENT_PORT = 8080
BROWSER_PORT = 80


def open_listening_socket(port):
    """A TCP socket bound to 0.0.0.0:port, for Waitress to listen on.

    Bound here rather than by Waitress because Waitress binds a list of
    addresses one after another and gives up at the first that fails,
    leaving the ones before it bound and nothing serving them. Binding each
    port separately is what lets a port 80 that will not open be skipped.

    Exclusive on Windows. Waitress's own sockets use SO_REUSEADDR, and on
    Windows two sockets that both set it can bind the same port and split
    its traffic - a second Waitress server, or a second copy of AppEase,
    would quietly share port 80 or 8080 instead of failing. Exclusive makes
    a busy port fail cleanly, which is what lets port 80 fall back.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", port))
    except OSError:
        sock.close()
        raise
    return sock


if __name__ == "__main__":
    if "--prod" in sys.argv:
        import logging as prod_logging
        from waitress import create_server

        # What waitress.serve() does first, so its own log lines still show.
        prod_logging.basicConfig()
        # No try: without 8080 AppEase must not start, unreachable by agents.
        listeners = [open_listening_socket(AGENT_PORT)]
        try:
            listeners.append(open_listening_socket(BROWSER_PORT))
            print(f"Starting AppEase on Waitress (production) with 8 threads: agents on port "
                  f"{AGENT_PORT}, browsers on port {BROWSER_PORT}")
        except OSError as exc:
            app.logger.warning(
                "Port %s could not be opened (%s). AppEase is running on port %s only. "
                "Agents are unaffected, but browsers need the port in the address: "
                "http://<this computer's address>:%s. Usually another program already "
                "holds port %s (IIS, the Windows HTTP service, another web server), or "
                "this account is not allowed to open it.",
                BROWSER_PORT, exc, AGENT_PORT, AGENT_PORT, BROWSER_PORT,
            )
            print(f"Starting AppEase on Waitress (production) with 8 threads: agents and "
                  f"browsers on port {AGENT_PORT} (port {BROWSER_PORT} unavailable, see the warning)")
        server = create_server(app, sockets=listeners, threads=8)
        server.print_listen("Serving on http://{}:{}")
        server.run()
    else:
        # debug=False, and explicitly so that FLASK_DEBUG cannot turn it back
        # on. Debug mode puts Werkzeug's debugger - a Python console in the
        # browser - and full tracebacks in front of every machine that can
        # reach this port. Turning it off removes the console everywhere;
        # binding to 127.0.0.1 instead would only hide it from other
        # machines while leaving it live on this one.
        print("Starting AppEase on Flask dev server (no debugger). Use --prod for Waitress.")
        app.run(host="0.0.0.0", debug=False)
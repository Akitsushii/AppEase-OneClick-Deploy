"""QR sticker labels. The key assertions DECODE the PNGs with a real QR
reader: a code too dense to scan would otherwise only show on paper. That
half needs opencv-python-headless and is skipped without it.
"""

import base64
import importlib
import io
import os
import re
import secrets
import shutil
import sys
import tempfile
from urllib.parse import parse_qs, urlparse

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_DIR = os.path.join(PROJECT_ROOT, "server")

SECRET = "b" * 64
PUBLIC_HOST = "report.example.test"

passed, failed, skipped = [], [], []


def check(label, condition, detail=""):
    (passed if condition else failed).append(label if condition else f"{label}  <-- {detail}")


try:
    import cv2
    import numpy as np
    HAVE_DECODER = True
except ImportError:
    HAVE_DECODER = False


def decode_qr(data_uri):
    """Read a data-URI PNG back into the string it encodes."""
    raw = base64.b64decode(data_uri.split(",", 1)[1])
    from PIL import Image
    image = Image.open(io.BytesIO(raw)).convert("RGB")
    array = np.array(image)[:, :, ::-1]
    text, _, _ = cv2.QRCodeDetector().detectAndDecode(array)
    return text


def stage():
    tmp = tempfile.mkdtemp(prefix="appease-qr-")
    dest = os.path.join(tmp, "server")
    shutil.copytree(
        SERVER_DIR, dest,
        ignore=shutil.ignore_patterns("appease.db*", "__pycache__", "*.pyc", ".env"),
    )
    return tmp, dest


def load_app(dest, secret, public_host):
    for mod in ("app", "database"):
        sys.modules.pop(mod, None)
    if secret is None:
        os.environ.pop("REPORT_QR_SECRET", None)
    else:
        os.environ["REPORT_QR_SECRET"] = secret
    if public_host is None:
        os.environ.pop("PUBLIC_REPORT_HOST", None)
    else:
        os.environ["PUBLIC_REPORT_HOST"] = public_host
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
    for host, loc, ip in [
        ("LAB-PC-01", "Lab 1", "192.168.1.11"),
        ("LAB-PC-02", "Lab 1", "192.168.1.12"),
        ("LAB-PC-03", None, "192.168.1.13"),
    ]:
        conn.execute(
            "INSERT OR IGNORE INTO computers (hostname, location, ip_address, status) "
            "VALUES (?, ?, ?, 'online')", (host, loc, ip))
    conn.commit()
    ids = {r["hostname"]: r["id"] for r in conn.execute(
        "SELECT id, hostname FROM computers").fetchall()}
    conn.execute("UPDATE admins SET role = 'admin' WHERE id = 1")
    conn.commit()
    conn.close()
    appmod.app.config["TESTING"] = True
    return appmod, ids


def admin_client(appmod):
    client = appmod.app.test_client()
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_role"] = "admin"
    return client


def data_uris(html):
    return re.findall(r'src="(data:image/png;base64,[^"]+)"', html)


tmpdir, dest = stage()
try:
    app, ids = load_app(dest, SECRET, PUBLIC_HOST)
    c = admin_client(app)

    # --- access control ----------------------------------------------------
    anon = app.app.test_client()
    r = anon.get("/computers/qr-labels", follow_redirects=False)
    check("signed-out request is redirected away", r.status_code == 302, f"got {r.status_code}")

    r = c.get("/computers/qr-labels", headers={"Host": PUBLIC_HOST}, follow_redirects=False)
    check("the label sheet is NOT reachable on the public host",
          r.status_code == 404, f"got {r.status_code}")

    # --- one computer ------------------------------------------------------
    one = ids["LAB-PC-01"]
    r = c.get(f"/computers/qr-labels?ids={one}")
    check("single-id sheet renders", r.status_code == 200, f"got {r.status_code}")
    html = r.get_data(as_text=True)
    check("single-id sheet has exactly one QR image", len(data_uris(html)) == 1,
          f"found {len(data_uris(html))}")
    check("single-id sheet names the hostname", "LAB-PC-01" in html)
    check("single-id sheet shows the location", "Lab 1" in html)
    check("single-id sheet carries the instruction line",
          "Scan to report a problem" in html)
    check("no auto-print script without ?print=1", "window.print();" not in html
          or "addEventListener('load'" not in html)

    r = c.get(f"/computers/qr-labels?ids={one}&print=1")
    check("?print=1 injects the auto-print script",
          "addEventListener('load'" in r.get_data(as_text=True))

    # --- what the codes actually decode to ---------------------------------
    if HAVE_DECODER:
        html = c.get(f"/computers/qr-labels?ids={one}").get_data(as_text=True)
        decoded = decode_qr(data_uris(html)[0])
        check("the QR decodes at all", bool(decoded), "decoder returned nothing")
        parts = urlparse(decoded)
        query = parse_qs(parts.query)
        check("QR points at https", parts.scheme == "https", parts.scheme)
        check("QR points at the public host", parts.netloc == PUBLIC_HOST, parts.netloc)
        check("QR path is /report", parts.path == "/report", parts.path)
        check("QR carries computer=<hostname>",
              query.get("computer") == ["LAB-PC-01"], str(query.get("computer")))
        check("QR carries a t= token", bool(query.get("t")), "no token")
        check("the token in the QR verifies against the hostname",
              app.verify_computer_token("LAB-PC-01", query["t"][0]), "token did not verify")
        check("the token is not valid for a DIFFERENT hostname",
              not app.verify_computer_token("LAB-PC-02", query["t"][0]), "token cross-matched")
    else:
        skipped.append("QR decode tests (opencv-python-headless not installed)")

    # --- several computers, and ordering -----------------------------------
    pair = f"{ids['LAB-PC-01']},{ids['LAB-PC-03']}"
    html = c.get(f"/computers/qr-labels?ids={pair}").get_data(as_text=True)
    check("two-id sheet has two QR images", len(data_uris(html)) == 2,
          f"found {len(data_uris(html))}")
    check("two-id sheet includes both hostnames",
          "LAB-PC-01" in html and "LAB-PC-03" in html)
    check("a computer NOT asked for is absent", "LAB-PC-02" not in html)

    if HAVE_DECODER:
        decoded = [decode_qr(u) for u in data_uris(html)]
        hosts = [parse_qs(urlparse(d).query).get("computer", [""])[0] for d in decoded]
        check("each code carries its OWN hostname, in row order",
              hosts == ["LAB-PC-01", "LAB-PC-03"], str(hosts))

    # --- no ids means every computer ---------------------------------------
    html = c.get("/computers/qr-labels").get_data(as_text=True)
    check("no ids prints every computer", len(data_uris(html)) == 3,
          f"found {len(data_uris(html))}")

    # --- junk ids ----------------------------------------------------------
    # Nothing numeric at all: every chunk is dropped, leaving no id list, which
    # is the same as asking for no ids - so the sheet covers everything.
    html = c.get("/computers/qr-labels?ids=abc,%20,-1,,").get_data(as_text=True)
    check("non-numeric ids are dropped and the sheet covers everything",
          len(data_uris(html)) == 3, f"found {len(data_uris(html))}")

    # Numeric but no such computer: an id list WAS supplied and matched
    # nothing, so this must not quietly print all 3 instead. (This is the case
    # a stale bookmark or a deleted row produces.)
    r = c.get("/computers/qr-labels?ids=9999", follow_redirects=True)
    check("an unknown id redirects back with an error rather than printing everything",
          "No computers to print labels for" in r.get_data(as_text=True),
          f"got {r.status_code}")

    r = c.get(f"/computers/qr-labels?ids={one}';DROP TABLE computers;--")
    check("an injection attempt does not 500", r.status_code in (200, 302), f"got {r.status_code}")
    conn = importlib.import_module("database").get_db()
    still = conn.execute("SELECT COUNT(*) AS n FROM computers").fetchone()["n"]
    conn.close()
    check("the computers table survived the injection attempt", still == 3, f"{still} rows")

    # --- a machine with no location ----------------------------------------
    html = c.get(f"/computers/qr-labels?ids={ids['LAB-PC-03']}").get_data(as_text=True)
    check("a computer with no location still renders", len(data_uris(html)) == 1)
    check("no empty location line is printed", 'class="qr-loc"' not in html)

    # --- signing off -------------------------------------------------------
    app2, ids2 = load_app(dest, None, PUBLIC_HOST)
    c2 = admin_client(app2)
    html = c2.get(f"/computers/qr-labels?ids={ids2['LAB-PC-01']}").get_data(as_text=True)
    check("labels still generate with no signing secret", len(data_uris(html)) == 1)
    check("an on-screen warning says the labels are unsigned",
          "unsigned" in html.lower(), "no warning shown")
    if HAVE_DECODER:
        query = parse_qs(urlparse(decode_qr(data_uris(html)[0])).query)
        check("an unsigned QR carries computer= but no t=",
              query.get("computer") == ["LAB-PC-01"] and "t" not in query, str(query))

    # --- tunnel host unset -------------------------------------------------
    app3, ids3 = load_app(dest, SECRET, None)
    c3 = admin_client(app3)
    html = c3.get(f"/computers/qr-labels?ids={ids3['LAB-PC-01']}").get_data(as_text=True)
    check("labels still generate with no public host configured",
          len(data_uris(html)) == 1)
    if HAVE_DECODER:
        decoded = decode_qr(data_uris(html)[0])
        check("with no public host the QR falls back to the request's own origin",
              decoded.startswith("http://localhost/report?"), decoded[:60])

finally:
    os.chdir(PROJECT_ROOT)
    shutil.rmtree(tmpdir, ignore_errors=True)

print()
for line in passed:
    print(f"  PASS  {line}")
for line in failed:
    print(f"  FAIL  {line}")
for line in skipped:
    print(f"  SKIP  {line}")
print(f"\n{len(passed)} passed, {len(failed)} failed, "
      f"{len(skipped)} skipped, {len(passed) + len(failed)} total")
sys.exit(1 if failed else 0)
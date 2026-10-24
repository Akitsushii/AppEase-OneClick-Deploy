"""Native dropdown lists follow the theme: in dark mode every native
select's options use Canvas / CanvasText, out-ranking every other option
rule, and light mode is untouched. Python cannot run a browser, so this
checks app.css's rules and specificity; the click-test opens one.
"""

import importlib
import os
import re
import secrets
import shutil
import sys
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_DIR = os.path.join(PROJECT_ROOT, "server")

passed, failed = [], []


def check(label, condition, detail=""):
    (passed if condition else failed).append(label if condition else f"{label}  <-- {detail}")


def css_rules(text):
    """(selector_list, declarations, inside_at_rule) for every style rule."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    rules, i, depth_stack = [], 0, []
    buf = ""
    while i < len(text):
        ch = text[i]
        if ch == "{":
            head = buf.strip()
            buf = ""
            if head.startswith("@"):
                depth_stack.append(head)
                i += 1
                continue
            end = text.index("}", i)
            rules.append((head, text[i + 1:end], bool(depth_stack)))
            i = end + 1
            continue
        if ch == "}":
            if depth_stack:
                depth_stack.pop()
            buf = ""
            i += 1
            continue
        buf += ch
        i += 1
    return rules


def declarations(body):
    out = {}
    for part in body.split(";"):
        if ":" in part:
            name, value = part.split(":", 1)
            out[name.strip().lower()] = value.strip()
    return out


def specificity(selector):
    """(ids, classes+attributes+pseudo-classes, elements) for a simple
    selector - enough for the plain selectors app.css uses around options."""
    s = re.sub(r"::[a-z-]+", " ", selector)
    ids = len(re.findall(r"#[\w-]+", s))
    classes = len(re.findall(r"\.[\w-]+", s)) + len(re.findall(r"\[[^\]]*\]", s)) + len(re.findall(r":[a-z-]+", s))
    s = re.sub(r"#[\w-]+|\.[\w-]+|\[[^\]]*\]|:[a-z-]+(\([^)]*\))?", " ", s)
    elements = len(re.findall(r"(?<![\w-])[a-z][\w-]*", s))
    return ids, classes, elements


tmpdir = tempfile.mkdtemp(prefix="appease-dropdowns-")
dest = os.path.join(tmpdir, "server")
shutil.copytree(SERVER_DIR, dest, ignore=shutil.ignore_patterns(
    "appease.db*", "__pycache__", "*.pyc", ".env", "uploads", "staging", "report_photos", "avatars"))
os.environ["REPORT_QR_SECRET"] = "f" * 64
os.environ.pop("PUBLIC_REPORT_HOST", None)
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
sys.path.insert(0, dest)
os.chdir(dest)

try:
    app = importlib.import_module("app")
    import database
    database.init_db()
    conn = database.get_db()
    conn.execute("INSERT INTO computers (hostname, location, ip_address, status) "
                 "VALUES ('LAB-PC-01', 'Lab 1', '10.0.0.5', 'online')")
    conn.commit()
    conn.close()
    app.app.config["TESTING"] = True

    # =======================================================================
    print("1-2. What the page declares")
    # =======================================================================
    def page(path, cookie=None, signed_in=True):
        client = app.app.test_client()
        if signed_in:
            with client.session_transaction() as sess:
                sess["admin_id"] = 1
                sess["admin_role"] = "admin"
        if cookie is not None:
            client.set_cookie("theme", cookie)
        return client.get(path).get_data(as_text=True)

    def scheme(html):
        found = re.search(r'<meta name="color-scheme" content="([^"]*)"', html)
        return found.group(1) if found else None

    def html_is_dark(html):
        tag = re.search(r"<html[^>]*>", html)
        return bool(tag) and re.search(r'class="[^"]*\bdark\b', tag.group(0)) is not None

    for cookie, expected in (("dark", "dark"), ("light", "light"), (None, "light"), ("DARK", "light")):
        html = page("/problem-reports", cookie)
        check(f"theme cookie {cookie!r}: the page declares color-scheme {expected}",
              scheme(html) == expected and html_is_dark(html) == (expected == "dark"),
              (scheme(html), html_is_dark(html)))

    token = app.sign_computer("LAB-PC-01")
    html = page(f"/report?computer=LAB-PC-01&t={token}", "dark", signed_in=False)
    check("the public report form stays light with a dark cookie",
          scheme(html) == "light" and not html_is_dark(html), (scheme(html), html_is_dark(html)))

    # =======================================================================
    print("3-5. The option rule")
    # =======================================================================
    css = open(os.path.join(dest, "static", "css", "app.css"), encoding="utf-8").read()
    rules = css_rules(css)

    def targets_option(selector):
        return re.search(r"(?:^|[\s>+~])(option|optgroup)(?=$|[:\s\[.#])", selector.strip()) is not None

    dark_rule = None
    for head, body, nested in rules:
        selectors = [s.strip() for s in head.split(",")]
        decl = declarations(body)
        if (set(selectors) == {"html.dark select option", "html.dark select optgroup"}
                and decl.get("background-color", "").lower() == "canvas"
                and decl.get("color", "").lower() == "canvastext"):
            dark_rule = (selectors, nested)
    check("in dark mode every select's options take Canvas / CanvasText", dark_rule is not None)
    check("...in a rule that always applies (not inside a media query)", dark_rule is not None and not dark_rule[1])

    ours = specificity("html.dark select option")
    stronger = []
    for head, body, nested in rules:
        decl = declarations(body)
        if not ({"color", "background-color", "background"} & set(decl)):
            continue
        for selector in (s.strip() for s in head.split(",")):
            if not targets_option(selector) or selector.startswith("html.dark select "):
                continue
            if specificity(selector) >= ours:
                stronger.append(selector)
    check("no other rule that colours an option out-ranks it - the Priority dropdown's light list included",
          not stronger, stronger)

    disabled = [declarations(body) for head, body, nested in rules
                if head.strip() == "html.dark select option:disabled"]
    check("a disabled option is greyed out in dark mode (GrayText)",
          bool(disabled) and disabled[0].get("color", "").lower() == "graytext", disabled)
    check("...and that rule out-ranks the option rule",
          specificity("html.dark select option:disabled") > ours)

    unscoped = [head for head, body, nested in rules
                if any(targets_option(s) and s.strip().startswith(("select option", "option"))
                       for s in head.split(","))
                and declarations(body).get("background-color", "").lower() == "canvas"]
    check("light mode is untouched: the Canvas rule exists only under html.dark", not unscoped, unscoped)
    forced = [head for head, body, nested in rules
              if declarations(body).get("color-scheme", "").lower().startswith("light")]
    check("app.css never forces a light colour scheme", not forced, forced)

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

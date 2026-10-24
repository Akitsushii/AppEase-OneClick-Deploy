"""Render-level gate for the four auth pages, which nothing else in t/
asserts on. Templates are rendered directly with a stub url_for: no Flask
and no database, so the real appease.db can never be touched.
"""
import ast
import os
import re
import sys

import jinja2

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TEMPLATES = os.path.join(ROOT, "server", "templates")
APP_CSS = os.path.join(ROOT, "server", "static", "css", "app.css")
APP_PY = os.path.join(ROOT, "server", "app.py")

passed = 0
failed = []


def check(label, condition):
    global passed
    if condition:
        passed += 1
    else:
        failed.append(label)
        print(f"  FAIL: {label}")


env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES))
# Enough url_for for markup: every call here is an endpoint or a static
# filename, and the tests care about structure, not the exact path.
env.globals["url_for"] = lambda ep, **kw: (
    "/" + ep + ("/" + kw["filename"] if "filename" in kw else "")
)

# Elements with no closing tag, which the balance walk must not push.
VOID = {"meta", "link", "img", "br", "hr", "input", "source", "path", "circle", "rect"}


def tag_balance(html):
    """Return (still-open tags, mismatched closers) for a rendered page.
    Comments, <script> and <style> bodies are stripped first: a tag name
    inside them is text, not markup."""
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    html = re.sub(r"(?s)<script\b[^>]*>.*?</script>", "", html)
    html = re.sub(r"(?s)<style\b[^>]*>.*?</style>", "", html)
    stack, bad = [], []
    for m in re.finditer(r"<\s*(/?)\s*([a-zA-Z][\w-]*)([^>]*?)(/?)\s*>", html):
        closing, tag, selfclose = m.group(1), m.group(2).lower(), m.group(4)
        if tag in VOID or selfclose == "/" or tag == "!doctype":
            continue
        if closing:
            if not stack or stack[-1] != tag:
                bad.append(f"</{tag}>")
            else:
                stack.pop()
        else:
            stack.append(tag)
    return stack, bad


def card_inner(html):
    """Return the inner HTML of .auth-form-card by walking the div nesting:
    position alone is not containment."""
    open_at = html.find('<div class="auth-form-card">')
    if open_at == -1:
        return None
    i = html.index(">", open_at) + 1
    start, depth = i, 1
    for m in re.finditer(r"<(/?)div\b[^>]*?(/?)>", html[i:]):
        if m.group(2) == "/":
            continue
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return html[start:i + m.start()]
    return None


# The cooldown message is read out of app.py rather than retyped, so this
# render case cannot drift from what the login route actually sends. Parsed,
# not imported: this suite runs with no Flask and no staged copy.
LOGIN_COOLDOWN_MESSAGE = None
for node in ast.parse(open(APP_PY, "rb").read()).body:
    if (isinstance(node, ast.Assign) and len(node.targets) == 1
            and getattr(node.targets[0], "id", None) == "LOGIN_COOLDOWN_MESSAGE"):
        LOGIN_COOLDOWN_MESSAGE = ast.literal_eval(node.value)
check("app.py defines LOGIN_COOLDOWN_MESSAGE as a plain string",
      isinstance(LOGIN_COOLDOWN_MESSAGE, str))
LOGIN_COOLDOWN_MESSAGE = LOGIN_COOLDOWN_MESSAGE or "LOGIN_COOLDOWN_MESSAGE missing"

# (template, label, context, must contain, must NOT contain)
CASES = [
    ("login.html", "normal", {},
     ["Welcome to AppEase", "Sign in to your account to continue",
      "Deploy. Track.", "Report.",
      'name="username"', "BulSU Meneses Campus", "auth-brand-panel",
      "auth-compact-brand"],
     ['name="email"', "First run", "First-time setup"]),

    ("login.html", "wrong password", {"error": "Invalid username or password"},
     ["auth-error", "Invalid username or password"],
     []),

    # Refused by the sign-in cooldown. The message names the reset as the way
    # out, so the page it renders on has to still carry the link to it.
    ("login.html", "cooldown", {"error": LOGIN_COOLDOWN_MESSAGE},
     ['<p class="auth-error">' + LOGIN_COOLDOWN_MESSAGE + "</p>",
      "about half an hour", "reset your password by email",
      'href="/forgot_password"', "Forgot password?", 'name="password"'],
     ["Invalid username or password"]),

    ("forgot_password.html", "normal", {},
     ["Forgot your password?", "Locked out of your account?",
      "A reset link goes to the email address on your account",
      'name="email"', "Send reset link", "Back to sign in", "15 minutes"],
     ['name="username"']),

    ("forgot_password.html", "NO FORM - after sending",
     {"sent_message": "We've sent a reset link to the email provided (if an "
                      "account exists). Check your spam folder if it doesn't "
                      "arrive shortly."},
     ["auth-notice", "if an account exists", "Back to sign in", "15 minutes",
      "Forgot your password?"],
     ["<form", 'name="email"']),

    ("reset_password.html", "normal", {"token": "t"},
     ["Set a new password", "Almost there.",
      "Choose a password you have not used here before",
      'name="password"', 'name="confirm_password"', "Save new password",
      "Back to sign in", "password-toggle.js"],
     ["Request a new link"]),

    ("reset_password.html", "NO FORM - dead link",
     {"invalid": "This reset link has expired or has already been used."},
     ["Link no longer valid", "has expired or has already been used",
      "Request a new link"],
     ["<form", "Save new password", 'name="password"']),

    ("reset_password.html", "NO FORM - done",
     {"done": "Your password has been changed."},
     ["Password changed", "Your password has been changed.", "Back to sign in"],
     ["<form", "Save new password"]),

    # set_password is the page a brand-new admin lands on straight after
    # signing in, so it has to look like the page they just came from.
    ("set_password.html", "normal", {"username": "jdoe"},
     ["Set your password", "One more step.", "Your account is ready.",
      "Signed in as", "jdoe", 'name="new_password"', 'name="confirm_password"',
      "data-password-policy", "Sign out instead", "password-policy.js",
      "password-toggle.js", "auth-brand-panel", "auth-compact-brand"],
     ["bg-canvas", "appease-appicon.svg", "appease-appicon-32.png",
      "appease-appicon-180.png"]),

    ("set_password.html", "no username in session", {},
     ["Set your password", "temporary password", "Sign out instead"],
     ["Signed in as"]),

    ("set_password.html", "rejected password", {"error_message": "That password is too short."},
     ["auth-error", "That password is too short.", "data-password-policy"],
     []),
]

rendered = {}
for name, label, ctx, must, must_not in CASES:
    html = env.get_template(name).render(**ctx)
    rendered[(name, label)] = html
    where = f"{name} [{label}]"

    open_tags, bad = tag_balance(html)
    check(f"{where}: every tag closes", not open_tags and not bad)

    for needle in must:
        check(f"{where}: contains {needle!r}", needle in html)
    for needle in must_not:
        check(f"{where}: does NOT contain {needle!r}", needle not in html)

    # The pre-paint pair only works above the stylesheet links - it covers the
    # gap BEFORE a stylesheet has loaded, so a rule in app.css cannot reach it.
    meta_at = html.find('<meta name="color-scheme"')
    style_at = html.find("<style>html { background-color: #1e2128; }</style>")
    css_at = html.find('rel="stylesheet"')
    check(f"{where}: pre-paint pair sits above the stylesheet links",
          0 <= meta_at < css_at and 0 <= style_at < css_at)

# --- the empty-footer contract ------------------------------------------
# login has no footer content, and app.css collapses it with .auth-foot:empty.
# :empty does not match an element containing whitespace, so the block must
# stay on one line in auth_base.html.
login_html = rendered[("login.html", "normal")]
check("login renders a literally empty footer, so .auth-foot:empty matches",
      '<footer class="auth-foot"></footer>' in login_html)

# --- structure -----------------------------------------------------------
sources = {}
for name in ["login.html", "forgot_password.html", "reset_password.html",
             "set_password.html"]:
    sources[name] = open(os.path.join(TEMPLATES, name), "rb").read().decode("utf-8")
    check(f"{name} extends auth_base.html",
          '{% extends "auth_base.html" %}' in sources[name])
    check(f"{name} carries no shell of its own (no <html>)",
          "<html" not in sources[name])

base = open(os.path.join(TEMPLATES, "auth_base.html"), "rb").read().decode("utf-8")
for block in ["title", "headline", "brand_lede", "brand_features",
              "heading", "helper", "form", "foot", "scripts"]:
    check(f"auth_base.html declares the {block!r} block",
          "{% block " + block + " %}" in base)

check("auth_base.html keeps the footer block on one line (the :empty contract)",
      '<footer class="auth-foot">{% block foot %}{% endblock %}</footer>' in base)

# --- the literal that must be kept in sync ------------------------------
# The inline pre-paint colour duplicates app.css on purpose - these pages
# must not borrow a token - so it is checked rather than trusted.
css = open(APP_CSS, "rb").read().decode("utf-8")
# .auth-stage is the surface, so it is what the literal must match.
stage = re.search(r"\.auth-stage\s*\{[^}]*background-color:\s*(#[0-9a-fA-F]{6})", css)
check("app.css still paints the stage a colour", stage is not None)
if stage:
    check("the inline pre-paint literal matches .auth-stage in app.css",
          f"<style>html {{ background-color: {stage.group(1)}; }}</style>" in base)
check("the brand panel no longer paints a background of its own",
      re.search(r"\n\.auth-brand-panel \{[^}]*background", css) is None)

# No ellipse: it must not creep back onto the surface.
check("the ellipse rule is gone", ".auth-brand-panel::before" not in css)
check("no ellipse anywhere on these pages", ".auth-stage::before" not in css)
for gone in ["#15171c", "top: -30vh", "left: -30%", "height: 160vh"]:
    check(f"no leftover of the ellipse's {gone!r}", gone not in css)
# Its stacking context lifted the children above it, so it goes too.
check("the swoosh stacking block went with it",
      "Every direct child sits above the swoosh" not in css)

# Retired classes must not linger as dead rules.
for dead in [".auth-main", ".auth-card", ".auth-logo",
             ".auth-brand-feature-chip"]:
    check(f"{dead} is gone from app.css", dead + " " not in css and dead + "\n" not in css)


# --- one layout, no attribute -------------------------------------------
# All four pages share one layout, so no data-auth-layout: its (0,2,0)
# selectors could silently beat the 880px media query's (0,1,0).
check("the shell carries no layout attribute", 'data-auth-layout="' not in base)
check("no layout block survives in the shell", "{% block auth_layout %}" not in base)
for name in sources:
    check(f"{name} declares no layout of its own", "auth_layout" not in sources[name])
for (name, label), html in rendered.items():
    check(f"{name} [{label}]: renders no layout attribute",
          "data-auth-layout" not in html)
check("no layout selector survives in app.css", "[data-auth-layout" not in css)

# --- the spread mode's three feature blocks -----------------------------
FEATURES = [
    ("Software Installation", "From one dashboard to every desk."),
    ("Equipment Tracking", "Track it. Trust it."),
    ("Problem Reporting", "Every issue, one scan away."),
]
login_normal = rendered[("login.html", "normal")]
for title, line in FEATURES:
    check(f"login's brand panel carries the {title!r} block",
          title in login_normal and line in login_normal)
check("login renders exactly three feature blocks",
      login_normal.count('class="auth-brand-feature"') == 3)

# No icon chips: a hairline rule separates the blocks.
check("no feature block carries an icon chip",
      "auth-brand-feature-chip" not in login_normal)
feature_list = re.search(r'<ul class="auth-brand-features">(.*?)</ul>',
                         login_normal, re.S)
check("login renders the feature list", feature_list is not None)
if feature_list:
    items = feature_list.group(1)
    check("the feature blocks carry no icons at all", "<svg" not in items)
    check("each block is a title and a line, nothing else",
          items.count('class="auth-brand-feature-title"') == 3
          and items.count('class="auth-brand-feature-line"') == 3)

# The dividers. A rule on top of every block plus one under the last closes
# the group at both ends - without the last-child rule the group is open at
# the bottom and reads as three loose items.
HAIRLINE = "rgba(255, 255, 255, 0.09)"
feat_rule = re.search(r"\n\.auth-brand-feature \{(.*?)\n\}", css, re.S)
check("app.css still has a rule for the feature block", feat_rule is not None)
if feat_rule:
    check("every feature block carries a top hairline",
          f"border-top: 1px solid {HAIRLINE}" in feat_rule.group(1))
    check("the blocks are tightened from the 1.25rem that measured 41px apart",
          "padding: 0.875rem 0" in feat_rule.group(1))
last_rule = re.search(r"\.auth-brand-feature:last-child \{(.*?)\n\}", css, re.S)
check("the last block is closed off with a bottom hairline",
      last_rule is not None
      and f"border-bottom: 1px solid {HAIRLINE}" in last_rule.group(1))

# The joint below the headline belongs to whatever follows it - 64px for the
# feature list, 24px for the lede - which is what lets ONE column rule serve a
# page with a feature list and a page with a lede, with no conditional and no
# layout attribute.
group_rule = re.search(r"\n\.auth-brand-features \{(.*?)\n\}", css, re.S)
check("app.css still has a rule for the feature group", group_rule is not None)
if group_rule:
    check("the feature group carries its own joint below the headline",
          re.search(r"margin:\s*64px 0 0", group_rule.group(1)) is not None)
    check("the feature group never centres itself - that tore it off the "
          "headline twice", "auto" not in group_rule.group(1))
    check("the group's own gap is gone, so the hairlines meet",
          re.search(r"gap:\s*0;", group_rule.group(1)) is not None)

lede_rule = re.search(r"\n\.auth-brand-lede \{(.*?)\n\}", css, re.S)
check("app.css still has a rule for the lede", lede_rule is not None)
if lede_rule:
    check("the lede sits closer to its headline than the feature list does",
          re.search(r"margin-top:\s*24px", lede_rule.group(1)) is not None)
    check("the lede is sized for the column it lives in, not 15px",
          "clamp(" in lede_rule.group(1))
    check("the lede keeps its own measure", "max-width" in lede_rule.group(1))

# The headline and what follows are ONE unit, centred together: centring
# the lower block in the leftover space (`margin: auto 0`) is biased by
# whatever sits above it.
mid_rule = re.search(r"\n\.auth-brand-mid \{(.*?)\n\}", css, re.S)
check("app.css lays out the left column", mid_rule is not None)
if mid_rule:
    check("the column centres the headline and what follows as one unit",
          "justify-content: safe center" in mid_rule.group(1))
    # safe, not plain center: a 1366x768 lab monitor makes this column shorter
    # than its content, and plain centring would overflow both ways.
    check("it centres SAFELY, so a short lab monitor start-aligns",
          "safe center" in mid_rule.group(1))
    check("the column's own gap is zero - the joint belongs to the block below",
          re.search(r"gap:\s*0;", mid_rule.group(1)) is not None)
    # The 817px grid track is the measure, so a max-width here would compete.
    check("the column leaves the measure to its grid track",
          "max-width" not in mid_rule.group(1))
check("no rule reintroduces an auto margin on the feature group",
      re.search(r"\.auth-brand-features \{[^}]*margin:\s*auto", css) is None)

# --- the showcase layout itself ------------------------------------------
grid = re.search(r"\n\.auth-split \{(.*?)\n\}", css, re.S)
check("app.css lays out the page", grid is not None)
if grid:
    g = grid.group(1)
    check("the page is a grid, not the 50/50 flex",
          "display: grid" in g and "display: flex" not in g)
    check("the left column and the card are its two tracks",
          re.search(r"grid-template-columns:\s*minmax\(0, 817px\) minmax\(0, 440px\)", g)
          is not None)
    # A radial-gradient and NOT an image file: the labs are offline, and a
    # decorative asset can 404.
    check("the dot grid is a CSS gradient", "radial-gradient(" in g)
    check("the dot grid is not an image file",
          "url(" not in g and ".png" not in g and ".svg" not in g)
    check("the dot grid is tiled by background-size", "background-size:" in g)

# The card is the only light surface on the page.
check("the white right half is gone",
      re.search(r"\n\.auth-form-panel \{[^}]*background:\s*#f{3,6}", css) is None)
check("the 50/50 flex basis is gone with it",
      re.search(r"\n\.auth-form-panel \{[^}]*flex:\s*1 1 50%", css) is None
      and re.search(r"\n\.auth-brand-panel \{[^}]*flex:\s*1 1 50%", css) is None)

# login's headline is hard-wrapped into spans, so there is no wrapping left
# to balance and balance would fight them.
head_rule = re.search(r"\n\.auth-headline \{(.*?)\n\}", css, re.S)
check("app.css styles the headline", head_rule is not None)
if head_rule:
    check("text-wrap: balance is gone", "balance" not in head_rule.group(1))
    check("the ch measure is gone with it - the grid track is the measure",
          "26ch" not in head_rule.group(1))
check("app.css defines the headline line class", "\n.auth-headline-line {" in css)

login_head = re.search(r'<p class="auth-headline">(.*?)</p>', login_normal, re.S)
check("login renders its headline", login_head is not None)
if login_head:
    h = login_head.group(1)
    check("login's headline is two hard-wrapped lines",
          h.count('class="auth-headline-line"') == 1
          and h.count('class="auth-headline-accent"') == 1)
    check("the accent word is the second line", h.find("auth-headline-line") < h.find("auth-headline-accent"))
    # Students report faults; the technician resolves them. The accent word
    # must not claim something the system does not do.
    check("the accent word is 'Report.', not 'Resolve'",
          "Report." in h and "Resolve" not in h)

# Both sizes ramp with the viewport. Fixed px on a panel that is half of
# anything from 880 to 1920 is the thing being fixed here.
for cls in ["auth-brand-feature-title", "auth-brand-feature-line"]:
    rule = re.search(r"\." + cls + r" \{(.*?)\n\}", css, re.S)
    check(f".{cls} still has a rule", rule is not None)
    if rule:
        check(f".{cls} sizes fluidly rather than pinning px",
              "clamp(" in rule.group(1))

# The dot grid is the surface's only decoration.
check("the dot grid is the only decoration left",
      "radial-gradient(" in css and ".auth-brand-panel::before" not in css)

# The centred pages fill the lede instead, and must not inherit login's blocks.
for name in ["forgot_password.html", "reset_password.html", "set_password.html"]:
    html = rendered[(name, "normal")]
    check(f"{name} renders no feature blocks", "auth-brand-feature" not in html)
    lede = re.search(r'<p class="auth-brand-lede">(.*?)</p>', html, re.S)
    check(f"{name} fills the brand lede",
          lede is not None and bool(lede.group(1).strip()))

# login fills brand_features instead, so its lede paragraph renders LITERALLY
# empty - .auth-brand-lede:empty collapses it, and :empty does not match an
# element containing whitespace.
check("login renders an empty lede paragraph, so :empty matches",
      '<p class="auth-brand-lede"></p>' in login_normal)
check("auth_base.html keeps the lede block on one line (the :empty contract)",
      '<p class="auth-brand-lede">{% block brand_lede %}{% endblock %}</p>' in base)
check("app.css collapses the empty lede", ".auth-brand-lede:empty" in css)

# --- the copyright line, on every page ----------------------------------
COPYRIGHT = "&copy; 2026 AppEase &middot; All rights reserved"
for (name, label), html in rendered.items():
    check(f"{name} [{label}]: carries the copyright line", COPYRIGHT in html)
check("the copyright sits at the foot of the brand panel, not in the form",
      re.search(r'<p class="auth-brand-legal">' + re.escape(COPYRIGHT)
                + r"</p>\s*</aside>", login_normal) is not None)

# The panel that carries it is display:none below 880px, so a phone would have
# had no copyright at all. It is re-homed under the help block for those
# widths - which means it renders twice, and app.css shows exactly one.
for (name, label), html in rendered.items():
    where = f"{name} [{label}]"
    check(f"{where}: renders the copyright in both homes",
          html.count(COPYRIGHT) == 2)
    check(f"{where}: the compact copyright carries the same words",
          f'<p class="auth-legal-compact">{COPYRIGHT}</p>' in html)

# Twice in the OUTPUT, once in the SOURCE. A Jinja variable holds the string so
# the two cannot drift apart the way two hand-typed copies would.
check("auth_base.html states the copyright exactly once",
      base.count(COPYRIGHT) == 1)
check("auth_base.html holds it in a variable both homes read",
      re.search(r"\{%-?\s*set AUTH_LEGAL\s*=", base) is not None
      and base.count("{{ AUTH_LEGAL|safe }}") == 2)

# It belongs to the page, not to the panel or the card: outside BOTH.
compact_at = login_normal.find('class="auth-legal-compact"')
check("the compact copyright sits outside the brand panel",
      compact_at > login_normal.find("</aside>"))
check("the compact copyright sits outside the card",
      COPYRIGHT not in (card_inner(login_normal) or COPYRIGHT))
check("the compact copyright sits under the help block",
      compact_at > login_normal.find('class="auth-help"'))

# Exactly one of the two is ever visible: the compact line is display:none by
# default and turns on in the same media query that hides the panel.
check("app.css hides the compact copyright by default",
      re.search(r"\.auth-legal-compact\s*\{[^}]*display:\s*none", css) is not None)
narrow = re.search(r"@media \(max-width: 880px\) \{(.*?)\n\}", css, re.S)
check("app.css has the 880px rule", narrow is not None)
if narrow:
    body = narrow.group(1)
    check("the 880px rule hides the brand panel",
          re.search(r"\.auth-brand-panel\s*\{[^}]*display:\s*none", body) is not None)
    check("the same rule shows the compact copyright instead",
          re.search(r"\.auth-legal-compact\s*\{[^}]*display:\s*block", body) is not None)

# --- forgot_password says the 15-minute rule ONCE ------------------------
# Said once, in the card foot at the point of action - never repeated in
# the brand lede.
forgot = rendered[("forgot_password.html", "normal")]
forgot_lede = re.search(r'<p class="auth-brand-lede">(.*?)</p>', forgot, re.S)
check("forgot_password still fills its brand lede", forgot_lede is not None)
if forgot_lede:
    check("the brand lede does not repeat the 15-minute rule",
          "15 minutes" not in forgot_lede.group(1))
check("the card foot still states the 15-minute rule",
      "The link works for 15 minutes and can only be used once."
      in (card_inner(forgot) or ""))
check("forgot_password states the 15-minute rule exactly once",
      forgot.count("15 minutes") == 1)


# --- the form card and the help line ------------------------------------
HELP_Q = "Need help accessing your account?"
HELP_A = "Contact the Computer Technician of BulSU Meneses"
for (name, label), html in rendered.items():
    where = f"{name} [{label}]"
    check(f"{where}: the form sits in a card", 'class="auth-form-card"' in html)
    check(f"{where}: carries the help line", HELP_Q in html and HELP_A in html)
    # Exactly one copy, wherever it lives - the wording is defined once in
    # auth_base.html and only PLACED by the layout.
    check(f"{where}: renders the help block exactly once",
          html.count('class="auth-help"') == 1 and html.count(HELP_A) == 1)
    inner = card_inner(html)
    check(f"{where}: the card's closing tag is findable", inner is not None)
    # Inside the card on every page: below it, the block would be loose
    # text on the dark surface.
    check(f"{where}: the help line sits INSIDE the card",
          inner is not None and HELP_Q in inner)

# The shell states the wording once, so no copy can drift.
check("auth_base.html states the contact wording exactly once",
      base.count(HELP_A) == 1)
# One placement, so none of the per-layout slot machinery.
check("the two-slot contact machinery is gone",
      "AUTH_HELP" not in base and "card_help" not in base and "page_help" not in base)
for name in sources:
    check(f"{name} does not restate the contact wording",
          HELP_A not in sources[name])
# All four must render byte-identical contact copy.
help_texts = {re.search(r'<div class="auth-help">(.*?)</div>', h, re.S).group(1).strip()
              for h in rendered.values()}
check("every page renders identical contact copy", len(help_texts) == 1)

# The contact line is plain text. There is no support address or extension
# anywhere in the app to point at, and a blue line that does nothing when
# clicked is worse than a grey one that never promised anything.
help_block = re.search(r'<div class="auth-help">(.*?)</div>', login_normal, re.S)
check("the help block renders", help_block is not None)
if help_block:
    check("the contact line is plain text, not a link",
          "<a " not in help_block.group(1) and "href" not in help_block.group(1))

# The card floats on the dark surface, so it needs a shadow's lift.
card_rule = re.search(r"\n\.auth-form-card \{(.*?)\n\}", css, re.S)
check("app.css still has the card rule", card_rule is not None)
if card_rule:
    check("the card floats with a shadow", "box-shadow:" in card_rule.group(1))
    check("the 1px border it needed on white is gone",
          "border: 1px solid" not in card_rule.group(1))
    check("the card is padded to read as a panel, not a box",
          re.search(r"padding:\s*52px 40px", card_rule.group(1)) is not None)

# --- ONE footer zone, one hairline ---------------------------------------
# Three of the four pages have a card foot as well - "Back to sign in", the
# 15-minute note, "Sign out instead". Two hairlines stacked read as clutter,
# so the foot takes it when it has content and the contact block falls in
# behind it. On login the foot is empty and .auth-help keeps the hairline.
foot_rule = re.search(r"\n\.auth-foot \{(.*?)\n\}", css, re.S)
check("app.css still has the foot rule", foot_rule is not None)
if foot_rule:
    check("the footer zone is centred, matching the contact block below it",
          "text-align: center" in foot_rule.group(1))
check("a foot with content takes the hairline",
      re.search(r"\.auth-foot:not\(:empty\) \{[^}]*border-top:\s*1px solid", css) is not None)
check("and the contact block then draws none of its own",
      re.search(r"\.auth-foot:not\(:empty\) \+ \.auth-help \{[^}]*border-top:\s*0", css)
      is not None)
check("login's empty foot leaves the hairline to the contact block",
      re.search(r"\n\.auth-help \{[^}]*border-top:\s*1px solid", css) is not None
      and re.search(r"\.auth-foot:empty \{[^}]*display:\s*none", css) is not None)

# Exactly one hairline is drawn in each state - asserted on the rendered
# markup, since which rule wins depends on whether the foot has content.
for (name, label), html in rendered.items():
    foot = re.search(r'<footer class="auth-foot">(.*?)</footer>', html, re.S)
    check(f"{name} [{label}]: renders a foot element", foot is not None)
    if foot and name == "login.html":
        check(f"{name} [{label}]: login's foot is literally empty, so :empty hides it",
              foot.group(1) == "")

# --- the two copy fixes ---------------------------------------------------
# The helper told people to fill in an email field that is not on the page
# once the link had been sent, directly above the notice saying it had gone.
forgot_sent = rendered[("forgot_password.html", "NO FORM - after sending")]
helper_sent = re.search(r"<h1>.*?</h1>\s*<p>(.*?)</p>", forgot_sent, re.S)
check("forgot_password renders a helper element", helper_sent is not None)
if helper_sent:
    check("the helper says nothing once the link is sent",
          helper_sent.group(1) == "")
    check("and it is LITERALLY empty, so :empty can collapse it",
          "<p></p>" in forgot_sent)
check("app.css collapses an empty helper", ".auth-head p:empty" in css)
check("the helper still instructs before sending",
      "Enter the email address" in rendered[("forgot_password.html", "normal")])

# The lede said "choose a password" beside a card announcing the link was
# dead, or that the password had already been changed.
for label, must, must_not in [
        ("NO FORM - dead link", "no longer usable", "Choose a password"),
        ("NO FORM - done", "sign in with your new password", "Choose a password")]:
    html = rendered.get(("reset_password.html", label))
    if html is None:
        continue
    lede = re.search(r'<p class="auth-brand-lede">(.*?)</p>', html, re.S)
    check(f"reset_password [{label}]: renders a lede", lede is not None)
    if lede:
        check(f"reset_password [{label}]: the lede matches the card's state",
              must in lede.group(1) and must_not not in lede.group(1))
lede_normal = re.search(r'<p class="auth-brand-lede">(.*?)</p>',
                        rendered[("reset_password.html", "normal")], re.S)
check("reset_password [normal]: the lede still asks for a new password",
      lede_normal is not None and "Choose a password" in lede_normal.group(1))

# --- the two no-form states still fill the card -------------------------
# Both of these render no form at all. The card wraps the heading, the form and
# the foot together rather than framing the form alone, so these states show a
# card with the message in it instead of a bare heading. Asserted
# by POSITION: the message has to fall between the card's opening tag and the
# help block that follows the card's close.
NO_FORM = [
    (("forgot_password.html", "NO FORM - after sending"),
     ["Forgot your password?", "if an account exists", "Back to sign in"]),
    (("reset_password.html", "NO FORM - dead link"),
     ["Link no longer valid", "has expired or has already been used",
      "Request a new link"]),
]
for key, needles in NO_FORM:
    html = rendered[key]
    where = f"{key[0]} [{key[1]}]"
    inner = card_inner(html)
    check(f"{where}: renders a card even with no form", inner is not None)
    check(f"{where}: renders no form at all", "<form" not in html)
    for needle in needles:
        check(f"{where}: {needle!r} sits inside the card, not loose on the page",
              inner is not None and needle in inner)

# --- no required-field asterisks anywhere on the auth pages -------------
# Every field on all four pages is required, so marking them all marked
# nothing. The MARKER goes; the required ATTRIBUTE stays.
for (name, label), html in rendered.items():
    where = f"{name} [{label}]"
    check(f"{where}: no auth-req marker survives", "auth-req" not in html)
    starred = [m.group(0) for m in re.finditer(r"<label\b[^>]*>(.*?)</label>",
                                               html, re.S) if "*" in m.group(1)]
    check(f"{where}: no label carries an asterisk", not starred)

for name in ["login.html", "forgot_password.html", "reset_password.html",
             "set_password.html"]:
    inputs = re.findall(r"<input\b[^>]*>", sources[name], re.S)
    check(f"{name}: still has fields to require", bool(inputs))
    check(f"{name}: every field kept its required attribute",
          all("required" in i for i in inputs))

check(".auth-req is gone from app.css", ".auth-req" not in css)

# report.html is deliberately NOT touched by this. Required-ness there really
# does change - student vs faculty - so the marker does real work and stays.
report_src = open(os.path.join(TEMPLATES, "report.html"), "rb").read().decode("utf-8")
check("app.css keeps the .rep-req rule report.html needs", ".rep-req" in css)
for field in ["reporter_role", "reporter_name", "reporter_student_number",
              "reporter_program", "reporter_section", "computer_hostname",
              "problem_type", "description"]:
    check(f"report.html keeps the .rep-req marker on {field}",
          re.search(r'<label for="' + field + r'"[^>]*>[^<]*'
                    r'<span class="rep-req">\*</span>', report_src) is not None)


# --- set_password's own machinery ---------------------------------------
# These three are the reason this page is not interchangeable with the others.
# Each has a silent failure mode: drop the attribute and the checklist simply
# stops appearing, drop the script and the same, drop the form and the page
# becomes a trap with no way out except closing the browser.
setpw = rendered[("set_password.html", "normal")]
check("set_password keeps the data-password-policy hook on the new password field",
      re.search(r'id="new_password"[^>]*data-password-policy', setpw) is not None
      or re.search(r'data-password-policy[^>]*id="new_password"', setpw) is not None)
check("set_password still loads password-policy.js",
      "js/password-policy.js" in setpw)
check("password-toggle.js is loaded BEFORE password-policy.js",
      setpw.find("js/password-toggle.js") < setpw.find("js/password-policy.js"))
check("set_password keeps a sign-out form posting to logout",
      re.search(r'<form[^>]*action="/logout"', setpw) is not None)
check("the sign-out control is a submit button, not a link",
      re.search(r'<button[^>]*type="submit"[^>]*class="auth-signout"', setpw) is not None
      or re.search(r'<button[^>]*class="auth-signout"[^>]*type="submit"', setpw) is not None)
check("set_password is on the dark stage like the rest of the family",
      'class="auth-stage font-sans"' in setpw and "bg-canvas" not in setpw)

setpw_src = sources["set_password.html"]
check("set_password references no v1 icon name",
      "appease-appicon.svg" not in setpw_src
      and "appease-appicon-32.png" not in setpw_src
      and "appease-appicon-180.png" not in setpw_src)

# --- login must not ask for an email ------------------------------------
# Accounts sign in by username and an email address is optional on every one
# of them, so an email field here would lock out every account without one.
check("login asks for a username, never an email",
      'id="username"' in login_html and 'id="email"' not in login_html)
check("forgot_password asks for an email",
      'id="email"' in rendered[("forgot_password.html", "normal")])

print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)
sys.exit(1 if failed else 0)

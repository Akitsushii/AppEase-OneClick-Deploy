"""Every element the app hides with Tailwind's .hidden must actually hide:
app.css is unlayered and loads after tailwind.css, so a plain class there
that sets `display` beats .hidden and the element stays on screen.

It finds hidden elements in templates, script class strings and traceable
classList toggles, and evaluates app.css's cascade for each with `hidden`
added. Its ceiling: untraceable toggles are listed, not checked; it reads
app.css only (layered rules cannot beat .hidden); rules under an ancestor,
state or screen @media count as able to apply; and it reads source, not a
browser.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

passed = 0
failed = []


def check(label, condition):
    global passed
    if condition:
        passed += 1
    else:
        failed.append(label)
        print(f"  FAIL: {label}")


def arg_path(name, default):
    for index, arg in enumerate(sys.argv):
        if arg == name and index + 1 < len(sys.argv):
            return os.path.abspath(sys.argv[index + 1])
        if arg.startswith(name + "="):
            return os.path.abspath(arg.split("=", 1)[1])
    return default


SERVER = arg_path("--server", os.path.join(ROOT, "server"))
if not os.path.isdir(SERVER):
    print("COULD NOT RUN - server/ not found.")
    sys.exit(2)


# ---- a small cascade, for the selectors app.css actually uses ---------
def parse_css(text):
    """(enclosing at-rules, selector prelude, declaration body, source
    order)."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    rules, stack, buf, order = [], [], "", 0
    for ch in text:
        if ch == "{":
            stack.append(buf.strip())
            buf = ""
        elif ch == "}":
            if stack:
                prelude = stack.pop()
                if not prelude.startswith("@"):
                    rules.append((tuple(stack), prelude, buf, order))
                    order += 1
            buf = ""
        else:
            buf += ch
    return rules


def display_declarations(body):
    out = []
    for part in body.split(";"):
        if ":" in part:
            prop, val = part.split(":", 1)
            if prop.strip().lower() == "display":
                out.append((val.replace("!important", "").strip().lower(), "!important" in val))
    return out


def split_selectors(prelude):
    parts, depth, cur = [], 0, ""
    for ch in prelude:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


SIMPLE = re.compile(r"(::?[\w-]+(?:\((?:[^()]|\([^()]*\))*\))?|\.(?:\\.|[\w-])+|#[\w-]+|\[[^\]]*\]|\*|[a-zA-Z][\w-]*)")


def rightmost_compound(selector):
    depth, cur, parts = 0, "", []
    for ch in selector:
        depth += ch in "(["
        depth -= ch in ")]"
        if depth == 0 and ch in " >+~":
            if cur.strip():
                parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return (parts[-1] if parts else ""), len(parts) > 1


def specificity(selector):
    a = b = c = 0
    for tok in SIMPLE.findall(selector):
        if tok.startswith("::"):
            c += 1
        elif tok.startswith(":where("):
            continue
        elif tok.startswith((":not(", ":is(")):
            inner = tok[tok.index("(") + 1:-1]
            sa, sb, sc = max((specificity(s) for s in split_selectors(inner)), default=(0, 0, 0))
            a, b, c = a + sa, b + sb, c + sc
        elif tok.startswith((":", ".", "[")):
            b += 1
        elif tok.startswith("#"):
            a += 1
        elif tok != "*":
            c += 1
    return (a, b, c)


def compound_matches(compound, el):
    """(matches, certain, named). named: the compound picks the element out
    by one of ITS OWN classes (not `hidden`) or its id; a rule like
    `.settings-rail > [role=tab]` names nothing about it."""
    certain, named = True, False
    for tok in SIMPLE.findall(compound):
        if tok.startswith("::"):
            return False, False, False          # styles a pseudo-element, not the box
        if tok.startswith(":not("):
            inner = tok[5:-1].strip()
            if re.fullmatch(r"\.[\w-]+", inner):
                if inner[1:] in el["classes"]:
                    return False, False, False
                continue
            certain = False
        elif tok.startswith(":"):
            certain = False
        elif tok.startswith("."):
            name = tok[1:].replace("\\", "")
            if name not in el["classes"]:
                return False, False, False
            named = named or name != "hidden"
        elif tok.startswith("#"):
            if el.get("id") != tok[1:]:
                return False, False, False
            named = True
        elif tok.startswith("["):
            certain = False
        elif tok != "*":
            if el.get("tag") and el["tag"].lower() != tok.lower():
                return False, False, False
    return True, certain, named


def hidden_loses(rules, el):
    """None if .hidden wins on this element, else (selector, display value,
    conditional)."""
    found = []
    for ctx, prelude, body, order in rules:
        if any(c.startswith(("@keyframes", "@media print")) for c in ctx):
            continue
        decls = display_declarations(body)
        if not decls:
            continue
        for sel in split_selectors(prelude):
            compound, has_ancestor = rightmost_compound(sel)
            ok, certain, named = compound_matches(compound, el)
            if ok and named:
                for value, important in decls:
                    rank = (important, specificity(sel), order)
                    found.append((rank, value, sel, ctx, certain and not has_ancestor and not ctx))
    if not found:
        return None                               # nothing in app.css competes with .hidden
    certain = [f for f in found if f[4]]
    if certain:
        best = max(certain, key=lambda f: f[0])
        if best[1] != "none":
            return best[2], best[1], False
    top = max(found, key=lambda f: f[0])
    if not top[4] and top[1] != "none":
        return " ".join(top[3] + (top[2],)), top[1], True
    return None


print("\n=== 1. The cascade model, on cases with a known answer ===")


def verdict(css, classes, el_id=None, tag="span"):
    return hidden_loses(parse_css(css), {"classes": set(classes) | {"hidden"}, "id": el_id, "tag": tag})


check("a class that sets display beats .hidden",
      verdict(".x{display:inline-flex}", ["x"]) is not None)
check("...and the .x.hidden override wins it back",
      verdict(".x{display:inline-flex}\n.x.hidden{display:none}", ["x"]) is None)
check(".x:not(.hidden) never applies to a hidden element",
      verdict(".x:not(.hidden){display:flex}", ["x"]) is None)
check("a later rule of equal specificity beats an earlier override",
      verdict(".x.hidden{display:none}\n.x.y{display:flex}", ["x", "y"]) is not None)
check("a more specific override beats a later, weaker rule",
      verdict(".x.y.hidden{display:none}\n.x.y{display:flex}", ["x", "y"]) is None)
check("!important wins over specificity",
      verdict(".x.hidden{display:none}\n.x{display:flex!important}", ["x"]) is not None)
check("an id rule counts", verdict("#box{display:block}", ["x"], el_id="box") is not None)
check("another element's id does not", verdict("#other{display:block}", ["x"], el_id="box") is None)
check("a screen @media rule can beat .hidden, and is reported as conditional",
      verdict("@media (min-width:1024px){.x{display:grid}}", ["x"]) == (
          "@media (min-width:1024px) .x", "grid", True))
check("@media print is not the screen cascade",
      verdict("@media print{.x{display:block}}", ["x"]) is None)
check("a pseudo-element's display is not the element's",
      verdict(".x::before{display:block}", ["x"]) is None)
check("a rule that names nothing about the element is ignored",
      verdict(".rail > [role=tab]{display:flex}", ["x"]) is None)
check("a rule for a class the element does not have is ignored",
      verdict(".y{display:flex}", ["x"]) is None)
check("display:none set by the class itself is not a problem",
      verdict(".x{display:none}", ["x"]) is None)


print("\n=== 2. Finding the elements the app hides ===")
TEMPLATES = os.path.join(SERVER, "templates")
JS_DIR = os.path.join(SERVER, "static", "js")
JINJA_WORDS = {"if", "else", "elif", "not", "and", "or", "is", "none", "true", "false", "in", "endif", "loop"}
CLASS_TOKEN = re.compile(r"^[A-Za-z][\w:/@.\[\]-]*$")
TAG = re.compile(r"<([a-zA-Z][\w-]*)((?:[^<>\"']|\"[^\"]*\"|'[^']*')*)>", re.S)
TOGGLE = re.compile(r"([\w$.\[\]'\"()-]+?)\s*\.classList\.(?:toggle|add|remove)\(\s*['\"]hidden['\"]")

elements = {}


def add_element(source, tag, classes, el_id=None):
    classes = {c for c in classes if CLASS_TOKEN.match(c) and c not in JINJA_WORDS}
    elements.setdefault((source, tag, tuple(sorted(classes)), el_id),
                        {"classes": classes | {"hidden"}, "id": el_id, "tag": tag, "source": source})


id_index, scripts = {}, []
for name in sorted(os.listdir(TEMPLATES)):
    if not name.endswith(".html"):
        continue
    src = open(os.path.join(TEMPLATES, name), encoding="utf-8").read()
    src = re.sub(r"\{#.*?#\}", "", src, flags=re.S)
    scripts += [(name, m.group(1)) for m in re.finditer(r"<script\b[^>]*>(.*?)</script>", src, flags=re.S)]
    markup = re.sub(r"<script\b[^>]*>.*?</script>", "", src, flags=re.S)
    for m in TAG.finditer(markup):
        tag, attrs = m.group(1), m.group(2)
        cm = re.search(r'\bclass\s*=\s*"([^"]*)"', attrs)
        im = re.search(r'\bid\s*=\s*"([^"{}]*)"', attrs)
        words = re.findall(r"[A-Za-z][\w:/@.\[\]-]*", cm.group(1)) if cm else []
        if im:
            id_index[im.group(1)] = (name, tag, set(words))
        if "hidden" in words:
            add_element(f'{name} <{tag} class="{cm.group(1).strip()[:48]}">', tag, words,
                        im.group(1) if im else None)

for name in sorted(os.listdir(JS_DIR)):
    if name.endswith(".js"):
        scripts.append((name, open(os.path.join(JS_DIR, name), encoding="utf-8").read()))

unresolved = []
for source, js in scripts:
    code = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    code = re.sub(r"(?m)^\s*//.*$", "", code)
    var_classes, var_ids, alias = {}, {}, {}
    for m in re.finditer(r"(?:var\s+)?([\w$]+)\s*=\s*el\(\s*'(\w+)'\s*,\s*'([^']*)'", code):
        var_classes[m.group(1)] = (m.group(2), m.group(3).split())
        if "hidden" in m.group(3).split():
            add_element(f"{source} el('{m.group(2)}', '{m.group(3)}')", m.group(2), m.group(3).split())
    for m in re.finditer(r"(?:var\s+)?([\w$]+)\s*=\s*document\.getElementById\(\s*['\"]([\w-]+)['\"]\s*\)", code):
        var_ids[m.group(1)] = m.group(2)
    for m in re.finditer(r"([\w$]+)\.className\s*=\s*'([^']*)'", code):
        if "hidden" in m.group(2).split():
            add_element(f"{source} {m.group(1)}.className = '{m.group(2)}'", None, m.group(2).split())
        var_classes.setdefault(m.group(1), (None, m.group(2).split()))
    for m in re.finditer(r"[\w$]+\.([\w$]+)\s*=\s*([\w$]+)\s*;", code):
        alias[m.group(1)] = m.group(2)
    # forEach.call(list.children, function (node) { node.classList... }):
    # the loop variable is whatever was appended to THAT container. Names are
    # reused - data-table.js declares `var list` in seven different menus - so
    # the container is scoped to the stretch between its own declaration and
    # the next one of the same name, and each appended variable resolves to
    # its nearest declaration before the append. Without this the Sort
    # panel's rows were charged to the filter menu's search.
    el_decls = [(m.start(), m.group(1), m.group(2), m.group(3).split())
                for m in re.finditer(r"var\s+([\w$]+)\s*=\s*el\(\s*'(\w+)'\s*,\s*'([^']*)'", code)]
    var_decl_pos = {}
    for m in re.finditer(r"var\s+([\w$]+)\s*=", code):
        var_decl_pos.setdefault(m.group(1), []).append(m.start())
    appends = [(m.start(), m.group(1), m.group(2))
               for m in re.finditer(r"([\w$]+)\.appendChild\(\s*([\w$]+)\s*\)", code)]
    loops = [(m.start(), m.group(1), m.group(2))
             for m in re.finditer(r"forEach\.call\(\s*([\w$]+)\.children\s*,\s*function\s*\(\s*([\w$]+)\s*\)", code)]

    def nearest_el(var, pos):
        found = [d for d in el_decls if d[1] == var and d[0] <= pos]
        return found[-1] if found else None

    def loop_children(param, pos):
        """(container, [(tag, classes)]) for the loop whose variable is `param`
        and which most recently started before `pos`."""
        mine = [lp for lp in loops if lp[2] == param and lp[0] <= pos]
        if not mine:
            return None, []
        lpos, container, _ = mine[-1]
        starts = [p for p in var_decl_pos.get(container, []) if p <= lpos]
        ends = [p for p in var_decl_pos.get(container, []) if p > lpos]
        lo, hi = (starts[-1] if starts else 0), (ends[0] if ends else len(code))
        kids = []
        for apos, parent, child in appends:
            if parent == container and lo <= apos < hi:
                decl = nearest_el(child, apos)
                if decl:
                    kids.append((decl[2], decl[3]))
        return container, kids
    for m in re.finditer(r"([\w$]+)\s*:\s*([\w$]+)\s*[,}\n]", code):
        alias.setdefault(m.group(1), m.group(2))
    for m in TOGGLE.finditer(code):
        expr = m.group(1)
        direct = re.fullmatch(r"document\.getElementById\(\s*['\"]([\w-]+)['\"]\s*\)", expr)
        el_id = direct.group(1) if direct else None
        if not direct:
            name = re.split(r"[.\]]", expr.rstrip(")"))[-1]
            base = alias.get(name, name)
            if base in var_classes:
                tag, cls = var_classes[base]
                add_element(f"{source} toggles {expr} (el '{' '.join(cls)}')", tag, cls)
                continue
            container, kids = loop_children(base, m.start())
            for tag, cls in kids:
                add_element(f"{source} toggles {expr} in {container}.children (el '{' '.join(cls)}')", tag, cls)
            if kids:
                continue
            el_id = var_ids.get(base) or var_ids.get(name)
        if el_id and el_id in id_index:
            tname, tag, cls = id_index[el_id]
            add_element(f"{source} toggles #{el_id} ({tname})", tag, cls, el_id)
        else:
            unresolved.append(f"{source}: {expr}")

reached = lambda pred: any(pred(el) for el in elements.values())
check(f"found a realistic number of hidden elements ({len(elements)})", len(elements) >= 100)
check("reaches the Sort count badge through its property alias (sortButton.badgeNode)",
      reached(lambda el: "dt-badge" in el["classes"] and "badgeNode" in el["source"]))
check("reaches the filter buttons' chip slot created as 'dt-chips hidden'",
      reached(lambda el: "dt-chips" in el["classes"]))
check("reaches a menu's items through forEach.call(list.children, ...) (dt-menu-item)",
      reached(lambda el: "dt-menu-item" in el["classes"] and ".children" in el["source"]))
check("does not charge the Sort panel's rows to a search loop over a different `list`",
      not reached(lambda el: "dt-sort-row" in el["classes"]))
check("reaches Software Library's tier pill by id (#filled-tier)",
      reached(lambda el: el["id"] == "filled-tier" and "dt-pill" in el["classes"]))
check("reaches an element hidden by getElementById in a .js file (#deploy-progress-overlay)",
      reached(lambda el: el["id"] == "deploy-progress-overlay"))
check("reaches a Jinja-conditional hidden class (settings.html profile-error)",
      reached(lambda el: "profile-error" in el["classes"] and "{%" in el["source"]))

tailwind = open(os.path.join(SERVER, "static", "css", "tailwind.css"), encoding="utf-8").read()
app_css_text = open(os.path.join(SERVER, "static", "css", "app.css"), encoding="utf-8").read()
check("premise: Tailwind's .hidden is display:none", ".hidden{display:none}" in tailwind.replace(" ", ""))
check("premise: app.css is unlayered, which is what makes this trap possible",
      not re.search(r"@layer\b", re.sub(r"/\*.*?\*/", "", app_css_text, flags=re.S)))


print("\n=== 3. Nothing in app.css defeats .hidden ===")
css_rules = parse_css(app_css_text)
for el in sorted(elements.values(), key=lambda e: e["source"]):
    loser = hidden_loses(css_rules, el)
    label = el["source"]
    if loser:
        sel, value, conditional = loser
        label += f" - beaten by {sel} {{ display: {value} }}" + (" (conditionally)" if conditional else "")
    check(f"hides: {label}", loser is None)

print(f"\n  {len(unresolved)} hidden toggle(s) act on elements this cannot trace, so were not checked:")
for u in unresolved:
    print(f"    {u}")

print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)
sys.exit(1 if failed else 0)

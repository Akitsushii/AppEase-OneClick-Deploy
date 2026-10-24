"""Full-screen overlays are never shrunk by a margin: a space-y-* parent
gives every child but the last a bottom margin, leaving an undimmed strip
under a fixed inset-0 box. Each is also a .deploy-backdrop, so all dim and
blur alike. This guards the cause; coverage itself is a browser's to see.
"""
import html.parser
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TEMPLATES = os.path.join(ROOT, "server", "templates")

passed = 0
failed = []


def check(label, condition):
    global passed
    if condition:
        passed += 1
    else:
        failed.append(label)
        print(f"  FAIL: {label}")


VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr",
        "path", "circle", "rect", "line", "polyline", "polygon", "ellipse"}


class Overlays(html.parser.HTMLParser):
    """(line, id, own classes, parent classes) for every fixed inset-0
    element."""

    def __init__(self):
        super().__init__()
        self.stack, self.found = [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if "fixed" in classes and "inset-0" in classes:
            parent = self.stack[-1][1] if self.stack else []
            self.found.append((self.getpos()[0], attrs.get("id") or tag, classes, parent))
        if tag not in VOID:
            self.stack.append((tag, classes))

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break


def strip_jinja(text):
    # Comments go whole; tags and expressions are blanked in place, so line
    # numbers stay true and nothing inside them is read as markup.
    text = re.sub(r"\{#.*?#\}", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    text = re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    text = re.sub(r"<script\b.*?</script>", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S | re.I)
    return re.sub(r"\{%.*?%\}|\{\{.*?\}\}", " ", text, flags=re.S)


MARGIN = re.compile(r"^-?(?:m|mx|my|mt|mb|ml|mr|ms|me)-")
overlays = []
for name in sorted(os.listdir(TEMPLATES)):
    if not name.endswith(".html"):
        continue
    parser = Overlays()
    parser.feed(strip_jinja(open(os.path.join(TEMPLATES, name), encoding="utf-8").read()))
    overlays += [(name,) + item for item in parser.found]

print(f"\n=== {len(overlays)} full-screen overlays ===")
check("the scan finds the app's overlays", len(overlays) >= 8)
check("including the two that stopped short",
      {"loading-overlay", "duplicate-dialog-overlay"} <= {o[2] for o in overlays})
for name, line, ident, classes, parent in overlays:
    stack = [c for c in parent if c.startswith("space-y-")]
    check(f"{name}:{line} #{ident} is not a direct child of a spacing stack ({stack})", not stack)
    margins = [c for c in classes if MARGIN.match(c)]
    check(f"{name}:{line} #{ident} carries no margin utility ({margins})", not margins)
    check(f"{name}:{line} #{ident} is a .deploy-backdrop, dimmed and blurred like the rest",
          "deploy-backdrop" in classes)


print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)
sys.exit(1 if failed else 0)

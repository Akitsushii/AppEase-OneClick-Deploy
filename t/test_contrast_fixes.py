"""Two contrast bugs: error lines must swap text-muted for text-danger (the
compiled .text-muted outranks it), and black category icons are read from
the image files themselves. Source and pixels only, not what Chrome paints:
that needs a click-test in both themes.
"""
import os
import re
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "server")
failed = []
passed = []


def check(label, condition):
    if condition:
        passed.append(label)
        print(f"  ok  {label}")
    else:
        failed.append(label)
        print(f"  FAIL: {label}")


def read(*parts):
    return open(os.path.join(SERVER, *parts), encoding="utf-8").read()


def js_function(source, name):
    """The text of `function name(...) { ... }`, found by brace matching."""
    start = source.find("function " + name + "(")
    if start == -1:
        return ""
    depth = 0
    for i in range(source.index("{", start), len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
    return ""


page = read("templates", "software_library.html")
tailwind = read("static", "css", "tailwind.css")
app_css = read("static", "css", "app.css")

# ---------------------------------------------------------------------------
print("1. The edit drawer's error lines are red")
# ---------------------------------------------------------------------------
danger_at = tailwind.find(".text-danger{")
muted_at = tailwind.find(".text-muted{")
check("the premise: both are plain one-class utilities, and grey is compiled later, so grey wins a tie",
      danger_at != -1 and muted_at != -1 and muted_at > danger_at)

drawer = page[page.index('<dialog id="sw-edit-drawer"'):]
drawer = drawer[:drawer.index("</dialog>")]
error_lines = re.findall(r'<p class="([^"]*)" id="(sw-edit-[\w-]*-error)"', drawer)
check(f"the drawer's error lines were found ({[i for _, i in error_lines]})",
      {"sw-edit-name-error", "sw-edit-version-error", "sw-edit-new-category-error"}
      <= {i for _, i in error_lines})
check("each starts in the grey hint colour - which is why stacking lost",
      error_lines and all("text-muted" in c.split() for c, _ in error_lines))

show = js_function(page, "show")
check("an error line gains red...", "classList.toggle('text-danger', !!message)" in show)
check("...and loses grey at the same moment, so the two are never stacked",
      "classList.toggle('text-muted', !message)" in show)

# Any other place that paints an error red by ADDING the class has the same
# trap if the element can also be grey. Every such line, anywhere, has to
# take text-muted off in the same function.
sources = {}
for folder, ext in (("templates", ".html"), (os.path.join("static", "js"), ".js")):
    for name in sorted(os.listdir(os.path.join(SERVER, folder))):
        if name.endswith(ext):
            sources[name] = read(folder, name)
adders = []
for name, text in sources.items():
    for m in re.finditer(r"classList\.(?:add|toggle)\('text-danger'", text):
        body = text[text.rfind("function", 0, m.start()):]
        body = body[:body.find("\n        }\n") + 1 if "\n        }\n" in body else len(body)]
        adders.append((name, "'text-muted'" in body))
check(f"every script that adds red also takes grey off ({adders})",
      adders and all(ok for _, ok in adders))

# ---------------------------------------------------------------------------
print("2. Black category icons are readable in dark mode")
# ---------------------------------------------------------------------------


def png_pixels(path):
    """RGBA bytes of an 8-bit, non-interlaced RGBA PNG - stdlib only."""
    data = open(path, "rb").read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path
    pos, idat, width, height = 8, b"", 0, 0
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", body)
            assert (depth, colour, interlace) == (8, 6, 0), f"{path}: not 8-bit RGBA"
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
    raw = zlib.decompress(idat)
    stride = width * 4
    prev = bytearray(stride)
    out = bytearray()
    i = 0
    for _ in range(height):
        kind, line = raw[i], bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        if kind == 1:
            for x in range(4, stride):
                line[x] = (line[x] + line[x - 4]) & 255
        elif kind == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 255
        elif kind == 3:
            for x in range(stride):
                left = line[x - 4] if x >= 4 else 0
                line[x] = (line[x] + ((left + prev[x]) >> 1)) & 255
        elif kind == 4:
            for x in range(stride):
                a = line[x - 4] if x >= 4 else 0
                b = prev[x]
                c = prev[x - 4] if x >= 4 else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        out += line
        prev = line
    return out


def is_black_glyph(path):
    """Every visible pixel near-black and colourless: it disappears on a
    dark ground."""
    px = png_pixels(path)
    seen = dark = 0
    for i in range(0, len(px), 4 * 7):
        r, g, b, a = px[i:i + 4]
        if a > 32:
            seen += 1
            dark += max(r, g, b) < 40
    return seen > 0 and dark / seen > 0.95


# The category icons are the files the deploy panel maps categories to,
# plus the generic one an admin's category falls back to.
panel = sources["deploy-panel.js"]
icon_files = sorted(set(re.findall(r"'(\w+\.png)'", panel)))
check(f"the category icons were found ({len(icon_files)})", len(icon_files) >= 10 and "other.png" in icon_files)
black = [f for f in icon_files if is_black_glyph(os.path.join(SERVER, "static", "img", f))]
coloured = [f for f in icon_files if f not in black]
print(f"     black glyphs: {black}")

# Comments out first, or one above a rule reads as part of its selector.
rules = re.findall(r"([^{}]+)\{([^}]*)\}", re.sub(r"/\*.*?\*/", "", app_css, flags=re.S))


def rules_for(name):
    return [(sel.strip(), body) for sel, body in rules if f'/img/{name}"]' in sel]


DARK_INK = re.search(r"html\.dark\s*\{[^}]*--color-ink:\s*(#[0-9a-fA-F]{6})", app_css)
DARK_CANVAS = re.search(r"html\.dark\s*\{[^}]*--color-canvas:\s*(#[0-9a-fA-F]{6})", app_css)


def luminance(value):
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * channel(value) + 0.7152 * channel(value) + 0.0722 * channel(value)


for name in black:
    found = rules_for(name)
    filters = [re.search(r"filter:\s*invert\(1\)\s*brightness\(([\d.]+)\)", body) for sel, body in found]
    check(f"{name}: lightened in dark mode", found and all(f for f in filters))
    check(f"{name}: and only in dark mode - light mode keeps the original",
          found and all(part.strip().startswith("html.dark ")
                        for sel, _ in found for part in sel.split(",") if f'/img/{name}"]' in part))
    if found and all(filters) and DARK_CANVAS:
        grey = round(255 * float(filters[0].group(1)))
        ground = int(DARK_CANVAS.group(1)[1:3], 16)
        ratio = (luminance(grey) + 0.05) / (luminance(ground) + 0.05)
        check(f"{name}: comes out #{grey:02x}{grey:02x}{grey:02x}, {ratio:.1f}:1 on the dark list (3:1 needed)",
              ratio >= 3)
        if DARK_INK:
            check(f"{name}: the same grey as dark mode's text ({DARK_INK.group(1)})",
                  f"#{grey:02x}{grey:02x}{grey:02x}" == DARK_INK.group(1).lower())

for name in coloured:
    check(f"{name}: a coloured icon, left exactly as it is", not rules_for(name))

# The generic icon is the one every admin-added category shows, so it is
# named here as well as found: if it ever stopped reading as black, this
# suite would otherwise stop checking it without a word.
check("the generic icon admin categories use is one of the black ones", "other.png" in black)

print()
print(f"{len(passed)} passed, {len(failed)} failed")
for label in failed:
    print(f"  - {label}")
sys.exit(1 if failed else 0)

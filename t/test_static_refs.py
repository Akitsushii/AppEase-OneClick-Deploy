"""Every static file the app refers to exists under exactly that name,
compared character for character against os.walk() - never by opening it,
which Windows allows in the wrong case and Linux does not. Paths known only
at runtime (uploads, avatars, report photos) are not checked.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SERVER = os.path.join(ROOT, "server")
STATIC = os.path.join(SERVER, "static")

EXT = r"(?:png|jpe?g|svg|gif|webp|ico|css|js|woff2?|ttf|otf|eot|map)"
# Written or created at runtime, per install - not files the app ships.
RUNTIME_DIRS = ("avatars/", "report_photos/", "uploads/")

passed = 0
failed = []


def check(label, condition):
    global passed
    if condition:
        passed += 1
    else:
        failed.append(label)
        print(f"  FAIL: {label}")


def real_files():
    """Every file under static/, as a forward-slash path, exact case."""
    found = set()
    for dirpath, dirnames, filenames in os.walk(STATIC):
        rel_dir = os.path.relpath(dirpath, STATIC).replace(os.sep, "/")
        if rel_dir.split("/")[0] in ("avatars", "report_photos"):
            continue
        for name in filenames:
            found.add(name if rel_dir == "." else f"{rel_dir}/{name}")
    return found


def sources():
    """(repo-relative path, text) for every file that can refer to static."""
    out = []
    for dirpath, dirnames, filenames in os.walk(SERVER):
        # venv: a virtual environment inside server/ is Python packages, not
        # AppEase - Werkzeug's debugger files there refer to their own assets.
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "uploads", "staging",
                                                         "avatars", "report_photos",
                                                         "venv", ".venv", "env")]
        for name in filenames:
            if name.endswith((".html", ".js", ".css", ".py")):
                path = os.path.join(dirpath, name)
                out.append((os.path.relpath(path, ROOT).replace(os.sep, "/"),
                            open(path, encoding="utf-8", errors="replace").read()))
    return out


def references():
    """(source file, line, referenced static path, kind)."""
    refs = []
    for rel, text in sources():
        is_vendor = "/vendor/" in rel
        lines = text.split("\n")
        for number, line in enumerate(lines, 1):
            for m in re.finditer(r"url_for\(\s*['\"]static['\"]\s*,\s*filename\s*=\s*['\"]([^'\"]+)['\"]", line):
                refs.append((rel, number, m.group(1), "url_for"))
            for m in re.finditer(r"/static/([A-Za-z0-9_./-]+\." + EXT + r")\b", line):
                refs.append((rel, number, m.group(1), "/static/ path"))
            if rel.endswith(".py"):
                for m in re.finditer(r"['\"]((?:img|css|js|fonts)/[A-Za-z0-9_./-]+\." + EXT + r")['\"]", line):
                    refs.append((rel, number, m.group(1), "python path"))
            if rel.endswith((".js", ".html")) and not is_vendor:
                stripped = line.strip()
                if stripped.startswith(("//", "*", "/*", "{#")):
                    continue
                for m in re.finditer(r"['\"]([A-Za-z0-9_.-]+\.(?:png|jpe?g|svg|gif|webp))['\"]", line):
                    refs.append((rel, number, m.group(1), "bare file name"))
            if rel.endswith(".css"):
                for m in re.finditer(r"url\(\s*['\"]?([^'\")]+?)['\"]?\s*\)", line):
                    target = m.group(1).strip()
                    if target.startswith(("data:", "http:", "https:", "#", "/")):
                        continue
                    base = os.path.dirname(rel[len("server/static/"):]) if rel.startswith("server/static/") else ""
                    resolved = os.path.normpath(os.path.join(base, target.split("?")[0].split("#")[0])).replace(os.sep, "/")
                    refs.append((rel, number, resolved, "css url()"))
    return [r for r in refs if not r[2].startswith(RUNTIME_DIRS) and not r[2].endswith("/")]


files = real_files()
by_lower = {}
for f in files:
    by_lower.setdefault(f.lower(), []).append(f)
basenames = {}
for f in files:
    basenames.setdefault(f.rsplit("/", 1)[-1], []).append(f)
base_lower = {}
for f in files:
    base_lower.setdefault(f.rsplit("/", 1)[-1].lower(), []).append(f)


def verdict(ref):
    """'ok', ('case', real name) or ('missing', None)."""
    path, kind = ref[2], ref[3]
    if kind == "bare file name":
        if path in basenames:
            return "ok"
        near = base_lower.get(path.lower())
        return ("case", near[0]) if near else ("missing", None)
    if path in files:
        return "ok"
    near = by_lower.get(path.lower())
    return ("case", near[0]) if near else ("missing", None)


refs = references()
problems = []
for ref in refs:
    v = verdict(ref)
    if v != "ok":
        problems.append((ref, v))

if __name__ == "__main__" and "--report" in sys.argv:
    print(f"{len(files)} static files, {len(refs)} references")
    for ref in refs:
        v = verdict(ref)
        tag = "ok     " if v == "ok" else f"{v[0].upper():7}"
        extra = "" if v == "ok" else f"  (on disk: {v[1]})"
        print(f"  {tag} {ref[3]:15} {ref[2]:45} {ref[0]}:{ref[1]}{extra}")
    sys.exit(0)

print(f"\n=== {len(refs)} references to {len(files)} static files ===")
check("the scan found the app's static references", len(refs) >= 30)
for kind in ("url_for", "bare file name", "python path", "css url()"):
    check(f"it found references of kind {kind!r}", any(r[3] == kind for r in refs))
check("the category icons are among them", any(r[2] == "browser.png" for r in refs))
for (rel, number, path, kind), v in problems:
    check(f"{rel}:{number} refers to {path!r} ({kind}), but "
          + (f"the file is named {v[1]!r}" if v[0] == "case" else "no such file exists"), False)
check("every static reference names a real file, exactly, case included", not problems)

print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)
sys.exit(1 if failed else 0)

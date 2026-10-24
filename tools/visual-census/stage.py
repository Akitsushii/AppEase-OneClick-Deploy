"""Stage server/ with a fresh database and serve it, for census.mjs.

    python tools/visual-census/stage.py <work-dir> <port> [<project-root>]

Copies server/ from <project-root> (default: this checkout) into <work-dir>,
EXCLUDING the real database, .env, uploads and the other runtime folders, so
the census never opens or changes anything real. Seeds one admin and one
software row - the row is what the duplicate dialog and banner match against.
"""
import os
import secrets
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.dirname(os.path.dirname(HERE))

USERNAME = "census.admin"
PASSWORD = "Census!2026"

work_dir, port = sys.argv[1], int(sys.argv[2])
root = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_ROOT
dest = os.path.join(work_dir, "server")
shutil.rmtree(dest, ignore_errors=True)
shutil.copytree(
    os.path.join(root, "server"), dest,
    ignore=shutil.ignore_patterns("appease.db*", "__pycache__", "*.pyc", ".env",
                                  "uploads", "staging", "report_photos", "avatars"),
)
if os.path.exists(os.path.join(dest, ".env")) or os.path.exists(os.path.join(dest, "appease.db")):
    sys.exit("refusing to run: the staged copy carries a real .env or database")

# No mail, and a throwaway secret: app.py refuses to start without one.
for name in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "PUBLIC_REPORT_HOST"):
    os.environ.pop(name, None)
os.environ["SECRET_KEY"] = secrets.token_hex(32)
os.chdir(dest)
sys.path.insert(0, dest)

import app as appmod  # noqa: E402
import database  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

database.init_db()
conn = database.get_db()
conn.execute(
    "INSERT INTO admins (username, password_hash, email, role, first_name, last_name, "
    "must_change_password, created_at) VALUES (?, ?, 'census@example.com', 'admin', "
    "'Census', 'Admin', 0, CURRENT_TIMESTAMP)",
    (USERNAME, generate_password_hash(PASSWORD)),
)
conn.execute(
    "INSERT INTO software (name, version, type, install_command, category) "
    "VALUES ('VLC media player', '3.0.20', 'online', 'choco install vlc -y', 'Multimedia')"
)
conn.commit()
conn.close()
print(f"staged {root} at {dest}, serving on 127.0.0.1:{port}", flush=True)
appmod.app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)

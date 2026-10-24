"""The session signing key, checked at STARTUP in a fresh subprocess: a
second in-process import would reuse the cached module. The last section
shows a known key is enough to forge an admin session.
"""
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import textwrap

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


work = tempfile.mkdtemp(prefix="appease_secret_")
server_dir = os.path.join(work, "server")
# NEVER copy the real appease.db: the suite would test whatever data is in
# production rather than the code. It builds its own from an empty schema.
# uploads/staging/photos are skipped too - nothing here serves files, and a
# folder of installers would cost tens of megabytes a run.
shutil.copytree(
    os.path.join(ROOT, "server"), server_dir,
    ignore=shutil.ignore_patterns(
        "appease.db*", "__pycache__", "*.pyc", ".env",
        "uploads", "staging", "report_photos", "avatars",
    ),
)


def start_app(secret_value, use_env_file=False):
    """Import app.py in a FRESH process. Returns (ok, output); ok means the
    app would have started."""
    env = dict(os.environ)
    env.pop("SECRET_KEY", None)
    env["APPEASE_TEST_DB"] = os.path.join(work, "probe.db")

    env_path = os.path.join(server_dir, ".env")
    if os.path.exists(env_path):
        os.remove(env_path)

    if secret_value is not None:
        if use_env_file:
            with open(env_path, "w", encoding="utf-8") as handle:
                handle.write(f"SECRET_KEY={secret_value}\n")
        else:
            env["SECRET_KEY"] = secret_value

    script = textwrap.dedent(
        """
        import sys
        sys.argv = ["app.py"]
        import app
        print("STARTED", len(app.app.secret_key))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=server_dir, env=env, capture_output=True, text=True, timeout=120,
    )
    output = (result.stdout or "") + (result.stderr or "")
    return "STARTED" in output, output


GOOD_KEY = secrets.token_hex(32)


print("\n=== 1. The old key is gone from the source ===")
source = open(os.path.join(server_dir, "app.py"), "rb").read().decode("utf-8")
check("no hardcoded assignment remains",
      not re.search(r'app\.secret_key\s*=\s*["\']', source))
check("the key is read from the environment", 'os.getenv(SECRET_KEY_ENV)' in source)
check("load_dotenv runs BEFORE the key is read",
      source.index("load_dotenv()") < source.index("_load_secret_key"))
# The old literal survives only inside the rejection set. That is the one
# place it is allowed to appear, so the check is that it is not being
# ASSIGNED anywhere.
check("old literal appears only as a rejected value",
      "COMPROMISED_SECRET_KEYS" in source
      and source.count("appease-bulsu-meneses-2026") == 1)


print("\n=== 2. Startup refuses a bad key ===")
ok, output = start_app(None)
check("refuses to start with no key at all", not ok)
check("the error names the variable", "SECRET_KEY" in output)
check("the error says how to generate one", "token_hex" in output)

ok, output = start_app("")
check("refuses an empty key", not ok)
ok, output = start_app("   ")
check("refuses a whitespace-only key", not ok)

ok, output = start_app("appease-bulsu-meneses-2026")
check("REFUSES the old compromised value", not ok)
# The old key is only 26 characters, so the length check alone would also
# reject it - which means "not ok" above proves less than it looks. The
# real assertion is that it was refused for the RIGHT reason, by name.
check("refused specifically as the compromised value, not merely as too short",
      "hardcoded" in output.lower() or "compromised" in output.lower())
check("explains that the old value is public", "git history" in output.lower()
      or "public" in output.lower())
check("the compromised message is NOT the length message",
      "brute" not in output.lower())

ok, output = start_app("short")
check("refuses a key that is too short", not ok)
check("the error explains why length matters", "brute" in output.lower()
      or "characters" in output.lower())

ok, output = start_app("x" * 31)
check("refuses 31 characters (one under the floor)", not ok)


print("\n=== 3. Startup accepts a good key ===")
ok, output = start_app(GOOD_KEY)
check("starts with a proper key from the environment", ok)
check("the whole key is used", f"STARTED {len(GOOD_KEY)}" in output)

ok, output = start_app("x" * 32)
check("accepts exactly 32 characters (the floor itself)", ok)

ok, output = start_app(GOOD_KEY, use_env_file=True)
check("reads the key from a .env file, not just the shell", ok)

ok, output = start_app("  " + GOOD_KEY + "  ")
check("surrounding whitespace is trimmed, not treated as part of the key", ok)
check("trimmed key is the right length", f"STARTED {len(GOOD_KEY)}" in output)


print("\n=== 4. Why it mattered: forging a session ===")
# With the key known, a signed cookie can be minted from nothing. This is
# what an attacker could do with the published value, and it is why
# rotating the value - not merely relocating it - is the actual fix.
os.environ.setdefault("SECRET_KEY", GOOD_KEY)
sys.path.insert(0, server_dir)
os.chdir(server_dir)
import database  # noqa: E402
database.DB_PATH = os.path.join(server_dir, "appease.db")
database.init_db()
import app as appmod  # noqa: E402
appmod.app.config["TESTING"] = True

from flask.sessions import SecureCookieSessionInterface  # noqa: E402


def forge(secret, payload):
    """Mint a signed session cookie from a key, exactly as an attacker
    holding a published key could."""
    interface = SecureCookieSessionInterface()

    class Fake:
        secret_key = secret
        config = {
            "SESSION_COOKIE_NAME": "session",
            "SECRET_KEY_FALLBACKS": None,
            "SESSION_COOKIE_SALT": "cookie-session",
        }

    serializer = interface.get_signing_serializer(Fake())
    return serializer.dumps(dict(payload))


client = appmod.app.test_client()
res = client.get("/computers")
check("an anonymous request is refused", res.status_code in (302, 401, 403))

forged = forge(appmod.app.secret_key, {"admin_id": 1, "admin_role": "admin",
                                       "admin_username": "admin"})
client.set_cookie("session", forged, domain="localhost")
res = client.get("/computers")
check("a cookie forged with the REAL key is accepted (this is the risk)",
      res.status_code == 200)

# And the same forgery made with the old published key must now be worthless.
client2 = appmod.app.test_client()
stale = forge("appease-bulsu-meneses-2026", {"admin_id": 1, "admin_role": "admin",
                                             "admin_username": "admin"})
client2.set_cookie("session", stale, domain="localhost")
res = client2.get("/computers")
check("a cookie forged with the OLD leaked key is now REJECTED",
      res.status_code in (302, 401, 403))

# Rotating again invalidates everything signed with the previous key -
# which is what "everyone gets logged out once" means in practice.
client3 = appmod.app.test_client()
other = forge(secrets.token_hex(32), {"admin_id": 1, "admin_role": "admin"})
client3.set_cookie("session", other, domain="localhost")
res = client3.get("/computers")
check("a cookie signed with any other key is rejected",
      res.status_code in (302, 401, 403))

print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for f in failed:
    print("  -", f)
print("=" * 56)

os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)
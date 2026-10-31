"""Helpers for setup-server.bat and start-appease.bat. Standard library only.

    python server_setup.py env          create server/.env and fill in its keys
    python server_setup.py addresses    print the addresses to open AppEase at

Neither command prints a secret. "env" never overwrites a value someone
typed, apart from a SECRET_KEY that app.py would refuse to start with.
"""
import os
import secrets
import socket
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, ".env")
EXAMPLE_PATH = os.path.join(HERE, ".env.example")

# Mirrors app.py. Kept in step by t/test_easy_setup.py, which reads both.
MIN_SECRET_KEY_LENGTH = 32
COMPROMISED_SECRET_KEYS = {"appease-bulsu-meneses-2026"}

# Generated when blank. REPORT_QR_SECRET is filled only when blank: changing
# it later would invalidate every sticker already printed.
GENERATED_KEYS = ("SECRET_KEY", "REPORT_QR_SECRET")

AGENT_PORT = 8080
BROWSER_PORT = 80


def _split_value(line):
    """(key, value) for a KEY=value line, or (None, None) for anything else."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None, None
    key, value = stripped.split("=", 1)
    return key.strip(), value.strip().strip('"').strip("'")


def secret_key_problem(value):
    """Why app.py would refuse this SECRET_KEY, or None if it is usable."""
    if not value:
        return "it was blank"
    if value in COMPROMISED_SECRET_KEYS:
        return "it was the old public value"
    if len(value) < MIN_SECRET_KEY_LENGTH:
        return f"it was shorter than {MIN_SECRET_KEY_LENGTH} characters"
    return None


def prepare_env(env_path=ENV_PATH, example_path=EXAMPLE_PATH):
    """Create .env from .env.example if missing, then fill the generated keys.

    Returns a list of plain sentences saying what changed. Line endings and
    every other line are kept exactly as they were.
    """
    notes = []
    if not os.path.exists(env_path):
        if not os.path.exists(example_path):
            raise FileNotFoundError(f"Neither {env_path} nor {example_path} exists.")
        with open(example_path, "rb") as source:
            content = source.read()
        with open(env_path, "wb") as target:
            target.write(content)
        notes.append("Created server\\.env from server\\.env.example.")

    with open(env_path, "r", encoding="utf-8", newline="") as handle:
        text = handle.read()
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)

    seen = set()
    for index, line in enumerate(lines):
        key, value = _split_value(line)
        if key not in GENERATED_KEYS or key in seen:
            continue
        seen.add(key)
        if key == "SECRET_KEY":
            problem = secret_key_problem(value)
            if problem is None:
                continue
            notes.append(f"Generated a new SECRET_KEY ({problem}).")
        else:
            if value:
                continue
            notes.append(f"Generated {key}.")
        ending = line[len(line.rstrip("\r\n")):] or newline
        lines[index] = f"{key}={secrets.token_hex(32)}{ending}"

    for key in GENERATED_KEYS:
        if key not in seen:
            if lines and not lines[-1].endswith(("\n", "\r")):
                lines[-1] += newline
            lines.append(f"{key}={secrets.token_hex(32)}{newline}")
            notes.append(f"Added {key}.")

    updated = "".join(lines)
    if updated != text:
        with open(env_path, "w", encoding="utf-8", newline="") as handle:
            handle.write(updated)
    if not notes:
        notes.append("server\\.env already had its keys. Nothing was changed.")
    return notes


def lan_addresses():
    """This computer's IPv4 addresses that other machines could use, the one
    on the default route first. Never loopback or link-local (169.254)."""
    found = []

    def keep(address):
        if address and not address.startswith(("127.", "169.254.", "0.")) and address not in found:
            found.append(address)

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # UDP connect sends nothing; it only asks the OS which adapter it
        # would use. TEST-NET-1, so no real host is ever involved.
        probe.connect(("192.0.2.1", 9))
        keep(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        probe.close()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            keep(info[4][0])
    except OSError:
        pass
    return found


def address_lines(addresses):
    """What start-appease.bat and setup-server.bat show, one line each."""
    lines = [f"  On this computer:           http://localhost/"]
    if not addresses:
        lines.append("  This computer has no network address yet. Connect it to the lab network.")
        return lines
    for address in addresses:
        lines.append(f"  From other computers:       http://{address}/")
    lines.append(f"  Lab computer agents use:    http://{addresses[0]}:{AGENT_PORT}")
    lines.append(f"  (If port {BROWSER_PORT} is busy, add :{AGENT_PORT} to the browser addresses.)")
    return lines


def main(argv):
    command = argv[0] if argv else ""
    if command == "env":
        try:
            for note in prepare_env():
                print("      " + note)
        except (OSError, UnicodeDecodeError) as exc:
            print(f"      Could not prepare server\\.env: {exc}", file=sys.stderr)
            return 1
        return 0
    if command == "addresses":
        for line in address_lines(lan_addresses()):
            print(line)
        return 0
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

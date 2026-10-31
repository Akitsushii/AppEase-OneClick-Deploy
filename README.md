# AppEase — Network-Based Lab Inventory & Deployment System

AppEase is a software deployment and inventory system for Windows computer
laboratories. A Flask server on the lab network hands install and uninstall
jobs to a small agent on each lab PC; the agents poll the server, do the
work, and report back. Admins run everything from a web dashboard.

---

## Features

- **Software deployment** — push `.exe` / `.msi` installers or Chocolatey
  packages to one PC, a selection, or the whole lab, and uninstall them again
- **Computer inventory** — online/offline status from agent heartbeats, plus
  the hardware each agent reports
- **Equipment** — the keyboards, mice, monitors and other peripherals
  recorded against each computer
- **Problem reports** — students report a broken PC from their own phone by
  scanning a QR sticker on it; admins track and resolve the reports
- **Accounts and roles** — `admin` (full access) and `faculty` (problem
  reports only), with an audit trail of admin actions
- **Light and dark themes**

---

## Requirements

- Windows, on the server and on every lab PC
- Python on the server (developed and tested on Python 3.14)
- Lab PCs get Python and Chocolatey from `agent/setup.bat` if they do not
  already have them

---

## Quick setup (Windows)

For a lab technician who just wants it running. No editing of files.

**On the server computer**

1. Download **`install-appease.bat`** on its own: open it on GitHub and
   click **Download raw file**. If Windows SmartScreen warns about it, click
   **More info**, then **Run anyway**.
2. Double-click it and click **Yes** when Windows asks for administrator
   rights. It downloads the latest AppEase into `C:\AppEase`, then runs
   `C:\AppEase\setup-server.bat`, which installs Python if needed, installs
   AppEase's packages, creates `server/.env` with fresh secret keys, opens
   ports 8080 and 80 in Windows Firewall, and puts a **Start AppEase**
   shortcut on the desktop.
3. Double-click **Start AppEase** on the desktop. Keep that window open
   while AppEase is in use; closing it stops AppEase. The window shows the
   addresses to open, and the browser opens the dashboard by itself.
4. The first time, the window also prints the starting password for the
   `admin` account. Sign in with it and choose your own password.

**To update**, close the Start AppEase window and run
`install-appease.bat` again. It only adds and replaces program files, so
`server/.env`, the database, uploaded installers and photos are kept.

Already have the AppEase folder (a clone or an unzipped copy)? Skip the
download and double-click **`setup-server.bat`** inside it instead. Running
it again is safe: it skips what is done and never replaces keys already set.

**On each lab computer**

1. Copy `agent/agent.py` and `agent/setup.bat` into one folder.
2. Double-click **`setup.bat`**, click **Yes**, and type the server's IP
   address when asked (the Start AppEase window shows it). Port 8080 is
   added for you, and setup checks that the server answers before going on.

Everything below is the same setup done by hand, for anyone who wants to
see or change each step.

---

## Server setup (by hand)

### 1. Install the packages

From the repository root:

```powershell
python -m venv server\venv
server\venv\Scripts\activate
python -m pip install -r requirements.txt
```

### 2. Create `server/.env`

Copy the example and fill it in:

```powershell
copy server\.env.example server\.env
```

`SECRET_KEY` is the only setting you must fill in. It signs the admin session
cookie, so anyone who knows it can sign in as any admin. It must be random,
private, and at least 32 characters, and **the server will not start without
one**. Generate one with:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

and paste the output after `SECRET_KEY=`. Every other setting in the file is
optional and explained there: email, silent-install flag detection, QR
sticker signing, a public report form over a Cloudflare Tunnel, and demo
mode. Each one stays off while it is blank.

Never commit `server/.env`. It is already in `.gitignore`.

### 3. Start the server

```powershell
cd server
python app.py --prod
```

`--prod` runs AppEase on Waitress, listening on two ports:

- **8080** — the port agents poll. AppEase will not start if it cannot open it.
- **80** — for browsers, so the dashboard opens at `http://YOUR-SERVER-IP/`. If
  port 80 is taken or not allowed, AppEase logs a warning and serves browsers
  on 8080 instead: `http://YOUR-SERVER-IP:8080/`.

Allow inbound TCP 8080 (and 80) through Windows Firewall on the server so the
lab PCs can reach it.

Without `--prod`, `python app.py` starts Flask's development server on port
5000, with the debugger off. That is for a quick local look only: agents poll
8080 and cannot reach it.

The database, `server/appease.db`, is created on first start.

### 4. First sign-in

On a new database AppEase creates one account, `admin`, and prints its
starting password in the server console that first time. Sign in with it and
you are taken straight to a page to choose a new password; nothing else opens
until you do. Create the other admin and faculty accounts from **Admins**
afterwards.

---

## Agent setup

Do this on each lab PC.

1. Copy `agent.py` and `setup.bat` from `agent/` into one folder on the PC.

2. Run `setup.bat`. It asks for administrator rights, then:
   - installs a machine-wide Python if there is none,
   - asks for the server's address (for example `192.168.1.10`; port 8080
     is added) and checks that it answers. On a re-run it offers the
     address already in use, so Enter keeps it,
   - copies the agent to `C:\Appease\agent.py` with that address written
     into its `SERVER_BASE_URL`,
   - registers an **AppEase Agent** scheduled task that runs at startup,
   - starts the agent, and
   - installs Chocolatey if it is missing.

The PC appears on the **Computers** page once its first poll arrives.
Re-running `setup.bat` updates the agent in place. The agent uses only the
Python standard library.

---

## Running the tests

From the repository root, with the server's packages installed:

```powershell
python t/run_all.py
```

Each suite runs in its own process. A green run prints one line per suite
and exits 0. Run a subset by name, for example
`python t/run_all.py equipment`, or add `-v` for every suite's full output.

`tools/` holds two manual browser checks that need Chrome (and Node for
`visual-census`). They are not part of the test suite; see each one's README.

---

## Rebuilding the stylesheet

`server/static/css/tailwind.css` is compiled and committed, so you only need
this after adding Tailwind classes to a template or script. Put the
standalone Tailwind CSS **v4** CLI at the repository root as
`tailwindcss.exe` (it is not committed), then run `build-css.bat`.

---

## Project structure

```
agent/
  agent.py              the agent, run on each lab PC
  setup.bat             installs the agent as a scheduled task
server/
  app.py                the Flask application and every route
  database.py           schema and migrations
  server_setup.py       helpers for setup-server.bat and start-appease.bat
  .env.example          every setting, with what it does
  templates/            Jinja2 pages
  static/               CSS, JavaScript, fonts and images
  uploads/              uploaded installers (not committed)
  staging/              installers waiting to be confirmed (not committed)
t/                      the test suites; run_all.py runs them all
tools/                  manual browser checks, outside the test suite
build-css.bat           rebuilds tailwind.css
install-appease.bat     downloads AppEase into C:\AppEase and runs setup-server.bat
setup-server.bat        one-time server setup (Python, packages, .env, firewall)
start-appease.bat       starts the server; keep its window open
requirements.txt        the server's Python packages
```

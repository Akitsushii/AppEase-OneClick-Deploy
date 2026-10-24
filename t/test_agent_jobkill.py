"""How agent.py ends a timed-out install: the whole process tree, through
a Windows Job Object, never at the cost of the install. Sections 1-3 use
stand-ins; section 4 starts real processes and runs on Windows only.

Only the VM or a lab PC can show: the job NESTED inside Task Scheduler's own
(the S4U task); real installers (an MSI's work in the Windows Installer
service, which no job reaches, and ones that break away); and that ending a
real installer mid-way leaves the PC usable.

    python t/test_agent_jobkill.py --agent C:\\Appease\\agent.py
"""
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

passed = 0
failed = []
skipped = []


def check(label, condition):
    global passed
    if condition:
        passed += 1
    else:
        failed.append(label)
        print(f"  FAIL: {label}")


def skip(label, why):
    skipped.append(label)
    print(f"  SKIPPED: {label} - {why}")


def agent_path():
    for index, arg in enumerate(sys.argv):
        if arg == "--agent" and index + 1 < len(sys.argv):
            return os.path.abspath(sys.argv[index + 1])
        if arg.startswith("--agent="):
            return os.path.abspath(arg.split("=", 1)[1])
    return os.path.join(ROOT, "agent", "agent.py")


AGENT_PATH = agent_path()
if not os.path.isfile(AGENT_PATH):
    print("COULD NOT RUN - agent.py was not found.")
    sys.exit(2)

work = tempfile.mkdtemp(prefix="appease_jobkill_")
# The copy is imported, so agent.log lands here and not beside the real one.
shutil.copy(AGENT_PATH, os.path.join(work, "agent.py"))
sys.path.insert(0, work)
import agent  # noqa: E402

needed = ("start_install_process", "stop_install_process", "InstallJob", "CREATE_SUSPENDED")
if any(not hasattr(agent, name) for name in needed):
    print("COULD NOT RUN - agent.py predates the job-object tree kill.")
    sys.exit(3)


class Captured(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append((record.levelno, record.getMessage()))

    def text(self, level=logging.WARNING):
        return "\n".join(m for lv, m in self.records if lv >= level)

    def clear(self):
        self.records.clear()


for handler in list(agent.logger.handlers):
    if type(handler) is logging.StreamHandler:
        agent.logger.removeHandler(handler)
logs = Captured()
agent.logger.addHandler(logs)
# Never reached with these deadlines, but a heartbeat must not dial out.
agent.send_install_heartbeat = lambda: None


# ---------------------------------------------------------------------------
print("1. start_install_process: the order, and every fallback")
# ---------------------------------------------------------------------------
class Scene:
    """Stand-ins for Windows; everything they are asked to do lands in
    .events."""

    def __init__(self, fail=()):
        self.fail = set(fail)
        self.events = []
        self.next_pid = 100
        self.processes = []
        self.jobs = []

    def job_factory(self):
        if "create" in self.fail:
            raise OSError("CreateJobObjectW failed (Windows error 5)")
        if "create-odd" in self.fail:
            raise AttributeError("something ctypes raised")
        scene = self

        class Job:
            closed = 0

            def assign(self, pid):
                scene.events.append(("assign", pid))
                if "assign" in scene.fail:
                    raise OSError("AssignProcessToJobObject failed (Windows error 5)")

            def terminate(self):
                scene.events.append(("terminate-job",))
                if "terminate" in scene.fail:
                    raise OSError("TerminateJobObject failed (Windows error 5)")

            def active_processes(self):
                return 0

            def close(self):
                self.closed += 1
                scene.events.append(("close-job",))

        job = Job()
        self.jobs.append(job)
        return job

    def popen(self, command, **kwargs):
        flags = kwargs.get("creationflags", 0)
        suspended = bool(flags & agent.CREATE_SUSPENDED)
        self.events.append(("popen", "suspended" if suspended else "running"))
        if "popen-suspended" in self.fail and suspended:
            raise OSError("CreateProcess failed")
        if "popen" in self.fail:
            raise OSError("CreateProcess failed")
        scene = self

        class Process:
            pid = self.next_pid

            def kill(self):
                scene.events.append(("kill", self.pid))

            def wait(self, timeout=None):
                return 1

        self.next_pid += 1
        process = Process()
        process.kwargs = kwargs
        self.processes.append(process)
        return process

    def resume(self, pid):
        self.events.append(("resume", pid))
        if "resume" in self.fail:
            raise OSError("no thread of process 100 was found to resume")

    def start(self):
        logs.clear()
        return agent.start_install_process("setup.exe /S", None, job_factory=self.job_factory,
                                           resume=self.resume, popen=self.popen)


s = Scene()
process, job = s.start()
check("all well: the install runs inside its job", job is not None and job is s.jobs[0])
check("all well: started SUSPENDED, put in the job, THEN resumed - in that order",
      s.events == [("popen", "suspended"), ("assign", 100), ("resume", 100)])
check("all well: started once, through the shell, with stdin closed",
      len(s.processes) == 1 and process.kwargs.get("shell") is True
      and process.kwargs.get("stdin") is subprocess.DEVNULL)
check("all well: the job is left open for the timeout to use", s.jobs[0].closed == 0)
check("all well: nothing is logged as a problem", logs.text() == "")

for failure, words in (("create", "Windows error 5"), ("create-odd", "something ctypes raised")):
    s = Scene(fail={failure})
    try:
        process, job = s.start()
    except Exception:  # noqa: BLE001 - that IS the failure being checked for
        process = job = "raised"
    check(f"job cannot be created ({failure}): the install still runs, the old way",
          job is None and s.events == [("popen", "running")])
    check(f"job cannot be created ({failure}): the log says why and what that means",
          words in logs.text() and "ends only cmd.exe" in logs.text())

s = Scene(fail={"popen-suspended"})
process, job = s.start()
check("cannot start suspended: started again normally, outside a job",
      job is None and s.events == [("popen", "suspended"), ("close-job",), ("popen", "running")])
check("cannot start suspended: logged", "Could not start this install suspended" in logs.text())

s = Scene(fail={"create", "popen"})
try:
    s.start()
    raised = None
except OSError as exc:
    raised = exc
check("a command that cannot start at all still raises OSError, as before jobs",
      isinstance(raised, OSError))

s = Scene(fail={"assign"})
process, job = s.start()
check("cannot join the job: STILL resumed, so the install runs",
      ("resume", 100) in s.events and job is None)
check("cannot join the job: the running install is not ended or started twice",
      len(s.processes) == 1 and not any(e[0] in ("kill", "terminate-job") for e in s.events))
check("cannot join the job: the unused job is closed", s.jobs[0].closed == 1)
check("cannot join the job: logged", "Could not put this install into its job" in logs.text())

s = Scene(fail={"resume"})
process, job = s.start()
check("cannot resume: the never-run copy is ended - its job AND cmd.exe directly - then started again",
      s.events == [("popen", "suspended"), ("assign", 100), ("resume", 100),
                   ("terminate-job",), ("kill", 100), ("close-job",), ("popen", "running")])
check("cannot resume: the install that runs is the second one, outside a job",
      job is None and process is s.processes[1])
check("cannot resume: logged", "Could not resume this install" in logs.text())

s = Scene(fail={"resume", "assign"})
process, job = s.start()
check("cannot resume and not in the job: the never-run copy is ended directly",
      ("kill", 100) in s.events and ("terminate-job",) not in s.events
      and s.events[-1] == ("popen", "running"))


# ---------------------------------------------------------------------------
print("2. stop_install_process: the job when there is one")
# ---------------------------------------------------------------------------
s = Scene()
process, job = s.start()
s.events.clear()
check("with a job: the whole job is ended", agent.stop_install_process(process, job) is True)
check("with a job: cmd.exe alone is not what gets killed", s.events == [("terminate-job",)])

s = Scene(fail={"terminate"})
process, job = s.start()
s.events.clear()
logs.clear()
check("job cannot be ended: says only cmd.exe was", agent.stop_install_process(process, job) is False)
check("job cannot be ended: falls back to killing cmd.exe, as before",
      s.events == [("terminate-job",), ("kill", 100)])
check("job cannot be ended: logged", "Could not end this install's job" in logs.text())

s = Scene(fail={"create"})
process, job = s.start()
s.events.clear()
check("no job: cmd.exe is killed, as before",
      agent.stop_install_process(process, None) is False and s.events == [("kill", 100)])


# ---------------------------------------------------------------------------
print("3. run_and_classify: wired to both")
# ---------------------------------------------------------------------------
real_start = agent.start_install_process


class Hangs:
    pid = 7
    returncode = None

    def __init__(self):
        self.killed = False

    def poll(self):
        return 1 if self.killed else None

    def kill(self):
        self.killed = True
        self.returncode = 1

    def wait(self):
        return self.returncode


class StubJob:
    def __init__(self, terminate_works=True):
        self.terminate_works = terminate_works
        self.terminated = 0
        self.closed = 0
        self.process = None

    def terminate(self):
        self.terminated += 1
        if not self.terminate_works:
            raise OSError("TerminateJobObject failed (Windows error 5)")
        self.process.killed = True
        self.process.returncode = 1

    def active_processes(self):
        return 0

    def close(self):
        self.closed += 1


def timed_out_run(terminate_works):
    process, job = Hangs(), StubJob(terminate_works)
    job.process = process
    agent.start_install_process = lambda command, stdout: (process, job)
    logs.clear()
    try:
        result = agent.run_and_classify("setup.exe /S", "setup.exe /S", time.time() - 1)
    finally:
        agent.start_install_process = real_start
    return result, process, job


result, process, job = timed_out_run(True)
check("timeout: reported as timed out, with no exit code",
      result.reason == agent.REASON_TIMED_OUT and result.exit_code is None and not result.ok)
check("timeout: ends the job, not cmd.exe", job.terminated == 1)
check("timeout: the log says the whole tree was ended",
      "ended the install and everything it started" in logs.text(logging.ERROR))
check("timeout: the job is closed afterwards", job.closed == 1)

result, process, job = timed_out_run(False)
check("timeout, job cannot be ended: cmd.exe is killed instead",
      process.killed and result.reason == agent.REASON_TIMED_OUT)
check("timeout, job cannot be ended: the log says the installer may still run",
      "cmd.exe only - the installer may still be running" in logs.text(logging.ERROR))
check("timeout, job cannot be ended: still closed", job.closed == 1)


def cannot_start(command, stdout):
    raise OSError("CreateProcess failed")


agent.start_install_process = cannot_start
try:
    result = agent.run_and_classify("setup.exe /S", "setup.exe /S", time.time() + 60)
finally:
    agent.start_install_process = real_start
check("a command that cannot start is still 'Could not start the installer'",
      result.reason == agent.REASON_LAUNCH_FAILED)


# ---------------------------------------------------------------------------
print("4. Real processes on Windows")
# ---------------------------------------------------------------------------
if os.name != "nt":
    # Not a failure: Job Objects exist only on Windows, and so does the agent.
    # Sections 1-3 above still ran here.
    skip("section 4, real processes", "Windows only - Job Objects do not exist on this platform")
else:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    SYNCHRONIZE, PROCESS_TERMINATE = 0x00100000, 0x0001
    WAIT_OBJECT_0, WAIT_TIMEOUT = 0, 0x102

    # The "installer": records its own pid (pidfile + ".installer"), starts a
    # grandchild that records its pid and sleeps, then either hangs itself or
    # exits at once leaving the grandchild. 60s sleeps: long enough to outlive
    # every check, short enough that a broken tree kill cannot stall the run.
    installer = os.path.join(work, "fake_installer.py")
    grandchild = os.path.join(work, "fake_grandchild.py")
    with open(grandchild, "w", encoding="utf-8") as handle:
        handle.write(
            "import os, sys, time\n"
            "open(sys.argv[1] + '.tmp', 'w').write(str(os.getpid()))\n"
            "os.replace(sys.argv[1] + '.tmp', sys.argv[1])\n"
            "time.sleep(60)\n"
        )
    with open(installer, "w", encoding="utf-8") as handle:
        handle.write(
            "import os, subprocess, sys, time\n"
            "mode, pidfile = sys.argv[1], sys.argv[2]\n"
            "if mode == 'breakaway':\n"
            "    try:\n"
            "        subprocess.Popen([sys.executable, '-c', 'pass'], creationflags=0x01000000).wait()\n"
            "        open(pidfile, 'w').write('ok')\n"
            "    except OSError as exc:\n"
            "        open(pidfile, 'w').write('denied: %s' % exc)\n"
            "    sys.exit(0)\n"
            "open(pidfile + '.installer.tmp', 'w').write(str(os.getpid()))\n"
            "os.replace(pidfile + '.installer.tmp', pidfile + '.installer')\n"
            f"subprocess.Popen([sys.executable, {grandchild!r}, pidfile])\n"
            "if mode == 'hang':\n"
            "    time.sleep(60)\n"
        )

    # The base interpreter, not sys.executable: a venv's python.exe is a
    # redirector that starts the real one as a child, so the pid a script
    # records would not be the process cmd.exe started.
    PYTHON = getattr(sys, "_base_executable", None) or sys.executable

    def command(mode, pidfile):
        return f'"{PYTHON}" "{installer}" {mode} "{pidfile}"'

    def grandchild_handle(pidfile, seconds=20):
        """A handle to the process whose pid is in pidfile, opened while it
        is alive, or None."""
        end = time.time() + seconds
        while time.time() < end:
            try:
                with open(pidfile, encoding="ascii") as handle:
                    pid = int(handle.read())
                return kernel32.OpenProcess(SYNCHRONIZE | PROCESS_TERMINATE, False, pid)
            except (OSError, ValueError):
                time.sleep(0.1)
        return None

    def ended(handle, wait_ms):
        return kernel32.WaitForSingleObject(handle, wait_ms) == WAIT_OBJECT_0

    def clean_up(handle, pidfile):
        """End the grandchild AND the installer: a broken tree kill leaves the
        installer sleeping, and the next check must not inherit it."""
        for h in (handle, grandchild_handle(pidfile + ".installer", seconds=1)):
            if h:
                kernel32.TerminateProcess(h, 1)
                kernel32.CloseHandle(h)

    def run_timed_out(pidfile, seconds=6):
        """run_and_classify on a hanging installer, in a thread; returns
        (result, grandchild handle) once the timeout has fired."""
        box = {}
        cmd = command("hang", pidfile)
        thread = threading.Thread(
            target=lambda: box.update(result=agent.run_and_classify(cmd, cmd, time.time() + seconds)),
            daemon=True)
        thread.start()
        handle = grandchild_handle(pidfile)
        thread.join(60)
        return box.get("result"), handle

    # 4a. The real start path: suspended, assigned, resumed - and it runs.
    out = tempfile.TemporaryFile(mode="w+")
    logs.clear()
    process, job = agent.start_install_process("echo started in a job", out)
    try:
        code = process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        code = "still suspended after 30s"
        process.kill()
    out.seek(0)
    check("real: the install runs inside a job on this machine", job is not None)
    check("real: resumed and ran to the end, exit 0", code == 0)
    check("real: its output arrives", "started in a job" in out.read())
    check("real: no fallback was needed", logs.text() == "")
    if job is not None:
        job.close()

    # 4b. A timeout ends the grandchild too.
    logs.clear()
    pidfile = os.path.join(work, "tree.pid")
    result, handle = run_timed_out(pidfile)
    check("real timeout: reported as timed out", result is not None and result.reason == agent.REASON_TIMED_OUT)
    check("real timeout: the grandchild had started", bool(handle))
    check("real timeout: the GRANDCHILD is ended, not just cmd.exe", bool(handle) and ended(handle, 10000))
    check("real timeout: the log says so", "ended the install and everything it started" in logs.text(logging.ERROR))
    clean_up(handle, pidfile)

    # 4c. The control: without a job the grandchild survives. Proves 4b can
    # see the difference, and that the fallback is the job-less behaviour.
    real_job = agent.InstallJob

    class NoJob:
        def __init__(self):
            raise OSError("CreateJobObjectW failed (Windows error 5)")

    agent.InstallJob = NoJob
    logs.clear()
    try:
        pidfile = os.path.join(work, "nojob.pid")
        result, handle = run_timed_out(pidfile)
    finally:
        agent.InstallJob = real_job
    check("fallback: the install still ran and timed out", result is not None and result.reason == agent.REASON_TIMED_OUT)
    check("fallback: the grandchild survives, exactly as before jobs (the control for 4b)",
          bool(handle) and not ended(handle, 2000))
    check("fallback: the log says the install ran without a job",
          "Could not create a job for this install" in logs.text())
    check("fallback: the timeout line says only cmd.exe was ended",
          "cmd.exe only - the installer may still be running" in logs.text(logging.ERROR))
    clean_up(handle, pidfile)

    # 4d. A normal finish ends nothing the installer left running.
    pidfile = os.path.join(work, "left.pid")
    cmd = command("exit", pidfile)
    logs.clear()
    result = agent.run_and_classify(cmd, cmd, time.time() + 60)
    handle = grandchild_handle(pidfile)
    check("normal finish: reported as an ordinary success", result.ok and result.exit_code == 0)
    check("normal finish: what the installer left running is NOT ended",
          bool(handle) and not ended(handle, 2000))
    check("normal finish: the log says processes are still running",
          "process(es) the install started are still running" in logs.text(logging.INFO))
    clean_up(handle, pidfile)

    # 4e. Asking to start a child outside the job is allowed. Only checked
    # where this test's own process may break away at all - a terminal can
    # run everything inside a job of its own that forbids it.
    control = os.path.join(work, "breakaway-control.txt")
    subprocess.run(command("breakaway", control), shell=True, timeout=60)
    with open(control, encoding="utf-8") as handle:
        allowed_here = handle.read() == "ok"
    if not allowed_here:
        skip("breakaway inside the job", "this test's own process may not break away from its job")
    else:
        inside = os.path.join(work, "breakaway-inside.txt")
        cmd = command("breakaway", inside)
        result = agent.run_and_classify(cmd, cmd, time.time() + 60)
        with open(inside, encoding="utf-8") as handle:
            answer = handle.read()
        check("breakaway: an installer that asks to start a child outside its job is not refused",
              answer == "ok")
        check("breakaway: and the install completes", result.ok)

    # 4f. A suspended copy that cannot be resumed never ran: it is ended and
    # the install runs once, not twice.
    def cannot_resume(pid):
        raise OSError("no thread of process %d was found to resume" % pid)

    out = tempfile.TemporaryFile(mode="w+")
    process, job = agent.start_install_process("echo ran", out, resume=cannot_resume)
    process.wait(timeout=30)
    out.seek(0)
    check("cannot resume (real): the install ran exactly once", out.read().count("ran") == 1)
    check("cannot resume (real): outside a job", job is None)


print("\n" + "=" * 56)
print(f"PASSED: {passed}")
print(f"FAILED: {len(failed)}")
for label in failed:
    print("  -", label)
if skipped:
    print(f"SKIPPED: {len(skipped)}")
    for label in skipped:
        print("  -", label)
print("=" * 56)

for handler in list(agent.logger.handlers):
    handler.close()
os.chdir(ROOT)
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failed else 0)

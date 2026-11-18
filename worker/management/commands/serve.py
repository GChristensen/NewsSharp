"""`reader serve` (worker/management/commands/serve.py): a small supervisor that applies
migrations, starts the web and worker processes, connects them with the wake-up pipes,
restarts them and forwards termination. It does no other work."""
import os
import signal
import subprocess
import sys
import time

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection

from feeds.models import Setting
from worker import wakeup

HEARTBEAT_LIMIT = 120


class Managed:
    def __init__(self, name: str, env: dict, pass_fds: list[int]):
        self.name = name
        self.env = {**os.environ, **env, "NEWSSHARP_SUPERVISED": "1"}
        self.pass_fds = pass_fds
        self.process = None
        self.delay = 1.0
        self.started_at = 0.0
        self.restart_at = 0.0

    def start(self) -> None:
        command = [sys.executable, str(settings.BASE_DIR / "manage.py"), self.name]
        kwargs = {"close_fds": False} if os.name == "nt" else {"pass_fds": self.pass_fds}
        self.process = subprocess.Popen(command, cwd=settings.BASE_DIR, env=self.env, **kwargs)
        self.started_at = time.monotonic()

    def check(self) -> None:
        """Restart an exited process with a delay of 1 s doubling to 30 s."""
        if self.process is not None and self.process.poll() is None:
            if time.monotonic() - self.started_at > 60:
                self.delay = 1.0
            return
        if self.process is not None:
            print(f"supervisor: {self.name} exited with code {self.process.returncode}; restarting in {self.delay:.0f}s", flush=True)
            self.process = None
            self.restart_at = time.monotonic() + self.delay
            self.delay = min(self.delay * 2, 30.0)
        if time.monotonic() >= self.restart_at:
            self.start()

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None


class Command(BaseCommand):
    help = "Start NewsSharp: the web process and the worker process under a supervisor."

    def handle(self, *args, **options):
        os.environ["NEWSSHARP_PROC"] = "supervisor"
        call_command("migrate", interactive=False, verbosity=0)
        to_worker_r, to_worker_w, fds_a = wakeup.make_pipe()
        to_web_r, to_web_w, fds_b = wakeup.make_pipe()
        fds = fds_a + fds_b
        web = Managed("web", {wakeup.ENV_WRITE: str(to_worker_w), wakeup.ENV_READ: str(to_web_r)}, fds)
        worker = Managed("worker", {wakeup.ENV_READ: str(to_worker_r), wakeup.ENV_WRITE: str(to_web_w)}, fds)
        processes = [web, worker]

        stopping = []
        for name in ("SIGTERM", "SIGBREAK"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), lambda *_: stopping.append(True))

        print(f"supervisor: starting NewsSharp on http://{settings.BIND}:{settings.PORT}", flush=True)
        last_heartbeat_check = time.monotonic()
        try:
            while not stopping:
                for managed in processes:
                    managed.check()
                if time.monotonic() - last_heartbeat_check >= 10:
                    last_heartbeat_check = time.monotonic()
                    self.check_heartbeat(worker)
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown(processes)

    def check_heartbeat(self, worker: Managed) -> None:
        """Kill and restart a worker whose heartbeat is older than 120 s."""
        if not worker.alive() or time.monotonic() - worker.started_at < HEARTBEAT_LIMIT:
            return
        try:
            value = Setting.get("worker_heartbeat")
        except Exception:
            return
        finally:
            connection.close()
        if value and time.time() - int(value) > HEARTBEAT_LIMIT:
            print("supervisor: worker heartbeat is stale; killing the worker", flush=True)
            worker.process.kill()

    def shutdown(self, processes: list[Managed]) -> None:
        """Signal both processes; kill what has not exited after 15 s."""
        print("supervisor: stopping", flush=True)
        for managed in processes:
            if managed.alive() and os.name != "nt":
                managed.process.terminate()
        deadline = time.monotonic() + (15 if os.name != "nt" else 5)
        while time.monotonic() < deadline and any(m.alive() for m in processes):
            time.sleep(0.2)
        for managed in processes:
            if managed.alive():
                managed.process.kill()

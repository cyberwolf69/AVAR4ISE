"""Supervise the optional Telegram remote alongside the trading engine."""
import json
import subprocess
import sys
import time
from hedging_tools.paths import PROJECT_ROOT
from hedging_tools.telegram_remote import settings


class RemoteWorker:
    def __init__(self, clock=time.monotonic, launch=subprocess.Popen,
                 configure=settings, publish=None):
        self.clock, self.launch = clock, launch
        self.publish = publish or (lambda report: print(json.dumps(report), flush=True))
        self.child = None
        self.next_run = 0
        self.closed = False
        try:
            # Validate locally. Credentials are loaded by the child, never passed in argv.
            configure()
            self.enabled = True
        except ValueError as exc:
            self.enabled = False
            self.publish({'status': 'REMOTE_NOT_STARTED', 'reason': str(exc),
                          'engine': 'continues', 'action': 'Configure .env and restart run to enable the remote'})

    def tick(self):
        if not self.enabled or self.closed:
            return
        now = self.clock()
        if self.child is not None:
            code = self.child.poll()
            if code is None:
                return
            self.child.wait()
            self.child = None
            self.next_run = now + 60
            self.publish({'status': 'REMOTE_RESTART_PENDING', 'exit_code': code,
                          'retry_seconds': 60, 'engine': 'continues'})
        if now < self.next_run:
            return
        try:
            # Separate session: Ctrl+C is handled by the parent, which closes its child.
            self.child = self.launch(
                [sys.executable, '-B', '-m', 'hedging_tools.telegram_remote'],
                cwd=PROJECT_ROOT, start_new_session=True, stderr=subprocess.DEVNULL)
            self.publish({'status': 'REMOTE_STARTING', 'managed_by': 'run'})
        except OSError:
            self.next_run = now + 60
            self.publish({'status': 'REMOTE_RESTART_PENDING', 'retry_seconds': 60,
                          'reason': 'Unable to start Telegram remote', 'engine': 'continues'})

    def close(self):
        self.closed = True
        if self.child is None:
            return
        if self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=5)
        else:
            self.child.wait()
        self.child = None

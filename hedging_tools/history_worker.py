"""Bounded reference-history worker; never blocks portfolio supervision."""
import subprocess
import sys
import time
from hedging_tools.paths import PROJECT_ROOT


class HistoryWorker:
    def __init__(self, clock=time.monotonic, launch=subprocess.Popen, module='hedging_tools.signal_history', arguments=('--update',)):
        self.clock, self.launch = clock, launch
        self.module, self.arguments = module, arguments
        self.child = None
        self.started, self.next_run = 0, 0

    def tick(self):
        now = self.clock()
        if self.child is not None:
            if self.child.poll() is None:
                if now - self.started > 120:
                    self.child.kill()
                return
            self.child = None
            self.next_run = now + 60
        if now >= self.next_run:
            try:
                self.child = self.launch([sys.executable, '-B', '-m', self.module, *self.arguments],
                                         cwd=PROJECT_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.started = now
            except OSError:
                self.next_run = now + 60

    def close(self):
        if self.child is not None:
            if self.child.poll() is None:
                self.child.terminate()
                try:
                    self.child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.child.kill()
            self.child.wait(timeout=5)

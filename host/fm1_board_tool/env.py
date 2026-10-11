"""Where the tools find ports and boot disks: the real system or the simulator."""

from __future__ import annotations

import time

from . import volumes


class Env:
    def __init__(self, backend=None, sim=None, input_fn=input, interactive=False):
        self.sim = sim
        if sim is not None:
            self.backend = sim
        elif backend is not None:
            self.backend = backend
        else:
            from .ports import PySerialBackend
            self.backend = PySerialBackend()
        self.input_fn = input_fn
        self.interactive = interactive

    @property
    def evidence(self) -> str:
        return "SIMULATED" if self.sim is not None else "HARDWARE"

    def find_volumes(self):
        if self.sim is not None:
            # simulated disks are folders on a big file system: no size heuristic
            return volumes.find(self.sim.volume_candidates(), max_bytes=None)
        return volumes.find()

    def wait(self, fn, timeout, step=0.25):
        deadline = time.monotonic() + timeout
        while True:
            v = fn()
            if v or time.monotonic() >= deadline:
                return v
            time.sleep(step)

    def ask(self, prompt, choices=("y", "n", "s")) -> str:
        """y/n/s(kip); non-interactive runs answer s."""
        if not self.interactive:
            return "s"
        while True:
            try:
                a = self.input_fn(prompt).strip().lower()[:1]
            except EOFError:
                return "s"
            if a in choices:
                return a

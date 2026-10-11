"""Simple window for the FM-1 Transporter tools (Tkinter, part of Python).

Started by FM1-Transporter.bat. Every button runs the same code as the
command line (make-firmware, hwtest phase A / B, list, console) in a worker
thread; the questions those procedures ask become Yes/No or text dialogs.
Nothing here can write to the FM-1: phase B is the read-only first contact.

FM1T_SIM=<board> runs it against the simulator (for testing, no hardware).
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import __version__, profiles as prof
from .profiles import REPO_ROOT

log = logging.getLogger("fm1_board_tool")

LOGDIR = REPO_ROOT / "logs"
BACKUPS = REPO_ROOT / "backups"


def classify_prompt(prompt: str) -> str:
    """How to ask a procedure's question: 'yns' (Yes/No/Skip), 'yn', or 'text'."""
    p = prompt.lower()
    if "[y/n/s]" in p:
        return "yns"
    if "[y/n]" in p:
        return "yn"
    return "text"


class Asker:
    """input() replacement callable from the worker thread: the question is
    shown by the Tk thread and the worker waits for the answer."""

    def __init__(self):
        self.requests = queue.Queue()

    def __call__(self, prompt):
        reply = queue.Queue(maxsize=1)
        self.requests.put((prompt, reply))
        return reply.get()


class QueueLogHandler(logging.Handler):
    def __init__(self, q):
        super().__init__(logging.INFO)
        self.q = q
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record):
        self.q.put(("log", record.levelno, self.format(record)))


def make_env(asker):
    from .env import Env
    sim = os.environ.get("FM1T_SIM")
    if sim:
        from .sim import SimWorld, SimBoard, FakeFM1
        w = SimWorld()
        w.add(SimBoard(board_id=sim, fm1=FakeFM1() if os.environ.get("FM1T_SIM_FM1") else None,
                       state="bootsel" if os.environ.get("FM1T_SIM_BOOTSEL") else "app"))
        log.info("SIMULATORE: scheda %s (nessun hardware usato)", sim)
        return Env(sim=w, interactive=True, input_fn=asker)
    return Env(interactive=True, input_fn=asker)


class App:
    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.root = tk, root
        self.events = queue.Queue()
        self.asker = Asker()
        self.busy = False
        root.title("FM-1 Transporter %s" % __version__)
        root.geometry("900x640")
        root.minsize(700, 480)

        top = ttk.Frame(root, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="Scheda:").pack(side="left")
        boards = [p.id for p in prof.load_profiles().values() if p.enabled]
        self.board = tk.StringVar(value="dollatek_rp2040_typec" if "dollatek_rp2040_typec" in boards else boards[0])
        ttk.Combobox(top, textvariable=self.board, values=boards, state="readonly", width=28).pack(side="left", padx=6)
        self.noflash = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="la scheda ha già il firmware (non riflashare)",
                        variable=self.noflash).pack(side="left", padx=12)

        steps = ttk.LabelFrame(root, text="Procedura", padding=10)
        steps.pack(fill="x", padx=10)
        self.buttons = []

        def btn(parent, text, cmd, row, col, hint):
            b = ttk.Button(parent, text=text, command=cmd, width=34)
            b.grid(row=row, column=col, sticky="w", padx=4, pady=3)
            ttk.Label(parent, text=hint, foreground="#555").grid(row=row, column=col + 1, sticky="w", padx=4)
            self.buttons.append(b)

        btn(steps, "1. Prepara il firmware", self.do_firmware, 0, 0,
            "scarica il loader JieLi (verificato) e crea l'UF2 completo")
        btn(steps, "2. Collaudo della scheda (fase A)", self.do_phase_a, 1, 0,
            "scheda da sola, niente collegato a GP0/GP1, nessun FM-1")
        btn(steps, "3. Primo contatto con l'FM-1 (fase B)", self.do_phase_b, 2, 0,
            "SOLA LETTURA: identifica il chip e salva il backup")

        tools = ttk.Frame(root, padding=(10, 6))
        tools.pack(fill="x")
        for text, cmd in (("Porte e dischi", self.do_list), ("Console (20 s)", self.do_console),
                          ("Apri cartella log", lambda: self.open_dir(LOGDIR)),
                          ("Apri cartella backup", lambda: self.open_dir(BACKUPS))):
            b = ttk.Button(tools, text=text, command=cmd)
            b.pack(side="left", padx=4)
            if text in ("Porte e dischi", "Console (20 s)"):
                self.buttons.append(b)

        self.status = tk.StringVar(value="Pronto. Leggi DOLLATEK-RP2040-GUIDE.md prima di collegare l'FM-1.")
        ttk.Label(root, textvariable=self.status, padding=(10, 2)).pack(fill="x")

        frame = ttk.Frame(root, padding=(10, 0, 10, 10))
        frame.pack(fill="both", expand=True)
        self.text = tk.Text(frame, wrap="word", font=("Consolas", 10), state="disabled")
        sb = ttk.Scrollbar(frame, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("err", foreground="#b00020")
        self.text.tag_configure("ok", foreground="#1b5e20")

        self._setup_logging()
        root.after(100, self.pump)
        log.info("FM-1 Transporter - strumenti %s. Cartella: %s", __version__, REPO_ROOT)

    # --- plumbing -------------------------------------------------------------
    def _setup_logging(self):
        LOGDIR.mkdir(parents=True, exist_ok=True)
        log.setLevel(logging.DEBUG)
        for h in list(log.handlers):
            log.removeHandler(h)
        fh = logging.FileHandler(LOGDIR / ("gui-%s.log" % _dt.datetime.now().strftime("%Y%m%d-%H%M%S")),
                                 encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
        log.addHandler(fh)
        log.addHandler(QueueLogHandler(self.events))

    def write(self, line, tag=None):
        self.text.configure(state="normal")
        self.text.insert("end", line + "\n", tag)
        self.text.see("end")
        self.text.configure(state="disabled")

    def pump(self):
        try:
            while True:
                kind, *rest = self.events.get_nowait()
                if kind == "log":
                    level, msg = rest
                    tag = "err" if level >= logging.ERROR else ("ok" if "PASS" in msg or "BUILD VERIFIED" in msg else None)
                    for line in msg.splitlines() or [""]:
                        self.write(line, tag)
                elif kind == "done":
                    title, ok, summary = rest
                    self.set_busy(False)
                    self.status.set("%s: %s" % (title, summary))
                    from tkinter import messagebox
                    (messagebox.showinfo if ok else messagebox.showwarning)(title, summary)
        except queue.Empty:
            pass
        try:
            while True:
                prompt, reply = self.asker.requests.get_nowait()
                reply.put(self.ask(prompt))
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    def ask(self, prompt):
        from tkinter import messagebox, simpledialog
        kind = classify_prompt(prompt)
        self.write("? " + prompt.strip())
        if kind == "yns":
            a = messagebox.askyesnocancel("Domanda", prompt.strip() + "\n\n(Annulla = salta)", parent=self.root)
            ans = "s" if a is None else ("y" if a else "n")
        elif kind == "yn":
            ans = "y" if messagebox.askyesno("Domanda", prompt.strip(), parent=self.root) else "n"
        else:
            a = simpledialog.askstring("Domanda", prompt.strip() + "\n\n(OK per continuare, Annulla per fermarsi)",
                                       parent=self.root)
            ans = "q" if a is None else a
        self.write("  -> " + {"y": "sì", "n": "no", "s": "salta", "q": "stop"}.get(ans, ans or "(Invio)"))
        return ans

    def set_busy(self, busy):
        self.busy = busy
        for b in self.buttons:
            b.configure(state="disabled" if busy else "normal")

    def run_task(self, title, fn):
        if self.busy:
            return
        self.set_busy(True)
        self.status.set(title + " in corso...")
        self.write("")
        self.write("===== %s =====" % title)

        def worker():
            try:
                ok, summary = fn()
            except Exception as e:      # shown, logged, never silent
                log.debug("task failed", exc_info=True)
                log.error("ERRORE: %s", e)
                ok, summary = False, str(e)
            self.events.put(("done", title, ok, summary))
        threading.Thread(target=worker, daemon=True).start()

    def profile(self):
        return prof.get_profile(self.board.get())

    def open_dir(self, d):
        d.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(d))
        else:
            subprocess.Popen(["xdg-open" if sys.platform.startswith("linux") else "open", str(d)])

    # --- actions --------------------------------------------------------------
    def do_firmware(self):
        def task():
            from . import build
            p = self.profile()
            if not (REPO_ROOT / "dist" / p.id / "manifest-placeholder.json").is_file():
                return False, "nessun firmware pronto per %s in dist/" % p.id
            loader = build.fetch_loader(REPO_ROOT / ".cache" / "wl82loader.bin")
            r = build.make_firmware(p.id, loader)
            log.info("Firmware pronto: %s", r["file"])
            return True, "firmware pronto: %s" % Path(r["file"]).name
        self.run_task("Preparazione firmware", task)

    def _report(self, rep):
        log.info(rep.markdown())
        counts = ", ".join("%s %d" % (s, rep.count(s)) for s in ("PASS", "FAIL", "NOT TESTED", "REQUIRES FM-1")
                           if rep.count(s))
        stopped = rep.meta.get("stopped")
        ok = not rep.failed and not stopped
        return ok, "%s%s (%s). Report in logs." % ("OK" if ok else "NON superato",
                                                   " - fermato: %s" % stopped if stopped else "", counts)

    def do_phase_a(self):
        from tkinter import messagebox
        p = self.profile()
        if not messagebox.askokcancel(
                "Fase A",
                "Collaudo della %s DA SOLA.\n\n- niente collegato a GP0/GP1\n- FM-1 non collegato\n"
                "- cavo USB-C DATI verso il PC\n\nIl programma ti guiderà con alcune domande." % p.name):
            return

        def task():
            from . import hwtest
            from .cli import default_uf2
            uf2 = None if self.noflash.get() else default_uf2(p)
            if uf2 is None and not self.noflash.get():
                return False, "nessun UF2 per %s: usa prima il pulsante 1" % p.id
            if uf2 and uf2.name.endswith("-noloader.uf2"):
                log.warning("Uso %s (senza loader): va bene per la fase A; per la fase B serve il pulsante 1.",
                            uf2.name)
            env = make_env(self.asker)
            return self._report(hwtest.run(env, p, uf2=uf2, phases="a", logdir=LOGDIR))
        self.run_task("Fase A - collaudo scheda", task)

    def do_phase_b(self):
        from tkinter import messagebox
        p = self.profile()
        if not messagebox.askokcancel(
                "Fase B - sola lettura",
                "Primo contatto con l'FM-1. NULLA viene scritto sull'FM-1.\n\nPrima:\n"
                "- fase A superata e firmware completo (pulsante 1) sulla scheda\n"
                "- FM-1 carico e SPENTO\n- solo D+, D-, GND collegati, VBUS isolato\n"
                "- collegamenti misurati col multimetro\n\nTi verrà chiesto di confermare ogni punto.",
                icon="warning"):
            return

        def task():
            from . import hwtest
            env = make_env(self.asker)
            return self._report(hwtest.run(env, p, phases="b", logdir=LOGDIR, dump_dir=BACKUPS))
        self.run_task("Fase B - FM-1 sola lettura", task)

    def do_list(self):
        def task():
            from .ports import group_boards
            env = make_env(self.asker)
            ports = env.backend.list_ports()
            log.info("Porte seriali:")
            for x in ports:
                tag = ("RP2040, interfaccia %s (%s)" % (x.interface_number, x.role)) if x.is_transporter_candidate else ""
                log.info("  %-10s %-36s %s", x.device, (x.description or "")[:36], tag)
            if not ports:
                log.info("  (nessuna)")
            vols = env.find_volumes()
            log.info("Dischi RPI-RP2: %s", ", ".join(str(v.path) for v in vols) or "(nessuno)")
            n = len(group_boards(ports))
            return True, "%d porte, %d schede RP2040, %d dischi RPI-RP2" % (len(ports), n, len(vols))
        self.run_task("Porte e dischi", task)

    def do_console(self):
        def task():
            from .ports import discover
            env = make_env(self.asker)
            found = discover(env.backend)
            if not found or not found[0].console:
                return False, "nessun Transporter trovato"
            s = env.backend.open(found[0].console)
            end, n = time.monotonic() + 20, 0
            try:
                while time.monotonic() < end:
                    s.timeout = 0.5
                    line = s.readline().decode("utf-8", "replace").rstrip()
                    if line:
                        n += 1
                        log.info("console| %s", line)
            finally:
                s.close()
            return True, "%d righe dalla console %s" % (n, found[0].console)
        self.run_task("Console", task)


def main():
    try:
        import tkinter as tk
    except ImportError:
        print("Tkinter non disponibile: reinstalla Python da python.org con 'tcl/tk and IDLE'.")
        return 1
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

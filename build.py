#!/usr/bin/env python3
"""
build.py — build / extend PyTorchUI's node database.

The script infers what to do from the flags you pass.  If any of
--install / --update / --register / --unregister / --check / --list
/ --verify is present, ONLY those operations run.  Otherwise a full
build is done with -l libraries (or the default list).

Every operation flag accepts an optional value:

    python build.py --install numpy               install numpy only
    python build.py --install                     install - asks what
    python build.py --register torch              register torch only
    python build.py --install torch --register torch
                                                  install, then register
    python build.py --update torch                pip install -U torch
    python build.py --unregister numpy            drop numpy from DB
    python build.py --check torch                 installed + DB status
    python build.py --list                        list registered libs
    python build.py --verify                      DB integrity report

    python build.py                               full build, defaults
    python build.py -l torch,numpy -nc            full build, no cap
    python build.py --gui                         the same in a window

Progress and log:

    In GUI mode a progress bar shows N steps and a log pane streams
    each subprocess's stdout/stderr as it runs.  In CLI mode the same
    output goes straight to your terminal.

Missing data:

    If you ask for an operation without enough info (e.g. --register
    with no library) the script asks for it.  CLI uses input(), GUI
    uses an input dialog.
"""

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys


# ------------------------------------------------------------------ #
#  Defaults                                                           #
# ------------------------------------------------------------------ #

DEFAULT_LIBS = [
    "os", "io", "os.path", "math", "random", "json", "pathlib",
    "shutil", "tempfile", "time", "datetime", "sqlite3",
    "torch", "torchvision", "torchaudio",
    "transformers", "tensorflow",
    "numpy", "scipy", "pandas", "matplotlib",
    "pyautogui", "pyperclip", "httpx", "PyQt5",
    "bs4", "requests",
]


def _root():
    return os.path.dirname(os.path.abspath(__file__))


def _create_py():
    return os.path.join(_root(), "create.py")


# ------------------------------------------------------------------ #
#  Steps                                                              #
# ------------------------------------------------------------------ #

class Step:
    __slots__ = ("kind", "target")

    def __init__(self, kind, target=""):
        self.kind = kind          # install | update | register |
                                  # unregister | check | list |
                                  # verify | build
        self.target = target or ""

    def label(self):
        if self.kind == "install":
            return "install %s" % self.target
        if self.kind == "update":
            return "update %s" % self.target
        if self.kind == "register":
            return "register %s" % self.target
        if self.kind == "unregister":
            return "unregister %s" % self.target
        if self.kind == "check":
            return "check %s" % self.target
        if self.kind == "list":
            return "list registered libraries"
        if self.kind == "verify":
            return "verify database"
        if self.kind == "build":
            return "build database"
        if self.kind == "fetch":
            return "fetch pre-built database"
        return self.kind


def _collect_from_args(args):
    """Translate CLI flags into an ordered list of steps."""
    explicit = any([
        args.install is not None,
        args.update is not None,
        args.register is not None,
        args.unregister is not None,
        args.check is not None,
        args.list,
        args.verify,
        getattr(args, "fetch", False),
    ])

    steps = []

    if getattr(args, "fetch", False):
        steps.append(Step("fetch"))

    if args.install is not None:
        for pkg in args.install:
            steps.append(Step("install", pkg))

    if args.update is not None:
        for pkg in args.update:
            steps.append(Step("update", pkg))

    if args.unregister is not None:
        for lib in args.unregister:
            steps.append(Step("unregister", lib))

    if args.register is not None:
        for lib in args.register:
            steps.append(Step("register", lib))

    if args.check is not None:
        for lib in args.check:
            steps.append(Step("check", lib))

    if args.list:
        steps.append(Step("list"))

    if args.verify:
        steps.append(Step("verify"))

    if not explicit:
        # Full build is the default.
        steps.append(Step("build"))

    return steps


# ------------------------------------------------------------------ #
#  Prompts                                                            #
# ------------------------------------------------------------------ #

class _Prompter:
    """Asks for missing data.  CLI or GUI depending on mode."""

    def __init__(self, gui=False):
        self.gui = gui

    def text(self, title, prompt, default=""):
        if self.gui:
            from PyQt5.QtWidgets import QInputDialog
            ans, ok = QInputDialog.getText(
                None, title, prompt, text=default)
            return ans if ok else None
        if not sys.stdin.isatty():
            print("! cannot prompt in non-interactive session: %s"
                  % prompt)
            return None
        try:
            ans = input("%s [%s]: " % (prompt, default)).strip()
        except EOFError:
            return None
        return ans or default


def _preflight(steps, args, prompt):
    """Fill in anything a step needs but doesn't have."""
    # Libraries: any empty target on register / unregister / install
    for st in steps:
        if st.kind in ("install", "update") and not st.target:
            ans = prompt.text(
                "Package name",
                "Which PyPI package?")
            if not ans:
                return False
            st.target = ans.strip()
        elif st.kind in ("register", "unregister", "check") \
                and not st.target:
            ans = prompt.text(
                "Library name",
                "Which library (e.g. torch, numpy)?")
            if not ans:
                return False
            st.target = ans.strip()

    # Database path: only needed if we register / unregister / list /
    # verify.  Falls back to main.db.
    needs_db = any(st.kind in ("register", "unregister", "list",
                               "verify", "build") for st in steps)
    if needs_db and not args.db_path:
        args.db_path = "main.db"

    return True


# ------------------------------------------------------------------ #
#  Subprocess helpers                                                 #
# ------------------------------------------------------------------ #

def _stream(cmd, log_fn, cwd=None):
    """Run cmd, stream every line to log_fn, return exit code.

    Returns (code, was_cancelled).
    """
    log_fn("$ " + " ".join(cmd))
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, cwd=cwd,
            start_new_session=(sys.platform != "win32"))
    except Exception as ex:
        log_fn("! failed to start: %s" % ex)
        return 1, False
    try:
        for line in iter(proc.stdout.readline, ""):
            log_fn(line.rstrip("\n"))
        return proc.wait(), False
    except KeyboardInterrupt:
        try:
            proc.terminate()
        except Exception:
            pass
        return 130, True


# ------------------------------------------------------------------ #
#  Step runners                                                       #
# ------------------------------------------------------------------ #

def _run_install(step, args, log_fn):
    cmd = [args.python, "-m", "pip", "install"]
    if step.kind == "update":
        cmd.append("--upgrade")
    cmd.append(step.target)
    code, _ = _stream(cmd, log_fn)
    return code == 0


def _run_register(step, args, log_fn):
    if not os.path.isfile(_create_py()):
        log_fn("! create.py not found")
        return False
    cmd = [args.python, _create_py(),
           "--db", "--no-torch",
           "--db-path", args.db_path,
           "--register-lib", step.target]
    if args.no_wal:
        cmd.append("--no-wal")
    code, _ = _stream(cmd, log_fn, cwd=_root())
    return code == 0


def _run_unregister(step, args, log_fn):
    if not os.path.isfile(_create_py()):
        log_fn("! create.py not found")
        return False
    cmd = [args.python, _create_py(),
           "--db", "--no-torch",
           "--db-path", args.db_path,
           "--unregister-lib", step.target]
    if args.no_wal:
        cmd.append("--no-wal")
    code, _ = _stream(cmd, log_fn, cwd=_root())
    return code == 0


def _run_check(step, args, log_fn):
    cmd = [args.python, _create_py(),
           "--db", "--check-lib", step.target,
           "--db-path", args.db_path]
    code, _ = _stream(cmd, log_fn, cwd=_root())
    return code == 0


def _run_list(step, args, log_fn):
    cmd = [args.python, _create_py(),
           "--db", "--list-libs", "--db-path", args.db_path]
    code, _ = _stream(cmd, log_fn, cwd=_root())
    return code == 0


def _run_verify(step, args, log_fn):
    db = args.db_path
    if not os.path.isfile(db):
        log_fn("! no database at %s" % db)
        return False
    try:
        con = sqlite3.connect(db)
        try:
            log_fn("journal_mode:    %s"
                   % con.execute("PRAGMA journal_mode").fetchone()[0])
            log_fn("integrity_check: %s"
                   % con.execute("PRAGMA integrity_check").fetchone()[0])
            try:
                log_fn("nodes:           %s"
                       % con.execute("SELECT COUNT(*) FROM nodes"
                                     ).fetchone()[0])
            except sqlite3.OperationalError:
                log_fn("nodes:            (no nodes table)")
            tables = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "ORDER BY name")]
            log_fn("tables:          %s" % tables)
            return True
        finally:
            con.close()
    except Exception as ex:
        log_fn("! %s" % ex)
        return False


# ---------------------------------------------------------------- #
#  main.db download                                                 #
# ---------------------------------------------------------------- #

_DB_URL = "https://github.com/OpenNoorIlm/PyTorchUI/releases/download/Database/main.db"
_DB_PATHS = ("data/main.db", "main.db")


def _ensure_db(force=False, quiet=False):
    """Return the path to main.db, downloading it if missing.

    Returns None on failure.  Callers fall back to a build in that
    case.
    """
    import urllib.request

    if not force:
        for p in _DB_PATHS:
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            if size > 4096:
                if not quiet:
                    print("  using existing %s (%d bytes)" % (p, size))
                return p

    target = _DB_PATHS[0]
    parent = os.path.dirname(os.path.abspath(target))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)

    tmp = target + ".part"
    if not quiet:
        print("  downloading %s" % _DB_URL)
        print("  -> %s" % os.path.abspath(target))

    got = 0
    try:
        req = urllib.request.Request(
            _DB_URL,
            headers={"User-Agent": "PyTorchUI-fetch/1.0"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            with open(tmp, "wb") as out:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    out.write(chunk)
                    got += len(chunk)
                    if not quiet and total:
                        pct = got * 100 // total
                        sys.stderr.write(
                            "\r    %3d%%  %d / %d MB"
                            % (pct,
                               got // (1024 * 1024),
                               total // (1024 * 1024)))
                        sys.stderr.flush()
            if not quiet and total:
                sys.stderr.write("\n")
    except Exception as ex:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except Exception:
            pass
        print("  ! download failed: %s" % ex)
        return None

    if got < 4096:
        try:
            os.remove(tmp)
        except Exception:
            pass
        print("  ! downloaded file is only %d bytes; refusing" % got)
        return None

    os.replace(tmp, target)
    if not quiet:
        print("  downloaded %d bytes -> %s" % (got, target))
    return target


def _run_fetch(step, args, log_fn):
    """Step runner: download the pre-built database."""
    db = _ensure_db(force=True, quiet=getattr(args, "quiet", False))
    if db is None:
        log_fn("! download failed")
        return False
    log_fn("main.db is at %s" % db)
    return True

def _run_build(step, args, log_fn):
    libs = (args.libs or ",".join(DEFAULT_LIBS)).split(",")
    libs = [l.strip() for l in libs if l.strip()]
    cmd = [args.python, _create_py(), "--db",
           "--db-path", args.db_path]
    if args.format == "json":
        cmd = [args.python, _create_py(), "--json",
               "--json-path", args.db_path]
    elif args.format == "py":
        cmd = [args.python, _create_py(), "--format", "py"]
    if args.no_cap:
        cmd.append("--no-cap")
    elif args.cap:
        cmd += ["-c", str(args.cap)]
    if args.no_torch:
        cmd.append("--no-torch")
    if args.deep_c:
        cmd.append("--deep-c")
    if args.no_wal:
        cmd.append("--no-wal")
    if args.quiet:
        cmd.append("--quiet")
    for lib in libs:
        cmd += ["-l", lib]
    code, _ = _stream(cmd, log_fn, cwd=_root())
    return code == 0


_RUNNERS = {
    "install":    _run_install,
    "update":     _run_install,
    "register":   _run_register,
    "unregister": _run_unregister,
    "check":      _run_check,
    "list":       _run_list,
    "verify":     _run_verify,
    "build":      _run_build,
    "fetch":      _run_fetch,
}


# ------------------------------------------------------------------ #
#  CLI runner                                                         #
# ------------------------------------------------------------------ #

def run_cli(steps, args):
    prompt = _Prompter(gui=False)
    if not _preflight(steps, args, prompt):
        print("! cancelled at preflight")
        return 2

    print("Plan: %d step(s)" % len(steps))
    for i, st in enumerate(steps, 1):
        print("  %d. %s" % (i, st.label()))
    print()

    ok_count = 0
    for i, st in enumerate(steps, 1):
        print("=" * 60)
        print("[%d/%d] %s" % (i, len(steps), st.label()))
        print("=" * 60)
        runner = _RUNNERS.get(st.kind)
        if runner is None:
            print("! unknown step kind:", st.kind)
            continue
        try:
            ok = runner(st, args, log_fn=print)
        except KeyboardInterrupt:
            print()
            print("! interrupted — stopping")
            break
        if ok:
            ok_count += 1
            print("[ok] %s" % st.label())
        else:
            print("[fail] %s" % st.label())
            if args.stop_on_fail:
                print("! stopping because --stop-on-fail was given")
                break
        print()

    print("=" * 60)
    print("Done: %d / %d steps succeeded" % (ok_count, len(steps)))
    return 0 if ok_count == len(steps) else 1


# ------------------------------------------------------------------ #
#  GUI runner                                                         #
# ------------------------------------------------------------------ #

def run_gui(steps, args):
    from PyQt5.QtCore import Qt, QThread, pyqtSignal
    from PyQt5.QtWidgets import (
        QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
        QPushButton, QProgressBar, QPlainTextEdit, QMessageBox)

    app = QApplication.instance() or QApplication(sys.argv)

    # Preflight before the worker starts — running dialogs on the
    # main thread keeps Qt happy.
    prompt = _Prompter(gui=True)
    if not _preflight(steps, args, prompt):
        QMessageBox.information(
            None, "Cancelled", "Missing information.")
        return 2

    # ---- worker ---- #

    class Worker(QThread):
        log_line = pyqtSignal(str)
        step_start = pyqtSignal(int, int, str)
        step_done = pyqtSignal(int, bool)
        all_done = pyqtSignal(int, int)

        def __init__(self, steps, args):
            super().__init__()
            self.steps = steps
            self.args = args
            self._cancel = False

        def cancel(self):
            self._cancel = True

        def run(self):
            ok_count = 0
            total = len(self.steps)
            for i, st in enumerate(self.steps, 1):
                if self._cancel:
                    break
                self.step_start.emit(i, total, st.label())
                runner = _RUNNERS.get(st.kind)
                if runner is None:
                    self.log_line.emit("! unknown step: %s" % st.kind)
                    self.step_done.emit(i, False)
                    continue
                try:
                    ok = runner(st, self.args,
                                log_fn=self.log_line.emit)
                except Exception as ex:
                    self.log_line.emit("! exception: %s" % ex)
                    ok = False
                self.step_done.emit(i, ok)
                if ok:
                    ok_count += 1
            self.all_done.emit(ok_count, total)

    # ---- window ---- #

    dlg = QDialog()
    dlg.setWindowTitle("PyTorchUI Build")
    dlg.resize(860, 640)
    dlg.setStyleSheet(
        "QDialog{background:#202020;color:#DDD;}"
        "QLabel{color:#DDD;}"
        "QPushButton{background:#3C3C3C;border:1px solid #555;"
        " color:#EEE;padding:5px 14px;border-radius:3px;}"
        "QPushButton:hover{background:#4A4A4A;}"
        "QPushButton:disabled{color:#666;}")

    v = QVBoxLayout(dlg)
    v.setContentsMargins(14, 14, 14, 14)
    v.setSpacing(8)

    head = QLabel("Build plan: %d step(s)" % len(steps))
    head.setStyleSheet(
        "font-weight:600;color:#F0F0F0;font-size:13px;")
    v.addWidget(head)

    plan = QLabel(
        "<br>".join("%d. %s" % (i, st.label())
                    for i, st in enumerate(steps, 1)))
    plan.setStyleSheet("color:#8A8A8A;font-size:11px;")
    v.addWidget(plan)

    self_lbl_stage = QLabel("Ready.")
    self_lbl_stage.setStyleSheet(
        "color:#E08C4A;font-weight:600;margin-top:6px;")
    v.addWidget(self_lbl_stage)

    bar = QProgressBar()
    bar.setRange(0, len(steps))
    bar.setValue(0)
    bar.setTextVisible(False)
    bar.setFixedHeight(10)
    bar.setStyleSheet(
        "QProgressBar{background:#141414;border:1px solid #2A2A2A;"
        " border-radius:3px;}"
        "QProgressBar::chunk{background:#E08C4A;border-radius:2px;}")
    v.addWidget(bar)

    log = QPlainTextEdit()
    log.setReadOnly(True)
    log.setStyleSheet(
        "QPlainTextEdit{background:#141414;color:#DDD;"
        " border:1px solid #2A2A2A;padding:6px;"
        " font-family:'JetBrains Mono','Consolas',monospace;"
        " font-size:11px;}")
    v.addWidget(log, 1)

    row = QHBoxLayout()
    row.addStretch(1)
    b_cancel = QPushButton("Cancel")
    b_close = QPushButton("Close")
    b_close.setEnabled(False)
    row.addWidget(b_cancel)
    row.addWidget(b_close)
    v.addLayout(row)

    def _append(line):
        log.appendPlainText(str(line))
        sb = log.verticalScrollBar()
        sb.setValue(sb.maximum())

    worker = Worker(steps, args)

    def on_log(line):
        _append(line)

    def on_start(i, total, label):
        bar.setRange(0, total)
        bar.setValue(i - 1)
        self_lbl_stage.setText(
            "[%d/%d] %s" % (i, total, label))

    def on_step(i, ok):
        bar.setValue(i)

    def on_done(ok_count, total):
        bar.setValue(total)
        self_lbl_stage.setText(
            "Finished — %d / %d succeeded" % (ok_count, total))
        self_lbl_stage.setStyleSheet(
            "color:#5CB85C;font-weight:600;margin-top:6px;")
        b_cancel.setEnabled(False)
        b_close.setEnabled(True)

    worker.log_line.connect(on_log)
    worker.step_start.connect(on_start)
    worker.step_done.connect(on_step)
    worker.all_done.connect(on_done)

    def on_cancel():
        worker.cancel()
        b_cancel.setEnabled(False)
        _append("! cancelling after current step…")

    b_cancel.clicked.connect(on_cancel)
    b_close.clicked.connect(dlg.accept)

    dlg.show()
    worker.start()
    dlg.exec_()
    if worker.isRunning():
        worker.cancel()
        worker.wait(2000)
    return 0


# ------------------------------------------------------------------ #
#  CLI                                                                #
# ------------------------------------------------------------------ #

def _parser():
    p = argparse.ArgumentParser(
        prog="build.py",
        description="Build / extend PyTorchUI's node database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  build.py                                 full build\n"
            "  build.py -l torch,numpy -nc              full, no cap\n"
            "  build.py --install numpy                 install only\n"
            "  build.py --register torch                register only\n"
            "  build.py --install torch --register torch\n"
            "  build.py --update torch                  pip -U torch\n"
            "  build.py --unregister numpy              drop from DB\n"
            "  build.py --check torch                   status\n"
            "  build.py --list                          list registered\n"
            "  build.py --verify                        DB integrity\n"
            "  build.py --gui                           same, in a window\n"
        ),
    )
    # Ops — nargs='?' + const='' means "flag alone = ask me later"
    p.add_argument("--install", action="append", nargs="?", const="",
                   metavar="PKG",
                   help="pip install PKG.  No value = ask.")
    p.add_argument("--update", action="append", nargs="?", const="",
                   metavar="PKG",
                   help="pip install -U PKG.")
    p.add_argument("--register", action="append", nargs="?", const="",
                   metavar="LIB",
                   help="walk LIB and add its nodes to the DB.")
    p.add_argument("--unregister", action="append", nargs="?", const="",
                   metavar="LIB",
                   help="remove LIB's nodes from the DB.")
    p.add_argument("--check", action="append", nargs="?", const="",
                   metavar="LIB",
                   help="report installed version + node count.")
    p.add_argument("--list", action="store_true",
                   help="list every registered library.")
    p.add_argument("--verify", action="store_true",
                   help="DB integrity check.")

    # Build options (used when no op flag is given, or alongside them)
    p.add_argument("-l", "--libs", default="",
                   help="comma-separated library names for a full build")
    p.add_argument("-c", "--cap", type=int, default=2000)
    p.add_argument("-nc", "--no-cap", action="store_true")
    p.add_argument("-nt", "--no-torch", action="store_true")
    p.add_argument("--deep-c", action="store_true")
    p.add_argument("--format", choices=["db", "json", "py"],
                   default="db")
    p.add_argument("--db-path", default=None,
                   help="database path (default: main.db)")
    p.add_argument("--json-path", default=None,
                   help="JSON output path if --format json")
    p.add_argument("--no-wal", action="store_true")
    p.add_argument("--python", default=sys.executable,
                   help="interpreter for pip and create.py")
    p.add_argument("--stop-on-fail", action="store_true",
                   help="abort remaining steps on first failure")

    # Mode
    p.add_argument("--gui", action="store_true")
    p.add_argument("--cli", action="store_true")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--fetch", action="store_true",
                   help="download main.db from the release URL "
                        "instead of building it")
    return p


def main():
    args = _parser().parse_args()

    if args.json_path:
        args.db_path = args.json_path  # alias

    steps = _collect_from_args(args)

    if args.gui:
        return run_gui(steps, args)
    return run_cli(steps, args)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
start.py — cross-platform launcher for PyTorchUI.

Commands:
    run       Build if needed, then launch the editor   [default]
    build     Rebuild the node database
    clean     Remove caches, backups, stray outputs
    deps      pip install -r requirements.txt
    test      Run the test suite
    doctor    Print an environment report
    reset     clean + build + run
    convert   Convert an external Python project to a db
    help      Full help
    version   Print version

Python selection:
    start.py --choose            interactive picker
    start.py --python PATH       use a specific interpreter
    start.py --python list       list found interpreters
    start.py --show              print the current choice

The choice is stored in .pytorchui_python at the repo root and is
used by every subsequent invocation.
"""

import argparse
import datetime
import glob
import json
import os
import shutil
import subprocess
import sys
import time


ROOT = os.path.dirname(os.path.abspath(__file__))
PY_FILE = os.path.join(ROOT, ".pytorchui_python")
VERSION = "0.2.0"

COMMON_LIBS = [
    "os", "json", "math", "random", "pathlib", "shutil", "time",
    "datetime", "torch", "torchvision", "torchaudio", "transformers",
    "tensorflow", "numpy", "scipy", "pandas", "matplotlib", "sklearn",
    "pyautogui", "PIL", "cv2",
]


# ---- colours ------------------------------------------------------ #

def _enable_windows_ansi():
    if os.name != "nt":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.SetConsoleMode(k.GetStdHandle(-11), 7)
    except Exception:
        pass


def _colors():
    _enable_windows_ansi()
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return {k: "" for k in
                ("BOLD", "DIM", "RESET", "RED", "GREEN",
                 "YELLOW", "BLUE", "MAGENTA", "CYAN", "WHITE")}
    return {
        "BOLD": "\033[1m", "DIM": "\033[2m", "RESET": "\033[0m",
        "RED": "\033[31m", "GREEN": "\033[32m", "YELLOW": "\033[33m",
        "BLUE": "\033[34m", "MAGENTA": "\033[35m", "CYAN": "\033[36m",
        "WHITE": "\033[37m",
    }


C = _colors()


def step(msg):  print(f"{C['BLUE']}{C['BOLD']}==>{C['RESET']} {C['BOLD']}{msg}{C['RESET']}")
def info(msg):  print(f"    {C['CYAN']}{msg}{C['RESET']}")
def ok(msg):    print(f"    {C['GREEN']}*{C['RESET']} {msg}")
def warn(msg):  print(f"    {C['YELLOW']}!{C['RESET']} {msg}")
def err(msg):   print(f"    {C['RED']}x{C['RESET']} {msg}")
def fatal(msg): err(msg); sys.exit(1)


# ---- ASCII banner ------------------------------------------------- #

BANNER = r"""
{white}{bold}  ____        _   _____          _     _   _ ___{reset}
{white}{bold} |  _ \ _   _| |_|_   _|__  _ __| |__ | | | |_ _|{reset}
{white}{bold} | |_) | | | | __| | |/ _ \| '__| '_ \| | | || |{reset}
{white}{bold} |  __/| |_| | |_  | | (_) | |  | | | | |_| || |{reset}
{white}{bold} |_|    \__, |\__| |_|\___/|_|  |_| |_|\___/|___|{reset}
{white}{bold}        |___/{reset}  {dim}v{version}  -  visual node editor for PyTorch{reset}
"""


def banner():
    b = BANNER.format(
        white=C["WHITE"], bold=C["BOLD"], reset=C["RESET"],
        dim=C["DIM"], version=VERSION)
    print(b)


# ---- Python selection --------------------------------------------- #

def read_python_choice():
    if not os.path.isfile(PY_FILE):
        return None
    try:
        with open(PY_FILE, "r", encoding="utf-8") as f:
            path = f.read().strip()
        return path if path and os.path.isfile(path) else None
    except Exception:
        return None


def write_python_choice(path):
    try:
        with open(PY_FILE, "w", encoding="utf-8") as f:
            f.write(path)
        return True
    except Exception as ex:
        err(f"could not save choice: {ex}")
        return False


def find_pythons():
    """Return a list of (path, version, has_pyqt5)."""
    found = []
    seen = set()

    def probe(path):
        try:
            real = os.path.realpath(path)
            if real in seen:
                return
            seen.add(real)
            out = subprocess.check_output(
                [path, "-c",
                 "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
                stderr=subprocess.DEVNULL, timeout=5).decode().strip()
            has_pyqt = subprocess.run(
                [path, "-c", "import PyQt5"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=5).returncode == 0
            found.append((path, out, has_pyqt))
        except Exception:
            pass

    if os.name == "nt":
        for cmd in ("python", "python3", "py"):
            for p in shutil.which(cmd) and [shutil.which(cmd)] or []:
                probe(p)
        for v in ("310", "311", "312", "313"):
            p = shutil.which(f"python{v}")
            if p:
                probe(p)
    else:
        for pattern in (
            "/usr/bin/python3*",
            "/usr/local/bin/python3*",
            "/opt/**/bin/python3*",
            os.path.expanduser("~/.venv*/bin/python*"),
            os.path.expanduser("~/venv*/bin/python*"),
        ):
            for p in sorted(glob.glob(pattern, recursive=True)):
                if os.path.isfile(p) and os.access(p, os.X_OK):
                    probe(p)
        for cmd in ("python3", "python"):
            p = shutil.which(cmd)
            if p:
                probe(p)

    return found


def choose_python_interactive():
    pythons = find_pythons()
    if not pythons:
        fatal("no Python interpreters found")

    step("Choose a Python interpreter")
    for i, (path, ver, pyqt) in enumerate(pythons, 1):
        mark = f"{C['GREEN']}PyQt5{C['RESET']}" if pyqt else f"{C['DIM']}    {C['RESET']}"
        print(f"    {C['BOLD']}{i:>2}{C['RESET']}.  {mark}  "
              f"{C['CYAN']}{ver:<10}{C['RESET']}  {path}")

    current = read_python_choice()
    if current:
        print()
        info(f"current: {current}")

    print()
    try:
        raw = input(f"    {C['MAGENTA']}number (Enter to keep current): {C['RESET']}").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return current

    if not raw:
        return current
    try:
        idx = int(raw) - 1
        if not (0 <= idx < len(pythons)):
            fatal("out of range")
        path = pythons[idx][0]
        write_python_choice(path)
        ok(f"saved: {path}")
        return path
    except ValueError:
        # Maybe they typed a path
        if os.path.isfile(raw):
            write_python_choice(raw)
            ok(f"saved: {raw}")
            return raw
        fatal("not a number or existing path")


def get_python(cli_path=None):
    if cli_path and cli_path != "list":
        if os.path.isfile(cli_path):
            return cli_path
        p = shutil.which(cli_path)
        if p:
            return p
        fatal(f"python not found: {cli_path}")

    saved = read_python_choice()
    if saved:
        return saved

    pythons = find_pythons()
    for path, _, pyqt in pythons:
        if pyqt:
            return path
    if pythons:
        return pythons[0][0]
    return sys.executable


# ---- commands ----------------------------------------------------- #

def cmd_help():
    banner()
    print(f"""  {C['BOLD']}COMMANDS{C['RESET']}
    run       Build if needed, then launch the editor   {C['DIM']}[default]{C['RESET']}
    build     Rebuild the node database
    clean     Remove caches, backups, stray outputs
    deps      pip install -r requirements.txt
    test      Run the test suite
    doctor    Print an environment report
    reset     clean + build + run
    convert   Convert an external Python project
    help      Show this help
    version   Print version

  {C['BOLD']}PYTHON SELECTION{C['RESET']}
    --choose             pick an interpreter interactively
    --python PATH        use a specific interpreter
    --python list        list every interpreter found
    --show               print the saved choice

  {C['BOLD']}BUILD OPTIONS{C['RESET']}
    -l, --libs LIST      comma-separated libraries
    -nc, --no-cap        no node cap
    -c,  --cap N         cap per package (default 2000)
    -nt, --no-torch      skip torch
    -i,  --interactive   prompt for options
    -v,  --verbose       verbose output
    -q,  --quiet         quiet output

  {C['BOLD']}EXAMPLES{C['RESET']}
    ./start.sh                    build if needed, run
    ./start.sh --choose           change interpreter
    ./start.sh build -nc -l os,json
    ./start.sh build -i
    ./start.sh convert ~/myproj
""")


def cmd_show_python(py):
    path = read_python_choice()
    if not path:
        warn("no saved interpreter; would use: " + py)
        return
    ok(f"saved interpreter: {path}")


def cmd_list_pythons():
    pythons = find_pythons()
    if not pythons:
        warn("no interpreters found")
        return
    for path, ver, pyqt in pythons:
        flag = "PyQt5" if pyqt else "     "
        print(f"  {C['BOLD']}{flag}{C['RESET']}  {C['CYAN']}{ver:<10}{C['RESET']}  {path}")


def run_create(py, libs, cap, no_cap, no_torch, quiet, verbose):
    args = [py, os.path.join(ROOT, "create.py"), "--db"]
    if not verbose:
        args.append("--quiet")
    if no_cap:
        args.append("--no-cap")
    if cap:
        args.extend(["-c", str(cap)])
    if no_torch:
        args.append("--no-torch")
    if libs:
        for lib in [x.strip() for x in libs.split(",") if x.strip()]:
            args.extend(["-l", lib])
    return args


# ---------------------------------------------------------------- #
#  main.db download                                                 #
# ---------------------------------------------------------------- #
#
# If a pre-built database is published as a release asset, fetch it
# instead of running the walk.  The release binary is a few tens of
# megabytes; walking every installed library takes minutes.
#
# Falls back to the caller's own build path if the download fails.

_DB_URL = 'https://github.com/OpenNoorIlm/PyTorchUI/releases/download/Database/main.db'
_DB_PATHS = ("data/main.db", "main.db")


def _ensure_db(force=False, quiet=False):
    """Return the path to main.db, downloading it if missing.

    Returns None if no database could be obtained.  Callers should
    fall back to their own build path in that case.
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

    try:
        req = urllib.request.Request(
            _DB_URL,
            headers={"User-Agent": "PyTorchUI-fetch/1.0"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            got = 0
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

def cmd_build(py, args):
    if getattr(args, "fetch", False):
        db = _ensure_db(force=True, quiet=getattr(args, "quiet", False))
        if db is None:
            print("! download failed")
            return 1
        return 0
    if args.interactive:
        libs = input(f"    {C['MAGENTA']}libraries (comma-sep, blank=none): {C['RESET']}").strip()
        cap = input(f"    {C['MAGENTA']}cap [{args.cap or '2000'}]: {C['RESET']}").strip()
        if cap:
            args.cap = cap
        no_torch = input(f"    {C['MAGENTA']}skip torch? [y/N]: {C['RESET']}").strip().lower()
        if no_torch.startswith("y"):
            args.no_torch = True
        no_cap = input(f"    {C['MAGENTA']}no cap? [y/N]: {C['RESET']}").strip().lower()
        if no_cap.startswith("y"):
            args.no_cap = True
        args.libs = libs

    argv = run_create(py, args.libs, args.cap, args.no_cap,
                      args.no_torch, args.quiet, args.verbose)
    step("Building node database")
    info(" ".join(argv))
    t0 = time.monotonic()
    rc = subprocess.run(argv).returncode
    dt = time.monotonic() - t0
    if rc != 0:
        fatal(f"create.py exited with {rc}")
    ok(f"done in {dt:.1f}s")
    db = os.path.join(ROOT, "data", "main.db")
    if os.path.isfile(db):
        try:
            import sqlite3
            n = sqlite3.connect(db).execute(
                "SELECT COUNT(*) FROM nodes").fetchone()[0]
            ok(f"data/main.db: {n} nodes, {os.path.getsize(db):,} bytes")
        except Exception:
            pass


def cmd_run(py, args):
    # Try the pre-built database first; fall through to build if it fails.
    _ensure_db()
    db = os.path.join(ROOT, "data", "main.db")
    if not os.path.isfile(db):
        warn("no database; building first")
        cmd_build(py, args)
    step("Launching editor")
    return subprocess.run([py, os.path.join(ROOT, "main.py")]).returncode


def cmd_clean():
    step("Cleaning")
    n = 0
    for dirpath, dirs, files in os.walk(ROOT):
        if ".git" in dirpath.split(os.sep):
            continue
        for d in list(dirs):
            if d == "__pycache__":
                shutil.rmtree(os.path.join(dirpath, d), ignore_errors=True)
                n += 1
                dirs.remove(d)
        for f in files:
            if f.endswith(".pyc") or ".bak_" in f:
                try:
                    os.remove(os.path.join(dirpath, f))
                    n += 1
                except OSError:
                    pass
    for stray in ("autosave.json", "generated.py"):
        p = os.path.join(ROOT, stray)
        if os.path.isfile(p):
            os.remove(p)
            n += 1
    ok(f"removed {n} item(s)")


def cmd_deps(py):
    req = os.path.join(ROOT, "requirements.txt")
    if not os.path.isfile(req):
        fatal("requirements.txt not found")
    step("Installing dependencies")
    return subprocess.run([py, "-m", "pip", "install", "-r", req]).returncode


def cmd_test(py):
    step("Running tests")
    tests = os.path.join(ROOT, "tests")
    if os.path.isdir(tests):
        return subprocess.run([py, "-m", "pytest", tests, "-q"]).returncode
    if os.path.isfile(os.path.join(ROOT, "test.py")):
        return subprocess.run([py, os.path.join(ROOT, "test.py")]).returncode
    warn("no tests found")
    return 1


def cmd_doctor(py):
    step("Environment report")
    print()
    info(f"OS:          {sys.platform}")
    info(f"Python:      {sys.version.split()[0]}  ({py})")
    info(f"Repository:  {ROOT}")
    print()
    info("Interpreter probe:")
    for mod in ("PyQt5", "matplotlib", "torch", "numpy", "PIL",
                "pyautogui", "Xlib"):
        r = subprocess.run([py, "-c", f"import {mod}"],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        mark = f"{C['GREEN']}yes{C['RESET']}" if r.returncode == 0 \
               else f"{C['RED']}no {C['RESET']}"
        print(f"      {mark}  {mod}")
    print()
    db = os.path.join(ROOT, "data", "main.db")
    if os.path.isfile(db):
        try:
            import sqlite3
            con = sqlite3.connect(db)
            n = con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            fts = bool(con.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='node_search'").fetchone())
            info(f"Database:    {n} nodes, FTS5 {'yes' if fts else 'no'}")
        except Exception as ex:
            warn(f"Database unreadable: {ex}")
    else:
        warn("Database:    none (run ./start.sh build)")


def cmd_convert(py, args):
    # Lazy import; the dialog lives in the app.
    sys.path.insert(0, ROOT)
    try:
        from helpers.Nodes.Nodes import API
    except ImportError:
        fatal("could not import helpers.Nodes.Nodes")
    app = API.instance()
    win = app.window
    win.show()
    try:
        win._open_convert_dialog(py, args.folder or "")
    except Exception as ex:
        fatal(f"convert dialog failed: {ex}")
    return app.app().exec_()


# ---- argparse ----------------------------------------------------- #

def build_parser():
    p = argparse.ArgumentParser(
        prog="start.py", add_help=False,
        description="PyTorchUI cross-platform launcher")
    p.add_argument("command", nargs="?", default="run")
    p.add_argument("-l", "--libs", default="")
    p.add_argument("-nc", "--no-cap", action="store_true")
    p.add_argument("-c", "--cap", default="")
    p.add_argument("-nt", "--no-torch", action="store_true")
    p.add_argument("-i", "--interactive", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--python", default="")
    p.add_argument("--choose", action="store_true")
    p.add_argument("--show", action="store_true")
    p.add_argument("--folder", default="")
    p.add_argument("-h", "--help", action="store_true")
    p.add_argument("--version", action="store_true")
    p.add_argument("--fetch", action="store_true",
                   help="download main.db from the release URL "
                        "instead of building it")
    return p


def main():
    p = build_parser()
    args, _ = p.parse_known_args()

    if args.version:
        print(VERSION); return 0
    if args.help or args.command == "help":
        cmd_help(); return 0

    if args.python == "list":
        cmd_list_pythons(); return 0

    if args.choose:
        banner()
        py = choose_python_interactive()
        if py:
            ok("interpreter saved; run ./start.sh to use it")
        return 0

    py = get_python(args.python or None)

    if args.show:
        cmd_show_python(py); return 0

    cmd = args.command
    if cmd in ("run", ""):
        banner()
        return cmd_run(py, args)
    if cmd == "build":
        banner()
        cmd_build(py, args); return 0
    if cmd == "clean":
        banner()
        cmd_clean(); return 0
    if cmd == "deps":
        banner()
        return cmd_deps(py)
    if cmd == "test":
        banner()
        return cmd_test(py)
    if cmd == "doctor":
        banner()
        cmd_doctor(py); return 0
    if cmd == "reset":
        banner()
        cmd_clean()
        print()
        cmd_build(py, args)
        print()
        return cmd_run(py, args)
    if cmd == "convert":
        return cmd_convert(py, args)

    fatal(f"unknown command: {cmd}")


if __name__ == "__main__":
    sys.exit(main() or 0)

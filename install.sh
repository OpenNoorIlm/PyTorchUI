#!/usr/bin/env bash
#
# PyTorchUI installer for Linux and macOS.
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/OpenNoorIlm/PyTorchUI/main/install.sh | bash
#   curl -sSL ... | bash -s -- --dir ~/my-pytorchui
#   bash install.sh
#
set -eu

REPO_URL="${PYTORCHUI_REPO:-https://github.com/OpenNoorIlm/PyTorchUI.git}"
BRANCH="${PYTORCHUI_BRANCH:-main}"
DEFAULT_DIR="${HOME}/PyTorchUI"
INSTALL_DIR=""
DO_VENV=1
DO_BUILD=1
DO_LAUNCH=0
QUIET=0

while [ $# -gt 0 ]; do
    case "$1" in
        --dir)       INSTALL_DIR="$2"; shift 2 ;;
        --branch)    BRANCH="$2"; shift 2 ;;
        --no-venv)   DO_VENV=0; shift ;;
        --no-build)  DO_BUILD=0; shift ;;
        --launch)    DO_LAUNCH=1; shift ;;
        --quiet)     QUIET=1; shift ;;
        -h|--help)
            cat <<'HELP_EOF'
PyTorchUI installer

Usage:
    install.sh [OPTIONS]

Options:
    --dir PATH       Where to install (default: ~/PyTorchUI)
    --branch NAME    Git branch (default: main)
    --no-venv        Do not create a virtualenv
    --no-build       Skip the initial node-database build
    --launch         Launch the editor after install
    --quiet          Minimal output
    -h, --help       Show this message
HELP_EOF
            exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

if [ -t 1 ] && command -v tput >/dev/null 2>&1; then
    BOLD=$(tput bold); DIM=$(tput dim); RESET=$(tput sgr0)
    RED=$(tput setaf 1); GREEN=$(tput setaf 2); YELLOW=$(tput setaf 3)
    CYAN=$(tput setaf 6)
else
    BOLD=""; DIM=""; RESET=""; RED=""; GREEN=""; YELLOW=""; CYAN=""
fi

step()  { [ "$QUIET" -eq 1 ] || printf "%s==>%s %s%s%s\n" "$CYAN" "$RESET" "$BOLD" "$*" "$RESET"; }
info()  { [ "$QUIET" -eq 1 ] || printf "    %s\n" "$*"; }
ok()    { [ "$QUIET" -eq 1 ] || printf "    %s*%s %s\n" "$GREEN" "$RESET" "$*"; }
warn()  { printf "    %s!%s %s\n" "$YELLOW" "$RESET" "$*" >&2; }
fail()  { printf "    %sx%s %s\n" "$RED" "$RESET" "$*" >&2; exit 1; }

if [ -r /dev/tty ] && [ -w /dev/tty ]; then
    HAS_TTY=1
else
    HAS_TTY=0
fi

step "Detecting platform"
UNAME="$(uname -s 2>/dev/null || echo unknown)"
case "$UNAME" in
    Linux*)   OS=linux ;;
    Darwin*)  OS=macos ;;
    *)        fail "Unsupported OS: $UNAME.  Use install.ps1 on Windows." ;;
esac
info "OS: $OS"

step "Looking for Python 3.9+"
PYTHON_BIN=""
for cmd in python3.13 python3.12 python3.11 python3.10 python3.9 python3 python; do
    if command -v "$cmd" >/dev/null 2>&1; then
        if "$cmd" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
            PYTHON_BIN="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    fail "No Python 3.9 or newer found.
    Linux:   sudo apt install python3 python3-venv python3-pip
    macOS:   brew install python@3.12
    Or download:  https://www.python.org/downloads/"
fi
info "Found: $PYTHON_BIN  ($($PYTHON_BIN --version 2>&1))"

step "Checking for git"
if ! command -v git >/dev/null 2>&1; then
    fail "git is not installed.
    Linux:  sudo apt install git
    macOS:  xcode-select --install   (or: brew install git)"
fi
info "git: $(git --version)"

if [ -z "$INSTALL_DIR" ]; then
    INSTALL_DIR="$DEFAULT_DIR"
fi

step "Install directory: $INSTALL_DIR"

if [ -e "$INSTALL_DIR" ]; then
    if [ -d "$INSTALL_DIR/.git" ]; then
        info "Existing checkout found.  Will update in place."
        EXISTING=1
    elif [ -z "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
        info "Directory exists and is empty."
        EXISTING=0
    else
        fail "Directory exists and is not empty, and is not a git checkout:
    $INSTALL_DIR
    Move it aside, or pass --dir to install elsewhere."
    fi
else
    EXISTING=0
fi

if [ "$EXISTING" -eq 1 ]; then
    step "Updating existing checkout"
    ( cd "$INSTALL_DIR" && git fetch origin "$BRANCH" && git checkout "$BRANCH" && git pull --ff-only origin "$BRANCH" ) \
        || fail "git pull failed"
    ok "Updated $INSTALL_DIR"
else
    step "Cloning $REPO_URL"
    git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$INSTALL_DIR" \
        || fail "git clone failed"
    ok "Cloned to $INSTALL_DIR"
fi

cd "$INSTALL_DIR"

PYEXEC="$PYTHON_BIN"

if [ "$DO_VENV" -eq 1 ]; then
    step "Creating virtual environment"
    if [ ! -d ".venv" ]; then
        "$PYTHON_BIN" -m venv .venv || fail "venv creation failed.
    On Debian/Ubuntu you may need:  sudo apt install python3-venv"
        ok "Created .venv"
    else
        info ".venv already exists"
    fi
    PYEXEC="$INSTALL_DIR/.venv/bin/python"
    if [ ! -x "$PYEXEC" ]; then
        fail "venv python not found at $PYEXEC"
    fi
    info "Using $PYEXEC"
fi

printf "%s" "$PYEXEC" > .pytorchui_python

step "Installing dependencies"
"$PYEXEC" -m pip install --upgrade pip >/dev/null 2>&1 || true

if [ -f requirements.txt ]; then
    "$PYEXEC" -m pip install -r requirements.txt || fail "pip install failed"
    ok "Requirements installed"
else
    warn "No requirements.txt, installing the essentials directly"
    "$PYEXEC" -m pip install PyQt5 matplotlib || fail "pip install failed"
fi

if ! "$PYEXEC" -c "import PyQt5" 2>/dev/null; then
    fail "PyQt5 is not importable in the chosen Python.
    Try:  $PYEXEC -m pip install PyQt5"
fi
ok "PyQt5 imports OK"

if [ "$DO_BUILD" -eq 1 ]; then
    step "Building the node database"
    info "This walks every installed library and takes a few minutes."
    if [ -f build.py ]; then
        "$PYEXEC" build.py -q || warn "build.py exited non-zero — you can retry with 'python build.py'"
    elif [ -f create.py ]; then
        "$PYEXEC" create.py --db --quiet || warn "create.py exited non-zero"
    else
        warn "Neither build.py nor create.py found"
    fi
    if [ -f main.db ] || [ -f data/main.db ]; then
        ok "Database built"
    else
        warn "No database file found — the editor will start with an empty library."
    fi
fi

step "Installing shell wrappers"
chmod +x start.sh 2>/dev/null || true
chmod +x build.py 2>/dev/null || true
chmod +x create.py 2>/dev/null || true
chmod +x start.py 2>/dev/null || true
ok "Wrappers marked executable"

if [ "$OS" = "linux" ]; then
    if [ -f requirements.txt ] && grep -qi pyautogui requirements.txt; then
        if ! id -nG 2>/dev/null | tr ' ' '\n' | grep -qx input; then
            warn "Not a member of the 'input' group."
            info "For pyautogui key capture:  sudo usermod -a -G input \$USER"
            info "Then log out and back in.  The editor works without this."
        fi
    fi
fi

printf "\n"
printf "%s%sInstalled%s\n" "$BOLD" "$GREEN" "$RESET"
printf "\n"
printf "  Directory:    %s\n" "$INSTALL_DIR"
printf "  Interpreter:  %s\n" "$PYEXEC"
printf "\n"
printf "  To launch:\n"
printf "      cd %s\n" "$INSTALL_DIR"
printf "      ./start.sh\n"
printf "\n"

if [ "$DO_LAUNCH" -eq 1 ]; then
    step "Launching the editor"
    exec "$PYEXEC" main.py
fi

"""
LibraryDialogs.py — install, register, update, unregister libraries
from inside the editor.
"""

import os
import sqlite3
import subprocess
import sys

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QPlainTextEdit, QMessageBox, QAbstractItemView,
    QWidget,
)


POPULAR = [
    ("torch",           "torch"),
    ("torchvision",     "torchvision"),
    ("torchaudio",      "torchaudio"),
    ("tensorflow",      "tensorflow"),
    ("transformers",    "transformers"),
    ("numpy",           "numpy"),
    ("scipy",           "scipy"),
    ("pandas",          "pandas"),
    ("matplotlib",      "matplotlib"),
    ("sklearn",         "scikit-learn"),
    ("PIL",             "Pillow"),
    ("cv2",             "opencv-python"),
    ("requests",        "requests"),
    ("httpx",           "httpx"),
    ("aiohttp",         "aiohttp"),
    ("bs4",             "beautifulsoup4"),
    ("lxml",            "lxml"),
    ("yaml",            "PyYAML"),
    ("sqlalchemy",      "SQLAlchemy"),
    ("flask",           "Flask"),
    ("fastapi",         "fastapi"),
    ("django",          "Django"),
    ("PyQt5",           "PyQt5"),
    ("PySide6",         "PySide6"),
    ("pyautogui",       "pyautogui"),
    ("pyperclip",       "pyperclip"),
    ("pynput",          "pynput"),
    ("serial",          "pyserial"),
    ("sounddevice",     "sounddevice"),
    ("librosa",         "librosa"),
    ("moviepy",         "moviepy"),
    ("imageio",         "imageio"),
    ("plotly",          "plotly"),
    ("seaborn",         "seaborn"),
    ("streamlit",       "streamlit"),
    ("gradio",          "gradio"),
    ("xgboost",         "xgboost"),
    ("lightgbm",        "lightgbm"),
    ("statsmodels",     "statsmodels"),
    ("sympy",           "sympy"),
    ("networkx",        "networkx"),
    ("nltk",            "nltk"),
    ("spacy",           "spacy"),
    ("datasets",        "datasets"),
    ("accelerate",      "accelerate"),
    ("peft",            "peft"),
    ("diffusers",       "diffusers"),
    ("safetensors",     "safetensors"),
    ("huggingface_hub", "huggingface-hub"),
    ("tqdm",            "tqdm"),
    ("rich",            "rich"),
    ("click",           "click"),
    ("typer",           "typer"),
    ("pytest",          "pytest"),
    ("black",           "black"),
    ("ruff",            "ruff"),
    ("mypy",            "mypy"),
    ("openai",          "openai"),
    ("anthropic",       "anthropic"),
    ("langchain",       "langchain"),
    ("faiss",           "faiss-cpu"),
    ("pyarrow",         "pyarrow"),
    ("polars",          "polars"),
    ("duckdb",          "duckdb"),
    ("dask",            "dask"),
    ("ray",             "ray"),
    ("joblib",          "joblib"),
]


def _find_root():
    root = os.path.dirname(os.path.abspath(__file__))
    while not os.path.isfile(os.path.join(root, "create.py")):
        parent = os.path.dirname(root)
        if parent == root:
            return None
        root = parent
    return root


def _pypi_of(import_name):
    for k, v in POPULAR:
        if k == import_name:
            return v
    return import_name


def installed_version(import_name):
    try:
        from importlib.metadata import version, PackageNotFoundError
    except ImportError:
        return None
    pypi = _pypi_of(import_name)
    try:
        return version(pypi)
    except PackageNotFoundError:
        return None
    except Exception:
        return None


def registered_count(db_path, import_name):
    if not os.path.isfile(db_path):
        return 0
    try:
        con = sqlite3.connect(db_path)
        try:
            row = con.execute(
                "SELECT COUNT(*) FROM nodes WHERE "
                "category = ? OR category LIKE ?",
                (import_name, import_name + "/%")).fetchone()
            return int(row[0]) if row else 0
        finally:
            con.close()
    except Exception:
        return 0


# ================================================================== #
#  PyPI access                                                        #
# ================================================================== #
#
# pip search has been disabled since December 2020.  We do NOT call
# it.  Two endpoints are used instead:
#
#   * https://pypi.org/pypi/<name>/json    metadata for one package
#   * https://pypi.org/search/?q=<query>   HTML search page
#
# The HTML page is parsed with regex.  It is rate-limited, so all
# queries go through _pypi_throttle() which enforces at least 1 s
# between requests and backs off for 60 s on HTTP 429.
#
# Results are cached at ~/.pytorchui/pypi_cache.json for 24 h.

import json as _json
import re as _re
import time as _time
import urllib.error
import urllib.parse
import urllib.request


CACHE_PATH = os.path.join(
    os.path.expanduser("~"), ".pytorchui", "pypi_cache.json")
CACHE_TTL = 24 * 3600
HTTP_TIMEOUT = 20


_pypi_cache = None
_pypi_last_request = [0.0]
_pypi_backoff_until = [0.0]


def _load_cache():
    global _pypi_cache
    if _pypi_cache is not None:
        return _pypi_cache
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            _pypi_cache = _json.load(f)
    except Exception:
        _pypi_cache = {}
    return _pypi_cache


def _save_cache():
    if _pypi_cache is None:
        return
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(_pypi_cache, f)
        os.replace(tmp, CACHE_PATH)
    except Exception:
        pass


def _cache_get(key):
    c = _load_cache()
    item = c.get(key)
    if not item:
        return None
    if _time.time() - item.get("ts", 0) > CACHE_TTL:
        return None
    return item.get("data")


def _cache_put(key, data):
    c = _load_cache()
    c[key] = {"ts": _time.time(), "data": data}
    _save_cache()


def _pypi_throttle():
    """Enforce >= 1 s between requests and honour backoff."""
    now = _time.time()
    if now < _pypi_backoff_until[0]:
        wait = _pypi_backoff_until[0] - now
        if wait > 0:
            _time.sleep(wait)
    gap = now - _pypi_last_request[0]
    if gap < 1.0:
        _time.sleep(1.0 - gap)
    _pypi_last_request[0] = _time.time()


def _http_get(url):
    """Return (status, body_bytes) or (status, None) on transport error."""
    _pypi_throttle()
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "PyTorchUI-LibraryManager/1.0 "
                          "(https://github.com/OpenNoorIlm/PyTorchUI)",
            "Accept": "application/json, text/html;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return resp.getcode(), resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 429:
            # Be polite: back off for a minute.
            _pypi_backoff_until[0] = _time.time() + 60
            print("[pypi] 429 rate-limited; backing off 60 s")
        return e.code, None
    except Exception as e:
        print("[pypi] transport error:", e)
        return 0, None


def pypi_lookup(name):
    """Return metadata for a package, or None.

    Uses https://pypi.org/pypi/<name>/json.  Cached 24 h.
    """
    name = (name or "").strip()
    if not name:
        return None
    key = "lookup:" + name.lower()
    cached = _cache_get(key)
    if cached is not None:
        return cached
    url = "https://pypi.org/pypi/%s/json" % urllib.parse.quote(name)
    status, body = _http_get(url)
    if status != 200 or not body:
        _cache_put(key, None)
        return None
    try:
        d = _json.loads(body.decode("utf-8"))
    except Exception:
        _cache_put(key, None)
        return None
    info = d.get("info", {})
    data = {
        "name":        info.get("name", name),
        "version":     info.get("version", ""),
        "summary":     info.get("summary", ""),
        "home_page":   info.get("home_page", "")
                       or (info.get("project_urls") or {}).get("Homepage", ""),
        "author":      info.get("author", ""),
        "license":     info.get("license", ""),
        "requires_py": info.get("requires_python", ""),
    }
    _cache_put(key, data)
    return data


_SEARCH_ROW = _re.compile(
    r'<a\s+href="/project/([^/]+)/"[^>]*class="package-snippet"[^>]*>'
    r'(.*?)</a>',
    _re.DOTALL | _re.IGNORECASE)

_SEARCH_NAME = _re.compile(
    r'<span\s+class="package-snippet__name">([^<]+)</span>')

_SEARCH_VERSION = _re.compile(
    r'<span\s+class="package-snippet__version">([^<]+)</span>')

_SEARCH_DESC = _re.compile(
    r'<p\s+class="package-snippet__description">([^<]*)</p>',
    _re.DOTALL)


def pypi_search(query, limit=30):
    """Return a list of {name, version, summary} for a search query.

    Uses https://pypi.org/search/?q=<query>.  Results cached 24 h.
    On 429 or transport error, returns [].
    """
    query = (query or "").strip()
    if not query:
        return []
    key = "search:" + query.lower()
    cached = _cache_get(key)
    if cached is not None:
        return cached[:limit]

    url = ("https://pypi.org/search/?q="
           + urllib.parse.quote_plus(query))
    status, body = _http_get(url)
    if status != 200 or not body:
        _cache_put(key, [])
        return []

    html = body.decode("utf-8", errors="replace")
    results = []

    # Split into package-snippet blocks and pull fields out of each.
    for m in _SEARCH_ROW.finditer(html):
        name = m.group(1)
        block = m.group(2)
        v = _SEARCH_VERSION.search(block)
        d = _SEARCH_DESC.search(block)
        results.append({
            "name":    name,
            "version": v.group(1).strip() if v else "",
            "summary": d.group(1).strip() if d else "",
        })
        if len(results) >= limit:
            break

    _cache_put(key, results)
    return results


# ---- worker for async search ---- #

try:
    from PyQt5.QtCore import QThread, pyqtSignal

    class _PyPIWorker(QThread):
        done = pyqtSignal(str, object)   # kind, payload

        def __init__(self, kind, arg, parent=None):
            super().__init__(parent)
            self.kind = kind             # "search" | "lookup"
            self.arg = arg

        def run(self):
            try:
                if self.kind == "search":
                    self.done.emit("search", pypi_search(self.arg))
                else:
                    self.done.emit("lookup", pypi_lookup(self.arg))
            except Exception as ex:
                print("[pypi] worker error:", ex)
                self.done.emit(self.kind, None)
except ImportError:
    _PyPIWorker = None


class LibraryManagerDialog(QDialog):

    def __init__(self, parent=None, db_path="main.db"):
        super().__init__(parent)
        self.setWindowTitle("Library Manager")
        self.setModal(True)
        self.resize(920, 640)
        self._custom = []

        if not os.path.isabs(db_path):
            root = _find_root()
            if root:
                db_path = os.path.join(root, db_path)
        self._db_path = db_path

        v = QVBoxLayout(self)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(8)

        head = QLabel("Library Manager")
        head.setStyleSheet(
            "font-weight:600;font-size:13px;color:#F0F0F0;")
        v.addWidget(head)

        sub = QLabel(
            "Install packages with pip, register them into the node "
            "database, and update or remove them.  pip runs in the "
            "same Python that launched the editor.\n\n"
            "Database: " + self._db_path)
        sub.setStyleSheet("color:#8A8A8A;font-size:11px;")
        sub.setWordWrap(True)
        v.addWidget(sub)

        row = QHBoxLayout()
        row.addWidget(QLabel("Search:"))
        self.edit_search = QLineEdit()
        self.edit_search.setPlaceholderText(
            "filter by name  (type to search)")
        self.edit_search.textChanged.connect(self._filter)
        row.addWidget(self.edit_search, 1)
        self.chk_registered_only = QCheckBox(
            "Show only registered")
        self.chk_registered_only.setStyleSheet("color:#DDD;")
        self.chk_registered_only.toggled.connect(self._reload)
        row.addWidget(self.chk_registered_only)
        b_refresh = QPushButton("Refresh")
        b_refresh.clicked.connect(self._reload)
        row.addWidget(b_refresh)
        v.addLayout(row)

        self.lbl_db_status = QLabel("")
        self.lbl_db_status.setStyleSheet(
            "color:#8A8A8A;font-size:11px;padding:2px 0;")
        v.addWidget(self.lbl_db_status)

        row = QHBoxLayout()
        row.addWidget(QLabel("Custom:"))
        self.edit_custom = QLineEdit()
        self.edit_custom.setPlaceholderText(
            "add a package not in the list, e.g.  my-lib")
        row.addWidget(self.edit_custom, 1)
        b_add = QPushButton("Add to list")
        b_add.clicked.connect(self._add_custom)
        row.addWidget(b_add)
        v.addLayout(row)

        # ---- PyPI search ---- #
        row = QHBoxLayout()
        row.addWidget(QLabel("PyPI search:"))
        self.edit_pypi = QLineEdit()
        self.edit_pypi.setPlaceholderText(
            "search pypi.org \u2014 e.g.  image processing")
        self.edit_pypi.returnPressed.connect(self._pypi_search)
        row.addWidget(self.edit_pypi, 1)
        b_search = QPushButton("Search PyPI")
        b_search.clicked.connect(self._pypi_search)
        row.addWidget(b_search)
        b_lookup = QPushButton("Look up exact name")
        b_lookup.clicked.connect(self._pypi_lookup_exact)
        row.addWidget(b_lookup)
        v.addLayout(row)

        self.lbl_pypi_status = QLabel(
            "PyPI search uses pypi.org directly.  pip search is "
            "disabled on PyPI and is not used here.")
        self.lbl_pypi_status.setStyleSheet(
            "color:#6E6E6E;font-size:10px;")
        v.addWidget(self.lbl_pypi_status)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([
            "Library", "PyPI", "Version",
            "Registered", "Actions"])
        h = self.table.horizontalHeader()
        for c in (0, 1, 2, 3):
            h.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setStyleSheet(
            "QTableWidget{background:#141414;color:#DDD;"
            " border:1px solid #2A2A2A;gridline-color:#2A2A2A;}"
            "QHeaderView::section{background:#2A2A2A;color:#DDD;"
            " padding:4px;border:0;}")
        v.addWidget(self.table, 1)

        v.addWidget(QLabel("Log"))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(120)
        self.log.setStyleSheet(
            "QPlainTextEdit{background:#141414;color:#DDD;"
            " border:1px solid #2A2A2A;padding:6px;"
            " font-family:'JetBrains Mono','Consolas',monospace;"
            " font-size:11px;}")
        v.addWidget(self.log)

        row = QHBoxLayout()
        row.addStretch(1)
        b_close = QPushButton("Close")
        b_close.clicked.connect(self.accept)
        row.addWidget(b_close)
        v.addLayout(row)

        self._populate()

    def _known(self):
        return POPULAR + self._custom

    def _reload(self, *_):
        self._populate()

    def _populate(self):
        self.table.setRowCount(0)
        only_reg = False
        try:
            only_reg = self.chk_registered_only.isChecked()
        except Exception:
            pass
        for import_name, pypi in self._known():
            n = registered_count(self._db_path, import_name)
            if only_reg and not n:
                continue
            self._add_row(import_name, pypi)
        self._filter(self.edit_search.text())
        self._refresh_status_bar()

    def _refresh_status_bar(self):
        try:
            total = 0
            if os.path.isfile(self._db_path):
                con = sqlite3.connect(self._db_path)
                try:
                    total = con.execute(
                        "SELECT COUNT(*) FROM nodes").fetchone()[0]
                except Exception:
                    total = -1
                finally:
                    con.close()
            libs = 0
            try:
                for import_name, _ in self._known():
                    if registered_count(self._db_path, import_name):
                        libs += 1
            except Exception:
                pass
            exists = "yes" if os.path.isfile(self._db_path) else "NO"
            self.lbl_db_status.setText(
                "Database: %s (exists: %s, %s nodes, %d library/ies "
                "registered)"
                % (self._db_path, exists,
                   total if total >= 0 else "?", libs))
        except Exception as ex:
            try:
                self.lbl_db_status.setText("status error: %s" % ex)
            except Exception:
                pass

    def _add_row(self, import_name, pypi):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(import_name))
        self.table.setItem(r, 1, QTableWidgetItem(pypi or "(stdlib)"))

        ver = installed_version(import_name) if pypi else "builtin"
        self.table.setItem(r, 2, QTableWidgetItem(ver or "-"))

        n = registered_count(self._db_path, import_name)
        self.table.setItem(r, 3, QTableWidgetItem(str(n) if n else "-"))

        cell = QWidget()
        h = QHBoxLayout(cell)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)

        if pypi:
            b = QPushButton("Update" if ver else "Install")
            b.clicked.connect(
                lambda _c=False, i=import_name, p=pypi, u=bool(ver):
                    self._install(i, p, u))
            h.addWidget(b)

        b = QPushButton("Re-register" if n else "Register")
        b.clicked.connect(lambda _c=False, i=import_name:
                          self._register(i))
        h.addWidget(b)

        if n:
            b = QPushButton("Unregister")
            b.clicked.connect(lambda _c=False, i=import_name:
                              self._unregister(i))
            h.addWidget(b)

        self.table.setCellWidget(r, 4, cell)

    def _filter(self, text):
        text = (text or "").strip().lower()
        shown = 0
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it is None:
                continue
            match = (not text) or (text in it.text().lower())
            self.table.setRowHidden(r, not match)
            if match:
                shown += 1
        if text:
            try:
                self.lbl_db_status.setText(
                    "filter %r: %d row(s) shown"
                    % (text, shown))
            except Exception:
                pass

    # ---- PyPI integration ---- #

    def _pypi_search(self):
        q = self.edit_pypi.text().strip()
        if not q:
            return
        self._pypi_set_status("Searching PyPI for \u201c%s\u201d\u2026"
                              % q)
        self._pypi_run("search", q)

    def _pypi_lookup_exact(self):
        q = self.edit_custom.text().strip() or self.edit_pypi.text().strip()
        if not q:
            return
        self._pypi_set_status("Looking up \u201c%s\u201d\u2026" % q)
        self._pypi_run("lookup", q)

    def _pypi_set_status(self, text):
        try:
            self.lbl_pypi_status.setText(text)
        except Exception:
            pass

    def _pypi_run(self, kind, arg):
        if _PyPIWorker is None:
            self._pypi_set_status(
                "PyQt5 QThread unavailable \u2014 cannot search")
            return
        # Disable the buttons while a request is in flight.
        self._pypi_set_busy(True)
        w = _PyPIWorker(kind, arg, self)
        self._pypi_worker = w
        w.done.connect(self._pypi_done)
        w.start()

    def _pypi_set_busy(self, busy):
        try:
            self.table.setEnabled(not busy)
        except Exception:
            pass

    def _pypi_done(self, kind, payload):
        self._pypi_set_busy(False)
        if kind == "search":
            self._pypi_show_search(payload or [])
        elif kind == "lookup":
            self._pypi_show_lookup(payload)

    def _pypi_show_search(self, results):
        if not results:
            self._pypi_set_status(
                "No results.  pypi.org may be rate-limiting; try "
                "again in a minute, or use \u201cLook up exact name\u201d.")
            return
        # Show results in a small picker.
        from PyQt5.QtWidgets import QDialog, QListWidget, QListWidgetItem
        dlg = QDialog(self)
        dlg.setWindowTitle("PyPI search \u2014 %d result(s)" % len(results))
        dlg.resize(700, 460)
        dlg.setStyleSheet(
            "QDialog{background:#202020;color:#DDD;}"
            "QListWidget{background:#141414;color:#DDD;"
            " border:1px solid #2A2A2A;padding:4px;}"
            "QListWidget::item{padding:6px 8px;}"
            "QListWidget::item:selected{background:#E08C4A;color:#1A1A1A;}")
        vv = QVBoxLayout(dlg)
        vv.setContentsMargins(12, 12, 12, 12)
        vv.addWidget(QLabel("Double-click a package to add it."))
        lst = QListWidget()
        for r in results:
            txt = "%s  %s\n%s" % (r["name"], r["version"] or "",
                                   r["summary"] or "")
            it = QListWidgetItem(txt)
            it.setData(Qt.UserRole, r["name"])
            lst.addItem(it)
        lst.setCurrentRow(0)
        vv.addWidget(lst, 1)
        row2 = QHBoxLayout()
        row2.addStretch(1)
        b_ok = QPushButton("Add selected")
        b_cancel = QPushButton("Cancel")
        row2.addWidget(b_cancel)
        row2.addWidget(b_ok)
        vv.addLayout(row2)

        def _accept():
            it = lst.currentItem()
            if it is None:
                return
            name = it.data(Qt.UserRole)
            self.edit_custom.setText(name)
            self._add_custom()
            dlg.accept()

        b_ok.clicked.connect(_accept)
        lst.itemDoubleClicked.connect(lambda _it: _accept())
        b_cancel.clicked.connect(dlg.reject)
        dlg.exec_()
        self._pypi_set_status(
            "Added selected package to the custom list.")

    def _pypi_show_lookup(self, info):
        if not info:
            self._pypi_set_status(
                "Package not found on PyPI.")
            return
        name = info.get("name", "")
        ver = info.get("version", "")
        summary = info.get("summary", "")
        self.edit_custom.setText(name)
        self._add_custom()
        self._pypi_set_status(
            "Found %s %s \u2014 %s"
            % (name, ver, summary[:80] if summary else ""))


    def _add_custom(self):
        name = self.edit_custom.text().strip()
        if not name:
            return
        self._custom.append((name, name))
        self.edit_custom.clear()
        self._populate()

    def _log(self, line):
        self.log.appendPlainText(str(line))
        sb = self.log.verticalScrollBar()
        sb.setValue(sb.maximum())
        QApplication.processEvents()

    def _run(self, cmd, label, cwd=None):
        self._log("")
        self._log("== %s ==" % label)
        self._log("$ " + " ".join(cmd))
        if cwd:
            self._log("  (cwd: %s)" % cwd)
        try:
            p = subprocess.Popen(
                cmd, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
                cwd=cwd)
        except Exception as ex:
            self._log("! failed to start: %s" % ex)
            self._populate()
            return 1
        any_output = False
        try:
            for line in iter(p.stdout.readline, ""):
                any_output = True
                self._log(line.rstrip("\n"))
        except Exception as ex:
            self._log("! read failed: %s" % ex)
        code = p.wait()
        if not any_output:
            self._log("(subprocess produced no output)")
        self._log("-> exit %d" % code)
        self._populate()
        return code

    def _install(self, import_name, pypi, update):
        cmd = [sys.executable, "-m", "pip", "install"]
        if update:
            cmd.append("--upgrade")
        cmd.append(pypi)
        self._run(cmd, "pip install")

    def _register(self, import_name):
        root = _find_root()
        if not root:
            self._log("! could not find PyTorchUI root")
            return
        cmd = [sys.executable, os.path.join(root, "create.py"),
               "--db", "--no-torch",
               "--db-path", self._db_path,
               "--register-lib", import_name]
        code = self._run(cmd, "register %s" % import_name, cwd=root)
        if code == 0:
            n = registered_count(self._db_path, import_name)
            self._log("registered_count(%s) = %d"
                      % (import_name, n))

    def _unregister(self, import_name):
        root = _find_root()
        if not root:
            self._log("! could not find PyTorchUI root")
            return
        if QMessageBox.question(
                self, "Unregister",
                "Delete all nodes for '%s' from the database?"
                % import_name) != QMessageBox.Yes:
            return
        cmd = [sys.executable, os.path.join(root, "create.py"),
               "--db", "--no-torch",
               "--db-path", self._db_path,
               "--unregister-lib", import_name]
        code = self._run(cmd, "unregister %s" % import_name, cwd=root)
        if code == 0:
            n = registered_count(self._db_path, import_name)
            self._log("remaining nodes for %s: %d"
                      % (import_name, n))

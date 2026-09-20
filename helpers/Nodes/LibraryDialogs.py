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
        self.edit_search.setPlaceholderText("filter by name")
        self.edit_search.textChanged.connect(self._filter)
        row.addWidget(self.edit_search, 1)
        b_refresh = QPushButton("Refresh")
        b_refresh.clicked.connect(self._reload)
        row.addWidget(b_refresh)
        v.addLayout(row)

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

    def _reload(self):
        self._populate()

    def _populate(self):
        self.table.setRowCount(0)
        for import_name, pypi in self._known():
            self._add_row(import_name, pypi)
        self._filter(self.edit_search.text())

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
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it is None:
                continue
            self.table.setRowHidden(
                r, bool(text) and text not in it.text().lower())

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

    def _run(self, cmd, label):
        self._log("$ " + " ".join(cmd))
        try:
            p = subprocess.Popen(
                cmd, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1)
        except Exception as ex:
            self._log("! %s failed to start: %s" % (label, ex))
            return 1
        for line in iter(p.stdout.readline, ""):
            self._log(line.rstrip("\n"))
        code = p.wait()
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
               "--register-lib", import_name]
        self._run(cmd, "register")

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
               "--db", "--unregister-lib", import_name]
        self._run(cmd, "unregister")

# PyTorchUI

<p align="center">
  <img src="https://raw.githubusercontent.com/OpenNoorIlm/PyTorchUI/main/docs/banner.png" alt="PyTorchUI" width="720">
</p>

A Blender-styled visual node editor for building and running PyTorch
pipelines.  Drag nodes onto a canvas, wire them together, press Run,
and the editor emits and executes real Python.

![status](https://img.shields.io/badge/status-alpha-orange)

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Install](#install)
- [Quick start](#quick-start)
- [Command line tools](#command-line-tools)
- [Editor keyboard shortcuts](#editor-keyboard-shortcuts)
- [Tools menu](#tools-menu)
- [Settings](#settings)
- [Node library](#node-library)
- [Project layout](#project-layout)
- [How it works](#how-it-works)
- [Extending](#extending)
- [Troubleshooting](#troubleshooting)

## Features

### Editor

- Blender-style nodes: rounded header, soft shadow, collapsible
  sections, per-node shell-output strip.
- Typed sockets with connection rules.  Only compatible types link.
- Bezier wires, one-input-one-edge enforcement, rubber-band
  selection, drag-duplicate.
- Unlimited zoom in / out.  Home resets the transform and frames
  every node.
- Ctrl+Z / Ctrl+Y undo and redo with a 200-snapshot history.
- Persistent settings for window geometry, panel sizes, auto-save,
  output paths.
- Hover tooltips everywhere.  F1 opens the help pane with the full
  docstring, rendered LaTeX math, and syntax-highlighted examples.

### Code generation

- Ctrl+G walks the graph in topological order and writes
  generated.py.
- Category-driven imports are added automatically:
  import torch, import torch.nn as nn,
  import torch.nn.functional as F, import torch.optim as optim.
- The "auto" sentinel input is dropped from the emitted call.  When
  the class has a Lazy variant (nn.Linear to nn.LazyLinear) the swap
  is made automatically.
- Positional comma lists (1, 2, 3) become positional args;
  parenthesised lists ((1, 2)) become a single argument.
- *args and **kwargs sockets are expanded appropriately in the call.

### Execution

- F5 runs generated.py in a subprocess with start_new_session=True
  so it can be cleanly stopped.
- Stdout is routed back line by line.  The editor reads RT markers
  and appends each node's output to its own strip.
- Ctrl+F5 pauses, Ctrl+Shift+F5 resumes, Shift+F5 stops.  Pause
  writes a flag file that the subprocess checks before each node,
  so it stops between operations rather than mid-write.
- input() nodes surface a QInputDialog during the run.
- Preview nodes render images, tables, plots, folders, JSON, text,
  HTML and Markdown in a dialog and inline on the node body.

### Node library

- create.py walks the installed package tree and produces a SQLite
  or JSON node library.
- A default build yields 50 000+ nodes across torch, tensorflow,
  transformers, PyQt5, numpy, scipy, matplotlib, and more.
- Full-text search via an FTS5 virtual table.
- WAL journal mode so the editor can read while a build writes.
- C-extension walker for .so / .pyd / .dylib modules with
  __module__-based category routing so classes land under their
  real subpackage.  torch.nn.Conv2d goes under
  Torch/nn/modules/conv, not just Torch/nn.
- --deep-c walks nested classes and enums inside C extensions.

### Tools

- Library Manager: search PyPI (pip search was disabled in 2020;
  this talks to pypi.org directly), install, update, register into
  the DB, unregister.
- Convert Python to PyTorchUI: scan a project, produce a tailored
  node library for its imports.
- Convert PyTorchUI to Python: export the graph as graph.json,
  graph.meta.json, annotated pipeline.py, open_graph.py, README.md.
- Output Paths: configure where generated files land.

## Requirements

- Python 3.9 or newer.
- PyQt5.
- matplotlib (help pane math, Preview Plot).
- torch (for the default library).
- Optional: pyautogui, Pillow, python-xlib.

    pip install -r requirements.txt

## Install

    git clone https://github.com/OpenNoorIlm/PyTorchUI.git
    cd PyTorchUI
    python -m venv .venv
    source .venv/bin/activate           # Linux / macOS
    # .venv\Scripts\activate.bat        # Windows CMD
    # .venv\Scripts\Activate.ps1        # Windows PowerShell
    pip install -r requirements.txt

## Quick start

    ./start.sh --choose    # one-time: pick the Python to use
    python build.py        # build the node database
    ./start.sh             # launch

Inside the editor: Shift+A opens the add-node popup.  Drag nodes
onto the canvas, wire them together, Ctrl+G writes the Python, F5
runs it.

## Command line tools

### start.py (start.sh / start.bat / start.ps1 wrappers)

    ./start.sh                  build if needed, then run
    ./start.sh build            rebuild the node database
    ./start.sh build -l os,json add specific libraries
    ./start.sh build -nc        no cap, walk everything
    ./start.sh clean            remove caches, backups, strays
    ./start.sh deps             install requirements.txt
    ./start.sh test             run pytest
    ./start.sh doctor           environment report
    ./start.sh reset            clean + build + run
    ./start.sh --choose         pick a Python interpreter
    ./start.sh --python PATH    use a specific interpreter
    ./start.sh --show           print the saved choice

### build.py

    python build.py                         default library list
    python build.py -l torch,numpy          specific libraries
    python build.py -l torch -nc --deep-c   no cap, nested classes
    python build.py --gui                   PyQt5 build dialog
    python build.py --check -l torch        installed + registered
    python build.py --list                  every library in the DB
    python build.py --verify                DB integrity + WAL
    python build.py --install numpy         pip install
    python build.py --register torch        walk + upsert into DB
    python build.py --update torch --register torch
    python build.py --unregister numpy      drop from DB

### create.py

    python create.py                        embed specs in main.py
    python create.py --db                   SQLite + loader
    python create.py --json                 JSON + loader
    python create.py -l pyautogui           add libraries
    python create.py -nt -l '[os,json]'     skip torch
    python create.py -c 500                 cap 500 per package
    python create.py -cl torch=5000         torch gets 5000
    python create.py -nc                    no cap
    python create.py --deep-c               nested C-ext classes
    python create.py --loader-only          re-emit main.py only
    python create.py --debug                verbose walk logging
    python create.py --list-modules         what discovery finds
    python create.py --check-lib torch      status
    python create.py --list-libs            list DB
    python create.py --register-lib numpy   add one library
    python create.py --unregister-lib numpy remove one
    python create.py --no-wal               disable WAL

## Editor keyboard shortcuts

| key | action |
| --- | --- |
| Shift+A | Open the add-node popup |
| Ctrl+G | Generate generated.py from the current graph |
| F5 | Run generated.py |
| Ctrl+F5 | Pause the running subprocess |
| Ctrl+Shift+F5 | Resume |
| Shift+F5 | Stop |
| Ctrl+Z | Undo |
| Ctrl+Y | Redo |
| Ctrl+Shift+Z | Redo (alternative binding) |
| Ctrl+D | Duplicate selection |
| Del / Backspace | Delete selection |
| Ctrl+A | Select all |
| Alt+A | Deselect all |
| Home | Reset zoom and frame everything |
| Ctrl+= / Ctrl+- | Zoom in / out |
| Ctrl+0 | Reset zoom |
| Ctrl+E | Browse example pipelines |
| F1 | Help pane for the last clicked item |
| middle-drag | Pan |
| wheel | Zoom (unlimited) |

## Tools menu

### Library Manager

Search packages on PyPI, install or update with pip, register into
the node database, unregister.  Every action streams its output to
the dialog's Log pane, and the Registered column updates live.  The
pip search subcommand has been disabled since December 2020 — this
dialog talks to pypi.org directly.

### Convert Python to PyTorchUI

Point at a Python project folder.  The dialog scans its .py files,
extracts imports (skipping stdlib and local modules), checks which
packages are installed in the chosen interpreter, offers to install
the missing ones, and runs create.py with the detected library list
to produce a tailored .db plus a loader.

### Convert PyTorchUI to Python

Export the current graph as a self-contained folder:

- graph.json      the raw graph, re-openable with File / Open.
- graph.meta.json a detailed JSON summary: for each node, its title,
                  template, category, position, and every input's
                  state (literal with the value, connected, or empty);
                  plus every edge and the category counts.
- pipeline.py     generated Python annotated with a header comment
                  block and per-node comments listing the parameters
                  each call uses.
- open_graph.py   a short loader script.
- README.md       explains what the five files are.

### Output Paths

Configure where generated.py goes, where graphs default-save, and
where databases land.  Stored in ~/.pytorchui.json.

## Settings

- Hide Path Wires   -- collapse the Path In / Path Out sockets.
- Auto Save         -- periodic snapshot to autosave.json.
- Auto Save Interval -- 10 s to 300 s.
- Save After Change -- debounced save on every graph change.
- Show Block Slot   -- the dashed slot below each node.
- Auto Reroute Wires -- route dangling edges through new nodes.
- Ask Before Reroute -- prompt instead of auto.
- Output Paths      -- three folder pickers.

## Node library

Nodes come from four sources:

1. create.py       -- every public class and function from the
                      installed libraries you name.
2. _register_builtins(api) -- stdlib helpers (print, len, range,
                      math.*, random.*, string and dict methods).
3. _register_roles(api)    -- language constructs (if, for, while,
                      try, def, class, lambda, comprehensions),
                      Variables, Values, Files, Media, Preview.
4. NODE_TEMPLATES.extend([...]) -- inline additions like the
                      Preview and Import JSON nodes.

### Flow nodes

Every non-anchor node has Path In / Path Out sockets.  Chain them to
define execution order.  Start and End are anchors with only one
flow socket each.

### Categories

- Torch, torch   -- PyTorch from torch.nn down to torch.nn.modules.conv.
- PyQt5          -- every Qt widget class.
- tensorflow, transformers, numpy, scipy, matplotlib -- as named.
- Built-ins/...  -- stdlib helpers and language constructs.
- Preview        -- image / audio / video / table / folder / plot /
                    text / JSON viewers.
- Flow           -- Start and End.

## Project layout

    PyTorchUI/
    |-- main.py                    generated loader (do not edit)
    |-- create.py                  node library generator
    |-- build.py                   build orchestration (CLI + GUI)
    |-- start.py                   cross-platform launcher
    |-- start.sh / .bat / .ps1     thin wrappers
    |-- helpers/
    |   `-- Nodes/
    |       |-- Nodes.py           the editor
    |       |-- NodeClasses.py     widget host + capture helpers
    |       |-- ConvertDialogs.py  Python <-> PyTorchUI converters
    |       `-- LibraryDialogs.py  library manager UI
    |-- examples/                  sample graphs
    |-- tests/                     pytest
    |-- docs/                      banner, social preview
    |-- data/                      preferred DB location
    |   `-- main.db                SQLite node library
    |-- generated.py               emitted on Ctrl+G
    |-- requirements.txt
    `-- README.md, CONTRIBUTING.md, SECURITY.md, CODE_OF_CONDUCT.md

## How it works

### 1. Node discovery

create.py imports each requested top-level package, walks its
submodules with pkgutil, and for each one collects public classes
and functions via inspect.  C extensions (.so / .pyd / .dylib) are
enumerated through vars(module) since they have no submodules.

Each object becomes a spec dict with name, category, description,
qualname, inputs, outputs, and full_path.

### 2. Database

Specs land in main.db, a SQLite file with:

- nodes: one row per node.
- metadata: key/value pairs (torch version, build date, node count).
- node_search: FTS5 virtual table indexing name, description, and
  category.
- Triggers that keep node_search in sync.

The DB is opened in WAL mode so the editor can read while a build
writes.

### 3. Editor startup

main.py reads all specs from the DB, calls
api.register.node.bulk(specs), and builds a name-to-template dict for
O(1) lookups.  Registering 50 000 specs takes roughly three seconds
on a modern machine.

### 4. Code generation

Ctrl+G walks the graph in topological order (by visual position, then
by dependency), and for each node emits a runtime.begin /
runtime.end block wrapping its Python.  Every category knows how to
translate itself: torch.nn.Conv2d becomes nn.Conv2d(...),
pyautogui.click becomes pyautogui.click(x=..., y=...), and so on.

### 5. Execution

F5 spawns generated.py in a subprocess.  The generated code imports
runtime from the editor and calls runtime.begin(nid) at the start of
every node.  Those emit RT markers on stdout, which the editor
parses and routes back to the correct node.

## Extending

### Add a new node type

Register a template at runtime:

    from helpers.Nodes.Nodes import API
    api = API.instance()

    api.register_node(
        name="My Node",
        category="Custom",
        color="#4A6B8A",
        description="Does a thing.",
        inputs=[
            ("A", "int", "First operand", "0"),
            ("B", "int", "Second operand", "0"),
        ],
        outputs=[("Result", "int", "A + B")],
    )

Nodes registered this way live only for the current session.  To make
them permanent, add them to NODE_TEMPLATES in Nodes.py or to the
ROLES / VALUES lists.

### Add a specialised widget

In NodeClasses.py, decorate a factory:

    @register_node_class("my_kind")
    def _make_my_node(Node):
        class MyNode(Node):
            def after_template_built(self, template):
                self.add_button("x", on_click=self._do)
            def _do(self):
                print("clicked")
        return MyNode

Then set node_class to "my_kind" on a template.

### Add a codegen branch

In Nodes.py, inside _generate_code / emit_leaf, add an
if kind == "my_kind": branch that appends Python lines to L.

## Troubleshooting

### Editor will not start

- NameError: QWidget...  A patched module uses a widget that was not
  imported.  Check the traceback's file and line.
- sqlite3.OperationalError: unable to open database file.  The DB's
  parent folder does not exist, or the file is owned by root.
  python build.py --verify reports the state.
- ModuleNotFoundError: PyQt5.  Wrong interpreter.  Run
  ./start.sh --choose and pick the one that has PyQt5.

### Build takes forever

A full walk of 20+ libraries is I/O-bound.  It is the imports, not
the DB write, that dominate.  --loader-only refreshes the loader in
under a second.  --register-lib X adds one library without re-walking
the rest.

### Editing while building

WAL mode makes this safe: the editor reads while the build writes.
The busy_timeout pragma handles short collisions.

### Register in Library Manager does nothing

Check the Log pane.  The exact create.py command is printed there
along with the exit code.  If it exits 0 but the Registered column is
unchanged, the DB path is different.  The status line at the top of
the dialog shows the actual path.

## License

GNU General Public License v3.0 or later.  See LICENSE.

#!/usr/bin/env python3
"""
create.py — auto-generate main.py (or a data file plus loader) from the
installed Python package tree.

Usage:
    python create.py                             # embed specs in main.py
    python create.py --json                      # write main.json + loader
    python create.py --db                        # write main.db + loader
    python create.py -l pyautogui                # add pyautogui
    python create.py -nt -l '[pyautogui,os]'     # skip torch
    python create.py -nc                        # no caps: walk everything
    python create.py -c 500                     # per-package cap 500
    python create.py -cl torch=5000              # torch gets 5000
    python create.py -cl torch=5000 -cl scipy=2000

Notes
-----
* main.py is truncated (opened "w") before every write — no stale lines.
* Global dedup by name — same class/function registered once.
* Modules walked shallow-first, so torch.nn.Conv2d wins over the
  internal torch.nn.modules.conv.Conv2d.
* torch is walked by default, unless --no-torch / -nt is passed.
* Built-in EXTRA_ROOTS are skipped if the same module was already
  walked via -l; nothing is registered twice.
* Deprecation warnings and module-import stdout/stderr noise are
  suppressed during the walk.
* Every BaseException subclass is moved into a single "Exceptions"
  category and carries a `qualname`.
* Enum-typed and object-typed parameter defaults are dropped, so the
  generated file always parses.
* Known PyAutoGUI nodes (click / press / hotkey) get a `node_class`
  field, which the editor uses to pick a specialised Node subclass.
* Both C-extension functions (math.sqrt, os.listdir) and Python
  functions are collected; inspect.isroutine covers both.
"""

import argparse
import contextlib
import datetime
import importlib
import inspect
import io
import json
import os
import pkgutil
import sqlite3
import sys
import warnings


# ============================================================== #
#  CONFIG                                                        #
# ============================================================== #

BASE_LIBRARIES = ["torch"]
BASE_CATEGORY = {"torch": "Torch"}

EXCEPTIONS_CATEGORY = "Exceptions"
EXCEPTIONS_COLOR    = "#A03A3A"

# Default per-package cap (overridable with -c / --cap).
PER_PACKAGE_CAP  = 2000
EXTRA_ROOT_CAP   = 150

EXTRA_ROOTS = [
    ("Built-ins/io",       "io"),
    ("Built-ins/os",       "os"),
    ("Built-ins/os.path",  "os.path"),
    ("Built-ins/math",     "math"),
    ("Built-ins/random",   "random"),
    ("Built-ins/json",     "json"),
    ("Built-ins/pathlib",  "pathlib"),
    ("Built-ins/shutil",   "shutil"),
    ("Built-ins/tempfile", "tempfile"),
    ("Built-ins/time",     "time"),
    ("Built-ins/datetime", "datetime"),
    ("Built-ins/Exceptions", "builtins"),
]

_EXCEPTIONS_ONLY_ROOTS = {"builtins"}

SKIP_PREFIXES = (
    "torch._C",
    "torch._inductor",
    "torch._dynamo",
    "torch._functorch",
    "torch._native",
    "torch._vendor",
    "torch.onnx",
    "torch.jit",
    "torch.fx",
    "torch.testing",
    "torch.version",
    "torch.overrides",
    "torch.types",
    "torch.torch_version",
    "torch.utils.tensorboard",
)

MAX_DEPTH      = 4
MAX_INPUTS     = 15
OUT_FILE       = "main.py"
JSON_FILE      = "main.json"
DB_FILE        = "main.db"
PROGRESS_EVERY = 100

# Safety net when --no-cap is used.  Five hundred thousand nodes is
# far more than anyone wants to browse; if the walk gets close to
# this, something is wrong.  Override by editing this constant.
HARD_SAFETY_CAP = 500_000


# ------------------------------------------------------------------ #
#  Debug / introspection                                              #
# ------------------------------------------------------------------ #

_DEBUG     = False
_LIST_ONLY = False


COLOR_PALETTE = [
    "#4A6B8A", "#8A4A4A", "#4A8A6B", "#6B4A8A",
    "#8A7A4A", "#4A7A8A", "#7A4A8A", "#7A6B3A",
]


# ============================================================== #
#  Node-class overrides for known libraries                      #
# ============================================================== #
#
# Maps (dotted_module, name) -> node_class tag.  The editor uses the
# tag to instantiate a specialised Node subclass from
# helpers/Nodes/NodeClasses.py instead of a plain Node, so the node
# comes with its own widgets (capture buttons, etc.).

_NODE_CLASS_MAP = {
    ("pyautogui", "click"):  "click",
    ("pyautogui", "press"):  "press",
    ("pyautogui", "hotkey"): "hotkey",
}


def _node_class_for(mod_path, name):
    return _NODE_CLASS_MAP.get((mod_path, name))


# ============================================================== #
#  CLI                                                           #
# ============================================================== #

def _split_lib_token(tok):
    tok = str(tok).strip()
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in ("'", '"'):
        tok = tok[1:-1].strip()
    if tok.startswith("[") and tok.endswith("]"):
        tok = tok[1:-1]
    parts = tok.replace(" ", ",").split(",")
    return [p.strip() for p in parts if p.strip()]


def _flatten_libraries(raw_lists):
    out = []
    for group in raw_lists:
        for tok in group:
            out.extend(_split_lib_token(tok))
    return out


def _parse_cap_libs(raw_list):
    """
    Parse -cl / --cap-libs entries.  Accepts any of:

        -cl torch=5000
        -cl torch:5000
        -cl 'torch=5000,transformers=3000'
        -cl '{"torch": 5000, "transformers": 3000}'

    Repeated flags are merged; later values win.
    """
    out = {}
    for token in raw_list:
        token = str(token).strip()
        if not token:
            continue
        if token.startswith("{"):
            try:
                d = json.loads(token)
            except Exception as ex:
                print("  ! could not parse --cap-libs JSON: %s" % ex)
                continue
            for k, v in d.items():
                try:
                    out[str(k).strip()] = int(v)
                except Exception:
                    print("  ! bad cap value for %r: %r" % (k, v))
            continue
        for piece in token.split(","):
            piece = piece.strip()
            if not piece:
                continue
            sep = "=" if "=" in piece else (":" if ":" in piece else None)
            if sep is None:
                print("  ! --cap-libs entry needs LIB=N: %r" % piece)
                continue
            k, v = piece.split(sep, 1)
            k = k.strip()
            try:
                out[k] = int(v.strip())
            except Exception:
                print("  ! bad cap number for %r: %r" % (k, v))
    return out


def _build_parser():
    p = argparse.ArgumentParser(
        prog="create.py",
        description=(
            "Auto-generate the node library from installed Python "
            "packages.  torch is walked by default.  Output can be a "
            "plain Python file, a JSON data file, or a SQLite database "
            "plus a small loader main.py."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  create.py\n"
            "  create.py -l pyautogui\n"
            "  create.py -l pyautogui --json\n"
            "  create.py -l pyautogui --db\n"
            "  create.py -nt -l '[pyautogui,os,time]'\n"
            "  create.py -nc -l '[torch,transformers]'\n"
            "  create.py -c 500\n"
            "  create.py -cl torch=5000 -cl transformers=8000\n"
        ),
    )
    p.add_argument(
        "-l", "--libraries", "--library", "--libs",
        dest="libraries",
        action="append", nargs="*", default=[],
        metavar="NAME",
        help="extra libraries to walk (in addition to torch).",
    )
    p.add_argument(
        "-nt", "--no-torch",
        dest="no_torch",
        action="store_true",
        help="skip walking torch.  Only -l libraries and the built-in "
             "EXTRA_ROOTS are walked.  Also lets create.py run on "
             "machines without torch installed.",
    )
    p.add_argument(
        "-c", "--cap", "--max-nodes",
        dest="cap",
        type=int,
        default=PER_PACKAGE_CAP,
        metavar="N",
        help="per-package cap on collected nodes (default: %(default)s).",
    )
    p.add_argument(
        "-nc", "--no-cap",
        dest="no_cap",
        action="store_true",
        help="no cap.  Walk every node in every requested library.  "
             "Ignored for libraries that have an explicit entry in "
             "--cap-libs.",
    )
    p.add_argument(
        "-cl", "--cap-libs",
        dest="cap_libs",
        action="append", default=[],
        metavar="SPEC",
        help="per-library cap override: LIB=N, LIB:N, or a JSON string "
             "'{\"LIB\": N}'.  May be repeated.",
    )
    p.add_argument(
        "--format", "-f",
        dest="fmt",
        choices=("py", "json", "db"),
        default=None,
        help="output format.  py = embed specs in main.py (default).",
    )
    p.add_argument(
        "--json",
        dest="use_json",
        action="store_true",
        help="shortcut for --format json",
    )
    p.add_argument(
        "--db",
        dest="use_db",
        action="store_true",
        help="shortcut for --format db",
    )
    p.add_argument(
        "-o", "--output",
        dest="out_file",
        default=OUT_FILE,
        metavar="PATH",
        help="output main.py path (default: %(default)s)",
    )
    p.add_argument(
        "--json-path",
        dest="json_path",
        default=JSON_FILE,
        metavar="PATH",
        help="json data path when --json is used (default: %(default)s)",
    )
    p.add_argument(
        "--db-path",
        dest="db_path",
        default=DB_FILE,
        metavar="PATH",
        help="sqlite path when --db is used (default: %(default)s)",
    )
    p.add_argument(
        "--max-depth",
        dest="max_depth",
        type=int,
        default=MAX_DEPTH,
        metavar="N",
        help="maximum submodule depth (default: %(default)s)",
    )
    p.add_argument(
        "--quiet", action="store_true",
        help="suppress progress output",
    )
    p.add_argument(
        "--deep-c", dest="deep_c", action="store_true",
        help="for compiled extensions (.so / .pyd), also register "
             "nested classes and enums.  Off by default.",
    )
    p.add_argument(
        "--debug", dest="debug", action="store_true",
        help="verbose walk logging: every module considered and why "
             "each object was kept or dropped.",
    )
    p.add_argument(
        "--list-modules", dest="list_modules", action="store_true",
        help="print every module discovery would visit, then exit.",
    )
    p.add_argument(
        "--loader-only", dest="loader_only", action="store_true",
        help="skip the walk; only re-emit the loader main.py using "
             "metadata already present in main.db / main.json.  Fast.",
    )
    p.add_argument(
        "--register-lib", dest="register_lib",
        action="append", default=[], metavar="LIB",
        help="walk and register a single library into the DB "
             "(implies --db, --no-torch).  Safe to run while the "
             "editor is open thanks to WAL.",
    )
    p.add_argument(
        "--unregister-lib", dest="unregister_lib",
        action="append", default=[], metavar="LIB",
        help="delete every node belonging to LIB from the DB.",
    )
    p.add_argument(
        "--check-lib", dest="check_lib",
        action="append", default=[], metavar="LIB",
        help="report installed version and DB status for LIB.",
    )
    p.add_argument(
        "--list-libs", dest="list_libs", action="store_true",
        help="list every library currently registered in the DB.",
    )
    p.add_argument(
        "--no-wal", dest="no_wal", action="store_true",
        help="disable WAL journal mode (default is on).",
    )
    return p


def _resolve_format(args):
    if args.use_json and args.use_db:
        print("! --json and --db are mutually exclusive")
        sys.exit(2)
    if args.use_json:
        return "json"
    if args.use_db:
        return "db"
    if args.fmt:
        return args.fmt
    return "py"


# ============================================================== #
#  Package list                                                  #
# ============================================================== #

def build_packages(extra_libs, no_torch=False):
    seen = set()
    ordered = []
    if not no_torch:
        for name in BASE_LIBRARIES:
            if name not in seen:
                seen.add(name); ordered.append(name)
    for name in extra_libs:
        if not name or name in seen:
            continue
        seen.add(name); ordered.append(name)
    out = []
    for name in ordered:
        cat = BASE_CATEGORY.get(name) or name.split(".")[0]
        out.append((cat, name))
    return out


# ============================================================== #
#  Helpers                                                       #
# ============================================================== #

def py_str(s):
    if s is None:
        return '""'
    if not isinstance(s, str):
        s = str(s)
    if ("\n" in s and "\\" not in s
            and '"""' not in s and not s.endswith('"')):
        return '"""' + s + '"""'
    return repr(s)


def color_for(cat):
    if cat == EXCEPTIONS_CATEGORY:
        return EXCEPTIONS_COLOR
    return COLOR_PALETTE[hash(cat) % len(COLOR_PALETTE)]


def get_doc(obj):
    doc = None
    try:
        doc = obj.__doc__
    except Exception:
        pass
    if not doc and inspect.isclass(obj):
        try:
            doc = obj.__init__.__doc__
        except Exception:
            pass
    if doc is None:
        return ""
    if not isinstance(doc, str):
        try:
            doc = str(doc)
        except Exception:
            return ""
    try:
        doc = inspect.cleandoc(doc)
    except Exception:
        pass
    return doc.strip()


def infer_type(annotation):
    if annotation is None or annotation is inspect.Parameter.empty:
        return "any"
    name = getattr(annotation, "__name__", None) or str(annotation)
    low = name.lower()
    if low == "int":                       return "int"
    if low == "float":                     return "float"
    if low == "bool":                      return "bool"
    if low in ("str", "string"):           return "string"
    if "tensor" in low:                    return "vector"
    if "tuple" in low or "list" in low:    return "vector"
    if "dtype" in low or "device" in low:  return "string"
    return "any"


def get_params(obj):
    try:
        if inspect.isclass(obj):
            sig = inspect.signature(obj.__init__)
            params = list(sig.parameters.values())[1:]
        else:
            sig = inspect.signature(obj)
            params = list(sig.parameters.values())
    except (ValueError, TypeError):
        return []

    out = []
    for p in params:
        if p.kind == p.VAR_POSITIONAL:
            out.append(("*" + p.name, "vector",
                        "Positional arguments as a list", "[]"))
            if len(out) >= MAX_INPUTS:
                break
            continue
        if p.kind == p.VAR_KEYWORD:
            out.append(("**" + p.name, "any",
                        "Keyword arguments as a dict", "{}"))
            if len(out) >= MAX_INPUTS:
                break
            continue
        if p.name in ("self", "cls"):
            continue
        t = infer_type(p.annotation)
        if p.default is inspect.Parameter.empty:
            desc, dv = "", None
        else:
            import enum as _enum

            def _is_literal_default(x):
                if x is None or isinstance(x, bool):
                    return True
                if isinstance(x, _enum.Enum):
                    return False
                if type(x) is int or type(x) is float:
                    return True
                if type(x) is str and len(x) < 200:
                    return True
                return False

            try:
                desc = "Default: %r" % (p.default,)
            except Exception:
                desc = "Has default"
            if not _is_literal_default(p.default):
                dv = None
            else:
                try:
                    r = repr(p.default)
                except Exception:
                    r = ""
                if (not r or r.startswith("<")
                        or r.endswith(">")
                        or " object at 0x" in r):
                    dv = None
                elif isinstance(p.default, str):
                    dv = repr(p.default)
                else:
                    dv = r
        out.append((p.name, t, desc, dv))
        if len(out) >= MAX_INPUTS:
            break
    return out


def _is_exception_class(obj):
    try:
        return (inspect.isclass(obj)
                and issubclass(obj, BaseException))
    except TypeError:
        return False


def _exception_qualname(obj, fallback_name):
    try:
        module = getattr(obj, "__module__", "") or ""
    except Exception:
        module = ""
    name = None
    try:
        name = getattr(obj, "__qualname__", None)
    except Exception:
        name = None
    if not name:
        name = getattr(obj, "__name__", None) or fallback_name
    name = name.replace(".<locals>.", ".")
    if not module or module == "builtins":
        return name
    return module + "." + name


# ============================================================== #
#  Discovery                                                     #
# ============================================================== #

def _skip_module(name):
    return any(name == p or name.startswith(p + ".") for p in SKIP_PREFIXES)


def module_to_category(root_cat, dotted_name):
    parts = dotted_name.split(".")
    return "/".join([root_cat] + parts[1:])


def _safe_import(name):
    """
    Import a module with all warnings and stdout/stderr noise silenced.

    Some libraries (scipy in particular) print debugging data straight
    to stdout on import.  Redirecting stderr alone isn't enough, so we
    capture both while the import runs.
    """
    devnull = io.StringIO()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with contextlib.redirect_stdout(devnull):
                with contextlib.redirect_stderr(devnull):
                    return importlib.import_module(name)
    except BaseException:
        return None


def discover_modules(root_cat, package_name, max_depth, quiet=False):
    results = [(root_cat, package_name, 0)]
    if _DEBUG:
        print("[debug] discover %-42s cat=%s depth=%d"
              % (package_name, root_cat, max_depth))

    pkg = _safe_import(package_name)
    if pkg is None:
        if _DEBUG:
            print("[debug]   import failed")
        if not quiet:
            print("  ! could not import %s" % package_name)
        return results
    pkg_path = getattr(pkg, "__path__", None)
    if pkg_path is None:
        if _DEBUG:
            print("[debug]   no __path__")
        return results

    base_depth = package_name.count(".")
    devnull = io.StringIO()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with contextlib.redirect_stdout(devnull):
                with contextlib.redirect_stderr(devnull):
                    for info in pkgutil.walk_packages(
                            pkg_path, package_name + "."):
                        name = info.name
                        leaf = name.rsplit(".", 1)[-1]

                        if leaf.startswith("_"):
                            if _DEBUG:
                                print("[debug]   skip %-42s leaf starts _"
                                      % name)
                            continue
                        if _skip_module(name):
                            if _DEBUG:
                                print("[debug]   skip %-42s SKIP_PREFIXES"
                                      % name)
                            continue
                        depth = name.count(".") - base_depth
                        if depth > max_depth:
                            if _DEBUG:
                                print("[debug]   skip %-42s depth %d > %d"
                                      % (name, depth, max_depth))
                            continue

                        origin = ""
                        try:
                            sp = importlib.util.find_spec(name)
                            origin = (sp.origin or "") if sp else ""
                        except Exception:
                            pass
                        if info.ispkg:
                            kind = "pkg"
                        elif origin.endswith((".so", ".pyd", ".dylib")):
                            kind = "c-ext"
                        else:
                            kind = "mod"
                        if _DEBUG:
                            print("[debug]   keep %-42s %-5s %s"
                                  % (name, kind, origin))

                        results.append(
                            (module_to_category(root_cat, name),
                             name, depth))
    except BaseException as ex:
        if _DEBUG:
            print("[debug]   walk failed: %s" % ex)
        if not quiet:
            print("  ! walk failed for %s: %s" % (package_name, ex))
    results.sort(key=lambda r: (r[2], r[1]))
    return results


# ============================================================== #
#  Collect                                                       #
# ============================================================== #

def _is_c_extension(mod_path):
    """Return True when mod_path resolves to a compiled extension.

    Handles .so (Linux), .pyd (Windows), and .dylib (macOS).  Uses
    importlib.util.find_spec so it does NOT import the module.
    """
    try:
        import importlib.util as _ilu
        spec = _ilu.find_spec(mod_path)
    except (ImportError, ValueError, AttributeError):
        return False
    if spec is None:
        return False
    origin = spec.origin or ""
    return origin.endswith((".so", ".pyd", ".dylib"))


def _collect_from_c_extension(mod, mod_path, cat, seen_names,
                              specs_out, limit, exc_only, deep=False):
    """Enumerate classes and functions from a compiled extension.

    Re-exports are a real problem: PyQt5.Qt contains the whole Qt
    API copied from QtCore, QtGui, etc.  We route each object to the
    category named by its own __module__ when that points to a
    specific PyQt5.* submodule, so QObject lands under PyQt5/QtCore
    even when discovered via PyQt5.Qt.
    """
    added = 0
    try:
        members = list(vars(mod).items())
    except Exception:
        members = []

    for name, obj in members:
        if len(specs_out) >= limit:
            break
        if name.startswith("_"):
            continue
        if name in seen_names:
            continue
        if not (inspect.isclass(obj) or inspect.isroutine(obj)):
            continue

        is_exc = _is_exception_class(obj)
        if exc_only and not is_exc:
            continue

        # ---- category routing ---- #
        # Prefer the object's own __module__ when it points to a
        # specific module inside the same package.  Ignore SIP
        # placeholders and the empty string.
        spec_cat = cat
        real_mod = getattr(obj, "__module__", None) or ""
        if (real_mod
                and real_mod != mod_path
                and "." in real_mod
                and not real_mod.startswith("sip")
                and not real_mod.startswith("PyQt5.sip")):
            spec_cat = real_mod.replace(".", "/")

        seen_names.add(name)

        if is_exc:
            spec_cat = EXCEPTIONS_CATEGORY
            qualname = _exception_qualname(obj, name)
        else:
            qualname = None

        spec = {
            "name":        name,
            "color":       color_for(spec_cat),
            "category":    spec_cat,
            "description": get_doc(obj),
            "qualname":    qualname,
            "inputs":      get_params(obj),
            "outputs":     [(name, "object",
                             "Result of %s.%s" % (mod_path, name))],
            "full_path":   "%s.%s" % (mod_path, name),
        }

        _nc = _node_class_for(mod_path, name)
        if _nc:
            spec["node_class"] = _nc

        specs_out.append(spec)
        added += 1

        if deep and inspect.isclass(obj) and len(specs_out) < limit:
            try:
                inner = list(vars(obj).items())
            except Exception:
                inner = []
            for inner_name, inner_obj in inner:
                if inner_name.startswith("_"):
                    continue
                full_inner = "%s.%s" % (name, inner_name)
                if full_inner in seen_names:
                    continue
                if not (inspect.isclass(inner_obj)
                        or inspect.isroutine(inner_obj)):
                    continue
                seen_names.add(full_inner)
                sub = {
                    "name":        full_inner,
                    "color":       color_for(spec_cat),
                    "category":    spec_cat,
                    "description": get_doc(inner_obj),
                    "qualname":    ("%s.%s" % (qualname, inner_name))
                                   if qualname else None,
                    "inputs":      get_params(inner_obj),
                    "outputs":     [(inner_name, "object",
                                     "Nested attribute of %s.%s"
                                     % (mod_path, name))],
                    "full_path":   "%s.%s.%s" % (mod_path, name, inner_name),
                }
                specs_out.append(sub)
                added += 1

    return added

def collect_from_module(cat, mod_path, seen_names, specs_out, limit,
                        deep_c=False):
    if len(specs_out) >= limit:
        return 0
    if _DEBUG:
        print("[debug] collect %-45s cat=%s" % (mod_path, cat))
    mod = _safe_import(mod_path)
    if mod is None:
        if _DEBUG:
            print("[debug]   import failed")
        return 0

    exc_only = mod_path in _EXCEPTIONS_ONLY_ROOTS

    if _is_c_extension(mod_path):
        if _DEBUG:
            print("[debug]   -> C-extension path")
        n = _collect_from_c_extension(
            mod, mod_path, cat, seen_names, specs_out, limit,
            exc_only, deep=deep_c)
        if _DEBUG:
            print("[debug]   collected %d" % n)
        return n

    top_level = "." not in mod_path
    root = mod_path.split(".")[0]
    real_root = getattr(mod, "__name__", root).split(".")[0]
    roots = {root, real_root}
    added = 0
    for name in dir(mod):
        if len(specs_out) >= limit:
            break
        if name.startswith("_"):
            continue
        if name in seen_names:
            continue
        try:
            obj = getattr(mod, name)
        except BaseException:
            continue
        if not (inspect.isclass(obj) or inspect.isroutine(obj)):
            continue

        real_mod = getattr(obj, "__module__", "") or ""

        if not top_level:
            if real_mod and not any(real_mod.startswith(r)
                                    for r in roots):
                if _DEBUG:
                    print("[debug]   drop %-30s __module__=%r"
                          % (name, real_mod))
                continue

        # Route to the object's own module so a class found via a
        # shallow re-export lands under its real home.  Prefix is
        # taken from cat's first segment so the casing stays
        # consistent with the walker.
        spec_cat = cat
        if (real_mod
                and real_mod != mod_path
                and not real_mod.startswith("sip")
                and not real_mod.startswith("PyQt5.sip")
                and "." in real_mod
                and any(real_mod.startswith(r) for r in roots)):
            spec_cat = module_to_category(cat.split("/")[0], real_mod)
            if _DEBUG:
                print("[debug]   route %-24s -> %s"
                      % (name, spec_cat))

        is_exc = _is_exception_class(obj)
        if exc_only and not is_exc:
            continue

        seen_names.add(name)

        if is_exc:
            spec_cat = EXCEPTIONS_CATEGORY
            qualname = _exception_qualname(obj, name)
        else:
            qualname = None

        spec = {
            "name":        name,
            "color":       color_for(spec_cat),
            "category":    spec_cat,
            "description": get_doc(obj),
            "qualname":    qualname,
            "inputs":      get_params(obj),
            "outputs":     [(name, "object",
                             "Result of %s.%s" % (mod_path, name))],
            "full_path":   "%s.%s" % (mod_path, name),
        }

        _nc = _node_class_for(mod_path, name)
        if _nc:
            spec["node_class"] = _nc

        specs_out.append(spec)
        added += 1
        if _DEBUG:
            print("[debug]     + %-30s (cat=%s)" % (name, spec_cat))

    if _DEBUG:
        print("[debug]   collected %d" % added)
    return added


def _walk_one_package(cat, pkg, seen_names, max_depth, cap, quiet=False,
                      deep_c=False):
    def say(*a):
        if not quiet:
            print(*a)
    pkg_specs = []
    modules = discover_modules(cat, pkg, max_depth, quiet)
    say("  %-16s -> %-10s (%d modules)" % (pkg, cat, len(modules)))
    for i, (mcat, mod_path, _d) in enumerate(modules, 1):
        if not quiet and i % PROGRESS_EVERY == 0:
            say("    ... %d / %d modules, %d nodes"
                % (i, len(modules), len(pkg_specs)))
        try:
            collect_from_module(mcat, mod_path, seen_names, pkg_specs,
                                cap, deep_c=deep_c)
        except TypeError:
            collect_from_module(mcat, mod_path, seen_names,
                                pkg_specs, cap)
        if len(pkg_specs) >= cap:
            say("    cap reached for %s (%d nodes)" % (pkg, cap))
            break
    return pkg_specs


def collect(packages, max_depth, default_cap, quiet=False,
            cap_map=None, no_cap=False, deep_c=False):
    """Walk every (category, module) in packages, then EXTRA_ROOTS."""
    cap_map = cap_map or {}

    def say(*a):
        if not quiet:
            print(*a)

    def cap_for(pkg):
        if pkg in cap_map:
            return cap_map[pkg]
        if no_cap:
            return HARD_SAFETY_CAP
        return default_cap

    all_specs = []
    seen_names = set()
    walked_modules = set()

    say("discovering modules ...")
    for cat, pkg in packages:
        walked_modules.add(pkg)
        cap = cap_for(pkg)
        pkg_specs = _walk_one_package(
            cat, pkg, seen_names, max_depth, cap, quiet,
            deep_c=deep_c)
        all_specs.extend(pkg_specs)
        say("    -> %s: %d nodes%s"
            % (pkg, len(pkg_specs),
               "" if cap != HARD_SAFETY_CAP else " (no cap)"))

    for cat, mod_path in EXTRA_ROOTS:
        if mod_path in walked_modules:
            continue
        walked_modules.add(mod_path)
        pkg_specs = _walk_one_package(
            cat, mod_path, seen_names, max_depth, EXTRA_ROOT_CAP, quiet,
            deep_c=deep_c)
        all_specs.extend(pkg_specs)

    all_specs.sort(key=lambda s: (s["category"], s["name"].lower()))
    return all_specs


# ============================================================== #
#  emit — Python (embed SPECS)                                   #
# ============================================================== #

HEADER_EMBED = '''\
# Auto-generated by create.py — do not edit by hand.
#
# Source:  torch {version} ({torch_path})
# Output:  {out_file}
# Date:    {date}
# Nodes:   {node_count}
#
# Roots walked:
{libraries}
# Use the editor's Ctrl+G to write generated.py from the current graph,
# then F5 (or the Run toolbar button) to execute it.

import os
try:
    from helpers.Nodes.Nodes import (API, section, in_, out,
                                     PATH_TYPE,
                                     PATH_IN_NAME, PATH_OUT_NAME)
except ImportError:
    from Nodes import (API, section, in_, out,
                       PATH_TYPE,
                       PATH_IN_NAME, PATH_OUT_NAME)


def _print_env():
    import sys
    print("Python  %s" % sys.version.split()[0])
    try:
        import matplotlib
        print("matplotlib %s" % matplotlib.__version__)
    except Exception as ex:
        print("matplotlib NOT available (%s)" % ex)
    try:
        import PyQt5
        from PyQt5.QtCore import QT_VERSION_STR
        print("Qt      %s" % QT_VERSION_STR)
    except Exception:
        pass

_print_env()


api = API.instance()
api.clear()
api.register.node.clear()

try:
    try:
        from helpers.Nodes.Nodes import _register_builtins, _register_roles
    except ImportError:
        from Nodes import _register_builtins, _register_roles
    _register_builtins(api)
    _register_roles(api)
except Exception as _e:
    print("[builtins] restore failed:", _e)


SPECS = [
'''

HEADER_LOADER = '''\
# Auto-generated loader by create.py — do not edit by hand.
#
# Data file:  {data_file}
# Format:     {data_format}
# Source:     torch {version} ({torch_path})
# Date:       {date}
# Nodes:      {node_count}
#
# The specs live in {data_file}, not in this file.  Regenerate the
# data with `python create.py --{data_format}`.  This loader reads
# whichever data file is present (json or db).

import json
import os
import sqlite3

try:
    from helpers.Nodes.Nodes import (API, section, in_, out,
                                     PATH_TYPE,
                                     PATH_IN_NAME, PATH_OUT_NAME)
except ImportError:
    from Nodes import (API, section, in_, out,
                       PATH_TYPE,
                       PATH_IN_NAME, PATH_OUT_NAME)


def _print_env():
    import sys
    print("Python  %s" % sys.version.split()[0])
    try:
        import matplotlib
        print("matplotlib %s" % matplotlib.__version__)
    except Exception as ex:
        print("matplotlib NOT available (%s)" % ex)
    try:
        import PyQt5
        from PyQt5.QtCore import QT_VERSION_STR
        print("Qt      %s" % QT_VERSION_STR)
    except Exception:
        pass

_print_env()


def _load_specs(json_path, db_path):
    if os.path.isfile(json_path):
        print("[loader] reading %s" % json_path)
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f).get("nodes", [])
    if os.path.isfile(db_path):
        print("[loader] reading %s" % db_path)
        con = sqlite3.connect(db_path)
        try:
            cur = con.cursor()
            cur.execute("PRAGMA table_info(nodes)")
            cols = [r[1] for r in cur.fetchall()]
            has_nc = "node_class" in cols
            if has_nc:
                sel = ("SELECT name, color, category, description, "
                       "qualname, inputs, outputs, full_path, node_class "
                       "FROM nodes")
            else:
                sel = ("SELECT name, color, category, description, "
                       "qualname, inputs, outputs, full_path "
                       "FROM nodes")
            cur.execute(sel)
            out = []
            for row in cur.fetchall():
                spec = {
                    "name":        row[0],
                    "color":       row[1],
                    "category":    row[2],
                    "description": row[3],
                    "qualname":    row[4],
                    "inputs":      json.loads(row[5]) if row[5] else [],
                    "outputs":     json.loads(row[6]) if row[6] else [],
                    "full_path":   row[7],
                }
                if has_nc and row[8]:
                    spec["node_class"] = row[8]
                out.append(spec)
            return out
        finally:
            con.close()
    print("[loader] no data file found; starting with an empty library")
    return []


api = API.instance()
api.clear()
api.register.node.clear()

try:
    try:
        from helpers.Nodes.Nodes import _register_builtins, _register_roles
    except ImportError:
        from Nodes import _register_builtins, _register_roles
    _register_builtins(api)
    _register_roles(api)
except Exception as _e:
    print("[builtins] restore failed:", _e)


SPECS = _load_specs({json_path!r}, {db_path!r})
for _s in SPECS:
    _s["on_exists"] = "keep"
n_ok, n_bad = api.register.node.bulk(SPECS)
print("Registered %d / %d templates" % (n_ok, n_ok + n_bad))
'''

FOOTER = '''\

# ============================================================== #
#  CUSTOM NODES                                                  #
# ============================================================== #

api.register_node(
    name        = "Download Model",
    color       = "#B07030",
    category    = "Utils",
    description = """\\
Downloads a model from HuggingFace or GitHub.

Inputs
------
username  HuggingFace user / org (or GitHub owner)
repo      Repository or model name
source    "hf" for HuggingFace, "github" for GitHub

Outputs
-------
Path      Local directory the model was saved to
Status    "ok", "cached" or an error message
""",
    inputs = [
        ("username", "string", "HuggingFace user / org", "someuser"),
        ("repo",     "string", "Repository or model name", "somemodel"),
        ("source",   "string", "hf | github", "hf"),
    ],
    outputs = [
        ("Path",   "string", "Local model directory"),
        ("Status", "string", "ok / cached / error text"),
    ],
)


# ============================================================== #
#  FLOW NODES                                                    #
# ============================================================== #

api.register_node(
    name="Start",
    color="#2E5C2E",
    category="Flow",
    description="Entry point of the pipeline.",
    flow=False,
    outputs=[("Next", PATH_TYPE, "First node in the chain")],
)

api.register_node(
    name="End",
    color="#5C2E2E",
    category="Flow",
    description="Exit point of the pipeline.",
    flow=False,
    inputs=[("Prev", PATH_TYPE, "Last node in the chain")],
)


# ============================================================== #
#  RUN                                                           #
# ============================================================== #

api.report.success("Registered %d templates" % len(api.templates()))
api.show()
api.app().exec_()
'''


def _format_libraries_line(packages):
    lines = []
    for cat, name in packages:
        lines.append("#   %-20s  ->  %s\n" % (name, cat))
    lines.append("#\n")
    lines.append("# Built-in roots:\n")
    for cat, name in EXTRA_ROOTS:
        lines.append("#   %-20s  ->  %s\n" % (name, cat))
    return "".join(lines)


def emit_python(specs, out_file, torch_version, torch_path, packages):
    date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(HEADER_EMBED.format(
            version=torch_version, torch_path=torch_path,
            out_file=out_file, date=date,
            node_count=len(specs),
            libraries=_format_libraries_line(packages),
        ))
        for spec in specs:
            f.write("    # %s\n" % spec["full_path"])
            f.write("    {\n")
            f.write('        "name":        %s,\n' % py_str(spec["name"]))
            f.write('        "color":       %s,\n' % py_str(spec["color"]))
            f.write('        "category":    %s,\n' % py_str(spec["category"]))
            f.write('        "description": %s,\n' % py_str(spec["description"]))
            if spec.get("qualname"):
                f.write('        "qualname":    %s,\n'
                        % py_str(spec["qualname"]))
            if spec.get("node_class"):
                f.write('        "node_class":  %s,\n'
                        % py_str(spec["node_class"]))
            f.write('        "inputs": [\n')
            for (n, t, d, dv) in spec["inputs"]:
                if dv is not None:
                    f.write("            (%s, %s, %s, %s),\n"
                            % (py_str(n), py_str(t), py_str(d), py_str(dv)))
                else:
                    f.write("            (%s, %s, %s),\n"
                            % (py_str(n), py_str(t), py_str(d)))
            f.write("        ],\n")
            f.write('        "outputs": [\n')
            for (n, t, d) in spec["outputs"]:
                f.write("            (%s, %s, %s),\n"
                        % (py_str(n), py_str(t), py_str(d)))
            f.write("        ],\n")
            f.write("    },\n\n")
        f.write("]\n\n")
        f.write("for _s in SPECS:\n")
        f.write("    _s[\"on_exists\"] = \"keep\"\n")
        f.write("n_ok, n_bad = api.register.node.bulk(SPECS)\n")
        f.write("print(\"Registered %d / %d templates\" "
                "% (n_ok, n_ok + n_bad))\n")
        f.write(FOOTER)


# ============================================================== #
#  emit — JSON + loader                                          #
# ============================================================== #

def _spec_to_json(spec):
    d = {
        "name":        spec["name"],
        "color":       spec["color"],
        "category":    spec["category"],
        "description": spec["description"],
        "qualname":    spec.get("qualname"),
        "inputs":      [list(t) for t in spec["inputs"]],
        "outputs":     [list(t) for t in spec["outputs"]],
        "full_path":   spec["full_path"],
    }
    if spec.get("node_class"):
        d["node_class"] = spec["node_class"]
    return d


def emit_json(specs, out_file, json_path,
              torch_version, torch_path, packages):
    date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "format":        "pytorchui-nodes",
        "version":       1,
        "torch_version": torch_version,
        "torch_path":    torch_path,
        "generated":     date,
        "node_count":    len(specs),
        "libraries":     [name for _cat, name in packages],
        "builtin_roots": [name for _cat, name in EXTRA_ROOTS],
        "nodes":         [_spec_to_json(s) for s in specs],
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    header = (HEADER_LOADER
              .replace("{json_path!r}", repr(json_path))
              .replace("{db_path!r}", repr(DB_FILE))
              .replace("{data_file}", json_path)
              .replace("{data_format}", "json")
              .replace("{version}", torch_version)
              .replace("{torch_path}", torch_path)
              .replace("{date}", date)
              .replace("{node_count}", str(len(specs))))
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(header)
        f.write(FOOTER)


# ============================================================== #
#  emit — SQLite + loader                                        #
# ============================================================== #

def emit_db(specs, out_file, db_path,
            torch_version, torch_path, packages):
    """Append or update a SQLite database.

    Unlike a fresh emit, this mode is designed to accumulate:

      * missing parent directories are created first, which fixes
        the "unable to open database file" error when db_path is
        something like  data/main.db  and data/ does not yet exist;
      * the file is not deleted — existing nodes stay, new ones are
        added, matching names are left alone;
      * schema and index creation uses IF NOT EXISTS so running
        twice is a no-op;
      * metadata rows are upserted with INSERT OR REPLACE, and
        node_count reflects the total after the run.

    To force a fresh rebuild, delete the file first:

        rm data/main.db
    """
    date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Fix: create parent directory if missing.  sqlite3.connect()
    # will fail with "unable to open database file" if any parent
    # of db_path does not exist.
    db_path = os.path.abspath(db_path)
    parent = os.path.dirname(db_path)
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
            print("  created directory %s" % parent)
        except OSError as ex:
            print("  ! could not create %s: %s" % (parent, ex))
            raise

    # If the file exists but is not writable, sqlite3.connect
    # still succeeds — the failure happens on the first write with
    # the unhelpful message "unable to open database file".  Catch
    # the two common causes up front and report something useful.
    if os.path.exists(db_path):
        if not os.access(db_path, os.W_OK):
            print("  ! %s is not writable" % db_path)
            try:
                import pwd
                st = os.stat(db_path)
                owner = pwd.getpwuid(st.st_uid).pw_name
                me = pwd.getpwuid(os.getuid()).pw_name
                if owner != me:
                    print("    owned by %r, running as %r" % (owner, me))
                    print("    fix:  sudo chown %s %s" % (me, db_path))
                else:
                    print("    mode is %o; fix: chmod u+w %s"
                          % (st.st_mode & 0o777, db_path))
            except Exception:
                pass
            raise PermissionError(db_path)

    print("  opening %s" % db_path)
    con = sqlite3.connect(db_path)
    # WAL allows readers while a writer is active — main.py can keep
    # reading the DB while build.py or create.py writes to it.
    try:
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA synchronous = NORMAL")
        con.execute("PRAGMA busy_timeout = 5000")
    except sqlite3.OperationalError:
        pass
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                color TEXT,
                category TEXT,
                description TEXT,
                qualname TEXT,
                node_class TEXT,
                inputs TEXT,
                outputs TEXT,
                full_path TEXT,
                UNIQUE(name)
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_nodes_category "
                    "ON nodes(category)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_nodes_full_path "
                    "ON nodes(full_path)")

        # FTS5 virtual table.  IF NOT EXISTS is supported since
        # SQLite 3.9.0.
        fts_ok = False
        try:
            con.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS node_search USING fts5(
                    name,
                    description,
                    category,
                    content='nodes',
                    content_rowid='id',
                    tokenize='unicode61 remove_diacritics 2'
                )
            """)
            fts_ok = True
        except sqlite3.OperationalError as ex:
            print("  ! FTS5 unavailable (%s); writing plain table" % ex)

        if fts_ok:
            con.execute("""
                CREATE TRIGGER IF NOT EXISTS nodes_ai
                AFTER INSERT ON nodes BEGIN
                    INSERT INTO node_search(
                        rowid, name, description, category)
                    VALUES (new.id, new.name, new.description,
                            new.category);
                END
            """)
            con.execute("""
                CREATE TRIGGER IF NOT EXISTS nodes_ad
                AFTER DELETE ON nodes BEGIN
                    INSERT INTO node_search(
                        node_search, rowid, name, description, category)
                    VALUES ('delete', old.id, old.name,
                            old.description, old.category);
                END
            """)
            con.execute("""
                CREATE TRIGGER IF NOT EXISTS nodes_au
                AFTER UPDATE ON nodes BEGIN
                    INSERT INTO node_search(
                        node_search, rowid, name, description, category)
                    VALUES ('delete', old.id, old.name,
                            old.description, old.category);
                    INSERT INTO node_search(
                        rowid, name, description, category)
                    VALUES (new.id, new.name, new.description,
                            new.category);
                END
            """)

        # Snapshot existing rows so we can classify each spec as
        # new / changed / unchanged before writing anything.
        existing = {}
        for row in con.execute(
                "SELECT name, color, category, description, qualname, "
                "node_class, inputs, outputs, full_path FROM nodes"):
            existing[row[0]] = row[1:]

        rows = []
        added = updated = unchanged = 0
        for s in specs:
            new_row = (
                s["color"], s["category"], s["description"],
                s.get("qualname"), s.get("node_class"),
                json.dumps([list(t) for t in s["inputs"]]),
                json.dumps([list(t) for t in s["outputs"]]),
                s["full_path"],
            )
            old = existing.get(s["name"])
            if old is None:
                added += 1
            elif tuple(old) == new_row:
                unchanged += 1
            else:
                updated += 1
            rows.append((s["name"],) + new_row)

        if rows:
            # Upsert: new rows are inserted; existing rows whose
            # content changed are updated in place; existing rows
            # with identical content are left alone.  The WHERE
            # clause on the DO UPDATE is what makes "unchanged"
            # actually a no-op at the SQLite level, which keeps the
            # FTS triggers quiet for rows that did not move.
            con.executemany(
                "INSERT INTO nodes "
                "(name, color, category, description, qualname, "
                " node_class, inputs, outputs, full_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET "
                "  color = excluded.color, "
                "  category = excluded.category, "
                "  description = excluded.description, "
                "  qualname = excluded.qualname, "
                "  node_class = excluded.node_class, "
                "  inputs = excluded.inputs, "
                "  outputs = excluded.outputs, "
                "  full_path = excluded.full_path "
                "WHERE nodes.color       IS NOT excluded.color "
                "   OR nodes.category    IS NOT excluded.category "
                "   OR nodes.description IS NOT excluded.description "
                "   OR nodes.qualname    IS NOT excluded.qualname "
                "   OR nodes.node_class  IS NOT excluded.node_class "
                "   OR nodes.inputs      IS NOT excluded.inputs "
                "   OR nodes.outputs     IS NOT excluded.outputs "
                "   OR nodes.full_path   IS NOT excluded.full_path",
                rows,
            )
        con.commit()

        total = con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]

        # Upsert metadata.  node_count reflects the total after the
        # upsert, not just what was passed in.
        for k, v in (
            ("format", "pytorchui-nodes"),
            ("version", "2"),
            ("torch_version", torch_version),
            ("torch_path", torch_path),
            ("generated", date),
            ("node_count", str(total)),
        ):
            con.execute(
                "INSERT OR REPLACE INTO metadata VALUES (?, ?)",
                (k, v))
        con.commit()

        # Merge FTS5 b-tree segments into one for fast queries.
        if fts_ok:
            try:
                con.execute(
                    "INSERT INTO node_search(node_search) "
                    "VALUES('optimize')")
                con.commit()
            except sqlite3.OperationalError:
                pass

        print("  total %d nodes, %d new, %d updated, %d unchanged"
              % (total, added, updated, unchanged))
    finally:
        con.close()

    with open(out_file, "w", encoding="utf-8") as f:
        _hdr = (HEADER_LOADER
                .replace("{json_path!r}", repr(JSON_FILE))
                .replace("{db_path!r}", repr(db_path))
                .replace("{data_file}", db_path)
                .replace("{data_format}", "db")
                .replace("{version}", torch_version)
                .replace("{torch_path}", torch_path)
                .replace("{date}", date)
                .replace("{node_count}", str(total)))
        f.write(_hdr)
        f.write(FOOTER)


# ============================================================== #
#  Main                                                          #
# ============================================================== #

def _read_db_metadata(path):
    """Return the metadata table from a PyTorchUI db as a dict.

    Empty dict if the file is missing or malformed — the caller
    falls back to defaults.
    """
    if not os.path.isfile(path):
        return {}
    try:
        con = sqlite3.connect(path)
        try:
            rows = con.execute(
                "SELECT key, value FROM metadata").fetchall()
        finally:
            con.close()
        return {str(k): str(v) for k, v in rows}
    except Exception:
        return {}


def _read_json_metadata(path):
    """Return the top-level metadata from a PyTorchUI json dump."""
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return {
            "torch_version": str(d.get("torch_version", "n/a")),
            "torch_path":    str(d.get("torch_path", "n/a")),
            "node_count":    str(d.get("node_count", 0)),
        }
    except Exception:
        return {}


def emit_loader_only(out_file, data_file, data_format,
                     version, path, node_count,
                     json_path=None, db_path=None):
    """Emit just the loader main.py, no walking, no package imports.

    For format=db   the loader tries main.json first, then main.db.
    For format=json the loader tries the given json path, then the
                    default db path.
    """
    date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # The loader template already substitutes both fallback paths.
    # For db output, we leave the json fallback at its default.
    _json = json_path if json_path is not None else JSON_FILE
    _db   = db_path if db_path is not None else DB_FILE

    with open(out_file, "w", encoding="utf-8") as f:
        header = (HEADER_LOADER
                  .replace("{json_path!r}", repr(_json))
                  .replace("{db_path!r}",   repr(_db))
                  .replace("{data_file}",   data_file)
                  .replace("{data_format}", data_format)
                  .replace("{version}",     version)
                  .replace("{torch_path}",  path)
                  .replace("{date}",        date)
                  .replace("{node_count}",  str(node_count)))
        f.write(header)
        f.write(FOOTER)
    return out_file


# ============================================================== #
#  Library management subcommands                                 #
# ============================================================== #

def _open_db_rw(db_path, use_wal=True):
    """Open a connection suitable for writing.

    WAL mode lets main.py keep reading while we write, so a build can
    run alongside the editor without blocking it.
    """
    con = sqlite3.connect(db_path)
    if use_wal:
        try:
            con.execute("PRAGMA journal_mode = WAL")
            con.execute("PRAGMA synchronous = NORMAL")
            con.execute("PRAGMA busy_timeout = 5000")
        except sqlite3.OperationalError:
            pass
    return con


def _lib_installed_version(lib):
    """Return the pip-installed version of LIB or None."""
    try:
        from importlib.metadata import version, PackageNotFoundError
    except ImportError:
        return None
    try:
        return version(lib)
    except PackageNotFoundError:
        return None
    except Exception:
        return None


def _lib_registered_count(db_path, lib):
    """Count nodes whose category starts with LIB (case-sensitive)."""
    if not os.path.isfile(db_path):
        return 0
    try:
        con = _open_db_rw(db_path, use_wal=False)
    except Exception:
        return 0
    try:
        try:
            row = con.execute(
                "SELECT COUNT(*) FROM nodes WHERE "
                "category = ? OR category LIKE ?",
                (lib, lib + "/%")).fetchone()
            return int(row[0]) if row else 0
        except sqlite3.OperationalError:
            return 0
    finally:
        con.close()


def _check_libs(db_path, libs, quiet=False):
    for lib in libs:
        ver = _lib_installed_version(lib) or "-"
        n = _lib_registered_count(db_path, lib)
        print("%-24s installed=%-12s nodes=%d"
              % (lib, ver, n))


def _list_registered_libs(db_path, quiet=False):
    if not os.path.isfile(db_path):
        print("! no database at %s" % db_path)
        return
    con = _open_db_rw(db_path, use_wal=False)
    try:
        try:
            rows = con.execute(
                "SELECT "
                "  CASE WHEN INSTR(category, '/') > 0 "
                "    THEN SUBSTR(category, 1, INSTR(category, '/') - 1) "
                "    ELSE category END AS root, "
                "  COUNT(*) AS n "
                "FROM nodes GROUP BY root ORDER BY n DESC"
            ).fetchall()
        except sqlite3.OperationalError:
            print("! no nodes table")
            return
        print("%-40s %s" % ("LIBRARY", "NODES"))
        print("-" * 50)
        for lib, n in rows:
            print("%-40s %d" % (lib, n))
    finally:
        con.close()


def _register_libs(db_path, libs, quiet=False, no_wal=False):
    """Walk each lib and upsert its nodes into the DB."""
    import tempfile
    for lib in libs:
        if not quiet:
            print("register: %s" % lib)
        cat = lib.split(".")[0]
        specs = collect([(cat, lib)], MAX_DEPTH, PER_PACKAGE_CAP,
                        quiet, deep_c=False)
        if not specs:
            print("  ! no nodes collected from %s" % lib)
            continue
        # emit_db writes the loader to out_file; we use a throwaway.
        fd, tmp_loader = tempfile.mkstemp(
            suffix=".py", prefix="pytorchui_loader_")
        os.close(fd)
        try:
            emit_db(specs, tmp_loader, db_path,
                    "n/a (register)", "n/a",
                    [(cat, lib)])
        finally:
            try:
                os.remove(tmp_loader)
            except Exception:
                pass
        if not quiet:
            print("  -> %d node(s)" % len(specs))


def _unregister_libs(db_path, libs, quiet=False, no_wal=False):
    if not os.path.isfile(db_path):
        print("! no database at %s" % db_path)
        return
    con = _open_db_rw(db_path, use_wal=not no_wal)
    try:
        for lib in libs:
            cur = con.execute(
                "DELETE FROM nodes WHERE "
                "category = ? OR category LIKE ?",
                (lib, lib + "/%"))
            if not quiet:
                print("unregister: %s -> %d row(s)"
                      % (lib, cur.rowcount))
        con.commit()
        # FTS cleanup
        try:
            con.execute("INSERT INTO node_search(node_search) "
                        "VALUES('optimize')")
            con.commit()
        except sqlite3.OperationalError:
            pass
    finally:
        con.close()



def main(argv=None):
    global _DEBUG, _LIST_ONLY
    warnings.filterwarnings("ignore")
    parser = _build_parser()
    args = parser.parse_args(argv)

    _DEBUG     = bool(getattr(args, "debug", False))
    _LIST_ONLY = bool(getattr(args, "list_modules", False))

    # ---- library management subcommands ---- #
    if getattr(args, "list_libs", False):
        _list_registered_libs(args.db_path, args.quiet)
        return

    if getattr(args, "check_lib", None):
        _check_libs(args.db_path, args.check_lib, args.quiet)
        return

    if getattr(args, "register_lib", None):
        _register_libs(args.db_path, args.register_lib,
                       args.quiet, getattr(args, "no_wal", False))
        return

    if getattr(args, "unregister_lib", None):
        _unregister_libs(args.db_path, args.unregister_lib,
                         args.quiet, getattr(args, "no_wal", False))
        return

    fmt = _resolve_format(args)

    # ---- fast path: emit only the loader, no walk ---- #
    if getattr(args, "loader_only", False):
        if fmt == "py":
            print("! --loader-only requires --db or --json")
            sys.exit(2)

        # Read metadata from whichever data file we already have.
        meta = {}
        if fmt == "db":
            meta = _read_db_metadata(args.db_path)
            if not meta:
                meta = _read_json_metadata(args.json_path)
            data_file = args.db_path
            json_path = args.json_path
            db_path   = args.db_path
        else:
            meta = _read_json_metadata(args.json_path)
            if not meta:
                meta = _read_db_metadata(args.db_path)
            data_file = args.json_path
            json_path = args.json_path
            db_path   = args.db_path

        if not meta:
            print("! no existing %s found; run a full build first"
                  % (data_file,))
            sys.exit(1)

        version    = meta.get("torch_version", "n/a")
        path       = meta.get("torch_path",    "n/a")
        node_count = meta.get("node_count",    "0")

        emit_loader_only(
            out_file=args.out_file,
            data_file=data_file,
            data_format=fmt,
            version=version,
            path=path,
            node_count=node_count,
            json_path=json_path,
            db_path=db_path,
        )

        if not args.quiet:
            print("output format:", fmt, "(loader-only)")
            print("data file:    ", data_file)
            print("nodes:        ", node_count)
            print("Wrote", args.out_file)
        return

    cap_map = _parse_cap_libs(args.cap_libs)

    extra_libs = _flatten_libraries(args.libraries)
    packages = build_packages(extra_libs, no_torch=args.no_torch)

    if not packages:
        print("! nothing to walk.  Use -l LIBRARY, or drop --no-torch.")
        sys.exit(2)

    if _LIST_ONLY:
        print("Modules discovery would visit:")
        for cat, pkg in packages:
            print()
            print("  %-40s (category %s)" % (pkg, cat))
            mods = discover_modules(cat, pkg, args.max_depth, quiet=True)
            for mcat, mod_path, depth in mods:
                indent = "  " * (depth + 1)
                print("    %s%-50s -> %s" % (indent, mod_path, mcat))
        return

    if not args.quiet:
        print("output format:", fmt)
        if args.no_torch:
            print("torch:         SKIPPED (--no-torch)")
        if args.no_cap:
            print("cap:           NONE (--no-cap)")
        else:
            print("cap:           %d per package" % args.cap)
        if cap_map:
            print("cap overrides:")
            for k, v in sorted(cap_map.items()):
                print("  %-20s  %d" % (k, v))
        print("roots to walk:")
        for cat, name in packages:
            print("  %-20s  ->  %s" % (name, cat))
        print()

    version = "n/a (not walked)"
    path = "n/a"
    try:
        import torch
        version = getattr(torch, "__version__", "unknown")
        path = getattr(torch, "__file__", "?")
    except ImportError:
        if not args.no_torch:
            print("torch is not installed for this interpreter.")
            print("Either install it, or pass --no-torch / -nt to skip.")
            sys.exit(1)
    if not args.quiet:
        print("torch %s at %s" % (version, path))

    specs = collect(
        packages,
        args.max_depth,
        args.cap,
        args.quiet,
        cap_map=cap_map,
        no_cap=args.no_cap,
        deep_c=getattr(args, "deep_c", False),
    )
    if not args.quiet:
        print("Collected %d nodes total" % len(specs))
        from collections import Counter
        c = Counter(s["category"] for s in specs)
        for cat, n in sorted(c.items()):
            print("  %-30s %d nodes" % (cat, n))

    # The database is append-only — do NOT delete it before emit.
    # Only the loader is cleared, since it is regenerated from
    # scratch.  If fmt is json, the json data file is rewritten by
    # emit_json's open(..., "w"), so it does not need to be removed
    # here either.
    _cleanup_targets = [args.out_file]
    for p in _cleanup_targets:
        if os.path.isfile(p):
            try:
                os.remove(p)
                if not args.quiet:
                    print("Removed old %s" % p)
            except OSError as ex:
                print("  ! could not remove %s: %s" % (p, ex))

    if fmt == "py":
        emit_python(specs, args.out_file, version, path, packages)
        with open(args.out_file, "r", encoding="utf-8") as f:
            n = sum(1 for _ in f)
        if not args.quiet:
            print("Wrote %s (%d lines)" % (args.out_file, n))
    elif fmt == "json":
        emit_json(specs, args.out_file, args.json_path,
                  version, path, packages)
        if not args.quiet:
            print("Wrote %s (data)" % args.json_path)
            print("Wrote %s (loader)" % args.out_file)
    elif fmt == "db":
        emit_db(specs, args.out_file, args.db_path,
                version, path, packages)
        if not args.quiet:
            print("Wrote %s (data)" % args.db_path)
            print("Wrote %s (loader)" % args.out_file)

    if not args.quiet:
        print()
        print("Next:")
        print("  python main.py")


if __name__ == "__main__":
    main()
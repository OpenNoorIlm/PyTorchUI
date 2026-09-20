"""
Blender-style Node Editor for PyQt5
-----------------------------------
Full application: canvas, library, sidebar, help dock, codegen, runtime.

Features
  * Blender-styled nodes: rounded header, soft shadow, sections, Flow sockets
  * Sockets with type colours, Bezier edges, one-input-one-edge rule
  * Hover tooltips (200-word truncation + F1 for the full doc)
  * Help dock: markdown, LaTeX via matplotlib, Python/C++ highlighting
  * Settings: Hide Path Wires, Auto Save, Save After Change, Show Block Slot,
              Auto Reroute, Ask Before Reroute
  * Blocks (nested child nodes), Auto reroute on drop, Lifecycle hooks
  * Literal values on input sockets, Dynamic add/remove sockets
  * Per-node SHELL OUTPUT strip with collapse
  * Runtime protocol: generated code reports begin/output/end/error
  * Threaded runner: streams markers to each node, dialog only on error
  * Codegen: qualname, category-driven imports, positional comma values,
             module forward calls, single-output binds
  * APICli: headless subclass of API, for building graphs offline
"""

import os
import re
import sys
import json
import glob
import shutil
import struct
import subprocess
import copy as _copy
import weakref

from PyQt5 import sip
from PyQt5.QtCore import (Qt, QRectF, QRect, QPointF, QPoint, QLineF, QSize,
                          pyqtSignal, QObject, QThread, QEventLoop,
                          QTimer, QEvent, QEasingCurve)
from PyQt5.QtGui import (QColor, QPen, QBrush, QPainter, QPainterPath, QFont,
                         QFontMetrics, QLinearGradient, QPolygonF, QTransform,
                         QCursor, QKeySequence)
from PyQt5.QtWidgets import (QApplication, QMainWindow, QGraphicsView,
                             QGraphicsScene, QGraphicsItem, QGraphicsPathItem,
                             QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QLineEdit, QPlainTextEdit, QPushButton,
                             QComboBox, QCheckBox,
                             QScrollArea, QSplitter, QColorDialog, QStyle,
                             QFrame, QSizePolicy, QToolButton, QMenu,
                             QTreeWidget, QTreeWidgetItem, QFileDialog,
                             QMessageBox, QAction, QWidgetAction, QStatusBar,
                             QToolBar, QShortcut, QDockWidget, QTextEdit,
                             QTextBrowser, QDialog,
                             QListWidget, QListWidgetItem,
)


# --------------------------------------------------------------------------- #
#  Runtime protocol                                                           #
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
#  Value summarization for runtime reports                                    #
# --------------------------------------------------------------------------- #

def _short_repr(value):
    """Single-line, collapsed representation of a value."""
    try:
        r = repr(value)
    except Exception:
        try:
            r = str(value)
        except Exception:
            return "<unrepresentable>"
    # collapse all whitespace so multi-line tensors become one line
    r = " ".join(r.split())
    if len(r) > 400:
        r = r[:397] + "..."
    return r


def _long_repr(value):
    """Multi-line representation, good for the full-output dialog."""
    try:
        import torch
        if isinstance(value, torch.Tensor):
            try:
                torch.set_printoptions(edgeitems=30, linewidth=240,
                                       precision=4, sci_mode=True)
            except Exception:
                pass
            return repr(value)
    except Exception:
        pass
    try:
        return repr(value)
    except Exception:
        try:
            return str(value)
        except Exception:
            return "<unrepresentable>"


# --------------------------------------------------------------------- #
#  Built-in node definitions                                            #
# --------------------------------------------------------------------- #
BUILTINS = [('Built-ins/IO', 'print', 'print', None, [('Value', 'any', 'Value to print', None)], [], 'Print a value to stdout.  Captured by the run panel.'), ('Built-ins/Basics', 'len', 'len', None, [('Object', 'any', 'Sequence or container', None)], [('Length', 'int', 'Number of items')], 'Length of a sequence, string or container.'), ('Built-ins/Basics', 'range', 'range', None, [('Start', 'int', 'Start (inclusive)', '0'), ('Stop', 'int', 'Stop (exclusive)', '10'), ('Step', 'int', 'Step', '1')], [('Range', 'any', 'Range object')], 'Generate a range of integers.'), ('Built-ins/Basics', 'isinstance', 'isinstance', None, [('Object', 'any', 'Value', None), ('Type', 'any', 'Type name (a Python expression)', 'int')], [('Result', 'bool', 'Is instance of type')], 'Check whether an object is an instance of a type.'), ('Built-ins/Math', 'abs', 'abs', None, [('x', 'float', 'Number', None)], [('Result', 'float', 'Absolute value')], 'Absolute value.'), ('Built-ins/Math', 'round', 'round', None, [('x', 'float', 'Number', None), ('Digits', 'int', 'Decimal places', '0')], [('Result', 'float', 'Rounded')], 'Round a number to the given precision.'), ('Built-ins/Math', 'sum', 'sum', None, [('Iterable', 'any', 'Iterable of numbers', None)], [('Total', 'float', 'Sum')], 'Sum of an iterable.'), ('Built-ins/Math', 'min', 'min', None, [('A', 'any', 'First', None), ('B', 'any', 'Second', None)], [('Result', 'any', 'Smaller')], 'Smaller of two values.'), ('Built-ins/Math', 'max', 'max', None, [('A', 'any', 'First', None), ('B', 'any', 'Second', None)], [('Result', 'any', 'Larger')], 'Larger of two values.'), ('Built-ins/Math', 'pow', 'pow', None, [('Base', 'float', 'Base', None), ('Exp', 'float', 'Exponent', None)], [('Result', 'float', 'Base ** Exp')], 'Raise to a power.'), ('Built-ins/Trig', 'sqrt', 'math.sqrt', None, [('x', 'float', 'Number >= 0', None)], [('Result', 'float', 'Square root')], 'Square root.'), ('Built-ins/Trig', 'sin', 'math.sin', None, [('x', 'float', 'Radians', None)], [('Result', 'float', 'Sine')], 'Sine.'), ('Built-ins/Trig', 'cos', 'math.cos', None, [('x', 'float', 'Radians', None)], [('Result', 'float', 'Cosine')], 'Cosine.'), ('Built-ins/Trig', 'tan', 'math.tan', None, [('x', 'float', 'Radians', None)], [('Result', 'float', 'Tangent')], 'Tangent.'), ('Built-ins/Trig', 'atan2', 'math.atan2', None, [('y', 'float', 'Y', None), ('x', 'float', 'X', None)], [('Result', 'float', 'Angle (radians)')], 'Two-argument arctangent.'), ('Built-ins/Trig', 'radians', 'math.radians', None, [('x', 'float', 'Degrees', None)], [('Result', 'float', 'Radians')], 'Degrees to radians.'), ('Built-ins/Trig', 'degrees', 'math.degrees', None, [('x', 'float', 'Radians', None)], [('Result', 'float', 'Degrees')], 'Radians to degrees.'), ('Built-ins/Random', 'randint', 'random.randint', None, [('a', 'int', 'Low (inclusive)', '0'), ('b', 'int', 'High (inclusive)', '100')], [('Result', 'int', 'Random int')], 'Random integer in [a, b].'), ('Built-ins/Random', 'random', 'random.random', None, [], [('Result', 'float', 'Random float in [0, 1)')], 'Random float in [0, 1).'), ('Built-ins/Random', 'choice', 'random.choice', None, [('Sequence', 'any', 'Non-empty sequence', None)], [('Result', 'any', 'Random element')], 'Pick a random element from a sequence.'), ('Built-ins/Random', 'shuffle', 'random.shuffle', None, [('Sequence', 'any', 'List to shuffle in place', None)], [], 'Shuffle a list in place.'), ('Built-ins/Convert', 'int', 'int', None, [('Value', 'any', 'Value to convert', None)], [('Result', 'int', 'Integer')], 'Convert to int.'), ('Built-ins/Convert', 'float', 'float', None, [('Value', 'any', 'Value to convert', None)], [('Result', 'float', 'Float')], 'Convert to float.'), ('Built-ins/Convert', 'str', 'str', None, [('Value', 'any', 'Value to convert', None)], [('Result', 'string', 'String')], 'Convert to string.'), ('Built-ins/Convert', 'bool', 'bool', None, [('Value', 'any', 'Value to convert', None)], [('Result', 'bool', 'Boolean')], 'Convert to bool.'), ('Built-ins/Convert', 'list', 'list', None, [('Iterable', 'any', 'Iterable', None)], [('Result', 'any', 'List')], 'Convert an iterable to a list.'), ('Built-ins/Convert', 'dict', 'dict', None, [('Pairs', 'any', 'Iterable of (key, value) pairs', None)], [('Result', 'any', 'Dictionary')], 'Build a dictionary from pairs.'), ('Built-ins/String', 'upper', None, '{Text}.upper()', [('Text', 'string', 'Text', None)], [('Result', 'string', 'Uppercase')], 'Uppercase text.'), ('Built-ins/String', 'lower', None, '{Text}.lower()', [('Text', 'string', 'Text', None)], [('Result', 'string', 'Lowercase')], 'Lowercase text.'), ('Built-ins/String', 'strip', None, '{Text}.strip()', [('Text', 'string', 'Text', None)], [('Result', 'string', 'Trimmed')], 'Trim leading and trailing whitespace.'), ('Built-ins/String', 'split', None, '{Text}.split({Sep})', [('Text', 'string', 'Text', None), ('Sep', 'string', 'Separator', ' ')], [('Result', 'any', 'List of parts')], 'Split text into a list.'), ('Built-ins/String', 'join', None, '{Sep}.join({Items})', [('Sep', 'string', 'Separator', None), ('Items', 'any', 'Iterable of strings', None)], [('Result', 'string', 'Joined text')], 'Join a sequence of strings.'), ('Built-ins/String', 'replace', None, '{Text}.replace({Old}, {New})', [('Text', 'string', 'Text', None), ('Old', 'string', 'Old substring', None), ('New', 'string', 'New substring', None)], [('Result', 'string', 'Replaced')], 'Replace every occurrence of a substring.'), ('Built-ins/String', 'startswith', None, '{Text}.startswith({Prefix})', [('Text', 'string', 'Text', None), ('Prefix', 'string', 'Prefix', None)], [('Result', 'bool', 'Starts with prefix')], 'Check whether text starts with a prefix.'), ('Built-ins/String', 'endswith', None, '{Text}.endswith({Suffix})', [('Text', 'string', 'Text', None), ('Suffix', 'string', 'Suffix', None)], [('Result', 'bool', 'Ends with suffix')], 'Check whether text ends with a suffix.'), ('Built-ins/String', 'find', None, '{Text}.find({Sub})', [('Text', 'string', 'Text', None), ('Sub', 'string', 'Substring to find', None)], [('Result', 'int', 'Index or -1')], "Find a substring's first index, or -1."), ('Built-ins/List', 'append', None, '{List}.append({Item})', [('List', 'any', 'List to modify', None), ('Item', 'any', 'Item to append', None)], [], 'Append an item to a list (in place).'), ('Built-ins/List', 'extend', None, '{List}.extend({Items})', [('List', 'any', 'List to modify', None), ('Items', 'any', 'Iterable to append', None)], [], 'Extend a list with items (in place).'), ('Built-ins/List', 'pop', None, '{List}.pop({Index})', [('List', 'any', 'List to modify', None), ('Index', 'int', 'Index to remove', '-1')], [('Result', 'any', 'Removed item')], 'Remove and return the item at the given index.'), ('Built-ins/List', 'index', None, '{List}.index({Item})', [('List', 'any', 'List to search', None), ('Item', 'any', 'Item to find', None)], [('Result', 'int', 'First index')], 'First index of an item.  Raises if missing.'), ('Built-ins/List', 'count', None, '{List}.count({Item})', [('List', 'any', 'List to search', None), ('Item', 'any', 'Item to count', None)], [('Result', 'int', 'Occurrences')], 'Count occurrences of an item.'), ('Built-ins/List', 'sort', None, '{List}.sort()', [('List', 'any', 'List to sort in place', None)], [], 'Sort a list in place.'), ('Built-ins/List', 'reverse', None, '{List}.reverse()', [('List', 'any', 'List to reverse in place', None)], [], 'Reverse a list in place.'), ('Built-ins/List', 'sorted', 'sorted', None, [('Iterable', 'any', 'Iterable to sort', None)], [('Result', 'any', 'New sorted list')], 'Return a new sorted list.'), ('Built-ins/List', 'reversed', 'reversed', None, [('Iterable', 'any', 'Iterable', None)], [('Result', 'any', 'Reversed iterator')], 'Return a reversed iterator.'), ('Built-ins/Dict', 'get', None, '{Dict}.get({Key}, {Default})', [('Dict', 'any', 'Dictionary', None), ('Key', 'any', 'Key to look up', None), ('Default', 'any', 'Value if missing', 'None')], [('Result', 'any', 'Value or default')], 'Get a value by key, with a default.'), ('Built-ins/Dict', 'keys', None, '{Dict}.keys()', [('Dict', 'any', 'Dictionary', None)], [('Result', 'any', 'Keys view')], 'Return the keys of a dictionary.'), ('Built-ins/Dict', 'values', None, '{Dict}.values()', [('Dict', 'any', 'Dictionary', None)], [('Result', 'any', 'Values view')], 'Return the values of a dictionary.'), ('Built-ins/Dict', 'items', None, '{Dict}.items()', [('Dict', 'any', 'Dictionary', None)], [('Result', 'any', '(key, value) pairs')], 'Return the items of a dictionary.')]


# --------------------------------------------------------------------------- #
#  Built-in nodes  (Python stdlib)                                            #
# --------------------------------------------------------------------------- #

def _register_builtins(api):
    """
    Register every node in BUILTINS.

    Uses the existing register_node() signature; each entry becomes one
    template under the given category.  Inputs without a default get no
    literal value, so they must be wired before the run.
    """

    def _in_list(pairs):
        out = []
        for tup in pairs:
            name, t, desc, default = tup
            if default is None:
                out.append((name, t, desc))
            else:
                out.append((name, t, desc, default))
        return out

    for cat, name, qualname, call, inputs, outputs, desc in BUILTINS:
        kwargs = dict(
            name=name,
            category=cat,
            color="#5A5A5A",
            description=desc,
            inputs=_in_list(inputs) if inputs else None,
            outputs=[(n, t, d) for (n, t, d) in outputs] if outputs else None,
            on_exists="replace",
        )
        if qualname:
            kwargs["qualname"] = qualname
        if call:
            kwargs["call"] = call
        try:
            api.register_node(**kwargs)
        except Exception as ex:
            print("  ! builtin %s: %s" % (name, ex))


# --------------------------------------------------------------------- #
#  Role nodes (values, variables, containers, functions, classes)       #
# --------------------------------------------------------------------- #

ROLES = [
    # ----- variables -----
    ("Built-ins/Variables", "Set Variable", "set",
     "**Set NAME = VALUE.**  Assigns to a variable.",
     [("Name", "string", "Variable name", "'x'"),
      ("Value", "any", "Value", None)], [], None, None),
    ("Built-ins/Variables", "Get Variable", "get",
     "**Get NAME.**  Reads a variable.",
     [("Name", "string", "Variable name", "'x'")],
     [("Value", "any", "The value")], None, None),
    ("Built-ins/Variables", "Delete", "del",
     "**del TARGET.**",
     [("Target", "string", "Target", "'x'")], [], None, None),
    ("Built-ins/Variables", "Global", "global_",
     "**global NAME.**",
     [("Name", "string", "Name", "'x'")], [], None, None),
    ("Built-ins/Variables", "Nonlocal", "nonlocal_",
     "**nonlocal NAME.**",
     [("Name", "string", "Name", "'x'")], [], None, None),

    # ----- operators -----
    ("Built-ins/Operators", "Binary Op", "binop",
     "**A op B.**",
     [("A", "any", "A", "0"), ("Op", "string", "Operator", "'+'"),
      ("B", "any", "B", "0")],
     [("Result", "any", "Result")], None, None),
    ("Built-ins/Operators", "Unary Op", "unaryop",
     "**op A.**",
     [("Op", "string", "Operator", "'-'"), ("Value", "any", "Value", "0")],
     [("Result", "any", "Result")], None, None),
    ("Built-ins/Operators", "Compare", "compare",
     "**A op B.**",
     [("A", "any", "A", "0"), ("Op", "string", "Operator", "'=='"),
      ("B", "any", "B", "0")],
     [("Result", "bool", "Result")], None, None),
    ("Built-ins/Operators", "Ternary", "ternary",
     "**A if cond else B.**",
     [("Condition", "bool", "Condition", "True"),
      ("Then", "any", "If true", "None"),
      ("Else", "any", "If false", "None")],
     [("Result", "any", "Selected")], None, None),
    ("Built-ins/Operators", "F-String", "fstring",
     "**f\"...\".**",
     [("Template", "string", "Template", "''")],
     [("Result", "string", "String")], None, None),

    # ----- objects -----
    ("Built-ins/Objects", "Get Attr", "attr_get",
     "**getattr(obj, attr).**\n\n"
     "Reads an attribute from any object:\n\n"
     "- instance variables: feed the instance into `Object`\n"
     "- class constants: feed the class into `Object`\n"
     "- for nested access (obj.a.b), use two Get Attr nodes",
     [("Object", "any", "Instance or class", "None"),
      ("Attr", "string", "Attribute name", "'x'")],
     [("Result", "any", "Value")], None, None),
    ("Built-ins/Objects", "Set Attr", "attr_set",
     "**setattr(obj, attr, value).**",
     [("Object", "any", "Object", "None"),
      ("Attr", "string", "Attr", "'x'"),
      ("Value", "any", "Value", "None")], [], None, None),
    ("Built-ins/Objects", "Method Call", "method_call",
     "**obj.method(*args, **kwargs).**\n\n"
     "- `Object` is any expression evaluating to an instance\n"
     "  (a **Call Class** output, a **Get Variable**, ...).\n"
     "- `Method` is the method name, without parentheses.\n"
     "- `Args` is a Python list of positional arguments.\n"
     "- `Kwargs` is a Python dict of keyword arguments.  Leave it\n"
     "  empty (`{}`) if the method takes none.",
     [("Object", "any", "Object", "None"),
      ("Method", "string", "Method name", "'append'"),
      ("Args", "any", "Positional args as a list", "[]"),
      ("Kwargs", "any", "Keyword args as a dict", "{}")],
     [("Result", "any", "Return value")], None, None),
    ("Built-ins/Objects", "Index", "index",
     "**obj[i].**",
     [("Object", "any", "Object", "[]"), ("Index", "any", "Index", "0")],
     [("Result", "any", "Element")], None, None),
    ("Built-ins/Objects", "Set Index", "index_set",
     "**obj[i] = v.**",
     [("Object", "any", "Object", "[]"), ("Index", "any", "Index", "0"),
      ("Value", "any", "Value", "None")], [], None, None),
    ("Built-ins/Objects", "Slice", "slice",
     "**obj[start:stop:step].**",
     [("Object", "any", "Object", "[]"), ("Start", "int", "Start", "0"),
      ("Stop", "int", "Stop", "0"), ("Step", "int", "Step", "1")],
     [("Result", "any", "Slice")], None, None),

    # ----- comprehensions -----
    ("Built-ins/Comprehensions", "List Comp", "comprehension_list",
     "**[expr for x in it if cond].**",
     [("Expr", "string", "Expression", "'x'"),
      ("Var", "string", "Loop var", "'x'"),
      ("Iterable", "any", "Iterable", "[]"),
      ("Condition", "string", "Filter", "''")],
     [("Result", "any", "List")], None, None),
    ("Built-ins/Comprehensions", "Dict Comp", "comprehension_dict",
     "**{k: v for x in it}.**",
     [("Key", "string", "Key", "'k'"), ("Val", "string", "Val", "'v'"),
      ("Var", "string", "Loop var", "'k'"),
      ("Iterable", "any", "Iterable", "[]")],
     [("Result", "any", "Dict")], None, None),
    ("Built-ins/Comprehensions", "Set Comp", "comprehension_set",
     "**{x for x in it}.**",
     [("Expr", "string", "Expr", "'x'"),
      ("Var", "string", "Loop var", "'x'"),
      ("Iterable", "any", "Iterable", "[]")],
     [("Result", "any", "Set")], None, None),
    ("Built-ins/Comprehensions", "Generator", "generator",
     "**(x for x in it).**",
     [("Expr", "string", "Expr", "'x'"),
      ("Var", "string", "Loop var", "'x'"),
      ("Iterable", "any", "Iterable", "[]")],
     [("Result", "any", "Gen")], None, None),

    # ----- control -----
    ("Built-ins/Control", "If", "if",
     "**if COND:** body.  Add `Else`/`Elif` at the body tail.",
     [("Condition", "bool", "Condition", "True")], [], None, None),
    ("Built-ins/Control", "Elif", "elif_marker",
     "**elif COND:**  Put at the end of an If body.",
     [("Condition", "bool", "Condition", "False")], [], None, None),
    ("Built-ins/Control", "Else", "else_marker",
     "**else:**  Put at the end of an If body.",
     [], [], None, None),
    ("Built-ins/Control", "For", "for",
     "**for VAR in ITERABLE:**",
     [("Var", "string", "Loop var", "'i'"),
      ("Iterable", "any", "Iterable", "[]")], [], None, None),
    ("Built-ins/Control", "While", "while",
     "**while COND:**",
     [("Condition", "bool", "Condition", "False")], [], None, None),
    ("Built-ins/Control", "Do", "do",
     "**Do.**  Bare block.", [], [], None, None),
    ("Built-ins/Control", "Break", "break", "**break.**",
     [], [], None, None),
    ("Built-ins/Control", "Continue", "continue", "**continue.**",
     [], [], None, None),
    ("Built-ins/Control", "Pass", "pass_stmt", "**pass.**",
     [], [], None, None),

    # ----- functions -----
    ("Built-ins/Functions", "Define Function", "def",
     "**def NAME(args):** body via blocks.",
     [("Name", "string", "Name", "'my_func'"),
      ("Args", "string", "Args", "'a, b'")], [], None, None),
    ("Built-ins/Functions", "Define Async Function", "async_def",
     "**async def NAME(args):** body via blocks.",
     [("Name", "string", "Name", "'my_func'"),
      ("Args", "string", "Args", "'a, b'")], [], None, None),
    ("Built-ins/Functions", "Call Function", "call_by_name",
     "**NAME(*args).**",
     [("Name", "string", "Name", "'my_func'"),
      ("Args", "any", "Args", "[]")],
     [("Result", "any", "Return")], None, None),
    ("Built-ins/Functions", "Return", "return", "**return VALUE.**",
     [("Value", "any", "Value", "None")], [], None, None),
    ("Built-ins/Functions", "Lambda", "lambda", "**lambda x: expr.**",
     [("Args", "string", "Args", "'x'"),
      ("Body", "string", "Body", "'x'")],
     [("Result", "any", "Lambda")], None, None),
    ("Built-ins/Functions", "Await", "await", "**await EXPR.**",
     [("Expr", "any", "Awaitable", "None")],
     [("Result", "any", "Result")], None, None),
    ("Built-ins/Functions", "Yield", "yield_value", "**yield VALUE.**",
     [("Value", "any", "Value", "None")],
     [("Result", "any", "Sent")], None, None),

    # ----- classes -----
    ("Built-ins/Classes", "Define Class", "class_",
     "**class NAME(bases):** body via blocks.\n\n"
     "**Bases** is a comma-separated list of parent classes, or an\n"
     "empty string for none:\n\n"
     "- no bases:      `''` (or leave blank)\n"
     "- one base:      `'QWidget'`\n"
     "- several:       `'QWidget, QObject'`\n"
     "- module path:   `'PyQt5.QtWidgets.QWidget'`\n\n"
     "Example: `Name=\"MyWindow\"`, `Bases=\"'QMainWindow'\"`\n"
     "emits `class MyWindow(QMainWindow):`.",
     [("Name", "string", "Class name", "'MyClass'"),
      ("Bases", "string", "Parent classes (empty for none)", "''")],
     [], None, None),

    # ---- self helpers ---- #
    ("Built-ins/Classes", "Self", "call",
     "**self.**  Reference the current instance inside a method body.\n\n"
     "Wire the output into **Call Self Method**, **Get Self Attr**,\n"
     "**Set Self Attr**, or the more general **Method Call** /\n"
     "**Get Attr** / **Set Attr** nodes.",
     [],
     [("Result", "any", "The instance (\"self\")")], None, "self"),
    ("Built-ins/Classes", "Set Self Attr", "self_set",
     "**self.NAME = VALUE.**  Assign an instance attribute.\n\n"
     "Reads naturally inside `__init__` or any method body.  Use\n"
     "**Set Attr** instead if you need to set an attribute on an\n"
     "object other than `self`.",
     [("Name", "string", "Attribute name", "'x'"),
      ("Value", "any", "Value to assign", "None")],
     [], None, None),
    ("Built-ins/Classes", "Get Self Attr", "self_get",
     "**self.NAME.**  Read an instance attribute.",
     [("Name", "string", "Attribute name", "'x'")],
     [("Result", "any", "Attribute value")], None, None),
    ("Built-ins/Classes", "Call Self Method", "self_method",
     "**self.METHOD(*args, **kwargs).**  Invoke a method on the\n"
     "current instance from inside another method.\n\n"
     "Example: inside `def update(self): ...` this emits\n"
     "`self.show()`, `self.resize(800, 600)`, etc.",
     [("Method", "string", "Method name", "'show'"),
      ("Args", "any", "Positional args as a list", "[]"),
      ("Kwargs", "any", "Keyword args as a dict", "{}")],
     [("Result", "any", "Return value")], None, None),
    ("Built-ins/Classes", "Call Class", "call_by_name",
     "**Instantiate a class.**  NAME(*args).\n\n"
     "- `NAME` may be a bare class name (`Point`) or a dotted\n"
     "  path (`math.Vector`, `subprocess.Popen`).\n"
     "- `Args` is a Python list of positional arguments:\n"
     "  `[3, 4]`, `[cmd, shell=True]`, or `[]` for none.\n"
     "- The output is the new instance; feed it into\n"
     "  **Method Call** or **Get Attr**.",
     [("Name", "string", "Class name or dotted path", "'Point'"),
      ("Args", "any", "Positional arguments as a list", "[]")],
     [("Instance", "any", "The newly created object")],
     None, None),
    ("Built-ins/Classes", "Class Attribute", "attr_get",
     "**Read a class-level attribute.**\n\n"
     "`getattr(ClassName, \"CONST\")` — class constants, class\n"
     "variables, classmethods, or staticmethods.  Feed the\n"
     "class (not an instance) into `Object`.",
     [("Object", "any", "A class object", "None"),
      ("Attr", "string", "Attribute name", "'CONST'")],
     [("Result", "any", "Attribute value")],
     None, None),

    # ----- exceptions -----
    ("Built-ins/Exceptions", "Try", "try",
     "**try:** body.  Add Except/Finally at the tail.", [], [], None, None),
    ("Built-ins/Exceptions", "Except", "except_marker",
     "**except Exc:**",
     [("Exception", "string", "Exception", "'Exception'")], [], None, None),
    ("Built-ins/Exceptions", "Finally", "finally_marker",
     "**finally:**", [], [], None, None),
    ("Built-ins/Exceptions", "Raise", "raise", "**raise Exc.**",
     [("Exception", "string", "Exc", "'Exception()'")], [], None, None),
    ("Built-ins/Exceptions", "Assert", "assert", "**assert C, MSG.**",
     [("Condition", "bool", "Condition", "True"),
      ("Message", "string", "Message", "''")], [], None, None),

    # ----- imports -----
    ("Built-ins/Imports", "Import", "import_stmt",
     "**import MOD.**",
     [("Module", "string", "Module", "'os'")], [], None, None),
    ("Built-ins/Imports", "From Import", "from_import",
     "**from MOD import NAME.**",
     [("Module", "string", "Module", "'os'"),
      ("Name", "string", "Name", "'path'")],
     [("Result", "any", "Imported")], None, None),

    # ----- with / match -----
    ("Built-ins/Context", "With", "with",
     "**with CTX as NAME:**",
     [("Context", "any", "Ctx", "None"),
      ("As", "string", "Name", "''")], [], None, None),
    ("Built-ins/Match", "Match", "match",
     "**match SUBJ:**  Add Case markers.",
     [("Subject", "any", "Subject", "None")], [], None, None),
    ("Built-ins/Match", "Case", "case_marker",
     "**case PATTERN:**",
     [("Pattern", "string", "Pattern", "'_'")], [], None, None),

    # ----- io -----
    ("Built-ins/IO", "input", "io",
     "**input(prompt).**  Pop a dialog and read the typed value.",
     [("Prompt", "string", "Prompt", "'Enter: '")],
     [("Result", "string", "Text")], None, None),

    # ----- files -----
    ("Built-ins/IO", "File Open", "file_open",
     "**open(path, mode).**  Returns a file handle.\n\n"
     "The `Mode` dropdown selects the standard Python mode flags.\n"
     "Use it with a **With** node to guarantee the file closes:\n"
     "wire the handle into `With.Context`, name it, then use\n"
     "**File Read** / **File Write** or a **Method Call** inside.\n\n"
     "Common modes:\n"
     "- `r`  read text (default)\n"
     "- `w`  write text, truncate\n"
     "- `a`  append text\n"
     "- `rb` / `wb` / `ab`  binary variants\n"
     "- `r+` / `w+`  read+write",
     [("Path", "string", "File path", "'data.txt'"),
      ("Mode", "string", "Open mode", "'r'",
       ["'r'", "'w'", "'a'", "'x'",
        "'rb'", "'wb'", "'ab'",
        "'r+'", "'w+'", "'rb+'", "'wb+'"])],
     [("File", "any", "File handle")], None, None),
    ("Built-ins/IO", "File Read", "file_read",
     "**Read entire file.**  Opens, reads, closes.",
     [("Path", "string", "File path", "'data.txt'")],
     [("Result", "string", "File contents")], None, None),
    ("Built-ins/IO", "File Write", "file_write",
     "**Write entire file.**  Opens, writes, closes.",
     [("Path", "string", "File path", "'out.txt'"),
      ("Content", "string", "Text to write", "''")],
     [], None, None),

    # ----- media -----
    ("Built-ins/Media", "Image Viewer", "image_viewer",
     "**Show an image.**\n\n"
     "Accept a path (str), a PIL image, or a torch Tensor.\n"
     "Tensors and PIL images are saved to a temp PNG so the editor\n"
     "can display them.  The popup is non-modal; the run continues.\n\n"
     "Tensor layouts handled: (N, C, H, W), (C, H, W), (H, W),\n"
     "with C in {1, 3, 4}.  Values are normalised to [0, 1].",
     [("Source", "any", "Path, PIL image, or tensor", "None")],
     [], None, None),
    ("Built-ins/Media", "Audio Player", "audio_player",
     "**Play an audio file.**  Path to .wav/.mp3/.ogg/\u2026",
     [("Source", "any", "Path or bytes", "None")],
     [], None, None),
    ("Built-ins/Media", "Video Player", "video_player",
     "**Play a video file.**  Path to .mp4/.mkv/.webm/\u2026",
     [("Source", "any", "Path", "None")],
     [], None, None),
]


def _register_roles(api):
    """Register every node in ROLES."""
    def _in_list(pairs):
        out = []
        for tup in pairs:
            if len(tup) == 5:
                out.append(tup)
                continue
            name, t, desc, default = tup
            out.append((name, t, desc, default) if default is not None
                       else (name, t, desc))
        return out
    for cat, name, kind, desc, ins, outs, qual, call_t in ROLES:
        kw = dict(name=name, category=cat, color="#6A5A8A",
                  kind=kind, description=desc, on_exists="replace")
        if ins:
            kw["inputs"] = _in_list(ins)
        if outs:
            kw["outputs"] = outs
        if qual:
            kw["qualname"] = qual
        if call_t:
            kw["call"] = call_t
        try:
            api.register_node(**kw)
        except Exception as ex:
            print("  ! role %s: %s" % (name, ex))

    # ---------------------------------------------------------------- #
    #  Value / literal nodes                                           #
    # ---------------------------------------------------------------- #
    VALUES = [
        ("Built-ins/Values", "String Literal", "call",
         "**String.**  Type text in the Value field.",
         [("Value", "string", "Text", "'hello'")],
         [("Result", "string", "The string")], None, "{Value}"),
        ("Built-ins/Values", "Int Literal", "call",
         "**Integer.**",
         [("Value", "int", "Integer", "0")],
         [("Result", "int", "Int")], None, "{Value}"),
        ("Built-ins/Values", "Float Literal", "call",
         "**Float.**",
         [("Value", "float", "Float", "0.0")],
         [("Result", "float", "Float")], None, "{Value}"),
        ("Built-ins/Values", "Bool Literal", "call",
         "**Bool.**  True or False.",
         [("Value", "bool", "Bool", "True")],
         [("Result", "bool", "Bool")], None, "{Value}"),
        ("Built-ins/Values", "None Literal", "call",
         "**None.**",
         [], [("Result", "any", "None")], None, "None"),
        ("Built-ins/Values", "List Literal", "literal_list",
         "**List.**  Add slots with the + button.",
         [("Item1", "any", "Item 1", "0"),
          ("Item2", "any", "Item 2", "0"),
          ("Item3", "any", "Item 3", "0")],
         [("Result", "any", "The list")], None, None),
        ("Built-ins/Values", "Tuple Literal", "literal_tuple",
         "**Tuple.**",
         [("Item1", "any", "Item 1", "0"),
          ("Item2", "any", "Item 2", "0")],
         [("Result", "any", "The tuple")], None, None),
        ("Built-ins/Values", "Set Literal", "literal_set",
         "**Set.**",
         [("Item1", "any", "Item 1", "0"),
          ("Item2", "any", "Item 2", "0")],
         [("Result", "any", "The set")], None, None),
        ("Built-ins/Values", "Dict Literal", "literal_dict",
         "**Dict.**  Pairs of (key, value) slots.",
         [("K1", "string", "Key 1",   "'a'"),
          ("V1", "any",    "Value 1", "1"),
          ("K2", "string", "Key 2",   "'b'"),
          ("V2", "any",    "Value 2", "2")],
         [("Result", "any", "The dict")], None, None),
    ]
    for cat, name, kind, desc, ins, outs, qual, call_t in VALUES:
        kw = dict(name=name, category=cat, color="#C08A3A",
                  kind=kind, description=desc,
                  dynamic=True, on_exists="replace")
        if ins:
            kw["inputs"] = _in_list(ins)
        if outs:
            kw["outputs"] = outs
        if qual:
            kw["qualname"] = qual
        if call_t:
            kw["call"] = call_t
        try:
            api.register_node(**kw)
        except Exception as ex:
            print("  ! value %s: %s" % (name, ex))


# File the editor creates to request a pause between nodes.  The generated
# subprocess polls this path (see _Runtime._check_pause) so that pressing
# Pause halts the run after the current node finishes, and pressing
# Resume lets it continue from the next node without restarting.
_PAUSE_FLAG_FILE = ".pytorchui_pause"


class _Runtime:
    """
    Called by generated.py to signal begin / output / end / error of a node.
    Markers on stdout:

        @@RT begin  <node_id>
        @@RT output <node_id> <json-value>
        @@RT end    <node_id>
        @@RT error  <node_id> <json-error>

    When the pause flag file exists, begin() blocks until it is removed,
    so the run stops cleanly between nodes instead of mid-operation.
    """

    def _emit(self, *parts):
        try:
            import sys as _sys
            out = getattr(_sys, "__stdout__", None) or _sys.stdout
            out.write("@@RT " + " ".join(str(p) for p in parts) + "\n")
            out.flush()
        except Exception:
            pass

    def media_show(self, node_id, kind, payload):
        """Emit a @@RT media marker after materialising the payload.

        payload may be a filesystem path (str), a PIL image, or a
        torch.Tensor.  Tensors and PIL images are written to a
        temporary PNG file so the parent process can load them.
        """
        path = self._materialize_media(payload)
        if path:
            self._emit("media", str(node_id),
                       json.dumps({"kind": kind, "path": path}))

    def _materialize_media(self, payload):
        import os as _os
        import tempfile as _tmp
        if isinstance(payload, str):
            if _os.path.isfile(payload):
                return _os.path.abspath(payload)
            return None
        try:
            if hasattr(payload, "save") and hasattr(payload, "size"):
                f = _tmp.NamedTemporaryFile(suffix=".png", delete=False)
                f.close()
                payload.save(f.name)
                return f.name
        except Exception:
            pass
        try:
            import torch as _torch
            if not isinstance(payload, _torch.Tensor):
                return None
            img = payload
            if img.dim() == 4:
                img = img[0]
            if img.dim() == 3 and img.shape[0] in (1, 3, 4):
                img = img.permute(1, 2, 0)
            arr = img.detach().cpu().float().numpy()
            arr = arr - arr.min()
            if arr.max() > 0:
                arr = arr / arr.max()
            arr = (arr * 255).astype("uint8")
            from PIL import Image as _Image
            if arr.ndim == 2:
                pil = _Image.fromarray(arr, "L")
            elif arr.shape[-1] == 1:
                pil = _Image.fromarray(arr[..., 0], "L")
            elif arr.shape[-1] == 3:
                pil = _Image.fromarray(arr, "RGB")
            elif arr.shape[-1] == 4:
                pil = _Image.fromarray(arr, "RGBA")
            else:
                return None
            f = _tmp.NamedTemporaryFile(suffix=".png", delete=False)
            f.close()
            pil.save(f.name)
            return f.name
        except Exception as ex:
            print("[media] tensor conversion failed:", ex)
        return None

    def _check_pause(self):
        if not os.path.exists(_PAUSE_FLAG_FILE):
            return
        # Busy-wait with a short sleep.  50 ms latency is imperceptible
        # between nodes and this avoids holding any OS handle the editor
        # would have to keep in sync.
        import time as _t
        self._emit("paused", "__run__")
        while os.path.exists(_PAUSE_FLAG_FILE):
            _t.sleep(0.05)
        self._emit("resumed", "__run__")

    def begin(self, node_id):
        self._check_pause()
        self._emit("begin", str(node_id))

    def output(self, node_id, value):
        short = _short_repr(value)
        long_ = _long_repr(value)
        try:
            self._emit("output", str(node_id), json.dumps(short))
            self._emit("full",   str(node_id), json.dumps(long_))
        except Exception:
            pass

    def end(self, node_id):
        self._emit("end", str(node_id))

    def stdout(self, node_id, text):
        if not text:
            return
        try:
            payload = json.dumps(str(text))
        except Exception:
            payload = json.dumps("")
        self._emit("stdout", str(node_id), payload)

    def error(self, node_id, err):
        self._emit("error", str(node_id), json.dumps(str(err)))

    # -------- richer preview API -------- #

    def _ns(self, d):
        import types
        return types.SimpleNamespace(**d)

    def _emit_media(self, node_id, payload):
        try:
            self._emit("media", str(node_id), json.dumps(payload))
        except Exception as ex:
            print("[media] emit failed:", ex)

    def preview_image(self, node_id, source, title="", scale=1.0,
                      width=0, height=0, grid=False, normalize=True):
        path = self._materialize_media(source)
        meta = {"Path": path or "",
                "Width": 0, "Height": 0,
                "Mode": "", "Format": "", "Size": 0}
        if path:
            try:
                from PIL import Image as _I
                im = _I.open(path)
                meta["Width"] = im.width
                meta["Height"] = im.height
                meta["Mode"] = im.mode or ""
                meta["Format"] = im.format or ""
                im.close()
            except Exception:
                pass
            try:
                import os as _os
                meta["Size"] = _os.path.getsize(path)
            except Exception:
                pass
        self._emit_media(node_id, {
            "kind": "image", "path": path or "",
            "title": str(title), "scale": float(scale),
            "width": int(width), "height": int(height),
            "grid": bool(grid),
        })
        return self._ns(meta)

    def preview_audio(self, node_id, source, title="", autoplay=True,
                      loop=False, volume=1.0, start=0.0):
        path = self._materialize_media(source)
        meta = {"Path": path or "",
                "Duration": 0.0, "SampleRate": 0,
                "Channels": 0, "Format": ""}
        if path:
            try:
                import wave as _w
                with _w.open(path, "rb") as wf:
                    frames = wf.getnframes()
                    sr = wf.getframerate() or 1
                    meta["Duration"] = frames / sr
                    meta["SampleRate"] = sr
                    meta["Channels"] = wf.getnchannels()
                    meta["Format"] = "wav"
            except Exception:
                pass
        self._emit_media(node_id, {
            "kind": "audio", "path": path or "",
            "title": str(title), "autoplay": bool(autoplay),
            "loop": bool(loop), "volume": float(volume),
            "start": float(start),
        })
        return self._ns(meta)

    def preview_video(self, node_id, source, title="", autoplay=True,
                      loop=False, start=0.0, end=0.0, muted=False):
        path = self._materialize_media(source)
        meta = {"Path": path or "", "Duration": 0.0,
                "FPS": 0.0, "Width": 0, "Height": 0, "Codec": ""}
        self._emit_media(node_id, {
            "kind": "video", "path": path or "",
            "title": str(title), "autoplay": bool(autoplay),
            "loop": bool(loop), "start": float(start),
            "end": float(end), "muted": bool(muted),
        })
        return self._ns(meta)

    def preview_folder(self, node_id, source, filter="*",
                       recursive=False, columns=0, thumb_size=128,
                       sort="name"):
        import os as _os
        import glob as _g
        folder = str(source or ".")
        if not _os.path.isdir(folder):
            folder = _os.path.dirname(folder)
        pattern = _os.path.join(folder, "**", filter) if recursive \
                  else _os.path.join(folder, filter)
        files = []
        try:
            files = [p for p in _g.glob(pattern, recursive=recursive)
                     if _os.path.isfile(p)]
        except Exception:
            pass
        if sort == "size":
            try:
                files.sort(key=lambda p: _os.path.getsize(p), reverse=True)
            except Exception:
                pass
        elif sort == "mtime":
            try:
                files.sort(key=lambda p: _os.path.getmtime(p), reverse=True)
            except Exception:
                pass
        self._emit_media(node_id, {
            "kind": "folder", "folder": folder,
            "files": files[:2000],
            "count": len(files),
            "columns": int(columns),
            "thumb": int(thumb_size),
        })
        return self._ns({
            "Path": folder,
            "Count": len(files),
            "Files": files,
            "First": files[0] if files else "",
        })

    def preview_table(self, node_id, source, title="", max_rows=100,
                      max_cols=20, editable=False):
        cols, rows = [], []
        try:
            if hasattr(source, "to_dict") and hasattr(source, "columns"):
                df = source.head(int(max_rows))
                cols = [str(c) for c in list(df.columns)[:int(max_cols)]]
                rows = [[repr(v) for v in r]
                        for r in df.values.tolist()]
            elif isinstance(source, dict):
                cols = ["key", "value"]
                items = list(source.items())[:int(max_rows)]
                rows = [[str(k), repr(v)] for k, v in items]
            elif isinstance(source, (list, tuple)):
                if source and isinstance(source[0], dict):
                    cols = list(source[0].keys())[:int(max_cols)]
                    rows = [[repr(r.get(c)) for c in cols]
                            for r in source[:int(max_rows)]]
                else:
                    cols = ["value"]
                    rows = [[repr(x)] for x in source[:int(max_rows)]]
            else:
                cols = ["repr"]
                rows = [[repr(source)]]
        except Exception as ex:
            print("[preview_table]", ex)
        self._emit_media(node_id, {
            "kind": "table", "title": str(title),
            "cols": cols, "rows": rows,
            "editable": bool(editable),
        })
        return self._ns({
            "Rows": len(rows), "Cols": len(cols),
            "Columns": cols,
            "FirstRow": rows[0] if rows else [],
        })

    def preview_text(self, node_id, source, title="", max_lines=500,
                     wrap=True, monospace=True, highlight=""):
        text = "" if source is None else str(source)
        lines = text.split("\n")
        if len(lines) > int(max_lines):
            text = "\n".join(lines[:int(max_lines)]) + "\n... (truncated)"
        self._emit_media(node_id, {
            "kind": "text", "title": str(title), "text": text,
            "wrap": bool(wrap), "monospace": bool(monospace),
            "highlight": str(highlight),
        })
        return self._ns({
            "Text": text,
            "Length": len(text),
            "Lines": len(lines),
        })

    def preview_json(self, node_id, source, title="", indent=2,
                     sort=False, max_depth=20):
        import json as _j
        valid = True
        try:
            text = _j.dumps(source, indent=int(indent), default=repr,
                            sort_keys=bool(sort))
        except Exception:
            valid = False
            text = repr(source)
        keys = list(source.keys()) if isinstance(source, dict) else []
        self._emit_media(node_id, {
            "kind": "json", "title": str(title),
            "text": text, "valid": valid,
        })
        return self._ns({
            "Text": text, "Valid": valid,
            "Keys": keys, "Length": len(text),
        })

    def preview_plot(self, node_id, x=None, y=None, kind="line",
                     title="", xlabel="", ylabel="",
                     color="#E08C4A", figsize_w=8.0, figsize_h=5.0):
        import os as _os
        import tempfile as _tmp
        path = ""
        xmin = xmax = ymin = ymax = 0.0
        npts = 0
        try:
            import matplotlib
            matplotlib.use("Agg", force=True)
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(
                figsize=(float(figsize_w), float(figsize_h)))
            fig.patch.set_facecolor("#1E1E1E")
            ax.set_facecolor("#242424")
            for s in ("bottom", "top", "left", "right"):
                ax.spines[s].set_color("#555")
            ax.tick_params(colors="#CCC")
            if kind == "hist" and y is None:
                ax.hist(list(x), bins=min(30, max(5, len(x) // 4)),
                        color=color)
                npts = len(x)
                xmin, xmax = float(min(x)), float(max(x))
            elif kind == "pie":
                ax.pie(list(y) if y is not None else list(x))
                npts = len(y) if y is not None else len(x)
            else:
                xs = list(range(len(y))) if x is None else list(x)
                ys = list(y) if y is not None else list(x)
                npts = len(ys)
                if kind == "bar":
                    ax.bar(range(len(ys)), ys, color=color)
                elif kind == "scatter":
                    ax.scatter(xs, ys, color=color)
                elif kind == "step":
                    ax.step(xs, ys, color=color)
                elif kind == "fill":
                    ax.fill_between(range(len(ys)), ys, color=color,
                                    alpha=0.4)
                else:
                    ax.plot(xs, ys, color=color, linewidth=2)
                try:
                    xmin, xmax = float(min(xs)), float(max(xs))
                    ymin, ymax = float(min(ys)), float(max(ys))
                except Exception:
                    pass
            if title:  ax.set_title(title, color="#EEE")
            if xlabel: ax.set_xlabel(xlabel, color="#AAA")
            if ylabel: ax.set_ylabel(ylabel, color="#AAA")
            f = _tmp.NamedTemporaryFile(suffix=".png", delete=False)
            f.close()
            fig.savefig(f.name, facecolor="#1E1E1E", bbox_inches="tight")
            plt.close(fig)
            path = f.name
        except Exception as ex:
            print("[preview_plot]", ex)
        self._emit_media(node_id, {
            "kind": "image", "path": path,
            "title": str(title), "scale": 1.0,
            "width": 0, "height": 0, "grid": False,
        })
        return self._ns({
            "Path": path, "Points": npts,
            "XMin": xmin, "XMax": xmax,
            "YMin": ymin, "YMax": ymax,
        })

    def preview_html(self, node_id, source, title="", height=400):
        self._emit_media(node_id, {
            "kind": "html", "title": str(title),
            "text": str(source or ""), "height": int(height),
        })
        return self._ns({"Rendered": True})

    def preview_markdown(self, node_id, source, title="", height=400):
        self._emit_media(node_id, {
            "kind": "markdown", "title": str(title),
            "text": str(source or ""), "height": int(height),
        })
        return self._ns({"Rendered": True})


runtime = _Runtime()


# --------------------------------------------------------------------------- #
#  Theme                                                                      #
# --------------------------------------------------------------------------- #

SOCKET_RADIUS  = 6.0
HEADER_HEIGHT  = 28
ROW_HEIGHT     = 22
SECTION_HEIGHT = 20
PADDING        = 8
NODE_WIDTH     = 200
ROUNDING       = 8

NODE_BODY_TOP    = QColor("#2E2E2E")
NODE_BODY_BOTTOM = QColor("#191919")
NODE_BORDER      = QColor(0, 0, 0, 210)
NODE_HEADER_LINE = QColor(0, 0, 0, 190)
SECTION_BAR_BG   = QColor(255, 255, 255, 10)
SECTION_BAR_HOV  = QColor(255, 255, 255, 24)
SECTION_TEXT     = QColor(210, 210, 210)
LABEL_COLOR      = QColor(228, 228, 228)
TITLE_COLOR      = QColor(245, 245, 245)
TITLE_SHADOW     = QColor(0, 0, 0, 150)
SELECT_COLOR     = QColor(255, 160, 40)
ACCENT           = QColor("#E08C4A")

TYPE_COLORS = {
    "float":  QColor("#A1A1A1"),
    "int":    QColor("#A1A1A1"),
    "bool":   QColor("#CCA6A6"),
    "vector": QColor("#6363C7"),
    "color":  QColor("#C7C729"),
    "string": QColor("#7B68A6"),
    "image":  QColor("#C7A163"),
    "shader": QColor("#63C763"),
    "object": QColor("#E08C4A"),
    "any":    QColor("#B0B0B0"),
    "path":   QColor("#6A6A6A"),
}
SOCKET_TYPES = list(TYPE_COLORS.keys())


def type_color(t):
    return TYPE_COLORS.get(t, TYPE_COLORS["any"])


def rounded_top_path(x, y, w, h, r):
    p = QPainterPath()
    p.moveTo(x + r, y)
    p.lineTo(x + w - r, y)
    p.arcTo(x + w - 2 * r, y, 2 * r, 2 * r, 90, -90)
    p.lineTo(x + w, y + h)
    p.lineTo(x, y + h)
    p.lineTo(x, y + r)
    p.arcTo(x, y, 2 * r, 2 * r, 180, -90)
    p.closeSubpath()
    return p


# --------------------------------------------------------------------------- #
#  Module flags                                                               #
# --------------------------------------------------------------------------- #

_LIB_REFRESH = {"pending": False, "suspended": 0}

_FOCUS = {
    "kind": "",
    "object": None,
    "section_index": -1,
    "library_title": "",
    "library_body": "",
}

PATH_TYPE         = "path"
PATH_SECTION_NAME = "Flow"
PATH_IN_NAME      = "Path In"
PATH_OUT_NAME     = "Path Out"
_PATH_FLAGS       = {"hidden": True}

SETTINGS_PATH = os.path.expanduser("~/.pytorchui.json")


def _load_settings():
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_settings(data):
    try:
        d = os.path.dirname(SETTINGS_PATH)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as ex:
        print("[settings] save failed:", ex)
        return False

# ---------------------------------------------------------------- #
#  Output path settings                                            #
# ---------------------------------------------------------------- #
#
# These are stored in ~/.pytorchui.json alongside the rest of the
# settings.  They are read on boot by MainWindow.__init__ and used
# by _generate_code_to_file and save_graph_as.
#
# main.py does NOT know about these.  main.py is a generated loader;
# create.py overwrites it on every `--db` run.  The state and the
# menu live here, in Nodes.py, which create.py never touches.

_OUTPUT_PATHS = {
    "generated_py": "",     # where Ctrl+G writes generated.py
    "graph_export": "",     # default dir for Save As...
    "db_output":    "",     # default dir for the Convert dialog
}


def _load_output_paths():
    try:
        data = _load_settings()
        p = data.get("paths", {}) or {}
        for k in _OUTPUT_PATHS:
            _OUTPUT_PATHS[k] = str(p.get(k, "") or "")
    except Exception as ex:
        print("[paths] load failed:", ex)


def _save_output_paths():
    try:
        data = _load_settings()
        data.setdefault("paths", {})
        for k, v in _OUTPUT_PATHS.items():
            data["paths"][k] = v
        _save_settings(data)
    except Exception as ex:
        print("[paths] save failed:", ex)


def _output_dir(kind, fallback=""):
    """Return the configured folder for `kind`, or fallback.

    kind is one of "generated_py", "graph_export", "db_output".
    """
    p = _OUTPUT_PATHS.get(kind, "")
    if p and os.path.isdir(p):
        return p
    return fallback or os.getcwd()



def _apply_settings(data):
    if not isinstance(data, dict):
        return
    a = data.get("autosave", {}) or {}
    if "enabled" in a:      _AUTOSAVE["enabled"]      = bool(a["enabled"])
    if "interval" in a:     _AUTOSAVE["interval"]     = int(a["interval"])
    if "after_change" in a: _AUTOSAVE["after_change"] = bool(a["after_change"])
    p = data.get("paths", {}) or {}
    if "hide" in p:         _PATH_FLAGS["hidden"]     = bool(p["hide"])
    r = data.get("reroute", {}) or {}
    if "enabled" in r:      _REROUTE["enabled"]       = bool(r["enabled"])
    if "ask" in r:          _REROUTE["ask"]           = bool(r["ask"])
    if "flow" in r:         _REROUTE["flow"]          = bool(r["flow"])
    if "data" in r:         _REROUTE["data"]          = bool(r["data"])
    b = data.get("blocks", {}) or {}
    if "show_slot" in b:    _BLOCKS["show_slot"]      = bool(b["show_slot"])


def _collect_settings():
    return {
        "autosave": {
            "enabled":      _AUTOSAVE["enabled"],
            "interval":     _AUTOSAVE["interval"],
            "after_change": _AUTOSAVE["after_change"],
        },
        "paths":   {"hide": _PATH_FLAGS["hidden"]},
        "reroute": {
            "enabled": _REROUTE["enabled"],
            "ask":     _REROUTE["ask"],
            "flow":    _REROUTE["flow"],
            "data":    _REROUTE["data"],
        },
        "blocks":  {"show_slot": _BLOCKS["show_slot"]},
    }


_AUTOSAVE = {
    "enabled":      False,
    "interval":     30,
    "path":         "autosave.json",
    "after_change": False,
}

_BLOCKS = {
    "slot_height": 14,
    "slot_gap":    6,
    "indent":      16,
    "show_slot":   True,
}

_REROUTE = {
    "enabled": True,
    "ask":     False,
    "flow":    True,
    "data":    True,
}



# --------------------------------------------------------------------------- #
#  Library filter (non-coder UI)                                              #
# --------------------------------------------------------------------------- #

_LIBRARY_FILTER = {"disabled": set()}   # set of category paths, "/"-joined



# --------------------------------------------------------------------------- #
#  Library filter persistence (side-car, independent of settings.json)        #
# --------------------------------------------------------------------------- #

_LIB_FILTER_FILE = os.path.join(
    os.path.expanduser("~"), ".pytorchui", "library_filter.json")


def _lib_filter_save():
    try:
        os.makedirs(os.path.dirname(_LIB_FILTER_FILE), exist_ok=True)
        tmp = _LIB_FILTER_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sorted(_LIBRARY_FILTER["disabled"]), f)
        os.replace(tmp, _LIB_FILTER_FILE)
    except Exception as ex:
        print("[filter] save failed:", ex)


def _lib_filter_load():
    try:
        with open(_LIB_FILTER_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            _LIBRARY_FILTER["disabled"] = set(str(x) for x in data)
    except FileNotFoundError:
        pass
    except Exception as ex:
        print("[filter] load failed:", ex)


def _iter_category_paths():
    """Yield every unique category path (prefixes included) in sorted order."""
    seen = set()
    for cat, _tpls in NODE_TEMPLATES:
        if isinstance(cat, (list, tuple)):
            parts = [str(p) for p in cat]
        else:
            parts = [p for p in str(cat).split("/") if p]
        for i in range(1, len(parts) + 1):
            seen.add("/".join(parts[:i]))
    return sorted(seen)


def _is_category_disabled(path):
    if not _LIBRARY_FILTER["disabled"]:
        return False
    if isinstance(path, (list, tuple)):
        parts = [str(p) for p in path]
    else:
        parts = [p for p in str(path).split("/") if p]
    for i in range(1, len(parts) + 1):
        if "/".join(parts[:i]) in _LIBRARY_FILTER["disabled"]:
            return True
    return False


def _truncate_words(text, limit=100):
    """Return (shortened_text, was_cut).  Word-based, not char-based."""
    if not text:
        return text or "", False
    words = str(text).split()
    if len(words) <= limit:
        return text, False
    return " ".join(words[:limit]), True




def _safe_nid(node):
    """Whitespace-free identifier for a node, used in runtime markers."""
    nid = node.metadata.get("id") if hasattr(node, "metadata") else None
    if nid:
        return str(nid)
    return _safe_var(getattr(node, "title", "node"))


def _safe_var(name):
    v = re.sub(r"[^0-9a-zA-Z_]", "_", str(name or "node")).strip("_")
    if not v:
        v = "node"
    if v[0].isdigit():
        v = "_" + v
    return v


def _unique_var(base, used):
    if base not in used:
        used.add(base)
        return base
    i = 2
    while f"{base}_{i}" in used:
        i += 1
    v = f"{base}_{i}"
    used.add(v)
    return v



def _sorted_children(n):
    """Return n.blocks sorted by visual (top-to-bottom) position.

    Click order is not meaningful for code emission — the user controls
    statement order by dragging children up or down on the canvas.
    """
    try:
        kids = list(n.blocks)
    except AttributeError:
        return []
    if not kids:
        return kids
    try:
        if n.scene() is None:
            return kids
    except RuntimeError:
        return kids
    return sorted(kids, key=lambda c: (c.pos().y(), c.pos().x()))



def _looks_positional_splat(v):
    """True if `v` is a comma-separated list of positional args.

    A string like "1, 3, 32, 32" has a top-level comma, so it must be
    emitted positionally: torch.randn(1, 3, 32, 32).  A string like
    "(1, 3)" has commas at depth 1 only, so it is a single value and
    is emitted as a keyword: shape=(1, 3).
    """
    if not v or not isinstance(v, str):
        return False
    s = v.strip()
    if not s:
        return False
    depth = 0
    quote = None
    for c in s:
        if quote is not None:
            if c == quote:
                quote = None
            continue
        if c in ("'", '"'):
            quote = c
            continue
        if c in "([{":
            depth += 1
            continue
        if c in ")]}":
            depth -= 1
            continue
        if c == "," and depth == 0:
            return True
    return False


def _is_auto_sentinel(v):
    """True if `v` is the placeholder string "auto" (with or without quotes).

    The graph UI uses "auto" to mean "let the runtime infer this
    parameter".  Emitting `name='auto'` is almost never valid Python,
    and PyTorch will reject it with a cryptic error.  Filtering it out
    here means we either fall back to a default value or, in the case
    of a Lazy variant, switch to a class that really does infer.
    """
    if v is None:
        return False
    s = str(v).strip()
    if not s:
        return False
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        s = s[1:-1].strip()
    return s.lower() == "auto"


# torch.nn modules that have a Lazy variant which infers the missing
# parameter on first forward call.  Used to rewrite auto-skipped calls
# like nn.Linear(in_features='auto', ...) into nn.LazyLinear(...).
_LAZY_VARIANTS = {
    "Linear":          "LazyLinear",
    "Conv1d":          "LazyConv1d",
    "Conv2d":          "LazyConv2d",
    "Conv3d":          "LazyConv3d",
    "ConvTranspose1d": "LazyConvTranspose1d",
    "ConvTranspose2d": "LazyConvTranspose2d",
    "ConvTranspose3d": "LazyConvTranspose3d",
    "BatchNorm1d":     "LazyBatchNorm1d",
    "BatchNorm2d":     "LazyBatchNorm2d",
    "BatchNorm3d":     "LazyBatchNorm3d",
}



def _looks_positional_splat(v):
    """True if `v` is a comma-separated list of positional args.

    Called on values about to be emitted into a Python call.  A string
    like "1, 3, 32, 32" has a top-level comma, so it must be emitted
    positionally: torch.randn(1, 3, 32, 32).  A string like "(1, 3)"
    has commas at depth 1 only, so it is a single value and is emitted
    as a keyword: shape=(1, 3).
    """
    if not v or not isinstance(v, str):
        return False
    s = v.strip()
    if not s:
        return False
    depth = 0
    quote = None
    for c in s:
        if quote is not None:
            if c == quote:
                quote = None
            continue
        if c in ("'", '"'):
            quote = c
            continue
        if c in "([{":
            depth += 1
            continue
        if c in ")]}":
            depth -= 1
            continue
        if c == "," and depth == 0:
            return True
    return False

# --------------------------------------------------------------------------- #
#  Param helpers (used when registering a template in code)                   #
# --------------------------------------------------------------------------- #

def section(name, *sockets, description=""):
    return (name, list(sockets), description)


def in_(name, socket_type, description="", default=None):
    return (name, socket_type, "in", description, default)


def out(name, socket_type, description="", default=None):
    return (name, socket_type, "out", description, default)


# --------------------------------------------------------------------------- #
#  Hover tooltip                                                              #
# --------------------------------------------------------------------------- #

class HoverTooltip(QGraphicsItem):
    OFFSET_X  = 14
    OFFSET_Y  = 14
    PAD_X     = 10
    PAD_Y     = 8
    GAP       = 5
    MAX_WIDTH = 300

    def __init__(self):
        super().__init__()
        self.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        self.setZValue(100000)
        self.setAcceptedMouseButtons(Qt.NoButton)
        self.setVisible(False)
        self._title = ""
        self._body  = ""
        self._w = 0
        self._h = 0
        self._title_h = 0
        self._body_h  = 0

    def set_content(self, title, body):
        self.prepareGeometryChange()
        self._title = title or ""
        t, cut = _truncate_words(body or "", 100)
        if cut:
            t = t + "   (F1 for full)"
        self._body = t
        self._layout()

    def _layout(self):
        f_t = QFont("Segoe UI", 9); f_t.setBold(True)
        f_b = QFont("Segoe UI", 8)
        fm_t = QFontMetrics(f_t)
        fm_b = QFontMetrics(f_b)
        title_w = fm_t.horizontalAdvance(self._title) if self._title else 0
        body_w  = fm_b.horizontalAdvance(self._body)  if self._body  else 0
        content_w = min(max(title_w, body_w), self.MAX_WIDTH)
        content_w = max(content_w, 90)
        self._w = content_w + self.PAD_X * 2
        tr = fm_t.boundingRect(QRect(0, 0, content_w, 10000),
                               Qt.TextWordWrap, self._title or " ")
        br = fm_b.boundingRect(QRect(0, 0, content_w, 10000),
                               Qt.TextWordWrap, self._body  or " ")
        self._title_h = tr.height() if self._title else 0
        self._body_h  = br.height() if self._body  else 0
        self._h = self.PAD_Y * 2 + self._title_h
        if self._body:
            self._h += self.GAP + self._body_h

    def boundingRect(self):
        return QRectF(-4, -4,
                      self._w + self.OFFSET_X + 12,
                      self._h + self.OFFSET_Y + 12)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.translate(self.OFFSET_X, self.OFFSET_Y)
        body = QRectF(0, 0, self._w, self._h)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 130))
        painter.drawRoundedRect(body.translated(2, 3), 6, 6)
        painter.setBrush(QColor(28, 28, 28, 248))
        painter.setPen(QPen(QColor(0, 0, 0, 220), 1.0))
        painter.drawRoundedRect(body, 6, 6)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 22), 1.0))
        painter.drawRoundedRect(body.adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
        y = self.PAD_Y
        if self._title:
            f = QFont("Segoe UI", 9); f.setBold(True)
            painter.setFont(f)
            painter.setPen(QColor(250, 250, 250))
            r = QRectF(self.PAD_X, y, self._w - self.PAD_X * 2, self._title_h)
            painter.drawText(r, Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop,
                             self._title)
            y += self._title_h + self.GAP
        if self._body:
            f = QFont("Segoe UI", 8)
            painter.setFont(f)
            painter.setPen(QColor(192, 192, 192))
            r = QRectF(self.PAD_X, y, self._w - self.PAD_X * 2, self._body_h)
            painter.drawText(r, Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop,
                             self._body)


# --------------------------------------------------------------------------- #
#  Socket                                                                     #
# --------------------------------------------------------------------------- #

class NodeSocket(QGraphicsItem):
    def __init__(self, node, name, socket_type, is_input,
                 description="", value=None, options=None):
        super().__init__(node)
        self.node = node
        self.name = name
        self.socket_type = socket_type
        self.is_input = is_input
        self.description = description
        self.value = value
        self.options = options
        self.connections = []
        self.radius = SOCKET_RADIUS
        self._hovered = False
        self.setZValue(3)
        self.setAcceptHoverEvents(True)

    def boundingRect(self):
        r = self.radius + 4
        return QRectF(-r, -r, 2 * r, 2 * r)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing, True)
        col = type_color(self.socket_type)
        r = self.radius
        connected = bool(self.connections)
        if connected:
            painter.setPen(QPen(col.darker(190), 1.0))
            painter.setBrush(QBrush(col))
        else:
            painter.setPen(QPen(col, 1.5))
            painter.setBrush(QBrush(QColor(22, 22, 22)))
        if self._hovered:
            painter.setPen(QPen(col.lighter(150), 2.2))
            if connected:
                painter.setBrush(QBrush(col.lighter(120)))
        painter.drawEllipse(QPointF(0, 0), r, r)

    def _tip_title(self):
        return self.name

    def _tip_body(self):
        lines = [f"Type: {self.socket_type}",
                 "Input" if self.is_input else "Output"]
        if self.description:
            lines.append(self.description)
        return "\n".join(lines)

    def _show_tip(self, scene_pos):
        sc = self.scene()
        if sc is not None and hasattr(sc, "show_tooltip"):
            sc.show_tooltip(self._tip_title(), self._tip_body(), scene_pos)

    def _hide_tip(self):
        sc = self.scene()
        if sc is not None and hasattr(sc, "hide_tooltip"):
            sc.hide_tooltip()

    def hoverEnterEvent(self, event):
        self._hovered = True
        self.update()
        self._show_tip(event.scenePos())
        super().hoverEnterEvent(event)

    def hoverMoveEvent(self, event):
        self._show_tip(event.scenePos())
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event):
        self._hovered = False
        self.update()
        self._hide_tip()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event):
        _FOCUS["kind"] = "socket"
        _FOCUS["object"] = self
        _FOCUS["section_index"] = -1
        if event.button() == Qt.LeftButton and self.scene() is not None:
            self._hide_tip()
            self.scene().start_connection(self, event.scenePos())
            event.accept(); return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self._last_scene_pos = self.mapToScene(event.pos())

        # While the user is dragging a node, highlight whichever parent's
        # block slot is under the cursor.
        scene = self.scene()
        if scene is not None:
            dragging = bool(event.buttons() & Qt.LeftButton)
            scene_pos = self._last_scene_pos
            hit_parent = None
            if dragging:
                for n in scene.items():
                    if not isinstance(n, Node):
                        continue
                    br = n.block_rect()
                    br_scene = QRectF(
                        n.pos().x() + br.x(),
                        n.pos().y() + br.y() - 24,
                        br.width(),
                        br.height() + 48)
                    if br_scene.contains(scene_pos):
                        hit_parent = n
                        break
            for n in scene.items():
                if not isinstance(n, Node):
                    continue
                want = (n is hit_parent)
                if getattr(n, "_block_hovered", False) != want:
                    n._block_hovered = want
                    n.update()

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        sc = self.scene()
        if sc is not None and getattr(sc, "temp_edge", None) is not None:
            sc.finish_connection(event.scenePos())
            event.accept(); return
        super().mouseReleaseEvent(event)

    def update_edges(self):
        for e in list(self.connections):
            e.update_path()


# --------------------------------------------------------------------------- #
#  Edge                                                                       #
# --------------------------------------------------------------------------- #

class Edge(QGraphicsPathItem):
    def __init__(self, start_socket=None, end_socket=None):
        super().__init__()
        self.start_socket = start_socket
        self.end_socket = end_socket
        self.loose_end = QPointF(0, 0)
        self.is_path_edge = (
            start_socket is not None
            and end_socket is not None
            and start_socket.socket_type == PATH_TYPE
            and end_socket.socket_type == PATH_TYPE
        )
        self.setZValue(-1)
        self.setBrush(QBrush(Qt.NoBrush))
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.update_pen()
        self.apply_path_visibility()

    def apply_path_visibility(self):
        if getattr(self, "is_path_edge", False):
            self.setVisible(not _PATH_FLAGS["hidden"])

    def _out_socket(self):
        for s in (self.start_socket, self.end_socket):
            if s is not None and not s.is_input:
                return s
        return None

    def _in_socket(self):
        for s in (self.start_socket, self.end_socket):
            if s is not None and s.is_input:
                return s
        return None

    def _color(self):
        s = self._out_socket() or self._in_socket()
        return type_color(s.socket_type) if s else type_color("any")

    def update_pen(self):
        if getattr(self, "preview", False):
            self.setPen(QPen(QColor(255, 200, 80, 210), 2.0, Qt.DashLine))
            return
        w = 3.2 if self.isSelected() else 2.2
        self.setPen(QPen(self._color(), w, Qt.SolidLine, Qt.RoundCap))

    def update_path(self):
        out = self._out_socket(); inp = self._in_socket()
        p_out = out.scenePos() if out is not None else self.loose_end
        p_in = inp.scenePos() if inp is not None else self.loose_end
        dx = max(abs(p_in.x() - p_out.x()) * 0.5, 45.0)
        c1 = QPointF(p_out.x() + dx, p_out.y())
        c2 = QPointF(p_in.x() - dx, p_in.y())
        path = QPainterPath(p_out)
        path.cubicTo(c1, c2, p_in)
        self.setPath(path)
        self.update_pen()

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing, True)
        if self.isSelected():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(255, 180, 60, 110),
                                self.pen().widthF() + 5.0,
                                Qt.SolidLine, Qt.RoundCap))
            painter.drawPath(self.path())
        option.state &= ~QStyle.State_Selected
        super().paint(painter, option, widget)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemSelectedHasChanged:
            self.update_pen()
        return super().itemChange(change, value)


# --------------------------------------------------------------------------- #
#  Node                                                                       #
# --------------------------------------------------------------------------- #

class Node(QGraphicsItem):
    output_full = ""
    def __init__(self, title="Node", color=QColor("#3B3B3B"),
                 width=NODE_WIDTH, description="", dynamic=False):
        super().__init__()
        self.title = title
        self.color = QColor(color)
        self.width = width
        self.height = HEADER_HEIGHT
        self.corner_radius = ROUNDING
        self.description = description or ""
        self.dynamic = bool(dynamic)
        self.sections = []
        self.inputs = []
        self.outputs = []
        self._current_section = None
        self._hovered_section = -1
        self.metadata = {}
        self.output_lines = []
        self.output_full = ""
        self.running = False
        self._output_rect = QRectF()
        self._preview_rect = QRectF()
        self._preview_info = {}
        self._preview_kind = ""
        self._preview_pixmap = None
        self._preview_lines = []
        self._shell_collapsed = False
        self._block_hovered = False
        self.blocks = []
        self.parent_node = None
        self.setFlags(QGraphicsItem.ItemIsMovable |
                      QGraphicsItem.ItemIsSelectable |
                      QGraphicsItem.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.setZValue(1)
        self._new_section("")
        self._select_order = 0

    # -- API -------------------------------------------------------------- #
    def _new_section(self, name, description=""):
        sec = {"name": name, "description": description, "collapsed": False,
               "inputs": [], "outputs": [], "rect": QRectF()}
        self.sections.append(sec)
        self._current_section = sec
        return sec

    def add_section(self, name, description=""):
        return self._new_section(name, description)

    def add_input(self, name, socket_type="float", description="",
                  value=None, options=None):
        if self._current_section is None:
            self._new_section("")
        s = NodeSocket(self, name, socket_type, True, description,
                       value, options)
        self._current_section["inputs"].append(s)
        self.inputs.append(s)
        self.layout()
        return s

    def add_output(self, name, socket_type="float", description="",
                   options=None):
        if self._current_section is None:
            self._new_section("")
        s = NodeSocket(self, name, socket_type, False, description,
                       None, options)
        self._current_section["outputs"].append(s)
        self.outputs.append(s)
        self.layout()
        return s

    def socket(self, name, is_input=None):
        for s in self.inputs:
            if s.name == name and (is_input is None or is_input is True):
                return s
        for s in self.outputs:
            if s.name == name and (is_input is None or is_input is False):
                return s
        return None

    def set_node_color(self, color):
        self.color = QColor(color); self.update()

    def set_title(self, title):
        self.title = title; self.update()

    def set_description(self, text):
        self.description = text or ""

    # -- dynamic sockets -------------------------------------------------- #
    def add_socket(self, section_index, is_input,
                   name=None, socket_type="float", description=""):
        if section_index < 0 or section_index >= len(self.sections):
            return None
        sec = self.sections[section_index]
        if name is None:
            base = "Input" if is_input else "Output"
            existing = {s.name for s in sec["inputs"] + sec["outputs"]}
            i = 1
            while f"{base}{i}" in existing:
                i += 1
            name = f"{base}{i}"
        s = NodeSocket(self, name, socket_type, is_input, description)
        if is_input:
            sec["inputs"].append(s)
            self.inputs.append(s)
        else:
            sec["outputs"].append(s)
            self.outputs.append(s)
        self.layout()
        sc = self.scene()
        if sc is not None:
            sc.graph_changed.emit()
        return s

    def remove_socket(self, socket):
        sc = self.scene()
        if sc is not None:
            for e in list(socket.connections):
                sc.remove_edge(e)
        for sec in self.sections:
            if socket in sec["inputs"]:
                sec["inputs"].remove(socket)
            if socket in sec["outputs"]:
                sec["outputs"].remove(socket)
        if socket in self.inputs:
            self.inputs.remove(socket)
        if socket in self.outputs:
            self.outputs.remove(socket)
        socket.setParentItem(None)
        self.layout()
        if sc is not None:
            sc.graph_changed.emit()

    def section_index_of(self, socket):
        for i, sec in enumerate(self.sections):
            if socket in sec["inputs"] or socket in sec["outputs"]:
                return i
        return -1

    def add_flow_sockets(self):
        sec = self.add_section(PATH_SECTION_NAME,
                               "Execution order (Path to Path)")
        sec["flow"] = True
        self.add_input(PATH_IN_NAME, PATH_TYPE,
                       "Previous node in the flow")
        self.add_output(PATH_OUT_NAME, PATH_TYPE,
                        "Next node in the flow")

    # -- blocks ----------------------------------------------------------- #
    def block_rect(self):
        w = self.width
        h = _BLOCKS["slot_height"]
        y = self.height + _BLOCKS["slot_gap"]
        return QRectF(6, y, w - 12, h)

    def block_chain(self):
        return [self] + list(self.blocks)

    def add_block(self, child):
        if child is self:
            return False
        walk = self
        while walk is not None:
            if walk is child:
                return False
            walk = getattr(walk, "parent_node", None)
        if child in self.blocks:
            return False
        old_parent = getattr(child, "parent_node", None)
        if old_parent is not None:
            try:
                old_parent.blocks.remove(child)
                old_parent.layout_blocks()
            except ValueError:
                pass
        child.parent_node = self
        self.blocks.append(child)
        self.layout_blocks()
        return True

    def remove_block(self, child):
        if child in self.blocks:
            self.blocks.remove(child)
            try:
                child.parent_node = None
            except Exception:
                pass
            self.layout_blocks()
            return True
        return False

    def layout_blocks(self):
        y = self.pos().y() + self.height + _BLOCKS["slot_gap"] + _BLOCKS["slot_height"]
        x = self.pos().x() + _BLOCKS["indent"]
        for child in self.blocks:
            child.setPos(x, y)
            y += child.height + _BLOCKS["slot_gap"]
        for child in self.blocks:
            try:
                child.layout_blocks()
            except Exception:
                pass

    # -- runtime output --------------------------------------------------- #
    def _shell_button_rect(self):
        if not getattr(self, "output_full", ""):
            return QRectF()
        orct = self._output_rect
        if orct.height() <= 0:
            return QRectF()
        w = 40
        return QRectF(orct.right() - w - 5,
                      orct.top() + 2,
                      w, SECTION_HEIGHT - 4)

    def set_full_output(self, text):
        self.output_full = str(text) if text is not None else ""
        self.update()

    def _is_preview_node(self):
        k = self.metadata.get("kind", "") or ""
        return str(k).startswith("preview_")

    def set_preview(self, info):
        """Store a preview payload and repaint the node body.

        info is the dict emitted by the runtime.preview_*() methods.
        The 'kind' field decides how the node renders it.
        """
        self._preview_info = dict(info or {})
        self._preview_kind = str(self._preview_info.get("kind", ""))
        self._preview_pixmap = None
        self._preview_lines = []

        kind = self._preview_kind

        if kind in ("image", "plot"):
            path = self._preview_info.get("path", "") or ""
            if path and os.path.isfile(path):
                from PyQt5.QtGui import QPixmap
                pm = QPixmap(path)
                if not pm.isNull():
                    self._preview_pixmap = pm
                    print("[preview] loaded %dx%d from %s"
                          % (pm.width(), pm.height(), path))
                else:
                    print("[preview] QPixmap failed for", path)

        elif kind == "table":
            cols = self._preview_info.get("cols", []) or []
            rows = self._preview_info.get("rows", []) or []
            if cols:
                self._preview_lines.append(
                    " | ".join(str(c)[:16] for c in cols))
                self._preview_lines.append("-" * 40)
            for r in rows[:10]:
                self._preview_lines.append(
                    " | ".join(str(x)[:16] for x in r))

        elif kind in ("text", "json"):
            text = self._preview_info.get("text", "") or ""
            self._preview_lines = text.split("\n")[:12]

        elif kind == "folder":
            n = self._preview_info.get("count", 0)
            files = self._preview_info.get("files", []) or []
            self._preview_lines.append("%d file(s)" % n)
            for p in files[:10]:
                self._preview_lines.append(os.path.basename(str(p)))

        elif kind in ("html", "markdown"):
            text = self._preview_info.get("text", "") or ""
            self._preview_lines = text.split("\n")[:12]

        elif kind in ("audio", "video"):
            path = self._preview_info.get("path", "") or ""
            self._preview_lines.append(
                "%s: %s" % (kind, os.path.basename(path) if path else "(none)"))

        else:
            self._preview_lines.append("(unknown preview kind: %s)"
                                       % kind)

        self.layout()
        self.update()

    def set_running(self, running):
        self.running = bool(running)
        self.layout()
        self.update()

    def append_output(self, text):
        self.output_lines.append(str(text))
        if len(self.output_lines) > 12:
            self.output_lines.pop(0)
        self.layout()
        self.update()

    def add_stdout(self, text):
        """Append captured stdout to both the shell strip and the full text."""
        if not text:
            return
        s = str(text).rstrip("\n")
        if not s:
            return
        for ln in s.splitlines():
            self.output_lines.append(ln)
            if len(self.output_lines) > 12:
                self.output_lines.pop(0)
        cur = getattr(self, "output_full", "") or ""
        if cur.strip() in ("", "None"):
            self.output_full = s
        else:
            self.output_full = cur + "\n" + s
        self.layout()
        self.update()

    def clear_output(self):
        self.output_lines = []
        self.output_full = ""
        self.running = False
        self.layout()
        self.update()

    def clone(self):
        n = Node(self.title, QColor(self.color), self.width, self.description,
                 dynamic=self.dynamic)
        n.sections = []; n.inputs = []; n.outputs = []
        n._current_section = None
        for sec in self.sections:
            if not (sec["name"] or sec["inputs"] or sec["outputs"]):
                continue
            n.add_section(sec["name"], sec.get("description", ""))
            for s in sec["inputs"]:
                n.add_input(s.name, s.socket_type, s.description, s.value)
            for s in sec["outputs"]:
                n.add_output(s.name, s.socket_type, s.description)
        n.metadata = dict(self.metadata)
        return n

    # -- serialization ---------------------------------------------------- #
    def to_dict(self):
        return {
            "title": self.title,
            "color": self.color.name(),
            "x": self.pos().x(),
            "y": self.pos().y(),
            "description": self.description,
            "dynamic": self.dynamic,
            "metadata": dict(self.metadata),
            "sections": [
                {
                    "name": sec["name"],
                    "description": sec.get("description", ""),
                    "collapsed": sec["collapsed"],
                    "inputs":  [[s.name, s.socket_type, s.description, s.value]
                                for s in sec["inputs"]],
                    "outputs": [[s.name, s.socket_type, s.description]
                                for s in sec["outputs"]],
                }
                for sec in self.sections
                if sec["name"] or sec["inputs"] or sec["outputs"]
            ],
        }

    @classmethod
    def from_dict(cls, d):
        n = cls(d["title"], QColor(d["color"]),
                description=d.get("description", ""),
                dynamic=d.get("dynamic", False))
        n.sections = []; n.inputs = []; n.outputs = []
        n._current_section = None
        for sec in d["sections"]:
            n.add_section(sec["name"], sec.get("description", ""))
            for entry in sec["inputs"]:
                name = entry[0]
                t = entry[1]
                desc = entry[2] if len(entry) > 2 else ""
                val  = entry[3] if len(entry) > 3 else None
                n.add_input(name, t, desc, val)
            for entry in sec["outputs"]:
                if len(entry) == 3:
                    name, t, desc = entry
                else:
                    name, t = entry; desc = ""
                n.add_output(name, t, desc)
            n.sections[-1]["collapsed"] = sec.get("collapsed", False)
        n.setPos(d["x"], d["y"])
        n.metadata = dict(d.get("metadata", {}))
        n.layout()
        return n

    # -- layout ----------------------------------------------------------- #
    def layout(self):
        for sec in self.sections:
            for s in sec["inputs"] + sec["outputs"]:
                s.setVisible(False)
        y = HEADER_HEIGHT + PADDING * 0.5
        for sec in self.sections:
            sec["rect"] = QRectF()
            if sec["name"]:
                sec["rect"] = QRectF(0, y, self.width, SECTION_HEIGHT)
                y += SECTION_HEIGHT
            if sec["collapsed"]:
                continue
            rows = max(len(sec["inputs"]), len(sec["outputs"]))
            for i in range(rows):
                cy = y + ROW_HEIGHT / 2.0
                if i < len(sec["inputs"]):
                    s = sec["inputs"][i]
                    s.setPos(0, cy); s.setVisible(True)
                if i < len(sec["outputs"]):
                    s = sec["outputs"][i]
                    s.setPos(self.width, cy); s.setVisible(True)
                y += ROW_HEIGHT

        if self._shell_collapsed:
            shell_h = SECTION_HEIGHT
        else:
            n_lines = len(self.output_lines)
            if self.running and n_lines == 0:
                n_lines = 1
            display_lines = max(1, n_lines)
            shell_h = SECTION_HEIGHT + display_lines * 12 + 8
        self._output_rect = QRectF(0, y, self.width, shell_h)
        y += shell_h

        # Preview area for preview_* nodes.  200px tall when the
        # node is a preview kind, zero otherwise.  Painted in
        # Node.paint(); content is filled by set_preview().
        if self._is_preview_node():
            self._preview_rect = QRectF(0, y, self.width, 200)
            y += 200
        else:
            self._preview_rect = QRectF()

        new_h = max(y + PADDING * 0.5, HEADER_HEIGHT + 12)
        if new_h != self.height:
            self.prepareGeometryChange()
            self.height = new_h
        self.update_edges()
        self.update()

    def boundingRect(self):
        r = SOCKET_RADIUS + 4
        return QRectF(-r, -r, self.width + 2 * r, self.height + r + 8)

    def shape(self):
        p = QPainterPath()
        p.addRoundedRect(QRectF(0, 0, self.width, self.height),
                         self.corner_radius, self.corner_radius)
        return p

    # -- paint ------------------------------------------------------------ #
    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        w, h, r = self.width, self.height, self.corner_radius
        body = QRectF(0, 0, w, h)
        base = self.color

        painter.setPen(Qt.NoPen)
        for dy, alpha in ((5, 22), (4, 38), (3, 58), (2, 82)):
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(body.translated(0, dy), r, r)

        bg = QLinearGradient(0, 0, 0, h)
        bg.setColorAt(0.0, NODE_BODY_TOP)
        bg.setColorAt(1.0, NODE_BODY_BOTTOM)
        painter.setBrush(QBrush(bg))
        painter.setPen(QPen(NODE_BORDER, 1.0))
        painter.drawRoundedRect(body, r, r)

        hp = rounded_top_path(0, 0, w, HEADER_HEIGHT, r)
        hg = QLinearGradient(0, 0, 0, HEADER_HEIGHT)
        hg.setColorAt(0.0, base.lighter(142))
        hg.setColorAt(1.0, base.darker(108))
        painter.setBrush(QBrush(hg))
        painter.setPen(Qt.NoPen)
        painter.drawPath(hp)

        painter.setPen(QPen(QColor(255, 255, 255, 40), 1.0))
        painter.drawLine(QPointF(r * 0.6, 1.2), QPointF(w - r * 0.6, 1.2))
        painter.setPen(QPen(NODE_HEADER_LINE, 1.0))
        painter.drawLine(QPointF(0, HEADER_HEIGHT), QPointF(w, HEADER_HEIGHT))

        f = QFont("Segoe UI", 9)
        f.setBold(True)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 0.3)
        painter.setFont(f)
        painter.setPen(QPen(TITLE_SHADOW))
        painter.drawText(QRectF(11, 1, w - 20, HEADER_HEIGHT),
                         Qt.AlignVCenter | Qt.AlignLeft, self.title)
        painter.setPen(QPen(TITLE_COLOR))
        painter.drawText(QRectF(10, 0, w - 20, HEADER_HEIGHT),
                         Qt.AlignVCenter | Qt.AlignLeft, self.title)

        painter.setFont(QFont("Segoe UI", 8))
        for idx, sec in enumerate(self.sections):
            if sec["name"]:
                sr = sec["rect"]
                hovered = (idx == self._hovered_section)
                bar = QRectF(4, sr.top() + 2, w - 8, sr.height() - 4)
                painter.setPen(Qt.NoPen)
                painter.setBrush(SECTION_BAR_HOV if hovered else SECTION_BAR_BG)
                painter.drawRoundedRect(bar, 4, 4)
                cx, cy = 12.0, sr.center().y()
                tri = QPolygonF()
                if sec["collapsed"]:
                    tri << QPointF(cx - 3, cy - 4) \
                        << QPointF(cx + 3, cy) \
                        << QPointF(cx - 3, cy + 4)
                else:
                    tri << QPointF(cx - 4, cy - 2) \
                        << QPointF(cx + 4, cy - 2) \
                        << QPointF(cx, cy + 3)
                painter.setBrush(QColor(200, 200, 200))
                painter.drawPolygon(tri)
                painter.setPen(QPen(SECTION_TEXT))
                painter.drawText(sr.adjusted(22, 0, -8, 0),
                                 Qt.AlignVCenter | Qt.AlignLeft, sec["name"])

            if sec["collapsed"]:
                continue
            painter.setPen(QPen(LABEL_COLOR))
            rows = max(len(sec["inputs"]), len(sec["outputs"]))
            for i in range(rows):
                if i < len(sec["inputs"]):
                    s = sec["inputs"][i]
                    label = s.name
                    if (not s.connections
                            and getattr(s, "value", None) is not None):
                        val = str(s.value)
                        if len(val) > 16:
                            val = val[:15] + "\u2026"
                        label = "%s = %s" % (s.name, val)
                    painter.drawText(
                        QRectF(14, s.y() - ROW_HEIGHT / 2,
                               w / 2 - 14, ROW_HEIGHT),
                        Qt.AlignVCenter | Qt.AlignLeft, label)
                if i < len(sec["outputs"]):
                    s = sec["outputs"][i]
                    painter.drawText(
                        QRectF(w / 2, s.y() - ROW_HEIGHT / 2,
                               w / 2 - 14, ROW_HEIGHT),
                        Qt.AlignVCenter | Qt.AlignRight, s.name)

        # block slot
        if _BLOCKS["show_slot"] or getattr(self, "blocks", None):
            try:
                br = self.block_rect()
                br_draw = QRectF(br.x(), self.height - _BLOCKS["slot_height"] - 2,
                                 br.width(), br.height())
                hov = getattr(self, "_block_hovered", False)
                if hov:
                    painter.setPen(QPen(QColor(255, 200, 80), 2.0,
                                        Qt.DashLine))
                    painter.setBrush(QColor(255, 200, 80, 60))
                else:
                    painter.setPen(QPen(QColor(80, 140, 220, 160),
                                        1.2, Qt.DashLine))
                    painter.setBrush(QColor(60, 110, 180, 26))
                painter.drawRoundedRect(br_draw, 4, 4)
            except Exception:
                pass

        # shell output
        orct = self._output_rect
        if orct.height() > 0:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, 140))
            painter.drawRect(orct)
            painter.setPen(QPen(QColor(255, 200, 60) if self.running
                                else QColor(255, 255, 255, 30), 1))
            painter.drawLine(QPointF(orct.left(), orct.top()),
                             QPointF(orct.right(), orct.top()))
            hdr = QRectF(orct.left(), orct.top(),
                         orct.width(), SECTION_HEIGHT)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 255, 255, 14))
            painter.drawRect(hdr)
            cx = hdr.left() + 10
            cy = hdr.center().y()
            tri = QPolygonF()
            if self._shell_collapsed:
                tri << QPointF(cx - 3, cy - 4) << QPointF(cx + 3, cy) \
                    << QPointF(cx - 3, cy + 4)
            else:
                tri << QPointF(cx - 4, cy - 2) << QPointF(cx + 4, cy - 2) \
                    << QPointF(cx, cy + 3)
            painter.setBrush(QColor(200, 200, 200))
            painter.setPen(Qt.NoPen)
            painter.drawPolygon(tri)
            f = QFont("Segoe UI", 8)
            f.setBold(True)
            painter.setFont(f)
            if self.running:
                painter.setPen(QColor(255, 200, 60))
                hdr_text = "SHELL  \u25cf  running\u2026"
            elif self._shell_collapsed:
                painter.setPen(QColor(150, 150, 150))
                n = len(self.output_lines)
                hdr_text = ("SHELL  (%d)" % n) if n else "SHELL"
            else:
                painter.setPen(QColor(150, 150, 150))
                hdr_text = "SHELL OUTPUT"
            painter.drawText(hdr.adjusted(22, 0, -8, 0),
                             Qt.AlignVCenter | Qt.AlignLeft, hdr_text)
            # full-output button at the right end of the header
            if self.output_full:
                btn = self._shell_button_rect()
                if btn.height() > 0:
                    hov = getattr(self, "_shell_btn_hovered", False)
                    painter.setBrush(
                        QColor(255, 200, 60, 90) if hov
                        else QColor(255, 255, 255, 26))
                    painter.setPen(QPen(
                        QColor(255, 200, 60) if hov
                        else QColor(200, 200, 200, 90), 1))
                    painter.drawRoundedRect(btn, 3, 3)
                    painter.setPen(QColor(240, 240, 240))
                    fb = QFont("Segoe UI", 8)
                    fb.setBold(True)
                    painter.setFont(fb)
                    painter.drawText(btn, Qt.AlignCenter, "full")
            if not self._shell_collapsed:
                fm = QFontMetrics(QFont("Segoe UI", 8))
                ty = orct.top() + SECTION_HEIGHT + 4
                if not self.output_lines:
                    painter.setPen(QColor(105, 105, 105))
                    placeholder = "(running\u2026)" if self.running \
                                  else "(no output yet)"
                    painter.drawText(
                        QRectF(orct.left() + 12, ty,
                               orct.width() - 24, 12),
                        Qt.AlignVCenter | Qt.AlignLeft, placeholder)
                else:
                    painter.setPen(QColor(210, 225, 210))
                    for line in self.output_lines:
                        painter.drawText(
                            QRectF(orct.left() + 12, ty,
                                   orct.width() - 24, 12),
                            Qt.AlignVCenter | Qt.AlignLeft,
                            fm.elidedText(str(line), Qt.ElideRight,
                                          int(orct.width() - 24)))
                        ty += 12

        # Preview area for preview_* nodes.
        prv = self._preview_rect
        if prv.height() > 0:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, 190))
            painter.drawRect(prv)
            painter.setPen(QPen(QColor(255, 255, 255, 32), 1))
            painter.drawRect(prv.adjusted(0.5, 0.5, -0.5, -0.5))

            inner = prv.adjusted(6, 6, -6, -6)

            if self._preview_pixmap is not None:
                pm = self._preview_pixmap
                scaled = pm.scaled(
                    int(inner.width()), int(inner.height()),
                    Qt.KeepAspectRatio, Qt.SmoothTransformation)
                x = inner.left() + (inner.width() - scaled.width()) / 2.0
                y = inner.top() + (inner.height() - scaled.height()) / 2.0
                painter.drawPixmap(int(x), int(y), scaled)

            elif self._preview_lines:
                f = QFont("Segoe UI", 8)
                painter.setFont(f)
                fm = QFontMetrics(f)
                ty = inner.top() + 2
                for line in self._preview_lines:
                    if ty + 12 > inner.bottom():
                        break
                    painter.setPen(QColor(220, 220, 220))
                    painter.drawText(
                        QRectF(inner.left(), ty, inner.width(), 12),
                        Qt.AlignVCenter | Qt.AlignLeft,
                        fm.elidedText(str(line), Qt.ElideRight,
                                      int(inner.width())))
                    ty += 12

            else:
                painter.setPen(QColor(120, 120, 120))
                painter.setFont(QFont("Segoe UI", 9))
                painter.drawText(inner, Qt.AlignCenter,
                                 "(no preview yet)")

        if self.running:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(255, 200, 60, 220), 2.0))
            painter.drawRoundedRect(body.adjusted(0.5, 0.5, -0.5, -0.5), r, r)

        if self.isSelected():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(SELECT_COLOR, 1.6))
            painter.drawRoundedRect(body.adjusted(0.5, 0.5, -0.5, -0.5), r, r)

    # -- hover / tooltip -------------------------------------------------- #
    def _show_tip(self, scene_pos, title, body):
        sc = self.scene()
        if sc is not None and hasattr(sc, "show_tooltip"):
            sc.show_tooltip(title, body, scene_pos)

    def _hide_tip(self):
        sc = self.scene()
        if sc is not None and hasattr(sc, "hide_tooltip"):
            sc.hide_tooltip()

    def hoverMoveEvent(self, event):
        pos = event.pos()
        # shell output button hover
        btn = self._shell_button_rect()
        new_hov = bool(self.output_full and btn.contains(pos))
        if new_hov != getattr(self, "_shell_btn_hovered", False):
            self._shell_btn_hovered = new_hov
            self.update()
        found = -1
        for idx, sec in enumerate(self.sections):
            if sec["name"] and sec["rect"].contains(pos):
                found = idx
                break
        if found != self._hovered_section:
            self._hovered_section = found
            self.update()
        if found >= 0:
            sec = self.sections[found]
            body = sec.get("description", "") or ""
            if not body:
                body = f"Section — {len(sec['inputs'])} in, " \
                       f"{len(sec['outputs'])} out"
            self._show_tip(event.scenePos(), sec["name"], body)
        elif pos.y() < HEADER_HEIGHT:
            body = self.description or ""
            if not body:
                body = f"{len(self.inputs)} inputs · " \
                       f"{len(self.outputs)} outputs"
            self._show_tip(event.scenePos(), self.title, body)
        else:
            self._hide_tip()
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event):
        if self._hovered_section != -1:
            self._hovered_section = -1
            self.update()
        self._hide_tip()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event):
        pos = event.pos()
        orct = self._output_rect
        if orct.height() > 0:
            # full-output button
            btn = self._shell_button_rect()
            if self.output_full and btn.contains(pos):
                sc = self.scene()
                if sc is not None and hasattr(sc, "full_output_requested"):
                    try:
                        sc.full_output_requested.emit(self)
                    except Exception:
                        pass
                event.accept(); return
            hdr = QRectF(orct.left(), orct.top(),
                         orct.width(), SECTION_HEIGHT)
            if hdr.contains(pos):
                self._shell_collapsed = not self._shell_collapsed
                self.layout()
                self.update()
                event.accept(); return
        _FOCUS["kind"] = "node"
        _FOCUS["object"] = self
        _FOCUS["section_index"] = -1
        for idx, sec in enumerate(self.sections):
            if sec["name"] and sec["rect"].contains(pos):
                _FOCUS["section_index"] = idx
                sec["collapsed"] = not sec["collapsed"]
                self.layout()
                event.accept(); return
        self._hide_tip()
        super().mousePressEvent(event)
        _FOCUS["kind"] = "node"
        _FOCUS["object"] = self
        _FOCUS["section_index"] = -1
        for idx, sec in enumerate(self.sections):
            if sec["name"] and sec["rect"].contains(pos):
                _FOCUS["section_index"] = idx
                sec["collapsed"] = not sec["collapsed"]
                self.layout()
                event.accept(); return
        self._hide_tip()
        super().mousePressEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.update_edges()
            self._settle_seq = getattr(self, "_settle_seq", 0) + 1
            seq = self._settle_seq
            QTimer.singleShot(300, lambda s=seq: self._maybe_settle(s))
        return super().itemChange(change, value)

    def _maybe_settle(self, seq):
        if sip.isdeleted(self):
            return
        if getattr(self, "_settle_seq", 0) != seq:
            return
        try:
            sc = self.scene()
        except RuntimeError:
            return
        if sc is not None and hasattr(sc, "node_settled"):
            try:
                sc.node_settled.emit(self)
            except Exception:
                pass

    def update_edges(self):
        for s in self.inputs + self.outputs:
            s.update_edges()


def build_node_from_template(template):
    _nc = template.get("node_class")
    if not _nc:
        _lc = template.get("_lifecycle") or {}
        _nc = _lc.get("node_class")
    if not _nc:
        _cat = str(template.get("category", "")).split("/")[0]
        _n = template.get("name", "")
        if _cat == "pyautogui" and _n in ("click", "hotkey", "press"):
            _nc = _n
    if not _nc:
        _k = template.get("kind")
        if _k in ("file_open", "file_read", "file_write"):
            _nc = _k
    _classes = globals().get("_NODE_CLASSES", {})
    _cls = _classes.get(_nc) if _nc else None
    if _cls is None:
        _cls = Node
    n = _cls(
        template["name"],
        QColor(template.get("color", "#3B3B3B")),
        description=template.get("description", ""),
        dynamic=template.get("dynamic", False),
    )
    n.sections = []
    n._current_section = None

    # Templates may arrive in two shapes:
    #   * the full form   -- template["sections"] = [(name, socks, desc), ...]
    #   * the shorthand   -- template["inputs"] = [...], template["outputs"] = [...]
    # register_node() normalises shorthand into sections, but templates
    # added via NODE_TEMPLATES.extend() at import time skip that step.
    # Normalise here so both shapes build.
    _sections = template.get("sections")
    if _sections is None:
        _sections = []
        _ins = template.get("inputs") or []
        _outs = template.get("outputs") or []
        if _ins:
            _norm_in = []
            for entry in _ins:
                if len(entry) == 5:
                    _norm_in.append((entry[0], entry[1], "in",
                                     entry[2], entry[3], entry[4]))
                elif len(entry) == 4:
                    _norm_in.append((entry[0], entry[1], "in",
                                     entry[2], entry[3]))
                elif len(entry) == 3:
                    _norm_in.append((entry[0], entry[1], "in", entry[2]))
                elif len(entry) == 2:
                    _norm_in.append((entry[0], entry[1], "in", ""))
            _sections.append(("Inputs", _norm_in, ""))
        if _outs:
            _norm_out = []
            for entry in _outs:
                if len(entry) == 4:
                    _norm_out.append((entry[0], entry[1], "out",
                                      entry[2], None, entry[3]))
                elif len(entry) == 3:
                    _norm_out.append((entry[0], entry[1], "out", entry[2]))
                elif len(entry) == 2:
                    _norm_out.append((entry[0], entry[1], "out", ""))
                else:
                    _norm_out.append((entry[0], entry[1], "out", ""))
            _sections.append(("Outputs", _norm_out, ""))

    for raw in _sections:
        if len(raw) == 2:
            sec_name, socks = raw; sec_desc = ""
        else:
            sec_name, socks, sec_desc = raw
        n.add_section(sec_name, sec_desc)
        for rs in socks:
            sock_name = rs[0]
            sock_type = rs[1]
            direction = rs[2]
            sock_desc = rs[3] if len(rs) > 3 else ""
            sock_default = rs[4] if len(rs) > 4 else None
            sock_options = rs[5] if len(rs) > 5 else None
            if direction == "in":
                n.add_input(sock_name, sock_type, sock_desc,
                            sock_default, sock_options)
            else:
                n.add_output(sock_name, sock_type, sock_desc,
                             sock_options)
    n.metadata["template"] = template["name"]
    if template.get("kind"):
        n.metadata["kind"] = template["kind"]
    try:
        n.after_template_built(template)
    except Exception as _ex:
        print("[nodeclass] after_template_built:", _ex)

    # Preview nodes get a manual "show full preview" button.
    _kind = str(template.get("kind", "") or "")
    if _kind.startswith("preview_"):
        try:
            n.add_button(
                "\U0001F50D",   # magnifying glass
                on_click=n._show_preview_button,
                tooltip="Show the full preview in a window",
                anchor=ANCHOR_HEADER, w=26, h=18)
        except Exception as _ex:
            print("[preview] could not add show button:", _ex)

    if template.get("flow", True):
        n.add_flow_sockets()
    return n


# --------------------------------------------------------------------------- #
#  Built-in node templates                                                    #
# --------------------------------------------------------------------------- #

NODE_TEMPLATES = [
    ("Input", [
        {"name": "Image Input", "color": "#4A6B8A",
         "description": "Loads an image from disk or memory.",
         "sections": [
             ("Image", [("Image", "image", "out", "The loaded image"),
                        ("Alpha", "float", "out", "Alpha channel mask")],
              "Image outputs"),
             ("Meta",  [("Width", "int", "out", "Image width in pixels"),
                        ("Height", "int", "out", "Image height in pixels")],
              "Metadata of the source image"),
         ]},
        {"name": "Value", "color": "#5A5A5A",
         "description": "Numeric constant.",
         "sections": [
             ("Value", [("Value", "float", "out", "The value")]),
         ]},
        {"name": "Color", "color": "#7A7A3A",
         "description": "RGBA color constant.",
         "sections": [
             ("Color", [("RGBA", "color", "out", "RGBA"),
                        ("Alpha", "float", "out", "Alpha")]),
         ]},
        {"name": "String", "color": "#6B4A8A",
         "description": "Text constant.",
         "sections": [
             ("String", [("Text", "string", "out", "The text")]),
         ]},
    ]),
    ("AI", [
        {"name": "Prompt", "color": "#6B4A8A",
         "description": "Text prompt for conditioning.",
         "sections": [
             ("Text", [("Text", "string", "out", "Prompt text"),
                       ("Weight", "float", "out", "Conditioning weight")]),
         ]},
        {"name": "Sampler", "color": "#8A4A4A",
         "description": "Diffusion sampler — turns noise into an image.",
         "sections": [
             ("Inputs", [("Image", "image", "in", "Source latent or image"),
                         ("Prompt", "string", "in", "Text prompt"),
                         ("Steps", "int", "in", "Number of steps"),
                         ("CFG", "float", "in", "Classifier-free guidance"),
                         ("Seed", "int", "in", "Random seed")],
              "Sampler inputs"),
             ("Outputs", [("Result", "image", "out", "Sampled image"),
                          ("Latent", "vector", "out", "Raw latent tensor")],
              "Sampler outputs"),
         ]},
        {"name": "Upscale", "color": "#4A8A6B",
         "description": "Upscales an image by a scale factor.",
         "sections": [
             ("Input", [("Image", "image", "in", "Source image"),
                        ("Scale", "float", "in", "Upscale factor")]),
             ("Output", [("Image", "image", "out", "Upscaled image")]),
         ]},
    ]),
    ("Utility", [
        {"name": "Math", "color": "#5A5A6A",
         "description": "Binary math operation.",
         "sections": [
             ("Inputs", [("A", "float", "in", "First operand"),
                         ("B", "float", "in", "Second operand")]),
             ("Output", [("Result", "float", "out", "Result")]),
         ]},
        {"name": "Reroute", "color": "#5A5A5A",
         "description": "Pass-through for tidy graphs.",
         "sections": [
             ("IO", [("In", "any", "in", "Input"),
                     ("Out", "any", "out", "Output")]),
         ]},
    ]),
    ("Output", [
        {"name": "Viewer", "color": "#8A7A4A",
         "description": "Displays an image in the viewport.",
         "sections": [
             ("Display", [("Image", "image", "in", "Image to display"),
                          ("Alpha", "float", "in", "Alpha channel")]),
         ]},
    ]),
]



NODE_TEMPLATES.extend([
    ("Built-ins/IO", [
        {"name": "print", "color": "#5A5A5A", "qualname": "print",
         "description": "**print.**  Send a value to stdout.",
         "inputs": [("Value", "any", "Value to print")]},

        {"name": "input", "color": "#5A5A5A", "qualname": "input",
         "description": "**input.**  Read one line from stdin.",
         "inputs": [("Prompt", "string", "Prompt text", "''")],
         "outputs": [("Text", "string", "User input")]},

        {"name": "len", "color": "#5A5A5A", "qualname": "len",
         "description": "**len.**  Length of a sequence.",
         "inputs": [("Value", "any", "Sequence")],
         "outputs": [("Length", "int", "Number of items")]},

        {"name": "type", "color": "#5A5A5A", "qualname": "type",
         "description": "**type.**  The class of a value.",
         "inputs": [("Value", "any", "Value")],
         "outputs": [("Type", "object", "The type object")]},

        {"name": "isinstance", "color": "#5A5A5A", "qualname": "isinstance",
         "description": "**isinstance.**  Check whether value has a type.",
         "inputs": [("Value", "any", "Value"),
                    ("Type", "any", "Type or tuple of types")],
         "outputs": [("Result", "bool", "True or False")]},

        {"name": "repr", "color": "#5A5A5A", "qualname": "repr",
         "description": "**repr.**  Developer-facing string of a value.",
         "inputs": [("Value", "any", "Value")],
         "outputs": [("Text", "string", "Repr string")]},

        {"name": "id", "color": "#5A5A5A", "qualname": "id",
         "description": "**id.**  Object identity.",
         "inputs": [("Value", "any", "Value")],
         "outputs": [("Id", "int", "Identity")]},
    ]),

    ("Built-ins/Convert", [
        {"name": "to int", "color": "#5A7A8A", "qualname": "int",
         "description": "**int(x).**  Convert to integer.",
         "inputs": [("Value", "any", "Value to convert")],
         "outputs": [("Result", "int", "Integer")]},

        {"name": "to float", "color": "#5A7A8A", "qualname": "float",
         "description": "**float(x).**  Convert to float.",
         "inputs": [("Value", "any", "Value to convert")],
         "outputs": [("Result", "float", "Float")]},

        {"name": "to str", "color": "#5A7A8A", "qualname": "str",
         "description": "**str(x).**  Convert to string.",
         "inputs": [("Value", "any", "Value to convert")],
         "outputs": [("Result", "string", "String")]},

        {"name": "to bool", "color": "#5A7A8A", "qualname": "bool",
         "description": "**bool(x).**  Convert to boolean.",
         "inputs": [("Value", "any", "Value to convert")],
         "outputs": [("Result", "bool", "Boolean")]},

        {"name": "to list", "color": "#5A7A8A", "qualname": "list",
         "description": "**list(x).**  Convert to a list.",
         "inputs": [("Value", "any", "Iterable")],
         "outputs": [("Result", "any", "List")]},

        {"name": "to tuple", "color": "#5A7A8A", "qualname": "tuple",
         "description": "**tuple(x).**  Convert to a tuple.",
         "inputs": [("Value", "any", "Iterable")],
         "outputs": [("Result", "any", "Tuple")]},

        {"name": "to set", "color": "#5A7A8A", "qualname": "set",
         "description": "**set(x).**  Convert to a set.",
         "inputs": [("Value", "any", "Iterable")],
         "outputs": [("Result", "any", "Set")]},

        {"name": "to dict", "color": "#5A7A8A", "qualname": "dict",
         "description": "**dict(x).**  Convert pairs to a dict.",
         "inputs": [("Value", "any", "Iterable of pairs")],
         "outputs": [("Result", "any", "Dictionary")]},
    ]),

    ("Built-ins/Logic", [
        {"name": "==", "color": "#6A8A5A",
         "call": "({A} == {B})",
         "description": "**Equality.**  a == b",
         "inputs": [("A", "any", "A", "None"), ("B", "any", "B", "None")],
         "outputs": [("Result", "bool", "True if equal")]},

        {"name": "!=", "color": "#6A8A5A",
         "call": "({A} != {B})",
         "description": "**Inequality.**  a != b",
         "inputs": [("A", "any", "A", "None"), ("B", "any", "B", "None")],
         "outputs": [("Result", "bool", "True if not equal")]},

        {"name": "<", "color": "#6A8A5A",
         "call": "({A} < {B})",
         "description": "**Less than.**",
         "inputs": [("A", "any", "A", "0"), ("B", "any", "B", "0")],
         "outputs": [("Result", "bool", "True if less")]},

        {"name": "<=", "color": "#6A8A5A",
         "call": "({A} <= {B})",
         "description": "**Less than or equal.**",
         "inputs": [("A", "any", "A", "0"), ("B", "any", "B", "0")],
         "outputs": [("Result", "bool", "True if ≤")]},

        {"name": ">", "color": "#6A8A5A",
         "call": "({A} > {B})",
         "description": "**Greater than.**",
         "inputs": [("A", "any", "A", "0"), ("B", "any", "B", "0")],
         "outputs": [("Result", "bool", "True if greater")]},

        {"name": ">=", "color": "#6A8A5A",
         "call": "({A} >= {B})",
         "description": "**Greater than or equal.**",
         "inputs": [("A", "any", "A", "0"), ("B", "any", "B", "0")],
         "outputs": [("Result", "bool", "True if ≥")]},

        {"name": "not", "color": "#6A8A5A",
         "call": "(not {Value})",
         "description": "**not.**  Logical negation.",
         "inputs": [("Value", "bool", "Value", "False")],
         "outputs": [("Result", "bool", "Negated value")]},

        {"name": "and", "color": "#6A8A5A",
         "call": "({A} and {B})",
         "description": "**and.**  Logical conjunction.",
         "inputs": [("A", "bool", "A", "True"), ("B", "bool", "B", "True")],
         "outputs": [("Result", "bool", "True if both")]},

        {"name": "or", "color": "#6A8A5A",
         "call": "({A} or {B})",
         "description": "**or.**  Logical disjunction.",
         "inputs": [("A", "bool", "A", "False"), ("B", "bool", "B", "False")],
         "outputs": [("Result", "bool", "True if either")]},

        {"name": "if / then / else", "color": "#8A7A4A",
         "call": "(({Then}) if {Condition} else ({Else}))",
         "description": "**Ternary.**  Value if condition is true, "
                        "else the other.",
         "inputs": [("Condition", "bool", "Condition", "True"),
                    ("Then", "any", "If true", "None"),
                    ("Else", "any", "If false", "None")],
         "outputs": [("Result", "any", "Selected value")]},
    ]),

    ("Built-ins/Math", [
        {"name": "+", "color": "#5A6A8A",
         "call": "({A} + {B})",
         "description": "**Add.**",
         "inputs": [("A", "any", "A", "0"), ("B", "any", "B", "0")],
         "outputs": [("Result", "any", "Sum")]},

        {"name": "-", "color": "#5A6A8A",
         "call": "({A} - {B})",
         "description": "**Subtract.**",
         "inputs": [("A", "any", "A", "0"), ("B", "any", "B", "0")],
         "outputs": [("Result", "any", "Difference")]},

        {"name": "*", "color": "#5A6A8A",
         "call": "({A} * {B})",
         "description": "**Multiply.**",
         "inputs": [("A", "any", "A", "1"), ("B", "any", "B", "1")],
         "outputs": [("Result", "any", "Product")]},

        {"name": "/", "color": "#5A6A8A",
         "call": "({A} / {B})",
         "description": "**True divide.**",
         "inputs": [("A", "any", "A", "1"), ("B", "any", "B", "1")],
         "outputs": [("Result", "any", "Quotient")]},

        {"name": "//", "color": "#5A6A8A",
         "call": "({A} // {B})",
         "description": "**Floor divide.**",
         "inputs": [("A", "any", "A", "1"), ("B", "any", "B", "1")],
         "outputs": [("Result", "any", "Quotient")]},

        {"name": "%", "color": "#5A6A8A",
         "call": "({A} % {B})",
         "description": "**Modulo.**",
         "inputs": [("A", "any", "A", "1"), ("B", "any", "B", "1")],
         "outputs": [("Result", "any", "Remainder")]},

        {"name": "**", "color": "#5A6A8A",
         "call": "({A} ** {B})",
         "description": "**Power.**",
         "inputs": [("A", "any", "Base", "2"), ("B", "any", "Exponent", "3")],
         "outputs": [("Result", "any", "Result")]},

        {"name": "abs", "color": "#5A6A8A", "qualname": "abs",
         "description": "**abs(x).**  Absolute value.",
         "inputs": [("Value", "any", "Value", "0")],
         "outputs": [("Result", "any", "Absolute value")]},

        {"name": "round", "color": "#5A6A8A", "qualname": "round",
         "description": "**round(x).**  Nearest integer.",
         "inputs": [("Value", "float", "Value", "0.0")],
         "outputs": [("Result", "int", "Rounded value")]},

        {"name": "min", "color": "#5A6A8A", "qualname": "min",
         "description": "**min(x, y).**  Smaller value.",
         "inputs": [("A", "any", "A", "0"), ("B", "any", "B", "0")],
         "outputs": [("Result", "any", "Minimum")]},

        {"name": "max", "color": "#5A6A8A", "qualname": "max",
         "description": "**max(x, y).**  Larger value.",
         "inputs": [("A", "any", "A", "0"), ("B", "any", "B", "0")],
         "outputs": [("Result", "any", "Maximum")]},

        {"name": "sum", "color": "#5A6A8A", "qualname": "sum",
         "description": "**sum(iterable).**  Sum of a sequence.",
         "inputs": [("Iterable", "any", "Values to add", "[0]")],
         "outputs": [("Result", "any", "Sum")]},

        {"name": "sqrt", "color": "#5A6A8A",
         "call": "math.sqrt({Value})",
         "description": "**math.sqrt(x).**  Square root.",
         "inputs": [("Value", "float", "Value", "0.0")],
         "outputs": [("Result", "float", "Square root")]},

        {"name": "floor", "color": "#5A6A8A",
         "call": "math.floor({Value})",
         "description": "**math.floor(x).**  Largest integer ≤ x.",
         "inputs": [("Value", "float", "Value", "0.0")],
         "outputs": [("Result", "int", "Floor")]},

        {"name": "ceil", "color": "#5A6A8A",
         "call": "math.ceil({Value})",
         "description": "**math.ceil(x).**  Smallest integer ≥ x.",
         "inputs": [("Value", "float", "Value", "0.0")],
         "outputs": [("Result", "int", "Ceiling")]},
    ]),

    ("Built-ins/Trig", [
        {"name": "sin", "color": "#5A6A9A",
         "call": "math.sin({Value})",
         "description": "**math.sin(x).**  Sine (radians).",
         "inputs": [("Value", "float", "Angle in radians", "0.0")],
         "outputs": [("Result", "float", "Sine")]},

        {"name": "cos", "color": "#5A6A9A",
         "call": "math.cos({Value})",
         "description": "**math.cos(x).**  Cosine (radians).",
         "inputs": [("Value", "float", "Angle in radians", "0.0")],
         "outputs": [("Result", "float", "Cosine")]},

        {"name": "tan", "color": "#5A6A9A",
         "call": "math.tan({Value})",
         "description": "**math.tan(x).**  Tangent (radians).",
         "inputs": [("Value", "float", "Angle in radians", "0.0")],
         "outputs": [("Result", "float", "Tangent")]},

        {"name": "atan2", "color": "#5A6A9A",
         "call": "math.atan2({Y}, {X})",
         "description": "**math.atan2(y, x).**  Two-argument arctangent.",
         "inputs": [("Y", "float", "Y", "0.0"), ("X", "float", "X", "1.0")],
         "outputs": [("Result", "float", "Angle in radians")]},

        {"name": "pi", "color": "#5A6A9A",
         "call": "math.pi",
         "description": "**math.pi.**  The constant π.",
         "outputs": [("Result", "float", "π")]},

        {"name": "e", "color": "#5A6A9A",
         "call": "math.e",
         "description": "**math.e.**  Euler's number.",
         "outputs": [("Result", "float", "e")]},
    ]),

    ("Built-ins/Random", [
        {"name": "random", "color": "#8A5A7A",
         "call": "random.random()",
         "description": "**random.random().**  Uniform float in [0, 1).",
         "outputs": [("Result", "float", "Random float")]},

        {"name": "randint", "color": "#8A5A7A",
         "call": "random.randint({Low}, {High})",
         "description": "**random.randint(a, b).**  Random integer in "
                        "`[a, b]`.",
         "inputs": [("Low", "int", "Low", "0"),
                    ("High", "int", "High", "10")],
         "outputs": [("Result", "int", "Random integer")]},

        {"name": "uniform", "color": "#8A5A7A",
         "call": "random.uniform({Low}, {High})",
         "description": "**random.uniform(a, b).**  Random float in "
                        "`[a, b]`.",
         "inputs": [("Low", "float", "Low", "0.0"),
                    ("High", "float", "High", "1.0")],
         "outputs": [("Result", "float", "Random float")]},

        {"name": "choice", "color": "#8A5A7A",
         "call": "random.choice({Iterable})",
         "description": "**random.choice(x).**  Pick a random element.",
         "inputs": [("Iterable", "any", "Items", "[1, 2, 3]")],
         "outputs": [("Result", "any", "Chosen item")]},

        {"name": "seed", "color": "#8A5A7A",
         "call": "random.seed({Value})",
         "description": "**random.seed(x).**  Reproducible randomness.",
         "inputs": [("Value", "int", "Seed", "0")],
         "outputs": [("Result", "any", "None")]},
    ]),

    ("Built-ins/String", [
        {"name": "upper", "color": "#8A4A8A",
         "call": "{Value}.upper()",
         "description": "**str.upper.**  Uppercase.",
         "inputs": [("Value", "string", "String", "''")],
         "outputs": [("Result", "string", "Uppercase string")]},

        {"name": "lower", "color": "#8A4A8A",
         "call": "{Value}.lower()",
         "description": "**str.lower.**  Lowercase.",
         "inputs": [("Value", "string", "String", "''")],
         "outputs": [("Result", "string", "Lowercase string")]},

        {"name": "strip", "color": "#8A4A8A",
         "call": "{Value}.strip()",
         "description": "**str.strip.**  Trim whitespace.",
         "inputs": [("Value", "string", "String", "''")],
         "outputs": [("Result", "string", "Trimmed string")]},

        {"name": "split", "color": "#8A4A8A",
         "call": "{Value}.split({Sep})",
         "description": "**str.split.**  Break on a separator.",
         "inputs": [("Value", "string", "String", "''"),
                    ("Sep",   "string", "Separator", "' '")],
         "outputs": [("Result", "any", "List of parts")]},

        {"name": "replace", "color": "#8A4A8A",
         "call": "{Value}.replace({Old}, {New})",
         "description": "**str.replace.**  Replace substring.",
         "inputs": [("Value", "string", "String", "''"),
                    ("Old",   "string", "Old", "''"),
                    ("New",   "string", "New", "''")],
         "outputs": [("Result", "string", "New string")]},

        {"name": "find", "color": "#8A4A8A",
         "call": "{Value}.find({Sub})",
         "description": "**str.find.**  Index of a substring, -1 if absent.",
         "inputs": [("Value", "string", "String", "''"),
                    ("Sub",   "string", "Substring", "''")],
         "outputs": [("Result", "int", "Index")]},

        {"name": "startswith", "color": "#8A4A8A",
         "call": "{Value}.startswith({Prefix})",
         "description": "**str.startswith.**",
         "inputs": [("Value",  "string", "String", "''"),
                    ("Prefix", "string", "Prefix", "''")],
         "outputs": [("Result", "bool", "True or False")]},

        {"name": "endswith", "color": "#8A4A8A",
         "call": "{Value}.endswith({Suffix})",
         "description": "**str.endswith.**",
         "inputs": [("Value",  "string", "String", "''"),
                    ("Suffix", "string", "Suffix", "''")],
         "outputs": [("Result", "bool", "True or False")]},

        {"name": "join", "color": "#8A4A8A",
         "call": "{Sep}.join({Items})",
         "description": "**str.join.**  Join items with a separator.",
         "inputs": [("Sep",   "string", "Separator", "''"),
                    ("Items", "any", "Items", "[]")],
         "outputs": [("Result", "string", "Joined string")]},

        {"name": "count", "color": "#8A4A8A",
         "call": "{Value}.count({Sub})",
         "description": "**str.count.**  Count occurrences.",
         "inputs": [("Value", "string", "String", "''"),
                    ("Sub",   "string", "Substring", "''")],
         "outputs": [("Result", "int", "Count")]},
    ]),

    ("Built-ins/List", [
        {"name": "sorted", "color": "#4A7A8A", "qualname": "sorted",
         "description": "**sorted(x).**  Sorted copy.",
         "inputs": [("Iterable", "any", "Iterable", "[]")],
         "outputs": [("Result", "any", "Sorted list")]},

        {"name": "reversed", "color": "#4A7A8A", "qualname": "reversed",
         "description": "**reversed(x).**  Reverse iterator.",
         "inputs": [("Iterable", "any", "Iterable", "[]")],
         "outputs": [("Result", "any", "Reversed")]},

        {"name": "range", "color": "#4A7A8A", "qualname": "range",
         "description": "**range(start, stop, step).**",
         "inputs": [("Start", "int", "Start", "0"),
                    ("Stop",  "int", "Stop",  "10"),
                    ("Step",  "int", "Step",  "1")],
         "outputs": [("Result", "any", "Range")]},

        {"name": "enumerate", "color": "#4A7A8A", "qualname": "enumerate",
         "description": "**enumerate(x).**  Yield (i, item) pairs.",
         "inputs": [("Iterable", "any", "Iterable", "[]")],
         "outputs": [("Result", "any", "Enumerate")]},

        {"name": "zip", "color": "#4A7A8A", "qualname": "zip",
         "description": "**zip(a, b).**  Pair items from two iterables.",
         "inputs": [("A", "any", "A", "[]"), ("B", "any", "B", "[]")],
         "outputs": [("Result", "any", "Zipped")]},

        {"name": "any", "color": "#4A7A8A", "qualname": "any",
         "description": "**any(x).**  True if any item is truthy.",
         "inputs": [("Iterable", "any", "Iterable", "[]")],
         "outputs": [("Result", "bool", "True or False")]},

        {"name": "all", "color": "#4A7A8A", "qualname": "all",
         "description": "**all(x).**  True if every item is truthy.",
         "inputs": [("Iterable", "any", "Iterable", "[]")],
         "outputs": [("Result", "bool", "True or False")]},

        {"name": "make list", "color": "#4A7A8A",
         "call": "[{A}, {B}, {C}]",
         "description": "**make list.**  Build a list from three values.",
         "inputs": [("A", "any", "A", "0"),
                    ("B", "any", "B", "0"),
                    ("C", "any", "C", "0")],
         "outputs": [("Result", "any", "The list")]},
    ]),

    ("Built-ins/Dict", [
        {"name": "keys", "color": "#4A4A8A",
         "call": "list({Value}.keys())",
         "description": "**dict.keys.**  Keys of a dictionary.",
         "inputs": [("Value", "any", "Dictionary", "{}")],
         "outputs": [("Result", "any", "List of keys")]},

        {"name": "values", "color": "#4A4A8A",
         "call": "list({Value}.values())",
         "description": "**dict.values.**  Values of a dictionary.",
         "inputs": [("Value", "any", "Dictionary", "{}")],
         "outputs": [("Result", "any", "List of values")]},

        {"name": "items", "color": "#4A4A8A",
         "call": "list({Value}.items())",
         "description": "**dict.items.**  (key, value) pairs.",
         "inputs": [("Value", "any", "Dictionary", "{}")],
         "outputs": [("Result", "any", "List of pairs")]},

        {"name": "get", "color": "#4A4A8A",
         "call": "{Value}.get({Key}, {Default})",
         "description": "**dict.get.**  Look up a key with a fallback.",
         "inputs": [("Value",   "any", "Dictionary", "{}"),
                    ("Key",     "any", "Key", "''"),
                    ("Default", "any", "Default", "None")],
         "outputs": [("Result", "any", "Value or default")]},

        {"name": "make dict", "color": "#4A4A8A",
         "call": "{{ {K1}: {V1}, {K2}: {V2} }}",
         "description": "**make dict.**  Build a dictionary from two "
                        "key/value pairs.",
         "inputs": [("K1", "string", "Key 1", "'a'"),
                    ("V1", "any", "Value 1", "1"),
                    ("K2", "string", "Key 2", "'b'"),
                    ("V2", "any", "Value 2", "2")],
         "outputs": [("Result", "any", "The dictionary")]},
    ]),
])


# ---------------------------------------------------------------- #
#  Preview category                                                 #
# ---------------------------------------------------------------- #
#
# These are separate from the Built-ins/Media trio, which lives in
# ROLES and is registered by _register_roles.  The names here are
# prefixed with "Preview" so they do not collide with the existing
# Image Viewer / Audio Player / Video Player nodes.

NODE_TEMPLATES.extend([
    ("Preview", [
        {"name": "Preview Image",
         "color": "#8A7A4A",
         "kind": "image_viewer",
         "description":
             "**Show an image while the pipeline runs.**\n\n"
             "Accepts:\n"
             "- a filesystem path (str)\n"
             "- a PIL image (anything with .save and .size)\n"
             "- a torch Tensor of shape (N,C,H,W), (C,H,W), or (H,W)\n"
             "  with C in {1, 3, 4}.  Values are normalised to [0, 1].",
         "inputs": [("Source", "any", "Path, PIL image, or tensor", "None")],
         "outputs": []},

        {"name": "Preview Audio",
         "color": "#8A7A4A",
         "kind": "audio_player",
         "description":
             "**Play an audio file while the pipeline runs.**\n\n"
             "Accepts a filesystem path to .wav / .mp3 / .ogg.",
         "inputs": [("Source", "any", "Path to an audio file", "None")],
         "outputs": []},

        {"name": "Preview Video",
         "color": "#8A7A4A",
         "kind": "video_player",
         "description":
             "**Play a video file while the pipeline runs.**\n\n"
             "Accepts a filesystem path to .mp4 / .mkv / .webm.",
         "inputs": [("Source", "any", "Path to a video file", "None")],
         "outputs": []},

        {"name": "Preview Folder",
         "color": "#7A6B4A",
         "kind": "image_viewer",
         "description":
             "**Open a folder of images as a grid.**\n\n"
             "Experimental.  Currently shows only the first image; "
             "the grid view is a planned upgrade.",
         "inputs": [("Source", "string", "Folder path", "'images/'")],
         "outputs": []},
    ]),
])



# ---------------------------------------------------------------- #
#  Preview category                                                 #
# ---------------------------------------------------------------- #

NODE_TEMPLATES.extend([
    ("Preview", [
        {"name": "Preview Image",
         "color": "#8A7A4A",
         "kind": "preview_image",
         "description":
             "**Show an image during a run.**\n\n"
             "Accepts a filesystem path, a PIL image, or a torch "
             "Tensor of shape (N,C,H,W), (C,H,W), or (H,W) with C in "
             "{1, 3, 4}.  Values are normalised to [0, 1] if "
             "Normalize is on.\n\n"
             "Outputs expose width, height, mode, format and file "
             "size so they can feed downstream nodes.",
         "inputs": [
             ("Source",    "any",    "Path, PIL image, or tensor", "None"),
             ("Title",     "string", "Window title", "''"),
             ("Scale",     "float",  "Zoom factor (1.0 = native)", "1.0"),
             ("Width",     "int",    "Max display width (0 = auto)", "0"),
             ("Height",    "int",    "Max display height (0 = auto)", "0"),
             ("Grid",      "bool",   "If multiple images, show as grid", "False"),
             ("Normalize", "bool",   "Normalise tensor values", "True"),
         ],
         "outputs": [
             ("Path",   "string", "Filesystem path of the shown image"),
             ("Width",  "int",    "Image width in pixels"),
             ("Height", "int",    "Image height in pixels"),
             ("Mode",   "string", "PIL mode (RGB, L, RGBA, ...)"),
             ("Format", "string", "PNG, JPEG, ..."),
             ("Size",   "int",    "File size in bytes"),
         ]},

        {"name": "Preview Audio",
         "color": "#8A7A4A",
         "kind": "preview_audio",
         "description":
             "**Play an audio file during a run.**\n\n"
             "Accepts a path to a .wav / .mp3 / .ogg file.  Uses "
             "QtMultimedia when available.\n\n"
             "Outputs expose duration, sample rate and channel count.",
         "inputs": [
             ("Source",   "any",    "Path to an audio file", "None"),
             ("Title",    "string", "Window title", "''"),
             ("Autoplay", "bool",   "Start playing immediately", "True"),
             ("Loop",     "bool",   "Repeat when finished", "False"),
             ("Volume",   "float",  "0.0 to 1.0", "1.0"),
             ("Start",    "float",  "Start position (seconds)", "0.0"),
         ],
         "outputs": [
             ("Path",       "string", "Filesystem path"),
             ("Duration",   "float",  "Duration in seconds"),
             ("SampleRate", "int",    "Sample rate in Hz"),
             ("Channels",   "int",    "Channel count"),
             ("Format",     "string", "Codec / container"),
         ]},

        {"name": "Preview Video",
         "color": "#8A7A4A",
         "kind": "preview_video",
         "description":
             "**Play a video file during a run.**\n\n"
             "Accepts a path to a .mp4 / .mkv / .webm file.  Uses "
             "QtMultimedia when available.\n\n"
             "Outputs expose duration, resolution, frame rate and "
             "codec.",
         "inputs": [
             ("Source",   "any",    "Path to a video file", "None"),
             ("Title",    "string", "Window title", "''"),
             ("Autoplay", "bool",   "Start playing immediately", "True"),
             ("Loop",     "bool",   "Repeat when finished", "False"),
             ("Start",    "float",  "Start position (seconds)", "0.0"),
             ("End",      "float",  "End position (0 = until end)", "0.0"),
             ("Muted",    "bool",   "Silence the audio", "False"),
         ],
         "outputs": [
             ("Path",     "string", "Filesystem path"),
             ("Duration", "float",  "Duration in seconds"),
             ("FPS",      "float",  "Frames per second"),
             ("Width",    "int",    "Frame width"),
             ("Height",   "int",    "Frame height"),
             ("Codec",    "string", "Codec name"),
         ]},

        {"name": "Preview Folder",
         "color": "#8A7A4A",
         "kind": "preview_folder",
         "description":
             "**List the contents of a folder.**\n\n"
             "Shows a table of filenames, sizes and modification "
             "times.  Optional glob filter and recursion.\n\n"
             "Outputs the file list and count so downstream nodes "
             "can iterate or filter them.",
         "inputs": [
             ("Source",    "string", "Folder path", "'.'"),
             ("Filter",    "string", "Glob pattern", "'*'"),
             ("Recursive", "bool",   "Include subfolders", "False"),
             ("Columns",   "int",    "Grid columns (0 = table)", "0"),
             ("ThumbSize", "int",    "Thumbnail size in pixels", "128"),
             ("Sort",      "string", "name | size | mtime", "'name'"),
         ],
         "outputs": [
             ("Path",  "string", "Folder path"),
             ("Count", "int",    "Number of matching files"),
             ("Files", "any",    "List of full paths"),
             ("First", "string", "Path of the first file"),
         ]},

        {"name": "Preview Table",
         "color": "#8A7A4A",
         "kind": "preview_table",
         "description":
             "**Show a table.**\n\n"
             "Accepts a pandas DataFrame, a list of dicts, a dict, "
             "or a list of lists.  Renders as a QTableWidget.\n\n"
             "Outputs row count, column count, column names and the "
             "first row so downstream logic can branch on shape.",
         "inputs": [
             ("Source",   "any",    "DataFrame / dict / list", "None"),
             ("Title",    "string", "Window title", "''"),
             ("MaxRows",  "int",    "Truncate after N rows", "100"),
             ("MaxCols",  "int",    "Truncate after N columns", "20"),
             ("Editable", "bool",   "Allow cell editing", "False"),
         ],
         "outputs": [
             ("Rows",    "int", "Rows displayed"),
             ("Cols",    "int", "Columns displayed"),
             ("Columns", "any", "List of column names"),
             ("FirstRow","any", "First row as a list"),
         ]},

        {"name": "Preview Text",
         "color": "#8A7A4A",
         "kind": "preview_text",
         "description":
             "**Show a text blob.**\n\n"
             "Any value is stringified and displayed in a "
             "scrollable viewer.  Optional line limit, word wrap "
             "and monospace font.\n\n"
             "Outputs the exact text that was shown.",
         "inputs": [
             ("Source",     "any",    "Value to display", "None"),
             ("Title",      "string", "Window title", "''"),
             ("MaxLines",   "int",    "Truncate after N lines", "500"),
             ("Wrap",       "bool",   "Word wrap", "True"),
             ("Monospace",  "bool",   "Monospace font", "True"),
             ("Highlight",  "string", "Keyword to highlight", "''"),
         ],
         "outputs": [
             ("Text",   "string", "The displayed text"),
             ("Length", "int",    "Character count"),
             ("Lines",  "int",    "Line count"),
         ]},

        {"name": "Preview JSON",
         "color": "#8A7A4A",
         "kind": "preview_json",
         "description":
             "**Show a JSON value as an indented tree.**\n\n"
             "Accepts any JSON-serialisable object.  Falls back to "
             "repr() when serialisation fails.\n\n"
             "Outputs the pretty-printed text and the top-level keys.",
         "inputs": [
             ("Source",   "any",    "Any value", "None"),
             ("Title",    "string", "Window title", "''"),
             ("Indent",   "int",    "Indent width", "2"),
             ("Sort",     "bool",   "Sort keys alphabetically", "False"),
             ("MaxDepth", "int",    "Truncate deeper than N", "20"),
         ],
         "outputs": [
             ("Text",   "string", "Pretty-printed JSON"),
             ("Valid",  "bool",   "True if JSON-serialisable"),
             ("Keys",   "any",    "Top-level keys"),
             ("Length", "int",    "Character count"),
         ]},

        {"name": "Preview Plot",
         "color": "#8A7A4A",
         "kind": "preview_plot",
         "description":
             "**Plot X and Y with matplotlib.**\n\n"
             "Kind is one of line, bar, scatter, hist, pie, "
             "step, fill.  Renders to a temp PNG and shows it "
             "using the same viewer as Preview Image.\n\n"
             "Outputs the path and min/max of each axis.",
         "inputs": [
             ("X",        "any",    "X values or labels", "None"),
             ("Y",        "any",    "Y values", "None"),
             ("Kind",     "string", "line | bar | scatter | hist | pie", "'line'"),
             ("Title",    "string", "Plot title", "''"),
             ("Xlabel",   "string", "X axis label", "''"),
             ("Ylabel",   "string", "Y axis label", "''"),
             ("Color",    "string", "Line / marker colour", "'#E08C4A'"),
             ("FigsizeW", "float",  "Figure width (inches)", "8.0"),
             ("FigsizeH", "float",  "Figure height (inches)", "5.0"),
         ],
         "outputs": [
             ("Path",   "string", "Path to the rendered PNG"),
             ("Points", "int",    "Number of data points"),
             ("XMin",   "float",  "Minimum X"),
             ("XMax",   "float",  "Maximum X"),
             ("YMin",   "float",  "Minimum Y"),
             ("YMax",   "float",  "Maximum Y"),
         ]},

        {"name": "Preview HTML",
         "color": "#8A7A4A",
         "kind": "preview_html",
         "description":
             "**Render HTML in a scrollable window.**\n\n"
             "Accepts an HTML string.  Useful for quick reports.",
         "inputs": [
             ("Source", "string", "HTML markup", "''"),
             ("Title",  "string", "Window title", "''"),
             ("Height", "int",    "Window height in pixels", "400"),
         ],
         "outputs": [
             ("Rendered", "bool", "True if rendered"),
         ]},

        {"name": "Preview Markdown",
         "color": "#8A7A4A",
         "kind": "preview_markdown",
         "description":
             "**Render Markdown in a scrollable window.**\n\n"
             "Accepts a Markdown string.  Uses Qt's built-in "
             "Markdown renderer.",
         "inputs": [
             ("Source", "string", "Markdown text", "''"),
             ("Title",  "string", "Window title", "''"),
             ("Height", "int",    "Window height in pixels", "400"),
         ],
         "outputs": [
             ("Rendered", "bool", "True if rendered"),
         ]},
    ]),
])




# ---------------------------------------------------------------- #
#  Normalise shorthand templates                                     #
# ---------------------------------------------------------------- #
#
# register_node() converts inputs=[...] / outputs=[...] shorthand
# into the canonical "sections" form before appending to
# NODE_TEMPLATES.  Templates added directly with
# NODE_TEMPLATES.extend() never go through that step, so they land
# with the shorthand still attached.
#
# build_node_from_template() only understands "sections".  This
# helper walks every template once and normalises whatever is still
# in shorthand form, so both registration paths end up identical.

def _normalize_extended_templates():
    for _cat, _tpls in NODE_TEMPLATES:
        for _t in _tpls:
            if "sections" in _t:
                continue
            _secs = []
            for _key, _dir in (("inputs", "in"), ("outputs", "out")):
                _list = _t.pop(_key, None) or []
                if not _list:
                    continue
                _norm = []
                for _e in _list:
                    if len(_e) >= 3 and _e[2] in ("in", "out"):
                        _norm.append(tuple(_e))
                        continue
                    _name = _e[0] if len(_e) > 0 else ""
                    _type = _e[1] if len(_e) > 1 else "any"
                    _desc = _e[2] if len(_e) > 2 else ""
                    _def  = _e[3] if len(_e) > 3 else None
                    _opts = _e[4] if len(_e) > 4 else None
                    _norm.append((_name, _type, _dir, _desc, _def, _opts))
                _secs.append(
                    ("Inputs" if _dir == "in" else "Outputs", _norm, ""))
            _t["sections"] = _secs


_normalize_extended_templates()




# ---------------------------------------------------------------- #
#  Deduplicate NODE_TEMPLATES by name                                #
# ---------------------------------------------------------------- #
#
# Multiple patches may have added templates with the same name.
# find_template() returns the first match, which is the oldest; the
# one we want is the newest.  This collapses the list so each name
# appears once, keeping the last occurrence.

def _dedupe_templates():
    seen = {}
    for _cat, _tpls in NODE_TEMPLATES:
        for _t in _tpls:
            seen[_t["name"]] = _t
    removed = 0
    new_list = []
    for _cat, _tpls in NODE_TEMPLATES:
        kept = []
        for _t in _tpls:
            if seen.get(_t["name"]) is _t:
                kept.append(_t)
            else:
                removed += 1
        if kept:
            new_list.append((_cat, kept))
    NODE_TEMPLATES[:] = new_list
    return removed


_dedupe_templates()



NODE_TEMPLATES.extend([
    ("Built-ins/IO", [
        {"name": "Import PyTorchUI JSON",
         "color": "#5A5A5A",
         "kind": "pyui_json",
         "description":
             "**Read a PyTorchUI graph file (or any JSON) and "
             "return the parsed dict.**",
         "inputs": [("Path", "string", "Path to a .json file",
                     "'graph.json'")],
         "outputs": [("Result", "any",
                      "Parsed dict from the file")]},
    ]),
])

_BUILTIN_TEMPLATES = _copy.deepcopy(NODE_TEMPLATES)


# Name -> template dict, kept in sync with NODE_TEMPLATES.  Without
# this, every register_node call scans the whole list and startup is
# O(n^2).  This is the difference between a ~140 s boot and a ~1 s one.
_TEMPLATE_BY_NAME = {}


def _rebuild_template_index():
    _TEMPLATE_BY_NAME.clear()
    for _cat, _tpls in NODE_TEMPLATES:
        for _t in _tpls:
            _TEMPLATE_BY_NAME[_t["name"]] = _t


_rebuild_template_index()


def find_template(name):
    tpl = _TEMPLATE_BY_NAME.get(name)
    if tpl is not None:
        return tpl
    # Fallback for templates added before the index existed.  Also
    # populates the index so the next call is O(1).
    for _, tpls in NODE_TEMPLATES:
        for tpl in tpls:
            if tpl["name"] == name:
                _TEMPLATE_BY_NAME[name] = tpl
                return tpl
    return None


# --------------------------------------------------------------------------- #
#  Scene                                                                      #
# --------------------------------------------------------------------------- #

class NodeScene(QGraphicsScene):
    graph_changed = pyqtSignal()
    full_output_requested = pyqtSignal(object)
    node_settled = pyqtSignal(object)
    block_dropped = pyqtSignal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSceneRect(-6000, -6000, 12000, 12000)
        self.grid_size = 20
        self.temp_edge = None
        self.drag_socket = None
        self._tooltip = None
        self._conn_timer = QTimer(self)
        self._conn_timer.setInterval(16)      # ~60 Hz
        self._conn_timer.timeout.connect(self._tick_connection)

    def show_tooltip(self, title, body, scene_pos):
        if self._tooltip is None or sip.isdeleted(self._tooltip):
            self._tooltip = HoverTooltip()
            self.addItem(self._tooltip)
        try:
            self._tooltip.set_content(title, body)
            self._tooltip.setPos(scene_pos)
            self._tooltip.setVisible(True)
        except RuntimeError:
            self._tooltip = None

    def hide_tooltip(self):
        if self._tooltip is not None and not sip.isdeleted(self._tooltip):
            try:
                self._tooltip.setVisible(False)
            except RuntimeError:
                self._tooltip = None

    def drawBackground(self, painter, rect):
        painter.fillRect(rect, QColor("#1E1E1E"))
        scale = painter.worldTransform().m11() or 1.0
        gs = self.grid_size
        while gs * scale < 7:
            gs *= 2

        def make_lines(step):
            out = []
            left = int(rect.left()) - (int(rect.left()) % step)
            top = int(rect.top()) - (int(rect.top()) % step)
            for x in range(left, int(rect.right()) + step, step):
                out.append(QLineF(x, rect.top(), x, rect.bottom()))
            for y in range(top, int(rect.bottom()) + step, step):
                out.append(QLineF(rect.left(), y, rect.right(), y))
            return out

        painter.setPen(QPen(QColor(255, 255, 255, 12), 0))
        painter.drawLines(make_lines(gs))
        painter.setPen(QPen(QColor(255, 255, 255, 26), 0))
        painter.drawLines(make_lines(gs * 5))

    def start_connection(self, socket, scene_pos):
        self.hide_tooltip()
        self.drag_socket = socket
        self.temp_edge = Edge()
        if socket.is_input:
            self.temp_edge.end_socket = socket
        else:
            self.temp_edge.start_socket = socket
        self.temp_edge.loose_end = scene_pos
        self.addItem(self.temp_edge)
        self.temp_edge.update_path()
        if not self._conn_timer.isActive():
            self._conn_timer.start()

    def update_connection(self, scene_pos):
        if self.temp_edge is not None:
            self.temp_edge.loose_end = scene_pos
            self.temp_edge.update_path()

    def _tick_connection(self):
        if self.temp_edge is None:
            self._conn_timer.stop()
            return
        views = self.views()
        if not views:
            return
        view = views[0]
        gp = QCursor.pos()
        vp = view.viewport().mapFromGlobal(gp)
        if not view.viewport().rect().contains(vp):
            return
        scene_pos = view.mapToScene(vp)
        self.temp_edge.loose_end = scene_pos
        self.temp_edge.update_path()
        try:
            self.highlight_valid_targets(scene_pos)
        except Exception:
            pass

    def finish_connection(self, scene_pos):
        edge = self.temp_edge
        start = self.drag_socket
        self.temp_edge = None
        self.drag_socket = None
        try:
            if self._conn_timer.isActive():
                self._conn_timer.stop()
        except Exception:
            pass
        if edge is not None:
            self.removeItem(edge)
        if start is None:
            return
        target = self.itemAt(scene_pos, QTransform())
        if isinstance(target, NodeSocket) and target is not start:
            self.connect_sockets(start, target)

    def connect_sockets(self, a, b):
        if a is None or b is None or a is b:
            return None
        if a.is_input == b.is_input:
            return None
        src = a if not a.is_input else b
        dst = b if not a.is_input else a
        if (src.socket_type != dst.socket_type
                and "any" not in (src.socket_type, dst.socket_type)):
            return None
        for e in list(dst.connections):
            self.remove_edge(e)
        edge = Edge(src, dst)
        src.connections.append(edge)
        dst.connections.append(edge)
        self.addItem(edge)
        edge.update_path()
        edge.apply_path_visibility()
        src.node.update(); dst.node.update()
        self.graph_changed.emit()
        return edge

    def remove_edge(self, edge):
        for s in (edge.start_socket, edge.end_socket):
            if s is not None and edge in s.connections:
                s.connections.remove(edge)
        if edge.scene() is self:
            self.removeItem(edge)
        for s in (edge.start_socket, edge.end_socket):
            if s is not None:
                s.node.update()
        self.graph_changed.emit()

    def remove_node(self, node):
        for s in list(node.inputs) + list(node.outputs):
            for e in list(s.connections):
                self.remove_edge(e)
        if node.scene() is self:
            self.removeItem(node)
        self.graph_changed.emit()

    def add_node_from_template(self, template, scene_pos=None):
        node = build_node_from_template(template)
        if scene_pos is None:
            scene_pos = QPointF(0, 0)
        node.setPos(scene_pos - QPointF(node.width * 0.5, 0))
        self.addItem(node)
        self.clearSelection()
        node.setSelected(True)
        self.graph_changed.emit()
        return node

    def to_dict(self):
        node_list = [i for i in self.items() if isinstance(i, Node)]
        index_of = {id(n): i for i, n in enumerate(node_list)}
        nodes_data = [n.to_dict() for n in node_list]
        edges_data = []
        for item in self.items():
            if (isinstance(item, Edge)
                    and item.start_socket is not None
                    and item.end_socket is not None):
                edges_data.append({
                    "from_node": index_of[id(item.start_socket.node)],
                    "from_socket": item.start_socket.name,
                    "to_node": index_of[id(item.end_socket.node)],
                    "to_socket": item.end_socket.name,
                })
        return {"nodes": nodes_data, "edges": edges_data}

    def _build_saved_node(self, nd):
        """Rebuild one node, preserving its specialised class.

        Loading through Node.from_dict() alone would downgrade every
        pyautogui click / press / hotkey node to a plain Node, and lose
        its header capture button.  Look up the template first; if it
        carries a node_class tag, build through
        build_node_from_template and then overwrite the default
        sockets with the saved ones.
        """
        meta = nd.get("metadata") or {}
        tpl_name = meta.get("template")
        tpl = find_template(tpl_name) if tpl_name else None
        if tpl is None:
            return Node.from_dict(nd)

        # Build via the factory.  build_node_from_template already runs
        # after_template_built (which adds the capture button) and
        # add_flow_sockets; wipe the socket lists below and re-add from
        # saved data.  Attached widgets survive the wipe.
        n = build_node_from_template(tpl)
        for s in list(n.inputs) + list(n.outputs):
            s.setParentItem(None)
        n.inputs.clear()
        n.outputs.clear()
        n.sections = []
        n._current_section = None

        for sec in nd.get("sections", []):
            n.add_section(sec.get("name", ""),
                          sec.get("description", ""))
            for entry in sec.get("inputs", []):
                name = entry[0]
                t    = entry[1]
                desc = entry[2] if len(entry) > 2 else ""
                val  = entry[3] if len(entry) > 3 else None
                n.add_input(name, t, desc, val)
            for entry in sec.get("outputs", []):
                name = entry[0]
                t    = entry[1]
                desc = entry[2] if len(entry) > 2 else ""
                n.add_output(name, t, desc)
            n.sections[-1]["collapsed"] = sec.get("collapsed", False)

        n.setPos(nd.get("x", 0.0), nd.get("y", 0.0))
        n.metadata.update(meta)
        n.set_title(nd.get("title", tpl_name))
        n.set_node_color(nd.get("color", tpl.get("color", "#3B3B3B")))
        n.set_description(nd.get("description", ""))
        n.dynamic = bool(nd.get("dynamic", n.dynamic))
        n.layout()
        return n

    def load_from_dict(self, data):
        self.clear()
        self.temp_edge = None
        self.drag_socket = None
        self._tooltip = None
        created = []
        for nd in data.get("nodes", []):
            n = self._build_saved_node(nd)
            self.addItem(n)
            created.append(n)
        for ed in data.get("edges", []):
            try:
                sn = created[ed["from_node"]]
                dn = created[ed["to_node"]]
            except (IndexError, KeyError):
                continue
            src = next((s for s in sn.outputs
                        if s.name == ed["from_socket"]), None)
            dst = next((s for s in dn.inputs
                        if s.name == ed["to_socket"]), None)
            if src is not None and dst is not None:
                self.connect_sockets(src, dst)
        self.graph_changed.emit()

    def counts(self):
        n = sum(1 for i in self.items() if isinstance(i, Node))
        e = sum(1 for i in self.items() if isinstance(i, Edge))
        return n, e


# --------------------------------------------------------------------------- #
#  Menu style                                                                 #
# --------------------------------------------------------------------------- #

MENU_STYLE = """
QMenu { background:#2E2E2E; color:#DDDDDD; border:1px solid #1A1A1A;
        padding:4px 0px; }
QMenu::item { padding:5px 26px 5px 22px; background:transparent; }
QMenu::item:selected { background:#E08C4A; color:#1A1A1A; }
QMenu::item:disabled { color:#666; }
QMenu::separator { height:1px; background:#3E3E3E; margin:4px 6px; }
QMenu::right-arrow { width:12px; height:12px; }
"""


# --------------------------------------------------------------------------- #
#  Add-node popup                                                             #
# --------------------------------------------------------------------------- #

class AddNodePopup(QMenu):
    def __init__(self, scene, scene_pos, parent=None):
        super().__init__(parent)
        self.node_scene = scene
        self.scene_pos = scene_pos
        self.setStyleSheet(MENU_STYLE)
        self.setMinimumWidth(240)
        self._menus = {}

        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search…  (try cat:Torch/nn)")
        self.search.setStyleSheet(
            "QLineEdit{background:#3C3C3C;border:1px solid #555;"
            "border-radius:3px;padding:3px 6px;color:#EEE;}"
            "QLineEdit:focus{border:1px solid #E08C4A;}")
        self.search.textChanged.connect(self._refilter)
        wa = QWidgetAction(self)
        holder = QWidget()
        h = QVBoxLayout(holder)
        h.setContentsMargins(6, 6, 6, 4)
        h.addWidget(self.search)
        wa.setDefaultWidget(holder)
        self.addAction(wa)
        self.addSeparator()
        self._build_items()
        self.search.setFocus()

    def _build_items(self):
        for category, templates in NODE_TEMPLATES:
            if _is_category_disabled(category):
                continue
            parent_menu = self._ensure_menu(category)
            for tpl in templates:
                act = parent_menu.addAction(tpl["name"])
                if tpl.get("description"):
                    act.setToolTip(tpl["description"])
                act.setProperty("search_text",
                                (tpl["name"] + " " + str(category)).lower())
                act.triggered.connect(
                    lambda checked=False, t=tpl: self._add(t))

    def _ensure_menu(self, path):
        if isinstance(path, (list, tuple)):
            parts = [str(p) for p in path]
        else:
            parts = [p for p in str(path).split("/") if p]
        if not parts:
            parts = ["Custom"]
        parent = self
        walked = ""
        for p in parts:
            walked = (walked + "/" + p) if walked else p
            if walked in self._menus:
                parent = self._menus[walked]
                continue
            sub = parent.addMenu(p)
            sub.setStyleSheet(MENU_STYLE)
            self._menus[walked] = sub
            parent = sub
        return parent

    def _add(self, template):
        self.node_scene.add_node_from_template(template, self.scene_pos)

    def _parse_query(self, text):
        cats = []
        words = []
        for tok in text.strip().split():
            low = tok.lower()
            if (low.startswith("cat:")
                    or low.startswith("category:")
                    or low.startswith("path:")):
                val = tok.split(":", 1)[1].strip().lower()
                if val:
                    cats.append(val)
            else:
                words.append(low)
        return cats, words

    def _refilter(self, text):
        cats, words = self._parse_query(text)
        for top in self.actions():
            menu = top.menu()
            if menu is None:
                continue
            any_visible = self._filter_menu(menu, cats, words, "")
            top.setVisible(any_visible)

    def _filter_menu(self, menu, cats, words, prefix):
        title = menu.title() or ""
        path_here = (prefix + "/" + title).strip("/").lower()
        any_visible = False
        for act in menu.actions():
            sub = act.menu()
            if sub is not None:
                v = self._filter_menu(sub, cats, words, path_here)
                act.setVisible(v)
                any_visible = any_visible or v
            else:
                st = (act.property("search_text") or "").lower()
                full = st + " " + path_here
                self_match = True
                for c in cats:
                    if c not in path_here:
                        self_match = False
                        break
                if self_match:
                    for w in words:
                        if w not in full:
                            self_match = False
                            break
                act.setVisible(self_match)
                any_visible = any_visible or self_match
        return any_visible


# --------------------------------------------------------------------------- #
#  View                                                                       #
# --------------------------------------------------------------------------- #

class NodeView(QGraphicsView):
    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self._panning = False
        self._pan_start = QPointF()
        self._last_scene_pos = QPointF(0.0, 0.0)
        self._select_counter = 0

    def current_scene_pos(self):
        gp = QCursor.pos()
        vp = self.viewport().mapFromGlobal(gp)
        if self.viewport().rect().contains(vp):
            return self.mapToScene(vp)
        return self._last_scene_pos

    def scene_pos_at_cursor(self):
        return self.current_scene_pos()

    def wheelEvent(self, event):
        # Unlimited zoom.
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)
        self._last_scene_pos = self.mapToScene(event.pos())

    def mousePressEvent(self, event):
        self._last_scene_pos = self.mapToScene(event.pos())
        if event.button() == Qt.MiddleButton:
            self._panning = True
            self._pan_start = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept(); return
        # Record selection order so the parent/child menu can use it.
        if event.button() == Qt.LeftButton:
            item = self.scene().itemAt(
                self._last_scene_pos, self.transform())
            cur = item
            while cur is not None and not isinstance(cur, Node):
                cur = cur.parentItem()
            if cur is not None:
                self._select_counter = (
                    getattr(self, "_select_counter", 0) + 1)
                cur._select_order = self._select_counter
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self._last_scene_pos = self.mapToScene(event.pos())
        if self._panning:
            d = event.pos() - self._pan_start
            self._pan_start = event.pos()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - d.x())
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - d.y())
            event.accept(); return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._last_scene_pos = self.mapToScene(event.pos())
        if event.button() == Qt.MiddleButton:
            self._panning = False
            self.setCursor(Qt.ArrowCursor)
            event.accept(); return
        super().mouseReleaseEvent(event)

    def show_add_menu_at_cursor(self):
        scene_pos = self.current_scene_pos()
        menu = AddNodePopup(self.scene(), scene_pos, self)
        menu.exec_(QCursor.pos())

    def contextMenuEvent(self, event):
        scene_pos = self.mapToScene(event.pos())
        item = self.scene().itemAt(scene_pos, self.transform())
        node = None
        cur = item
        while cur is not None:
            if isinstance(cur, Node):
                node = cur; break
            cur = cur.parentItem()
        if node is None:
            menu = AddNodePopup(self.scene(), scene_pos, self)
            menu.exec_(event.globalPos())
        else:
            self._node_context_menu(node, event.globalPos())

    def _node_context_menu(self, node, global_pos):
        menu = QMenu(self)
        menu.setStyleSheet(MENU_STYLE)
        sel = [i for i in self.scene().selectedItems() if isinstance(i, Node)]
        if node not in sel:
            self.scene().clearSelection()
            node.setSelected(True)
            sel = [node]

        # Kept for reference: sort the selection in the order the user
        # clicked, so the submenu lists parents in that order too.
        ordered = sorted(sel,
                         key=lambda n: getattr(n, "_select_order", 0))

        act_dup = menu.addAction("Duplicate\tCtrl+D")

        # Submenu: pick which node is the parent.
        parent_actions = {}
        if len(ordered) >= 2:
            menu.addSeparator()
            sub = menu.addMenu("Make these a child of")
            sub.setStyleSheet(MENU_STYLE)
            for candidate in ordered:
                others = [n for n in ordered if n is not candidate]
                label = "%s  (make %d child%s)" % (
                    candidate.title,
                    len(others),
                    "" if len(others) == 1 else "ren")
                act = sub.addAction(label)
                parent_actions[act] = candidate

        act_detach = None
        has_parent = any(getattr(n, "parent_node", None) is not None
                         for n in ordered)
        if has_parent:
            menu.addSeparator()
            act_detach = menu.addAction("Detach from parent")

        menu.addSeparator()
        act_del = menu.addAction("Delete\tDel")

        chosen = menu.exec_(global_pos)
        if chosen is None:
            return
        if chosen is act_dup:
            self.duplicate_selected()
        elif chosen in parent_actions:
            parent = parent_actions[chosen]
            children = [n for n in ordered if n is not parent]
            self._make_children(parent, children)
        elif act_detach is not None and chosen is act_detach:
            for n in ordered:
                p = getattr(n, "parent_node", None)
                if p is not None:
                    p.remove_block(n)
            self.scene().graph_changed.emit()
        elif chosen is act_del:
            for n in sel:
                self.scene().remove_node(n)


    def _make_children(self, parent, children):
        count = 0
        for child in children:
            if child is parent:
                continue
            try:
                if parent.add_block(child):
                    count += 1
            except Exception as ex:
                print("[children]", ex)
        parent.layout_blocks()
        self.scene().graph_changed.emit()
        win = self.window()
        if hasattr(win, "report"):
            win.report(
                "Made '%s' the parent of %d child(ren)"
                % (parent.title, count),
                "success", 2500)


    def duplicate_selected(self):
        nodes = [i for i in self.scene().selectedItems() if isinstance(i, Node)]
        if not nodes:
            return
        self.scene().clearSelection()
        for n in nodes:
            copy = n.clone()
            copy.setPos(n.pos() + QPointF(30, 30))
            self.scene().addItem(copy)
            copy.setSelected(True)
        self.scene().graph_changed.emit()

    def delete_selected(self):
        sc = self.scene()
        for item in list(sc.selectedItems()):
            if isinstance(item, Edge):
                sc.remove_edge(item)
            elif isinstance(item, Node):
                sc.remove_node(item)

    def select_all(self):
        for i in self.scene().items():
            if isinstance(i, Node):
                i.setSelected(True)

    def frame_all(self):
        items = [i for i in self.scene().items() if isinstance(i, Node)]
        if not items:
            return
        r = QRectF()
        for it in items:
            r = r.united(it.sceneBoundingRect())
        if r.isValid():
            self.fitInView(r.adjusted(-40, -40, 40, 40), Qt.KeepAspectRatio)

    def keyPressEvent(self, event):
        mods = event.modifiers()
        if event.key() == Qt.Key_A and (mods & Qt.ShiftModifier):
            self.show_add_menu_at_cursor(); event.accept(); return
        if event.key() == Qt.Key_D and (mods & Qt.ControlModifier):
            self.duplicate_selected(); event.accept(); return
        if event.key() == Qt.Key_A and (mods & Qt.ControlModifier):
            self.select_all(); event.accept(); return
        if event.key() == Qt.Key_Home:
            self.frame_all(); event.accept(); return
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selected(); event.accept(); return
        if event.key() == Qt.Key_G and (mods & Qt.ShiftModifier):
            window = self.window()
            if hasattr(window, "_make_function_from_selection"):
                window._make_function_from_selection()
            event.accept(); return
        if event.key() == Qt.Key_K and (mods & Qt.ShiftModifier):
            window = self.window()
            if hasattr(window, "_make_class_from_selection"):
                window._make_class_from_selection()
            event.accept(); return
        super().keyPressEvent(event)

class NodeLibraryPanel(QWidget):
    node_requested = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("LeftPanel")
        self.setFixedWidth(230)
        self._cat_items = {}
        try:
            _lib_filter_load()
        except Exception as _e:
            print("[filter] load skipped:", _e)
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(6)
        title = QLabel("NODE LIBRARY")
        title.setStyleSheet("color:#7F7F7F;font-size:10px;font-weight:bold;"
                            "letter-spacing:1.5px;")
        v.addWidget(title)
        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.setSpacing(4)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter  (try cat:Torch/nn)")
        self.search.textChanged.connect(self._filter)
        search_row.addWidget(self.search, 1)
        self.btn_filter = QToolButton()
        self.btn_filter.setText("Filter\u2026")
        self.btn_filter.setToolTip(
            "Tick the categories you want to see")
        self.btn_filter.setCursor(Qt.PointingHandCursor)
        self.btn_filter.setStyleSheet(
            "QToolButton{background:#3C3C3C;"
            "border:1px solid #555;border-radius:3px;"
            "padding:3px 8px;color:#EEE;}"
            "QToolButton:hover{background:#4A4A4A;"
            "border:1px solid #E08C4A;}")
        self.btn_filter.clicked.connect(self._open_filter_dialog)
        search_row.addWidget(self.btn_filter)
        v.addLayout(search_row)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setRootIsDecorated(True)
        self.tree.setStyleSheet("""
            QTreeWidget{background:#2A2A2A;border:1px solid #1A1A1A;
                        color:#DDD;outline:0;padding:4px;}
            QTreeWidget::item{padding:4px 4px;border-radius:3px;}
            QTreeWidget::item:hover{background:#3A3A3A;}
            QTreeWidget::item:selected{background:#E08C4A;color:#1A1A1A;}
        """)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        self.tree.itemClicked.connect(self._library_item_clicked)
        v.addWidget(self.tree, 1)
        self.refresh()

    def refresh(self):
        self.tree.clear()
        self._cat_items = {}
        for category, templates in NODE_TEMPLATES:
            if _is_category_disabled(category):
                continue
            parent = self._ensure_category(category)
            for tpl in templates:
                enabled = tpl.get("enabled", True)
                label = tpl["name"] if enabled else tpl["name"] + "  (locked)"
                child = QTreeWidgetItem([label])
                child.setData(0, Qt.UserRole, tpl)
                child.setForeground(0, QColor("#DDD" if enabled else "#666"))
                body, cut = _truncate_words(tpl.get("description") or "", 100)
                if cut:
                    body = body + "   (F1 for full)"
                lock_msg = (tpl.get("_lifecycle") or {}).get("locked_message")
                if not enabled and lock_msg:
                    body = (body + "  --  Locked: " + lock_msg) if body else ("Locked: " + lock_msg)
                if body:
                    child.setToolTip(0, body)
                parent.addChild(child)
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setExpanded(True)

    def _ensure_category(self, path):
        if isinstance(path, (list, tuple)):
            parts = [str(p) for p in path]
        else:
            parts = [p for p in str(path).split("/") if p]
        parts = [p for p in parts if p]
        if not parts:
            parts = ["Custom"]
        parent = self.tree.invisibleRootItem()
        walked = ""
        for p in parts:
            walked = (walked + "/" + p) if walked else p
            if walked in self._cat_items:
                parent = self._cat_items[walked]
                continue
            item = QTreeWidgetItem([p])
            item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
            f = item.font(0); f.setBold(True); item.setFont(0, f)
            item.setForeground(0, QColor("#B8B8B8"))
            parent.addChild(item)
            self._cat_items[walked] = item
            parent = item
        return parent

    def _library_path(self, item):
        parts = [item.text(0)]
        p = item.parent()
        while p is not None:
            parts.append(p.text(0))
            p = p.parent()
        parts.reverse()
        return parts

    def _full_text(self, item):
        return " ".join(self._library_path(item)).lower()

    def _parse_query(self, text):
        """Parse a filter query.

        Tokens of the form  cat:X  /  category:X  /  path:X
        restrict the search to items whose category path
        contains X.  All other tokens are plain substrings that
        must each appear somewhere in the path.
        """
        cats = []
        words = []
        for tok in text.strip().split():
            low = tok.lower()
            if (low.startswith("cat:")
                    or low.startswith("category:")
                    or low.startswith("path:")):
                val = tok.split(":", 1)[1].strip().lower()
                if val:
                    cats.append(val)
            else:
                words.append(low)
        return cats, words

    def _apply_filter(self, item, cats, words):
        if not cats and not words:
            item.setHidden(False)
            for i in range(item.childCount()):
                self._apply_filter(item.child(i), cats, words)
            return True
        path = self._library_path(item)
        catpath = "/".join(path[:-1]).lower()
        full = " ".join(path).lower()
        self_match = True
        for c in cats:
            if c not in catpath:
                self_match = False
                break
        if self_match:
            for w in words:
                if w not in full:
                    self_match = False
                    break
        any_child = False
        for i in range(item.childCount()):
            if self._apply_filter(item.child(i), cats, words):
                any_child = True
        visible = self_match or any_child
        item.setHidden(not visible)
        if any_child:
            item.setExpanded(True)
        return visible

    def _filter(self, text):
        cats, words = self._parse_query(text)
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            self._apply_filter(root.child(i), cats, words)

    def _library_item_clicked(self, item, col):
        parts = [item.text(0)]
        p = item.parent()
        while p is not None:
            parts.insert(0, p.text(0))
            p = p.parent()
        tpl = item.data(0, Qt.UserRole)
        if isinstance(tpl, dict):
            body = tpl.get("description") or "(no description)"
            parts[-1] = tpl["name"]
        else:
            body = "(category — click a node inside)"
        _FOCUS["kind"] = "library"
        _FOCUS["object"] = None
        _FOCUS["section_index"] = -1
        _FOCUS["library_title"] = " / ".join(parts)
        _FOCUS["library_body"] = body

    def _open_filter_dialog(self):
        dlg = _LibraryFilterDialog(self)
        dlg.exec_()

    def _double_clicked(self, item, col):
        tpl = item.data(0, Qt.UserRole)
        if tpl is not None:
            self.node_requested.emit(tpl)


# --------------------------------------------------------------------------- #
#  Right panel: node sidebar                                                  #
# --------------------------------------------------------------------------- #

def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None); w.deleteLater()
        else:
            sub = item.layout()
            if sub is not None:
                clear_layout(sub)


class ColorSwatch(QPushButton):
    def __init__(self, color, on_click=None, size=(26, 20)):
        super().__init__()
        self._color = QColor(color)
        self.setFixedSize(*size)
        self.setCursor(Qt.PointingHandCursor)
        self._refresh()
        if on_click:
            self.clicked.connect(on_click)

    def color(self): return self._color

    def set_color(self, c):
        self._color = QColor(c); self._refresh()

    def _refresh(self):
        self.setStyleSheet(
            f"QPushButton {{ background:{self._color.name()};"
            f" border:1px solid #666; border-radius:3px; }}"
            f"QPushButton:hover {{ border:1px solid #E08C4A; }}")


def _find_main_window(widget):
    """Return the top-level MainWindow from any nested widget.

    QDialog and other top-level widgets make widget.window() return
    themselves, not the MainWindow, so we walk the parent chain and
    (falling back) scan all top-level widgets for one that has both
    `library` and `scene`.
    """
    try:
        cur = widget
        while cur is not None:
            if (hasattr(cur, "library")
                    and hasattr(cur, "scene")):
                return cur
            cur = cur.parent()
    except Exception:
        pass
    try:
        app = QApplication.instance()
        if app is not None:
            for w in app.topLevelWidgets():
                if (hasattr(w, "library")
                        and hasattr(w, "scene")):
                    return w
    except Exception:
        pass
    return None


class _LibraryFilterDialog(QDialog):
    """Checkbox tree of categories with a search bar and bulk expand."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Filter node library")
        self.setStyleSheet("QDialog { background:#252525; color:#DDD; }")
        self.resize(440, 640)
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)

        head = QLabel("Show these categories")
        head.setStyleSheet(
            "font-weight:700; color:#F0F0F0; font-size:13px;")
        v.addWidget(head)

        sub = QLabel("Uncheck a category to hide every node inside it.")
        sub.setStyleSheet("color:#8A8A8A; font-size:11px;")
        v.addWidget(sub)

        # --- search row --- #
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search categories\u2026")
        self.search.setClearButtonEnabled(True)
        self.search.setStyleSheet(
            "QLineEdit{background:#1E1E1E;color:#DDD;"
            "border:1px solid #333;border-radius:3px;"
            "padding:5px 8px;}"
            "QLineEdit:focus{border:1px solid #E08C4A;}")
        self.search.textChanged.connect(self._on_search)
        v.addWidget(self.search)

        # --- expand / collapse --- #
        bulk_row = QHBoxLayout()
        bulk_row.setSpacing(6)
        btn_expand   = QPushButton("Expand All")
        btn_collapse = QPushButton("Collapse All")
        for b in (btn_expand, btn_collapse):
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton{background:#333;color:#DDD;"
                "border:1px solid #4A4A4A;border-radius:3px;"
                "padding:4px 10px;}"
                "QPushButton:hover{background:#3F3F3F;"
                "border:1px solid #E08C4A;}")
        bulk_row.addWidget(btn_expand)
        bulk_row.addWidget(btn_collapse)
        bulk_row.addStretch(1)
        v.addLayout(bulk_row)

        # --- tree --- #
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(16)
        self.tree.setStyleSheet(
            "QTreeWidget{background:#1E1E1E; color:#DDD;"
            " border:1px solid #333; outline:0; padding:4px;}"
            "QTreeWidget::item{padding:3px 2px;}"
            "QTreeWidget::item:hover{background:#2E2E2E;}"
            "QTreeWidget::indicator{width:14px;height:14px;}")
        self.tree.itemChanged.connect(self._on_item_changed)
        v.addWidget(self.tree, 1)

        # --- bottom row --- #
        row = QHBoxLayout()
        btn_all  = QPushButton("Select All")
        btn_none = QPushButton("Select None")
        row.addWidget(btn_all)
        row.addWidget(btn_none)
        row.addStretch(1)
        btn_cancel = QPushButton("Cancel")
        btn_apply  = QPushButton("Apply")
        row.addWidget(btn_cancel)
        row.addWidget(btn_apply)
        v.addLayout(row)

        btn_expand.clicked.connect(self.tree.expandAll)
        btn_collapse.clicked.connect(self.tree.collapseAll)
        btn_all.clicked.connect(lambda: self._set_all(True))
        btn_none.clicked.connect(lambda: self._set_all(False))
        btn_cancel.clicked.connect(self.reject)
        btn_apply.clicked.connect(self._apply)

        self._items = {}
        self._build_tree()

    # ---------------------------------------------------------------- #
    #  Tree construction                                               #
    # ---------------------------------------------------------------- #

    def _build_tree(self):
        self.tree.blockSignals(True)
        self.tree.clear()
        self._items = {}
        for path in _iter_category_paths():
            parts = path.split("/")
            parent = self.tree.invisibleRootItem()
            walked = ""
            for p in parts:
                walked = (walked + "/" + p) if walked else p
                if walked in self._items:
                    parent = self._items[walked]
                    continue
                it = QTreeWidgetItem([p])
                it.setFlags(Qt.ItemIsUserCheckable
                            | Qt.ItemIsEnabled
                            | Qt.ItemIsSelectable)
                checked = walked not in _LIBRARY_FILTER["disabled"]
                it.setCheckState(
                    0, Qt.Checked if checked else Qt.Unchecked)
                it.setData(0, Qt.UserRole, walked)
                parent.addChild(it)
                self._items[walked] = it
                parent = it
        for it in list(self._items.values()):
            if it.childCount():
                self._refresh_tristate(it)
        self.tree.expandAll()
        self.tree.blockSignals(False)

    def _refresh_tristate(self, item):
        children = [item.child(i) for i in range(item.childCount())]
        if not children:
            return
        states = [c.checkState(0) for c in children]
        if all(s == Qt.Checked for s in states):
            item.setCheckState(0, Qt.Checked)
        elif all(s == Qt.Unchecked for s in states):
            item.setCheckState(0, Qt.Unchecked)
        else:
            item.setCheckState(0, Qt.PartiallyChecked)

    # ---------------------------------------------------------------- #
    #  Checkbox handling                                               #
    # ---------------------------------------------------------------- #

    def _on_item_changed(self, item, col):
        if col != 0:
            return
        self.tree.blockSignals(True)
        state = item.checkState(0)
        if state != Qt.PartiallyChecked:
            self._set_subtree(item, state)
        parent = item.parent()
        while parent is not None:
            self._refresh_tristate(parent)
            parent = parent.parent()
        self.tree.blockSignals(False)

    def _set_subtree(self, item, state):
        item.setCheckState(0, state)
        for i in range(item.childCount()):
            self._set_subtree(item.child(i), state)

    def _set_all(self, on):
        self.tree.blockSignals(True)
        for it in list(self._items.values()):
            it.setCheckState(0, Qt.Checked if on else Qt.Unchecked)
        self.tree.blockSignals(False)

    # ---------------------------------------------------------------- #
    #  Search                                                          #
    # ---------------------------------------------------------------- #

    def _on_search(self, text):
        tokens = [t for t in text.strip().lower().split() if t]
        for path, it in self._items.items():
            it.setHidden(False)
        if not tokens:
            self.tree.expandAll()
            return

        # mark each node: True if its own path or any descendant matches
        def matches(path):
            low = path.lower()
            return all(t in low for t in tokens)

        def mark(item):
            """Return True if item or any descendant matches."""
            path = item.data(0, Qt.UserRole) or ""
            hit = matches(path)
            any_child = False
            for i in range(item.childCount()):
                c = item.child(i)
                if mark(c):
                    any_child = True
            if not hit and not any_child:
                item.setHidden(True)
                return False
            item.setHidden(False)
            if any_child:
                item.setExpanded(True)
            return True

        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            mark(root.child(i))

    # ---------------------------------------------------------------- #
    #  Apply                                                           #
    # ---------------------------------------------------------------- #

    def _apply(self):
        disabled = set()
        for path, it in self._items.items():
            if it.checkState(0) == Qt.Unchecked:
                disabled.add(path)
        _LIBRARY_FILTER["disabled"] = disabled
        try:
            _lib_filter_save()
        except Exception as _e:
            print("[filter] save skipped:", _e)
        win = _find_main_window(self)
        if win is None:
            print("[filter] could not locate MainWindow")
            self.accept()
            return
        try:
            win.library.refresh()
        except Exception as _e:
            print("[filter] refresh failed:", _e)
        try:
            win._persist_settings()
        except Exception as _e:
            print("[filter] persist skipped:", _e)
        self.accept()


class NodeSidebar(QScrollArea):
    def __init__(self, scene, parent=None):
        super().__init__(parent)
        self.scene = scene
        self.node = None
        self.setWidgetResizable(True)
        self.setFixedWidth(272)
        self.setFrameShape(QFrame.NoFrame)
        self.setObjectName("Sidebar")
        self.container = QWidget()
        self.container.setObjectName("Sidebar")
        self.vbox = QVBoxLayout(self.container)
        self.vbox.setContentsMargins(12, 12, 12, 12)
        self.vbox.setSpacing(9)
        self.setWidget(self.container)
        scene.selectionChanged.connect(self.on_selection_changed)
        self.rebuild()

    def on_selection_changed(self):
        if self.scene is None or sip.isdeleted(self.scene):
            return
        try:
            nodes = [i for i in self.scene.selectedItems() if isinstance(i, Node)]
        except RuntimeError:
            return
        new = nodes[0] if len(nodes) == 1 else None
        if new is not self.node:
            self.node = new
            self.rebuild()

    def rebuild(self):
        clear_layout(self.vbox)
        if self.node is None:
            lbl = QLabel("No node selected.\n\n"
                         "• Shift+A — open the Add popup\n"
                         "• Hover header / section / socket for info\n"
                         "• Double-click a library node to add\n"
                         "• Right-click a node — Duplicate / Delete\n"
                         "• Middle-drag to pan, wheel to zoom\n"
                         "• Ctrl+D duplicate, Del removes selection\n"
                         "• F1 — full help in the Help pane")
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color:#8A8A8A; line-height:150%;")
            self.vbox.addWidget(lbl)
            self.vbox.addStretch(1)
            return

        node = self.node
        head = QLabel("NODE")
        head.setStyleSheet("color:#7F7F7F;font-weight:bold;letter-spacing:1.5px;")
        self.vbox.addWidget(head)

        self.vbox.addWidget(self._sub_label("Name"))
        name_edit = QLineEdit(node.title)
        name_edit.textChanged.connect(lambda t: (node.set_title(t), node.update()))
        self.vbox.addWidget(name_edit)

        self.vbox.addWidget(self._sub_label("Description"))
        desc_edit = QPlainTextEdit(node.description)
        desc_edit.setPlaceholderText("Optional description…")
        desc_edit.setFixedHeight(72)
        desc_edit.setStyleSheet(
            "QPlainTextEdit{background:#3C3C3C;border:1px solid #555;"
            "border-radius:3px;color:#EEEEEE;padding:2px 4px;"
            "font-size:11px;}")
        desc_edit.textChanged.connect(
            lambda pe=desc_edit: node.set_description(pe.toPlainText()))
        self.vbox.addWidget(desc_edit)

        self.vbox.addWidget(self._sub_label("Node Color"))
        row = QHBoxLayout(); row.setSpacing(6)
        swatch = ColorSwatch(node.color,
                             on_click=lambda: self._pick_node_color(swatch),
                             size=(40, 22))
        row.addWidget(swatch)
        for preset in ("#3B3B3B", "#4A6B8A", "#6B4A8A", "#8A4A4A",
                       "#4A8A5C", "#8A7A4A", "#4A7A8A"):
            b = QToolButton(); b.setFixedSize(20, 20)
            b.setStyleSheet(f"QToolButton {{ background:{preset};"
                            f" border:1px solid #666; border-radius:3px; }}")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _, c=preset: self._set_node_color(c))
            row.addWidget(b)
        row.addStretch(1)
        self.vbox.addLayout(row)

        for sec_idx, sec in enumerate(node.sections):
            if not sec["name"] and not sec["inputs"] and not sec["outputs"]:
                continue
            self.vbox.addWidget(self._separator())
            title_row = QHBoxLayout(); title_row.setSpacing(6)
            if sec["name"]:
                chk = QCheckBox(sec["name"])
                chk.setChecked(not sec["collapsed"])
                chk.setStyleSheet("QCheckBox{font-weight:bold; color:#D8D8D8;}")
                chk.toggled.connect(
                    lambda checked, s=sec: self._toggle_section(s, checked))
                if sec.get("description"):
                    chk.setToolTip(sec["description"])
                title_row.addWidget(chk)
            else:
                title_row.addWidget(self._sub_label("General"))
            title_row.addStretch(1)
            if getattr(node, "dynamic", False):
                def _mk(text, tip, cb):
                    b = QToolButton()
                    b.setText(text)
                    b.setToolTip(tip)
                    b.setFixedSize(22, 20)
                    b.setStyleSheet(
                        "QToolButton{background:#3C3C3C;"
                        "border:1px solid #555;border-radius:3px;color:#EEE;}"
                        "QToolButton:hover{background:#4A4A4A;"
                        "border:1px solid #E08C4A;}")
                    b.clicked.connect(cb)
                    return b
                title_row.addWidget(_mk("+", "Add an input",
                                        lambda _, i=sec_idx: self._add_socket(i, True)))
                title_row.addWidget(_mk("++", "Add an output",
                                        lambda _, i=sec_idx: self._add_socket(i, False)))
                title_row.addWidget(_mk("\u2212", "Remove last socket",
                                        lambda _, i=sec_idx: self._remove_last_socket(i)))
            self.vbox.addLayout(title_row)
            for sock in sec["inputs"] + sec["outputs"]:
                self.vbox.addLayout(self._socket_row(sock))

        self.vbox.addStretch(1)

    def _sub_label(self, text):
        lbl = QLabel(text.upper())
        lbl.setStyleSheet("color:#7F7F7F;font-size:10px;font-weight:bold;"
                          "letter-spacing:1px;")
        return lbl

    def _separator(self):
        line = QFrame(); line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background:#3E3E3E;max-height:1px;border:none;")
        return line

    def _socket_row(self, sock):
        row = QHBoxLayout(); row.setSpacing(6)
        sw = ColorSwatch(type_color(sock.socket_type),
                         on_click=lambda s=sock: self._pick_socket_color(s, sw_ref[0]),
                         size=(20, 18))
        sw_ref = [sw]
        row.addWidget(sw)
        arrow = "→" if not sock.is_input else "←"
        name = QLabel(f"{arrow} {sock.name}")
        name.setStyleSheet("color:#CFCFCF;")
        if sock.description:
            name.setToolTip(sock.description)
        row.addWidget(name, 1)
        combo = QComboBox()
        combo.addItems(SOCKET_TYPES)
        combo.setCurrentText(sock.socket_type)
        combo.setFixedWidth(84)
        combo.currentTextChanged.connect(
            lambda t, s=sock, w=sw: self._set_socket_type(s, t, w))
        row.addWidget(combo)
        val_edit = self._socket_value_row(sock)
        if val_edit is not None:
            wrapper = QVBoxLayout()
            wrapper.setContentsMargins(0, 0, 0, 0)
            wrapper.addWidget(val_edit)
            outer = QHBoxLayout()
            outer.setContentsMargins(0, 0, 0, 0)
            outer.addLayout(wrapper)
            row_parent = QVBoxLayout()
            row_parent.setContentsMargins(0, 0, 0, 0)
            row_parent.addLayout(row)
            row_parent.addLayout(outer)
            return row_parent
        return row

    def _add_socket(self, section_index, is_input):
        node = self.node
        if node is None:
            return
        sock = node.add_socket(section_index, is_input)
        if sock is not None:
            self.rebuild()

    def _remove_last_socket(self, section_index):
        node = self.node
        if node is None:
            return
        if section_index < 0 or section_index >= len(node.sections):
            return
        sec = node.sections[section_index]
        target = None
        if sec["inputs"]:
            target = sec["inputs"][-1]
        elif sec["outputs"]:
            target = sec["outputs"][-1]
        if target is not None:
            node.remove_socket(target)
            self.rebuild()

    def _socket_value_row(self, sock):
        if not sock.is_input:
            return None
        opts = getattr(sock, "options", None)
        if opts:
            combo = QComboBox()
            combo.addItems([str(o) for o in opts])
            cur = str(sock.value) if sock.value is not None else ""
            names = [str(o) for o in opts]
            if cur in names:
                combo.setCurrentText(cur)
            elif combo.count():
                sock.value = combo.currentText()
            combo.currentTextChanged.connect(
                lambda t, s=sock: (setattr(s, "value", t),
                                   s.node.update()))
            return combo
        edit = QLineEdit()
        edit.setPlaceholderText("literal value…")
        edit.setText("" if sock.value is None else str(sock.value))
        def _apply(text):
            sock.value = text if text != "" else None
            sock.node.update()
        edit.textEdited.connect(_apply)
        return edit

    def _pick_node_color(self, swatch):
        c = QColorDialog.getColor(self.node.color, self, "Node Color")
        if c.isValid():
            self.node.set_node_color(c); swatch.set_color(c)

    def _set_node_color(self, name):
        self.node.set_node_color(QColor(name)); self.rebuild()

    def _toggle_section(self, sec, expanded):
        sec["collapsed"] = not expanded
        self.node.layout()

    def _pick_socket_color(self, sock, swatch):
        c = QColorDialog.getColor(type_color(sock.socket_type), self, "Socket Color")
        if c.isValid():
            TYPE_COLORS[sock.socket_type] = c
            swatch.set_color(c)
            sock.update(); sock.update_edges()

    def _set_socket_type(self, sock, t, swatch):
        sock.socket_type = t
        swatch.set_color(type_color(t))
        sock.update(); sock.update_edges()
        sc = sock.scene()
        for e in list(sock.connections):
            other = e.end_socket if e.start_socket is sock else e.start_socket
            if other is None:
                continue
            if (other.socket_type != sock.socket_type
                    and "any" not in (other.socket_type, sock.socket_type)):
                sc.remove_edge(e)
        sock.node.update()


# --------------------------------------------------------------------------- #
#  Stylesheet                                                                 #
# --------------------------------------------------------------------------- #

APP_STYLE = """
QWidget { background:#2B2B2B; color:#DDDDDD;
          font-family:'Segoe UI','DejaVu Sans',sans-serif; font-size:12px; }
QWidget#Sidebar { background:#313131; }
QWidget#LeftPanel { background:#2A2A2A; }
QLineEdit, QComboBox {
    background:#3C3C3C; border:1px solid #555; border-radius:3px;
    padding:3px 6px; color:#EEEEEE;
}
QLineEdit:focus, QComboBox:focus { border:1px solid #E08C4A; }
QComboBox QAbstractItemView {
    background:#3C3C3C; color:#EEE; selection-background-color:#E08C4A;
}
QPushButton { background:#3C3C3C; border:1px solid #555;
              border-radius:3px; padding:4px 8px; }
QPushButton:hover { background:#4A4A4A; }
QCheckBox { spacing:6px; }
QScrollArea { border:none; }
QScrollBar:vertical { background:transparent; width:9px; margin:0; }
QScrollBar::handle:vertical { background:#4A4A4A; border-radius:4px;
                              min-height:24px; }
QScrollBar::add-line, QScrollBar::sub-line { height:0; }
QSplitter::handle { background:#1A1A1A; width:1px; }
QMenuBar { background:#232323; color:#DDDDDD; }
QMenuBar::item { padding:5px 10px; background:transparent; }
QMenuBar::item:selected { background:#3A3A3A; }
QMenuBar::item:pressed { background:#E08C4A; color:#1A1A1A; }
QToolBar { background:#262626; border:0; border-bottom:1px solid #1A1A1A;
           spacing:2px; padding:3px 4px; }
QToolButton { background:transparent; border:1px solid transparent;
              padding:4px 8px; border-radius:3px; color:#DDD; }
QToolButton:hover { background:#3A3A3A; border:1px solid #4A4A4A; }
QToolButton:pressed { background:#E08C4A; color:#1A1A1A; }
QStatusBar { background:#232323; color:#B0B0B0;
             border-top:1px solid #1A1A1A; }
QStatusBar QLabel { color:#B0B0B0; padding:0 8px; }
"""


# --------------------------------------------------------------------------- #
#  Reporter overlay                                                           #
# --------------------------------------------------------------------------- #

class ReportItem:
    LEVEL_COLORS = {
        "info":    QColor("#4A90D9"),
        "success": QColor("#5CB85C"),
        "warning": QColor("#E0A030"),
        "error":   QColor("#D9534F"),
        "debug":   QColor("#8A8A8A"),
    }
    LEVEL_ICONS = {
        "info":    "i",
        "success": "✓",
        "warning": "!",
        "error":   "✕",
        "debug":   "•",
    }


class Reporter(QWidget):
    MARGIN    = 16
    SPACING   = 6
    ITEM_H    = 30
    MAX_ITEMS = 5
    MIN_W     = 200
    MAX_W     = 400

    def __init__(self, window, view):
        super().__init__(window)
        self._window = window
        self._view   = view
        self._messages = []
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent;")
        self.setFixedHeight(0)
        self.setFixedWidth(0)
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        view.installEventFilter(self)
        self._reposition()

    def eventFilter(self, obj, event):
        if obj is self._view and event.type() in (QEvent.Resize, QEvent.Move):
            QTimer.singleShot(0, self._safe_reposition)
        return False

    def _safe_reposition(self):
        if sip.isdeleted(self) or self._window is None:
            return
        try:
            self._reposition()
        except RuntimeError:
            pass

    def post(self, text, level="info", duration=3500):
        import time
        while len(self._messages) >= self.MAX_ITEMS:
            self._messages.pop(0)
        self._messages.append({
            "text":  text,
            "level": level,
            "born":  time.monotonic(),
            "until": time.monotonic() + (duration / 1000.0 if duration else 0),
        })
        self._reposition()
        self.raise_()
        self.update()

    def clear(self):
        self._messages.clear()
        self._reposition()
        self.update()

    def _tick(self):
        import time
        if not self._messages:
            return
        now = time.monotonic()
        keep = [m for m in self._messages
                if m["until"] == 0 or m["until"] > now]
        if len(keep) != len(self._messages):
            self._messages = keep
            self._reposition()
        self.update()

    def _reposition(self):
        vp = self._view.viewport()
        tl = vp.mapTo(self._window, QPoint(0, 0))
        n  = len(self._messages)
        if n == 0:
            self.setGeometry(tl.x(), tl.y(), 0, 0)
            return
        w = self.MAX_W
        h = n * self.ITEM_H + max(0, n - 1) * self.SPACING + 2 * self.MARGIN
        self.setGeometry(tl.x() + vp.width()  - w,
                         tl.y() + vp.height() - h,
                         w, h)

    def paintEvent(self, event):
        import time
        if not self._messages:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        now = time.monotonic()
        y = self.MARGIN
        for m in self._messages:
            age = now - m["born"]
            opacity = min(1.0, age / 0.18)
            self._draw_card(p, m, y, opacity)
            y += self.ITEM_H + self.SPACING

    def _draw_card(self, p, msg, y, opacity):
        col  = ReportItem.LEVEL_COLORS.get(msg["level"], QColor("#4A90D9"))
        icon = ReportItem.LEVEL_ICONS.get(msg["level"], "i")
        p.save()
        p.setOpacity(opacity)
        body = QRectF(self.MARGIN, y,
                      self.width() - 2 * self.MARGIN,
                      self.ITEM_H - 2)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 110))
        p.drawRoundedRect(body.translated(0, 2), 6, 6)
        p.setBrush(QColor(28, 28, 28, 240))
        p.setPen(QPen(QColor(0, 0, 0, 200), 1.0))
        p.drawRoundedRect(body, 6, 6)
        bar = QRectF(body.left() + 3, body.top() + 3, 3, body.height() - 6)
        p.setBrush(col); p.setPen(Qt.NoPen)
        p.drawRoundedRect(bar, 1.5, 1.5)
        cx = body.left() + 20; cy = body.center().y()
        p.setBrush(col)
        p.drawEllipse(QPointF(cx, cy), 8, 8)
        fi = QFont("Segoe UI", 8); fi.setBold(True)
        p.setFont(fi)
        p.setPen(QColor(20, 20, 20))
        p.drawText(QRectF(cx - 8, cy - 8, 16, 16), Qt.AlignCenter, icon)
        p.setFont(QFont("Segoe UI", 9))
        p.setPen(QColor(235, 235, 235))
        p.drawText(QRectF(body.left() + 36, body.top(),
                          body.width() - 44, body.height()),
                   Qt.AlignVCenter | Qt.AlignLeft, msg["text"])
        p.restore()


# --------------------------------------------------------------------------- #
#  Run thread                                                                 #
# --------------------------------------------------------------------------- #

class _RunThread(QThread):
    line_read    = pyqtSignal(str)
    finished_run = pyqtSignal(int, str)

    def __init__(self, script_path, parent=None):
        super().__init__(parent)
        self.script_path = script_path
        self._proc = None
        self._killed = False

    def run(self):
        import subprocess
        try:
            self._proc = subprocess.Popen(
                [sys.executable, "-u", self.script_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True, bufsize=1,
                start_new_session=True,
            )
            for line in iter(self._proc.stdout.readline, ""):
                if self._killed:
                    break
                self.line_read.emit(line.rstrip("\n"))
            code = self._proc.wait()
            self.finished_run.emit(code, "")
        except Exception as e:
            self.finished_run.emit(-1, str(e))

    def kill(self):
        self._killed = True
        self._paused = False
        if self._proc and self._proc.poll() is None:
            try:
                if sys.platform != "win32":
                    import signal as _sig
                    try:
                        os.killpg(os.getpgid(self._proc.pid), _sig.SIGCONT)
                    except Exception:
                        pass
                    try:
                        os.killpg(os.getpgid(self._proc.pid), _sig.SIGKILL)
                        return
                    except Exception:
                        pass
                self._proc.kill()
            except Exception:
                pass

    def pause(self):
        if getattr(self, "_paused", False):
            return
        self._paused = True
        if (self._proc and self._proc.poll() is None
                and sys.platform != "win32"):
            try:
                import signal as _sig
                os.killpg(os.getpgid(self._proc.pid), _sig.SIGSTOP)
            except Exception as ex:
                print("[run] pause failed:", ex)

    def resume(self):
        if not getattr(self, "_paused", False):
            return
        self._paused = False
        if (self._proc and self._proc.poll() is None
                and sys.platform != "win32"):
            try:
                import signal as _sig
                os.killpg(os.getpgid(self._proc.pid), _sig.SIGCONT)
            except Exception as ex:
                print("[run] resume failed:", ex)


# --------------------------------------------------------------------------- #
#  Main window                                                                #
# --------------------------------------------------------------------------- #


# ---------------------------------------------------------------- #
#  Undo / redo                                                      #
# ---------------------------------------------------------------- #

class _UndoManager(QObject):
    """Snapshot-based undo stack for a NodeScene.

    A snapshot is scene.to_dict() — the same JSON that File > Save
    produces.  Every structural change pushes one.  Undo loads the
    previous snapshot; redo loads the next.  Node item identity is
    not preserved across an undo (the scene is rebuilt), but node
    ids, positions, metadata, and edges all survive.
    """

    LIMIT = 200

    def __init__(self, scene, parent=None):
        super().__init__(parent)
        self.scene = scene
        self._undo = []
        self._redo = []
        self._last = None
        self._restoring = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._capture)
        scene.graph_changed.connect(self._on_changed)
        self._capture_now()

    # ---- internal ---- #

    def _on_changed(self):
        if self._restoring:
            return
        self._timer.start()

    def _capture_now(self):
        try:
            self._last = self.scene.to_dict()
        except Exception:
            self._last = None

    def _capture(self):
        try:
            snap = self.scene.to_dict()
        except Exception:
            return
        if snap == self._last:
            return
        if self._last is not None:
            self._undo.append(self._last)
            if len(self._undo) > self.LIMIT:
                self._undo.pop(0)
        self._last = snap
        self._redo.clear()

    def _flush(self):
        """Commit any pending change before an undo/redo."""
        if self._timer.isActive():
            self._timer.stop()
            self._capture()

    # ---- public ---- #

    def clear(self):
        self._undo.clear()
        self._redo.clear()
        self._capture_now()

    def undo(self):
        self._flush()
        if not self._undo:
            return 0
        prev = self._undo.pop()
        if self._last is not None:
            self._redo.append(self._last)
        self._restoring = True
        try:
            self.scene.load_from_dict(prev)
        except Exception as ex:
            print("[undo] restore failed:", ex)
            return 0
        finally:
            self._restoring = False
        self._last = prev
        return len(self._undo)

    def redo(self):
        self._flush()
        if not self._redo:
            return 0
        nxt = self._redo.pop()
        if self._last is not None:
            self._undo.append(self._last)
        self._restoring = True
        try:
            self.scene.load_from_dict(nxt)
        except Exception as ex:
            print("[redo] restore failed:", ex)
            return 0
        finally:
            self._restoring = False
        self._last = nxt
        return len(self._redo)

    def can_undo(self):
        return bool(self._undo) or self._timer.isActive()

    def can_redo(self):
        return bool(self._redo)

    def undo_depth(self):
        return len(self._undo) + (1 if self._timer.isActive() else 0)

    def redo_depth(self):
        return len(self._redo)


class MainWindow(QMainWindow):
    _MATH_CACHE = {}
    _RST_CACHE  = {}

    _HELP_KIND_COLORS = {
        "node":    "#4A90D9",
        "socket":  "#5CB85C",
        "section": "#E0A030",
        "library": "#6B4A8A",
        "help":    "#8A8A8A",
    }

    _PY_PATTERN = re.compile(
        r'(?P<comment>#[^\n]*)'
        r'|(?P<string>"""(?:\\.|[^\\])*?"""|\'\'\'(?:\\.|[^\\])*?\'\'\''
        r'|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\')'
        r'|(?P<keyword>\b(?:def|class|return|if|elif|else|for|while|try|'
        r'except|finally|with|as|import|from|pass|break|continue|raise|'
        r'yield|lambda|in|is|not|and|or|None|True|False|self|async|await|'
        r'global|nonlocal|del|assert|match|case)\b)'
        r'|(?P<builtin>\b(?:print|len|range|list|dict|set|tuple|str|int|'
        r'float|bool|bytes|open|enumerate|zip|map|filter|sum|min|max|abs|'
        r'sorted|reversed|type|isinstance|super|iter|next|getattr|setattr|'
        r'hasattr|repr|id|hash|format|input)\b)'
        r'|(?P<num>\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)'
        r'|(?P<func>\b[A-Za-z_]\w*(?=\())'
    )
    _PY_COLORS = {
        "comment": "#6A9955", "string": "#CE9178", "keyword": "#569CD6",
        "builtin": "#4EC9B0", "num": "#B5CEA8", "func": "#DCDCAA",
    }

    _CXX_PATTERN = re.compile(
        r'(?P<comment>//[^\n]*|/\*.*?\*/)'
        r'|(?P<preproc>^\s*#[^\n]*)'
        r'|(?P<string>"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\')'
        r'|(?P<keyword>\b(?:int|void|class|struct|if|else|for|while|return|'
        r'break|continue|new|delete|this|nullptr|true|false|namespace|'
        r'using|public|private|protected|virtual|override|const|static|'
        r'inline|template|typename|auto|sizeof|typedef|enum|switch|case|'
        r'default|goto|do|try|catch|throw|extern|operator|friend|mutable|'
        r'register|volatile|wchar_t|char|short|long|signed|unsigned|'
        r'float|double|bool)\b)'
        r'|(?P<type>\b(?:size_t|ssize_t|uint8_t|uint16_t|uint32_t|uint64_t|'
        r'int8_t|int16_t|int32_t|int64_t|std|string|vector|map|set|'
        r'unordered_map|shared_ptr|unique_ptr|weak_ptr|optional|variant|'
        r'tuple|pair|span|string_view)\b)'
        r'|(?P<num>\b(?:0x[0-9A-Fa-f]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)[fFuUlL]*\b)'
        r'|(?P<func>\b[A-Za-z_]\w*(?=\())',
        re.MULTILINE,
    )
    _CXX_COLORS = {
        "comment": "#6A9955", "preproc": "#C586C0", "string": "#CE9178",
        "keyword": "#569CD6", "type": "#4EC9B0", "num": "#B5CEA8",
        "func": "#DCDCAA",
    }

    _SECTION_RE = re.compile(
        r"^\s*(Args|Arguments|Parameters|Shape|Returns?|Yields?|"
        r"Raises?|Examples?|Notes?|Warnings?|Attributes|"
        r"Keyword\s+Args|Keyword\s+Arguments|Other\s+Parameters|"
        r"See\s+Also)\s*:?\s*$", re.I)

    _LANG_RE = re.compile(
        r"^\s*(python|py|cpp|c\+\+|cxx|cc|bash|sh|shell|maths?|latex)\s*$",
        re.I)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI Node Editor  —  Blender Style")
        self.resize(1500, 900)
        self._current_path = None
        self._dirty = False
        _apply_settings(_load_settings())

        self.scene = NodeScene()
        self.scene.graph_changed.connect(self._refresh_status)
        self.scene.full_output_requested.connect(
            self._show_full_output)
        self.view = NodeView(self.scene)
        self.sidebar = NodeSidebar(self.scene)
        self.library = NodeLibraryPanel()
        self.library.node_requested.connect(self._add_from_library)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.library)
        splitter.addWidget(self.view)
        splitter.addWidget(self.sidebar)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([230, 1000, 272])
        self.setCentralWidget(splitter)

        self._build_menu_bar()
        self._build_toolbar()
        self._build_status_bar()

        self.reporter = Reporter(self, self.view)
        self.reporter.raise_()

        self._setup_autosave()
        self._setup_after_change()
        self._setup_reroute()
        self._setup_blocks()
        self._setup_help_dock()

        # ---- undo / redo ---- #
        self._undo_mgr = _UndoManager(self.scene)
        QShortcut(QKeySequence("Ctrl+Z"), self,
                  activated=self._on_undo)
        QShortcut(QKeySequence("Ctrl+Y"), self,
                  activated=self._on_redo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self,
                  activated=self._on_redo)

        try:
            data = _load_settings()
            g = data.get("window_geometry")
            if g:
                from PyQt5.QtCore import QByteArray
                self.restoreGeometry(QByteArray.fromBase64(g.encode("ascii")))
            lf = data.get("last_file") or ""
            if lf and os.path.isfile(lf):
                self._last_file_hint = lf
            else:
                self._last_file_hint = None
        except Exception as ex:
            print("[settings] geometry restore failed:", ex)
            self._last_file_hint = None

        self._refresh_status()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "reporter") and self.reporter is not None:
            self.reporter._safe_reposition()

    def moveEvent(self, event):
        super().moveEvent(event)
        if hasattr(self, "reporter") and self.reporter is not None:
            self.reporter._safe_reposition()

    # ------------------------------------------------------------ menu bar #
    def _build_menu_bar(self):
        mb = self.menuBar()
        mb.setStyleSheet(MENU_STYLE)

        m = mb.addMenu("File")
        self._act(m, "New",          "Ctrl+N",       self.new_graph)
        self._act(m, "Open…",        "Ctrl+O",       self.open_graph)
        self._act(m, "Save",         "Ctrl+S",       self.save_graph)
        self._act(m, "Save As…",     "Ctrl+Shift+S", self.save_graph_as)
        m.addSeparator()
        self._act(m, "Quit",         "Ctrl+Q",       self.close)

        m = mb.addMenu("Edit")
        self._act_undo = self._act(m, "Undo", "Ctrl+Z", self._on_undo)
        self._act_redo = self._act(m, "Redo", "Ctrl+Y", self._on_redo)
        m.addSeparator()
        self._act(m, "Duplicate",    "Ctrl+D",   self.view.duplicate_selected)
        self._act(m, "Delete",       "Del",      self.view.delete_selected)
        m.addSeparator()
        self._act(m, "Select All",   "Ctrl+A",   self.view.select_all)
        self._act(m, "Deselect All", "Alt+A",    lambda: self.scene.clearSelection())
        m.aboutToShow.connect(self._sync_edit_menu)

        m = mb.addMenu("View")
        self._act(m, "Home",        "Home",    self._go_home)
        self._act(m, "Zoom In",     "Ctrl+=",  lambda: self.view.scale(1.15, 1.15))
        self._act(m, "Zoom Out",    "Ctrl+-",  lambda: self.view.scale(1/1.15, 1/1.15))
        m.addSeparator()
        self._act(m, "Reset Zoom",  "Ctrl+0",  lambda: self.view.resetTransform())

        m = mb.addMenu("Add")
        self._act(m, "Add Node…", "Shift+A", self.view.show_add_menu_at_cursor)
        m.addSeparator()
        for category, templates in NODE_TEMPLATES:
            sub = m.addMenu(category)
            sub.setStyleSheet(MENU_STYLE)
            for tpl in templates:
                act = sub.addAction(tpl["name"])
                if tpl.get("description"):
                    act.setToolTip(tpl["description"])
                act.triggered.connect(
                    lambda checked=False, t=tpl: self._add_template_at_cursor(t))

        m = mb.addMenu("Tools")
        self._act(m, "Library Manager\u2026", None,
                  self._open_library_manager)
        m.addSeparator()
        self._act(m, "Convert Python \u2192 PyTorchUI\u2026", None,
                  self._open_convert_dialog_menu)
        self._act(m, "Convert PyTorchUI \u2192 Python\u2026", None,
                  self._open_pyui_to_py_dialog)
        m.addSeparator()
        self._act(m, "Show output paths", None,
                  self._show_output_paths)
        self._act(m, "Clear output paths", None,
                  self._clear_output_paths)

        m = mb.addMenu("Examples")
        self._act(m, "Browse Examples…", "Ctrl+E", self._pick_example)
        m.addSeparator()
        self._rebuild_examples_menu()

        m = mb.addMenu("Code")
        self._act(m, "Generate Python",  "Ctrl+G", self._generate_code_to_file)
        self._act(m, "Run generated.py", "F5",     self._run_generated_code)
        self._act(m, "Pause Run",  "Ctrl+F5",       self._pause_run)
        self._act(m, "Resume Run", "Ctrl+Shift+F5", self._resume_run)
        self._act(m, "Stop Run",   "Shift+F5",      self._stop_run)
        m.addSeparator()
        self._act(m, "Show All Node Outputs", "Ctrl+Shift+O",
                  self._show_all_outputs)

        m = mb.addMenu("Settings")
        self._build_settings_menu(m)

        m = mb.addMenu("Help")
        self._act(m, "Show Help (F1)", "F1", self._show_focus_help)
        self._act(m, "About…", None, self.show_about)

    def _act(self, menu, text, shortcut, slot):
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.triggered.connect(slot)
        menu.addAction(a)
        return a

    def report(self, text, level="info", duration=3500):
        if hasattr(self, "reporter") and self.reporter is not None:
            self.reporter.post(text, level, duration)

    # ------------------------------------------------------------ toolbar  #
    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(QSize(16, 16))
        self.addToolBar(Qt.TopToolBarArea, tb)

        def btn(text, tip, slot):
            a = QAction(text, self)
            a.setToolTip(tip)
            a.triggered.connect(slot)
            tb.addAction(a)
            return a

        btn("New",       "New graph",           self.new_graph)
        btn("Open",      "Open graph",          self.open_graph)
        btn("Save",      "Save graph",          self.save_graph)
        tb.addSeparator()
        btn("+ Add",     "Add node (Shift+A)",  self.view.show_add_menu_at_cursor)
        btn("Duplicate", "Duplicate (Ctrl+D)",  self.view.duplicate_selected)
        btn("Delete",    "Delete (Del)",        self.view.delete_selected)
        tb.addSeparator()
        btn("Home",      "Home (reset zoom + frame all)", self._go_home)
        tb.addSeparator()
        btn("Generate",  "Write generated.py (Ctrl+G)", self._generate_code_to_file)
        btn("Run",       "Run generated.py (F5)",       self._run_generated_code)
        self._act_pause = btn("Pause", "Pause after current node",
                              self._toggle_pause)
        self._act_stop  = btn("Stop", "Stop execution",
                              self._stop_run)
        btn("Examples",  "Browse examples (Ctrl+E)",    self._pick_example)
        tb.addSeparator()
        btn("Full Output", "Show every node's full output",
                          self._show_all_outputs)

    # ------------------------------------------------------------ status   #
    def _go_home(self):
        """Reset zoom and frame every node in the scene."""
        try:
            self.view.resetTransform()
        except Exception:
            pass
        try:
            self.view.frame_all()
        except Exception:
            pass
        self.report("Home", "info", 1200)


    # ---- Convert dialog ---- #

    def _open_convert_dialog_menu(self):
        python = sys.executable
        default_out = _output_dir("db_output", os.getcwd())
        self._open_convert_dialog(python, "", default_out)

    def _open_convert_dialog(self, python, folder, default_out=""):
        dlg = ConvertDialog(self, python=python,
                            default_folder=folder,
                            default_out=default_out)
        dlg.exec_()



    # ---- Output Paths pickers ---- #

    def _pick_output_path(self, key):
        start = _OUTPUT_PATHS.get(key, "") or os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(
            self, "Choose folder", start)
        if not path:
            return
        _OUTPUT_PATHS[key] = path
        _save_output_paths()
        self.report("%s -> %s" % (key, path), "success", 2500)

    def _clear_output_paths(self):
        for k in _OUTPUT_PATHS:
            _OUTPUT_PATHS[k] = ""
        _save_output_paths()
        self.report("Output paths cleared", "info", 2000)

    def _show_output_paths(self):
        lines = [
            "generated.py:  %s" % (_OUTPUT_PATHS["generated_py"] or "(cwd)"),
            "graph export:  %s" % (_OUTPUT_PATHS["graph_export"] or "(ask)"),
            "database out:  %s" % (_OUTPUT_PATHS["db_output"] or "(cwd)"),
        ]
        QMessageBox.information(self, "Output Paths", "\n".join(lines))


    def _open_py_to_pyui_dialog(self):
        if PythonToPyUIDialog is None:
            self.report(
                "ConvertDialogs module not importable",
                "error", 3000)
            return
        dlg = PythonToPyUIDialog(self)
        dlg.exec_()

    def _open_pyui_to_py_dialog(self):
        if PyUIToPythonDialog is None:
            self.report(
                "ConvertDialogs module not importable",
                "error", 3000)
            return

        def _provide(graph_dict):
            # Save the current scene, load the graph, run codegen,
            # restore.  Keeps the user's graph untouched.
            try:
                saved = self.scene.to_dict()
            except Exception:
                saved = None
            try:
                self.scene.load_from_dict(graph_dict)
                return self._generate_code()
            finally:
                if saved is not None:
                    try:
                        self.scene.load_from_dict(saved)
                    except Exception as ex:
                        print("[convert] scene restore failed:", ex)

        dlg = PyUIToPythonDialog(self, code_provider=_provide)
        dlg.exec_()

    def _on_undo(self):
        n = self._undo_mgr.undo()
        self._refresh_status()
        if n or self._undo_mgr.can_redo():
            self.report("Undo  (%d left)" % n, "info", 900)
        else:
            self.report("Nothing to undo", "debug", 900)

    def _on_redo(self):
        n = self._undo_mgr.redo()
        self._refresh_status()
        if n or self._undo_mgr.can_undo():
            self.report("Redo  (%d left)" % n, "info", 900)
        else:
            self.report("Nothing to redo", "debug", 900)


    def _sync_edit_menu(self):
        try:
            self._act_undo.setEnabled(self._undo_mgr.can_undo())
            self._act_redo.setEnabled(self._undo_mgr.can_redo())
        except Exception:
            pass


    def _open_library_manager(self):
        if LibraryManagerDialog is None:
            self.report(
                "LibraryDialogs module not importable",
                "error", 3000)
            return
        # Prefer data/main.db if the repo was tidied; fall back to
        # the classic main.db.
        db_path = "main.db"
        for cand in ("data/main.db", "main.db"):
            if os.path.isfile(cand):
                db_path = cand
                break
        dlg = LibraryManagerDialog(self, db_path=db_path)
        dlg.exec_()


    def _build_status_bar(self):
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.lbl_counts = QLabel("0 nodes · 0 links")
        self.lbl_hint   = QLabel("Shift+A add · Hover for info · Middle-drag pan")
        sb.addWidget(self.lbl_counts)
        sb.addPermanentWidget(self.lbl_hint)

    def _refresh_status(self):
        if self.scene is None or sip.isdeleted(self.scene):
            return
        try:
            n, e = self.scene.counts()
        except RuntimeError:
            return
        if hasattr(self, "lbl_counts"):
            name = os.path.basename(self._current_path) if self._current_path else "untitled"
            star = "*" if getattr(self, "_dirty", False) else ""
            self.lbl_counts.setText(
                f"{name}{star} · {n} nodes · {e} links")

    def closeEvent(self, event):
        if not self._confirm_discard(label="Quit"):
            event.ignore()
            return
        try:
            data = _collect_settings()
            g = self.saveGeometry().toBase64().data().decode("ascii")
            data["window_geometry"] = g
            data["last_file"] = self._current_path or ""
            _save_settings(data)
        except Exception as ex:
            print("[settings] close save failed:", ex)
        try:
            self.scene.selectionChanged.disconnect(self.sidebar.on_selection_changed)
        except Exception:
            pass
        try:
            self.scene.graph_changed.disconnect(self._refresh_status)
        except Exception:
            pass
        super().closeEvent(event)

    # ---------------------------------------------------------- file I/O  #
    def new_graph(self):
        if not self._confirm_discard(label="New Graph"):
            return
        self.scene.clear()
        self.scene.temp_edge = None
        self.scene.drag_socket = None
        self.scene._tooltip = None
        self._current_path = None
        self._dirty = False
        self._refresh_title()
        self._refresh_status()
        self.report("New graph", "info", 1500)

    def open_graph(self):
        if not self._confirm_discard(label="Open Graph"):
            return
        start_dir = ""
        if self._current_path:
            start_dir = os.path.dirname(self._current_path)
        elif getattr(self, "_last_file_hint", None):
            start_dir = os.path.dirname(self._last_file_hint)
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Graph", start_dir,
            "Node Graph (*.json);;All Files (*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.scene.load_from_dict(data)
            self.view.frame_all()
            self._current_path = path
            self._dirty = False
            self._refresh_title()
            self._refresh_status()
            self.report("Opened %s" % os.path.basename(path),
                        "success", 2000)
        except Exception as ex:
            QMessageBox.critical(self, "Open failed", str(ex))

    def save_graph(self):
        if self._current_path:
            return self._save_to(self._current_path)
        return self.save_graph_as()

    def _save_current(self):
        return self.save_graph()

    def _save_to(self, path):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.scene.to_dict(), f, indent=2)
            self._current_path = path
            self._dirty = False
            self._refresh_title()
            self.report("Saved %s" % os.path.basename(path),
                        "success", 2000)
            return True
        except Exception as ex:
            QMessageBox.critical(self, "Save failed", str(ex))
            return False

    def save_graph_as(self):
        if self._current_path:
            start = self._current_path
        else:
            _out_dir = _output_dir("graph_export", os.getcwd())
            start = os.path.join(_out_dir, "graph.json")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Graph", start,
            "Node Graph (*.json);;All Files (*)")
        if not path:
            return False
        return self._save_to(path)

    def _refresh_title(self):
        name = os.path.basename(self._current_path) if self._current_path else "untitled"
        star = "*" if self._dirty else ""
        self.setWindowTitle(
            "AI Node Editor  —  %s%s  —  Blender Style" % (name, star))

    def _confirm_discard(self, label="New Graph", allow_save=True):
        n, _ = self.scene.counts()
        if n == 0 or not getattr(self, "_dirty", False):
            return True
        box = QMessageBox(self)
        box.setWindowTitle(label)
        box.setIcon(QMessageBox.Question)
        box.setText("You have unsaved changes.")
        box.setInformativeText("Save before continuing?")
        b_save    = box.addButton("Save",   QMessageBox.AcceptRole)
        b_discard = box.addButton("Discard", QMessageBox.DestructiveRole)
        b_cancel  = box.addButton("Cancel",  QMessageBox.RejectRole)
        box.setDefaultButton(b_save)
        box.exec_()
        clicked = box.clickedButton()
        if clicked is b_save:
            return self._save_current()
        if clicked is b_discard:
            return True
        return False

    # ----------------------------------------------------------- add nodes #
    def _add_from_library(self, template):
        if not template.get("enabled", True):
            msg = (template.get("_lifecycle") or {}).get("locked_message") or \
                  ("%s is locked." % template["name"])
            self.report(msg, "warning", 4000)
            return
        pos = self.view.current_scene_pos()
        self.scene.add_node_from_template(template, pos)

    def _add_template_at_cursor(self, template):
        if not template.get("enabled", True):
            msg = (template.get("_lifecycle") or {}).get("locked_message") or \
                  ("%s is locked." % template["name"])
            self.report(msg, "warning", 4000)
            return
        pos = self.view.current_scene_pos()
        self.scene.add_node_from_template(template, pos)

    # --------------------------------------------------------- settings    #
    def _persist_settings(self):
        try:
            data = _collect_settings()
            data["last_file"] = self._current_path or ""
            g = self.saveGeometry().toBase64().data().decode("ascii")
            data["window_geometry"] = g
            _save_settings(data)
        except Exception as ex:
            print("[settings] persist failed:", ex)

    def _build_settings_menu(self, menu):
        menu.setStyleSheet(MENU_STYLE)

        a = QAction("Hide Path Wires", self)
        a.setCheckable(True)
        a.setChecked(_PATH_FLAGS.get("hidden", True))
        def _toggle_hide(checked):
            _PATH_FLAGS["hidden"] = bool(checked)
            for it in self.scene.items():
                if isinstance(it, Edge) and getattr(it, "is_path_edge", False):
                    try:
                        it.setVisible(not checked)
                    except Exception:
                        pass
            self._persist_settings()
        a.toggled.connect(_toggle_hide)
        menu.addAction(a)

        menu.addSeparator()

        b = QAction("Auto Save", self)
        b.setCheckable(True)
        b.setChecked(_AUTOSAVE["enabled"])
        def _toggle_auto(checked):
            _AUTOSAVE["enabled"] = bool(checked)
            if checked:
                self._autosave_timer.start(_AUTOSAVE["interval"] * 1000)
                self.report("Auto save enabled (%ss)" % _AUTOSAVE["interval"], "info")
            else:
                self._autosave_timer.stop()
                self.report("Auto save disabled", "info")
            self._persist_settings()
        b.toggled.connect(_toggle_auto)
        menu.addAction(b)

        sub = menu.addMenu("Auto Save Interval")
        sub.setStyleSheet(MENU_STYLE)
        for sec in (10, 30, 60, 120, 300):
            act = sub.addAction("%d seconds" % sec)
            act.setCheckable(True)
            act.setChecked(_AUTOSAVE["interval"] == sec)
            def _set_interval(checked, s=sec):
                if not checked:
                    return
                _AUTOSAVE["interval"] = s
                if self._autosave_timer.isActive():
                    self._autosave_timer.start(s * 1000)
                self.report("Auto save interval: %ds" % s, "info")
                self._persist_settings()
            act.toggled.connect(_set_interval)
            sub.addAction(act)

        bs = QAction("Show Block Slot", self)
        bs.setCheckable(True)
        bs.setChecked(_BLOCKS["show_slot"])
        def _toggle_bs(checked):
            _BLOCKS["show_slot"] = bool(checked)
            for it in self.scene.items():
                if isinstance(it, Node):
                    it.update()
            self._persist_settings()
        bs.toggled.connect(_toggle_bs)
        menu.addAction(bs)

        rr = QAction("Auto Reroute Wires", self)
        rr.setCheckable(True)
        rr.setChecked(_REROUTE["enabled"])
        def _toggle_rr(checked):
            _REROUTE["enabled"] = bool(checked)
            self.report("Auto reroute enabled" if checked
                        else "Auto reroute disabled", "info")
            self._persist_settings()
        rr.toggled.connect(_toggle_rr)
        menu.addAction(rr)

        ra = QAction("Ask Before Reroute", self)
        ra.setCheckable(True)
        ra.setChecked(_REROUTE["ask"])
        def _toggle_ra(checked):
            _REROUTE["ask"] = bool(checked)
            self.report("Ask before reroute enabled" if checked
                        else "Ask before reroute disabled", "info")
            self._persist_settings()
        ra.toggled.connect(_toggle_ra)
        menu.addAction(ra)

        menu.addSeparator()

        c = QAction("Save After Change", self)
        c.setCheckable(True)
        c.setChecked(_AUTOSAVE.get("after_change", False))
        def _toggle_after(checked):
            _AUTOSAVE["after_change"] = bool(checked)
            self.report("Save after change enabled" if checked
                        else "Save after change disabled", "info")
            self._persist_settings()
        c.toggled.connect(_toggle_after)
        menu.addAction(c)

        menu.addSeparator()

        now = menu.addAction("Save Now")
        now.triggered.connect(self._autosave_tick)

        menu.addSeparator()

        sub_paths = menu.addMenu("Output Paths")
        sub_paths.setStyleSheet(MENU_STYLE)
        for label, key in (
            ("generated.py folder\u2026", "generated_py"),
            ("Graph export folder\u2026", "graph_export"),
            ("Database output folder\u2026", "db_output"),
        ):
            act = sub_paths.addAction(label)
            act.triggered.connect(
                lambda _c=False, k=key: self._pick_output_path(k))
        sub_paths.addSeparator()
        clr = sub_paths.addAction("Clear all")
        clr.triggered.connect(self._clear_output_paths)
        show = sub_paths.addAction("Show current")
        show.triggered.connect(self._show_output_paths)

    def _autosave_target(self):
        if self._current_path:
            return self._current_path + ".autosave"
        d = os.path.expanduser("~/.pytorchui")
        if not os.path.isdir(d):
            try:
                os.makedirs(d, exist_ok=True)
            except Exception:
                pass
        return os.path.join(d, "autosave.json")

    def _autosave_tick(self):
        path = self._autosave_target()
        try:
            d = os.path.dirname(path)
            if d and not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.scene.to_dict(), f, indent=2)
            self.report("Auto-saved -> %s" % os.path.basename(path),
                        "debug", 1500)
        except Exception as ex:
            print("[autosave] failed:", ex)

    def _setup_autosave(self):
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(_AUTOSAVE["interval"] * 1000)
        self._autosave_timer.timeout.connect(self._autosave_tick)
        if _AUTOSAVE["enabled"]:
            self._autosave_timer.start()
        QTimer.singleShot(0, self._sync_settings_menu)

    def _sync_settings_menu(self):
        """Reflect persisted settings on the menu checkboxes."""
        mb = self.menuBar()
        for act in mb.actions():
            if act.text().strip() != "Settings":
                continue
            menu = act.menu()
            if menu is None:
                return
            for a in menu.actions():
                t = a.text()
                if t == "Hide Path Wires":
                    a.setChecked(_PATH_FLAGS["hidden"])
                elif t == "Auto Save":
                    a.setChecked(_AUTOSAVE["enabled"])
                elif t == "Save After Change":
                    a.setChecked(_AUTOSAVE["after_change"])
                elif t == "Show Block Slot":
                    a.setChecked(_BLOCKS["show_slot"])
                elif t == "Auto Reroute Wires":
                    a.setChecked(_REROUTE["enabled"])
                elif t == "Ask Before Reroute":
                    a.setChecked(_REROUTE["ask"])
                elif t == "Auto Save Interval":
                    sub = a.menu()
                    if sub is not None:
                        for s in sub.actions():
                            s.setChecked(
                                _AUTOSAVE["interval"]
                                == int(s.text().split()[0]))
            return

    def _setup_after_change(self):
        self._after_change_timer = QTimer(self)
        self._after_change_timer.setSingleShot(True)
        self._after_change_timer.setInterval(600)
        self._after_change_timer.timeout.connect(self._autosave_tick)
        self.scene.graph_changed.connect(self._on_graph_changed_save)

    def _on_graph_changed_save(self):
        if not self._dirty:
            self._dirty = True
            self._refresh_title()
        if not _AUTOSAVE.get("after_change", False):
            return
        if hasattr(self, "_after_change_timer"):
            self._after_change_timer.start()

    # ------------------------------------------------------ auto reroute  #
    def _setup_reroute(self):
        self.scene.node_settled.connect(self._on_node_settled)

    def _on_node_settled(self, node):
        try:
            self._check_block_drop(node)
        except Exception as _ex:
            print("[blocks] drop check failed:", _ex)
        if not _REROUTE["enabled"]:
            return
        try:
            candidates = self._find_reroute_candidates(node)
        except Exception as ex:
            print("[reroute] scan failed:", ex)
            return
        if not candidates:
            return
        previews = self._make_reroute_previews(candidates)
        if _REROUTE["ask"]:
            lines = ["Reroute %d wire(s) through '%s'?"
                     % (len(candidates), node.title), ""]
            for (edge, _in_s, _out_s) in candidates:
                a = edge.start_socket.node.title
                b = edge.end_socket.node.title
                lines.append("   %s  ->  %s  ->  %s" % (a, node.title, b))
            r = QMessageBox.question(
                self, "Auto Reroute", "\n".join(lines),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if r != QMessageBox.Yes:
                for p in previews:
                    if p.scene() is not None:
                        self.scene.removeItem(p)
                return
        for p in previews:
            if p.scene() is not None:
                self.scene.removeItem(p)
        self._apply_reroutes(candidates)
        self.report("Rerouted %d wire(s) through '%s'"
                    % (len(candidates), node.title), "success", 2500)

    def _find_reroute_candidates(self, node):
        scene = self.scene
        node_rect = node.sceneBoundingRect()
        candidates = []
        for item in scene.items():
            if not isinstance(item, Edge):
                continue
            if item.start_socket is None or item.end_socket is None:
                continue
            if item.start_socket.node is node or item.end_socket.node is node:
                continue
            src = item.start_socket
            dst = item.end_socket
            is_flow = (src.socket_type == "path" and dst.socket_type == "path")
            if is_flow and not _REROUTE["flow"]:
                continue
            if not is_flow and not _REROUTE["data"]:
                continue
            try:
                if not item.path().intersects(node_rect):
                    continue
            except Exception:
                continue
            in_match = None
            for s in node.inputs:
                if s.socket_type == dst.socket_type \
                   or s.socket_type == "any" or dst.socket_type == "any":
                    in_match = s
                    break
            if in_match is None or in_match.connections:
                continue
            out_match = None
            for s in node.outputs:
                if s.socket_type == src.socket_type \
                   or s.socket_type == "any" or src.socket_type == "any":
                    out_match = s
                    break
            if out_match is None:
                continue
            candidates.append((item, in_match, out_match))
        return candidates

    def _make_reroute_previews(self, candidates):
        out = []
        for (edge, in_sock, out_sock) in candidates:
            try:
                p1 = Edge(edge.start_socket, in_sock)
                p1.preview = True
                p1.setZValue(50)
                self.scene.addItem(p1)
                p1.update_path()
                out.append(p1)
                p2 = Edge(out_sock, edge.end_socket)
                p2.preview = True
                p2.setZValue(50)
                self.scene.addItem(p2)
                p2.update_path()
                out.append(p2)
            except Exception as ex:
                print("[reroute] preview failed:", ex)
        return out

    def _apply_reroutes(self, candidates):
        scene = self.scene
        for (edge, in_sock, out_sock) in candidates:
            src = edge.start_socket
            dst = edge.end_socket
            try:
                scene.remove_edge(edge)
            except Exception as ex:
                print("[reroute] remove_edge:", ex)
            try:
                scene.connect_sockets(src, in_sock)
            except Exception as ex:
                print("[reroute] src->in:", ex)
            try:
                scene.connect_sockets(out_sock, dst)
            except Exception as ex:
                print("[reroute] out->dst:", ex)

    # ---------------------------------------------------------- blocks    #
    def _setup_blocks(self):
        self.scene.block_dropped.connect(self._on_block_dropped)

    def _on_block_dropped(self, parent, child):
        if parent is child:
            return
        if getattr(child, "parent_node", None) is parent:
            return
        # already inside something else? ask again so user can re-parent
        cur = getattr(child, "parent_node", None)
        cur_name = cur.title if cur is not None else ""
        msg = "Attach '%s' as a child of '%s'?" % (child.title, parent.title)
        if cur_name:
            msg += "\n\nCurrently attached to '%s'." % cur_name
        reply = QMessageBox.question(
            self, "Make child?", msg,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if reply != QMessageBox.Yes:
            # clear hover highlight
            for n in self.scene.items():
                if isinstance(n, Node):
                    n._block_hovered = False
                    n.update()
            return
        if parent.add_block(child):
            self.report("'%s' now contains '%s'"
                        % (parent.title, child.title), "success", 2200)
        parent.layout_blocks()

    def _check_block_drop(self, node):
        # Widened drop zone: we accept the node if its center is within
        # the parent's block slot OR within +-24px of it.
        scene = self.scene
        center = node.sceneBoundingRect().center()
        for other in scene.items():
            if not isinstance(other, Node) or other is node:
                continue
            if node in getattr(other, "blocks", []):
                continue
            br = other.block_rect()
            br_scene = QRectF(
                other.pos().x() + br.x(),
                other.pos().y() + br.y() - 24,     # widen upward
                br.width(),
                br.height() + 48)                  # widen downward
            if br_scene.contains(center):
                scene.block_dropped.emit(other, node)
                return
        # no hit: clear any hover highlight
        for n in scene.items():
            if isinstance(n, Node):
                n._block_hovered = False
                n.update()

    def _help_css(self):
        return (
            "body { font-family: 'Segoe UI','DejaVu Sans',sans-serif;"
            " font-size: 13px; color: #DEDEDE; }"
            "h1 { color: #F5F5F5; font-size: 17px; font-weight: 700;"
            " margin: 18px 0 6px 0; padding-bottom: 4px;"
            " border-bottom: 1px solid #3E3E3E; }"
            "h2 { color: #E0A030; font-size: 12px; font-weight: 700;"
            " margin: 18px 0 6px 0; letter-spacing: 1.2px; }"
            "h3 { color: #E8E8E8; font-size: 13px; font-weight: 600;"
            " margin: 12px 0 4px 0; }"
            "p  { margin: 8px 0; }"
            "code { font-family: 'JetBrains Mono','Consolas',"
            "'DejaVu Sans Mono',monospace;"
            " background-color: #1A1A1A; color: #E6C07B;"
            " padding: 1px 5px; }"
            "blockquote { color: #B8B8B8;"
            " border-left: 3px solid #4A90D9;"
            " padding: 4px 12px; margin: 10px 0; font-style: italic; }"
            "a { color: #4A90D9; text-decoration: none; }"
            "table { border-collapse: collapse; margin: 10px 0; }"
            "th { background-color: #2E2E2E; color: #F0F0F0;"
            " padding: 6px 12px; text-align: left; font-weight: 600;"
            " border-bottom: 2px solid #E0A030; }"
            "td { padding: 5px 12px; border-bottom: 1px solid #303030; }"
            "hr { border: none; border-top: 1px solid #3E3E3E;"
            " margin: 14px 0; }"
            "ul, ol { margin: 6px 0; }"
            "li { margin: 3px 0; }"
        )

    def eventFilter(self, obj, event):
        try:
            if obj is getattr(self, "_help_body", None):
                if event.type() == QEvent.Wheel:
                    if event.modifiers() & Qt.ShiftModifier:
                        delta = (event.angleDelta().y()
                                 or event.angleDelta().x())
                        bar = self._help_body.horizontalScrollBar()
                        bar.setValue(bar.value() - delta // 2)
                        return True
        except Exception:
            pass
        return super().eventFilter(obj, event)

    def _setup_help_dock(self):
        self._help_dock = QDockWidget("  Help", self)
        self._help_dock.setAllowedAreas(
            Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self._help_dock.setStyleSheet(
            "QDockWidget { color:#DDD; font-weight:600; }"
            "QDockWidget::title {"
            " background-color:#1E1E1E; padding:6px 10px;"
            " border-bottom:1px solid #2A2A2A; }")
        container = QWidget()
        container.setObjectName("HelpPanel")
        container.setStyleSheet("QWidget#HelpPanel { background-color:#202020; }")
        v = QVBoxLayout(container)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(8)

        head_row = QHBoxLayout(); head_row.setSpacing(8); head_row.setContentsMargins(0, 0, 0, 0)
        self._help_badge = QLabel("HELP")
        self._help_badge.setAlignment(Qt.AlignCenter)
        self._help_badge.setFixedHeight(20)
        self._set_badge("help")
        head_row.addWidget(self._help_badge, 0)
        head_row.addStretch(1)
        v.addLayout(head_row)

        self._help_title = QLabel()
        f = QFont("Segoe UI", 12); f.setBold(True)
        self._help_title.setFont(f)
        self._help_title.setStyleSheet("color:#F5F5F5;")
        self._help_title.setWordWrap(True)
        v.addWidget(self._help_title)

        self._help_sub = QLabel()
        self._help_sub.setStyleSheet("color:#8A8A8A; font-size:11px;")
        self._help_sub.setWordWrap(True)
        v.addWidget(self._help_sub)

        line = QFrame(); line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background-color:#3A3A3A; max-height:1px; border:none;")
        v.addWidget(line)

        self._help_body = QTextBrowser()
        self._help_body.installEventFilter(self)
        self._help_body.setOpenExternalLinks(True)
        self._help_body.setOpenLinks(True)
        self._help_body.setReadOnly(True)
        self._help_body.setFrameShape(QFrame.NoFrame)
        self._help_body.setStyleSheet(
            "QTextBrowser { background-color:#1A1A1A; color:#DEDEDE;"
            " border:1px solid #2A2A2A; padding:10px 14px;"
            " selection-background-color:#E08C4A; selection-color:#1A1A1A; }")
        try:
            self._help_body.document().setDefaultStyleSheet(self._help_css())
        except Exception:
            pass
        v.addWidget(self._help_body, 1)

        self._help_dock.setWidget(container)
        self._help_dock.setMinimumWidth(400)
        self.addDockWidget(Qt.RightDockWidgetArea, self._help_dock)
        self._help_dock.hide()

    def _set_badge(self, kind):
        color = self._HELP_KIND_COLORS.get(kind, "#8A8A8A")
        self._help_badge.setText(kind.upper())
        self._help_badge.setStyleSheet(
            "QLabel {"
            " background-color: " + color + ";"
            " color: #101010;"
            " border-radius: 8px;"
            " padding: 2px 10px;"
            " font-weight: 700;"
            " font-size: 10px;"
            " }")

    def _show_focus_help(self):
        focus = _FOCUS
        if focus.get("kind") == "":
            self._set_badge("help")
            self._help_title.setText("Help")
            self._help_sub.setText("")
            self._set_help_body(
                "# Welcome\n\n"
                "Click any **node**, **socket**, **section header**, or "
                "**library entry** and press `F1` to see its full "
                "documentation here.\n\n"
                "---\n\n"
                "## What gets rendered\n\n"
                "- Markdown headings, lists, tables and links\n"
                "- Torch-style reStructuredText docstrings\n"
                "- `.. math::` blocks, rendered as real LaTeX images\n"
                "- Python and C++ code with syntax highlighting\n\n"
                "## Keyboard\n\n"
                "| key | action |\n"
                "| --- | --- |\n"
                "| `F1` | show help for the last clicked item |\n"
                "| `Shift+A` | open the Add-node popup |\n"
                "| `Ctrl+D` | duplicate selection |\n"
                "| `Del` | delete selection |")
            self._help_dock.show()
            self._help_dock.raise_()
            return

        kind = focus["kind"]
        title = ""
        subtitle = ""
        body = ""

        if kind == "library":
            title = focus["library_title"]
            subtitle = "Node Library"
            body = focus["library_body"]

        elif kind == "socket":
            obj = focus["object"]
            if obj is None or sip.isdeleted(obj):
                return
            title = "%s  \u2192  %s" % (obj.node.title, obj.name)
            subtitle = "%s \u00b7 %s" % (
                "input" if obj.is_input else "output", obj.socket_type)
            pieces = [
                "| property | value |",
                "| --- | --- |",
                "| direction | %s |" % ("input" if obj.is_input else "output"),
                "| type | `%s` |" % obj.socket_type,
                "| parent node | %s |" % obj.node.title,
            ]
            if obj.value is not None and not obj.connections:
                pieces.append("| literal value | `%r` |" % obj.value)
            pieces.append("| connected | %s |" % ("yes" if obj.connections else "no"))
            if obj.connections:
                pieces.append("| link count | %d |" % len(obj.connections))
            pieces.append("")
            pieces.append("---")
            pieces.append("")
            pieces.append(obj.description or "*No description.*")
            body = "\n".join(pieces)

        elif kind == "node":
            obj = focus["object"]
            if obj is None or sip.isdeleted(obj):
                return
            idx = focus.get("section_index", -1)
            if 0 <= idx < len(obj.sections):
                kind = "section"
                sec = obj.sections[idx]
                title = "%s  \u2192  %s" % (
                    obj.title, sec.get("name") or "(unnamed)")
                subtitle = "%d inputs \u00b7 %d outputs" % (
                    len(sec["inputs"]), len(sec["outputs"]))
                body = (sec.get("description") or "").strip() or \
                       "*This section has no description.*"
            else:
                title = obj.title
                subtitle = "%d inputs \u00b7 %d outputs \u00b7 %d sections" % (
                    len(obj.inputs), len(obj.outputs), len(obj.sections))
                body = obj.description or "*This node has no description.*"

        self._set_badge(kind)
        self._help_title.setText(title)
        self._help_sub.setText(subtitle)
        self._set_help_body(body)
        self._help_dock.show()
        self._help_dock.raise_()

    def _rst_to_markdown(self, text):
        if not text:
            return text
        cached = self._RST_CACHE.get(text)
        if cached is not None:
            return cached

        refs = {}
        raw_lines = text.split("\n")
        lines = []
        for raw in raw_lines:
            m = re.match(r"^\s*\.\.\s+_([^:]+):\s*(\S+)\s*$", raw)
            if m:
                refs[m.group(1).strip()] = m.group(2).strip()
                continue
            lines.append(raw)

        out = []
        i = 0
        n = len(lines)

        def is_directive(s):
            return s.lstrip().startswith("..")

        def is_math_continuation(s):
            if s.startswith("   ") or s.startswith("\t"):
                return True
            return s.lstrip().startswith("\\")

        def strip_rst_roles(s):
            s = re.sub(r":math:`([^`]*)`", r"\1", s)
            s = re.sub(
                r":(?:class|func|meth|attr|mod|obj|ref|doc|"
                r"py:class|py:func|py:meth|py:attr|py:mod):"
                r"`~?([^`]+)`", r"\1", s)
            return s

        def read_code_block(start):
            code = []
            i2 = start
            while i2 < n:
                ln = lines[i2]
                s = ln.lstrip()
                if s.startswith(">>>") or s.startswith("..."):
                    code.append(s)
                    i2 += 1
                    continue
                if not ln.strip():
                    j = i2 + 1
                    while j < n and not lines[j].strip():
                        j += 1
                    if j < n:
                        nx = lines[j].lstrip()
                        if (nx.startswith(">>>") or nx.startswith("...")
                                or (len(lines[j]) - len(lines[j].lstrip())) >= 4):
                            code.append("")
                            i2 = j
                            continue
                    break
                if (len(ln) - len(ln.lstrip())) >= 4:
                    code.append(ln.strip())
                    i2 += 1
                    continue
                break
            return code, i2

        while i < n:
            raw = lines[i]
            stripped = raw.strip()
            if not stripped:
                out.append("")
                i += 1
                continue

            m = re.match(r"^\s*\.\.\s+math::\s*(.*)$", raw)
            if m:
                first = m.group(1).rstrip()
                i += 1
                body = []
                if first:
                    body.append(first)
                while i < n:
                    nxt = lines[i]
                    if not nxt.strip():
                        j = i + 1
                        while j < n and not lines[j].strip():
                            j += 1
                        if j >= n:
                            break
                        if not is_math_continuation(lines[j]):
                            break
                        body.append("")
                        i = j
                        continue
                    if is_directive(nxt):
                        break
                    if self._SECTION_RE.match(nxt):
                        break
                    if is_math_continuation(nxt):
                        body.append(nxt.strip())
                        i += 1
                        continue
                    break
                math_text = " ".join(x for x in body if x).strip()
                math_text = strip_rst_roles(math_text)
                if math_text:
                    out.append("")
                    out.append("```maths")
                    out.append(math_text)
                    out.append("```")
                    out.append("")
                continue

            if is_directive(raw):
                m = re.match(r"^\s*\.\.\s+_[^:]+:\s*\S*\s*$", raw)
                if m:
                    i += 1
                    continue
                m = re.match(
                    r"^\s*\.\.\s+(note|warning|tip|important|caution)::\s*(.*)$",
                    raw, re.I)
                if m:
                    kind = m.group(1).capitalize()
                    rest = m.group(2).strip()
                    i += 1
                    while i < n:
                        nxt = lines[i]
                        if not nxt.strip():
                            i += 1
                            continue
                        if nxt.startswith("   ") or nxt.startswith("\t"):
                            rest += " " + nxt.strip()
                            i += 1
                            continue
                        break
                    out.append("")
                    out.append("> **" + kind + ":** " + rest)
                    out.append("")
                    continue
                i += 1
                continue

            m = self._SECTION_RE.match(stripped)
            if m:
                title = m.group(1).rstrip(":").strip()
                tlow = title.lower()
                title = "See Also" if tlow == "see also" else title.title()
                out.append("")
                out.append("## " + title)
                out.append("")
                i += 1
                while i < n and not lines[i].strip():
                    i += 1
                if tlow.startswith("example") and i < n:
                    lm = self._LANG_RE.match(lines[i])
                    if lm:
                        lang = lm.group(1).lower()
                        i += 1
                        while i < n and not lines[i].strip():
                            i += 1
                        code, i = read_code_block(i)
                        out.append("```" + lang)
                        out.extend(code)
                        out.append("```")
                        out.append("")
                continue

            lm = self._LANG_RE.match(stripped)
            if lm:
                j = i + 1
                while j < n and not lines[j].strip():
                    j += 1
                if j < n and lines[j].lstrip().startswith(">>>"):
                    lang = lm.group(1).lower()
                    i = j
                    code, i = read_code_block(i)
                    out.append("")
                    out.append("```" + lang)
                    out.extend(code)
                    out.append("```")
                    out.append("")
                    continue

            if stripped.startswith(">>>"):
                code, i = read_code_block(i)
                out.append("")
                out.append("```python")
                out.extend(code)
                out.append("```")
                out.append("")
                continue

            line = raw
            line = re.sub(r":math:`([^`]*)`", r"`\1`", line)
            line = re.sub(
                r":(?:class|func|meth|attr|mod|obj|ref|doc|"
                r"py:class|py:func|py:meth|py:attr|py:mod):"
                r"`~?([^`]+)`", r"`\1`", line)

            def _sub_ref(m):
                name = m.group(1).rstrip("_").strip()
                if name in refs:
                    return "[%s](%s)" % (name, refs[name])
                return name

            line = re.sub(r"`([^`]+)`_", _sub_ref, line)
            line = re.sub(
                r"\b([A-Za-z][\w\-]*)_\b",
                lambda mm: _sub_ref(mm) if mm.group(1) in refs else mm.group(0),
                line)
            line = re.sub(r"\.\.\s+_[^:]+:\s*\S+", "", line)
            out.append(line)
            i += 1

        result = "\n".join(out)
        result = re.sub(r"\n{3,}", "\n\n", result).strip()
        self._RST_CACHE[text] = result
        return result

    def _detect_lang(self, code):
        if re.search(r"^\s*(def|class|import|from)\s+\w+", code, re.M):
            return "python"
        if ">>>" in code:
            return "python"
        if re.search(r"^\s*#\s*include\s*<", code, re.M):
            return "cpp"
        if re.search(r"\bstd::\b", code):
            return "cpp"
        if re.search(r"\\(sum|frac|sqrt|int|left|right|times|alpha|beta|"
                     r"mathcal|mathbb|text|star|prod|math)", code):
            return "maths"
        return ""

    def _highlight(self, code, pattern, colors):
        import html as _html
        out = []
        pos = 0
        for m in pattern.finditer(code):
            if m.start() > pos:
                out.append(_html.escape(code[pos:m.start()]))
            kind = m.lastgroup
            col = colors.get(kind, "#DCDCDC")
            out.append('<span style="color:' + col + ';">'
                       + _html.escape(m.group()) + '</span>')
            pos = m.end()
        if pos < len(code):
            out.append(_html.escape(code[pos:]))
        return "".join(out)

    def _render_math_png(self, latex):
        s = (latex or "").strip()
        for rx in (r":[a-z][a-z0-9_]*:",
                   r"`",
                   r"\s[A-Za-z][\w-]*_\s",
                   r"\s(?:where|but|see|note|when|however)\s"):
            m = re.search(rx, s)
            if m:
                s = s[:m.start()]
                break
        s = re.sub(r"\s+(?:where|but|see|note|when|however|the|this|"
                   r"that|for|and|or|is|are|was|were)\s*$", "", s).strip()
        if len(s) > 500:
            cut = s[:500]
            last = None
            for m in re.finditer(r"\\[A-Za-z]+(?:\{[^{}]*\})?", cut):
                last = m
            s = cut[:last.end()].strip() if last else cut.strip()
        s = re.sub(r"\s+", " ", s).strip()
        s = s.replace("\\_", " ")
        s = s.replace("\\left", "").replace("\\right", "")
        s = re.sub(r"\\text\{([^{}]*)\}", r"\\mathrm{\1}", s)
        for sp in ("\\!", "\\,", "\\;", "\\:", "\\ "):
            s = s.replace(sp, " ")
        s = re.sub(r"\\\\([^\\])", r"\\\1", s)
        if not s:
            return None
        key = s
        if key in self._MATH_CACHE:
            return self._MATH_CACHE[key]
        png = None
        try:
            import io
            import matplotlib
            matplotlib.use("Agg", force=True)
            import matplotlib.pyplot as plt
            try:
                matplotlib.rcParams["mathtext.fontset"] = "stix"
            except Exception:
                pass
            fig = plt.figure(figsize=(0.01, 0.01), dpi=200)
            fig.patch.set_facecolor("#141414")
            fig.text(0.5, 0.5, "$" + s + "$",
                     fontsize=16, color="#E8E8E8",
                     ha="center", va="center")
            buf = io.BytesIO()
            fig.savefig(buf, format="png", bbox_inches="tight",
                        facecolor="#141414", edgecolor="none",
                        pad_inches=0.18)
            plt.close(fig)
            buf.seek(0)
            png = buf.read()
        except Exception as ex:
            preview = (s[:100] + "...") if len(s) > 100 else s
            print("[math] FAILED on: %s" % preview)
            print("[math] type: %s | msg: %s"
                  % (type(ex).__name__, str(ex).splitlines()[0]))
        if png:
            import base64
            url = ("data:image/png;base64,"
                   + base64.b64encode(png).decode("ascii"))
        else:
            url = None
        self._MATH_CACHE[key] = url
        return url

    def _math_block_html(self, latex):
        import html as _html
        url = self._render_math_png(latex)
        if url:
            return (
                '<div style="background-color:#141414; margin:14px 0; '
                'padding:12px 16px; border-left:3px solid #8A6BB8; '
                'border-radius:4px; text-align:center;">'
                '<img src="' + url + '" alt="formula" '
                'style="vertical-align:middle; max-width:100%;"></div>')
        display = (latex or "").strip()
        for rx in (r":[a-z][a-z0-9_]*:", r"`",
                   r"\s(?:where|but|see|note|when|however)\s"):
            m = re.search(rx, display)
            if m:
                display = display[:m.start()]
                break
        display = display.strip()
        return (
            '<div style="background-color:#141414; margin:12px 0; '
            'border-left:3px solid #8A6BB8; border-radius:4px;">'
            '<div style="background-color:#1A1A1A; color:#8A8A8A; '
            'padding:3px 12px; font-family:monospace; font-size:10px; '
            'letter-spacing:1.5px;">MATHS</div>'
            '<pre style="margin:0; padding:12px 14px; font-family:monospace; '
            'font-size:12px; color:#C586C0; font-style:italic; '
            'white-space:pre-wrap;">'
            + _html.escape(display) + '</pre></div>')

    def _code_block_html(self, lang, code):
        import html as _html
        code = code.rstrip("\n")
        if not lang:
            lang = self._detect_lang(code)
        lc = (lang or "").lower()
        if lc in ("math", "maths", "latex"):
            return self._math_block_html(code)
        if lc in ("python", "py"):
            inner = self._highlight(code, self._PY_PATTERN, self._PY_COLORS)
            accent = "#E0A030"
            label = "python"
        elif lc in ("cpp", "c++", "cc", "cxx", "c", "h", "hpp"):
            inner = self._highlight(code, self._CXX_PATTERN, self._CXX_COLORS)
            accent = "#E0A030"
            label = "cpp"
        else:
            inner = _html.escape(code)
            accent = "#3A3A3A"
            label = lc or "code"
        return (
            '<div style="background-color:#0E0E0E; margin:12px 0; '
            'border-left:3px solid ' + accent + '; border-radius:4px;">'
            '<div style="background-color:#1A1A1A; color:#8A8A8A; '
            'padding:3px 12px; font-family:monospace; font-size:10px; '
            'letter-spacing:1.5px;">' + _html.escape(label) + '</div>'
            '<pre style="margin:0; padding:12px 14px; font-family:monospace; '
            'font-size:12px; color:#DCDCDC; white-space:pre-wrap;">'
            + inner + '</pre></div>')

    def _md_to_html(self, text):
        from PyQt5.QtGui import QTextDocument
        pattern = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
        parts = []
        last = 0
        for m in pattern.finditer(text):
            if m.start() > last:
                parts.append(("md", text[last:m.start()]))
            parts.append(("code", m.group(1).lower(), m.group(2)))
            last = m.end()
        if last < len(text):
            parts.append(("md", text[last:]))
        pieces = []
        for p in parts:
            if p[0] == "md":
                chunk = p[1].strip()
                if not chunk:
                    continue
                try:
                    doc = QTextDocument()
                    doc.setDefaultStyleSheet(self._help_css())
                    doc.setMarkdown(chunk)
                    html = doc.toHtml()
                except Exception:
                    pieces.append("<p>" + p[1].replace("\n", "<br>") + "</p>")
                    continue
                m2 = re.search(r"<body[^>]*>(.*)</body>", html, re.DOTALL)
                pieces.append(m2.group(1) if m2 else html)
            else:
                pieces.append(self._code_block_html(p[1], p[2]))
        return ('<html><head><meta charset="utf-8"></head><body>'
                + "".join(pieces) + "</body></html>")

    def _set_help_body(self, markdown_text):
        text = markdown_text if markdown_text else "(no description)"

        # Cache the fully-rendered HTML.  Torch docstrings are
        # enormous; without this, re-clicking the same node
        # re-parses RST, re-renders every math block through
        # matplotlib, and re-colours every code block.
        cache = getattr(MainWindow, "_HELP_HTML_CACHE", None)
        if cache is None:
            cache = MainWindow._HELP_HTML_CACHE = {}
        key = hash(text)
        if key in cache:
            html = cache[key]
        else:
            try:
                md = self._rst_to_markdown(text)
            except Exception as ex:
                print("[help] RST parse failed:", ex)
                md = text
            try:
                html = self._md_to_html(md)
            except Exception as ex:
                print("[help] markdown render failed:", ex)
                import html as _h
                html = ("<html><body><pre>"
                        + _h.escape(md) + "</pre></body></html>")
            if len(cache) > 200:
                cache.clear()
            cache[key] = html

        try:
            self._help_body.setHtml(html)
        except Exception:
            try:
                self._help_body.setMarkdown(text)
            except Exception:
                self._help_body.setPlainText(text)
        cur = self._help_body.textCursor()
        cur.movePosition(cur.Start)
        self._help_body.setTextCursor(cur)

    # ------------------------------------------------------- examples menu #
    def _examples_dir(self):
        d = os.path.join(os.getcwd(), "examples")
        if not os.path.isdir(d):
            try:
                os.makedirs(d, exist_ok=True)
            except Exception:
                pass
        return d

    def _list_examples(self):
        d = self._examples_dir()
        out = []
        try:
            for name in sorted(os.listdir(d)):
                if not name.lower().endswith(".json"):
                    continue
                full = os.path.join(d, name)
                try:
                    size = os.path.getsize(full)
                except Exception:
                    size = 0
                out.append((name, full, size))
        except Exception:
            pass
        return out

    def _load_example_file(self, path):
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.scene.load_from_dict(data)
            self.view.frame_all()
            self._refresh_status()
            self.report("Loaded example: %s"
                        % os.path.basename(path), "success", 3000)
        except Exception as ex:
            self.report("Could not load %s: %s" % (path, ex),
                        "error", 5000)

    def _rebuild_examples_menu(self):
        for act in self.menuBar().actions():
            if act.text().strip() == "Examples":
                menu = act.menu()
                if menu is None:
                    return
                menu.clear()
                menu.setStyleSheet(MENU_STYLE)
                a = menu.addAction("Browse Examples…")
                a.setShortcut(QKeySequence("Ctrl+E"))
                a.triggered.connect(self._pick_example)
                menu.addSeparator()
                files = self._list_examples()
                if not files:
                    e = menu.addAction("(no examples — run createExample.py)")
                    e.setEnabled(False)
                else:
                    for name, full, _size in files:
                        it = menu.addAction(name)
                        it.setToolTip(full)
                        it.triggered.connect(
                            lambda checked=False, p=full:
                                self._load_example_file(p))
                return

    def _pick_example(self):
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                                     QLabel, QListWidget, QListWidgetItem,
                                     QPushButton, QLineEdit)
        d = self._examples_dir()
        files = self._list_examples()
        dlg = QDialog(self)
        dlg.setWindowTitle("Examples")
        dlg.resize(560, 480)
        dlg.setStyleSheet(
            "QDialog { background:#2B2B2B; color:#DDD; }"
            "QLineEdit { background:#3C3C3C; border:1px solid #555;"
            " border-radius:3px; padding:4px 8px; color:#EEE; }"
            "QListWidget { background:#262626; color:#DDD;"
            " border:1px solid #3A3A3A; outline:0; }"
            "QListWidget::item { padding:8px 10px; }"
            "QListWidget::item:hover { background:#333; }"
            "QListWidget::item:selected { background:#E08C4A; color:#1A1A1A; }"
            "QPushButton { background:#3C3C3C; border:1px solid #555;"
            " border-radius:3px; padding:6px 14px; color:#EEE; }"
            "QPushButton:hover { background:#4A4A4A; }")
        v = QVBoxLayout(dlg)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(8)
        title = QLabel("Choose an example pipeline")
        title.setStyleSheet("font-size:14px; font-weight:600;")
        v.addWidget(title)
        hint = QLabel("Path: " + d)
        hint.setStyleSheet("color:#8A8A8A; font-size:11px;")
        v.addWidget(hint)
        search = QLineEdit()
        search.setPlaceholderText("Filter…")
        v.addWidget(search)
        lst = QListWidget()
        v.addWidget(lst, 1)
        if not files:
            empty = QListWidgetItem("No examples found.  Run: python createExample.py")
            empty.setFlags(empty.flags() & ~Qt.ItemIsSelectable)
            lst.addItem(empty)
        else:
            for name, full, size in files:
                it = QListWidgetItem("%s    (%d bytes)" % (name, size))
                it.setData(Qt.UserRole, full)
                it.setToolTip(full)
                lst.addItem(it)
            lst.setCurrentRow(0)
        def _filter(text):
            text = text.strip().lower()
            for i in range(lst.count()):
                it = lst.item(i)
                it.setHidden(text and text not in it.text().lower())
        search.textChanged.connect(_filter)
        row = QHBoxLayout()
        btn_open   = QPushButton("Open")
        btn_cancel = QPushButton("Cancel")
        row.addStretch(1)
        row.addWidget(btn_cancel)
        row.addWidget(btn_open)
        v.addLayout(row)
        def _open_current():
            it = lst.currentItem()
            if it is None:
                return
            path = it.data(Qt.UserRole)
            if path:
                dlg.accept()
                self._load_example_file(path)
        lst.itemDoubleClicked.connect(lambda _it: _open_current())
        btn_open.clicked.connect(_open_current)
        btn_cancel.clicked.connect(dlg.reject)
        dlg.exec_()

    # ------------------------------------------------------ code + run    #
    def _generate_code_to_file(self):
        import traceback as _tb
        _out_dir = _output_dir("generated_py", os.getcwd())
        path = os.path.join(_out_dir, "generated.py")

        # 1. Run codegen entirely in memory.  If it raises, we do not
        #    touch the existing file until we know what happened.
        try:
            code = self._generate_code()
        except Exception as ex:
            tb_text = _tb.format_exc()

            # Print the traceback to the terminal that launched the editor.
            print()
            print("=" * 60)
            print("[generate] FAILED")
            print("=" * 60)
            print(tb_text)
            print("=" * 60)
            print()

            # Also write the traceback as comments into generated.py so
            # `cat generated.py` explains itself.
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write("# Generate FAILED\n")
                    f.write("# " + str(ex) + "\n")
                    f.write("#\n")
                    for ln in tb_text.splitlines():
                        f.write("# " + ln + "\n")
            except Exception:
                pass

            self.report("Generate failed: %s" % ex, "error", 8000)
            return

        # 2. Codegen succeeded — write it out.
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(code)
            n = code.count("\n")
            self.report("Wrote %s (%d lines)" % (path, n), "success", 3000)
        except Exception as ex:
            self.report("Write failed: %s" % ex, "error", 6000)


    def _node_by_id(self, node_id):
        for n in self.scene.items():
            if not isinstance(n, Node):
                continue
            if n.metadata.get("id") == node_id:
                return n
            if n.title == node_id:
                return n
            # Markers use _safe_safe_nid(n), which is the id if set,
            # otherwise a whitespace-free version of the title.
            try:
                if _safe_safe_nid(n) == node_id:
                    return n
            except NameError:
                pass
        return None

    def _clear_all_outputs(self):
        for n in self.scene.items():
            if isinstance(n, Node):
                n.clear_output()

    def _ensure_run_dialog(self):
        if getattr(self, "_run_dialog", None) is not None:
            return
        self._run_dialog = QDialog(self)
        self._run_dialog.setWindowTitle("Run Output")
        self._run_dialog.resize(880, 560)
        self._run_dialog.setStyleSheet("QDialog { background:#202020; }")
        v = QVBoxLayout(self._run_dialog)
        v.setContentsMargins(10, 10, 10, 10)
        self._run_output = QTextEdit()
        self._run_output.setReadOnly(True)
        self._run_output.setStyleSheet(
            "background:#141414; color:#DDDDDD;"
            " font-family:'JetBrains Mono','Consolas',monospace;"
            " font-size:12px; border:1px solid #2A2A2A;")
        v.addWidget(self._run_output)

    def _show_all_outputs(self):
        """Show every node's full output in one styled dialog."""
        from PyQt5.QtWidgets import QTextBrowser
        parts = []
        for n in self.scene.items():
            if not isinstance(n, Node):
                continue
            full = getattr(n, "output_full", "") or ""
            if full.strip() in ("", "None") and n.output_lines:
                full = "\n".join(n.output_lines)
            parts.append((n.title, n.metadata.get("id") or "", full))

        dlg = QDialog(self)
        dlg.setWindowTitle("All node outputs")
        dlg.resize(960, 680)
        dlg.setStyleSheet("QDialog { background:#202020; }")
        v = QVBoxLayout(dlg)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)

        head = QLabel("Full output of every node")
        head.setStyleSheet("font-weight:700; color:#F0F0F0; font-size:14px;")
        v.addWidget(head)

        browser = QTextBrowser()
        browser.setFrameShape(QFrame.NoFrame)
        browser.setStyleSheet(
            "QTextBrowser{"
            "  background:#141414; color:#DDDDDD;"
            "  font-family:'JetBrains Mono','Consolas','DejaVu Sans Mono',monospace;"
            "  font-size:12px; border:1px solid #2A2A2A; padding:8px 10px;"
            "  selection-background-color:#E08C4A; selection-color:#1A1A1A;}")

        import html as _html
        chunks = []
        for title, nid, text in parts:
            chunks.append(
                "<div style='margin-top:16px;'>"
                "<div style='color:#E0A030; font-weight:700; font-size:13px;'>"
                + _html.escape(title) +
                "</div>"
                "<div style='color:#5A5A5A; font-size:10px; margin-bottom:4px;'>"
                "id: " + _html.escape(nid) +
                "</div>"
                "<div style='border-left:3px solid #3A3A3A;"
                " padding:6px 10px; background:#1A1A1A;'>"
                + self._format_output_html(text or "(no output)").replace(
                    "\n", "<br>") +
                "</div></div>")

        browser.setHtml(
            "<html><head><meta charset='utf-8'></head>"
            "<body style='background:#141414; color:#DDDDDD;"
            " font-family:monospace; font-size:12px; margin:6px;'>"
            + "".join(chunks) +
            "</body></html>")
        v.addWidget(browser, 1)

        all_text = "\n\n".join(
            "%s\n%s\n%s" % ("=" * 60, t, x) for t, _id, x in parts)

        row = QHBoxLayout()
        btn_copy  = QPushButton("Copy All")
        btn_close = QPushButton("Close")
        def _copy():
            try:
                QApplication.clipboard().setText(all_text)
            except Exception:
                pass
        btn_copy.clicked.connect(_copy)
        btn_close.clicked.connect(dlg.accept)
        row.addStretch(1)
        row.addWidget(btn_copy)
        row.addWidget(btn_close)
        v.addLayout(row)

        dlg.exec_()

    def _format_output_html(self, text):
        """Turn a plain output blob into a colourised HTML fragment."""
        import html as _html
        s = _html.escape(text or "")
        # tensor / module keyword
        s = re.sub(r"\btensor\b",
                   '<span style="color:#569CD6;font-weight:bold;">tensor</span>',
                   s)
        # grad_fn=<...> (after html.escape the angle brackets are &lt; &gt;)
        s = re.sub(r"(grad_fn=&lt;[^&]+&gt;)",
                   r'<span style="color:#6A9955;font-style:italic;">\1</span>',
                   s)
        # dtype / device / shape / requires_grad
        s = re.sub(r"\b(dtype|device|shape|requires_grad|layout)\b",
                   '<span style="color:#C586C0;">\\1</span>', s)
        # scientific notation numbers
        s = re.sub(r"(-?\d+\.\d+e[+\-]?\d+)",
                   '<span style="color:#B5CEA8;">\\1</span>', s)
        # decimal numbers
        s = re.sub(r"(?<![\w.])(-?\d+\.\d+)(?![\w.])",
                   '<span style="color:#B5CEA8;">\\1</span>', s)
        # small integers (word-boundary, not part of an identifier)
        s = re.sub(r"(?<![\w.])(\b\d+\b)(?![\w.])",
                   '<span style="color:#B5CEA8;">\\1</span>', s)
        # Python literals
        s = re.sub(r"\b(None|True|False)\b",
                   '<span style="color:#C586C0;font-style:italic;">\\1</span>',
                   s)
        # quotes / strings
        s = re.sub(r"(&#x27;[^&]*?&#x27;|&quot;[^&]*?&quot;)",
                   r'<span style="color:#CE9178;">\1</span>', s)
        return s

    def _make_output_browser(self):
        from PyQt5.QtWidgets import QTextBrowser
        b = QTextBrowser()
        b.setOpenExternalLinks(False)
        b.setFrameShape(QFrame.NoFrame)
        b.setStyleSheet(
            "QTextBrowser{"
            "  background:#141414;"
            "  color:#DDDDDD;"
            "  font-family:'JetBrains Mono','Consolas','DejaVu Sans Mono',monospace;"
            "  font-size:12px;"
            "  border:1px solid #2A2A2A;"
            "  padding:8px 10px;"
            "  selection-background-color:#E08C4A;"
            "  selection-color:#1A1A1A;"
            "}")
        return b

    def _html_for_text(self, title, text):
        body = self._format_output_html(text or "(no output)")
        return (
            "<html><head><meta charset='utf-8'></head>"
            "<body style='background:#141414; color:#DDDDDD;"
            " font-family:monospace; font-size:12px; margin:8px;'>"
            + body.replace("\n", "<br>") +
            "</body></html>"
        )


    def _show_full_output(self, node):
        if node is None or sip.isdeleted(node):
            return
        text = getattr(node, "output_full", "") or ""
        # fall back to the shell's lines if the full text is empty or just
        # the None placeholder
        if text.strip() in ("", "None") and node.output_lines:
            text = "\n".join(node.output_lines)

        dlg = QDialog(self)
        dlg.setWindowTitle("Output — %s" % node.title)
        dlg.resize(880, 600)
        dlg.setStyleSheet("QDialog { background:#202020; }")
        v = QVBoxLayout(dlg)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)

        head = QLabel(node.title)
        head.setStyleSheet("font-weight:700; font-size:14px; color:#F0F0F0;")
        v.addWidget(head)

        sub = QLabel("node id: %s" % (node.metadata.get("id") or node.title))
        sub.setStyleSheet("color:#8A8A8A; font-size:11px;")
        v.addWidget(sub)

        line = QFrame(); line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background:#3A3A3A; max-height:1px; border:none;")
        v.addWidget(line)

        browser = self._make_output_browser()
        browser.setHtml(self._html_for_text(node.title, text))
        v.addWidget(browser, 1)

        row = QHBoxLayout()
        btn_copy  = QPushButton("Copy")
        btn_close = QPushButton("Close")
        def _copy():
            try:
                QApplication.clipboard().setText(text or "")
            except Exception:
                pass
        btn_copy.clicked.connect(_copy)
        btn_close.clicked.connect(dlg.accept)
        row.addStretch(1)
        row.addWidget(btn_copy)
        row.addWidget(btn_close)
        v.addLayout(row)

        dlg.exec_()

    def _show_run_dialog(self):
        self._ensure_run_dialog()
        self._run_output.clear()
        for l in getattr(self, "_run_log", []):
            self._run_output.append(l)
        self._run_dialog.show()
        self._run_dialog.raise_()

    def _run_generated_code(self):
        self._generate_code_to_file()
        if not os.path.isfile("generated.py"):
            self.report("generated.py missing", "error", 4000)
            return
        if getattr(self, "_run_thread", None) is not None:
            try:
                self._run_thread.kill()
                self._run_thread.wait(1500)
            except Exception:
                pass
            self._run_thread = None
        self._clear_all_outputs()
        self._run_log = []
        self._run_error_seen = False
        # make sure we never start in a paused state
        if os.path.exists(_PAUSE_FLAG_FILE):
            try:
                os.remove(_PAUSE_FLAG_FILE)
            except Exception:
                pass
        self._run_paused = False
        if getattr(self, "_act_pause", None) is not None:
            self._act_pause.setText("Pause")
        self._run_thread = _RunThread("generated.py", self)
        self._run_thread.line_read.connect(self._on_run_line)
        self._run_thread.finished_run.connect(self._on_run_finished)
        self._run_thread.start()

    def _pause_run(self):
        t = getattr(self, "_run_thread", None)
        if t is None:
            self.report("Nothing is running", "info", 2000)
            return
        t.pause()
        self.report("Paused", "warning", 2000)

    def _resume_run(self):
        t = getattr(self, "_run_thread", None)
        if t is None:
            self.report("Nothing is running", "info", 2000)
            return
        t.resume()
        self.report("Resumed", "success", 2000)

    def _stop_run(self):
        t = getattr(self, "_run_thread", None)
        if t is None:
            self.report("Nothing is running", "info", 2000)
            return
        t.kill()
        self._run_thread = None
        self._clear_all_outputs()
        self.report("Stopped", "warning", 2000)

    def _on_run_line(self, line):
        self._run_log.append(line)
        if not line.startswith("@@RT "):
            return
        parts = line[5:].split(" ", 2)
        cmd = parts[0] if parts else ""
        nid = parts[1] if len(parts) > 1 else ""
        payload = parts[2] if len(parts) > 2 else ""

        # Commands that must run before any node lookup.
        if cmd == "ask":
            self._handle_ask(nid, payload)
            return
        if cmd == "media":
            self._handle_media(nid, payload)
            return

        # ---- global markers that don't belong to a node ---- #
        if cmd == "ask":
            try:
                prompt = json.loads(payload)
            except Exception:
                prompt = str(payload)
            from PyQt5.QtWidgets import QInputDialog
            text, ok = QInputDialog.getText(
                self, "Input required", str(prompt))
            reply = text if ok else ""
            proc = getattr(self._run_thread, "_proc", None)
            if proc is not None and proc.stdin is not None:
                try:
                    proc.stdin.write(reply + "\n")
                    proc.stdin.flush()
                except Exception as ex:
                    print("[ask] write failed:", ex)
            return

        if cmd == "paused":
            self._run_paused = True
            if getattr(self, "_act_pause", None) is not None:
                self._act_pause.setText("Resume")
            self.report("Paused", "warning", 2500)
            return

        if cmd == "resumed":
            self._run_paused = False
            if getattr(self, "_act_pause", None) is not None:
                self._act_pause.setText("Pause")
            self.report("Resumed", "success", 2000)
            return

        # ---- node-scoped markers ---- #
        node = self._node_by_id(nid)
        if node is None:
            if cmd == "error":
                self._run_error_seen = True
                self._show_run_dialog()
            return
        if cmd == "begin":
            node.clear_output()
            node.set_running(True)
        elif cmd == "end":
            node.set_running(False)
        elif cmd == "output":
            try:
                val = json.loads(payload)
            except Exception:
                val = payload
            node.append_output(val)
        elif cmd == "full":
            try:
                val = json.loads(payload)
            except Exception:
                val = payload
            if hasattr(node, "set_full_output"):
                node.set_full_output(val)
        elif cmd == "stdout":
            try:
                val = json.loads(payload)
            except Exception:
                val = payload
            if hasattr(node, "add_stdout"):
                node.add_stdout(str(val))
            else:
                text = str(val).rstrip("\n")
                for ln in text.splitlines():
                    node.append_output(ln)
        elif cmd == "error":
            try:
                val = json.loads(payload)
            except Exception:
                val = payload
            node.append_output("ERROR: " + str(val))
            node.set_running(False)
            self._run_error_seen = True
            self._show_run_dialog()
    def _handle_ask(self, nid, payload):
        try:
            prompt = json.loads(payload)
        except Exception:
            prompt = str(payload)
        from PyQt5.QtWidgets import QInputDialog
        self._show_run_dialog()
        text, ok = QInputDialog.getText(
            self, "Input required",
            str(prompt) if prompt else "Enter value:")
        reply = text if ok else ""
        proc = getattr(getattr(self, "_run_thread", None), "_proc", None)
        if proc is not None and proc.stdin is not None:
            try:
                proc.stdin.write(reply + "\n")
                proc.stdin.flush()
            except Exception as ex:
                print("[ask] write failed:", ex)
        try:
            self._run_log.append("> %s%s" % (prompt, reply))
        except Exception:
            pass

    def _handle_media(self, nid, payload):
        try:
            info = json.loads(payload)
        except Exception:
            return
        kind = info.get("kind", "")
        path = info.get("path", "")

        # Paint the payload on the node body as well as opening a
        # dialog.  set_preview is defined on Node and picks a
        # renderer by kind.
        _node = self._node_by_id(nid)
        if _node is not None and hasattr(_node, "set_preview"):
            try:
                _node.set_preview(info)
            except Exception as ex:
                print("[preview] set_preview failed:", ex)

        if kind in ("image", "plot"):
            if path and os.path.isfile(path):
                self._show_image_dialog(nid, path)
        elif kind in ("audio", "video"):
            if path and os.path.isfile(path):
                self._show_media_player(nid, path, kind)
        elif kind == "folder":
            self._show_folder_dialog(nid, info)
        elif kind == "table":
            self._show_table_dialog(nid, info)
        elif kind == "text":
            self._show_text_dialog(nid, info)
        elif kind == "json":
            self._show_text_dialog(nid, info, mono=True)
        elif kind in ("html", "markdown"):
            self._show_html_dialog(nid, info)

    # ---- new preview dialogs ---- #

    def _show_folder_dialog(self, nid, info):
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                                     QLabel, QTableWidget,
                                     QTableWidgetItem, QPushButton)
        folder = info.get("folder", "")
        files = info.get("files", []) or []
        dlg = QDialog(self)
        dlg.setWindowTitle("Folder \u2014 %s" % nid)
        dlg.resize(880, 600)
        dlg.setStyleSheet(
            "QDialog{background:#202020;color:#DDD;}"
            "QTableWidget{background:#141414;color:#DDD;"
            " border:1px solid #2A2A2A;gridline-color:#2A2A2A;}"
            "QHeaderView::section{background:#2A2A2A;color:#DDD;"
            " padding:4px;border:0;}"
            "QPushButton{background:#3C3C3C;color:#EEE;"
            " border:1px solid #555;padding:5px 14px;border-radius:3px;}")
        v = QVBoxLayout(dlg)
        v.setContentsMargins(12, 12, 12, 12)
        head = QLabel("%d file(s) in %s" % (len(files), folder))
        head.setStyleSheet("font-weight:600;color:#F0F0F0;")
        v.addWidget(head)
        tbl = QTableWidget(len(files), 3)
        tbl.setHorizontalHeaderLabels(["Name", "Size", "Modified"])
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        import os as _os
        import time as _t
        for i, p in enumerate(files):
            try:
                size = _os.path.getsize(p)
                mtime = _t.strftime("%Y-%m-%d %H:%M",
                                    _t.localtime(_os.path.getmtime(p)))
            except Exception:
                size, mtime = 0, ""
            tbl.setItem(i, 0, QTableWidgetItem(_os.path.basename(p)))
            tbl.setItem(i, 1, QTableWidgetItem(str(size)))
            tbl.setItem(i, 2, QTableWidgetItem(mtime))
        tbl.resizeColumnsToContents()
        v.addWidget(tbl, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        b = QPushButton("Close")
        b.clicked.connect(dlg.accept)
        row.addWidget(b)
        v.addLayout(row)
        if not hasattr(self, "_media_dialogs"):
            self._media_dialogs = []
        self._media_dialogs.append(dlg)
        dlg.show()
        dlg.raise_()

    def _show_table_dialog(self, nid, info):
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                                     QLabel, QTableWidget,
                                     QTableWidgetItem, QPushButton)
        cols = info.get("cols", []) or []
        rows = info.get("rows", []) or []
        dlg = QDialog(self)
        dlg.setWindowTitle("Table \u2014 %s" % (info.get("title") or nid))
        dlg.resize(880, 600)
        dlg.setStyleSheet(
            "QDialog{background:#202020;color:#DDD;}"
            "QTableWidget{background:#141414;color:#DDD;"
            " border:1px solid #2A2A2A;gridline-color:#2A2A2A;}"
            "QHeaderView::section{background:#2A2A2A;color:#DDD;"
            " padding:4px;border:0;}"
            "QPushButton{background:#3C3C3C;color:#EEE;"
            " border:1px solid #555;padding:5px 14px;border-radius:3px;}")
        v = QVBoxLayout(dlg)
        v.setContentsMargins(12, 12, 12, 12)
        head = QLabel("%d x %d" % (len(rows), len(cols)))
        head.setStyleSheet("font-weight:600;color:#F0F0F0;")
        v.addWidget(head)
        tbl = QTableWidget(len(rows), len(cols))
        tbl.setHorizontalHeaderLabels([str(c) for c in cols])
        if not info.get("editable", False):
            tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        for i, row in enumerate(rows):
            for j, val in enumerate(row[:len(cols)]):
                tbl.setItem(i, j, QTableWidgetItem(str(val)))
        tbl.resizeColumnsToContents()
        v.addWidget(tbl, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        b = QPushButton("Close")
        b.clicked.connect(dlg.accept)
        row.addWidget(b)
        v.addLayout(row)
        if not hasattr(self, "_media_dialogs"):
            self._media_dialogs = []
        self._media_dialogs.append(dlg)
        dlg.show()
        dlg.raise_()

    def _show_text_dialog(self, nid, info, mono=True):
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                                     QLabel, QTextEdit, QPushButton)
        text = info.get("text", "")
        title = info.get("title") or nid
        dlg = QDialog(self)
        dlg.setWindowTitle("Text \u2014 %s" % title)
        dlg.resize(880, 600)
        dlg.setStyleSheet(
            "QDialog{background:#202020;color:#DDD;}"
            "QTextEdit{background:#141414;color:#DDD;"
            " border:1px solid #2A2A2A;padding:8px;}"
            "QPushButton{background:#3C3C3C;color:#EEE;"
            " border:1px solid #555;padding:5px 14px;border-radius:3px;}")
        v = QVBoxLayout(dlg)
        v.setContentsMargins(12, 12, 12, 12)
        head = QLabel("%d characters, %d lines"
                      % (len(text), text.count("\n") + 1))
        head.setStyleSheet("font-weight:600;color:#F0F0F0;")
        v.addWidget(head)
        te = QTextEdit()
        te.setPlainText(text)
        if mono:
            te.setStyleSheet(
                "background:#141414;color:#DDD;border:1px solid #2A2A2A;"
                "font-family:'JetBrains Mono','Consolas',monospace;"
                "font-size:12px;padding:8px;")
        v.addWidget(te, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        b = QPushButton("Close")
        b.clicked.connect(dlg.accept)
        row.addWidget(b)
        v.addLayout(row)
        if not hasattr(self, "_media_dialogs"):
            self._media_dialogs = []
        self._media_dialogs.append(dlg)
        dlg.show()
        dlg.raise_()

    def _show_html_dialog(self, nid, info):
        from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                                     QTextBrowser, QPushButton)
        text = info.get("text", "")
        title = info.get("title") or nid
        h = int(info.get("height", 400))
        dlg = QDialog(self)
        dlg.setWindowTitle("HTML \u2014 %s" % title)
        dlg.resize(880, max(300, h))
        dlg.setStyleSheet(
            "QDialog{background:#202020;}"
            "QTextBrowser{background:#141414;color:#DDD;"
            " border:1px solid #2A2A2A;padding:8px;}"
            "QPushButton{background:#3C3C3C;color:#EEE;"
            " border:1px solid #555;padding:5px 14px;border-radius:3px;}")
        v = QVBoxLayout(dlg)
        v.setContentsMargins(12, 12, 12, 12)
        te = QTextBrowser()
        kind = info.get("kind", "html")
        if kind == "markdown":
            try:
                te.setMarkdown(text)
            except Exception:
                te.setHtml(text)
        else:
            te.setHtml(text)
        v.addWidget(te, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        b = QPushButton("Close")
        b.clicked.connect(dlg.accept)
        row.addWidget(b)
        v.addLayout(row)
        if not hasattr(self, "_media_dialogs"):
            self._media_dialogs = []
        self._media_dialogs.append(dlg)
        dlg.show()
        dlg.raise_()

    def _show_image_dialog(self, nid, path):
        from PyQt5.QtGui import QPixmap
        parent = _find_main_window(self) or self
        dlg = QDialog(parent)
        dlg.setWindowTitle("Image \u2014 %s" % nid)
        dlg.setStyleSheet("QDialog{background:#202020;}")
        v = QVBoxLayout(dlg)
        v.setContentsMargins(8, 8, 8, 8)
        pix = QPixmap(path)
        lbl = QLabel()
        if pix.width() > 900 or pix.height() > 640:
            pix = pix.scaled(900, 640, Qt.KeepAspectRatio,
                             Qt.SmoothTransformation)
        lbl.setPixmap(pix)
        lbl.setAlignment(Qt.AlignCenter)
        v.addWidget(lbl, 1)
        info = QLabel(os.path.basename(path))
        info.setStyleSheet("color:#8A8A8A;font-size:11px;")
        v.addWidget(info)
        row = QHBoxLayout()
        row.addStretch(1)
        btn = QPushButton("Close")
        btn.clicked.connect(dlg.accept)
        row.addWidget(btn)
        v.addLayout(row)
        dlg.resize(min(pix.width() + 32, 960),
                   min(pix.height() + 96, 760))
        if not hasattr(self, "_media_dialogs"):
            self._media_dialogs = []
        self._media_dialogs.append(dlg)
        dlg.show()
        dlg.raise_()

    def _show_media_player(self, nid, path, kind):
        try:
            from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent
            from PyQt5.QtMultimediaWidgets import QVideoWidget
            from PyQt5.QtCore import QUrl
        except Exception as ex:
            self.report("QtMultimedia unavailable: %s" % ex,
                        "warning", 4000)
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("%s \u2014 %s"
                          % (kind.capitalize(), nid))
        dlg.setStyleSheet("QDialog{background:#202020;}")
        v = QVBoxLayout(dlg)
        v.setContentsMargins(8, 8, 8, 8)
        if kind == "video":
            widget = QVideoWidget()
            v.addWidget(widget, 1)
            player = QMediaPlayer(None, QMediaPlayer.VideoSurface)
            player.setVideoOutput(widget)
        else:
            lbl = QLabel(os.path.basename(path))
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("color:#DDD;font-size:14px;")
            v.addWidget(lbl, 1)
            player = QMediaPlayer()
        player.setMedia(QMediaContent(QUrl.fromLocalFile(path)))
        row = QHBoxLayout()
        btn_play  = QPushButton("Play")
        btn_pause = QPushButton("Pause")
        btn_stop  = QPushButton("Stop")
        row.addWidget(btn_play)
        row.addWidget(btn_pause)
        row.addWidget(btn_stop)
        row.addStretch(1)
        v.addLayout(row)
        btn_play.clicked.connect(player.play)
        btn_pause.clicked.connect(player.pause)
        btn_stop.clicked.connect(player.stop)
        player.play()
        if not hasattr(self, "_media_dialogs"):
            self._media_dialogs = []
        self._media_dialogs.append((dlg, player))
        dlg.resize(800, 600 if kind == "video" else 200)
        dlg.show()
        dlg.raise_()

    def _on_run_finished(self, code, err):
        self._run_thread = None
        self._run_paused = False
        if getattr(self, "_act_pause", None) is not None:
            self._act_pause.setText("Pause")
        if os.path.exists(_PAUSE_FLAG_FILE):
            try:
                os.remove(_PAUSE_FLAG_FILE)
            except Exception:
                pass
        if err:
            self._run_log.append("subprocess error: " + err)
            self.report("Run error: %s" % err, "error", 5000)
            self._show_run_dialog()
            return
        if code != 0:
            self._run_log.append("generated.py exited with code %d" % code)
            self.report("generated.py exited with code %d" % code,
                        "warning", 5000)
            if not self._run_error_seen:
                self._show_run_dialog()
            return
        self.report("generated.py finished cleanly", "success", 4000)

    def _toggle_pause(self):
        """Create or remove the pause flag file.

        The generated subprocess checks the flag at the start of every
        node, so pressing Pause halts the run cleanly between nodes
        rather than mid-operation.
        """
        if getattr(self, "_run_thread", None) is None:
            return
        paused = not getattr(self, "_run_paused", False)
        try:
            if paused:
                with open(_PAUSE_FLAG_FILE, "w") as f:
                    f.write("paused\n")
                self._run_paused = True
                if getattr(self, "_act_pause", None) is not None:
                    self._act_pause.setText("Resume")
                self.report("Paused — will stop before next node",
                            "warning", 3000)
            else:
                if os.path.exists(_PAUSE_FLAG_FILE):
                    os.remove(_PAUSE_FLAG_FILE)
                self._run_paused = False
                if getattr(self, "_act_pause", None) is not None:
                    self._act_pause.setText("Pause")
                self.report("Resumed", "success", 2000)
        except Exception as ex:
            print("[pause] failed:", ex)

    def _stop_run(self):
        """Terminate the running subprocess and clear the pause flag."""
        if getattr(self, "_run_thread", None) is None:
            return
        try:
            self._run_thread.kill()
        except Exception as ex:
            print("[stop] kill failed:", ex)
        if os.path.exists(_PAUSE_FLAG_FILE):
            try:
                os.remove(_PAUSE_FLAG_FILE)
            except Exception:
                pass
        self._run_paused = False
        if getattr(self, "_act_pause", None) is not None:
            self._act_pause.setText("Pause")
        self.report("Stopped", "warning", 2500)

    def _generate_code(self):
        import re as _re
        import keyword as _kw

        nodes = [i for i in self.scene.items() if isinstance(i, Node)]
        if not nodes:
            return "# (empty graph)\n"
        edges = [i for i in self.scene.items() if isinstance(i, Edge)]

        used = set()
        var_of = {}

        def _uniq(base):
            b = _re.sub(r"[^0-9A-Za-z_]", "_", str(base or "n")).strip("_") or "n"
            if b[0].isdigit():
                b = "_" + b
            if _kw.iskeyword(b):
                b = b + "_"
            if b not in used:
                used.add(b)
                return b
            i = 2
            while ("%s_%d" % (b, i)) in used:
                i += 1
            v = "%s_%d" % (b, i)
            used.add(v)
            return v

        def _tpl(n):
            return find_template(n.metadata.get("template", "")) or {}

        def _kind(n):
            return n.metadata.get("kind") or _tpl(n).get("kind") or "call"


        def _cat(n):
            t = _tpl(n)
            for c, ts in NODE_TEMPLATES:
                if t in ts:
                    return c
            return ""

        def _lit(s):
            if s is None or s.value is None:
                return None
            v = str(s.value).strip()
            if not v:
                return "''"

            # Human-friendly forms for the *args and **kwargs sockets
            # produced by create.py for functions like hotkey(*args,
            # **kwargs) or nn.Sequential(*args).
            nm = getattr(s, "name", "") or ""
            if nm.startswith("**"):
                # `interval=0.2, timeout=5` -> `{'interval': 0.2, 'timeout': 5}`
                if not v.startswith("{"):
                    pairs = []
                    for piece in v.split(","):
                        piece = piece.strip()
                        if not piece or "=" not in piece:
                            continue
                        k, val = piece.split("=", 1)
                        pairs.append("%s: %s"
                                     % (k.strip(), val.strip()))
                    v = "{" + ", ".join(pairs) + "}"
            elif nm.startswith("*"):
                # `ctrl, c` or `1, 2, 3` -> `['ctrl', 'c']`
                if not v.startswith(("[", "(")):
                    v = "[" + v + "]"
            if v in ("True", "False", "None"):
                return v
            if v[0].isdigit() or v[0] in "-+.[({":
                return v
            if v.endswith((")", "]", "}")):
                return v
            # Already a quoted string: keep as-is.
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                return v
            # String-typed sockets always become string literals.
            stype = getattr(s, "socket_type", "any")
            if stype == "string":
                return repr(v)
            # Bare dotted identifier on an `any` socket: treat it as a
            # variable reference so `n * n` does not become `'n' * 'n'`.
            # Python keywords and non-identifiers fall through and get quoted.
            if (_re.match(r"^[A-Za-z_][A-Za-z0-9_]*"
                          r"(\.[A-Za-z_][A-Za-z0-9_]*)*$", v)
                    and not _kw.iskeyword(v)):
                return v
            return repr(v)


        def _binds(n):
            out, miss = {}, []
            for inp in n.inputs:
                if inp.socket_type == "path":
                    continue
                if inp.name in ("Path In", "Path Out", "Next", "Prev"):
                    continue
                # Accept plain identifiers and splat names (`*args`,
                # `**kwargs`) so the create.py-generated synthetic
                # inputs are not filtered out.
                if not _re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", inp.name) \
                        and not _re.match(
                            r"^\*{1,2}[A-Za-z_][A-Za-z0-9_]*$", inp.name):
                    continue
                if inp.connections:
                    src = inp.connections[0].start_socket
                    if src is None:
                        continue
                    sv = var_of.get(id(src.node), "None")
                    outs = [o for o in src.node.outputs
                            if o.socket_type != "path"
                            and o.name not in ("Path In", "Path Out", "Next", "Prev")]
                    out[inp.name] = sv if len(outs) <= 1 else "%s.%s" % (sv, src.name)
                else:
                    lit = _lit(inp)
                    if lit is not None:
                        out[inp.name] = lit
                    else:
                        miss.append(inp.name)
            return out, miss

        def _prefix(cat, qual):
            if qual:
                parts = qual.split(".")
                if len(parts) >= 2:
                    m = ".".join(parts[:-1])
                    if m in ("nn", "torch.nn"):
                        return "nn", "import torch.nn as nn"
                    if m in ("F", "torch.nn.functional"):
                        return "F", "import torch.nn.functional as F"
                    if m in ("optim", "torch.optim"):
                        return "optim", "import torch.optim as optim"
                    if m == "torch":
                        return "torch", "import torch"
                    if m.startswith("torch."):
                        return m, "import " + m
                    return m, "import " + m
                return "", ""
            c = (cat or "").strip("/")
            if c.startswith("Torch/nn/functional"):
                return "F", "import torch.nn.functional as F"
            if c.startswith("Torch/nn"):
                return "nn", "import torch.nn as nn"
            if c.startswith("Torch/optim"):
                return "optim", "import torch.optim as optim"
            if c.startswith("Torch"):
                r = c[5:].strip("/").replace("/", ".")
                if r:
                    return "torch." + r, "import torch." + r
                return "torch", "import torch"
            if c.startswith("Built-ins"):
                r = c[9:].strip("/").replace("/", ".")
                if r:
                    return r, "import " + r
                return "", ""
            # Fallback for libraries passed with `create.py -l NAME`:
            # the root segment of the category is the top-level module,
            # and we import it directly.  So a node with category
            # "pyautogui" gets `import pyautogui` and its call sites
            # become pyautogui.<name>(...).
            root = c.split("/")[0].strip()
            if root and _re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", root):
                return root, "import " + root
            return "", ""

        for n in nodes:
            k = _kind(n)
            if k in ("def", "async_def", "class_"):
                ns = n.socket("Name", is_input=True)
                raw = (ns.value if ns else None) or n.title
                base = _re.sub(r"[^0-9A-Za-z_]", "_",
                               str(raw).strip("'\"")).strip("_") or "x"
                var_of[id(n)] = _uniq(base)
            else:
                var_of[id(n)] = _uniq(n.metadata.get("id") or n.title)

        imports = set()

        def emit_leaf(n, indent):
            pad = "    " * indent
            kind = _kind(n)
            var = var_of[id(n)]
            tpl = _tpl(n)
            cat = n.metadata.get("category") or _cat(n)
            B, miss = _binds(n)
            L = []

            # ---- literal containers ---- #
            if kind == "literal_list":
                vals = []
                for inp in n.inputs:
                    if inp.socket_type == "path":
                        continue
                    if inp.name in ("Path In", "Path Out", "Next", "Prev"):
                        continue
                    vals.append(B.get(inp.name, "None"))
                L.append("%s%s = [%s]" % (pad, var, ", ".join(vals)))
                return L, miss

            if kind == "literal_tuple":
                vals = []
                for inp in n.inputs:
                    if inp.socket_type == "path":
                        continue
                    if inp.name in ("Path In", "Path Out", "Next", "Prev"):
                        continue
                    vals.append(B.get(inp.name, "None"))
                if not vals:
                    L.append("%s%s = ()" % (pad, var))
                elif len(vals) == 1:
                    L.append("%s%s = (%s,)" % (pad, var, vals[0]))
                else:
                    L.append("%s%s = (%s)" % (pad, var, ", ".join(vals)))
                return L, miss

            if kind == "literal_set":
                vals = []
                for inp in n.inputs:
                    if inp.socket_type == "path":
                        continue
                    if inp.name in ("Path In", "Path Out", "Next", "Prev"):
                        continue
                    vals.append(B.get(inp.name, "None"))
                if not vals:
                    L.append("%s%s = set()" % (pad, var))
                else:
                    L.append("%s%s = {%s}" % (pad, var, ", ".join(vals)))
                return L, miss

            if kind == "literal_dict":
                items = [inp for inp in n.inputs
                         if inp.socket_type != "path"
                         and inp.name not in ("Path In", "Path Out",
                                              "Next", "Prev")]
                pairs = []
                for i in range(0, len(items) - 1, 2):
                    k = B.get(items[i].name, "None")
                    v = B.get(items[i + 1].name, "None")
                    pairs.append("%s: %s" % (k, v))
                L.append("%s%s = {%s}" % (pad, var, ", ".join(pairs)))
                return L, miss

            # ---- JSON module imports ---- #
            if kind == "json_import":
                mod = str(B.get("Module") or "'app'").strip("'\"")
                imports.add("import " + mod)
                L.append("%s%s = %s" % (pad, var, mod))
                return L, miss

            if kind == "json_from_import":
                mod = str(B.get("Module") or "'app'").strip("'\"")
                nm  = str(B.get("Name")   or "'x'").strip("'\"")
                imports.add("from " + mod + " import " + nm)
                L.append("%s%s = %s" % (pad, var, nm))
                return L, miss

            if kind in ("call", "value"):
                # Start / End and anything else under the Flow category
                # are graph anchors, not real calls.  Emit a plain None
                # and skip import resolution entirely.
                _cat_str = (n.metadata.get("category")
                            or n.metadata.get("template") or "")
                if (str(_cat_str).strip() == "Flow"
                        or n.title in ("Start", "End")):
                    L.append("%s%s = None" % (pad, var))
                    return L, miss
                call_t = n.metadata.get("call") or tpl.get("call")
                qual = n.metadata.get("qualname") or tpl.get("qualname")
                imp = n.metadata.get("import_module") or tpl.get("import_module")
                if imp:
                    imports.add("import " + imp)
                    L.append("%s%s = %s" % (pad, var, imp))
                    return L, miss
                if call_t:
                    expr = call_t
                    for k, v in B.items():
                        expr = expr.replace("{" + k + "}", v)
                    m = _re.match(r"\s*(torch\.nn\.functional|torch\.nn|"
                                  r"torch\.optim|torch|[A-Za-z_][\w.]*)\.", expr)
                    if m:
                        t = m.group(1)
                        if t == "torch":
                            imports.add("import torch")
                        elif t == "torch.nn":
                            imports.add("import torch.nn as nn")
                        elif t == "torch.nn.functional":
                            imports.add("import torch.nn.functional as F")
                        elif t == "torch.optim":
                            imports.add("import torch.optim as optim")
                    L.append("%s%s = %s" % (pad, var, expr))
                    return L, miss
                fname = n.title.replace(" ", "_").replace("/", "_")
                pre, imp2 = _prefix(cat, qual)
                if imp2:
                    imports.add(imp2)
                builtin = bool(qual) and "." not in qual
                if qual:
                    ps = qual.split(".")
                    if len(ps) >= 2:
                        pre = ".".join(ps[:-1])
                        fname = ps[-1]
                    else:
                        fname = qual
                        pre = ""
                if builtin:
                    argstr = ", ".join(B.values())
                    L.append("%s%s = %s(%s)" % (pad, var, fname, argstr))
                    return L, miss
                if not pre:
                    L.append("%s%s = None" % (pad, var))
                    return L, miss
                is_mod = qual and (qual.startswith("nn.") or qual.startswith("torch.nn."))
                is_loss = qual and ("Loss" in qual or "Criterion" in qual)
                fa, fas, pos, kw = None, [], [], []
                if is_loss:
                    for k, v in B.items():
                        (pos if k == "Tensor" else fas).append(v)
                else:
                    # Split the bound inputs into four classes:
                    #   "auto" sentinel        -> drop entirely
                    #   "*name" (splat args)   -> emit as *(...)
                    #   "**name" (splat kwargs)-> emit as **(...)
                    #   everything else        -> name=value (or positional)
                    items = [(k, v) for (k, v) in B.items()
                             if not _is_auto_sentinel(v)]
                    star_args = None
                    star_kwargs = None
                    regular = []
                    for (k, v) in items:
                        if k.startswith("**"):
                            star_kwargs = v
                        elif k.startswith("*"):
                            star_args = v
                        else:
                            regular.append((k, v))
                    first_is_splat = (
                        bool(regular)
                        and _looks_positional_splat(regular[0][1]))
                    for (k, v) in regular:
                        if is_mod and k == "Tensor":
                            fa = v
                        elif first_is_splat:
                            pos.append(v)
                        else:
                            kw.append("%s=%s" % (k, v))
                    if star_args is not None:
                        pos.append("*(" + star_args + ")")
                    if star_kwargs is not None:
                        kw.append("**(" + star_kwargs + ")")
                argstr = ", ".join(pos + kw)
                # If we dropped an "auto" input and the class has a Lazy variant,
                # swap to the variant that infers the missing parameter at runtime.
                if pre in ("nn", "torch.nn"):
                    _had_auto = any(_is_auto_sentinel(v) for v in B.values())
                    if _had_auto and fname in _LAZY_VARIANTS:
                        fname = _LAZY_VARIANTS[fname]
                L.append("%s%s = %s.%s(%s)" % (pad, var, pre, fname, argstr))
                if fa is not None:
                    L.append("%s%s = %s(%s)" % (pad, var, var, fa))
                elif fas:
                    L.append("%s%s = %s(%s)" % (pad, var, var, ", ".join(fas)))
                return L, miss

            if kind == "get":
                ns = n.socket("Name", is_input=True)
                if ns is not None and not ns.connections and ns.value:
                    name = str(ns.value).strip().strip("'\"")
                    L.append("%s%s = %s" % (pad, var, name))
                else:
                    L.append("%s%s = globals()[str(%s)]" % (pad, var, B.get("Name") or "'x'"))
                return L, miss
            if kind == "set":
                ns = n.socket("Name", is_input=True)
                val = B.get("Value") or "None"
                if ns is not None and not ns.connections and ns.value:
                    name = str(ns.value).strip().strip("'\"")
                    L.append("%s%s = %s" % (pad, name, val))
                else:
                    L.append("%sglobals()[str(%s)] = %s"
                             % (pad, B.get("Name") or "'x'", val))
                L.append("%s%s = None" % (pad, var))
                return L, miss
            if kind == "pyui_json":
                _p = B.get("Path") or "'graph.json'"
                imports.add("import json")
                L.append("%s%s = json.load(open(%s, 'r', "
                         "encoding='utf-8'))" % (pad, var, _p))
                return L, miss

            if kind == "io":
                _nid = _safe_nid(n)
                L.append("%s%s = _ask(%r, %s)"
                         % (pad, var, _nid, B.get("Prompt") or "''"))
                return L, miss

            if kind == "file_open":
                _p = B.get("Path") or "'data.txt'"
                _m = B.get("Mode") or "'r'"
                L.append("%s%s = open(%s, %s)" % (pad, var, _p, _m))
                return L, miss
            if kind == "file_read":
                _p = B.get("Path") or "'data.txt'"
                L.append("%swith open(%s, 'r') as _f:" % (pad, _p))
                L.append("%s    %s = _f.read()" % (pad, var))
                return L, miss
            if kind == "file_write":
                _p = B.get("Path") or "'out.txt'"
                _c = B.get("Content") or "''"
                L.append("%swith open(%s, 'w') as _f:" % (pad, _p))
                L.append("%s    _f.write(%s)" % (pad, _c))
                L.append("%s%s = None" % (pad, var))
                return L, miss
            if kind == "preview_image":
                _nid = _safe_nid(n)
                L.append("%s%s = runtime.preview_image(" % (pad, var))
                L.append("%s    %r," % (pad, _nid))
                L.append("%s    source=%s," % (pad, B.get("Source") or "None"))
                L.append("%s    title=%s," % (pad, B.get("Title") or "''"))
                L.append("%s    scale=%s," % (pad, B.get("Scale") or "1.0"))
                L.append("%s    width=%s," % (pad, B.get("Width") or "0"))
                L.append("%s    height=%s," % (pad, B.get("Height") or "0"))
                L.append("%s    grid=%s," % (pad, B.get("Grid") or "False"))
                L.append("%s    normalize=%s)" % (pad, B.get("Normalize") or "True"))
                return L, miss
            if kind == "preview_audio":
                _nid = _safe_nid(n)
                L.append("%s%s = runtime.preview_audio(" % (pad, var))
                L.append("%s    %r," % (pad, _nid))
                L.append("%s    source=%s," % (pad, B.get("Source") or "None"))
                L.append("%s    title=%s," % (pad, B.get("Title") or "''"))
                L.append("%s    autoplay=%s," % (pad, B.get("Autoplay") or "True"))
                L.append("%s    loop=%s," % (pad, B.get("Loop") or "False"))
                L.append("%s    volume=%s," % (pad, B.get("Volume") or "1.0"))
                L.append("%s    start=%s)" % (pad, B.get("Start") or "0.0"))
                return L, miss
            if kind == "preview_video":
                _nid = _safe_nid(n)
                L.append("%s%s = runtime.preview_video(" % (pad, var))
                L.append("%s    %r," % (pad, _nid))
                L.append("%s    source=%s," % (pad, B.get("Source") or "None"))
                L.append("%s    title=%s," % (pad, B.get("Title") or "''"))
                L.append("%s    autoplay=%s," % (pad, B.get("Autoplay") or "True"))
                L.append("%s    loop=%s," % (pad, B.get("Loop") or "False"))
                L.append("%s    start=%s," % (pad, B.get("Start") or "0.0"))
                L.append("%s    end=%s," % (pad, B.get("End") or "0.0"))
                L.append("%s    muted=%s)" % (pad, B.get("Muted") or "False"))
                return L, miss
            if kind == "preview_folder":
                _nid = _safe_nid(n)
                L.append("%s%s = runtime.preview_folder(" % (pad, var))
                L.append("%s    %r," % (pad, _nid))
                L.append("%s    source=%s," % (pad, B.get("Source") or "'.'"))
                L.append("%s    filter=%s," % (pad, B.get("Filter") or "'*'"))
                L.append("%s    recursive=%s," % (pad, B.get("Recursive") or "False"))
                L.append("%s    columns=%s," % (pad, B.get("Columns") or "0"))
                L.append("%s    thumb_size=%s," % (pad, B.get("ThumbSize") or "128"))
                L.append("%s    sort=%s)" % (pad, B.get("Sort") or "'name'"))
                return L, miss
            if kind == "preview_table":
                _nid = _safe_nid(n)
                L.append("%s%s = runtime.preview_table(" % (pad, var))
                L.append("%s    %r," % (pad, _nid))
                L.append("%s    source=%s," % (pad, B.get("Source") or "None"))
                L.append("%s    title=%s," % (pad, B.get("Title") or "''"))
                L.append("%s    max_rows=%s," % (pad, B.get("MaxRows") or "100"))
                L.append("%s    max_cols=%s," % (pad, B.get("MaxCols") or "20"))
                L.append("%s    editable=%s)" % (pad, B.get("Editable") or "False"))
                return L, miss
            if kind == "preview_text":
                _nid = _safe_nid(n)
                L.append("%s%s = runtime.preview_text(" % (pad, var))
                L.append("%s    %r," % (pad, _nid))
                L.append("%s    source=%s," % (pad, B.get("Source") or "None"))
                L.append("%s    title=%s," % (pad, B.get("Title") or "''"))
                L.append("%s    max_lines=%s," % (pad, B.get("MaxLines") or "500"))
                L.append("%s    wrap=%s," % (pad, B.get("Wrap") or "True"))
                L.append("%s    monospace=%s," % (pad, B.get("Monospace") or "True"))
                L.append("%s    highlight=%s)" % (pad, B.get("Highlight") or "''"))
                return L, miss
            if kind == "preview_json":
                _nid = _safe_nid(n)
                L.append("%s%s = runtime.preview_json(" % (pad, var))
                L.append("%s    %r," % (pad, _nid))
                L.append("%s    source=%s," % (pad, B.get("Source") or "None"))
                L.append("%s    title=%s," % (pad, B.get("Title") or "''"))
                L.append("%s    indent=%s," % (pad, B.get("Indent") or "2"))
                L.append("%s    sort=%s," % (pad, B.get("Sort") or "False"))
                L.append("%s    max_depth=%s)" % (pad, B.get("MaxDepth") or "20"))
                return L, miss
            if kind == "preview_plot":
                _nid = _safe_nid(n)
                L.append("%s%s = runtime.preview_plot(" % (pad, var))
                L.append("%s    %r," % (pad, _nid))
                L.append("%s    x=%s," % (pad, B.get("X") or "None"))
                L.append("%s    y=%s," % (pad, B.get("Y") or "None"))
                L.append("%s    kind=%s," % (pad, B.get("Kind") or "'line'"))
                L.append("%s    title=%s," % (pad, B.get("Title") or "''"))
                L.append("%s    xlabel=%s," % (pad, B.get("Xlabel") or "''"))
                L.append("%s    ylabel=%s," % (pad, B.get("Ylabel") or "''"))
                L.append("%s    color=%s," % (pad, B.get("Color") or "'#E08C4A'"))
                L.append("%s    figsize_w=%s," % (pad, B.get("FigsizeW") or "8.0"))
                L.append("%s    figsize_h=%s)" % (pad, B.get("FigsizeH") or "5.0"))
                return L, miss
            if kind == "preview_html":
                _nid = _safe_nid(n)
                L.append("%s%s = runtime.preview_html(" % (pad, var))
                L.append("%s    %r," % (pad, _nid))
                L.append("%s    source=%s," % (pad, B.get("Source") or "''"))
                L.append("%s    title=%s," % (pad, B.get("Title") or "''"))
                L.append("%s    height=%s)" % (pad, B.get("Height") or "400"))
                return L, miss
            if kind == "preview_markdown":
                _nid = _safe_nid(n)
                L.append("%s%s = runtime.preview_markdown(" % (pad, var))
                L.append("%s    %r," % (pad, _nid))
                L.append("%s    source=%s," % (pad, B.get("Source") or "''"))
                L.append("%s    title=%s," % (pad, B.get("Title") or "''"))
                L.append("%s    height=%s)" % (pad, B.get("Height") or "400"))
                return L, miss

            if kind in ("image_viewer", "audio_player", "video_player"):
                _nid = _safe_nid(n)
                _k = {"image_viewer": "image",
                      "audio_player": "audio",
                      "video_player": "video"}[kind]
                _src = B.get("Source") or "None"
                L.append("%sruntime.media_show(%r, %r, %s)"
                         % (pad, _nid, _k, _src))
                L.append("%s%s = None" % (pad, var))
                return L, miss
            if kind == "call_by_name":
                name_lit = B.get("Name") or "'f'"
                args_lit = B.get("Args") or "[]"
                raw = str(name_lit).strip()
                # Determine if the user typed a name (quoted in the
                # generated code) or wired an expression.
                if (len(raw) >= 2
                        and raw[0] == raw[-1]
                        and raw[0] in ("'", '"')):
                    bare = raw[1:-1]
                    quoted = True
                else:
                    bare = raw
                    quoted = False
                if quoted:
                    # Dotted path like math.sqrt: emit directly.
                    if ("." in bare
                            and _re.match(
                                r"^[A-Za-z_][A-Za-z0-9_]*"
                                r"(\.[A-Za-z_][A-Za-z0-9_]*)*$",
                                bare)):
                        L.append("%s%s = %s(*(%s))"
                                 % (pad, var, bare, args_lit))
                    else:
                        L.append("%s%s = globals()[str(%s)](*(%s))"
                                 % (pad, var, name_lit, args_lit))
                else:
                    # Wired expression: call it directly.
                    L.append("%s%s = (%s)(*(%s))"
                             % (pad, var, bare, args_lit))
                return L, miss
            if kind == "return":
                L.append("%sreturn %s" % (pad, B.get("Value") or "None"))
                L.append("%s%s = None" % (pad, var))
                return L, miss

            if kind == "self_set":
                _attr_raw = B.get("Name") or "'x'"
                _attr = str(_attr_raw).strip("\"'")
                _val = B.get("Value") or "None"
                L.append("%sself.%s = %s" % (pad, _attr, _val))
                L.append("%s%s = None" % (pad, var))
                return L, miss
            if kind == "self_get":
                _attr_raw = B.get("Name") or "'x'"
                _attr = str(_attr_raw).strip("\"'")
                L.append("%s%s = self.%s" % (pad, var, _attr))
                return L, miss
            if kind == "self_method":
                _meth = str(B.get("Method") or "'m'").strip("\"'")
                _args = B.get("Args") or "[]"
                _kw = B.get("Kwargs")
                if _kw and _kw.strip() not in ("{}", ""):
                    L.append("%s%s = self.%s(*(%s), **(%s))"
                             % (pad, var, _meth, _args, _kw))
                else:
                    L.append("%s%s = self.%s(*(%s))"
                             % (pad, var, _meth, _args))
                return L, miss
            if kind == "import_stmt":
                m = str(B.get("Module") or "'os'").strip("'\"")
                L.append("%simport %s" % (pad, m))
                L.append("%s%s = None" % (pad, var))
                return L, miss
            if kind == "from_import":
                m = str(B.get("Module") or "'os'").strip("'\"")
                nm = str(B.get("Name") or "'path'").strip("'\"")
                L.append("%sfrom %s import %s" % (pad, m, nm))
                L.append("%s%s = %s" % (pad, var, nm))
                return L, miss
            if kind == "del":
                L.append("%sdel %s" % (pad, B.get("Target") or "'x'"))
                L.append("%s%s = None" % (pad, var))
                return L, miss
            if kind == "break":
                L.append("%sbreak" % pad)
                L.append("%s%s = None" % (pad, var))
                return L, miss
            if kind == "continue":
                L.append("%scontinue" % pad)
                L.append("%s%s = None" % (pad, var))
                return L, miss
            if kind == "pass_stmt":
                L.append("%spass" % pad)
                L.append("%s%s = None" % (pad, var))
                return L, miss
            if kind == "raise":
                L.append("%sraise %s" % (pad, B.get("Exception") or "Exception()"))
                L.append("%s%s = None" % (pad, var))
                return L, miss
            if kind == "assert":
                L.append("%sassert %s, %s"
                         % (pad, B.get("Condition") or "True",
                            B.get("Message") or "''"))
                L.append("%s%s = None" % (pad, var))
                return L, miss
            if kind == "global_":
                L.append("%sglobal %s"
                         % (pad, str(B.get("Name") or "'x'").strip("'\"")))
                L.append("%s%s = None" % (pad, var))
                return L, miss
            if kind == "nonlocal_":
                L.append("%snonlocal %s"
                         % (pad, str(B.get("Name") or "'x'").strip("'\"")))
                L.append("%s%s = None" % (pad, var))
                return L, miss

            if kind == "binop":
                op = str(B.get("Op") or "'+'").strip("'\"")
                L.append("%s%s = (%s %s %s)"
                         % (pad, var, B.get("A") or "0", op, B.get("B") or "0"))
                return L, miss
            if kind == "unaryop":
                op = str(B.get("Op") or "'-'").strip("'\"")
                L.append("%s%s = (%s%s)" % (pad, var, op, B.get("Value") or "0"))
                return L, miss
            if kind == "compare":
                op = str(B.get("Op") or "'=='").strip("'\"")
                L.append("%s%s = (%s %s %s)"
                         % (pad, var, B.get("A") or "0", op, B.get("B") or "0"))
                return L, miss
            if kind == "ternary":
                L.append("%s%s = (%s if %s else %s)"
                         % (pad, var, B.get("Then") or "None",
                            B.get("Condition") or "True",
                            B.get("Else") or "None"))
                return L, miss
            if kind == "fstring":
                L.append("%s%s = f%s" % (pad, var, B.get("Template") or "''"))
                return L, miss
            if kind == "method_call":
                obj = B.get("Object") or "None"
                meth = str(B.get("Method") or "'m'").strip("'\"")
                args_lit = B.get("Args") or "[]"
                kwargs_lit = B.get("Kwargs")
                if kwargs_lit and kwargs_lit.strip() not in ("{}", ""):
                    L.append("%s%s = %s.%s(*(%s), **(%s))"
                             % (pad, var, obj, meth, args_lit,
                                kwargs_lit))
                else:
                    L.append("%s%s = %s.%s(*(%s))"
                             % (pad, var, obj, meth, args_lit))
                return L, miss
            if kind == "attr_get":
                L.append("%s%s = getattr(%s, %s)"
                         % (pad, var, B.get("Object") or "None",
                            B.get("Attr") or "'x'"))
                return L, miss
            if kind == "attr_set":
                L.append("%ssetattr(%s, %s, %s)"
                         % (pad, B.get("Object") or "None",
                            B.get("Attr") or "'x'",
                            B.get("Value") or "None"))
                L.append("%s%s = None" % (pad, var))
                return L, miss
            if kind == "index":
                L.append("%s%s = %s[%s]"
                         % (pad, var, B.get("Object") or "[]",
                            B.get("Index") or "0"))
                return L, miss
            if kind == "index_set":
                L.append("%s%s[%s] = %s"
                         % (pad, B.get("Object") or "[]",
                            B.get("Index") or "0",
                            B.get("Value") or "None"))
                L.append("%s%s = None" % (pad, var))
                return L, miss
            if kind == "slice":
                L.append("%s%s = %s[%s:%s:%s]"
                         % (pad, var, B.get("Object") or "[]",
                            B.get("Start") or "None",
                            B.get("Stop") or "None",
                            B.get("Step") or "None"))
                return L, miss
            if kind == "comprehension_list":
                cond = B.get("Condition")
                if cond:
                    L.append("%s%s = [%s for %s in %s if %s]"
                             % (pad, var, B.get("Expr") or "x",
                                B.get("Var") or "x",
                                B.get("Iterable") or "[]", cond))
                else:
                    L.append("%s%s = [%s for %s in %s]"
                             % (pad, var, B.get("Expr") or "x",
                                B.get("Var") or "x",
                                B.get("Iterable") or "[]"))
                return L, miss
            if kind == "comprehension_dict":
                L.append("%s%s = {%s: %s for %s in %s}"
                         % (pad, var, B.get("Key") or "k",
                            B.get("Val") or "v",
                            B.get("Var") or "k",
                            B.get("Iterable") or "[]"))
                return L, miss
            if kind == "comprehension_set":
                L.append("%s%s = {%s for %s in %s}"
                         % (pad, var, B.get("Expr") or "x",
                            B.get("Var") or "x",
                            B.get("Iterable") or "[]"))
                return L, miss
            if kind == "generator":
                L.append("%s%s = (%s for %s in %s)"
                         % (pad, var, B.get("Expr") or "x",
                            B.get("Var") or "x",
                            B.get("Iterable") or "[]"))
                return L, miss
            if kind == "lambda":
                L.append("%s%s = (lambda %s: %s)"
                         % (pad, var,
                            str(B.get("Args") or "''").strip("'\""),
                            B.get("Body") or "None"))
                return L, miss
            if kind == "await":
                L.append("%s%s = await %s" % (pad, var, B.get("Expr") or "None"))
                return L, miss
            if kind == "yield_value":
                L.append("%s%s = (yield %s)" % (pad, var, B.get("Value") or "None"))
                return L, miss
            if kind == "expr_stmt":
                L.append("%s%s" % (pad, B.get("Expr") or "None"))
                L.append("%s%s = None" % (pad, var))
                return L, miss

            L.append("%s%s = None" % (pad, var))
            return L, miss

        def emit(n, indent):
            pad = "    " * indent
            kind = _kind(n)
            nid = _safe_nid(n)
            var = var_of[id(n)]
            B, _miss = _binds(n)
            L = []

            if kind == "do":
                L.append("%sruntime.begin(%r)" % (pad, nid))
                for c in _sorted_children(n):
                    L.extend(emit(c, indent))
                L.append("%sruntime.end(%r)" % (pad, nid))
                return L
            if kind == "if":
                return emit_if(n, indent, B)
            if kind == "for":
                v = str(B.get("Var") or "'i'").strip("'\"")
                it = B.get("Iterable") or "[]"
                L.append("%sruntime.begin(%r)" % (pad, nid))
                L.append("%sfor %s in %s:" % (pad, v, it))
                body = _sorted_children(n)
                if body:
                    for c in body:
                        L.extend(emit(c, indent + 1))
                else:
                    L.append("%s    pass" % pad)
                L.append("%sruntime.end(%r)" % (pad, nid))
                return L
            if kind == "while":
                c = B.get("Condition") or "False"
                L.append("%sruntime.begin(%r)" % (pad, nid))
                L.append("%swhile %s:" % (pad, c))
                body = _sorted_children(n)
                if body:
                    for ch in body:
                        L.extend(emit(ch, indent + 1))
                else:
                    L.append("%s    pass" % pad)
                L.append("%sruntime.end(%r)" % (pad, nid))
                return L
            if kind == "with":
                ctx = B.get("Context") or "None"
                a = B.get("As")
                hdr = "with %s" % ctx
                if a:
                    hdr += " as %s" % str(a).strip("'\"")
                L.append("%sruntime.begin(%r)" % (pad, nid))
                L.append("%s%s:" % (pad, hdr))
                body = _sorted_children(n)
                if body:
                    for c in body:
                        L.extend(emit(c, indent + 1))
                else:
                    L.append("%s    pass" % pad)
                L.append("%sruntime.end(%r)" % (pad, nid))
                return L
            if kind == "try":
                return emit_try(n, indent)
            if kind == "match":
                subj = B.get("Subject") or "None"
                L.append("%sruntime.begin(%r)" % (pad, nid))
                L.append("%smatch %s:" % (pad, subj))
                body = _sorted_children(n)
                any_case = False
                for c in body:
                    if _kind(c) == "case_marker":
                        any_case = True
                        cb = _binds(c)[0]
                        pat = cb.get("Pattern") or "_"
                        L.append("%s    case %s:" % (pad, pat))
                        kids = getattr(c, "blocks", [])
                        if kids:
                            for k in kids:
                                L.extend(emit(k, indent + 2))
                        else:
                            L.append("%s        pass" % pad)
                if not any_case:
                    L.append("%s    case _:" % pad)
                    L.append("%s        pass" % pad)
                L.append("%sruntime.end(%r)" % (pad, nid))
                return L
            if kind in ("def", "async_def", "class_"):
                L.append("%sruntime.begin(%r)" % (pad, nid))
                L.append("%s%s = None" % (pad, var))
                L.append("%sruntime.end(%r)" % (pad, nid))
                return L

            if kind in ("else_marker", "elif_marker", "except_marker",
                        "finally_marker", "case_marker"):
                return ["%s# orphan %s" % (pad, kind)]

            if kind == "return":
                _ret_name = "_ret_" + var
                L.append("%sruntime.begin(%r)" % (pad, nid))
                L.append("%s_cap = _OutCapture()" % pad)
                L.append("%stry:" % pad)
                L.append("%s    with _cap:" % pad)
                L.append("%s        %s = (%s)"
                         % (pad, _ret_name, B.get("Value") or "None"))
                L.append("%s    runtime.output(%r, %s)"
                         % (pad, nid, _ret_name))
                L.append("%s    runtime.stdout(%r, _cap.getvalue())"
                         % (pad, nid))
                L.append("%sexcept Exception as _e:" % pad)
                L.append("%s    runtime.stdout(%r, _cap.getvalue())"
                         % (pad, nid))
                L.append("%s    runtime.error(%r, _e)" % (pad, nid))
                L.append("%s    raise" % pad)
                L.append("%sfinally:" % pad)
                L.append("%s    runtime.end(%r)" % (pad, nid))
                L.append("%sreturn %s" % (pad, _ret_name))
                L.append("")
                return L
            L.append("%sruntime.begin(%r)" % (pad, nid))
            L.append("%s_cap = _OutCapture()" % pad)
            L.append("%stry:" % pad)
            L.append("%s    with _cap:" % pad)
            inner, _m = emit_leaf(n, indent + 2)
            L.extend(inner)
            L.append("%s    runtime.output(%r, %s)" % (pad, nid, var))
            L.append("%s    runtime.stdout(%r, _cap.getvalue())" % (pad, nid))
            L.append("%sexcept Exception as _e:" % pad)
            L.append("%s    runtime.stdout(%r, _cap.getvalue())" % (pad, nid))
            L.append("%s    runtime.error(%r, _e)" % (pad, nid))
            L.append("%s    raise" % pad)
            L.append("%sfinally:" % pad)
            L.append("%s    runtime.end(%r)" % (pad, nid))
            L.append("")
            return L

        def emit_if(n, indent, B):
            pad = "    " * indent
            nid = n.metadata.get("id") or n.title
            L = ["%sruntime.begin(%r)" % (pad, nid)]
            cond = B.get("Condition") or "False"
            body = list(_sorted_children(n))
            clauses = [("if %s:" % cond, [])]
            for c in body:
                k = _kind(c)
                if k == "elif_marker":
                    cb = _binds(c)[0]
                    clauses.append(("elif %s:" % (cb.get("Condition") or "False"),
                                    list(getattr(c, "blocks", []))))
                elif k == "else_marker":
                    clauses.append(("else:", list(getattr(c, "blocks", []))))
                else:
                    clauses[-1][1].append(c)
            for h, kids in clauses:
                L.append("%s%s" % (pad, h))
                if kids:
                    for k in kids:
                        L.extend(emit(k, indent + 1))
                else:
                    L.append("%s    pass" % pad)
            L.append("%sruntime.end(%r)" % (pad, nid))
            return L

        def emit_try(n, indent):
            pad = "    " * indent
            nid = n.metadata.get("id") or n.title
            L = ["%sruntime.begin(%r)" % (pad, nid), "%stry:" % pad]
            body = list(_sorted_children(n))
            try_part, clauses = [], []
            for c in body:
                k = _kind(c)
                if k == "except_marker":
                    cb = _binds(c)[0]
                    clauses.append(("except", cb.get("Exception") or "Exception",
                                    list(getattr(c, "blocks", []))))
                elif k == "else_marker":
                    clauses.append(("else", None, list(getattr(c, "blocks", []))))
                elif k == "finally_marker":
                    clauses.append(("finally", None, list(getattr(c, "blocks", []))))
                else:
                    try_part.append(c)
            if try_part:
                for c in try_part:
                    L.extend(emit(c, indent + 1))
            else:
                L.append("%s    pass" % pad)
            has_fin = False
            for kind, arg, kids in clauses:
                if kind == "except":
                    L.append("%sexcept %s as _e:" % (pad, arg))
                elif kind == "else":
                    L.append("%selse:" % pad)
                elif kind == "finally":
                    L.append("%sfinally:" % pad)
                    has_fin = True
                if kids:
                    for c in kids:
                        L.extend(emit(c, indent + 1))
                else:
                    L.append("%s    pass" % pad)
            if not clauses:
                L.append("%sexcept Exception as _e:" % pad)
                L.append("%s    pass" % pad)
            L.append("%sruntime.end(%r)" % (pad, nid))
            return L

        def emit_def(n, indent):
            pad = "    " * indent
            var = var_of[id(n)]
            is_async = (_kind(n) == "async_def")
            args_sock = n.socket("Args", is_input=True)
            args_str = str(args_sock.value if args_sock else "''").strip("'\"").strip()
            head = "async def" if is_async else "def"
            L = ["%s%s %s(%s):" % (pad, head, var, args_str)]
            body = _sorted_children(n)
            if body:
                for c in body:
                    L.extend(emit(c, indent + 1))
            else:
                L.append("%s    pass" % pad)
            return L

        def emit_class(n, indent):
            pad = "    " * indent
            var = var_of[id(n)]
            bs = n.socket("Bases", is_input=True)
            bs_str = str(bs.value if bs else "''").strip("'\"").strip()
            L = ["%sclass %s(%s):" % (pad, var, bs_str) if bs_str
                 else "%sclass %s:" % (pad, var)]
            body = _sorted_children(n)
            if body:
                for c in body:
                    L.extend(emit(c, indent + 1))
            else:
                L.append("%s    pass" % pad)
            return L

        hoisted = [n for n in nodes if _kind(n) in ("def", "async_def", "class_")]
        hid = {id(n) for n in hoisted}
        def collect(n, acc):
            for c in _sorted_children(n):
                acc.add(id(c))
                collect(c, acc)
        for h in hoisted:
            collect(h, hid)
        top = [n for n in nodes if id(n) not in hid]

        inc = {id(n): 0 for n in top}
        out = {id(n): [] for n in top}
        by = {id(n): n for n in top}
        for e in edges:
            if e.start_socket is None or e.end_socket is None:
                continue
            s, t = e.start_socket.node, e.end_socket.node
            if id(s) in inc and id(t) in inc:
                out[id(s)].append(t)
                inc[id(t)] += 1

        def pk(n):
            return (n.pos().y(), n.pos().x())

        ready = sorted([by[k] for k, c in inc.items() if c == 0], key=pk)
        ordered, seen = [], set()
        while ready:
            n = ready.pop(0)
            if id(n) in seen:
                continue
            seen.add(id(n))
            ordered.append(n)
            for m in out[id(n)]:
                inc[id(m)] -= 1
                if inc[id(m)] == 0:
                    ready.append(m)
            ready.sort(key=pk)

        header = [
            "# Auto-generated from the node graph.",
            "",
            "import io as _io",
            "import sys as _sys",
            "",
            "from helpers.Nodes.Nodes import runtime",
            "",
            "",
            "def _ask(node_id, prompt):",
            "    import sys, json",
            "    _out = getattr(sys, '__stdout__', None) or sys.stdout",
            "    _out.write('@@RT ask ' + str(node_id) + ' ' + json.dumps(str(prompt)) + '\\n')",
            "    _out.flush()",
            "    line = sys.stdin.readline()",
            "    if not line:",
            "        return ''",
            "    return line.rstrip('\\n')",
            "",
            "",
            "class _OutCapture:",
            "    def __init__(self):",
            "        self.buf = _io.StringIO()",
            "    def __enter__(self):",
            "        self._old = _sys.stdout",
            "        _sys.stdout = self.buf",
            "        return self",
            "    def __exit__(self, *a):",
            "        _sys.stdout = self._old",
            "        return False",
            "    def getvalue(self):",
            "        return self.buf.getvalue()",
            "",
            "",
        ]

        body = []
        for h in hoisted:
            if _kind(h) == "class_":
                body.extend(emit_class(h, 0))
            else:
                body.extend(emit_def(h, 0))
            body.append("")
        for n in ordered:
            body.extend(emit(n, 0))
            body.append("")

        for imp in sorted(imports):
            header.append(imp)
        return "\n".join(header + body) + "\n"


    def show_about(self):
        QMessageBox.about(
            self, "About",
            "<b>AI Node Editor</b><br>"
            "Blender-styled node graph built with PyQt5.<br><br>"
            "Shift+A  add node · Ctrl+D  duplicate · Del  delete<br>"
            "Middle-drag  pan · Wheel  zoom · Home  frame all<br>"
            "F1 — help pane · Ctrl+G generate · F5 run")


# --------------------------------------------------------------------------- #
#  Lifecycle manager                                                          #
# --------------------------------------------------------------------------- #

class _LifecycleManager:
    def __init__(self, api):
        self._api = api
        self._placed_fired  = weakref.WeakSet()
        self._connect_fired = weakref.WeakSet()
        self._evaluating    = False

    def reset(self):
        self._placed_fired  = weakref.WeakSet()
        self._connect_fired = weakref.WeakSet()

    def evaluate(self):
        if self._evaluating:
            return
        try:
            self._evaluating = True
            ch1 = self._fire_placed_hooks()
            ch2 = self._fire_connect_hooks()
            ch3 = self._recheck_requirements()
            if ch1 or ch2 or ch3:
                try:
                    self._api.window.library.refresh()
                except Exception:
                    pass
        except Exception as ex:
            print(f"[lifecycle] error: {ex}")
        finally:
            self._evaluating = False

    def _fire_placed_hooks(self):
        scene = self._api.window.scene
        changed = False
        for item in list(scene.items()):
            if not isinstance(item, Node):
                continue
            if item in self._placed_fired:
                continue
            self._placed_fired.add(item)
            tpl_name = item.metadata.get("template")
            if not tpl_name:
                continue
            tpl = find_template(tpl_name)
            if tpl is None:
                continue
            if self._run_hooks(tpl, "placed"):
                changed = True
        return changed

    def _fire_connect_hooks(self):
        scene = self._api.window.scene
        changed = False
        for item in list(scene.items()):
            if not isinstance(item, Edge):
                continue
            if item in self._connect_fired:
                continue
            self._connect_fired.add(item)
            for sock in (item.start_socket, item.end_socket):
                if sock is None:
                    continue
                tpl_name = sock.node.metadata.get("template")
                if not tpl_name:
                    continue
                tpl = find_template(tpl_name)
                if tpl is None:
                    continue
                if self._run_hooks(tpl, "connect"):
                    changed = True
        return changed

    def _run_hooks(self, tpl, event):
        lc = tpl.get("_lifecycle") or {}
        prefix = "on_" + event + "_"
        enable_names   = lc.get(prefix + "enable")     or []
        disable_names  = lc.get(prefix + "disable")    or []
        unreg_names    = lc.get(prefix + "unregister") or []
        register_items = lc.get(prefix + "register")   or []
        message        = lc.get(prefix + "message")
        changed = False
        if "*" in enable_names:
            for t in self._wildcard_targets():
                if not t.get("enabled", True):
                    t["enabled"] = True
                    t.setdefault("_lifecycle", {})["_user_enabled"] = True
                    changed = True
        if "*" in disable_names:
            for t in self._wildcard_targets():
                if t.get("enabled", True):
                    t["enabled"] = False
                    t.setdefault("_lifecycle", {})["_user_enabled"] = False
                    changed = True
        for name in enable_names:
            if name == "*":
                continue
            if self._set_enabled(name, True):
                changed = True
        for name in disable_names:
            if name == "*":
                continue
            if self._set_enabled(name, False):
                changed = True
        for name in unreg_names:
            try:
                self._api.register.node.remove(name)
                changed = True
            except Exception:
                pass
        for entry in register_items:
            if isinstance(entry, str):
                if self._set_enabled(entry, True):
                    changed = True
            elif isinstance(entry, dict):
                try:
                    self._api.register_node(**entry)
                    changed = True
                except Exception as ex:
                    print("[lifecycle] register failed: " + str(ex))
        if message:
            try:
                self._api.report.success(message)
            except Exception:
                pass
        return changed

    def _recheck_requirements(self):
        scene = self._api.window.scene
        placed_names = set()
        non_flow_placed = set()
        flow_names = set()
        for _cat, _tpls in NODE_TEMPLATES:
            if _cat == "Flow":
                for _t in _tpls:
                    flow_names.add(_t["name"])
        for item in scene.items():
            if isinstance(item, Node):
                name = item.metadata.get("template")
                if name:
                    placed_names.add(name)
                    if name not in flow_names:
                        non_flow_placed.add(name)
        edges = [i for i in scene.items() if isinstance(i, Edge)]
        changed = False
        for _cat, tpls in NODE_TEMPLATES:
            for tpl in tpls:
                lc = tpl.get("_lifecycle") or {}
                user_enabled = lc.get("_user_enabled", True)
                was          = tpl.get("enabled", True)
                met          = True
                req_placed = lc.get("requires_placed")
                if req_placed:
                    mode = lc.get("requires_placed_mode", "any")
                    def _check(name):
                        if name == "*":
                            return bool(non_flow_placed)
                        return name in placed_names
                    if mode == "all":
                        met = all(_check(n) for n in req_placed)
                    else:
                        met = any(_check(n) for n in req_placed)
                if met and lc.get("requires_connected"):
                    mode = lc.get("requires_connected_mode", "any")
                    matches = [self._edge_matches(spec, edges)
                               for spec in lc["requires_connected"]]
                    if mode == "all":
                        met = all(matches)
                    else:
                        met = any(matches)
                now = bool(user_enabled and met)
                if now != was:
                    tpl["enabled"] = now
                    changed = True
                    if now and lc.get("unlock_message"):
                        try:
                            self._api.report.success(lc["unlock_message"])
                        except Exception:
                            pass
        return changed

    def _edge_matches(self, spec, edges):
        if len(spec) == 2:
            fn, tn = spec
            fs = ts = None
        elif len(spec) == 4:
            fn, fs, tn, ts = spec
        else:
            return False
        for e in edges:
            if e.start_socket is None or e.end_socket is None:
                continue
            s_node = e.start_socket.node
            t_node = e.end_socket.node
            if s_node.metadata.get("template") != fn:
                continue
            if t_node.metadata.get("template") != tn:
                continue
            if fs and e.start_socket.name != fs:
                continue
            if ts and e.end_socket.name != ts:
                continue
            return True
        return False

    def _wildcard_targets(self):
        out = []
        for cat, tpls in NODE_TEMPLATES:
            if cat == "Flow":
                continue
            for t in tpls:
                out.append(t)
        return out

    def _set_enabled(self, name, state):
        tpl = find_template(name)
        if tpl is None:
            return False
        old = tpl.get("enabled", True)
        tpl["enabled"] = bool(state)
        tpl.setdefault("_lifecycle", {})["_user_enabled"] = bool(state)
        return old != tpl["enabled"]


# --------------------------------------------------------------------------- #
#  Node query API (reached via api.register.nodes)                            #
# --------------------------------------------------------------------------- #

class _NodesQueryAPI:
    def __init__(self, api):
        self._api = api

    def get_by_line(self, start=None, end=None, strict=False):
        api = self._api
        nodes = api.nodes()
        if not nodes:
            return []
        edges = api.edges()
        out_map = {id(n): [] for n in nodes}
        in_map  = {id(n): [] for n in nodes}
        for e in edges:
            if e.start_socket is None or e.end_socket is None:
                continue
            s = e.start_socket.node
            t = e.end_socket.node
            out_map[id(s)].append(t)
            in_map[id(t)].append(s)
        if start is None:
            starts = [n for n in nodes if not in_map[id(n)]] or [nodes[0]]
        else:
            s = api._resolve_node(start)
            starts = [s] if s is not None else []
        if end is None:
            ends = None
        else:
            e = api._resolve_node(end)
            ends = {id(e)} if e is not None else set()
        reach = set()
        stack = list(starts)
        while stack:
            n = stack.pop()
            if n is None or id(n) in reach:
                continue
            reach.add(id(n))
            for m in out_map[id(n)]:
                stack.append(m)
        if strict and ends:
            can_end = set()
            stack = [n for n in nodes if id(n) in ends]
            while stack:
                n = stack.pop()
                if id(n) in can_end:
                    continue
                can_end.add(id(n))
                for m in in_map[id(n)]:
                    stack.append(m)
            valid = reach & can_end
        else:
            valid = reach
        sub = [n for n in nodes if id(n) in valid]
        def pos_key(n):
            return (n.pos().y(), n.pos().x())
        incoming = {id(n): 0 for n in sub}
        outgoing = {id(n): [] for n in sub}
        by_id    = {id(n): n for n in sub}
        for e in edges:
            if e.start_socket is None or e.end_socket is None:
                continue
            s = e.start_socket.node
            t = e.end_socket.node
            if id(s) in incoming and id(t) in incoming:
                outgoing[id(s)].append(t)
                incoming[id(t)] += 1
        ready = [by_id[nid] for nid, c in incoming.items() if c == 0]
        ready.sort(key=pos_key)
        ordered, placed = [], set()
        while ready:
            n = ready.pop(0)
            if id(n) in placed:
                continue
            placed.add(id(n))
            ordered.append(n)
            for m in outgoing[id(n)]:
                incoming[id(m)] -= 1
                if incoming[id(m)] == 0:
                    ready.append(m)
            ready.sort(key=pos_key)
        if len(ordered) < len(sub):
            leftovers = [n for n in sub if id(n) not in placed]
            leftovers.sort(key=pos_key)
            ordered.extend(leftovers)
        return ordered

    def getByLine(self, start=None, end=None, strict=False):
        return self.get_by_line(start=start, end=end, strict=strict)


# --------------------------------------------------------------------------- #
#  Picker API                                                                 #
# --------------------------------------------------------------------------- #

class _PickerAPI:
    def __init__(self, api):
        self._api = api

    def node(self, prompt="Click a node…"):
        picked = self.nodes(max_count=1, prompt=prompt)
        return picked[0] if picked else None

    def nodes(self, max_count=None, prompt="Click nodes… Enter to finish"):
        scene = self._api.window.scene
        view  = self._api.window.view
        self._api.window.statusBar().showMessage(prompt)
        result = {"nodes": [], "done": False, "cancel": False}
        loop = QEventLoop()
        original_press = view.mousePressEvent
        original_key   = view.keyPressEvent

        def pick_at(scene_pos):
            item = scene.itemAt(scene_pos, view.transform())
            cur = item
            while cur is not None and not isinstance(cur, Node):
                cur = cur.parentItem()
            if cur is None:
                return
            if cur not in result["nodes"]:
                result["nodes"].append(cur)
            if max_count is not None and len(result["nodes"]) >= max_count:
                result["done"] = True
                loop.quit()

        def on_press(event):
            if event.button() == Qt.LeftButton:
                pick_at(view.mapToScene(event.pos()))
                event.accept(); return
            if event.button() == Qt.RightButton:
                result["cancel"] = True
                loop.quit(); return
            original_press(event)

        def on_key(event):
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                result["done"] = True
                loop.quit(); return
            if event.key() == Qt.Key_Escape:
                result["cancel"] = True
                loop.quit(); return
            original_key(event)

        view.mousePressEvent = on_press
        view.keyPressEvent   = on_key
        try:
            loop.exec_()
        finally:
            view.mousePressEvent = original_press
            view.keyPressEvent   = original_key
            self._api.window.statusBar().clearMessage()
        return [] if result["cancel"] else result["nodes"]

    def socket(self, node=None, is_input=None, prompt="Click a socket…"):
        scene = self._api.window.scene
        view  = self._api.window.view
        self._api.window.statusBar().showMessage(prompt)
        result = {"socket": None, "cancel": False}
        loop = QEventLoop()
        original_press = view.mousePressEvent
        original_key   = view.keyPressEvent

        def on_press(event):
            if event.button() == Qt.LeftButton:
                scene_pos = view.mapToScene(event.pos())
                item = scene.itemAt(scene_pos, view.transform())
                if isinstance(item, NodeSocket):
                    ok = True
                    if node is not None and item.node is not node:
                        ok = False
                    if is_input is not None and item.is_input != is_input:
                        ok = False
                    if ok:
                        result["socket"] = item
                        loop.quit()
                event.accept(); return
            if event.button() == Qt.RightButton:
                result["cancel"] = True
                loop.quit(); return
            original_press(event)

        def on_key(event):
            if event.key() == Qt.Key_Escape:
                result["cancel"] = True
                loop.quit(); return
            original_key(event)

        view.mousePressEvent = on_press
        view.keyPressEvent   = on_key
        try:
            loop.exec_()
        finally:
            view.mousePressEvent = original_press
            view.keyPressEvent   = original_key
            self._api.window.statusBar().clearMessage()
        return None if result["cancel"] else result["socket"]

    def edge(self, prompt="Click a connection…"):
        scene = self._api.window.scene
        view  = self._api.window.view
        self._api.window.statusBar().showMessage(prompt)
        result = {"edge": None}
        loop = QEventLoop()
        original_press = view.mousePressEvent
        original_key   = view.keyPressEvent

        def on_press(event):
            if event.button() == Qt.LeftButton:
                scene_pos = view.mapToScene(event.pos())
                item = scene.itemAt(scene_pos, view.transform())
                if isinstance(item, Edge):
                    result["edge"] = item
                    loop.quit()
                event.accept(); return
            if event.button() == Qt.RightButton:
                loop.quit(); return
            original_press(event)

        def on_key(event):
            if event.key() == Qt.Key_Escape:
                loop.quit(); return
            original_key(event)

        view.mousePressEvent = on_press
        view.keyPressEvent   = on_key
        try:
            loop.exec_()
        finally:
            view.mousePressEvent = original_press
            view.keyPressEvent   = original_key
            self._api.window.statusBar().clearMessage()
        return result["edge"]

    def template(self, categories=None, prompt="Pick a node type"):
        result = {"tpl": None}
        own = QMenu(self._api.window)
        own.setStyleSheet(MENU_STYLE)
        own.setMinimumWidth(220)

        def make_pick(t):
            def _pick():
                result["tpl"] = t
                own.close()
            return _pick

        for category, templates in NODE_TEMPLATES:
            if categories is not None and category not in categories:
                continue
            sub = own.addMenu(category)
            sub.setStyleSheet(MENU_STYLE)
            for tpl in templates:
                act = sub.addAction(tpl["name"])
                if tpl.get("description"):
                    act.setToolTip(tpl["description"])
                act.triggered.connect(make_pick(tpl))
        own.exec_(QCursor.pos())
        return result["tpl"]

    def color(self, initial="#3B3B3B", title="Pick a color"):
        c = QColorDialog.getColor(QColor(initial), self._api.window, title)
        return c.name() if c.isValid() else None

    def file(self, title="Open file", filt="All Files (*)"):
        path, _ = QFileDialog.getOpenFileName(self._api.window, title, "", filt)
        return path or None

    def save_file(self, title="Save file", default="untitled",
                  filt="All Files (*)"):
        path, _ = QFileDialog.getSaveFileName(
            self._api.window, title, default, filt)
        return path or None

    def directory(self, title="Choose folder"):
        return QFileDialog.getExistingDirectory(self._api.window, title, "") or None

    def text(self, default="", title="Enter text", label="Value:"):
        from PyQt5.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(
            self._api.window, title, label, QLineEdit.Normal, default)
        return text if ok else None

    def number(self, default=0.0, minimum=-1e9, maximum=1e9,
               decimals=2, title="Enter number", label="Value:"):
        from PyQt5.QtWidgets import QInputDialog
        val, ok = QInputDialog.getDouble(
            self._api.window, title, label, float(default),
            float(minimum), float(maximum), int(decimals))
        return val if ok else None

    def integer(self, default=0, minimum=-2**31, maximum=2**31 - 1,
                step=1, title="Enter integer", label="Value:"):
        from PyQt5.QtWidgets import QInputDialog
        val, ok = QInputDialog.getInt(
            self._api.window, title, label, int(default),
            int(minimum), int(maximum), int(step))
        return val if ok else None

    def choice(self, options, current=0, title="Pick one", label="Value:"):
        from PyQt5.QtWidgets import QInputDialog
        if not options:
            return None
        item, ok = QInputDialog.getItem(
            self._api.window, title, label,
            [str(o) for o in options], int(current), False)
        return item if ok else None

    def confirm(self, message, title="Confirm"):
        from PyQt5.QtWidgets import QMessageBox
        r = QMessageBox.question(
            self._api.window, title, message,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return r == QMessageBox.Yes

    def info(self, message, title="Info"):
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.information(self._api.window, title, message)

    def error(self, message, title="Error"):
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.critical(self._api.window, title, message)


# --------------------------------------------------------------------------- #
#  Reporter API                                                               #
# --------------------------------------------------------------------------- #

class _ReporterAPI:
    def __init__(self, api):
        self._api = api

    def post(self, text, level="info", duration=3500):
        self._api.window.report(text, level, duration)

    def info(self, text, duration=3500):    self.post(text, "info",    duration)
    def success(self, text, duration=3500): self.post(text, "success", duration)
    def warning(self, text, duration=4000): self.post(text, "warning", duration)
    def error(self, text, duration=5000):   self.post(text, "error",   duration)
    def debug(self, text, duration=3000):   self.post(text, "debug",   duration)

    def clear(self):
        self._api.window.reporter.clear()


# --------------------------------------------------------------------------- #
#  Registrar + register API                                                   #
# --------------------------------------------------------------------------- #

class _NodeRegistrar:
    def __init__(self, api):
        self._api = api

    def add(self, name, color="#3B3B3B", category="Custom",
            description="", dynamic=False,
            inputs=None, outputs=None, sections=None):
        return self._api.register_node(
            name, color=color, category=category, description=description,
            dynamic=dynamic,
            inputs=inputs, outputs=outputs, sections=sections)

    def remove(self, name):
        for _, tpls in NODE_TEMPLATES:
            for i, t in enumerate(tpls):
                if t["name"] == name:
                    del tpls[i]
                    _TEMPLATE_BY_NAME.pop(name, None)
                    self._api.window.library.refresh()
                    return True
        return False

    def clear(self):
        # Snapshot categories added via NODE_TEMPLATES.extend() at
        # module load time.  _register_builtins and _register_roles
        # do not re-add them, so without this save they vanish the
        # moment the wipe below runs.  Preview is currently the
        # only such category.
        _EXTEND_CATS = ("Preview",)
        _saved_extra = []
        for _cat, _tpls in NODE_TEMPLATES:
            if _cat in _EXTEND_CATS:
                _saved_extra.append((_cat, list(_tpls)))

        NODE_TEMPLATES.clear()
        _TEMPLATE_BY_NAME.clear()
        try:
            _register_builtins(self._api)
            _register_roles(self._api)
        except Exception as _ex:
            print("[roles] restore failed:", _ex)
        try:
            _register_builtins(self._api)
        except Exception as _ex:
            print("[builtins] restore failed:", _ex)

        # Re-add the saved categories and index them by name.
        for _cat, _tpls in _saved_extra:
            NODE_TEMPLATES.append((_cat, _tpls))
            for _t in _tpls:
                _TEMPLATE_BY_NAME[_t["name"]] = _t

        # Collapse any same-named entries so find_template() returns
        # the newest one.
        try:
            _dedupe_templates()
        except NameError:
            pass

        self._api.window.library.refresh()

    def builtin(self):
        NODE_TEMPLATES[:] = _copy.deepcopy(_BUILTIN_TEMPLATES)
        _rebuild_template_index()
        self._api.window.library.refresh()

    def bulk(self, specs):
        return self._api.bulk_register(specs)

    def list(self):
        return self._api.templates()


class _RegisterAPI:
    def __init__(self, api):
        self._api = api
        self.node = _NodeRegistrar(api)
        self.nodes = _NodesQueryAPI(api)


# --------------------------------------------------------------------------- #
#  Settings API                                                               #
# --------------------------------------------------------------------------- #

class _SettingsAPI:
    def __init__(self, api):
        self._api = api

    @property
    def hide_paths(self):
        return _PATH_FLAGS["hidden"]

    @hide_paths.setter
    def hide_paths(self, value):
        _PATH_FLAGS["hidden"] = bool(value)
        self._reapply()

    @property
    def auto_reroute(self):       return _REROUTE["enabled"]
    @auto_reroute.setter
    def auto_reroute(self, v):    _REROUTE["enabled"] = bool(v)

    @property
    def auto_reroute_ask(self):   return _REROUTE["ask"]
    @auto_reroute_ask.setter
    def auto_reroute_ask(self, v):_REROUTE["ask"] = bool(v)

    @property
    def auto_reroute_flow(self):  return _REROUTE["flow"]
    @auto_reroute_flow.setter
    def auto_reroute_flow(self, v): _REROUTE["flow"] = bool(v)

    @property
    def auto_reroute_data(self):  return _REROUTE["data"]
    @auto_reroute_data.setter
    def auto_reroute_data(self, v): _REROUTE["data"] = bool(v)

    def get(self, key, default=None):
        if key == "hide_paths":       return _PATH_FLAGS["hidden"]
        if key == "auto_reroute":     return _REROUTE["enabled"]
        if key == "auto_reroute_ask": return _REROUTE["ask"]
        return default

    def set(self, key, value):
        if key == "hide_paths":       self.hide_paths = value
        elif key == "auto_reroute":   self.auto_reroute = value
        elif key == "auto_reroute_ask": self.auto_reroute_ask = value
        else:
            raise KeyError("Unknown setting: " + str(key))

    def _reapply(self):
        for e in self._api.edges():
            if getattr(e, "is_path_edge", False):
                try:
                    e.apply_path_visibility()
                except Exception:
                    pass


# --------------------------------------------------------------------------- #
#  Blocks API                                                                 #
# --------------------------------------------------------------------------- #

class _BlocksAPI:
    def __init__(self, api):
        self._api = api

    @property
    def show_slot(self):      return _BLOCKS["show_slot"]
    @show_slot.setter
    def show_slot(self, v):
        _BLOCKS["show_slot"] = bool(v)
        for n in self._api.nodes():
            n.update()

    def chain(self, ref):
        n = self._api._resolve_node(ref)
        return n.block_chain() if n is not None else []

    def attach(self, parent_ref, child_ref):
        p = self._api._resolve_node(parent_ref)
        c = self._api._resolve_node(child_ref)
        if p is None or c is None:
            return False
        return p.add_block(c)

    def detach(self, ref):
        n = self._api._resolve_node(ref)
        if n is None:
            return False
        p = getattr(n, "parent_node", None)
        if p is None:
            return False
        return p.remove_block(n)

    def parent(self, ref):
        n = self._api._resolve_node(ref)
        return getattr(n, "parent_node", None) if n is not None else None


# --------------------------------------------------------------------------- #
#  Main API                                                                   #
# --------------------------------------------------------------------------- #

class API(QObject):
    _inst = None

    @classmethod
    def instance(cls, argv=None):
        if cls._inst is None:
            cls._inst = cls(argv)
        return cls._inst

    def __init__(self, argv=None):
        if API._inst is not None:
            raise RuntimeError("Use API.instance() — singleton.")
        super().__init__()
        self._app = QApplication.instance()
        if self._app is None:
            self._app = QApplication(argv or sys.argv)
            self._app.setStyleSheet(APP_STYLE)

        self.window    = MainWindow()
        self._by_id    = {}
        self._staged   = []
        self._listeners = {}

        self.register = _RegisterAPI(self)
        self.settings = _SettingsAPI(self)
        self.blocks   = _BlocksAPI(self)
        self._lifecycle = _LifecycleManager(self)
        self.picker   = _PickerAPI(self)
        self.report   = _ReporterAPI(self)

        self.window.scene.graph_changed.connect(self._emit_graph_changed)
        self.window.scene.selectionChanged.connect(self._emit_selection)

    # -- lifecycle -------------------------------------------------------- #
    def show(self):  self.window.show(); return self.window
    def hide(self):  self.window.hide()
    def run(self):   self.show(); return self._app.exec_()
    def app(self):   return self._app
    def quit(self):  self._app.quit()

    # -- events ----------------------------------------------------------- #
    def on(self, event, callback):
        self._listeners.setdefault(event, []).append(callback)
        return callback

    def off(self, event, callback):
        try:
            self._listeners.get(event, []).remove(callback)
        except ValueError:
            pass

    def _emit(self, event, *args):
        for cb in list(self._listeners.get(event, [])):
            try:
                cb(*args)
            except Exception as ex:
                print(f"[API] listener error on '{event}':", ex)

    _last_counts = (0, 0)

    def _emit_graph_changed(self):
        if self.window is None or sip.isdeleted(self.window):
            return
        try:
            scene = self.window.scene
            if scene is None or sip.isdeleted(scene):
                return
            n, e = scene.counts()
        except RuntimeError:
            return
        if hasattr(self, "_lifecycle") and self._lifecycle:
            try:
                self._lifecycle.evaluate()
            except Exception as _lc_ex:
                print('[lifecycle] ' + str(_lc_ex))
        prev_n, prev_e = self._last_counts
        if n != prev_n:
            self._emit("node_added" if n > prev_n else "node_removed")
            self._emit("graph_changed", n, e)
        if e != prev_e:
            self._emit("edge_added" if e > prev_e else "edge_removed")
            if n == prev_n:
                self._emit("graph_changed", n, e)
        self._last_counts = (n, e)

    def _emit_selection(self):
        if self.window is None or sip.isdeleted(self.window):
            return
        try:
            scene = self.window.scene
            if scene is None or sip.isdeleted(scene):
                return
            self._emit("selection", self.selection())
        except RuntimeError:
            return

    # -- lookup ----------------------------------------------------------- #
    def _resolve_node(self, ref):
        if ref is None:
            return None
        if isinstance(ref, Node):
            return ref
        if isinstance(ref, str):
            if ref in self._by_id:
                return self._by_id[ref]
            for i in self.window.scene.items():
                if isinstance(i, Node) and i.title == ref:
                    return i
            for n in self._staged:
                if n.metadata.get("id") == ref or n.title == ref:
                    return n
        return None

    def _resolve_socket(self, node_ref, socket_name, is_input=None):
        n = self._resolve_node(node_ref)
        if n is None:
            return None
        return n.socket(socket_name, is_input)

    # -- scene ------------------------------------------------------------ #
    def clear(self):
        self.window.scene.clear()
        self.window.scene.temp_edge = None
        self.window.scene.drag_socket = None
        self.window.scene._tooltip = None
        self._by_id.clear()
        self._staged.clear()
        self._last_counts = (0, 0)
        self._emit("cleared")
        self.window._refresh_status()

    # -- nodes ------------------------------------------------------------ #
    def add_node(self, name, x=None, y=None, id=None, metadata=None,
                 overrides=None, description=None):
        tpl = find_template(name)
        if tpl is not None and not tpl.get("enabled", True):
            msg = (tpl.get("_lifecycle") or {}).get("locked_message")
            raise ValueError(
                "Template '%s' is locked." % name
                + (" " + msg if msg else ""))
        if tpl is None:
            raise KeyError(
                f"No node template named '{name}'.\n"
                f"Registered: {sorted(self.templates().keys())}\n"
                f"Register one with api.register_node(...)")
        if overrides:
            tpl = {**tpl, **overrides}
        if description is not None:
            tpl = {**tpl, "description": description}

        node = build_node_from_template(tpl)
        # force-copy the codegen fields so a stale value in the
        # incoming metadata dict cannot shadow the template
        if tpl.get("qualname"):
            node.metadata["qualname"] = tpl.get("qualname")
        elif "qualname" not in node.metadata:
            node.metadata["qualname"] = None
        if tpl.get("import_module"):
            node.metadata["import_module"] = tpl.get("import_module")
        elif "import_module" not in node.metadata:
            node.metadata["import_module"] = None
        if tpl.get("kind"):
            node.metadata["kind"] = tpl.get("kind")
        elif "kind" not in node.metadata:
            node.metadata["kind"] = None
        if tpl.get("call"):
            node.metadata["call"] = tpl.get("call")
        elif "call" not in node.metadata:
            node.metadata["call"] = None
        node.metadata.setdefault("call",     tpl.get("call"))
        for _cat, _tpls in NODE_TEMPLATES:
            if tpl in _tpls:
                node.metadata.setdefault("category", _cat)
                break
        if metadata:
            node.metadata.update(metadata)
        if id:
            self._by_id[id] = node
            node.metadata["id"] = id
        if x is not None and y is not None:
            self._place_in_scene(node, x, y)
        else:
            self._staged.append(node)
        return node

    def _place_in_scene(self, node, x, y):
        node.setPos(x, y)
        if node.scene() is None:
            self.window.scene.addItem(node)
        self.window.scene.clearSelection()
        node.setSelected(True)
        node.update_edges()
        self.window.scene.graph_changed.emit()

    def place(self, node, x, y):
        if isinstance(node, str):
            node = self._resolve_node(node)
        if node is None:
            return False
        self._place_in_scene(node, x, y)
        if node in self._staged:
            self._staged.remove(node)
        return True

    def staged(self):
        return list(self._staged)

    def is_placed(self, ref):
        n = self._resolve_node(ref)
        return n is not None and n.scene() is not None

    def is_staged(self, ref):
        n = self._resolve_node(ref)
        return n is not None and n.scene() is None

    def remove_node(self, ref):
        node = self._resolve_node(ref)
        if node is None:
            return False
        if node in self._staged:
            self._staged.remove(node); return True
        self.window.scene.remove_node(node)
        for k, v in list(self._by_id.items()):
            if v is node:
                del self._by_id[k]
        return True

    def duplicate_node(self, ref, dx=30, dy=30):
        node = self._resolve_node(ref)
        if node is None:
            return None
        copy = node.clone()
        copy.setPos(node.pos() + QPointF(dx, dy))
        if node.scene() is not None:
            self.window.scene.addItem(copy)
        else:
            self._staged.append(copy)
        return copy

    def nodes(self):
        return [i for i in self.window.scene.items() if isinstance(i, Node)]

    def node(self, ref):
        return self._resolve_node(ref)

    def set_node_title(self, ref, title):
        n = self._resolve_node(ref)
        if n is None: return False
        n.set_title(title); return True

    def set_node_color(self, ref, color):
        n = self._resolve_node(ref)
        if n is None: return False
        n.set_node_color(QColor(color)); return True

    def set_node_description(self, ref, text):
        n = self._resolve_node(ref)
        if n is None: return False
        n.set_description(text); return True

    def move_node(self, ref, x, y):
        n = self._resolve_node(ref)
        if n is None: return False
        if n.scene() is None:
            return self.place(n, x, y)
        n.setPos(x, y); return True

    def get_node_pos(self, ref):
        n = self._resolve_node(ref)
        return (n.pos().x(), n.pos().y()) if n else None

    # -- ordering --------------------------------------------------------- #
    def nodes_by_position(self):
        return sorted(self.nodes(), key=lambda n: (n.pos().y(), n.pos().x()))

    def nodes_topo(self):
        nodes = self.nodes()
        if not nodes:
            return []
        incoming = {id(n): 0 for n in nodes}
        outgoing = {id(n): [] for n in nodes}
        by_id    = {id(n): n for n in nodes}
        for e in self.edges():
            if e.start_socket is None or e.end_socket is None:
                continue
            src = e.start_socket.node
            dst = e.end_socket.node
            if id(src) in incoming and id(dst) in incoming:
                outgoing[id(src)].append(dst)
                incoming[id(dst)] += 1
        def pos_key(n):
            return (n.pos().y(), n.pos().x())
        ready   = [by_id[nid] for nid, cnt in incoming.items() if cnt == 0]
        ready.sort(key=pos_key)
        ordered, placed = [], set()
        while ready:
            n = ready.pop(0)
            if id(n) in placed:
                continue
            placed.add(id(n))
            ordered.append(n)
            for d in outgoing[id(n)]:
                incoming[id(d)] -= 1
                if incoming[id(d)] == 0:
                    ready.append(d)
            ready.sort(key=pos_key)
        if len(ordered) < len(nodes):
            leftovers = [n for n in nodes if id(n) not in placed]
            leftovers.sort(key=pos_key)
            ordered.extend(leftovers)
        return ordered

    def upstream(self, ref, recursive=True):
        start = self._resolve_node(ref)
        if start is None:
            return []
        def direct(node):
            res = []
            for s in node.inputs:
                for e in s.connections:
                    if e.start_socket is not None:
                        res.append(e.start_socket.node)
            return res
        if not recursive:
            return direct(start)
        seen, stack, collect = set(), [start], []
        while stack:
            n = stack.pop()
            for p in direct(n):
                if id(p) not in seen:
                    seen.add(id(p)); collect.append(p); stack.append(p)
        order = {id(n): i for i, n in enumerate(self.nodes_topo())}
        collect.sort(key=lambda x: order.get(id(x), 10**9))
        return collect

    def downstream(self, ref, recursive=True):
        start = self._resolve_node(ref)
        if start is None:
            return []
        def direct(node):
            res = []
            for s in node.outputs:
                for e in s.connections:
                    if e.end_socket is not None:
                        res.append(e.end_socket.node)
            return res
        if not recursive:
            return direct(start)
        seen, stack, collect = set(), [start], []
        while stack:
            n = stack.pop()
            for c in direct(n):
                if id(c) not in seen:
                    seen.add(id(c)); collect.append(c); stack.append(c)
        order = {id(n): i for i, n in enumerate(self.nodes_topo())}
        collect.sort(key=lambda x: order.get(id(x), 10**9))
        return collect

    # -- edges ------------------------------------------------------------ #
    def connect(self, from_node, from_socket, to_node, to_socket):
        sn = self._resolve_node(from_node)
        dn = self._resolve_node(to_node)
        if sn is None or sn.scene() is None:
            raise ValueError(f"Source node '{from_node}' is not in the scene.")
        if dn is None or dn.scene() is None:
            raise ValueError(f"Target node '{to_node}' is not in the scene.")
        src = self._resolve_socket(from_node, from_socket, is_input=False)
        dst = self._resolve_socket(to_node,   to_socket,   is_input=True)
        if src is None:
            raise KeyError(f"Output socket '{from_socket}' not found on "
                           f"'{sn.title}'")
        if dst is None:
            raise KeyError(f"Input socket '{to_socket}' not found on "
                           f"'{dn.title}'")
        return self.window.scene.connect_sockets(src, dst)

    def disconnect(self, edge):
        self.window.scene.remove_edge(edge)

    def disconnect_socket(self, node_ref, socket_name):
        sock = self._resolve_socket(node_ref, socket_name)
        if sock is None: return 0
        count = 0
        for e in list(sock.connections):
            self.window.scene.remove_edge(e); count += 1
        return count

    def edges(self):
        return [i for i in self.window.scene.items() if isinstance(i, Edge)]

    def connections_of(self, node_ref):
        n = self._resolve_node(node_ref)
        if n is None: return []
        out = []
        for s in n.inputs + n.outputs:
            for e in s.connections:
                if e.start_socket is None or e.end_socket is None:
                    continue
                out.append({
                    "from_node":   e.start_socket.node,
                    "from_socket": e.start_socket.name,
                    "to_node":     e.end_socket.node,
                    "to_socket":   e.end_socket.name,
                })
        return out

    # -- selection -------------------------------------------------------- #
    def selection(self):
        return [i for i in self.window.scene.selectedItems() if isinstance(i, Node)]

    def select(self, *refs):
        sc = self.window.scene; sc.clearSelection()
        for r in refs:
            n = self._resolve_node(r)
            if n is not None and n.scene() is not None:
                n.setSelected(True)

    def select_all(self):      self.window.view.select_all()
    def clear_selection(self): self.window.scene.clearSelection()

    # -- view ------------------------------------------------------------- #
    def frame_all(self):   self.window.view.frame_all()
    def zoom(self, f):     self.window.view.scale(f, f)
    def reset_zoom(self):  self.window.view.resetTransform()

    # -- registration ----------------------------------------------------- #
    def register_node(self, name, color="#3B3B3B", category="Custom",
                      description="", dynamic=False,
                      inputs=None, outputs=None, sections=None,
                      **lifecycle):
        if not name or not isinstance(name, str):
            raise ValueError("register_node needs a non-empty 'name'")
        _on_exists = lifecycle.pop("on_exists", "error")
        _existing = _TEMPLATE_BY_NAME.get(name)
        if _existing is not None:
            if description == "default" or _on_exists == "keep":
                return _existing
            if _on_exists == "replace":
                for _cat, _tpls in NODE_TEMPLATES:
                    if _existing in _tpls:
                        _tpls.remove(_existing)
                        break
                _TEMPLATE_BY_NAME.pop(name, None)
            else:
                raise ValueError(f"Template '{name}' already registered.")

        def _norm_sock(entry, direction):
            n = entry[0]
            t = entry[1]
            default = None
            options = None
            if len(entry) >= 3 and entry[2] in ("in", "out"):
                desc = entry[3] if len(entry) > 3 else ""
                if len(entry) > 4:
                    default = entry[4]
                if len(entry) > 5:
                    options = entry[5]
            elif len(entry) == 5:
                desc = entry[2]
                default = entry[3]
                options = entry[4]
            elif len(entry) == 4:
                desc = entry[2]
                default = entry[3]
            elif len(entry) == 3:
                desc = entry[2]
            elif len(entry) == 2:
                desc = ""
            else:
                raise ValueError(f"Bad socket entry: {entry!r}")
            return (n, t, direction, desc, default, options)

        if sections is None:
            sections = []
            if inputs:
                sections.append(("Inputs",
                                 [_norm_sock(s, "in") for s in inputs], ""))
            if outputs:
                sections.append(("Outputs",
                                 [_norm_sock(s, "out") for s in outputs], ""))
            if not sections:
                sections = [("IO", [], "")]

        norm_sections = []
        for raw in sections:
            if isinstance(raw, dict):
                norm_sections.append((raw.get("name", ""),
                                      raw.get("sockets", []),
                                      raw.get("description", "")))
            elif isinstance(raw, (tuple, list)):
                if len(raw) == 2:
                    norm_sections.append((raw[0], raw[1], ""))
                else:
                    norm_sections.append((raw[0], raw[1], raw[2]))
            else:
                raise ValueError(f"Bad section entry: {raw!r}")

        call_tpl   = lifecycle.pop("call", None)
        block_kind = lifecycle.pop("block_kind", None)
        is_seq     = lifecycle.pop("is_sequential", False)
        _qualname  = lifecycle.pop("qualname", None)
        _import_module = lifecycle.pop("import_module", None)
        _kind = lifecycle.pop("kind", None)

        template = {
            "name":         name,
            "color":        color,
            "description":  description,
            "dynamic":      bool(dynamic),
            "sections":     norm_sections,
            "qualname":     _qualname,
            "import_module": _import_module,
            "kind":         _kind,
            "call":         call_tpl,
            "block_kind":   block_kind,
            "is_sequential": bool(is_seq),
            "_lifecycle":   dict(lifecycle),
            "enabled":      bool(lifecycle.get("start_enabled", True)),
        }
        template["_lifecycle"]["_user_enabled"] = template["enabled"]

        if isinstance(category, (list, tuple)):
            category = "/".join(str(p) for p in category)
        else:
            category = str(category)

        for i, (cat, tpls) in enumerate(NODE_TEMPLATES):
            if cat == category:
                tpls.append(template); break
        else:
            NODE_TEMPLATES.append((category, [template]))

        _TEMPLATE_BY_NAME[name] = template
        self._schedule_library_refresh()
        return template

    def _schedule_library_refresh(self):
        if _LIB_REFRESH["suspended"] > 0:
            _LIB_REFRESH["pending"] = True
            return
        if _LIB_REFRESH["pending"]:
            return
        _LIB_REFRESH["pending"] = True
        QTimer.singleShot(0, self._flush_library_refresh)

    def _flush_library_refresh(self):
        _LIB_REFRESH["pending"] = False
        try:
            self.window.library.refresh()
        except Exception as ex:
            print("[lib] refresh failed:", ex)

    def begin_batch(self):
        _LIB_REFRESH["suspended"] += 1

    def end_batch(self):
        _LIB_REFRESH["suspended"] = max(0, _LIB_REFRESH["suspended"] - 1)
        if _LIB_REFRESH["suspended"] == 0 and _LIB_REFRESH["pending"]:
            QTimer.singleShot(0, self._flush_library_refresh)

    def bulk_register(self, specs):
        self.begin_batch()
        n_ok = 0
        n_bad = 0
        try:
            for spec in specs:
                try:
                    self.register_node(**spec)
                    n_ok += 1
                except Exception as ex:
                    n_bad += 1
                    if n_bad <= 20:
                        print("[bulk] register failed:",
                              spec.get("name"), ex)
            if n_bad > 20:
                print("[bulk] ... and %d more failures suppressed" % (n_bad - 20))
        finally:
            self.end_batch()
        return n_ok, n_bad

    # -- graph building helpers ------------------------------------------- #
    def chain_path(self, *ids):
        made = 0
        for a, b in zip(ids, ids[1:]):
            try:
                self.connect(a, "Path Out", b, "Path In")
                made += 1
            except Exception as ex:
                print("[chain] %s -> %s: %s" % (a, b, ex))
        return made

    def auto_connect(self, a, b, ignore_flow=True):
        src = self._resolve_node(a)
        dst = self._resolve_node(b)
        if src is None or dst is None:
            return None
        for out_sock in src.outputs:
            if ignore_flow and out_sock.socket_type == "path":
                continue
            for in_sock in dst.inputs:
                if ignore_flow and in_sock.socket_type == "path":
                    continue
                if in_sock.connections:
                    continue
                ok = (out_sock.socket_type == in_sock.socket_type
                      or out_sock.socket_type == "any"
                      or in_sock.socket_type == "any")
                if not ok:
                    continue
                try:
                    return self.connect(a, out_sock.name, b, in_sock.name)
                except Exception:
                    continue
        return None

    def enable_template(self, name):
        tpl = find_template(name)
        if tpl is None: return False
        tpl["enabled"] = True
        tpl.setdefault("_lifecycle", {})["_user_enabled"] = True
        self.window.library.refresh()
        return True

    def disable_template(self, name):
        tpl = find_template(name)
        if tpl is None: return False
        tpl["enabled"] = False
        tpl.setdefault("_lifecycle", {})["_user_enabled"] = False
        self.window.library.refresh()
        return True

    def is_template_enabled(self, name):
        tpl = find_template(name)
        return bool(tpl and tpl.get("enabled", True))

    def refresh_lifecycle(self):
        self._lifecycle.evaluate()

    def templates_status(self):
        out = []
        for _c, tpls in NODE_TEMPLATES:
            for t in tpls:
                out.append({
                    "name":    t["name"],
                    "enabled": t.get("enabled", True),
                    "locked":  not t.get("enabled", True),
                })
        return out

    def get_by_line(self, start=None, end=None, strict=False):
        return self.register.nodes.get_by_line(
            start=start, end=end, strict=strict)

    def getByLine(self, start=None, end=None, strict=False):
        return self.get_by_line(start=start, end=end, strict=strict)

    def templates(self):
        out = {}
        for _, tpls in NODE_TEMPLATES:
            for t in tpls:
                out[t["name"]] = t
        return out

    # -- serialization ---------------------------------------------------- #
    def to_dict(self):         return self.window.scene.to_dict()
    def from_dict(self, data): self.window.scene.load_from_dict(data)

    def save(self, path):
        if self._staged:
            print(f"[API] warning: {len(self._staged)} staged node(s) "
                  f"were not saved (they have no position yet).")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
        try:
            self.report.success(
                f"Saved {len(self.nodes())} nodes → {os.path.basename(path)}")
        except Exception:
            pass

    def load(self, path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.from_dict(data)
        self.window.view.frame_all()
        try:
            self.report.success(f"Opened {os.path.basename(path)}")
        except Exception:
            pass

    # -- misc ------------------------------------------------------------- #
    def status(self, text, timeout=4000):
        self.window.statusBar().showMessage(text, timeout)

    def counts(self):
        return self.window.scene.counts()


# --------------------------------------------------------------------------- #
#  Headless variants for APICli                                               #
# --------------------------------------------------------------------------- #

class _HeadlessLibrary:
    def refresh(self): pass


class _HeadlessStatusBar:
    def showMessage(self, text, timeout=0): pass
    def clearMessage(self): pass


class _HeadlessView:
    def current_scene_pos(self):     return QPointF(0.0, 0.0)
    def scene_pos_at_cursor(self):   return QPointF(0.0, 0.0)
    def frame_all(self):             pass
    def select_all(self):            pass
    def duplicate_selected(self):    pass
    def delete_selected(self):       pass
    def scale(self, *a, **kw):       pass
    def resetTransform(self):        pass


class _HeadlessWindow(QObject):
    def __init__(self):
        super().__init__()
        self.scene = NodeScene()
        self.library = _HeadlessLibrary()
        self.view = _HeadlessView()
        self._statusbar = _HeadlessStatusBar()

    def report(self, text, level="info", duration=3500):
        print("[%s] %s" % (level, text))

    def statusBar(self):
        return self._statusbar

    def _refresh_status(self): pass


class APICli(API):
    """
    Headless variant of API.  Inherits everything so any edit to API
    is automatically available here.

    The only difference is that it does not build a MainWindow and does
    not need a running event loop.  QApplication is still required by
    QGraphicsScene, so it is created on demand using the offscreen QPA
    platform (no display needed).
    """

    _inst_cli = None

    @classmethod
    def instance(cls, argv=None):
        if cls._inst_cli is None:
            cls._inst_cli = cls(argv)
        return cls._inst_cli

    def __init__(self, argv=None):
        from PyQt5.QtWidgets import QApplication
        if QApplication.instance() is None:
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            self._app = QApplication(argv or sys.argv)
        else:
            self._app = QApplication.instance()

        QObject.__init__(self)

        self.window = _HeadlessWindow()
        self._by_id = {}
        self._staged = []
        self._listeners = {}

        for attr, cls_name in (("register", "_RegisterAPI"),
                               ("settings", "_SettingsAPI"),
                               ("blocks",   "_BlocksAPI"),
                               ("report",   "_ReporterAPI"),
                               ("picker",   "_PickerAPI")):
            cls_ref = globals().get(cls_name)
            if cls_ref is None:
                continue
            try:
                setattr(self, attr, cls_ref(self))
            except Exception as ex:
                print("[APICli] skipping %s: %s" % (attr, ex))

        lc = globals().get("_LifecycleManager")
        if lc is not None:
            try:
                self._lifecycle = lc(self)
            except Exception:
                self._lifecycle = None

        self.window.scene.graph_changed.connect(self._emit_graph_changed)
        self.window.scene.selectionChanged.connect(self._emit_selection)

    def show(self):  pass
    def hide(self):  pass
    def run(self):   return 0
    def app(self):   return self._app
    def quit(self):  pass


# --------------------------------------------------------------------------- #
#  Entry point                                                                #
# --------------------------------------------------------------------------- #

def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(APP_STYLE)
    api = API.instance()
    win = api.window
    win.show()
    sys.exit(app.exec_())




# ============================================================== #
#  Per-node widget host                                          #
# ============================================================== #

def _install_nodehost():
    try:
        try:
            from helpers.Nodes import NodeClasses as NC
        except ImportError:
            import NodeClasses as NC
        NC.install(Node, NodeSocket)
        _classes = {}
        for _tag, _factory in list(NC._NODE_CLASS_REGISTRY.items()):
            try:
                _classes[_tag] = _factory(Node)
            except Exception as _fex:
                print("[nodehost] factory %r failed: %s" % (_tag, _fex))
        globals()["_NODE_CLASSES"] = _classes
        print("[nodehost] live classes:", sorted(_classes.keys()))
    except Exception as _ex:
        import traceback
        print("[nodehost] install failed:")
        traceback.print_exc()



# ---------------------------------------------------------------- #
#  Python project converter                                       #
# ---------------------------------------------------------------- #

_STDLIB = set(getattr(sys, "stdlib_module_names", ()) or ())
if not _STDLIB:
    # Python < 3.10 fallback: hard-coded common stdlib names.
    _STDLIB = {
        "abc", "argparse", "ast", "asyncio", "base64", "bisect",
        "builtins", "calendar", "collections", "concurrent",
        "configparser", "contextlib", "copy", "csv", "ctypes",
        "dataclasses", "datetime", "decimal", "difflib", "dis",
        "email", "enum", "errno", "faulthandler", "fnmatch",
        "functools", "gc", "getopt", "getpass", "glob", "gzip",
        "hashlib", "heapq", "hmac", "html", "http", "importlib",
        "inspect", "io", "ipaddress", "itertools", "json", "keyword",
        "linecache", "locale", "logging", "lzma", "math", "mimetypes",
        "multiprocessing", "operator", "os", "pathlib", "pickle",
        "pkgutil", "platform", "plistlib", "pprint", "profile",
        "pstats", "py_compile", "queue", "random", "re", "readline",
        "reprlib", "secrets", "select", "shelve", "shlex", "shutil",
        "signal", "site", "smtplib", "socket", "socketserver",
        "sqlite3", "ssl", "stat", "statistics", "string", "stringprep",
        "struct", "subprocess", "sys", "sysconfig", "tarfile",
        "tempfile", "textwrap", "threading", "time", "timeit",
        "tkinter", "token", "tokenize", "traceback", "tracemalloc",
        "types", "typing", "unicodedata", "unittest", "urllib",
        "uuid", "venv", "warnings", "wave", "weakref", "webbrowser",
        "xml", "xmlrpc", "zipfile", "zipimport", "zlib",
    }

_LOCAL_NAMES = {
    "helpers", "Nodes", "NodeClasses", "create", "edit", "editGit",
    "createGit", "main", "examples", "initiate", "start", "test",
}


def _scan_python_folder(folder):
    """Return (all_imports, per_file, local_names).

    local_names are the top-level package directories and .py file
    names inside the folder that should be treated as project-local
    and excluded from the dependency list.
    """
    import ast
    all_imports = set()
    per_file = {}
    local_names = set()

    folder = os.path.abspath(folder)
    for entry in os.listdir(folder):
        full = os.path.join(folder, entry)
        if os.path.isdir(full):
            if os.path.isfile(os.path.join(full, "__init__.py")) or \
               any(f.endswith(".py") for f in os.listdir(full)):
                local_names.add(entry)
        elif entry.endswith(".py"):
            local_names.add(entry[:-3])

    for dirpath, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs
                   if d not in (".git", "__pycache__", ".venv",
                                "venv", "env", "build", "dist",
                                ".tox", "node_modules")]
        for f in files:
            # Convert only touches .py source files.  Everything
            # else in the folder — .json, .yaml, .md, images, the
            # compiled __pycache__ — is ignored.  The three checks
            # below make that explicit and cheap.
            if not f.endswith(".py"):
                continue
            if f.startswith("."):
                continue
            if f.endswith(".pyc") or f.endswith(".pyo"):
                continue
            path = os.path.join(dirpath, f)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    src = fh.read()
                tree = ast.parse(src, filename=path)
            except Exception as ex:
                per_file[path] = ("error: %s" % ex, [])
                continue
            mods = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        mods.add(a.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    if node.level == 0 and node.module:
                        mods.add(node.module.split(".")[0])
            mods = {m for m in mods
                    if m and not m.startswith("_")}
            per_file[path] = ("ok", sorted(mods))
            all_imports |= mods

    external = sorted(
        m for m in all_imports
        if m not in _STDLIB
        and m not in _LOCAL_NAMES
        and m not in local_names
    )
    return external, per_file, sorted(local_names)


def _check_libs(python, libs):
    """Return (installed, missing) by probing the interpreter."""
    installed, missing = [], []
    for lib in libs:
        r = subprocess.run(
            [python, "-c", "import %s" % lib],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10)
        (installed if r.returncode == 0 else missing).append(lib)
    return installed, missing


# ---- the dialog --------------------------------------------------- #

class ConvertDialog(QDialog):
    def __init__(self, parent=None, python="python",
                 default_folder="", default_out=""):
        super().__init__(parent)
        self.setWindowTitle("Convert Python project")
        self.setModal(True)
        self.resize(680, 620)
        self._python = python
        self._folder = default_folder
        self._default_out = default_out

        from PyQt5.QtWidgets import QRadioButton, QButtonGroup

        v = QVBoxLayout(self)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(8)

        # ---- folder row ---- #
        row = QHBoxLayout()
        row.addWidget(QLabel("Folder:"))
        self.edit_folder = QLineEdit(default_folder)
        self.edit_folder.setPlaceholderText("path to a Python project")
        row.addWidget(self.edit_folder, 1)
        b_browse = QPushButton("Browse\u2026")
        b_browse.clicked.connect(self._pick_folder)
        row.addWidget(b_browse)
        v.addLayout(row)

        # ---- python row ---- #
        row = QHBoxLayout()
        row.addWidget(QLabel("Python:"))
        self.edit_python = QLineEdit(python)
        row.addWidget(self.edit_python, 1)
        b_choose = QPushButton("Choose\u2026")
        b_choose.clicked.connect(self._pick_python)
        row.addWidget(b_choose)
        v.addLayout(row)

        # ---- output format ---- #
        row = QHBoxLayout()
        row.addWidget(QLabel("Output:"))
        self.rb_db   = QRadioButton("SQLite (.db)")
        self.rb_json = QRadioButton("JSON (.json)")
        self.rb_db.setChecked(True)
        row.addWidget(self.rb_db)
        row.addWidget(self.rb_json)
        row.addStretch(1)
        row.addWidget(QLabel("Path:"))
        self.edit_out = QLineEdit(default_out)
        self.edit_out.setPlaceholderText("output file")
        row.addWidget(self.edit_out, 1)
        b_out = QPushButton("Browse\u2026")
        b_out.clicked.connect(self._pick_output)
        row.addWidget(b_out)
        v.addLayout(row)

        # ---- libs list ---- #
        v.addWidget(QLabel("Detected libraries:"))
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.NoSelection)
        v.addWidget(self.list, 1)

        # ---- status ---- #
        self.lbl_status = QLabel("Pick a folder and click Scan.")
        self.lbl_status.setStyleSheet("color:#8A8A8A;")
        v.addWidget(self.lbl_status)

        # ---- output pane ---- #
        self.lbl_output = QLabel("Output")
        self.lbl_output.setStyleSheet(
            "color:#7F7F7F;font-weight:bold;letter-spacing:1px;"
            "font-size:10px;")
        v.addWidget(self.lbl_output)

        self.edit_output = QPlainTextEdit()
        self.edit_output.setReadOnly(True)
        self.edit_output.setFixedHeight(140)
        self.edit_output.setStyleSheet(
            "QPlainTextEdit{background:#141414;color:#DDD;"
            " border:1px solid #2A2A2A;padding:6px;"
            " font-family:'JetBrains Mono','Consolas',monospace;"
            " font-size:11px;}")
        v.addWidget(self.edit_output)

        self._output_log_path = ""

        # ---- buttons ---- #
        row = QHBoxLayout()
        b_scan = QPushButton("Scan")
        b_scan.clicked.connect(self._scan)
        row.addWidget(b_scan)
        self.b_install = QPushButton("Install missing\u2026")
        self.b_install.clicked.connect(self._install_missing)
        self.b_install.setEnabled(False)
        row.addWidget(self.b_install)
        self.b_req = QPushButton("Use requirements.txt\u2026")
        self.b_req.clicked.connect(self._use_requirements)
        self.b_req.setEnabled(False)
        row.addWidget(self.b_req)
        row.addStretch(1)
        b_cancel = QPushButton("Cancel")
        b_cancel.clicked.connect(self.reject)
        row.addWidget(b_cancel)
        self.b_convert = QPushButton("Convert")
        self.b_convert.setDefault(True)
        self.b_convert.setEnabled(False)
        self.b_convert.clicked.connect(self._convert)
        row.addWidget(self.b_convert)
        v.addLayout(row)

        self._external = []
        self._missing = []
        self._req_file = ""

    # ---- pickers ---- #

    def _pick_folder(self):
        from PyQt5.QtWidgets import QFileDialog
        start = self.edit_folder.text() or os.path.expanduser("~")
        path = QFileDialog.getExistingDirectory(
            self, "Choose Python project folder", start)
        if path:
            self.edit_folder.setText(path)
            self._scan()

    def _pick_python(self):
        """Pick from a list of every interpreter found on the box.

        No file browser.  Interpreters are located with shutil.which
        and by probing a handful of well-known install locations.
        Each entry shows the interpreter path and its version, so
        you can tell a 3.10 from a 3.12 at a glance.
        """
        import glob
        from PyQt5.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
            QListWidgetItem, QPushButton, QLineEdit)
        import subprocess

        seen = set()
        found = []

        def probe(path):
            if not path:
                return
            real = os.path.realpath(path)
            if real in seen:
                return
            seen.add(real)
            try:
                out = subprocess.check_output(
                    [path, "-c",
                     "import sys; print('.'.join(map(str,"
                     " sys.version_info[:3])))"],
                    stderr=subprocess.DEVNULL, timeout=4).decode().strip()
            except Exception:
                return
            try:
                has_qt = subprocess.run(
                    [path, "-c", "import PyQt5"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=4).returncode == 0
            except Exception:
                has_qt = False
            found.append((path, out, has_qt))

        # PATH entries
        for name in ("python", "python3", "python3.9", "python3.10",
                     "python3.11", "python3.12", "python3.13"):
            probe(shutil.which(name))
        # Common install locations
        for pat in (
                "/usr/bin/python3*",
                "/usr/local/bin/python3*",
                "/opt/**/bin/python3*",
                os.path.expanduser("~/.local/bin/python*"),
                os.path.expanduser("~/.venv*/bin/python*"),
                os.path.expanduser("~/venv*/bin/python*")):
            for p in sorted(glob.glob(pat, recursive=True)):
                if os.path.isfile(p) and os.access(p, os.X_OK):
                    probe(p)

        if not found:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Choose Python",
                                "No Python interpreters found.")
            return

        # --- build the picker --- #
        dlg = QDialog(self)
        dlg.setWindowTitle("Choose Python interpreter")
        dlg.setModal(True)
        dlg.resize(560, 380)
        dlg.setStyleSheet(
            "QDialog{background:#252525;color:#DDD;}"
            "QListWidget{background:#1E1E1E;color:#DDD;"
            " border:1px solid #333;padding:4px;}"
            "QListWidget::item{padding:5px 8px;}"
            "QListWidget::item:selected{background:#E08C4A;color:#1A1A1A;}"
            "QLineEdit{background:#1E1E1E;color:#DDD;"
            " border:1px solid #333;padding:5px 8px;}"
            "QPushButton{background:#3C3C3C;border:1px solid #555;"
            " color:#EEE;padding:5px 14px;border-radius:3px;}"
            "QPushButton:hover{background:#4A4A4A;}")

        v = QVBoxLayout(dlg)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)
        v.addWidget(QLabel(
            "Pick the Python the editor should use for Convert."))

        search = QLineEdit()
        search.setPlaceholderText("Filter…")
        v.addWidget(search)

        lst = QListWidget()
        current = self.edit_python.text().strip()
        selected_row = 0
        for i, (path, ver, has_qt) in enumerate(found):
            tag = "PyQt5 OK" if has_qt else "        "
            text = "%s   %-8s   %s" % (tag, ver, path)
            it = QListWidgetItem(text)
            it.setData(Qt.UserRole, path)
            lst.addItem(it)
            if path == current:
                selected_row = i
        lst.setCurrentRow(selected_row)
        v.addWidget(lst, 1)

        def _filter(t):
            t = (t or "").strip().lower()
            for i in range(lst.count()):
                it = lst.item(i)
                it.setHidden(bool(t) and t not in it.text().lower())
        search.textChanged.connect(_filter)

        row = QHBoxLayout()
        row.addStretch(1)
        b_cancel = QPushButton("Cancel")
        b_ok = QPushButton("OK")
        b_ok.setDefault(True)
        row.addWidget(b_cancel)
        row.addWidget(b_ok)
        v.addLayout(row)

        b_cancel.clicked.connect(dlg.reject)

        def _accept():
            it = lst.currentItem()
            if it is None:
                return
            self.edit_python.setText(it.data(Qt.UserRole))
            dlg.accept()
        b_ok.clicked.connect(_accept)
        lst.itemDoubleClicked.connect(lambda _it: _accept())

        dlg.exec_()

    def _pick_output(self):
        from PyQt5.QtWidgets import QFileDialog
        start = self.edit_out.text() or self._default_out or os.getcwd()
        if self.rb_db.isChecked():
            path, _ = QFileDialog.getSaveFileName(
                self, "Save database", start, "SQLite (*.db)")
        else:
            path, _ = QFileDialog.getSaveFileName(
                self, "Save JSON", start, "JSON (*.json)")
        if path:
            self.edit_out.setText(path)

    # ---- scan ---- #

    def _scan(self):
        folder = self.edit_folder.text().strip()
        if not folder or not os.path.isdir(folder):
            self.lbl_status.setText("Folder not found.")
            return
        self.list.clear()
        self.lbl_status.setText("Scanning\u2026")
        QApplication.processEvents()

        try:
            external, per_file, local = _scan_python_folder(folder)
        except Exception as ex:
            self.lbl_status.setText("Scan failed: %s" % ex)
            return

        self._external = external
        self._req_file = os.path.join(folder, "requirements.txt")
        self.b_req.setEnabled(os.path.isfile(self._req_file))

        if not external:
            self.lbl_status.setText(
                "No external imports found.  (%d local, %d file(s) scanned)"
                % (len(local), len(per_file)))
            self.b_convert.setEnabled(False)
            self.b_install.setEnabled(False)
            return

        python = self.edit_python.text().strip() or "python"
        installed, missing = _check_libs(python, external)
        self._missing = missing

        for lib in external:
            mark = "OK " if lib in installed else "MISS"
            color = "#5CB85C" if lib in installed else "#D9534F"
            it = QListWidgetItem("[%s]  %s" % (mark, lib))
            it.setForeground(QColor(color))
            it.setData(Qt.UserRole, lib)
            self.list.addItem(it)

        self.b_install.setEnabled(bool(missing))
        self.b_convert.setEnabled(True)
        self.lbl_status.setText(
            "%d external, %d installed, %d missing"
            % (len(external), len(installed), len(missing)))

    # ---- install ---- #

    def _install_missing(self):
        if not self._missing:
            return
        python = self.edit_python.text().strip() or "python"
        msg = "Install these with\n\n    %s -m pip install ...\n\n%s" % (
            python, "\n".join(self._missing))
        from PyQt5.QtWidgets import QMessageBox
        if QMessageBox.question(self, "Install", msg) != QMessageBox.Yes:
            return
        subprocess.run(
            [python, "-m", "pip", "install", "--upgrade"] + self._missing)
        self._scan()

    def _use_requirements(self):
        if not os.path.isfile(self._req_file):
            return
        python = self.edit_python.text().strip() or "python"
        from PyQt5.QtWidgets import QMessageBox
        if QMessageBox.question(
                self, "Install",
                "Run\n\n    %s -m pip install -r %s\n\n?"
                % (python, self._req_file)) != QMessageBox.Yes:
            return
        subprocess.run(
            [python, "-m", "pip", "install", "-r", self._req_file])
        self._scan()

    # ---- convert ---- #

    def _log(self, line):
        """Append a line to the output pane and to the log file."""
        try:
            self.edit_output.appendPlainText(str(line))
            sb = self.edit_output.verticalScrollBar()
            sb.setValue(sb.maximum())
        except Exception:
            pass
        try:
            if self._output_log_path:
                with open(self._output_log_path, "a",
                          encoding="utf-8") as f:
                    f.write(str(line) + "\n")
        except Exception:
            pass
        QApplication.processEvents()

    def _log_clear(self):
        try:
            self.edit_output.clear()
        except Exception:
            pass

    def _convert(self):
        import time
        import subprocess as _sp

        folder = self.edit_folder.text().strip()
        out = self.edit_out.text().strip()

        if not out:
            self._pick_output()
            out = self.edit_out.text().strip()
        if not out:
            return

        if os.path.isdir(out):
            ext = ".db" if self.rb_db.isChecked() else ".json"
            out = os.path.join(out, "converted" + ext)
            self.edit_out.setText(out)

        fmt = "db" if self.rb_db.isChecked() else "json"
        python = self.edit_python.text().strip() or "python"

        parent = os.path.dirname(os.path.abspath(out))
        if parent and not os.path.isdir(parent):
            try:
                os.makedirs(parent, exist_ok=True)
            except Exception as ex:
                self._log("! could not create %s: %s" % (parent, ex))
                return

        self._output_log_path = out + ".log"
        try:
            with open(self._output_log_path, "w",
                      encoding="utf-8") as f:
                f.write("# Convert log\n")
        except Exception:
            self._output_log_path = ""

        self._log_clear()
        self._log("# Convert Python project")
        self._log("# Folder:  %s" % folder)
        self._log("# Output:  %s" % out)
        self._log("# Format:  %s" % fmt)
        self._log("# Python:  %s" % python)
        self._log("")

        root = os.path.dirname(os.path.abspath(__file__))
        while not os.path.isfile(os.path.join(root, "create.py")):
            parent = os.path.dirname(root)
            if parent == root:
                self._log("! could not locate create.py")
                self.lbl_status.setText("create.py not found")
                return
            root = parent

        create_py = os.path.join(root, "create.py")
        args = [python, create_py, "--format", fmt, "--quiet",
                "--db-path" if fmt == "db" else "--json-path", out,
                "-o", out + ".loader.py"]
        for lib in self._external:
            args.extend(["-l", lib])

        self._log("$ " + " ".join(
            ("'%s'" % a if " " in a else a) for a in args))
        self._log("")

        self.lbl_status.setText("Running create.py\u2026")
        QApplication.processEvents()

        t0 = time.monotonic()
        try:
            proc = _sp.Popen(
                args,
                stdout=_sp.PIPE,
                stderr=_sp.STDOUT,
                text=True,
                bufsize=1,
                cwd=root,
            )
        except Exception as ex:
            self._log("! failed to start: %s" % ex)
            self.lbl_status.setText("Launch failed")
            return

        for line in iter(proc.stdout.readline, ""):
            self._log(line.rstrip("\n"))
        proc.stdout.close()
        code = proc.wait()
        dt = time.monotonic() - t0

        self._log("")
        self._log("# exited %d in %.2fs" % (code, dt))

        if code != 0:
            self.lbl_status.setText(
                "create.py failed (exit %d) after %.1fs" % (code, dt))
            self.lbl_status.setStyleSheet("color:#D9534F;")
            return

        if not os.path.isfile(out):
            self._log("! create.py reported success but %s is missing"
                      % out)
            self.lbl_status.setText("Output file missing after run")
            self.lbl_status.setStyleSheet("color:#D9534F;")
            return

        size = os.path.getsize(out)
        self._log("")
        self._log("# wrote %s (%d bytes)" % (out, size))
        self._log("# log saved to %s" % self._output_log_path)

        self.lbl_status.setStyleSheet("color:#5CB85C;")
        self.lbl_status.setText(
            "Wrote %s (%d bytes) in %.1fs"
            % (os.path.basename(out), size, dt))

        try:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.information(
                self, "Convert complete",
                "Wrote:\n  %s\n  %s.loader.py\n\n"
                "Log:\n  %s"
                % (out, out, self._output_log_path))
        except Exception:
            pass





# ---------------------------------------------------------------- #
#  Two-way converters                                              #
# ---------------------------------------------------------------- #

try:
    from helpers.Nodes.ConvertDialogs import (
        PythonToPyUIDialog, PyUIToPythonDialog)
except ImportError:
    try:
        from ConvertDialogs import (
            PythonToPyUIDialog, PyUIToPythonDialog)
    except ImportError:
        PythonToPyUIDialog = None
        PyUIToPythonDialog = None

try:
    from helpers.Nodes.LibraryDialogs import LibraryManagerDialog
except ImportError:
    try:
        from LibraryDialogs import LibraryManagerDialog
    except ImportError:
        LibraryManagerDialog = None


_install_nodehost()

if __name__ == "__main__":
    main()
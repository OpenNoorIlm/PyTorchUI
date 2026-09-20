# Security Policy

## Supported versions

PyTorchUI is alpha software.  Only the latest commit on main receives
security fixes.

| version | supported |
| --- | --- |
| main | yes |
| older | no |

## Threat model

PyTorchUI runs Python code that you construct in the editor.  It does
not sandbox that code.  A graph is a Python program, and pressing F5
is equivalent to running that program with your full user privileges.

This is not a bug.  It is the same contract a text editor or REPL has
with its user.  Anything else would cripple the tool.

That said, some things are security-relevant even under that model:

- Code that runs before you press F5: during startup, when the
  editor imports main.py, or when a graph is loaded from disk.
- Code that runs when you open a graph file you did not write.
- Bugs in the editor itself (parsing, file IO, subprocess spawning)
  that could be exploited by a crafted graph or DB.
- Privilege escalation via the input-capture features (evdev,
  XGrabKeyboard, WH_KEYBOARD_LL) if a graph can drive them.

## In scope

- Memory-safety bugs in the editor or its dependencies.
- Path traversal or symlink attacks in file loading.
- Command injection in the subprocess runner.
- Graph JSON that triggers code execution at open time rather than
  at F5 time.
- Anything that lets one user's file harm another user's system when
  both run on the same machine.

## Out of scope

- The fact that running a graph executes arbitrary code.
  Documented by design.
- Loading a graph and pressing F5.  That is what the graph is for.
- Bugs that require an attacker to already have write access to your
  filesystem.
- DoS via a graph that allocates a lot of memory.  That is a user
  mistake, not a vulnerability.
- Vulnerabilities in torch, PyQt5, or other upstream packages.  See
  the dependency section below.

## Reporting a vulnerability

Do not open a public issue for security problems.

Email security@example.com with:

- A description of the issue.
- Steps to reproduce.
- The impact you believe it has.
- Any suggested fix or mitigation.
- Whether you want to be credited.

You will get an acknowledgement within 72 hours.  If the report is
accepted, we will:

1. Confirm the issue and its severity.
2. Prepare a fix on a private branch.
3. Credit you in the release notes unless you ask otherwise.
4. Publish a security advisory once a fix is available.

We ask that you do not disclose the issue publicly until a fix has
been released.

## Hardening tips for users

If you load graphs from sources you do not trust:

- Read generated.py before pressing F5.  Ctrl+G writes it and runs
  nothing.  Read the file, then decide.
- Use Ctrl+G, not F5 first.  Generation is safe; execution is not.
- Run the editor in a virtualenv or container.  The blast radius of
  a malicious graph is then the container, not your home folder.
- Do not open .json files you received via email or a link unless
  you trust the sender.
- Watch the terminal the editor was launched from.  Errors and
  subprocess output appear there.

## Dependencies

PyTorchUI depends on large upstream projects with their own security
policies.  Vulnerabilities in those should be reported to them
directly:

- PyQt5       https://www.riverbankcomputing.com/software/pyqt/
- PyTorch     https://github.com/pytorch/pytorch/security/policy
- matplotlib  https://github.com/matplotlib/matplotlib/security/policy
- numpy       https://github.com/numpy/numpy/security/policy

We track updates for these and will bump requirements.txt when a
security fix is released.

## Input capture on Linux

The pyautogui, press, and hotkey nodes use evdev (/dev/input/event*)
for global capture.  Reading those devices requires membership in the
input group:

    sudo usermod -a -G input $USER

After adding yourself, log out and back in so the group takes effect.
Without this the editor falls back to X11 polling which is less
accurate but not a security concern.

## License

PyTorchUI is distributed under the GNU General Public License v3.0 or
later.  See LICENSE.  The GPL's warranty disclaimer is relevant here:
the software is provided as is, without warranty of any kind.
Security issues are nonetheless taken seriously.

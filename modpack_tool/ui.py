"""Console output and prompts shared by every action."""

import os
import sys

if os.name == "nt":
    os.system("")  # Turns on ANSI escape handling in classic Windows consoles.

_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


class ToolError(Exception):
    """An expected failure whose message is meant for the user (no traceback)."""


def _style(code, text):
    return f"\033[{code}m{text}\033[0m" if _COLOR else str(text)


def bold(text):
    return _style("1", text)


def dim(text):
    return _style("2", text)


def title(text):
    print()
    print(bold(text))


def step(text):
    print(_style("36", "» ") + text)


def ok(text):
    print(_style("32", "✓ ") + text)


def warn(text):
    print(_style("33", "! ") + text)


def error(text):
    print(_style("31", "✗ ") + text)


def info(text=""):
    print(f"  {text}" if text else "")


def items(values, limit=None):
    """Print an indented bullet list, optionally truncated after ``limit`` entries."""
    values = list(values)
    shown = values if limit is None else values[:limit]
    for value in shown:
        print(f"    - {value}")
    if len(shown) < len(values):
        print(dim(f"    … and {len(values) - len(shown)} more"))


def ask(question, default=""):
    hint = f" [{default}]" if default else ""
    answer = input(f"{question}{hint}: ").strip()
    return answer or default


def confirm(question, default=False):
    hint = "Y/n" if default else "y/N"
    while True:
        answer = input(f"{question} [{hint}]: ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("  Please answer y or n.")


def choose(question, options, default):
    """Ask for one of ``options`` (a dict of key -> description) and return the key."""
    listing = ", ".join(f"{key} = {label}" for key, label in options.items())
    while True:
        answer = input(f"{question} ({listing}) [{default}]: ").strip().lower() or default
        if answer in options:
            return answer
        print(f"  Please enter one of: {', '.join(options)}.")


def pick_many(question, values, label=str, default="none"):
    """Let the user pick all, none, or some of ``values``; returns the picked values."""
    values = list(values)
    if not values:
        return []
    for number, value in enumerate(values, 1):
        print(f"    {number:>2}) {label(value)}")
    while True:
        answer = input(f"{question} (all / none / numbers like 1,3-5) [{default}]: ").strip().lower()
        answer = answer or default
        if answer in ("a", "all"):
            return values
        if answer in ("n", "none"):
            return []
        picked = _parse_numbers(answer, len(values))
        if picked is not None:
            return [values[index] for index in picked]
        print("  Please enter all, none, or item numbers.")


def _parse_numbers(text, count):
    picked = set()
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        first, _, last = part.partition("-")
        if not first.isdigit() or (last and not last.isdigit()):
            return None
        start, end = int(first), int(last or first)
        if not 1 <= start <= end <= count:
            return None
        picked.update(range(start - 1, end))
    return sorted(picked)


def clean_path(raw):
    """Normalize a typed or drag-and-dropped path (terminals often wrap it in quotes)."""
    text = str(raw or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1].strip()
    text = os.path.expanduser(os.path.expandvars(text))
    return os.path.normpath(text) if text else ""


def open_file(path):
    """Open a file in its default app, or say where it is when that isn't possible."""
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]  (Windows only)
    except (AttributeError, OSError):
        info(f"Open {path} in your editor.")

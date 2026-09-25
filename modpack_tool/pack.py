"""Everything that reads or writes the Packwiz folder.

Covers the packwiz CLI, the metafiles (*.pw.toml), index.toml, and the files the
tool generates inside the pack (bcc.json, the Crash Assistant modlist and
modlist.md). Reads use tomllib; edits use tomlkit so packwiz's formatting and
line endings survive (index hashes are computed over the raw bytes).
"""

import json
import re
import shutil
import subprocess
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import tomlkit

from . import ui
from .ui import ToolError

CATEGORIES = ("mods", "resourcepacks", "shaderpacks")
# Parts of the Packwiz folder that comparisons between releases look at.
TREE_PARTS = ("pack.toml", *CATEGORIES, "config")
VALID_SIDES = ("both", "client", "server")
LOADER_LABELS = {
    "fabric": "Fabric",
    "quilt": "Quilt",
    "forge": "Forge",
    "neoforge": "NeoForge",
    "liteloader": "LiteLoader",
}

_BRACKETED = re.compile(r"\(.*?\)|\[.*?\]|\{.*?\}")


def strip_brackets(text):
    """Drop "(...)", "[...]" and "{...}" parts from a display name."""
    return _BRACKETED.sub("", str(text)).strip()


############################################################
# Text files

def write_text(path, text):
    """Write ``text`` ("\\n" line breaks), keeping an existing file's line endings.

    New files get the platform default, which is what the tool always wrote.
    """
    path = Path(path)
    newline = None
    if path.is_file():
        newline = "\r\n" if b"\r\n" in path.read_bytes() else "\n"
    with open(path, "w", encoding="utf-8", newline=newline) as f:
        f.write(text)


def _edit_toml(path, change):
    """Apply ``change(doc)`` to a TOML file through tomlkit, preserving its style."""
    with open(path, "r", encoding="utf-8", newline="") as f:
        original = f.read()
    doc = tomlkit.parse(original)
    change(doc)
    text = tomlkit.dumps(doc)
    if "\r\n" in original:  # Keys tomlkit adds use "\n"; keep the file consistent.
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


############################################################
# packwiz CLI

class Packwiz:
    """Runs packwiz commands inside the pack folder, streaming their output."""

    def __init__(self, exe, pack_dir):
        self.exe = str(exe)
        self.pack_dir = Path(pack_dir)

    def run(self, *args, check=True, echo=True):
        """Run ``packwiz <args>``; returns (exit code, output lines)."""
        if not (Path(self.exe).is_file() or shutil.which(self.exe)):
            raise ToolError(
                f"packwiz was not found at '{self.exe}'. Install it with "
                "'go install github.com/packwiz/packwiz@latest' or set packwiz_exe_path in tool_config.yml."
            )
        try:
            process = subprocess.Popen(
                [self.exe, *args],
                cwd=self.pack_dir,
                stdin=subprocess.DEVNULL,  # packwiz must never wait for input; pass -y instead.
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as ex:
            raise ToolError(f"Could not start packwiz: {ex}") from ex
        output = []
        for line in process.stdout:
            line = line.rstrip()
            output.append(line)
            if echo and line.strip():
                print(ui.dim("  packwiz | ") + line)
        code = process.wait()
        if check and code != 0:
            if not echo:
                for line in output:
                    print(ui.dim("  packwiz | ") + line)
            raise ToolError(f"'packwiz {' '.join(args)}' failed (exit code {code}).")
        return code, output

    def refresh(self):
        self.run("refresh", echo=False)

    def update_all(self):
        # Exit codes are ignored: one unavailable mod shouldn't abort the rest.
        self.run("update", "--all", "-y", check=False)

    def update(self, slug):
        return self.run("update", slug, "-y", check=False)[0] == 0

    def pin(self, slug):
        self.run("pin", slug, echo=False)

    def unpin(self, slug):
        self.run("unpin", slug, echo=False)

    def remove(self, slug):
        self.run("remove", slug)

    def migrate_minecraft(self, version):
        self.run("migrate", "minecraft", version, "-y")

    def migrate_loader(self, version):
        self.run("migrate", "loader", version, "-y")

    def add_acceptable_version(self, version):
        self.run("settings", "acceptable-versions", "--add", version, echo=False)

    def remove_acceptable_version(self, version):
        self.run("settings", "acceptable-versions", "--remove", version, echo=False)


@contextmanager
def editing(packwiz):
    """Group metafile edits; the index is refreshed once they are done."""
    try:
        yield
    finally:
        packwiz.refresh()


############################################################
# Metafiles

@dataclass
class Mod:
    """One metafile (mod, resource pack or shader pack) as packwiz stores it."""

    rel: str  # Path relative to the Packwiz folder, e.g. "mods/sodium.pw.toml".
    data: dict

    @property
    def slug(self):
        name = PurePosixPath(self.rel).name
        return name.removesuffix(".toml").removesuffix(".pw")

    @property
    def folder(self):
        return str(PurePosixPath(self.rel).parent)

    @property
    def category(self):
        return PurePosixPath(self.rel).parts[0]

    @property
    def name(self):
        return str(self.data.get("name") or self.slug)

    @property
    def display_name(self):
        return strip_brackets(self.name) or self.slug

    @property
    def filename(self):
        return str(self.data.get("filename") or "")

    @property
    def side_raw(self):
        return str(self.data.get("side") or "").strip()

    @property
    def disabled(self):
        """Only sides packwiz installs count as active; "both(disabled)", "none" and typos don't."""
        return self.side_raw.lower() not in ("", *VALID_SIDES)

    @property
    def base_side(self):
        """The side without the "(disabled)" marker; empty means both, as in packwiz."""
        return self.side_raw.split("(", 1)[0].strip().lower() or "both"

    @property
    def side_valid(self):
        # "none" is how older packs marked disabled mods.
        return self.base_side in (*VALID_SIDES, "none")

    @property
    def side(self):
        """The side used for installs and when re-enabling: anything else counts as "both"."""
        return self.base_side if self.base_side in VALID_SIDES else "both"

    def installs_on(self, side):
        """True when this file ships in a ``side`` ("client"/"server") install."""
        return not self.disabled and self.side in ("both", side)

    @property
    def pinned(self):
        return bool(self.data.get("pin"))

    @property
    def optional(self):
        return bool((self.data.get("option") or {}).get("optional"))

    @property
    def download(self):
        return dict(self.data.get("download") or {})

    @property
    def hash(self):
        """(hash-format, hash) from the metafile, e.g. ("sha512", "ab12...")."""
        download = self.download
        return str(download.get("hash-format") or ""), str(download.get("hash") or "")

    @property
    def modrinth(self):
        return (self.data.get("update") or {}).get("modrinth")

    @property
    def curseforge(self):
        return (self.data.get("update") or {}).get("curseforge")

    @property
    def github(self):
        return (self.data.get("update") or {}).get("github")


def parse_mods(tree, categories=CATEGORIES):
    """Metafiles directly inside the category folders of a tree ({rel path: bytes})."""
    mods = []
    for rel, content in sorted(tree.items(), key=lambda item: item[0].lower()):
        parts = rel.split("/")
        if len(parts) != 2 or parts[0] not in categories or not parts[1].endswith(".toml"):
            continue
        try:
            mods.append(Mod(rel, tomllib.loads(content.decode("utf-8"))))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as ex:
            ui.warn(f"Skipping unreadable metafile {rel}: {ex}")
    return mods


def read_tree(pack_dir, parts=TREE_PARTS):
    """Read parts of a Packwiz folder into {posix path relative to it: bytes}."""
    pack_dir = Path(pack_dir)
    tree = {}
    for part in parts:
        path = pack_dir / part
        if path.is_file():
            tree[part] = path.read_bytes()
        elif path.is_dir():
            for file in path.rglob("*"):
                if file.is_file():
                    tree[file.relative_to(pack_dir).as_posix()] = file.read_bytes()
    return tree


def load_mods(pack_dir, categories=CATEGORIES):
    return parse_mods(read_tree(pack_dir, categories), categories)


def snapshot_texts(pack_dir, mods):
    """Raw text of each metafile, so an update can be compared and reverted."""
    return {mod.rel: (Path(pack_dir) / mod.rel).read_bytes() for mod in mods}


def restore(pack_dir, rel, content):
    (Path(pack_dir) / rel).write_bytes(content)


def set_disabled(pack_dir, mod, disabled):
    side = f"{mod.side}(disabled)" if disabled else mod.side
    _edit_toml(Path(pack_dir) / mod.rel, lambda doc: doc.__setitem__("side", side))


def apply_modrinth_version(pack_dir, mod, version):
    """Point a Modrinth metafile at another version of the same project.

    Returns False when the version has no usable primary file.
    """
    files = version.get("files") or []
    primary = next((f for f in files if f.get("primary")), files[0] if files else None)
    if not primary:
        return False
    hashes = primary.get("hashes") or {}
    hash_format = "sha512" if "sha512" in hashes else "sha1" if "sha1" in hashes else ""
    if not hash_format or not primary.get("url") or not primary.get("filename"):
        return False

    def change(doc):
        doc["filename"] = primary["filename"]
        download = doc.setdefault("download", tomlkit.table())
        download["url"] = primary["url"]
        download["hash-format"] = hash_format
        download["hash"] = hashes[hash_format]
        if primary.get("size"):
            download["file-size"] = int(primary["size"])
        doc["update"]["modrinth"]["version"] = version["id"]

    _edit_toml(Path(pack_dir) / mod.rel, change)
    return True


############################################################
# pack.toml and index.toml

def read_pack_toml(pack_dir):
    path = Path(pack_dir) / "pack.toml"
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError as ex:
        raise ToolError(f"pack.toml not found at {path}.") from ex
    except tomllib.TOMLDecodeError as ex:
        raise ToolError(f"Could not parse {path}: {ex}") from ex


def loader_of(pack_toml):
    """(loader id, loader version) of the pack, e.g. ("fabric", "0.18.4")."""
    versions = pack_toml.get("versions") or {}
    for loader in LOADER_LABELS:
        if str(versions.get(loader) or "").strip():
            return loader, str(versions[loader]).strip()
    return "", ""


def set_pack_version(pack_dir, version):
    _edit_toml(Path(pack_dir) / "pack.toml", lambda doc: doc.__setitem__("version", version))


def index_entries(pack_dir):
    """The [[files]] entries of the index: every file packwiz ships (ignores applied)."""
    pack_toml = read_pack_toml(pack_dir)
    index_path = Path(pack_dir) / (pack_toml.get("index") or {}).get("file", "index.toml")
    with open(index_path, "rb") as f:
        return tomllib.load(f).get("files", [])


def index_hash(pack_dir):
    return str((read_pack_toml(pack_dir).get("index") or {}).get("hash") or "")


############################################################
# Files the tool keeps up to date inside the pack

def write_bcc_version(path, version):
    """Set modpackVersion in a BetterCompatibilityChecker config; False if absent/unchanged."""
    path = Path(path)
    if not path.is_file():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("modpackVersion") == version:
        return False
    data["modpackVersion"] = version
    write_text(path, json.dumps(data))
    return True


def crash_assistant_modlist(mods):
    """JSON list of the jar filenames a client install contains."""
    names = sorted((mod.filename for mod in mods
                    if mod.category == "mods" and mod.filename and mod.installs_on("client")),
                   key=str.lower)
    return json.dumps(names, indent=2)


def modlist_markdown(mods, side_tags):
    """The modlist.md document: active and inactive mods by display name."""
    def label(mod):
        return f"{mod.display_name} [{mod.side.capitalize()}]" if side_tags else mod.display_name

    mod_files = [mod for mod in mods if mod.category == "mods"]
    active = [label(mod) for mod in mod_files if not mod.disabled]
    inactive = [label(mod) for mod in mod_files if mod.disabled]
    lines = ["# Mod List", "", "## Active Mods"]
    lines += [f"- {name}" for name in active] or ["- None"]
    lines += ["", "## Inactive Mods"]
    lines += [f"- {name}" for name in inactive] or ["- None"]
    lines.append("")
    return "\n".join(lines)

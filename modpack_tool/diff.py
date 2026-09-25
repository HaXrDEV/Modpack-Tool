"""What changed between two states of a pack (an earlier release and now).

Pure functions: both sides are trees of {path relative to Packwiz/: bytes}, from
``pack.read_tree`` (the working copy) or ``GitRepo.snapshot`` (a release tag).
"""

import difflib
import hashlib
import tomllib
from collections import namedtuple
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from . import pack

# A mod/pack name plus its side ("both", "client" or "server").
Named = namedtuple("Named", "name side")

_TEXT_EXTENSIONS = {".json", ".json5", ".yaml", ".yml", ".toml", ".cfg", ".conf", ".ini", ".properties", ".txt"}
_LINE_LIMIT = 20


@dataclass
class CategoryDiff:
    added: list = field(default_factory=list)    # [Named]
    removed: list = field(default_factory=list)  # [Named]
    updated: list = field(default_factory=list)  # [(name, before, after)]


@dataclass
class ConfigDiff:
    added: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    modified: list = field(default_factory=list)
    # [{path, removed_lines, added_lines, previous_content, current_content}]
    line_diffs: list = field(default_factory=list)
    # [{from, to, content_changed}]: plain configs moved into the YOSBR defaults folder.
    moved_to_yosbr: list = field(default_factory=list)


@dataclass
class PackDiff:
    previous_version: str
    previous_minecraft: str
    current_version: str
    minecraft: str
    mods: CategoryDiff
    resourcepacks: CategoryDiff
    shaderpacks: CategoryDiff
    newly_added: list  # Names of mods that are new to the pack.
    reenabled: list    # Names of mods that were disabled before and are active now.
    config: ConfigDiff

    @property
    def migration(self):
        """True when the previous release was for another Minecraft version."""
        return bool(self.previous_minecraft) and self.previous_minecraft != self.minecraft

    def summary(self):
        """A one-line count of the changes, e.g. "+3 mods, 22 updated, 4 config files changed"."""
        parts = []
        if self.mods.added:
            parts.append(f"+{len(self.mods.added)} mod{'s' * (len(self.mods.added) != 1)}")
        if self.mods.removed:
            parts.append(f"-{len(self.mods.removed)} mod{'s' * (len(self.mods.removed) != 1)}")
        updated = len(self.mods.updated) + len(self.resourcepacks.updated) + len(self.shaderpacks.updated)
        if updated:
            parts.append(f"{updated} updated")
        packs = sum(len(d.added) + len(d.removed) for d in (self.resourcepacks, self.shaderpacks))
        if packs:
            parts.append(f"{packs} resource/shader pack{'s' * (packs != 1)} added or removed")
        configs = len(set(self.config.modified) | set(self.config.removed)
                      | {move["to"] for move in self.config.moved_to_yosbr})
        if configs:
            parts.append(f"{configs} config file{'s' * (configs != 1)} changed")
        return ", ".join(parts) or "no changes"


def tagged(named, side_tags):
    """A name for changelogs, with a `Client`/`Server` tag when side tags are on."""
    if side_tags and named.side != "both":
        return f"{named.name} `{named.side.capitalize()}`"
    return named.name


############################################################
# Metafiles

def _metafiles(tree, category):
    return {PurePosixPath(mod.rel).name: mod for mod in pack.parse_mods(tree, (category,))}


def _hash_label(filename, hash_value):
    short = str(hash_value or "")[:12]
    return f"{filename} (hash {short})" if short else filename


def diff_category(old_tree, new_tree, category):
    """Added, removed and updated active files of one category (mods, resourcepacks, shaderpacks)."""
    old = {name: mod for name, mod in _metafiles(old_tree, category).items() if not mod.disabled}
    new = {name: mod for name, mod in _metafiles(new_tree, category).items() if not mod.disabled}
    result = CategoryDiff()

    added = [Named(new[name].display_name, new[name].side) for name in new if name not in old]
    removed = [Named(old[name].display_name, old[name].side) for name in old if name not in new]
    # A file renamed without a name change (e.g. a new slug) is neither added nor removed.
    renamed = {n.name for n in added} & {n.name for n in removed}
    result.added = [n for n in added if n.name not in renamed]
    result.removed = [n for n in removed if n.name not in renamed]

    for name, current in new.items():
        previous = old.get(name)
        if previous is None:
            continue
        (old_format, old_hash), (new_format, new_hash) = previous.hash, current.hash
        if previous.filename == current.filename:
            if old_hash == new_hash or old_format != new_format:
                continue  # Unchanged, or the same jar with metadata from another platform.
            before = _hash_label(previous.filename, old_hash)
            after = _hash_label(current.filename, new_hash)
        else:
            before = previous.filename or old_hash
            after = current.filename or new_hash
        result.updated.append((current.display_name, before, after))
    return result


def addition_breakdown(old_tree, new_tree):
    """(names of newly added mods, names of re-enabled mods), each sorted without duplicates."""
    old = _metafiles(old_tree, "mods")
    old_by_name = {mod.display_name: mod for mod in old.values()}
    newly_added, reenabled = set(), set()
    for name, mod in _metafiles(new_tree, "mods").items():
        if mod.disabled:
            continue
        previous = old.get(name) or old_by_name.get(mod.display_name)  # Also follow renamed files.
        if previous is None:
            newly_added.add(mod.display_name)
        elif previous.disabled:
            reenabled.add(mod.display_name)
    return sorted(newly_added, key=str.lower), sorted(reenabled, key=str.lower)


############################################################
# Config files

def _is_yosbr(path):
    return str(path).lower().startswith("yosbr/")


def _generated_by_tool(path):
    """Files the tool rewrites itself (bcc.json, the Crash Assistant modlist) aren't config changes."""
    lowered = f"/{str(path).lower()}"
    stem = PurePosixPath(lowered).stem
    return stem == "bcc" or "/bcc/" in lowered or (stem == "modlist" and "/crash_assistant/" in lowered)


def _text(content):
    return content.decode("utf-8", errors="replace").splitlines()


def _line_diff(path, old_content, new_content, output_path=None):
    if PurePosixPath(path.lower()).suffix not in _TEXT_EXTENSIONS:
        return None
    old_lines, new_lines = _text(old_content), _text(new_content)
    removed, added = [], []
    for line in difflib.ndiff(old_lines, new_lines):
        if line.startswith("- ") and line[2:].strip():
            removed.append(line[2:].strip())
        elif line.startswith("+ ") and line[2:].strip():
            added.append(line[2:].strip())
    if not removed and not added:
        return None
    return {
        "path": output_path or path,
        "removed_lines": removed[:_LINE_LIMIT],
        "added_lines": added[:_LINE_LIMIT],
        "previous_content": "\n".join(old_lines),
        "current_content": "\n".join(new_lines),
    }


def diff_config(old_files, new_files):
    """Compare two config folders ({path relative to config/: bytes})."""
    old = {path: hashlib.sha256(data).hexdigest() for path, data in old_files.items() if not _generated_by_tool(path)}
    new = {path: hashlib.sha256(data).hexdigest() for path, data in new_files.items() if not _generated_by_tool(path)}
    result = ConfigDiff()
    added = sorted(set(new) - set(old), key=str.lower)
    removed = sorted(set(old) - set(new), key=str.lower)
    modified = sorted((p for p in set(old) & set(new) if old[p] != new[p]), key=str.lower)

    # Moving a config into the YOSBR overlay ("config/x" -> "config/yosbr/x" or
    # "config/yosbr/config/x") makes it a first-launch default. That is a move, not
    # a removal plus an addition.
    added_lookup = {path.lower(): path for path in added}
    consumed, kept_removed = set(), []
    for path in removed:
        target = None
        if not _is_yosbr(path):
            for candidate in (f"yosbr/{path}", f"yosbr/config/{path}"):
                if candidate.lower() in added_lookup:
                    target = added_lookup[candidate.lower()]
                    break
        if target is None:
            kept_removed.append(path)
            continue
        consumed.add(target.lower())
        result.moved_to_yosbr.append({"from": path, "to": target, "content_changed": old[path] != new[target]})
    result.added = [path for path in added if path.lower() not in consumed]
    result.removed = kept_removed

    line_diffs = {}
    for path in modified:
        entry = _line_diff(path, old_files[path], new_files[path])
        if entry:
            line_diffs[path.lower()] = entry
    for move in result.moved_to_yosbr:
        if move["content_changed"]:
            modified.append(move["to"])
            entry = _line_diff(move["to"], old_files[move["from"]], new_files[move["to"]])
            if entry:
                line_diffs.setdefault(move["to"].lower(), entry)
    result.modified = sorted(set(modified), key=str.lower)
    result.moved_to_yosbr.sort(key=lambda move: move["to"].lower())
    result.line_diffs = [line_diffs[key] for key in sorted(line_diffs)]
    return result


############################################################
# Whole pack

def _subtree(tree, folder):
    prefix = f"{folder}/"
    return {path[len(prefix):]: data for path, data in tree.items() if path.startswith(prefix)}


def compare(old_tree, new_tree, previous_version, current_version, minecraft):
    """Everything that changed from ``old_tree`` (an earlier release) to ``new_tree``."""
    previous_minecraft = ""
    if "pack.toml" in old_tree:
        try:
            previous_minecraft = str(tomllib.loads(old_tree["pack.toml"].decode("utf-8"))
                                     .get("versions", {}).get("minecraft", ""))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError):
            pass
    newly_added, reenabled = addition_breakdown(old_tree, new_tree)
    return PackDiff(
        previous_version=previous_version or "",
        previous_minecraft=previous_minecraft,
        current_version=current_version,
        minecraft=minecraft,
        mods=diff_category(old_tree, new_tree, "mods"),
        resourcepacks=diff_category(old_tree, new_tree, "resourcepacks"),
        shaderpacks=diff_category(old_tree, new_tree, "shaderpacks"),
        newly_added=newly_added,
        reenabled=reenabled,
        config=diff_config(_subtree(old_tree, "config"), _subtree(new_tree, "config")),
    )

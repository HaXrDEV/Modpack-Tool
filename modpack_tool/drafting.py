"""Draft changelog text from a PackDiff: the "Update overview" and "Config Changes" sections.

Everything here is deterministic. The drafts are a starting point to edit, not
final wording.
"""

import re
from pathlib import PurePosixPath

_MAX_BULLETS_PER_FILE = 4


############################################################
# Update overview

def _quote(name):
    # A non-breaking space after ": " stops YAML from quoting names such as "Mod: Addon".
    return "'" + str(name).strip().replace(": ", ":\u00a0") + "'"


def _quoted_list(names):
    quoted = [_quote(name) for name in names if str(name).strip()]
    if len(quoted) <= 1:
        return "".join(quoted)
    if len(quoted) == 2:
        return f"{quoted[0]} & {quoted[1]}"
    return f"{', '.join(quoted[:-1])} & {quoted[-1]}"


def _dedupe(values):
    seen, result = set(), []
    for value in values:
        key = str(value).strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(str(value).strip())
    return result


def update_overview(diff):
    """One sentence per kind of change, e.g. "Added 'Sodium' & 'Lithium' mods."."""
    lines = []
    if diff.migration:
        lines.append(f"Updated to Minecraft {diff.minecraft}.")

    new_mods = _dedupe(diff.newly_added)
    if new_mods:
        lines.append(f"Added {_quoted_list(new_mods)} mod{'s' * (len(new_mods) > 1)}.")
    if diff.reenabled:
        if re.search(r"\b(alpha|beta)\b", diff.current_version, re.IGNORECASE):
            lines.append(f"Re-added some mods that have become available for {diff.minecraft}.")
        else:
            lines.append("Re-added some mods.")

    removed = _dedupe(named.name for named in diff.mods.removed)
    if removed:
        if diff.migration:
            # No ": " in the sentence, so YAML can keep it unquoted.
            lines.append(f"Temporarily removed incompatible mod{'s' * (len(removed) > 1)} {_quoted_list(removed)}.")
        else:
            lines.append(f"Removed {_quoted_list(removed)} mod{'s' * (len(removed) > 1)}.")

    updated = [label for label, category in (("mods", diff.mods), ("resource packs", diff.resourcepacks),
                                             ("shaderpacks", diff.shaderpacks)) if category.updated]
    if len(updated) == 1:
        lines.append(f"Updated {updated[0]}.")
    elif len(updated) == 2:
        lines.append(f"Updated {updated[0]} & {updated[1]}.")
    elif updated:
        lines.append(f"Updated {', '.join(updated[:-1])}, & {updated[-1]}.")

    for category, singular, plural in ((diff.resourcepacks, "resource pack", "resource packs"),
                                       (diff.shaderpacks, "shaderpack", "shaderpacks")):
        for verb, entries in (("Added", category.added), ("Removed", category.removed)):
            names = _dedupe(named.name for named in entries)
            if names:
                lines.append(f"{verb} {_quoted_list(names)} {singular if len(names) == 1 else plural}.")

    return _dedupe(lines) or ["Maintenance update."]


############################################################
# Mod labels for config files

def _key(text):
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def title_case_filename(filename):
    """"myMod_config.json" -> "My Mod Config"."""
    name = PurePosixPath(str(filename or "")).stem
    name = re.sub(r"[_\-.]+", " ", name)
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return " ".join(token.capitalize() for token in name.split()) or "Unknown"


class ModLabels:
    """Finds the mod a config file belongs to, by matching its path against mod names and slugs."""

    def __init__(self, mods):
        self.index = {}
        self.aliases = []  # (normalized alias, label)
        for mod in mods:
            if mod.category != "mods" or mod.disabled:
                continue
            label = re.sub(r"\s*-\s*$", "", mod.display_name).strip() or mod.display_name
            for alias in (label, mod.slug):
                key = _key(alias)
                if key:
                    self.index.setdefault(key, label)
                    self.aliases.append((key, label))

    def _lookup(self, exact, loose):
        for key in exact:
            if key and key in self.index:
                return self.index[key]
        for key in dict.fromkeys(k for k in loose if k):
            best, best_score = None, 0
            for alias, label in self.aliases:
                if key == alias:
                    return label
                # Containment either way; the shorter key's length scores the match.
                if key in alias or alias in key:
                    score = min(len(key), len(alias))
                    if score > best_score:
                        best, best_score = label, score
            if best and best_score >= 6:
                return best
        return None

    def for_config(self, path):
        """The mod label for a path relative to config/ ("yosbr/config/" prefixes are ignored)."""
        parts = [part for part in str(path).replace("\\", "/").split("/") if part]
        if parts and parts[0].lower() == "yosbr":
            parts = parts[2:] if len(parts) > 1 and parts[1].lower() == "config" else parts[1:]
        filename = parts[-1] if parts else str(path)
        stem = PurePosixPath(filename).stem
        parent = parts[-2] if len(parts) > 1 else ""
        top = parts[0] if len(parts) > 1 else ""
        label = self._lookup([_key(top), _key(stem), _key(filename), _key(parent)], [_key(top), _key(stem)])
        return label or title_case_filename(top or filename)


############################################################
# Config Changes

def _is_yosbr(path):
    return str(path).lower().startswith("yosbr/")


def _array_value(line):
    """The string of a JSON array element line such as '"file/Pack.zip",', else None."""
    match = re.match(r'^\s*"((?:[^"\\]|\\.)*)"\s*,?\s*$', str(line))
    return match.group(1) if match else None


def _strip_line_comment(line):
    output, in_string, escaped = [], False, False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
        elif char == "\\" and in_string:
            escaped = True
        elif char == '"':
            in_string = not in_string
        elif not in_string and line.startswith("//", index):
            break
        output.append(char)
    return "".join(output)


def _array_sections(content, path):
    """{lowercased array value: [dotted names of the arrays containing it]} for JSON/JSON5 files."""
    if PurePosixPath(str(path).lower()).suffix not in (".json", ".json5"):
        return {}
    sections, stack = {}, []

    def pop(kind):
        for index in range(len(stack) - 1, -1, -1):
            if stack[index][0] == kind:
                del stack[index]
                return
        if stack:
            stack.pop()

    for raw in str(content or "").splitlines():
        line = _strip_line_comment(raw).strip()
        if not line:
            continue
        value = _array_value(line)
        names = [name for _, name in stack if name]
        if value is not None and names:
            found = sections.setdefault(value.lower(), [])
            if ".".join(names) not in found:
                found.append(".".join(names))
        opener = re.match(r'^"?([A-Za-z0-9_.\-]+)"?\s*:\s*([\[{])', line)
        if opener:
            stack.append(("array" if opener.group(2) == "[" else "object", opener.group(1)))
        for char in re.sub(r'"(?:[^"\\]|\\.)*"', '""', line):
            if char == "]":
                pop("array")
            elif char == "}":
                pop("object")
    return sections


_KEY_VALUE = re.compile(r'^["\']?([A-Za-z0-9_.\-]+)["\']?\s*[:=]\s*(.+?)\s*,?\s*$')


def _key_values(lines):
    pairs = []
    for line in lines:
        text = str(line).strip()
        if not text or text.startswith(("#", "//", ";", "/*", "*")):
            continue
        match = _KEY_VALUE.match(text)
        if match and not match.group(2).endswith(("{", "[")):
            pairs.append((match.group(1), match.group(2)))
    return pairs


def _file_bullets(entry, label):
    path = entry["path"]
    default = "default " if _is_yosbr(path) else ""
    if "/fancymenu/customization/" in f"/{path.lower()}/":
        return [f"- Adjusted {default}FancyMenu customizations: [{label}]"]

    bullets, used_added, used_removed = [], set(), set()
    added_values = {_array_value(line) for line in entry["added_lines"]} - {None}
    removed_values = {_array_value(line) for line in entry["removed_lines"]} - {None}
    for lines, used, content_key, verb in (
        (entry["added_lines"], used_added, "current_content", "Added"),
        (entry["removed_lines"], used_removed, "previous_content", "Removed"),
    ):
        sections = None
        for index, line in enumerate(lines):
            value = _array_value(line)
            if value is None:
                continue
            used.add(index)
            sections = sections if sections is not None else _array_sections(entry[content_key], path)
            where = ", ".join(sections.get(value.lower(), []))
            shown = value[5:] if value.startswith("file/") else value
            if value in added_values and value in removed_values:
                if verb == "Added":  # The value only moved within its list.
                    bullets.append(f"- Reordered {default}{shown}{f' in section {where}' if where else ''}: [{label}]")
                continue
            preposition = "to" if verb == "Added" else "from"
            bullets.append(f"- {verb} {default}{shown}{f' {preposition} section {where}' if where else ''}: [{label}]")

    removed_pairs = _key_values(line for i, line in enumerate(entry["removed_lines"]) if i not in used_removed)
    added_pairs = _key_values(line for i, line in enumerate(entry["added_lines"]) if i not in used_added)
    for key, new_value in added_pairs:
        match = next((pair for pair in removed_pairs if pair[0] == key), None)
        if match:
            removed_pairs.remove(match)
            if match[1] != new_value:
                bullets.append(f'- Changed {default}"{key}" from {match[1]} to {new_value}: [{label}]')
        else:
            bullets.append(f'- Set {default}"{key}" to {new_value}: [{label}]')
    for key, _ in removed_pairs:
        bullets.append(f'- Removed the "{key}" setting: [{label}]')

    bullets = list(dict.fromkeys(bullets))  # The same value can be added to several lists.
    if not bullets:
        return [f"- Updated {default}config values: [{label}]"]
    if len(bullets) > _MAX_BULLETS_PER_FILE:
        extra = len(bullets) - _MAX_BULLETS_PER_FILE
        bullets = bullets[:_MAX_BULLETS_PER_FILE]
        bullets.append(f"- …and {extra} more change{'s' * (extra != 1)} in {PurePosixPath(path).name}: [{label}]")
    return bullets


def config_changes(diff, labels):
    """Config Changes bullets, each ending with ": [Mod Label]" (the wiki shows labels as code)."""
    config = diff.config
    bullets = []
    for move in config.moved_to_yosbr:
        bullets.append(f"- Moved {move['from']} to YOSBR so it applies as a default on first launch: "
                       f"[{labels.for_config(move['to'])}]")
    moved_from = {move["from"].lower() for move in config.moved_to_yosbr}
    for path in config.removed:
        if path.lower() not in moved_from:
            bullets.append(f"- Removed config file {path}: [{labels.for_config(path)}]")
    for entry in config.line_diffs:
        bullets.extend(_file_bullets(entry, labels.for_config(entry["path"])))
    return bullets

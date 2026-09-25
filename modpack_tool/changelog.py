"""Changelog files: the hand-written YAML, the release record for the wiki, and release notes.

Each release has ``Changelogs/<stem>.yml`` (written by you, optionally drafted by
the tool) and ``Changelogs/data/<stem>.json``, a presentation-free record that
the wiki (CrismPack/Wiki docs/.vitepress/changelog.mjs) renders. Keep the
record's keys in sync with that renderer.
"""

import json
from datetime import date
from io import StringIO
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from . import pack, ui
from .diff import tagged
from .ui import ToolError
from .version import format_version_anchor, is_mc_prefixed_version, is_prerelease, minecraft_content_key

# (YAML key, record key) for every hand-written section, in display order.
SECTIONS = (
    ("Update overview", "overview"),
    ("Changes/Improvements", "changes"),
    ("Bug Fixes", "bugfixes"),
    ("Script/Datapack changes", "scriptChanges"),
    ("Config Changes", "configChanges"),
)
# Metadata keys older templates wrote; loader info now comes from pack.toml.
_METADATA_KEYS = {"version", "mc_version", "Mod loader", "Mod loader version", "Fabric version"}
# The old template's example line, which must never reach a changelog.
_PLACEHOLDER = ": [mod], [Client]"

_TEMPLATE = """\
# Changelog for {pack} {version}. Empty sections are left out.
# "Draft changelog" in the tool can fill 'Update overview' and 'Config Changes' for you.
Update overview:
Changes/Improvements:
Bug Fixes:
Script/Datapack changes:
Config Changes:
"""


def _yaml():
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


############################################################
# File names

def changelog_stem(version, minecraft):
    """Name of a release's files: "4.11.1+1.21.11", or just "26.1.1-1.2" for MC-scheme versions."""
    if is_mc_prefixed_version(version):
        return str(version)
    return f"{version}+{minecraft}"


def changelog_filename(version, minecraft, ext="yml"):
    return f"{changelog_stem(version, minecraft)}.{ext}"


def parse_changelog_filename(filename):
    """(version, minecraft) from a changelog filename, or (None, None)."""
    stem = Path(str(filename or "")).stem
    if "+" in stem:
        version, _, minecraft = stem.partition("+")
        return (version.strip(), minecraft.strip()) if version.strip() and minecraft.strip() else (None, None)
    if is_mc_prefixed_version(stem):
        return stem, stem.partition("-")[0]
    return None, None


def changelog_path(project, version=None, minecraft=None):
    """The changelog YAML for a version (the current one by default), existing or not."""
    version = version or project.version
    minecraft = minecraft or project.minecraft
    canonical = project.changelog_dir / changelog_filename(version, minecraft)
    if not canonical.exists():
        for name in (f"{version}+{minecraft}.yml", f"{version}.yml", f"{version}+{minecraft}.yaml"):
            if (project.changelog_dir / name).exists():
                return project.changelog_dir / name
    return canonical


def record_path(project, version=None, minecraft=None):
    return project.data_dir / changelog_filename(version or project.version, minecraft or project.minecraft, "json")


############################################################
# The hand-written YAML

def create_changelog(project, version=None, minecraft=None):
    """Create the changelog template for a version unless it exists; returns its path."""
    path = changelog_path(project, version, minecraft)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        pack.write_text(path, _TEMPLATE.format(pack=project.name, version=version or project.version))
    return path


def load_changelog(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = _yaml().load(f)
    except YAMLError as ex:
        raise ToolError(f"{Path(path).name} isn't valid YAML, so it can't be read:\n{ex}") from ex
    if data is None:
        return _yaml().load("{}")
    if not isinstance(data, dict):
        raise ToolError(f"{Path(path).name} should contain sections such as 'Update overview:'.")
    return data


def save_changelog(path, data):
    buffer = StringIO()
    _yaml().dump(data, buffer)
    pack.write_text(path, buffer.getvalue())


def _as_text(item):
    # "- Sodium: fixed flicker" is a one-entry mapping in YAML; keep it as written.
    if isinstance(item, dict):
        return ", ".join(f"{key}: {value}" for key, value in item.items())
    return str(item)


def section_lines(value):
    """A section's bullets as plain strings (without "- ")."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        lines = value.splitlines()
    elif isinstance(value, dict):
        lines = [_as_text({key: item}) for key, item in value.items()]
    elif isinstance(value, (list, tuple)):
        lines = [_as_text(item) for item in value]
    else:
        lines = [str(value)]
    cleaned = []
    for line in lines:
        text = str(line).strip()
        if text.startswith("- "):
            text = text[2:].strip()
        if text and text != _PLACEHOLDER.strip() and not text.endswith(_PLACEHOLDER):
            cleaned.append(text)
    return cleaned


def unknown_sections(data):
    """Keys with content that no output uses (e.g. a typo), so they can be flagged."""
    known = {key for key, _ in SECTIONS} | _METADATA_KEYS
    return [key for key, value in data.items() if key not in known and section_lines(value)]


def is_empty(data):
    return not any(section_lines(data.get(key)) for key, _ in SECTIONS)


############################################################
# Release record

def _record_diff(category, side_tags):
    return {
        "added": [tagged(named, side_tags) for named in category.added],
        "removed": [tagged(named, side_tags) for named in category.removed],
        "updated": [{"name": name, "from": before, "to": after} for name, before, after in category.updated],
    }


def build_record(project, data, diff, released=None):
    """The presentation-free record of the current release (the wiki's input)."""
    side_tags = project.settings.side_tags
    record = {
        "pack": project.name,
        "version": project.version,
        "minecraft": project.minecraft,
        "loader": {"name": project.loader_label, "version": project.loader_version},
        "released": released or date.today().isoformat(),
        "prerelease": is_prerelease(project.version),
        "comparedTo": None,
    }
    if diff and diff.previous_version:
        record["comparedTo"] = {"version": diff.previous_version,
                                "minecraft": diff.previous_minecraft or project.minecraft}
    for key, record_key in SECTIONS:
        record[record_key] = section_lines(data.get(key))
    for category in ("mods", "resourcepacks", "shaderpacks"):
        record[category] = (_record_diff(getattr(diff, category), side_tags) if diff
                            else {"added": [], "removed": [], "updated": []})
    return record


def write_record(project, record):
    path = record_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    pack.write_text(path, json.dumps(record, indent=2, ensure_ascii=False))
    return path


############################################################
# Release notes (uploaded to CurseForge and Modrinth by the publish workflow)

def changelog_url(project):
    template = project.settings.changelog_url.strip()
    if not template:
        return ""
    try:
        return template.format(mc_group=minecraft_content_key(project.minecraft), mc=project.minecraft,
                               version=project.version, anchor=format_version_anchor(project.version))
    except (KeyError, IndexError, ValueError) as ex:
        ui.warn(f"changelog_url has an unknown placeholder ({ex}); leaving the link out.")
        return ""


def release_notes(project, data, platform):
    """Markdown release notes for "curseforge" or "modrinth"."""
    blocks = []
    if is_prerelease(project.version):
        blocks.append("**This is a pre-release. Here be dragons!**")
    overview = section_lines(data.get("Update overview"))
    if overview:
        blocks.append("\n".join(f"- {line}" for line in overview))
    else:
        for key, heading in (("Changes/Improvements", "Changes/Improvements ⭐"), ("Bug Fixes", "Bug Fixes 🪲")):
            lines = section_lines(data.get(key))
            if lines:
                blocks.append(f"### {heading}\n\n" + "\n".join(f"- {line}" for line in lines))
    url = changelog_url(project)
    if url:
        link = f"**[[Full Changelog]]({url})**"
        blocks.append(f"#### {link}" if platform == "curseforge" else link)
    footer = project.settings.curseforge_notes_footer if platform == "curseforge" else project.settings.modrinth_notes_footer
    if footer and str(footer).strip():
        blocks.append(str(footer).strip())
    return "\n\n".join(blocks) + "\n"


def write_release_notes(project, data):
    paths = []
    for platform, filename in (("curseforge", "CurseForge-Release.md"), ("modrinth", "Modrinth-Release.md")):
        path = project.root / filename
        pack.write_text(path, release_notes(project, data, platform))
        paths.append(path)
    return paths

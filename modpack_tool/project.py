"""A modpack project (paths, pack.toml info, settings) and the tool's project registry."""

import os
import shutil
from dataclasses import dataclass, field, fields
from io import StringIO
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedSeq
from ruamel.yaml.error import YAMLError
from ruamel.yaml.representer import RoundTripRepresenter
from ruamel.yaml.scalarstring import DoubleQuotedScalarString, LiteralScalarString, ScalarString

from . import pack, ui
from .gitrepo import GitRepo
from .ui import ToolError
from .version import is_mc_prefixed_version

TOOL_DIR = Path(__file__).resolve().parent.parent
# MODPACK_TOOL_CONFIG points the tool at another registry (handy for testing).
TOOL_CONFIG_PATH = Path(os.environ.get("MODPACK_TOOL_CONFIG") or TOOL_DIR / "tool_config.yml")
CF_KEY_FILE = TOOL_DIR / "cf-api-key.txt"
CACHE_DIR = TOOL_DIR / "cache"
SETTINGS_FILE = "modpack-tool.yml"
LEGACY_SETTINGS_FILE = "settings.yml"
TEMPLATE_PATH = Path(__file__).with_name("settings_template.yml")
EXPORT_KINDS = ("curseforge", "modrinth", "server")
ALPHA_POLICIES = ("prompt", "never", "always")


############################################################
# YAML

class _SettingsRepresenter(RoundTripRepresenter):
    """Writes booleans as True/False, matching the template."""


_SettingsRepresenter.yaml_representers = dict(RoundTripRepresenter.yaml_representers)
_SettingsRepresenter.add_representer(
    bool, lambda rep, data: rep.represent_scalar("tag:yaml.org,2002:bool", "True" if data else "False"))


def _settings_yaml():
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096  # Never wrap long values such as banner URLs.
    yaml.Representer = _SettingsRepresenter
    return yaml


def _dump(yaml, data):
    buffer = StringIO()
    yaml.dump(data, buffer)
    return buffer.getvalue()


def _styled_like(template_value, value):
    """Give a plain imported value the template's YAML style (flow lists, quoted strings)."""
    if isinstance(value, list):
        # Rebuilt, because a loaded list carries the comments that followed it,
        # which the template already has.
        flow = any(isinstance(seq, CommentedSeq) and seq.fa.flow_style() for seq in (value, template_value))
        styled = CommentedSeq(list(value))
        if flow or not value:
            styled.fa.set_flow_style()
        return styled
    if isinstance(value, str) and not isinstance(value, ScalarString):
        if "\n" in value:
            return LiteralScalarString(value)
        if isinstance(template_value, DoubleQuotedScalarString):
            return DoubleQuotedScalarString(value)
    return value


############################################################
# Settings

@dataclass
class Settings:
    exports: list = field(default_factory=lambda: ["curseforge", "modrinth"])
    server_template: str = "Server Pack"
    server_exclude: list = field(default_factory=list)
    mc_prefixed_versions: bool = False
    alpha_updates: str = "prompt"
    side_tags: bool = False
    changelog_url: str = ""
    curseforge_notes_footer: str = ""
    modrinth_notes_footer: str = ""


def _coerce(name, value, default, notes):
    """A setting's value as the type of its default; empty values mean the default."""
    if value is None or value == "":
        return default
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("true", "yes", "on", "1"):
            return True
        if text in ("false", "no", "off", "0"):
            return False
        notes.append(f"{name}: '{value}' isn't True or False; using {default}.")
        return default
    if isinstance(default, list):
        values = value if isinstance(value, list) else [value]
        return [str(entry).strip() for entry in values if str(entry).strip()]
    if isinstance(value, (list, dict)):
        notes.append(f"{name} should be a single value; using the default.")
        return default
    return str(value)


def default_changelog_url(pack_name):
    slug = str(pack_name).lower().split(" ", 1)[0] or "pack"
    return f"https://crismpack.net/{slug}/changelogs/{{mc_group}}#{{anchor}}"


def load_settings(root, pack_name):
    """Load ``modpack-tool.yml``, creating it on first use; returns (Settings, notes).

    The file is kept in the template's layout (all keys, comments, order) while
    preserving values. Without one, values come from the old tool's settings.yml.
    """
    root = Path(root)
    path = root / SETTINGS_FILE
    yaml = _settings_yaml()
    template = yaml.load(TEMPLATE_PATH.read_text(encoding="utf-8"))
    notes = []
    if path.is_file():
        current = path.read_text(encoding="utf-8")
        try:
            values = yaml.load(current) or {}
        except YAMLError as ex:
            raise ToolError(f"{path} isn't valid YAML: {ex}\n"
                            "Tip: put Windows paths in single quotes, e.g. 'D:\\Servers\\Pack'.") from ex
        if not isinstance(values, dict):
            raise ToolError(f"{path} should contain settings such as 'exports: [curseforge]'.")
    else:
        current = ""
        legacy = root / LEGACY_SETTINGS_FILE
        if legacy.is_file():
            values = import_legacy_settings(legacy, root, pack_name, notes)
        else:
            values = {"changelog_url": default_changelog_url(pack_name)}
            notes.append(f"Created {SETTINGS_FILE}; review it to configure this pack.")

    unknown = [key for key in values if key not in template]
    for key in template:
        if key in values:
            template[key] = _styled_like(template[key], values[key])
    rendered = _dump(yaml, template)
    if rendered != current:
        pack.write_text(path, rendered)
    if unknown:
        notes.append(f"Removed keys that aren't settings from {SETTINGS_FILE}: {', '.join(unknown)}")

    settings = Settings()
    for item in fields(Settings):
        setattr(settings, item.name, _coerce(item.name, template[item.name], getattr(settings, item.name), notes))

    bad = [kind for kind in settings.exports if kind not in EXPORT_KINDS]
    if bad:
        notes.append(f"Unknown exports ignored: {', '.join(bad)} (use {', '.join(EXPORT_KINDS)}).")
        settings.exports = [kind for kind in settings.exports if kind in EXPORT_KINDS]
    if settings.alpha_updates not in ALPHA_POLICIES:
        notes.append(f"alpha_updates '{settings.alpha_updates}' is not one of {', '.join(ALPHA_POLICIES)}; using prompt.")
        settings.alpha_updates = "prompt"
    return settings, notes


def import_legacy_settings(legacy_path, root, pack_name, notes):
    """Translate the old tool's settings.yml into the new keys (asks to confirm the wiki link)."""
    try:
        old = YAML(typ="safe").load(Path(legacy_path).read_text(encoding="utf-8")) or {}
    except YAMLError as ex:
        notes.append(f"Couldn't read the old {LEGACY_SETTINGS_FILE} ({ex}); starting from the defaults.")
        old = {}
    if not isinstance(old, dict):
        old = {}

    def flag(key, default=False):
        return bool(old.get(key, default))

    exports = []
    if flag("export_client", True):
        if flag("client_export_multi_platform") or flag("breakneck_fixes"):
            exports += ["curseforge", "modrinth"]
        elif str(old.get("client_export_format", "curseforge")).lower() == "modrinth":
            exports.append("modrinth")
        else:
            exports.append("curseforge")
    if flag("export_server"):
        exports.append("server")

    policy = str(old.get("alpha_update_policy", "prompt")).lower()
    policy = {"always_skip": "never", "skip": "never", "never": "never",
              "always_allow": "always", "allow": "always"}.get(policy, "prompt")

    banner = str(old.get("bh_banner") or "").strip()
    footer = (LiteralScalarString(f"<br>\n\n[![BisectHosting Banner]({banner})](https://bisecthosting.com/CRISM)\n")
              if banner else "")

    # Exclusions were jar filenames, which change on every update; use slugs where possible.
    mods = pack.load_mods(Path(root) / "Packwiz", ("mods",))
    by_filename = {mod.filename: mod.slug for mod in mods}

    def squash(text):
        return "".join(ch for ch in text.lower() if ch.isalnum())

    exclude = []
    for entry in old.get("server_mods_remove_list") or []:
        entry = str(entry)
        if entry in by_filename:
            exclude.append(by_filename[entry])
            continue
        # An outdated filename usually still starts with the mod's slug.
        guesses = [mod.slug for mod in mods if len(squash(mod.slug)) >= 5 and squash(entry).startswith(squash(mod.slug))]
        if len(guesses) == 1:
            exclude.append(guesses[0])
            notes.append(f"server_exclude: '{entry}' is an old filename; now excluding the mod '{guesses[0]}'.")
        else:
            exclude.append(entry)
            notes.append(f"server_exclude: '{entry}' matches no current mod; check it.")

    url = default_changelog_url(pack_name)
    ui.info(f"Release notes will link to the wiki as: {url}")
    slug = ui.ask("Wiki folder for this pack (the part after crismpack.net/)", url.split("/")[3])
    url = f"https://crismpack.net/{slug.strip('/')}/changelogs/{{mc_group}}#{{anchor}}"

    notes.append(f"Imported settings from {LEGACY_SETTINGS_FILE} into {SETTINGS_FILE}; "
                 f"{LEGACY_SETTINGS_FILE} is no longer used and can be deleted.")
    return {
        "exports": exports,
        "server_exclude": exclude,
        "mc_prefixed_versions": flag("mc_prefixed_versions"),
        "alpha_updates": policy,
        "side_tags": flag("changelog_side_tag", True),
        "changelog_url": url,
        "curseforge_notes_footer": footer,
        "modrinth_notes_footer": "",
    }


############################################################
# Project

@dataclass
class Project:
    root: Path
    settings: Settings
    packwiz: pack.Packwiz
    git: GitRepo
    name: str = ""
    version: str = ""
    minecraft: str = ""
    loader: str = ""
    loader_version: str = ""
    acceptable_versions: list = field(default_factory=list)

    @property
    def pack_dir(self):
        return self.root / "Packwiz"

    @property
    def changelog_dir(self):
        return self.root / "Changelogs"

    @property
    def data_dir(self):
        return self.changelog_dir / "data"

    @property
    def export_dir(self):
        return self.root / "Export"

    @property
    def server_template_dir(self):
        return self.root / self.settings.server_template

    @property
    def loader_label(self):
        return pack.LOADER_LABELS.get(self.loader, self.loader.capitalize() or "Unknown loader")

    @property
    def mc_prefixed(self):
        """Whether to suggest "<mc>-<release>" versions."""
        return self.settings.mc_prefixed_versions or is_mc_prefixed_version(self.version)

    def reload(self):
        """Re-read pack.toml (after packwiz or the tool changed it)."""
        pack_toml = pack.read_pack_toml(self.pack_dir)
        missing = [key for key in ("name", "version") if not pack_toml.get(key)]
        if not (pack_toml.get("versions") or {}).get("minecraft"):
            missing.append("versions.minecraft")
        if missing:
            raise ToolError(f"{self.pack_dir / 'pack.toml'} is missing: {', '.join(missing)}.")
        self.name = str(pack_toml["name"])
        self.version = str(pack_toml["version"])
        self.minecraft = str(pack_toml["versions"]["minecraft"])
        self.loader, self.loader_version = pack.loader_of(pack_toml)
        self.acceptable_versions = [
            str(v) for v in (pack_toml.get("options") or {}).get("acceptable-game-versions") or []]


def open_project(root, config):
    """Load a project folder (the one containing Packwiz/pack.toml); returns (project, notes)."""
    root = Path(root).resolve()
    if not (root / "Packwiz" / "pack.toml").is_file():
        raise ToolError(f"No Packwiz\\pack.toml in {root}. Choose the modpack folder that contains 'Packwiz'.")
    pack_toml = pack.read_pack_toml(root / "Packwiz")
    settings, notes = load_settings(root, pack_toml.get("name", root.name))
    project = Project(
        root=root,
        settings=settings,
        packwiz=pack.Packwiz(resolve_packwiz_exe(config), root / "Packwiz"),
        git=GitRepo(root),
    )
    project.reload()
    return project, notes


############################################################
# Tool configuration (registry of projects)

@dataclass
class ToolConfig:
    last_used_project: str = ""
    packwiz_exe_path: str = ""
    curseforge_api_key: str = ""
    projects: list = field(default_factory=list)  # Absolute project roots.
    read_only: bool = False  # Set when the file couldn't be read, so it isn't overwritten.


def load_tool_config():
    config = ToolConfig()
    if not TOOL_CONFIG_PATH.is_file():
        return config
    try:
        raw = YAML(typ="safe").load(TOOL_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError("expected keys such as 'projects:'")
    except Exception as ex:  # A broken file shouldn't stop the tool from starting.
        ui.warn(f"Could not read {TOOL_CONFIG_PATH}: {ex}\n"
                "  It is left untouched until you fix it (tip: single quotes around Windows paths).")
        config.read_only = True
        return config
    config.last_used_project = str(raw.get("last_used_project") or "")
    config.packwiz_exe_path = str(raw.get("packwiz_exe_path") or "")
    config.curseforge_api_key = str(raw.get("curseforge_api_key") or "")
    projects = raw.get("projects")
    for entry in projects if isinstance(projects, list) else []:
        root = entry.get("root") if isinstance(entry, dict) else entry
        if root:
            config.projects.append(str(root))
    return config


def save_tool_config(config):
    if config.read_only:
        return
    data = {
        "last_used_project": config.last_used_project,
        "packwiz_exe_path": config.packwiz_exe_path,
        "curseforge_api_key": config.curseforge_api_key,
        "projects": [{"name": Path(root).name, "root": root} for root in config.projects],
    }
    yaml = YAML()
    yaml.width = 4096  # Keep long paths on one line.
    with open(TOOL_CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(data, f)


def _same_path(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def remember_project(config, root):
    root = str(Path(root).resolve())
    if not any(_same_path(root, known) for known in config.projects):
        config.projects.append(root)
    config.last_used_project = root
    save_tool_config(config)


def forget_project(config, root):
    config.projects = [known for known in config.projects if not _same_path(root, known)]
    if config.last_used_project and _same_path(config.last_used_project, root):
        config.last_used_project = ""
    save_tool_config(config)


def resolve_packwiz_exe(config):
    """packwiz from tool_config.yml, then PATH, then Go's default install folder."""
    if config.packwiz_exe_path.strip():
        return config.packwiz_exe_path.strip()
    return shutil.which("packwiz") or str(Path.home() / "go" / "bin" / "packwiz.exe")


def curseforge_key(config):
    """A personal CurseForge API key from tool_config.yml or cf-api-key.txt, if any."""
    if config.curseforge_api_key.strip():
        return config.curseforge_api_key.strip()
    if CF_KEY_FILE.is_file():
        return CF_KEY_FILE.read_text(encoding="utf-8").strip() or None
    return None

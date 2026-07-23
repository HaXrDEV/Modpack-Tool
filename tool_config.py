import os
from dataclasses import dataclass, field
from typing import List, Optional


############################################################
# Tool-level configuration (project registry)

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
TOOL_CONFIG_PATH = os.path.join(TOOL_DIR, "tool_config.yml")
DEFAULT_PACKWIZ_EXE = os.path.join(os.path.expanduser("~"), "go", "bin", "packwiz.exe")
GH_TOKEN_FILE = os.path.join(TOOL_DIR, "gh-token.txt")
CF_API_KEY_FILE = os.path.join(TOOL_DIR, "cf-api-key.txt")


@dataclass
class RegisteredProject:
    """A modpack project known to the tool.

    Attributes:
        name: Display name shown in the project picker.
        root: Absolute path to the modpack project root (the folder that
            contains ``Packwiz/pack.toml`` and ``settings.yml``).
    """
    name: str
    root: str


@dataclass
class ToolConfig:
    """Global tool configuration stored in ``tool_config.yml`` next to the scripts.

    This is tool-wide state (which projects exist, which was used last), as
    opposed to the per-project ``settings.yml`` that lives at each modpack
    project root.
    """
    last_used_project: str = ""
    packwiz_exe_path: str = ""
    github_token: str = ""
    curseforge_api_key: str = ""
    projects: List[RegisteredProject] = field(default_factory=list)


def _normalize_root_key(root: str) -> str:
    """Return a case/format-insensitive comparison key for a project root path."""
    return os.path.normcase(os.path.abspath(str(root or "")))


def load_tool_config(yaml_instance) -> ToolConfig:
    """Load ``tool_config.yml`` and return a populated ``ToolConfig``.

    Returns default (empty) configuration when the file does not exist or
    cannot be parsed, so a fresh clone starts with an empty project registry.

    Args:
        yaml_instance: A ``ruamel.yaml.YAML`` (or compatible) instance.
    """
    cfg = ToolConfig()
    if not os.path.isfile(TOOL_CONFIG_PATH):
        return cfg
    try:
        with open(TOOL_CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = yaml_instance.load(f) or {}
    except Exception as ex:
        print(f"[ToolConfig] Could not read {TOOL_CONFIG_PATH}: {ex}. Using defaults.")
        return cfg

    cfg.last_used_project = str(raw.get("last_used_project", "") or "")
    cfg.packwiz_exe_path = str(raw.get("packwiz_exe_path", "") or "")
    cfg.github_token = str(raw.get("github_token", "") or "")
    cfg.curseforge_api_key = str(raw.get("curseforge_api_key", "") or "")
    for entry in raw.get("projects", []) or []:
        if not isinstance(entry, dict):
            continue
        root = str(entry.get("root", "") or "")
        if not root:
            continue
        name = str(entry.get("name", "") or "") or os.path.basename(os.path.normpath(root))
        cfg.projects.append(RegisteredProject(name=name, root=root))
    return cfg


def save_tool_config(cfg: ToolConfig, yaml_instance) -> None:
    """Write the tool configuration back to ``tool_config.yml``."""
    data = {
        "last_used_project": cfg.last_used_project,
        "packwiz_exe_path": cfg.packwiz_exe_path,
        "github_token": cfg.github_token,
        "curseforge_api_key": cfg.curseforge_api_key,
        "projects": [{"name": p.name, "root": p.root} for p in cfg.projects],
    }
    try:
        with open(TOOL_CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml_instance.dump(data, f)
    except OSError as ex:
        print(f"[ToolConfig] Could not save {TOOL_CONFIG_PATH}: {ex}")


def find_project(cfg: ToolConfig, root: str) -> Optional[RegisteredProject]:
    """Return the registered project matching ``root`` (path-normalized), or None."""
    key = _normalize_root_key(root)
    for project in cfg.projects:
        if _normalize_root_key(project.root) == key:
            return project
    return None


def register_project(cfg: ToolConfig, root: str) -> RegisteredProject:
    """Add a project to the registry (no-op when already registered).

    The path is stored as typed (absolute-normalized) while duplicate detection
    uses a case-insensitive key. Validation of the project layout is the
    caller's responsibility (see ``project_context.validate_project_root``).

    Returns:
        The newly added (or already existing) ``RegisteredProject``.
    """
    root = os.path.abspath(str(root))
    existing = find_project(cfg, root)
    if existing is not None:
        return existing
    project = RegisteredProject(name=os.path.basename(os.path.normpath(root)), root=root)
    cfg.projects.append(project)
    return project


def remove_project(cfg: ToolConfig, project: RegisteredProject) -> None:
    """Remove a project from the registry and clear last-used if it pointed there."""
    cfg.projects = [p for p in cfg.projects if p is not project]
    if _normalize_root_key(cfg.last_used_project) == _normalize_root_key(project.root):
        cfg.last_used_project = ""


def resolve_packwiz_exe(cfg: ToolConfig) -> str:
    """Return the packwiz executable path (config override or the Go default).

    Missing executables produce a warning rather than an error so that menu
    actions that never shell out to packwiz remain usable.
    """
    exe = cfg.packwiz_exe_path.strip() or DEFAULT_PACKWIZ_EXE
    if not os.path.isfile(exe):
        print(f"[ToolConfig] Warning: packwiz executable not found at '{exe}'. "
              f"Install packwiz or set 'packwiz_exe_path' in {TOOL_CONFIG_PATH}.")
    return exe


def _resolve_secret(config_value: str, fallback_file: str) -> Optional[str]:
    """Return a secret from its config value, then its gitignored file, then None."""
    if config_value.strip():
        return config_value.strip()
    try:
        with open(fallback_file, "r", encoding="utf-8") as f:
            return f.read().strip() or None
    except FileNotFoundError:
        return None


def resolve_github_token(cfg: ToolConfig) -> Optional[str]:
    """Return a GitHub token from config, then gh-token.txt, then None.

    ``None`` means the caller should fall back to an interactive prompt.
    """
    return _resolve_secret(cfg.github_token, GH_TOKEN_FILE)


def resolve_curseforge_key(cfg: ToolConfig) -> Optional[str]:
    """Return a CurseForge API key from config, then cf-api-key.txt, then None.

    ``None`` means the packwiz community default key stays in effect.
    """
    return _resolve_secret(cfg.curseforge_api_key, CF_API_KEY_FILE)

import os
import shutil
from dataclasses import dataclass
from typing import List

import toml


############################################################
# Per-project paths and activation helpers

DATA_DIR_NAME = ".modpack-tool"
LEGACY_TOOL_DIR_NAME = "Modpack-CLI-Tool"


@dataclass
class ProjectPaths:
    """All filesystem locations derived from a modpack project root.

    A "project" is the folder that contains ``Packwiz/pack.toml`` and
    ``settings.yml`` (plus ``Changelogs``, ``Export``, ``Server Pack``).
    Tool working data lives under ``<root>/.modpack-tool/``.
    """
    root: str
    packwiz_path: str
    serverpack_path: str
    bcc_client_config_path: str
    bcc_server_config_path: str
    export_path: str
    tempfolder_path: str
    temp_mods_path: str
    settings_path: str
    mods_path: str
    changelog_dir_path: str
    crash_assistant_config_path: str
    crash_assistant_markdown_path: str
    data_dir: str
    tempgit_path: str
    prev_release_path: str
    pack_manifest_path: str


def compute_project_paths(root: str) -> ProjectPaths:
    """Build a ``ProjectPaths`` from a project root. Pure path math, no I/O."""
    root = os.path.abspath(str(root))
    packwiz_path = os.path.join(root, "Packwiz")
    serverpack_path = os.path.join(root, "Server Pack")
    export_path = os.path.join(root, "Export")
    tempfolder_path = os.path.join(export_path, "temp")
    data_dir = os.path.join(root, DATA_DIR_NAME)
    return ProjectPaths(
        root=root,
        packwiz_path=packwiz_path,
        serverpack_path=serverpack_path,
        bcc_client_config_path=os.path.join(packwiz_path, "config", "bcc.json"),
        bcc_server_config_path=os.path.join(serverpack_path, "config", "bcc.json"),
        export_path=export_path,
        tempfolder_path=tempfolder_path,
        temp_mods_path=os.path.join(tempfolder_path, "mods"),
        settings_path=os.path.join(root, "settings.yml"),
        mods_path=os.path.join(packwiz_path, "mods"),
        changelog_dir_path=os.path.join(root, "Changelogs"),
        crash_assistant_config_path=os.path.join(packwiz_path, "config", "crash_assistant", "modlist.json"),
        crash_assistant_markdown_path=os.path.join(root, "modlist.md"),
        data_dir=data_dir,
        tempgit_path=os.path.join(data_dir, "tempgit"),
        prev_release_path=os.path.join(data_dir, "prev_release"),
        pack_manifest_path=os.path.join(packwiz_path, "pack.toml"),
    )


def prompt_yes(question: str) -> bool:
    """Ask a yes/no question where pressing Enter means yes."""
    return input(question).strip().lower() in ("", "y", "yes")


def normalize_drag_drop_path(raw_path: str) -> str:
    """Normalize terminal drag-and-drop paths (often wrapped in quotes)."""
    cleaned_path = str(raw_path or "").strip()
    if len(cleaned_path) >= 2 and cleaned_path[0] == cleaned_path[-1] and cleaned_path[0] in ("'", '"'):
        cleaned_path = cleaned_path[1:-1].strip()
    cleaned_path = os.path.expanduser(os.path.expandvars(cleaned_path))
    return os.path.normpath(cleaned_path) if cleaned_path else ""


def validate_project_root(root: str) -> List[str]:
    """Return a list of human-readable problems with a candidate project root.

    An empty list means the root is usable. A missing ``settings.yml`` is not
    reported here because ``preflight_project`` can create it from the template.
    """
    problems = []
    root = os.path.abspath(str(root or ""))
    if not os.path.isdir(root):
        problems.append(f"'{root}' is not a directory.")
        return problems
    if not os.path.isfile(compute_project_paths(root).pack_manifest_path):
        problems.append(
            f"No 'Packwiz\\pack.toml' found under '{root}'. "
            "Point the tool at the modpack project root (the folder containing 'Packwiz')."
        )
    return problems


def read_pack_manifest(paths: ProjectPaths) -> dict:
    """Read and parse ``Packwiz/pack.toml`` with friendly error messages.

    Raises:
        RuntimeError: When the manifest is missing, unreadable, or lacks the
            required ``version``/``name``/``versions.minecraft`` keys.
    """
    try:
        with open(paths.pack_manifest_path, "r", encoding="utf-8") as f:
            pack_toml = toml.load(f)
    except FileNotFoundError:
        raise RuntimeError(f"pack.toml not found at '{paths.pack_manifest_path}'.")
    except (OSError, toml.TomlDecodeError) as ex:
        raise RuntimeError(f"Could not parse '{paths.pack_manifest_path}': {ex}")

    missing = [key for key in ("version", "name") if key not in pack_toml]
    if "minecraft" not in pack_toml.get("versions", {}):
        missing.append("versions.minecraft")
    if missing:
        raise RuntimeError(
            f"'{paths.pack_manifest_path}' is missing required key(s): {', '.join(missing)}."
        )
    return pack_toml


def ensure_gitignore_entry(root: str) -> None:
    """Make sure the project's .gitignore ignores the tool's data directory."""
    gitignore_path = os.path.join(root, ".gitignore")
    entry_variants = {DATA_DIR_NAME, DATA_DIR_NAME + "/"}
    existing = ""
    if os.path.isfile(gitignore_path):
        try:
            with open(gitignore_path, "r", encoding="utf-8") as f:
                existing = f.read()
        except OSError as ex:
            print(f"[Project] Could not read {gitignore_path}: {ex}")
            return
        if any(line.strip() in entry_variants for line in existing.splitlines()):
            return
    try:
        with open(gitignore_path, "a", encoding="utf-8") as f:
            prefix = "" if (not existing or existing.endswith("\n")) else "\n"
            f.write(f"{prefix}# Modpack tool working data\n{DATA_DIR_NAME}/\n")
        print(f"[Project] Added '{DATA_DIR_NAME}/' to {gitignore_path}")
    except OSError as ex:
        print(f"[Project] Could not update {gitignore_path}: {ex}")


def _dir_has_content(path: str) -> bool:
    try:
        return bool(os.listdir(path))
    except OSError:
        return False


def migrate_legacy_cache(paths: ProjectPaths) -> None:
    """Offer to move cache data from the old embedded-tool layout.

    Older versions stored comparison snapshots and previous releases inside
    ``<root>/Modpack-CLI-Tool/``. If those folders still hold data and the new
    ``.modpack-tool/`` locations are empty, offer a one-time move.
    """
    legacy_base = os.path.join(paths.root, LEGACY_TOOL_DIR_NAME)
    pairs = [
        (os.path.join(legacy_base, "tempgit"), paths.tempgit_path),
        (os.path.join(legacy_base, "prev_release"), paths.prev_release_path),
    ]
    pending = [(src, dst) for src, dst in pairs
               if _dir_has_content(src) and not _dir_has_content(dst)]
    if not pending:
        return

    print(f"[Project] Found working data from an embedded tool install under '{legacy_base}':")
    for src, _ in pending:
        print(f"  - {src}")
    if not prompt_yes(f"Move it into '{paths.data_dir}'? [Y]: "):
        print("[Project] Leaving legacy cache in place. Note: the tool now reads "
              f"'{paths.data_dir}', so the old data will not be used.")
        return

    for src, dst in pending:
        os.makedirs(dst, exist_ok=True)
        for entry in os.listdir(src):
            try:
                shutil.move(os.path.join(src, entry), os.path.join(dst, entry))
            except (OSError, shutil.Error) as ex:
                print(f"[Project] Could not move '{entry}': {ex}")
        try:
            os.rmdir(src)
        except OSError:
            pass
    print(f"[Project] Legacy cache moved into '{paths.data_dir}'.")


def preflight_project(paths: ProjectPaths, settings_template_path: str) -> None:
    """Prepare a project for activation: dirs, settings.yml, gitignore, migration."""
    if not os.path.isfile(paths.settings_path):
        if prompt_yes(f"No settings.yml found at '{paths.settings_path}'. Create one from the template? [Y]: "):
            shutil.copyfile(settings_template_path, paths.settings_path)
            print(f"[Project] Created {paths.settings_path} — review it to configure this pack.")
        else:
            raise RuntimeError("settings.yml is required to run the workflow.")

    for directory in (paths.export_path, paths.changelog_dir_path, paths.data_dir,
                      paths.tempgit_path, paths.prev_release_path):
        os.makedirs(directory, exist_ok=True)

    ensure_gitignore_entry(paths.root)
    migrate_legacy_cache(paths)

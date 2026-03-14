from dataclasses import dataclass, field
from typing import List, Optional


############################################################
# Configuration

@dataclass
class Settings:
    """Runtime configuration for the Modpack CLI Tool.

    All fields map directly to YAML keys in the settings file and are populated
    by `load_settings`. Boolean flags default to ``False`` unless a safe-on
    default is noted inline. String fields default to ``""`` and list fields
    default to ``None`` (treated as empty by callers).
    """
    # Boolean flags
    update_crash_assistant_modlist: bool = False
    export_client: bool = False
    export_server: bool = False
    refresh_only: bool = False
    update_bcc_version: bool = False
    cleanup_temp: bool = False
    create_release_notes: bool = False
    print_path_debug: bool = False
    update_publish_workflow: bool = False
    download_comparison_files: bool = False
    generate_mods_changelog: bool = False
    generate_primary_changelog: bool = False
    # Legacy preset alias. When true, missing modular options are auto-enabled.
    breakneck_fixes: bool = False
    client_export_use_mmc: bool = False
    show_export_mode_notice: bool = False
    changelog_template_use_overview_layout: bool = False
    changelog_include_compare_notice: bool = False
    comparison_files_use_versioned_packwiz_root: bool = False
    github_auth: bool = False
    changelog_side_tag: bool = True
    changelog_updated_mods: bool = False
    changelog_updated_resourcepacks: bool = False
    modlist_side_tag: bool = True
    update_mods_only: bool = False
    bump_version_only: bool = False
    clear_repo_data_only: bool = False
    generate_update_summary_only: bool = False
    list_disabled_mods_only: bool = False
    add_mod_only: bool = False
    migrate_minecraft_version: bool = False
    migration_disable_incompatible_mods: bool = True
    migration_update_all_mods: bool = True
    auto_generate_update_overview: bool = False
    auto_summary_overwrite_existing: bool = False
    auto_generate_config_changes: bool = False
    auto_config_overwrite_existing: bool = False
    auto_config_include_removed_files: bool = True

    # String settings
    bh_banner: str = ""
    repo_owner: str = ""
    repo_name: str = ""
    repo_main_branch: str = ""
    migration_target_minecraft: str = ""
    migration_target_fabric: str = ""
    migration_target_mod_loader: str = ""
    migration_target_mod_loader_version: str = ""
    migration_mod_loader: str = "fabric"
    alpha_update_policy: str = "prompt"
    bump_target_version: str = ""
    auto_summary_provider: str = "ollama"
    auto_summary_model: str = "qwen3:4b-instruct"
    auto_summary_endpoint: str = "http://127.0.0.1:11434/api/generate"
    auto_config_provider: str = "ollama"
    auto_config_model: str = "qwen3:4b-instruct"
    auto_config_endpoint: str = "http://127.0.0.1:11434/api/generate"
    comparison_files_versioned_root_pattern: str = "Packwiz/{mc_version}"
    comparison_files_versioned_root_min_version: str = "4.0.0-beta.3"
    comparison_files_versioned_root_max_version: str = "4.4.0-beta.1"

    # List settings
    server_mods_remove_list: List[str] = None
    auto_summary_timeout_seconds: int = 45
    auto_summary_max_items: int = 8
    auto_config_timeout_seconds: int = 45
    auto_config_temperature: float = 0.25
    auto_config_max_items: int = 20
    auto_config_max_lines: int = 18


def apply_legacy_breakneck_settings(settings_dict: dict):
    """Expand the deprecated ``breakneck_fixes`` preset into its modular equivalents.

    If ``breakneck_fixes`` is falsy the dict is returned unchanged. Otherwise,
    each modular option that the preset implies is inserted via ``setdefault`` so
    that explicit values in the file are never overwritten.

    Args:
        settings_dict: Raw key/value pairs loaded from the settings YAML file.

    Returns:
        The same ``settings_dict`` with any missing modular keys filled in.
    """
    if not bool(settings_dict.get("breakneck_fixes", False)):
        return settings_dict

    legacy_defaults = {
        "client_export_use_mmc": True,
        "show_export_mode_notice": True,
        "changelog_template_use_overview_layout": True,
        "changelog_include_compare_notice": True,
        "comparison_files_use_versioned_packwiz_root": True,
    }
    for key, value in legacy_defaults.items():
        settings_dict.setdefault(key, value)
    print("[Settings] 'breakneck_fixes' is deprecated. Applied equivalent modular settings for compatibility.")
    return settings_dict


def update_settings_from_dict(settings: Settings, settings_dict: dict):
    """Apply key/value pairs from a dict onto a ``Settings`` instance in place.

    Unknown keys are skipped with a printed warning rather than raising an error.

    Args:
        settings: The ``Settings`` object to mutate.
        settings_dict: Mapping of attribute names to their desired values.
    """
    for key, value in settings_dict.items():
        if hasattr(settings, key):
            setattr(settings, key, value)
        else:
            print(f"Warning: '{key}' is not a valid setting attribute.")


def load_settings(settings_path: str, yaml_instance) -> "Settings":
    """Parse a YAML settings file and return a populated ``Settings`` object.

    Handles legacy ``breakneck_fixes`` expansion before mapping values onto the
    dataclass. Missing keys retain their dataclass defaults.

    Args:
        settings_path: Absolute or relative path to the settings YAML file.
        yaml_instance: A ``ruamel.yaml.YAML`` (or compatible) instance used to
            parse the file.

    Returns:
        A fully initialised ``Settings`` instance.
    """
    with open(settings_path, "r", encoding="utf-8") as s_file:
        settings_yml = yaml_instance.load(s_file) or {}
    settings_yml = apply_legacy_breakneck_settings(settings_yml)
    s = Settings()
    update_settings_from_dict(s, settings_yml)
    return s

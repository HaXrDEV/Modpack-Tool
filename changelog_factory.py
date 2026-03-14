import os
import hashlib
import difflib

from ruamel.yaml.error import YAMLError

from mdutils.mdutils import MdUtils
import re
import toml
import markdown_helper as markdown
from packaging import version as version_helper
import requests
from datetime import datetime
from urllib.parse import quote

class ChangelogFactory:
    """Generates and compares modpack changelogs from YAML changelog files and packwiz TOML data.

    Handles version resolution, mod/config diffing, Modrinth API queries, and
    Markdown/VitePress output for a single modpack project.
    """

    def __init__(self, changelog_dir, modpack_name, modpack_version, settings, yaml_instance):
        self.changelog_dir = changelog_dir
        self.modpack_name = modpack_name
        self.modpack_version = modpack_version
        self.settings = settings
        self.yaml = yaml_instance
        self._missing_key_warnings = set()
        
    def get_changelog_value(self, changelog_yml, key):
        """Read a single key from a YAML changelog file, with deduplicated warnings.

        Args:
            changelog_yml (str): Filename (not full path) of the changelog YAML inside
                ``self.changelog_dir``.
            key: The top-level key to retrieve from the parsed YAML mapping.

        Returns:
            The value associated with ``key``, or ``None`` if the file cannot be
            read, parsed, or does not contain the key.
        """
        if changelog_yml and changelog_yml.endswith(('.yml', '.yaml')):
            file_path = os.path.join(self.changelog_dir, changelog_yml)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    changelog_data = self.yaml.load(f) or {}
                return changelog_data[key]
            except YAMLError as e:
                print(f"Error parsing {file_path}: {e}")
            except KeyError:
                warning_key = (file_path, str(key))
                if warning_key not in self._missing_key_warnings:
                    self._missing_key_warnings.add(warning_key)
                    print(f"Key '{key}' not found in {file_path}")
            except OSError as e:
                print(f"Error reading {file_path}: {e}")



    def compare_toml_files(self, dir1, dir2):
        """Diff two directories of packwiz mod TOML files and return added/removed/modified entries.

        Mods whose display name appears in both the added and removed sets are
        treated as renames and excluded from both lists to reduce noise.

        Args:
            dir1 (str): Path to the older (previous) mods directory.
            dir2 (str): Path to the newer (current) mods directory.

        Returns:
            dict: Keys ``'added'``, ``'removed'``, and ``'modified'``.
                  ``'added'`` and ``'removed'`` are lists of display-name strings.
                  ``'modified'`` is a list of ``(name, before, after)`` tuples where
                  *before* and *after* describe the filename or hash that changed.
        """
        # Initialize dictionaries to store TOML data
        toml_data_1 = {}
        toml_data_2 = {}
        
        def local_load_toml_files_from_dir(input_dir, output_dict):
            for filename in os.listdir(input_dir):
                if not filename.endswith(".toml"):
                    continue

                filepath = os.path.join(input_dir, filename)
                try:
                    with open(filepath, "r", encoding="utf8") as f:
                        mod_toml = toml.load(f)
                    side = str(mod_toml.get("side", "both"))
                    if side in ("both", "client", "server"):
                        output_dict[filename] = mod_toml
                except (OSError, toml.TomlDecodeError) as ex:
                    print(ex)

        local_load_toml_files_from_dir(dir1, toml_data_1)
        local_load_toml_files_from_dir(dir2, toml_data_2)

        # Prepare to store results
        results = {
            'added': [],
            'removed': [],
            'modified': []
        }
        def local_get_side_str(side):
            if side != "both" and self.settings.changelog_side_tag:
                return f" `{str(side).capitalize()}`"
            else:
                return ""

        # Temporary lists to store names for comparison
        added_names = []
        removed_names = []
        
        # First pass: collect all names
        for filename, data in toml_data_2.items():
            if filename not in toml_data_1:
                name = markdown.remove_bracketed_text(data.get('name', filename))
                side_data = data.get('side', filename)
                side_str = local_get_side_str(side_data)
                added_names.append((name, name + side_str))

        for filename in toml_data_1.keys():
            if filename not in toml_data_2:
                name = markdown.remove_bracketed_text(toml_data_1[filename].get('name', filename))
                side_data = toml_data_1[filename].get('side', filename)
                side_str = local_get_side_str(side_data)
                removed_names.append((name, name + side_str))

        # Find names that appear in both lists
        added_base_names = set(name[0] for name in added_names)
        removed_base_names = set(name[0] for name in removed_names)
        duplicates = added_base_names.intersection(removed_base_names)

        # Second pass: add to results, excluding duplicates
        for base_name, full_name in added_names:
            if base_name not in duplicates:
                results['added'].append(full_name)

        for base_name, full_name in removed_names:
            if base_name not in duplicates:
                results['removed'].append(full_name)

        def local_short_hash(raw_hash):
            value = str(raw_hash or "").strip()
            if not value:
                return ""
            return value[:12]

        # Handle modified files.
        for filename, data in toml_data_2.items():
            if filename in toml_data_1:
                previous_entry = toml_data_1[filename]
                current_entry = data

                prev_filename = str(previous_entry.get("filename", "") or "")
                curr_filename = str(current_entry.get("filename", "") or "")
                prev_hash = str(previous_entry.get("download", {}).get("hash", ""))
                curr_hash = str(current_entry.get("download", {}).get("hash", ""))

                # Detect updates even when providers keep artifact filenames stable.
                if prev_filename != curr_filename or prev_hash != curr_hash:
                    if prev_filename == curr_filename and prev_hash != curr_hash and prev_filename:
                        prev_hash_short = local_short_hash(prev_hash)
                        curr_hash_short = local_short_hash(curr_hash)
                        before = f"{prev_filename} (hash {prev_hash_short})" if prev_hash_short else prev_filename
                        after = f"{curr_filename} (hash {curr_hash_short})" if curr_hash_short else curr_filename
                    else:
                        before = prev_filename or prev_hash or ""
                        after = curr_filename or curr_hash or ""
                    results['modified'].append(
                        (markdown.remove_bracketed_text(current_entry.get('name', filename)), before, after)
                    )

        return results

    def _extract_version_and_mc_from_filename(self, changelog_filename):
        base_name = os.path.splitext(os.path.basename(str(changelog_filename or "")))[0]
        version_part, has_sep, mc_part = base_name.partition("+")
        version_part = str(version_part or "").strip()
        mc_part = str(mc_part or "").strip()
        if not has_sep or not version_part or not mc_part:
            return None, None
        return version_part, mc_part

    def parse_changelog_filename(self, changelog_filename):
        """Return ``(version, mc_version)`` parsed from a changelog filename.

        Delegates to ``_extract_version_and_mc_from_filename``.  Expected
        filename format: ``<version>+<mc_version>.yml``.  Returns
        ``(None, None)`` when the format does not match.
        """
        return self._extract_version_and_mc_from_filename(changelog_filename)

    def _resolve_compare_version_path(self, tempgit_path, version, mc_version):
        combined_path = os.path.join(tempgit_path, f"{version}+{mc_version}")
        legacy_path = os.path.join(tempgit_path, str(version))
        if os.path.isdir(combined_path):
            return combined_path
        return legacy_path

    def get_previous_version_for_mc(self, target_version, mc_version, migration_mode=False):
        """Find the most recent changelog version that precedes ``target_version``.

        In normal mode only candidates sharing the same MC version are
        considered.  In migration mode the MC version constraint is lifted so
        the diff can span a Minecraft version upgrade.

        Args:
            target_version (str): The modpack version to search below.
            mc_version (str): The Minecraft version to match (ignored in
                migration mode).
            migration_mode (bool): When ``True``, return the highest version
                strictly below ``target_version`` regardless of MC version.

        Returns:
            tuple[str, str] | tuple[None, None]: ``(version, mc_version)`` of
            the best candidate, or ``(None, None)`` if none exists.
        """
        version_candidates = []
        for changelog in os.listdir(self.changelog_dir):
            if not changelog.endswith((".yml", ".yaml")):
                continue
            current_version, changelog_mc_version = self._extract_version_and_mc_from_filename(changelog)
            if not current_version or not changelog_mc_version:
                continue
            version_candidates.append((str(current_version), str(changelog_mc_version)))

        if not version_candidates:
            return None, None

        sorted_versions = sorted(
            version_candidates,
            key=lambda x: (
                version_helper.parse(self.normalize_version(str(x[0]))),
                version_helper.parse(str(x[1])),
            ),
            reverse=True,
        )
        target_parsed = (
            version_helper.parse(self.normalize_version(str(target_version))),
            version_helper.parse(str(mc_version)),
        )

        if migration_mode:
            for candidate_version, candidate_mc in sorted_versions:
                candidate_parsed = (
                    version_helper.parse(self.normalize_version(str(candidate_version))),
                    version_helper.parse(str(candidate_mc)),
                )
                if candidate_parsed < target_parsed:
                    return candidate_version, candidate_mc
            return None, None

        target_mc_text = str(mc_version)
        for candidate_version, candidate_mc in sorted_versions:
            if str(candidate_mc) != target_mc_text:
                continue
            candidate_parsed = version_helper.parse(self.normalize_version(str(candidate_version)))
            if candidate_parsed < target_parsed[0]:
                return candidate_version, candidate_mc
        return None, None

    def get_current_pack_diff_payload(self, target_version, mc_version, tempgit_path, packwiz_path, migration_mode=False):
        """Build a full diff payload comparing the previous pack snapshot to the current working tree.

        Args:
            target_version (str): The modpack version being released.
            mc_version (str): The Minecraft version for this release.
            tempgit_path (str): Root of the git-history snapshot directory,
                containing per-version subdirectories.
            packwiz_path (str): Root of the live packwiz project (current state).
            migration_mode (bool): Passed through to ``get_previous_version_for_mc``
                to allow cross-MC-version comparisons.

        Returns:
            dict | None: Mapping with keys ``previous_version``,
            ``previous_mc_version``, ``current_version``, ``mc_version``,
            ``mod_differences``, ``resourcepack_differences``,
            ``shaderpack_differences``, ``mod_addition_breakdown``, and
            ``config_differences``.  Returns ``None`` when no previous version
            can be found or the previous snapshot is missing required directories.
        """
        previous_version, previous_mc_version = self.get_previous_version_for_mc(
            target_version,
            mc_version,
            migration_mode=bool(migration_mode),
        )
        if not previous_version:
            return None

        previous_version_path = self._resolve_compare_version_path(
            tempgit_path,
            previous_version,
            previous_mc_version or mc_version,
        )
        previous_mods_path = os.path.join(previous_version_path, "mods")
        previous_resourcepacks_path = os.path.join(previous_version_path, "resourcepacks")
        previous_shaderpacks_path = os.path.join(previous_version_path, "shaderpacks")
        previous_config_path = os.path.join(previous_version_path, "config")

        current_mods_path = os.path.join(packwiz_path, "mods")
        current_resourcepacks_path = os.path.join(packwiz_path, "resourcepacks")
        current_shaderpacks_path = os.path.join(packwiz_path, "shaderpacks")
        current_config_path = os.path.join(packwiz_path, "config")

        if not os.path.isdir(previous_mods_path) or not os.path.isdir(previous_resourcepacks_path):
            return None

        mod_differences = self.compare_toml_files(previous_mods_path, current_mods_path)
        resourcepack_differences = self.compare_toml_files(previous_resourcepacks_path, current_resourcepacks_path)
        if os.path.isdir(previous_shaderpacks_path) and os.path.isdir(current_shaderpacks_path):
            shaderpack_differences = self.compare_toml_files(previous_shaderpacks_path, current_shaderpacks_path)
        else:
            shaderpack_differences = {"added": [], "removed": [], "modified": []}
        mod_addition_breakdown = self.get_mod_addition_breakdown(previous_mods_path, current_mods_path)
        if os.path.isdir(previous_config_path) and os.path.isdir(current_config_path):
            config_differences = self.compare_directory_files(previous_config_path, current_config_path)
        else:
            config_differences = {
                "added": [],
                "removed": [],
                "modified": [],
                "modified_line_diffs": [],
                "moved_to_yosbr": [],
            }

        return {
            "previous_version": previous_version,
            "previous_mc_version": previous_mc_version,
            "current_version": str(target_version),
            "mc_version": str(mc_version),
            "mod_differences": mod_differences,
            "resourcepack_differences": resourcepack_differences,
            "shaderpack_differences": shaderpack_differences,
            "mod_addition_breakdown": mod_addition_breakdown,
            "config_differences": config_differences,
        }

    def compare_directory_files(self, previous_dir, current_dir):
        """Recursively diff two config directories and return file-level change details.

        Computes SHA-256 hashes to detect changes, collects line-level diffs for
        text-based formats, and identifies files that were relocated into a YOSBR
        overlay subtree.

        Args:
            previous_dir (str): Path to the older config directory snapshot.
            current_dir (str): Path to the newer config directory.

        Returns:
            dict: Keys:
                ``'added'`` — list of relative paths that are new.
                ``'removed'`` — list of relative paths that were deleted (excluding
                    those accounted for by a YOSBR move).
                ``'modified'`` — list of relative paths whose content changed
                    (including YOSBR-moved files whose content also changed).
                ``'modified_line_diffs'`` — list of dicts, each with ``'path'``,
                    ``'removed_lines'``, ``'added_lines'``, ``'previous_content'``,
                    and ``'current_content'`` for text-diffable files.
                ``'moved_to_yosbr'`` — list of dicts with ``'from'``, ``'to'``,
                    and ``'content_changed'`` for files relocated under ``yosbr/``.
        """
        line_diff_extensions = {
            ".json",
            ".json5",
            ".yaml",
            ".yml",
            ".toml",
            ".cfg",
            ".conf",
            ".ini",
            ".properties",
            ".txt",
        }

        def _is_yosbr_path(relative_path):
            normalized = str(relative_path or "").replace("\\", "/").strip("/").lower()
            return normalized.startswith("yosbr/")

        def _is_excluded_config_path(relative_path):
            normalized = str(relative_path or "").replace("\\", "/").strip("/").lower()
            wrapped = f"/{normalized}/"
            filename = os.path.basename(normalized)
            stem, _ = os.path.splitext(filename)
            # Exclude noisy/generated config files from changelog generation.
            is_bcc_config = stem == "bcc" or "/bcc/" in wrapped
            is_crash_assistant_modlist = stem == "modlist" and "/crash_assistant/" in wrapped
            return is_bcc_config or is_crash_assistant_modlist

        def _collect_state(base_dir):
            state = {}
            for root, _, files in os.walk(base_dir):
                for filename in files:
                    absolute_path = os.path.join(root, filename)
                    relative_path = os.path.relpath(absolute_path, base_dir).replace("\\", "/")
                    if _is_excluded_config_path(relative_path):
                        continue
                    try:
                        with open(absolute_path, "rb") as f:
                            file_hash = hashlib.sha256(f.read()).hexdigest()
                        state[relative_path] = file_hash
                    except OSError:
                        continue
            return state

        def _collect_line_diff_for_paths(previous_relative_path, current_relative_path, output_relative_path=None):
            previous_relative_path = str(previous_relative_path or "").replace("\\", "/").strip("/")
            current_relative_path = str(current_relative_path or "").replace("\\", "/").strip("/")
            output_relative_path = str(output_relative_path or current_relative_path).replace("\\", "/").strip("/")

            _, ext = os.path.splitext(current_relative_path.lower() or previous_relative_path.lower())
            if ext not in line_diff_extensions:
                return None

            previous_abs = os.path.join(previous_dir, previous_relative_path)
            current_abs = os.path.join(current_dir, current_relative_path)
            try:
                with open(previous_abs, "r", encoding="utf-8", errors="replace") as f_prev:
                    old_lines = f_prev.read().splitlines()
                with open(current_abs, "r", encoding="utf-8", errors="replace") as f_cur:
                    new_lines = f_cur.read().splitlines()
            except OSError:
                return None

            # Keep only concrete changes and cap payload size.
            removed_lines = []
            added_lines = []
            for diff_line in difflib.ndiff(old_lines, new_lines):
                if diff_line.startswith("- "):
                    removed_lines.append(diff_line[2:].strip())
                elif diff_line.startswith("+ "):
                    added_lines.append(diff_line[2:].strip())

            removed_lines = [line for line in removed_lines if line][:20]
            added_lines = [line for line in added_lines if line][:20]
            if not removed_lines and not added_lines:
                return None
            return {
                "path": output_relative_path,
                "removed_lines": removed_lines,
                "added_lines": added_lines,
                "previous_content": "\n".join(old_lines),
                "current_content": "\n".join(new_lines),
            }

        previous_state = _collect_state(previous_dir)
        current_state = _collect_state(current_dir)

        previous_paths = set(previous_state.keys())
        current_paths = set(current_state.keys())

        added = sorted(current_paths - previous_paths, key=lambda x: x.lower())
        removed = sorted(previous_paths - current_paths, key=lambda x: x.lower())
        modified = sorted(
            [
                rel_path
                for rel_path in (current_paths & previous_paths)
                if current_state[rel_path] != previous_state[rel_path]
            ],
            key=lambda x: x.lower(),
        )

        # YOSBR (You Shall Not Break Recipes) is a Minecraft mod that lets pack authors
        # ship default configs inside a special "yosbr/" overlay directory so that those
        # defaults are only applied on first run and are never overwritten again.  When a
        # pack migrates a config file from its plain location (e.g. "config/mod.toml") into
        # the YOSBR overlay (e.g. "yosbr/config/mod.toml") the naive diff reports the
        # original path as removed and the new overlay path as added — even though it is
        # really just a structural reorganisation.  The block below detects these pairs and
        # records them as "moved_to_yosbr" instead, keeping the removed/added lists clean.
        moved_to_yosbr = []
        if added and removed:
            added_lookup = {str(path).lower(): path for path in added}
            consumed_added_keys = set()
            filtered_removed = []

            for removed_path in removed:
                normalized_removed = str(removed_path).replace("\\", "/").strip("/")
                # If the removed path is itself already inside yosbr/, it was not
                # moved out — skip it so it stays in the removed list as-is.
                if _is_yosbr_path(normalized_removed):
                    filtered_removed.append(removed_path)
                    continue

                # Try both "yosbr/<path>" and "yosbr/config/<path>" because packs
                # sometimes nest files one level deeper inside the overlay.
                candidate_counterparts = [
                    f"yosbr/{normalized_removed}",
                    f"yosbr/config/{normalized_removed}",
                ]
                matched_added_path = None
                added_key = None
                for candidate in candidate_counterparts:
                    candidate_key = candidate.lower()
                    if candidate_key in added_lookup:
                        matched_added_path = added_lookup[candidate_key]
                        added_key = candidate_key
                        break
                if not matched_added_path:
                    filtered_removed.append(removed_path)
                    continue

                consumed_added_keys.add(added_key)
                moved_to_yosbr.append(
                    {
                        "from": removed_path,
                        "to": matched_added_path,
                        "content_changed": previous_state.get(removed_path) != current_state.get(matched_added_path),
                    }
                )

            if consumed_added_keys:
                added = [path for path in added if str(path).lower() not in consumed_added_keys]
            removed = filtered_removed

        modified_line_diffs = []
        for rel_path in modified:
            line_diff = _collect_line_diff_for_paths(rel_path, rel_path)
            if line_diff:
                modified_line_diffs.append(line_diff)

        for move in moved_to_yosbr:
            previous_path = str(move.get("from", "")).replace("\\", "/").strip("/")
            current_path = str(move.get("to", "")).replace("\\", "/").strip("/")
            content_changed = bool(move.get("content_changed"))
            if content_changed and current_path:
                if current_path not in modified:
                    modified.append(current_path)
                line_diff = _collect_line_diff_for_paths(previous_path, current_path, output_relative_path=current_path)
                if line_diff:
                    modified_line_diffs.append(line_diff)

        modified = sorted(set(modified), key=lambda x: x.lower())
        moved_to_yosbr = sorted(moved_to_yosbr, key=lambda x: str(x.get("to", "")).lower())
        if modified_line_diffs:
            deduped_line_diffs = {}
            for entry in modified_line_diffs:
                dedupe_key = str(entry.get("path", "")).lower()
                if dedupe_key and dedupe_key not in deduped_line_diffs:
                    deduped_line_diffs[dedupe_key] = entry
            modified_line_diffs = sorted(
                deduped_line_diffs.values(),
                key=lambda x: str(x.get("path", "")).lower(),
            )

        return {
            "added": added,
            "removed": removed,
            "modified": modified,
            "modified_line_diffs": modified_line_diffs,
            "moved_to_yosbr": moved_to_yosbr,
        }

    def _load_toml_state(self, input_dir):
        state = {}
        for filename in os.listdir(input_dir):
            if not filename.endswith(".toml"):
                continue
            filepath = os.path.join(input_dir, filename)
            if not os.path.isfile(filepath):
                continue
            try:
                with open(filepath, "r", encoding="utf8") as f:
                    mod_toml = toml.load(f)
                side_raw = str(mod_toml.get("side", "both")).strip()
                state[filename] = {
                    "name": markdown.remove_bracketed_text(mod_toml.get("name", filename)),
                    "side": side_raw,
                    "enabled": "disabled" not in side_raw.lower(),
                }
            except Exception:
                continue
        return state

    def get_mod_addition_breakdown(self, previous_mods_path, current_mods_path):
        """Classify newly appearing enabled mods as brand-new additions or re-enablements.

        A mod counts as "reenabled" when its TOML file existed in the previous
        snapshot but was disabled (side contained "disabled") and is now enabled.
        All other newly enabled mods are treated as "newly added".

        Args:
            previous_mods_path (str): Path to the previous snapshot's mods directory.
            current_mods_path (str): Path to the current mods directory.

        Returns:
            dict: ``{'newly_added': [...], 'reenabled_from_disabled': [...]}`` —
            both values are sorted, deduplicated lists of display-name strings.
        """
        previous_state = self._load_toml_state(previous_mods_path)
        current_state = self._load_toml_state(current_mods_path)

        newly_added = []
        reenabled_from_disabled = []

        for filename, current_entry in current_state.items():
            if not current_entry["enabled"]:
                continue

            previous_entry = previous_state.get(filename)
            if not previous_entry:
                newly_added.append(current_entry["name"])
                continue

            if not previous_entry["enabled"]:
                reenabled_from_disabled.append(current_entry["name"])

        def unique_sorted(items):
            return sorted(set(items), key=lambda x: x.lower())

        return {
            "newly_added": unique_sorted(newly_added),
            "reenabled_from_disabled": unique_sorted(reenabled_from_disabled),
        }

    def sort_versions(self, version_list):
        """
        Sort versions according to semantic versioning rules, handling post-releases correctly.
        Returns list in descending order (newest first).
        """
        return sorted(version_list, key=lambda x: version_helper.parse(self.normalize_version(str(x))), reverse=True)


    def normalize_version(self, version_str):
        """
        Normalize version strings to handle letter suffixes as post-releases.
        Examples:
            4.1.1a -> 4.1.1.post1
            4.1.1b -> 4.1.1.post2
            etc.
        """
        # Regular expression to match version with optional letter suffix
        match = re.match(r'^(\d+\.\d+\.\d+)([a-zA-Z])?$', str(version_str))
        if match:
            base_version, letter_suffix = match.groups()
            if letter_suffix:
                # Convert letter to number (a=1, b=2, etc.) and use as post-release number
                post_number = ord(letter_suffix.lower()) - ord('a') + 1
                return f"{base_version}.post{post_number}"
        return str(version_str)



    def vitepress_container_maker(self, type: str, content: str):
        """https://vitepress.dev/guide/markdown#custom-containers"""
        return(
            f"::: {type}\n"
            f"{content}\n"
            f":::"
        )

    def fetch_modrinth_versions(self, modpack_slug):
        """
        Fetches all version data for a given modpack from the Modrinth API.

        Args:
            modpack_slug (str): The slug of the modpack.

        Returns:
            list: A list of version data in JSON format, or None if an error occurs.
        """
        raw_value = str(modpack_slug or "").strip()
        if not raw_value:
            return None

        candidates = [raw_value]
        slug_candidate = re.sub(r"[^a-z0-9]+", "-", raw_value.lower()).strip("-")
        if slug_candidate and slug_candidate not in candidates:
            candidates.append(slug_candidate)

        last_error = None
        for candidate in candidates:
            try:
                encoded_candidate = quote(candidate, safe="")
                api_url = f"https://api.modrinth.com/v2/project/{encoded_candidate}/version"
                response = requests.get(api_url, timeout=20)
                if response.status_code == 404:
                    continue
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as e:
                last_error = e

        if last_error:
            print(f"Error fetching data from API: {last_error}")
        return None


    def extract_modrinth_version_info(self, versions, version_number=None):
        """
        Extracts version information from the fetched JSON data.

        Args:
            versions (list): The list of version data in JSON format.
            version_number (str, optional): The specific version number to find. Defaults to None.

        Returns:
            dict: A dictionary containing version details, or None if not found.
        """
        if not versions:
            print("No version data available.")
            return None

        if version_number:
            for version in versions:
                if version["version_number"] == version_number:
                    return {
                        "name": version["name"],
                        "version_number": version["version_number"],
                        "date_published": version["date_published"],
                        "download_url": version["files"][0]["url"] if version["files"] else None,
                        "changelog": version["changelog"]
                    }
            print(f"Version {version_number} not found.")
            return None

        # Find the latest version by date_published
        latest_version = max(versions, key=lambda v: v["date_published"])
        return {
            "name": latest_version["name"],
            "version_number": latest_version["version_number"],
            "date_published": latest_version["date_published"],
            "download_url": latest_version["files"][0]["url"] if latest_version["files"] else None,
            "changelog": latest_version["changelog"]
        }

    def build_markdown_changelog(self, repo_owner, repo_name, tempgit_path, packwiz_path, file_name="CHANGELOG", repo_branch="main", mc_version=None):
        """Generate a full Markdown changelog file from all YAML changelog entries.

        Iterates over every changelog YAML in ``self.changelog_dir``, sorted
        newest-first, diffs consecutive pack snapshots from ``tempgit_path``,
        annotates each section with Modrinth publish dates and VitePress badges,
        and writes a per-version mod-update sidecar Markdown file under
        ``Changelogs/``.

        Args:
            repo_owner (str): GitHub repository owner used to build badge/link URLs.
            repo_name (str): GitHub repository name.
            tempgit_path (str): Root directory of the git-history pack snapshots.
            packwiz_path (str): Root of the live packwiz project (current state).
            file_name (str): Output filename for the Markdown file (no extension).
            repo_branch (str): Branch name used in GitHub URLs.
            mc_version (str | None): When set, only changelog entries matching
                this Minecraft version are included.
        """
        mdFile = MdUtils(file_name)

        changelog_files = os.listdir(self.changelog_dir)
        modrinth_versions = self.fetch_modrinth_versions(self.modpack_name)
        
        # Create a list of (filename, version, mc_version) tuples from filenames.
        version_file_pairs = []
        for changelog in changelog_files:
            if changelog.endswith(('.yml', '.yaml')):
                ver, mc_ver = self._extract_version_and_mc_from_filename(changelog)
                if not ver or not mc_ver:
                    continue
                version_file_pairs.append((changelog, str(ver), str(mc_ver)))
        
        # Sort by modpack version first, then MC version.
        # The key is a 2-tuple so that when two entries share the same modpack
        # version (e.g. a build released for multiple MC lines), the one with the
        # higher MC version sorts first.  normalize_version() is called on the
        # modpack version to handle letter suffixes (e.g. "4.1.1a" → "4.1.1.post1")
        # so that post-release letters rank above the bare version; MC versions are
        # standard PEP-440 strings and need no normalisation.
        sorted_pairs = sorted(
            version_file_pairs,
            key=lambda x: (
                version_helper.parse(self.normalize_version(x[1])),  # modpack version (index 1)
                version_helper.parse(str(x[2])),                     # MC version fallback (index 2)
            ),
            reverse=True
        )
        
        changelog_list = list(sorted_pairs)




        # Iterate over the list with an index using enumerate
        mdFile.new_paragraph(f"##### {self.modpack_name}")

        if mc_version:
            mdFile.new_paragraph(f"# Changelog - {mc_version}")
        else:
            mdFile.new_paragraph(f"# Changelog")

        for i, changelog_entry in enumerate(changelog_list):
            changelog, version, changelog_mc_version = changelog_entry
            # Check if there's a "next" item
            if i + 1 < len(changelog_list):
                next_changelog = changelog_list[i + 1]
            else:
                next_changelog = None  # No next item if we're at the last one


            added_mods = None
            removed_mods = None
            added_resourcepacks = None
            removed_resourcepacks = None
            modified_mods = []
            modified_resourcepacks = []
            next_version = None
            next_mc_version = None

            if changelog.endswith(('.yml', '.yaml')) and (not mc_version or str(mc_version) == str(changelog_mc_version)):
                if next_changelog:
                    _, next_version, next_mc_version = next_changelog

                legacy_fabric_loader = self.get_changelog_value(changelog, "Fabric version")
                mod_loader_name = self.get_changelog_value(changelog, "Mod loader")
                mod_loader_version = self.get_changelog_value(changelog, "Mod loader version")
                if not mod_loader_name:
                    mod_loader_name = "Fabric" if legacy_fabric_loader else "Unknown Loader"
                mod_loader_label = str(mod_loader_name).strip() or "Unknown Loader"
                mod_loader_version_label = str(mod_loader_version or legacy_fabric_loader or "").strip()
                loader_badge_text = f"{mod_loader_label} {mod_loader_version_label}".strip()
                improvements = self.get_changelog_value(changelog, "Changes/Improvements")
                overview_legacy = self.get_changelog_value(changelog, "Update overview")
                bug_fixes = self.get_changelog_value(changelog, "Bug Fixes")
                config_changes = self.get_changelog_value(changelog, "Config Changes")
                script_changes = self.get_changelog_value(changelog, "Script/Datapack changes")

                version_path = self._resolve_compare_version_path(
                    tempgit_path,
                    version,
                    changelog_mc_version,
                )
                version_mods_path = os.path.join(version_path, "mods")
                version_resourcepacks_path = os.path.join(version_path, "resourcepacks")

                packwiz_mods_path = os.path.join(packwiz_path, "mods")
                packwiz_resourcepacks_path = os.path.join(packwiz_path, "resourcepacks")

                if next_version:
                    next_version_path = self._resolve_compare_version_path(
                        tempgit_path,
                        next_version,
                        next_mc_version,
                    )
                    next_version_mods_path = os.path.join(next_version_path, "mods")
                    next_version_resourcepacks_path = os.path.join(next_version_path, "resourcepacks")
                    print(f"[DEBUG] {next_version_path} + {version_path}")

                if str(version) != str(self.modpack_version) and next_version:
                    mod_differences = self.compare_toml_files(next_version_mods_path, version_mods_path)
                    resourcepack_differences = self.compare_toml_files(next_version_resourcepacks_path, version_resourcepacks_path)
                elif str(version) == str(self.modpack_version) and next_version:
                    mod_differences = self.compare_toml_files(next_version_mods_path, packwiz_mods_path)
                    resourcepack_differences = self.compare_toml_files(next_version_resourcepacks_path, packwiz_resourcepacks_path)
                else:
                    mod_differences = None
                    resourcepack_differences = None

                if mod_differences:
                    added_mods = mod_differences['added']
                    removed_mods = mod_differences['removed']
                    modified_mods = mod_differences['modified']

                if resourcepack_differences:
                    added_resourcepacks = resourcepack_differences['added']
                    removed_resourcepacks = resourcepack_differences['removed']
                    modified_resourcepacks = resourcepack_differences['modified']
                
                latest_modrinth_version_info = self.extract_modrinth_version_info(modrinth_versions)
                if latest_modrinth_version_info:
                    latest_modrinth_version_number = latest_modrinth_version_info['version_number']
                else:
                    latest_modrinth_version_number = None
                
                date_only = None
                try:
                    current_modrinth_version_info = self.extract_modrinth_version_info(modrinth_versions, version)
                    if current_modrinth_version_info:
                        modrinth_publish_timestamp = current_modrinth_version_info["date_published"]
                        dt_object = datetime.fromisoformat(modrinth_publish_timestamp.replace("Z", ""))
                        date_only = dt_object.strftime("%Y-%m-%d")
                except Exception as e:
                    print(e)
                    continue

                if version == self.modpack_version and version != latest_modrinth_version_number:
                        mdFile.new_paragraph(f"## v{version} <Badge type='warning' text='Work in progress'/> <a href='#v{version}' id='v{version}'></a>")
                else: 
                    if "v" not in version:
                        mdFile.new_paragraph(f"## v{version} <a href='#v{version}' id='v{version}'></a>")
                    else:
                        mdFile.new_paragraph(f"## {version} <a href='#{version}' id='{version}'></a>")

                
                if date_only:
                    mdFile.new_paragraph(f"<a href='https://github.com/{repo_owner}/{repo_name}/blob/{repo_branch}/Changelogs/changelog_mods_{version}.md'><Badge type='tip' text='Mod Updates'/></a><Badge type='info' text='{loader_badge_text}'/><Badge type='info' text='{date_only}'/>")
                else:
                    mdFile.new_paragraph(f"<a href='https://github.com/{repo_owner}/{repo_name}/blob/{repo_branch}/Changelogs/changelog_mods_{version}.md'><Badge type='tip' text='Mod Updates'/></a><Badge type='info' text='{loader_badge_text}'/>")
                # mdFile.new_paragraph(f"*{date_only}* | *Fabric Loader {fabric_loader}* | *[Mod Updates](https://github.com/{repo_owner}/{repo_name}/blob/{repo_branch}/Changelogs/changelog_mods_{version}.md)*")

                # Show comparison note when crossing to a previous MC line.
                if (
                    next_version
                    and next_mc_version
                    and str(next_mc_version) != str(changelog_mc_version)
                    and getattr(self.settings, "changelog_include_compare_notice", False)
                ):
                    mdFile.new_paragraph(self.vitepress_container_maker("info", f"Changes are in comparison to version [{next_version}]({next_mc_version}.md#v{next_version})."))
                
                if "beta" in version or "alpha" in version:
                    mdFile.new_paragraph(self.vitepress_container_maker("warning", "This is a pre-release. Here be dragons!"))

                if improvements:
                    mdFile.new_paragraph("### Changes/Improvements ⭐")
                    mdFile.new_paragraph(markdown.markdown_list_maker(improvements))
                if overview_legacy:
                    mdFile.new_paragraph("### Update Overview ⭐")
                    mdFile.new_paragraph(markdown.markdown_list_maker(overview_legacy))
                if bug_fixes:
                    mdFile.new_paragraph("### Bug Fixes 🪲")
                    mdFile.new_paragraph(markdown.markdown_list_maker(bug_fixes))
                if added_mods:
                    mdFile.new_paragraph("### Added Mods ✅")
                    mdFile.new_paragraph(markdown.markdown_list_maker(added_mods))
                if added_resourcepacks:
                    mdFile.new_paragraph("### Added Resource Packs 📦")
                    mdFile.new_paragraph(markdown.markdown_list_maker(added_resourcepacks))
                if removed_mods:
                    mdFile.new_paragraph("### Removed Mods ❌")
                    mdFile.new_paragraph(markdown.markdown_list_maker(removed_mods))
                if removed_resourcepacks:
                    mdFile.new_paragraph("### Removed Resource Packs ❌")
                    mdFile.new_paragraph(markdown.markdown_list_maker(removed_resourcepacks))

                # Modified mods section
                if mod_differences and mod_differences.get('modified') and self.settings.changelog_updated_mods:
                    mdFile.new_paragraph("### Updated Mods 🔄")
                    mdFile.new_paragraph(markdown.markdown_list_maker([item[0] for item in modified_mods]))
                
                # Modified resource packs section
                if resourcepack_differences and resourcepack_differences.get('modified') and self.settings.changelog_updated_resourcepacks:
                    mdFile.new_paragraph("### Updated Resource Packs 🔃")
                    mdFile.new_paragraph(markdown.markdown_list_maker([item[0] for item in modified_resourcepacks]))

                if script_changes:
                    mdFile.new_paragraph("### Script/Datapack Changes 📝")
                    mdFile.new_paragraph(markdown.markdown_list_maker(script_changes))
                
                if config_changes:
                    mdFile.new_paragraph("### Config Changes 📝")
                    mdFile.new_paragraph(markdown.codify_bracketed_text(config_changes))


                if next_version and next_version != version and mod_differences:
                    markdown.write_differences_to_markdown(
                        mod_differences,
                        self.modpack_name,
                        next_version,
                        version,
                        os.path.join('Changelogs', f'changelog_mods_{version}.md')
                    )
        mdFile.create_md_file()

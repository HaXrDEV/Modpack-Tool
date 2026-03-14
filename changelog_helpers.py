import os
import re
import requests
from typing import List, Optional

from ruamel.yaml.scalarstring import LiteralScalarString


_mod_label_index_cache = None


def initialize_modlist_label_index(active_mod_names: list):
    """Seed the module-level mod label cache from the active mod list.

    Must be called once before any changelog generation that needs display-label
    resolution.  Subsequent calls overwrite the cache entirely.

    Args:
        active_mod_names: Raw mod name strings as they appear in the modlist
            (may include side-suffixes like ``[Client]`` or trailing dashes).
    """
    global _mod_label_index_cache
    index = {}
    entries = []
    for raw_name in active_mod_names:
        name_no_side = re.sub(r"\s*\[[^\]]+\]\s*$", "", str(raw_name or "")).strip()
        mod_name = re.sub(r"\s*-\s*$", "", name_no_side).strip() or name_no_side
        if not mod_name:
            continue
        key = _normalize_lookup_key(mod_name)
        if not key:
            continue
        index.setdefault(key, mod_name)
        entries.append(mod_name)
    _mod_label_index_cache = {"index": index, "entries": entries}


def uses_llm_config_changes(settings) -> bool:
    """Return True when the active settings configure Ollama as the config-change provider."""
    return str(settings.auto_config_provider).strip().lower() == "ollama"


def _extract_modified_names(modified_items):
    return [str(item[0]) for item in modified_items if isinstance(item, (list, tuple)) and item]


def _yaml_plain_safe_name(name):
    # Prevent YAML from forcing quoted scalars for names containing ": ".
    return str(name).replace(": ", ":\u00A0")


def _format_quoted_names(names):
    cleaned = [_yaml_plain_safe_name(str(name).strip()) for name in names if str(name).strip()]
    return ", ".join(f"'{name}'" for name in cleaned)


def _format_name_list(names):
    cleaned = [_yaml_plain_safe_name(str(name).strip()) for name in names if str(name).strip()]
    quoted = [f"'{name}'" for name in cleaned]
    if not quoted:
        return ""
    if len(quoted) == 1:
        return quoted[0]
    if len(quoted) == 2:
        return f"{quoted[0]} & {quoted[1]}"
    return f"{', '.join(quoted[:-1])} & {quoted[-1]}"


def _resolve_mod_display_label(name: str) -> str:
    raw_name = str(name or "").strip()
    if not raw_name:
        return ""

    # Remove side suffixes used in some diff views, then normalize trailing separators.
    base_name = re.sub(r"\s*`[^`]+`\s*$", "", raw_name).strip()
    base_name = re.sub(r"\s*\[[^\]]+\]\s*$", "", base_name).strip()
    base_name = re.sub(r"\s*-\s*$", "", base_name).strip() or base_name

    label_index = _load_modlist_label_index()
    index = label_index.get("index", {})
    entries = label_index.get("entries", [])

    candidate_keys = [
        _normalize_lookup_key(base_name),
        _normalize_lookup_key(raw_name),
    ]
    for key in candidate_keys:
        if key and key in index:
            return index[key]

    # Loose fallback: containment match on normalized names.
    base_key = _normalize_lookup_key(base_name)
    if base_key:
        best_name = None
        best_score = 0
        for mod_name in entries:
            alias_key = _normalize_lookup_key(mod_name)
            if not alias_key:
                continue
            if base_key == alias_key:
                return mod_name
            if base_key in alias_key or alias_key in base_key:
                score = min(len(base_key), len(alias_key))
                if score > best_score:
                    best_score = score
                    best_name = mod_name
        if best_name and best_score >= 6:
            return best_name

    return base_name


def _dedupe_preserve_order(items):
    output = []
    seen = set()
    for item in items:
        value = str(item or "").strip()
        if not value:
            continue
        lowered = value.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        output.append(value)
    return output


def _append_added_removed_summary(lines, added_names, removed_names, singular_label, plural_label):
    cleaned_added = [str(name).strip() for name in added_names if str(name).strip()]
    cleaned_removed = [str(name).strip() for name in removed_names if str(name).strip()]

    if cleaned_added:
        label = singular_label if len(cleaned_added) == 1 else plural_label
        lines.append(f"Added {_format_name_list(cleaned_added)} {label}.")
    if cleaned_removed:
        label = singular_label if len(cleaned_removed) == 1 else plural_label
        lines.append(f"Removed {_format_name_list(cleaned_removed)} {label}.")


def generate_deterministic_update_overview(diff_payload, migration_mode=False) -> List[str]:
    """Build a human-readable summary of pack changes without calling an LLM.

    Produces one sentence per significant change category (added/removed mods,
    updated mods/resource-packs/shaderpacks, etc.) and deduplicates the result.

    Args:
        diff_payload: Structured diff dict produced by the pack comparison step.
        migration_mode: When True, prepends a Minecraft version upgrade line and
            formats removed mods as temporarily incompatible.

    Returns:
        Ordered list of summary sentences, each ending with a period.
    """
    mod_diff = diff_payload.get("mod_differences") or {}
    res_diff = diff_payload.get("resourcepack_differences") or {}
    shader_diff = diff_payload.get("shaderpack_differences") or {}
    mod_addition_breakdown = diff_payload.get("mod_addition_breakdown") or {}

    new_mod_names = _dedupe_preserve_order(
        _resolve_mod_display_label(name) for name in mod_addition_breakdown.get("newly_added", [])
    )
    reenabled_mod_names = _dedupe_preserve_order(
        _resolve_mod_display_label(name) for name in mod_addition_breakdown.get("reenabled_from_disabled", [])
    )
    removed_mod_names = _dedupe_preserve_order(
        _resolve_mod_display_label(name) for name in mod_diff.get("removed", [])
    )
    added_res_names = list(res_diff.get("added", []))
    removed_res_names = list(res_diff.get("removed", []))
    added_shader_names = list(shader_diff.get("added", []))
    removed_shader_names = list(shader_diff.get("removed", []))

    new_mod_count = len(new_mod_names)
    reenabled_mod_count = len(reenabled_mod_names)
    removed_mod_count = len(removed_mod_names)
    updated_mod_count = len(_extract_modified_names(mod_diff.get("modified", [])))

    added_res_count = len(added_res_names)
    removed_res_count = len(removed_res_names)
    updated_res_count = len(_extract_modified_names(res_diff.get("modified", [])))
    added_shader_count = len(added_shader_names)
    removed_shader_count = len(removed_shader_names)
    updated_shader_count = len(_extract_modified_names(shader_diff.get("modified", [])))

    lines = []
    if migration_mode:
        lines.append(f"Updated to Minecraft {diff_payload.get('mc_version')}.")

    current_version = str(diff_payload.get("current_version") or "")
    is_alpha_or_beta = bool(re.search(r"\b(alpha|beta)\b", current_version, re.IGNORECASE))

    if new_mod_count > 0:
        if new_mod_count == 1:
            lines.append(f"Added '{new_mod_names[0]}' mod.")
        else:
            lines.append(f"Added {_format_name_list(new_mod_names)} mods.")
    if reenabled_mod_count > 0:
        if is_alpha_or_beta:
            lines.append(f"Re-added some mods that have become available for {diff_payload.get('mc_version')}.")
        else:
            lines.append("Re-added some mods.")


    if removed_mod_count > 0:
        if migration_mode:
            lines.append(f"Temporarily removed incompatible mods: {_format_quoted_names(removed_mod_names)}.")
        else:
            if removed_mod_count == 1:
                lines.append(f"Removed '{removed_mod_names[0]}' mod.")
            else:
                lines.append(f"Removed {_format_name_list(removed_mod_names)} mods.")

    updated_categories = []
    if updated_mod_count > 0:
        updated_categories.append("mods")
    if updated_res_count > 0:
        updated_categories.append("resource packs")
    if updated_shader_count > 0:
        updated_categories.append("shaderpacks")

    if len(updated_categories) == 1:
        lines.append(f"Updated {updated_categories[0]}.")
    elif len(updated_categories) == 2:
        lines.append(f"Updated {updated_categories[0]} & {updated_categories[1]}.")
    elif len(updated_categories) > 2:
        lines.append(
            f"Updated {', '.join(updated_categories[:-1])}, & {updated_categories[-1]}."
        )

    _append_added_removed_summary(
        lines,
        added_res_names,
        removed_res_names,
        singular_label="resource pack",
        plural_label="resource packs",
    )

    _append_added_removed_summary(
        lines,
        added_shader_names,
        removed_shader_names,
        singular_label="shaderpack",
        plural_label="shaderpacks",
    )

    if not lines:
        lines.append("Maintenance update.")

    # Keep stable order but remove accidental duplicates.
    unique_lines = []
    seen = set()
    for line in lines:
        key = line.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        unique_lines.append(line)
    return unique_lines


def _normalize_llm_text_to_bullets(raw_text: str, max_lines: int):
    lines = []
    for raw_line in str(raw_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^[\-\*\u2022]\s*", "", line)
        line = re.sub(r"^\d+[.)]\s*", "", line)
        if line:
            normalized = line.strip()
            lines.append(f"- {normalized}")
    return lines[:max_lines]


def _normalize_config_llm_bullets(raw_text: str, max_lines: int):
    normalized = _normalize_llm_text_to_bullets(raw_text, max_lines=max(max_lines * 2, max_lines))
    valid = []
    invalid_count = 0
    required_suffix_pattern = re.compile(r"\[[^\[\]]+\]\.?$")

    for bullet in normalized:
        line = str(bullet or "").strip()
        if required_suffix_pattern.search(line):
            valid.append(line)
            if len(valid) >= max_lines:
                break
        else:
            invalid_count += 1

    return valid[:max_lines], invalid_count


def _normalize_redundant_config_context_tail(bullets: List[str]) -> List[str]:
    normalized = []
    label_suffix_pattern = re.compile(r"(\s*[:,-]?\s*\[[^\[\]]+\]\.?)$", flags=re.IGNORECASE)
    tail_pattern = re.compile(
        r"\s+(?:in|within|inside)\s+(?:the\s+)?"
        r"(?:[a-z0-9][a-z0-9\s_\-./&']{0,80}\s+)?"
        r"(?:mod\s+)?(?:config(?:uration)?s?|settings?|options?|overrides?)(?:\s+file)?\s*$",
        flags=re.IGNORECASE,
    )
    for raw_line in bullets:
        line = str(raw_line or "").strip()
        if not line:
            continue
        suffix_match = label_suffix_pattern.search(line)
        if not suffix_match:
            normalized.append(line)
            continue

        suffix = suffix_match.group(1)
        body = line[:suffix_match.start()]
        body = tail_pattern.sub("", body).rstrip()
        if not body:
            body = line[:suffix_match.start()].rstrip()
        line = f"{body}{suffix}"
        normalized.append(line)
    return normalized


def format_config_filename_as_title(filename: str) -> str:
    """Convert a raw config filename into a title-cased display label.

    Strips the file extension, splits on underscores/hyphens/dots and
    camelCase boundaries, then capitalizes each token.

    Args:
        filename: Bare filename or path component (e.g. ``"myMod_config.json"``).

    Returns:
        Title-cased string (e.g. ``"My Mod Config"``), or ``"Unknown"`` when the
        input is empty or yields no tokens.
    """
    name = os.path.splitext(str(filename or "").strip())[0]
    if not name:
        return "Unknown"

    name = re.sub(r"[_\-.]+", " ", name)
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    tokens = [token for token in name.split() if token]
    if not tokens:
        return "Unknown"

    return " ".join(token.capitalize() for token in tokens)


def _normalize_lookup_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _load_modlist_label_index():
    if _mod_label_index_cache is not None:
        return _mod_label_index_cache
    return {"index": {}, "entries": []}  # not initialized yet, return empty


def _get_normalized_config_path_parts(path: str) -> List[str]:
    raw_path = str(path or "").replace("\\", "/").strip("/")
    parts = [p for p in raw_path.split("/") if p]
    if not parts:
        return []

    # Treat yosbr/config as the config root for label inference.
    if parts[0].lower() == "yosbr":
        if len(parts) > 1 and parts[1].lower() == "config":
            return parts[2:]
        return parts[1:]

    return parts


def derive_mod_display_label_from_config_path(path: str) -> str:
    """Resolve a config file path to the best-matching mod display label.

    Resolution order:
    1. Exact normalized-key lookup against the mod label index (tries top-level
       folder, filename stem, full filename, then parent folder).
    2. Score-based containment match across all index entries (longest common
       normalized substring wins, minimum length 6 to suppress false positives).
    3. Title-cased top-level folder name, or title-cased filename as last resort.

    Args:
        path: Relative config path (forward- or back-slash separated).

    Returns:
        Human-readable mod label string, never empty.
    """
    normalized_parts = _get_normalized_config_path_parts(path)
    filename = normalized_parts[-1] if normalized_parts else derive_mod_label_from_config_path(path)
    stem = os.path.splitext(filename)[0].strip()
    parent = normalized_parts[-2] if len(normalized_parts) > 1 else ""
    top_folder = normalized_parts[0] if len(normalized_parts) > 1 else ""

    label_index = _load_modlist_label_index()
    index = label_index.get("index", {})
    entries = label_index.get("entries", [])

    candidate_keys = [
        _normalize_lookup_key(top_folder),
        _normalize_lookup_key(stem),
        _normalize_lookup_key(filename),
        _normalize_lookup_key(parent),
    ]
    for key in candidate_keys:
        if key and key in index:
            return index[key]

    # Loose fallback: match by containment on normalized keys.
    # Build the list of lookup keys to try, prioritising the top-level folder
    # (most likely to match the mod name) before the filename stem.
    loose_keys = []
    top_folder_key = _normalize_lookup_key(top_folder)
    if top_folder_key:
        loose_keys.append(top_folder_key)
    stem_key = _normalize_lookup_key(stem)
    if stem_key and stem_key not in loose_keys:
        loose_keys.append(stem_key)

    for lookup_key in loose_keys:
        best_name = None
        best_score = 0
        for mod_name in entries:
            alias_key = _normalize_lookup_key(mod_name)
            if not alias_key:
                continue
            # Exact match on the normalized key — return immediately.
            if lookup_key == alias_key:
                return mod_name
            # Containment in either direction qualifies as a partial match.
            if lookup_key in alias_key or alias_key in lookup_key:
                # Score is the length of the shorter key: longer overlap = more
                # specific match.  This prefers "sodium" over "na" when both
                # are substrings of the lookup key.
                score = min(len(lookup_key), len(alias_key))
                if score > best_score:
                    best_score = score
                    best_name = mod_name
        # Require a minimum overlap length of 6 characters to avoid spurious
        # matches from very short or generic tokens.
        if best_name and best_score >= 6:
            return best_name

    if top_folder:
        return format_config_filename_as_title(top_folder)

    return format_config_filename_as_title(filename)


def _build_config_label_maps(diff_payload):
    config_diff = diff_payload.get("config_differences") or {}
    line_diffs = list(config_diff.get("modified_line_diffs", []))
    moved_to_yosbr = list(config_diff.get("moved_to_yosbr", []))
    stem_to_label = {}
    alias_to_label = {}

    for entry in line_diffs:
        file_path = str(entry.get("path", "")).strip()
        if not file_path:
            continue

        config_filename = derive_mod_label_from_config_path(file_path)
        stem = os.path.splitext(config_filename)[0].strip().lower()
        title_label = format_config_filename_as_title(config_filename)
        resolved_label = derive_mod_display_label_from_config_path(file_path)

        if stem and resolved_label:
            stem_to_label[stem] = resolved_label

        if config_filename:
            alias_to_label[config_filename.strip().lower()] = resolved_label
        if stem:
            alias_to_label[stem] = resolved_label
        if title_label:
            alias_to_label[title_label.strip().lower()] = resolved_label
        if resolved_label:
            alias_to_label[resolved_label.strip().lower()] = resolved_label

    for move in moved_to_yosbr:
        move_to_path = str(move.get("to", "")).strip()
        if not move_to_path:
            continue
        config_filename = derive_mod_label_from_config_path(move_to_path)
        stem = os.path.splitext(config_filename)[0].strip().lower()
        title_label = format_config_filename_as_title(config_filename)
        resolved_label = derive_mod_display_label_from_config_path(move_to_path)

        if stem and resolved_label:
            stem_to_label[stem] = resolved_label

        if config_filename:
            alias_to_label[config_filename.strip().lower()] = resolved_label
        if stem:
            alias_to_label[stem] = resolved_label
        if title_label:
            alias_to_label[title_label.strip().lower()] = resolved_label
        if resolved_label:
            alias_to_label[resolved_label.strip().lower()] = resolved_label

    return stem_to_label, alias_to_label


def _normalize_config_change_labels(text: str, diff_payload) -> str:
    stem_to_label, _ = _build_config_label_maps(diff_payload)

    if not stem_to_label:
        return str(text or "")

    def _replace_label(match):
        label = str(match.group(1) or "").strip()
        lowered = label.lower()
        if lowered in stem_to_label:
            return f"[{stem_to_label[lowered]}]"
        return match.group(0)

    return re.sub(r"\[([^\]]+)\]", _replace_label, str(text or ""))


def _normalize_config_change_title_labels(text: str, diff_payload) -> str:
    _, alias_to_label = _build_config_label_maps(diff_payload)
    if not alias_to_label:
        return str(text or "")

    def _replace_label(match):
        label = str(match.group(1) or "").strip()
        lowered = label.lower()
        resolved_label = alias_to_label.get(lowered)
        if resolved_label:
            return f"[{resolved_label}]"
        return match.group(0)

    return re.sub(r"\[([^\]]+)\]", _replace_label, str(text or ""))


def _apply_yosbr_default_wording(text: str, diff_payload) -> str:
    config_diff = diff_payload.get("config_differences") or {}
    line_diffs = list(config_diff.get("modified_line_diffs", []))
    yosbr_labels = set()
    for entry in line_diffs:
        file_path = str(entry.get("path", "")).strip()
        if not file_path or not is_yosbr_config_path(file_path):
            continue
        yosbr_labels.add(derive_mod_display_label_from_config_path(file_path).strip().lower())

    if not yosbr_labels:
        return str(text or "")

    normalized_lines = []
    suffix_pattern = re.compile(r"(?::\s*)?\[([^\[\]]+)\]\.?$")

    for raw_line in str(text or "").splitlines():
        line = str(raw_line).strip()
        if not line:
            continue

        suffix_match = suffix_pattern.search(line)
        label = str(suffix_match.group(1)).strip().lower() if suffix_match else ""
        if label in yosbr_labels:
            if re.search(r"^-\s*changed\s+(?!default\b)", line, flags=re.IGNORECASE):
                line = re.sub(r"^-\s*changed\s+", "- Changed default ", line, count=1, flags=re.IGNORECASE)
            elif re.search(r"^-\s*updated\s+config\s+values\b", line, flags=re.IGNORECASE):
                line = re.sub(
                    r"^-\s*updated\s+config\s+values\b",
                    "- Updated default config values",
                    line,
                    count=1,
                    flags=re.IGNORECASE,
                )

        normalized_lines.append(line)

    return "\n".join(normalized_lines)


def _extract_config_bullet_labels(bullets: List[str]) -> set:
    labels = set()
    suffix_pattern = re.compile(r"(?::\s*)?\[([^\[\]]+)\]\.?$")
    for raw_line in bullets:
        line = str(raw_line or "").strip()
        if not line:
            continue
        match = suffix_pattern.search(line)
        if match:
            labels.add(str(match.group(1)).strip().lower())
    return labels


def _get_expected_config_labels(diff_payload) -> set:
    config_diff = diff_payload.get("config_differences") or {}
    modified_line_diffs = list(config_diff.get("modified_line_diffs", []))
    labels = set()
    if not modified_line_diffs:
        return labels

    for entry in modified_line_diffs:
        file_path = str(entry.get("path", "")).strip()
        if not file_path:
            continue
        mod_label = derive_mod_display_label_from_config_path(file_path).strip().lower()
        if mod_label:
            labels.add(mod_label)
    return labels


def _get_missing_config_labels_from_bullets(bullets: List[str], diff_payload) -> List[str]:
    expected_labels = _get_expected_config_labels(diff_payload)
    if not expected_labels:
        return []
    existing_labels = _extract_config_bullet_labels(bullets)
    return sorted(expected_labels - existing_labels)


def _get_expected_yosbr_moves(diff_payload) -> List[str]:
    config_diff = diff_payload.get("config_differences") or {}
    moved_to_yosbr = list(config_diff.get("moved_to_yosbr", []))
    moves = []
    seen = set()

    for move in moved_to_yosbr:
        from_path = str(move.get("from", "")).replace("\\", "/").strip("/")
        if not from_path:
            continue
        key = from_path.lower()
        if key in seen:
            continue
        seen.add(key)
        moves.append(from_path)
    return moves


def _get_missing_yosbr_moves_from_bullets(bullets: List[str], diff_payload) -> List[str]:
    expected_moves = _get_expected_yosbr_moves(diff_payload)
    if not expected_moves:
        return []

    lowered_bullets = [str(line or "").strip().lower() for line in bullets if str(line or "").strip()]
    missing = []
    for from_path in expected_moves:
        from_lower = from_path.lower()
        found = any(
            (from_lower in bullet) and ("yosbr" in bullet)
            for bullet in lowered_bullets
        )
        if not found:
            missing.append(from_path)
    return missing


def _normalize_yosbr_move_bullet_wording(bullets: List[str], diff_payload) -> List[str]:
    config_diff = diff_payload.get("config_differences") or {}
    moved_to_yosbr = list(config_diff.get("moved_to_yosbr", []))
    if not moved_to_yosbr:
        return list(bullets)

    move_map = {}
    for move in moved_to_yosbr:
        from_path = str(move.get("from", "")).replace("\\", "/").strip("/")
        to_path = str(move.get("to", "")).replace("\\", "/").strip("/")
        if not from_path:
            continue
        move_map[from_path.lower()] = {
            "from": from_path,
            "to": to_path,
            "label": derive_mod_display_label_from_config_path(to_path or from_path),
        }

    normalized = []
    suffix_pattern = re.compile(r"((?::\s*)?\[[^\[\]]+\]\.?)$")
    for raw_line in bullets:
        line = str(raw_line or "").strip()
        if not line:
            continue
        line_lower = line.lower()
        replaced = False

        for from_lower, meta in move_map.items():
            if from_lower not in line_lower:
                continue

            suffix_match = suffix_pattern.search(line)
            if suffix_match:
                label_suffix = suffix_match.group(1)
                line_body = line[:suffix_match.start()].rstrip()
            else:
                label_suffix = f": [{meta.get('label', 'Unknown')}]"
                line_body = line

            if "yosbr" not in line_body.lower():
                line_body = f"{line_body} (YOSBR)"

            line = f"{line_body}{label_suffix}"
            replaced = True
            break

        normalized.append(line if replaced else line)

    return normalized


def _filter_unexpected_yosbr_move_bullets(bullets: List[str], diff_payload) -> List[str]:
    expected_moves = [str(path).lower() for path in _get_expected_yosbr_moves(diff_payload)]
    filtered = []
    for raw_line in bullets:
        line = str(raw_line or "").strip()
        if not line:
            continue
        lowered = line.lower()
        is_move_bullet = ("moved " in lowered) and ("yosbr" in lowered)
        if not is_move_bullet:
            filtered.append(line)
            continue

        if not expected_moves:
            continue
        if any(expected_move in lowered for expected_move in expected_moves):
            filtered.append(line)

    return filtered


def _filter_unverified_config_operation_bullets(bullets: List[str], diff_payload) -> List[str]:
    config_diff = diff_payload.get("config_differences") or {}
    line_diffs = list(config_diff.get("modified_line_diffs", []))
    moved_to_yosbr = list(config_diff.get("moved_to_yosbr", []))

    has_removed_lines = False
    evidence_chunks = []
    for entry in line_diffs:
        removed_lines = [str(line).strip() for line in list(entry.get("removed_lines", [])) if str(line).strip()]
        added_lines = [str(line).strip() for line in list(entry.get("added_lines", [])) if str(line).strip()]
        if removed_lines:
            has_removed_lines = True
        evidence_chunks.extend(removed_lines)
        evidence_chunks.extend(added_lines)

    has_move_entries = bool(moved_to_yosbr)
    evidence_text = "\n".join(evidence_chunks).lower()
    operation_pattern = re.compile(r"\b(removed|moved|reordered|re-ordered|replaced|relocated|swapped)\b")
    set_key_pattern = re.compile(r'\bset\s+"([^"]+)"')

    filtered = []
    for raw_line in bullets:
        line = str(raw_line or "").strip()
        if not line:
            continue
        lowered = line.lower()

        # Prevent hallucinated move/remove/reorder language when the source diff has no removals or yosbr moves.
        if not has_removed_lines and not has_move_entries and operation_pattern.search(lowered):
            continue

        # If a bullet claims a specific quoted key was set, keep it only when that key appears in diff evidence.
        set_key_match = set_key_pattern.search(lowered)
        if set_key_match:
            quoted_key = str(set_key_match.group(1) or "").strip().lower()
            if quoted_key and quoted_key not in evidence_text:
                continue

        filtered.append(line)

    return filtered


def derive_mod_label_from_config_path(path: str) -> str:
    """Return the bare filename component of a config path.

    Args:
        path: Relative config path, forward- or back-slash separated.

    Returns:
        The final path segment (filename), or ``"Unknown"`` for empty input.
    """
    raw_path = str(path or "").replace("\\", "/").strip("/")
    if not raw_path:
        return "Unknown"

    parts = [p for p in raw_path.split("/") if p]
    filename = parts[-1] if parts else raw_path
    return filename


def is_yosbr_config_path(path: str) -> bool:
    """Return True when *path* lives inside the ``yosbr/`` defaults directory."""
    normalized = str(path or "").replace("\\", "/").strip("/").lower()
    return normalized.startswith("yosbr/")


def get_config_change_verb(path: str) -> str:
    """Return the appropriate change verb for a config path.

    Returns ``"Changed default"`` for yosbr paths and ``"Changed"`` otherwise.
    """
    return "Changed default" if is_yosbr_config_path(path) else "Changed"


def _strip_double_slash_comments(line: str) -> str:
    text = str(line or "")
    output = []
    in_string = False
    escaped = False

    for i, ch in enumerate(text):
        if escaped:
            output.append(ch)
            escaped = False
            continue

        if ch == "\\" and in_string:
            output.append(ch)
            escaped = True
            continue

        if ch == '"':
            output.append(ch)
            in_string = not in_string
            continue

        if not in_string and ch == "/" and i + 1 < len(text) and text[i + 1] == "/":
            break

        output.append(ch)

    return "".join(output)


def _extract_array_item_string_value(raw_line: str) -> Optional[str]:
    line = str(raw_line or "").strip()
    match = re.match(r'^\s*"((?:[^"\\]|\\.)*)"\s*,?\s*$', line)
    if not match:
        return None
    raw_value = match.group(1)
    try:
        return bytes(raw_value, "utf-8").decode("unicode_escape")
    except Exception:
        return raw_value


def _build_json_array_value_section_index(relative_config_path: str, packwiz_config_path: str):
    normalized_rel = str(relative_config_path or "").replace("\\", "/").strip("/")
    if not normalized_rel:
        return {}

    _, ext = os.path.splitext(normalized_rel.lower())
    if ext not in (".json", ".json5"):
        return {}

    config_file_path = os.path.join(packwiz_config_path, *normalized_rel.split("/"))
    if not os.path.isfile(config_file_path):
        return {}

    try:
        with open(config_file_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return {}

    section_index = {}
    container_stack = []

    def _pop_matching_container(container_type: str):
        for stack_idx in range(len(container_stack) - 1, -1, -1):
            if container_stack[stack_idx]["type"] == container_type:
                container_stack.pop(stack_idx)
                return
        if container_stack:
            container_stack.pop()

    for raw_line in lines:
        # Strip inline // comments before processing so JSON5 files parse cleanly.
        line = _strip_double_slash_comments(raw_line).strip()
        if not line:
            continue

        # If this line is a quoted array element, record which named containers
        # it belongs to so callers can surface section context in changelogs.
        string_value = _extract_array_item_string_value(line)
        if string_value is not None:
            # Collect names of all enclosing named containers (arrays/objects).
            section_names = [item["name"] for item in container_stack if item.get("name")]
            if section_names:
                # Build a dot-separated path, e.g. "dependencies.required".
                section_path = ".".join(section_names)
                lowered = string_value.lower()
                section_list = section_index.setdefault(lowered, [])
                if section_path not in section_list:
                    section_list.append(section_path)

        # Detect the opening of a named array or object (e.g. "key": [ or "key": {)
        # and push it onto the container stack so nested values inherit context.
        key_open_match = re.match(r'^"([^"]+)"\s*:\s*([\[{])', line)
        if key_open_match:
            key_name = key_open_match.group(1)
            opener = key_open_match.group(2)
            container_stack.append(
                {
                    "type": "array" if opener == "[" else "object",
                    "name": key_name,
                }
            )

        # Scan closing brackets on this line to pop the matching container.
        # String literals are blanked out first so brackets inside them are ignored.
        line_no_strings = re.sub(r'"(?:[^"\\]|\\.)*"', '""', line)
        for ch in line_no_strings:
            if ch == "]":
                _pop_matching_container("array")
            elif ch == "}":
                _pop_matching_container("object")

    return section_index


def _find_config_sections_for_line(file_path: str, raw_line: str, section_index_cache, packwiz_config_path: str) -> List[str]:
    value = _extract_array_item_string_value(raw_line)
    if not value:
        return []

    relative_config_path = str(file_path or "").replace("\\", "/").strip("/")
    if relative_config_path not in section_index_cache:
        section_index_cache[relative_config_path] = _build_json_array_value_section_index(relative_config_path, packwiz_config_path)

    section_index = section_index_cache.get(relative_config_path) or {}
    return list(section_index.get(value.lower(), []))


def _format_line_with_section_context(file_path: str, raw_line: str, section_index_cache, packwiz_config_path: str) -> str:
    base_line = str(raw_line or "").strip()
    if not base_line:
        return base_line

    sections = _find_config_sections_for_line(file_path, base_line, section_index_cache, packwiz_config_path)
    if not sections:
        return base_line

    if len(sections) == 1:
        return f"{base_line} (section: {sections[0]})"
    return f"{base_line} (sections: {', '.join(sections)})"


def _format_added_or_removed_list_value_bullet(action: str, file_path: str, raw_line: str, mod_label: str, section_index_cache, packwiz_config_path: str) -> Optional[str]:
    value = _extract_array_item_string_value(raw_line)
    if not value:
        return None

    action_label = str(action or "").strip()
    if is_yosbr_config_path(file_path):
        action_label = f"{action_label} default"

    sections = _find_config_sections_for_line(file_path, raw_line, section_index_cache, packwiz_config_path)
    display_value = value[5:] if value.startswith("file/") else value
    if sections:
        if len(sections) == 1:
            return f"- {action_label} {display_value} to section {sections[0]}: [{mod_label}]."
        return f"- {action_label} {display_value} to sections {', '.join(sections)}: [{mod_label}]."
    return f"- {action_label} {display_value}: [{mod_label}]."


def _is_fancymenu_customization_path(path: str) -> bool:
    normalized = str(path or "").replace("\\", "/").strip("/").lower()
    if not normalized:
        return False
    wrapped = f"/{normalized}/"
    return "/fancymenu/customization/" in wrapped


def build_config_label_title_prompt(text: str, diff_payload, max_items=20):
    """Build a prompt that asks an LLM to rewrite bracket labels in *text*.

    The prompt instructs the model to replace raw filename-based labels inside
    ``[…]`` with the proper mod display names derived from the diff payload,
    leaving all other wording untouched.

    Args:
        text: Existing bullet text whose bracket labels need correcting.
        diff_payload: Structured diff dict used to build the filename-to-label mapping.
        max_items: Maximum number of file mappings to include in the prompt.

    Returns:
        Prompt string ready to be sent to the LLM.
    """
    config_diff = diff_payload.get("config_differences") or {}
    modified_line_diffs = list(config_diff.get("modified_line_diffs", []))
    moved_to_yosbr = list(config_diff.get("moved_to_yosbr", []))

    mapping_lines = []
    for entry in modified_line_diffs[:max_items]:
        path = str(entry.get("path", "")).strip()
        if not path:
            continue
        filename = derive_mod_label_from_config_path(path)
        resolved_label = derive_mod_display_label_from_config_path(path)
        mapping_lines.append(f"- {filename} => {resolved_label}")
    remaining_capacity = max(max_items - len(mapping_lines), 0)
    if remaining_capacity > 0:
        for move in moved_to_yosbr[:remaining_capacity]:
            to_path = str(move.get("to", "")).strip()
            if not to_path:
                continue
            filename = derive_mod_label_from_config_path(to_path)
            resolved_label = derive_mod_display_label_from_config_path(to_path)
            mapping_lines.append(f"- {filename} => {resolved_label}")

    mapping = "\n".join(mapping_lines) if mapping_lines else "none"
    return (
        "Rewrite only bracket labels in these bullets.\n"
        "Keep the bullets, wording, order, and punctuation unchanged.\n"
        "Only update text inside square brackets to the mapped mod labels from the mod list.\n"
        "If a bracket label already matches the mapped mod label, keep it.\n"
        "Output only bullet lines.\n\n"
        "Filename to mod label mapping:\n"
        f"{mapping}\n\n"
        "Bullets:\n"
        f"{str(text or '').strip()}\n"
    )


def build_config_changes_prompt(diff_payload, max_items=12, packwiz_config_path=""):
    """Build the main LLM prompt for generating config-change changelog bullets.

    Embeds verified line-delta evidence, file-to-mod label mappings, and yosbr
    move entries alongside detailed style and accuracy instructions.

    Args:
        diff_payload: Structured diff dict produced by the pack comparison step.
        max_items: Maximum number of config file entries to include in the prompt.
        packwiz_config_path: Absolute path to the packwiz config directory, used
            to resolve section context for JSON array values.

    Returns:
        Prompt string ready to be sent to the LLM.
    """
    config_diff = diff_payload.get("config_differences") or {}
    modified_line_diffs = list(config_diff.get("modified_line_diffs", []))
    moved_to_yosbr = list(config_diff.get("moved_to_yosbr", []))
    section_index_cache = {}

    def _render_line_changes(items):
        if not items:
            return "none"
        rendered = []
        for entry in items[:max_items]:
            file_path = str(entry.get("path", "")).strip()
            rendered.append(f"File: {file_path}")
            if _is_fancymenu_customization_path(file_path):
                rendered.append("Detail policy: Do not list specific option names or exact values for this file.")
                rendered.append("Use a generic summary only (example: 'Adjusted FancyMenu customizations').")
                rendered.append("")
                continue
            removed_lines = list(entry.get("removed_lines", []))[:8]
            added_lines = list(entry.get("added_lines", []))[:8]
            rendered.append("Verified line deltas (source of truth):")
            if removed_lines:
                rendered.append("Removed lines:")
                rendered.extend(
                    f"- {_format_line_with_section_context(file_path, line, section_index_cache, packwiz_config_path)}"
                    for line in removed_lines
                )
            else:
                rendered.append("Removed lines: none")
            if added_lines:
                rendered.append("Added lines:")
                rendered.extend(
                    f"- {_format_line_with_section_context(file_path, line, section_index_cache, packwiz_config_path)}"
                    for line in added_lines
                )
            else:
                rendered.append("Added lines: none")
            rendered.append("")
        return "\n".join(rendered).strip()

    def _render_path_to_label(line_items, move_items):
        rendered = []
        seen_paths = set()

        for entry in line_items[:max_items]:
            path = str(entry.get("path", "")).strip()
            if not path:
                continue
            lowered = path.lower()
            if lowered in seen_paths:
                continue
            seen_paths.add(lowered)
            label = derive_mod_display_label_from_config_path(path)
            rendered.append(f"- {path} => {label}")

        remaining_capacity = max(max_items - len(rendered), 0)
        if remaining_capacity > 0:
            for move in move_items[:remaining_capacity]:
                path = str(move.get("to", "")).strip()
                if not path:
                    continue
                lowered = path.lower()
                if lowered in seen_paths:
                    continue
                seen_paths.add(lowered)
                label = derive_mod_display_label_from_config_path(path)
                rendered.append(f"- {path} => {label}")

        if not rendered:
            return "none"
        return "\n".join(rendered)

    def _render_yosbr_moves(items):
        if not items:
            return "none"
        rendered = []
        for entry in items[:max_items]:
            from_path = str(entry.get("from", "")).strip()
            if not from_path:
                continue
            if entry.get("content_changed"):
                rendered.append(f"- {from_path} (moved to yosbr, defaults also changed)")
            else:
                rendered.append(f"- {from_path} (moved to yosbr)")
        return "\n".join(rendered) if rendered else "none"

    return (
        "Write concise end-user facing config change bullets for a Minecraft modpack changelog.\n"
        "Output only bullet lines. Each line must start with '- '.\n"
        "Keep wording factual, short, and natural. No markdown headers and no counts.\n"
        "Use varied sentence openings; do not repeat the same lead-in phrase on every bullet.\n"
        "Treat 'Verified line deltas' as the only source of truth.\n"
        "Only describe facts directly supported by those deltas.\n"
        "If a value appears in both old and new file state, treat it as unchanged and do not mention it.\n"
        "Do not claim moves/removals/reorders/replacements unless that operation is explicitly shown in removed+added evidence or YOSBR move entries.\n"
        "If a file says 'Removed lines: none', do not use moved/removed/reordered/replaced wording for that file.\n"
        "Summarize player-facing impact when clear.\n"
        "Files under 'yosbr/' are defaults applied only on first launch.\n"
        "Do not treat regular config -> yosbr moves as removals.\n"
        "For each item listed under 'Regular config files moved to yosbr', include one explicit bullet that mentions the source path and YOSBR.\n"
        "For added/removed list entries, explicitly mention the section path when provided in '(section: ...)' context.\n"
        "Do not write generic lines like 'updated config files'.\n"
        "Avoid redundant tails like 'in <mod> config/settings/overrides'; keep wording focused on the actual value/behavior change.\n"
        "For files under 'fancymenu/customization', do not mention specific keys/values; summarize those changes generically but do mention which menu was changes.\n"
        "Do not write about added/removed config files.\n"
        "Summarize in-file setting/value changes from modified files, and also include yosbr move bullets.\n"
        "Each bullet must end with '[Mod Name]'.\n"
        "Use the file-to-mod mapping below when possible.\n"
        "Preferred style examples:\n"
        "- Ads are now disabled in the client panel: [Resourcify]\n"
        "- Switched the button URL to the new wiki format: [Simple Discord RPC]\n"
        "- Set \"coloredText\" to false for cleaner menu text: [Breakneck Menu]\n\n"
        "YOSBR style example:\n"
        "- Set default \"coloredText\" to false by default: [Breakneck Menu]\n\n"
        "YOSBR move style example:\n"
        "- Moved <source path> to YOSBR so defaults are applied on first launch: [Mod Name]\n\n"
        f"Current version: {diff_payload.get('current_version')}\n"
        f"Compared from: {diff_payload.get('previous_version')}\n"
        f"Minecraft: {diff_payload.get('mc_version')}\n\n"
        "File-to-mod mapping:\n"
        f"{_render_path_to_label(modified_line_diffs, moved_to_yosbr)}\n\n"
        "Regular config files moved to yosbr:\n"
        f"{_render_yosbr_moves(moved_to_yosbr)}\n\n"
        "Modified config evidence (verified line deltas):\n"
        f"{_render_line_changes(modified_line_diffs)}\n"
    )


def generate_config_changes_fallback_from_line_diffs(diff_payload, max_lines=8, packwiz_config_path="") -> str:
    """Generate config-change bullets deterministically from raw line diffs.

    Used when LLM generation is disabled or unavailable.  Attempts to produce
    specific bullets for added/removed JSON array values with section context,
    then falls back to listing changed key names, and finally emits a generic
    summary line if no specific information can be extracted.

    Args:
        diff_payload: Structured diff dict produced by the pack comparison step.
        max_lines: Maximum number of bullet lines to produce.
        packwiz_config_path: Absolute path to the packwiz config directory.

    Returns:
        Newline-joined bullet string (may be empty if there are no diffs).
    """
    config_diff = diff_payload.get("config_differences") or {}
    line_diffs = list(config_diff.get("modified_line_diffs", []))
    bullets = []
    section_index_cache = {}

    for entry in line_diffs:
        file_path = str(entry.get("path", "")).strip()
        mod_label = derive_mod_display_label_from_config_path(file_path)
        if _is_fancymenu_customization_path(file_path):
            if is_yosbr_config_path(file_path):
                bullets.append(f"- Adjusted default FancyMenu customizations: [{mod_label}].")
            else:
                bullets.append(f"- Adjusted FancyMenu customizations: [{mod_label}].")
            if len(bullets) >= max_lines:
                break
            continue
        added_lines = entry.get("added_lines", [])
        removed_lines = entry.get("removed_lines", [])
        candidate_lines = list(added_lines) + list(removed_lines)
        entry_started_with_bullet_count = len(bullets)

        for raw_line in added_lines:
            bullet = _format_added_or_removed_list_value_bullet(
                action="Added",
                file_path=file_path,
                raw_line=raw_line,
                mod_label=mod_label,
                section_index_cache=section_index_cache,
                packwiz_config_path=packwiz_config_path,
            )
            if bullet:
                bullets.append(bullet)
            if len(bullets) >= max_lines:
                break
        if len(bullets) >= max_lines:
            break

        for raw_line in removed_lines:
            bullet = _format_added_or_removed_list_value_bullet(
                action="Removed",
                file_path=file_path,
                raw_line=raw_line,
                mod_label=mod_label,
                section_index_cache=section_index_cache,
                packwiz_config_path=packwiz_config_path,
            )
            if bullet:
                bullets.append(bullet)
            if len(bullets) >= max_lines:
                break
        if len(bullets) >= max_lines:
            break

        keys = []
        for raw_line in candidate_lines:
            line = str(raw_line).strip()
            if not line or line.startswith(("#", "//", ";", "/*", "*")):
                continue
            key_match = re.match(r'^["\']?([A-Za-z0-9_.-]+)["\']?\s*[:=]', line)
            if key_match:
                key = key_match.group(1)
                if key not in keys:
                    keys.append(key)
            if len(keys) >= 4:
                break

        if len(bullets) > entry_started_with_bullet_count:
            continue

        change_verb = get_config_change_verb(file_path)
        if keys:
            keys_str = ", ".join(keys)
            bullets.append(f"- {change_verb} {keys_str}: [{mod_label}].")
        else:
            if is_yosbr_config_path(file_path):
                bullets.append(f"- Updated default config values: [{mod_label}].")
            else:
                bullets.append(f"- Updated config values: [{mod_label}].")
        if len(bullets) >= max_lines:
            break

    return "\n".join(bullets)


def generate_removed_config_file_bullets(diff_payload, max_lines=8) -> List[str]:
    """Build bullets for config files that were deleted between versions.

    Files that appear in the ``moved_to_yosbr`` list are excluded because they
    are not true removals.

    Args:
        diff_payload: Structured diff dict produced by the pack comparison step.
        max_lines: Maximum number of bullets to return.

    Returns:
        List of formatted bullet strings.
    """
    config_diff = diff_payload.get("config_differences") or {}
    removed_paths = list(config_diff.get("removed", []))
    moved_to_yosbr = list(config_diff.get("moved_to_yosbr", []))
    moved_from_paths = {
        str(entry.get("from", "")).replace("\\", "/").strip("/").lower()
        for entry in moved_to_yosbr
        if str(entry.get("from", "")).strip()
    }
    bullets = []
    seen = set()

    for raw_path in removed_paths:
        relative_path = str(raw_path or "").replace("\\", "/").strip("/")
        if not relative_path:
            continue

        dedupe_key = relative_path.lower()
        if dedupe_key in seen:
            continue
        if dedupe_key in moved_from_paths:
            continue
        seen.add(dedupe_key)

        mod_label = derive_mod_display_label_from_config_path(relative_path)
        bullets.append(f"- Removed config file {relative_path}: [{mod_label}].")
        if len(bullets) >= max_lines:
            break

    return bullets


def generate_yosbr_default_move_bullets(diff_payload, max_lines=8) -> List[str]:
    """Build bullets describing configs that were promoted to yosbr defaults.

    Args:
        diff_payload: Structured diff dict produced by the pack comparison step.
        max_lines: Maximum number of bullets to return.

    Returns:
        List of formatted bullet strings.
    """
    config_diff = diff_payload.get("config_differences") or {}
    moved_to_yosbr = list(config_diff.get("moved_to_yosbr", []))
    bullets = []
    seen = set()

    for move in moved_to_yosbr:
        from_path = str(move.get("from", "")).replace("\\", "/").strip("/")
        to_path = str(move.get("to", "")).replace("\\", "/").strip("/")
        if not from_path and not to_path:
            continue

        dedupe_key = f"{from_path.lower()}->{to_path.lower()}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        mod_label = derive_mod_display_label_from_config_path(to_path or from_path)
        bullets.append(
            f"- Moved {from_path} to {to_path} so it is now applied as a default on first launch: [{mod_label}]."
        )
        if len(bullets) >= max_lines:
            break

    return bullets


def format_config_change_labels_with_llm(text: str, diff_payload, settings, packwiz_config_path="") -> Optional[str]:
    """Post-process config-change bullets by asking the LLM to fix bracket labels.

    Sends ``text`` to the configured Ollama model with a label-rewriting prompt
    and retries up to three times if the output is incomplete.

    Args:
        text: Bullet text with potentially incorrect bracket labels.
        diff_payload: Structured diff dict used to build the label mapping prompt.
        settings: Settings object with Ollama connection and model parameters.
        packwiz_config_path: Unused here; accepted for call-site consistency.

    Returns:
        Corrected bullet text, or ``None`` if the provider is not Ollama or all
        attempts fail.
    """
    if str(settings.auto_config_provider).lower() != "ollama":
        return None

    prompt = build_config_label_title_prompt(
        text,
        diff_payload,
        max_items=settings.auto_config_max_items,
    )
    max_attempts = 3
    for attempt in range(max_attempts):
        num_predict = 200 + (attempt * 120)
        try:
            response = requests.post(
                settings.auto_config_endpoint,
                json={
                    "model": settings.auto_config_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0,
                        "num_predict": num_predict,
                    },
                },
                timeout=int(settings.auto_config_timeout_seconds),
            )
            response.raise_for_status()
            response_json = response.json()
            bullet_lines, invalid_count = _normalize_config_llm_bullets(
                response_json.get("response", ""),
                max_lines=settings.auto_config_max_lines,
            )
            done_reason = str(response_json.get("done_reason", "") or "").strip().lower()
            if bullet_lines and invalid_count == 0:
                return "\n".join(bullet_lines)

            needs_retry = (
                attempt + 1 < max_attempts
                and (invalid_count > 0 or done_reason in ("length", "max_tokens"))
            )
            if needs_retry:
                print("[Changelog] LLM label formatting returned incomplete output. Retrying...")
        except Exception as ex:
            print(f"[Changelog] LLM label formatting pass failed: {ex}")
            break

    return None


def _build_config_change_payload_for_file(diff_payload, file_path: str, packwiz_config_path=""):
    normalized_path = str(file_path or "").replace("\\", "/").strip("/")
    if not normalized_path:
        return None

    base_config_diff = dict((diff_payload or {}).get("config_differences") or {})
    all_line_diffs = list(base_config_diff.get("modified_line_diffs", []))
    all_moves = list(base_config_diff.get("moved_to_yosbr", []))

    selected_line_diffs = []
    for entry in all_line_diffs:
        entry_path = str(entry.get("path", "")).replace("\\", "/").strip("/")
        if entry_path == normalized_path:
            selected_line_diffs.append(entry)

    if len(selected_line_diffs) > 1:
        merged_removed = []
        merged_added = []
        merged_previous_content = ""
        merged_current_content = ""
        seen_removed = set()
        seen_added = set()
        for entry in selected_line_diffs:
            if not merged_previous_content:
                merged_previous_content = str(entry.get("previous_content", ""))
            if not merged_current_content:
                merged_current_content = str(entry.get("current_content", ""))
            for line in list(entry.get("removed_lines", [])):
                key = str(line).strip().lower()
                if not key or key in seen_removed:
                    continue
                seen_removed.add(key)
                merged_removed.append(str(line).strip())
            for line in list(entry.get("added_lines", [])):
                key = str(line).strip().lower()
                if not key or key in seen_added:
                    continue
                seen_added.add(key)
                merged_added.append(str(line).strip())
        selected_line_diffs = [
            {
                "path": normalized_path,
                "removed_lines": merged_removed[:20],
                "added_lines": merged_added[:20],
                "previous_content": merged_previous_content,
                "current_content": merged_current_content,
            }
        ]

    selected_moves = []
    for move in all_moves:
        move_to_path = str(move.get("to", "")).replace("\\", "/").strip("/")
        if move_to_path == normalized_path:
            selected_moves.append(move)

    if not selected_line_diffs and not selected_moves:
        return None

    single_payload = dict(diff_payload or {})
    config_diff = dict(base_config_diff)
    config_diff["added"] = []
    config_diff["removed"] = []
    config_diff["modified"] = [normalized_path]
    config_diff["modified_line_diffs"] = selected_line_diffs
    config_diff["moved_to_yosbr"] = selected_moves
    single_payload["config_differences"] = config_diff
    return single_payload


def _generate_config_change_item_with_llm(item_payload, settings, max_lines: int, packwiz_config_path="") -> List[str]:
    if max_lines <= 0:
        return []

    base_prompt = build_config_changes_prompt(
        item_payload,
        max_items=1,
        packwiz_config_path=packwiz_config_path,
    )
    max_attempts = 4
    best_bullets = []
    best_missing_score = None
    missing_labels_for_retry = []
    missing_moves_for_retry = []
    try:
        llm_temperature = float(getattr(settings, "auto_config_temperature", 0.25))
    except (TypeError, ValueError):
        llm_temperature = 0.25
    llm_temperature = max(0.0, min(llm_temperature, 2.0))

    for attempt in range(max_attempts):
        num_predict = 200 + (attempt * 140)
        coverage_hint = ""
        if missing_labels_for_retry or missing_moves_for_retry:
            coverage_hint_parts = []
            if missing_labels_for_retry:
                missing_label_render = "\n".join(f"- {label}" for label in missing_labels_for_retry[:6])
                coverage_hint_parts.append(
                    "Ensure at least one bullet for each missing label below.\n"
                    "Keep exact label spelling inside [] for these:\n"
                    f"{missing_label_render}\n"
                )
            if missing_moves_for_retry:
                missing_move_render = "\n".join(f"- {move}" for move in missing_moves_for_retry[:6])
                coverage_hint_parts.append(
                    "Ensure one bullet for each missing yosbr move below.\n"
                    "Mention each exact source path and make clear it moved to YOSBR:\n"
                    f"{missing_move_render}\n"
                )
            coverage_hint = (
                "\nCoverage retry requirement:\n"
                f"{''.join(coverage_hint_parts)}"
            )

        prompt = f"{base_prompt}{coverage_hint}"
        try:
            response = requests.post(
                settings.auto_config_endpoint,
                json={
                    "model": settings.auto_config_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": llm_temperature,
                        "num_predict": num_predict,
                    },
                },
                timeout=int(settings.auto_config_timeout_seconds),
            )
            response.raise_for_status()
            response_json = response.json()
            bullet_lines, invalid_count = _normalize_config_llm_bullets(
                response_json.get("response", ""),
                max_lines=max_lines,
            )
            done_reason = str(response_json.get("done_reason", "") or "").strip().lower()
            if bullet_lines and invalid_count == 0:
                normalized = _normalize_config_change_labels("\n".join(bullet_lines), item_payload)
                titled = format_config_change_labels_with_llm(normalized, item_payload, settings, packwiz_config_path=packwiz_config_path)
                if titled:
                    llm_text = _apply_yosbr_default_wording(
                        _normalize_config_change_title_labels(titled, item_payload),
                        item_payload,
                    )
                else:
                    llm_text = _apply_yosbr_default_wording(
                        _normalize_config_change_title_labels(normalized, item_payload),
                        item_payload,
                    )

                llm_bullets = _normalize_llm_text_to_bullets(llm_text, max_lines=max_lines)
                llm_bullets = _normalize_yosbr_move_bullet_wording(llm_bullets, item_payload)
                llm_bullets = _filter_unexpected_yosbr_move_bullets(llm_bullets, item_payload)
                llm_bullets = _filter_unverified_config_operation_bullets(llm_bullets, item_payload)
                llm_bullets = _normalize_redundant_config_context_tail(llm_bullets)
                missing_labels = _get_missing_config_labels_from_bullets(llm_bullets, item_payload)
                missing_moves = _get_missing_yosbr_moves_from_bullets(llm_bullets, item_payload)
                missing_score = len(missing_labels) + len(missing_moves)

                if not best_bullets or best_missing_score is None or missing_score < best_missing_score:
                    best_bullets = list(llm_bullets)
                    best_missing_score = missing_score

                if not missing_labels and not missing_moves:
                    return llm_bullets

                if attempt + 1 < max_attempts:
                    missing_labels_for_retry = list(missing_labels)
                    missing_moves_for_retry = list(missing_moves)
                    print("[Changelog] Per-item LLM config output missed required coverage. Retrying...")
                    continue

                return best_bullets

            needs_retry = (
                attempt + 1 < max_attempts
                and (invalid_count > 0 or done_reason in ("length", "max_tokens"))
            )
            if needs_retry:
                print("[Changelog] Per-item LLM config generation returned incomplete output. Retrying...")
        except Exception as ex:
            print(f"[Changelog] Per-item LLM config change generation failed: {ex}")
            break

    return best_bullets


def generate_config_changes_with_llm(diff_payload, settings, max_lines: Optional[int] = None, packwiz_config_path="") -> Optional[str]:
    """Generate config-change changelog bullets via Ollama, one file at a time.

    Iterates over each changed config file in the diff, issues a focused LLM
    prompt per file, applies post-processing and deduplication, and aggregates
    results up to the line budget.

    Args:
        diff_payload: Structured diff dict produced by the pack comparison step.
        settings: Settings object with Ollama connection and generation parameters.
        max_lines: Override for the maximum total bullet lines.  Defaults to
            ``settings.auto_config_max_lines`` when ``None``.
        packwiz_config_path: Absolute path to the packwiz config directory.

    Returns:
        Newline-joined bullet string, or ``None`` when the provider is not Ollama,
        the budget is zero, or no usable output was produced.
    """
    if str(settings.auto_config_provider).lower() != "ollama":
        print(f"[Changelog] Unsupported auto_config_provider '{settings.auto_config_provider}'.")
        return None

    effective_max_lines = int(max_lines) if max_lines is not None else int(settings.auto_config_max_lines)
    if effective_max_lines <= 0:
        return None

    config_diff = diff_payload.get("config_differences") or {}
    modified_line_diffs = list(config_diff.get("modified_line_diffs", []))
    moved_to_yosbr = list(config_diff.get("moved_to_yosbr", []))

    ordered_paths = []
    seen_paths = set()

    def _append_path(raw_path):
        normalized = str(raw_path or "").replace("\\", "/").strip("/")
        if not normalized:
            return
        lowered = normalized.lower()
        if lowered in seen_paths:
            return
        seen_paths.add(lowered)
        ordered_paths.append(normalized)

    for entry in moved_to_yosbr:
        _append_path(entry.get("to", ""))
    for entry in modified_line_diffs:
        _append_path(entry.get("path", ""))

    change_items = []
    for file_path in ordered_paths:
        payload = _build_config_change_payload_for_file(diff_payload, file_path, packwiz_config_path=packwiz_config_path)
        if payload:
            change_items.append(payload)

    if not change_items:
        return None

    aggregated_bullets = []
    seen = set()

    for item_payload in change_items:
        remaining_budget = effective_max_lines - len(aggregated_bullets)
        if remaining_budget <= 0:
            break

        # One prompt per modified config file; allow each file to emit as many bullets as budget allows.
        item_bullets = _generate_config_change_item_with_llm(
            item_payload,
            settings,
            max_lines=remaining_budget,
            packwiz_config_path=packwiz_config_path,
        )
        for bullet in item_bullets:
            line = str(bullet or "").strip()
            if not line:
                continue
            dedupe_key = re.sub(r"[\s\.:;,\-]+$", "", line.lower())
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            aggregated_bullets.append(line)
            if len(aggregated_bullets) >= effective_max_lines:
                break

    if not aggregated_bullets:
        return None
    return "\n".join(aggregated_bullets)


def maybe_generate_update_overview(changelog_path, diff_payload, settings, yaml_instance):
    """Write a deterministic ``Update overview`` section into a changelog YAML file.

    Skips writing when the section already exists and
    ``settings.auto_summary_overwrite_existing`` is False.

    Args:
        changelog_path: Absolute path to the changelog ``.yml`` file to update.
        diff_payload: Structured diff dict produced by the pack comparison step.
        settings: Settings object checked for ``auto_summary_overwrite_existing``.
        yaml_instance: Configured ``ruamel.yaml.YAML`` instance for round-trip I/O.
    """
    if not diff_payload:
        print("[Changelog] No diff payload available. Skipping auto summary.")
        return

    with open(changelog_path, "r", encoding="utf-8") as f:
        changelog_yml = yaml_instance.load(f) or {}

    existing_overview = changelog_yml.get("Update overview")
    if existing_overview and not settings.auto_summary_overwrite_existing:
        print("[Changelog] 'Update overview' already exists. Skipping auto summary.")
        return

    summary_lines = generate_deterministic_update_overview(
        diff_payload,
        migration_mode=bool(settings.migrate_minecraft_version),
    )
    changelog_yml["Update overview"] = summary_lines
    with open(changelog_path, "w", encoding="utf-8") as f:
        yaml_instance.dump(changelog_yml, f)

    print(f"[Changelog] Wrote deterministic 'Update overview' in {os.path.basename(changelog_path)}.")


def maybe_generate_config_changes(changelog_path, diff_payload, settings, yaml_instance, packwiz_config_path=""):
    """Write a ``Config Changes`` section into a changelog YAML file.

    Coordinates all config-change generation strategies: yosbr-move bullets,
    removed-file bullets, LLM-generated line-diff bullets (when Ollama is
    configured), and the deterministic fallback.  Skips writing when the section
    already contains non-placeholder content and
    ``settings.auto_config_overwrite_existing`` is False.

    Args:
        changelog_path: Absolute path to the changelog ``.yml`` file to update.
        diff_payload: Structured diff dict produced by the pack comparison step.
        settings: Settings object controlling generation strategy and limits.
        yaml_instance: Configured ``ruamel.yaml.YAML`` instance for round-trip I/O.
        packwiz_config_path: Absolute path to the packwiz config directory, passed
            through to JSON section-index helpers.
    """
    if not diff_payload:
        print("[Changelog] No diff payload available. Skipping config change generation.")
        return

    config_diff = diff_payload.get("config_differences") or {}
    modified_line_diffs = list(config_diff.get("modified_line_diffs", []))
    moved_to_yosbr_entries = list(config_diff.get("moved_to_yosbr", []))
    use_llm = uses_llm_config_changes(settings)
    if use_llm:
        moved_to_yosbr_bullets = []
    else:
        moved_to_yosbr_bullets = generate_yosbr_default_move_bullets(
            diff_payload,
            max_lines=settings.auto_config_max_lines,
        )
    remaining_after_yosbr = max(int(settings.auto_config_max_lines) - len(moved_to_yosbr_bullets), 0)
    include_removed_config_files = bool(getattr(settings, "auto_config_include_removed_files", True))
    if include_removed_config_files and remaining_after_yosbr > 0:
        removed_config_file_bullets = generate_removed_config_file_bullets(
            diff_payload,
            max_lines=remaining_after_yosbr,
        )
    else:
        removed_config_file_bullets = []

    with open(changelog_path, "r", encoding="utf-8") as f:
        changelog_yml = yaml_instance.load(f) or {}

    existing_config_changes = changelog_yml.get("Config Changes")
    existing_config_text = str(existing_config_changes or "").strip()
    is_default_placeholder = existing_config_text in ("- : [mod], [Client]", "")
    if existing_config_changes and not is_default_placeholder and not settings.auto_config_overwrite_existing:
        print("[Changelog] 'Config Changes' already exists. Skipping auto generation.")
        return

    if not modified_line_diffs and not removed_config_file_bullets and not moved_to_yosbr_bullets and not moved_to_yosbr_entries:
        changelog_yml["Config Changes"] = ""
        with open(changelog_path, "w", encoding="utf-8") as f:
            yaml_instance.dump(changelog_yml, f)
        print(f"[Changelog] Cleared 'Config Changes' (no config changes detected) in {os.path.basename(changelog_path)}.")
        return

    final_bullets = list(moved_to_yosbr_bullets) + list(removed_config_file_bullets)
    source_parts = []
    if moved_to_yosbr_bullets:
        source_parts.append("yosbr-defaults")
    if removed_config_file_bullets:
        source_parts.append("removed-files")

    remaining_line_budget = max(int(settings.auto_config_max_lines) - len(final_bullets), 0)
    line_change_text = None

    has_llm_line_source = bool(modified_line_diffs or moved_to_yosbr_entries)
    if has_llm_line_source and remaining_line_budget > 0:
        if use_llm:
            line_change_text = generate_config_changes_with_llm(
                diff_payload,
                settings,
                max_lines=remaining_line_budget,
                packwiz_config_path=packwiz_config_path,
            )
            if line_change_text:
                source_parts.append("LLM")
            else:
                print("[Changelog] LLM output was empty or failed after retries. Skipping deterministic fallback.")

        if not line_change_text and not use_llm:
            line_change_text = generate_config_changes_fallback_from_line_diffs(
                diff_payload,
                max_lines=remaining_line_budget,
                packwiz_config_path=packwiz_config_path,
            )
            if line_change_text:
                source_parts.append("fallback")

    if line_change_text and remaining_line_budget > 0:
        final_bullets.extend(
            _normalize_llm_text_to_bullets(line_change_text, max_lines=remaining_line_budget)
        )

    if not final_bullets:
        print("[Changelog] Skipping 'Config Changes': no usable output from LLM or fallback.")
        return

    changelog_yml["Config Changes"] = LiteralScalarString("\n".join(final_bullets))
    with open(changelog_path, "w", encoding="utf-8") as f:
        yaml_instance.dump(changelog_yml, f)

    source_label = " + ".join(source_parts) if source_parts else "unknown"
    print(f"[Changelog] Auto-generated 'Config Changes' via {source_label} in {os.path.basename(changelog_path)}.")

"""Pack manager — packwiz-independent implementations of refresh, update, remove, add, and export."""

import hashlib
import json
import os
import re
import threading
import urllib.parse
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import toml

from api_clients import (
    CURSEFORGE_API_BASE,
    _compute_cf_fingerprint,
    _evaluate_curseforge_file_compatibility,
    _get_curseforge_project_files,
    apply_modrinth_version_to_mod_toml,
    fetch_cf_fingerprints_batch,
    fetch_curseforge_download_url,
    fetch_curseforge_project_info,
    fetch_modrinth_project_info,
    fetch_modrinth_project_versions,
    infer_release_channel_from_metadata,
    normalize_mod_loader_name,
    select_latest_allowed_modrinth_version,
    select_modrinth_primary_file,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_packwizignore_rules(packwiz_path):
    """Return list of non-blank, non-comment lines from .packwizignore."""
    ignore_path = os.path.join(packwiz_path, ".packwizignore")
    if not os.path.isfile(ignore_path):
        return []
    rules = []
    with open(ignore_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n\r")
            if line and not line.startswith("#"):
                rules.append(line)
    return rules


def _is_ignored(rel_path, rules):
    """Return True if rel_path (forward-slash, relative to packwiz root) matches any ignore rule.

    Implements the gitignore-format subset used by .packwizignore:
    - Leading ``/`` = anchored to packwiz root.
    - Trailing ``/*`` = all entries directly inside that directory.
    - Otherwise treated as a literal path prefix/suffix check.
    """
    for rule in rules:
        anchored = rule.startswith("/")
        pattern = rule[1:] if anchored else rule

        if pattern.endswith("/*"):
            # Match everything inside the directory
            dir_prefix = pattern[:-1]  # strip the trailing *
            if anchored:
                if rel_path.startswith(dir_prefix):
                    return True
            else:
                # non-anchored: match anywhere in tree
                if ("/" + dir_prefix) in ("/" + rel_path) or rel_path.startswith(dir_prefix):
                    return True
        else:
            # Literal path: exact match OR the path is inside this directory
            if anchored:
                if rel_path == pattern or rel_path.startswith(pattern + "/") or rel_path.startswith(pattern):
                    return True
            else:
                if rel_path == pattern or rel_path.endswith("/" + pattern):
                    return True
    return False


def _write_mod_toml(item_path, mod_toml):
    """Write mod_toml dict to item_path using toml.dump."""
    with open(item_path, "w", encoding="utf-8") as f:
        toml.dump(mod_toml, f)


# ---------------------------------------------------------------------------
# run_packwiz_export
# ---------------------------------------------------------------------------

def run_packwiz_export(packwiz_exe, packwiz_path, export_format="curseforge"):
    """Run 'packwiz {format} export' and return the path to the output file.

    Args:
        packwiz_exe:   Absolute path to the packwiz executable.
        packwiz_path:  Packwiz project root (command runs here).
        export_format: ``'curseforge'`` or ``'modrinth'``.

    Returns:
        Absolute path to the generated zip / mrpack file.

    Raises:
        RuntimeError: if packwiz exits with a non-zero return code.
        FileNotFoundError: if the expected output file is missing after export.
    """
    import subprocess

    ext = ".mrpack" if export_format == "modrinth" else ".zip"

    # Snapshot existing output files before running so we can detect the new one.
    before = {
        f for f in os.listdir(packwiz_path)
        if f.endswith(ext) and os.path.isfile(os.path.join(packwiz_path, f))
    }

    result = subprocess.run(
        [packwiz_exe, export_format, "export"],
        cwd=packwiz_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"packwiz {export_format} export failed:\n{result.stderr.strip()}"
        )

    after = {
        f for f in os.listdir(packwiz_path)
        if f.endswith(ext) and os.path.isfile(os.path.join(packwiz_path, f))
    }
    new_files = after - before
    if not new_files:
        raise FileNotFoundError(
            f"packwiz {export_format} export completed but no new {ext} file found in {packwiz_path}"
        )
    return os.path.join(packwiz_path, new_files.pop())


# ---------------------------------------------------------------------------
# refresh_index
# ---------------------------------------------------------------------------

def refresh_index(packwiz_path):
    """Rebuild index.toml and update pack.toml's index hash.

    Walks the packwiz directory, applies .packwizignore rules, computes SHA-256
    for every tracked file, writes index.toml, then updates pack.toml with the
    new index hash.

    Args:
        packwiz_path: Absolute path to the packwiz directory (contains pack.toml).
    """
    rules = _load_packwizignore_rules(packwiz_path)
    entries = []  # list of (rel_path, is_metafile, full_path)

    _skip_always = {"pack.toml", "index.toml", ".packwizignore"}

    for dirpath, dirnames, filenames in os.walk(packwiz_path):
        # Skip hidden directories (e.g. .git)
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for filename in sorted(filenames):
            full_path = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(full_path, packwiz_path).replace("\\", "/")

            if rel_path in _skip_always:
                continue
            if _is_ignored(rel_path, rules):
                continue

            is_metafile = rel_path.lower().endswith(".pw.toml")
            entries.append((rel_path, is_metafile, full_path))

    # Sort alphabetically (case-insensitive, forward-slash paths)
    entries.sort(key=lambda x: x[0].lower())

    # Write index.toml
    index_path = os.path.join(packwiz_path, "index.toml")
    with open(index_path, "w", encoding="utf-8", newline="\n") as f:
        f.write('hash-format = "sha256"\n')
        for rel_path, is_metafile, full_path in entries:
            file_hash = _sha256_file(full_path)
            f.write("\n[[files]]\n")
            f.write(f'file = "{rel_path}"\n')
            f.write(f'hash = "{file_hash}"\n')
            if is_metafile:
                f.write("metafile = true\n")

    print(f"[Refresh] Indexed {len(entries)} file(s).", flush=True)

    # Update pack.toml with new index hash
    index_hash = _sha256_file(index_path)
    pack_toml_path = os.path.join(packwiz_path, "pack.toml")
    with open(pack_toml_path, "r", encoding="utf-8") as f:
        pack_data = toml.load(f)
    pack_data.setdefault("index", {})["hash"] = index_hash
    with open(pack_toml_path, "w", encoding="utf-8") as f:
        toml.dump(pack_data, f)


# ---------------------------------------------------------------------------
# remove_mod
# ---------------------------------------------------------------------------

def remove_mod(packwiz_path, mods_path, slug):
    """Delete a mod's .pw.toml file and refresh the index.

    Args:
        packwiz_path: Absolute path to the packwiz directory.
        mods_path: Absolute path to the mods subdirectory.
        slug: Mod slug (with or without .pw.toml extension).

    Raises:
        FileNotFoundError: If the .pw.toml file does not exist.
    """
    slug_clean = re.sub(r"\.pw\.toml$", "", slug, flags=re.IGNORECASE)
    toml_path = os.path.join(mods_path, slug_clean + ".pw.toml")
    if not os.path.isfile(toml_path):
        raise FileNotFoundError(f"Mod file not found: {toml_path}")
    os.remove(toml_path)
    refresh_index(packwiz_path)


# ---------------------------------------------------------------------------
# update_single_mod
# ---------------------------------------------------------------------------

def update_single_mod(item_path, mod_toml, mc_version, loader, settings, caches):
    """Update a single mod to its latest compatible version.

    Tries Modrinth first (if the mod has a Modrinth update section), then
    CurseForge. Writes the updated TOML back to disk on success.

    Args:
        item_path: Absolute path to the .pw.toml file.
        mod_toml: Parsed TOML dict (mutated in-place on success).
        mc_version: Target Minecraft version string (e.g. "1.21.1").
        loader: Mod loader name (e.g. "fabric").
        settings: Settings instance (used for alpha update policy).
        caches: Dict with ``"project_versions"`` and ``"project_files"`` sub-dicts.

    Returns:
        True if the mod was updated and item_path was rewritten, False otherwise.
    """
    mr_info = mod_toml.get("update", {}).get("modrinth", {})
    cf_info = mod_toml.get("update", {}).get("curseforge", {})

    # --- Dual-platform: find the latest version available on BOTH platforms ---
    if mr_info.get("mod-id") and cf_info.get("project-id"):
        mr_versions = fetch_modrinth_project_versions(
            mr_info["mod-id"],
            [str(mc_version)],
            [normalize_mod_loader_name(loader, default="fabric")],
            caches["project_versions"],
        )
        cf_files = _get_curseforge_project_files(
            str(cf_info["project-id"]), mc_version, caches["project_files"]
        )
        cf_by_filename = {
            f.get("fileName"): f for f in cf_files
            if _evaluate_curseforge_file_compatibility(f, mc_version, loader) is not False
        }
        for mr_version in mr_versions:
            primary = select_modrinth_primary_file(mr_version)
            if not primary:
                continue
            cf_file = cf_by_filename.get(primary.get("filename"))
            if cf_file is None:
                continue
            # Check if already up to date on both
            if (str(mr_version.get("id", "")) == str(mr_info.get("version", "")) and
                    int(cf_file.get("id", 0)) == int(cf_info.get("file-id", 0))):
                return False
            if not apply_modrinth_version_to_mod_toml(mod_toml, mr_version):
                continue
            sha1 = next(
                (h["value"] for h in cf_file.get("hashes", []) if h.get("algo") == 1), None
            )
            if sha1:
                mod_toml["update"]["curseforge"]["file-id"] = int(cf_file["id"])
            _write_mod_toml(item_path, mod_toml)
            return True
        return False

    # --- Modrinth (single-platform) ---
    if mr_info.get("mod-id"):
        current_channel = infer_release_channel_from_metadata(mod_toml)
        version_payload = select_latest_allowed_modrinth_version(
            project_id=mr_info["mod-id"],
            current_channel=current_channel,
            game_versions=[str(mc_version)],
            loaders=[normalize_mod_loader_name(loader, default="fabric")],
            project_versions_cache=caches["project_versions"],
        )
        if not version_payload:
            return False
        # Skip write if already on this version
        if str(version_payload.get("id", "")) == str(mr_info.get("version", "")):
            return False
        if apply_modrinth_version_to_mod_toml(mod_toml, version_payload):
            _write_mod_toml(item_path, mod_toml)
            return True
        return False

    # --- CurseForge (single-platform) ---
    if cf_info.get("project-id"):
        files = _get_curseforge_project_files(
            str(cf_info["project-id"]), mc_version, caches["project_files"]
        )
        compatible = [
            f for f in files
            if _evaluate_curseforge_file_compatibility(f, mc_version, loader) is not False
        ]
        if not compatible:
            return False
        best = max(compatible, key=lambda f: int(f.get("id", 0)))
        # Skip write if already on this file
        if int(best["id"]) == int(cf_info.get("file-id", 0)):
            return False
        sha1 = next(
            (h["value"] for h in best.get("hashes", []) if h.get("algo") == 1), None
        )
        if not sha1:
            return False
        mod_toml["filename"] = best.get("fileName", mod_toml.get("filename", ""))
        mod_toml.setdefault("download", {})["hash-format"] = "sha1"
        mod_toml["download"]["hash"] = sha1
        mod_toml.setdefault("update", {}).setdefault("curseforge", {})["file-id"] = int(best["id"])
        _write_mod_toml(item_path, mod_toml)
        return True

    return False


# ---------------------------------------------------------------------------
# update_all_mods
# ---------------------------------------------------------------------------

def update_all_mods(packwiz_path, mods_path, mc_version, loader, settings):
    """Update all non-pinned mods to their latest compatible versions.

    Iterates all .pw.toml files in mods_path, skips pinned mods, calls
    update_single_mod for each, then refreshes the index once at the end.

    Args:
        packwiz_path: Absolute path to the packwiz directory.
        mods_path: Absolute path to the mods subdirectory.
        mc_version: Target Minecraft version string.
        loader: Mod loader name.
        settings: Settings instance.

    Returns:
        Number of mods that were successfully updated.
    """
    files = sorted(f for f in os.listdir(mods_path) if f.endswith(".pw.toml"))
    # Load all mod TOMLs first, split into pinned vs. to-update
    to_update = []   # list of (item_path, mod_toml, mod_name)
    pinned_count = 0
    for filename in files:
        item_path = os.path.join(mods_path, filename)
        try:
            with open(item_path, "r", encoding="utf-8") as f:
                mod_toml = toml.load(f)
        except Exception:
            continue
        mod_name = mod_toml.get("name", filename)
        if mod_toml.get("pin"):
            pinned_count += 1
            continue
        to_update.append((item_path, mod_toml, mod_name))

    total = len(to_update)
    print(f"[Update] Checking {total} mod(s) for updates ({pinned_count} pinned, skipped)...", flush=True)

    # Shared caches — dict reads/writes are atomic in CPython; concurrent access is safe
    shared_caches = {"project_versions": {}, "project_files": {}}
    print_lock = threading.Lock()
    completed = [0]
    updated = [0]

    def _update_one(item_path, mod_toml, mod_name):
        try:
            result = update_single_mod(item_path, mod_toml, mc_version, loader, settings, shared_caches)
        except Exception as ex:
            result = ex
        with print_lock:
            completed[0] += 1
            idx = completed[0]
            if isinstance(result, Exception):
                print(f"[Update] [{idx}/{total}] Failed: {mod_name} — {result}", flush=True)
            elif result:
                updated[0] += 1
                print(f"[Update] [{idx}/{total}] Updated: {mod_name}", flush=True)
            else:
                print(f"[Update] [{idx}/{total}] No update: {mod_name}", flush=True)

    with ThreadPoolExecutor(max_workers=total or 1) as executor:
        futures = [
            executor.submit(_update_one, item_path, mod_toml, mod_name)
            for item_path, mod_toml, mod_name in to_update
        ]
        for f in as_completed(futures):
            f.result()  # propagate unexpected exceptions

    print(f"[Update] Done. Updated {updated[0]}/{total} mod(s).", flush=True)
    refresh_index(packwiz_path)
    return updated[0]


# ---------------------------------------------------------------------------
# add_mod helpers
# ---------------------------------------------------------------------------

def add_mod_from_modrinth(packwiz_path, mods_path, identifier, mc_version, loader, settings):
    """Fetch a Modrinth project by slug/id and create its .pw.toml file.

    Args:
        packwiz_path: Absolute path to the packwiz directory.
        mods_path: Absolute path to the mods subdirectory.
        identifier: Modrinth project slug or ID.
        mc_version: Target Minecraft version string.
        loader: Mod loader name.
        settings: Settings instance.

    Returns:
        Absolute path to the created .pw.toml file.

    Raises:
        ValueError: If the project or a compatible version cannot be found.
        RuntimeError: If file metadata extraction fails.
    """
    info_cache = {}
    info = fetch_modrinth_project_info(identifier, info_cache)
    if not info:
        raise ValueError(f"Modrinth project not found: {identifier}")

    project_id = info["id"]
    project_slug = info.get("slug", identifier)
    mod_name = info.get("title", project_slug)

    client_side = str(info.get("client_side", "required")).lower()
    server_side = str(info.get("server_side", "required")).lower()
    if client_side == "unsupported":
        side = "server"
    elif server_side == "unsupported":
        side = "client"
    else:
        side = "both"

    version_cache = {}
    versions = fetch_modrinth_project_versions(
        project_id,
        [str(mc_version)],
        [normalize_mod_loader_name(loader, "fabric")],
        version_cache,
    )
    if not versions:
        raise ValueError(
            f"No compatible Modrinth version found for '{mod_name}' on {mc_version}/{loader}"
        )

    version_payload = versions[0]
    mod_toml = {
        "name": mod_name,
        "filename": "",
        "side": side,
        "download": {},
        "update": {"modrinth": {"mod-id": project_id}},
    }
    if not apply_modrinth_version_to_mod_toml(mod_toml, version_payload):
        raise RuntimeError(f"Failed to extract file info from Modrinth version for '{mod_name}'")

    out_path = os.path.join(mods_path, project_slug + ".pw.toml")
    _write_mod_toml(out_path, mod_toml)
    refresh_index(packwiz_path)
    print(f"[PackManager] Added from Modrinth: {mod_name}")
    return out_path


def add_mod_from_curseforge(packwiz_path, mods_path, identifier, mc_version, loader, settings):
    """Fetch a CurseForge project by ID or slug and create its .pw.toml file.

    Args:
        packwiz_path: Absolute path to the packwiz directory.
        mods_path: Absolute path to the mods subdirectory.
        identifier: CurseForge project ID (numeric) or slug string.
        mc_version: Target Minecraft version string.
        loader: Mod loader name.
        settings: Settings instance.

    Returns:
        Absolute path to the created .pw.toml file.

    Raises:
        ValueError: If the project or a compatible file cannot be found.
    """
    info_cache = {}
    info = fetch_curseforge_project_info(str(identifier), info_cache)
    if not info:
        # Try slug search via CF search API
        resp = requests.get(
            f"{CURSEFORGE_API_BASE}/mods/search",
            params={"gameId": 432, "slug": identifier},
            timeout=20,
        )
        resp.raise_for_status()
        results = (resp.json() or {}).get("data", [])
        if not results:
            raise ValueError(f"CurseForge project not found: {identifier}")
        info = results[0]

    project_id = str(info["id"])
    mod_name = info.get("name", str(identifier))

    proj_files_cache = {}
    files = _get_curseforge_project_files(project_id, mc_version, proj_files_cache)
    compatible = [
        f for f in files
        if _evaluate_curseforge_file_compatibility(f, mc_version, loader) is not False
    ]
    if not compatible:
        raise ValueError(
            f"No compatible CurseForge file found for '{mod_name}' on {mc_version}/{loader}"
        )

    best = max(compatible, key=lambda f: int(f.get("id", 0)))
    sha1 = next(
        (h["value"] for h in best.get("hashes", []) if h.get("algo") == 1), None
    )
    if not sha1:
        raise ValueError(f"No sha1 hash found for '{mod_name}' file {best.get('id')}")

    slug = re.sub(r"[^a-z0-9\-]", "-", mod_name.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)

    mod_toml = {
        "name": mod_name,
        "filename": best.get("fileName", ""),
        "side": "both",
        "download": {
            "hash-format": "sha1",
            "hash": sha1,
            "mode": "metadata:curseforge",
        },
        "update": {
            "curseforge": {
                "file-id": int(best["id"]),
                "project-id": int(project_id),
            }
        },
    }
    out_path = os.path.join(mods_path, slug + ".pw.toml")
    _write_mod_toml(out_path, mod_toml)
    refresh_index(packwiz_path)
    print(f"[PackManager] Added from CurseForge: {mod_name}")
    return out_path


def add_mod_from_url(packwiz_path, mods_path, url, settings):
    """Download a mod from a direct URL and create its .pw.toml file.

    Args:
        packwiz_path: Absolute path to the packwiz directory.
        mods_path: Absolute path to the mods subdirectory.
        url: Direct download URL for the mod jar.
        settings: Settings instance (unused, kept for consistent signature).

    Returns:
        Absolute path to the created .pw.toml file.
    """
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()

    parsed = urllib.parse.urlparse(url)
    filename = os.path.basename(parsed.path) or "mod.jar"
    sha512 = hashlib.sha512(resp.content).hexdigest()

    base = os.path.splitext(filename)[0]
    slug = re.sub(r"[^a-z0-9\-]", "-", base.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)

    mod_toml = {
        "name": slug,
        "filename": filename,
        "side": "both",
        "download": {
            "url": url,
            "hash-format": "sha512",
            "hash": sha512,
        },
    }
    out_path = os.path.join(mods_path, slug + ".pw.toml")
    _write_mod_toml(out_path, mod_toml)
    refresh_index(packwiz_path)
    print(f"[PackManager] Added from URL: {filename}")
    return out_path


# ---------------------------------------------------------------------------
# export_cf_pack
# ---------------------------------------------------------------------------

def _cf_pack_side_included(mod_toml, export_side):
    """Return True if this mod should be included for export_side."""
    side = str(mod_toml.get("side", "both")).lower()
    if "disabled" in side:
        return False
    if export_side == "server" and side == "client":
        return False
    if export_side == "client" and side == "server":
        return False
    return True


def export_cf_pack(packwiz_path, mods_path, output_dir, side="client"):
    """Build a CurseForge modpack zip using fingerprint-based mod resolution.

    Mods that already have ``[update.curseforge]`` metadata are listed in
    ``manifest.json`` directly (fast path — no download needed).

    Mods with only Modrinth / direct-URL metadata are downloaded in parallel,
    their CurseForge murmur2 fingerprint is computed, and a single batch POST
    to CF ``/fingerprints`` resolves their project-id + file-id.  Mods whose
    fingerprint is not found on CurseForge are bundled as jar overrides.

    Args:
        packwiz_path: Absolute path to the packwiz directory.
        mods_path: Absolute path to the mods subdirectory.
        output_dir: Directory where the output zip will be written.
        side: ``"client"`` or ``"server"``.

    Returns:
        Absolute path to the created zip file.
    """
    pack_toml_path = os.path.join(packwiz_path, "pack.toml")
    with open(pack_toml_path, "r", encoding="utf-8") as f:
        pack_data = toml.load(f)

    pack_name = pack_data.get("name", "modpack")
    pack_version = pack_data.get("version", "0.0.0")
    pack_author = pack_data.get("author", "")
    versions = pack_data.get("versions", {})
    mc_version = versions.get("minecraft", "")

    loader_name = None
    loader_version = None
    for key, val in versions.items():
        if key != "minecraft":
            loader_name = key
            loader_version = str(val)
            break

    mod_loaders = []
    if loader_name and loader_version:
        mod_loaders.append({"id": f"{loader_name}-{loader_version}", "primary": True})

    # -------------------------------------------------------------------------
    # Categorise mods
    # -------------------------------------------------------------------------
    cf_direct = []       # {"projectID": int, "fileID": int, "required": True}
    need_download = []   # {"filename": str, "url": str} — need fingerprint lookup

    toml_files = sorted(f for f in os.listdir(mods_path) if f.endswith(".pw.toml"))
    for toml_filename in toml_files:
        item_path = os.path.join(mods_path, toml_filename)
        try:
            with open(item_path, "r", encoding="utf-8") as f:
                mod_toml = toml.load(f)
        except Exception:
            continue

        if not _cf_pack_side_included(mod_toml, side):
            continue

        cf_info = mod_toml.get("update", {}).get("curseforge", {})
        dl = mod_toml.get("download", {})
        dl_url = dl.get("url", "")
        filename = mod_toml.get("filename", "")

        if cf_info.get("project-id") and cf_info.get("file-id"):
            # Already have CF IDs — use them directly.
            cf_direct.append({
                "projectID": int(cf_info["project-id"]),
                "fileID":    int(cf_info["file-id"]),
                "required":  True,
            })
        elif dl_url and filename:
            need_download.append({"filename": filename, "url": dl_url})
        else:
            print(
                f"[Export] Warning: '{mod_toml.get('name', toml_filename)}' has no "
                f"download URL — skipping.",
                flush=True,
            )

    # -------------------------------------------------------------------------
    # Download JARs in parallel
    # -------------------------------------------------------------------------
    downloaded = {}  # filename → bytes

    def _fetch(entry):
        try:
            r = requests.get(entry["url"], timeout=60)
            r.raise_for_status()
            return entry["filename"], r.content
        except Exception as ex:
            print(f"[Export] Warning: failed to download '{entry['filename']}': {ex}", flush=True)
            return entry["filename"], None

    if need_download:
        print(f"[Export] Downloading {len(need_download)} mod(s) for CF fingerprinting...", flush=True)
        with ThreadPoolExecutor(max_workers=8) as pool:
            for fname, data in pool.map(_fetch, need_download):
                if data is not None:
                    downloaded[fname] = data

    # -------------------------------------------------------------------------
    # Fingerprint + batch CF lookup
    # -------------------------------------------------------------------------
    fp_to_fname = {}
    for entry in need_download:
        fname = entry["filename"]
        if fname in downloaded:
            fp = _compute_cf_fingerprint(downloaded[fname])
            fp_to_fname[fp] = fname

    cf_matches = {}
    if fp_to_fname:
        sample_fp, sample_fname = next(iter(fp_to_fname.items()))
        print(
            f"[Export] Sample fingerprint: {sample_fname} → {sample_fp}",
            flush=True,
        )
        print(f"[Export] Looking up {len(fp_to_fname)} fingerprint(s) on CurseForge...", flush=True)
        cf_matches = fetch_cf_fingerprints_batch(list(fp_to_fname.keys()))

    # Split fingerprinted mods into manifest entries vs jar overrides
    cf_files = list(cf_direct)
    cf_overrides = []  # (filename, bytes)

    for entry in need_download:
        fname = entry["filename"]
        jar = downloaded.get(fname)
        if jar is None:
            continue
        fp = _compute_cf_fingerprint(jar)
        match = cf_matches.get(fp)
        if match:
            cf_files.append({
                "projectID": match["project_id"],
                "fileID":    match["file_id"],
                "required":  True,
            })
        else:
            cf_overrides.append((fname, jar))
            print(f"[Export] Not found on CF — bundling as override: {fname}", flush=True)

    # -------------------------------------------------------------------------
    # Build manifest
    # -------------------------------------------------------------------------
    manifest = {
        "minecraft": {
            "version": mc_version,
            "modLoaders": mod_loaders,
        },
        "manifestType": "minecraftModpack",
        "manifestVersion": 1,
        "name": pack_name,
        "version": pack_version,
        "author": pack_author,
        "files": cf_files,
        "overrides": "overrides",
    }

    if side == "server":
        zip_name = f"{pack_name}-{mc_version}-Server-{pack_version}.zip"
    else:
        zip_name = f"{pack_name}-{mc_version}-{pack_version}.zip"
    zip_path = os.path.join(output_dir, zip_name)

    # -------------------------------------------------------------------------
    # Collect non-mod override files (configs, resourcepacks, etc.)
    # -------------------------------------------------------------------------
    _meta_skip = {"pack.toml", "index.toml", ".packwizignore"}
    override_entries = []

    for dirpath, dirnames, filenames in os.walk(packwiz_path):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for filename in sorted(filenames):
            full_path = os.path.join(dirpath, filename)
            rel = os.path.relpath(full_path, packwiz_path).replace("\\", "/")
            if rel in _meta_skip:
                continue
            if rel.endswith(".pw.toml"):
                continue
            if rel.startswith("mods/"):
                continue
            if rel.endswith(".zip") or rel.endswith(".mrpack"):
                continue
            override_entries.append((rel, full_path))

    # -------------------------------------------------------------------------
    # Write ZIP
    # -------------------------------------------------------------------------
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        for rel, full_path in override_entries:
            zf.write(full_path, f"overrides/{rel}")
        for fname, jar_bytes in cf_overrides:
            zf.writestr(f"overrides/mods/{fname}", jar_bytes)

    found = len(cf_files)
    overridden = len(cf_overrides)
    print(
        f"[Export] CF pack written: {zip_name} "
        f"({found} CF manifest entries, {overridden} override(s))",
        flush=True,
    )
    return zip_path


# ---------------------------------------------------------------------------
# export_mrpack
# ---------------------------------------------------------------------------

def export_mrpack(packwiz_path, mods_path, output_dir, side="client"):
    """Build a Modrinth-compatible .mrpack zip from the packwiz project.

    Mods with Modrinth metadata (``update.modrinth``) are listed in
    ``modrinth.index.json`` files[]. CurseForge-only mods are downloaded and
    bundled in ``overrides/mods/``. All other non-packwiz-meta content is
    placed in ``overrides/``.

    Args:
        packwiz_path: Absolute path to the packwiz directory.
        mods_path: Absolute path to the mods subdirectory.
        output_dir: Directory where the output .mrpack will be written.
        side: ``"client"`` or ``"server"``.

    Returns:
        Absolute path to the created .mrpack file.
    """
    pack_toml_path = os.path.join(packwiz_path, "pack.toml")
    with open(pack_toml_path, "r", encoding="utf-8") as f:
        pack_data = toml.load(f)

    pack_name = pack_data.get("name", "modpack")
    pack_version = pack_data.get("version", "0.0.0")
    versions = pack_data.get("versions", {})
    mc_version = versions.get("minecraft", "")

    dependencies = {"minecraft": mc_version}
    for key, val in versions.items():
        if key != "minecraft":
            dep_key = "fabric-loader" if key == "fabric" else key
            dependencies[dep_key] = str(val)

    mr_files = []     # entries for modrinth.index.json files[]
    cf_bundles = []   # mod_toml dicts for CF-only mods that need jar download

    toml_files = sorted(f for f in os.listdir(mods_path) if f.endswith(".pw.toml"))
    for toml_filename in toml_files:
        item_path = os.path.join(mods_path, toml_filename)
        try:
            with open(item_path, "r", encoding="utf-8") as f:
                mod_toml = toml.load(f)
        except Exception:
            continue
        if not _cf_pack_side_included(mod_toml, side):
            continue

        mr_info = mod_toml.get("update", {}).get("modrinth", {})
        cf_info = mod_toml.get("update", {}).get("curseforge", {})
        dl = mod_toml.get("download", {})
        dl_url = dl.get("url", "")
        filename = mod_toml.get("filename", "")

        mod_side = str(mod_toml.get("side", "both")).lower()
        if mod_side == "client":
            env = {"client": "required", "server": "unsupported"}
        elif mod_side == "server":
            env = {"client": "unsupported", "server": "required"}
        else:
            env = {"client": "required", "server": "required"}

        if mr_info.get("mod-id") and dl_url:
            # Has Modrinth metadata — list in files[]
            hashes = {}
            if dl.get("hash-format") == "sha512":
                hashes["sha512"] = dl["hash"]
            elif dl.get("hash-format") == "sha1":
                hashes["sha1"] = dl["hash"]
            mr_files.append({
                "path": f"mods/{filename}",
                "hashes": hashes,
                "env": env,
                "downloads": [dl_url],
                "fileSize": int(dl.get("file-size", 0)),
            })
        elif cf_info.get("project-id") and cf_info.get("file-id"):
            # CF-only — needs to be downloaded and bundled in overrides/mods/
            cf_bundles.append(mod_toml)
        elif dl_url:
            # Direct URL mod — list in files[]
            hashes = {}
            if dl.get("hash"):
                hashes[dl.get("hash-format", "sha1")] = dl["hash"]
            mr_files.append({
                "path": f"mods/{filename}",
                "hashes": hashes,
                "env": env,
                "downloads": [dl_url],
                "fileSize": int(dl.get("file-size", 0)),
            })
        elif mod_toml.get("name"):
            print(f"[PackManager] Warning: '{mod_toml['name']}' has no usable download info for mrpack export — skipping.")

    index = {
        "formatVersion": 1,
        "game": "minecraft",
        "versionId": pack_version,
        "name": pack_name,
        "files": mr_files,
        "dependencies": dependencies,
    }

    zip_name = f"{pack_name}-{mc_version}-{pack_version}.mrpack"
    zip_path = os.path.join(output_dir, zip_name)

    _meta_skip = {"pack.toml", "index.toml", ".packwizignore"}
    override_entries = []
    for dirpath, dirnames, filenames in os.walk(packwiz_path):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for filename_entry in sorted(filenames):
            full_path = os.path.join(dirpath, filename_entry)
            rel = os.path.relpath(full_path, packwiz_path).replace("\\", "/")
            if rel in _meta_skip or rel.endswith(".pw.toml") or rel.startswith("mods/"):
                continue
            # Skip previously exported pack zips sitting in packwiz_path
            if rel.endswith(".zip") or rel.endswith(".mrpack"):
                continue
            override_entries.append((rel, full_path))

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("modrinth.index.json", json.dumps(index, indent=2))
        for rel, full_path in override_entries:
            zf.write(full_path, f"overrides/{rel}")
        # Download and bundle CF-only mods
        for mod_toml in cf_bundles:
            cf_info = mod_toml.get("update", {}).get("curseforge", {})
            cf_filename = mod_toml.get("filename", "")
            cf_project_id = str(cf_info.get("project-id", ""))
            cf_file_id = str(cf_info.get("file-id", ""))
            try:
                dl_url = fetch_curseforge_download_url(cf_project_id, cf_file_id)
                if dl_url:
                    resp = requests.get(dl_url, timeout=30)
                    resp.raise_for_status()
                    zf.writestr(f"overrides/mods/{cf_filename}", resp.content)
                    print(f"[PackManager] Bundled CF-only mod: {cf_filename}")
                else:
                    print(f"[PackManager] Warning: No download URL for '{mod_toml.get('name')}' — skipping.")
            except Exception as ex:
                print(f"[PackManager] Warning: Failed to bundle '{mod_toml.get('name')}': {ex}")

    print(f"[PackManager] Exported Modrinth pack: {zip_name}")
    return zip_path

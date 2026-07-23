"""Pack manager — native export implementations; packwiz CLI delegation for refresh, update, and add."""

import hashlib
import json
import os
import re
import subprocess
import urllib.parse
import zipfile
from concurrent.futures import ThreadPoolExecutor

import requests
import toml

from api_clients import (
    _compute_cf_fingerprint,
    fetch_cf_fingerprints_batch,
    fetch_curseforge_download_url,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _write_mod_toml(item_path, mod_toml):
    """Write mod_toml dict to item_path using toml.dump."""
    with open(item_path, "w", encoding="utf-8") as f:
        toml.dump(mod_toml, f)


# Packwiz metadata files that must never be bundled as pack overrides.
_META_SKIP = {"pack.toml", "index.toml", ".packwizignore"}


def _load_pack_data(packwiz_path):
    """Parse and return the pack.toml of a packwiz project as a dict."""
    with open(os.path.join(packwiz_path, "pack.toml"), "r", encoding="utf-8") as f:
        return toml.load(f)


def _collect_override_entries(packwiz_path):
    """Collect non-mod files to bundle as pack overrides.

    Walks the packwiz tree skipping dot-directories, packwiz metadata,
    ``.pw.toml`` mod entries, the ``mods/`` folder, and previously exported
    pack archives.

    Returns:
        List of ``(relative_path, full_path)`` tuples with forward-slash
        relative paths.
    """
    override_entries = []
    for dirpath, dirnames, filenames in os.walk(packwiz_path):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for filename in sorted(filenames):
            full_path = os.path.join(dirpath, filename)
            rel = os.path.relpath(full_path, packwiz_path).replace("\\", "/")
            if rel in _META_SKIP or rel.endswith(".pw.toml") or rel.startswith("mods/"):
                continue
            if rel.endswith(".zip") or rel.endswith(".mrpack"):
                continue
            override_entries.append((rel, full_path))
    return override_entries


def _print_packwiz(text):
    """Print each non-empty line from packwiz output with a [PackWiz] prefix."""
    for line in text.splitlines():
        if line.strip():
            print(f"[PackWiz] {line}", flush=True)


def _run_packwiz(packwiz_exe, packwiz_path, *args):
    """Run a packwiz subcommand in the pack directory and echo its output.

    Returns:
        The ``subprocess.CompletedProcess``; callers apply their own
        return-code policy.
    """
    result = subprocess.run(
        [packwiz_exe, *args],
        cwd=packwiz_path,
        capture_output=True,
        text=True,
    )
    _print_packwiz(result.stdout)
    return result


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
    ext = ".mrpack" if export_format == "modrinth" else ".zip"

    # Snapshot existing output files before running so we can detect the new one.
    before = {
        f for f in os.listdir(packwiz_path)
        if f.endswith(ext) and os.path.isfile(os.path.join(packwiz_path, f))
    }

    result = _run_packwiz(packwiz_exe, packwiz_path, export_format, "export")
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

def refresh_index(packwiz_exe, packwiz_path):
    """Rebuild the packwiz index by delegating to 'packwiz refresh'.

    Args:
        packwiz_exe: Absolute path to the packwiz executable.
        packwiz_path: Absolute path to the packwiz directory (contains pack.toml).
    """
    result = _run_packwiz(packwiz_exe, packwiz_path, "refresh")
    if result.returncode != 0:
        raise RuntimeError(f"packwiz refresh failed:\n{result.stderr.strip()}")


# ---------------------------------------------------------------------------
# remove_mod
# ---------------------------------------------------------------------------

def remove_mod(packwiz_exe, packwiz_path, mods_path, slug):
    """Delete a mod's .pw.toml file and refresh the index.

    Args:
        packwiz_exe: Absolute path to the packwiz executable.
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
    refresh_index(packwiz_exe, packwiz_path)


# ---------------------------------------------------------------------------
# update_single_mod
# ---------------------------------------------------------------------------

def update_single_mod(packwiz_exe, packwiz_path, item_path):
    """Update a single mod via 'packwiz update <slug> -y'.

    Args:
        packwiz_exe: Absolute path to the packwiz executable.
        packwiz_path: Absolute path to the packwiz directory.
        item_path: Absolute path to the .pw.toml file.

    Returns:
        True if packwiz exited successfully, False otherwise.
    """
    slug = os.path.basename(item_path)
    if slug.endswith(".pw.toml"):
        slug = slug[:-len(".pw.toml")]
    result = _run_packwiz(packwiz_exe, packwiz_path, "update", slug, "-y")
    return result.returncode == 0


# ---------------------------------------------------------------------------
# update_all_mods
# ---------------------------------------------------------------------------

def update_all_mods(packwiz_exe, packwiz_path, mods_path):
    """Update all non-pinned mods via 'packwiz update --all -y'.

    Args:
        packwiz_exe: Absolute path to the packwiz executable.
        packwiz_path: Absolute path to the packwiz directory.
        mods_path: Absolute path to the mods subdirectory.
    """
    files = [f for f in os.listdir(mods_path) if f.endswith(".pw.toml")]
    pinned_count = 0
    for f in files:
        try:
            with open(os.path.join(mods_path, f), "r", encoding="utf-8") as fh:
                if toml.load(fh).get("pin"):
                    pinned_count += 1
        except Exception:
            pass
    print(f"[Update] Updating mods ({pinned_count} pinned, skipped)...", flush=True)
    _run_packwiz(packwiz_exe, packwiz_path, "update", "--all", "-y")
    refresh_index(packwiz_exe, packwiz_path)


# ---------------------------------------------------------------------------
# add_mod helpers
# ---------------------------------------------------------------------------

def add_mod_from_modrinth(packwiz_exe, packwiz_path, identifier):
    """Add a Modrinth mod via 'packwiz modrinth add <identifier>'.

    Args:
        packwiz_exe: Absolute path to the packwiz executable.
        packwiz_path: Absolute path to the packwiz directory.
        identifier: Modrinth project slug or ID.

    Raises:
        RuntimeError: If packwiz exits with a non-zero return code.
    """
    result = _run_packwiz(packwiz_exe, packwiz_path, "modrinth", "add", identifier)
    if result.returncode != 0:
        raise RuntimeError(f"packwiz modrinth add failed for '{identifier}'")


def add_mod_from_curseforge(packwiz_exe, packwiz_path, identifier):
    """Add a CurseForge mod via 'packwiz curseforge add <identifier>'.

    Args:
        packwiz_exe: Absolute path to the packwiz executable.
        packwiz_path: Absolute path to the packwiz directory.
        identifier: CurseForge project ID or slug.

    Raises:
        RuntimeError: If packwiz exits with a non-zero return code.
    """
    result = _run_packwiz(packwiz_exe, packwiz_path, "curseforge", "add", identifier)
    if result.returncode != 0:
        raise RuntimeError(f"packwiz curseforge add failed for '{identifier}'")


def add_mod_from_url(packwiz_exe, packwiz_path, mods_path, url):
    """Download a mod from a direct URL and create its .pw.toml file.

    Args:
        packwiz_exe: Absolute path to the packwiz executable.
        packwiz_path: Absolute path to the packwiz directory.
        mods_path: Absolute path to the mods subdirectory.
        url: Direct download URL for the mod jar.

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
    refresh_index(packwiz_exe, packwiz_path)
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
    pack_data = _load_pack_data(packwiz_path)

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
    fname_to_fp = {}
    for entry in need_download:
        fname = entry["filename"]
        if fname in downloaded:
            fname_to_fp[fname] = _compute_cf_fingerprint(downloaded[fname])

    cf_matches = {}
    if fname_to_fp:
        sample_fname, sample_fp = next(iter(fname_to_fp.items()))
        print(
            f"[Export] Sample fingerprint: {sample_fname} → {sample_fp}",
            flush=True,
        )
        print(f"[Export] Looking up {len(fname_to_fp)} fingerprint(s) on CurseForge...", flush=True)
        cf_matches = fetch_cf_fingerprints_batch(list(fname_to_fp.values()))

    # Split fingerprinted mods into manifest entries vs jar overrides
    cf_files = list(cf_direct)
    cf_overrides = []  # (filename, bytes)

    for entry in need_download:
        fname = entry["filename"]
        jar = downloaded.get(fname)
        if jar is None:
            continue
        match = cf_matches.get(fname_to_fp[fname])
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
    override_entries = _collect_override_entries(packwiz_path)

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
    pack_data = _load_pack_data(packwiz_path)

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

    override_entries = _collect_override_entries(packwiz_path)

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

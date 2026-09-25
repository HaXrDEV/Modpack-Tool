"""Build the CurseForge zip, Modrinth .mrpack and server pack from the packwiz metadata.

Every metafile in index.toml (mods, resource packs, shader packs) is installed
into its own folder, and every other indexed file becomes an override, exactly
as packwiz ships the pack (.packwizignore applies). Disabled files are left out.
Anything that can't be resolved stops the export with a list instead of being
dropped silently.
"""

import hashlib
import json
import shutil
import sys
import tempfile
import tomllib
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from . import pack, platforms, ui
from .project import CACHE_DIR
from .ui import ToolError

# Hosts a .mrpack may download from (anything else must be bundled).
MODRINTH_ALLOWED_HOSTS = {"cdn.modrinth.com", "github.com", "raw.githubusercontent.com", "gitlab.com"}
MRPACK_LOADER_KEYS = {"fabric": "fabric-loader", "quilt": "quilt-loader", "forge": "forge", "neoforge": "neoforge"}


############################################################
# Files and the download cache

def _digest(data, hash_format):
    if hash_format == "murmur2":
        return str(platforms.murmur2(data))
    return hashlib.new(hash_format, data).hexdigest()


class FileStore:
    """Downloads the files a pack needs, keeping them in a cache keyed by their hash."""

    def __init__(self, cache_dir=CACHE_DIR):
        self.dir = Path(cache_dir) / "files"
        self._cf_files = {}

    def _path(self, mod):
        hash_format, hash_value = mod.hash
        return self.dir / f"{hash_format}-{hash_value.lower()}"

    def cached(self, mod):
        path = self._path(mod)
        return path.read_bytes() if path.is_file() else None

    def add(self, mod, data):
        hash_format, hash_value = mod.hash
        if hash_format and _digest(data, hash_format).lower() != hash_value.lower():
            raise ToolError(f"{mod.filename or mod.name}: the file doesn't match the hash in {mod.rel}.")
        self.dir.mkdir(parents=True, exist_ok=True)
        self._path(mod).write_bytes(data)

    def _download_url(self, mod):
        if mod.download.get("url"):
            return mod.download["url"]
        if mod.curseforge:
            file = self._cf_files.get(int(mod.curseforge.get("file-id", 0)))
            return (file or {}).get("downloadUrl")
        return None

    def fetch(self, mods):
        """{metafile path: bytes} for all ``mods``; asks for a folder for anything not downloadable."""
        result, needed = {}, []
        for mod in mods:
            data = self.cached(mod)
            if data is None:
                needed.append(mod)
            else:
                result[mod.rel] = data
        if not needed:
            return result
        cf_ids = [int(mod.curseforge["file-id"]) for mod in needed if mod.curseforge and not mod.download.get("url")]
        if cf_ids:
            self._cf_files.update(platforms.curseforge_files(cf_ids))

        downloadable = [mod for mod in needed if self._download_url(mod)]
        missing = [mod for mod in needed if not self._download_url(mod)]
        if downloadable:
            ui.step(f"Downloading {len(downloadable)} file{'s' * (len(downloadable) != 1)}...")
            failures = []

            def get(mod):
                try:
                    data = platforms.download(self._download_url(mod))
                    self.add(mod, data)
                    return mod, data
                except ToolError as ex:
                    failures.append(f"{mod.name}: {ex}")
                    return mod, None

            with ThreadPoolExecutor(max_workers=8) as pool:
                for done, (mod, data) in enumerate(pool.map(get, downloadable), 1):
                    if data is not None:
                        result[mod.rel] = data
                    _progress(done, len(downloadable))
            if failures:
                raise ToolError("Some downloads failed:\n" + "\n".join(f"  - {line}" for line in failures))
        if missing:
            result.update(self._ask_for_files(missing))
        return result

    def _ask_for_files(self, mods):
        ui.warn(f"{len(mods)} file{'s' * (len(mods) != 1)} can't be downloaded automatically "
                "(their authors block third-party downloads):")
        ui.items(f"{mod.name} ({mod.filename})" for mod in mods)
        ui.info("Download them from CurseForge (or use a CurseForge app instance's mods folder).")
        found = {}
        while len(found) < len(mods):
            folder = ui.clean_path(ui.ask("Folder containing these files (Enter to cancel)"))
            if not folder:
                break
            folder = Path(folder)
            if not folder.is_dir():
                ui.warn(f"{folder} is not a folder.")
                continue
            candidates = [path for path in folder.rglob("*") if path.is_file()]
            for mod in mods:
                if mod.rel in found:
                    continue
                hash_format, hash_value = mod.hash
                named = [path for path in candidates if path.name == mod.filename]
                for path in named + [path for path in candidates if path not in named]:
                    data = path.read_bytes()
                    if not hash_format or _digest(data, hash_format).lower() == hash_value.lower():
                        self.add(mod, data)
                        found[mod.rel] = data
                        break
            still = [mod for mod in mods if mod.rel not in found]
            if still:
                ui.warn("Still missing:")
                ui.items(f"{mod.name} ({mod.filename})" for mod in still)
        still = [mod for mod in mods if mod.rel not in found]
        if still:
            raise ToolError("Export stopped; these files are missing: " + ", ".join(mod.name for mod in still))
        ui.ok(f"Found all {len(found)} file{'s' * (len(found) != 1)}; they are cached for next time.")
        return found


def _progress(done, total):
    if sys.stdout.isatty():
        end = "\n" if done == total else ""
        print(f"\r  {done}/{total}", end=end, flush=True)


############################################################
# What goes into a pack

class PackContents:
    """The pack as packwiz indexes it: active metafiles and override files."""

    def __init__(self, project):
        self.project = project
        pack_toml = pack.read_pack_toml(project.pack_dir)
        self.author = str(pack_toml.get("author") or "")
        self.mods = []
        self.overrides = []
        for entry in pack.index_entries(project.pack_dir):
            rel = str(entry["file"])
            if entry.get("metafile"):
                with open(project.pack_dir / rel, "rb") as f:
                    mod = pack.Mod(rel, tomllib.load(f))
                if not mod.disabled:
                    self.mods.append(mod)
            else:
                self.overrides.append(rel)

    def for_side(self, side):
        return [mod for mod in self.mods if mod.installs_on(side)]

    def write_overrides(self, archive, prefix="overrides"):
        for rel in self.overrides:
            archive.write(self.project.pack_dir / rel, f"{prefix}/{rel}")


def _install_path(mod):
    filename = mod.filename or PurePosixPath(urlparse(mod.download.get("url", "")).path).name
    return f"{mod.folder}/{filename}" if mod.folder != "." else filename


############################################################
# CurseForge

def _fingerprint_cache():
    path = CACHE_DIR / "curseforge-fingerprints.json"
    try:
        return path, json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return path, {}


def resolve_on_curseforge(mods, store):
    """{metafile path: (project id, file id)} for non-CurseForge files that exist on CurseForge.

    Matching uses CurseForge's murmur2 fingerprint of the exact file. Results are
    cached by file hash, so only new files have to be downloaded and fingerprinted.
    """
    cache_path, cache = _fingerprint_cache()
    key = {mod.rel: ":".join(mod.hash) for mod in mods}
    matches, unknown = {}, []
    for mod in mods:
        entry = cache.get(key[mod.rel]) or {}
        if entry.get("match"):
            matches[mod.rel] = tuple(entry["match"])
        else:
            unknown.append(mod)
    if unknown:
        need_bytes = [mod for mod in unknown if "fingerprint" not in cache.get(key[mod.rel], {})]
        data = store.fetch(need_bytes) if need_bytes else {}
        if need_bytes:
            ui.step(f"Fingerprinting {len(need_bytes)} file{'s' * (len(need_bytes) != 1)} for CurseForge...")
        for mod in need_bytes:
            cache.setdefault(key[mod.rel], {})["fingerprint"] = platforms.murmur2(data[mod.rel])
        by_fingerprint = platforms.curseforge_fingerprint_matches(
            cache[key[mod.rel]]["fingerprint"] for mod in unknown)
        for mod in unknown:
            match = by_fingerprint.get(cache[key[mod.rel]]["fingerprint"])
            if match:
                cache[key[mod.rel]]["match"] = list(match)
                matches[mod.rel] = match
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    return matches


def build_curseforge(project, contents, store, output):
    """Write the CurseForge modpack zip; returns (bundled mods, summary)."""
    mods = contents.for_side("client")
    listed, others = [], []
    for mod in mods:
        if mod.curseforge:
            listed.append((mod, (int(mod.curseforge["project-id"]), int(mod.curseforge["file-id"]))))
        else:
            others.append(mod)
    matches = resolve_on_curseforge(others, store) if others else {}
    listed += [(mod, matches[mod.rel]) for mod in others if mod.rel in matches]
    bundled = [mod for mod in others if mod.rel not in matches]
    bundled_data = store.fetch(bundled) if bundled else {}

    manifest = {
        "minecraft": {
            "version": project.minecraft,
            "modLoaders": [{"id": f"{project.loader}-{project.loader_version}", "primary": True}]
            if project.loader else [],
        },
        "manifestType": "minecraftModpack",
        "manifestVersion": 1,
        "name": project.name,
        "version": project.version,
        "author": contents.author,
        "files": [
            {"projectID": project_id, "fileID": file_id,
             "required": not (mod.optional and not (mod.data.get("option") or {}).get("default"))}
            for mod, (project_id, file_id) in sorted(listed, key=lambda item: item[0].rel.lower())
        ],
        "overrides": "overrides",
    }
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        contents.write_overrides(archive)
        for mod in bundled:
            archive.writestr(f"overrides/{_install_path(mod)}", bundled_data[mod.rel])
    return bundled, f"{len(listed)} from CurseForge, {len(bundled)} bundled"


############################################################
# Modrinth

def _env(mod):
    level = "optional" if mod.optional else "required"
    return {
        "client": "unsupported" if mod.side == "server" else level,
        "server": "unsupported" if mod.side == "client" else level,
    }


def build_modrinth(project, contents, store, output):
    """Write the Modrinth .mrpack; returns (bundled mods, summary)."""
    mods = [mod for mod in contents.mods if mod.installs_on("client") or mod.installs_on("server")]
    versions = platforms.modrinth_versions(mod.modrinth.get("version") for mod in mods if mod.modrinth)
    files, bundled, need_bytes = [], [], []
    for mod in mods:
        url = mod.download.get("url", "")
        if urlparse(url).hostname not in MODRINTH_ALLOWED_HOSTS:
            bundled.append(mod)
            continue
        info = None
        if mod.modrinth:
            version = versions.get(str(mod.modrinth.get("version"))) or {}
            hash_format, hash_value = mod.hash
            info = next((f for f in version.get("files", [])
                         if f.get("hashes", {}).get(hash_format) == hash_value or f.get("filename") == mod.filename),
                        None)
        if info and {"sha1", "sha512"} <= set(info.get("hashes", {})) and info.get("size"):
            files.append((mod, url, info["hashes"]["sha1"], info["hashes"]["sha512"], int(info["size"])))
        else:
            need_bytes.append(mod)
    data = store.fetch(need_bytes + bundled) if (need_bytes or bundled) else {}
    for mod in need_bytes:
        content = data[mod.rel]
        files.append((mod, mod.download["url"], hashlib.sha1(content).hexdigest(),
                      hashlib.sha512(content).hexdigest(), len(content)))

    dependencies = {"minecraft": project.minecraft}
    if project.loader:
        dependencies[MRPACK_LOADER_KEYS.get(project.loader, project.loader)] = project.loader_version
    index = {
        "formatVersion": 1,
        "game": "minecraft",
        "versionId": project.version,
        "name": project.name,
        "files": [
            {"path": _install_path(mod), "hashes": {"sha1": sha1, "sha512": sha512}, "env": _env(mod),
             "downloads": [url], "fileSize": size}
            for mod, url, sha1, sha512, size in sorted(files, key=lambda item: item[0].rel.lower())
        ],
        "dependencies": dependencies,
    }
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("modrinth.index.json", json.dumps(index, indent=2))
        contents.write_overrides(archive)
        for mod in bundled:
            folder = {"client": "client-overrides", "server": "server-overrides"}.get(mod.side, "overrides")
            archive.writestr(f"{folder}/{_install_path(mod)}", data[mod.rel])
    return bundled, f"{len(files)} from Modrinth, {len(bundled)} bundled"


############################################################
# Server pack

def _excluded(mod, exclude):
    wanted = {str(entry).strip().lower() for entry in exclude}
    return bool(wanted & {mod.slug.lower(), mod.name.lower(), mod.display_name.lower(), mod.filename.lower()})


def build_server(project, contents, store, output):
    """Write the server pack: the template folder plus every server-side mod jar."""
    template = project.server_template_dir
    mods = [mod for mod in contents.for_side("server") if mod.category == "mods"]
    skipped = [mod for mod in mods if _excluded(mod, project.settings.server_exclude)]
    mods = [mod for mod in mods if mod not in skipped]
    data = store.fetch(mods)
    with tempfile.TemporaryDirectory() as staging:
        staging = Path(staging)
        if template.is_dir():
            shutil.copytree(template, staging, dirs_exist_ok=True)
        else:
            ui.warn(f"No server template folder at {template}; the server pack will only contain mods.")
        (staging / "mods").mkdir(exist_ok=True)
        template_jars = sum(1 for path in (staging / "mods").iterdir() if path.is_file())
        for mod in mods:
            target = staging / "mods" / mod.filename
            if not target.exists():  # A jar placed in the template wins (e.g. a patched build).
                target.write_bytes(data[mod.rel])
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(staging.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(staging).as_posix())
    summary = f"{len(mods)} mods"
    if template_jars:
        summary += f" + {template_jars} from {template.name}"
    if skipped:
        summary += f", left out: {', '.join(mod.name for mod in skipped)}"
    return [], summary


############################################################
# All of it

def _source_link(mod):
    if mod.modrinth:
        return f"https://modrinth.com/project/{mod.modrinth.get('mod-id')}"
    if mod.curseforge:
        return f"https://www.curseforge.com/projects/{mod.curseforge.get('project-id')}"
    if mod.github:
        return f"https://github.com/{mod.github.get('slug')}"
    return mod.download.get("url") or "(link unknown)"


def export_names(project):
    """Export file names by kind, e.g. {"curseforge": "Breakneck-4.11.1.zip", ...}."""
    return {
        "curseforge": f"{project.name}-{project.version}.zip",
        "modrinth": f"{project.name}-{project.version}.mrpack",
        "server": f"{project.name}-Server-{project.version}.zip",
    }


def export(project, kinds):
    """Build the requested packs into Export/; returns the written paths."""
    builders = {"curseforge": build_curseforge, "modrinth": build_modrinth, "server": build_server}
    labels = {"curseforge": "CurseForge pack", "modrinth": "Modrinth pack", "server": "Server pack"}
    project.export_dir.mkdir(parents=True, exist_ok=True)
    contents = PackContents(project)
    store = FileStore()
    written, bundled_by_kind = [], {}
    for kind in kinds:
        ui.step(f"Building the {labels[kind]}...")
        output = project.export_dir / export_names(project)[kind]
        partial = output.with_name(output.name + ".part")
        try:
            bundled, summary = builders[kind](project, contents, store, partial)
            partial.replace(output)
        finally:
            partial.unlink(missing_ok=True)
        written.append(output)
        if bundled:
            bundled_by_kind[labels[kind]] = bundled
        ui.ok(f"{output.name} ({summary})")

    report = project.export_dir / "bundled_links.md"
    if bundled_by_kind:
        lines = [f"# Bundled files in {project.name} {project.version}", "",
                 "These files are included in the packs instead of being downloaded from the platform.",
                 "Check that each license allows redistribution.", ""]
        for label, mods in bundled_by_kind.items():
            lines += [f"## {label}", ""]
            lines += [f"- [{mod.display_name}]({_source_link(mod)}): `{mod.filename}`" for mod in mods]
            lines.append("")
        pack.write_text(report, "\n".join(lines))
        ui.info(f"Bundled files and their sources: {report}")
    return written

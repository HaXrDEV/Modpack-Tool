"""The release cycle: start a new version, build the release, publish it."""

import json
import shutil
import subprocess
from pathlib import Path

from ruamel.yaml.scalarstring import LiteralScalarString

from . import changelog, diff, drafting, exporter, pack, ui
from .ui import ToolError
from .version import is_mc_prefixed_version, is_prerelease, suggest_migration_version, suggest_next_version

LAST_BUILD_FILE = "modpack-tool-last-build.json"


############################################################
# Where the pack stands

def release_tag(project, version=None):
    """The tag of a released version, or None (also when the folder isn't a git repo)."""
    return project.git.tag_for(version or project.version) if project.git.is_repo else None


def changes_since_release(project, since=None):
    """(PackDiff, base ref) for the working copy against the previous release, or (None, None)."""
    if not project.git.is_repo:
        return None, None
    # A released version is compared against its own tag: what isn't in any release yet.
    base = since or release_tag(project) or project.git.previous_release(project.version)
    if not base:
        return None, None
    if not project.git.ref_exists(base):
        raise ToolError(f"'{base}' is not a tag or commit in {project.root}.")
    old_tree = project.git.snapshot(base, pack.TREE_PARTS)
    new_tree = pack.read_tree(project.pack_dir)
    previous = base[1:] if base[:1] == "v" and base[1:2].isdigit() else base
    return diff.compare(old_tree, new_tree, previous, project.version, project.minecraft), base


############################################################
# New version

def _suggest(project):
    if project.settings.mc_prefixed_versions and not is_mc_prefixed_version(project.version):
        return suggest_migration_version(project.minecraft, project.version)
    return suggest_next_version(project.version) or ""


def set_version(project, version):
    """Write the version to pack.toml and the BetterCompatibilityChecker configs."""
    pack.set_pack_version(project.pack_dir, version)
    for path in (project.pack_dir / "config" / "bcc.json", project.server_template_dir / "config" / "bcc.json"):
        pack.write_bcc_version(path, version)
    project.packwiz.refresh()
    project.reload()


def new_version(project, suggestion=None, version=None):
    """Bump to a new version (or rename the current one if it was never released)."""
    ui.title("New version")
    current_tag = release_tag(project)
    ui.info(f"Current version: {project.version} ({'released' if current_tag else 'not released yet'})")
    new = (version or ui.ask("New version", suggestion or _suggest(project))).strip()
    if not new or new == project.version:
        ui.info("Version unchanged.")
        return False
    if release_tag(project, new):
        raise ToolError(f"{new} is already released (tag {release_tag(project, new)}).")

    old_version = project.version
    old_changelog = changelog.changelog_path(project)
    rename = False
    if not current_tag and old_changelog.exists():
        rename = ui.choose(f"{old_version} was never released. Rename it to {new} (keeps its changelog), "
                           f"or start {new} as a separate version?", {"r": "rename", "n": "new"}, "r") == "r"
    if rename:
        target = changelog.changelog_path(project, new)
        if target.exists():
            raise ToolError(f"{target.name} already exists; remove it or choose another version.")
        old_record = changelog.record_path(project)
        old_changelog.rename(target)
        set_version(project, new)
        if old_record.exists():
            record = json.loads(old_record.read_text(encoding="utf-8"))
            record["version"] = new
            changelog.write_record(project, record)
            old_record.unlink()
        ui.ok(f"Renamed {old_version} to {new} ({target.name}).")
    else:
        set_version(project, new)
        path = changelog.create_changelog(project)
        ui.ok(f"Version is now {new}. Changelog: {path.relative_to(project.root)}")
    ui.info("Next: work on the pack, then Draft changelog and Build release.")
    return True


############################################################
# Draft changelog

def _draft_sections(project, changes):
    labels = drafting.ModLabels(pack.load_mods(project.pack_dir))
    return {
        "Update overview": drafting.update_overview(changes),
        "Config Changes": [line[2:] for line in drafting.config_changes(changes, labels)],
    }


def _write_section(data, key, lines):
    if key == "Config Changes":
        data[key] = LiteralScalarString("\n".join(f"- {line}" for line in lines)) if lines else None
    else:
        data[key] = lines or None


def draft(project, since=None, only_empty=False):
    """Fill changelog sections from the changes since the last release; returns True if anything changed."""
    ui.title("Draft changelog")
    if release_tag(project):
        ui.warn(f"{project.version} is already released; start a New version before drafting.")
        return False
    changes, base = changes_since_release(project, since)
    if changes is None:
        ui.warn("No earlier release tag found, so there is nothing to compare against.")
        return False
    ui.info(f"Comparing against {base}: {changes.summary()}")
    path = changelog.create_changelog(project)
    data = changelog.load_changelog(path)
    changed = False
    for key, lines in _draft_sections(project, changes).items():
        existing = changelog.section_lines(data.get(key))
        if existing == lines:
            continue
        if existing:
            if only_empty:
                continue
            ui.info(f"'{key}' has text already. Draft:")
            ui.items(lines or ["(empty)"])
            if not ui.confirm(f"Replace your '{key}' with this draft?", False):
                continue
        elif not lines:
            continue
        _write_section(data, key, lines)
        changed = True
        ui.ok(f"Drafted '{key}' ({len(lines)} line{'s' * (len(lines) != 1)}).")
    if changed:
        changelog.save_changelog(path, data)
    ui.info(f"Changelog: {path.relative_to(project.root)}")
    return changed


############################################################
# Build release

def update_generated_files(project):
    """Keep bcc.json, the Crash Assistant modlist and modlist.md in step with the pack."""
    written = []
    for path in (project.pack_dir / "config" / "bcc.json", project.server_template_dir / "config" / "bcc.json"):
        if pack.write_bcc_version(path, project.version):
            written.append(path)
    mods = pack.load_mods(project.pack_dir)
    outputs = {}
    if (project.pack_dir / "config" / "crash_assistant").is_dir():
        outputs[project.pack_dir / "config" / "crash_assistant" / "modlist.json"] = pack.crash_assistant_modlist(mods)
    if (project.root / "modlist.md").is_file():
        outputs[project.root / "modlist.md"] = pack.modlist_markdown(mods, project.settings.side_tags)
    for path, text in outputs.items():
        current = path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.exists() else None
        if current != text:
            pack.write_text(path, text)
            written.append(path)
    return written


def build(project, since=None, skip_server=False, review=True):
    """Write the release record and notes, update pack files, and build the packs."""
    ui.title(f"Build release: {project.name} {project.version}")
    tag = release_tag(project)
    if tag:
        ui.warn(f"{project.version} is already released (tag {tag}); a new build needs a new version.")
        if not ui.confirm("Start a new version now?", True) or not new_version(project) or release_tag(project):
            return None
    path = changelog.create_changelog(project)
    project.packwiz.refresh()
    changes, base = changes_since_release(project, since)
    if changes:
        ui.info(f"Changes since {base}: {changes.summary()}")
    else:
        ui.warn("No earlier release tag found; the release record won't list mod changes.")

    data = changelog.load_changelog(path)
    empty = [key for key in ("Update overview", "Config Changes") if not changelog.section_lines(data.get(key))]
    if changes and empty and ui.confirm(f"Draft the empty section{'s' * (len(empty) > 1)} "
                                        f"({', '.join(empty)}) from the changes?", True):
        draft(project, since, only_empty=True)
    if review:
        ui.info(f"Changelog: {path.relative_to(project.root)}")
        if ui.confirm("Open it to review before building?", True):
            ui.open_file(path)
            ui.ask("Press Enter when you've saved your edits")
    data = changelog.load_changelog(path)
    for key in changelog.unknown_sections(data):
        ui.warn(f"'{key}' in {path.name} isn't a known section, so it won't appear anywhere.")
    if changelog.is_empty(data):
        raise ToolError(f"{path.name} is empty. Write at least one section (or use Draft changelog) and build again.")

    written = update_generated_files(project)
    project.packwiz.refresh()
    written.append(changelog.write_record(project, changelog.build_record(project, data, changes)))
    written += changelog.write_release_notes(project, data)
    for item in written:
        ui.ok(f"Wrote {item.relative_to(project.root)}")

    kinds = [kind for kind in project.settings.exports if not (skip_server and kind == "server")]
    files = exporter.export(project, kinds) if kinds else []
    last = {"version": project.version, "index_hash": pack.index_hash(project.pack_dir),
            "files": [file.name for file in files]}
    _last_build_path(project).write_text(json.dumps(last, indent=2), encoding="utf-8")
    ui.ok(f"Release {project.version} is built.")
    ui.info("Next: Publish (commit, push and create the GitHub release).")
    return files


############################################################
# Publish

def _last_build_path(project):
    """Where Build notes what it built: inside .git, so it never ends up in a commit."""
    if project.git.is_repo:
        git_dir = Path(project.git.run("rev-parse", "--git-dir").strip())
        return (git_dir if git_dir.is_absolute() else project.root / git_dir) / LAST_BUILD_FILE
    project.export_dir.mkdir(parents=True, exist_ok=True)
    return project.export_dir / LAST_BUILD_FILE


def _last_build(project):
    path = _last_build_path(project)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def publish(project, dry_run=False):
    """Commit, push and create the GitHub release; publish.yml then uploads to CurseForge/Modrinth."""
    ui.title(f"Publish {project.name} {project.version}")
    git = project.git
    if not git.is_repo:
        raise ToolError(f"{project.root} is not a git repository.")
    if release_tag(project):
        raise ToolError(f"{project.version} is already released (tag {release_tag(project)}).")
    last = _last_build(project)
    if not last or last.get("version") != project.version:
        raise ToolError(f"There is no build of {project.version} yet; run Build release first.")
    if pack.index_hash(project.pack_dir) != last.get("index_hash"):
        raise ToolError("The pack changed since the last build. Build release again so the uploaded files match it.")
    files = [project.export_dir / name for name in last.get("files", [])]
    missing = [file.name for file in files if not file.is_file()]
    if missing:
        raise ToolError(f"Missing build output: {', '.join(missing)}. Build release again.")
    notes = project.root / "Modrinth-Release.md"
    branch = git.branch()
    if not branch:
        raise ToolError("The repository is not on a branch (detached HEAD).")
    if not shutil.which("gh"):
        raise ToolError("The GitHub CLI (gh) is needed to create the release: https://cli.github.com")

    message = f"Release {project.version}"
    release_cmd = ["gh", "release", "create", project.version,
                   *[file.relative_to(project.root).as_posix() for file in files],
                   "--title", project.version, "--notes-file", notes.name, "--target", branch]
    if is_prerelease(project.version):
        release_cmd.append("--prerelease")

    changes = git.status()
    if dry_run:
        ui.info("Dry run; these commands would run:")
        if changes:
            ui.items([f'git add --all && git commit -m "{message}"   ({len(changes)} changed paths)'])
        ui.items(["git push", " ".join(f'"{part}"' if " " in part else part for part in release_cmd)])
        return

    if changes:
        ui.info(f"{len(changes)} changed path{'s' * (len(changes) != 1)}:")
        ui.items((line[3:] for line in changes), limit=15)
        if not ui.confirm(f"Commit all of them as '{message}'?", True):
            return
        git.commit_all(message)
        ui.ok("Committed.")
    if not ui.confirm(f"Push {branch} to GitHub?", True):
        return
    git.push()
    ui.ok("Pushed.")
    kind = "pre-release" if is_prerelease(project.version) else "release"
    ui.info(f"This creates the {kind} {project.version} with {', '.join(file.name for file in files)}.")
    ui.info("GitHub Actions (publish.yml) then uploads it to CurseForge/Modrinth.")
    if not ui.confirm("Create the GitHub release now?", True):
        return
    result = subprocess.run(release_cmd, cwd=project.root, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", stdin=subprocess.DEVNULL)
    if result.returncode != 0:
        raise ToolError(f"gh release create failed: {result.stderr.strip() or result.stdout.strip()}")
    git.fetch_tags()
    ui.ok(f"Released: {result.stdout.strip()}")
    ui.info("Next: New version, so further changes go into the next release.")

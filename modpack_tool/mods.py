"""Updating mods and migrating the pack to another Minecraft version.

packwiz does the updating. On top of it the tool adds what packwiz lacks: a
guard against updates that land on alpha versions, an offer to re-enable
disabled mods that received an update, and (for migrations) disabling mods that
have no build for the new Minecraft version.
"""

import tomllib

from . import pack, platforms, ui
from .version import minecraft_content_key, suggest_migration_version, suggest_minor_version, suggest_next_version


############################################################
# Update mods

def update_mods(project):
    ui.title("Update mods")
    project.packwiz.refresh()
    mods = pack.load_mods(project.pack_dir)
    before = pack.snapshot_texts(project.pack_dir, mods)
    selected = _choose_pinned(mods, "packwiz update skips pinned mods.")
    _run_unpinned(project, selected, project.packwiz.update_all)
    _after_update(project, before, migration=False)
    project.packwiz.refresh()
    ui.ok("Mods are up to date.")
    ui.info("Next: Build release when you're ready, or keep editing the pack.")


def _choose_pinned(mods, reason):
    pinned = [mod for mod in mods if mod.pinned]
    if not pinned:
        return []
    ui.info(f"{reason} Pinned: {len(pinned)}")
    return ui.pick_many("Unpin any of them for this run?", pinned, label=lambda mod: mod.name)


def _run_unpinned(project, mods, action):
    """Run ``action`` with ``mods`` temporarily unpinned; the pins always come back."""
    unpinned = []
    try:
        for mod in mods:
            project.packwiz.unpin(mod.slug)
            unpinned.append(mod)
        action()
    finally:
        for mod in unpinned:
            project.packwiz.pin(mod.slug)


def _changed(project, before):
    """[(old Mod, new Mod)] for metafiles whose content changed."""
    current = {mod.rel: mod for mod in pack.load_mods(project.pack_dir)}
    pairs = []
    for rel, old_bytes in before.items():
        mod = current.get(rel)
        if mod is not None and (project.pack_dir / rel).read_bytes() != old_bytes:
            pairs.append((pack.Mod(rel, tomllib.loads(old_bytes.decode("utf-8"))), mod))
    return pairs


def _after_update(project, before, migration):
    changed = _changed(project, before)
    active = [(old, new) for old, new in changed if not new.disabled]
    ui.ok(f"{len(active)} active file{'s' * (len(active) != 1)} updated.")
    _alpha_guard(project, before, active, migration)
    updated_disabled = [new for _, new in _changed(project, before) if new.disabled]
    if updated_disabled:
        where = f"Minecraft {project.minecraft}"
        ui.info(f"{len(updated_disabled)} disabled mod{'s' * (len(updated_disabled) != 1)} received an update, "
                f"so packwiz found a build for {where}:")
        picked = ui.pick_many("Enable any of them?", updated_disabled, label=lambda mod: mod.name)
        with pack.editing(project.packwiz):
            for mod in picked:
                pack.set_disabled(project.pack_dir, mod, False)
        if picked:
            ui.ok(f"Enabled {', '.join(mod.name for mod in picked)}.")


############################################################
# Alpha guard

def _channels(pairs):
    """{metafile path: (old channel, new channel)} from Modrinth version types / CurseForge release types."""
    modrinth_ids = [m.modrinth.get("version") for old, new in pairs for m in (old, new) if m.modrinth]
    curseforge_ids = [m.curseforge.get("file-id") for old, new in pairs for m in (old, new) if m.curseforge]
    versions = platforms.modrinth_versions(modrinth_ids) if modrinth_ids else {}
    files = platforms.curseforge_files(curseforge_ids) if curseforge_ids else {}

    def channel(mod):
        if mod.modrinth:
            return (versions.get(str(mod.modrinth.get("version"))) or {}).get("version_type")
        if mod.curseforge:
            file = files.get(int(mod.curseforge.get("file-id", 0))) or {}
            return platforms.CF_RELEASE_TYPES.get(file.get("releaseType"))
        return None

    return {new.rel: (channel(old), channel(new)) for old, new in pairs}


def _alpha_guard(project, before, pairs, migration):
    candidates = [(old, new) for old, new in pairs if old.modrinth or old.curseforge]
    if not candidates:
        return
    channels = _channels(candidates)
    alphas = [(old, new) for old, new in candidates
              if channels[new.rel][1] == "alpha" and channels[new.rel][0] != "alpha"]
    if not alphas:
        return
    policy = project.settings.alpha_updates
    ui.warn(f"{len(alphas)} update{'s' * (len(alphas) != 1)} landed on an alpha version:")
    ui.items(f"{new.name}: {old.filename} -> {new.filename}" for old, new in alphas)
    if policy == "always":
        ui.info("Keeping them (alpha_updates: always).")
        return
    if policy == "never":
        undo = alphas
    else:
        other = ("reverted, and then disabled as incompatible" if migration
                 else "moved to the newest beta/release, or reverted")
        keep = ui.pick_many(f"Keep which alpha versions? The others are {other}.", alphas,
                            label=lambda pair: pair[1].name)
        undo = [pair for pair in alphas if pair not in keep]
    for old, new in undo:
        target = _newest_allowed(project, old, channels[new.rel][0]) if (new.modrinth and not migration) else None
        if target and target.get("id") != old.modrinth.get("version") and pack.apply_modrinth_version(
                project.pack_dir, new, target):
            ui.info(f"{new.name}: using {target.get('version_number')} ({target.get('version_type')}) instead.")
        else:
            pack.restore(project.pack_dir, new.rel, before[new.rel])
            ui.info(f"{new.name}: reverted to {old.filename}.")
    project.packwiz.refresh()


def _newest_allowed(project, mod, current_channel):
    """The newest Modrinth version on an allowed channel for the pack's Minecraft version and loader."""
    loaders = [project.loader] + (["fabric"] if project.loader == "quilt" else [])
    versions = platforms.modrinth_project_versions(
        mod.modrinth.get("mod-id"), [project.minecraft, *project.acceptable_versions], loaders)
    allowed = platforms.allowed_channels(current_channel or "release")
    return next((v for v in versions if v.get("version_type") in allowed), None)


############################################################
# Migrate Minecraft

def incompatible_mods(project):
    """(incompatible, unknown): active mods whose installed file doesn't list the pack's Minecraft version."""
    mods = [mod for mod in pack.load_mods(project.pack_dir, ("mods",)) if not mod.disabled]
    accepted = {project.minecraft, *project.acceptable_versions}
    versions = platforms.modrinth_versions(mod.modrinth.get("version") for mod in mods if mod.modrinth)
    files = platforms.curseforge_files(mod.curseforge.get("file-id") for mod in mods if mod.curseforge)
    incompatible, unknown = [], []
    for mod in mods:
        if mod.modrinth:
            game_versions = (versions.get(str(mod.modrinth.get("version"))) or {}).get("game_versions")
        elif mod.curseforge:
            game_versions = (files.get(int(mod.curseforge.get("file-id", 0))) or {}).get("gameVersions")
        else:
            game_versions = None
        if not game_versions:
            unknown.append(mod)
        elif not accepted & set(game_versions):
            incompatible.append(mod)
    return incompatible, unknown


def migrate(project, target=None, new_version_step=None):
    """Move the pack to another Minecraft version. ``new_version_step(project, suggestion)`` bumps the version."""
    ui.title("Migrate Minecraft")
    ui.info(f"Now: Minecraft {project.minecraft}, {project.loader_label} {project.loader_version}, "
            f"pack version {project.version}.")
    target = (target or ui.ask("Target Minecraft version")).strip()
    if not target or target == project.minecraft:
        ui.info("Nothing to do.")
        return
    loader_version = ui.ask(f"{project.loader_label} version for {target} ('latest' or a version)", "latest")
    old_minecraft, old_version = project.minecraft, project.version

    # Acceptable versions: a patch (26.1 -> 26.1.1) can usually use builds for its content update.
    keep = [v for v in project.acceptable_versions if minecraft_content_key(v) == minecraft_content_key(target)]
    for version in project.acceptable_versions:
        if version not in keep:
            project.packwiz.remove_acceptable_version(version)
            ui.info(f"No longer accepting builds for {version} (a different content update).")
    if (minecraft_content_key(old_minecraft) == minecraft_content_key(target) and old_minecraft not in keep
            and ui.confirm(f"Also accept mods built for {old_minecraft}? Patch updates usually work with them", True)):
        project.packwiz.add_acceptable_version(old_minecraft)

    mods = pack.load_mods(project.pack_dir)
    before = pack.snapshot_texts(project.pack_dir, mods)
    selected = _choose_pinned(mods, "packwiz won't update pinned mods to the new version.")

    def run():
        ui.step(f"packwiz migrate minecraft {target} (also updates the loader and all mods)...")
        project.packwiz.migrate_minecraft(target)
        if loader_version.lower() != "latest":
            project.packwiz.migrate_loader(loader_version)

    _run_unpinned(project, selected, run)
    project.reload()
    _after_update(project, before, migration=True)

    ui.step(f"Checking which mods have a build for Minecraft {project.minecraft}...")
    incompatible, unknown = incompatible_mods(project)
    if unknown:
        ui.warn("Couldn't check these (no Modrinth/CurseForge data); test them yourself:")
        ui.items(mod.name for mod in unknown)
    if incompatible:
        ui.warn(f"{len(incompatible)} mod{'s' * (len(incompatible) != 1)} have no build for {project.minecraft}:")
        ui.items(f"{mod.name} ({mod.filename})" for mod in incompatible)
        if ui.confirm("Disable them? They stay in the pack and packwiz keeps checking for updates", True):
            with pack.editing(project.packwiz):
                for mod in incompatible:
                    pack.set_disabled(project.pack_dir, mod, True)
            ui.ok(f"Disabled {len(incompatible)} mod{'s' * (len(incompatible) != 1)}.")
    project.packwiz.refresh()
    project.reload()
    ui.ok(f"Migrated from Minecraft {old_minecraft} to {project.minecraft} "
          f"({project.loader_label} {project.loader_version}).")

    if new_version_step:
        if project.mc_prefixed:
            suggestion = suggest_migration_version(project.minecraft, old_version)
        else:
            suggestion = suggest_minor_version(old_version) or suggest_next_version(old_version) or ""
        new_version_step(project, suggestion)

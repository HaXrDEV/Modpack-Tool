"""Check pack: problems and cleanup suggestions that packwiz doesn't point out."""

from . import pack, platforms, ui


def orphaned_libraries(mods):
    """Active library mods that no other active mod requires (per Modrinth/CurseForge dependency data).

    Dependencies are matched within each platform, so a library required by a mod
    from the other platform can show up here; check before removing.
    """
    active = [mod for mod in mods if mod.category == "mods" and not mod.disabled]
    versions = platforms.modrinth_versions(mod.modrinth.get("version") for mod in active if mod.modrinth)
    files = platforms.curseforge_files(mod.curseforge.get("file-id") for mod in active if mod.curseforge)
    required_modrinth = {dep.get("project_id") for version in versions.values()
                         for dep in version.get("dependencies") or [] if dep.get("dependency_type") == "required"}
    required_curseforge = {int(dep.get("modId") or 0) for file in files.values()
                           for dep in file.get("dependencies") or [] if dep.get("relationType") == 3}
    unused = [mod for mod in active
              if (mod.modrinth and mod.modrinth.get("mod-id") not in required_modrinth)
              or (mod.curseforge and int(mod.curseforge.get("project-id", 0)) not in required_curseforge)]
    projects = platforms.modrinth_projects(mod.modrinth.get("mod-id") for mod in unused if mod.modrinth)
    curseforge = platforms.curseforge_mods(mod.curseforge.get("project-id") for mod in unused if mod.curseforge)

    def is_library(mod):
        if mod.modrinth:
            project = projects.get(str(mod.modrinth.get("mod-id"))) or {}
            return "library" in [str(c).lower() for c in project.get("categories") or []]
        project = curseforge.get(int(mod.curseforge.get("project-id", 0))) or {}
        return any("librar" in f"{c.get('name', '')} {c.get('slug', '')}".lower()
                   for c in project.get("categories") or [])

    return [mod for mod in unused if is_library(mod)]


def check(project):
    ui.title("Check pack")
    mods = pack.load_mods(project.pack_dir)
    mod_files = [mod for mod in mods if mod.category == "mods"]
    found = 0

    invalid = [mod for mod in mods if not mod.side_valid]
    if invalid:
        found += 1
        ui.warn("Files with an invalid side (packwiz accepts both, client or server):")
        ui.items(f"{mod.rel}: side = \"{mod.side_raw}\"" for mod in invalid)

    unknown_source = [mod for mod in mod_files if not mod.disabled and not (mod.modrinth or mod.curseforge or mod.github)]
    if unknown_source:
        ui.info("Mods without Modrinth/CurseForge/GitHub metadata (packwiz can't update them):")
        ui.items(mod.name for mod in unknown_source)

    for folder in (project.pack_dir / "disabled", project.pack_dir / "mods" / "disabled"):
        stale = list(folder.rglob("*.toml")) if folder.is_dir() else []
        if stale:
            found += 1
            ui.warn(f"{folder.relative_to(project.root)} holds {len(stale)} old metafiles from the previous way of "
                    "disabling mods; packwiz ignores them, so they can probably be deleted.")

    disabled = [mod for mod in mod_files if mod.disabled]
    pinned = [mod for mod in mods if mod.pinned]
    ui.info(f"Mods: {len(mod_files) - len(disabled)} active, {len(disabled)} disabled, {len(pinned)} pinned.")
    if disabled:
        ui.info("Disabled (packwiz keeps updating them; Update mods offers to re-enable ones that get a build):")
        ui.items((mod.name for mod in disabled), limit=40)
    if pinned:
        ui.info("Pinned (skipped by packwiz update):")
        ui.items(mod.name for mod in pinned)

    ui.step("Looking for library mods nothing depends on...")
    orphans = orphaned_libraries(mods)
    if orphans:
        found += 1
        ui.info("These are tagged as libraries, but no active mod requires them. Some are useful on their own, and "
                "dependencies declared on the other platform aren't seen, so check before removing:")
        picked = ui.pick_many("Remove any of them with packwiz remove?", orphans, label=lambda mod: mod.name)
        for mod in picked:
            project.packwiz.remove(mod.slug)
        if picked:
            ui.ok(f"Removed {', '.join(mod.name for mod in picked)}.")
    else:
        ui.ok("No unused libraries.")
    if not found:
        ui.ok("No problems found.")

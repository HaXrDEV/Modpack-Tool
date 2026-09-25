package workflow

import (
	"context"
	"fmt"
	"io/fs"
	"path/filepath"
	"slices"
	"strings"

	"github.com/HaXrDEV/Modpack-Tool/internal/pack"
	"github.com/HaXrDEV/Modpack-Tool/internal/pycompat"
	"github.com/HaXrDEV/Modpack-Tool/internal/ui"
)

// OrphanedLibraries are active library mods that no other active mod
// requires (per Modrinth/CurseForge dependency data). Dependencies are matched
// within each platform, so a library required by a mod from the other
// platform can show up here; check before removing.
func OrphanedLibraries(ctx context.Context, env *Env, mods []pack.Mod) ([]pack.Mod, error) {
	var active []pack.Mod
	var modrinthIDs []string
	var curseforgeIDs []int64
	for _, mod := range mods {
		if mod.Category() != "mods" || mod.Disabled() {
			continue
		}
		active = append(active, mod)
		if mr := mod.Modrinth(); mr != nil {
			modrinthIDs = append(modrinthIDs, pycompat.Or(mr["version"], ""))
		}
		if cf := mod.CurseForge(); cf != nil {
			curseforgeIDs = append(curseforgeIDs, pack.Int(cf["file-id"]))
		}
	}
	versions, err := env.API.ModrinthVersions(ctx, modrinthIDs)
	if err != nil {
		return nil, err
	}
	files, err := env.API.CurseForgeFiles(ctx, curseforgeIDs)
	if err != nil {
		return nil, err
	}
	requiredModrinth := map[string]bool{}
	for _, v := range versions {
		for _, dep := range v.Dependencies {
			if dep.DependencyType == "required" {
				requiredModrinth[dep.ProjectID] = true
			}
		}
	}
	requiredCurseForge := map[int64]bool{}
	for _, f := range files {
		for _, dep := range f.Dependencies {
			if dep.RelationType == 3 {
				requiredCurseForge[dep.ModID] = true
			}
		}
	}
	var unused []pack.Mod
	var projectIDs []string
	var cfProjectIDs []int64
	for _, mod := range active {
		mr, cf := mod.Modrinth(), mod.CurseForge()
		if mr != nil && !requiredModrinth[pycompat.Or(mr["mod-id"], "")] || cf != nil && !requiredCurseForge[pack.Int(cf["project-id"])] {
			unused = append(unused, mod)
			if mr != nil {
				projectIDs = append(projectIDs, pycompat.Or(mr["mod-id"], ""))
			}
			if cf != nil {
				cfProjectIDs = append(cfProjectIDs, pack.Int(cf["project-id"]))
			}
		}
	}
	projects, err := env.API.ModrinthProjects(ctx, projectIDs)
	if err != nil {
		return nil, err
	}
	cfMods, err := env.API.CurseForgeMods(ctx, cfProjectIDs)
	if err != nil {
		return nil, err
	}
	var libraries []pack.Mod
	for _, mod := range unused {
		if mr := mod.Modrinth(); mr != nil {
			if slices.ContainsFunc(projects[pycompat.Or(mr["mod-id"], "")].Categories, func(c string) bool {
				return strings.ToLower(c) == "library"
			}) {
				libraries = append(libraries, mod)
			}
			continue
		}
		for _, c := range cfMods[pack.Int(mod.CurseForge()["project-id"])].Categories {
			if strings.Contains(strings.ToLower(c.Name+" "+c.Slug), "librar") {
				libraries = append(libraries, mod)
				break
			}
		}
	}
	return libraries, nil
}

// Check points out problems packwiz doesn't: invalid sides, leftover disabled
// folders, disabled and pinned mods, and library mods nothing depends on.
func Check(ctx context.Context, env *Env) error {
	p := env.Project
	mods, warnings, err := pack.LoadMods(p.PackDir(), pack.Categories)
	if err != nil {
		return err
	}
	for _, warning := range warnings {
		env.UI.Warn(warning)
	}
	var modFiles, invalid, unknownSource, disabled, pinned []pack.Mod
	for _, mod := range mods {
		if mod.Category() == "mods" {
			modFiles = append(modFiles, mod)
		}
		if !mod.SideValid() {
			invalid = append(invalid, mod)
		}
		if mod.Pinned() {
			pinned = append(pinned, mod)
		}
	}
	for _, mod := range modFiles {
		if mod.Disabled() {
			disabled = append(disabled, mod)
		} else if mod.Modrinth() == nil && mod.CurseForge() == nil && mod.GitHub() == nil {
			unknownSource = append(unknownSource, mod)
		}
	}
	found := 0
	if len(invalid) > 0 {
		found++
		var lines []string
		for _, mod := range invalid {
			lines = append(lines, fmt.Sprintf("%s: side = \"%s\"", mod.Rel, mod.SideRaw()))
		}
		env.UI.Warn("Files with an invalid side (packwiz accepts both, client or server):", lines...)
	}
	if len(unknownSource) > 0 {
		env.UI.Info("Mods without Modrinth/CurseForge/GitHub metadata (packwiz can't update them):", names(unknownSource)...)
	}
	for _, folder := range []string{filepath.Join(p.PackDir(), "disabled"), filepath.Join(p.PackDir(), "mods", "disabled")} {
		stale := 0
		filepath.WalkDir(folder, func(path string, entry fs.DirEntry, err error) error {
			if err == nil && !entry.IsDir() && strings.HasSuffix(entry.Name(), ".toml") {
				stale++
			}
			return nil
		})
		if stale > 0 {
			found++
			env.UI.Warn(fmt.Sprintf("%s holds %d old metafiles from the previous way of disabling mods; "+
				"packwiz ignores them, so they can probably be deleted.", p.Rel(folder), stale))
		}
	}
	env.UI.Info(fmt.Sprintf("Mods: %d active, %d disabled, %d pinned.", len(modFiles)-len(disabled), len(disabled), len(pinned)))
	if len(disabled) > 0 {
		env.UI.Info("Disabled (packwiz keeps updating them; Update mods offers to re-enable ones that get a build):",
			ui.Limit(names(disabled), 40)...)
	}
	if len(pinned) > 0 {
		env.UI.Info("Pinned (skipped by packwiz update):", names(pinned)...)
	}

	step := env.UI.Step("Looking for library mods nothing depends on")
	orphans, err := OrphanedLibraries(ctx, env, mods)
	if err != nil {
		step.Fail(err)
		return err
	}
	step.Done("")
	if len(orphans) > 0 {
		found++
		env.UI.Info("These are tagged as libraries, but no active mod requires them. Some are useful on their own, and " +
			"dependencies declared on the other platform aren't seen, so check before removing:")
		picked, err := ui.Pick(ctx, env.UI, "Remove any of them with packwiz remove?", orphans, modName)
		if err != nil {
			return err
		}
		for _, mod := range picked {
			if err := env.Packwiz.Remove(ctx, mod.Slug()); err != nil {
				return err
			}
		}
		if len(picked) > 0 {
			env.UI.Info("Removed " + strings.Join(names(picked), ", ") + ".")
		}
	} else {
		env.UI.Info("No unused libraries.")
	}
	if found == 0 {
		env.UI.Result("No problems found.", "")
	}
	return nil
}

func names(mods []pack.Mod) []string {
	result := make([]string, 0, len(mods))
	for _, mod := range mods {
		result = append(result, mod.Name())
	}
	return result
}

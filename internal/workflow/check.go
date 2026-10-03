package workflow

import (
	"context"
	"fmt"
	"io/fs"
	"path/filepath"
	"slices"
	"strings"

	"github.com/HaXrDEV/Modpack-Tool/internal/pack"
	"github.com/HaXrDEV/Modpack-Tool/internal/platform"
	"github.com/HaXrDEV/Modpack-Tool/internal/ui"
)

// OrphanedLibraries are active library mods that no other active mod
// requires (per Modrinth/CurseForge dependency data). Dependencies are matched
// within each platform, so a library required by a mod from the other
// platform can show up here; check before removing.
func OrphanedLibraries(ctx context.Context, env *Env, mods []pack.Mod) ([]pack.Mod, error) {
	var active []pack.Mod
	for _, mod := range mods {
		if mod.Category() == "mods" && !mod.Disabled() {
			active = append(active, mod)
		}
	}
	found, err := lookUpInstalled(ctx, env, active)
	if err != nil {
		return nil, err
	}
	requiredModrinth := map[string]bool{}
	for _, v := range found.versions {
		for _, dep := range v.Dependencies {
			if dep.DependencyType == "required" {
				requiredModrinth[dep.ProjectID] = true
			}
		}
	}
	requiredCurseForge := map[int64]bool{}
	for _, f := range found.files {
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
		onModrinth, onCurseForge := mod.Modrinth() != nil, mod.CurseForge() != nil
		if onModrinth && !requiredModrinth[mod.ModrinthProject()] || onCurseForge && !requiredCurseForge[mod.CurseForgeProject()] {
			unused = append(unused, mod)
			if onModrinth {
				projectIDs = append(projectIDs, mod.ModrinthProject())
			}
			if onCurseForge {
				cfProjectIDs = append(cfProjectIDs, mod.CurseForgeProject())
			}
		}
	}
	var projects map[string]platform.Project
	var cfMods map[int64]platform.CFMod
	err = together(ctx, func(ctx context.Context) (err error) {
		projects, err = env.API.ModrinthProjects(ctx, projectIDs)
		return err
	}, func(ctx context.Context) (err error) {
		cfMods, err = env.API.CurseForgeMods(ctx, cfProjectIDs)
		return err
	})
	if err != nil {
		return nil, err
	}
	var libraries []pack.Mod
	for _, mod := range unused {
		if mod.Modrinth() != nil {
			if slices.ContainsFunc(projects[mod.ModrinthProject()].Categories, func(c string) bool {
				return strings.ToLower(c) == "library"
			}) {
				libraries = append(libraries, mod)
			}
			continue
		}
		for _, c := range cfMods[mod.CurseForgeProject()].Categories {
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
	mods, before, err := loadMods(env)
	if err != nil {
		return err
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
		env.UI.Info("Mods without Modrinth/CurseForge/GitHub metadata (packwiz can't update them):", pack.Names(unknownSource)...)
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
			ui.Limit(pack.Names(disabled), 40)...)
	}
	if len(pinned) > 0 {
		env.UI.Info("Pinned (skipped by packwiz update):", pack.Names(pinned)...)
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
		picked, err := ui.Pick(ctx, env.UI, "Remove any of them with packwiz remove?", orphans, pack.Mod.Name)
		if err != nil {
			return err
		}
		if len(picked) > 0 {
			var removeErr error
			for _, mod := range picked {
				if removeErr = env.Packwiz.Remove(ctx, mod.Slug()); removeErr != nil {
					break
				}
			}
			if removeErr == nil {
				env.UI.Info("Removed " + strings.Join(pack.Names(picked), ", ") + ".")
			}
			// Libraries removed before one failed are gone, so the modlists follow.
			if err := refreshPackFiles(ctx, env, before, removeErr); err != nil {
				return err
			}
		}
	} else {
		env.UI.Info("No unused libraries.")
	}
	if found == 0 {
		env.UI.Result("No problems found.", "")
	}
	return nil
}

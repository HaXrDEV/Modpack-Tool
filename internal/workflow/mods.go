package workflow

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"slices"
	"strings"

	"golang.org/x/sync/errgroup"

	"github.com/HaXrDEV/Modpack-Tool/internal/pack"
	"github.com/HaXrDEV/Modpack-Tool/internal/platform"
	"github.com/HaXrDEV/Modpack-Tool/internal/pycompat"
	"github.com/HaXrDEV/Modpack-Tool/internal/ui"
	"github.com/HaXrDEV/Modpack-Tool/internal/version"
)

// packwiz does the updating. On top of it the tool adds what packwiz lacks:
// shader packs that follow their newest version whatever Minecraft version it
// names, a guard against updates that land on alpha versions, an offer to
// re-enable disabled mods that received an update, and (for migrations)
// disabling mods that have no build for the new Minecraft version.

// UpdateMods runs packwiz update --all, then brings shader packs to their
// newest versions, with the alpha guard and re-enable offers.
func UpdateMods(ctx context.Context, env *Env) error {
	if err := env.Packwiz.Refresh(ctx); err != nil {
		return err
	}
	mods, before, err := loadMods(env)
	if err != nil {
		return err
	}
	selected, err := choosePinned(ctx, env, mods, "packwiz update skips pinned mods.")
	if err != nil {
		return err
	}
	var shadersErr error
	err = runUnpinned(ctx, env, selected, func() error {
		step := env.UI.Step("packwiz update --all")
		if err := env.Packwiz.UpdateAll(ctx); err != nil {
			step.Fail(err)
			return err
		}
		step.Done("")
		// Reported after the alpha guard, which packwiz's updates need either way:
		// a later run can't tell what they changed.
		shadersErr = updateShaders(ctx, env)
		return nil
	})
	if err != nil {
		return err
	}
	if err := errors.Join(afterUpdate(ctx, env, mods, before, false), shadersErr); err != nil {
		return err
	}
	if err := env.Packwiz.Refresh(ctx); err != nil {
		return err
	}
	env.UI.Result("Mods are up to date.", "Build release (4) when you're ready, or keep editing the pack.")
	return nil
}

// updateShaders brings every unpinned Modrinth shader pack to its newest
// version on an allowed channel, whichever Minecraft versions that lists:
// shader packs rarely depend on the Minecraft version, and new versions often
// don't name the newest one, so packwiz update leaves them behind. Versions
// must share a shader loader (iris, optifine, ...) with the installed one.
func updateShaders(ctx context.Context, env *Env) error {
	mods, _, err := loadMods(env)
	if err != nil {
		return err
	}
	var shaders []pack.Mod
	for _, mod := range mods {
		if mod.Category() == "shaderpacks" && mod.Modrinth() != nil && !mod.Pinned() {
			shaders = append(shaders, mod)
		}
	}
	if len(shaders) == 0 {
		return nil
	}
	step := env.UI.Step("Updating shader packs, for any Minecraft version")
	found, err := lookUpInstalled(ctx, env, shaders)
	if err != nil {
		step.Fail(err)
		return err
	}
	var updated []string
	err = editing(ctx, env, func() error {
		for _, mod := range shaders {
			installed, ok := found.versions[mod.ModrinthVersion()]
			if !ok || len(installed.Loaders) == 0 {
				continue // Without its loaders, a newer version might not load.
			}
			versions, err := env.API.ModrinthProjectVersions(ctx, mod.ModrinthProject(), nil, installed.Loaders)
			if err != nil {
				return err
			}
			channel := installed.VersionType
			if channel == "" {
				channel = "release"
			}
			allowed := platform.AllowedChannels(channel)
			i := slices.IndexFunc(versions, func(v platform.Version) bool { return allowed[v.VersionType] })
			if i < 0 || versions[i].ID == installed.ID {
				continue
			}
			target := versions[i]
			var files []pack.ModrinthFile
			for _, f := range target.Files {
				files = append(files, pack.ModrinthFile{URL: f.URL, Filename: f.Filename, Primary: f.Primary, Hashes: f.Hashes})
			}
			applied, err := pack.ApplyModrinthVersion(env.Project.PackDir(), mod, target.ID, files)
			if err != nil {
				return err
			}
			if applied {
				updated = append(updated, fmt.Sprintf("%s: %s -> %s", mod.Name(), installed.VersionNumber, target.VersionNumber))
			}
		}
		return nil
	})
	if err != nil {
		step.Fail(err)
		return err
	}
	step.Done(fmt.Sprintf("%d updated", len(updated)))
	if len(updated) > 0 {
		env.UI.Info("Shader packs on their newest versions:", updated...)
	}
	return nil
}

// loadMods reads the pack's metafiles; the tree holds their bytes, so a later
// look can tell which ones changed. Each unreadable metafile is warned about
// once per action, however often the action looks.
func loadMods(env *Env) ([]pack.Mod, pack.Tree, error) {
	tree, err := pack.ReadTree(env.Project.PackDir(), pack.Categories)
	if err != nil {
		return nil, nil, err
	}
	mods, warnings := pack.ParseMods(tree, pack.Categories)
	for _, warning := range warnings {
		if env.warned == nil {
			env.warned = map[string]bool{}
		}
		if !env.warned[warning] {
			env.warned[warning] = true
			env.UI.Warn(warning)
		}
	}
	return mods, tree, nil
}

// installed is the platform data of the files the mods have installed.
type installed struct {
	versions map[string]platform.Version // Modrinth, by version id.
	files    map[int64]platform.CFFile   // CurseForge, by file id.
}

// lookUpInstalled fetches each mod's installed file from Modrinth and CurseForge.
func lookUpInstalled(ctx context.Context, env *Env, mods []pack.Mod) (installed, error) {
	var modrinthIDs []string
	var curseforgeIDs []int64
	for _, mod := range mods {
		if mod.Modrinth() != nil {
			modrinthIDs = append(modrinthIDs, mod.ModrinthVersion())
		}
		if mod.CurseForge() != nil {
			curseforgeIDs = append(curseforgeIDs, mod.CurseForgeFile())
		}
	}
	var found installed
	err := together(ctx, func(ctx context.Context) (err error) {
		found.versions, err = env.API.ModrinthVersions(ctx, modrinthIDs)
		return err
	}, func(ctx context.Context) (err error) {
		found.files, err = env.API.CurseForgeFiles(ctx, curseforgeIDs)
		return err
	})
	return found, err
}

// together runs independent lookups at the same time.
func together(ctx context.Context, lookups ...func(context.Context) error) error {
	group, groupCtx := errgroup.WithContext(ctx)
	for _, lookup := range lookups {
		group.Go(func() error { return lookup(groupCtx) })
	}
	return group.Wait()
}

func choosePinned(ctx context.Context, env *Env, mods []pack.Mod, reason string) ([]pack.Mod, error) {
	var pinned []pack.Mod
	for _, mod := range mods {
		if mod.Pinned() {
			pinned = append(pinned, mod)
		}
	}
	if len(pinned) == 0 {
		return nil, nil
	}
	env.UI.Info(fmt.Sprintf("%s Pinned: %d", reason, len(pinned)))
	return ui.Pick(ctx, env.UI, "Unpin any of them for this run?", pinned, pack.Mod.Name)
}

// runUnpinned runs action with mods temporarily unpinned; the pins always
// come back, even when the action fails or is canceled.
func runUnpinned(ctx context.Context, env *Env, mods []pack.Mod, action func() error) (err error) {
	var unpinned []pack.Mod
	defer func() {
		cleanup := context.WithoutCancel(ctx)
		for _, mod := range unpinned {
			if pinErr := env.Packwiz.Pin(cleanup, mod.Slug()); pinErr != nil && err == nil {
				err = pinErr
			}
		}
	}()
	for _, mod := range mods {
		if err := env.Packwiz.Unpin(ctx, mod.Slug()); err != nil {
			return err
		}
		unpinned = append(unpinned, mod)
	}
	return action()
}

type modPair struct{ old, current pack.Mod }

// changed returns (old, new) pairs for metafiles whose content changed.
func changed(env *Env, order []pack.Mod, before pack.Tree) ([]modPair, error) {
	now, tree, err := loadMods(env)
	if err != nil {
		return nil, err
	}
	current := map[string]pack.Mod{}
	for _, mod := range now {
		current[mod.Rel] = mod
	}
	var pairs []modPair
	for _, mod := range order {
		next, ok := current[mod.Rel]
		if !ok || bytes.Equal(tree[mod.Rel], before[mod.Rel]) {
			continue
		}
		data, err := pack.DecodeTOML(before[mod.Rel])
		if err != nil {
			continue
		}
		pairs = append(pairs, modPair{pack.Mod{Rel: mod.Rel, Data: data}, next})
	}
	return pairs, nil
}

func afterUpdate(ctx context.Context, env *Env, order []pack.Mod, before pack.Tree, migration bool) error {
	pairs, err := changed(env, order, before)
	if err != nil {
		return err
	}
	var active []modPair
	for _, pair := range pairs {
		if !pair.current.Disabled() {
			active = append(active, pair)
		}
	}
	env.UI.Info(fmt.Sprintf("%d active file%s updated.", len(active), ui.Plural(len(active))))
	if err := alphaGuard(ctx, env, before, active, migration); err != nil {
		return err
	}
	pairs, err = changed(env, order, before)
	if err != nil {
		return err
	}
	var updatedDisabled []pack.Mod
	for _, pair := range pairs {
		if pair.current.Disabled() {
			updatedDisabled = append(updatedDisabled, pair.current)
		}
	}
	if len(updatedDisabled) == 0 {
		return nil
	}
	env.UI.Info(fmt.Sprintf("%d disabled mod%s received an update, so packwiz found a build for Minecraft %s:",
		len(updatedDisabled), ui.Plural(len(updatedDisabled)), env.Project.Minecraft))
	picked, err := ui.Pick(ctx, env.UI, "Enable any of them?", updatedDisabled, pack.Mod.Name)
	if err != nil || len(picked) == 0 {
		return err
	}
	if err := editing(ctx, env, func() error {
		for _, mod := range picked {
			if err := pack.SetDisabled(env.Project.PackDir(), mod, false); err != nil {
				return err
			}
		}
		return nil
	}); err != nil {
		return err
	}
	env.UI.Info("Enabled " + strings.Join(pack.Names(picked), ", ") + ".")
	// Enabled, they ship whatever build packwiz picked, so the alpha guard
	// looks at them too (reverting one also disables it again).
	if pairs, err = changed(env, order, before); err != nil {
		return err
	}
	enabled := slices.DeleteFunc(pairs, func(pair modPair) bool {
		return !slices.ContainsFunc(picked, func(mod pack.Mod) bool { return mod.Rel == pair.current.Rel })
	})
	return alphaGuard(ctx, env, before, enabled, migration)
}

// editing groups metafile edits; the index is refreshed once they are done.
func editing(ctx context.Context, env *Env, edit func() error) error {
	err := edit()
	if refreshErr := env.Packwiz.Refresh(context.WithoutCancel(ctx)); err == nil {
		err = refreshErr
	}
	return err
}

////////////////////////////////////////////////////////////
// Alpha guard

// channels returns {metafile path: (old channel, new channel)} from Modrinth
// version types and CurseForge release types.
func channels(ctx context.Context, env *Env, pairs []modPair) (map[string][2]string, error) {
	var mods []pack.Mod
	for _, pair := range pairs {
		mods = append(mods, pair.old, pair.current)
	}
	found, err := lookUpInstalled(ctx, env, mods)
	if err != nil {
		return nil, err
	}
	channel := func(mod pack.Mod) string {
		if mod.Modrinth() != nil {
			return found.versions[mod.ModrinthVersion()].VersionType
		}
		if mod.CurseForge() != nil {
			return platform.CurseForgeReleaseTypes[found.files[mod.CurseForgeFile()].ReleaseType]
		}
		return ""
	}
	result := map[string][2]string{}
	for _, pair := range pairs {
		result[pair.current.Rel] = [2]string{channel(pair.old), channel(pair.current)}
	}
	return result, nil
}

func alphaGuard(ctx context.Context, env *Env, before pack.Tree, pairs []modPair, migration bool) error {
	var candidates []modPair
	for _, pair := range pairs {
		if pair.old.Modrinth() != nil || pair.old.CurseForge() != nil {
			candidates = append(candidates, pair)
		}
	}
	if len(candidates) == 0 {
		return nil
	}
	found, err := channels(ctx, env, candidates)
	if err != nil {
		return err
	}
	var alphas []modPair
	for _, pair := range candidates {
		if c := found[pair.current.Rel]; c[1] == "alpha" && c[0] != "alpha" {
			alphas = append(alphas, pair)
		}
	}
	if len(alphas) == 0 {
		return nil
	}
	var listing []string
	for _, pair := range alphas {
		listing = append(listing, fmt.Sprintf("%s: %s -> %s", pair.current.Name(), pair.old.Filename(), pair.current.Filename()))
	}
	env.UI.Warn(fmt.Sprintf("%d update%s landed on an alpha version:", len(alphas), ui.Plural(len(alphas))), listing...)
	undo := alphas
	switch env.Project.Settings.AlphaUpdates {
	case "always":
		env.UI.Info("Keeping them (alpha_updates: always).")
		return nil
	case "never":
	default:
		other := "moved to the newest beta/release, or reverted"
		if migration {
			other = fmt.Sprintf("moved to the newest beta/release for Minecraft %s, or disabled as incompatible without one",
				env.Project.Minecraft)
		}
		keep, err := ui.Pick(ctx, env.UI, "Keep which alpha versions? The others are "+other+".", alphas,
			func(pair modPair) string { return pair.current.Name() })
		if err != nil {
			return err
		}
		undo = slices.DeleteFunc(slices.Clone(alphas), func(pair modPair) bool {
			return slices.ContainsFunc(keep, func(k modPair) bool { return k.current.Rel == pair.current.Rel })
		})
	}
	return editing(ctx, env, func() error {
		for _, pair := range undo {
			applied := false
			// In a migration too: the pack is on its new Minecraft version by now, so
			// this finds builds for it, and only a mod without one is reverted (and
			// then disabled as incompatible).
			if pair.current.Modrinth() != nil && pair.old.Modrinth() != nil {
				target, err := newestAllowed(ctx, env, pair.old, found[pair.current.Rel][0])
				if err != nil {
					return err
				}
				if target != nil && target.ID != pair.old.ModrinthVersion() {
					var files []pack.ModrinthFile
					for _, f := range target.Files {
						files = append(files, pack.ModrinthFile{URL: f.URL, Filename: f.Filename, Primary: f.Primary, Hashes: f.Hashes})
					}
					if applied, err = pack.ApplyModrinthVersion(env.Project.PackDir(), pair.current, target.ID, files); err != nil {
						return err
					}
					if applied {
						env.UI.Info(fmt.Sprintf("%s: using %s (%s) instead.", pair.current.Name(), target.VersionNumber, target.VersionType))
					}
				}
			}
			if !applied {
				if err := pack.Restore(env.Project.PackDir(), pair.current.Rel, before[pair.current.Rel]); err != nil {
					return err
				}
				env.UI.Info(fmt.Sprintf("%s: reverted to %s.", pair.current.Name(), pair.old.Filename()))
			}
		}
		return nil
	})
}

// newestAllowed is the newest Modrinth version on an allowed channel for the
// pack's Minecraft version and loader.
func newestAllowed(ctx context.Context, env *Env, mod pack.Mod, currentChannel string) (*platform.Version, error) {
	p := env.Project
	loaders := []string{p.Loader}
	if p.Loader == "quilt" {
		loaders = append(loaders, "fabric")
	}
	versions, err := env.API.ModrinthProjectVersions(ctx, mod.ModrinthProject(),
		append([]string{p.Minecraft}, p.AcceptableVersions...), loaders)
	if err != nil {
		return nil, err
	}
	if currentChannel == "" {
		currentChannel = "release"
	}
	allowed := platform.AllowedChannels(currentChannel)
	for i, v := range versions {
		if allowed[v.VersionType] {
			return &versions[i], nil
		}
	}
	return nil, nil
}

////////////////////////////////////////////////////////////
// Migrate Minecraft

// IncompatibleMods returns the active mods whose installed file doesn't list
// the pack's Minecraft version, and the ones that can't be checked.
func IncompatibleMods(ctx context.Context, env *Env) ([]pack.Mod, []pack.Mod, error) {
	p := env.Project
	all, _, err := pack.LoadMods(p.PackDir(), []string{"mods"})
	if err != nil {
		return nil, nil, err
	}
	mods := slices.DeleteFunc(all, pack.Mod.Disabled)
	found, err := lookUpInstalled(ctx, env, mods)
	if err != nil {
		return nil, nil, err
	}
	accepted := map[string]bool{p.Minecraft: true}
	for _, v := range p.AcceptableVersions {
		accepted[v] = true
	}
	var incompatible, unknown []pack.Mod
	for _, mod := range mods {
		var gameVersions []string
		if mod.Modrinth() != nil {
			gameVersions = found.versions[mod.ModrinthVersion()].GameVersions
		} else if mod.CurseForge() != nil {
			gameVersions = found.files[mod.CurseForgeFile()].GameVersions
		}
		if len(gameVersions) == 0 {
			unknown = append(unknown, mod)
		} else if !slices.ContainsFunc(gameVersions, func(v string) bool { return accepted[v] }) {
			incompatible = append(incompatible, mod)
		}
	}
	return incompatible, unknown, nil
}

// Migrate moves the pack to another Minecraft version, then starts a new version.
func Migrate(ctx context.Context, env *Env, target string) error {
	p := env.Project
	env.UI.Info(fmt.Sprintf("Now: Minecraft %s, %s %s, pack version %s.", p.Minecraft, p.LoaderLabel(), p.LoaderVersion, p.Version))
	var err error
	if target == "" {
		if target, err = env.UI.Ask(ctx, "Target Minecraft version", ""); err != nil {
			return err
		}
	}
	if target = pycompat.Strip(target); target == "" || target == p.Minecraft {
		env.UI.Info("Nothing to do.")
		return nil
	}
	loaderVersion, err := env.UI.Ask(ctx, fmt.Sprintf("%s version for %s ('latest' or a version)", p.LoaderLabel(), target), "latest")
	if err != nil {
		return err
	}
	oldMinecraft, oldVersion := p.Minecraft, p.Version

	// Acceptable versions: a patch (26.1 -> 26.1.1) can usually use builds for its content update.
	for _, v := range p.AcceptableVersions {
		if version.ContentKey(v) != version.ContentKey(target) {
			if err := env.Packwiz.RemoveAcceptableVersion(ctx, v); err != nil {
				return err
			}
			env.UI.Info(fmt.Sprintf("No longer accepting builds for %s (a different content update).", v))
		}
	}
	if version.ContentKey(oldMinecraft) == version.ContentKey(target) && !slices.Contains(p.AcceptableVersions, oldMinecraft) {
		accept, err := env.UI.Confirm(ctx, fmt.Sprintf("Also accept mods built for %s? Patch updates usually work with them", oldMinecraft), true)
		if err != nil {
			return err
		}
		if accept {
			if err := env.Packwiz.AddAcceptableVersion(ctx, oldMinecraft); err != nil {
				return err
			}
		}
	}

	mods, before, err := loadMods(env)
	if err != nil {
		return err
	}
	selected, err := choosePinned(ctx, env, mods, "packwiz won't update pinned mods to the new version.")
	if err != nil {
		return err
	}
	err = runUnpinned(ctx, env, selected, func() error {
		step := env.UI.Step(fmt.Sprintf("packwiz migrate minecraft %s (also updates the loader and all mods)", target))
		if err := env.Packwiz.MigrateMinecraft(ctx, target); err != nil {
			step.Fail(err)
			return err
		}
		if strings.ToLower(loaderVersion) != "latest" {
			if err := env.Packwiz.MigrateLoader(ctx, loaderVersion); err != nil {
				step.Fail(err)
				return err
			}
		}
		step.Done("")
		return nil
	})
	if err != nil {
		return err
	}
	if err := p.Reload(); err != nil {
		return err
	}
	if err := followMinecraft(ctx, env, oldMinecraft); err != nil {
		return err
	}
	if err := afterUpdate(ctx, env, mods, before, true); err != nil {
		return err
	}

	step := env.UI.Step(fmt.Sprintf("Checking which mods have a build for Minecraft %s", p.Minecraft))
	incompatible, unknown, err := IncompatibleMods(ctx, env)
	if err != nil {
		step.Fail(err)
		return err
	}
	step.Done("")
	if len(unknown) > 0 {
		env.UI.Warn("Couldn't check these (no Modrinth/CurseForge data); test them yourself:", pack.Names(unknown)...)
	}
	disabled := 0
	if len(incompatible) > 0 {
		var names []string
		for _, mod := range incompatible {
			names = append(names, fmt.Sprintf("%s (%s)", mod.Name(), mod.Filename()))
		}
		env.UI.Warn(fmt.Sprintf("%d mod%s have no build for %s:", len(incompatible), ui.Plural(len(incompatible)), p.Minecraft), names...)
		disable, err := env.UI.Confirm(ctx, "Disable them? They stay in the pack and packwiz keeps checking for updates", true)
		if err != nil {
			return err
		}
		if disable {
			disabled = len(incompatible)
			if err := editing(ctx, env, func() error {
				for _, mod := range incompatible {
					if err := pack.SetDisabled(p.PackDir(), mod, true); err != nil {
						return err
					}
				}
				return nil
			}); err != nil {
				return err
			}
			env.UI.Info(fmt.Sprintf("Disabled %d mod%s.", len(incompatible), ui.Plural(len(incompatible))))
		}
	}
	if err := env.Packwiz.Refresh(ctx); err != nil {
		return err
	}
	if err := p.Reload(); err != nil {
		return err
	}
	env.UI.Info(fmt.Sprintf("Migrated from Minecraft %s to %s (%s %s).", oldMinecraft, p.Minecraft, p.LoaderLabel(), p.LoaderVersion))

	suggestion := version.MigrationVersion(p.Minecraft, oldVersion)
	if !p.MCPrefixed() {
		if suggestion = version.MinorVersion(oldVersion); suggestion == "" {
			suggestion = version.NextVersion(oldVersion)
		}
	}
	// Missing mods make the pack less feature complete than a full release.
	if disabled > 0 && suggestion != "" {
		suggestion += "-beta.1"
		env.UI.Info(fmt.Sprintf("Suggesting a beta, since the pack is missing %d mod%s until they're updated.", disabled, ui.Plural(disabled)))
	}
	_, err = NewVersion(ctx, env, suggestion, "")
	return err
}

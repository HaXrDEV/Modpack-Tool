package workflow

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"

	"github.com/HaXrDEV/Modpack-Tool/internal/changelog"
	"github.com/HaXrDEV/Modpack-Tool/internal/diff"
	"github.com/HaXrDEV/Modpack-Tool/internal/draft"
	"github.com/HaXrDEV/Modpack-Tool/internal/export"
	"github.com/HaXrDEV/Modpack-Tool/internal/fail"
	"github.com/HaXrDEV/Modpack-Tool/internal/files"
	"github.com/HaXrDEV/Modpack-Tool/internal/pack"
	"github.com/HaXrDEV/Modpack-Tool/internal/pycompat"
	"github.com/HaXrDEV/Modpack-Tool/internal/ui"
	"github.com/HaXrDEV/Modpack-Tool/internal/version"
)

// LastBuildFile is where Build notes what it built, inside .git so it never
// ends up in a commit.
const LastBuildFile = "modpack-tool-last-build.json"

////////////////////////////////////////////////////////////
// Where the pack stands

// ReleaseTag is the tag of a released version (the current one when empty),
// or "" (also when the folder isn't a git repo).
func ReleaseTag(ctx context.Context, env *Env, v string) (string, error) {
	if !env.Git.IsRepo() {
		return "", nil
	}
	if v == "" {
		v = env.Project.Version
	}
	return env.Git.TagFor(ctx, v)
}

// ChangesSinceRelease compares the working copy with the previous release
// (or since); nil when there is nothing to compare against. A released
// version is compared against its own tag: what isn't in any release yet.
// details=false skips config line diffs, enough for the status.
func ChangesSinceRelease(ctx context.Context, env *Env, since string, details bool) (*diff.PackDiff, string, error) {
	if !env.Git.IsRepo() {
		return nil, "", nil
	}
	p := env.Project
	base := since
	if base == "" {
		tag, err := ReleaseTag(ctx, env, "")
		if err != nil {
			return nil, "", err
		}
		base = tag
	}
	if base == "" {
		previous, err := env.Git.PreviousRelease(ctx, p.Version, "HEAD")
		if err != nil {
			return nil, "", err
		}
		base = previous
	}
	if base == "" {
		return nil, "", nil
	}
	if ok, err := env.Git.RefExists(ctx, base); err != nil {
		return nil, "", err
	} else if !ok {
		return nil, "", fail.Errorf("'%s' is not a tag or commit in %s.", base, p.Root)
	}
	oldTree, err := env.Git.Snapshot(ctx, base, pack.TreeParts, "Packwiz")
	if err != nil {
		return nil, "", err
	}
	newTree, err := pack.ReadTree(p.PackDir(), pack.TreeParts)
	if err != nil {
		return nil, "", err
	}
	previous := base
	if len(base) > 1 && base[0] == 'v' && base[1] >= '0' && base[1] <= '9' {
		previous = base[1:]
	}
	return diff.Compare(oldTree, newTree, previous, p.Version, p.Minecraft, details), base, nil
}

////////////////////////////////////////////////////////////
// New version

func suggestVersion(env *Env) string {
	p := env.Project
	if p.Settings.MCPrefixedVersions && !version.IsMCPrefixed(p.Version) {
		return version.MigrationVersion(p.Minecraft, p.Version)
	}
	return version.NextVersion(p.Version)
}

// SetVersion writes the version to pack.toml and the BetterCompatibilityChecker configs.
func SetVersion(ctx context.Context, env *Env, v string) error {
	p := env.Project
	err := editing(ctx, env, func() error {
		if err := pack.SetPackVersion(p.PackDir(), v); err != nil {
			return err
		}
		for _, path := range bccFiles(env) {
			if _, err := pack.WriteBCCVersion(path, v); err != nil {
				return err
			}
		}
		return nil
	})
	if err != nil {
		return err
	}
	return p.Reload()
}

func bccFiles(env *Env) []string {
	return []string{filepath.Join(env.Project.PackDir(), "config", "bcc.json"),
		filepath.Join(env.Project.ServerTemplateDir(), "config", "bcc.json")}
}

// NewVersion bumps to a new version, or renames the current one if it was
// never released. It returns false when the version stayed the same.
func NewVersion(ctx context.Context, env *Env, suggestion, newVersion string) (bool, error) {
	p := env.Project
	currentTag, err := ReleaseTag(ctx, env, "")
	if err != nil {
		return false, err
	}
	state := "not released yet"
	if currentTag != "" {
		state = "released"
	}
	env.UI.Info(fmt.Sprintf("Current version: %s (%s)", p.Version, state))
	if newVersion == "" {
		if suggestion == "" {
			suggestion = suggestVersion(env)
		}
		if newVersion, err = env.UI.Ask(ctx, "New version", suggestion); err != nil {
			return false, err
		}
	}
	newVersion = pycompat.Strip(newVersion)
	if newVersion == "" || newVersion == p.Version {
		env.UI.Info("Version unchanged.")
		return false, nil
	}
	if tag, err := ReleaseTag(ctx, env, newVersion); err != nil {
		return false, err
	} else if tag != "" {
		return false, fail.Errorf("%s is already released (tag %s).", newVersion, tag)
	}

	oldVersion := p.Version
	oldChangelog := changelog.Path(p, "", "")
	rename := false
	if currentTag == "" && files.Exists(oldChangelog) {
		choice, err := env.UI.Choose(ctx, fmt.Sprintf("%s was never released. Rename it to %s (keeps its changelog), "+
			"or start %s as a separate version?", oldVersion, newVersion, newVersion),
			[]ui.Option{{Key: "r", Label: "rename"}, {Key: "n", Label: "new"}}, "r")
		if err != nil {
			return false, err
		}
		rename = choice == "r"
	}
	if !rename {
		if err := SetVersion(ctx, env, newVersion); err != nil {
			return false, err
		}
		path, err := changelog.Create(p, "")
		if err != nil {
			return false, err
		}
		env.UI.Result(fmt.Sprintf("Version is now %s. Changelog: %s", newVersion, p.Rel(path)),
			"work on the pack, then Draft changelog (3) and Build release (4).")
		return true, nil
	}
	target := changelog.Path(p, newVersion, "")
	if files.Exists(target) {
		return false, fail.Errorf("%s already exists; remove it or choose another version.", filepath.Base(target))
	}
	oldRecord := changelog.RecordPath(p, "", "")
	if err := files.Rename(oldChangelog, target); err != nil {
		return false, err
	}
	if err := SetVersion(ctx, env, newVersion); err != nil {
		return false, err
	}
	if record, err := pack.ReadJSONObject(oldRecord); !errors.Is(err, fs.ErrNotExist) {
		if err != nil {
			return false, err
		}
		record.Set("version", newVersion)
		if _, err := changelog.WriteRecord(p, record); err != nil {
			return false, err
		}
		if err := os.Remove(oldRecord); err != nil {
			return false, err
		}
	}
	env.UI.Result(fmt.Sprintf("Renamed %s to %s (%s).", oldVersion, newVersion, filepath.Base(target)),
		"work on the pack, then Draft changelog (3) and Build release (4).")
	return true, nil
}

////////////////////////////////////////////////////////////
// Draft changelog

func draftSections(env *Env, changes *diff.PackDiff) ([]string, []string, error) {
	mods, _, err := pack.LoadMods(env.Project.PackDir(), pack.Categories)
	if err != nil {
		return nil, nil, err
	}
	var config []string
	for _, line := range draft.ConfigChanges(changes, draft.NewLabels(mods)) {
		config = append(config, strings.TrimPrefix(line, "- "))
	}
	return draft.UpdateOverview(changes), config, nil
}

// Draft fills the Update overview and Config Changes sections from the
// changes since the last release. Text you wrote is only replaced after you
// confirm (and never with onlyEmpty). It returns whether anything changed.
func Draft(ctx context.Context, env *Env, since string, onlyEmpty bool) (bool, error) {
	p := env.Project
	if tag, err := ReleaseTag(ctx, env, ""); err != nil {
		return false, err
	} else if tag != "" {
		env.UI.Warn(fmt.Sprintf("%s is already released; start a New version (2) before drafting.", p.Version))
		return false, nil
	}
	step := env.UI.Step("Comparing with the last release")
	changes, base, err := ChangesSinceRelease(ctx, env, since, true)
	if err != nil {
		step.Fail(err)
		return false, err
	}
	if changes == nil {
		step.Done("")
		env.UI.Warn("No earlier release tag found, so there is nothing to compare against.")
		return false, nil
	}
	step.Done(fmt.Sprintf("Comparing against %s: %s", base, changes.Summary()))
	return applyDraft(ctx, env, changes, onlyEmpty)
}

// applyDraft writes the drafted sections into the changelog.
func applyDraft(ctx context.Context, env *Env, changes *diff.PackDiff, onlyEmpty bool) (bool, error) {
	p := env.Project
	path, err := changelog.Create(p, "")
	if err != nil {
		return false, err
	}
	data, err := changelog.Load(path)
	if err != nil {
		return false, err
	}
	overview, config, err := draftSections(env, changes)
	if err != nil {
		return false, err
	}
	changed := false
	for _, section := range []struct {
		key   string
		lines []string
	}{{"Update overview", overview}, {"Config Changes", config}} {
		existing := data.Lines(section.key)
		if slices.Equal(existing, section.lines) {
			continue
		}
		if len(existing) > 0 {
			if onlyEmpty {
				continue
			}
			shown := section.lines
			if len(shown) == 0 {
				shown = []string{"(empty)"}
			}
			env.UI.Info(fmt.Sprintf("'%s' has text already. Draft:", section.key), shown...)
			replace, err := env.UI.Confirm(ctx, fmt.Sprintf("Replace your '%s' with this draft?", section.key), false)
			if err != nil {
				return false, err
			}
			if !replace {
				continue
			}
		} else if len(section.lines) == 0 {
			continue
		}
		if err := data.SetSection(section.key, section.lines); err != nil {
			return false, err
		}
		changed = true
		env.UI.Info(fmt.Sprintf("Drafted '%s' (%d line%s).", section.key, len(section.lines), ui.Plural(len(section.lines))))
	}
	if changed {
		if err := data.Save(); err != nil {
			return false, err
		}
	}
	env.UI.Info("Changelog: " + p.Rel(path))
	return changed, nil
}

////////////////////////////////////////////////////////////
// Build release

var workflowEnv = regexp.MustCompile(`(?m)^(\s+)(MC_VERSION|RELEASE_TYPE|PRE_RELEASE):[^\n]*$`)

// SyncPublishWorkflow updates an old-style publish.yml that hard-codes
// MC_VERSION, RELEASE_TYPE and PRE_RELEASE. Workflows that read these from
// the release itself don't have those keys, so nothing changes for them. It
// returns the path when the file was rewritten.
func SyncPublishWorkflow(env *Env) (string, error) {
	p := env.Project
	path := filepath.Join(p.Root, ".github", "workflows", "publish.yml")
	text, err := pycompat.ReadText(path)
	if errors.Is(err, fs.ErrNotExist) {
		return "", nil
	} else if err != nil {
		return "", err
	}
	kind := "release"
	switch {
	case strings.Contains(strings.ToLower(p.Version), "alpha"):
		kind = "alpha"
	case version.IsPrerelease(p.Version):
		kind = "beta"
	}
	preRelease := "true"
	if kind == "release" {
		preRelease = "false"
	}
	values := map[string]string{"MC_VERSION": p.Minecraft, "RELEASE_TYPE": kind, "PRE_RELEASE": preRelease}
	updated := workflowEnv.ReplaceAllStringFunc(text, func(match string) string {
		m := workflowEnv.FindStringSubmatch(match)
		return m[1] + m[2] + ": " + values[m[2]]
	})
	if updated == text {
		return "", nil
	}
	return path, pycompat.WriteText(path, updated)
}

// UpdateGeneratedFiles keeps bcc.json, the Crash Assistant modlist,
// modlist.md and an old-style publish.yml in step; it returns what it wrote.
func UpdateGeneratedFiles(ctx context.Context, env *Env) ([]string, error) {
	p := env.Project
	var written []string
	if path, err := SyncPublishWorkflow(env); err != nil {
		return nil, err
	} else if path != "" {
		written = append(written, path)
	}
	for _, path := range bccFiles(env) {
		if changed, err := pack.WriteBCCVersion(path, p.Version); err != nil {
			return nil, err
		} else if changed {
			written = append(written, path)
		}
	}
	mods, _, err := loadMods(env)
	if err != nil {
		return nil, err
	}
	type output struct{ path, text string }
	var outputs []output
	if files.IsDir(filepath.Join(p.PackDir(), "config", "crash_assistant")) {
		outputs = append(outputs, output{filepath.Join(p.PackDir(), "config", "crash_assistant", "modlist.json"), pack.CrashAssistantModlist(mods)})
	}
	if files.IsFile(filepath.Join(p.Root, "modlist.md")) {
		outputs = append(outputs, output{filepath.Join(p.Root, "modlist.md"), pack.ModlistMarkdown(mods, p.Settings.SideTags)})
	}
	for _, o := range outputs {
		current, err := pycompat.ReadText(o.path)
		if err == nil && current == o.text {
			continue
		}
		if err := pycompat.WriteText(o.path, o.text); err != nil {
			return nil, err
		}
		written = append(written, o.path)
	}
	return written, nil
}

// LastBuild is what Build release last built.
type LastBuild struct {
	Version       string   `json:"version"`
	IndexHash     string   `json:"index_hash"`
	ChangelogHash string   `json:"changelog_hash"`
	Files         []string `json:"files"`
}

func lastBuildPath(ctx context.Context, env *Env) (string, error) {
	if env.Git.IsRepo() {
		dir, err := env.Git.GitDir(ctx)
		if err != nil {
			return "", err
		}
		return filepath.Join(dir, LastBuildFile), nil
	}
	return filepath.Join(env.Project.ExportDir(), LastBuildFile), nil
}

// ReadLastBuild returns the last build, or nil.
func ReadLastBuild(ctx context.Context, env *Env) *LastBuild {
	path, err := lastBuildPath(ctx, env)
	if err != nil {
		return nil
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var last LastBuild
	if json.Unmarshal(data, &last) != nil {
		return nil
	}
	return &last
}

// BuildIsCurrent reports whether the last build matches the pack and the
// changelog as they are now; the reason says why not.
func BuildIsCurrent(env *Env, last *LastBuild) (bool, string) {
	p := env.Project
	if last == nil || last.Version != p.Version {
		return false, fmt.Sprintf("There is no build of %s yet; run Build release first.", p.Version)
	}
	if hash, _ := pack.IndexHash(p.PackDir()); hash != last.IndexHash {
		return false, "The pack changed since the last build. Build release again so the uploaded files match it."
	}
	if export.FileDigest(changelog.Path(p, "", ""), "sha256") != last.ChangelogHash {
		return false, "The changelog changed since the last build. Build release again so the release notes match it."
	}
	return true, ""
}

// Build writes the release record and notes, updates the pack's generated
// files and builds the packs listed in exports. It returns the built files.
func Build(ctx context.Context, env *Env, since string, skipServer, review bool) ([]string, error) {
	p := env.Project
	if tag, err := ReleaseTag(ctx, env, ""); err != nil {
		return nil, err
	} else if tag != "" {
		env.UI.Warn(fmt.Sprintf("%s is already released (tag %s); a new build needs a new version.", p.Version, tag))
		start, err := env.UI.Confirm(ctx, "Start a new version now?", true)
		if err != nil || !start {
			return nil, err
		}
		if changed, err := NewVersion(ctx, env, "", ""); err != nil || !changed {
			return nil, err
		}
		if tag, err := ReleaseTag(ctx, env, ""); err != nil || tag != "" {
			return nil, err
		}
	}
	path, err := changelog.Create(p, "")
	if err != nil {
		return nil, err
	}
	step := env.UI.Step("Refreshing the index and comparing with the last release")
	if err := env.Packwiz.Refresh(ctx); err != nil {
		step.Fail(err)
		return nil, err
	}
	changes, base, err := ChangesSinceRelease(ctx, env, since, true)
	if err != nil {
		step.Fail(err)
		return nil, err
	}
	if changes != nil {
		step.Done(fmt.Sprintf("Changes since %s: %s", base, changes.Summary()))
	} else {
		step.Done("")
		env.UI.Warn("No earlier release tag found; the release record won't list mod changes.")
	}

	data, err := changelog.Load(path)
	if err != nil {
		return nil, err
	}
	var empty []string
	for _, key := range []string{"Update overview", "Config Changes"} {
		if len(data.Lines(key)) == 0 {
			empty = append(empty, key)
		}
	}
	if changes != nil && len(empty) > 0 {
		ok, err := env.UI.Confirm(ctx, fmt.Sprintf("Draft the empty section%s (%s) from the changes?",
			ui.Plural(len(empty)), strings.Join(empty, ", ")), true)
		if err != nil {
			return nil, err
		}
		if ok {
			if _, err := applyDraft(ctx, env, changes, true); err != nil {
				return nil, err
			}
		}
	}
	if review {
		env.UI.Info("Changelog: " + p.Rel(path))
		open, err := env.UI.Confirm(ctx, "Open it to review before building?", true)
		if err != nil {
			return nil, err
		}
		if open {
			if err := env.UI.WaitForEdit(ctx, path); err != nil {
				return nil, err
			}
		}
	}
	if data, err = changelog.Load(path); err != nil {
		return nil, err
	}
	for _, key := range data.UnknownSections() {
		env.UI.Warn(fmt.Sprintf("'%s' in %s isn't a known section, so it won't appear anywhere.", key, filepath.Base(path)))
	}
	if data.IsEmpty() {
		return nil, fail.Errorf("%s is empty. Write at least one section (or use Draft changelog) and build again.", filepath.Base(path))
	}

	step = env.UI.Step("Writing the release record, release notes and pack files")
	var written []string
	err = editing(ctx, env, func() (err error) {
		written, err = UpdateGeneratedFiles(ctx, env)
		return err
	})
	var recordPath string
	if err == nil {
		recordPath, err = changelog.WriteRecord(p, changelog.BuildRecord(p, data, changes, env.Now().Format("2006-01-02")))
	}
	var notes []string
	if err == nil {
		notes, err = changelog.WriteReleaseNotes(p, data, func(msg string) { env.UI.Warn(msg) })
	}
	if err != nil {
		step.Fail(err)
		return nil, err
	}
	written = append(append(written, recordPath), notes...)
	var names []string
	for _, path := range written {
		names = append(names, p.Rel(path))
	}
	step.Done("Wrote " + strings.Join(names, ", "))

	var kinds []string
	for _, kind := range p.Settings.Exports {
		if !(skipServer && kind == "server") {
			kinds = append(kinds, kind)
		}
	}
	built, err := export.Export(ctx, p, kinds, env.Store)
	if err != nil {
		return nil, err
	}
	indexHash, err := pack.IndexHash(p.PackDir())
	if err != nil {
		return nil, err
	}
	last := LastBuild{Version: p.Version, IndexHash: indexHash, ChangelogHash: export.FileDigest(path, "sha256"), Files: []string{}}
	for _, file := range built {
		last.Files = append(last.Files, filepath.Base(file))
	}
	lastPath, err := lastBuildPath(ctx, env)
	if err != nil {
		return nil, err
	}
	encoded, err := pycompat.Dumps(last, 2, true)
	if err == nil {
		err = pycompat.WriteText(lastPath, string(encoded))
	}
	if err != nil {
		return nil, err
	}
	env.UI.Result(fmt.Sprintf("Release %s is built.", p.Version), "Publish (5): commit, push and create the GitHub release.")
	return built, nil
}

////////////////////////////////////////////////////////////
// Publish

// Publish commits, pushes and creates the GitHub release; the pack's
// publish.yml then uploads it to CurseForge and Modrinth.
func Publish(ctx context.Context, env *Env, dryRun bool) error {
	p := env.Project
	if !env.Git.IsRepo() {
		return fail.Errorf("%s is not a git repository.", p.Root)
	}
	if tag, err := ReleaseTag(ctx, env, ""); err != nil {
		return err
	} else if tag != "" {
		return fail.Errorf("%s is already released (tag %s).", p.Version, tag)
	}
	last := ReadLastBuild(ctx, env)
	if last != nil && last.Version == p.Version {
		if err := env.Packwiz.Refresh(ctx); err != nil { // So edits made after the build show up in the index hash.
			return err
		}
	}
	if current, reason := BuildIsCurrent(env, last); !current {
		return fail.Errorf("%s", reason)
	}
	var built, missing []string
	for _, name := range last.Files {
		path := filepath.Join(p.ExportDir(), name)
		built = append(built, path)
		if !files.IsFile(path) {
			missing = append(missing, name)
		}
	}
	if len(missing) > 0 {
		return fail.Errorf("Missing build output: %s. Build release again.", strings.Join(missing, ", "))
	}
	branch, err := env.Git.Branch(ctx)
	if err != nil {
		return err
	}
	if branch == "" {
		return fail.Errorf("The repository is not on a branch (detached HEAD).")
	}
	if _, err := env.LookPath("gh"); err != nil {
		return fail.Errorf("The GitHub CLI (gh) is needed to create the release: https://cli.github.com")
	}

	message := "Release " + p.Version
	args := []string{"release", "create", p.Version}
	for _, file := range built {
		args = append(args, p.Rel(file))
	}
	args = append(args, "--title", p.Version, "--notes-file", changelog.NotesFile("modrinth"), "--target", branch)
	if version.IsPrerelease(p.Version) {
		args = append(args, "--prerelease")
	}
	changes, err := env.Git.Status(ctx)
	if err != nil {
		return err
	}
	if dryRun {
		var commands []string
		if len(changes) > 0 {
			commands = append(commands, fmt.Sprintf(`git add --all && git commit -m "%s"   (%d changed paths)`, message, len(changes)))
		}
		quoted := []string{"gh"}
		for _, arg := range args {
			quoted = append(quoted, quoteArg(arg))
		}
		env.UI.Info("Dry run; these commands would run:", append(commands, "git push", strings.Join(quoted, " "))...)
		return nil
	}

	if len(changes) > 0 {
		var paths []string
		for _, line := range changes {
			if len(line) > 3 {
				paths = append(paths, line[3:])
			}
		}
		env.UI.Info(fmt.Sprintf("%d changed path%s:", len(changes), ui.Plural(len(changes))), ui.Limit(paths, 15)...)
		ok, err := env.UI.Confirm(ctx, fmt.Sprintf("Commit all of them as '%s'?", message), true)
		if err != nil || !ok {
			return err
		}
		step := env.UI.Step("Committing")
		if err := env.Git.CommitAll(ctx, message); err != nil {
			step.Fail(err)
			return err
		}
		step.Done("Committed.")
	}
	ok, err := env.UI.Confirm(ctx, fmt.Sprintf("Push %s to GitHub?", branch), true)
	if err != nil || !ok {
		return err
	}
	step := env.UI.Step("Pushing " + branch)
	if err := env.Git.Push(ctx); err != nil {
		step.Fail(err)
		return err
	}
	step.Done("Pushed.")
	kind := "release"
	if version.IsPrerelease(p.Version) {
		kind = "pre-release"
	}
	env.UI.Info(fmt.Sprintf("This creates the %s %s with %s.", kind, p.Version, strings.Join(last.Files, ", ")),
		"GitHub Actions (publish.yml) then uploads it to CurseForge/Modrinth.")
	if ok, err := env.UI.Confirm(ctx, "Create the GitHub release now?", true); err != nil || !ok {
		return err
	}
	step = env.UI.Step("Creating the GitHub release")
	stdout, stderr, code, err := env.RunGH(ctx, p.Root, args...)
	if err == nil && code != 0 {
		message := strings.TrimSpace(stderr)
		if message == "" {
			message = strings.TrimSpace(stdout)
		}
		err = fail.Errorf("gh release create failed: %s", message)
	}
	if err != nil {
		step.Fail(err)
		return err
	}
	step.Done("Released: " + strings.TrimSpace(stdout))
	env.Git.FetchTags(ctx)
	env.UI.Result(fmt.Sprintf("Released %s.", p.Version), "New version (2), so further changes go into the next release.")
	return nil
}

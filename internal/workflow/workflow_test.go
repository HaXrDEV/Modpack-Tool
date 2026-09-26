package workflow

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/HaXrDEV/Modpack-Tool/internal/changelog"
	"github.com/HaXrDEV/Modpack-Tool/internal/export"
	"github.com/HaXrDEV/Modpack-Tool/internal/files"
	"github.com/HaXrDEV/Modpack-Tool/internal/git"
	"github.com/HaXrDEV/Modpack-Tool/internal/pack"
	"github.com/HaXrDEV/Modpack-Tool/internal/platform"
	"github.com/HaXrDEV/Modpack-Tool/internal/project"
	"github.com/HaXrDEV/Modpack-Tool/internal/testutil"
	"github.com/HaXrDEV/Modpack-Tool/internal/ui/uitest"
)

// fakePackwiz records packwiz calls instead of running packwiz.
type fakePackwiz struct {
	mu    sync.Mutex
	calls []string
}

func (f *fakePackwiz) record(call string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls = append(f.calls, call)
	return nil
}

func (f *fakePackwiz) Refresh(context.Context) error               { return f.record("refresh") }
func (f *fakePackwiz) UpdateAll(context.Context) error             { return f.record("update --all") }
func (f *fakePackwiz) Pin(_ context.Context, slug string) error    { return f.record("pin " + slug) }
func (f *fakePackwiz) Unpin(_ context.Context, slug string) error  { return f.record("unpin " + slug) }
func (f *fakePackwiz) Remove(_ context.Context, slug string) error { return f.record("remove " + slug) }
func (f *fakePackwiz) MigrateMinecraft(_ context.Context, v string) error {
	return f.record("migrate minecraft " + v)
}
func (f *fakePackwiz) MigrateLoader(_ context.Context, v string) error {
	return f.record("migrate loader " + v)
}
func (f *fakePackwiz) AddAcceptableVersion(_ context.Context, v string) error {
	return f.record("add acceptable " + v)
}
func (f *fakePackwiz) RemoveAcceptableVersion(_ context.Context, v string) error {
	return f.record("remove acceptable " + v)
}

type fixture struct {
	*Env
	packwiz *fakePackwiz
	api     *platform.Fake
	session *uitest.Session
	opened  []string // What OpenPath opened.
}

// answers gives the next prompts their answers (a fresh scripted session).
func (f *fixture) answers(values ...string) *uitest.Session {
	f.session = uitest.New(values...)
	f.UI = f.session
	f.Store.Session = f.session
	return f.session
}

func newFixture(t *testing.T, root string) *fixture {
	t.Helper()
	p, _, err := project.Open(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	f := &fixture{packwiz: &fakePackwiz{}, api: &platform.Fake{}}
	f.Env = &Env{
		Project:  p,
		Packwiz:  f.packwiz,
		Git:      git.New(p.Root),
		API:      f.api,
		Store:    export.NewStore(filepath.Join(t.TempDir(), "cache"), f.api, nil),
		Now:      func() time.Time { return time.Date(2026, 9, 25, 12, 0, 0, 0, time.UTC) },
		LookPath: func(string) (string, error) { return "", errors.New("not found") },
		RunGH: func(context.Context, string, ...string) (string, string, int, error) {
			return "", "", 1, errors.New("gh must not run in tests")
		},
		OpenPath: func(path string) error {
			f.opened = append(f.opened, path)
			return nil
		},
	}
	f.answers()
	return f
}

// repoProject is a pack in a git repo: release 1.0.0 is tagged.
func repoProject(t *testing.T) *fixture {
	t.Helper()
	testutil.RequireGit(t)
	root := filepath.Join(t.TempDir(), "Pack")
	pw := filepath.Join(root, "Packwiz")
	testutil.Write(t, filepath.Join(pw, "pack.toml"), "name = \"Pack\"\nversion = \"1.0.0\"\n[index]\nfile = \"index.toml\"\nhash = \"h1\"\n"+
		"[versions]\nfabric = \"0.18.4\"\nminecraft = \"1.21.11\"\n")
	testutil.Write(t, filepath.Join(pw, "index.toml"), "hash-format = \"sha256\"\n")
	testutil.Write(t, filepath.Join(pw, "mods", "a.pw.toml"), testutil.Metafile("Alpha Mod", "a-1.jar"))
	testutil.Write(t, filepath.Join(pw, "config", "bcc.json"), `{"modpackVersion": "1.0.0"}`)
	testutil.Write(t, filepath.Join(pw, "config", "crash_assistant", "config.toml"), "")
	testutil.Write(t, filepath.Join(root, "modlist.md"), "old")
	testutil.Write(t, filepath.Join(root, "Changelogs", "1.0.0+1.21.11.yml"), "Update overview:\n  - First.\n")
	testutil.Write(t, filepath.Join(root, "modpack-tool.yml"), "exports: []\n")
	testutil.InitRepo(t, root)
	testutil.Git(t, root, "add", "-A")
	testutil.Git(t, root, "commit", "-q", "-m", "1.0.0")
	testutil.Git(t, root, "tag", "1.0.0")
	return newFixture(t, root)
}

var ctx = context.Background()

func mustNewVersion(t *testing.T, f *fixture, v string) {
	t.Helper()
	if _, err := NewVersion(ctx, f.Env, "", v); err != nil {
		t.Fatal(err)
	}
}

// py: test_workflows.py::test_new_version_bumps_after_a_release
func TestNewVersionBumpsAfterARelease(t *testing.T) {
	f := repoProject(t)
	f.answers("") // Accept the suggested 1.0.1.
	if changed, err := NewVersion(ctx, f.Env, "", ""); !changed || err != nil {
		t.Fatal(changed, err)
	}
	if f.Project.Version != "1.0.1" {
		t.Error(f.Project.Version)
	}
	if !files.Exists(filepath.Join(f.Project.ChangelogDir(), "1.0.1+1.21.11.yml")) {
		t.Error("no changelog")
	}
	var bcc map[string]string
	testutil.ReadJSON(t, filepath.Join(f.Project.PackDir(), "config", "bcc.json"), &bcc)
	if bcc["modpackVersion"] != "1.0.1" {
		t.Error(bcc)
	}
}

// Starting the next version removes the released version's changelog when
// its record has the same notes, found by the record's Minecraft version also
// after Migrate moved the pack to a new one. One that differs from its record,
// or has none, stays.
func TestNewVersionDropsTheReleasedChangelog(t *testing.T) {
	for name, c := range map[string]struct {
		text, minecraft string
		record, kept    bool
	}{
		"as released":       {"Update overview:\n  - First.\n", "1.21.11", true, false},
		"reformatted":       {"# Notes\r\nUpdate overview:\r\n- First.\r\nBug Fixes:\r\n", "1.21.11", true, false},
		"after a migration": {"Update overview:\n  - First.\n", "26.1", true, false},
		"edited":            {"Update overview:\n  - First, edited.\n", "1.21.11", true, true},
		"without a record":  {"Update overview:\n  - First.\n", "1.21.11", false, true},
	} {
		f := repoProject(t)
		path := testutil.Write(t, filepath.Join(f.Project.ChangelogDir(), "1.0.0+1.21.11.yml"), c.text)
		if c.record {
			record := changelog.Record{Version: "1.0.0", Minecraft: "1.21.11", Overview: []string{"First."}}
			if _, err := changelog.WriteRecord(f.Project, record); err != nil {
				t.Fatal(err)
			}
		}
		toml := filepath.Join(f.Project.PackDir(), "pack.toml")
		testutil.Write(t, toml, strings.Replace(testutil.Read(t, toml), `"1.21.11"`, `"`+c.minecraft+`"`, 1))
		if err := f.Project.Reload(); err != nil {
			t.Fatal(err)
		}
		mustNewVersion(t, f, "1.1.0")
		if files.Exists(path) != c.kept {
			t.Errorf("%s: kept %v\n%s", name, !c.kept, f.session.Text())
		}
	}
}

// py: test_workflows.py::test_new_version_renames_an_unreleased_version
func TestNewVersionRenamesAnUnreleasedVersion(t *testing.T) {
	f := repoProject(t)
	mustNewVersion(t, f, "1.1.0")
	if _, err := changelog.WriteRecord(f.Project, map[string]string{"version": "1.1.0"}); err != nil {
		t.Fatal(err)
	}
	f.answers("r")
	mustNewVersion(t, f, "2.0.0")
	if files.Exists(filepath.Join(f.Project.ChangelogDir(), "1.1.0+1.21.11.yml")) ||
		!files.Exists(filepath.Join(f.Project.ChangelogDir(), "2.0.0+1.21.11.yml")) {
		t.Error("changelog not renamed")
	}
	var record map[string]string
	testutil.ReadJSON(t, filepath.Join(f.Project.DataDir(), "2.0.0+1.21.11.json"), &record)
	if record["version"] != "2.0.0" {
		t.Error(record)
	}
}

// py: test_workflows.py::test_new_version_refuses_a_released_version
func TestNewVersionRefusesAReleasedVersion(t *testing.T) {
	f := repoProject(t)
	mustNewVersion(t, f, "1.1.0")
	if _, err := NewVersion(ctx, f.Env, "", "1.0.0"); err == nil || !strings.Contains(err.Error(), "already released") {
		t.Errorf("got %v", err)
	}
}

// release writes the record, commits the pack as it is and tags its version,
// as Build and Publish do.
func release(t *testing.T, f *fixture) {
	t.Helper()
	data, err := changelog.Load(changelog.Path(f.Project, "", ""))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := changelog.WriteRecord(f.Project, changelog.BuildRecord(f.Project, data, nil, "")); err != nil {
		t.Fatal(err)
	}
	root := f.Project.Root
	testutil.Git(t, root, "add", "-A")
	testutil.Git(t, root, "commit", "-q", "-m", f.Project.Version)
	testutil.Git(t, root, "tag", f.Project.Version)
	f.Git = git.New(root) // Forgets the tags it read before.
}

// betasThenFull releases 1.1.0-beta.1 (which adds Beta Mod) and 1.1.0-beta.2,
// then starts 1.1.0. A pre-release is always compared with the release just
// before it.
func betasThenFull(t *testing.T, f *fixture) {
	t.Helper()
	mustNewVersion(t, f, "1.1.0-beta.1")
	testutil.Write(t, filepath.Join(f.Project.PackDir(), "mods", "b.pw.toml"), testutil.Metafile("Beta Mod", "b-1.jar"))
	writeChangelog(t, f, "Update overview:\n  - Added 'Beta Mod' mod.\nBug Fixes:\n  - Fixed a crash.\n")
	release(t, f)
	mustNewVersion(t, f, "1.1.0-beta.2")
	if _, base, err := ChangesSinceRelease(ctx, f.Env, "", false); base != "1.1.0-beta.1" || err != nil {
		t.Error("beta 2 is compared with", base, err)
	}
	writeChangelog(t, f, "Changes/Improvements:\n  - New menu.\nBug Fixes:\n  - Fixed a crash.\n  - Fixed the menu.\n")
	release(t, f)
	f.answers("") // Accept the suggested 1.1.0.
	if changed, err := NewVersion(ctx, f.Env, "", ""); !changed || err != nil || f.Project.Version != "1.1.0" {
		t.Fatal(changed, err, f.Project.Version)
	}
}

// By default a pre-release is a release like any other, so the full release
// after it is compared with it and starts with an empty changelog.
func TestStandalonePrereleases(t *testing.T) {
	f := repoProject(t)
	betasThenFull(t, f)
	if data, err := changelog.Load(changelog.Path(f.Project, "", "")); err != nil || !data.IsEmpty() {
		t.Error("the changelog isn't empty", err)
	}
	if _, base, err := ChangesSinceRelease(ctx, f.Env, "", false); base != "1.1.0-beta.2" || err != nil {
		t.Error(base, err)
	}
}

// With prereleases: previews, the full release is compared with the previous
// full release and starts with what was written for its pre-releases, so it
// covers them.
func TestPreviewPrereleases(t *testing.T) {
	f := repoProject(t)
	f.Project.Settings.Prereleases = "previews"
	betasThenFull(t, f)
	data, err := changelog.Load(changelog.Path(f.Project, "", ""))
	if err != nil {
		t.Fatal(err)
	}
	for key, want := range map[string][]string{
		"Update overview":      nil, // Drafted from the comparison below.
		"Changes/Improvements": {"New menu."},
		"Bug Fixes":            {"Fixed a crash.", "Fixed the menu."},
	} {
		if got := data.Lines(key); !slices.Equal(got, want) {
			t.Errorf("%s: %q", key, got)
		}
	}
	for _, beta := range []string{"1.1.0-beta.1", "1.1.0-beta.2"} { // So their notes came from their records.
		if files.Exists(filepath.Join(f.Project.ChangelogDir(), beta+"+1.21.11.yml")) {
			t.Error(beta, "still has its changelog")
		}
	}
	changes, base, err := ChangesSinceRelease(ctx, f.Env, "", false)
	if err != nil || base != "1.0.0" || len(changes.Mods.Added) != 1 || changes.Mods.Added[0].Name != "Beta Mod" {
		t.Error(changes, base, err)
	}
}

// Renaming an unreleased pre-release to its full release keeps its changelog.
// With previews, it adds the notes of the pre-releases before it and asks for
// a new draft.
func TestRenamingAPrereleaseToItsFullRelease(t *testing.T) {
	for mode, want := range map[string][]string{"standalone": {"Fixed the menu."}, "previews": {"Fixed a crash.", "Fixed the menu."}} {
		f := repoProject(t)
		f.Project.Settings.Prereleases = mode
		mustNewVersion(t, f, "1.1.0-beta.1")
		writeChangelog(t, f, "Bug Fixes:\n  - Fixed a crash.\n")
		release(t, f)
		mustNewVersion(t, f, "1.1.0-beta.2")
		writeChangelog(t, f, "Update overview:\n  - Updated mods.\nBug Fixes:\n  - Fixed the menu.\n")
		f.answers("r")
		mustNewVersion(t, f, "1.1.0")
		data, err := changelog.Load(changelog.Path(f.Project, "", ""))
		if err != nil || !slices.Equal(data.Lines("Bug Fixes"), want) || !slices.Equal(data.Lines("Update overview"), []string{"Updated mods."}) {
			t.Error(mode, data.Lines("Bug Fixes"), data.Lines("Update overview"), err)
		}
		if strings.Contains(f.session.Text(), "Draft changelog (3) again") != (mode == "previews") {
			t.Error(mode, f.session.Text())
		}
	}
}

// py: test_workflows.py::test_changes_since_release_and_draft
func TestChangesSinceReleaseAndDraft(t *testing.T) {
	f := repoProject(t)
	mustNewVersion(t, f, "1.1.0")
	testutil.Write(t, filepath.Join(f.Project.PackDir(), "mods", "b.pw.toml"), testutil.Metafile("Beta Mod", "b-1.jar"))
	changes, base, err := ChangesSinceRelease(ctx, f.Env, "", true)
	if err != nil || base != "1.0.0" || len(changes.Mods.Added) != 1 || changes.Mods.Added[0].Name != "Beta Mod" {
		t.Fatal(changes, base, err)
	}
	if changed, err := Draft(ctx, f.Env, "", false); !changed || err != nil {
		t.Fatal(changed, err)
	}
	data, _ := changelog.Load(changelog.Path(f.Project, "", ""))
	if got := data.Lines("Update overview"); !slices.Equal(got, []string{"Added 'Beta Mod' mod."}) {
		t.Error(got)
	}
}

// Once packs are built, Build offers to open the Export folder. Enter opens
// it; no, or no answer at all (the end of a script's input), doesn't. With
// nothing built, there's no question.
func TestOfferExportFolder(t *testing.T) {
	f := repoProject(t)
	for _, c := range []struct {
		answers []string
		built   []string
		opens   bool
	}{
		{[]string{""}, []string{"Pack-1.0.mrpack"}, true},
		{[]string{"n"}, []string{"Pack-1.0.mrpack"}, false},
		{nil, []string{"Pack-1.0.mrpack"}, false},
		{nil, nil, false},
	} {
		f.opened = nil
		f.answers(c.answers...)
		offerExportFolder(ctx, f.Env, c.built)
		if opened := slices.Equal(f.opened, []string{f.Project.ExportDir()}); opened != c.opens || len(f.opened) > 1 {
			t.Errorf("%q with %v built: opened %v", c.answers, c.built, f.opened)
		}
		if asked := strings.Contains(f.session.Text(), "Open the Export folder?"); asked != (len(c.built) > 0) {
			t.Errorf("%v built: asked %v", c.built, asked)
		}
	}
}

// py: test_workflows.py::test_build_writes_record_notes_and_pack_files
func TestBuildWritesRecordNotesAndPackFiles(t *testing.T) {
	f := repoProject(t)
	mustNewVersion(t, f, "1.1.0")
	testutil.Write(t, filepath.Join(f.Project.PackDir(), "mods", "b.pw.toml"), testutil.Metafile("Beta Mod", "b-1.jar"))
	f.answers("y") // Draft the empty sections.
	if _, err := Build(ctx, f.Env, "", false, false); err != nil {
		t.Fatal(err, f.session.Text())
	}
	var record changelog.Record
	testutil.ReadJSON(t, filepath.Join(f.Project.DataDir(), "1.1.0+1.21.11.json"), &record)
	if !slices.Equal(record.Mods.Added, []string{"Beta Mod"}) || !slices.Equal(record.Overview, []string{"Added 'Beta Mod' mod."}) {
		t.Error(record.Mods, record.Overview)
	}
	if record.Contents == nil || len(record.Contents.Mods) != 2 || record.Contents.Mods[1].File != "b-1.jar" {
		t.Error("contents", record.Contents)
	}
	if notes := testutil.Read(t, filepath.Join(f.Project.Root, "CurseForge-Release.md")); !strings.HasPrefix(notes, "- Added 'Beta Mod' mod.") {
		t.Error(notes)
	}
	if !strings.Contains(testutil.Read(t, filepath.Join(f.Project.Root, "modlist.md")), "- Beta Mod") {
		t.Error("modlist.md")
	}
	var modlist []string
	testutil.ReadJSON(t, filepath.Join(f.Project.PackDir(), "config", "crash_assistant", "modlist.json"), &modlist)
	if !slices.Equal(modlist, []string{"a-1.jar", "b-1.jar"}) {
		t.Error(modlist)
	}
	last := ReadLastBuild(ctx, f.Env)
	if last == nil || last.Version != "1.1.0" || last.IndexHash != "h1" || len(last.Files) != 0 {
		t.Error(last)
	}
	if _, err := os.Stat(filepath.Join(f.Project.Root, ".git", LastBuildFile)); err != nil {
		t.Error("the last-build note isn't inside .git")
	}
	if s := ComputeStatus(ctx, f.Env); s.Next != "Publish (5)." || s.NextKey != "5" {
		t.Error(s.Next)
	}
}

// py: test_workflows.py::test_build_refuses_an_empty_changelog
func TestBuildRefusesAnEmptyChangelog(t *testing.T) {
	f := repoProject(t)
	mustNewVersion(t, f, "1.1.0")
	f.answers("n") // Don't draft.
	if _, err := Build(ctx, f.Env, "", false, false); err == nil || !strings.Contains(err.Error(), "is empty") {
		t.Errorf("got %v", err)
	}
}

func writeChangelog(t *testing.T, f *fixture, text string) string {
	t.Helper()
	return testutil.Write(t, changelog.Path(f.Project, "", ""), text)
}

// py: test_workflows.py::test_publish_guards
func TestPublishGuards(t *testing.T) {
	f := repoProject(t)
	if err := Publish(ctx, f.Env, false); err == nil || !strings.Contains(err.Error(), "already released") {
		t.Errorf("released: %v", err)
	}
	mustNewVersion(t, f, "1.1.0")
	if err := Publish(ctx, f.Env, false); err == nil || !strings.Contains(err.Error(), "no build") {
		t.Errorf("no build: %v", err)
	}
	writeChangelog(t, f, "Bug Fixes:\n  - Fixed it.\n")
	f.answers("n") // Don't draft the empty sections.
	if _, err := Build(ctx, f.Env, "", false, false); err != nil {
		t.Fatal(err)
	}
	f.LookPath = func(string) (string, error) { return "gh", nil }
	session := f.answers()
	if err := Publish(ctx, f.Env, true); err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{`git commit -m "Release 1.1.0"`, "gh release create 1.1.0", "--notes-file Modrinth-Release.md --target main"} {
		if !strings.Contains(session.Text(), want) {
			t.Errorf("dry run lacks %q:\n%s", want, session.Text())
		}
	}
	// Same version, but pretend the index changed:
	packTOML := filepath.Join(f.Project.PackDir(), "pack.toml")
	testutil.Write(t, packTOML, strings.Replace(testutil.Read(t, packTOML), `hash = "h1"`, `hash = "h2"`, 1))
	if err := Publish(ctx, f.Env, false); err == nil || !strings.Contains(err.Error(), "changed since the last build") {
		t.Errorf("changed: %v", err)
	}
}

// py: test_robustness.py::test_publish_refuses_after_changelog_edits
func TestPublishRefusesAfterChangelogEdits(t *testing.T) {
	f := repoProject(t)
	mustNewVersion(t, f, "1.1.0")
	path := writeChangelog(t, f, "Bug Fixes:\n  - Fixed it.\n")
	f.answers("n")
	if _, err := Build(ctx, f.Env, "", false, false); err != nil {
		t.Fatal(err)
	}
	if current, _ := BuildIsCurrent(f.Env, ReadLastBuild(ctx, f.Env)); !current {
		t.Fatal("the build should be current")
	}
	testutil.Write(t, path, "Bug Fixes:\n  - Fixed it.\n  - And another thing.\n")
	if current, reason := BuildIsCurrent(f.Env, ReadLastBuild(ctx, f.Env)); current || !strings.Contains(reason, "changelog changed") {
		t.Error(current, reason)
	}
	f.LookPath = func(string) (string, error) { return "gh", nil }
	if err := Publish(ctx, f.Env, true); err == nil || !strings.Contains(err.Error(), "changelog changed") {
		t.Errorf("got %v", err)
	}
}

// py: test_robustness.py::test_status_next_step_after_build
func TestStatusNextStepAfterBuild(t *testing.T) {
	f := repoProject(t)
	mustNewVersion(t, f, "1.1.0")
	writeChangelog(t, f, "Bug Fixes:\n  - Fixed it.\n")
	f.answers("n")
	if _, err := Build(ctx, f.Env, "", false, false); err != nil {
		t.Fatal(err)
	}
	if s := ComputeStatus(ctx, f.Env); s.Next != "Publish (5)." {
		t.Error(s.Next)
	}
	writeChangelog(t, f, "Bug Fixes:\n  - Changed.\n")
	if s := ComputeStatus(ctx, f.Env); !strings.HasPrefix(s.Next, "Build release (4) again. The changelog changed") || s.NextKey != "4" {
		t.Error(s.Next)
	}
}

// py: test_robustness.py::test_broken_changelog_yaml_is_a_clear_error
func TestStatusSurvivesABrokenChangelog(t *testing.T) {
	f := newFixture(t, filepath.Dir(testutil.PackDir(t)))
	writeChangelog(t, f, "Update overview:\n  - `oops\n")
	s := ComputeStatus(ctx, f.Env)
	if !strings.Contains(strings.Join(s.Lines(), "\n"), "isn't valid YAML") {
		t.Error(s.Lines())
	}
}

// py: test_robustness.py::test_old_style_publish_workflow_is_kept_in_step
func TestOldStylePublishWorkflowIsKeptInStep(t *testing.T) {
	f := repoProject(t)
	old := "env:\n  MODRINTH_TOKEN: ${{secrets.MODRINTH_TOKEN}}\n\n  MC_VERSION: 1.21.10\n" +
		"  RELEASE_TYPE: release\n  PRE_RELEASE: false\njobs:\n  x:\n    steps:\n" +
		"      - with:\n          game-versions: ${{env.MC_VERSION}}\n"
	path := testutil.Write(t, filepath.Join(f.Project.Root, ".github", "workflows", "publish.yml"), old, "\r\n")
	f.Project.Version = "1.1.0-beta.1"
	if got, err := SyncPublishWorkflow(f.Env); got != path || err != nil {
		t.Fatal(got, err)
	}
	text := testutil.Read(t, path)
	if !strings.Contains(text, "  MC_VERSION: 1.21.11\r\n  RELEASE_TYPE: beta\r\n  PRE_RELEASE: true\r\n") ||
		!strings.Contains(text, "game-versions: ${{env.MC_VERSION}}") {
		t.Error(text)
	}
	newStyle := "env:\n  TAG: ${{github.event.release.tag_name}}\njobs: {}\n"
	testutil.Write(t, path, newStyle)
	if got, _ := SyncPublishWorkflow(f.Env); got != "" || testutil.Read(t, path) != newStyle {
		t.Error("a new-style workflow was changed")
	}
}

// py: test_workflows.py::test_alpha_guard_redirects_and_reverts
func TestAlphaGuardRedirectsAndReverts(t *testing.T) {
	pw := testutil.PackDir(t)
	f := newFixture(t, filepath.Dir(pw))
	f.Project.Settings.AlphaUpdates = "never"
	mods, before, err := loadMods(f.Env)
	if err != nil {
		t.Fatal(err)
	}
	// "Update" two Modrinth mods to new versions.
	for slug, newVersion := range map[string]string{"lithium": "LithNEW", "sodium": "SodiNEW"} {
		path := filepath.Join(pw, "mods", slug+".pw.toml")
		old := `version = "` + strings.ToUpper(slug[:1]) + slug[1:4] + `v1"`
		testutil.Write(t, path, strings.Replace(testutil.Read(t, path), old, `version = "`+newVersion+`"`, 1))
	}
	types := map[string]string{"Lithv1": "release", "LithNEW": "alpha", "Sodiv1": "beta", "SodiNEW": "release"}
	f.api.Versions = map[string]platform.Version{}
	for id, kind := range types {
		f.api.Versions[id] = platform.Version{ID: id, VersionType: kind}
	}
	f.api.ProjectVersions = map[string][]platform.Version{"Lithium": {
		{ID: "LithALPHA2", VersionType: "alpha"},
		{ID: "LithBETA", VersionType: "beta", VersionNumber: "0.22-beta", Files: []platform.File{
			{Primary: true, URL: "https://x/l.jar", Filename: "lithium-0.22-beta.jar", Hashes: map[string]string{"sha512": "b"}}}},
	}}
	if err := afterUpdate(ctx, f.Env, mods, before, false); err != nil {
		t.Fatal(err)
	}
	after, _, _ := pack.LoadMods(pw, pack.Categories)
	for _, mod := range after {
		switch mod.Slug() {
		case "lithium":
			if mod.Modrinth()["version"] != "LithBETA" || mod.Filename() != "lithium-0.22-beta.jar" {
				t.Error("lithium", mod.Data)
			}
		case "sodium":
			if mod.Modrinth()["version"] != "SodiNEW" { // beta -> release is fine.
				t.Error("sodium", mod.Data)
			}
		}
	}
}

// py: test_workflows.py::test_incompatible_mods
func TestIncompatibleMods(t *testing.T) {
	f := newFixture(t, filepath.Dir(testutil.PackDir(t)))
	f.api.Versions = map[string]platform.Version{
		"Lithv1": {GameVersions: []string{"1.21.11"}}, "Sodiv1": {GameVersions: []string{"1.21.1", "1.21.10"}},
		"Pinnv1": {GameVersions: []string{"1.21.11"}},
	}
	incompatible, unknown, err := IncompatibleMods(ctx, f.Env)
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(slugs(incompatible), []string{"sodium"}) || !slices.Equal(slugs(unknown), []string{"server"}) {
		t.Error(slugs(incompatible), slugs(unknown)) // 1.21.1 isn't 1.21.11.
	}
	f.Project.AcceptableVersions = []string{"1.21.10"}
	if incompatible, _, _ := IncompatibleMods(ctx, f.Env); len(incompatible) != 0 {
		t.Error(slugs(incompatible))
	}
}

// migratingPackwiz changes the Minecraft version in pack.toml, as packwiz does.
type migratingPackwiz struct {
	fakePackwiz
	packDir string
}

func (m *migratingPackwiz) MigrateMinecraft(_ context.Context, v string) error {
	path := filepath.Join(m.packDir, "pack.toml")
	text, err := os.ReadFile(path)
	if err == nil {
		err = os.WriteFile(path, regexp.MustCompile(`minecraft = "[^"]*"`).ReplaceAll(text, []byte(`minecraft = "`+v+`"`)), 0o644)
	}
	return err
}

// Migrate suggests a beta when it had to disable mods that aren't updated yet.
func TestMigrateSuggestsABetaWhenModsAreMissing(t *testing.T) {
	for sodium, want := range map[string]string{"26.1": "1.3.0", "1.21.11": "1.3.0-beta.1"} {
		f := newFixture(t, filepath.Dir(testutil.PackDir(t)))
		f.Packwiz = &migratingPackwiz{packDir: f.Project.PackDir()}
		f.api.Versions = map[string]platform.Version{"Lithv1": {GameVersions: []string{"26.1"}},
			"Sodiv1": {GameVersions: []string{sodium}}, "Pinnv1": {GameVersions: []string{"26.1"}}}
		f.answers("", "", "", "") // The latest loader, no unpinning, disable Sodium if asked, the suggested version.
		if err := Migrate(ctx, f.Env, "26.1"); err != nil {
			t.Fatal(err, f.session.Text())
		}
		if f.Project.Minecraft != "26.1" || f.Project.Version != want {
			t.Errorf("Sodium for %s: Minecraft %s, version %s", sodium, f.Project.Minecraft, f.Project.Version)
		}
		if strings.Contains(f.session.Text(), "Suggesting a beta, since the pack is missing 1 mod until") != (sodium != "26.1") {
			t.Error(f.session.Text())
		}
	}
}

// A release's contents list every enabled file with its project page and
// authors, sorted by name, and sides when the pack shows side tags, plus the
// files the pack bundles by name.
func TestReleaseContents(t *testing.T) {
	pw := testutil.PackDir(t)
	testutil.Write(t, filepath.Join(pw, "mods", "cf.pw.toml"), testutil.Metafile("CF Mod", "cf-1.jar", testutil.MetaOptions{Source: "curseforge"}))
	f := newFixture(t, filepath.Dir(pw))
	f.Project.Settings.SideTags = true
	f.api.Projects = map[string]platform.Project{"Sodium": {ID: "Sodium", Slug: "sodium", ProjectType: "mod", Team: "T"}}
	f.api.Teams = map[string][]platform.TeamMember{"T": {{TeamID: "T", Role: "Developer", Ordering: 0}, {TeamID: "T", Role: "Owner", Ordering: 1}}}
	f.api.Teams["T"][1].User.Username = "jellysquid3"
	cf := platform.CFMod{ID: 222}
	cf.Links.WebsiteURL = "https://www.curseforge.com/minecraft/mc-mods/cf-mod"
	cf.Authors = append(cf.Authors, struct {
		Name string `json:"name"`
	}{"Someone"})
	f.api.Mods = map[int64]platform.CFMod{222: cf}
	mods, _, err := loadMods(f.Env)
	if err != nil {
		t.Fatal(err)
	}
	contents := ReleaseContents(ctx, f.Env, mods, []string{"shaderpacks/Complementary r5.2.1 [Insomnia Edit]",
		"mods/[Let's Do] Extra-1.0.jar"})
	want := []changelog.Item{
		{Name: "CF Mod", File: "cf-1.jar", URL: "https://www.curseforge.com/minecraft/mc-mods/cf-mod", Authors: []string{"Someone"}},
		{Name: "[Let's Do] Extra-1.0", File: "[Let's Do] Extra-1.0.jar"}, // Sorted under L, bundled.
		{Name: "Lithium", File: "lithium-0.21.jar", URL: "https://modrinth.com/project/Lithium"},
		{Name: "Pinned Mod", File: "pinned-1.0.jar", URL: "https://modrinth.com/project/Pinned M"}, // Without "[Fabric]".
		{Name: "Server Thing", File: "server-1.0.jar", Side: "server"},
		{Name: "Sodium", File: "sodium-0.8.jar", Side: "client", URL: "https://modrinth.com/mod/sodium", Authors: []string{"jellysquid3"}},
	}
	if !reflect.DeepEqual(contents.Mods, want) { // The disabled Boss Checklist is left out.
		t.Errorf("mods\n got %+v\nwant %+v", contents.Mods, want)
	}
	shaders := []changelog.Item{{Name: "Complementary r5.2.1", File: "Complementary r5.2.1 [Insomnia Edit]"}}
	if len(contents.ResourcePacks) != 1 || contents.ResourcePacks[0].Name != "Fresh Animations" || !reflect.DeepEqual(contents.ShaderPacks, shaders) {
		t.Error(contents.ResourcePacks, contents.ShaderPacks)
	}
}

// failingAPI can't reach the platforms.
type failingAPI struct{ platform.Fake }

func (*failingAPI) CurseForgeMods(context.Context, []int64) (map[int64]platform.CFMod, error) {
	return nil, errors.New("offline")
}

// Without the platforms, the contents link projects by id and leave the authors out.
func TestReleaseContentsWithoutThePlatforms(t *testing.T) {
	f := newFixture(t, filepath.Dir(testutil.PackDir(t)))
	f.API = &failingAPI{}
	mods, _, _ := loadMods(f.Env)
	contents := ReleaseContents(ctx, f.Env, mods, nil)
	if len(contents.Mods) != 4 || contents.Mods[0].URL != "https://modrinth.com/project/Lithium" || !strings.Contains(f.session.Text(), "offline") {
		t.Error(contents.Mods, f.session.Text())
	}
}

func slugs(mods []pack.Mod) []string {
	var result []string
	for _, mod := range mods {
		result = append(result, mod.Slug())
	}
	return result
}

func TestUpdateModsRepinsAfterACancel(t *testing.T) {
	f := newFixture(t, filepath.Dir(testutil.PackDir(t)))
	f.answers("1") // Unpin the pinned mod for this run.
	canceled, cancel := context.WithCancel(ctx)
	f.Packwiz = &cancelingPackwiz{fakePackwiz: f.packwiz, cancel: cancel}
	err := UpdateMods(canceled, f.Env)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("got %v", err)
	}
	if !slices.Contains(f.packwiz.calls, "unpin pinned") || !slices.Contains(f.packwiz.calls, "pin pinned") {
		t.Error(f.packwiz.calls)
	}
}

// cancelingPackwiz cancels the run while packwiz update is running.
type cancelingPackwiz struct {
	*fakePackwiz
	cancel func()
}

func (c *cancelingPackwiz) UpdateAll(context.Context) error {
	c.cancel()
	return context.Canceled
}

// An action that looks at the mods several times warns about a broken metafile once.
func TestUnreadableMetafilesAreWarnedAboutOnce(t *testing.T) {
	pw := testutil.PackDir(t)
	testutil.Write(t, filepath.Join(pw, "mods", "broken.pw.toml"), "name = \n")
	f := newFixture(t, filepath.Dir(pw))
	for range 3 {
		if _, _, err := loadMods(f.Env); err != nil {
			t.Fatal(err)
		}
	}
	if n := strings.Count(f.session.Text(), "broken.pw.toml"); n != 1 {
		t.Errorf("warned %d times:\n%s", n, f.session.Text())
	}
}

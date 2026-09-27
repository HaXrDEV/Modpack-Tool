package changelog

import (
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"

	"github.com/HaXrDEV/Modpack-Tool/internal/diff"
	"github.com/HaXrDEV/Modpack-Tool/internal/pack"
	"github.com/HaXrDEV/Modpack-Tool/internal/project"
	"github.com/HaXrDEV/Modpack-Tool/internal/pycompat"
	"github.com/HaXrDEV/Modpack-Tool/internal/testutil"
)

type golden struct {
	Sections []struct {
		Name    string              `json:"name"`
		Text    string              `json:"text"`
		Lines   map[string][]string `json:"lines"`
		Unknown []string            `json:"unknown"`
		Empty   bool                `json:"empty"`
	} `json:"sections"`
	Records []struct {
		Changelog string `json:"changelog"`
		SideTags  bool   `json:"side_tags"`
		NoDiff    bool   `json:"no_diff"`
		JSON      string `json:"json"`
	} `json:"records"`
	Notes []struct {
		Version, URL, Changelog, Platform, Notes string
		CFFooter                                 string `json:"cf_footer"`
		MRFooter                                 string `json:"mr_footer"`
	} `json:"notes"`
	Drafts []struct {
		Name  string              `json:"name"`
		Text  *string             `json:"text"`
		Saved string              `json:"saved"`
		Lines map[string][]string `json:"lines"`
	} `json:"drafts"`
	Overview    [][]string `json:"overview"`
	ConfigLines [][]string `json:"config_lines"`
}

func loadGolden(t *testing.T) golden {
	t.Helper()
	var g golden
	testutil.ReadJSON(t, "testdata/golden.json", &g)
	return g
}

func loadText(t *testing.T, text string) *Changelog {
	t.Helper()
	path := testutil.Write(t, filepath.Join(t.TempDir(), "c.yml"), text)
	c, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func testProject(t *testing.T) *project.Project {
	t.Helper()
	root := filepath.Join(t.TempDir(), "MyPack")
	testutil.Write(t, filepath.Join(root, "Packwiz", "pack.toml"),
		"name = \"MyPack\"\nversion = \"1.2.0\"\n[versions]\nfabric = \"0.18.4\"\nminecraft = \"1.21.11\"\n")
	testutil.Write(t, filepath.Join(root, "modpack-tool.yml"), "exports: []\n")
	p, _, err := project.Open(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	return p
}

func withoutNone(lines []string) []string {
	return slices.DeleteFunc(slices.Clone(lines), func(line string) bool { return line == "None" })
}

// The goldens come from the Python tool (scripts/golden.py at the python-final tag).
func TestSectionsMatchPython(t *testing.T) {
	for _, c := range loadGolden(t).Sections {
		cl := loadText(t, c.Text)
		for key, want := range c.Lines {
			// Deliberately unlike the Python tool: an empty "- " bullet is left
			// out instead of becoming "None".
			if got := cl.Lines(key); !slices.Equal(got, withoutNone(want)) && !(len(got) == 0 && len(want) == 0) {
				t.Errorf("%s: %s = %q, want %q", c.Name, key, got, want)
			}
		}
		if got := cl.UnknownSections(); !slices.Equal(got, c.Unknown) && !(len(got) == 0 && len(c.Unknown) == 0) {
			t.Errorf("%s: unknown %q", c.Name, got)
		}
		if cl.IsEmpty() != c.Empty {
			t.Errorf("%s: IsEmpty = %v", c.Name, cl.IsEmpty())
		}
	}
}

func TestRecordsMatchPython(t *testing.T) {
	windowsNewlines(t)
	p := testProject(t)
	d := diff.Compare(pack.Tree(testutil.OldTree()), pack.Tree(testutil.NewTree()), "1.1.0", "1.2.0", "1.21.11", true)
	for _, c := range loadGolden(t).Records {
		p.Settings.SideTags = c.SideTags
		cl := loadText(t, c.Changelog)
		var record Record
		if c.NoDiff {
			record = BuildRecord(p, cl, nil, "2026-09-25")
		} else {
			record = BuildRecord(p, cl, d, "2026-09-25")
		}
		os.Remove(RecordPath(p, "", ""))
		path, err := WriteRecord(p, record)
		if err != nil {
			t.Fatal(err)
		}
		if got := testutil.Read(t, path); got != c.JSON {
			t.Errorf("record for %q (side tags %v):\n got %s\nwant %s", c.Changelog, c.SideTags, got, c.JSON)
		}
	}
}

// Pre-releases now say what they are and what that means before "Here be
// dragons!", where Python only said "This is a pre-release.".
func TestReleaseNotesMatchPython(t *testing.T) {
	p := testProject(t)
	for _, c := range loadGolden(t).Notes {
		p.Version = c.Version
		p.Settings.ChangelogURL, p.Settings.CurseForgeNotesFooter, p.Settings.ModrinthNotesFooter = c.URL, c.CFFooter, c.MRFooter
		var warnings []string
		got := ReleaseNotes(p, loadText(t, c.Changelog), c.Platform, func(w string) { warnings = append(warnings, w) })
		want := strings.Replace(c.Notes, "This is a pre-release. Here be dragons!", PrereleaseNotice(c.Version), 1)
		if got != want {
			t.Errorf("%s %s %q:\n got %q\nwant %q", c.Version, c.Platform, c.URL, got, want)
		}
		if strings.Contains(c.URL, "unknown") && len(warnings) == 0 {
			t.Error("no warning for a broken link template")
		}
	}
}

// Drafting only replaces the drafted sections; Python rewrote the whole file
// (re-wrapping long bullets), so the texts are compared parsed, except for the
// untouched template, which must come out the same.
func TestDraftedSectionsMatchPython(t *testing.T) {
	g := loadGolden(t)
	p := testProject(t)
	for _, c := range g.Drafts {
		var path string
		if c.Text == nil {
			os.RemoveAll(p.ChangelogDir())
			var err error
			if path, err = Create(p, ""); err != nil {
				t.Fatal(err)
			}
		} else {
			path = testutil.Write(t, filepath.Join(t.TempDir(), "d.yml"), *c.Text)
		}
		cl, err := Load(path)
		if err != nil {
			t.Fatal(err)
		}
		if err := cl.SetSection("Update overview", g.Overview[0]); err != nil {
			t.Fatal(err)
		}
		if err := cl.SetSection("Config Changes", g.ConfigLines[0]); err != nil {
			t.Fatal(err)
		}
		if err := cl.Save(); err != nil {
			t.Fatal(err)
		}
		saved, err := Load(path)
		if err != nil {
			t.Fatalf("%s: %v\n%s", c.Name, err, testutil.Read(t, path))
		}
		for key, want := range c.Lines {
			if got := saved.Lines(key); !slices.Equal(got, want) && !(len(got) == 0 && len(want) == 0) {
				t.Errorf("%s: %s = %q, want %q", c.Name, key, got, want)
			}
		}
		text := pycompat.UniversalNewlines(testutil.Read(t, path))
		if (c.Text == nil || c.Name == "missing keys") && text != c.Saved {
			t.Errorf("%s:\n got %q\nwant %q", c.Name, text, c.Saved)
		}
		if c.Name == "comments" && (!strings.Contains(text, "# Section comment\n") || !strings.Contains(text, "past eighty columns for sure.\n")) {
			t.Errorf("comments or hand-written text lost:\n%s", text)
		}
	}
}

func windowsNewlines(t *testing.T) {
	saved := pycompat.NewFileNewline
	pycompat.NewFileNewline = "\r\n"
	t.Cleanup(func() { pycompat.NewFileNewline = saved })
}

// py: test_changelog.py::test_filenames
func TestFilenames(t *testing.T) {
	if Stem("4.11.1", "1.21.11")+".yml" != "4.11.1+1.21.11.yml" || Stem("26.1.1-1.2", "26.1.1")+".json" != "26.1.1-1.2.json" {
		t.Error("Stem")
	}
}

// py: test_changelog.py::test_template_and_sections
func TestTemplateAndSections(t *testing.T) {
	p := testProject(t)
	path, err := Create(p, "")
	if err != nil || filepath.Base(path) != "1.2.0+1.21.11.yml" {
		t.Fatal(path, err)
	}
	cl, _ := Load(path)
	if !cl.IsEmpty() {
		t.Error("template should be empty")
	}
	cl.SetSection("Update overview", []string{"Added 'Sodium' mod."})
	testutil.Write(t, path, strings.ReplaceAll(testutil.Read(t, path), "Config Changes:", "Config Changes: |-\n  - : [mod], [Client]\n  - Changed x: [Mod]")+"DISCLAIMER: Something important\n")
	cl, err = Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(testutil.Read(t, path), "# Changelog for MyPack 1.2.0") {
		t.Error("header")
	}
	if !slices.Equal(cl.Lines("Config Changes"), []string{"Changed x: [Mod]"}) || !slices.Equal(cl.UnknownSections(), []string{"DISCLAIMER"}) || cl.IsEmpty() {
		t.Error(cl.Lines("Config Changes"), cl.UnknownSections())
	}
}

// The records are read oldest first, and their sections by changelog key.
func TestRecords(t *testing.T) {
	p := testProject(t)
	for name, text := range map[string]string{
		"1.2.0-beta.2+1.21.11.json": `{"version": "1.2.0-beta.2", "bugfixes": ["Fixed the menu."]}`,
		"1.2.0-beta.1+1.21.11.json": `{"version": "1.2.0-beta.1", "changes": ["New menu."]}`,
		"notes.txt":                 "not a record",
	} {
		testutil.Write(t, filepath.Join(p.DataDir(), name), text)
	}
	records, err := Records(p)
	if err != nil || len(records) != 2 || records[0].Version != "1.2.0-beta.1" ||
		!slices.Equal(records[0].Lines("Changes/Improvements"), []string{"New menu."}) ||
		!slices.Equal(records[1].Lines("Bug Fixes"), []string{"Fixed the menu."}) {
		t.Error(records, err)
	}
}

// A full release takes over what was written for its pre-releases, oldest
// first and once, but not the drafted sections.
func TestIncludeAddsThePrereleasesNotes(t *testing.T) {
	c := loadText(t, "Update overview:\n  - Drafted.\nBug Fixes:\n  - Own fix.\n")
	earlier := []Notes{
		loadText(t, "Update overview:\n  - Beta overview.\nChanges/Improvements:\n  - New menu.\nBug Fixes:\n  - Fixed a crash.\n"),
		loadText(t, "Bug Fixes:\n  - fixed a crash.\n  - Own fix.\n  - 'Sodium: fixed flicker'\nConfig Changes: |-\n  - Changed x: [Mod]\n"),
	}
	if changed, err := c.Include(earlier); !changed || err != nil {
		t.Fatal(changed, err)
	}
	for key, want := range map[string][]string{
		"Update overview":      {"Drafted."},
		"Changes/Improvements": {"New menu."},
		"Bug Fixes":            {"Fixed a crash.", "Sodium: fixed flicker", "Own fix."},
		"Config Changes":       nil,
	} {
		if got := c.Lines(key); !slices.Equal(got, want) {
			t.Errorf("%s: %q", key, got)
		}
	}
	if changed, err := c.Include(earlier); changed || err != nil {
		t.Error("included twice", err)
	}
}

// py: test_changelog.py::test_record_matches_the_wiki_contract
func TestRecordMatchesTheWikiContract(t *testing.T) {
	p := testProject(t)
	cl := loadText(t, "Update overview:\n  - Did things.\nBug Fixes: \"- Fixed a crash\"\nMod loader: Fabric\nScript/Datapack changes:\n  - Cheaper recipe\n")
	d := diff.Compare(pack.Tree(testutil.OldTree()), pack.Tree(testutil.NewTree()), "1.1.0", "1.2.0", "1.21.11", true)
	record := BuildRecord(p, cl, d, "2026-09-25")
	encoded, _ := pycompat.Dumps(record, 2, false)
	value, _ := pycompat.Loads(encoded)
	var keys []string
	for _, m := range value.(pycompat.Object) {
		keys = append(keys, m.Key)
	}
	want := []string{"pack", "version", "minecraft", "loader", "released", "prerelease", "comparedTo",
		"overview", "changes", "bugfixes", "scriptChanges", "configChanges", "mods", "resourcepacks", "shaderpacks"}
	if !slices.Equal(keys, want) {
		t.Error(keys)
	}
	if record.Loader != (Loader{"Fabric", "0.18.4"}) || *record.ComparedTo != (ComparedTo{"1.1.0", "1.21.10"}) {
		t.Error(record.Loader, record.ComparedTo)
	}
	if !slices.Equal(record.BugFixes, []string{"Fixed a crash"}) || record.Prerelease {
		t.Error(record.BugFixes)
	}
	if !reflect.DeepEqual(record.Mods.Updated, []RecordUpdate{{"Sodium", "sodium-1.jar", "sodium-2.jar"}}) {
		t.Error(record.Mods.Updated)
	}
	path, err := WriteRecord(p, record)
	if err != nil || filepath.Base(path) != "1.2.0+1.21.11.json" || filepath.Base(filepath.Dir(path)) != "data" {
		t.Error(path, err)
	}
}

// A release's contents come last in its record, without a file's empty fields.
func TestRecordContents(t *testing.T) {
	record := BuildRecord(testProject(t), loadText(t, "Bug Fixes:\n  - Fixed it.\n"), nil, "2026-09-26")
	record.Contents = &Contents{Mods: []Item{{Name: "Sodium", File: "sodium.jar"}}, ResourcePacks: []Item{}, ShaderPacks: []Item{}}
	encoded, _ := pycompat.Dumps(record, 2, false)
	want := "\n  \"contents\": {\n    \"mods\": [\n      {\n        \"name\": \"Sodium\",\n        \"file\": \"sodium.jar\"\n      }\n    ],\n" +
		"    \"resourcepacks\": [],\n    \"shaderpacks\": []\n  }\n}"
	if !strings.HasSuffix(strings.TrimSpace(string(encoded)), want) {
		t.Errorf("%s", encoded)
	}
}

// py: test_changelog.py::test_release_notes
func TestReleaseNotes(t *testing.T) {
	p := testProject(t)
	p.Settings.ChangelogURL = "https://crismpack.net/mypack/changelogs/{mc_group}#{anchor}"
	p.Settings.CurseForgeNotesFooter = "<br>\n\n[banner](https://example.com)\n"
	cl := loadText(t, "Update overview:\n  - Added 'Sodium' mod.\n  - Updated mods.\n")
	if got := ReleaseNotes(p, cl, "curseforge", nil); got != "- Added 'Sodium' mod.\n- Updated mods.\n\n"+
		"#### **[[Full Changelog]](https://crismpack.net/mypack/changelogs/1.21#v1.2.0)**\n\n<br>\n\n[banner](https://example.com)\n" {
		t.Errorf("%q", got)
	}
	if got := ReleaseNotes(p, cl, "modrinth", nil); got != "- Added 'Sodium' mod.\n- Updated mods.\n\n"+
		"**[[Full Changelog]](https://crismpack.net/mypack/changelogs/1.21#v1.2.0)**\n" {
		t.Errorf("%q", got)
	}
}

// py: test_changelog.py::test_release_notes_for_prerelease_without_overview
func TestReleaseNotesForPrereleaseWithoutOverview(t *testing.T) {
	p := testProject(t)
	p.Version = "1.3.0-beta.1"
	cl := loadText(t, "Changes/Improvements:\n  - New menu\nBug Fixes:\n  - Fixed crash\n")
	want := "**This is a beta, so it may be less stable or feature complete than a full release. Here be dragons!**\n\n" +
		"### Changes/Improvements ⭐\n\n- New menu\n\n### Bug Fixes 🪲\n\n- Fixed crash\n"
	if got := ReleaseNotes(p, cl, "modrinth", nil); got != want {
		t.Errorf("%q", got)
	}
}

// py: test_robustness.py::test_broken_changelog_yaml_is_a_clear_error
func TestBrokenChangelogYAMLIsAClearError(t *testing.T) {
	path := testutil.Write(t, filepath.Join(t.TempDir(), "bad.yml"), "Update overview:\n  - `backtick start\n\tTab: x\n")
	if _, err := Load(path); err == nil || !strings.Contains(err.Error(), "isn't valid YAML") {
		t.Errorf("got %v", err)
	}
}

// A section written twice or a second document is an error, as it was for
// the Python tool, instead of text that silently goes missing (or is drafted
// over without asking).
func TestRepeatedSectionsAreAClearError(t *testing.T) {
	for text, want := range map[string]string{
		"Update overview:\n  - My own summary.\nBug Fixes:\nUpdate overview:\n": `"Update overview" already defined at line 1`,
		"Update overview:\n  - One.\n---\nBug Fixes:\n  - Two.\n":               "expected a single document",
	} {
		path := testutil.Write(t, filepath.Join(t.TempDir(), "c.yml"), text)
		if _, err := Load(path); err == nil || !strings.Contains(err.Error(), "isn't valid YAML") || !strings.Contains(err.Error(), want) {
			t.Errorf("%q: got %v", text, err)
		}
	}
}

// py: test_robustness.py::test_mapping_bullets_keep_their_text
func TestMappingBulletsKeepTheirText(t *testing.T) {
	if got := loadText(t, "B:\n  - 'Sodium: fixed flicker'\n  - Lithium: faster\n").Lines("B"); !slices.Equal(got, []string{"Sodium: fixed flicker", "Lithium: faster"}) {
		t.Error(got)
	}
	if got := loadText(t, "B:\n  Sodium: fixed flicker\n").Lines("B"); !slices.Equal(got, []string{"Sodium: fixed flicker"}) {
		t.Error(got)
	}
	if got := loadText(t, "B: 42\n").Lines("B"); !slices.Equal(got, []string{"42"}) {
		t.Error(got)
	}
}

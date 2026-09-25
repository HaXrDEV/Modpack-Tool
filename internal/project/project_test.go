package project

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"

	"github.com/HaXrDEV/Modpack-Tool/internal/pycompat"
	"github.com/HaXrDEV/Modpack-Tool/internal/testutil"
)

type goldenSettings struct {
	Exports               []string `json:"exports"`
	ServerTemplate        string   `json:"server_template"`
	ServerExclude         []string `json:"server_exclude"`
	MCPrefixedVersions    bool     `json:"mc_prefixed_versions"`
	AlphaUpdates          string   `json:"alpha_updates"`
	SideTags              bool     `json:"side_tags"`
	ChangelogURL          string   `json:"changelog_url"`
	CurseForgeNotesFooter string   `json:"curseforge_notes_footer"`
	ModrinthNotesFooter   string   `json:"modrinth_notes_footer"`
}

func (g goldenSettings) settings() Settings {
	return Settings{Exports: g.Exports, ServerTemplate: g.ServerTemplate, ServerExclude: g.ServerExclude,
		MCPrefixedVersions: g.MCPrefixedVersions, AlphaUpdates: g.AlphaUpdates, SideTags: g.SideTags,
		ChangelogURL: g.ChangelogURL, CurseForgeNotesFooter: g.CurseForgeNotesFooter, ModrinthNotesFooter: g.ModrinthNotesFooter}
}

// Python wrote new files with CRLF on Windows, where the goldens were made.
func windowsNewlines(t *testing.T) {
	saved := pycompat.NewFileNewline
	pycompat.NewFileNewline = "\r\n"
	t.Cleanup(func() { pycompat.NewFileNewline = saved })
}

// The goldens are what the Python tool wrote for each input (scripts/golden.py).
func TestSettingsMatchPython(t *testing.T) {
	windowsNewlines(t)
	data, err := os.ReadFile("testdata/settings.json")
	if err != nil {
		t.Fatal(err)
	}
	var golden struct {
		Cases []struct {
			Name     string          `json:"name"`
			Input    *string         `json:"input"`
			Legacy   *string         `json:"legacy"`
			Answer   string          `json:"answer"`
			Output   *string         `json:"output"`
			Settings *goldenSettings `json:"settings"`
			Notes    []string        `json:"notes"`
			Error    string          `json:"error"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(data, &golden); err != nil {
		t.Fatal(err)
	}
	for _, c := range golden.Cases {
		root := filepath.Join(t.TempDir(), "Pack")
		os.MkdirAll(filepath.Join(root, "Packwiz", "mods"), 0o755)
		packName := "Breakneck"
		var ask AskFunc
		if c.Legacy != nil {
			packName = "InsomniaHardcore"
			testutil.Write(t, filepath.Join(root, "Packwiz", "mods", "from-the-fog.pw.toml"),
				"name = \"From The Fog\"\nfilename = \"From-The-Fog-1.21-v2.0.jar\"\nside = \"both\"\n")
			testutil.Write(t, filepath.Join(root, LegacySettingsFile), *c.Legacy)
			answer := c.Answer
			ask = func(string, string) (string, error) { return answer, nil }
		} else if c.Input != nil {
			testutil.Write(t, filepath.Join(root, SettingsFile), *c.Input)
		}
		label := c.Name
		if c.Input != nil && strings.Contains(*c.Input, "\r\n") {
			label += " (crlf)"
		}
		settings, notes, err := LoadSettings(root, packName, ask)
		if c.Error != "" {
			if err == nil {
				t.Errorf("%s: expected an error like %q", label, c.Error)
			}
			continue
		}
		if err != nil {
			t.Errorf("%s: %v", label, err)
			continue
		}
		if got := testutil.Read(t, filepath.Join(root, SettingsFile)); c.Output != nil && got != *c.Output {
			t.Errorf("%s: file\n got %q\nwant %q", label, got, *c.Output)
		}
		if want := c.Settings.settings(); !reflect.DeepEqual(settings, want) {
			t.Errorf("%s: settings\n got %+v\nwant %+v", label, settings, want)
		}
		if c.Legacy == nil && !slices.Equal(notes, c.Notes) && !(len(notes) == 0 && len(c.Notes) == 0) {
			t.Errorf("%s: notes\n got %q\nwant %q", label, notes, c.Notes)
		}
	}
}

func legacyProject(t *testing.T, fixture string) string {
	t.Helper()
	root := filepath.Join(t.TempDir(), "Pack")
	testutil.Write(t, filepath.Join(root, "Packwiz", "pack.toml"), "name = \"InsomniaHardcore\"\nversion = \"2.2.0\"\n"+
		"[versions]\nfabric = \"0.18.4\"\nminecraft = \"1.21.1\"\n")
	testutil.Write(t, filepath.Join(root, "Packwiz", "mods", "from-the-fog.pw.toml"),
		testutil.Metafile("From The Fog", "From-The-Fog-1.21-v2.0.jar"))
	legacy, err := os.ReadFile(filepath.Join("..", "..", "tests", "fixtures", "legacy_settings", fixture))
	if err != nil {
		legacy, err = os.ReadFile(filepath.Join("testdata", "legacy_"+fixture))
	}
	if err != nil {
		t.Fatal(err)
	}
	testutil.Write(t, filepath.Join(root, LegacySettingsFile), string(legacy))
	return root
}

func answer(text string) AskFunc {
	return func(string, string) (string, error) { return text, nil }
}

func anyContains(notes []string, text string) bool {
	return slices.ContainsFunc(notes, func(note string) bool { return strings.Contains(note, text) })
}

// py: test_project.py::test_import_breakneck_settings
func TestImportBreakneckSettings(t *testing.T) {
	root := legacyProject(t, "breakneck.yml")
	settings, notes, err := LoadSettings(root, "Breakneck", answer(""))
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(settings.Exports, []string{"curseforge", "modrinth"}) || settings.SideTags || settings.AlphaUpdates != "prompt" {
		t.Errorf("settings %+v", settings)
	}
	if settings.ChangelogURL != "https://crismpack.net/breakneck/changelogs/{mc_group}#{anchor}" {
		t.Error(settings.ChangelogURL)
	}
	if !strings.Contains(settings.CurseForgeNotesFooter, "bh.png") || !anyContains(notes, "Imported settings") {
		t.Error("footer or notes", notes)
	}
	if _, err := os.Stat(filepath.Join(root, SettingsFile)); err != nil {
		t.Error("settings file not written")
	}
}

// py: test_project.py::test_import_insomnia_settings_and_fix_old_exclusion
func TestImportInsomniaSettings(t *testing.T) {
	root := legacyProject(t, "insomnia.yml")
	settings, notes, err := LoadSettings(root, "InsomniaHardcore", answer("insomnia"))
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(settings.Exports, []string{"curseforge", "server"}) || !settings.SideTags ||
		!slices.Equal(settings.ServerExclude, []string{"from-the-fog"}) {
		t.Errorf("settings %+v", settings)
	}
	if settings.ChangelogURL != "https://crismpack.net/insomnia/changelogs/{mc_group}#{anchor}" || !anyContains(notes, "old filename") {
		t.Error(settings.ChangelogURL, notes)
	}
}

// py: test_project.py::test_settings_file_is_stable_and_keeps_edits
func TestSettingsFileIsStableAndKeepsEdits(t *testing.T) {
	root := legacyProject(t, "insomnia.yml")
	LoadSettings(root, "InsomniaHardcore", answer("insomnia"))
	path := filepath.Join(root, SettingsFile)
	first := testutil.Read(t, path)
	LoadSettings(root, "InsomniaHardcore", nil)
	if testutil.Read(t, path) != first {
		t.Error("loading twice changed the file")
	}
	text := strings.Replace(first, `alpha_updates: "prompt"`, `alpha_updates: "never"`, 1)
	testutil.Write(t, path, text+"\nmystery_key: 1\n")
	settings, notes, err := LoadSettings(root, "InsomniaHardcore", nil)
	if err != nil || settings.AlphaUpdates != "never" || !anyContains(notes, "mystery_key") {
		t.Error(err, settings, notes)
	}
	after := testutil.Read(t, path)
	if strings.Contains(after, "mystery_key") || !strings.Contains(after, "# When an update lands on an alpha version") {
		t.Error("layout not restored")
	}
}

// py: test_project.py::test_new_project_gets_template
func TestNewProjectGetsTemplate(t *testing.T) {
	root := filepath.Join(t.TempDir(), "Fresh")
	testutil.Write(t, filepath.Join(root, "Packwiz", "pack.toml"), "name = \"Fresh Pack\"\nversion = \"1.0.0\"\n[versions]\nminecraft = \"26.1\"\n")
	settings, notes, err := LoadSettings(root, "Fresh Pack", nil)
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(settings.Exports, []string{"curseforge", "modrinth"}) ||
		settings.ChangelogURL != "https://crismpack.net/fresh/changelogs/{mc_group}#{anchor}" || !anyContains(notes, "Created") {
		t.Error(settings, notes)
	}
}

// py: test_project.py::test_bad_values_fall_back
func TestBadValuesFallBack(t *testing.T) {
	root := filepath.Join(t.TempDir(), "Odd")
	testutil.Write(t, filepath.Join(root, SettingsFile), "exports: [curseforge, itch]\nalpha_updates: \"sometimes\"\n")
	settings, notes, err := LoadSettings(root, "Odd", nil)
	if err != nil || !slices.Equal(settings.Exports, []string{"curseforge"}) || settings.AlphaUpdates != "prompt" || len(notes) != 2 {
		t.Error(err, settings, notes)
	}
}

// py: test_project.py::test_open_project_reads_pack_info
func TestOpenProjectReadsPackInfo(t *testing.T) {
	pw := testutil.PackDir(t)
	p, _, err := Open(filepath.Dir(pw), nil)
	if err != nil {
		t.Fatal(err)
	}
	if p.Name != "MyPack" || p.Version != "1.2.0" || p.Minecraft != "1.21.11" {
		t.Error(p)
	}
	if p.Loader != "fabric" || p.LoaderVersion != "0.18.4" || p.LoaderLabel() != "Fabric" || p.MCPrefixed() {
		t.Error(p)
	}
	if FindRoot(filepath.Join(pw, "mods")) != p.Root {
		t.Error("FindRoot from inside the pack")
	}
}

// py: test_robustness.py::test_settings_values_are_coerced
func TestSettingsValuesAreCoerced(t *testing.T) {
	root := filepath.Join(t.TempDir(), "Pack")
	testutil.Write(t, filepath.Join(root, SettingsFile), "exports:\nside_tags: no\nmc_prefixed_versions: \"yes\"\n"+
		"curseforge_notes_footer:\nserver_exclude: from-the-fog\nchangelog_url: [a, b]\n")
	settings, notes, err := LoadSettings(root, "Pack", nil)
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(settings.Exports, []string{"curseforge", "modrinth"}) || settings.SideTags || !settings.MCPrefixedVersions {
		t.Error(settings)
	}
	if settings.CurseForgeNotesFooter != "" || !slices.Equal(settings.ServerExclude, []string{"from-the-fog"}) {
		t.Error(settings)
	}
	if settings.ChangelogURL != "" || !anyContains(notes, "single value") {
		t.Error(settings.ChangelogURL, notes)
	}
	testutil.Write(t, filepath.Join(root, SettingsFile), "exports: curseforge\n")
	if settings, _, _ := LoadSettings(root, "Pack", nil); !slices.Equal(settings.Exports, []string{"curseforge"}) {
		t.Error(settings.Exports)
	}
}

// py: test_robustness.py::test_broken_settings_yaml_is_a_clear_error
func TestBrokenSettingsYAMLIsAClearError(t *testing.T) {
	root := filepath.Join(t.TempDir(), "Pack")
	testutil.Write(t, filepath.Join(root, SettingsFile), "server_template: \"D:\\Servers\\X\"\n")
	if _, _, err := LoadSettings(root, "Pack", nil); err == nil || !strings.Contains(err.Error(), "single quotes") {
		t.Errorf("got %v", err)
	}
}

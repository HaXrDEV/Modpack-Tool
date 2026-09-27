package project

import (
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

// settings adds the settings the Python tool didn't have, at their defaults.
func (g goldenSettings) settings() Settings {
	return Settings{Exports: g.Exports, CurseForgeExclude: []string{}, ModrinthExclude: []string{},
		ServerTemplate: g.ServerTemplate, ServerExclude: g.ServerExclude,
		MCPrefixedVersions: g.MCPrefixedVersions, Prereleases: "standalone", AlphaUpdates: g.AlphaUpdates, SideTags: g.SideTags,
		ChangelogURL: g.ChangelogURL, CurseForgeNotesFooter: g.CurseForgeNotesFooter, ModrinthNotesFooter: g.ModrinthNotesFooter}
}

// withNewSettings adds the settings the Python tool didn't have to a file it
// wrote, the way the template has them.
func withNewSettings(text string) string {
	newline := "\n"
	if strings.Contains(text, "\r\n") {
		newline = "\r\n"
	}
	entries, _ := parseTemplate()
	for i, entry := range entries {
		if !slices.Contains([]string{"curseforge_exclude", "modrinth_exclude", "prereleases"}, entry.key) {
			continue
		}
		// After the line of the setting before it.
		start := strings.Index(text, newline+entries[i-1].key+":") + len(newline)
		end := start + strings.Index(text[start:], newline)
		block := append(slices.Clone(entry.before), entry.line)
		text = text[:end] + newline + strings.Join(block, newline) + text[end:]
	}
	return text
}

// Python wrote new files with CRLF on Windows, where the goldens were made.
func windowsNewlines(t *testing.T) {
	saved := pycompat.NewFileNewline
	pycompat.NewFileNewline = "\r\n"
	t.Cleanup(func() { pycompat.NewFileNewline = saved })
}

// The goldens are what the Python tool wrote for each input (scripts/golden.py at the python-final tag).
func TestSettingsMatchPython(t *testing.T) {
	windowsNewlines(t)
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
	testutil.ReadJSON(t, "testdata/settings.json", &golden)
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
		if got := testutil.Read(t, filepath.Join(root, SettingsFile)); c.Output != nil && got != withNewSettings(*c.Output) {
			t.Errorf("%s: file\n got %q\nwant %q", label, got, withNewSettings(*c.Output))
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
	legacy, err := os.ReadFile(filepath.Join("testdata", "legacy_"+fixture)) // The old tool's settings.yml of the packs.
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

// prereleases is standalone or previews, and falls back to standalone.
func TestPrereleasesSetting(t *testing.T) {
	root := filepath.Join(t.TempDir(), "Pack")
	for value, want := range map[string]string{"": "standalone", "previews": "previews", "cumulative": "standalone"} {
		testutil.Write(t, filepath.Join(root, SettingsFile), "prereleases: \""+value+"\"\n")
		settings, notes, err := LoadSettings(root, "Pack", nil)
		if err != nil || settings.Prereleases != want || (value == "cumulative") != anyContains(notes, "prereleases 'cumulative'") {
			t.Error(value, settings.Prereleases, notes, err)
		}
		if !strings.Contains(testutil.Read(t, filepath.Join(root, SettingsFile)), "\nprereleases: \""+value+"\"\n") {
			t.Error("the file doesn't keep", value)
		}
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

// server_template is a folder of the project, or a full path to one anywhere.
func TestServerTemplateDir(t *testing.T) {
	root := filepath.Join(t.TempDir(), "Pack")
	elsewhere := filepath.Join(t.TempDir(), "Servers", "Pack")
	for template, want := range map[string]string{"Server Pack": filepath.Join(root, "Server Pack"), elsewhere: elsewhere} {
		p := &Project{Root: root, Settings: Settings{ServerTemplate: template}}
		if got := p.ServerTemplateDir(); got != want {
			t.Errorf("%s: got %s", template, got)
		}
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

// curseforge_exclude and modrinth_exclude are lists like server_exclude, which
// the file keeps in the template's layout.
func TestPlatformExcludeSettings(t *testing.T) {
	root := filepath.Join(t.TempDir(), "Pack")
	testutil.Write(t, filepath.Join(root, SettingsFile), "modrinth_exclude:\n- a\n- b\ncurseforge_exclude: voxy-worldgen\n")
	settings, _, err := LoadSettings(root, "Pack", nil)
	if err != nil || !slices.Equal(settings.CurseForgeExclude, []string{"voxy-worldgen"}) || !slices.Equal(settings.ModrinthExclude, []string{"a", "b"}) {
		t.Error(settings, err)
	}
	text := testutil.Read(t, filepath.Join(root, SettingsFile))
	if !strings.Contains(text, "only the Modrinth pack (slug, name or filename)") ||
		!strings.Contains(text, "\ncurseforge_exclude: voxy-worldgen\nmodrinth_exclude:\n- a\n- b\n") {
		t.Error(text)
	}
}

// Opening a project notes the exclude entries that name none of its files,
// like a jar's filename after an update. Disabled mods and resource packs count.
func TestStaleExcludesAreNoted(t *testing.T) {
	pw := testutil.PackDir(t)
	testutil.Write(t, filepath.Join(filepath.Dir(pw), SettingsFile), "curseforge_exclude: [sodium, lithium-0.20.jar]\n"+
		"modrinth_exclude: [Boss Checklist, Fresh Animations]\nserver_exclude: [pinned mod, gone]\n")
	_, notes, err := Open(filepath.Dir(pw), nil)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"curseforge_exclude: 'lithium-0.20.jar' matches no current mod; check it.",
		"server_exclude: 'gone' matches no current mod; check it."}
	if !slices.Equal(notes, want) {
		t.Errorf("notes %q", notes)
	}
}

// The import of old settings already notes an exclusion it can't place, so
// opening the project doesn't say it twice.
func TestImportedStaleExcludeIsNotedOnce(t *testing.T) {
	root := legacyProject(t, "insomnia.yml")
	testutil.Write(t, filepath.Join(root, LegacySettingsFile), "export_server: True\nserver_mods_remove_list: [\"Gone-1.0.jar\"]\n")
	_, notes, err := Open(root, answer(""))
	if err != nil {
		t.Fatal(err)
	}
	stale := slices.DeleteFunc(slices.Clone(notes), func(note string) bool { return !strings.Contains(note, "'Gone-1.0.jar' matches no current mod") })
	if len(stale) != 1 {
		t.Errorf("notes %q", notes)
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

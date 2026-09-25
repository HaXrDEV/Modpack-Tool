package pack

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"

	"github.com/HaXrDEV/Modpack-Tool/internal/testutil"
)

var modrinthVersion = map[string]any{"id": "NEWID", "files": []any{
	map[string]any{"primary": false, "url": "https://cdn.modrinth.com/data/x/versions/y/other.jar", "filename": "other.jar",
		"hashes": map[string]any{"sha512": "o"}},
	map[string]any{"primary": true, "url": "https://cdn.modrinth.com/data/x/versions/y/new%20file%2B1.jar",
		"filename": "new file+1.jar", "hashes": map[string]any{"sha1": "s1", "sha512": "s512"}},
}}

// The goldens are tomlkit's edits of real metafiles from both packs, in both
// line endings (scripts/golden.py).
func TestEditsMatchTomlkit(t *testing.T) {
	data, err := os.ReadFile("testdata/tomledits.json")
	if err != nil {
		t.Fatal(err)
	}
	var golden struct {
		Cases []struct{ Name, Op, Input, Output string } `json:"cases"`
	}
	if err := json.Unmarshal(data, &golden); err != nil {
		t.Fatal(err)
	}
	pw := t.TempDir()
	for _, c := range golden.Cases {
		rel := "mods/x.pw.toml"
		if c.Op == "version" {
			rel = "pack.toml"
		}
		testutil.Write(t, filepath.Join(pw, rel), c.Input)
		decoded, err := DecodeTOML([]byte(c.Input))
		if err != nil {
			t.Fatal(err)
		}
		mod := Mod{Rel: rel, Data: decoded}
		switch c.Op {
		case "disable", "enable":
			err = SetDisabled(pw, mod, c.Op == "disable")
		case "modrinth":
			_, err = ApplyModrinthVersion(pw, mod, modrinthVersion)
		case "version":
			err = SetPackVersion(pw, "9.9.9-beta.1")
		}
		if err != nil {
			t.Errorf("%s %s: %v", c.Name, c.Op, err)
			continue
		}
		if got := testutil.Read(t, filepath.Join(pw, rel)); got != c.Output {
			t.Errorf("%s %s (crlf=%v):\n got %q\nwant %q", c.Name, c.Op, strings.Contains(c.Input, "\r\n"), got, c.Output)
		}
	}
}

func loadMods(t *testing.T, pw string) []Mod {
	t.Helper()
	mods, warnings, err := LoadMods(pw, Categories)
	if err != nil || len(warnings) > 0 {
		t.Fatal(err, warnings)
	}
	return mods
}

func bySlug(mods []Mod) map[string]Mod {
	result := map[string]Mod{}
	for _, mod := range mods {
		result[mod.Slug()] = mod
	}
	return result
}

// py: test_pack.py::test_parse_mods_reads_only_direct_metafiles
func TestParseModsReadsOnlyDirectMetafiles(t *testing.T) {
	var rels []string
	for _, mod := range loadMods(t, testutil.PackDir(t)) {
		rels = append(rels, mod.Rel)
	}
	want := []string{"mods/boss.pw.toml", "mods/lithium.pw.toml", "mods/pinned.pw.toml",
		"mods/server.pw.toml", "mods/sodium.pw.toml", "resourcepacks/fresh.pw.toml"}
	if !slices.Equal(rels, want) {
		t.Errorf("got %q", rels)
	}
}

// py: test_pack.py::test_mod_properties
func TestModProperties(t *testing.T) {
	mods := bySlug(loadMods(t, testutil.PackDir(t)))
	boss, sodium, server := mods["boss"], mods["sodium"], mods["server"]
	if !boss.Disabled() || boss.BaseSide() != "both" || boss.CurseForge() == nil {
		t.Error("boss")
	}
	if sodium.Side() != "client" || !sodium.InstallsOn("client") || sodium.InstallsOn("server") {
		t.Error("sodium")
	}
	if boss.InstallsOn("client") || !server.InstallsOn("server") || server.InstallsOn("client") {
		t.Error("installs")
	}
	if !mods["pinned"].Pinned() || mods["pinned"].DisplayName() != "Pinned Mod" {
		t.Error("pinned")
	}
	if !reflect.DeepEqual(mods["lithium"].Modrinth(), map[string]any{"mod-id": "Lithium", "version": "Lithv1"}) {
		t.Errorf("lithium modrinth: %v", mods["lithium"].Modrinth())
	}
	if mods["fresh"].Category() != "resourcepacks" || mods["fresh"].Folder() != "resourcepacks" {
		t.Error("fresh")
	}
}

// py: test_pack.py::test_invalid_legacy_and_empty_sides
func TestInvalidLegacyAndEmptySides(t *testing.T) {
	mod := func(side any) Mod {
		data := map[string]any{}
		if side != nil {
			data["side"] = side
		}
		return Mod{Rel: "mods/x.pw.toml", Data: data}
	}
	if m := mod("dyed(disabled)"); !m.Disabled() || m.Side() != "both" || m.SideValid() {
		t.Error("dyed(disabled)")
	}
	if !mod("none").Disabled() || !mod("both(none)").Disabled() || !mod("none").SideValid() {
		t.Error("none")
	}
	if mod("none").Side() != "both" || mod("both(none)").Side() != "both" {
		t.Error("none side")
	}
	if !mod("dyed").Disabled() {
		t.Error("typo")
	}
	if !mod("").InstallsOn("server") || !mod(nil).InstallsOn("client") {
		t.Error("empty side")
	}
}

// py: test_pack.py::test_disable_and_enable_keep_formatting_and_line_endings
func TestDisableAndEnableKeepFormatting(t *testing.T) {
	pw := testutil.PackDir(t)
	path := filepath.Join(pw, "mods", "sodium.pw.toml")
	original := testutil.Read(t, path)
	if err := SetDisabled(pw, bySlug(loadMods(t, pw))["sodium"], true); err != nil {
		t.Fatal(err)
	}
	changed := testutil.Read(t, path)
	if !strings.Contains(changed, "side = \"client(disabled)\"\r\n") || strings.ReplaceAll(changed, "client(disabled)", "client") != original {
		t.Errorf("disabled: %q", changed)
	}
	if err := SetDisabled(pw, bySlug(loadMods(t, pw))["sodium"], false); err != nil {
		t.Fatal(err)
	}
	if testutil.Read(t, path) != original {
		t.Error("enabling didn't restore the file")
	}
}

// py: test_pack.py::test_apply_modrinth_version
func TestApplyModrinthVersion(t *testing.T) {
	pw := testutil.PackDir(t)
	version := map[string]any{"id": "NEWID", "files": []any{
		map[string]any{"primary": false, "url": "https://x/other.jar", "filename": "other.jar", "hashes": map[string]any{"sha512": "o"}},
		map[string]any{"primary": true, "url": "https://x/lithium-0.22.jar", "filename": "lithium-0.22.jar",
			"hashes": map[string]any{"sha1": "s1", "sha512": "s512"}, "size": 1234},
	}}
	ok, err := ApplyModrinthVersion(pw, bySlug(loadMods(t, pw))["lithium"], version)
	if !ok || err != nil {
		t.Fatal(ok, err)
	}
	lithium := bySlug(loadMods(t, pw))["lithium"]
	format, hash := lithium.Hash()
	if lithium.Filename() != "lithium-0.22.jar" || format != "sha512" || hash != "s512" || lithium.Modrinth()["version"] != "NEWID" {
		t.Errorf("got %v", lithium.Data)
	}
	// Deliberately unlike the Python tool: no file-size (packwiz doesn't write or read it).
	if _, has := lithium.Download()["file-size"]; has {
		t.Error("file-size was written")
	}
}

// py: test_pack.py::test_pack_toml_helpers
func TestPackTOMLHelpers(t *testing.T) {
	pw := testutil.PackDir(t)
	data, err := ReadPackTOML(pw)
	if err != nil {
		t.Fatal(err)
	}
	if loader, version := LoaderOf(data); loader != "fabric" || version != "0.18.4" {
		t.Error("LoaderOf", loader, version)
	}
	if err := SetPackVersion(pw, "1.3.0"); err != nil {
		t.Fatal(err)
	}
	if data, _ := ReadPackTOML(pw); data["version"] != "1.3.0" {
		t.Error("version not written")
	}
	if !strings.Contains(testutil.Read(t, filepath.Join(pw, "pack.toml")), "version = \"1.3.0\"\n") {
		t.Error("pack.toml formatting")
	}
	entries, err := IndexEntries(pw)
	if err != nil || len(entries) != 2 || entries[0].File != "config/bcc.json" || entries[1].File != "mods/sodium.pw.toml" || !entries[1].Metafile {
		t.Errorf("IndexEntries = %v, %v", entries, err)
	}
	if hash, _ := IndexHash(pw); hash != "abc" {
		t.Error("IndexHash", hash)
	}
}

// py: test_pack.py::test_generated_files
func TestGeneratedFiles(t *testing.T) {
	pw := testutil.PackDir(t)
	bcc := filepath.Join(pw, "config", "bcc.json")
	if changed, err := WriteBCCVersion(bcc, "1.2.0"); !changed || err != nil {
		t.Fatal(changed, err)
	}
	if got := testutil.Read(t, bcc); got != `{"projectID": 1, "modpackName": "MyPack", "modpackVersion": "1.2.0"}` {
		t.Errorf("bcc.json = %q", got)
	}
	if changed, _ := WriteBCCVersion(bcc, "1.2.0"); changed {
		t.Error("unchanged version was written")
	}
	if changed, err := WriteBCCVersion(filepath.Join(pw, "missing.json"), "1.2.0"); changed || err != nil {
		t.Error("missing file", err)
	}
	mods := loadMods(t, pw)
	var names []string
	json.Unmarshal([]byte(CrashAssistantModlist(mods)), &names)
	if !slices.Equal(names, []string{"lithium-0.21.jar", "pinned-1.0.jar", "sodium-0.8.jar"}) {
		t.Errorf("modlist = %q", names)
	}
	want := "# Mod List\n\n## Active Mods\n- Lithium\n- Pinned Mod\n- Server Thing\n- Sodium\n\n## Inactive Mods\n- Boss Checklist\n"
	if got := ModlistMarkdown(mods, false); got != want {
		t.Errorf("modlist.md = %q", got)
	}
	if !strings.Contains(ModlistMarkdown(mods, true), "- Sodium [Client]") {
		t.Error("side tags")
	}
}

// py: test_pack.py::test_strip_brackets
func TestStripBrackets(t *testing.T) {
	if got := StripBrackets("Entity Texture Features [Fabric] (beta)"); got != "Entity Texture Features" {
		t.Errorf("got %q", got)
	}
	if !strings.HasPrefix(testutil.Metafile("A", "a.jar"), `name = "A"`) {
		t.Error("Metafile")
	}
}

func TestEditRefusesUnintendedChanges(t *testing.T) {
	if _, err := EditTOML("a = 1\n[t]\nb = 2\n", []Edit{{"missing", "x", "1"}}); err == nil {
		t.Error("editing a missing table should fail")
	}
	got, err := EditTOML("a = \"x\" # keep\nlist = [\n  1,\n]\n\n[t]\n", []Edit{{"", "a", `"y"`}, {"", "b", "2"}, {"t", "c", "3"}})
	if err != nil {
		t.Fatal(err)
	}
	if want := "a = \"y\" # keep\nlist = [\n  1,\n]\nb = 2\n\n[t]\nc = 3\n"; got != want {
		t.Errorf("got %q", got)
	}
}

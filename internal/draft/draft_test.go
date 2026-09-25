package draft

import (
	"slices"
	"testing"

	"github.com/HaXrDEV/Modpack-Tool/internal/diff"
	"github.com/HaXrDEV/Modpack-Tool/internal/pack"
	"github.com/HaXrDEV/Modpack-Tool/internal/testutil"
)

type opts = testutil.MetaOptions

var old, current = pack.Tree(testutil.OldTree()), pack.Tree(testutil.NewTree())

func tree(files map[string]string) pack.Tree { return testutil.Tree(files) }

func compare() *diff.PackDiff { return diff.Compare(old, current, "1.0.0", "1.1.0", "1.21.11", true) }

// py: test_diff_and_drafting.py::test_update_overview_uses_real_names
func TestUpdateOverviewUsesRealNames(t *testing.T) {
	want := []string{
		"Updated to Minecraft 1.21.11.",
		"Added 'Brand New' mod.",
		"Re-added some mods.",
		"Temporarily removed incompatible mod 'Sodium Extra'.", // Not mistaken for "Sodium".
		"Updated mods.",
		"Added 'BSL Shaders' shaderpack.",
	}
	if got := UpdateOverview(compare()); !slices.Equal(got, want) {
		t.Errorf("got %q", got)
	}
}

// py: test_diff_and_drafting.py::test_update_overview_without_migration
func TestUpdateOverviewWithoutMigration(t *testing.T) {
	d := compare()
	d.PreviousMinecraft = "1.21.11"
	if !slices.Contains(UpdateOverview(d), "Removed 'Sodium Extra' mod.") {
		t.Error(UpdateOverview(d))
	}
	empty := diff.Compare(old, old, "1.0.0", "1.0.1", "1.21.10", true)
	if got := UpdateOverview(empty); !slices.Equal(got, []string{"Maintenance update."}) {
		t.Error(got)
	}
}

// py: test_diff_and_drafting.py::test_config_change_draft
func TestConfigChangeDraft(t *testing.T) {
	mods, _ := pack.ParseMods(current, pack.Categories)
	mods = append(mods, pack.Mod{Rel: "mods/voxy.pw.toml", Data: map[string]any{"name": "Voxy WorldGen"}},
		pack.Mod{Rel: "mods/breakneckmenu.pw.toml", Data: map[string]any{"name": "Breakneck Menu"}},
		pack.Mod{Rel: "mods/rpo.pw.toml", Data: map[string]any{"name": "Resource Pack Overrides"}})
	want := []string{
		"- Moved breakneckmenu.json5 to YOSBR so it applies as a default on first launch: [Breakneck Menu]",
		"- Removed config file gone.json: [Gone]",
		"- Reordered B.zip in section default_packs: [Resource Pack Overrides]",
		"- Added C.zip to section default_packs: [Resource Pack Overrides]",
		`- Changed "maxActiveTasks" from 5 to 2: [Voxy WorldGen]`,
		`- Changed default "coloredText" from false to true: [Breakneck Menu]`,
	}
	if got := ConfigChanges(compare(), NewLabels(mods)); !slices.Equal(got, want) {
		t.Errorf("got %q", got)
	}
}

// py: test_diff_and_drafting.py::test_labels_match_config_paths_to_mods
func TestLabelsMatchConfigPathsToMods(t *testing.T) {
	labels := NewLabels([]pack.Mod{
		{Rel: "mods/voxy-worldgen.pw.toml", Data: map[string]any{"name": "Voxy WorldGen"}},
		{Rel: "mods/sodium.pw.toml", Data: map[string]any{"name": "Sodium"}},
		{Rel: "mods/lambdynamiclights.pw.toml", Data: map[string]any{"name": "LambDynamicLights - Dynamic Lights"}},
		{Rel: "mods/off.pw.toml", Data: map[string]any{"name": "Disabled One", "side": "both(disabled)"}},
	})
	cases := map[string]string{
		"yosbr/config/voxyworldgenv2.json": "Voxy WorldGen",
		"sodium-options.json":              "Sodium",
		"fancymenu/customization/x.txt":    "Fancymenu",
		"disabled_one.json":                "Disabled One",
	}
	for path, want := range cases {
		if got := labels.ForConfig(path); got != want {
			t.Errorf("ForConfig(%q) = %q, want %q", path, got, want)
		}
	}
}

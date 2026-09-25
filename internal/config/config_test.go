package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// py: test_project.py::test_registry
func TestRegistry(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "config.yml")
	c, warning := Load(path)
	if warning != "" {
		t.Fatal(warning)
	}
	for _, name := range []string{"A", "B", "a"} {
		if err := c.Remember(filepath.Join(dir, name)); err != nil {
			t.Fatal(err)
		}
	}
	loaded, _ := Load(path)
	if !strings.HasSuffix(strings.ToLower(loaded.LastUsedProject), "a") {
		t.Error(loaded.LastUsedProject)
	}
	if filepath.Separator == '\\' && len(loaded.Projects) != 2 {
		t.Errorf("A and a are the same folder on Windows: %q", loaded.Projects)
	}
	if err := loaded.Forget(filepath.Join(dir, "B")); err != nil {
		t.Fatal(err)
	}
	again, _ := Load(path)
	for _, root := range again.Projects {
		if strings.HasSuffix(root, "B") {
			t.Error("B wasn't forgotten")
		}
	}
}

// py: test_robustness.py::test_unreadable_tool_config_is_not_overwritten
func TestUnreadableConfigIsNotOverwritten(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.yml")
	before := "packwiz_exe_path: \"C:\\Users\\me\\packwiz.exe\"\nprojects: []\n"
	os.WriteFile(path, []byte(before), 0o644)
	c, warning := Load(path)
	if !c.ReadOnly || warning == "" {
		t.Fatal("expected a read-only config and a warning")
	}
	c.Remember(filepath.Join(t.TempDir(), "Pack"))
	if data, _ := os.ReadFile(path); string(data) != before {
		t.Error("the unreadable file was overwritten")
	}
}

// The Python tool's tool_config.yml can be copied over as it is.
func TestReadsPythonToolConfig(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.yml")
	os.WriteFile(path, []byte("last_used_project: D:\\GitHub Projects\\Breakneck\r\npackwiz_exe_path: ''\r\n"+
		"curseforge_api_key: key123\r\nprojects:\r\n- name: Breakneck\r\n  root: D:\\GitHub Projects\\Breakneck\r\n"+
		"- D:\\Plain\\Entry\r\n"), 0o644)
	c, warning := Load(path)
	if warning != "" || c.LastUsedProject != `D:\GitHub Projects\Breakneck` || c.CurseForgeKey() != "key123" ||
		len(c.Projects) != 2 || c.Projects[1] != `D:\Plain\Entry` {
		t.Errorf("%+v %s", c, warning)
	}
	if err := c.Save(); err != nil {
		t.Fatal(err)
	}
	saved, _ := os.ReadFile(path)
	if !strings.Contains(string(saved), "- name: Breakneck\n  root: D:\\GitHub Projects\\Breakneck\n") ||
		!strings.Contains(string(saved), "packwiz_exe_path: ''\n") {
		t.Errorf("saved:\n%s", saved)
	}
}

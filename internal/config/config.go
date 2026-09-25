// Package config is the tool-wide state: the registry of known projects,
// where packwiz is, an optional CurseForge API key, and the cache folder.
package config

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"go.yaml.in/yaml/v3"

	"github.com/HaXrDEV/Modpack-Tool/internal/files"
	"github.com/HaXrDEV/Modpack-Tool/internal/pycompat"
)

// Config is the tool configuration (config.yml). Its keys are the same as the
// Python tool's tool_config.yml, so that file can simply be copied over.
type Config struct {
	LastUsedProject  string
	PackwizExePath   string
	CurseForgeAPIKey string
	Projects         []string // Absolute project roots.
	// ReadOnly is set when the file couldn't be read, so it is never overwritten.
	ReadOnly bool
	Path     string
}

// Dir is the tool's folder for configuration: %AppData%\modpack-tool.
func Dir() string {
	base, err := os.UserConfigDir()
	if err != nil {
		base = "."
	}
	return filepath.Join(base, "modpack-tool")
}

// DefaultPath is config.yml in Dir, or MODPACK_TOOL_CONFIG when set (handy
// for testing with a scratch registry).
func DefaultPath() string {
	if path := os.Getenv("MODPACK_TOOL_CONFIG"); path != "" {
		return path
	}
	return filepath.Join(Dir(), "config.yml")
}

// CacheDir is where downloads and CurseForge fingerprints are cached:
// %LocalAppData%\modpack-tool (MODPACK_TOOL_CACHE overrides it).
func CacheDir() string {
	if path := os.Getenv("MODPACK_TOOL_CACHE"); path != "" {
		return path
	}
	base, err := os.UserCacheDir()
	if err != nil {
		base = os.TempDir()
	}
	return filepath.Join(base, "modpack-tool")
}

// LogPath is the full log of the last action.
func LogPath() string { return filepath.Join(CacheDir(), "last-run.log") }

// Load reads the configuration. A file that can't be read gives a warning and
// a read-only config instead of an error, so a typo never locks you out.
func Load(path string) (*Config, string) {
	c := &Config{Path: path}
	content, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return c, ""
	}
	var raw any
	if err == nil {
		err = yaml.Unmarshal([]byte(pycompat.UniversalNewlines(string(content))), &raw)
	}
	values, ok := raw.(map[string]any)
	if err == nil && !ok && raw != nil {
		err = fmt.Errorf("expected keys such as 'projects:'")
	}
	if err != nil {
		c.ReadOnly = true
		return c, fmt.Sprintf("Could not read %s: %v\n"+
			"  It is left untouched until you fix it (tip: single quotes around Windows paths).", path, err)
	}
	c.LastUsedProject = pycompat.Or(values["last_used_project"], "")
	c.PackwizExePath = pycompat.Or(values["packwiz_exe_path"], "")
	c.CurseForgeAPIKey = pycompat.Or(values["curseforge_api_key"], "")
	list, _ := values["projects"].([]any)
	for _, entry := range list {
		root := entry
		if m, ok := entry.(map[string]any); ok {
			root = m["root"]
		}
		if pycompat.Truthy(root) {
			c.Projects = append(c.Projects, pycompat.Str(root))
		}
	}
	return c, ""
}

// Save writes the configuration (unless it is read-only).
func (c *Config) Save() error {
	if c.ReadOnly {
		return nil
	}
	lines := []string{
		"last_used_project: " + pycompat.YAMLScalar(c.LastUsedProject, false),
		"packwiz_exe_path: " + pycompat.YAMLScalar(c.PackwizExePath, false),
		"curseforge_api_key: " + pycompat.YAMLScalar(c.CurseForgeAPIKey, false),
		"projects:",
	}
	if len(c.Projects) == 0 {
		lines[len(lines)-1] = "projects: []"
	}
	for _, root := range c.Projects {
		lines = append(lines, "- name: "+pycompat.YAMLScalar(filepath.Base(root), false),
			"  root: "+pycompat.YAMLScalar(root, false))
	}
	return files.WriteAtomic(c.Path, []byte(strings.Join(lines, "\n")+"\n"))
}

// SamePath reports whether two paths name the same folder (case-insensitive on Windows).
func SamePath(a, b string) bool {
	absA, errA := filepath.Abs(a)
	absB, errB := filepath.Abs(b)
	if errA != nil || errB != nil {
		return a == b
	}
	if filepath.Separator == '\\' {
		return strings.EqualFold(absA, absB)
	}
	return absA == absB
}

// Remember adds a project root (if new) and makes it the last used one.
func (c *Config) Remember(root string) error {
	abs, err := filepath.Abs(root)
	if err != nil {
		return err
	}
	known := false
	for _, p := range c.Projects {
		known = known || SamePath(p, abs)
	}
	if !known {
		c.Projects = append(c.Projects, abs)
	}
	c.LastUsedProject = abs
	return c.Save()
}

// Forget removes a project root from the registry (its files stay untouched).
func (c *Config) Forget(root string) error {
	var kept []string
	for _, p := range c.Projects {
		if !SamePath(p, root) {
			kept = append(kept, p)
		}
	}
	c.Projects = kept
	if c.LastUsedProject != "" && SamePath(c.LastUsedProject, root) {
		c.LastUsedProject = ""
	}
	return c.Save()
}

// PackwizExe is packwiz from the config, then PATH, then Go's default install folder.
func (c *Config) PackwizExe() string {
	if path := strings.TrimSpace(c.PackwizExePath); path != "" {
		return path
	}
	if path, err := exec.LookPath("packwiz"); err == nil {
		return path
	}
	home, _ := os.UserHomeDir()
	name := "packwiz"
	if filepath.Separator == '\\' {
		name += ".exe"
	}
	return filepath.Join(home, "go", "bin", name)
}

// CurseForgeKey is a personal CurseForge API key, or "" to use packwiz's.
func (c *Config) CurseForgeKey() string { return strings.TrimSpace(c.CurseForgeAPIKey) }

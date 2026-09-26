// Package project is one modpack project: its folders, what pack.toml says
// about it, and its settings (modpack-tool.yml).
package project

import (
	"path/filepath"
	"strings"

	"github.com/HaXrDEV/Modpack-Tool/internal/fail"
	"github.com/HaXrDEV/Modpack-Tool/internal/files"
	"github.com/HaXrDEV/Modpack-Tool/internal/pack"
	"github.com/HaXrDEV/Modpack-Tool/internal/pycompat"
	"github.com/HaXrDEV/Modpack-Tool/internal/version"
)

// Project is a modpack folder (the one containing Packwiz/pack.toml).
type Project struct {
	Root               string
	Settings           Settings
	Name               string
	Version            string
	Minecraft          string
	Loader             string
	LoaderVersion      string
	AcceptableVersions []string
}

// PackDir is the Packwiz folder.
func (p *Project) PackDir() string { return filepath.Join(p.Root, "Packwiz") }

// ChangelogDir holds the changelog YAML files.
func (p *Project) ChangelogDir() string { return filepath.Join(p.Root, "Changelogs") }

// DataDir holds the release records the wiki reads.
func (p *Project) DataDir() string { return filepath.Join(p.ChangelogDir(), "data") }

// ExportDir receives the built packs.
func (p *Project) ExportDir() string { return filepath.Join(p.Root, "Export") }

// ServerTemplateDir is the folder copied into the server pack.
func (p *Project) ServerTemplateDir() string { return filepath.Join(p.Root, p.Settings.ServerTemplate) }

// LoaderLabel is the loader's display name, e.g. "Fabric".
func (p *Project) LoaderLabel() string { return pack.LoaderLabel(p.Loader) }

// MCPrefixed reports whether to suggest "<mc>-<release>" versions.
func (p *Project) MCPrefixed() bool {
	return p.Settings.MCPrefixedVersions || version.IsMCPrefixed(p.Version)
}

// CoversPrereleases reports whether the version is a full release that covers
// its pre-releases (prereleases: previews): it's compared with the previous
// full release and its changelog starts with their notes.
func (p *Project) CoversPrereleases() bool {
	return p.Settings.Prereleases == "previews" && !version.IsPrerelease(p.Version)
}

// Rel returns path relative to the project root, with forward slashes.
func (p *Project) Rel(path string) string {
	if rel, err := filepath.Rel(p.Root, path); err == nil {
		return filepath.ToSlash(rel)
	}
	return path
}

// Reload re-reads pack.toml (after packwiz or the tool changed it).
func (p *Project) Reload() error {
	data, err := pack.ReadPackTOML(p.PackDir())
	if err != nil {
		return err
	}
	versions := pack.Table(data["versions"])
	var missing []string
	for _, key := range []string{"name", "version"} {
		if !pycompat.Truthy(data[key]) {
			missing = append(missing, key)
		}
	}
	if !pycompat.Truthy(versions["minecraft"]) {
		missing = append(missing, "versions.minecraft")
	}
	if len(missing) > 0 {
		return fail.Errorf("%s is missing: %s.", filepath.Join(p.PackDir(), "pack.toml"), strings.Join(missing, ", "))
	}
	p.Name = pycompat.Str(data["name"])
	p.Version = pycompat.Str(data["version"])
	p.Minecraft = pycompat.Str(versions["minecraft"])
	p.Loader, p.LoaderVersion = pack.LoaderOf(data)
	p.AcceptableVersions = nil
	if list, ok := pack.Table(data["options"])["acceptable-game-versions"].([]any); ok {
		for _, v := range list {
			p.AcceptableVersions = append(p.AcceptableVersions, pycompat.Str(v))
		}
	}
	return nil
}

// Open loads a project folder; the notes are for the user (for example
// "Created modpack-tool.yml"). ask answers the one question a first import of
// old settings needs; nil takes the default.
func Open(root string, ask AskFunc) (*Project, []string, error) {
	abs, err := filepath.Abs(root)
	if err != nil {
		return nil, nil, err
	}
	if !files.IsFile(filepath.Join(abs, "Packwiz", "pack.toml")) {
		return nil, nil, fail.Errorf("No Packwiz\\pack.toml in %s. Choose the modpack folder that contains 'Packwiz'.", abs)
	}
	data, err := pack.ReadPackTOML(filepath.Join(abs, "Packwiz"))
	if err != nil {
		return nil, nil, err
	}
	name := pycompat.Or(data["name"], filepath.Base(abs))
	settings, notes, err := LoadSettings(abs, name, ask)
	if err != nil {
		return nil, nil, err
	}
	p := &Project{Root: abs, Settings: settings}
	if err := p.Reload(); err != nil {
		return nil, nil, err
	}
	return p, notes, nil
}

// FindRoot returns the pack folder containing dir (the nearest folder with
// Packwiz/pack.toml), or "".
func FindRoot(dir string) string {
	dir, err := filepath.Abs(dir)
	if err != nil {
		return ""
	}
	for {
		if files.IsFile(filepath.Join(dir, "Packwiz", "pack.toml")) {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return ""
		}
		dir = parent
	}
}

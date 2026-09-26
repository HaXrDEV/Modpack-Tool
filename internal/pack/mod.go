// Package pack reads and writes the Packwiz folder: the metafiles (*.pw.toml),
// pack.toml and index.toml, and the files the tool keeps up to date inside the
// pack (bcc.json, the Crash Assistant modlist and modlist.md). Reads decode
// TOML into maps; edits change single lines, so packwiz's formatting and line
// endings survive (index hashes are computed over the raw bytes).
package pack

import (
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"slices"
	"strings"

	"github.com/BurntSushi/toml"

	"github.com/HaXrDEV/Modpack-Tool/internal/pycompat"
)

// Categories are the folders whose metafiles install into their own folder.
var Categories = []string{"mods", "resourcepacks", "shaderpacks"}

// TreeParts are the parts of the Packwiz folder that comparisons between
// releases look at.
var TreeParts = []string{"pack.toml", "mods", "resourcepacks", "shaderpacks", "config"}

var validSides = []string{"both", "client", "server"}

// LoaderLabels maps pack.toml loader keys to display names, in lookup order.
var LoaderLabels = []struct{ Key, Label string }{
	{"fabric", "Fabric"}, {"quilt", "Quilt"}, {"forge", "Forge"}, {"neoforge", "NeoForge"}, {"liteloader", "LiteLoader"},
}

var bracketed = regexp.MustCompile(`\(.*?\)|\[.*?\]|\{.*?\}`)

// StripBrackets drops "(...)", "[...]" and "{...}" parts from a display name.
func StripBrackets(text string) string {
	return pycompat.Strip(bracketed.ReplaceAllString(text, ""))
}

// Tree is part of a Packwiz folder as {slash path relative to it: bytes}.
type Tree map[string][]byte

// Mod is one metafile (mod, resource pack or shader pack) as packwiz stores it.
type Mod struct {
	Rel  string         // Path relative to the Packwiz folder, e.g. "mods/sodium.pw.toml".
	Data map[string]any // The decoded TOML.
}

// Slug is the metafile name without ".pw.toml".
func (m Mod) Slug() string {
	return strings.TrimSuffix(strings.TrimSuffix(pycompat.Name(m.Rel), ".toml"), ".pw")
}

// Folder is the folder the metafile is in, e.g. "mods".
func (m Mod) Folder() string { return path.Dir(m.Rel) }

// Category is the first folder of the path, e.g. "resourcepacks".
func (m Mod) Category() string {
	first, _, _ := strings.Cut(m.Rel, "/")
	return first
}

// Name is the metafile's name, or its slug when it has none.
func (m Mod) Name() string { return pycompat.Or(m.Data["name"], m.Slug()) }

// DisplayName is the name without bracketed parts such as "[Fabric]".
func (m Mod) DisplayName() string {
	if name := StripBrackets(m.Name()); name != "" {
		return name
	}
	return m.Slug()
}

// Filename is the file packwiz installs.
func (m Mod) Filename() string { return pycompat.Or(m.Data["filename"], "") }

// SideRaw is the side as written in the metafile.
func (m Mod) SideRaw() string { return pycompat.Strip(pycompat.Or(m.Data["side"], "")) }

// Disabled reports whether packwiz leaves the file out: only the sides packwiz
// installs count as active, so "both(disabled)", "none" and typos don't.
func (m Mod) Disabled() bool {
	side := strings.ToLower(m.SideRaw())
	return side != "" && !slices.Contains(validSides, side)
}

// BaseSide is the side without the "(disabled)" marker; empty means both.
func (m Mod) BaseSide() string {
	base, _, _ := strings.Cut(m.SideRaw(), "(")
	if base = strings.ToLower(pycompat.Strip(base)); base != "" {
		return base
	}
	return "both"
}

// SideValid reports whether the side is one packwiz accepts ("none" is how
// older packs marked disabled mods).
func (m Mod) SideValid() bool {
	return slices.Contains(validSides, m.BaseSide()) || m.BaseSide() == "none"
}

// Side is the side used for installs and when re-enabling; anything else
// counts as "both".
func (m Mod) Side() string {
	if slices.Contains(validSides, m.BaseSide()) {
		return m.BaseSide()
	}
	return "both"
}

// InstallsOn reports whether the file ships in a "client" or "server" install.
func (m Mod) InstallsOn(side string) bool {
	return !m.Disabled() && (m.Side() == "both" || m.Side() == side)
}

// Pinned reports whether packwiz update skips the file.
func (m Mod) Pinned() bool { return pycompat.Truthy(m.Data["pin"]) }

// Optional reports whether the file is marked optional.
func (m Mod) Optional() bool { return pycompat.Truthy(Table(m.Data["option"])["optional"]) }

// OptionalDefault reports whether an optional file is on by default.
func (m Mod) OptionalDefault() bool { return pycompat.Truthy(Table(m.Data["option"])["default"]) }

// Download is the [download] table.
func (m Mod) Download() map[string]any { return Table(m.Data["download"]) }

// DownloadURL is the download URL, if the metafile has one.
func (m Mod) DownloadURL() string { return pycompat.Or(m.Download()["url"], "") }

// Hash returns (hash format, hash), e.g. ("sha512", "ab12...").
func (m Mod) Hash() (string, string) {
	download := m.Download()
	return pycompat.Or(download["hash-format"], ""), pycompat.Or(download["hash"], "")
}

func (m Mod) source(name string) map[string]any {
	if value, ok := Table(m.Data["update"])[name].(map[string]any); ok {
		return value
	}
	return nil
}

// Modrinth is the [update.modrinth] table, or nil.
func (m Mod) Modrinth() map[string]any { return m.source("modrinth") }

// CurseForge is the [update.curseforge] table, or nil.
func (m Mod) CurseForge() map[string]any { return m.source("curseforge") }

// GitHub is the [update.github] table, or nil.
func (m Mod) GitHub() map[string]any { return m.source("github") }

// ModrinthVersion is the installed Modrinth version id ("" without one).
func (m Mod) ModrinthVersion() string { return pycompat.Or(m.Modrinth()["version"], "") }

// ModrinthProject is the Modrinth project id ("" without one).
func (m Mod) ModrinthProject() string { return pycompat.Or(m.Modrinth()["mod-id"], "") }

// CurseForgeFile is the installed CurseForge file id (0 without one).
func (m Mod) CurseForgeFile() int64 { return Int(m.CurseForge()["file-id"]) }

// CurseForgeProject is the CurseForge project id (0 without one).
func (m Mod) CurseForgeProject() int64 { return Int(m.CurseForge()["project-id"]) }

// Table returns value as a TOML table, or an empty one.
func Table(value any) map[string]any {
	if table, ok := value.(map[string]any); ok {
		return table
	}
	return map[string]any{}
}

// Int is Python's int() of a decoded number or numeric string; 0 otherwise.
func Int(value any) int64 {
	switch v := value.(type) {
	case int64:
		return v
	case float64:
		return int64(v)
	case string:
		var n int64
		for _, c := range pycompat.Strip(v) {
			if c < '0' || c > '9' {
				return 0
			}
			n = n*10 + int64(c-'0')
		}
		return n
	}
	return 0
}

// Names are the mods' names.
func Names(mods []Mod) []string {
	names := make([]string, 0, len(mods))
	for _, mod := range mods {
		names = append(names, mod.Name())
	}
	return names
}

// ParseMods returns the metafiles directly inside the category folders of a
// tree, sorted by path. Unreadable metafiles are skipped with a warning.
func ParseMods(tree Tree, categories []string) ([]Mod, []string) {
	var rels []string
	for rel := range tree {
		parts := strings.Split(rel, "/")
		if len(parts) == 2 && slices.Contains(categories, parts[0]) && strings.HasSuffix(parts[1], ".toml") {
			rels = append(rels, rel)
		}
	}
	var mods []Mod
	var warnings []string
	for _, rel := range pycompat.SortedLower(rels) {
		data, err := DecodeTOML(tree[rel])
		if err != nil {
			warnings = append(warnings, "Skipping unreadable metafile "+rel+": "+err.Error())
			continue
		}
		mods = append(mods, Mod{Rel: rel, Data: data})
	}
	return mods, warnings
}

// DecodeTOML decodes a TOML document.
func DecodeTOML(content []byte) (map[string]any, error) {
	data := map[string]any{}
	if _, err := toml.Decode(string(content), &data); err != nil {
		return nil, err
	}
	return data, nil
}

// InTree reports whether a file (a path relative to the Packwiz folder)
// belongs in a Tree. A category folder contributes only its metafiles, the
// .toml files directly inside it, which is all ParseMods reads; the other
// parts contribute every file.
func InTree(rel string) bool {
	category, name, ok := strings.Cut(rel, "/")
	if !ok || !slices.Contains(Categories, category) {
		return true
	}
	return !strings.Contains(name, "/") && strings.HasSuffix(name, ".toml")
}

// ReadTree reads parts of a Packwiz folder (see InTree).
func ReadTree(packDir string, parts []string) (Tree, error) {
	tree := Tree{}
	for _, part := range parts {
		root := filepath.Join(packDir, part)
		info, err := os.Stat(root)
		if err != nil {
			continue
		}
		if info.Mode().IsRegular() {
			data, err := os.ReadFile(root)
			if err != nil {
				return nil, err
			}
			tree[part] = data
			continue
		}
		err = filepath.WalkDir(root, func(p string, entry fs.DirEntry, err error) error {
			if err != nil {
				return err
			}
			if entry.IsDir() && p != root && slices.Contains(Categories, part) {
				return fs.SkipDir // Metafiles are directly inside a category folder.
			}
			rel, _ := filepath.Rel(packDir, p)
			rel = filepath.ToSlash(rel)
			if !entry.Type().IsRegular() || !InTree(rel) {
				return nil
			}
			data, err := os.ReadFile(p)
			if err != nil {
				return err
			}
			tree[rel] = data
			return nil
		})
		if err != nil {
			return nil, err
		}
	}
	return tree, nil
}

// LoadMods reads the metafiles of the given categories.
func LoadMods(packDir string, categories []string) ([]Mod, []string, error) {
	tree, err := ReadTree(packDir, categories)
	if err != nil {
		return nil, nil, err
	}
	mods, warnings := ParseMods(tree, categories)
	return mods, warnings, nil
}

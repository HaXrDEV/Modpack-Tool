package pack

import (
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"strings"

	"github.com/HaXrDEV/Modpack-Tool/internal/fail"
	"github.com/HaXrDEV/Modpack-Tool/internal/files"
	"github.com/HaXrDEV/Modpack-Tool/internal/pycompat"
)

// ReadPackTOML decodes pack.toml.
func ReadPackTOML(packDir string) (map[string]any, error) {
	path := filepath.Join(packDir, "pack.toml")
	content, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return nil, fail.Errorf("pack.toml not found at %s.", path)
	} else if err != nil {
		return nil, err
	}
	data, err := DecodeTOML(content)
	if err != nil {
		return nil, fail.Wrapf(err, "Could not parse %s: %v", path, err)
	}
	return data, nil
}

// LoaderOf returns the pack's loader and its version, e.g. ("fabric", "0.18.4").
func LoaderOf(packTOML map[string]any) (string, string) {
	versions := Table(packTOML["versions"])
	for _, loader := range LoaderLabels {
		if version := pycompat.Strip(pycompat.Or(versions[loader.Key], "")); version != "" {
			return loader.Key, version
		}
	}
	return "", ""
}

// LoaderLabel is the display name of a loader key.
func LoaderLabel(loader string) string {
	for _, l := range LoaderLabels {
		if l.Key == loader {
			return l.Label
		}
	}
	if loader == "" {
		return "Unknown loader"
	}
	return pycompat.Capitalize(loader)
}

// editFile applies TOML edits to a file, keeping its formatting.
func editFile(path string, edits []Edit) error {
	content, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	edited, err := EditTOML(string(content), edits)
	if err != nil {
		return fail.Wrapf(err, "Couldn't edit %s: %v", path, err)
	}
	return files.WriteAtomic(path, []byte(edited))
}

// SetPackVersion writes the version to pack.toml.
func SetPackVersion(packDir, version string) error {
	return editFile(filepath.Join(packDir, "pack.toml"), []Edit{{"", "version", Quote(version)}})
}

// SetDisabled marks a metafile disabled ("client(disabled)") or active again.
func SetDisabled(packDir string, mod Mod, disabled bool) error {
	side := mod.Side()
	if disabled {
		side += "(disabled)"
	}
	return editFile(filepath.Join(packDir, filepath.FromSlash(mod.Rel)), []Edit{{"", "side", Quote(side)}})
}

// ApplyModrinthVersion points a Modrinth metafile at another version of the
// same project. It returns false when the version has no usable primary file.
func ApplyModrinthVersion(packDir string, mod Mod, version map[string]any) (bool, error) {
	fileList, _ := version["files"].([]any)
	var primary map[string]any
	for _, f := range fileList {
		if file, ok := f.(map[string]any); ok && pycompat.Truthy(file["primary"]) {
			primary = file
			break
		}
	}
	if primary == nil && len(fileList) > 0 {
		primary, _ = fileList[0].(map[string]any)
	}
	if primary == nil {
		return false, nil
	}
	hashes := Table(primary["hashes"])
	format := ""
	for _, candidate := range []string{"sha512", "sha1"} {
		if _, ok := hashes[candidate]; ok {
			format = candidate
			break
		}
	}
	url, filename := pycompat.Or(primary["url"], ""), pycompat.Or(primary["filename"], "")
	if format == "" || url == "" || filename == "" {
		return false, nil
	}
	edits := []Edit{
		{"", "filename", Quote(filename)},
		{"download", "url", Quote(url)},
		{"download", "hash-format", Quote(format)},
		{"download", "hash", Quote(pycompat.Str(hashes[format]))},
		{"update.modrinth", "version", Quote(pycompat.Str(version["id"]))},
	}
	return true, editFile(filepath.Join(packDir, filepath.FromSlash(mod.Rel)), edits)
}

// Restore writes a metafile's earlier bytes back.
func Restore(packDir, rel string, content []byte) error {
	return files.WriteAtomic(filepath.Join(packDir, filepath.FromSlash(rel)), content)
}

// IndexEntry is one [[files]] entry of index.toml.
type IndexEntry struct {
	File     string
	Metafile bool
}

// IndexEntries lists every file packwiz ships (ignores applied).
func IndexEntries(packDir string) ([]IndexEntry, error) {
	packTOML, err := ReadPackTOML(packDir)
	if err != nil {
		return nil, err
	}
	name := pycompat.Or(Table(packTOML["index"])["file"], "index.toml")
	content, err := os.ReadFile(filepath.Join(packDir, filepath.FromSlash(name)))
	if err != nil {
		return nil, err
	}
	data, err := DecodeTOML(content)
	if err != nil {
		return nil, fail.Wrapf(err, "Could not parse %s: %v", name, err)
	}
	var entries []IndexEntry
	list, _ := data["files"].([]map[string]any)
	for _, entry := range list {
		entries = append(entries, IndexEntry{File: pycompat.Str(entry["file"]), Metafile: pycompat.Truthy(entry["metafile"])})
	}
	return entries, nil
}

// IndexHash is the index hash recorded in pack.toml.
func IndexHash(packDir string) (string, error) {
	packTOML, err := ReadPackTOML(packDir)
	if err != nil {
		return "", err
	}
	return pycompat.Or(Table(packTOML["index"])["hash"], ""), nil
}

// WriteBCCVersion sets modpackVersion in a BetterCompatibilityChecker config.
// It returns false when the file is missing or already up to date.
func WriteBCCVersion(path, version string) (bool, error) {
	text, err := pycompat.ReadText(path)
	if errors.Is(err, fs.ErrNotExist) {
		return false, nil
	} else if err != nil {
		return false, err
	}
	value, err := pycompat.Loads([]byte(text))
	if err != nil {
		return false, fail.Wrapf(err, "%s isn't valid JSON: %v", path, err)
	}
	data, ok := value.(pycompat.Object)
	if !ok {
		return false, fail.Errorf("%s should contain a JSON object.", path)
	}
	if current, _ := data.Get("modpackVersion"); current == version {
		return false, nil
	}
	data.Set("modpackVersion", version)
	encoded, err := pycompat.Dumps(data, pycompat.NoIndent, true)
	if err != nil {
		return false, err
	}
	return true, pycompat.WriteText(path, string(encoded))
}

// CrashAssistantModlist is the JSON list of jar filenames a client install contains.
func CrashAssistantModlist(mods []Mod) string {
	names := []string{}
	for _, mod := range mods {
		if mod.Category() == "mods" && mod.Filename() != "" && mod.InstallsOn("client") {
			names = append(names, mod.Filename())
		}
	}
	pycompat.SortLower(names)
	encoded, _ := pycompat.Dumps(names, 2, true)
	return string(encoded)
}

// ModlistMarkdown is the modlist.md document: active and inactive mods.
func ModlistMarkdown(mods []Mod, sideTags bool) string {
	label := func(mod Mod) string {
		if sideTags {
			return mod.DisplayName() + " [" + pycompat.Capitalize(mod.Side()) + "]"
		}
		return mod.DisplayName()
	}
	var active, inactive []string
	for _, mod := range mods {
		if mod.Category() != "mods" {
			continue
		}
		if mod.Disabled() {
			inactive = append(inactive, "- "+label(mod))
		} else {
			active = append(active, "- "+label(mod))
		}
	}
	if len(active) == 0 {
		active = []string{"- None"}
	}
	if len(inactive) == 0 {
		inactive = []string{"- None"}
	}
	lines := append([]string{"# Mod List", "", "## Active Mods"}, active...)
	lines = append(append(lines, "", "## Inactive Mods"), inactive...)
	return strings.Join(append(lines, ""), "\n")
}

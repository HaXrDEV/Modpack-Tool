package pack

import (
	"errors"
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"slices"
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

// ModrinthFile is one file of a Modrinth version.
type ModrinthFile struct {
	URL, Filename string
	Primary       bool
	Hashes        map[string]string
}

// PrimaryFile is the file of a Modrinth version that a metafile points at, and
// its hash format. ok is false when the version has no usable primary file.
func PrimaryFile(versionFiles []ModrinthFile) (primary ModrinthFile, format string, ok bool) {
	if len(versionFiles) == 0 {
		return ModrinthFile{}, "", false
	}
	primary = versionFiles[0]
	for _, file := range versionFiles {
		if file.Primary {
			primary = file
			break
		}
	}
	for _, candidate := range []string{"sha512", "sha1"} {
		if _, ok := primary.Hashes[candidate]; ok {
			format = candidate
			break
		}
	}
	return primary, format, format != "" && primary.URL != "" && primary.Filename != ""
}

// ApplyModrinthVersion points a Modrinth metafile at another version of the
// same project. It returns false when the version has no usable primary file.
func ApplyModrinthVersion(packDir string, mod Mod, versionID string, versionFiles []ModrinthFile) (bool, error) {
	primary, format, ok := PrimaryFile(versionFiles)
	if !ok {
		return false, nil
	}
	edits := []Edit{
		{"", "filename", Quote(primary.Filename)},
		{"download", "url", Quote(primary.URL)},
		{"download", "hash-format", Quote(format)},
		{"download", "hash", Quote(primary.Hashes[format])},
		{"update.modrinth", "version", Quote(versionID)},
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

// Bundled lists what a pack ships itself instead of downloading, such as an
// edited shader pack: every .jar directly in mods/, and every .zip or folder
// directly in the other category folders, as paths like
// "shaderpacks/Complementary". It follows the index, so ignored files are
// left out.
func Bundled(entries []IndexEntry) []string {
	var found []string
	for _, entry := range entries {
		category, rest, ok := strings.Cut(entry.File, "/")
		if entry.Metafile || !ok || !slices.Contains(Categories, category) {
			continue
		}
		name, _, folder := strings.Cut(rest, "/")
		switch ext := strings.ToLower(path.Ext(name)); {
		case category == "mods" && (folder || ext != ".jar"):
			continue
		case category != "mods" && !folder && ext != ".zip":
			continue
		}
		if item := category + "/" + name; !slices.Contains(found, item) {
			found = append(found, item)
		}
	}
	return found
}

// IndexHash is the index hash recorded in pack.toml.
func IndexHash(packDir string) (string, error) {
	packTOML, err := ReadPackTOML(packDir)
	if err != nil {
		return "", err
	}
	return pycompat.Or(Table(packTOML["index"])["hash"], ""), nil
}

// ReadJSONObject reads a file that holds a JSON object. A missing file gives
// an error that matches fs.ErrNotExist.
func ReadJSONObject(path string) (pycompat.Object, error) {
	text, err := pycompat.ReadText(path)
	if err != nil {
		return nil, err
	}
	value, err := pycompat.Loads([]byte(text))
	if err != nil {
		return nil, fail.Wrapf(err, "%s isn't valid JSON: %v", path, err)
	}
	data, ok := value.(pycompat.Object)
	if !ok {
		return nil, fail.Errorf("%s should contain a JSON object.", path)
	}
	return data, nil
}

// WriteBCCVersion sets modpackVersion in a BetterCompatibilityChecker config.
// It returns false when the file is missing or already up to date.
func WriteBCCVersion(path, version string) (bool, error) {
	data, err := ReadJSONObject(path)
	if errors.Is(err, fs.ErrNotExist) {
		return false, nil
	} else if err != nil {
		return false, err
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

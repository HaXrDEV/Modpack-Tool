package export

import (
	"archive/zip"
	"context"
	"crypto/sha1"
	"crypto/sha512"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"io/fs"
	"maps"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"slices"
	"strings"
	"time"

	"github.com/HaXrDEV/Modpack-Tool/internal/files"
	"github.com/HaXrDEV/Modpack-Tool/internal/pack"
	"github.com/HaXrDEV/Modpack-Tool/internal/platform"
	"github.com/HaXrDEV/Modpack-Tool/internal/project"
	"github.com/HaXrDEV/Modpack-Tool/internal/pycompat"
	"github.com/HaXrDEV/Modpack-Tool/internal/ui"
)

// Hosts a .mrpack may download from; anything else must be bundled.
var modrinthAllowedHosts = map[string]bool{"cdn.modrinth.com": true, "github.com": true,
	"raw.githubusercontent.com": true, "gitlab.com": true}

var mrpackLoaderKeys = map[string]string{"fabric": "fabric-loader", "quilt": "quilt-loader", "forge": "forge", "neoforge": "neoforge"}

// Contents is the pack as packwiz indexes it: active metafiles and overrides.
type Contents struct {
	PackDir   string
	Author    string
	Mods      []pack.Mod
	Overrides []string
}

// ReadContents reads the index.
func ReadContents(p *project.Project) (*Contents, error) {
	packTOML, err := pack.ReadPackTOML(p.PackDir())
	if err != nil {
		return nil, err
	}
	c := &Contents{PackDir: p.PackDir(), Author: pycompat.Or(packTOML["author"], "")}
	entries, err := pack.IndexEntries(p.PackDir())
	if err != nil {
		return nil, err
	}
	for _, entry := range entries {
		if strings.Contains("/"+entry.File+"/", "/../") {
			return nil, fmt.Errorf("index.toml lists a file outside the pack: %s", entry.File)
		}
		if !entry.Metafile {
			c.Overrides = append(c.Overrides, entry.File)
			continue
		}
		content, err := os.ReadFile(filepath.Join(p.PackDir(), filepath.FromSlash(entry.File)))
		if err != nil {
			return nil, err
		}
		data, err := pack.DecodeTOML(content)
		if err != nil {
			return nil, fmt.Errorf("could not parse %s: %w", entry.File, err)
		}
		if mod := (pack.Mod{Rel: entry.File, Data: data}); !mod.Disabled() {
			c.Mods = append(c.Mods, mod)
		}
	}
	return c, nil
}

// ForSide returns the files a "client" or "server" install contains.
func (c *Contents) ForSide(side string) []pack.Mod {
	var result []pack.Mod
	for _, mod := range c.Mods {
		if mod.InstallsOn(side) {
			result = append(result, mod)
		}
	}
	return result
}

// zipWriter adds files to a zip archive.
type zipWriter struct{ *zip.Writer }

func (z zipWriter) addFile(name, source string) error {
	info, err := os.Stat(source)
	if err != nil {
		return err
	}
	header := &zip.FileHeader{Name: name, Method: zip.Deflate, Modified: info.ModTime()}
	header.SetMode(info.Mode())
	w, err := z.CreateHeader(header)
	if err != nil {
		return err
	}
	f, err := os.Open(source)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = io.Copy(w, f)
	return err
}

func (z zipWriter) addBytes(name string, data []byte) error {
	header := &zip.FileHeader{Name: name, Method: zip.Deflate, Modified: time.Now()}
	header.SetMode(0o644)
	w, err := z.CreateHeader(header)
	if err != nil {
		return err
	}
	_, err = w.Write(data)
	return err
}

func (c *Contents) writeOverrides(z zipWriter) error {
	for _, rel := range c.Overrides {
		if err := z.addFile("overrides/"+rel, filepath.Join(c.PackDir, filepath.FromSlash(rel))); err != nil {
			return err
		}
	}
	return nil
}

// installPath is where packwiz installs a file, relative to the instance.
func installPath(mod pack.Mod) string {
	filename := mod.Filename()
	if filename == "" {
		if u, err := url.Parse(mod.DownloadURL()); err == nil {
			filename = path.Base(u.Path)
		}
	}
	if mod.Folder() != "." {
		return mod.Folder() + "/" + filename
	}
	return filename
}

// writeZip writes an archive to path via a temporary file, so a failed build
// never leaves a broken pack behind.
func writeZip(path string, build func(zipWriter) error) error {
	return files.WriteAtomicFrom(path, func(f *os.File) error {
		archive := zip.NewWriter(f)
		if err := build(zipWriter{archive}); err != nil {
			archive.Close()
			return err
		}
		return archive.Close()
	})
}

func dumps(value any) []byte {
	encoded, err := pycompat.Dumps(value, 2, true)
	if err != nil {
		panic(err) // Only plain data is encoded here.
	}
	return encoded
}

////////////////////////////////////////////////////////////
// CurseForge

type fingerprintEntry struct {
	Fingerprint *uint32 `json:"fingerprint,omitempty"`
	Match       []int64 `json:"match,omitempty"`
}

// ResolveOnCurseForge returns {metafile path: match} for the non-CurseForge
// files that exist on CurseForge. Matching uses CurseForge's murmur2
// fingerprint of the exact file. Results are cached by file hash, so only new
// files have to be downloaded and fingerprinted.
func ResolveOnCurseForge(ctx context.Context, mods []pack.Mod, store *Store) (map[string]platform.Match, error) {
	cachePath := filepath.Join(store.Dir, "curseforge-fingerprints.json")
	cache := map[string]*fingerprintEntry{}
	if data, err := os.ReadFile(cachePath); err == nil {
		if json.Unmarshal(data, &cache) != nil {
			cache = map[string]*fingerprintEntry{}
		}
	}
	key := func(mod pack.Mod) string {
		format, value := mod.Hash()
		return format + ":" + value
	}
	matches := map[string]platform.Match{}
	var unknown []pack.Mod
	for _, mod := range mods {
		if entry := cache[key(mod)]; entry != nil && len(entry.Match) == 2 {
			matches[mod.Rel] = platform.Match{ProjectID: entry.Match[0], FileID: entry.Match[1]}
		} else {
			unknown = append(unknown, mod)
		}
	}
	if len(unknown) == 0 {
		return matches, nil
	}
	var needBytes []pack.Mod
	for _, mod := range unknown {
		if entry := cache[key(mod)]; entry == nil || entry.Fingerprint == nil {
			needBytes = append(needBytes, mod)
		}
	}
	if len(needBytes) > 0 {
		fetched, err := store.Fetch(ctx, needBytes)
		if err != nil {
			return nil, err
		}
		step := store.Session.Step(fmt.Sprintf("Fingerprinting %d file%s for CurseForge", len(needBytes), ui.Plural(len(needBytes))))
		for i, mod := range needBytes {
			data, err := os.ReadFile(fetched[mod.Rel])
			if err != nil {
				step.Fail(err)
				return nil, err
			}
			fingerprint := platform.Murmur2(data)
			if cache[key(mod)] == nil {
				cache[key(mod)] = &fingerprintEntry{}
			}
			cache[key(mod)].Fingerprint = &fingerprint
			step.Progress(i+1, len(needBytes))
		}
		step.Done("")
	}
	var fingerprints []uint32
	for _, mod := range unknown {
		fingerprints = append(fingerprints, *cache[key(mod)].Fingerprint)
	}
	found, err := store.API.CurseForgeFingerprints(ctx, fingerprints)
	if err != nil {
		return nil, err
	}
	for _, mod := range unknown {
		if match, ok := found[*cache[key(mod)].Fingerprint]; ok {
			cache[key(mod)].Match = []int64{match.ProjectID, match.FileID}
			matches[mod.Rel] = match
		}
	}
	encoded, err := pycompat.Dumps(cache, 1, true)
	if err != nil {
		return nil, err
	}
	return matches, files.WriteAtomic(cachePath, encoded)
}

type cfLoader struct {
	ID      string `json:"id"`
	Primary bool   `json:"primary"`
}

type cfManifest struct {
	Minecraft struct {
		Version    string     `json:"version"`
		ModLoaders []cfLoader `json:"modLoaders"`
	} `json:"minecraft"`
	ManifestType    string   `json:"manifestType"`
	ManifestVersion int      `json:"manifestVersion"`
	Name            string   `json:"name"`
	Version         string   `json:"version"`
	Author          string   `json:"author"`
	Files           []cfFile `json:"files"`
	Overrides       string   `json:"overrides"`
}

type cfFile struct {
	ProjectID int64 `json:"projectID"`
	FileID    int64 `json:"fileID"`
	Required  bool  `json:"required"`
}

// BuildCurseForge writes the CurseForge modpack zip; it returns the bundled
// files and a summary.
func BuildCurseForge(ctx context.Context, p *project.Project, contents *Contents, store *Store, output string) ([]pack.Mod, string, error) {
	type listedMod struct {
		mod   pack.Mod
		match platform.Match
	}
	var listed []listedMod
	var others []pack.Mod
	for _, mod := range contents.ForSide("client") {
		if mod.CurseForge() != nil {
			listed = append(listed, listedMod{mod, platform.Match{ProjectID: mod.CurseForgeProject(), FileID: mod.CurseForgeFile()}})
		} else {
			others = append(others, mod)
		}
	}
	matches := map[string]platform.Match{}
	if len(others) > 0 {
		var err error
		if matches, err = ResolveOnCurseForge(ctx, others, store); err != nil {
			return nil, "", err
		}
	}
	var bundled []pack.Mod
	for _, mod := range others {
		if match, ok := matches[mod.Rel]; ok {
			listed = append(listed, listedMod{mod, match})
		} else {
			bundled = append(bundled, mod)
		}
	}
	bundledFiles := map[string]string{}
	if len(bundled) > 0 {
		var err error
		if bundledFiles, err = store.Fetch(ctx, bundled); err != nil {
			return nil, "", err
		}
	}
	var manifest cfManifest
	manifest.Minecraft.Version = p.Minecraft
	manifest.Minecraft.ModLoaders = []cfLoader{}
	if p.Loader != "" {
		manifest.Minecraft.ModLoaders = []cfLoader{{p.Loader + "-" + p.LoaderVersion, true}}
	}
	manifest.ManifestType, manifest.ManifestVersion = "minecraftModpack", 1
	manifest.Name, manifest.Version, manifest.Author = p.Name, p.Version, contents.Author
	manifest.Overrides = "overrides"
	pycompat.SortLowerBy(listed, func(l listedMod) string { return l.mod.Rel })
	manifest.Files = []cfFile{}
	for _, l := range listed {
		required := !(l.mod.Optional() && !l.mod.OptionalDefault())
		manifest.Files = append(manifest.Files, cfFile{l.match.ProjectID, l.match.FileID, required})
	}
	err := writeZip(output, func(z zipWriter) error {
		if err := z.addBytes("manifest.json", dumps(manifest)); err != nil {
			return err
		}
		if err := contents.writeOverrides(z); err != nil {
			return err
		}
		for _, mod := range bundled {
			if err := z.addFile("overrides/"+installPath(mod), bundledFiles[mod.Rel]); err != nil {
				return err
			}
		}
		return nil
	})
	return bundled, fmt.Sprintf("%d from CurseForge, %d bundled", len(listed), len(bundled)), err
}

////////////////////////////////////////////////////////////
// Modrinth

type mrEnv struct {
	Client string `json:"client"`
	Server string `json:"server"`
}

type mrHashes struct {
	SHA1   string `json:"sha1"`
	SHA512 string `json:"sha512"`
}

type mrFile struct {
	Path      string   `json:"path"`
	Hashes    mrHashes `json:"hashes"`
	Env       mrEnv    `json:"env"`
	Downloads []string `json:"downloads"`
	FileSize  int64    `json:"fileSize"`
}

type mrIndex struct {
	FormatVersion int             `json:"formatVersion"`
	Game          string          `json:"game"`
	VersionID     string          `json:"versionId"`
	Name          string          `json:"name"`
	Files         []mrFile        `json:"files"`
	Dependencies  pycompat.Object `json:"dependencies"`
}

func env(mod pack.Mod) mrEnv {
	level := "required"
	if mod.Optional() {
		level = "optional"
	}
	e := mrEnv{level, level}
	if mod.Side() == "server" {
		e.Client = "unsupported"
	}
	if mod.Side() == "client" {
		e.Server = "unsupported"
	}
	return e
}

func hashFile(path string) (string, string, int64, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", "", 0, err
	}
	defer f.Close()
	h1, h512 := sha1.New(), sha512.New()
	size, err := io.Copy(io.MultiWriter(h1, h512), f)
	return hex.EncodeToString(h1.Sum(nil)), hex.EncodeToString(h512.Sum(nil)), size, err
}

// BuildModrinth writes the Modrinth .mrpack; it returns the bundled files and
// a summary.
func BuildModrinth(ctx context.Context, p *project.Project, contents *Contents, store *Store, output string) ([]pack.Mod, string, error) {
	var mods []pack.Mod
	var versionIDs []string
	for _, mod := range contents.Mods {
		if mod.InstallsOn("client") || mod.InstallsOn("server") {
			mods = append(mods, mod)
			if mod.Modrinth() != nil {
				versionIDs = append(versionIDs, mod.ModrinthVersion())
			}
		}
	}
	versions := map[string]platform.Version{}
	if len(versionIDs) > 0 {
		var err error
		if versions, err = store.API.ModrinthVersions(ctx, versionIDs); err != nil {
			return nil, "", err
		}
	}
	type entry struct {
		mod          pack.Mod
		url          string
		sha1, sha512 string
		size         int64
	}
	var entries []entry
	var bundled, needBytes []pack.Mod
	for _, mod := range mods {
		link := mod.DownloadURL()
		u, err := url.Parse(link)
		if link == "" || err != nil || !modrinthAllowedHosts[strings.ToLower(u.Hostname())] {
			bundled = append(bundled, mod)
			continue
		}
		var info *platform.File
		if mod.Modrinth() != nil {
			format, value := mod.Hash()
			version := versions[mod.ModrinthVersion()]
			for i, f := range version.Files {
				if f.Hashes[format] == value || f.Filename == mod.Filename() {
					info = &version.Files[i]
					break
				}
			}
		}
		if info != nil && info.Hashes["sha1"] != "" && info.Hashes["sha512"] != "" && info.Size > 0 {
			entries = append(entries, entry{mod, link, info.Hashes["sha1"], info.Hashes["sha512"], info.Size})
		} else {
			needBytes = append(needBytes, mod)
		}
	}
	fetched := map[string]string{}
	if len(needBytes)+len(bundled) > 0 {
		var err error
		if fetched, err = store.Fetch(ctx, slices.Concat(needBytes, bundled)); err != nil {
			return nil, "", err
		}
	}
	for _, mod := range needBytes {
		h1, h512, size, err := hashFile(fetched[mod.Rel])
		if err != nil {
			return nil, "", err
		}
		entries = append(entries, entry{mod, mod.DownloadURL(), h1, h512, size})
	}
	index := mrIndex{FormatVersion: 1, Game: "minecraft", VersionID: p.Version, Name: p.Name, Files: []mrFile{},
		Dependencies: pycompat.Object{{Key: "minecraft", Value: p.Minecraft}}}
	if p.Loader != "" {
		key := mrpackLoaderKeys[p.Loader]
		if key == "" {
			key = p.Loader
		}
		index.Dependencies.Set(key, p.LoaderVersion)
	}
	pycompat.SortLowerBy(entries, func(e entry) string { return e.mod.Rel })
	for _, e := range entries {
		index.Files = append(index.Files, mrFile{installPath(e.mod), mrHashes{e.sha1, e.sha512}, env(e.mod), []string{e.url}, e.size})
	}
	err := writeZip(output, func(z zipWriter) error {
		if err := z.addBytes("modrinth.index.json", dumps(index)); err != nil {
			return err
		}
		if err := contents.writeOverrides(z); err != nil {
			return err
		}
		for _, mod := range bundled {
			folder := map[string]string{"client": "client-overrides", "server": "server-overrides"}[mod.Side()]
			if folder == "" {
				folder = "overrides"
			}
			if err := z.addFile(folder+"/"+installPath(mod), fetched[mod.Rel]); err != nil {
				return err
			}
		}
		return nil
	})
	return bundled, fmt.Sprintf("%d from Modrinth, %d bundled", len(entries), len(bundled)), err
}

////////////////////////////////////////////////////////////
// Server pack

func excluded(mod pack.Mod, exclude []string) bool {
	names := map[string]bool{strings.ToLower(mod.Slug()): true, strings.ToLower(mod.Name()): true,
		strings.ToLower(mod.DisplayName()): true, strings.ToLower(mod.Filename()): true}
	for _, entry := range exclude {
		if names[strings.ToLower(pycompat.Strip(entry))] {
			return true
		}
	}
	return false
}

// BuildServer writes the server pack: the template folder plus every
// server-side mod jar.
func BuildServer(ctx context.Context, p *project.Project, contents *Contents, store *Store, output string) ([]pack.Mod, string, error) {
	template := p.ServerTemplateDir()
	var mods, skipped []pack.Mod
	for _, mod := range contents.ForSide("server") {
		if mod.Category() != "mods" {
			continue
		}
		if excluded(mod, p.Settings.ServerExclude) {
			skipped = append(skipped, mod)
		} else {
			mods = append(mods, mod)
		}
	}
	fetched, err := store.Fetch(ctx, mods)
	if err != nil {
		return nil, "", err
	}
	entries := map[string]string{} // Archive name -> source file.
	templateJars := 0
	if files.IsDir(template) {
		err := filepath.WalkDir(template, func(source string, entry fs.DirEntry, err error) error {
			if err != nil || !entry.Type().IsRegular() {
				return err
			}
			rel, _ := filepath.Rel(template, source)
			rel = filepath.ToSlash(rel)
			entries[rel] = source
			if path.Dir(rel) == "mods" {
				templateJars++
			}
			return nil
		})
		if err != nil {
			return nil, "", err
		}
	} else {
		store.Session.Warn(fmt.Sprintf("No server template folder at %s; the server pack will only contain mods.", template))
	}
	for _, mod := range mods {
		name := "mods/" + mod.Filename()
		if _, ok := entries[name]; !ok { // A jar placed in the template wins (e.g. a patched build).
			entries[name] = fetched[mod.Rel]
		}
	}
	err = writeZip(output, func(z zipWriter) error {
		for _, name := range pycompat.SortedLower(slices.Collect(maps.Keys(entries))) {
			if err := z.addFile(name, entries[name]); err != nil {
				return err
			}
		}
		return nil
	})
	summary := fmt.Sprintf("%d mods", len(mods))
	if templateJars > 0 {
		summary += fmt.Sprintf(" + %d from %s", templateJars, filepath.Base(template))
	}
	if len(skipped) > 0 {
		summary += ", left out: " + strings.Join(pack.Names(skipped), ", ")
	}
	return nil, summary, err
}

////////////////////////////////////////////////////////////
// All of it

func sourceLink(mod pack.Mod) string {
	switch {
	case mod.Modrinth() != nil:
		return "https://modrinth.com/project/" + pycompat.Str(mod.Modrinth()["mod-id"])
	case mod.CurseForge() != nil:
		return "https://www.curseforge.com/projects/" + pycompat.Str(mod.CurseForge()["project-id"])
	case mod.GitHub() != nil:
		return "https://github.com/" + pycompat.Str(mod.GitHub()["slug"])
	case mod.DownloadURL() != "":
		return mod.DownloadURL()
	}
	return "(link unknown)"
}

// Names are the export file names by kind.
func Names(p *project.Project) map[string]string {
	return map[string]string{
		"curseforge": p.Name + "-" + p.Version + ".zip",
		"modrinth":   p.Name + "-" + p.Version + ".mrpack",
		"server":     p.Name + "-Server-" + p.Version + ".zip",
	}
}

var labels = map[string]string{"curseforge": "CurseForge pack", "modrinth": "Modrinth pack", "server": "Server pack"}

type builder func(context.Context, *project.Project, *Contents, *Store, string) ([]pack.Mod, string, error)

// Export builds the requested packs into Export/ and returns their paths,
// plus Export/bundled_links.md listing every bundled file and its source.
func Export(ctx context.Context, p *project.Project, kinds []string, store *Store) ([]string, error) {
	builders := map[string]builder{"curseforge": BuildCurseForge, "modrinth": BuildModrinth, "server": BuildServer}
	var contents *Contents
	if len(kinds) > 0 {
		var err error
		if contents, err = ReadContents(p); err != nil {
			return nil, err
		}
	}
	var written []string
	type bundle struct {
		label string
		mods  []pack.Mod
	}
	var bundles []bundle
	for _, kind := range kinds {
		step := store.Session.Step("Building the " + labels[kind])
		output := filepath.Join(p.ExportDir(), Names(p)[kind])
		bundled, summary, err := builders[kind](ctx, p, contents, store, output)
		if err != nil {
			step.Fail(err)
			return nil, err
		}
		written = append(written, output)
		if len(bundled) > 0 {
			bundles = append(bundles, bundle{labels[kind], bundled})
		}
		step.Done(fmt.Sprintf("%s (%s)", filepath.Base(output), summary))
	}
	report := filepath.Join(p.ExportDir(), "bundled_links.md")
	lines := []string{fmt.Sprintf("# Bundled files in %s %s", p.Name, p.Version), ""}
	if len(bundles) > 0 {
		lines = append(lines, "These files are included in the packs instead of being downloaded from the platform.",
			"Check that each license allows redistribution.", "")
		for _, b := range bundles {
			lines = append(lines, "## "+b.label, "")
			for _, mod := range b.mods {
				lines = append(lines, fmt.Sprintf("- [%s](%s): `%s`", mod.DisplayName(), sourceLink(mod), mod.Filename()))
			}
			lines = append(lines, "")
		}
	} else {
		lines = append(lines, "No files are bundled; everything is downloaded from CurseForge or Modrinth.", "")
	}
	if len(bundles) > 0 || files.Exists(report) { // An old list must never describe a new release.
		if err := pycompat.WriteText(report, strings.Join(lines, "\n")); err != nil {
			return nil, err
		}
	}
	if len(bundles) > 0 {
		store.Session.Info("Bundled files and their sources: " + p.Rel(report))
	}
	return written, nil
}

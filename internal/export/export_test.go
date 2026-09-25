package export

import (
	"archive/zip"
	"context"
	"crypto/sha1"
	"crypto/sha256"
	"crypto/sha512"
	"encoding/hex"
	"encoding/json"
	"errors"
	"hash"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/HaXrDEV/Modpack-Tool/internal/pack"
	"github.com/HaXrDEV/Modpack-Tool/internal/platform"
	"github.com/HaXrDEV/Modpack-Tool/internal/project"
	"github.com/HaXrDEV/Modpack-Tool/internal/testutil"
	"github.com/HaXrDEV/Modpack-Tool/internal/ui/uitest"
)

var jar = map[string][]byte{}

func init() {
	for _, name := range []string{"sodium", "cfmod", "ghmod", "odd", "server", "fa"} {
		jar[name] = []byte(name + " jar bytes")
	}
}

func sha(name string, h hash.Hash) string {
	h.Write(jar[name])
	return hex.EncodeToString(h.Sum(nil))
}

func modrinthMeta(name, filename, side string) string {
	return "name = \"" + strings.ToUpper(name[:1]) + name[1:] + "\"\nfilename = \"" + filename + "\"\nside = \"" + side + "\"\n\n[download]\n" +
		"url = \"https://cdn.modrinth.com/data/P" + name + "/versions/V" + name + "/" + filename + "\"\n" +
		"hash-format = \"sha512\"\nhash = \"" + sha(name, sha512.New()) + "\"\n\n[update]\n[update.modrinth]\n" +
		"mod-id = \"P" + name + "\"\nversion = \"V" + name + "\"\n"
}

type fixture struct {
	project *project.Project
	api     *platform.Fake
	session *uitest.Session
	store   *Store
	cache   string
}

// exportProject is the Python tests' export_project fixture.
func exportProject(t *testing.T, answers ...string) *fixture {
	t.Helper()
	dir := t.TempDir()
	root := filepath.Join(dir, "Pack")
	pw := filepath.Join(root, "Packwiz")
	testutil.Write(t, filepath.Join(pw, "pack.toml"), "name = \"Pack\"\nauthor = \"me\"\nversion = \"2.0.0\"\n[index]\nfile = \"index.toml\"\n"+
		"hash-format = \"sha256\"\nhash = \"h\"\n[versions]\nfabric = \"0.18.4\"\nminecraft = \"1.21.11\"\n")
	testutil.Write(t, filepath.Join(pw, "mods", "sodium.pw.toml"), modrinthMeta("sodium", "sodium.jar", "client"))
	testutil.Write(t, filepath.Join(pw, "mods", "cfmod.pw.toml"), "name = \"CF Mod\"\nfilename = \"cfmod.jar\"\nside = \"both\"\n[download]\nhash-format = \"sha1\"\n"+
		"hash = \""+sha("cfmod", sha1.New())+"\"\nmode = \"metadata:curseforge\"\n[update]\n[update.curseforge]\nfile-id = 11\nproject-id = 22\n")
	testutil.Write(t, filepath.Join(pw, "mods", "ghmod.pw.toml"), "name = \"GH Mod\"\nfilename = \"ghmod.jar\"\nside = \"both\"\n[download]\n"+
		"url = \"https://github.com/me/gh/releases/download/v1/ghmod.jar\"\nhash-format = \"sha256\"\n"+
		"hash = \""+sha("ghmod", sha256.New())+"\"\n[update]\n[update.github]\nslug = \"me/gh\"\n")
	testutil.Write(t, filepath.Join(pw, "mods", "odd.pw.toml"), "name = \"Odd Host\"\nfilename = \"odd.jar\"\nside = \"both\"\n[download]\nurl = \"https://example.com/odd.jar\"\n"+
		"hash-format = \"sha256\"\nhash = \""+sha("odd", sha256.New())+"\"\n")
	testutil.Write(t, filepath.Join(pw, "mods", "server.pw.toml"), modrinthMeta("server", "server.jar", "server"))
	testutil.Write(t, filepath.Join(pw, "mods", "off.pw.toml"), modrinthMeta("sodium", "off.jar", "both(disabled)"))
	testutil.Write(t, filepath.Join(pw, "resourcepacks", "fa.pw.toml"), modrinthMeta("fa", "fa.zip", "client"))
	testutil.Write(t, filepath.Join(pw, "resourcepacks", "Bundled.zip"), "zip bytes")
	testutil.Write(t, filepath.Join(pw, "config", "a.json"), "{}")
	testutil.Write(t, filepath.Join(pw, "mmc-export.toml"), "junk") // Not in the index, so never shipped.
	index := []string{`hash-format = "sha256"`}
	for _, rel := range []string{"mods/sodium.pw.toml", "mods/cfmod.pw.toml", "mods/ghmod.pw.toml", "mods/odd.pw.toml",
		"mods/server.pw.toml", "mods/off.pw.toml", "resourcepacks/fa.pw.toml"} {
		index = append(index, "[[files]]\nfile = \""+rel+"\"\nhash = \"x\"\nmetafile = true")
	}
	for _, rel := range []string{"resourcepacks/Bundled.zip", "config/a.json"} {
		index = append(index, "[[files]]\nfile = \""+rel+"\"\nhash = \"x\"")
	}
	testutil.Write(t, filepath.Join(pw, "index.toml"), strings.Join(index, "\n\n")+"\n")
	testutil.Write(t, filepath.Join(root, "Server Pack", "start.bat"), "java -jar server.jar")
	testutil.Write(t, filepath.Join(root, "Server Pack", "mods", "patched.jar"), "patched")

	api := &platform.Fake{
		URLs: map[string][]byte{
			"https://cdn.modrinth.com/data/Psodium/versions/Vsodium/sodium.jar": jar["sodium"],
			"https://cdn.modrinth.com/data/Pserver/versions/Vserver/server.jar": jar["server"],
			"https://cdn.modrinth.com/data/Pfa/versions/Vfa/fa.zip":             jar["fa"],
			"https://github.com/me/gh/releases/download/v1/ghmod.jar":           jar["ghmod"],
			"https://example.com/odd.jar":                                       jar["odd"],
			"https://edge.forgecdn.net/cfmod.jar":                               jar["cfmod"],
		},
		Files:    map[int64]platform.CFFile{11: {ID: 11, DownloadURL: "https://edge.forgecdn.net/cfmod.jar"}},
		Versions: map[string]platform.Version{},
		// Sodium and the resource pack also exist on CurseForge; nothing else does.
		Fingerprints: map[uint32]platform.Match{platform.Murmur2(jar["sodium"]): {ProjectID: 100, FileID: 1000}, platform.Murmur2(jar["fa"]): {ProjectID: 200, FileID: 2000}},
	}
	for _, name := range []string{"sodium", "server"} {
		api.Versions["V"+name] = platform.Version{ID: "V" + name, Files: []platform.File{{Filename: name + ".jar",
			Size: int64(len(jar[name])), Hashes: map[string]string{"sha1": sha(name, sha1.New()), "sha512": sha(name, sha512.New())}}}}
	}
	p, _, err := project.Open(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	session := uitest.New(answers...)
	cache := filepath.Join(dir, "cache")
	return &fixture{project: p, api: api, session: session, store: NewStore(cache, api, session), cache: cache}
}

func (f *fixture) contents(t *testing.T) *Contents {
	t.Helper()
	c, err := ReadContents(f.project)
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func entries(t *testing.T, path string) []string {
	t.Helper()
	archive, err := zip.OpenReader(path)
	if err != nil {
		t.Fatal(err)
	}
	defer archive.Close()
	var names []string
	for _, f := range archive.File {
		names = append(names, f.Name)
	}
	sort.Strings(names)
	return names
}

func readJSON(t *testing.T, path, member string, into any) {
	t.Helper()
	archive, err := zip.OpenReader(path)
	if err != nil {
		t.Fatal(err)
	}
	defer archive.Close()
	f, err := archive.Open(member)
	if err != nil {
		t.Fatal(err)
	}
	data, _ := io.ReadAll(f)
	if err := json.Unmarshal(data, into); err != nil {
		t.Fatal(err)
	}
}

func slugs(mods []pack.Mod) []string {
	var result []string
	for _, mod := range mods {
		result = append(result, mod.Slug())
	}
	sort.Strings(result)
	return result
}

func sorted(values ...string) []string {
	sort.Strings(values)
	return values
}

// py: test_exporter.py::test_curseforge_pack
func TestCurseForgePack(t *testing.T) {
	f := exportProject(t)
	output := filepath.Join(t.TempDir(), "cf.zip")
	bundled, summary, err := BuildCurseForge(context.Background(), f.project, f.contents(t), f.store, output)
	if err != nil {
		t.Fatal(err)
	}
	var manifest cfManifest
	readJSON(t, output, "manifest.json", &manifest)
	if manifest.Minecraft.Version != "1.21.11" || !reflect.DeepEqual(manifest.Minecraft.ModLoaders, []cfLoader{{"fabric-0.18.4", true}}) {
		t.Error(manifest.Minecraft)
	}
	var ids [][2]int64
	for _, file := range manifest.Files {
		ids = append(ids, [2]int64{file.ProjectID, file.FileID})
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i][0] < ids[j][0] })
	if !reflect.DeepEqual(ids, [][2]int64{{22, 11}, {100, 1000}, {200, 2000}}) {
		t.Error(ids)
	}
	want := sorted("manifest.json", "overrides/resourcepacks/Bundled.zip", "overrides/config/a.json",
		"overrides/mods/ghmod.jar", "overrides/mods/odd.jar")
	if got := entries(t, output); !slices.Equal(got, want) {
		t.Error(got)
	}
	if !slices.Equal(slugs(bundled), []string{"ghmod", "odd"}) || summary != "3 from CurseForge, 2 bundled" {
		t.Error(slugs(bundled), summary)
	}
}

// py: test_exporter.py::test_fingerprints_are_cached
func TestFingerprintsAreCached(t *testing.T) {
	f := exportProject(t)
	contents := f.contents(t)
	dir := t.TempDir()
	if _, _, err := BuildCurseForge(context.Background(), f.project, contents, f.store, filepath.Join(dir, "a.zip")); err != nil {
		t.Fatal(err)
	}
	first := len(f.api.Downloads)
	store := NewStore(f.cache, f.api, f.session)
	if _, _, err := BuildCurseForge(context.Background(), f.project, contents, store, filepath.Join(dir, "b.zip")); err != nil {
		t.Fatal(err)
	}
	if len(f.api.Downloads) != first {
		t.Error("the second build downloaded again")
	}
}

// py: test_exporter.py::test_modrinth_pack
func TestModrinthPack(t *testing.T) {
	f := exportProject(t)
	output := filepath.Join(t.TempDir(), "pack.mrpack")
	bundled, summary, err := BuildModrinth(context.Background(), f.project, f.contents(t), f.store, output)
	if err != nil {
		t.Fatal(err)
	}
	var index struct {
		Files        []mrFile          `json:"files"`
		Dependencies map[string]string `json:"dependencies"`
	}
	readJSON(t, output, "modrinth.index.json", &index)
	if !reflect.DeepEqual(index.Dependencies, map[string]string{"minecraft": "1.21.11", "fabric-loader": "0.18.4"}) {
		t.Error(index.Dependencies)
	}
	files := map[string]mrFile{}
	for _, file := range index.Files {
		files[file.Path] = file
		if file.Hashes.SHA1 == "" || file.Hashes.SHA512 == "" || file.FileSize == 0 {
			t.Error("incomplete", file)
		}
	}
	var paths []string
	for p := range files {
		paths = append(paths, p)
	}
	if !slices.Equal(sorted(paths...), sorted("mods/sodium.jar", "mods/ghmod.jar", "mods/server.jar", "resourcepacks/fa.zip")) {
		t.Error(paths)
	}
	if files["mods/sodium.jar"].Env != (mrEnv{"required", "unsupported"}) || files["mods/server.jar"].Env != (mrEnv{"unsupported", "required"}) {
		t.Error("env")
	}
	if files["mods/ghmod.jar"].Hashes.SHA1 != sha("ghmod", sha1.New()) {
		t.Error("ghmod hash")
	}
	got := entries(t, output)
	for _, want := range []string{"overrides/mods/cfmod.jar", "overrides/mods/odd.jar", "overrides/resourcepacks/Bundled.zip"} {
		if !slices.Contains(got, want) {
			t.Error("missing", want)
		}
	}
	if !slices.Equal(slugs(bundled), []string{"cfmod", "odd"}) || summary != "4 from Modrinth, 2 bundled" {
		t.Error(slugs(bundled), summary)
	}
}

// py: test_exporter.py::test_server_pack
func TestServerPack(t *testing.T) {
	f := exportProject(t)
	f.project.Settings.ServerExclude = []string{"GH Mod"}
	output := filepath.Join(t.TempDir(), "server.zip")
	_, summary, err := BuildServer(context.Background(), f.project, f.contents(t), f.store, output)
	if err != nil {
		t.Fatal(err)
	}
	want := sorted("start.bat", "mods/patched.jar", "mods/cfmod.jar", "mods/odd.jar", "mods/server.jar")
	if got := entries(t, output); !slices.Equal(got, want) {
		t.Error(got)
	}
	if summary != "3 mods + 1 from Server Pack, left out: GH Mod" {
		t.Error(summary)
	}
}

// py: test_exporter.py::test_bad_download_stops_the_export
func TestBadDownloadStopsTheExport(t *testing.T) {
	f := exportProject(t)
	for url := range f.api.URLs {
		f.api.URLs[url] = []byte("tampered")
	}
	_, _, err := BuildServer(context.Background(), f.project, f.contents(t), f.store, filepath.Join(t.TempDir(), "s.zip"))
	if err == nil || !strings.Contains(err.Error(), "doesn't match the hash") {
		t.Errorf("got %v", err)
	}
}

// py: test_exporter.py::test_blocked_files_are_asked_for
func TestBlockedFilesAreAskedFor(t *testing.T) {
	folder := filepath.Join(t.TempDir(), "instance-mods")
	testutil.Write(t, filepath.Join(folder, "renamed-by-user.jar"), string(jar["cfmod"]))
	f := exportProject(t, "f", folder)
	f.api.Files = map[int64]platform.CFFile{11: {ID: 11}} // No download URL: the author blocks third-party downloads.
	f.buildServerWithCFMod(t)
}

// buildServerWithCFMod builds the server pack and checks that the CurseForge
// mod made it in and is cached for next time.
func (f *fixture) buildServerWithCFMod(t *testing.T) {
	t.Helper()
	contents := f.contents(t)
	output := filepath.Join(t.TempDir(), "s.zip")
	if _, _, err := BuildServer(context.Background(), f.project, contents, f.store, output); err != nil {
		t.Fatal(err)
	}
	if !slices.Contains(entries(t, output), "mods/cfmod.jar") {
		t.Error("cfmod.jar missing")
	}
	for _, mod := range contents.Mods {
		if mod.Slug() == "cfmod" {
			if _, ok := f.store.Cached(mod); !ok {
				t.Error("not cached for next time")
			}
		}
	}
}

// browserDownloads makes the store watch a temporary Downloads folder.
func (f *fixture) browserDownloads(t *testing.T) string {
	downloads := t.TempDir()
	f.store.DownloadsDir = func() (string, error) { return downloads, nil }
	f.store.Poll = 10 * time.Millisecond
	f.api.Files = map[int64]platform.CFFile{11: {ID: 11}}
	mod := platform.CFMod{ID: 22}
	mod.Links.WebsiteURL = "https://www.curseforge.com/minecraft/mc-mods/cf-mod"
	f.api.Mods = map[int64]platform.CFMod{22: mod}
	return downloads
}

func TestBlockedFilesComeFromTheBrowser(t *testing.T) {
	f := exportProject(t, "") // Enter: the browser is the default.
	downloads := f.browserDownloads(t)
	testutil.Write(t, filepath.Join(downloads, "cfmod (1).jar.crdownload"), "cfmod") // Another download, still going.
	testutil.Write(t, filepath.Join(downloads, "other.jar"), "something else")
	var opened []string
	f.store.OpenURL = func(url string) error {
		opened = append(opened, url)
		// The browser saves the file a moment later, renamed since cfmod.jar was taken.
		time.AfterFunc(50*time.Millisecond, func() {
			os.WriteFile(filepath.Join(downloads, "cfmod (1).jar"), jar["cfmod"], 0o644)
		})
		return nil
	}
	f.buildServerWithCFMod(t)
	if !slices.Equal(opened, []string{"https://www.curseforge.com/minecraft/mc-mods/cf-mod/download/11"}) {
		t.Error(opened)
	}
	if text := f.session.Text(); !strings.Contains(text, "Picked up 1 file from "+downloads) {
		t.Error(text)
	}
}

func TestBlockedFilesAlreadyDownloadedAreUsed(t *testing.T) {
	f := exportProject(t, "b")
	downloads := f.browserDownloads(t)
	path := filepath.Join(downloads, "cfmod.jar")
	testutil.Write(t, path, string(jar["cfmod"]))
	yesterday := time.Now().Add(-24 * time.Hour)
	if err := os.Chtimes(path, yesterday, yesterday); err != nil {
		t.Fatal(err)
	}
	f.store.OpenURL = func(url string) error {
		t.Error("opened", url)
		return nil
	}
	f.buildServerWithCFMod(t)
}

func TestBlockedFilesWithoutAWebsiteOpenTheProject(t *testing.T) {
	f := exportProject(t, "b")
	downloads := f.browserDownloads(t)
	f.api.Mods = nil
	var opened []string
	f.store.OpenURL = func(url string) error {
		opened = append(opened, url)
		return os.WriteFile(filepath.Join(downloads, "cfmod.jar"), jar["cfmod"], 0o644)
	}
	f.buildServerWithCFMod(t)
	if !slices.Equal(opened, []string{"https://www.curseforge.com/projects/22"}) {
		t.Error(opened)
	}
}

func TestPagesThatDontOpenAreListed(t *testing.T) {
	f := exportProject(t, "b")
	downloads := f.browserDownloads(t)
	f.store.OpenURL = func(string) error {
		time.AfterFunc(20*time.Millisecond, func() { // Saved by hand.
			os.WriteFile(filepath.Join(downloads, "cfmod.jar"), jar["cfmod"], 0o644)
		})
		return errors.New("no browser")
	}
	f.buildServerWithCFMod(t)
	if text := f.session.Text(); !strings.Contains(text, "Couldn't open the browser") ||
		!strings.Contains(text, "https://www.curseforge.com/minecraft/mc-mods/cf-mod/download/11") {
		t.Error(text)
	}
}

func TestWaitingForDownloadsStopsWhenCanceled(t *testing.T) {
	f := exportProject(t, "b")
	f.browserDownloads(t)
	ctx, cancel := context.WithCancel(context.Background())
	f.store.OpenURL = func(string) error {
		time.AfterFunc(50*time.Millisecond, cancel) // Esc while waiting.
		return nil
	}
	contents := f.contents(t)
	_, _, err := BuildServer(ctx, f.project, contents, f.store, filepath.Join(t.TempDir(), "s.zip"))
	if !errors.Is(err, context.Canceled) {
		t.Error(err)
	}
}

func TestBlockedFilesCanBeCanceled(t *testing.T) {
	f := exportProject(t, "c")
	f.api.Files = map[int64]platform.CFFile{11: {ID: 11}}
	contents := f.contents(t)
	_, _, err := BuildServer(context.Background(), f.project, contents, f.store, filepath.Join(t.TempDir(), "s.zip"))
	if err == nil || err.Error() != "Export stopped; these files are missing: CF Mod" {
		t.Error(err)
	}
}

// py: test_exporter.py::test_export_writes_named_files_and_report
func TestExportWritesNamedFilesAndReport(t *testing.T) {
	f := exportProject(t)
	written, err := Export(context.Background(), f.project, []string{"curseforge", "modrinth", "server"}, f.store)
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	for _, path := range written {
		names = append(names, filepath.Base(path))
	}
	if !slices.Equal(names, []string{"Pack-2.0.0.zip", "Pack-2.0.0.mrpack", "Pack-Server-2.0.0.zip"}) {
		t.Error(names)
	}
	report := testutil.Read(t, filepath.Join(f.project.ExportDir(), "bundled_links.md"))
	if !strings.Contains(report, "[GH Mod](https://github.com/me/gh): `ghmod.jar`") || !strings.Contains(report, "## Modrinth pack") {
		t.Error(report)
	}
}

// py: test_robustness.py::test_bundled_report_never_describes_an_old_release
func TestBundledReportNeverDescribesAnOldRelease(t *testing.T) {
	f := exportProject(t)
	report := testutil.Write(t, filepath.Join(f.project.ExportDir(), "bundled_links.md"), "# Bundled files in MyPack 1.0.0\n- [Old](x)\n")
	if _, err := Export(context.Background(), f.project, nil, f.store); err != nil {
		t.Fatal(err)
	}
	if text := testutil.Read(t, report); !strings.Contains(text, "No files are bundled") || !strings.Contains(text, "2.0.0") {
		t.Error(text)
	}
}

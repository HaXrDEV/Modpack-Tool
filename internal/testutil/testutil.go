// Package testutil holds the fixtures shared by the tests (the port of the
// Python tool's tests/conftest.py).
package testutil

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// Write creates a file (and its folders) with text, using newline for line breaks.
func Write(t testing.TB, path, text string, newline ...string) string {
	t.Helper()
	if len(newline) > 0 && newline[0] != "\n" {
		text = strings.ReplaceAll(text, "\n", newline[0])
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

// Read returns a file's text.
func Read(t testing.TB, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

// MetaOptions varies a test metafile.
type MetaOptions struct {
	Side   string // Default "both".
	Source string // "modrinth" (default), "curseforge" or "url".
	Pin    bool
	Extra  string
}

// Metafile is the text of a packwiz metafile for tests.
func Metafile(name, filename string, opts ...MetaOptions) string {
	o := MetaOptions{}
	if len(opts) > 0 {
		o = opts[0]
	}
	if o.Side == "" {
		o.Side = "both"
	}
	if o.Source == "" {
		o.Source = "modrinth"
	}
	lines := []string{`name = "` + name + `"`, `filename = "` + filename + `"`, `side = "` + o.Side + `"`}
	if o.Pin {
		lines = append(lines, "pin = true")
	}
	lower := strings.ToLower(name)
	switch o.Source {
	case "modrinth":
		lines = append(lines, "", "[download]", `url = "https://cdn.modrinth.com/data/AAA/versions/BBB/`+filename+`"`,
			`hash-format = "sha512"`, `hash = "`+lower+`hash"`, "", "[update]",
			"[update.modrinth]", `mod-id = "`+prefix(name, 8)+`"`, `version = "`+prefix(name, 4)+`v1"`)
	case "curseforge":
		lines = append(lines, "", "[download]", `hash-format = "sha1"`, `hash = "`+lower+`sha1"`,
			`mode = "metadata:curseforge"`, "", "[update]", "[update.curseforge]", "file-id = 111", "project-id = 222")
	default:
		lines = append(lines, "", "[download]", `url = "https://example.com/`+filename+`"`, `hash-format = "sha256"`,
			`hash = "`+lower+`sha256"`)
	}
	return strings.Join(lines, "\n") + "\n" + o.Extra
}

func prefix(s string, n int) string {
	if len(s) > n {
		return s[:n]
	}
	return s
}

// PackDir creates a small packwiz pack with mixed sources, a disabled mod and
// a pinned mod, and returns its Packwiz folder.
func PackDir(t testing.TB) string {
	t.Helper()
	root := filepath.Join(t.TempDir(), "MyPack")
	pw := filepath.Join(root, "Packwiz")
	Write(t, filepath.Join(pw, "pack.toml"), "name = \"MyPack\"\nauthor = \"me\"\nversion = \"1.2.0\"\npack-format = \"packwiz:1.1.0\"\n\n"+
		"[index]\nfile = \"index.toml\"\nhash-format = \"sha256\"\nhash = \"abc\"\n\n"+
		"[versions]\nfabric = \"0.18.4\"\nminecraft = \"1.21.11\"\n")
	Write(t, filepath.Join(pw, "mods", "sodium.pw.toml"), Metafile("Sodium", "sodium-0.8.jar", MetaOptions{Side: "client"}), "\r\n")
	Write(t, filepath.Join(pw, "mods", "lithium.pw.toml"), Metafile("Lithium", "lithium-0.21.jar"))
	Write(t, filepath.Join(pw, "mods", "boss.pw.toml"), Metafile("Boss Checklist", "boss-4.1.jar",
		MetaOptions{Side: "both(disabled)", Source: "curseforge"}))
	Write(t, filepath.Join(pw, "mods", "pinned.pw.toml"), Metafile("Pinned Mod [Fabric]", "pinned-1.0.jar", MetaOptions{Pin: true}))
	Write(t, filepath.Join(pw, "mods", "server.pw.toml"), Metafile("Server Thing", "server-1.0.jar", MetaOptions{Side: "server", Source: "url"}))
	Write(t, filepath.Join(pw, "resourcepacks", "fresh.pw.toml"), Metafile("Fresh Animations", "fa.zip", MetaOptions{Side: "client"}))
	Write(t, filepath.Join(pw, "mods", "disabled", "old.pw.toml"), Metafile("Old", "old.jar")) // Ignored subfolder.
	Write(t, filepath.Join(pw, "config", "bcc.json"), `{"projectID": 1, "modpackName": "MyPack", "modpackVersion": "1.1.0"}`)
	Write(t, filepath.Join(pw, "index.toml"), "hash-format = \"sha256\"\n\n"+
		"[[files]]\nfile = \"config/bcc.json\"\nhash = \"x\"\n\n"+
		"[[files]]\nfile = \"mods/sodium.pw.toml\"\nhash = \"y\"\nmetafile = true\n")
	return pw
}

// RequireGit skips the test when git isn't installed.
func RequireGit(t testing.TB) {
	t.Helper()
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git is not installed")
	}
}

// Git runs a git command in dir and fails the test when it fails.
func Git(t testing.TB, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), "GIT_TERMINAL_PROMPT=0")
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %s: %v\n%s", strings.Join(args, " "), err, out)
	}
	return string(out)
}

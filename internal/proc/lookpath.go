package proc

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"

	"github.com/HaXrDEV/Modpack-Tool/internal/files"
)

// LookPath finds an executable the way cmd.exe does. Go's exec.LookPath
// reads a quote in PATH as the start of a quoted section, so a single stray
// quote (such as `C:\Program Files\PowerShell\7"`, which some installers
// leave behind) hides every folder after it; here PATH is split at each ";"
// and quotes are ignored.
func LookPath(name string) (string, error) {
	path, err := exec.LookPath(name)
	if err == nil || runtime.GOOS != "windows" || strings.ContainsAny(name, `\/:`) {
		return path, err
	}
	extensions := []string{""}
	if filepath.Ext(name) == "" {
		extensions = pathExtensions()
	}
	for _, dir := range pathDirs() {
		for _, ext := range extensions {
			candidate := filepath.Join(dir, name+ext)
			if files.IsFile(candidate) {
				return candidate, nil
			}
		}
	}
	return "", &exec.Error{Name: name, Err: exec.ErrNotFound}
}

func pathExtensions() []string {
	var extensions []string
	for _, ext := range strings.Split(os.Getenv("PATHEXT"), ";") {
		if ext = strings.ToLower(strings.TrimSpace(ext)); strings.HasPrefix(ext, ".") {
			extensions = append(extensions, ext)
		}
	}
	if len(extensions) == 0 {
		extensions = []string{".com", ".exe", ".bat", ".cmd"}
	}
	return extensions
}

func pathDirs() []string {
	var dirs []string
	for _, dir := range strings.Split(os.Getenv("PATH"), string(os.PathListSeparator)) {
		if dir = strings.TrimSpace(strings.ReplaceAll(dir, `"`, "")); dir != "" {
			dirs = append(dirs, dir)
		}
	}
	return dirs
}

// cleanPath is PATH without quotes, for child processes: gh, for one, looks
// up git with Go's rules and would trip over the same stray quote.
func cleanPath(environ []string) []string {
	if runtime.GOOS != "windows" {
		return environ
	}
	for i, kv := range environ {
		if key, _, ok := strings.Cut(kv, "="); ok && strings.EqualFold(key, "PATH") && strings.Contains(kv, `"`) {
			environ[i] = key + "=" + strings.Join(pathDirs(), ";")
		}
	}
	return environ
}

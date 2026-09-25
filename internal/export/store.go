// Package export builds the CurseForge zip, the Modrinth .mrpack and the
// server pack from the packwiz metadata.
//
// Every metafile in index.toml (mods, resource packs, shader packs) is
// installed into its own folder, and every other indexed file becomes an
// override, exactly as packwiz ships the pack (.packwizignore applies).
// Disabled files are left out. Anything that can't be resolved stops the
// export with a list instead of being dropped silently.
package export

import (
	"context"
	"crypto/md5"
	"crypto/sha1"
	"crypto/sha256"
	"crypto/sha512"
	"encoding/hex"
	"fmt"
	"hash"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"

	"golang.org/x/sync/errgroup"

	"github.com/HaXrDEV/Modpack-Tool/internal/fail"
	"github.com/HaXrDEV/Modpack-Tool/internal/files"
	"github.com/HaXrDEV/Modpack-Tool/internal/pack"
	"github.com/HaXrDEV/Modpack-Tool/internal/platform"
	"github.com/HaXrDEV/Modpack-Tool/internal/ui"
)

func hasher(format string) hash.Hash {
	switch format {
	case "sha1":
		return sha1.New()
	case "sha256":
		return sha256.New()
	case "sha512":
		return sha512.New()
	case "md5":
		return md5.New()
	}
	return nil
}

// digest is the hash of data in a packwiz hash format.
func digest(data []byte, format string) string {
	if format == "murmur2" {
		return strconv.FormatUint(uint64(platform.Murmur2(data)), 10)
	}
	h := hasher(format)
	if h == nil {
		return ""
	}
	h.Write(data)
	return hex.EncodeToString(h.Sum(nil))
}

// fileDigest hashes a file on disk; "" when it can't be read.
func fileDigest(path, format string) string {
	if h := hasher(format); h != nil {
		f, err := os.Open(path)
		if err != nil {
			return ""
		}
		defer f.Close()
		if _, err := io.Copy(h, f); err != nil {
			return ""
		}
		return hex.EncodeToString(h.Sum(nil))
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return digest(data, format)
}

// Store downloads the files a pack needs, keeping them in a cache keyed by
// their hash (the same layout the Python tool used, so its cache carries over).
type Store struct {
	Dir     string // The cache folder; files go to Dir/files.
	API     platform.API
	Session ui.Session
	// OpenURL opens a page in the default browser.
	OpenURL func(url string) error
	// DownloadsDir is the folder the browser saves downloads to.
	DownloadsDir func() (string, error)
	// Poll is how often the Downloads folder is checked for files.
	Poll time.Duration

	mu      sync.Mutex
	cfFiles map[int64]platform.CFFile
}

// NewStore returns a store with its cache in dir.
func NewStore(dir string, api platform.API, session ui.Session) *Store {
	return &Store{Dir: dir, API: api, Session: session, cfFiles: map[int64]platform.CFFile{},
		OpenURL: ui.OpenFile, DownloadsDir: downloadsDir, Poll: time.Second}
}

// Path is where a file is cached.
func (s *Store) Path(mod pack.Mod) string {
	format, value := mod.Hash()
	return filepath.Join(s.Dir, "files", format+"-"+strings.ToLower(value))
}

// Cached returns the cached copy of a file, if there is one.
func (s *Store) Cached(mod pack.Mod) (string, bool) {
	path := s.Path(mod)
	info, err := os.Stat(path)
	return path, err == nil && info.Mode().IsRegular()
}

// Add caches a file's bytes after checking them against the metafile's hash.
func (s *Store) Add(mod pack.Mod, data []byte) error {
	format, value := mod.Hash()
	if format != "" && !strings.EqualFold(digest(data, format), value) {
		return fail.Errorf("%s: the file doesn't match the hash in %s.", fileLabel(mod), mod.Rel)
	}
	return files.WriteAtomic(s.Path(mod), data)
}

func fileLabel(mod pack.Mod) string {
	if mod.Filename() != "" {
		return mod.Filename()
	}
	return mod.Name()
}

func (s *Store) downloadURL(mod pack.Mod) string {
	if url := mod.DownloadURL(); url != "" {
		return url
	}
	if cf := mod.CurseForge(); cf != nil {
		s.mu.Lock()
		defer s.mu.Unlock()
		return s.cfFiles[pack.Int(cf["file-id"])].DownloadURL
	}
	return ""
}

// Fetch returns {metafile path: cached file} for all mods, downloading what
// isn't cached yet and asking for a folder for anything not downloadable.
func (s *Store) Fetch(ctx context.Context, mods []pack.Mod) (map[string]string, error) {
	result := map[string]string{}
	var needed []pack.Mod
	for _, mod := range mods {
		if path, ok := s.Cached(mod); ok {
			result[mod.Rel] = path
		} else {
			needed = append(needed, mod)
		}
	}
	if len(needed) == 0 {
		return result, nil
	}
	var cfIDs []int64
	for _, mod := range needed {
		if cf := mod.CurseForge(); cf != nil && mod.DownloadURL() == "" {
			cfIDs = append(cfIDs, pack.Int(cf["file-id"]))
		}
	}
	if len(cfIDs) > 0 {
		found, err := s.API.CurseForgeFiles(ctx, cfIDs)
		if err != nil {
			return nil, err
		}
		s.mu.Lock()
		for id, f := range found {
			s.cfFiles[id] = f
		}
		s.mu.Unlock()
	}
	var downloadable, missing []pack.Mod
	for _, mod := range needed {
		if s.downloadURL(mod) != "" {
			downloadable = append(downloadable, mod)
		} else {
			missing = append(missing, mod)
		}
	}
	if len(downloadable) > 0 {
		if err := s.download(ctx, downloadable); err != nil {
			return nil, err
		}
		for _, mod := range downloadable {
			result[mod.Rel] = s.Path(mod)
		}
	}
	if len(missing) > 0 {
		found, err := s.askForFiles(ctx, missing)
		if err != nil {
			return nil, err
		}
		for rel, path := range found {
			result[rel] = path
		}
	}
	return result, nil
}

func plural(n int) string {
	if n == 1 {
		return ""
	}
	return "s"
}

// download fetches files 8 at a time; every failure is reported together.
func (s *Store) download(ctx context.Context, mods []pack.Mod) error {
	step := s.Session.Step(fmt.Sprintf("Downloading %d file%s", len(mods), plural(len(mods))))
	var mu sync.Mutex
	var failures []string
	done := 0
	group, groupCtx := errgroup.WithContext(ctx)
	group.SetLimit(8)
	for _, mod := range mods {
		group.Go(func() error {
			err := s.downloadOne(groupCtx, mod)
			mu.Lock()
			defer mu.Unlock()
			if err != nil {
				if ctx.Err() != nil {
					return ctx.Err()
				}
				failures = append(failures, mod.Name()+": "+err.Error())
			}
			done++
			step.Progress(done, len(mods))
			return nil
		})
	}
	if err := group.Wait(); err != nil {
		step.Fail(err)
		return err
	}
	if len(failures) > 0 {
		err := fail.Errorf("Some downloads failed:\n  - %s", strings.Join(failures, "\n  - "))
		step.Fail(err)
		return err
	}
	step.Done(fmt.Sprintf("Downloaded %d file%s", len(mods), plural(len(mods))))
	return nil
}

func (s *Store) downloadOne(ctx context.Context, mod pack.Mod) error {
	target := s.Path(mod)
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		return err
	}
	part, err := os.CreateTemp(filepath.Dir(target), filepath.Base(target)+".*.part")
	if err != nil {
		return err
	}
	defer os.Remove(part.Name())
	defer part.Close()
	format, value := mod.Hash()
	var h hash.Hash
	err = s.API.Download(ctx, s.downloadURL(mod), func() (io.Writer, error) {
		if _, err := part.Seek(0, io.SeekStart); err != nil {
			return nil, err
		}
		if err := part.Truncate(0); err != nil {
			return nil, err
		}
		if h = hasher(format); h == nil {
			return part, nil
		}
		return io.MultiWriter(part, h), nil
	})
	if err != nil {
		return err
	}
	if err := part.Close(); err != nil {
		return err
	}
	switch {
	case h != nil:
		if !strings.EqualFold(hex.EncodeToString(h.Sum(nil)), value) {
			return fail.Errorf("%s: the file doesn't match the hash in %s.", fileLabel(mod), mod.Rel)
		}
	case format != "":
		data, err := os.ReadFile(part.Name())
		if err != nil {
			return err
		}
		if !strings.EqualFold(digest(data, format), value) {
			return fail.Errorf("%s: the file doesn't match the hash in %s.", fileLabel(mod), mod.Rel)
		}
	}
	return files.Rename(part.Name(), target)
}

// askForFiles gets the files whose authors block third-party downloads:
// through the browser (their download pages open, and the files are picked
// up from the Downloads folder as they arrive), or from a folder the user
// points to (for example a CurseForge app instance's mods).
func (s *Store) askForFiles(ctx context.Context, mods []pack.Mod) (map[string]string, error) {
	s.Session.Warn(fmt.Sprintf("%d file%s can't be downloaded automatically (their authors block third-party downloads):",
		len(mods), plural(len(mods))), missingNames(mods, nil)...)
	choice, err := s.Session.Choose(ctx, "How do you want to get them?", []ui.Option{
		{Key: "b", Label: "download them in the browser"},
		{Key: "f", Label: "pick a folder that has them"},
		{Key: "c", Label: "cancel the export"},
	}, "b")
	if err != nil {
		return nil, err
	}
	found := map[string]string{}
	switch choice {
	case "b":
		found, err = s.fromBrowser(ctx, mods)
	case "f":
		found, err = s.fromFolders(ctx, mods)
	}
	if err != nil {
		return nil, err
	}
	if len(found) < len(mods) {
		var still []string
		for _, mod := range mods {
			if _, ok := found[mod.Rel]; !ok {
				still = append(still, mod.Name())
			}
		}
		return nil, fail.Errorf("Export stopped; these files are missing: %s", strings.Join(still, ", "))
	}
	return found, nil
}

func missingNames(mods []pack.Mod, found map[string]string) []string {
	var names []string
	for _, mod := range mods {
		if _, ok := found[mod.Rel]; !ok {
			names = append(names, mod.Name()+" ("+mod.Filename()+")")
		}
	}
	return names
}

// fromFolders asks for folders until every file is found (Enter gives up).
func (s *Store) fromFolders(ctx context.Context, mods []pack.Mod) (map[string]string, error) {
	s.Session.Info("Download them from CurseForge (or use a CurseForge app instance's mods folder).")
	found := map[string]string{}
	digests := digestCache{}
	for len(found) < len(mods) {
		folder, err := s.Session.AskPath(ctx, "Folder containing these files (Enter to cancel)")
		if err != nil {
			return nil, err
		}
		if folder == "" {
			break
		}
		if info, err := os.Stat(folder); err != nil || !info.IsDir() {
			s.Session.Warn(folder + " is not a folder.")
			continue
		}
		var candidates []candidate
		filepath.WalkDir(folder, func(path string, entry fs.DirEntry, err error) error {
			if err == nil && entry.Type().IsRegular() {
				if info, err := entry.Info(); err == nil {
					candidates = append(candidates, candidate{path, info.Size(), info.ModTime().UnixNano()})
				}
			}
			return nil
		})
		if err := s.match(mods, candidates, found, digests); err != nil {
			return nil, err
		}
		if still := missingNames(mods, found); len(still) > 0 {
			s.Session.Warn("Still missing:", still...)
		} else {
			s.Session.Info(fmt.Sprintf("Found all %d file%s; they are cached for next time.", len(found), plural(len(found))))
		}
	}
	return found, nil
}

// fromBrowser opens the download page of each file and picks the files up
// from the Downloads folder as the browser saves them. It waits until all of
// them are there, or until the run is canceled.
func (s *Store) fromBrowser(ctx context.Context, mods []pack.Mod) (map[string]string, error) {
	dir, err := s.DownloadsDir()
	if err != nil {
		return nil, fail.Wrapf(err, "Couldn't find your Downloads folder: %v", err)
	}
	pages, err := s.downloadPages(ctx, mods)
	if err != nil {
		return nil, err
	}
	found := map[string]string{}
	digests := digestCache{}
	started := time.Now()
	// Files downloaded earlier count right away.
	if err := s.match(mods, recentDownloads(dir, mods, started), found, digests); err != nil {
		return nil, err
	}
	opened := 0
	var pageless, unopened []string
	for _, mod := range mods {
		if _, ok := found[mod.Rel]; ok {
			continue
		}
		if pages[mod.Rel] == "" {
			pageless = append(pageless, mod.Name()+" ("+mod.Filename()+")")
			continue
		}
		if err := s.OpenURL(pages[mod.Rel]); err != nil {
			unopened = append(unopened, pages[mod.Rel])
			continue
		}
		opened++
		select { // A moment between tabs, so the browser opens them all.
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-time.After(s.Poll / 4):
		}
	}
	if opened > 0 {
		s.Session.Info(fmt.Sprintf("Opened %d download page%s in your browser (if it warns about the file%s, choose Keep).\n"+
			"Finished downloads are picked up from %s.", opened, plural(opened), plural(opened), dir))
	}
	if len(unopened) > 0 {
		s.Session.Warn("Couldn't open the browser; open these pages yourself and save the files to "+dir+":", unopened...)
	}
	if len(pageless) > 0 {
		s.Session.Warn("These have no CurseForge page to open; save them to "+dir+" yourself:", pageless...)
	}
	step := s.Session.Step("Waiting for the downloads")
	step.Progress(len(found), len(mods))
	previous := map[string]candidate{}
	for len(found) < len(mods) {
		select {
		case <-ctx.Done():
			step.Fail(ctx.Err())
			return nil, ctx.Err()
		case <-time.After(s.Poll):
		}
		// Only files that didn't change since the last look are hashed, so a
		// download that is still being written isn't read over and over.
		var settled []candidate
		current := map[string]candidate{}
		for _, c := range recentDownloads(dir, mods, started) {
			current[c.path] = c
			if previous[c.path] == c {
				settled = append(settled, c)
			}
		}
		previous = current
		if err := s.match(mods, settled, found, digests); err != nil {
			step.Fail(err)
			return nil, err
		}
		step.Progress(len(found), len(mods))
	}
	step.Done(fmt.Sprintf("Picked up %d file%s from %s (cached for next time)", len(mods), plural(len(mods)), dir))
	return found, nil
}

// downloadPages returns each file's CurseForge download page, which starts
// the download in the browser.
func (s *Store) downloadPages(ctx context.Context, mods []pack.Mod) (map[string]string, error) {
	var ids []int64
	for _, mod := range mods {
		if cf := mod.CurseForge(); cf != nil {
			ids = append(ids, pack.Int(cf["project-id"]))
		}
	}
	projects, err := s.API.CurseForgeMods(ctx, ids)
	if err != nil {
		return nil, err
	}
	pages := map[string]string{}
	for _, mod := range mods {
		cf := mod.CurseForge()
		if cf == nil {
			continue
		}
		projectID, fileID := pack.Int(cf["project-id"]), pack.Int(cf["file-id"])
		if site := strings.TrimRight(projects[projectID].Links.WebsiteURL, "/"); site != "" {
			pages[mod.Rel] = fmt.Sprintf("%s/download/%d", site, fileID)
		} else {
			pages[mod.Rel] = fmt.Sprintf("https://www.curseforge.com/projects/%d", projectID)
		}
	}
	return pages, nil
}

// Downloads that are still in progress have these names.
var partialDownload = []string{".crdownload", ".part", ".partial", ".download", ".tmp"}

// recentDownloads lists the files in the Downloads folder that may be one of
// the wanted files: those with a wanted name, and anything saved since the
// wait started (the browser may have renamed it, e.g. "mod (1).jar").
func recentDownloads(dir string, mods []pack.Mod, started time.Time) []candidate {
	wanted := map[string]bool{}
	for _, mod := range mods {
		wanted[mod.Filename()] = true
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil
	}
	var candidates []candidate
	for _, entry := range entries {
		name := strings.ToLower(entry.Name())
		if !entry.Type().IsRegular() || slices.ContainsFunc(partialDownload, func(ext string) bool { return strings.HasSuffix(name, ext) }) {
			continue
		}
		info, err := entry.Info()
		// An empty file is a placeholder the browser fills in when the download finishes.
		if err != nil || info.Size() == 0 || !(wanted[entry.Name()] || !info.ModTime().Before(started.Add(-time.Minute))) {
			continue
		}
		candidates = append(candidates, candidate{filepath.Join(dir, entry.Name()), info.Size(), info.ModTime().UnixNano()})
	}
	return candidates
}

// candidate is a file that may be one of the missing ones.
type candidate struct {
	path        string
	size, mtime int64
}

// digestCache hashes each file at most once per format (until it changes).
type digestCache map[digestKey]string

type digestKey struct {
	candidate
	format string
}

// match looks for each missing file among the candidates, by name first and
// then by hash; a file without a hash is only recognized by its name.
func (s *Store) match(mods []pack.Mod, candidates []candidate, found map[string]string, digests digestCache) error {
	for _, mod := range mods {
		if _, ok := found[mod.Rel]; ok {
			continue
		}
		format, value := mod.Hash()
		var named, others []candidate
		for _, c := range candidates {
			if filepath.Base(c.path) == mod.Filename() {
				named = append(named, c)
			} else {
				others = append(others, c)
			}
		}
		pool := named
		if format != "" {
			pool = append(named, others...)
		}
		for _, c := range pool {
			if format != "" {
				key := digestKey{c, format}
				sum, ok := digests[key]
				if !ok {
					// A file that can't be read yet (a virus scan may hold it
					// right after a download) is tried again next time.
					if sum = strings.ToLower(fileDigest(c.path, format)); sum != "" {
						digests[key] = sum
					}
				}
				if sum != strings.ToLower(value) {
					continue
				}
			}
			data, err := os.ReadFile(c.path)
			if err != nil || (format != "" && !strings.EqualFold(digest(data, format), value)) {
				continue // Gone, or changed since it was hashed.
			}
			if err := s.Add(mod, data); err != nil {
				return err
			}
			found[mod.Rel] = s.Path(mod)
			break
		}
	}
	return nil
}

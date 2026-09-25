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
	"strconv"
	"strings"
	"sync"

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

	mu      sync.Mutex
	cfFiles map[int64]platform.CFFile
}

// NewStore returns a store with its cache in dir.
func NewStore(dir string, api platform.API, session ui.Session) *Store {
	return &Store{Dir: dir, API: api, Session: session, cfFiles: map[int64]platform.CFFile{}}
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

// askForFiles finds files whose authors block third-party downloads in a
// folder the user points to (for example a CurseForge app instance's mods).
func (s *Store) askForFiles(ctx context.Context, mods []pack.Mod) (map[string]string, error) {
	var names []string
	for _, mod := range mods {
		names = append(names, mod.Name()+" ("+mod.Filename()+")")
	}
	s.Session.Warn(fmt.Sprintf("%d file%s can't be downloaded automatically (their authors block third-party downloads):",
		len(mods), plural(len(mods))), names...)
	s.Session.Info("Download them from CurseForge (or use a CurseForge app instance's mods folder).")
	found := map[string]string{}
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
		var candidates []string
		filepath.WalkDir(folder, func(path string, entry fs.DirEntry, err error) error {
			if err == nil && entry.Type().IsRegular() {
				candidates = append(candidates, path)
			}
			return nil
		})
		digests := map[[2]string]string{} // Each file is hashed at most once per format.
		for _, mod := range mods {
			if _, ok := found[mod.Rel]; ok {
				continue
			}
			format, value := mod.Hash()
			var named, others []string
			for _, path := range candidates {
				if filepath.Base(path) == mod.Filename() {
					named = append(named, path)
				} else {
					others = append(others, path)
				}
			}
			pool := named
			if format != "" { // Without a hash only the exact file name can identify the file.
				pool = append(named, others...)
			}
			for _, path := range pool {
				if format != "" {
					key := [2]string{path, format}
					if _, ok := digests[key]; !ok {
						digests[key] = strings.ToLower(fileDigest(path, format))
					}
					if digests[key] != strings.ToLower(value) {
						continue
					}
				}
				data, err := os.ReadFile(path)
				if err != nil {
					continue
				}
				if err := s.Add(mod, data); err != nil {
					return nil, err
				}
				found[mod.Rel] = s.Path(mod)
				break
			}
		}
		var still []string
		for _, mod := range mods {
			if _, ok := found[mod.Rel]; !ok {
				still = append(still, mod.Name()+" ("+mod.Filename()+")")
			}
		}
		if len(still) > 0 {
			s.Session.Warn("Still missing:", still...)
		}
	}
	var still []string
	for _, mod := range mods {
		if _, ok := found[mod.Rel]; !ok {
			still = append(still, mod.Name())
		}
	}
	if len(still) > 0 {
		return nil, fail.Errorf("Export stopped; these files are missing: %s", strings.Join(still, ", "))
	}
	s.Session.Info(fmt.Sprintf("Found all %d file%s; they are cached for next time.", len(found), plural(len(found))))
	return found, nil
}

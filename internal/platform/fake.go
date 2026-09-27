package platform

import (
	"context"
	"fmt"
	"io"
	"slices"
	"sync"
)

// Fake is an API with canned answers, for tests.
type Fake struct {
	mu              sync.Mutex
	Versions        map[string]Version
	Projects        map[string]Project
	Teams           map[string][]TeamMember
	ProjectVersions map[string][]Version // By project id.
	Files           map[int64]CFFile
	Mods            map[int64]CFMod
	Search          map[string][]CFMod // CurseForge projects by slug.
	ModFiles        map[int64][]CFFile // CurseForge files by project id.
	Fingerprints    map[uint32]Match
	URLs            map[string][]byte // Download contents.
	Downloads       []string          // Every URL downloaded, in order.
}

// pick returns the entries of m that ids name.
func pick[K comparable, V any](m map[K]V, ids []K) map[K]V {
	found := map[K]V{}
	for _, id := range ids {
		if v, ok := m[id]; ok {
			found[id] = v
		}
	}
	return found
}

func (f *Fake) ModrinthVersions(_ context.Context, ids []string) (map[string]Version, error) {
	return pick(f.Versions, ids), nil
}

func (f *Fake) ModrinthProjects(_ context.Context, ids []string) (map[string]Project, error) {
	return pick(f.Projects, ids), nil
}

func (f *Fake) ModrinthTeams(_ context.Context, ids []string) (map[string][]TeamMember, error) {
	return pick(f.Teams, ids), nil
}

// ModrinthProjectVersions filters by game version and loader as Modrinth does,
// for the versions that list them.
func (f *Fake) ModrinthProjectVersions(_ context.Context, projectID string, gameVersions, loaders []string) ([]Version, error) {
	var found []Version
	for _, v := range f.ProjectVersions[projectID] {
		if listsAny(v.GameVersions, gameVersions) && listsAny(v.Loaders, loaders) {
			found = append(found, v)
		}
	}
	return found, nil
}

// listsAny reports whether a version's list has one of the wanted values; an
// empty list or filter matches anything.
func listsAny(listed, wanted []string) bool {
	return len(listed) == 0 || len(wanted) == 0 || slices.ContainsFunc(listed, func(v string) bool { return slices.Contains(wanted, v) })
}

func (f *Fake) CurseForgeFiles(_ context.Context, ids []int64) (map[int64]CFFile, error) {
	return pick(f.Files, ids), nil
}

func (f *Fake) CurseForgeMods(_ context.Context, ids []int64) (map[int64]CFMod, error) {
	return pick(f.Mods, ids), nil
}

func (f *Fake) CurseForgeSearch(_ context.Context, slug string) ([]CFMod, error) {
	return f.Search[slug], nil
}

func (f *Fake) CurseForgeModFiles(_ context.Context, modID int64, _ string) ([]CFFile, error) {
	return f.ModFiles[modID], nil
}

func (f *Fake) CurseForgeFingerprints(_ context.Context, fingerprints []uint32) (map[uint32]Match, error) {
	return pick(f.Fingerprints, fingerprints), nil
}

func (f *Fake) Download(_ context.Context, url string, newWriter func() (io.Writer, error)) error {
	f.mu.Lock()
	f.Downloads = append(f.Downloads, url)
	data, ok := f.URLs[url]
	f.mu.Unlock()
	if !ok {
		return fmt.Errorf("HTTP 404")
	}
	w, err := newWriter()
	if err != nil {
		return err
	}
	_, err = w.Write(data)
	return err
}

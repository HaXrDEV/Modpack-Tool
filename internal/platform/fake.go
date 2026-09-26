package platform

import (
	"context"
	"fmt"
	"io"
	"sync"
)

// Fake is an API with canned answers, for tests.
type Fake struct {
	mu              sync.Mutex
	Versions        map[string]Version
	Projects        map[string]Project
	ProjectVersions map[string][]Version // By project id.
	Files           map[int64]CFFile
	Mods            map[int64]CFMod
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

func (f *Fake) ModrinthProjectVersions(_ context.Context, projectID string, _, _ []string) ([]Version, error) {
	return f.ProjectVersions[projectID], nil
}

func (f *Fake) CurseForgeFiles(_ context.Context, ids []int64) (map[int64]CFFile, error) {
	return pick(f.Files, ids), nil
}

func (f *Fake) CurseForgeMods(_ context.Context, ids []int64) (map[int64]CFMod, error) {
	return pick(f.Mods, ids), nil
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

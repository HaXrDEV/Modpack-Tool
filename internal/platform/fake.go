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

func (f *Fake) ModrinthVersions(_ context.Context, ids []string) (map[string]Version, error) {
	found := map[string]Version{}
	for _, id := range ids {
		if v, ok := f.Versions[id]; ok {
			found[id] = v
		}
	}
	return found, nil
}

func (f *Fake) ModrinthProjects(_ context.Context, ids []string) (map[string]Project, error) {
	found := map[string]Project{}
	for _, id := range ids {
		if p, ok := f.Projects[id]; ok {
			found[id] = p
		}
	}
	return found, nil
}

func (f *Fake) ModrinthProjectVersions(_ context.Context, projectID string, _, _ []string) ([]Version, error) {
	return f.ProjectVersions[projectID], nil
}

func (f *Fake) CurseForgeFiles(_ context.Context, ids []int64) (map[int64]CFFile, error) {
	found := map[int64]CFFile{}
	for _, id := range ids {
		if file, ok := f.Files[id]; ok {
			found[id] = file
		}
	}
	return found, nil
}

func (f *Fake) CurseForgeMods(_ context.Context, ids []int64) (map[int64]CFMod, error) {
	found := map[int64]CFMod{}
	for _, id := range ids {
		if m, ok := f.Mods[id]; ok {
			found[id] = m
		}
	}
	return found, nil
}

func (f *Fake) CurseForgeFingerprints(_ context.Context, fingerprints []uint32) (map[uint32]Match, error) {
	found := map[uint32]Match{}
	for _, fp := range fingerprints {
		if m, ok := f.Fingerprints[fp]; ok {
			found[fp] = m
		}
	}
	return found, nil
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

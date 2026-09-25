// Package platform talks to the Modrinth and CurseForge APIs and downloads
// files. Batch endpoints are used throughout, so a whole pack needs one or two
// requests instead of one per mod.
package platform

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/HaXrDEV/Modpack-Tool/internal/fail"
)

// UserAgent identifies the tool to the APIs.
const UserAgent = "HaXrDEV/Modpack-Tool (+https://github.com/HaXrDEV/Modpack-Tool)"

// packwizCurseForgeKey is packwiz's public community key; a key in the config replaces it.
var packwizCurseForgeKey = func() string {
	key, _ := base64.StdEncoding.DecodeString("JDJhJDEwJHNBWVhqblU1N0EzSmpzcmJYM3JVdk92UWk2NHBLS3BnQ2VpbGc1TUM1UGNKL0RYTmlGWWxh")
	return string(key)
}()

// CurseForgeReleaseTypes maps CurseForge releaseType values to channels.
var CurseForgeReleaseTypes = map[int]string{1: "release", 2: "beta", 3: "alpha"}

// Version is a Modrinth version.
type Version struct {
	ID            string       `json:"id"`
	ProjectID     string       `json:"project_id"`
	VersionNumber string       `json:"version_number"`
	VersionType   string       `json:"version_type"`
	GameVersions  []string     `json:"game_versions"`
	Loaders       []string     `json:"loaders"`
	Files         []File       `json:"files"`
	Dependencies  []Dependency `json:"dependencies"`
}

// File is one file of a Modrinth version.
type File struct {
	URL      string            `json:"url"`
	Filename string            `json:"filename"`
	Primary  bool              `json:"primary"`
	Size     int64             `json:"size"`
	Hashes   map[string]string `json:"hashes"`
}

// Dependency is a Modrinth version dependency.
type Dependency struct {
	ProjectID      string `json:"project_id"`
	DependencyType string `json:"dependency_type"`
}

// Project is a Modrinth project.
type Project struct {
	ID         string   `json:"id"`
	Categories []string `json:"categories"`
}

// CFFile is a CurseForge file.
type CFFile struct {
	ID              int64          `json:"id"`
	ModID           int64          `json:"modId"`
	FileName        string         `json:"fileName"`
	ReleaseType     int            `json:"releaseType"`
	DownloadURL     string         `json:"downloadUrl"`
	GameVersions    []string       `json:"gameVersions"`
	Dependencies    []CFDependency `json:"dependencies"`
	FileFingerprint uint32         `json:"fileFingerprint"`
}

// CFDependency is a CurseForge file dependency; relationType 3 means required.
type CFDependency struct {
	ModID        int64 `json:"modId"`
	RelationType int   `json:"relationType"`
}

// CFMod is a CurseForge project.
type CFMod struct {
	ID         int64 `json:"id"`
	Categories []struct {
		Name string `json:"name"`
		Slug string `json:"slug"`
	} `json:"categories"`
}

// Match is a CurseForge project and file that a fingerprint belongs to.
type Match struct{ ProjectID, FileID int64 }

// API is what the workflows need from the platforms; tests use a fake.
type API interface {
	ModrinthVersions(ctx context.Context, ids []string) (map[string]Version, error)
	ModrinthProjects(ctx context.Context, ids []string) (map[string]Project, error)
	ModrinthProjectVersions(ctx context.Context, projectID string, gameVersions, loaders []string) ([]Version, error)
	CurseForgeFiles(ctx context.Context, ids []int64) (map[int64]CFFile, error)
	CurseForgeMods(ctx context.Context, ids []int64) (map[int64]CFMod, error)
	CurseForgeFingerprints(ctx context.Context, fingerprints []uint32) (map[uint32]Match, error)
	// Download writes a file to the writer that newWriter returns; it is called
	// again (and must start over) when a download is retried.
	Download(ctx context.Context, url string, newWriter func() (io.Writer, error)) error
}

// Client is the real API client.
type Client struct {
	HTTP          *http.Client
	Modrinth      string
	CurseForge    string
	CurseForgeKey string
	Sleep         func(ctx context.Context, d time.Duration) error
}

// NewClient returns a client for the public APIs; key is a personal
// CurseForge API key or "" for packwiz's.
func NewClient(key string) *Client {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.MaxConnsPerHost = 8
	transport.ResponseHeaderTimeout = 60 * time.Second
	return &Client{
		HTTP:          &http.Client{Transport: transport},
		Modrinth:      "https://api.modrinth.com/v2",
		CurseForge:    "https://api.curseforge.com/v1",
		CurseForgeKey: key,
		Sleep:         sleep,
	}
}

func sleep(ctx context.Context, d time.Duration) error {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func (c *Client) cfHeaders() map[string]string {
	key := c.CurseForgeKey
	if key == "" {
		key = packwizCurseForgeKey
	}
	return map[string]string{"x-api-key": key, "Accept": "application/json"}
}

// request sends a request with retries for rate limits, network hiccups and
// server errors, and decodes the JSON answer into out. It returns false when
// the resource doesn't exist (404).
func (c *Client) request(ctx context.Context, method, target string, body any, headers map[string]string, out any) (bool, error) {
	var payload []byte
	if body != nil {
		var err error
		if payload, err = json.Marshal(body); err != nil {
			return false, err
		}
	}
	for attempt := 0; attempt < 5; attempt++ {
		req, err := http.NewRequestWithContext(ctx, method, target, bytes.NewReader(payload))
		if err != nil {
			return false, err
		}
		req.Header.Set("User-Agent", UserAgent)
		if body != nil {
			req.Header.Set("Content-Type", "application/json")
		}
		for key, value := range headers {
			req.Header.Set(key, value)
		}
		resp, err := c.HTTP.Do(req)
		if err != nil {
			if ctx.Err() != nil {
				return false, ctx.Err()
			}
			if attempt == 4 {
				return false, fail.Wrapf(err, "Network error talking to %s: %v", target, err)
			}
			if err := c.Sleep(ctx, backoff(attempt)); err != nil {
				return false, err
			}
			continue
		}
		data, err := io.ReadAll(resp.Body)
		resp.Body.Close()
		switch {
		case err != nil:
			if attempt == 4 {
				return false, fail.Wrapf(err, "Network error talking to %s: %v", target, err)
			}
			err = c.Sleep(ctx, backoff(attempt))
		case resp.StatusCode == http.StatusTooManyRequests:
			err = c.Sleep(ctx, retryAfter(resp.Header, attempt))
		case resp.StatusCode == http.StatusNotFound:
			return false, nil
		case resp.StatusCode >= 500 && attempt < 4:
			err = c.Sleep(ctx, backoff(attempt))
		case resp.StatusCode >= 400:
			text := string(data)
			if len(text) > 200 {
				text = text[:200]
			}
			return false, fail.Errorf("%s %s returned HTTP %d: %s", method, target, resp.StatusCode, text)
		default:
			if err := json.Unmarshal(data, out); err != nil {
				return false, fail.Wrapf(err, "%s %s returned something that isn't JSON: %v", method, target, err)
			}
			return true, nil
		}
		if err != nil {
			return false, err
		}
	}
	return false, fail.Errorf("%s kept rate-limiting requests; try again in a minute.", target)
}

func backoff(attempt int) time.Duration { return time.Duration(1<<attempt) * time.Second }

// retryAfter reads how long a rate limit asks to wait, clamped to 1-60 s.
func retryAfter(header http.Header, attempt int) time.Duration {
	wait := backoff(attempt)
	for _, name := range []string{"Retry-After", "X-Ratelimit-Reset"} {
		value := strings.TrimSpace(header.Get(name))
		if value == "" {
			continue
		}
		if seconds, err := strconv.ParseFloat(value, 64); err == nil {
			wait = time.Duration(seconds * float64(time.Second))
		} else if at, err := http.ParseTime(value); err == nil {
			wait = time.Until(at)
		}
		break
	}
	return min(max(wait, time.Second), time.Minute)
}

func uniqueSorted[T string | int64 | uint32](values []T) []T {
	seen := map[T]bool{}
	var result []T
	for _, v := range values {
		var zero T
		if v != zero && !seen[v] {
			seen[v] = true
			result = append(result, v)
		}
	}
	sort.Slice(result, func(i, j int) bool { return result[i] < result[j] })
	return result
}

func chunks[T any](values []T, size int) [][]T {
	var result [][]T
	for start := 0; start < len(values); start += size {
		result = append(result, values[start:min(start+size, len(values))])
	}
	return result
}

// ModrinthVersions returns {version id: version} for the given ids.
func (c *Client) ModrinthVersions(ctx context.Context, ids []string) (map[string]Version, error) {
	found := map[string]Version{}
	for _, chunk := range chunks(uniqueSorted(ids), 100) {
		encoded, _ := json.Marshal(chunk)
		var versions []Version
		if _, err := c.request(ctx, "GET", c.Modrinth+"/versions?ids="+url.QueryEscape(string(encoded)), nil, nil, &versions); err != nil {
			return nil, err
		}
		for _, v := range versions {
			found[v.ID] = v
		}
	}
	return found, nil
}

// ModrinthProjects returns {project id: project} for the given ids.
func (c *Client) ModrinthProjects(ctx context.Context, ids []string) (map[string]Project, error) {
	found := map[string]Project{}
	for _, chunk := range chunks(uniqueSorted(ids), 100) {
		encoded, _ := json.Marshal(chunk)
		var projects []Project
		if _, err := c.request(ctx, "GET", c.Modrinth+"/projects?ids="+url.QueryEscape(string(encoded)), nil, nil, &projects); err != nil {
			return nil, err
		}
		for _, p := range projects {
			found[p.ID] = p
		}
	}
	return found, nil
}

// ModrinthProjectVersions returns a project's versions, newest first,
// filtered by game version and loader.
func (c *Client) ModrinthProjectVersions(ctx context.Context, projectID string, gameVersions, loaders []string) ([]Version, error) {
	query := url.Values{}
	if len(gameVersions) > 0 {
		encoded, _ := json.Marshal(gameVersions)
		query.Set("game_versions", string(encoded))
	}
	if len(loaders) > 0 {
		encoded, _ := json.Marshal(loaders)
		query.Set("loaders", string(encoded))
	}
	target := c.Modrinth + "/project/" + url.PathEscape(projectID) + "/version"
	if len(query) > 0 {
		target += "?" + query.Encode()
	}
	var versions []Version
	if _, err := c.request(ctx, "GET", target, nil, nil, &versions); err != nil {
		return nil, err
	}
	return versions, nil
}

// CurseForgeFiles returns {file id: file} for the given CurseForge file ids.
func (c *Client) CurseForgeFiles(ctx context.Context, ids []int64) (map[int64]CFFile, error) {
	found := map[int64]CFFile{}
	for _, chunk := range chunks(uniqueSorted(ids), 250) {
		var answer struct{ Data []CFFile }
		if _, err := c.request(ctx, "POST", c.CurseForge+"/mods/files", map[string]any{"fileIds": chunk}, c.cfHeaders(), &answer); err != nil {
			return nil, err
		}
		for _, f := range answer.Data {
			found[f.ID] = f
		}
	}
	return found, nil
}

// CurseForgeMods returns {project id: project} for the given CurseForge project ids.
func (c *Client) CurseForgeMods(ctx context.Context, ids []int64) (map[int64]CFMod, error) {
	found := map[int64]CFMod{}
	for _, chunk := range chunks(uniqueSorted(ids), 250) {
		var answer struct{ Data []CFMod }
		if _, err := c.request(ctx, "POST", c.CurseForge+"/mods", map[string]any{"modIds": chunk}, c.cfHeaders(), &answer); err != nil {
			return nil, err
		}
		for _, m := range answer.Data {
			found[m.ID] = m
		}
	}
	return found, nil
}

// CurseForgeFingerprints returns {fingerprint: match} for the fingerprints
// CurseForge knows exactly.
func (c *Client) CurseForgeFingerprints(ctx context.Context, fingerprints []uint32) (map[uint32]Match, error) {
	found := map[uint32]Match{}
	for _, chunk := range chunks(uniqueSorted(fingerprints), 50) {
		var answer struct {
			Data struct {
				ExactMatches []struct {
					File CFFile `json:"file"`
				} `json:"exactMatches"`
				ExactFingerprints []uint32 `json:"exactFingerprints"`
			} `json:"data"`
		}
		if _, err := c.request(ctx, "POST", c.CurseForge+"/fingerprints", map[string]any{"fingerprints": chunk}, c.cfHeaders(), &answer); err != nil {
			return nil, err
		}
		for i, match := range answer.Data.ExactMatches {
			file := match.File
			if file.ID == 0 || file.ModID == 0 {
				continue
			}
			// The file names its fingerprint; exactFingerprints[i] is the fallback.
			fingerprint := file.FileFingerprint
			if fingerprint == 0 && i < len(answer.Data.ExactFingerprints) {
				fingerprint = answer.Data.ExactFingerprints[i]
			}
			found[fingerprint] = Match{ProjectID: file.ModID, FileID: file.ID}
		}
	}
	return found, nil
}

// Download fetches a file with the same kind of retries as the API calls.
func (c *Client) Download(ctx context.Context, target string, newWriter func() (io.Writer, error)) error {
	for attempt := 0; attempt < 4; attempt++ {
		err := c.downloadOnce(ctx, target, newWriter)
		var retry retryable
		switch {
		case err == nil:
			return nil
		case ctx.Err() != nil:
			return ctx.Err()
		case !errors.As(err, &retry):
			return err
		case attempt == 3:
			return fail.Errorf("Downloading %s failed: %v", target, retry.err)
		}
		if err := c.Sleep(ctx, backoff(attempt)); err != nil {
			return err
		}
	}
	return fail.Errorf("Downloading %s kept failing; try again later.", target)
}

type retryable struct{ err error }

func (r retryable) Error() string { return r.err.Error() }

func (c *Client) downloadOnce(ctx context.Context, target string, newWriter func() (io.Writer, error)) error {
	req, err := http.NewRequestWithContext(ctx, "GET", RequoteURL(target), nil)
	if err != nil {
		return fail.Wrapf(err, "Downloading %s failed: %v", target, err)
	}
	req.Header.Set("User-Agent", UserAgent)
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return retryable{err}
	}
	defer resp.Body.Close()
	switch resp.StatusCode {
	case http.StatusOK:
	case 429, 500, 502, 503, 504:
		return retryable{fmt.Errorf("HTTP %d", resp.StatusCode)}
	default:
		return fail.Errorf("Downloading %s failed: HTTP %d", target, resp.StatusCode)
	}
	w, err := newWriter()
	if err != nil {
		return err
	}
	if _, err := io.Copy(w, resp.Body); err != nil {
		var netErr net.Error
		if errors.As(err, &netErr) || errors.Is(err, io.ErrUnexpectedEOF) {
			return retryable{err}
		}
		return err
	}
	return nil
}

// RequoteURL escapes the characters requests would escape in a download URL
// (CurseForge download URLs can contain spaces and other raw characters).
func RequoteURL(target string) string {
	var b strings.Builder
	for i := 0; i < len(target); i++ {
		c := target[i]
		switch {
		case c == '%' && i+2 < len(target) && isHex(target[i+1]) && isHex(target[i+2]):
			b.WriteByte(c)
		case c <= ' ' || c >= 0x7f || strings.IndexByte(`"<>\^`+"`"+`{|}%`, c) >= 0:
			fmt.Fprintf(&b, "%%%02X", c)
		default:
			b.WriteByte(c)
		}
	}
	return b.String()
}

func isHex(c byte) bool {
	return c >= '0' && c <= '9' || c >= 'a' && c <= 'f' || c >= 'A' && c <= 'F'
}

// AllowedChannels are the channels an update may land on: alpha only when
// the mod is already on alpha.
func AllowedChannels(current string) map[string]bool {
	if current == "alpha" {
		return map[string]bool{"release": true, "beta": true, "alpha": true}
	}
	return map[string]bool{"release": true, "beta": true}
}

// Murmur2 is CurseForge's file fingerprint: murmur2 (seed 1) over the file
// without its whitespace bytes.
func Murmur2(data []byte) uint32 {
	filtered := make([]byte, 0, len(data))
	for _, b := range data {
		if b != 9 && b != 10 && b != 13 && b != 32 {
			filtered = append(filtered, b)
		}
	}
	const m = 0x5bd1e995
	length := uint32(len(filtered))
	h := 1 ^ length
	body := len(filtered) - len(filtered)%4
	for i := 0; i < body; i += 4 {
		k := uint32(filtered[i]) | uint32(filtered[i+1])<<8 | uint32(filtered[i+2])<<16 | uint32(filtered[i+3])<<24
		k *= m
		k ^= k >> 24
		k *= m
		h = h*m ^ k
	}
	tail := filtered[body:]
	switch len(tail) {
	case 3:
		h ^= uint32(tail[2]) << 16
		fallthrough
	case 2:
		h ^= uint32(tail[1]) << 8
		fallthrough
	case 1:
		h ^= uint32(tail[0])
		h *= m
	}
	h ^= h >> 13
	h *= m
	h ^= h >> 15
	return h
}

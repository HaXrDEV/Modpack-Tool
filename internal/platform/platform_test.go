package platform

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// py: test_platforms.py::test_murmur2_known_answers
func TestMurmur2KnownAnswers(t *testing.T) {
	// Values from the original implementation (same as CurseForge and mmc-export).
	cases := map[string]uint32{
		"hello world": 2824650221,
		"The quick brown fox\njumps over\tthe lazy dog": 3751777527,
		"": 1540447798,
	}
	for text, want := range cases {
		if got := Murmur2([]byte(text)); got != want {
			t.Errorf("Murmur2(%q) = %d, want %d", text, got, want)
		}
	}
}

// py: test_platforms.py::test_whitespace_is_ignored
func TestMurmur2IgnoresWhitespace(t *testing.T) {
	if Murmur2([]byte("a b\tc\r\nd")) != Murmur2([]byte("abcd")) {
		t.Error("whitespace changed the fingerprint")
	}
}

// py: test_platforms.py::test_allowed_channels
func TestAllowedChannels(t *testing.T) {
	if len(AllowedChannels("release")) != 2 || AllowedChannels("release")["alpha"] || !AllowedChannels("alpha")["alpha"] {
		t.Error("channels")
	}
}

func testClient(server *httptest.Server) *Client {
	c := NewClient("")
	c.Modrinth, c.CurseForge = server.URL, server.URL
	c.Sleep = func(context.Context, time.Duration) error { return nil }
	return c
}

func TestRetriesRateLimitsAndServerErrors(t *testing.T) {
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch calls.Add(1) {
		case 1:
			w.Header().Set("Retry-After", "0")
			w.WriteHeader(429)
		case 2:
			w.WriteHeader(502)
		default:
			if !strings.Contains(r.URL.RawQuery, "ids=") || r.Header.Get("User-Agent") != UserAgent {
				t.Errorf("request %s", r.URL)
			}
			json.NewEncoder(w).Encode([]Version{{ID: "a", VersionType: "beta"}})
		}
	}))
	defer server.Close()
	versions, err := testClient(server).ModrinthVersions(context.Background(), []string{"a", "a", ""})
	if err != nil || versions["a"].VersionType != "beta" || calls.Load() != 3 {
		t.Errorf("%v %v %d", versions, err, calls.Load())
	}
}

func TestNotFoundIsAbsentAndClientErrorsFail(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.Contains(r.URL.Path, "/project/") {
			w.WriteHeader(404)
			return
		}
		w.WriteHeader(403)
		io.WriteString(w, "forbidden")
	}))
	defer server.Close()
	c := testClient(server)
	if versions, err := c.ModrinthProjectVersions(context.Background(), "x", []string{"1.21"}, []string{"fabric"}); err != nil || len(versions) != 0 {
		t.Error(versions, err)
	}
	if _, err := c.CurseForgeFiles(context.Background(), []int64{1}); err == nil || !strings.Contains(err.Error(), "HTTP 403: forbidden") {
		t.Error(err)
	}
}

func TestFingerprintMatches(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("x-api-key") == "" {
			t.Error("no CurseForge key")
		}
		io.WriteString(w, `{"data": {"exactMatches": [{"file": {"id": 5, "modId": 6, "fileFingerprint": 77}},
			{"file": {"id": 7, "modId": 8}}], "exactFingerprints": [77, 99]}}`)
	}))
	defer server.Close()
	matches, err := testClient(server).CurseForgeFingerprints(context.Background(), []uint32{77, 99})
	if err != nil || matches[77] != (Match{6, 5}) || matches[99] != (Match{8, 7}) {
		t.Error(matches, err)
	}
}

// An upload CurseForge offers for download wins over other copies of the same
// file that it doesn't (archived, deleted or still under review), whichever
// comes first; a file with no such upload still matches.
func TestFingerprintMatchesPreferAnAvailableUpload(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.WriteString(w, `{"data": {"exactMatches": [
			{"file": {"id": 1, "modId": 9, "fileFingerprint": 77, "isAvailable": false}},
			{"file": {"id": 2, "modId": 9, "fileFingerprint": 77, "isAvailable": true}},
			{"file": {"id": 3, "modId": 9, "fileFingerprint": 77, "isAvailable": false}},
			{"file": {"id": 4, "modId": 8, "fileFingerprint": 88, "isAvailable": false}}]}}`)
	}))
	defer server.Close()
	matches, err := testClient(server).CurseForgeFingerprints(context.Background(), []uint32{77, 88})
	if err != nil || matches[77] != (Match{9, 2}) || matches[88] != (Match{8, 4}) {
		t.Error(matches, err)
	}
}

// Teams come back as one member list per team, keyed by the team id.
func TestModrinthTeams(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/teams" || r.URL.Query().Get("ids") != `["t1","t2"]` {
			t.Error(r.URL)
		}
		io.WriteString(w, `[[{"team_id": "t1", "user": {"username": "alice"}, "role": "Owner", "ordering": 0}], []]`)
	}))
	defer server.Close()
	teams, err := testClient(server).ModrinthTeams(context.Background(), []string{"t2", "t1", "t1", ""})
	if err != nil || len(teams) != 1 || teams["t1"][0].User.Username != "alice" || teams["t1"][0].Role != "Owner" {
		t.Error(teams, err)
	}
}

func TestDownloadRestartsAfterAFailure(t *testing.T) {
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if calls.Add(1) == 1 {
			w.WriteHeader(503)
			return
		}
		io.WriteString(w, "jar bytes "+r.URL.Path)
	}))
	defer server.Close()
	var buf bytes.Buffer
	err := testClient(server).Download(context.Background(), server.URL+"/My Mod.jar", func() (io.Writer, error) {
		buf.Reset()
		return &buf, nil
	})
	if err != nil || buf.String() != "jar bytes /My Mod.jar" {
		t.Errorf("%q %v", buf.String(), err)
	}
}

func TestRequoteURL(t *testing.T) {
	cases := map[string]string{
		"https://edge.forgecdn.net/files/1/2/My Mod [1.0].jar": "https://edge.forgecdn.net/files/1/2/My%20Mod%20[1.0].jar",
		"https://x/a%20b%zz.jar":                               "https://x/a%20b%25zz.jar",
		"https://x/ünï.jar":                                    "https://x/%C3%BCn%C3%AF.jar",
	}
	for in, want := range cases {
		if got := RequoteURL(in); got != want {
			t.Errorf("RequoteURL(%q) = %q, want %q", in, got, want)
		}
	}
}

package pycompat

import (
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"testing"
)

type golden struct {
	Dumps []struct {
		Input  string `json:"input"`
		Indent int    `json:"indent"`
		ASCII  bool   `json:"ascii"`
		Output string `json:"output"`
	} `json:"dumps"`
	Lines []struct {
		Text       string   `json:"text"`
		SplitLines []string `json:"splitlines"`
		Strip      string   `json:"strip"`
		Split      []string `json:"split"`
	} `json:"lines"`
	Paths []struct {
		Path   string `json:"path"`
		Suffix string `json:"suffix"`
		Stem   string `json:"stem"`
	} `json:"paths"`
	Capitalize []struct {
		Text       string `json:"text"`
		Capitalize string `json:"capitalize"`
	} `json:"capitalize"`
}

func loadGolden(t *testing.T) golden {
	t.Helper()
	data, err := os.ReadFile("testdata/golden.json")
	if err != nil {
		t.Fatal(err)
	}
	var g golden
	if err := json.Unmarshal(data, &g); err != nil {
		t.Fatal(err)
	}
	return g
}

func TestDumpsMatchesPython(t *testing.T) {
	for _, c := range loadGolden(t).Dumps {
		value, err := Loads([]byte(c.Input))
		if err != nil {
			t.Fatal(err)
		}
		got, err := Dumps(value, c.Indent, c.ASCII)
		if err != nil {
			t.Fatal(err)
		}
		if string(got) != c.Output {
			t.Errorf("Dumps(%s, indent=%d, ascii=%v)\n got %q\nwant %q", c.Input, c.Indent, c.ASCII, got, c.Output)
		}
	}
}

func TestTextMatchesPython(t *testing.T) {
	g := loadGolden(t)
	for _, c := range g.Lines {
		if got := SplitLines(c.Text); !slices.Equal(got, c.SplitLines) && !(len(got) == 0 && len(c.SplitLines) == 0) {
			t.Errorf("SplitLines(%q) = %q, want %q", c.Text, got, c.SplitLines)
		}
		if got := Strip(c.Text); got != c.Strip {
			t.Errorf("Strip(%q) = %q, want %q", c.Text, got, c.Strip)
		}
		if got := Fields(c.Text); !slices.Equal(got, c.Split) && !(len(got) == 0 && len(c.Split) == 0) {
			t.Errorf("Fields(%q) = %q, want %q", c.Text, got, c.Split)
		}
	}
	for _, c := range g.Paths {
		if got := Suffix(c.Path); got != c.Suffix {
			t.Errorf("Suffix(%q) = %q, want %q", c.Path, got, c.Suffix)
		}
		if got := Stem(c.Path); got != c.Stem {
			t.Errorf("Stem(%q) = %q, want %q", c.Path, got, c.Stem)
		}
	}
	for _, c := range g.Capitalize {
		if got := Capitalize(c.Text); got != c.Capitalize {
			t.Errorf("Capitalize(%q) = %q, want %q", c.Text, got, c.Capitalize)
		}
	}
}

func TestObjectKeepsOrder(t *testing.T) {
	value, err := Loads([]byte(`{"z": 1, "a": {"y": [1, 2.50], "b": null}}`))
	if err != nil {
		t.Fatal(err)
	}
	object := value.(Object)
	object.Set("modpackVersion", "2.0")
	object.Set("z", 3)
	got, _ := Dumps(object, NoIndent, true)
	if want := `{"z": 3, "a": {"y": [1, 2.50], "b": null}, "modpackVersion": "2.0"}`; string(got) != want {
		t.Errorf("got %s", got)
	}
}

func TestFormat(t *testing.T) {
	values := map[string]string{"mc_group": "1.21", "anchor": "v1.2.0"}
	if got, err := Format("https://x/{mc_group}#{anchor}{{}}", values); err != nil || got != "https://x/1.21#v1.2.0{}" {
		t.Errorf("got %q, %v", got, err)
	}
	for _, bad := range []string{"{nope}", "{}", "{0}", "a}", "{mc_group"} {
		if _, err := Format(bad, values); err == nil {
			t.Errorf("Format(%q) should fail", bad)
		}
	}
}

// py: test_pack.py::test_write_text_keeps_existing_line_endings
func TestWriteTextKeepsExistingLineEndings(t *testing.T) {
	dir := t.TempDir()
	crlf := filepath.Join(dir, "a.md")
	os.WriteFile(crlf, []byte("one\r\ntwo\r\n"), 0o644)
	if err := WriteText(crlf, "three\nfour\n"); err != nil {
		t.Fatal(err)
	}
	if data, _ := os.ReadFile(crlf); string(data) != "three\r\nfour\r\n" {
		t.Errorf("CRLF file: %q", data)
	}
	lf := filepath.Join(dir, "b.md")
	os.WriteFile(lf, []byte("one\n"), 0o644)
	WriteText(lf, "x\ny\n")
	if data, _ := os.ReadFile(lf); string(data) != "x\ny\n" {
		t.Errorf("LF file: %q", data)
	}
	fresh := filepath.Join(dir, "c.md")
	WriteText(fresh, "new\n")
	if data, _ := os.ReadFile(fresh); string(data) != "new"+NewFileNewline {
		t.Errorf("new file: %q", data)
	}
}

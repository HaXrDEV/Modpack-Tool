// Package pycompat reproduces the Python behaviors that the files this tool
// writes depend on. The Python version of the tool wrote release records,
// changelogs and pack files that are committed to the packs; matching its
// text handling exactly keeps those files byte-identical.
package pycompat

import (
	"os"
	"runtime"
	"slices"
	"sort"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/HaXrDEV/Modpack-Tool/internal/files"
)

// IsSpace is Python's str.isspace for one character: Unicode whitespace plus
// the ASCII separators \x1c-\x1f.
func IsSpace(r rune) bool {
	return unicode.IsSpace(r) || (r >= 0x1c && r <= 0x1f)
}

// Strip is Python's str.strip().
func Strip(s string) string {
	return strings.TrimFunc(s, IsSpace)
}

// Fields is Python's str.split() without arguments.
func Fields(s string) []string {
	return strings.FieldsFunc(s, IsSpace)
}

// SplitLines is Python's str.splitlines(): it splits at every line boundary
// Python knows and drops a trailing empty line.
func SplitLines(s string) []string {
	var lines []string
	start := 0
	for i := 0; i < len(s); {
		r, size := utf8.DecodeRuneInString(s[i:])
		switch r {
		case '\n', '\v', '\f', 0x1c, 0x1d, 0x1e, 0x85, 0x2028, 0x2029:
			lines = append(lines, s[start:i])
			start = i + size
		case '\r':
			lines = append(lines, s[start:i])
			if i+1 < len(s) && s[i+1] == '\n' {
				size = 2
			}
			start = i + size
		}
		i += size
	}
	if start < len(s) {
		lines = append(lines, s[start:])
	}
	return lines
}

// DecodeUTF8 is bytes.decode("utf-8", errors="replace"): each invalid byte
// becomes U+FFFD.
func DecodeUTF8(data []byte) string {
	if utf8.Valid(data) {
		return string(data)
	}
	var b strings.Builder
	for len(data) > 0 {
		r, size := utf8.DecodeRune(data)
		if r == utf8.RuneError && size <= 1 {
			b.WriteRune(utf8.RuneError)
			size = 1
		} else {
			b.WriteRune(r)
		}
		data = data[size:]
	}
	return b.String()
}

// IsDigits is Python's str.isdigit() for ASCII text: true for a non-empty
// string of digits.
func IsDigits(s string) bool {
	if s == "" {
		return false
	}
	for i := 0; i < len(s); i++ {
		if s[i] < '0' || s[i] > '9' {
			return false
		}
	}
	return true
}

// Capitalize is Python's str.capitalize(): the first character in title case,
// the rest in lower case.
func Capitalize(s string) string {
	r, size := utf8.DecodeRuneInString(s)
	if size == 0 {
		return s
	}
	return string(unicode.ToTitle(r)) + strings.ToLower(s[size:])
}

// SortLower sorts in place by lowercase text, keeping the order of equal
// keys, like Python's sorted(values, key=str.lower).
func SortLower(values []string) {
	SortLowerBy(values, func(v string) string { return v })
}

// SortLowerBy is SortLower for items sorted by a text key.
func SortLowerBy[T any](items []T, key func(T) string) {
	sort.SliceStable(items, func(i, j int) bool {
		return strings.ToLower(key(items[i])) < strings.ToLower(key(items[j]))
	})
}

// SortedLower returns a copy sorted by lowercase text with ties in plain text
// order, like Python's sorted(sorted(values), key=str.lower): the result
// doesn't depend on the order of values.
func SortedLower(values []string) []string {
	sorted := slices.Clone(values)
	slices.Sort(sorted)
	SortLower(sorted)
	return sorted
}

// Name is the last component of a slash-separated path, like PurePosixPath.name.
func Name(path string) string {
	path = strings.TrimRight(path, "/")
	return path[strings.LastIndex(path, "/")+1:]
}

// Suffix is PurePosixPath(path).suffix: "a/b.tar.gz" -> ".gz", while ".json"
// and "name." have none.
func Suffix(path string) string {
	name := Name(path)
	if i := strings.LastIndex(name, "."); i > 0 && i < len(name)-1 {
		return name[i:]
	}
	return ""
}

// Stem is PurePosixPath(path).stem: the name without its suffix.
func Stem(path string) string {
	name := Name(path)
	if i := strings.LastIndex(name, "."); i > 0 && i < len(name)-1 {
		return name[:i]
	}
	return name
}

// NewFileNewline is the line ending of newly created text files: CRLF on
// Windows, as Python's text mode writes them.
var NewFileNewline = func() string {
	if runtime.GOOS == "windows" {
		return "\r\n"
	}
	return "\n"
}()

// ReadText reads a text file the way Python's text mode does: every line
// ending becomes "\n".
func ReadText(path string) (string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	return UniversalNewlines(string(data)), nil
}

// UniversalNewlines turns "\r\n" and lone "\r" into "\n".
func UniversalNewlines(text string) string {
	if !strings.Contains(text, "\r") {
		return text
	}
	return strings.ReplaceAll(strings.ReplaceAll(text, "\r\n", "\n"), "\r", "\n")
}

// WriteText writes text (with "\n" line breaks), keeping an existing file's
// line endings: CRLF when it has any, else LF. New files get NewFileNewline.
func WriteText(path, text string) error {
	newline := NewFileNewline
	if current, err := files.ReadIfExists(path); err == nil && current != nil {
		newline = "\n"
		if strings.Contains(string(current), "\r\n") {
			newline = "\r\n"
		}
	}
	if newline != "\n" {
		text = strings.ReplaceAll(text, "\n", newline)
	}
	return files.WriteAtomic(path, []byte(text))
}

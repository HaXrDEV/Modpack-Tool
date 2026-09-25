package pack

import (
	"fmt"
	"reflect"
	"regexp"
	"strconv"
	"strings"

	"github.com/BurntSushi/toml"
)

// An Edit sets Key in Table ("" is the root table) to Value, a TOML literal
// such as `"both(disabled)"`.
type Edit struct {
	Table, Key, Value string
}

// Quote returns s as a TOML basic string.
func Quote(s string) string {
	var b strings.Builder
	b.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"':
			b.WriteString(`\"`)
		case '\\':
			b.WriteString(`\\`)
		case '\b':
			b.WriteString(`\b`)
		case '\t':
			b.WriteString(`\t`)
		case '\n':
			b.WriteString(`\n`)
		case '\f':
			b.WriteString(`\f`)
		case '\r':
			b.WriteString(`\r`)
		default:
			if r < 0x20 || r == 0x7f {
				fmt.Fprintf(&b, `\u%04X`, r)
			} else {
				b.WriteRune(r)
			}
		}
	}
	b.WriteByte('"')
	return b.String()
}

var (
	tableHeader = regexp.MustCompile(`^\s*\[\s*([^\[\]]+?)\s*\]\s*(#.*)?$`)
	arrayHeader = regexp.MustCompile(`^\s*\[\[`)
	keyLine     = regexp.MustCompile(`^(\s*)([A-Za-z0-9_-]+|"(?:[^"\\]|\\.)*")(\s*=\s*)`)
)

// line is one line of a TOML document and what it holds.
type line struct {
	text    string // Without the line ending.
	ending  string
	table   string // The table the line belongs to.
	key     string // Set on the first line of a key/value pair.
	valueAt int    // Where the value starts in text.
	lastOf  int    // Index of the pair's last line (multi-line values).
	header  bool
}

// EditTOML applies edits to a TOML document's text, changing nothing else:
// an existing value is replaced in place (spacing and comments stay), and a
// missing key is added after its table's last key, before blank lines, as
// tomlkit does. The result is decoded again and refused unless exactly the
// intended keys changed.
func EditTOML(text string, edits []Edit) (string, error) {
	lines, err := splitTOML(text)
	if err != nil {
		return "", err
	}
	newline := "\n"
	if strings.Contains(text, "\r\n") {
		newline = "\r\n"
	}
	for _, edit := range edits {
		lines, err = applyEdit(lines, edit, newline)
		if err != nil {
			return "", err
		}
	}
	var out strings.Builder
	for _, l := range lines {
		out.WriteString(l.text)
		out.WriteString(l.ending)
	}
	result := out.String()
	if err := verifyEdits(text, result, edits); err != nil {
		return "", err
	}
	return result, nil
}

func splitTOML(text string) ([]line, error) {
	var lines []line
	for rest := text; rest != ""; {
		l := line{text: rest, lastOf: -1}
		if i := strings.IndexByte(rest, '\n'); i >= 0 {
			l.text, l.ending, rest = rest[:i], "\n", rest[i+1:]
			if strings.HasSuffix(l.text, "\r") {
				l.text, l.ending = l.text[:len(l.text)-1], "\r\n"
			}
		} else {
			rest = ""
		}
		lines = append(lines, l)
	}
	table := ""
	for i := 0; i < len(lines); i++ {
		l := &lines[i]
		if m := tableHeader.FindStringSubmatch(l.text); m != nil && !arrayHeader.MatchString(l.text) {
			table = normalizeTable(m[1])
			l.table, l.header = table, true
			continue
		}
		if arrayHeader.MatchString(l.text) {
			table = "[[array]]" // Never edited.
			l.table, l.header = table, true
			continue
		}
		l.table = table
		m := keyLine.FindStringSubmatchIndex(l.text)
		if m == nil {
			continue
		}
		key := l.text[m[4]:m[5]]
		if strings.HasPrefix(key, `"`) {
			unquoted, err := strconv.Unquote(key)
			if err != nil {
				return nil, err
			}
			key = unquoted
		}
		l.key, l.valueAt = key, m[1]
		// A value can continue over several lines: multi-line strings and arrays.
		last, err := valueEnd(lines, i, m[1])
		if err != nil {
			return nil, err
		}
		l.lastOf = last
		for j := i + 1; j <= last; j++ {
			lines[j].table = table
		}
		i = last
	}
	return lines, nil
}

func normalizeTable(name string) string {
	parts := strings.Split(name, ".")
	for i, part := range parts {
		part = strings.TrimSpace(part)
		if unquoted, err := strconv.Unquote(part); err == nil {
			part = unquoted
		}
		parts[i] = part
	}
	return strings.Join(parts, ".")
}

// valueEnd returns the index of the line where the value starting at
// lines[i].text[start:] ends.
func valueEnd(lines []line, i, start int) (int, error) {
	text := lines[i].text[start:]
	for _, quote := range []string{`"""`, `'''`} {
		if strings.HasPrefix(text, quote) {
			if strings.Contains(text[3:], quote) {
				return i, nil
			}
			for j := i + 1; j < len(lines); j++ {
				if strings.Contains(lines[j].text, quote) {
					return j, nil
				}
			}
			return 0, fmt.Errorf("unterminated multi-line string")
		}
	}
	depth := 0
	for j := i; j < len(lines); j++ {
		if j > i {
			text = lines[j].text
		}
		inString := byte(0)
		for k := 0; k < len(text); k++ {
			c := text[k]
			switch {
			case inString != 0:
				if c == '\\' && inString == '"' {
					k++
				} else if c == inString {
					inString = 0
				}
			case c == '"' || c == '\'':
				inString = c
			case c == '#':
				k = len(text)
			case c == '[' || c == '{':
				depth++
			case c == ']' || c == '}':
				depth--
			}
		}
		if depth <= 0 {
			return j, nil
		}
	}
	return 0, fmt.Errorf("unterminated array")
}

func applyEdit(lines []line, edit Edit, newline string) ([]line, error) {
	// A new key goes after the table's last key or comment, before blank lines.
	lastContent, header := -1, -1
	for i := 0; i < len(lines); i++ {
		l := &lines[i]
		if l.table != edit.Table {
			continue
		}
		if l.header {
			header = i
			continue
		}
		if l.key == "" {
			if strings.TrimSpace(l.text) != "" {
				lastContent = i
			}
			continue
		}
		if l.key == edit.Key {
			if l.lastOf != i {
				return nil, fmt.Errorf("%s spans several lines", edit.Key)
			}
			l.text = l.text[:l.valueAt] + edit.Value + valueRest(l.text[l.valueAt:])
			return lines, nil
		}
		lastContent = l.lastOf
		i = l.lastOf
	}
	after := lastContent
	if after < 0 {
		after = header
	}
	if after < 0 && edit.Table != "" {
		return nil, fmt.Errorf("there is no [%s] table", edit.Table)
	}
	added := line{text: edit.Key + " = " + edit.Value, ending: newline, table: edit.Table, key: edit.Key,
		valueAt: len(edit.Key) + 3}
	if after >= 0 && lines[after].ending == "" {
		lines[after].ending = newline // The file didn't end with a line break.
	}
	lines = append(lines[:after+1], append([]line{added}, lines[after+1:]...)...)
	for i := range lines {
		if lines[i].lastOf > after {
			lines[i].lastOf++
		}
	}
	lines[after+1].lastOf = after + 1
	return lines, nil
}

// valueRest returns what follows a single-line value: spacing and a comment.
func valueRest(text string) string {
	inString, depth := byte(0), 0
	for k := 0; k < len(text); k++ {
		c := text[k]
		switch {
		case inString != 0:
			if c == '\\' && inString == '"' {
				k++
			} else if c == inString {
				inString = 0
			}
		case c == '"' || c == '\'':
			inString = c
		case c == '[' || c == '{':
			depth++
		case c == ']' || c == '}':
			depth--
		case depth == 0 && (c == ' ' || c == '\t' || c == '#'):
			return text[k:]
		}
	}
	return ""
}

func verifyEdits(before, after string, edits []Edit) error {
	want := map[string]any{}
	if _, err := toml.Decode(before, &want); err != nil {
		return err
	}
	for _, edit := range edits {
		var holder map[string]any
		if _, err := toml.Decode("v = "+edit.Value, &holder); err != nil {
			return fmt.Errorf("bad value for %s: %w", edit.Key, err)
		}
		table := want
		if edit.Table != "" {
			for _, part := range strings.Split(edit.Table, ".") {
				next, ok := table[part].(map[string]any)
				if !ok {
					next = map[string]any{}
					table[part] = next
				}
				table = next
			}
		}
		table[edit.Key] = holder["v"]
	}
	got := map[string]any{}
	if _, err := toml.Decode(after, &got); err != nil {
		return fmt.Errorf("the edit broke the file: %w", err)
	}
	if !reflect.DeepEqual(got, want) {
		return fmt.Errorf("the edit changed more than intended")
	}
	return nil
}

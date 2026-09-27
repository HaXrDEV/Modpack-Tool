package pycompat

import (
	"errors"
	"fmt"
	"io"
	"regexp"
	"strings"

	"go.yaml.in/yaml/v3"
)

// ParseYAML parses text as a single YAML document, as ruamel's load does:
// a second document or a repeated top-level key is an error. yaml.v3 alone
// keeps the first document and the last value of a key when parsing into a
// Node, so the rest would be dropped silently.
func ParseYAML(text string) (yaml.Node, error) {
	decoder := yaml.NewDecoder(strings.NewReader(text))
	var doc, next yaml.Node
	if err := decoder.Decode(&doc); err != nil && !errors.Is(err, io.EOF) {
		return yaml.Node{}, err
	}
	if err := decoder.Decode(&next); err == nil {
		return yaml.Node{}, fmt.Errorf("yaml: line %d: expected a single document, but found another", next.Line)
	} else if !errors.Is(err, io.EOF) {
		return yaml.Node{}, err
	}
	if len(doc.Content) > 0 && doc.Content[0].Kind == yaml.MappingNode {
		lines := map[string]int{}
		content := doc.Content[0].Content
		for i := 0; i+1 < len(content); i += 2 {
			key := content[i]
			if line, ok := lines[key.Value]; ok {
				return yaml.Node{}, fmt.Errorf("yaml: line %d: mapping key %q already defined at line %d", key.Line, key.Value, line)
			}
			lines[key.Value] = key.Line
		}
	}
	return doc, nil
}

// These reproduce the scalar styles ruamel.yaml (round-trip mode, YAML 1.2,
// allow_unicode) writes, for the YAML files the tool generates.

var (
	yamlBool      = regexp.MustCompile(`^(?:true|True|TRUE|false|False|FALSE)$`)
	yamlInt       = regexp.MustCompile(`^(?:[-+]?0b[0-1_]+|[-+]?0o?[0-7_]+|[-+]?[0-9_]+|[-+]?0x[0-9a-fA-F_]+)$`)
	yamlFloat     = regexp.MustCompile(`^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+]?[0-9]+)?|[-+]?(?:[0-9][0-9_]*)(?:[eE][-+]?[0-9]+)|\.[0-9_]+(?:[eE][-+][0-9]+)?|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$`)
	yamlNull      = regexp.MustCompile(`^(?:~|null|Null|NULL|)$`)
	yamlTimestamp = regexp.MustCompile(`^(?:[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]|[0-9][0-9][0-9][0-9]-[0-9][0-9]?-[0-9][0-9]?(?:[Tt]|[ \t]+)[0-9][0-9]?:[0-9][0-9]:[0-9][0-9](?:\.[0-9]*)?(?:[ \t]*(?:Z|[-+][0-9][0-9]?(?::[0-9][0-9])?))?)$`)
)

// yamlResolvesAsString reports whether text written plain reads back as a string.
func yamlResolvesAsString(text string) bool {
	return !(yamlBool.MatchString(text) || yamlInt.MatchString(text) || yamlFloat.MatchString(text) ||
		yamlNull.MatchString(text) || yamlTimestamp.MatchString(text) || text == "<<")
}

func isBreak(r rune) bool { return r == '\n' || r == 0x85 || r == 0x2028 || r == 0x2029 }

func isPrintable(r rune) bool {
	return r == '\n' || (r >= 0x20 && r <= 0x7e) || r == 0x85 || (r >= 0xa0 && r <= 0xd7ff) ||
		(r >= 0xe000 && r <= 0xfffd && r != 0xfeff) || (r >= 0x10000 && r < 0x10ffff)
}

// yamlPlainAllowed is ruamel's analyze_scalar: can text be written plain, in
// block context or inside a flow collection?
func yamlPlainAllowed(text string, flow bool) bool {
	if text == "" {
		return false
	}
	runes := []rune(text)
	if strings.HasPrefix(text, "---") || strings.HasPrefix(text, "...") {
		return false
	}
	flowIndicators, blockIndicators := false, false
	lineBreaks, special := false, false
	leadingSpace, leadingBreak, trailingSpace, trailingBreak := false, false, false, false
	breakSpace, spaceBreak := false, false
	previousSpace, previousBreak := false, false
	precededByWhitespace := true
	for i, r := range runes {
		followedByWhitespace := i+1 >= len(runes) || strings.ContainsRune("\x00 \t\r\n\u0085\u2028\u2029", runes[i+1])
		if i == 0 {
			if strings.ContainsRune("#,[]{}&*!|>'\"%@`", r) {
				flowIndicators, blockIndicators = true, true
			}
			if r == '?' || r == ':' {
				if len(runes) == 1 {
					flowIndicators = true
				}
				if followedByWhitespace {
					blockIndicators = true
				}
			}
			if r == '-' && followedByWhitespace {
				flowIndicators, blockIndicators = true, true
			}
		} else {
			if strings.ContainsRune(",[]{}", r) {
				flowIndicators = true
			}
			if r == ':' && followedByWhitespace {
				flowIndicators, blockIndicators = true, true
			}
			if r == '#' && precededByWhitespace {
				flowIndicators, blockIndicators = true, true
			}
		}
		if isBreak(r) {
			lineBreaks = true
		}
		if !isPrintable(r) {
			special = true
		}
		switch {
		case r == ' ':
			if i == 0 {
				leadingSpace = true
			}
			if i == len(runes)-1 {
				trailingSpace = true
			}
			if previousBreak {
				breakSpace = true
			}
			previousSpace, previousBreak = true, false
		case isBreak(r):
			if i == 0 {
				leadingBreak = true
			}
			if i == len(runes)-1 {
				trailingBreak = true
			}
			if previousSpace {
				spaceBreak = true
			}
			previousSpace, previousBreak = false, true
		default:
			previousSpace, previousBreak = false, false
		}
		precededByWhitespace = strings.ContainsRune("\x00 \t\r\n\u0085\u2028\u2029", r)
	}
	allowed := !(leadingSpace || leadingBreak || trailingSpace || trailingBreak || breakSpace || special ||
		spaceBreak || lineBreaks)
	if flow {
		return allowed && !flowIndicators
	}
	return allowed && !blockIndicators
}

// YAMLScalar writes a string the way ruamel does when it may pick the style:
// plain when that reads back as the same string, double-quoted when the text
// has a single quote or a line break, else single-quoted.
func YAMLScalar(text string, flow bool) string {
	if yamlResolvesAsString(text) && yamlPlainAllowed(text, flow) {
		return text
	}
	if strings.ContainsAny(text, "'\n") || strings.ContainsFunc(text, func(r rune) bool { return !isPrintable(r) || isBreak(r) }) {
		return YAMLDoubleQuoted(text)
	}
	return YAMLSingleQuoted(text)
}

// YAMLSingleQuoted writes 'text' with quotes doubled.
func YAMLSingleQuoted(text string) string {
	return "'" + strings.ReplaceAll(text, "'", "''") + "'"
}

var yamlEscapes = map[rune]string{
	0: `\0`, 0x07: `\a`, 0x08: `\b`, 0x09: `\t`, 0x0a: `\n`, 0x0b: `\v`, 0x0c: `\f`, 0x0d: `\r`,
	0x1b: `\e`, '"': `\"`, '\\': `\\`, 0x85: `\N`, 0xa0: `\_`, 0x2028: `\L`, 0x2029: `\P`,
}

// YAMLDoubleQuoted writes "text" with ruamel's escapes.
func YAMLDoubleQuoted(text string) string {
	var b strings.Builder
	b.WriteByte('"')
	for _, r := range text {
		escape := strings.ContainsRune("\"\\\u0085\u2028\u2029\ufeff", r) ||
			!((r >= 0x20 && r <= 0x7e) || (r >= 0xa0 && r <= 0xd7ff) || (r >= 0xe000 && r <= 0xfffd))
		switch {
		case !escape:
			b.WriteRune(r)
		case yamlEscapes[r] != "":
			b.WriteString(yamlEscapes[r])
		case r <= 0xff:
			fmt.Fprintf(&b, `\x%02X`, r)
		case r <= 0xffff:
			fmt.Fprintf(&b, `\u%04X`, r)
		default:
			fmt.Fprintf(&b, `\U%08X`, r)
		}
	}
	b.WriteByte('"')
	return b.String()
}

// YAMLLiteral writes text as a literal block scalar: the "|" header (with
// ruamel's chomping and indentation hints) and the indented lines.
func YAMLLiteral(text, indent string) []string {
	hints := ""
	if strings.HasPrefix(text, " ") || strings.HasPrefix(text, "\n") {
		hints += fmt.Sprint(len(indent))
	}
	switch {
	case !strings.HasSuffix(text, "\n"):
		hints += "-"
	case text == "\n" || strings.HasSuffix(text, "\n\n"):
		hints += "+"
	}
	lines := []string{"|" + hints}
	for _, line := range strings.Split(strings.TrimSuffix(text, "\n"), "\n") {
		if line == "" {
			lines = append(lines, "")
		} else {
			lines = append(lines, indent+line)
		}
	}
	return lines
}

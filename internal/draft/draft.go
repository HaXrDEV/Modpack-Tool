// Package draft writes changelog text from a PackDiff: the "Update overview"
// and "Config Changes" sections. Everything here is deterministic; the drafts
// are a starting point to edit, not final wording.
package draft

import (
	"fmt"
	"regexp"
	"slices"
	"strings"

	"github.com/HaXrDEV/Modpack-Tool/internal/diff"
	"github.com/HaXrDEV/Modpack-Tool/internal/pack"
	"github.com/HaXrDEV/Modpack-Tool/internal/pycompat"
	"github.com/HaXrDEV/Modpack-Tool/internal/ui"
)

const maxBulletsPerFile = 4

// ws is Python's \s in str patterns (Unicode whitespace).
const ws = `[\t\n\v\f\r \x{1c}-\x{1f}\x{85}\x{a0}\x{1680}\x{2000}-\x{200a}\x{2028}\x{2029}\x{202f}\x{205f}\x{3000}]`

var (
	alphaBeta    = regexp.MustCompile(`(?i)\b(alpha|beta)\b`)
	nonKey       = regexp.MustCompile(`[^a-z0-9]+`)
	separators   = regexp.MustCompile(`[_\-.]+`)
	camelCase    = regexp.MustCompile(`([a-z0-9])([A-Z])`)
	trailingDash = regexp.MustCompile(ws + `*-` + ws + `*$`)
	arrayValue   = regexp.MustCompile(`^` + ws + `*"((?:[^"\\]|\\.)*)"` + ws + `*,?` + ws + `*$`)
	opener       = regexp.MustCompile(`^"?([A-Za-z0-9_.\-]+)"?` + ws + `*:` + ws + `*([\[{])`)
	quoted       = regexp.MustCompile(`"(?:[^"\\]|\\.)*"`)
	keyValue     = regexp.MustCompile(`^["']?([A-Za-z0-9_.\-]+)["']?` + ws + `*[:=]` + ws + `*(.+?)` + ws + `*,?` + ws + `*$`)
)

////////////////////////////////////////////////////////////
// Update overview

// quote wraps a name in single quotes. A non-breaking space after ": " stops
// YAML from quoting names such as "Mod: Addon".
func quote(name string) string {
	return "'" + strings.ReplaceAll(pycompat.Strip(name), ": ", ": ") + "'"
}

func quotedList(names []string) string {
	var quoted []string
	for _, name := range names {
		if pycompat.Strip(name) != "" {
			quoted = append(quoted, quote(name))
		}
	}
	switch len(quoted) {
	case 0, 1:
		return strings.Join(quoted, "")
	case 2:
		return quoted[0] + " & " + quoted[1]
	}
	return strings.Join(quoted[:len(quoted)-1], ", ") + " & " + quoted[len(quoted)-1]
}

func dedupe(values []string) []string {
	seen := map[string]bool{}
	var result []string
	for _, value := range values {
		key := strings.ToLower(pycompat.Strip(value))
		if key != "" && !seen[key] {
			seen[key] = true
			result = append(result, pycompat.Strip(value))
		}
	}
	return result
}

func names(entries []diff.Named) []string {
	var result []string
	for _, n := range entries {
		result = append(result, n.Name)
	}
	return result
}

// UpdateOverview is one sentence per kind of change, e.g. "Added 'Sodium' &
// 'Lithium' mods.".
func UpdateOverview(d *diff.PackDiff) []string {
	var lines []string
	if d.Migration() {
		lines = append(lines, "Updated to Minecraft "+d.Minecraft+".")
	}
	if mods := dedupe(d.NewlyAdded); len(mods) > 0 {
		lines = append(lines, fmt.Sprintf("Added %s mod%s.", quotedList(mods), ui.Plural(len(mods))))
	}
	if len(d.Reenabled) > 0 {
		if alphaBeta.MatchString(d.CurrentVersion) {
			lines = append(lines, "Re-added some mods that have become available for "+d.Minecraft+".")
		} else {
			lines = append(lines, "Re-added some mods.")
		}
	}
	if removed := dedupe(names(d.Mods.Removed)); len(removed) > 0 {
		if d.Migration() {
			// No ": " in the sentence, so YAML can keep it unquoted.
			lines = append(lines, fmt.Sprintf("Temporarily removed incompatible mod%s %s.", ui.Plural(len(removed)), quotedList(removed)))
		} else {
			lines = append(lines, fmt.Sprintf("Removed %s mod%s.", quotedList(removed), ui.Plural(len(removed))))
		}
	}
	var updated []string
	for _, c := range []struct {
		label string
		diff  diff.CategoryDiff
	}{{"mods", d.Mods}, {"resource packs", d.ResourcePacks}, {"shaderpacks", d.ShaderPacks}} {
		if len(c.diff.Updated) > 0 {
			updated = append(updated, c.label)
		}
	}
	switch len(updated) {
	case 0:
	case 1:
		lines = append(lines, "Updated "+updated[0]+".")
	case 2:
		lines = append(lines, "Updated "+updated[0]+" & "+updated[1]+".")
	default:
		lines = append(lines, "Updated "+strings.Join(updated[:len(updated)-1], ", ")+", & "+updated[len(updated)-1]+".")
	}
	for _, c := range []struct {
		diff             diff.CategoryDiff
		singular, plural string
	}{{d.ResourcePacks, "resource pack", "resource packs"}, {d.ShaderPacks, "shaderpack", "shaderpacks"}} {
		for _, v := range []struct {
			verb    string
			entries []diff.Named
		}{{"Added", c.diff.Added}, {"Removed", c.diff.Removed}} {
			if list := dedupe(names(v.entries)); len(list) > 0 {
				noun := c.plural
				if len(list) == 1 {
					noun = c.singular
				}
				lines = append(lines, fmt.Sprintf("%s %s %s.", v.verb, quotedList(list), noun))
			}
		}
	}
	if lines = dedupe(lines); len(lines) == 0 {
		return []string{"Maintenance update."}
	}
	return lines
}

////////////////////////////////////////////////////////////
// Mod labels for config files

func key(text string) string { return nonKey.ReplaceAllString(strings.ToLower(text), "") }

// TitleCaseFilename turns "myMod_config.json" into "My Mod Config".
func TitleCaseFilename(filename string) string {
	name := separators.ReplaceAllString(pycompat.Stem(filename), " ")
	name = camelCase.ReplaceAllString(name, "$1 $2")
	var words []string
	for _, token := range pycompat.Fields(name) {
		words = append(words, pycompat.Capitalize(token))
	}
	if len(words) == 0 {
		return "Unknown"
	}
	return strings.Join(words, " ")
}

type alias struct{ key, label string }

// Labels finds the mod a config file belongs to, by matching its path
// against mod names and slugs.
type Labels struct {
	index   map[string]string
	aliases []alias
}

// NewLabels indexes the active mods.
func NewLabels(mods []pack.Mod) *Labels {
	l := &Labels{index: map[string]string{}}
	for _, mod := range mods {
		if mod.Category() != "mods" || mod.Disabled() {
			continue
		}
		label := pycompat.Strip(trailingDash.ReplaceAllString(mod.DisplayName(), ""))
		if label == "" {
			label = mod.DisplayName()
		}
		for _, text := range []string{label, mod.Slug()} {
			if k := key(text); k != "" {
				if _, ok := l.index[k]; !ok {
					l.index[k] = label
				}
				l.aliases = append(l.aliases, alias{k, label})
			}
		}
	}
	return l
}

func (l *Labels) lookup(exact, loose []string) string {
	for _, k := range exact {
		if label, ok := l.index[k]; k != "" && ok {
			return label
		}
	}
	seen := map[string]bool{}
	for _, k := range loose {
		if k == "" || seen[k] {
			continue
		}
		seen[k] = true
		best, bestScore := "", 0
		for _, a := range l.aliases {
			if k == a.key {
				return a.label
			}
			// Containment either way; the shorter key's length scores the match.
			if strings.Contains(a.key, k) || strings.Contains(k, a.key) {
				if score := min(len(k), len(a.key)); score > bestScore {
					best, bestScore = a.label, score
				}
			}
		}
		if best != "" && bestScore >= 6 {
			return best
		}
	}
	return ""
}

// ForConfig is the mod label for a path relative to config/ ("yosbr/config/"
// prefixes are ignored).
func (l *Labels) ForConfig(path string) string {
	var parts []string
	for _, part := range strings.Split(strings.ReplaceAll(path, "\\", "/"), "/") {
		if part != "" {
			parts = append(parts, part)
		}
	}
	if len(parts) > 0 && strings.ToLower(parts[0]) == "yosbr" {
		if len(parts) > 1 && strings.ToLower(parts[1]) == "config" {
			parts = parts[2:]
		} else {
			parts = parts[1:]
		}
	}
	filename := path
	if len(parts) > 0 {
		filename = parts[len(parts)-1]
	}
	stem := pycompat.Stem(filename)
	parent, top := "", ""
	if len(parts) > 1 {
		parent, top = parts[len(parts)-2], parts[0]
	}
	if label := l.lookup([]string{key(top), key(stem), key(filename), key(parent)}, []string{key(top), key(stem)}); label != "" {
		return label
	}
	if top != "" {
		return TitleCaseFilename(top)
	}
	return TitleCaseFilename(filename)
}

////////////////////////////////////////////////////////////
// Config Changes

// arrayValueOf returns the string of a JSON array element line such as
// '"file/Pack.zip",'.
func arrayValueOf(line string) (string, bool) {
	if m := arrayValue.FindStringSubmatch(line); m != nil {
		return m[1], true
	}
	return "", false
}

func stripLineComment(line string) string {
	var out strings.Builder
	inString, escaped := false, false
	for i, c := range line {
		switch {
		case escaped:
			escaped = false
		case c == '\\' && inString:
			escaped = true
		case c == '"':
			inString = !inString
		case !inString && strings.HasPrefix(line[i:], "//"):
			return out.String()
		}
		out.WriteRune(c)
	}
	return out.String()
}

// arraySections maps each lowercased array value of a JSON/JSON5 file to the
// dotted names of the arrays containing it.
func arraySections(content, path string) map[string][]string {
	sections := map[string][]string{}
	if suffix := pycompat.Suffix(strings.ToLower(path)); suffix != ".json" && suffix != ".json5" {
		return sections
	}
	type frame struct{ kind, name string }
	var stack []frame
	pop := func(kind string) {
		for i := len(stack) - 1; i >= 0; i-- {
			if stack[i].kind == kind {
				stack = slices.Delete(stack, i, i+1)
				return
			}
		}
		if len(stack) > 0 {
			stack = stack[:len(stack)-1]
		}
	}
	for _, raw := range pycompat.SplitLines(content) {
		line := pycompat.Strip(stripLineComment(raw))
		if line == "" {
			continue
		}
		var path []string
		for _, f := range stack {
			if f.name != "" {
				path = append(path, f.name)
			}
		}
		if value, ok := arrayValueOf(line); ok && len(path) > 0 {
			k, joined := strings.ToLower(value), strings.Join(path, ".")
			if !slices.Contains(sections[k], joined) {
				sections[k] = append(sections[k], joined)
			}
		}
		if m := opener.FindStringSubmatch(line); m != nil {
			kind := "object"
			if m[2] == "[" {
				kind = "array"
			}
			stack = append(stack, frame{kind, m[1]})
		}
		for _, c := range quoted.ReplaceAllString(line, `""`) {
			switch c {
			case ']':
				pop("array")
			case '}':
				pop("object")
			}
		}
	}
	return sections
}

type pair struct{ key, value string }

func keyValues(lines []string) []pair {
	var pairs []pair
	for _, line := range lines {
		text := pycompat.Strip(line)
		if text == "" || hasAnyPrefix(text, "#", "//", ";", "/*", "*") {
			continue
		}
		if m := keyValue.FindStringSubmatch(text); m != nil && !strings.HasSuffix(m[2], "{") && !strings.HasSuffix(m[2], "[") {
			pairs = append(pairs, pair{m[1], m[2]})
		}
	}
	return pairs
}

func fileBullets(entry diff.LineDiff, label string) []string {
	path := entry.Path
	def := ""
	if diff.IsYOSBR(path) {
		def = "default "
	}
	if strings.Contains("/"+strings.ToLower(path)+"/", "/fancymenu/customization/") {
		return []string{"- Adjusted " + def + "FancyMenu customizations: [" + label + "]"}
	}
	var bullets []string
	valueSet := func(lines []string) map[string]bool {
		set := map[string]bool{}
		for _, line := range lines {
			if value, ok := arrayValueOf(line); ok {
				set[value] = true
			}
		}
		return set
	}
	addedValues, removedValues := valueSet(entry.AddedLines), valueSet(entry.RemovedLines)
	usedAdded, usedRemoved := map[int]bool{}, map[int]bool{}
	for _, side := range []struct {
		lines   []string
		used    map[int]bool
		content string
		verb    string
	}{{entry.AddedLines, usedAdded, entry.CurrentContent, "Added"}, {entry.RemovedLines, usedRemoved, entry.PreviousContent, "Removed"}} {
		var sections map[string][]string
		for i, line := range side.lines {
			value, ok := arrayValueOf(line)
			if !ok {
				continue
			}
			side.used[i] = true
			if sections == nil {
				sections = arraySections(side.content, path)
			}
			where := strings.Join(sections[strings.ToLower(value)], ", ")
			shown := strings.TrimPrefix(value, "file/")
			if addedValues[value] && removedValues[value] {
				if side.verb == "Added" { // The value only moved within its list.
					bullet := "- Reordered " + def + shown
					if where != "" {
						bullet += " in section " + where
					}
					bullets = append(bullets, bullet+": ["+label+"]")
				}
				continue
			}
			preposition := "to"
			if side.verb == "Removed" {
				preposition = "from"
			}
			bullet := "- " + side.verb + " " + def + shown
			if where != "" {
				bullet += " " + preposition + " section " + where
			}
			bullets = append(bullets, bullet+": ["+label+"]")
		}
	}
	unused := func(lines []string, used map[int]bool) []string {
		var result []string
		for i, line := range lines {
			if !used[i] {
				result = append(result, line)
			}
		}
		return result
	}
	removedPairs := keyValues(unused(entry.RemovedLines, usedRemoved))
	for _, added := range keyValues(unused(entry.AddedLines, usedAdded)) {
		if match := slices.IndexFunc(removedPairs, func(p pair) bool { return p.key == added.key }); match >= 0 {
			old := removedPairs[match]
			removedPairs = slices.Delete(removedPairs, match, match+1)
			if old.value != added.value {
				bullets = append(bullets, fmt.Sprintf(`- Changed %s"%s" from %s to %s: [%s]`, def, added.key, old.value, added.value, label))
			}
		} else {
			bullets = append(bullets, fmt.Sprintf(`- Set %s"%s" to %s: [%s]`, def, added.key, added.value, label))
		}
	}
	for _, p := range removedPairs {
		bullets = append(bullets, fmt.Sprintf(`- Removed the "%s" setting: [%s]`, p.key, label))
	}
	bullets = unique(bullets) // The same value can be added to several lists.
	if len(bullets) == 0 {
		return []string{"- Updated " + def + "config values: [" + label + "]"}
	}
	if len(bullets) > maxBulletsPerFile {
		extra := len(bullets) - maxBulletsPerFile
		bullets = append(bullets[:maxBulletsPerFile],
			fmt.Sprintf("- …and %d more change%s in %s: [%s]", extra, ui.Plural(extra), pycompat.Name(path), label))
	}
	return bullets
}

// ConfigChanges are the Config Changes bullets, each ending with
// ": [Mod Label]" (the wiki shows labels as code).
func ConfigChanges(d *diff.PackDiff, labels *Labels) []string {
	config := d.Config
	var bullets []string
	movedFrom := map[string]bool{}
	for _, move := range config.MovedToYOSBR {
		bullets = append(bullets, "- Moved "+move.From+" to YOSBR so it applies as a default on first launch: ["+
			labels.ForConfig(move.To)+"]")
		movedFrom[strings.ToLower(move.From)] = true
	}
	for _, path := range config.Removed {
		if !movedFrom[strings.ToLower(path)] {
			bullets = append(bullets, "- Removed config file "+path+": ["+labels.ForConfig(path)+"]")
		}
	}
	for _, entry := range config.LineDiffs {
		bullets = append(bullets, fileBullets(entry, labels.ForConfig(entry.Path))...)
	}
	return bullets
}

func unique(values []string) []string {
	seen := map[string]bool{}
	var result []string
	for _, v := range values {
		if !seen[v] {
			seen[v] = true
			result = append(result, v)
		}
	}
	return result
}

func hasAnyPrefix(text string, prefixes ...string) bool {
	for _, p := range prefixes {
		if strings.HasPrefix(text, p) {
			return true
		}
	}
	return false
}

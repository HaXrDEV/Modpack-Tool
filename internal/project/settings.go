package project

import (
	_ "embed"
	"errors"
	"fmt"
	"io/fs"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"unicode"

	"go.yaml.in/yaml/v3"

	"github.com/HaXrDEV/Modpack-Tool/internal/fail"
	"github.com/HaXrDEV/Modpack-Tool/internal/files"
	"github.com/HaXrDEV/Modpack-Tool/internal/pack"
	"github.com/HaXrDEV/Modpack-Tool/internal/pycompat"
)

// SettingsFile is each pack's settings file, next to its Packwiz folder.
const SettingsFile = "modpack-tool.yml"

// LegacySettingsFile is the old tool's settings, imported once.
const LegacySettingsFile = "settings.yml"

// ExportKinds are the packs Build release can create.
var ExportKinds = []string{"curseforge", "modrinth", "server"}

// AlphaPolicies are the values of alpha_updates.
var AlphaPolicies = []string{"prompt", "never", "always"}

// PrereleaseModes are the values of prereleases.
var PrereleaseModes = []string{"standalone", "previews"}

//go:embed settings_template.yml
var templateText string

// Settings are one pack's settings.
type Settings struct {
	Exports               []string
	CurseForgeExclude     []string
	ModrinthExclude       []string
	ServerTemplate        string
	ServerExclude         []string
	MCPrefixedVersions    bool
	Prereleases           string
	AlphaUpdates          string
	SideTags              bool
	ChangelogURL          string
	CurseForgeNotesFooter string
	ModrinthNotesFooter   string
}

// DefaultSettings are the values used for anything the file leaves out.
func DefaultSettings() Settings {
	return Settings{Exports: []string{"curseforge", "modrinth"}, CurseForgeExclude: []string{}, ModrinthExclude: []string{},
		ServerTemplate: "Server Pack", ServerExclude: []string{}, Prereleases: "standalone", AlphaUpdates: "prompt"}
}

// ExcludeNotes notes the exclude entries that match none of mods (all of the
// pack's files, disabled ones too), like the import of old settings does. An
// entry goes stale when its mod is renamed or updated to a new filename, and
// the export includes the mod again.
func (s Settings) ExcludeNotes(mods []pack.Mod) []string {
	var notes []string
	for _, list := range []struct {
		key     string
		entries []string
	}{{"curseforge_exclude", s.CurseForgeExclude}, {"modrinth_exclude", s.ModrinthExclude}, {"server_exclude", s.ServerExclude}} {
		for _, entry := range list.entries {
			if !slices.ContainsFunc(mods, func(mod pack.Mod) bool { return mod.Matches(entry) }) {
				notes = append(notes, fmt.Sprintf("%s: '%s' matches no current mod; check it.", list.key, entry))
			}
		}
	}
	return notes
}

// DefaultChangelogURL is the CrismPack wiki link for a pack.
func DefaultChangelogURL(packName string) string {
	slug, _, _ := strings.Cut(strings.ToLower(packName), " ")
	if slug == "" {
		slug = "pack"
	}
	return "https://crismpack.net/" + slug + "/changelogs/{mc_group}#{anchor}"
}

// AskFunc asks the user a question with a default answer.
type AskFunc func(question, def string) (string, error)

// templateEntry is one setting in the template: the comment lines above it
// and its default line.
type templateEntry struct {
	before []string
	key    string
	line   string
	value  *yaml.Node
}

var templateKey = regexp.MustCompile(`^([a-z_]+):`)

func parseTemplate() ([]templateEntry, []string) {
	var entries []templateEntry
	var pending []string
	var doc yaml.Node
	text := pycompat.UniversalNewlines(templateText)
	if err := yaml.Unmarshal([]byte(text), &doc); err != nil {
		panic(err) // The embedded template is part of the program.
	}
	_, values := mapping(&doc)
	for _, line := range strings.Split(strings.TrimSuffix(text, "\n"), "\n") {
		if m := templateKey.FindStringSubmatch(line); m != nil {
			entries = append(entries, templateEntry{before: pending, key: m[1], line: line, value: values[m[1]]})
			pending = nil
		} else {
			pending = append(pending, line)
		}
	}
	return entries, pending
}

// mapping returns the top-level keys of a YAML document in order, and their values.
func mapping(doc *yaml.Node) ([]string, map[string]*yaml.Node) {
	var keys []string
	values := map[string]*yaml.Node{}
	if doc.Kind == yaml.DocumentNode && len(doc.Content) > 0 && doc.Content[0].Kind == yaml.MappingNode {
		content := doc.Content[0].Content
		for i := 0; i+1 < len(content); i += 2 {
			keys = append(keys, content[i].Value)
			values[content[i].Value] = content[i+1]
		}
	}
	return keys, values
}

// LoadSettings loads modpack-tool.yml, creating it on first use (from the old
// tool's settings.yml when there is one), and returns the settings plus notes
// for the user. The file is kept in the template's layout (all keys,
// comments, order) while preserving your values, exactly as the Python tool
// wrote it.
func LoadSettings(root, packName string, ask AskFunc) (Settings, []string, error) {
	path := filepath.Join(root, SettingsFile)
	var notes []string
	values := map[string]*yaml.Node{}
	var keys []string
	current, err := pycompat.ReadText(path)
	switch {
	case err == nil:
		var doc yaml.Node
		if err := yaml.Unmarshal([]byte(current), &doc); err != nil {
			return Settings{}, nil, fail.Wrapf(err, "%s isn't valid YAML: %v\n"+
				"Tip: put Windows paths in single quotes, e.g. 'D:\\Servers\\Pack'.", path, err)
		}
		if doc.Kind == yaml.DocumentNode && len(doc.Content) > 0 && doc.Content[0].Kind != yaml.MappingNode &&
			!(doc.Content[0].Kind == yaml.ScalarNode && doc.Content[0].Tag == "!!null") {
			return Settings{}, nil, fail.Errorf("%s should contain settings such as 'exports: [curseforge]'.", path)
		}
		keys, values = mapping(&doc)
	case errors.Is(err, fs.ErrNotExist):
		legacy := filepath.Join(root, LegacySettingsFile)
		if files.Exists(legacy) {
			values, err = importLegacySettings(legacy, root, packName, ask, &notes)
			if err != nil {
				return Settings{}, nil, err
			}
		} else {
			values = map[string]*yaml.Node{"changelog_url": stringNode(DefaultChangelogURL(packName), 0)}
			notes = append(notes, fmt.Sprintf("Created %s; review it to configure this pack.", SettingsFile))
		}
	default:
		return Settings{}, nil, err
	}

	entries, trailer := parseTemplate()
	known := map[string]bool{}
	var lines []string
	effective := map[string]*yaml.Node{}
	for _, entry := range entries {
		known[entry.key] = true
		lines = append(lines, entry.before...)
		if node, ok := values[entry.key]; ok {
			lines = append(lines, renderSetting(entry, node)...)
			effective[entry.key] = node
		} else {
			lines = append(lines, entry.line)
			effective[entry.key] = entry.value
		}
	}
	lines = append(lines, trailer...)
	rendered := strings.Join(lines, "\n") + "\n"
	if rendered != current {
		if err := pycompat.WriteText(path, rendered); err != nil {
			return Settings{}, nil, err
		}
	}
	var unknown []string
	for _, key := range keys {
		if !known[key] {
			unknown = append(unknown, key)
		}
	}
	if len(unknown) > 0 {
		notes = append(notes, fmt.Sprintf("Removed keys that aren't settings from %s: %s", SettingsFile, strings.Join(unknown, ", ")))
	}
	settings := coerceSettings(effective, &notes)
	return settings, notes, nil
}

// renderSetting writes one setting the way ruamel dumped it: the user's value
// in the template's style (flow lists, double-quoted strings, True/False,
// literal blocks for multi-line text) unless the user chose a quoting style.
func renderSetting(entry templateEntry, node *yaml.Node) []string {
	key := entry.key + ":"
	switch node.Kind {
	case yaml.SequenceNode:
		templateFlow := entry.value != nil && entry.value.Style&yaml.FlowStyle != 0 && len(entry.value.Content) > 0
		flow := node.Style&yaml.FlowStyle != 0 && len(node.Content) > 0 || templateFlow || len(node.Content) == 0
		items := make([]string, len(node.Content))
		for i, item := range node.Content {
			items[i] = renderItem(item, flow)
		}
		if flow {
			return []string{key + " [" + strings.Join(items, ", ") + "]"}
		}
		lines := []string{key}
		for _, item := range items {
			lines = append(lines, "- "+item)
		}
		return lines
	case yaml.MappingNode:
		var items []string
		for i := 0; i+1 < len(node.Content); i += 2 {
			items = append(items, renderItem(node.Content[i], true)+": "+renderItem(node.Content[i+1], true))
		}
		return []string{key + " {" + strings.Join(items, ", ") + "}"}
	}
	switch node.Tag {
	case "!!null":
		return []string{key}
	case "!!bool":
		return []string{key + " " + boolText(node)}
	case "!!int", "!!float":
		return []string{key + " " + node.Value}
	}
	switch {
	case node.Style&yaml.DoubleQuotedStyle != 0:
		return []string{key + " " + pycompat.YAMLDoubleQuoted(node.Value)}
	case node.Style&yaml.SingleQuotedStyle != 0:
		return []string{key + " " + pycompat.YAMLSingleQuoted(node.Value)}
	case node.Style&(yaml.LiteralStyle|yaml.FoldedStyle) != 0, strings.Contains(node.Value, "\n"):
		block := pycompat.YAMLLiteral(node.Value, "  ")
		return append([]string{key + " " + block[0]}, block[1:]...)
	case entry.value != nil && entry.value.Style&yaml.DoubleQuotedStyle != 0:
		return []string{key + " " + pycompat.YAMLDoubleQuoted(node.Value)}
	}
	return []string{key + " " + pycompat.YAMLScalar(node.Value, false)}
}

func renderItem(node *yaml.Node, flow bool) string {
	switch node.Kind {
	case yaml.SequenceNode, yaml.MappingNode:
		lines := renderSetting(templateEntry{key: ""}, node)
		return strings.TrimPrefix(lines[0], ": ")
	}
	switch node.Tag {
	case "!!null":
		return "null"
	case "!!bool":
		return boolText(node)
	case "!!int", "!!float":
		return node.Value
	}
	switch {
	case node.Style&yaml.DoubleQuotedStyle != 0:
		return pycompat.YAMLDoubleQuoted(node.Value)
	case node.Style&yaml.SingleQuotedStyle != 0:
		return pycompat.YAMLSingleQuoted(node.Value)
	}
	return pycompat.YAMLScalar(node.Value, flow)
}

func boolText(node *yaml.Node) string {
	var value bool
	if err := node.Decode(&value); err == nil && value {
		return "True"
	}
	return "False"
}

// nodeValue decodes a node into plain Go values, as ruamel hands them to Python.
func nodeValue(node *yaml.Node) any {
	if node == nil {
		return nil
	}
	var value any
	if err := node.Decode(&value); err != nil {
		return node.Value
	}
	return normalizeValue(value)
}

func normalizeValue(value any) any {
	switch v := value.(type) {
	case int:
		return int64(v)
	case []any:
		for i := range v {
			v[i] = normalizeValue(v[i])
		}
	case map[string]any:
		for key := range v {
			v[key] = normalizeValue(v[key])
		}
	}
	return value
}

// coerceSettings turns the effective values into Settings, like the Python
// tool's _coerce: empty means the default, and wrong types fall back with a note.
func coerceSettings(values map[string]*yaml.Node, notes *[]string) Settings {
	s := DefaultSettings()
	s.Exports = coerceList(nodeValue(values["exports"]), s.Exports)
	s.CurseForgeExclude = coerceList(nodeValue(values["curseforge_exclude"]), s.CurseForgeExclude)
	s.ModrinthExclude = coerceList(nodeValue(values["modrinth_exclude"]), s.ModrinthExclude)
	s.ServerTemplate = coerceString("server_template", nodeValue(values["server_template"]), s.ServerTemplate, notes)
	s.ServerExclude = coerceList(nodeValue(values["server_exclude"]), s.ServerExclude)
	s.MCPrefixedVersions = coerceBool("mc_prefixed_versions", nodeValue(values["mc_prefixed_versions"]), s.MCPrefixedVersions, notes)
	s.Prereleases = coerceString("prereleases", nodeValue(values["prereleases"]), s.Prereleases, notes)
	s.AlphaUpdates = coerceString("alpha_updates", nodeValue(values["alpha_updates"]), s.AlphaUpdates, notes)
	s.SideTags = coerceBool("side_tags", nodeValue(values["side_tags"]), s.SideTags, notes)
	s.ChangelogURL = coerceString("changelog_url", nodeValue(values["changelog_url"]), s.ChangelogURL, notes)
	s.CurseForgeNotesFooter = coerceString("curseforge_notes_footer", nodeValue(values["curseforge_notes_footer"]), "", notes)
	s.ModrinthNotesFooter = coerceString("modrinth_notes_footer", nodeValue(values["modrinth_notes_footer"]), "", notes)

	var bad, good []string
	for _, kind := range s.Exports {
		if slices.Contains(ExportKinds, kind) {
			good = append(good, kind)
		} else {
			bad = append(bad, kind)
		}
	}
	if len(bad) > 0 {
		*notes = append(*notes, fmt.Sprintf("Unknown exports ignored: %s (use %s).", strings.Join(bad, ", "), strings.Join(ExportKinds, ", ")))
		if good == nil {
			good = []string{}
		}
		s.Exports = good
	}
	if !slices.Contains(PrereleaseModes, s.Prereleases) {
		*notes = append(*notes, fmt.Sprintf("prereleases '%s' is not one of %s; using standalone.", s.Prereleases, strings.Join(PrereleaseModes, ", ")))
		s.Prereleases = "standalone"
	}
	if !slices.Contains(AlphaPolicies, s.AlphaUpdates) {
		*notes = append(*notes, fmt.Sprintf("alpha_updates '%s' is not one of %s; using prompt.", s.AlphaUpdates, strings.Join(AlphaPolicies, ", ")))
		s.AlphaUpdates = "prompt"
	}
	return s
}

func isEmpty(value any) bool {
	s, ok := value.(string)
	return value == nil || (ok && s == "")
}

func coerceBool(name string, value any, def bool, notes *[]string) bool {
	if isEmpty(value) {
		return def
	}
	if b, ok := value.(bool); ok {
		return b
	}
	switch strings.ToLower(pycompat.Strip(pycompat.Str(value))) {
	case "true", "yes", "on", "1":
		return true
	case "false", "no", "off", "0":
		return false
	}
	*notes = append(*notes, fmt.Sprintf("%s: '%s' isn't True or False; using %s.", name, pycompat.Str(value), pycompat.Str(def)))
	return def
}

func coerceList(value any, def []string) []string {
	if isEmpty(value) {
		return def
	}
	values, ok := value.([]any)
	if !ok {
		values = []any{value}
	}
	result := []string{}
	for _, entry := range values {
		if text := pycompat.Strip(pycompat.Str(entry)); text != "" {
			result = append(result, text)
		}
	}
	return result
}

func coerceString(name string, value any, def string, notes *[]string) string {
	if isEmpty(value) {
		return def
	}
	switch value.(type) {
	case []any, map[string]any:
		*notes = append(*notes, fmt.Sprintf("%s should be a single value; using the default.", name))
		return def
	}
	return pycompat.Str(value)
}

func stringNode(value string, style yaml.Style) *yaml.Node {
	return &yaml.Node{Kind: yaml.ScalarNode, Tag: "!!str", Value: value, Style: style}
}

func boolNode(value bool) *yaml.Node {
	text := "false"
	if value {
		text = "true"
	}
	return &yaml.Node{Kind: yaml.ScalarNode, Tag: "!!bool", Value: text}
}

func listNode(values []string) *yaml.Node {
	node := &yaml.Node{Kind: yaml.SequenceNode, Tag: "!!seq"}
	for _, value := range values {
		node.Content = append(node.Content, stringNode(value, 0))
	}
	return node
}

// importLegacySettings translates the old tool's settings.yml into the new
// keys (and asks to confirm the wiki link).
func importLegacySettings(path, root, packName string, ask AskFunc, notes *[]string) (map[string]*yaml.Node, error) {
	old := map[string]any{}
	content, err := pycompat.ReadText(path)
	if err == nil {
		var decoded any
		if err := yaml.Unmarshal([]byte(content), &decoded); err != nil {
			*notes = append(*notes, fmt.Sprintf("Couldn't read the old %s (%v); starting from the defaults.", LegacySettingsFile, err))
		} else if m, ok := decoded.(map[string]any); ok {
			old = m
		}
	}
	flag := func(key string, def bool) bool {
		if value, ok := old[key]; ok {
			return pycompat.Truthy(value)
		}
		return def
	}
	var exports []string
	if flag("export_client", true) {
		switch {
		case flag("client_export_multi_platform", false) || flag("breakneck_fixes", false):
			exports = append(exports, "curseforge", "modrinth")
		case strings.ToLower(pycompat.Str(orDefault(old, "client_export_format", "curseforge"))) == "modrinth":
			exports = append(exports, "modrinth")
		default:
			exports = append(exports, "curseforge")
		}
	}
	if flag("export_server", false) {
		exports = append(exports, "server")
	}
	policy := map[string]string{"always_skip": "never", "skip": "never", "never": "never",
		"always_allow": "always", "allow": "always"}[strings.ToLower(pycompat.Str(orDefault(old, "alpha_update_policy", "prompt")))]
	if policy == "" {
		policy = "prompt"
	}
	footer := stringNode("", 0)
	if banner := pycompat.Strip(pycompat.Or(old["bh_banner"], "")); banner != "" {
		footer = stringNode("<br>\n\n[![BisectHosting Banner]("+banner+")](https://bisecthosting.com/CRISM)\n", yaml.LiteralStyle)
	}

	// Exclusions were jar filenames, which change on every update; use slugs where possible.
	mods, _, err := pack.LoadMods(filepath.Join(root, "Packwiz"), []string{"mods"})
	if err != nil {
		return nil, err
	}
	byFilename := map[string]string{}
	for _, mod := range mods {
		byFilename[mod.Filename()] = mod.Slug()
	}
	squash := func(text string) string {
		var b strings.Builder
		for _, r := range strings.ToLower(text) {
			if isAlnum(r) {
				b.WriteRune(r)
			}
		}
		return b.String()
	}
	exclude := []string{}
	oldList, _ := old["server_mods_remove_list"].([]any)
	for _, item := range oldList {
		entry := pycompat.Str(item)
		if slug, ok := byFilename[entry]; ok {
			exclude = append(exclude, slug)
			continue
		}
		// An outdated filename usually still starts with the mod's slug.
		var guesses []string
		for _, mod := range mods {
			if key := squash(mod.Slug()); len(key) >= 5 && strings.HasPrefix(squash(entry), key) {
				guesses = append(guesses, mod.Slug())
			}
		}
		if len(guesses) == 1 {
			exclude = append(exclude, guesses[0])
			*notes = append(*notes, fmt.Sprintf("server_exclude: '%s' is an old filename; now excluding the mod '%s'.", entry, guesses[0]))
		} else {
			exclude = append(exclude, entry)
			*notes = append(*notes, fmt.Sprintf("server_exclude: '%s' matches no current mod; check it.", entry))
		}
	}

	url := DefaultChangelogURL(packName)
	slug := strings.Split(url, "/")[3]
	if ask != nil {
		answer, err := ask(fmt.Sprintf("Release notes will link to the wiki as %s. Wiki folder for this pack "+
			"(the part after crismpack.net/)", url), slug)
		if err != nil {
			return nil, err
		}
		if answer = pycompat.Strip(answer); answer != "" {
			slug = answer
		}
	}
	url = "https://crismpack.net/" + strings.Trim(slug, "/") + "/changelogs/{mc_group}#{anchor}"
	*notes = append(*notes, fmt.Sprintf("Imported settings from %s into %s; %s is no longer used and can be deleted.",
		LegacySettingsFile, SettingsFile, LegacySettingsFile))
	return map[string]*yaml.Node{
		"exports":                 listNode(exports),
		"server_exclude":          listNode(exclude),
		"mc_prefixed_versions":    boolNode(flag("mc_prefixed_versions", false)),
		"alpha_updates":           stringNode(policy, 0),
		"side_tags":               boolNode(flag("changelog_side_tag", true)),
		"changelog_url":           stringNode(url, 0),
		"curseforge_notes_footer": footer,
		"modrinth_notes_footer":   stringNode("", 0),
	}, nil
}

func orDefault(values map[string]any, key string, def any) any {
	if value, ok := values[key]; ok {
		return value
	}
	return def
}

// isAlnum is Python's str.isalnum for one character.
func isAlnum(r rune) bool {
	return unicode.IsLetter(r) || unicode.IsNumber(r)
}

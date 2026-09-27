// Package changelog handles the changelog files: the YAML you write, the
// release record the wiki reads, and the release notes.
//
// Each release has Changelogs/<stem>.yml (written by you, optionally drafted
// by the tool) and Changelogs/data/<stem>.json, a presentation-free record
// that the wiki (CrismPack/Wiki docs/.vitepress/changelog.mjs) renders. Keep
// the record's keys in sync with that renderer. The records are the source
// of truth: they stay, every wiki sync copies all of them, and the tool reads
// a released version's notes from its record. So a released version's YAML
// goes when the next version starts, once its record has the same notes. Git
// ignores a new record until Publish commits it with its release, so the wiki
// sync never carries an unreleased version.
package changelog

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"time"

	"go.yaml.in/yaml/v3"

	"github.com/HaXrDEV/Modpack-Tool/internal/diff"
	"github.com/HaXrDEV/Modpack-Tool/internal/fail"
	"github.com/HaXrDEV/Modpack-Tool/internal/files"
	"github.com/HaXrDEV/Modpack-Tool/internal/project"
	"github.com/HaXrDEV/Modpack-Tool/internal/pycompat"
	"github.com/HaXrDEV/Modpack-Tool/internal/version"
)

// Sections are the hand-written sections (YAML key, record key), in display order.
var Sections = []struct{ Key, Record string }{
	{"Update overview", "overview"},
	{"Changes/Improvements", "changes"},
	{"Bug Fixes", "bugfixes"},
	{"Script/Datapack changes", "scriptChanges"},
	{"Config Changes", "configChanges"},
}

// Metadata keys older templates wrote; loader info now comes from pack.toml.
var metadataKeys = map[string]bool{"version": true, "mc_version": true, "Mod loader": true,
	"Mod loader version": true, "Fabric version": true}

// The old template's example line, which must never reach a changelog.
const placeholder = ": [mod], [Client]"

const template = `# Changelog for %s %s. Empty sections are left out.
# "Draft changelog" in the tool can fill 'Update overview' and 'Config Changes' for you.
Update overview:
Changes/Improvements:
Bug Fixes:
Script/Datapack changes:
Config Changes:
`

////////////////////////////////////////////////////////////
// File names

// Stem is the name of a release's files: "4.11.1+1.21.11", or just
// "26.1.1-1.2" for MC-scheme versions.
func Stem(v, minecraft string) string {
	if version.IsMCPrefixed(v) {
		return v
	}
	return v + "+" + minecraft
}

// Path is the changelog YAML of a version (the current one when empty),
// whether it exists or not.
func Path(p *project.Project, v, minecraft string) string {
	if v == "" {
		v = p.Version
	}
	if minecraft == "" {
		minecraft = p.Minecraft
	}
	canonical := filepath.Join(p.ChangelogDir(), Stem(v, minecraft)+".yml")
	if !files.Exists(canonical) {
		for _, name := range []string{v + "+" + minecraft + ".yml", v + ".yml", v + "+" + minecraft + ".yaml"} {
			if files.Exists(filepath.Join(p.ChangelogDir(), name)) {
				return filepath.Join(p.ChangelogDir(), name)
			}
		}
	}
	return canonical
}

// RecordPath is the release record of a version (the current one when empty).
func RecordPath(p *project.Project, v, minecraft string) string {
	if v == "" {
		v = p.Version
	}
	if minecraft == "" {
		minecraft = p.Minecraft
	}
	return filepath.Join(p.DataDir(), Stem(v, minecraft)+".json")
}

// Create writes the changelog template for a version unless the file exists,
// and returns its path.
func Create(p *project.Project, v string) (string, error) {
	path := Path(p, v, "")
	if _, err := os.Stat(path); errors.Is(err, fs.ErrNotExist) {
		if v == "" {
			v = p.Version
		}
		if err := pycompat.WriteText(path, fmt.Sprintf(template, p.Name, v)); err != nil {
			return "", err
		}
	}
	return path, nil
}

////////////////////////////////////////////////////////////
// The hand-written YAML

// Changelog is a loaded changelog file.
type Changelog struct {
	Path string
	text string // With "\n" line breaks.
	keys []string
	// values holds each top-level key's value node.
	values map[string]*yaml.Node
}

// Load reads a changelog. A missing file is an error; see Exists.
func Load(path string) (*Changelog, error) {
	text, err := pycompat.ReadText(path)
	if err != nil {
		return nil, err
	}
	return parse(path, text)
}

func parse(path, text string) (*Changelog, error) {
	c := &Changelog{Path: path, text: text, values: map[string]*yaml.Node{}}
	var doc yaml.Node
	if err := yaml.Unmarshal([]byte(text), &doc); err != nil {
		return nil, fail.Wrapf(err, "%s isn't valid YAML, so it can't be read:\n%v", filepath.Base(path), err)
	}
	if doc.Kind == 0 || len(doc.Content) == 0 {
		return c, nil
	}
	root := doc.Content[0]
	if root.Kind == yaml.ScalarNode && root.Tag == "!!null" {
		return c, nil
	}
	if root.Kind != yaml.MappingNode {
		return nil, fail.Errorf("%s should contain sections such as 'Update overview:'.", filepath.Base(path))
	}
	for i := 0; i+1 < len(root.Content); i += 2 {
		key := root.Content[i].Value
		c.keys = append(c.keys, key)
		c.values[key] = root.Content[i+1]
	}
	return c, nil
}

// Keys are the top-level keys in file order.
func (c *Changelog) Keys() []string { return c.keys }

// Lines returns a section's bullets as plain strings (without "- ").
func (c *Changelog) Lines(key string) []string { return SectionLines(c.values[key]) }

// SectionLines turns a section's YAML value into its bullet texts.
func SectionLines(node *yaml.Node) []string {
	if node == nil {
		return nil
	}
	var lines []string
	switch node.Kind {
	case yaml.ScalarNode:
		if node.Tag == "!!null" || node.Value == "" && node.Tag == "!!str" {
			return nil
		}
		if node.Tag == "!!str" {
			lines = pycompat.SplitLines(node.Value)
		} else {
			lines = []string{scalarText(node)}
		}
	case yaml.MappingNode:
		for i := 0; i+1 < len(node.Content); i += 2 {
			lines = append(lines, itemText(node.Content[i])+": "+itemText(node.Content[i+1]))
		}
	case yaml.SequenceNode:
		for _, item := range node.Content {
			if item.Kind == yaml.ScalarNode && item.Tag == "!!null" {
				continue // An empty "- " bullet (the Python tool wrote "None" for it).
			}
			lines = append(lines, asText(item))
		}
	default:
		lines = []string{itemText(node)}
	}
	var cleaned []string
	for _, line := range lines {
		text := pycompat.Strip(line)
		if strings.HasPrefix(text, "- ") {
			text = pycompat.Strip(text[2:])
		}
		if text != "" && text != pycompat.Strip(placeholder) && !strings.HasSuffix(text, placeholder) {
			cleaned = append(cleaned, text)
		}
	}
	return cleaned
}

// asText keeps "- Sodium: fixed flicker" (a one-entry mapping in YAML) as written.
func asText(node *yaml.Node) string {
	if node.Kind == yaml.MappingNode {
		var parts []string
		for i := 0; i+1 < len(node.Content); i += 2 {
			parts = append(parts, itemText(node.Content[i])+": "+itemText(node.Content[i+1]))
		}
		return strings.Join(parts, ", ")
	}
	return itemText(node)
}

// itemText is Python's str() of a loaded YAML value.
func itemText(node *yaml.Node) string {
	if node.Kind == yaml.ScalarNode {
		return scalarText(node)
	}
	var value any
	if err := node.Decode(&value); err != nil {
		return node.Value
	}
	return pycompat.Str(value)
}

func scalarText(node *yaml.Node) string {
	switch node.Tag {
	case "!!str", "!!timestamp":
		return node.Value
	case "!!null":
		return "None"
	}
	var value any
	if err := node.Decode(&value); err != nil {
		return node.Value
	}
	if i, ok := value.(int); ok {
		value = int64(i)
	}
	return pycompat.Str(value)
}

// UnknownSections are keys with content that no output uses (e.g. a typo).
func (c *Changelog) UnknownSections() []string {
	known := map[string]bool{}
	for _, s := range Sections {
		known[s.Key] = true
	}
	var unknown []string
	for _, key := range c.keys {
		if !known[key] && !metadataKeys[key] && len(c.Lines(key)) > 0 {
			unknown = append(unknown, key)
		}
	}
	return unknown
}

// IsEmpty reports whether no section has content.
func (c *Changelog) IsEmpty() bool {
	for _, s := range Sections {
		if len(c.Lines(s.Key)) > 0 {
			return false
		}
	}
	return true
}

// SetSection replaces one section's text in the file with lines: a block list
// for most sections, a literal block for Config Changes (as the Python tool
// wrote them). Everything else in the file, comments included, stays as it is.
func (c *Changelog) SetSection(key string, lines []string) error {
	rendered := key + ":"
	if len(lines) > 0 {
		if key == "Config Changes" {
			bullets := make([]string, len(lines))
			for i, line := range lines {
				bullets[i] = "- " + line
			}
			block := pycompat.YAMLLiteral(strings.Join(bullets, "\n"), "  ")
			rendered += " " + strings.Join(block, "\n")
		} else {
			for _, line := range lines {
				rendered += "\n  - " + pycompat.YAMLScalar(line, false)
			}
		}
	}
	fileLines := strings.Split(strings.TrimSuffix(c.text, "\n"), "\n")
	if c.text == "" {
		fileLines = nil
	}
	start, end := c.sectionSpan(fileLines, key)
	if start < 0 {
		fileLines = append(fileLines, rendered)
	} else {
		fileLines = append(fileLines[:start], append([]string{rendered}, fileLines[end:]...)...)
	}
	text := strings.Join(fileLines, "\n") + "\n"
	reloaded, err := parse(c.Path, text)
	if err != nil {
		return fmt.Errorf("drafting %s would break %s: %w", key, filepath.Base(c.Path), err)
	}
	*c = *reloaded
	return nil
}

// sectionSpan returns the lines [start, end) that hold a top-level key and
// its value; start is -1 when the key is missing. Blank lines and comments
// after the value belong to what follows, so they are not part of the span.
func (c *Changelog) sectionSpan(lines []string, key string) (int, int) {
	node := c.values[key]
	if node == nil {
		return -1, -1
	}
	start := -1
	for i, line := range lines {
		if isKeyLine(line, key) {
			start = i
			break
		}
	}
	if start < 0 {
		return -1, -1
	}
	end := start + 1
	for i := start + 1; i < len(lines); i++ {
		line := lines[i]
		if trimmed := strings.TrimSpace(line); trimmed == "" || strings.HasPrefix(trimmed, "#") {
			continue // Part of the value only when more of the value follows.
		}
		if line[0] != ' ' && line[0] != '\t' && !strings.HasPrefix(line, "- ") && line != "-" {
			break // The next key, at column 0.
		}
		end = i + 1
	}
	return start, end
}

func isKeyLine(line, key string) bool {
	for _, candidate := range []string{key, `"` + key + `"`, "'" + key + "'"} {
		if strings.HasPrefix(line, candidate+":") {
			rest := line[len(candidate)+1:]
			return rest == "" || rest[0] == ' ' || rest[0] == '\t'
		}
	}
	return false
}

// handWritten are the sections that only you write. A full release takes
// them over from its pre-releases, while Update overview and Config Changes
// are drafted again from the changes since the previous full release, which
// cover the pre-releases' changes.
var handWritten = []string{"Changes/Improvements", "Bug Fixes", "Script/Datapack changes"}

// Notes are a release's notes by section, as its changelog or its record has
// them.
type Notes interface {
	Lines(key string) []string
}

// Include adds the hand-written sections of earlier notes (a full release's
// pre-releases, oldest first) in front of c's own lines, leaving out lines c
// has already. It reports whether c changed; Save writes it.
func (c *Changelog) Include(earlier []Notes) (bool, error) {
	changed := false
	for _, key := range handWritten {
		own := c.Lines(key)
		seen := map[string]bool{}
		for _, line := range own {
			seen[strings.ToLower(line)] = true
		}
		var added []string
		for _, e := range earlier {
			for _, line := range e.Lines(key) {
				if !seen[strings.ToLower(line)] {
					seen[strings.ToLower(line)] = true
					added = append(added, line)
				}
			}
		}
		if len(added) == 0 {
			continue
		}
		if err := c.SetSection(key, append(added, own...)); err != nil {
			return false, err
		}
		changed = true
	}
	return changed, nil
}

// Save writes the changelog, keeping the file's line endings.
func (c *Changelog) Save() error { return pycompat.WriteText(c.Path, c.text) }

////////////////////////////////////////////////////////////
// Release record

// Record is the presentation-free record of a release: the wiki's input.
type Record struct {
	Pack          string      `json:"pack"`
	Version       string      `json:"version"`
	Minecraft     string      `json:"minecraft"`
	Loader        Loader      `json:"loader"`
	Released      string      `json:"released"`
	Prerelease    bool        `json:"prerelease"`
	ComparedTo    *ComparedTo `json:"comparedTo"`
	Overview      []string    `json:"overview"`
	Changes       []string    `json:"changes"`
	BugFixes      []string    `json:"bugfixes"`
	ScriptChanges []string    `json:"scriptChanges"`
	ConfigChanges []string    `json:"configChanges"`
	Mods          RecordDiff  `json:"mods"`
	ResourcePacks RecordDiff  `json:"resourcepacks"`
	ShaderPacks   RecordDiff  `json:"shaderpacks"`
	Contents      *Contents   `json:"contents,omitempty"`
}

// Lines returns one of the record's sections by its changelog key ("Bug
// Fixes"), the way BuildRecord took it from the changelog.
func (r Record) Lines(key string) []string {
	switch key {
	case "Update overview":
		return r.Overview
	case "Changes/Improvements":
		return r.Changes
	case "Bug Fixes":
		return r.BugFixes
	case "Script/Datapack changes":
		return r.ScriptChanges
	case "Config Changes":
		return r.ConfigChanges
	}
	return nil
}

// Records reads the release records in Changelogs/data, oldest first.
func Records(p *project.Project) ([]Record, error) {
	entries, err := os.ReadDir(p.DataDir())
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil
	} else if err != nil {
		return nil, err
	}
	var records []Record
	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".json" {
			continue
		}
		var r Record
		data, err := os.ReadFile(filepath.Join(p.DataDir(), entry.Name()))
		if err == nil {
			err = json.Unmarshal(data, &r)
		}
		if err != nil {
			return nil, fail.Wrapf(err, "The record %s can't be read: %v", entry.Name(), err)
		}
		records = append(records, r)
	}
	slices.SortFunc(records, func(a, b Record) int {
		return version.Compare(version.ParseKey(a.Version), version.ParseKey(b.Version))
	})
	return records, nil
}

// Contents is everything a release contains, for the wiki's modlists.
type Contents struct {
	Mods          []Item `json:"mods"`
	ResourcePacks []Item `json:"resourcepacks"`
	ShaderPacks   []Item `json:"shaderpacks"`
}

// Add adds an item to the list of its category ("mods", "resourcepacks" or
// "shaderpacks").
func (c *Contents) Add(category string, item Item) {
	switch category {
	case "resourcepacks":
		c.ResourcePacks = append(c.ResourcePacks, item)
	case "shaderpacks":
		c.ShaderPacks = append(c.ShaderPacks, item)
	default:
		c.Mods = append(c.Mods, item)
	}
}

// Item is one mod, resource pack or shader pack of a release.
type Item struct {
	Name    string   `json:"name"`
	File    string   `json:"file"`
	Side    string   `json:"side,omitempty"` // "client" or "server", when the pack shows side tags.
	URL     string   `json:"url,omitempty"`  // Its project page.
	Authors []string `json:"authors,omitempty"`
}

// Loader is the record's loader entry.
type Loader struct {
	Name    string `json:"name"`
	Version string `json:"version"`
}

// ComparedTo is the release the record was compared against.
type ComparedTo struct {
	Version   string `json:"version"`
	Minecraft string `json:"minecraft"`
}

// RecordDiff is one category's changes in the record.
type RecordDiff struct {
	Added   []string       `json:"added"`
	Removed []string       `json:"removed"`
	Updated []RecordUpdate `json:"updated"`
}

// RecordUpdate is one updated file in the record.
type RecordUpdate struct {
	Name string `json:"name"`
	From string `json:"from"`
	To   string `json:"to"`
}

func recordDiff(c diff.CategoryDiff, sideTags bool) RecordDiff {
	r := RecordDiff{Added: []string{}, Removed: []string{}, Updated: []RecordUpdate{}}
	for _, n := range c.Added {
		r.Added = append(r.Added, diff.Tagged(n, sideTags))
	}
	for _, n := range c.Removed {
		r.Removed = append(r.Removed, diff.Tagged(n, sideTags))
	}
	for _, u := range c.Updated {
		r.Updated = append(r.Updated, RecordUpdate{u.Name, u.Before, u.After})
	}
	return r
}

func nonNil(values []string) []string {
	if values == nil {
		return []string{}
	}
	return values
}

// BuildRecord builds the current release's record; released defaults to today.
func BuildRecord(p *project.Project, c *Changelog, d *diff.PackDiff, released string) Record {
	if released == "" {
		released = time.Now().Format("2006-01-02")
	}
	r := Record{
		Pack: p.Name, Version: p.Version, Minecraft: p.Minecraft,
		Loader:     Loader{p.LoaderLabel(), p.LoaderVersion},
		Released:   released,
		Prerelease: version.IsPrerelease(p.Version),
	}
	if d != nil && d.PreviousVersion != "" {
		mc := d.PreviousMinecraft
		if mc == "" {
			mc = p.Minecraft
		}
		r.ComparedTo = &ComparedTo{d.PreviousVersion, mc}
	}
	r.Overview = nonNil(c.Lines("Update overview"))
	r.Changes = nonNil(c.Lines("Changes/Improvements"))
	r.BugFixes = nonNil(c.Lines("Bug Fixes"))
	r.ScriptChanges = nonNil(c.Lines("Script/Datapack changes"))
	r.ConfigChanges = nonNil(c.Lines("Config Changes"))
	if d == nil {
		d = &diff.PackDiff{}
	}
	r.Mods = recordDiff(d.Mods, p.Settings.SideTags)
	r.ResourcePacks = recordDiff(d.ResourcePacks, p.Settings.SideTags)
	r.ShaderPacks = recordDiff(d.ShaderPacks, p.Settings.SideTags)
	return r
}

// WriteRecord writes a record (a Record, or an ordered pycompat.Object) as
// the current version's JSON, the way the Python tool wrote it.
func WriteRecord(p *project.Project, record any) (string, error) {
	path := RecordPath(p, "", "")
	encoded, err := pycompat.Dumps(record, 2, false)
	if err != nil {
		return "", err
	}
	return path, pycompat.WriteText(path, string(encoded))
}

////////////////////////////////////////////////////////////
// Release notes (uploaded to CurseForge and Modrinth by the publish workflow)

// URL is the "Full changelog" link; "" when there is none. warn receives a
// message when the link template is broken.
func URL(p *project.Project, warn func(string)) string {
	template := pycompat.Strip(p.Settings.ChangelogURL)
	if template == "" {
		return ""
	}
	url, err := pycompat.Format(template, map[string]string{
		"mc_group": version.ContentKey(p.Minecraft), "mc": p.Minecraft,
		"version": p.Version, "anchor": version.Anchor(p.Version),
	})
	if err != nil {
		if warn != nil {
			warn(fmt.Sprintf("changelog_url has an unknown placeholder (%v); leaving the link out.", err))
		}
		return ""
	}
	return url
}

// PrereleaseNotice says what a pre-release means for players, at the top of
// its release notes; "" for a full release. The wiki shows the same notice.
func PrereleaseNotice(v string) string {
	kind := map[string]string{"dev": "a development build", "alpha": "an alpha", "beta": "a beta",
		"rc": "a release candidate", "pre": "a pre-release"}[version.PrereleaseKind(v)]
	if kind == "" {
		return ""
	}
	return "This is " + kind + ", so it may be less stable or feature complete than a full release. Here be dragons!"
}

// ReleaseNotes is the Markdown release notes for "curseforge" or "modrinth".
func ReleaseNotes(p *project.Project, c *Changelog, platform string, warn func(string)) string {
	var blocks []string
	if notice := PrereleaseNotice(p.Version); notice != "" {
		blocks = append(blocks, "**"+notice+"**")
	}
	bullets := func(lines []string) string {
		items := make([]string, len(lines))
		for i, line := range lines {
			items[i] = "- " + line
		}
		return strings.Join(items, "\n")
	}
	if overview := c.Lines("Update overview"); len(overview) > 0 {
		blocks = append(blocks, bullets(overview))
	} else {
		for _, section := range []struct{ key, heading string }{
			{"Changes/Improvements", "Changes/Improvements ⭐"}, {"Bug Fixes", "Bug Fixes 🪲"},
		} {
			if lines := c.Lines(section.key); len(lines) > 0 {
				blocks = append(blocks, "### "+section.heading+"\n\n"+bullets(lines))
			}
		}
	}
	if url := URL(p, warn); url != "" {
		link := "**[[Full Changelog]](" + url + ")**"
		if platform == "curseforge" {
			link = "#### " + link
		}
		blocks = append(blocks, link)
	}
	footer := p.Settings.ModrinthNotesFooter
	if platform == "curseforge" {
		footer = p.Settings.CurseForgeNotesFooter
	}
	if text := pycompat.Strip(footer); text != "" {
		blocks = append(blocks, text)
	}
	return strings.Join(blocks, "\n\n") + "\n"
}

// NotesFiles are the release notes files, by platform.
var NotesFiles = []struct{ Platform, Name string }{
	{"curseforge", "CurseForge-Release.md"}, {"modrinth", "Modrinth-Release.md"},
}

// NotesFile is the name of a platform's release notes file.
func NotesFile(platform string) string {
	for _, f := range NotesFiles {
		if f.Platform == platform {
			return f.Name
		}
	}
	return ""
}

// WriteReleaseNotes writes both release notes files and returns their paths.
func WriteReleaseNotes(p *project.Project, c *Changelog, warn func(string)) ([]string, error) {
	var paths []string
	for _, f := range NotesFiles {
		path := filepath.Join(p.Root, f.Name)
		if err := pycompat.WriteText(path, ReleaseNotes(p, c, f.Platform, warn)); err != nil {
			return nil, err
		}
		paths = append(paths, path)
	}
	return paths, nil
}

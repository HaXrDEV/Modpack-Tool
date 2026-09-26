// Package diff works out what changed between two states of a pack (an
// earlier release and now). Both sides are pack.Trees, from pack.ReadTree
// (the working copy) or git snapshots (a release tag).
package diff

import (
	"bytes"
	"fmt"
	"maps"
	"slices"
	"strings"

	"github.com/pmezard/go-difflib/difflib"

	"github.com/HaXrDEV/Modpack-Tool/internal/pack"
	"github.com/HaXrDEV/Modpack-Tool/internal/pycompat"
	"github.com/HaXrDEV/Modpack-Tool/internal/ui"
)

var textExtensions = map[string]bool{".json": true, ".json5": true, ".yaml": true, ".yml": true, ".toml": true,
	".cfg": true, ".conf": true, ".ini": true, ".properties": true, ".txt": true}

const lineLimit = 20

// Named is a mod or pack name plus its side ("both", "client" or "server").
type Named struct{ Name, Side string }

// Update is a file that changed version.
type Update struct{ Name, Before, After string }

// CategoryDiff is what changed in mods, resource packs or shader packs.
type CategoryDiff struct {
	Added, Removed []Named
	Updated        []Update
}

// LineDiff is the changed lines of one config file.
type LineDiff struct {
	Path                            string
	RemovedLines, AddedLines        []string
	PreviousContent, CurrentContent string
}

// Move is a plain config moved into the YOSBR defaults folder.
type Move struct {
	From, To       string
	ContentChanged bool
}

// ConfigDiff is what changed in the config folder.
type ConfigDiff struct {
	Added, Removed, Modified []string
	LineDiffs                []LineDiff
	MovedToYOSBR             []Move
}

// PackDiff is everything that changed from an earlier release to now.
type PackDiff struct {
	PreviousVersion, PreviousMinecraft string
	CurrentVersion, Minecraft          string
	Mods, ResourcePacks, ShaderPacks   CategoryDiff
	NewlyAdded                         []string // Mods that are new to the pack.
	Reenabled                          []string // Mods that were disabled before and are active now.
	Config                             ConfigDiff
}

// Migration reports whether the previous release was for another Minecraft version.
func (d *PackDiff) Migration() bool {
	return d.PreviousMinecraft != "" && d.PreviousMinecraft != d.Minecraft
}

// Summary is a one-line count of the changes, e.g. "+3 mods, 22 updated, 4
// config files changed".
func (d *PackDiff) Summary() string {
	var parts []string
	if n := len(d.Mods.Added); n > 0 {
		parts = append(parts, fmt.Sprintf("+%d mod%s", n, ui.Plural(n)))
	}
	if n := len(d.Mods.Removed); n > 0 {
		parts = append(parts, fmt.Sprintf("-%d mod%s", n, ui.Plural(n)))
	}
	if n := len(d.Mods.Updated) + len(d.ResourcePacks.Updated) + len(d.ShaderPacks.Updated); n > 0 {
		parts = append(parts, fmt.Sprintf("%d updated", n))
	}
	packs := len(d.ResourcePacks.Added) + len(d.ResourcePacks.Removed) + len(d.ShaderPacks.Added) + len(d.ShaderPacks.Removed)
	if packs > 0 {
		parts = append(parts, fmt.Sprintf("%d resource/shader pack%s added or removed", packs, ui.Plural(packs)))
	}
	if n := d.changedConfigs(); n > 0 {
		parts = append(parts, fmt.Sprintf("%d config file%s changed", n, ui.Plural(n)))
	}
	if len(parts) == 0 {
		return "no changes"
	}
	return strings.Join(parts, ", ")
}

// changedConfigs counts the modified, removed and moved config files.
func (d *PackDiff) changedConfigs() int {
	set := map[string]bool{}
	for _, p := range d.Config.Modified {
		set[p] = true
	}
	for _, p := range d.Config.Removed {
		set[p] = true
	}
	for _, m := range d.Config.MovedToYOSBR {
		set[m.To] = true
	}
	return len(set)
}

// Tagged is a name for changelogs, with a `Client`/`Server` tag when side tags are on.
func Tagged(n Named, sideTags bool) string {
	if sideTags && n.Side != "both" {
		return n.Name + " `" + pycompat.Capitalize(n.Side) + "`"
	}
	return n.Name
}

// metafiles are one category's metafiles by file name, in path order.
type metafiles struct {
	names  []string
	byName map[string]pack.Mod
}

// byCategory parses a tree's metafiles once, grouped by category.
func byCategory(tree pack.Tree) map[string]metafiles {
	mods, _ := pack.ParseMods(tree, pack.Categories)
	result := map[string]metafiles{}
	for _, mod := range mods {
		m, ok := result[mod.Category()]
		if !ok {
			m.byName = map[string]pack.Mod{}
		}
		name := pycompat.Name(mod.Rel)
		if _, seen := m.byName[name]; !seen {
			m.names = append(m.names, name)
		}
		m.byName[name] = mod
		result[mod.Category()] = m
	}
	return result
}

func active(m metafiles) ([]string, map[string]pack.Mod) {
	var kept []string
	result := map[string]pack.Mod{}
	for _, name := range m.names {
		if !m.byName[name].Disabled() {
			kept = append(kept, name)
			result[name] = m.byName[name]
		}
	}
	return kept, result
}

func hashLabel(filename, hash string) string {
	if len(hash) > 12 {
		hash = hash[:12]
	}
	if hash == "" {
		return filename
	}
	return filename + " (hash " + hash + ")"
}

// category returns the added, removed and updated active files of one
// category (mods, resourcepacks, shaderpacks).
func category(oldFiles, newFiles metafiles) CategoryDiff {
	oldNames, old := active(oldFiles)
	newNames, current := active(newFiles)
	var added, removed []Named
	for _, name := range newNames {
		if _, ok := old[name]; !ok {
			added = append(added, Named{current[name].DisplayName(), current[name].Side()})
		}
	}
	for _, name := range oldNames {
		if _, ok := current[name]; !ok {
			removed = append(removed, Named{old[name].DisplayName(), old[name].Side()})
		}
	}
	// A file renamed without a name change (e.g. a new slug) is neither added nor removed.
	addedNames := map[string]bool{}
	for _, n := range added {
		addedNames[n.Name] = true
	}
	renamed := map[string]bool{}
	for _, n := range removed {
		if addedNames[n.Name] {
			renamed[n.Name] = true
		}
	}
	var result CategoryDiff
	for _, n := range added {
		if !renamed[n.Name] {
			result.Added = append(result.Added, n)
		}
	}
	for _, n := range removed {
		if !renamed[n.Name] {
			result.Removed = append(result.Removed, n)
		}
	}
	for _, name := range newNames {
		previous, ok := old[name]
		if !ok {
			continue
		}
		now := current[name]
		oldFormat, oldHash := previous.Hash()
		newFormat, newHash := now.Hash()
		var before, after string
		if previous.Filename() == now.Filename() {
			if oldHash == newHash || oldFormat != newFormat {
				continue // Unchanged, or the same jar with metadata from another platform.
			}
			before, after = hashLabel(previous.Filename(), oldHash), hashLabel(now.Filename(), newHash)
		} else {
			before, after = previous.Filename(), now.Filename()
			if before == "" {
				before = oldHash
			}
			if after == "" {
				after = newHash
			}
		}
		result.Updated = append(result.Updated, Update{now.DisplayName(), before, after})
	}
	return result
}

// additionBreakdown returns the mods that are new to the pack and the ones
// that were disabled before, each sorted without duplicates.
func additionBreakdown(oldMods, newMods metafiles) ([]string, []string) {
	byDisplay := map[string]pack.Mod{}
	for _, name := range oldMods.names {
		byDisplay[oldMods.byName[name].DisplayName()] = oldMods.byName[name]
	}
	newlyAdded, reenabled := map[string]bool{}, map[string]bool{}
	for _, name := range newMods.names {
		mod := newMods.byName[name]
		if mod.Disabled() {
			continue
		}
		previous, ok := oldMods.byName[name]
		if !ok {
			previous, ok = byDisplay[mod.DisplayName()] // Also follow renamed files.
		}
		switch {
		case !ok:
			newlyAdded[mod.DisplayName()] = true
		case previous.Disabled():
			reenabled[mod.DisplayName()] = true
		}
	}
	return pycompat.SortedLower(slices.Collect(maps.Keys(newlyAdded))), pycompat.SortedLower(slices.Collect(maps.Keys(reenabled)))
}

// IsYOSBR reports whether a config path is in the YOSBR defaults folder.
func IsYOSBR(path string) bool { return strings.HasPrefix(strings.ToLower(path), "yosbr/") }

// generatedByTool: files the tool rewrites itself (bcc.json, the Crash
// Assistant modlist) aren't config changes.
func generatedByTool(path string) bool {
	lowered := "/" + strings.ToLower(path)
	stem := pycompat.Stem(lowered)
	return stem == "bcc" || strings.Contains(lowered, "/bcc/") || (stem == "modlist" && strings.Contains(lowered, "/crash_assistant/"))
}

func textLines(content []byte) []string {
	return pycompat.SplitLines(pycompat.DecodeUTF8(content))
}

func lineDiff(path string, oldContent, newContent []byte) *LineDiff {
	if !textExtensions[pycompat.Suffix(strings.ToLower(path))] {
		return nil
	}
	oldLines, newLines := textLines(oldContent), textLines(newContent)
	var removed, added []string
	// SequenceMatcher (with autojunk) is what Python's unified_diff uses, so
	// the lines match the Python tool's drafts.
	for _, op := range difflib.NewMatcher(oldLines, newLines).GetOpCodes() {
		if op.Tag == 'r' || op.Tag == 'd' {
			for _, line := range oldLines[op.I1:op.I2] {
				if text := pycompat.Strip(line); text != "" {
					removed = append(removed, text)
				}
			}
		}
		if op.Tag == 'r' || op.Tag == 'i' {
			for _, line := range newLines[op.J1:op.J2] {
				if text := pycompat.Strip(line); text != "" {
					added = append(added, text)
				}
			}
		}
	}
	if len(removed) == 0 && len(added) == 0 {
		return nil
	}
	return &LineDiff{
		Path:            path,
		RemovedLines:    removed[:min(len(removed), lineLimit)],
		AddedLines:      added[:min(len(added), lineLimit)],
		PreviousContent: strings.Join(oldLines, "\n"),
		CurrentContent:  strings.Join(newLines, "\n"),
	}
}

// withoutGenerated leaves out the files the tool writes itself.
func withoutGenerated(files map[string][]byte) map[string][]byte {
	result := map[string][]byte{}
	for path, data := range files {
		if !generatedByTool(path) {
			result[path] = data
		}
	}
	return result
}

// Config compares two config folders ({path relative to config/: bytes}).
// details=false skips the line-by-line diffs (enough for a summary).
func Config(oldFiles, newFiles map[string][]byte, details bool) ConfigDiff {
	old, current := withoutGenerated(oldFiles), withoutGenerated(newFiles)
	var added, removed, modified []string
	for path := range current {
		if _, ok := old[path]; !ok {
			added = append(added, path)
		}
	}
	for path, data := range old {
		if newData, ok := current[path]; !ok {
			removed = append(removed, path)
		} else if !bytes.Equal(newData, data) {
			modified = append(modified, path)
		}
	}
	added, removed, modified = pycompat.SortedLower(added), pycompat.SortedLower(removed), pycompat.SortedLower(modified)

	// Moving a config into the YOSBR overlay ("config/x" -> "config/yosbr/x"
	// or "config/yosbr/config/x") makes it a first-launch default. That is a
	// move, not a removal plus an addition.
	var result ConfigDiff
	addedLookup := map[string]string{}
	for _, path := range added {
		addedLookup[strings.ToLower(path)] = path
	}
	consumed := map[string]bool{}
	for _, path := range removed {
		target := ""
		if !IsYOSBR(path) {
			for _, candidate := range []string{"yosbr/" + path, "yosbr/config/" + path} {
				if found, ok := addedLookup[strings.ToLower(candidate)]; ok {
					target = found
					break
				}
			}
		}
		if target == "" {
			result.Removed = append(result.Removed, path)
			continue
		}
		consumed[strings.ToLower(target)] = true
		result.MovedToYOSBR = append(result.MovedToYOSBR, Move{From: path, To: target, ContentChanged: !bytes.Equal(old[path], current[target])})
	}
	for _, path := range added {
		if !consumed[strings.ToLower(path)] {
			result.Added = append(result.Added, path)
		}
	}

	lineDiffs := map[string]LineDiff{}
	if details {
		for _, path := range modified {
			if entry := lineDiff(path, oldFiles[path], newFiles[path]); entry != nil {
				lineDiffs[strings.ToLower(path)] = *entry
			}
		}
	}
	for _, move := range result.MovedToYOSBR {
		if !move.ContentChanged {
			continue
		}
		modified = append(modified, move.To)
		if details {
			if entry := lineDiff(move.To, oldFiles[move.From], newFiles[move.To]); entry != nil {
				if _, exists := lineDiffs[strings.ToLower(move.To)]; !exists {
					lineDiffs[strings.ToLower(move.To)] = *entry
				}
			}
		}
	}
	// Sorting puts duplicates next to each other.
	result.Modified = slices.Compact(pycompat.SortedLower(modified))
	pycompat.SortLowerBy(result.MovedToYOSBR, func(m Move) string { return m.To })
	for _, key := range slices.Sorted(maps.Keys(lineDiffs)) {
		result.LineDiffs = append(result.LineDiffs, lineDiffs[key])
	}
	return result
}

func subtree(tree pack.Tree, folder string) map[string][]byte {
	prefix := folder + "/"
	result := map[string][]byte{}
	for path, data := range tree {
		if strings.HasPrefix(path, prefix) {
			result[path[len(prefix):]] = data
		}
	}
	return result
}

// Compare returns everything that changed from oldTree (an earlier release)
// to newTree. details=false skips the config files' line diffs.
func Compare(oldTree, newTree pack.Tree, previousVersion, currentVersion, minecraft string, details bool) *PackDiff {
	previousMinecraft := ""
	if content, ok := oldTree["pack.toml"]; ok {
		if data, err := pack.DecodeTOML(content); err == nil {
			previousMinecraft = pycompat.Or(pack.Table(data["versions"])["minecraft"], "")
		}
	}
	old, current := byCategory(oldTree), byCategory(newTree)
	newlyAdded, reenabled := additionBreakdown(old["mods"], current["mods"])
	return &PackDiff{
		PreviousVersion:   previousVersion,
		PreviousMinecraft: previousMinecraft,
		CurrentVersion:    currentVersion,
		Minecraft:         minecraft,
		Mods:              category(old["mods"], current["mods"]),
		ResourcePacks:     category(old["resourcepacks"], current["resourcepacks"]),
		ShaderPacks:       category(old["shaderpacks"], current["shaderpacks"]),
		NewlyAdded:        newlyAdded,
		Reenabled:         reenabled,
		Config:            Config(subtree(oldTree, "config"), subtree(newTree, "config"), details),
	}
}

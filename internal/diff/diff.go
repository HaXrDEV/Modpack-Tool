// Package diff works out what changed between two states of a pack (an
// earlier release and now). Both sides are pack.Trees, from pack.ReadTree
// (the working copy) or git snapshots (a release tag).
package diff

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"sort"
	"strings"

	"github.com/pmezard/go-difflib/difflib"

	"github.com/HaXrDEV/Modpack-Tool/internal/pack"
	"github.com/HaXrDEV/Modpack-Tool/internal/pycompat"
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

func plural(n int) string {
	if n == 1 {
		return ""
	}
	return "s"
}

// Summary is a one-line count of the changes, e.g. "+3 mods, 22 updated, 4
// config files changed".
func (d *PackDiff) Summary() string {
	var parts []string
	if n := len(d.Mods.Added); n > 0 {
		parts = append(parts, fmt.Sprintf("+%d mod%s", n, plural(n)))
	}
	if n := len(d.Mods.Removed); n > 0 {
		parts = append(parts, fmt.Sprintf("-%d mod%s", n, plural(n)))
	}
	if n := len(d.Mods.Updated) + len(d.ResourcePacks.Updated) + len(d.ShaderPacks.Updated); n > 0 {
		parts = append(parts, fmt.Sprintf("%d updated", n))
	}
	packs := len(d.ResourcePacks.Added) + len(d.ResourcePacks.Removed) + len(d.ShaderPacks.Added) + len(d.ShaderPacks.Removed)
	if packs > 0 {
		parts = append(parts, fmt.Sprintf("%d resource/shader pack%s added or removed", packs, plural(packs)))
	}
	if n := len(d.ChangedConfigs()); n > 0 {
		parts = append(parts, fmt.Sprintf("%d config file%s changed", n, plural(n)))
	}
	if len(parts) == 0 {
		return "no changes"
	}
	return strings.Join(parts, ", ")
}

// ChangedConfigs are the modified, removed and moved config files.
func (d *PackDiff) ChangedConfigs() []string {
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
	paths := make([]string, 0, len(set))
	for p := range set {
		paths = append(paths, p)
	}
	sort.Strings(paths)
	return paths
}

// Tagged is a name for changelogs, with a `Client`/`Server` tag when side tags are on.
func Tagged(n Named, sideTags bool) string {
	if sideTags && n.Side != "both" {
		return n.Name + " `" + pycompat.Capitalize(n.Side) + "`"
	}
	return n.Name
}

// metafiles returns a category's metafiles by file name, in path order.
func metafiles(tree pack.Tree, category string) ([]string, map[string]pack.Mod) {
	mods, _ := pack.ParseMods(tree, []string{category})
	var names []string
	byName := map[string]pack.Mod{}
	for _, mod := range mods {
		name := pycompat.Name(mod.Rel)
		if _, seen := byName[name]; !seen {
			names = append(names, name)
		}
		byName[name] = mod
	}
	return names, byName
}

func active(names []string, mods map[string]pack.Mod) ([]string, map[string]pack.Mod) {
	var kept []string
	result := map[string]pack.Mod{}
	for _, name := range names {
		if !mods[name].Disabled() {
			kept = append(kept, name)
			result[name] = mods[name]
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

// Category returns the added, removed and updated active files of one
// category (mods, resourcepacks, shaderpacks).
func Category(oldTree, newTree pack.Tree, category string) CategoryDiff {
	oldNames, oldAll := metafiles(oldTree, category)
	newNames, newAll := metafiles(newTree, category)
	oldNames, old := active(oldNames, oldAll)
	newNames, current := active(newNames, newAll)
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

// AdditionBreakdown returns the mods that are new to the pack and the ones
// that were disabled before, each sorted without duplicates.
func AdditionBreakdown(oldTree, newTree pack.Tree) ([]string, []string) {
	oldNames, old := metafiles(oldTree, "mods")
	byDisplay := map[string]pack.Mod{}
	for _, name := range oldNames {
		byDisplay[old[name].DisplayName()] = old[name]
	}
	newlyAdded, reenabled := map[string]bool{}, map[string]bool{}
	newNames, current := metafiles(newTree, "mods")
	for _, name := range newNames {
		mod := current[name]
		if mod.Disabled() {
			continue
		}
		previous, ok := old[name]
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
	return sortedKeys(newlyAdded), sortedKeys(reenabled)
}

func sortedKeys(set map[string]bool) []string {
	keys := []string{}
	for key := range set {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	pycompat.SortLower(keys)
	return keys
}

func isYOSBR(path string) bool { return strings.HasPrefix(strings.ToLower(path), "yosbr/") }

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

func hashes(files map[string][]byte) map[string]string {
	result := map[string]string{}
	for path, data := range files {
		if !generatedByTool(path) {
			sum := sha256.Sum256(data)
			result[path] = hex.EncodeToString(sum[:])
		}
	}
	return result
}

func sortedLower(values []string) []string {
	sort.Strings(values)
	pycompat.SortLower(values)
	return values
}

// Config compares two config folders ({path relative to config/: bytes}).
// details=false skips the line-by-line diffs (enough for a summary).
func Config(oldFiles, newFiles map[string][]byte, details bool) ConfigDiff {
	old, current := hashes(oldFiles), hashes(newFiles)
	var added, removed, modified []string
	for path := range current {
		if _, ok := old[path]; !ok {
			added = append(added, path)
		}
	}
	for path, hash := range old {
		if newHash, ok := current[path]; !ok {
			removed = append(removed, path)
		} else if newHash != hash {
			modified = append(modified, path)
		}
	}
	added, removed, modified = sortedLower(added), sortedLower(removed), sortedLower(modified)

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
		if !isYOSBR(path) {
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
		result.MovedToYOSBR = append(result.MovedToYOSBR, Move{From: path, To: target, ContentChanged: old[path] != current[target]})
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
	unique := map[string]bool{}
	for _, path := range modified {
		if !unique[path] {
			unique[path] = true
			result.Modified = append(result.Modified, path)
		}
	}
	result.Modified = sortedLower(result.Modified)
	sort.SliceStable(result.MovedToYOSBR, func(i, j int) bool {
		return strings.ToLower(result.MovedToYOSBR[i].To) < strings.ToLower(result.MovedToYOSBR[j].To)
	})
	keys := make([]string, 0, len(lineDiffs))
	for key := range lineDiffs {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, key := range keys {
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
	newlyAdded, reenabled := AdditionBreakdown(oldTree, newTree)
	return &PackDiff{
		PreviousVersion:   previousVersion,
		PreviousMinecraft: previousMinecraft,
		CurrentVersion:    currentVersion,
		Minecraft:         minecraft,
		Mods:              Category(oldTree, newTree, "mods"),
		ResourcePacks:     Category(oldTree, newTree, "resourcepacks"),
		ShaderPacks:       Category(oldTree, newTree, "shaderpacks"),
		NewlyAdded:        newlyAdded,
		Reenabled:         reenabled,
		Config:            Config(subtree(oldTree, "config"), subtree(newTree, "config"), details),
	}
}

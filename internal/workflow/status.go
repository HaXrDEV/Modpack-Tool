package workflow

import (
	"context"
	"fmt"
	"path/filepath"
	"strings"

	"github.com/HaXrDEV/Modpack-Tool/internal/changelog"
	"github.com/HaXrDEV/Modpack-Tool/internal/diff"
	"github.com/HaXrDEV/Modpack-Tool/internal/files"
	"github.com/HaXrDEV/Modpack-Tool/internal/ui"
)

// Status is where the pack stands: the dashboard card and `status`.
type Status struct {
	Name, Version, Minecraft, Loader, LoaderVersion string
	Tag                                             string // The release tag of the version, "" when unreleased.
	IsRepo                                          bool

	Base    string         // The release compared against; "" when there is none.
	Changes *diff.PackDiff // Without config line diffs.
	// ChangesError is why the changes couldn't be read.
	ChangesError string

	ChangelogName   string
	ChangelogExists bool
	OverviewLines   int
	ConfigLines     int
	ChangelogError  string

	Next    string // What to do next, e.g. "Draft changelog (3), then edit it."
	NextKey string // The action Next names ("2".."5"), for the dashboard cursor.
}

// ComputeStatus works out where the pack stands. Problems end up in the
// status instead of failing, so the dashboard always shows something.
func ComputeStatus(ctx context.Context, env *Env) Status {
	p := env.Project
	s := Status{Name: p.Name, Version: p.Version, Minecraft: p.Minecraft, Loader: p.LoaderLabel(),
		LoaderVersion: p.LoaderVersion, IsRepo: env.Git.IsRepo()}
	var err error
	if s.Tag, err = ReleaseTag(ctx, env, ""); err != nil {
		s.ChangesError = err.Error()
	}
	if s.ChangesError == "" {
		s.Changes, s.Base, err = ChangesSinceRelease(ctx, env, "", false)
		if err != nil {
			s.ChangesError = err.Error()
		}
	}
	path := changelog.Path(p, "", "")
	s.ChangelogName = filepath.Base(path)
	var data *changelog.Changelog
	if s.ChangelogExists = files.Exists(path); s.ChangelogExists {
		if data, err = changelog.Load(path); err != nil {
			s.ChangelogError = err.Error()
		} else {
			s.OverviewLines = len(data.Lines("Update overview"))
			s.ConfigLines = len(data.Lines("Config Changes"))
		}
	}
	s.Next, s.NextKey = NextStep(ctx, env, s.Tag, data)
	return s
}

// NextStep is what to do next, and the key of that action.
func NextStep(ctx context.Context, env *Env, tag string, data *changelog.Changelog) (string, string) {
	if tag != "" {
		return "start a New version (2) before changing the pack.", "2"
	}
	if data == nil || data.IsEmpty() {
		return "Draft changelog (3), then edit it.", "3"
	}
	if last := ReadLastBuild(ctx, env); last != nil && last.Version == env.Project.Version {
		current, reason := BuildIsCurrent(env, last)
		if current {
			return "Publish (5).", "5"
		}
		first, _, _ := strings.Cut(reason, ".")
		return "Build release (4) again. " + first + ".", "4"
	}
	return "Build release (4) when the pack is ready.", "4"
}

// LineCount is "empty", "1 line" or "N lines", for a changelog section.
func LineCount(count int) string {
	if count == 0 {
		return "empty"
	}
	return fmt.Sprintf("%d line%s", count, ui.Plural(count))
}

// Lines renders the status as text, the way `status` prints it.
func (s Status) Lines() []string {
	state := "not released yet"
	if s.Tag != "" {
		state = "released, tag " + s.Tag
	}
	out := []string{fmt.Sprintf("%s %s (%s) · Minecraft %s · %s %s", s.Name, s.Version, state, s.Minecraft, s.Loader, s.LoaderVersion)}
	switch {
	case s.ChangesError != "":
		out = append(out, "Changes: "+s.ChangesError)
	case s.Base != "":
		out = append(out, fmt.Sprintf("Since %s: %s", s.Base, s.Changes.Summary()))
	case s.IsRepo:
		out = append(out, "No earlier release tag found.")
	}
	switch {
	case s.ChangelogError != "":
		out = append(out, "Changelog: "+s.ChangelogError)
	case !s.ChangelogExists:
		out = append(out, fmt.Sprintf("Changelog: %s doesn't exist yet", s.ChangelogName))
	default:
		out = append(out, fmt.Sprintf("Changelog: %s · overview %s · config changes %s", s.ChangelogName, LineCount(s.OverviewLines), LineCount(s.ConfigLines)))
	}
	return append(out, "Next: "+s.Next)
}

// ChangesReport describes everything that changed since the last release,
// for the View changes screen and `changes`.
func ChangesReport(ctx context.Context, env *Env, since string) ([]string, error) {
	changes, base, err := ChangesSinceRelease(ctx, env, since, true)
	if err != nil {
		return nil, err
	}
	if changes == nil {
		return []string{"No earlier release tag found, so there is nothing to compare against."}, nil
	}
	sideTags := env.Project.Settings.SideTags
	out := []string{fmt.Sprintf("Compared with %s: %s", base, changes.Summary())}
	if changes.Migration() {
		out = append(out, fmt.Sprintf("Minecraft %s -> %s", changes.PreviousMinecraft, changes.Minecraft))
	}
	for _, c := range []struct {
		title string
		diff  diff.CategoryDiff
	}{{"Mods", changes.Mods}, {"Resource packs", changes.ResourcePacks}, {"Shader packs", changes.ShaderPacks}} {
		if len(c.diff.Added)+len(c.diff.Removed)+len(c.diff.Updated) == 0 {
			continue
		}
		out = append(out, "", c.title)
		for _, n := range c.diff.Added {
			out = append(out, "  + "+diff.Tagged(n, sideTags))
		}
		for _, n := range c.diff.Removed {
			out = append(out, "  - "+diff.Tagged(n, sideTags))
		}
		for _, u := range c.diff.Updated {
			out = append(out, fmt.Sprintf("  ~ %s: %s -> %s", u.Name, u.Before, u.After))
		}
	}
	if len(changes.NewlyAdded)+len(changes.Reenabled) > 0 {
		out = append(out, "")
		if len(changes.NewlyAdded) > 0 {
			out = append(out, "New to the pack: "+strings.Join(changes.NewlyAdded, ", "))
		}
		if len(changes.Reenabled) > 0 {
			out = append(out, "Re-enabled: "+strings.Join(changes.Reenabled, ", "))
		}
	}
	config := changes.Config
	if len(config.Added)+len(config.Removed)+len(config.Modified)+len(config.MovedToYOSBR) > 0 {
		out = append(out, "", "Config files")
		for _, path := range config.Added {
			out = append(out, "  + "+path)
		}
		for _, path := range config.Removed {
			out = append(out, "  - "+path)
		}
		for _, move := range config.MovedToYOSBR {
			out = append(out, fmt.Sprintf("  > %s -> %s", move.From, move.To))
		}
		lineDiffs := map[string]diff.LineDiff{}
		for _, entry := range config.LineDiffs {
			lineDiffs[entry.Path] = entry
		}
		for _, path := range config.Modified {
			out = append(out, "  ~ "+path)
			entry, ok := lineDiffs[path]
			if !ok {
				continue
			}
			for _, line := range entry.RemovedLines {
				out = append(out, "      - "+line)
			}
			for _, line := range entry.AddedLines {
				out = append(out, "      + "+line)
			}
		}
	}
	return out, nil
}

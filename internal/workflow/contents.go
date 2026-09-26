package workflow

import (
	"cmp"
	"context"
	"path"
	"slices"
	"strings"

	"github.com/HaXrDEV/Modpack-Tool/internal/changelog"
	"github.com/HaXrDEV/Modpack-Tool/internal/pack"
	"github.com/HaXrDEV/Modpack-Tool/internal/platform"
)

// ReleaseContents is what a release contains, for the wiki's modlists: every
// enabled mod, resource pack and shader pack with its file, its side when the
// pack shows side tags, and its project page and authors from Modrinth and
// CurseForge. When those can't be looked up, the pages are linked by project
// id and the authors left out. bundled are the files the pack ships itself
// (see pack.Bundled), which are listed by name only.
func ReleaseContents(ctx context.Context, env *Env, mods []pack.Mod, bundled []string) *changelog.Contents {
	mods = slices.DeleteFunc(slices.Clone(mods), pack.Mod.Disabled)
	var modrinthIDs []string
	var curseForgeIDs []int64
	for _, mod := range mods {
		modrinthIDs = append(modrinthIDs, mod.ModrinthProject())
		curseForgeIDs = append(curseForgeIDs, mod.CurseForgeProject())
	}
	var projects map[string]platform.Project
	var teams map[string][]platform.TeamMember
	var cfMods map[int64]platform.CFMod
	err := together(ctx, func(ctx context.Context) (err error) {
		if projects, err = env.API.ModrinthProjects(ctx, modrinthIDs); err != nil {
			return err
		}
		var teamIDs []string
		for _, project := range projects {
			teamIDs = append(teamIDs, project.Team)
		}
		teams, err = env.API.ModrinthTeams(ctx, teamIDs)
		return err
	}, func(ctx context.Context) (err error) {
		cfMods, err = env.API.CurseForgeMods(ctx, curseForgeIDs)
		return err
	})
	if err != nil {
		env.UI.Warn("Couldn't look up the mods' pages and authors for the wiki's modlist, so it links them by id and leaves the authors out: " + err.Error())
	}
	contents := &changelog.Contents{Mods: []changelog.Item{}, ResourcePacks: []changelog.Item{}, ShaderPacks: []changelog.Item{}}
	for _, mod := range mods {
		item := changelog.Item{Name: mod.DisplayName(), File: mod.Filename(), URL: mod.ProjectURL()}
		if env.Project.Settings.SideTags && mod.Side() != "both" {
			item.Side = mod.Side()
		}
		if project, ok := projects[mod.ModrinthProject()]; ok && mod.Modrinth() != nil {
			item.URL = "https://modrinth.com/" + cmp.Or(project.ProjectType, "project") + "/" + cmp.Or(project.Slug, project.ID)
			item.Authors = owner(teams[project.Team])
		} else if project, ok := cfMods[mod.CurseForgeProject()]; ok && mod.CurseForge() != nil {
			item.URL = cmp.Or(project.Links.WebsiteURL, item.URL)
			for _, author := range project.Authors {
				item.Authors = append(item.Authors, author.Name)
			}
		}
		contents.Add(mod.Category(), item)
	}
	for _, rel := range bundled {
		category, file, _ := strings.Cut(rel, "/")
		name := file
		if ext := path.Ext(file); strings.EqualFold(ext, ".jar") || strings.EqualFold(ext, ".zip") {
			name = strings.TrimSuffix(file, ext) // Folders keep dots such as "r5.2.1".
		}
		contents.Add(category, changelog.Item{Name: pack.TidyName(name), File: file})
	}
	// "[Let's Do] Brewery" goes under L.
	key := func(item changelog.Item) string { return strings.ToLower(strings.TrimPrefix(item.Name, "[")) }
	for _, items := range [][]changelog.Item{contents.Mods, contents.ResourcePacks, contents.ShaderPacks} {
		slices.SortStableFunc(items, func(a, b changelog.Item) int { return strings.Compare(key(a), key(b)) })
	}
	return contents
}

// owner is a Modrinth team's owner, or its first member when none is marked.
func owner(members []platform.TeamMember) []string {
	if len(members) == 0 {
		return nil
	}
	first := slices.MinFunc(members, func(a, b platform.TeamMember) int { return cmp.Compare(a.Ordering, b.Ordering) })
	for _, member := range members {
		if strings.EqualFold(member.Role, "owner") {
			first = member
			break
		}
	}
	return []string{first.User.Username}
}

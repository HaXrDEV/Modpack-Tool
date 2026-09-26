package workflow

import "fmt"

// Help is the overview shown by the help screen and --help: how a release
// works, what packwiz does, disabled mods, and where files live.
func Help(configPath, cacheDir string) string {
	return fmt.Sprintf(`HOW A RELEASE WORKS
  1. New version (2)      right after a release, so new changes go into the next version
  2. Change the pack      with packwiz (below) and Update mods (1)
  3. Draft changelog (3)  then edit Changelogs/<version>.yml by hand
  4. Build release (4)    release record + notes, packs in Export/
  5. Publish (5)          commit, push, GitHub release. publish.yml uploads to CurseForge/Modrinth
                          and the wiki sync picks up Changelogs/data/.

PACKWIZ DOES THE REST (run these inside the pack's Packwiz folder)
  packwiz modrinth add <slug|url>      add a Modrinth project (curseforge / github / url add work alike)
  packwiz remove <slug>                remove a file
  packwiz pin <slug> / unpin <slug>    stop / resume updates for one file
  packwiz list -s client               what a client install gets
  packwiz refresh                      rebuild index.toml after editing files by hand
  packwiz serve                        serve the pack locally for packwiz-installer tests

DISABLED MODS
  A disabled mod has side = "both(disabled)" (or client/server). It stays in the pack and packwiz
  keeps updating it; the tool leaves it out of exports, changelogs and the modlists. Update mods
  offers to re-enable disabled mods that get an update; Migrate disables mods with no build.

FILES
  <pack>/modpack-tool.yml             this pack's settings
  <pack>/Changelogs/<version>.yml     the changelog you're writing (data/ has every release's record)
  <pack>/CurseForge-Release.md, Modrinth-Release.md   release notes publish.yml uploads
  <pack>/Export/                      built packs, plus bundled_links.md
  %s
      known projects, packwiz path, optional CurseForge API key
  %s
      downloaded files, fingerprints and the last run's log (safe to delete)`, configPath, cacheDir)
}

![header](https://capsule-render.vercel.app/api?type=waving&height=250&color=timeGradient&text=HaXr%27s%20Modpack%20Tool&fontAlignY=46&animation=fadeIn)

A guided release assistant for [packwiz](https://github.com/packwiz/packwiz) modpacks. packwiz manages the mods; this tool handles everything around a release: changelogs, release notes, CurseForge/Modrinth/server packs, and the GitHub release that publishes them.

> [!WARNING]
> A personal tool built around a specific workflow (CrismPack packs and wiki). It isn't meant for general use.

## After a long break: start here

Run `modpack-tool` inside a pack folder (or anywhere; it opens the last pack you used). The dashboard shows where the pack stands and what to do next, with the cursor already on that action:

```
 HaXr's Modpack Tool                                      Breakneck
╭────────────────────────────────────────────────────────────────╮
│ 26.2-1.1 · not released         Minecraft 26.2 · Fabric 0.19.1 │
│ Since 26.2-1.0  +3 mods, -1 mod, 22 updated, 4 config files ch…│
│ Changelog  overview 3 lines · config changes empty             │
│ Next  Draft changelog (3), then edit it.                       │
╰────────────────────────────────────────────────────────────────╯
  1  Update mods        packwiz update, then the alpha guard
  2  New version        bump or rename the version
› 3  Draft changelog    fill sections from the changes
  ...
 ↑↓ select • enter run • p projects • ? help • q quit
```

A release, start to finish:

1. **New version (2)** right after a release, so new changes go into the next version.
2. **Change the pack** with packwiz (see below) and **Update mods (1)**.
3. **Draft changelog (3)**, then edit `Changelogs/<version>.yml` by hand.
4. **Build release (4)**: release record and notes, pack files, and the packs in `Export/`.
5. **Publish (5)**: commit, push and create the GitHub release. The pack's `publish.yml` uploads it to CurseForge/Modrinth, and the wiki sync picks up `Changelogs/data/`.

Press `?` on the dashboard for the same overview, a packwiz cheat sheet and where files live.

## Setup

Needs [Go](https://go.dev/dl/) 1.25 or newer, [packwiz](https://github.com/packwiz/packwiz), git, and the [GitHub CLI](https://cli.github.com) (`gh`, logged in) for publishing. Install or update the tool with:

```
go install github.com/HaXrDEV/Modpack-Tool/cmd/modpack-tool@latest
```

That puts `modpack-tool.exe` in `%USERPROFILE%\go\bin` (next to packwiz); make sure that folder is on your `PATH`.

The first run asks for a modpack folder: the folder that contains `Packwiz/pack.toml` (drag & drop works). The tool remembers it; `p` on the dashboard switches, adds or removes projects.

## The dashboard

| Key | Action | What it does |
|---|---|---|
| 1 | Update mods | `packwiz update --all`, then an alpha guard (keep, move to the newest beta/release, or revert) and an offer to re-enable disabled mods that received an update. Pinned mods can be unpinned for one run. |
| 2 | New version | Bumps the version in `pack.toml` and the BetterCompatibilityChecker configs and creates the changelog file. If the current version was never released, it can be renamed instead. With `prereleases: previews`, a full release starts with what you wrote for its pre-releases. |
| 3 | Draft changelog | Fills `Update overview` and `Config Changes` from the changes since the last release. Text you wrote is only replaced after you confirm; the rest of the file stays exactly as it is. |
| 4 | Build release | Updates `bcc.json`, the Crash Assistant modlist and `modlist.md`, writes the release record and release notes, and builds the packs listed in `exports`. Refuses an empty changelog. |
| 5 | Publish | Commits, pushes and runs `gh release create` with the built files, asking before each step. Refuses if the pack changed since the last build. |
| 6 | Migrate Minecraft | `packwiz migrate minecraft`, the same alpha guard and re-enable offer, then disables mods with no build for the new version and starts a new version (it suggests a beta when mods had to be disabled). |
| 7 | Check pack | Invalid sides, leftover disabled folders, disabled and pinned mods, and library mods nothing depends on (removal via `packwiz remove`). |
| 8 | View changes | Everything that changed since the last release: mods, packs and config lines. |

Digits move the cursor and Enter runs the action, so a stray key never starts one. While an action runs, each step shows a spinner (with a progress bar for downloads), questions appear in a panel at the bottom, and `l` shows the full packwiz/git output. `Esc` or `Ctrl+C` cancels; packwiz and git always finish the command they are running first, so no file is left half-written. A second `Ctrl+C` quits.

Every action is also a command with plain output, for scripts: `modpack-tool status`, `update`, `new-version [VERSION]`, `draft [--since REF]`, `build [--since REF] [--skip-server] [--no-review]`, `publish [--dry-run]`, `migrate [MINECRAFT]`, `check`, `changes [--since REF]`. Add `--project PATH` to pick a pack; `--help` lists everything.

## packwiz does the rest

Things packwiz already does well aren't wrapped. Run these inside the pack's `Packwiz` folder:

```
packwiz modrinth add <slug|url>      add a Modrinth project (curseforge / github / url add work alike)
packwiz remove <slug>                remove a file
packwiz pin <slug> / unpin <slug>    stop / resume updates for one file
packwiz list -s client               what a client install gets
packwiz refresh                      rebuild index.toml after editing files by hand
```

## How it works

- **Releases are git tags.** A release is a tag named after the pack version (`4.11.1` or `v4.11.1`). The previous release is the nearest earlier tag (skipping pre-releases for a full release with `prereleases: previews`), and its files are read straight from git to compute what changed. A tagged version is frozen: Draft and Build point you to New version instead.
- **Pre-releases** are versions such as `26.2-1.0-beta.1` or `2.2.0-beta.1` (`alpha` and `rc` work too). Their release notes start by saying they may be less stable or feature complete than a full release, Publish creates a GitHub pre-release, and `publish.yml` marks them beta (or alpha) on CurseForge and Modrinth. The `prereleases` setting decides how they relate to their full release (`26.2-1.0`):
  - `standalone` (the default): they're releases like any other. Every release lists what changed since the one before it, and the wiki shows them among the other releases, labeled as alpha or beta.
  - `previews`: they lead up to their full release, which covers them all. It's compared with the previous full release, New version starts its changelog with the Changes/Improvements, Bug Fixes and Script/Datapack changes of its pre-releases, and the wiki folds them under it.
- **Changelogs.** You write `Changelogs/<version>+<minecraft>.yml` (just `<version>.yml` for versions like `26.2-1.0`). Build turns it into `Changelogs/data/<same name>.json`, the release record that the [wiki](https://github.com/CrismPack/Wiki) renders, plus `CurseForge-Release.md` and `Modrinth-Release.md` for `publish.yml`. The record also lists everything in the release, with each project's page and authors from Modrinth and CurseForge, for the wiki's modlists.
- **Disabled mods** have `side = "both(disabled)"` (or `client`/`server`). They stay in the pack and packwiz keeps updating them; the tool leaves them out of exports, changelogs and the modlists.
- **Exports** follow packwiz's own index, so `.packwizignore` applies. Mods, resource packs and shader packs are all included. For the CurseForge zip, files from other platforms are matched on CurseForge by fingerprint and bundled only when CurseForge doesn't have them; the `.mrpack` gets proper hashes and sizes. `Export/bundled_links.md` lists every bundled file with its source, for license checks.
- **Server pack**: the `Server Pack` folder (start scripts, server configs, extra jars in `mods/`) plus every server-side mod jar, minus `server_exclude`. Jars are downloaded and cached. For the few files whose authors block third-party downloads, the tool opens their CurseForge download pages in your browser and picks the files up from your Downloads folder as they arrive (recognized by hash, even if the browser renamed them). You can also point it at a folder that has them. Either way it's needed once per file version.

## Settings

Each pack has a `modpack-tool.yml` next to its `Packwiz` folder, created on first use (values from an old `settings.yml` are imported) and documented inline:

| Setting | Meaning |
|---|---|
| `exports` | Which packs Build creates: `curseforge`, `modrinth`, `server`. |
| `server_template` | The folder copied into the server pack (default `Server Pack`). |
| `server_exclude` | Mods left out of the server pack, by slug, name or jar filename. |
| `mc_prefixed_versions` | Suggest `<minecraft>-<release>` versions such as `26.2-1.0`. |
| `prereleases` | `standalone`: pre-releases are releases like any other, marked as less stable. `previews`: they lead up to a full release that covers them. |
| `alpha_updates` | When an update lands on an alpha: `prompt`, `never` or `always`. |
| `side_tags` | Show `Client`/`Server` after mod names in changelogs and `modlist.md`. |
| `changelog_url` | The "Full changelog" link in release notes (`{mc_group}`, `{mc}`, `{version}`, `{anchor}`). |
| `curseforge_notes_footer`, `modrinth_notes_footer` | Markdown appended to the release notes (e.g. a sponsor banner). |

## Where files live

| Path | What |
|---|---|
| `%AppData%\modpack-tool\config.yml` | Known projects, an optional `packwiz_exe_path` (default: PATH, then `%USERPROFILE%\go\bin\packwiz.exe`) and an optional `curseforge_api_key` (packwiz's public key is used otherwise). |
| `%LocalAppData%\modpack-tool\` | Downloaded files and CurseForge fingerprints (safe to delete), and `last-run.log`, the full output of the last action. |

`MODPACK_TOOL_CONFIG` and `MODPACK_TOOL_CACHE` point the tool somewhere else, handy for testing on a copy of a pack.

## Troubleshooting

- **"No earlier release tag found"**: the previous release isn't tagged. Tag it (`git tag 2.2.0 <commit>`) or pass `--since <tag or commit>` to `draft`/`build`.
- **A file can't be downloaded**: its author blocks third-party downloads on CurseForge. Choose `b` and the tool opens the download pages and waits for the files in your Downloads folder (if your browser saves somewhere else, save them there by hand). Or choose `f` and point it at a folder that has them, such as a CurseForge app instance's `mods` folder. Either way they are cached afterwards.
- **git push asks for a login**: the tool never waits for typed credentials. Run `git push` once in a terminal (or `gh auth setup-git`) and publish again.
- **An old tool is needed**: the Python version of this tool is tagged `python-final`, and the version before it `legacy-v1` (for example for a release on an old Minecraft line that still uses `CHANGELOG.md`). `git worktree add ../Modpack-Tool-python python-final` and run its `run_modpack_tool.bat` (needs Python 3.11).

## Development

`go test ./...` runs everything; `go vet ./...` and `gofmt -l .` should be clean. The command is `cmd/modpack-tool`, and `internal/` has one package per concern:

- `workflow`: the actions and the command table that drives the dashboard, the subcommands and the help;
- `tui`: the dashboard (Bubble Tea v2); `ui`: the `Session` interface workflows talk to, and the plain line-by-line session;
- `pack`, `project`, `changelog`, `diff`, `draft`, `export`, `platform`, `git`, `packwiz`, `version`: the parts of a release;
- `pycompat`: the text, JSON and YAML formats the Python version of the tool wrote, so the files in the packs stay byte-identical.

The `testdata` goldens were generated from the Python tool (`scripts/golden.py` at the `python-final` tag) and are the specification of those formats.

## Credits

- [packwiz](https://github.com/packwiz/packwiz): mod metadata and pack format; the bundled CurseForge community API key is packwiz's.
- [mmc-export](https://github.com/RozeFound/mmc-export): the fingerprint-based CurseForge export follows its approach.
- [Charm](https://charm.land): Bubble Tea, Bubbles and Lip Gloss for the dashboard.

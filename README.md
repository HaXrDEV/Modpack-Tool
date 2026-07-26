![header](https://capsule-render.vercel.app/api?type=waving&height=250&color=timeGradient&text=HaXr%27s%20Modpack%20Tool&fontAlignY=46&animation=fadeIn)

Requires Python 3.11.

> [!WARNING]
> A personal tool for automating my own modpack development. It expects a very specific workflow and isn't meant for general use.

## Credits

- [packwiz](https://github.com/packwiz/packwiz): mod metadata and pack format; the bundled CurseForge community API key is sourced from packwiz.
- [mmc-export](https://github.com/RozeFound/mmc-export): the fingerprint-based CurseForge export (murmur2 via `/v1/fingerprints`) follows its approach.

## Setup

Clone anywhere and run `run_modpack_tool.bat` (creates the `venv`, reinstalls deps when `requirements.txt` changes, then launches). Update with `git pull`.

The tool manages one or more **modpack projects**. A project is a folder containing `Packwiz/pack.toml` (required; how a project is recognized) and `settings.yml` (auto-created from the template). `Changelogs/`, `Export/`, and `Server Pack/` are created as needed. First launch asks for a project path (drag & drop works); after that it reopens the last-used one. `P)` in the menu switches, adds, or removes projects.

Tool state lives in `tool_config.yml` (gitignored) beside the scripts:

```yaml
last_used_project: C:\path\to\MyPack
packwiz_exe_path: ""   # empty → %USERPROFILE%\go\bin\packwiz.exe
github_token: ""       # empty → gh-token.txt, then a prompt
curseforge_api_key: "" # empty → cf-api-key.txt, then the packwiz community key
projects:
  - { name: MyPack, root: C:\path\to\MyPack }
```

Per-project cache (comparison snapshots, previous releases) lives in a gitignored `.modpack-tool/` at the project root. An old embedded install (`<project>/Modpack-CLI-Tool/`) is auto-detected and migrated on first activation.

Every `settings.yml` flag is documented inline in [`settings_template.yml`](settings_template.yml). On launch each project's `settings.yml` is reconciled to the template: missing settings are added, obsolete or unknown keys are dropped, and legacy keys are renamed, all while keeping your existing values, so the file stays complete and matches the template's layout.

## Action menu

`1` configured workflow · `2` migration · `3` export client · `4` export server · `5` migration + client · `6` migration + client + server · `7` refresh · `8` update mods · `9` change version (rename or bump) · `10` clear cache · `11` changelog summary · `12` list disabled · `13` add mod · `14` find orphaned libraries · `P` manage projects · `0` exit

## Export

- **Client** (`client_export_multi_platform`): `false` (default) delegates to `packwiz {client_export_format} export` (`curseforge`/`modrinth`). `true` natively builds both a CurseForge `.zip` and a Modrinth `.mrpack`, resolving mods by murmur2 fingerprint; anything unresolved on CurseForge is bundled as a JAR override.
- **Server**: one manual step. Build a CurseForge-launcher instance from the exported zip, then drag its `mods` folder into the terminal; the tool filters those into the server pack.

## Minecraft migration (`migrate_minecraft_version`)

Updates `pack.toml` to the target MC version and loader, refreshes and updates mods, disables incompatible ones, then bumps the version (prompted; Enter keeps the current one) and creates the matching changelog template. Targets are prompted if not set in `settings.yml`.

## Versioning

By default the version in `pack.toml` is used as-is. Set `mc_prefixed_versions: True` for versions that embed the Minecraft version as `<content-update>-<release>` (e.g. `26.1-1.0`). Following Minecraft's year-based scheme, the version tracks the content update (`26.1`): a patch such as `26.1.1` continues the same line as its next release, while a new content update (`26.2`) resets the release to `1.0`. Patches share their content update's changelog page, with each release showing its exact Minecraft version. Pre-releases append a tag and sort first (`26.1-1.0-beta.1` before `26.1-1.0`; `beta`/`alpha`/`rc` drive release-type detection). The flag only changes prompt defaults; sorting and rendering handle mixed histories automatically and never crash on a malformed version.

## Changelogs

Each release has an authored `Changelogs/<version>+<mc>.yml`; the tool also emits a presentation-free `Changelogs/data/<version>+<mc>.json` for the wiki to render. When migrating to a new MC version, keep only the previous version's changelog in the repo so the tool compares the first new release against it.

Optional auto-fill during export:

- `auto_generate_update_overview`: deterministic `Update overview` from the local diff.
- `auto_generate_config_changes`: `Config Changes` via a local Ollama model (`auto_config_model` / `auto_config_endpoint`, etc.); skipped with a notice if unavailable.

`*_overwrite_existing` flags control whether existing sections are replaced.

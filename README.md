![header](https://capsule-render.vercel.app/api?type=waving&height=250&color=timeGradient&text=Modpack%20CLI%20Tool&fontAlignY=46&animation=fadeIn)

Requires Python 3.11

> [!WARNING]  
> This tool is made for the sole purpose of automating stuff when i develop my modpacks. It is therefore not made to be user friendly or flexible in any way as it requires a very specific workflow to function.
> Long story short, i do not recommend that anyone else uses this tool due to those reasons.

## Current behavior

The script (`Modpack-Export.py`) assumes this repo layout:

- `../settings.yml` (next to `Packwiz`, `Changelogs`, `Export`, `Server Pack`)
- `../Packwiz/pack.toml`
- `packwiz.exe` at `%USERPROFILE%\go\bin\packwiz.exe`

If these paths do not match your setup, the workflow will fail.

## Action menu

At startup, the tool now shows an action menu so you can choose what to run for this session:

- configured workflow from `settings.yml`
- migration only
- client export only
- server export only
- migration + client export
- migration + client + server export
- refresh only
- update mods only
- bump modpack version only
- clear stored repository data
- generate changelog summary only

`generate changelog summary only` only regenerates changelog text (`Update overview` and/or `Config Changes`) without exporting packs.

## Breakneck specific stuff

When migrating to a new Minecraft version (*For example, going from 1.21.3 to 1.21.4*), make sure to leave only the last changelog in the repository from the previous version like seen below. 
This is due to the program reading the files and thereby being able to recognize that it should compare the first new version to that old one.
This feature is only active when enabling the `breakneck-fixes` option.

![1736264492349](image/README/1736264492349.png)

## Automated Minecraft migration

You can enable automated migration in `settings.yml`:

- `migrate_minecraft_version`: Enables the migration flow.
- `migration_target_minecraft`: Target Minecraft version.
- `migration_target_fabric`: Optional target Fabric loader version.
- `migration_mod_loader`: Loader used for compatibility checks (default: `fabric`).
- `migration_update_all_mods`: Runs `packwiz update --all -y` after changing MC version.
- `migration_disable_incompatible_mods`: Disables mods that do not have a target-compatible update.

When enabled, the tool will:
1. Update `Packwiz/pack.toml` to the target Minecraft/Fabric versions.
2. Refresh and update mods with Packwiz.
3. Disable incompatible mods by setting `side = "...(disabled)"` in their `.toml` entries.

## Auto-generated changelog text

The export flow can auto-populate changelog fields in `Changelogs/<version>+<mc_version>.yml`:

- `Update overview` is deterministic and generated from local diff data.
- `Config Changes` can be generated with an LLM from config file diffs.

### Settings

- `auto_generate_update_overview`: Enables deterministic `Update overview` generation during export.
- `auto_summary_overwrite_existing`: Overwrites existing `Update overview` when `true`.
- `auto_generate_config_changes`: Enables LLM generation for `Config Changes`.
- `auto_config_overwrite_existing`: Overwrites existing `Config Changes` when `true`.
- `auto_config_provider`: Provider name (`ollama` currently supported).
- `auto_config_model`: Model used by Ollama (default: `qwen3:4b-instruct`).
- `auto_config_endpoint`: Ollama generate endpoint (default: `http://127.0.0.1:11434/api/generate`).
- `auto_config_timeout_seconds`: HTTP timeout for the model call.
- `auto_config_max_items`: Max config diff items per category included in the prompt.
- `auto_config_max_lines`: Max output bullet lines written to `Config Changes`.

`Config Changes` generation uses line-level diffs from modified files (old/new lines) so the model can summarize concrete option/value changes instead of only reporting that a file changed.

### Requirements

For `Config Changes`, run Ollama locally and pull a model, for example:

```powershell
ollama pull qwen3:4b-instruct
```

If the model call fails or no model is available, the tool does not write `Config Changes` and prints a notice instead.

## Notable runtime prompts

- Migration actions can prompt for target Minecraft/Fabric versions if not set in `settings.yml`.
- When summary generation is active, the script prompts whether to overwrite existing `Update overview` / `Config Changes` for the current run.
- Server export currently includes a manual step where you provide a dragged `mods` folder path from a created CurseForge instance.

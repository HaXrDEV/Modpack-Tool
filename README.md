![header](https://capsule-render.vercel.app/api?type=waving&height=250&color=timeGradient&text=Modpack%20CLI%20Tool&fontAlignY=46&animation=fadeIn)

Requires Python 3.11

> [!WARNING]  
> This tool is made for the sole purpose of automating stuff when i develop my modpacks. It is therefore not made to be user friendly or flexible in any way as it requires a very specific workflow to function.
> Long story short, i do not recommend that anyone else uses this tool due to the reasons above.

## Action menu

At startup, the tool now shows an action menu so you can choose what to run for this session:

- configured workflow from `settings.yml`
- migration only
- client export only
- server export only
- migration + client export
- migration + client + server export
- refresh only

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

"""HaXr's Modpack Tool. Run `python -m modpack_tool` (or run_modpack_tool.bat); `--help` lists commands.

One COMMANDS table drives the menu, the command line and the help screen, so
the three can't drift apart.
"""

import sys

if sys.version_info < (3, 11):
    sys.exit("HaXr's Modpack Tool needs Python 3.11 or newer.")

import argparse  # noqa: E402
import traceback  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402

from . import changelog, check, mods, pack, platforms, release, ui  # noqa: E402
from . import project as projects  # noqa: E402
from .ui import ToolError  # noqa: E402


@dataclass
class Command:
    key: str       # Menu key.
    name: str      # Subcommand name.
    label: str
    summary: str
    run: object    # run(project, args)
    arguments: list = field(default_factory=list)  # [(flags, argparse kwargs)]


def _new_version(project, args):
    release.new_version(project, version=getattr(args, "version", None))


def _migrate(project, args):
    mods.migrate(project, target=getattr(args, "minecraft", None),
                 new_version_step=lambda proj, suggestion: release.new_version(proj, suggestion=suggestion))


SINCE = (["--since"], {"metavar": "REF", "help": "compare against this tag or commit instead of the last release"})

COMMANDS = [
    Command("1", "update", "Update mods", "packwiz update --all, with an alpha guard and re-enable offers",
            lambda p, a: mods.update_mods(p)),
    Command("2", "new-version", "New version", "bump the version (rename it if unreleased) and create its changelog",
            _new_version, [(["version"], {"nargs": "?", "help": "the new version (asked when left out)"})]),
    Command("3", "draft", "Draft changelog", "fill changelog sections from the changes since the last release",
            lambda p, a: release.draft(p, since=getattr(a, "since", None)), [SINCE]),
    Command("4", "build", "Build release", "release record + notes, pack files, CurseForge/Modrinth/server packs",
            lambda p, a: release.build(p, since=getattr(a, "since", None),
                                       skip_server=getattr(a, "skip_server", False),
                                       review=not getattr(a, "no_review", False)),
            [SINCE, (["--skip-server"], {"action": "store_true", "help": "don't build the server pack"}),
             (["--no-review"], {"action": "store_true", "help": "don't offer to open the changelog first"})]),
    Command("5", "publish", "Publish", "commit, push and create the GitHub release (asks before each step)",
            lambda p, a: release.publish(p, dry_run=getattr(a, "dry_run", False)),
            [(["--dry-run"], {"action": "store_true", "help": "only show what would run"})]),
    Command("6", "migrate", "Migrate Minecraft", "move to another Minecraft version, disable incompatible mods",
            _migrate, [(["minecraft"], {"nargs": "?", "help": "target Minecraft version (asked when left out)"})]),
    Command("7", "check", "Check pack", "unused libraries, invalid sides, disabled and pinned mods",
            lambda p, a: check.check(p)),
]

HELP = """\
HOW A RELEASE WORKS
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
  <pack>/Changelogs/<version>.yml     the changelog you write (data/<version>.json is for the wiki)
  <pack>/CurseForge-Release.md, Modrinth-Release.md   release notes publish.yml uploads
  <pack>/Export/                      built packs, plus bundled_links.md
  <tool>/tool_config.yml              known projects, packwiz path, optional CurseForge API key
  <tool>/cache/                       downloaded files and fingerprints (safe to delete)"""


############################################################
# Status

def _lines(count, noun="line"):
    return f"{count} {noun}{'s' * (count != 1)}" if count else "empty"


def next_step(project, changelog_data):
    if release.release_tag(project):
        return "start a New version (2) before changing the pack."
    if changelog_data is None or changelog.is_empty(changelog_data):
        return "Draft changelog (3), then edit it."
    last = release._last_build(project)
    if last and last.get("version") == project.version:
        current, reason = release.build_is_current(project, last)
        return "Publish (5)." if current else f"Build release (4) again. {reason.split('.')[0]}."
    return "Build release (4) when the pack is ready."


def show_status(project):
    """The header above the menu. Problems are shown in it instead of stopping the tool."""
    try:
        tag = release.release_tag(project)
        state = f"released, tag {tag}" if tag else "not released yet"
        print()
        print(f"{ui.bold(project.name)} {project.version} ({state}) · Minecraft {project.minecraft} · "
              f"{project.loader_label} {project.loader_version}")
        try:
            changes, base = release.changes_since_release(project, details=False)
            if base:
                print(f"Since {base}: {changes.summary()}")
            elif project.git.is_repo:
                print("No earlier release tag found.")
        except ToolError as ex:
            print(f"Changes: {ex}")
        path = changelog.changelog_path(project)
        data = changelog.load_changelog(path) if path.exists() else None
        if data is None:
            print(f"Changelog: {path.name} doesn't exist yet")
        else:
            overview = len(changelog.section_lines(data.get("Update overview")))
            config = len(changelog.section_lines(data.get("Config Changes")))
            print(f"Changelog: {path.name} · overview {_lines(overview)} · config changes {_lines(config)}")
        print(ui.bold("Next: ") + next_step(project, data))
    except ToolError as ex:
        ui.error(str(ex))
    except Exception as ex:  # A bug shouldn't lock you out of the menu.
        ui.error(f"Couldn't show the pack status ({type(ex).__name__}: {ex}).")


############################################################
# Projects

def activate(config, root):
    try:
        project, notes = projects.open_project(root, config)
    except ToolError:
        raise
    except Exception as ex:  # Anything unexpected becomes a message, not a crash at startup.
        raise ToolError(f"Couldn't open {root}: {type(ex).__name__}: {ex}") from ex
    for note in notes:
        ui.info(note)
    projects.remember_project(config, project.root)
    if project.git.is_repo and not project.git.fetch_tags():
        ui.warn("Couldn't fetch tags from GitHub; release information may be out of date.")
    return project


def pick_project(config, current=None):
    """Choose, add or remove projects; returns the chosen project (or ``current``)."""
    while True:
        ui.title("Projects")
        for number, root in enumerate(config.projects, 1):
            marker = " (open)" if current and projects._same_path(root, current.root) else ""
            print(f"  {number}  {root}{marker}")
        print("  a  Add a project    r  Remove one from this list    Enter  Back")
        choice = input("Choose: ").strip().lower()
        if not choice:
            return current
        if choice == "a":
            root = ui.clean_path(ui.ask("Modpack folder (the one containing 'Packwiz'; drag & drop works)"))
            if root:
                try:
                    return activate(config, root)
                except ToolError as ex:
                    ui.error(str(ex))
        elif choice == "r":
            number = ui.ask("Number to remove from the list (files stay untouched)")
            if number.isdigit() and 1 <= int(number) <= len(config.projects):
                projects.forget_project(config, config.projects[int(number) - 1])
        elif choice.isdigit() and 1 <= int(choice) <= len(config.projects):
            try:
                return activate(config, config.projects[int(choice) - 1])
            except ToolError as ex:
                ui.error(str(ex))


############################################################
# Running commands

def run_command(command, project, args):
    """Run one command; returns True when it finished without an error."""
    try:
        command.run(project, args)
        return True
    except ToolError as ex:
        ui.error(str(ex))
    except (KeyboardInterrupt, EOFError):
        print()
        ui.warn("Cancelled.")
    except Exception:  # A bug: show it, but keep the tool running.
        traceback.print_exc()
        ui.error("Unexpected error (the traceback above is a bug in the tool).")
    finally:
        try:
            project.reload()
        except ToolError:
            pass
    return False


def menu(config, project):
    while True:
        show_status(project)
        print()
        for command in COMMANDS:
            print(f"  {command.key}  {command.label:<18} {ui.dim(command.summary)}")
        print(f"  h  Help    p  Switch project    q  Quit")
        choice = input("Choose: ").strip().lower()
        if choice in ("q", "quit", "exit", "0"):
            return
        if choice == "h":
            print()
            print(HELP)
            input("\nPress Enter to return to the menu...")
            continue
        if choice == "p":
            project = pick_project(config, project) or project
            continue
        command = next((c for c in COMMANDS if choice in (c.key, c.name)), None)
        if command is None:
            continue
        run_command(command, project, argparse.Namespace())
        input("\nPress Enter to return to the menu...")


def build_parser():
    parser = argparse.ArgumentParser(prog="modpack_tool", description=__doc__.splitlines()[0],
                                     epilog="Without a command, the interactive menu opens.")
    parser.add_argument("--project", metavar="PATH", help="the modpack folder (default: the last one used)")
    commands = parser.add_subparsers(dest="command", metavar="command")
    commands.add_parser("status", help="show where the pack stands")
    for command in COMMANDS:
        sub = commands.add_parser(command.name, help=command.summary)
        for flags, options in command.arguments:
            sub.add_argument(*flags, **options)
    return parser


def main(argv=None):
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    config = projects.load_tool_config()
    key = projects.curseforge_key(config)
    if key:
        platforms.set_curseforge_key(key)

    project = None
    root = args.project or config.last_used_project
    if root:
        try:
            project = activate(config, root)
        except ToolError as ex:
            ui.error(str(ex))

    if args.command:
        if project is None:
            ui.error("No modpack project; pass --project PATH.")
            return 2
        if args.command == "status":
            show_status(project)
            return 0
        command = next(c for c in COMMANDS if c.name == args.command)
        return 0 if run_command(command, project, args) else 1

    print(ui.bold("HaXr's Modpack Tool") + ui.dim("   h = help"))
    if project is None:
        project = pick_project(config)
        if project is None:
            return 0
    try:
        menu(config, project)
    except (KeyboardInterrupt, EOFError):
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

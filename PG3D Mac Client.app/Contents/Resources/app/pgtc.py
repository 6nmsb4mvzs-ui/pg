#!/usr/bin/env python3
"""macOS port of PGTraceCleaner. GPL-3.0; based on PGTC by .lurk_r."""
import argparse
import datetime
import fnmatch
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlparse

TOKENS = (
    'ä acc afd anal auth clan cloud contine count crash data decor event face key '
    'histor ids indx inhouse item kill last lead level limit live lvi mail map mark '
    'new notif offs old pain permi perso player sched saved send session skin shop '
    'slot start stat steam terc time unlocked user version'
).split()


def matches(name):
    name = name.lower()
    return ('ae' in name or '\x84' in name or any(t in name for t in TOKENS)
            or re.search(r'\d+[._-]', name) is not None)


def game_name(name):
    name = name.lower()
    return 'pixel' in name and 'gun' in name


def clean_path(value):
    """Refuse symlinks in target paths, including intermediate components."""
    path = Path(os.path.abspath(os.path.expanduser(str(value))))
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Symlink in target path: ' + str(path))
    return path


def game_dirs(base):
    """Find game leaves, never the publisher parent or unrelated siblings."""
    if not base.is_dir() or base.is_symlink():
        return []
    found = []
    for parent in sorted(base.iterdir()):
        if parent.is_symlink() or not parent.is_dir() or not game_name(parent.name):
            continue
        children = [p for p in parent.iterdir()
                    if not p.is_symlink() and p.is_dir() and game_name(p.name)]
        if children:
            found.extend(children)
        elif game_name(parent.name) and parent.name.lower() != 'pixel gun team':
            found.append(parent)
    return found


def account_ids(root):
    found = set()
    queue = [(root, 0)]
    while queue:
        current, depth = queue.pop()
        for child in current.iterdir():
            if child.is_symlink() or not child.is_dir():
                continue
            if child.name.startswith('user'):
                match = re.fullmatch(r'user.*?(\d+)', child.name)
                if match:
                    found.add(match[1])
            elif depth < 3:
                queue.append((child, depth + 1))
    return sorted(found)


def wine_unescape(value):
    # Wine .reg strings escape backslashes and quotes. Preserve Unicode as-is.
    return re.sub(r'\\([\\"])', r'\1', value)


def filter_registry(raw):
    """Remove complete matching value records only inside the original PG keys."""
    text = raw.decode('utf-8')
    if not text.startswith('WINE REGISTRY Version 2'):
        raise ValueError('Expected a Wine user.reg (WINE REGISTRY Version 2)')
    lines = text.splitlines(keepends=True)
    kept, removed = [], []
    active = False
    i = 0
    while i < len(lines):
        line = lines[i]
        section = re.match(r'^\[(.*)\](?:\s.*)?$', line.rstrip('\r\n'))
        if section:
            parts = wine_unescape(section[1]).split('\\')
            active = (len(parts) == 3 and parts[0].lower() == 'software'
                      and parts[1].lower() == 'pixel gun team'
                      and (fnmatch.fnmatchcase(parts[2].lower(), 'pixel*gun*')
                           or fnmatch.fnmatchcase(parts[2].lower(), 'pg*')))
        value = re.match(r'^"((?:\\.|[^"\\])*)"=', line)
        if active and value and matches(wine_unescape(value[1])):
            removed.append(wine_unescape(value[1]))
            # Hex values can span lines; deleting only the first corrupts the file.
            while lines[i].rstrip('\r\n').endswith('\\'):
                i += 1
                if i >= len(lines) or not lines[i].startswith((' ', '\t')):
                    raise ValueError('Malformed registry continuation; refusing to edit')
            i += 1
            continue
        kept.append(line)
        i += 1
    return ''.join(kept).encode('utf-8'), removed


def defaults(*args):
    result = subprocess.run(['/usr/bin/defaults', *map(str, args)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace').strip())
    return result.stdout


def running_processes(wine):
    result = subprocess.run(['/bin/ps', '-axo', 'pid=,comm='],
                            check=True, stdout=subprocess.PIPE, text=True)
    found = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or int(parts[0]) == os.getpid():
            continue
        name = Path(parts[1]).name.lower()
        if game_name(name) or (wine and ('wine' in name or name.endswith('.exe'))):
            found.append(line.strip())
    return found


def plan(args):
    dirs, registry, prefs = [], None, []
    if args.wine_prefix:
        prefix = clean_path(args.wine_prefix)
        if not (prefix / 'drive_c').is_dir():
            raise ValueError('Wine prefix must contain drive_c and user.reg')
        reg = clean_path(prefix / 'user.reg')
        raw = reg.read_bytes()
        filtered, keys = filter_registry(raw)
        if keys:
            registry = (reg, raw, filtered, keys)
        users = prefix / 'drive_c/users'
        if users.is_dir():
            for user in sorted(users.iterdir()):
                if user.is_symlink() or not user.is_dir():
                    continue
                dirs.extend(game_dirs(user / 'AppData/LocalLow'))
                temp = user / 'AppData/Local/Temp'
                if temp.is_dir() and not temp.is_symlink():
                    dirs.extend(p for p in temp.iterdir()
                                if p.is_dir() and not p.is_symlink() and game_name(p.name))
    elif not args.data_dir and not args.preferences:
        library = Path.home() / 'Library'
        for location in ('Application Support', 'Caches', 'Logs'):
            dirs.extend(game_dirs(library / location))
        temp = Path(tempfile.gettempdir()).resolve()
        dirs.extend(p for p in temp.iterdir()
                    if p.is_dir() and not p.is_symlink() and game_name(p.name))
        preferences = library / 'Preferences'
        if preferences.is_dir():
            prefs.extend(p for p in preferences.glob('*.plist')
                         if game_name(p.name) and not p.is_symlink())
    dirs.extend(Path(p).expanduser() for p in args.data_dir)
    prefs.extend(Path(p).expanduser() for p in args.preferences)
    dirs = sorted(set(clean_path(p) for p in dirs))
    for path in dirs:
        if not path.is_dir() or not game_name(path.name):
            raise ValueError('Data directory must exist and contain "pixel" and "gun" in its name: ' + str(path))
        if path.name.lower() == 'pixel gun team' or path == Path.home():
            raise ValueError('Select the game directory, not its publisher parent: ' + str(path))
    dirs = [p for p in dirs if not any(parent in dirs for parent in p.parents)]
    pref_plans = []
    for path in sorted(set(clean_path(p) for p in prefs)):
        if not path.is_file() or path.suffix != '.plist' or not game_name(path.name):
            raise ValueError('Select a Pixel Gun .plist file: ' + str(path))
        if any(path.is_relative_to(d) for d in dirs):
            continue
        domain = str(path.with_suffix(''))
        # export can return an empty dictionary for an inaccessible domain.
        defaults('read', domain)
        raw = defaults('export', domain, '-')
        values = plistlib.loads(raw)
        if not isinstance(values, dict):
            raise ValueError('Preferences must be a dictionary')
        keys = sorted(k for k in values if matches(k))
        if keys:
            pref_plans.append((path, raw, keys))
    return dirs, registry, pref_plans


def atomic_write(path, data):
    fd, temp = tempfile.mkstemp(prefix='.pgtc-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        shutil.copystat(path, temp)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def discover_prefixes(home=None):
    """Inspect standard bottle locations and recorded custom locations only."""
    home = Path.home() if home is None else Path(home)
    roots = [home / 'Library/Application Support/CrossOver/Bottles',
             home / 'Library/Containers/com.isaacmarovitz.Whisky/Bottles']
    candidates = [home / '.wine']
    if os.environ.get('WINEPREFIX'):
        candidates.append(Path(os.environ['WINEPREFIX']).expanduser())
    if os.environ.get('CX_BOTTLE_PATH'):
        roots.extend(Path(p).expanduser() for p in os.environ['CX_BOTTLE_PATH'].split(':') if p)
    config = home / 'Library/Preferences/com.codeweavers.CrossOver.plist'
    if config.is_file():
        value = plistlib.loads(config.read_bytes()).get('BottleDir')
        if isinstance(value, str) and value:
            roots.append(Path(value).expanduser())
    # Whisky keeps custom bottle URLs in BottleVM.plist. Ignore non-path values.
    config = home / 'Library/Containers/com.isaacmarovitz.Whisky/BottleVM.plist'
    if config.is_file():
        pending = [plistlib.loads(config.read_bytes())]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
            elif isinstance(value, str):
                if value.startswith('file://'):
                    url = urlparse(value)
                    if url.netloc in ('', 'localhost'):
                        candidates.append(Path(unquote(url.path)))
                elif value.startswith('/'):
                    candidates.append(Path(value))
    for root in roots:
        if root.is_dir() and not root.is_symlink():
            candidates.extend(p for p in root.iterdir() if p.is_dir() and not p.is_symlink())
    found = set()
    for path in candidates:
        if not path.is_absolute() or path.is_symlink():
            continue
        if (path / 'drive_c').is_dir() and (path / 'user.reg').is_file():
            found.add(clean_path(path))
    return sorted(found)


def automatic_plan():
    native = argparse.Namespace(wine_prefix=None, data_dir=[], preferences=[])
    dirs, _, prefs = plan(native)
    registries = []
    for prefix in discover_prefixes():
        args = argparse.Namespace(wine_prefix=str(prefix), data_dir=[], preferences=[])
        bottle_dirs, registry, _ = plan(args)
        if bottle_dirs or registry:
            print('Detected bottle: ' + str(prefix))
            dirs.extend(bottle_dirs)
            if registry:
                registries.append(registry)
    dirs = sorted(set(dirs))
    dirs = [p for p in dirs if not any(parent in dirs for parent in p.parents)]
    prefs = [p for p in prefs if not any(p[0].is_relative_to(d) for d in dirs)]
    return dirs, registries, prefs


def apply_plan(dirs, registry, prefs, backup_root):
    registries = registry if isinstance(registry, list) else ([registry] if registry else [])
    backup_root = Path(backup_root).expanduser().resolve()
    for path in dirs:
        if backup_root == path or backup_root.is_relative_to(path):
            raise ValueError('Backups must be outside directories being removed')
    backup_root.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix=datetime.datetime.now().strftime('%Y%m%d-%H%M%S-'),
                                   dir=backup_root))
    manifest = {'directories': [], 'registry': None, 'registries': [], 'preferences': []}
    print('Backup: ' + str(backup))
    # Finish every backup before changing any source.
    for index, path in enumerate(dirs):
        clean_path(path)
        target = backup / ('data-' + str(index))
        shutil.copytree(path, target, symlinks=True)
        manifest['directories'].append({'original': str(path), 'backup': target.name})
    for index, (path, raw, filtered, keys) in enumerate(registries):
        if clean_path(path).read_bytes() != raw:
            raise RuntimeError('Registry changed after preview; no cleanup performed')
        name = 'user.reg' if len(registries) == 1 else 'user-' + str(index) + '.reg'
        (backup / name).write_bytes(raw)
        entry = {'original': str(path), 'backup': name}
        manifest['registries'].append(entry)
        if len(registries) == 1:
            manifest['registry'] = entry
    for index, (path, raw, keys) in enumerate(prefs):
        clean_path(path)
        if plistlib.loads(defaults('export', str(path.with_suffix('')), '-')) != plistlib.loads(raw):
            raise RuntimeError('Preferences changed after preview; no cleanup performed')
        target = backup / ('preferences-' + str(index) + '.plist')
        target.write_bytes(raw)
        manifest['preferences'].append({'original': str(path), 'backup': target.name})
    (backup / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    # Recheck after potentially slow backups, before making any changes.
    for path, raw, filtered, keys in registries:
        if clean_path(path).read_bytes() != raw:
            raise RuntimeError('Registry changed during backup; no cleanup performed')
    for path, raw, filtered, keys in registries:
        atomic_write(path, filtered)
    for path, raw, keys in prefs:
        domain = str(path.with_suffix(''))
        for key in keys:
            defaults('delete', domain, key)
    for path in dirs:
        clean_path(path)
        shutil.rmtree(path)
    return backup


def main(argv=None):
    parser = argparse.ArgumentParser(description='PGTraceCleaner for macOS. Preview by default; backups before cleanup.')
    parser.add_argument('--wine-prefix', help='CrossOver/Wine/Whisky bottle containing drive_c and user.reg')
    parser.add_argument('--data-dir', action='append', default=[], help='Explicit Pixel Gun game data directory (repeatable)')
    parser.add_argument('--preferences', action='append', default=[], help='Explicit native Pixel Gun .plist (repeatable)')
    parser.add_argument('--apply', action='store_true', help='Perform the displayed cleanup after making backups')
    parser.add_argument('--auto', action='store_true', help='Discover native data and installed Wine/CrossOver/Whisky bottles')
    parser.add_argument('--backup-dir', default=str(Path.home() / 'Library/Application Support/PGTraceCleaner/Backups'))
    args = parser.parse_args(argv)
    try:
        if sys.platform != 'darwin':
            raise ValueError('Run this port on macOS')
        if os.geteuid() == 0:
            raise ValueError('Run as your normal user, without sudo')
        if args.wine_prefix and args.preferences:
            raise ValueError('Use --preferences for native mode, separately from --wine-prefix')
        if args.auto and (args.wine_prefix or args.data_dir or args.preferences):
            raise ValueError('Use --auto alone, without manual target paths')
        # Automatic cleanup checks first, before reading potentially live registry data.
        if args.auto and args.apply:
            processes = running_processes(True)
            if processes:
                raise RuntimeError('Close Pixel Gun and shut down Wine/CrossOver/Whisky sessions, then retry:\n' + '\n'.join(processes))
        if args.auto:
            print('Automatically finding Pixel Gun data…')
            dirs, registry, prefs = automatic_plan()
        else:
            dirs, registry, prefs = plan(args)
        print('PGTraceCleaner — ' + ('APPLY' if args.apply else 'PREVIEW (no changes)'))
        print('Matching local account data and settings may be removed. This cannot unban an account.')
        for path in dirs:
            print('Remove directory: ' + str(path))
            ids = account_ids(path)
            if ids:
                print('  Account IDs: ' + ', '.join(ids))
        registries = registry if isinstance(registry, list) else ([registry] if registry else [])
        for item in registries:
            print('Registry: ' + str(item[0]))
            for key in item[3]:
                print('  Remove value: ' + repr(key))
        for path, raw, keys in prefs:
            print('Preferences: ' + str(path))
            for key in keys:
                print('  Remove key: ' + repr(key))
        if not dirs and not registry and not prefs:
            print('No matching data found. For Wine/CrossOver, specify --wine-prefix. For native sandboxes, use explicit paths.')
            return 0
        if not args.apply:
            print('Preview complete. Re-run with --apply to back up and perform this cleanup.')
            return 0
        processes = running_processes(args.auto or bool(args.wine_prefix))
        if processes:
            raise RuntimeError('Close Pixel Gun and shut down Wine/CrossOver/Whisky sessions first:\n' + '\n'.join(processes))
        apply_plan(dirs, registry, prefs, args.backup_dir)
        print('Cleanup completed. Backups and original paths are recorded in manifest.json.')
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, plistlib.InvalidFileException) as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

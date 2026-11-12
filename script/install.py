#!/usr/bin/env python3
"""Install, update, restore or remove the plugin in IsiX DICOM Viewer's plugins folder.

    script/install.py install   [BUNDLE] [--configuration Debug|Release] [--home DIR | --system]
    script/install.py restore   [NAME] [--home DIR | --system]
    script/install.py uninstall [NAME] [--identifier ID] [--home DIR | --system]
    script/install.py where     [--home DIR | --system]

The folder is ~/Library/Application Support/IsiX/Plugins/ for the current
user: no administrator password is needed, and nothing else is written.
--system chooses /Library/Application Support/IsiX/Plugins/, for all users of
the Mac, only when asked and only if you may already write there: the script
never asks for administrator rights. --home DIR puts the user folder under DIR
instead of the home folder, for a disposable test home or the isolated folder
of the development application.

install  checks the bundle as the application will (API version, identifier,
         entry class, executable, IsiXPluginKit linked and not embedded,
         signature intact), copies it next to the destination, checks the
         copy, and only then puts it in place in one rename. It refuses to
         replace a bundle with another identifier, or to install a second copy
         of the same identifier under another name. An existing copy is kept
         as the previous version (in .horos-plugin-previous, where the
         application looks for it if the new version stops it from starting).
         If an install is interrupted, the installed copy is either the old
         one or the new one, whole; the next run of this script cleans up.
restore  puts the previous version back.
uninstall removes the named bundle, and its previous version, only if it is a
         .isixplugin with the expected identifier.

Quit IsiX DICOM Viewer before installing or removing: it loads plugins when
it starts, so it must be started again to run a new version. The script
refuses to replace or remove a copy that a running process has loaded.
"""
from pathlib import Path
import argparse
import ctypes
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_bundle  # noqa: E402

PROJECT = Path(__file__).resolve().parents[1]
RELATIVE_FOLDER = Path('Library') / 'Application Support' / 'IsiX' / 'Plugins'
STAGING_PREFIX = '.isix-plugin-install-'
# The application's own installer keeps the previous version here, and its
# crash recovery offers to put it back from here.
PREVIOUS_FOLDER = '.horos-plugin-previous'
EXTENSION = '.isixplugin'

RENAME_SWAP = 0x2
RENAME_EXCL = 0x4


class InstallError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


# MARK: locations

def plugins_folder(home=None, system=False):
    if system:
        return Path('/') / RELATIVE_FOLDER
    return Path(home).expanduser().resolve() / RELATIVE_FOLDER if home else Path.home() / RELATIVE_FOLDER


def check_folder_is_own(folder, domain):
    """Refuses a plugins folder that is, or is under, a symbolic link: it could lead to another application's folder."""
    path = domain
    for part in folder.relative_to(domain).parts:
        path = path / part
        if path.is_symlink():
            raise InstallError(f'{path} is a symbolic link; the script writes only into a real IsiX plugins folder.')


def domain_of(home, system):
    if system:
        return Path('/')
    return Path(home).expanduser().resolve() if home else Path.home()


def build_setting(name):
    for line in (PROJECT / 'Config' / 'MyIsiXPlugin.xcconfig').read_text().splitlines():
        key, _, value = line.partition('=')
        if key.strip() == name:
            return value.strip()
    return None


def bundle_name(name):
    name = name or build_setting('PRODUCT_NAME')
    return name if name.endswith(EXTENSION) else name + EXTENSION


# MARK: bundles

def read_info(bundle):
    try:
        return plistlib.loads((bundle / 'Contents' / 'Info.plist').read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None


def fingerprint(bundle):
    """A digest of every file and link in the bundle, by relative path."""
    digest = hashlib.sha256()
    for path in sorted(Path(bundle).rglob('*')):
        relative = path.relative_to(bundle).as_posix().encode()
        if path.is_symlink():
            digest.update(b'L' + relative + b'\0' + os.readlink(path).encode() + b'\0')
        elif path.is_file():
            digest.update(b'F' + relative + b'\0' + hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def check_bundle(bundle, what):
    facts, problems, warnings = verify_bundle.inspect(bundle)
    if problems:
        raise InstallError(f'{what} would be refused by the application:\n  ' + '\n  '.join(problems), 3)
    if not facts.get('signed'):
        raise InstallError(f'{what} is not signed; build it with script/build.sh or sign it with codesign.', 3)
    return facts, warnings


def processes_using(bundle):
    """The processes that have the bundle's executable open or mapped."""
    info = read_info(bundle) or {}
    executable = bundle / 'Contents' / 'MacOS' / str(info.get('CFBundleExecutable', ''))
    if not executable.is_file():
        return []
    result = subprocess.run(['/usr/sbin/lsof', '-t', '--', str(executable)], capture_output=True, text=True)
    return sorted({int(p) for p in result.stdout.split() if p.isdigit() and int(p) != os.getpid()})


def refuse_if_loaded(bundle):
    users = processes_using(bundle)
    if users:
        raise InstallError(f'{bundle} is in use by process {", ".join(map(str, users))}. '
                           'Quit IsiX DICOM Viewer, then run this again.', 5)


def rename(source, destination, flags):
    libc = ctypes.CDLL(None, use_errno=True)
    libc.renamex_np.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
    if libc.renamex_np(os.fsencode(source), os.fsencode(destination), flags) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def interrupt_for_test(point):
    # Tests stop the script at these points, as a crash or a kill would.
    if os.environ.get('ISIX_INSTALL_TEST_INTERRUPT') == point:
        os._exit(9)


def keep_as_previous(old, destination):
    previous = destination.parent / PREVIOUS_FOLDER / destination.name
    previous.parent.mkdir(exist_ok=True)
    if previous.exists():
        shutil.rmtree(previous)
    os.rename(old, previous)
    return previous


def recover(folder):
    """Finishes or undoes installs that were interrupted; returns what it did."""
    done = []
    if not folder.is_dir():
        return done
    for staging in sorted(folder.glob(STAGING_PREFIX + '*')):
        try:
            state = json.loads((staging / 'state.json').read_text())
        except (OSError, ValueError):
            state = {}
        name = state.get('name')
        inner = staging / name if name else None
        if inner and inner.exists() and (folder / name).exists() and fingerprint(inner) != state.get('fingerprint'):
            # The new version was published and the old one is what is left here.
            previous = keep_as_previous(inner, folder / name)
            done.append(f'kept the version replaced by an interrupted install as {previous}')
        else:
            done.append(f'removed an install that was interrupted before it was published ({name or staging.name})')
        shutil.rmtree(staging, ignore_errors=True)
    return done


# MARK: commands

def install(source, folder, domain):
    source = Path(source).resolve()
    if source.suffix != EXTENSION or not source.is_dir():
        raise InstallError(f'{source} is not a {EXTENSION} bundle. Build it first with script/build.sh.', 2)
    facts, warnings = check_bundle(source, source.name)
    identifier = facts['identifier']
    machine = os.uname().machine
    if machine not in facts.get('architectures', []):
        warnings.append(f'the bundle has no {machine} slice: it loads only in an IsiX DICOM Viewer running as '
                        f'{", ".join(facts.get("architectures", []))}')
    folder.mkdir(parents=True, exist_ok=True)
    check_folder_is_own(folder, domain)
    for line in recover(folder):
        print(line)
    destination = folder / source.name

    for other in sorted(folder.glob('*' + EXTENSION)):
        info = read_info(other)
        if other.name != source.name and info and info.get('CFBundleIdentifier') == identifier:
            raise InstallError(f'{identifier} is already installed as {other.name}. Uninstall that copy first: '
                               f'script/install.py uninstall "{other.stem}"', 4)
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or not destination.is_dir():
            raise InstallError(f'{destination} exists and is not a plugin bundle; move it away first.', 4)
        installed = read_info(destination)
        if not installed or installed.get('CFBundleIdentifier') != identifier:
            other = installed.get('CFBundleIdentifier') if installed else 'no readable identifier'
            raise InstallError(f'{destination.name} in {folder} belongs to another plugin ({other}), not {identifier}. '
                               'It is left as it is; give your plugin another name or uninstall that one first.', 4)
        refuse_if_loaded(destination)

    staging = folder / f'{STAGING_PREFIX}{uuid.uuid4().hex}'
    staging.mkdir()
    candidate = staging / source.name
    expected = fingerprint(source)
    (staging / 'state.json').write_text(json.dumps({'name': source.name, 'identifier': identifier, 'fingerprint': expected}))
    published = False
    try:
        subprocess.run(['/usr/bin/ditto', str(source), str(candidate)], check=True)
        if fingerprint(candidate) != expected:
            raise InstallError('the copy differs from the bundle; nothing was installed.')
        check_bundle(candidate, 'the copy')
        interrupt_for_test('after-copy')
        rename(candidate, destination, RENAME_SWAP if destination.exists() else RENAME_EXCL)
        published = True
        interrupt_for_test('after-swap')
        previous = keep_as_previous(candidate, destination) if candidate.exists() else None
    finally:
        if not published or not candidate.exists():
            shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(staging, ignore_errors=True)
    for warning in warnings:
        print(f'warning: {warning}', file=sys.stderr)
    print(f'Installed {identifier} {facts.get("version")} as {destination}')
    if previous:
        print(f'The version it replaced is kept as {previous}; script/install.py restore puts it back.')
    print('Start IsiX DICOM Viewer again to load it.')


def restore(name, folder, domain):
    destination = folder / bundle_name(name)
    previous = folder / PREVIOUS_FOLDER / destination.name
    check_folder_is_own(folder, domain)
    if not previous.is_dir() or not destination.is_dir():
        raise InstallError(f'There is no previous version of {destination.name} in {folder}.', 2)
    old, new = read_info(previous) or {}, read_info(destination) or {}
    if old.get('CFBundleIdentifier') != new.get('CFBundleIdentifier'):
        raise InstallError('The previous version has another identifier; nothing was changed.', 4)
    refuse_if_loaded(destination)
    rename(previous, destination, RENAME_SWAP)
    shutil.rmtree(previous)
    if not any(previous.parent.iterdir()):
        previous.parent.rmdir()
    print(f'Restored {destination.name} {old.get("CFBundleShortVersionString")} in {folder}. Start IsiX DICOM Viewer again to load it.')


def uninstall(name, identifier, folder, domain):
    target = folder / bundle_name(name)
    identifier = identifier or build_setting('PRODUCT_BUNDLE_IDENTIFIER')
    check_folder_is_own(folder, domain)
    if not target.exists():
        raise InstallError(f'{target.name} is not installed in {folder}.', 2)
    if target.is_symlink() or not target.is_dir():
        raise InstallError(f'{target} is not a plugin bundle; nothing was removed.', 4)
    info = read_info(target)
    if not info or info.get('CFBundleIdentifier') != identifier:
        found = info.get('CFBundleIdentifier') if info else 'no readable identifier'
        raise InstallError(f'{target.name} has the identifier {found}, not {identifier}; nothing was removed. '
                           'Pass --identifier to remove another plugin.', 4)
    refuse_if_loaded(target)
    shutil.rmtree(target)
    previous = folder / PREVIOUS_FOLDER / target.name
    if previous.exists():
        shutil.rmtree(previous)
        if not any(previous.parent.iterdir()):
            previous.parent.rmdir()
    print(f'Removed {target.name} ({identifier}) from {folder}.')


def writable(folder):
    """Whether the folder, or the nearest folder above it that exists, may be written to."""
    path = folder
    while not path.exists():
        path = path.parent
    return os.access(path, os.W_OK)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    parsers = {}
    for command in ('install', 'restore', 'uninstall', 'where'):
        sub = commands.add_parser(command)
        where = sub.add_mutually_exclusive_group()
        where.add_argument('--home', type=Path, help='install for the user whose home folder is DIR')
        where.add_argument('--system', action='store_true', help='install for all users, in /Library')
        parsers[command] = sub
    parsers['install'].add_argument('bundle', nargs='?', type=Path)
    parsers['install'].add_argument('--configuration', default='Release', choices=('Debug', 'Release'),
                                    help='with no BUNDLE, install build/Products/<configuration>/<PRODUCT_NAME>.isixplugin')
    parsers['restore'].add_argument('name', nargs='?')
    parsers['uninstall'].add_argument('name', nargs='?')
    parsers['uninstall'].add_argument('--identifier', help='the identifier the bundle must have; by default PRODUCT_BUNDLE_IDENTIFIER')
    arguments = parser.parse_args()
    folder = plugins_folder(arguments.home, arguments.system)
    domain = domain_of(arguments.home, arguments.system)
    try:
        if arguments.command == 'where':
            print(folder)
        elif arguments.command == 'install':
            bundle = arguments.bundle or PROJECT / 'build' / 'Products' / arguments.configuration / bundle_name(None)
            if arguments.system and not writable(folder):
                raise InstallError(f'You may not write to {folder}. Install for your user (without --system), or '
                                   'create that folder and give yourself write access to it first; this script does not ask for administrator rights.', 6)
            install(bundle, folder, domain)
        elif arguments.command == 'restore':
            restore(arguments.name, folder, domain)
        else:
            uninstall(arguments.name, arguments.identifier, folder, domain)
    except InstallError as error:
        print(f'error: {error}', file=sys.stderr)
        return error.code
    except OSError as error:
        print(f'error: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

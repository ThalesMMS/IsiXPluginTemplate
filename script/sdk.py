#!/usr/bin/env python3
"""Check, or put in place, the IsiXPluginKit SDK this project builds against.

    script/sdk.py check [--sdk DIR] [--archs "arm64 x86_64"] [--record FILE] [--require-porting]
    script/sdk.py export --app-source DIR [--configuration Release] [--sdk DIR]

The SDK is a folder with IsiXPluginKit.framework (the binary to link and one
.swiftinterface per architecture) and SDK.json (API version, kit version,
source revision, architectures, compiler, SHA-256 of the binary and of each
interface). A release of IsiX DICOM Viewer publishes it as
IsiXPluginKit-SDK-<version>.zip; it is made from the application source with
its tools/export-isix-plugin-kit.py, and `export` runs that script for you.
The folder is ISIX_SDK_DIR, by default SDK/ in this project.

`export` also copies LegacyPorting/: the porting kit IsiXLegacyBridge and the
scripts that convert and build a Horos or OsiriX plugin's source with it
(tools/port-legacy-plugin.py and tools/build-isix-legacy-plugin.py), from the
same source revision, with PORTING.json listing the SHA-256 of each file.
`check` verifies those files against PORTING.json when the folder is there;
--require-porting makes its absence an error too.

`check` fails, with the reason, unless the folder holds an exported SDK for the
plugin API version this project declares in Config/Info.plist, with the
requested architectures and with the files SDK.json records the SHA-256 of
unchanged, and prints its identity with the SHA-256 of the binary. The build runs it first; with --record it also writes that identity
into the plugin's resources, so a built plugin says which SDK it was built
against. Nothing here is copied into the plugin besides that record.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[1]
NAME = 'IsiXPluginKit'
INSTALL_NAME = f'@rpath/{NAME}.framework/Versions/A/{NAME}'

# The porting kit for Horos and OsiriX plugins, and the scripts that use it,
# copied from the application source into <SDK>/LegacyPorting with the same
# layout: the converter finds the kit at ../IsiXLegacyBridge from its own
# folder, and the kit's README links ../IsiXPluginKit/README.md. The
# application's LICENSE goes with them: part of the kit derives from Horos and
# OsiriX, and that file carries their notice.
PORTING = 'LegacyPorting'
PORTING_MANIFEST = 'PORTING.json'
PORTING_SOURCES = ('LICENSE', 'IsiXLegacyBridge', 'IsiXPluginKit/README.md', 'tools/port-legacy-plugin.py',
                   'tools/build-isix-legacy-plugin.py', 'tools/inventory-plugin-api.py')
IGNORED = ('.DS_Store', '__pycache__')


class SDKError(Exception):
    pass


def default_sdk():
    return Path(os.environ.get('ISIX_SDK_DIR') or PROJECT / 'SDK')


def declared_api_version():
    info = plistlib.loads((PROJECT / 'Config' / 'Info.plist').read_bytes())
    return info['IsiXPluginAPIVersion']


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def tool(*command):
    result = subprocess.run(['xcrun', *command], capture_output=True, text=True)
    if result.returncode:
        raise SDKError(f'{" ".join(command)} failed: {result.stderr.strip()}')
    return result.stdout


def check(sdk, archs=()):
    """The SDK's identity, or SDKError with what is wrong."""
    sdk = Path(sdk)
    description_path = sdk / 'SDK.json'
    framework = sdk / f'{NAME}.framework'
    binary = framework / 'Versions' / 'A' / NAME
    hint = ('Unpack the IsiXPluginKit-SDK-<version>.zip of an IsiX DICOM Viewer release there, export one from '
            'the application source with script/sdk.py export --app-source <folder>, or set ISIX_SDK_DIR to a '
            'folder that has one.')
    if not description_path.is_file() or not binary.is_file():
        raise SDKError(f'No IsiXPluginKit SDK in {sdk}. {hint}')
    try:
        description = json.loads(description_path.read_text())
    except ValueError as error:
        raise SDKError(f'{description_path} is not valid JSON: {error}')
    if description.get('name') != NAME:
        raise SDKError(f'{description_path} does not describe {NAME}.')
    wanted = declared_api_version()
    if description.get('apiVersion') != wanted:
        raise SDKError(f'The SDK is for plugin API version {description.get("apiVersion")}; '
                       f'Config/Info.plist declares version {wanted}.')
    install_name = tool('otool', '-D', str(binary)).splitlines()[-1].strip()
    if install_name != INSTALL_NAME or description.get('installName') != INSTALL_NAME:
        raise SDKError(f'The framework\'s install name is {install_name}, not {INSTALL_NAME}.')
    present = tool('lipo', '-archs', str(binary)).split()
    modules = framework / 'Versions' / 'A' / 'Modules' / f'{NAME}.swiftmodule'
    for arch in archs:
        if arch not in present:
            raise SDKError(f'The SDK has no {arch} slice (it has {", ".join(present)}); export it with that architecture.')
        if not (modules / f'{arch}-apple-macos.swiftinterface').is_file():
            raise SDKError(f'The SDK has no {arch} module interface.')
    private = sorted(p.name for p in modules.glob('*.private.swiftinterface'))
    if private:
        raise SDKError(f'{framework} carries the application\'s private interface ({", ".join(private)}): '
                       'it is the application\'s copy, not an exported SDK.')
    identity = dict(description)
    identity['binarySHA256'] = sha256(binary)
    identity['interfaceSHA256'] = {p.name: sha256(p) for p in sorted(modules.glob('*.swiftinterface'))}
    # SDK.json records the SHA-256 of the binary and of each interface it was
    # exported with: a file that no longer matches is not that SDK any more.
    changed = []
    if description.get('binarySHA256') not in (None, identity['binarySHA256']):
        changed.append(binary.name)
    recorded = description.get('interfaceSHA256') or {}
    changed += sorted(name for name in recorded if identity['interfaceSHA256'].get(name) != recorded[name])
    if changed:
        raise SDKError(f'The SDK in {sdk} differs from the SHA-256 recorded in SDK.json: {", ".join(changed)} '
                       'changed or missing. Put the SDK in place again.')
    return identity


def porting_entries(root):
    """{relative path: SHA-256 of a file, or 'symlink:<target>'} under root."""
    entries = {}
    for path in sorted(Path(root).rglob('*')):
        relative = path.relative_to(root)
        if any(part in IGNORED for part in relative.parts):
            continue
        if path.is_symlink():
            entries[relative.as_posix()] = f'symlink:{os.readlink(path)}'
        elif path.is_file():
            entries[relative.as_posix()] = sha256(path)
    return entries


def export_porting(app_source, sdk, revision):
    """Copy the porting kit and its scripts into <sdk>/LegacyPorting, with a manifest."""
    app_source, destination = Path(app_source), Path(sdk) / PORTING
    missing = [s for s in PORTING_SOURCES if not (app_source / s).exists()]
    if missing:
        raise SDKError(f'{app_source} has no {", ".join(missing)}: it is not an IsiX DICOM Viewer source '
                       'with the porting kit.')
    if destination.exists():
        shutil.rmtree(destination)
    for source in PORTING_SOURCES:
        origin, target = app_source / source, destination / source
        target.parent.mkdir(parents=True, exist_ok=True)
        if origin.is_dir():
            shutil.copytree(origin, target, symlinks=True, ignore=shutil.ignore_patterns(*IGNORED))
        else:
            shutil.copy2(origin, target)
    manifest = {'revision': revision, 'files': porting_entries(destination)}
    (destination / PORTING_MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    return manifest


def check_porting(sdk):
    """The porting kit's manifest, or SDKError when a file differs from it. None when absent."""
    folder = Path(sdk) / PORTING
    if not folder.exists():
        return None
    try:
        manifest = json.loads((folder / PORTING_MANIFEST).read_text())
    except (OSError, ValueError) as error:
        raise SDKError(f'{folder / PORTING_MANIFEST} cannot be read: {error}')
    present = porting_entries(folder)
    present.pop(PORTING_MANIFEST, None)
    expected = manifest.get('files', {})
    changed = sorted(p for p in expected if present.get(p) != expected[p])
    extra = sorted(p for p in present if p not in expected)
    if changed or extra:
        raise SDKError(f'The porting kit in {folder} differs from its manifest: '
                       + ', '.join([f'{p} changed or missing' for p in changed] + [f'{p} not listed' for p in extra]))
    return manifest


def command_check(arguments):
    archs = [a for a in arguments.archs.split() if a]
    try:
        identity = check(arguments.sdk, archs)
        porting = check_porting(arguments.sdk)
        if porting is None and getattr(arguments, 'require_porting', False):
            raise SDKError(f'The SDK in {arguments.sdk} has no {PORTING} folder; export it with script/sdk.py export.')
    except SDKError as error:
        print(f'error: {error}', file=sys.stderr)
        return 1
    if arguments.record:
        arguments.record.parent.mkdir(parents=True, exist_ok=True)
        arguments.record.write_text(json.dumps(identity, indent=2, sort_keys=True) + '\n')
    if not arguments.quiet:
        print(f'IsiXPluginKit SDK in {arguments.sdk}: API {identity["apiVersion"]}, kit {identity["kitVersion"]}, '
              f'revision {identity["revision"]}, {identity["configuration"]}, '
              f'{" ".join(identity["architectures"])}, SHA-256 {identity["binarySHA256"]}')
        if porting is not None:
            print(f'Porting kit in {Path(arguments.sdk) / PORTING}: revision {porting["revision"]}, '
                  f'{len(porting["files"])} entries matching {PORTING_MANIFEST}')
    return 0


def command_export(arguments):
    exporter = arguments.app_source / 'tools' / 'export-isix-plugin-kit.py'
    if not exporter.is_file():
        print(f'error: {arguments.app_source} is not an IsiX DICOM Viewer source folder: no {exporter.relative_to(arguments.app_source)}',
              file=sys.stderr)
        return 1
    arguments.sdk.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([sys.executable, str(exporter), '--xcode', arguments.configuration,
                             '--output', str(arguments.sdk), '--archs', 'arm64,x86_64'])
    if result.returncode:
        return result.returncode
    try:
        revision = json.loads((arguments.sdk / 'SDK.json').read_text())['revision']
        export_porting(arguments.app_source, arguments.sdk, revision)
    except (SDKError, OSError, ValueError, KeyError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 1
    arguments.archs, arguments.record, arguments.quiet = 'arm64 x86_64', None, False
    arguments.require_porting = True
    return command_check(arguments)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    checking = commands.add_parser('check', help='check the SDK and print its identity')
    checking.add_argument('--sdk', type=Path, default=default_sdk())
    checking.add_argument('--archs', default='', help='architectures that must be present, separated by spaces')
    checking.add_argument('--record', type=Path, help='also write the identity, as JSON, to this file')
    checking.add_argument('--quiet', action='store_true')
    checking.add_argument('--require-porting', action='store_true',
                          help=f'also fail when the SDK has no {PORTING} folder (the porting kit for Horos and OsiriX plugins)')
    exporting = commands.add_parser('export', help='export the SDK from the application source into the SDK folder')
    exporting.add_argument('--app-source', type=Path, required=True, help='a checkout of the IsiX DICOM Viewer source')
    exporting.add_argument('--configuration', default='Release', choices=('Debug', 'Release'))
    exporting.add_argument('--sdk', type=Path, default=default_sdk())
    arguments = parser.parse_args()
    return command_check(arguments) if arguments.command == 'check' else command_export(arguments)


if __name__ == '__main__':
    sys.exit(main())

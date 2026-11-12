#!/usr/bin/env python3
"""Check a built .isixplugin the way the application checks it before loading it.

    script/verify_bundle.py <bundle.isixplugin> [--archs "arm64 x86_64"] [--json]

Reads the Info.plist and the executable's Mach-O headers, without loading the
plugin, and fails with the reasons when the bundle would be refused: wrong
extension or package type, missing or wrong IsiXPluginAPIVersion, identifier,
entry class or executable, unknown capabilities, an architecture missing, a
minimum system above macOS 15, IsiXPluginKit not linked or carried inside the
bundle, symbols left to be found at run time (-undefined dynamic_lookup),
libraries or keys of the former plugin API, or a broken signature. It also
checks that the entry class named in NSPrincipalClass is defined in the
executable, and warns when the executable exports weak definitions (C++
templates and inline functions), which the application's copies of the same
names would replace. The install script runs the same checks.
"""
from pathlib import Path
import argparse
import json
import plistlib
import re
import subprocess
import sys

API_VERSION = 1
KIT = '@rpath/IsiXPluginKit.framework/Versions/A/IsiXPluginKit'
CAPABILITIES = {'viewer.read', 'viewer.write', 'roi', 'database.read', 'database.import'}
LEGACY_KEYS = {'pluginType', 'MenuTitles', 'ToolbarNames', 'FileFormats', 'HorosCompatiblePlugin', 'Requirements'}
LEGACY_FRAMEWORKS = re.compile(r'/(Horos|HorosAPI|OsiriXAPI|OsiriX Headers|OsiriX|HorosDCM)\.framework/')
MINIMUM_SYSTEM = (15, 0)


def run(*command):
    return subprocess.run(command, capture_output=True, text=True)


def xcrun(*command):
    result = run('xcrun', *command)
    return result.stdout if result.returncode == 0 else ''


def version_tuple(text):
    return tuple(int(part) for part in str(text).split('.')[:2] if part.isdigit()) or (0,)


def inspect(bundle, archs=()):
    """(facts, problems, warnings) for the bundle."""
    bundle = Path(bundle)
    problems, warnings, facts = [], [], {'bundle': str(bundle)}
    if bundle.suffix != '.isixplugin':
        problems.append(f'the extension is "{bundle.suffix}", not ".isixplugin"')
    info_path = bundle / 'Contents' / 'Info.plist'
    try:
        info = plistlib.loads(info_path.read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError) as error:
        return facts, [f'no readable Contents/Info.plist ({error})'], warnings
    legacy = sorted(LEGACY_KEYS & set(info))
    if legacy:
        problems.append(f'keys of the former plugin API: {", ".join(legacy)}')
    if info.get('CFBundlePackageType') != 'BNDL':
        problems.append(f'CFBundlePackageType is {info.get("CFBundlePackageType")!r}, not "BNDL"')
    version = info.get('IsiXPluginAPIVersion')
    facts['apiVersion'] = version
    if version is None:
        problems.append('IsiXPluginAPIVersion is missing')
    elif type(version) is not int:
        problems.append(f'IsiXPluginAPIVersion must be an integer, not {type(version).__name__}')
    elif version != API_VERSION:
        problems.append(f'IsiXPluginAPIVersion is {version}; this SDK is for version {API_VERSION}')
    identifier = info.get('CFBundleIdentifier')
    facts['identifier'] = identifier
    if not isinstance(identifier, str) or not identifier:
        problems.append('CFBundleIdentifier is missing')
    elif identifier.startswith('com.example.'):
        warnings.append(f'the identifier {identifier} is the template\'s; set your own before distributing')
    facts['version'] = info.get('CFBundleShortVersionString') or info.get('CFBundleVersion')
    principal = info.get('NSPrincipalClass')
    facts['principalClass'] = principal
    if not isinstance(principal, str) or not principal:
        problems.append('NSPrincipalClass is missing')
    elif '$(' in principal:
        problems.append(f'NSPrincipalClass {principal} was not expanded by the build')
    capabilities = info.get('IsiXPluginCapabilities', [])
    facts['capabilities'] = capabilities
    if not isinstance(capabilities, list) or not all(isinstance(c, str) for c in capabilities):
        problems.append('IsiXPluginCapabilities must be an array of strings')
    elif set(capabilities) - CAPABILITIES:
        problems.append(f'unknown capabilities: {", ".join(sorted(set(capabilities) - CAPABILITIES))}')
    minimum = info.get('LSMinimumSystemVersion')
    if minimum and version_tuple(minimum) > MINIMUM_SYSTEM:
        warnings.append(f'LSMinimumSystemVersion {minimum} is above macOS 15: older systems refuse the plugin')
    if (bundle / 'Contents' / 'Frameworks' / 'IsiXPluginKit.framework').exists():
        problems.append('the bundle carries its own IsiXPluginKit.framework; link the SDK without embedding it')

    executable_name = info.get('CFBundleExecutable')
    executable = bundle / 'Contents' / 'MacOS' / str(executable_name)
    if not executable_name or not executable.is_file():
        problems.append(f'the executable Contents/MacOS/{executable_name} is missing')
        return facts, problems, warnings
    present = xcrun('lipo', '-archs', str(executable)).split()
    facts['architectures'] = present
    for arch in archs:
        if arch not in present:
            problems.append(f'no {arch} slice (the executable has {", ".join(present) or "none"})')
    for arch in present:
        header = xcrun('otool', '-arch', arch, '-hv', str(executable))
        if 'BUNDLE' not in header and 'DYLIB' not in header:
            problems.append(f'the {arch} executable is not a Mach-O bundle or library')
        libraries = xcrun('otool', '-arch', arch, '-L', str(executable))
        if KIT not in libraries:
            problems.append(f'the {arch} executable does not link {KIT}')
        if LEGACY_FRAMEWORKS.search(libraries):
            problems.append(f'the {arch} executable links a framework of the former plugin API')
        loads = xcrun('otool', '-arch', arch, '-l', str(executable))
        match = re.search(r'cmd LC_BUILD_VERSION.*?minos (\S+)', loads, re.S)
        if match:
            facts.setdefault('minimumSystem', {})[arch] = match.group(1)
            if version_tuple(match.group(1)) > MINIMUM_SYSTEM:
                warnings.append(f'the {arch} executable needs macOS {match.group(1)}')
        dynamic = [line for line in xcrun('nm', '-arch', arch, '-m', '-u', str(executable)).splitlines()
                   if 'dynamically looked up' in line]
        if dynamic:
            problems.append(f'the {arch} executable leaves {len(dynamic)} symbols to be found at run time '
                            f'(-undefined dynamic_lookup), for example {dynamic[0].split()[-1]}')
        weak = [line.split()[-1] for line in xcrun('nm', '-arch', arch, '-m', '-g', '-U', str(executable)).splitlines()
                if 'weak external' in line]
        if weak:
            warnings.append(f'the {arch} executable exports {len(weak)} weak definitions (C++ templates or inline '
                            f'functions), for example {weak[0]}: when the application has its own copy of one, the '
                            'plugin runs the application\'s instead; export no symbol (Config/Exports.exp)')
    if isinstance(principal, str) and principal and '$(' not in principal and present:
        symbols = xcrun('nm', '-arch', present[0], '-U', '-j', str(executable))
        if '.' in principal:
            demangled = subprocess.run(['xcrun', 'swift-demangle'], input=symbols, capture_output=True, text=True).stdout
            lines = set(demangled.splitlines())
            if f'nominal type descriptor for {principal}' not in lines and f'type metadata for {principal}' not in lines:
                problems.append(f'the executable defines no Swift class {principal}')
        elif f'_OBJC_CLASS_$_{principal}' not in symbols.split():
            problems.append(f'NSPrincipalClass {principal} names no class of the executable: a Swift class is named '
                            f'with its module, Module.{principal}, unless it is declared @objc({principal})')
    signature = run('codesign', '--verify', '--strict', '--deep', str(bundle))
    facts['signed'] = signature.returncode == 0
    if signature.returncode and 'not signed at all' not in signature.stderr:
        problems.append(f'the signature is broken: {signature.stderr.strip()}')
    elif signature.returncode and 'arm64' in facts.get('architectures', []):
        problems.append('the bundle is not signed: arm64 code must be signed, at least ad hoc (codesign --sign -), to load')
    elif signature.returncode:
        warnings.append('the bundle is not signed')
    return facts, problems, warnings


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--archs', default='', help='architectures that must be present, separated by spaces')
    parser.add_argument('--json', action='store_true', help='print the facts and findings as JSON')
    arguments = parser.parse_args()
    facts, problems, warnings = inspect(arguments.bundle, arguments.archs.split())
    if arguments.json:
        print(json.dumps({'facts': facts, 'problems': problems, 'warnings': warnings}, indent=2, ensure_ascii=False))
    else:
        for warning in warnings:
            print(f'warning: {warning}', file=sys.stderr)
        for problem in problems:
            print(f'error: {problem}', file=sys.stderr)
        if not problems:
            print(f'{arguments.bundle.name}: {facts.get("identifier")} {facts.get("version")}, API {facts.get("apiVersion")}, '
                  f'entry {facts.get("principalClass")}, {" ".join(facts.get("architectures", []))}: loadable')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())

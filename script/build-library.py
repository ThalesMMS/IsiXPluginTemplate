#!/usr/bin/env python3
"""Build a C or C++ library (ITK, DCMTK, or any CMake project) for the plugin
to carry inside its own bundle, isolated from the application's copy.

    script/build-library.py itk   --source <ITK source> [--module ITKRegionGrowing ...]
    script/build-library.py dcmtk --source <DCMTK source> [--module dcmdata ...]
    script/build-library.py cmake --name <Name> --source <folder with CMakeLists.txt>

    common options: [--arch arm64|x86_64|universal] [--output Libraries] [--jobs N]
                    [--cmake-arg -DNAME=VALUE ...] [--keep-build]

The application links its own ITK, VTK and DCMTK statically and exports their
symbols. A plugin may not use them: they are not part of the plugin API, and
the application's version changes without notice. A plugin that needs one of
these libraries carries its own copy, built by this script:

- static (no .dylib inside the bundle), for macOS 15.0, for the architectures
  the plugin is built for;
- with every symbol hidden: -fvisibility=hidden and -fvisibility-inlines-hidden
  (CMake's visibility presets), and for ITK also ITK_TEMPLATE_VISIBILITY_DEFAULT
  off, which otherwise forces ITK's templates to default visibility;
- nothing is taken from Homebrew, MacPorts or /usr/local.

The plugin then links the library and exports no symbol at all
(EXPORTED_SYMBOLS_FILE = Config/Exports.exp in Config/MyIsiXPlugin.xcconfig).
Two-level namespace keeps the plugin's calls to its own copy; the empty export
list keeps the C++ templates and inline functions of the library (weak
definitions) out of reach of dyld, which would otherwise bind them to the
application's copies of the same names when the plugin loads. See README.md,
"Libraries of your own".

The result goes to <output>/<Name>/: include/, lib/lib<Name>.a (all of the
library's archives joined into one), share/licenses/ (the library's own
licence files, which you must distribute with the plugin) and library.json
(source, version, architectures, options, sizes). The CMake build folder,
<output>/.build/<Name>, is deleted afterwards unless --keep-build.

The script downloads nothing: --source is a folder you extracted from the
library's release archive or checked out yourself. It needs cmake and the
Xcode command line tools.
"""
from pathlib import Path
import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[1]
MINIMUM_SYSTEM = '15.0'

COMMON = [
    '-DBUILD_SHARED_LIBS=OFF',
    '-DBUILD_TESTING=OFF',
    '-DCMAKE_BUILD_TYPE=Release',
    '-DCMAKE_POSITION_INDEPENDENT_CODE=ON',
    '-DCMAKE_C_VISIBILITY_PRESET=hidden',
    '-DCMAKE_CXX_VISIBILITY_PRESET=hidden',
    '-DCMAKE_VISIBILITY_INLINES_HIDDEN=ON',
    '-DCMAKE_C_FLAGS=-fvisibility=hidden',
    '-DCMAKE_CXX_FLAGS=-fvisibility=hidden -fvisibility-inlines-hidden',
    f'-DCMAKE_OSX_DEPLOYMENT_TARGET={MINIMUM_SYSTEM}',
    '-DCMAKE_POLICY_VERSION_MINIMUM=3.5',
    # Nothing from a package manager: the plugin must build the same everywhere.
    '-DCMAKE_IGNORE_PREFIX_PATH=/opt/homebrew;/opt/local;/usr/local',
    '-DCMAKE_FIND_FRAMEWORK=NEVER',
]

# ITK modules built when --module is not given: image filters, region
# growing, level sets, registration, transforms and their dependencies.
ITK_MODULES = ['ITKCommon', 'ITKImageFilterBase', 'ITKImageGrid', 'ITKImageIntensity', 'ITKImageGradient',
               'ITKThresholding', 'ITKSmoothing', 'ITKRegionGrowing', 'ITKConnectedComponents', 'ITKCurvatureFlow',
               'ITKLevelSets', 'ITKTransform', 'ITKOptimizers', 'ITKRegistrationCommon', 'ITKStatistics',
               'ITKMathematicalMorphology', 'ITKBinaryMathematicalMorphology']
# DCMTK modules built when --module is not given: reading and writing DICOM files.
DCMTK_MODULES = ['ofstd', 'oflog', 'oficonv', 'dcmdata']


def itk_arguments(modules):
    arguments = ['-DITK_BUILD_DEFAULT_MODULES=OFF', '-DITKGroup_Core=OFF', '-DBUILD_EXAMPLES=OFF',
                 '-DBUILD_DOCUMENTATION=OFF', '-DITK_TEMPLATE_VISIBILITY_DEFAULT=OFF', '-DITK_USE_SYSTEM_ZLIB=ON',
                 '-DITK_WRAP_PYTHON=OFF', '-DITK_INSTALL_INCLUDE_DIR=include']
    return arguments + [f'-DModule_{module}=ON' for module in modules]


def dcmtk_arguments(modules):
    arguments = ['-DDCMTK_ENABLE_MANPAGES=OFF', '-DBUILD_APPS=OFF', '-DDCMTK_DEFAULT_DICT=builtin',
                 '-DDCMTK_ENABLE_BUILTIN_OFICONV_DATA=ON', '-DDCMTK_WITH_ZLIB=ON', '-DDCMTK_WITH_OPENSSL=OFF',
                 '-DDCMTK_WITH_PNG=OFF', '-DDCMTK_WITH_TIFF=OFF', '-DDCMTK_WITH_XML=OFF', '-DDCMTK_WITH_ICONV=OFF',
                 '-DDCMTK_WITH_ICU=OFF', '-DDCMTK_WITH_SNDFILE=OFF', '-DDCMTK_WITH_WRAP=OFF',
                 '-DDCMTK_WITH_OPENJPEG=OFF', '-DDCMTK_WITH_DOXYGEN=OFF']
    return arguments + ['-DDCMTK_MODULES=' + ';'.join(modules)]


def itk_version(source):
    text = (source / 'CMake' / 'itkVersion.cmake').read_text()
    parts = [re.search(r'set\(ITK_VERSION_%s "(\d+)"\)' % part, text) for part in ('MAJOR', 'MINOR', 'PATCH')]
    return '.'.join(match.group(1) for match in parts) if all(parts) else None


def dcmtk_version(source):
    text = (source / 'CMake' / 'dcmtkPrepare.cmake').read_text()
    parts = [re.search(r'set\(DCMTK_%s_VERSION (\d+)\)' % part, text) for part in ('MAJOR', 'MINOR', 'BUILD')]
    return '.'.join(match.group(1) for match in parts) if all(parts) else None


PRESETS = {
    'itk': {'name': 'ITK', 'modules': ITK_MODULES, 'arguments': itk_arguments, 'version': itk_version,
            'check': 'CMake/itkVersion.cmake', 'link': ['-lz']},
    'dcmtk': {'name': 'DCMTK', 'modules': DCMTK_MODULES, 'arguments': dcmtk_arguments, 'version': dcmtk_version,
              'check': 'dcmdata/include/dcmtk/dcmdata/dctk.h', 'link': ['-lz']},
    'cmake': {'name': None, 'modules': [], 'arguments': lambda modules: [], 'version': lambda source: None,
              'check': 'CMakeLists.txt', 'link': []},
}
LICENCE_FILES = re.compile(r'(?i)^(licen[cs]e|copying|copyright|notice)(\.(txt|md))?$')


def run(command, **options):
    print('+ ' + ' '.join(str(part) for part in command), flush=True)
    subprocess.run([str(part) for part in command], check=True, **options)


def default_visibility_symbols(archive):
    """Defined global symbols of the archive that are not hidden."""
    listing = subprocess.run(['xcrun', 'nm', '-m', '-g', '-U', str(archive)], capture_output=True, text=True).stdout
    return sorted({line.split()[-1] for line in listing.splitlines()
                   if ' external ' in line and 'private external' not in line})


def build(kind, source, output, arch, modules=None, name=None, jobs=None, cmake_arguments=(), keep_build=False):
    preset = PRESETS[kind]
    name = name or preset['name']
    source = Path(source).resolve()
    if not (source / preset['check']).is_file():
        raise SystemExit(f'{source}: no {preset["check"]}; is this the {name} source folder?')
    if shutil.which('cmake') is None:
        raise SystemExit('cmake is needed: install it from cmake.org or with your package manager')
    modules = list(modules or preset['modules'])
    archs = ['arm64', 'x86_64'] if arch == 'universal' else [arch]
    destination = Path(output).resolve() / name
    work = Path(output).resolve() / '.build' / name
    for folder in (destination, work):
        if folder.exists():
            shutil.rmtree(folder)
    work.mkdir(parents=True)
    sysroot = subprocess.run(['xcrun', '--show-sdk-path'], capture_output=True, text=True, check=True).stdout.strip()
    arguments = [*COMMON, f'-DCMAKE_OSX_ARCHITECTURES={";".join(archs)}', f'-DCMAKE_OSX_SYSROOT={sysroot}',
                 f'-DCMAKE_INSTALL_PREFIX={destination}', *preset['arguments'](modules), *cmake_arguments]
    run(['cmake', '-S', source, '-B', work, '-Wno-dev', *arguments])
    run(['cmake', '--build', work, '--parallel', str(jobs or os.cpu_count() or 4)])
    run(['cmake', '--install', work])

    archives = sorted(destination.rglob('*.a'))
    if not archives:
        raise SystemExit(f'the build of {name} installed no static library')
    library = destination / 'lib' / f'lib{name}.a'
    joined = work / f'lib{name}.a'
    run(['xcrun', 'libtool', '-static', '-no_warning_for_no_symbols', '-o', joined, *archives])
    for archive in archives:
        archive.unlink()
    for folder in ('lib/cmake', 'lib/pkgconfig', 'bin', 'share/doc', 'share/man', 'etc'):
        if (destination / folder).exists():
            shutil.rmtree(destination / folder)
    library.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(joined), library)

    licences = destination / 'share' / 'licenses' / name
    licences.mkdir(parents=True, exist_ok=True)
    for path in source.iterdir():
        if path.is_file() and LICENCE_FILES.match(path.name):
            shutil.copy2(path, licences / path.name)

    exposed = default_visibility_symbols(library)
    present = subprocess.run(['xcrun', 'lipo', '-archs', str(library)], capture_output=True, text=True).stdout.split()
    record = {
        'name': name, 'kind': kind, 'version': preset['version'](source), 'source': str(source),
        'architectures': present, 'minimumSystem': MINIMUM_SYSTEM, 'modules': modules,
        'cmakeArguments': arguments, 'link': [f'-l{name}', *preset['link'], '-lc++'],
        'librarySize': library.stat().st_size,
        'defaultVisibilitySymbols': len(exposed), 'defaultVisibilityExamples': exposed[:10],
    }
    (destination / 'library.json').write_text(json.dumps(record, indent=2) + '\n')
    if not keep_build:
        shutil.rmtree(work)
        try:
            work.parent.rmdir()
        except OSError:
            pass
    print(f'{name} {record["version"] or ""} for {" ".join(present)}: {library} '
          f'({library.stat().st_size / 1e6:.1f} MB); {len(exposed)} symbols not hidden in the archive. '
          f'Link with {" ".join(record["link"])} and export no symbol from the plugin.')
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('kind', choices=sorted(PRESETS))
    parser.add_argument('--source', type=Path, required=True, help='the library source folder')
    parser.add_argument('--name', help='the library name (needed for cmake; ITK and DCMTK by default)')
    parser.add_argument('--module', action='append', dest='modules',
                        help='an ITK or DCMTK module to build (repeat); the defaults cover common uses')
    parser.add_argument('--arch', default=platform.machine(), choices=('arm64', 'x86_64', 'universal'))
    parser.add_argument('--output', type=Path, default=PROJECT / 'Libraries')
    parser.add_argument('--jobs', type=int)
    parser.add_argument('--cmake-arg', action='append', default=[], dest='cmake_arguments',
                        help='one more CMake argument, such as -DModule_ITKLabelMap=ON (repeat)')
    parser.add_argument('--keep-build', action='store_true', help='keep the CMake build folder')
    arguments = parser.parse_args()
    if arguments.kind == 'cmake' and not arguments.name:
        parser.error('cmake needs --name')
    build(arguments.kind, arguments.source, arguments.output, arguments.arch, arguments.modules, arguments.name,
          arguments.jobs, arguments.cmake_arguments, arguments.keep_build)
    return 0


if __name__ == '__main__':
    sys.exit(main())

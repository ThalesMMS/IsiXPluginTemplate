#!/usr/bin/env python3
"""Focused tests of the porting kit that script/sdk.py export puts in the SDK.

    python3 tests/test_sdk_porting.py [--app-source <IsiX DICOM Viewer source>] [-v]

On a synthetic application source, in a temporary folder: export_porting
copies the kit with its symbolic links and the three scripts into
<SDK>/LegacyPorting, leaves .DS_Store and __pycache__ out, and writes
PORTING.json; check_porting accepts that copy and refuses a changed file, a
missing file, a file not listed and a changed symbolic link; a source without
the kit is refused. With --app-source, the kit of a real source is exported
too, and its converter is run from the SDK copy to print its usage, which
proves it finds the kit and the inventory script beside it.

When the project's SDK folder holds an SDK, a copy of it is checked as well:
`check` accepts it and refuses it once an interface or the binary no longer
has the SHA-256 that SDK.json records.
"""
from pathlib import Path
import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sdk', PROJECT / 'script' / 'sdk.py')
sdk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sdk)

APP_SOURCE = None


def synthetic_source(root):
    root = Path(root)
    kit = root / 'IsiXLegacyBridge'
    (kit / 'Headers').mkdir(parents=True)
    (kit / 'Sources').mkdir()
    (kit / 'Include').mkdir()
    (kit / 'Headers' / 'PluginFilter.h').write_text('@interface PluginFilter\n@end\n')
    (kit / 'Sources' / 'IsiXLegacyPluginFilter.m').write_text('// filter\n')
    (kit / 'Sources' / '.DS_Store').write_bytes(b'\0')
    (kit / 'README.md').write_text('# IsiXLegacyBridge\n')
    os.symlink('../Headers', kit / 'Include' / 'OsiriXAPI')
    (root / 'LICENSE').write_text('Horos and OsiriX notice\n')
    (root / 'IsiXPluginKit').mkdir()
    (root / 'IsiXPluginKit' / 'README.md').write_text('# IsiXPluginKit\n')
    (root / 'tools' / '__pycache__').mkdir(parents=True)
    (root / 'tools' / '__pycache__' / 'x.pyc').write_bytes(b'\0')
    for name in ('port-legacy-plugin.py', 'build-isix-legacy-plugin.py', 'inventory-plugin-api.py'):
        (root / 'tools' / name).write_text(f'# {name}\n')
    return root


class PortingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = synthetic_source(self.root / 'source')
        self.sdk = self.root / 'SDK'
        self.manifest = sdk.export_porting(self.source, self.sdk, 'abc123')
        self.folder = self.sdk / sdk.PORTING

    def tearDown(self):
        self.temporary.cleanup()

    def test_export_copies_kit_scripts_and_links(self):
        files = self.manifest['files']
        self.assertEqual(self.manifest['revision'], 'abc123')
        self.assertIn('IsiXLegacyBridge/Headers/PluginFilter.h', files)
        self.assertIn('tools/port-legacy-plugin.py', files)
        self.assertIn('IsiXPluginKit/README.md', files)
        self.assertIn('LICENSE', files)
        self.assertEqual(files['IsiXLegacyBridge/Include/OsiriXAPI'], 'symlink:../Headers')
        self.assertTrue((self.folder / 'IsiXLegacyBridge' / 'Include' / 'OsiriXAPI').is_symlink())
        self.assertFalse(any('.DS_Store' in p or '__pycache__' in p for p in files))
        self.assertEqual(json.loads((self.folder / sdk.PORTING_MANIFEST).read_text()), self.manifest)
        self.assertEqual(sdk.check_porting(self.sdk), self.manifest)

    def test_export_replaces_an_older_copy(self):
        (self.folder / 'stale.txt').write_text('old\n')
        sdk.export_porting(self.source, self.sdk, 'def456')
        self.assertFalse((self.folder / 'stale.txt').exists())
        self.assertEqual(sdk.check_porting(self.sdk)['revision'], 'def456')

    def test_check_refuses_changes(self):
        def refused(change):
            sdk.export_porting(self.source, self.sdk, 'abc123')
            change()
            with self.assertRaises(sdk.SDKError):
                sdk.check_porting(self.sdk)
        refused(lambda: (self.folder / 'tools' / 'port-legacy-plugin.py').write_text('# changed\n'))
        refused(lambda: (self.folder / 'IsiXLegacyBridge' / 'README.md').unlink())
        refused(lambda: (self.folder / 'IsiXLegacyBridge' / 'Sources' / 'Extra.m').write_text('\n'))
        def relink():
            link = self.folder / 'IsiXLegacyBridge' / 'Include' / 'OsiriXAPI'
            link.unlink()
            os.symlink('../Sources', link)
        refused(relink)

    def test_check_ignores_finder_files(self):
        (self.folder / 'IsiXLegacyBridge' / '.DS_Store').write_bytes(b'\0')
        self.assertEqual(sdk.check_porting(self.sdk), self.manifest)

    def test_absent_folder_is_none_and_source_without_kit_is_refused(self):
        self.assertIsNone(sdk.check_porting(self.root / 'empty'))
        (self.root / 'bare').mkdir()
        with self.assertRaises(sdk.SDKError):
            sdk.export_porting(self.root / 'bare', self.root / 'other', 'abc123')


class RealSourceTests(unittest.TestCase):
    def test_converter_runs_from_the_sdk_copy(self):
        if not APP_SOURCE:
            self.skipTest('needs --app-source')
        with tempfile.TemporaryDirectory() as temporary:
            manifest = sdk.export_porting(APP_SOURCE, temporary, 'real')
            self.assertEqual(sdk.check_porting(temporary), manifest)
            converter = Path(temporary) / sdk.PORTING / 'tools' / 'port-legacy-plugin.py'
            result = subprocess.run([sys.executable, '-I', str(converter), '--help'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('IsiXLegacyBridge', result.stdout)


class RecordedChecksumTests(unittest.TestCase):
    def test_check_refuses_files_that_differ_from_sdk_json(self):
        origin = sdk.default_sdk()
        if not (origin / 'SDK.json').is_file():
            self.skipTest('needs an SDK in the SDK folder')
        with tempfile.TemporaryDirectory() as temporary:
            copy = Path(temporary) / 'SDK'
            shutil.copytree(origin, copy, symlinks=True)
            recorded = json.loads((copy / 'SDK.json').read_text())
            identity = sdk.check(copy)
            self.assertEqual(identity['binarySHA256'], recorded['binarySHA256'])
            self.assertEqual(identity['interfaceSHA256'], recorded['interfaceSHA256'])
            modules = copy / f'{sdk.NAME}.framework' / 'Versions' / 'A' / 'Modules' / f'{sdk.NAME}.swiftmodule'
            interface = sorted(modules.glob('*.swiftinterface'))[0]
            original = interface.read_bytes()
            interface.write_bytes(original + b'// changed\n')
            with self.assertRaisesRegex(sdk.SDKError, interface.name):
                sdk.check(copy)
            interface.write_bytes(original)
            sdk.check(copy)
            description = dict(recorded, binarySHA256='0' * 64)
            (copy / 'SDK.json').write_text(json.dumps(description))
            with self.assertRaisesRegex(sdk.SDKError, 'recorded in SDK.json'):
                sdk.check(copy)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--app-source', type=Path)
    known, rest = parser.parse_known_args()
    APP_SOURCE = known.app_source
    unittest.main(argv=[sys.argv[0], *rest])

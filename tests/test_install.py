#!/usr/bin/env python3
"""Focused tests of script/install.py, in disposable home folders.

    python3 tests/test_install.py [--bundle <built .isixplugin>] [-v]

Uses the plugin built by script/build.sh (build/Products/Debug by default) and
copies of it with another identifier, version or API version, re-signed ad
hoc. Every test runs in a new temporary home folder whose name has spaces and
non-ASCII letters, next to sentinel Horos and OsiriX plugins folders and
another IsiX plugin, and checks that those are left exactly as they were.
Exits with 2, skipped, when there is no built bundle.
"""
from pathlib import Path
import argparse
import hashlib
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

PROJECT = Path(__file__).resolve().parents[1]
INSTALL = PROJECT / 'script' / 'install.py'
BUNDLE = PROJECT / 'build' / 'Products' / 'Debug' / 'MyIsiXPlugin.isixplugin'
PLUGINS = Path('Library') / 'Application Support' / 'IsiX' / 'Plugins'
IDENTIFIER = 'com.example.myisixplugin'


def tree_digest(root):
    digest = hashlib.sha256()
    for path in sorted(Path(root).rglob('*')):
        digest.update(str(path.relative_to(root)).encode())
        if path.is_file() and not path.is_symlink():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def variant(source, destination, **changes):
    """A copy of the bundle with Info.plist keys changed, signed ad hoc again."""
    shutil.copytree(source, destination, symlinks=True)
    info_path = destination / 'Contents' / 'Info.plist'
    info = plistlib.loads(info_path.read_bytes())
    for key, value in changes.items():
        info[key] = value
    info_path.write_bytes(plistlib.dumps(info))
    subprocess.run(['codesign', '--force', '--sign', '-', str(destination)], check=True, capture_output=True)
    return destination


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix='isix install '))
        self.home = self.scratch / 'Fake Hôme with spaces' / 'usuário ü'
        self.folder = self.home / PLUGINS
        support = self.home / 'Library' / 'Application Support'
        # What must not be touched: the former applications' plugins and another IsiX plugin.
        (support / 'Horos' / 'Plugins' / 'Old.horosplugin' / 'Contents').mkdir(parents=True)
        (support / 'Horos' / 'Plugins' / 'Old.horosplugin' / 'Contents' / 'Info.plist').write_text('horos')
        (support / 'OsiriX' / 'Plugins' / 'Old.osirixplugin').mkdir(parents=True)
        (support / 'IsiX' / 'Plugins Disabled').mkdir(parents=True)
        self.folder.mkdir(parents=True)
        self.other = variant(BUNDLE, self.folder / 'Other Plugin.isixplugin', CFBundleIdentifier='org.example.other')
        self.sentinels = {path: tree_digest(path) for path in
                          (support / 'Horos', support / 'OsiriX', support / 'IsiX' / 'Plugins Disabled', self.other)}
        self.bundles = self.scratch / 'bundles'
        self.bundles.mkdir()

    def tearDown(self):
        for path, digest in self.sentinels.items():
            self.assertEqual(tree_digest(path), digest, f'{path} changed')
        shutil.rmtree(self.scratch, ignore_errors=True)

    def run_install(self, *arguments, environment=None):
        env = dict(os.environ, **(environment or {}))
        return subprocess.run([sys.executable, str(INSTALL), *arguments, '--home', str(self.home)],
                              capture_output=True, text=True, env=env)

    def version(self, name='MyIsiXPlugin.isixplugin', folder=None):
        info = plistlib.loads(((folder or self.folder) / name / 'Contents' / 'Info.plist').read_bytes())
        return info.get('CFBundleShortVersionString')

    def verify_signature(self, bundle):
        return subprocess.run(['codesign', '--verify', '--strict', '--deep', str(bundle)], capture_output=True).returncode

    def leftovers(self):
        return sorted(p.name for p in self.folder.glob('.isix-plugin-install-*'))

    def made(self, version, name='MyIsiXPlugin.isixplugin', **changes):
        folder = self.bundles / version
        folder.mkdir(exist_ok=True)
        if (folder / name).exists():
            return folder / name
        return variant(BUNDLE, folder / name, CFBundleShortVersionString=version, **changes)

    # MARK: tests

    def test_installs_into_a_home_with_spaces_and_unicode(self):
        result = self.run_install('install', str(self.made('1.0')))
        self.assertEqual(result.returncode, 0, result.stderr)
        installed = self.folder / 'MyIsiXPlugin.isixplugin'
        self.assertEqual(self.version(), '1.0')
        self.assertEqual(self.verify_signature(installed), 0)
        self.assertEqual(self.leftovers(), [])
        self.assertFalse((self.folder / '.horos-plugin-previous').exists())

    def test_default_folder_is_the_users_isix_plugins_folder(self):
        result = subprocess.run([sys.executable, str(INSTALL), 'where'], capture_output=True, text=True,
                                env=dict(os.environ, HOME=str(self.home)))
        self.assertEqual(Path(result.stdout.strip()), self.home / PLUGINS)
        system = subprocess.run([sys.executable, str(INSTALL), 'where', '--system'], capture_output=True, text=True)
        self.assertEqual(system.stdout.strip(), '/Library/Application Support/IsiX/Plugins')

    def test_replacing_keeps_the_previous_version_and_restore_puts_it_back(self):
        self.assertEqual(self.run_install('install', str(self.made('1.0'))).returncode, 0)
        result = self.run_install('install', str(self.made('2.0')))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.version(), '2.0')
        self.assertEqual(self.version(folder=self.folder / '.horos-plugin-previous'), '1.0')
        result = self.run_install('restore')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.version(), '1.0')
        self.assertFalse((self.folder / '.horos-plugin-previous').exists())

    def test_refuses_to_replace_a_bundle_of_another_identifier(self):
        foreign = variant(BUNDLE, self.folder / 'MyIsiXPlugin.isixplugin', CFBundleIdentifier='org.someone.else',
                          CFBundleShortVersionString='9.9')
        before = tree_digest(foreign)
        result = self.run_install('install', str(self.made('1.0')))
        self.assertEqual(result.returncode, 4)
        self.assertIn('org.someone.else', result.stderr)
        self.assertEqual(tree_digest(foreign), before)
        self.assertEqual(self.leftovers(), [])

    def test_refuses_a_second_copy_of_the_same_identifier_under_another_name(self):
        self.assertEqual(self.run_install('install', str(self.made('1.0'))).returncode, 0)
        renamed = self.made('1.1', name='Renamed.isixplugin')
        result = self.run_install('install', str(renamed))
        self.assertEqual(result.returncode, 4)
        self.assertIn('already installed as MyIsiXPlugin.isixplugin', result.stderr)
        self.assertFalse((self.folder / 'Renamed.isixplugin').exists())

    def test_refuses_bundles_the_application_would_refuse(self):
        for name, changes in (('future', {'IsiXPluginAPIVersion': 2}),
                              ('string', {'IsiXPluginAPIVersion': '1'}),
                              ('bare class', {'NSPrincipalClass': 'MyIsiXPlugin'}),
                              ('legacy', {'pluginType': 'imageFilter'})):
            with self.subTest(name):
                result = self.run_install('install', str(self.made(name, **changes)))
                self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
                self.assertFalse((self.folder / 'MyIsiXPlugin.isixplugin').exists())
        tampered = self.made('tampered')
        (tampered / 'Contents' / 'Resources' / 'Defaults.plist').write_text('changed after signing')
        result = self.run_install('install', str(tampered))
        self.assertEqual(result.returncode, 3)
        self.assertIn('signature', result.stderr)
        self.assertEqual(self.leftovers(), [])

    def test_interrupted_before_publishing_leaves_the_old_version(self):
        self.assertEqual(self.run_install('install', str(self.made('1.0'))).returncode, 0)
        result = self.run_install('install', str(self.made('2.0')), environment={'ISIX_INSTALL_TEST_INTERRUPT': 'after-copy'})
        self.assertEqual(result.returncode, 9)
        self.assertEqual(self.version(), '1.0')
        self.assertEqual(self.verify_signature(self.folder / 'MyIsiXPlugin.isixplugin'), 0)
        self.assertEqual(len(self.leftovers()), 1)
        result = self.run_install('install', str(self.made('2.0')))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('interrupted before it was published', result.stdout)
        self.assertEqual(self.version(), '2.0')
        self.assertEqual(self.version(folder=self.folder / '.horos-plugin-previous'), '1.0')
        self.assertEqual(self.leftovers(), [])

    def test_interrupted_after_publishing_keeps_both_versions(self):
        self.assertEqual(self.run_install('install', str(self.made('1.0'))).returncode, 0)
        result = self.run_install('install', str(self.made('2.0')), environment={'ISIX_INSTALL_TEST_INTERRUPT': 'after-swap'})
        self.assertEqual(result.returncode, 9)
        self.assertEqual(self.version(), '2.0')
        self.assertEqual(self.verify_signature(self.folder / 'MyIsiXPlugin.isixplugin'), 0)
        result = self.run_install('restore')
        self.assertNotEqual(result.returncode, 0, 'the old version is still in the interrupted staging folder')
        result = self.run_install('install', str(self.made('2.0')))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('kept the version replaced by an interrupted install', result.stdout)
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(self.run_install('restore').returncode, 0)
        self.assertEqual(self.version(), '2.0', 'the previous version is the 2.0 just replaced again')

    def test_refuses_to_replace_or_remove_a_loaded_copy(self):
        self.assertEqual(self.run_install('install', str(self.made('1.0'))).returncode, 0)
        executable = self.folder / 'MyIsiXPlugin.isixplugin' / 'Contents' / 'MacOS' / 'MyIsiXPlugin'
        holder = subprocess.Popen([sys.executable, '-c', 'import sys, time; f = open(sys.argv[1], "rb"); time.sleep(60)', str(executable)])
        try:
            time.sleep(1)
            result = self.run_install('install', str(self.made('2.0')))
            self.assertEqual(result.returncode, 5, result.stderr)
            self.assertIn(str(holder.pid), result.stderr)
            self.assertEqual(self.run_install('uninstall').returncode, 5)
            self.assertEqual(self.version(), '1.0')
        finally:
            holder.kill()
            holder.wait()

    def test_uninstall_removes_only_the_selected_bundle(self):
        self.assertEqual(self.run_install('install', str(self.made('1.0'))).returncode, 0)
        self.assertEqual(self.run_install('install', str(self.made('2.0'))).returncode, 0)
        result = self.run_install('uninstall', 'Other Plugin')
        self.assertEqual(result.returncode, 4, 'another identifier is not removed without --identifier')
        result = self.run_install('uninstall')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.folder / 'MyIsiXPlugin.isixplugin').exists())
        self.assertFalse((self.folder / '.horos-plugin-previous').exists())
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ['Other Plugin.isixplugin'])
        result = self.run_install('install', str(self.made('3.0')))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.version(), '3.0')

    def test_refuses_a_plugins_folder_that_is_a_link(self):
        # The folder is replaced by a link to another one, as a link to another
        # application's plugins folder would be; the other plugin goes with it.
        self.sentinels.pop(self.other)
        shutil.rmtree(self.folder)
        elsewhere = self.scratch / 'elsewhere'
        elsewhere.mkdir()
        self.folder.symlink_to(elsewhere, target_is_directory=True)
        result = self.run_install('install', str(self.made('1.0')))
        self.assertEqual(result.returncode, 1)
        self.assertIn('symbolic link', result.stderr)
        self.assertEqual(list(elsewhere.iterdir()), [])

def main():
    global BUNDLE
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--bundle', type=Path, default=BUNDLE)
    arguments, rest = parser.parse_known_args()
    BUNDLE = arguments.bundle.resolve()
    if not (BUNDLE / 'Contents' / 'Info.plist').is_file():
        print(f'skipped: no built plugin at {BUNDLE}; run script/build.sh first', file=sys.stderr)
        return 2
    program = unittest.main(argv=[sys.argv[0], *rest], exit=False)
    return 0 if program.result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())

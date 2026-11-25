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
import fcntl
import hashlib
import json
import os
import plistlib
import re
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

    def test_the_check_knows_every_capability_of_the_plugin_api(self):
        """script/verify_bundle.py, which the build and the installer run, accepts the capabilities the SDK documents."""
        sys.path.insert(0, str(PROJECT / 'script'))
        import verify_bundle
        documented = PROJECT / 'SDK' / 'README.md'
        if documented.is_file():
            table = set(re.findall(r'^\| `([a-z0-9.]+)` \| ', documented.read_text().split('## Capabilities', 1)[-1].split('\n## ', 1)[0], re.M))
            self.assertEqual(table, verify_bundle.CAPABILITIES, 'the check and SDK/README.md list other capabilities')

        def checked(name, capabilities):
            done = subprocess.run([sys.executable, str(PROJECT / 'script' / 'verify_bundle.py'), '--json',
                                   str(self.made(name, IsiXPluginCapabilities=capabilities))], capture_output=True, text=True)
            return done.returncode, json.loads(done.stdout)

        for capability in sorted(verify_bundle.CAPABILITIES):
            with self.subTest(capability):
                code, found = checked(capability, ['viewer.read', capability])
                self.assertEqual((code, found['problems']), (0, []))
                later = [w for w in found['warnings'] if 'needs an application after' in w]
                self.assertEqual(bool(later), capability in verify_bundle.LATER_CAPABILITIES, found['warnings'])
        code, found = checked('unknown', ['viewer.read', 'time.travel'])
        self.assertNotEqual(code, 0)
        self.assertIn('unknown capabilities: time.travel', found['problems'])
        # The installer refuses what the check refuses, and takes what it accepts.
        self.assertEqual(self.run_install('install', str(self.made('refused', IsiXPluginCapabilities=['time.travel']))).returncode, 3)
        result = self.run_install('install', str(self.made('report', IsiXPluginCapabilities=['viewer.read', 'roi', 'report'])))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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
        result = self.run_install('install', str(self.made('2.0')))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('kept the version replaced by an interrupted install', result.stdout)
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(self.run_install('restore').returncode, 0)
        self.assertEqual(self.version(), '2.0', 'the previous version is the 2.0 just replaced again')

    def test_restore_recovers_the_version_an_interrupted_update_replaced(self):
        self.assertEqual(self.run_install('install', str(self.made('1.0'))).returncode, 0)
        result = self.run_install('install', str(self.made('2.0')), environment={'ISIX_INSTALL_TEST_INTERRUPT': 'after-swap'})
        self.assertEqual(result.returncode, 9)
        result = self.run_install('restore')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('kept the version replaced by an interrupted install', result.stdout)
        self.assertEqual(self.version(), '1.0', 'restore puts back the version the interrupted update replaced')
        self.assertEqual(self.verify_signature(self.folder / 'MyIsiXPlugin.isixplugin'), 0)
        self.assertEqual(self.leftovers(), [])

    def test_restore_prefers_the_interrupted_update_over_an_older_previous_version(self):
        self.assertEqual(self.run_install('install', str(self.made('1.0'))).returncode, 0)
        self.assertEqual(self.run_install('install', str(self.made('2.0'))).returncode, 0)
        result = self.run_install('install', str(self.made('3.0')), environment={'ISIX_INSTALL_TEST_INTERRUPT': 'after-swap'})
        self.assertEqual(result.returncode, 9)
        result = self.run_install('restore')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.version(), '2.0', 'not 1.0, the version kept before the interrupted update')

    def test_an_unfinished_copy_is_never_kept_as_the_previous_version(self):
        self.assertEqual(self.run_install('install', str(self.made('1.0'))).returncode, 0)
        self.assertEqual(self.run_install('install', str(self.made('2.0'))).returncode, 0)
        result = self.run_install('install', str(self.made('3.0')), environment={'ISIX_INSTALL_TEST_INTERRUPT': 'after-copy'})
        self.assertEqual(result.returncode, 9)
        # A copy cut short by a crash: it never leaves the staging folder's copy area.
        staging = self.folder / self.leftovers()[0]
        inner = staging / 'MyIsiXPlugin.isixplugin'
        shutil.rmtree(inner)
        (staging / 'copy' / inner.name / 'Contents').mkdir(parents=True)
        (staging / 'copy' / inner.name / 'Contents' / 'Info.plist').write_text('partial')
        result = self.run_install('restore')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('interrupted before it was published', result.stdout)
        self.assertEqual(self.version(), '1.0')
        self.assertEqual(self.verify_signature(self.folder / 'MyIsiXPlugin.isixplugin'), 0)
        self.assertEqual(self.leftovers(), [])

    def test_a_partial_copy_left_by_an_older_installer_is_never_kept_or_published(self):
        self.assertEqual(self.run_install('install', str(self.made('1.0'))).returncode, 0)
        self.assertEqual(self.run_install('install', str(self.made('2.0'))).returncode, 0)
        # Older installers copied straight into the staging folder: a crash during
        # the copy left a partial bundle (readable Info.plist, no executable) there.
        staging = self.folder / '.isix-plugin-install-0123456789abcdef'
        partial = staging / 'MyIsiXPlugin.isixplugin'
        shutil.copytree(self.made('3.0'), partial, symlinks=True)
        shutil.rmtree(partial / 'Contents' / 'MacOS')
        (staging / 'state.json').write_text(json.dumps({'name': partial.name, 'identifier': IDENTIFIER,
                                                        'fingerprint': 'digest of the complete 3.0 copy'}))
        result = self.run_install('restore')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('interrupted before it was published', result.stdout)
        self.assertEqual(self.version(), '1.0', 'the real previous version, not the partial copy')
        self.assertEqual(self.verify_signature(self.folder / 'MyIsiXPlugin.isixplugin'), 0)
        self.assertEqual(self.leftovers(), [])

    def test_a_partial_copy_of_the_installed_version_is_never_kept_or_published(self):
        self.assertEqual(self.run_install('install', str(self.made('1.0'))).returncode, 0)
        self.assertEqual(self.run_install('install', str(self.made('2.0'))).returncode, 0)
        # An older installer killed while reinstalling the same 2.0: the installed
        # bundle already matches the fingerprint state.json records.
        sys.path.insert(0, str(PROJECT / 'script'))
        import install
        installed = self.folder / 'MyIsiXPlugin.isixplugin'
        staging = self.folder / '.isix-plugin-install-fedcba9876543210'
        partial = staging / installed.name
        shutil.copytree(installed, partial, symlinks=True)
        shutil.rmtree(partial / 'Contents' / 'MacOS')
        (staging / 'state.json').write_text(json.dumps({'name': installed.name, 'identifier': IDENTIFIER,
                                                        'fingerprint': install.fingerprint(installed)}))
        result = self.run_install('restore')
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertIn('as it is', result.stderr)
        self.assertEqual(self.version(), '2.0', 'nothing changes while the copy is unresolved')
        self.assertEqual(self.version(folder=self.folder / '.horos-plugin-previous'), '1.0')
        self.assertEqual(self.leftovers(), [staging.name], 'an unproven copy is never deleted')
        # Once the user moves it away, restore goes ahead with the real previous version.
        shutil.move(str(staging), str(self.scratch / 'moved away'))
        result = self.run_install('restore')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.version(), '1.0', 'the real previous version, not the partial copy')
        self.assertEqual(self.verify_signature(installed), 0)

    def test_restore_after_an_interrupted_update_needs_no_tools(self):
        self.assertEqual(self.run_install('install', str(self.made('1.0'))).returncode, 0)
        result = self.run_install('install', str(self.made('2.0')), environment={'ISIX_INSTALL_TEST_INTERRUPT': 'after-swap'})
        self.assertEqual(result.returncode, 9)
        # The displaced 1.0 is known by the fingerprint the installer recorded:
        # recovery keeps it even when xcrun and codesign cannot run.
        result = self.run_install('restore', environment={'PATH': str(self.scratch / 'no tools')})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('kept the version replaced by an interrupted install', result.stdout)
        self.assertEqual(self.version(), '1.0')
        self.assertEqual(self.leftovers(), [])

    def test_an_older_installers_copy_the_check_cannot_judge_is_left_until_it_can(self):
        self.assertEqual(self.run_install('install', str(self.made('1.0'))).returncode, 0)
        result = self.run_install('install', str(self.made('2.0')), environment={'ISIX_INSTALL_TEST_INTERRUPT': 'after-swap'})
        self.assertEqual(result.returncode, 9)
        # As an older installer left it: no record of the displaced bundle.
        staging = self.folder / self.leftovers()[0]
        state = json.loads((staging / 'state.json').read_text())
        del state['replaced']
        (staging / 'state.json').write_text(json.dumps(state))
        # The check fails for the staged bundle (no tools): it is left as it is.
        result = self.run_install('restore', environment={'PATH': str(self.scratch / 'no tools')})
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertIn('as it is', result.stderr)
        self.assertEqual(self.version(), '2.0')
        self.assertEqual(self.leftovers(), [staging.name])
        # Once the check works, the displaced 1.0 is recovered and restored.
        result = self.run_install('restore')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('kept the version replaced by an interrupted install', result.stdout)
        self.assertEqual(self.version(), '1.0')
        self.assertEqual(self.leftovers(), [])

    def test_an_unresolved_copy_blocks_changes_that_would_lose_it(self):
        self.assertEqual(self.run_install('install', str(self.made('1.0'))).returncode, 0)
        self.assertEqual(self.run_install('install', str(self.made('2.0'))).returncode, 0)
        result = self.run_install('install', str(self.made('3.0')), environment={'ISIX_INSTALL_TEST_INTERRUPT': 'after-swap'})
        self.assertEqual(result.returncode, 9)
        # An older installer's staging folder holding the displaced 2.0, with 1.0
        # saved as the previous version, and a check that cannot run.
        staging = self.folder / self.leftovers()[0]
        state = json.loads((staging / 'state.json').read_text())
        del state['replaced']
        (staging / 'state.json').write_text(json.dumps(state))
        no_tools = {'PATH': str(self.scratch / 'no tools')}
        for command in (['restore'], ['restore'], ['uninstall']):
            result = self.run_install(*command, environment=no_tools)
            self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
            self.assertEqual(self.version(), '3.0')
            self.assertEqual(self.version(folder=self.folder / '.horos-plugin-previous'), '1.0')
            self.assertEqual(self.leftovers(), [staging.name])
        # With the check working again, the displaced 2.0 is the one restored.
        result = self.run_install('restore')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.version(), '2.0')
        self.assertEqual(self.leftovers(), [])

    def test_an_unresolved_copy_stops_recovery_before_any_other_folder_is_handled(self):
        self.assertEqual(self.run_install('install', str(self.made('1.0'))).returncode, 0)
        self.assertEqual(self.run_install('install', str(self.made('2.0'))).returncode, 0)
        result = self.run_install('install', str(self.made('3.0')), environment={'ISIX_INSTALL_TEST_INTERRUPT': 'after-swap'})
        self.assertEqual(result.returncode, 9)
        resolvable = self.folder / self.leftovers()[0]  # this installer's: holds 2.0, records it
        # Next to it, an older installer's folder with a partial copy the check cannot judge.
        older = self.folder / '.isix-plugin-install-0000000000000000'
        partial = older / 'MyIsiXPlugin.isixplugin'
        shutil.copytree(self.made('2.5'), partial, symlinks=True)
        shutil.rmtree(partial / 'Contents' / 'MacOS')
        sys.path.insert(0, str(PROJECT / 'script'))
        import install
        (older / 'state.json').write_text(json.dumps({'name': partial.name, 'identifier': IDENTIFIER,
                                                      'fingerprint': install.fingerprint(self.folder / partial.name)}))
        before = {path: tree_digest(path) for path in (resolvable, older, self.folder / '.horos-plugin-previous')}
        result = self.run_install('restore')
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertIn('Nothing was changed', result.stderr)
        self.assertEqual(self.version(), '3.0')
        for path, digest in before.items():
            self.assertEqual(tree_digest(path), digest, f'{path} changed')
        # With the older folder moved away, the resolvable one is handled: 2.0 comes back.
        shutil.move(str(older), str(self.scratch / 'moved away'))
        result = self.run_install('restore')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.version(), '2.0')
        self.assertEqual(self.leftovers(), [])

    def test_a_copy_that_is_not_the_recorded_one_is_left_for_the_user(self):
        self.assertEqual(self.run_install('install', str(self.made('1.0'))).returncode, 0)
        self.assertEqual(self.run_install('install', str(self.made('2.0'))).returncode, 0)
        result = self.run_install('install', str(self.made('3.0')), environment={'ISIX_INSTALL_TEST_INTERRUPT': 'after-swap'})
        self.assertEqual(result.returncode, 9)
        # As if another install had run meanwhile: the copy here is not the bundle recorded as replaced.
        staging = self.folder / self.leftovers()[0]
        state = json.loads((staging / 'state.json').read_text())
        state['replaced'] = 'fingerprint of a bundle another install displaced'
        (staging / 'state.json').write_text(json.dumps(state))
        before = {path: tree_digest(path) for path in (staging, self.folder / '.horos-plugin-previous')}
        result = self.run_install('restore')
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertIn('another install may have run at the same time', result.stderr)
        self.assertEqual(self.version(), '3.0')
        for path, digest in before.items():
            self.assertEqual(tree_digest(path), digest, f'{path} changed')

    def test_a_run_while_another_holds_the_folder_changes_nothing(self):
        self.assertEqual(self.run_install('install', str(self.made('1.0'))).returncode, 0)
        result = self.run_install('install', str(self.made('2.0')), environment={'ISIX_INSTALL_TEST_INTERRUPT': 'after-swap'})
        self.assertEqual(result.returncode, 9)
        staging = self.folder / self.leftovers()[0]
        before = tree_digest(self.folder)
        # Another run holds the plugins folder: nothing is planned or applied.
        descriptor = os.open(self.folder, os.O_RDONLY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            for command in (['restore'], ['uninstall'], ['install', str(self.made('3.0'))]):
                result = self.run_install(*command)
                self.assertEqual(result.returncode, 8, result.stdout + result.stderr)
                self.assertIn('Another run of this script', result.stderr)
                self.assertEqual(tree_digest(self.folder), before)
        finally:
            os.close(descriptor)
        self.assertEqual(self.leftovers(), [staging.name])
        result = self.run_install('restore')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.version(), '1.0')

    def test_recovery_stops_if_the_installed_bundle_changes_after_it_decided(self):
        self.assertEqual(self.run_install('install', str(self.made('1.0'))).returncode, 0)
        result = self.run_install('install', str(self.made('2.0')), environment={'ISIX_INSTALL_TEST_INTERRUPT': 'after-copy'})
        self.assertEqual(result.returncode, 9)
        staging = self.folder / self.leftovers()[0]
        sys.path.insert(0, str(PROJECT / 'script'))
        import install
        installed = self.folder / 'MyIsiXPlugin.isixplugin'
        real, seen = install.fingerprint, []
        def fingerprint(bundle):
            # As if a script without the folder lock swapped a bundle in
            # between the decision and the action.
            if Path(bundle) == installed:
                seen.append(bundle)
                if len(seen) > 1:
                    return 'the bundle another script published'
            return real(bundle)
        install.fingerprint = fingerprint
        try:
            with self.assertRaises(install.InstallError) as raised:
                install.recover(self.folder)
        finally:
            install.fingerprint = real
        self.assertEqual(raised.exception.code, 8)
        self.assertEqual(self.leftovers(), [staging.name], 'the folder decided on is left as it is')

    def test_restore_and_uninstall_stop_when_there_is_no_plugins_folder(self):
        shutil.rmtree(self.other)
        self.sentinels.pop(self.other)
        self.folder.rmdir()
        for command in (['restore'], ['uninstall']):
            result = self.run_install(*command)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn('nothing is installed there', result.stderr)
            self.assertFalse(self.folder.exists(), 'only install creates the plugins folder')

    def test_the_check_refuses_an_executable_it_cannot_read(self):
        bundle = self.made('garbled')
        (bundle / 'Contents' / 'MacOS' / 'MyIsiXPlugin').write_text('not a Mach-O file')
        done = subprocess.run([sys.executable, str(PROJECT / 'script' / 'verify_bundle.py'), str(bundle)],
                              capture_output=True, text=True)
        self.assertNotEqual(done.returncode, 0, done.stdout)
        self.assertIn('architectures of the executable could not be read', done.stdout + done.stderr)

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

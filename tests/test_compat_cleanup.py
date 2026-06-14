#
# Project Ginger Base
#
# Regression tests for legacy compatibility cleanup.
# These tests verify that the specific issues addressed in the
# compatibility sweep are fixed and do not regress.
#
import ast
import os
import unittest

import mock


class PvsWithVgListIndependenceTest(unittest.TestCase):
    """
    Verify that pvs_with_vg_list() returns independent dicts per entry,
    not shared references to the same dict object.
    """

    @mock.patch('wok.plugins.gingerbase.disks.run_command')
    def test_each_entry_is_independent_dict(self, mock_run_command):
        from wok.plugins.gingerbase.disks import pvs_with_vg_list

        mock_run_command.return_value = (
            '/dev/sda1 vg_system\n'
            '/dev/sdb1 vg_data\n'
            '/dev/sdc1',
            '',
            0
        )

        result = pvs_with_vg_list()

        # Should have 3 entries
        self.assertEqual(len(result), 3)

        # Each entry should be a different dict object
        self.assertIsNot(result[0], result[1])
        self.assertIsNot(result[1], result[2])

        # Each entry should have its own correct data
        self.assertEqual(result[0].get('/dev/sda1'), 'vg_system')
        self.assertEqual(result[1].get('/dev/sdb1'), 'vg_data')
        self.assertEqual(result[2].get('/dev/sdc1'), 'N/A')

        # Modifying one entry should not affect others
        result[0]['extra_key'] = 'extra_value'
        self.assertNotIn('extra_key', result[1])
        self.assertNotIn('extra_key', result[2])


class ZypperPriorityTest(unittest.TestCase):
    """
    Verify that when dnf is available, zypper does not override it
    as the selected package manager.
    """

    @mock.patch('wok.plugins.gingerbase.swupdate.run_command')
    def test_dnf_wins_over_zypper(self, mock_run_command):
        from wok.plugins.gingerbase.swupdate import SoftwareUpdate, DnfUpdate
        from wok.basemodel import Singleton
        import builtins

        # Clean singleton cache for this test
        Singleton._instances = {}

        # dnf import succeeds (first in the for-loop), zypper is also available
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == 'dnf':
                return mock.MagicMock()
            if name in ('yum', 'apt', 'portage'):
                raise ImportError('No module named %s' % name)
            return original_import(name, *args, **kwargs)

        # zypper --help succeeds
        mock_run_command.return_value = ('', '', 0)

        with mock.patch('builtins.__import__', side_effect=mock_import):
            su = SoftwareUpdate()

        # Should be DnfUpdate, NOT ZypperUpdate
        self.assertIsInstance(su._pkg_mnger, DnfUpdate)

        # Clean up singleton
        Singleton._instances = {}


class RpmModuleGuardTest(unittest.TestCase):
    """
    Verify that _get_releasever() handles missing rpm module gracefully
    instead of raising NameError.
    """

    def test_returns_releasever_when_rpm_unavailable(self):
        import wok.plugins.gingerbase.yumparser as yumparser_mod

        # Simulate rpm module not being available
        original_rpm = yumparser_mod.rpm
        try:
            yumparser_mod.rpm = None
            result = yumparser_mod._get_releasever()
            self.assertEqual(result, '%releasever')
        finally:
            yumparser_mod.rpm = original_rpm


class ZypperReturncodeTest(unittest.TestCase):
    """
    Verify that Zypper methods use returncode (not stderr length)
    to determine command failure.
    """

    @mock.patch('wok.plugins.gingerbase.swupdate.run_command')
    def test_getPackagesList_ignores_stderr_warnings(self, mock_run_command):
        from wok.plugins.gingerbase.swupdate import ZypperUpdate

        # returncode=0 (success) but stderr has warnings
        mock_run_command.return_value = (
            'v | repository | pkg | 1.0 | x86_64 | repo1',
            'Warning: some repo is slow',
            0
        )

        zu = ZypperUpdate()
        # Should not raise, even though stderr is non-empty
        result = zu.getPackagesList()
        self.assertIsInstance(result, list)

    @mock.patch('wok.plugins.gingerbase.swupdate.run_command')
    def test_getPackageInfo_fails_on_nonzero_returncode(self, mock_run_command):
        from wok.plugins.gingerbase.swupdate import ZypperUpdate
        from wok.exception import OperationFailed

        # returncode != 0 indicates actual failure
        mock_run_command.return_value = ('', 'error details', 1)

        zu = ZypperUpdate()
        with self.assertRaises(OperationFailed):
            zu.getPackageInfo('some-pkg')


class LazyMapToListTest(unittest.TestCase):
    """
    Verify that vgs(), lvs(), and pvs() return actual lists,
    not lazy map iterators.
    """

    @mock.patch('wok.plugins.gingerbase.disks.run_command')
    def test_vgs_returns_list(self, mock_run_command):
        from wok.plugins.gingerbase.disks import vgs

        mock_run_command.return_value = (
            'vgtest 999653638144 0',
            '',
            0
        )

        result = vgs()
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['vgname'], 'vgtest')

    @mock.patch('wok.plugins.gingerbase.disks.run_command')
    def test_lvs_returns_list(self, mock_run_command):
        from wok.plugins.gingerbase.disks import lvs

        mock_run_command.return_value = (
            'lva /dev/vgtest/lva 12345 vgtest',
            '',
            0
        )

        result = lvs()
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['lvname'], 'lva')

    @mock.patch('wok.plugins.gingerbase.disks.run_command')
    def test_pvs_returns_list(self, mock_run_command):
        from wok.plugins.gingerbase.disks import pvs

        mock_run_command.return_value = (
            '/dev/sda3 469502001152 kkon5B-vnFI vgtest',
            '',
            0
        )

        result = pvs()
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['pvname'], '/dev/sda3')


class NoEMessageRegressionTest(unittest.TestCase):
    """
    Verify that no source file uses the Python 2 `e.message` pattern,
    which crashes with AttributeError on Python 3.
    """

    def test_no_e_message_in_source_files(self):
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        source_files = []

        for dirpath, dirnames, filenames in os.walk(project_root):
            # Skip test files and UI directory
            if 'tests' in dirpath or 'ui' in dirpath or '.git' in dirpath:
                continue
            for f in filenames:
                if f.endswith('.py'):
                    source_files.append(os.path.join(dirpath, f))

        violations = []
        for filepath in source_files:
            with open(filepath) as fh:
                try:
                    tree = ast.parse(fh.read(), filename=filepath)
                except SyntaxError:
                    continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    if node.attr == 'message':
                        if isinstance(node.value, ast.Name):
                            if node.value.id == 'e':
                                rel_path = os.path.relpath(filepath,
                                                           project_root)
                                violations.append(
                                    '%s:%d' % (rel_path, node.lineno))

        self.assertEqual(
            violations, [],
            'Found Python 2 e.message pattern in: %s' % ', '.join(violations)
        )


class NoSafeConfigParserRegressionTest(unittest.TestCase):
    """
    Verify that SafeConfigParser (removed in Python 3.12) is not used.
    """

    def test_no_safe_config_parser_in_source(self):
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        violations = []
        for dirpath, dirnames, filenames in os.walk(project_root):
            if 'tests' in dirpath or 'ui' in dirpath or '.git' in dirpath:
                continue
            for f in filenames:
                if f.endswith('.py'):
                    filepath = os.path.join(dirpath, f)
                    with open(filepath) as fh:
                        content = fh.read()
                    if 'SafeConfigParser' in content:
                        rel_path = os.path.relpath(filepath, project_root)
                        violations.append(rel_path)

        self.assertEqual(
            violations, [],
            'Found SafeConfigParser in: %s' % ', '.join(violations)
        )


if __name__ == '__main__':
    unittest.main()

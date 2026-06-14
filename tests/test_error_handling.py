#
# Project Ginger Base
#
# Copyright IBM Corp, 2015-2017
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
import unittest

import mock
import psutil
from wok.basemodel import Singleton
from wok.exception import InvalidOperation


class TestErrorMessageExtraction(unittest.TestCase):
    """Verify str(e) produces correct message text from various
    exception types -- the pattern used throughout after the
    e.message -> str(e) migration."""

    def test_standard_exception(self):
        try:
            raise Exception('test error message')
        except Exception as e:
            self.assertEqual(str(e), 'test error message')

    def test_oserror_with_errno(self):
        try:
            raise OSError(2, 'No such file or directory', '/fake/path')
        except OSError as e:
            msg = str(e)
            self.assertIn('No such file or directory', msg)

    def test_empty_exception(self):
        try:
            raise Exception()
        except Exception as e:
            self.assertEqual(str(e), '')

    def test_exception_no_message_attr(self):
        """Confirm Python 3 exceptions do NOT have .message attribute."""
        try:
            raise ValueError('some value error')
        except ValueError as e:
            self.assertFalse(hasattr(e, 'message'))
            self.assertEqual(str(e), 'some value error')


class TestSingletonMetaclass(unittest.TestCase):
    """Verify that the metaclass=Singleton syntax works in Python 3,
    which was broken when using __metaclass__ = Singleton."""

    def setUp(self):
        Singleton._instances = {}

    def test_singleton_returns_same_instance(self):
        class _TestSingleton(object, metaclass=Singleton):
            def __init__(self):
                self.value = 42

        a = _TestSingleton()
        b = _TestSingleton()
        self.assertIs(a, b)

    def test_singleton_reset_allows_new_instance(self):
        class _TestSingleton2(object, metaclass=Singleton):
            pass

        a = _TestSingleton2()
        Singleton._instances = {}
        b = _TestSingleton2()
        self.assertIsNot(a, b)

    def tearDown(self):
        Singleton._instances = {}


class TestPackageManagerDetection(unittest.TestCase):
    """Verify that when no package manager is available, the environment
    probing code raises InvalidOperation (not a bare Exception)."""

    @mock.patch('wok.plugins.gingerbase.repositories.wok_log')
    def test_no_repo_manager_raises_invalid_operation(self, mock_log):
        original_import = __builtins__['__import__'] if isinstance(
            __builtins__, dict) else __builtins__.__import__

        # Block all repo-related imports
        blocked = {'dnf', 'yum', 'apt_pkg'}

        def selective_import(name, *args, **kwargs):
            if name in blocked:
                raise ImportError('No module named %s' % name)
            return original_import(name, *args, **kwargs)

        Singleton._instances = {}
        with mock.patch('builtins.__import__', side_effect=selective_import):
            from wok.plugins.gingerbase.repositories import Repositories
            with self.assertRaises(InvalidOperation) as ctx:
                Repositories()
            self.assertEqual(ctx.exception.code, 'GGBREPOS0014E')

    @mock.patch('wok.plugins.gingerbase.swupdate.run_command')
    @mock.patch('wok.plugins.gingerbase.swupdate.wok_log')
    def test_no_update_manager_raises_invalid_operation(self, mock_log,
                                                         mock_run_cmd):
        original_import = __builtins__['__import__'] if isinstance(
            __builtins__, dict) else __builtins__.__import__

        # Block all update-related imports
        blocked = {'dnf', 'yum', 'apt', 'portage'}

        def selective_import(name, *args, **kwargs):
            if name in blocked:
                raise ImportError('No module named %s' % name)
            return original_import(name, *args, **kwargs)

        # Make zypper --help fail too
        mock_run_cmd.return_value = ['', 'command not found', 127]

        Singleton._instances = {}
        with mock.patch('builtins.__import__', side_effect=selective_import):
            from wok.plugins.gingerbase.swupdate import SoftwareUpdate
            with self.assertRaises(InvalidOperation) as ctx:
                SoftwareUpdate()
            self.assertEqual(ctx.exception.code, 'GGBPKGUPD0004E')

    def tearDown(self):
        Singleton._instances = {}


class TestPsutilDirectApi(unittest.TestCase):
    """Verify psutil modern APIs are directly available without
    hasattr guards -- confirms the >= 2.0 requirement is met."""

    def test_virtual_memory(self):
        mem = psutil.virtual_memory()
        self.assertGreater(mem.total, 0)

    def test_cpu_count(self):
        count = psutil.cpu_count()
        self.assertIsNotNone(count)
        self.assertGreater(count, 0)

    def test_net_io_counters(self):
        counters = psutil.net_io_counters()
        self.assertIsNotNone(counters)

    def test_cpu_percent(self):
        pct = psutil.cpu_percent(None)
        self.assertIsInstance(pct, float)

    def test_boot_time(self):
        boot = psutil.boot_time()
        self.assertGreater(boot, 0)

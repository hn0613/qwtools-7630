#
# Project Ginger Base
#
# Copyright IBM Corp, 2015-2016
#
# Code derived from Project Kimchi
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
import os
import shutil
import tempfile
import time
import unittest

from unittest import mock

from wok.exception import InvalidParameter
from wok.exception import NotFoundError
from wok.plugins.gingerbase.model.debugreports import _resolve_report_file
from wok.plugins.gingerbase.model.debugreports import DebugReportModel
from wok.plugins.gingerbase.model.debugreports import DebugReportsModel


def _touch(path, content=''):
    with open(path, 'w') as f:
        f.write(content)


class ResolveReportFileTests(unittest.TestCase):
    """Tests for the shared _resolve_report_file helper."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.patcher = mock.patch(
            'wok.plugins.gingerbase.model.debugreports.config'
            '.get_debugreports_path',
            return_value=self.tmpdir)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmpdir)

    def test_resolve_found(self):
        _touch(os.path.join(self.tmpdir, 'myreport.tar.xz'))
        result = _resolve_report_file('myreport')
        self.assertEqual(os.path.basename(result), 'myreport.tar.xz')

    def test_resolve_not_found(self):
        self.assertRaises(NotFoundError, _resolve_report_file, 'nosuch')

    def test_resolve_multiple_extensions(self):
        _touch(os.path.join(self.tmpdir, 'dup.tar.xz'))
        _touch(os.path.join(self.tmpdir, 'dup.tar.gz'))
        result = _resolve_report_file('dup')
        self.assertIn('dup.tar.', result)


class GetListTests(unittest.TestCase):
    """Tests for DebugReportsModel.get_list."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.patcher = mock.patch(
            'wok.plugins.gingerbase.model.debugreports.config'
            '.get_debugreports_path',
            return_value=self.tmpdir)
        self.patcher.start()
        # DebugReportsModel.__init__ needs objstore and TaskModel;
        # we only test get_list which doesn't use them.
        with mock.patch.object(DebugReportsModel, '__init__',
                               lambda self, **kw: None):
            self.model = DebugReportsModel()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmpdir)

    def test_empty_directory(self):
        self.assertEqual(self.model.get_list(), [])

    def test_with_files(self):
        _touch(os.path.join(self.tmpdir, 'alpha.tar.xz'))
        _touch(os.path.join(self.tmpdir, 'beta.txt'))
        result = self.model.get_list()
        self.assertIn('alpha', result)
        self.assertIn('beta', result)
        self.assertEqual(len(result), 2)

    def test_ignores_extensionless_files(self):
        _touch(os.path.join(self.tmpdir, 'noext'))
        self.assertEqual(self.model.get_list(), [])


class LookupTests(unittest.TestCase):
    """Tests for DebugReportModel.lookup."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.patcher = mock.patch(
            'wok.plugins.gingerbase.model.debugreports.config'
            '.get_debugreports_path',
            return_value=self.tmpdir)
        self.patcher.start()
        self.model = DebugReportModel()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmpdir)

    def test_lookup_existing(self):
        fpath = os.path.join(self.tmpdir, 'testreport.tar.xz')
        _touch(fpath, 'data')
        info = self.model.lookup('testreport')
        self.assertIn('testreport.tar.xz', info['uri'])
        self.assertIn('plugins/gingerbase/data/debugreports', info['uri'])
        self.assertRegex(info['ctime'], r'\d{4}-\d{2}-\d{2}-\d{2}:\d{2}:\d{2}')

    def test_lookup_nonexistent(self):
        self.assertRaises(NotFoundError, self.model.lookup, 'ghost')


class UpdateTests(unittest.TestCase):
    """Tests for DebugReportModel.update (rename)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.patcher = mock.patch(
            'wok.plugins.gingerbase.model.debugreports.config'
            '.get_debugreports_path',
            return_value=self.tmpdir)
        self.patcher.start()
        self.model = DebugReportModel()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmpdir)

    def test_rename_success(self):
        _touch(os.path.join(self.tmpdir, 'old.tar.xz'))
        result = self.model.update('old', {'name': 'new'})
        self.assertEqual(result, 'new')
        self.assertFalse(os.path.exists(
            os.path.join(self.tmpdir, 'old.tar.xz')))
        self.assertTrue(os.path.exists(
            os.path.join(self.tmpdir, 'new.tar.xz')))

    def test_rename_target_exists(self):
        _touch(os.path.join(self.tmpdir, 'src.tar.xz'))
        _touch(os.path.join(self.tmpdir, 'dst.tar.xz'))
        self.assertRaises(InvalidParameter,
                          self.model.update, 'src', {'name': 'dst'})
        # Source should still exist after failed rename
        self.assertTrue(os.path.exists(
            os.path.join(self.tmpdir, 'src.tar.xz')))

    def test_rename_source_missing(self):
        self.assertRaises(NotFoundError,
                          self.model.update, 'nosuch', {'name': 'new'})


class DeleteTests(unittest.TestCase):
    """Tests for DebugReportModel.delete."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.patcher = mock.patch(
            'wok.plugins.gingerbase.model.debugreports.config'
            '.get_debugreports_path',
            return_value=self.tmpdir)
        self.patcher.start()
        self.model = DebugReportModel()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmpdir)

    def test_delete_existing(self):
        fpath = os.path.join(self.tmpdir, 'todelete.txt')
        _touch(fpath)
        self.model.delete('todelete')
        self.assertFalse(os.path.exists(fpath))

    def test_delete_nonexistent(self):
        self.assertRaises(NotFoundError, self.model.delete, 'ghost')


class GenerateUniqueNameTests(unittest.TestCase):
    """Tests for DebugReportsModel._generate_unique_name."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.patcher = mock.patch(
            'wok.plugins.gingerbase.model.debugreports.config'
            '.get_debugreports_path',
            return_value=self.tmpdir)
        self.patcher.start()
        with mock.patch.object(DebugReportsModel, '__init__',
                               lambda self, **kw: None):
            self.model = DebugReportsModel()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmpdir)

    def test_unique_name_format(self):
        name = self.model._generate_unique_name()
        self.assertTrue(name.startswith('report-'))
        # Should be parseable as a millisecond timestamp
        ts_part = name.split('report-')[1]
        int(ts_part)  # should not raise

    def test_unique_name_avoids_collision(self):
        # Pre-create a file whose name matches the expected timestamp
        ts = str(int(time.time() * 1000))
        colliding_name = 'report-' + ts
        _touch(os.path.join(self.tmpdir, colliding_name + '.txt'))

        with mock.patch('wok.plugins.gingerbase.model.debugreports.time') \
                as mock_time:
            mock_time.time.return_value = int(ts) / 1000.0
            name = self.model._generate_unique_name()

        self.assertNotEqual(name, colliding_name)
        self.assertTrue(name.startswith(colliding_name + '-'))


class LifecycleTests(unittest.TestCase):
    """End-to-end lifecycle: create file, lookup, rename, delete."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.patcher = mock.patch(
            'wok.plugins.gingerbase.model.debugreports.config'
            '.get_debugreports_path',
            return_value=self.tmpdir)
        self.patcher.start()
        self.report_model = DebugReportModel()
        with mock.patch.object(DebugReportsModel, '__init__',
                               lambda self, **kw: None):
            self.reports_model = DebugReportsModel()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmpdir)

    def test_full_lifecycle(self):
        # Simulate a generated report landing on disk
        _touch(os.path.join(self.tmpdir, 'lifecycle-test.tar.xz'), 'payload')

        # List should contain it
        names = self.reports_model.get_list()
        self.assertIn('lifecycle-test', names)

        # Lookup should return valid info
        info = self.report_model.lookup('lifecycle-test')
        self.assertIn('lifecycle-test.tar.xz', info['uri'])

        # Rename
        self.report_model.update('lifecycle-test', {'name': 'renamed'})
        self.assertIn('renamed', self.reports_model.get_list())
        self.assertNotIn('lifecycle-test', self.reports_model.get_list())

        # Lookup renamed
        info = self.report_model.lookup('renamed')
        self.assertIn('renamed.tar.xz', info['uri'])

        # Delete
        self.report_model.delete('renamed')
        self.assertEqual(self.reports_model.get_list(), [])

        # Verify deleted report is gone
        self.assertRaises(NotFoundError, self.report_model.lookup, 'renamed')

    def test_delete_then_recreate(self):
        _touch(os.path.join(self.tmpdir, 'reuse.txt'), 'v1')
        self.report_model.delete('reuse')

        # Re-create with same name should work
        _touch(os.path.join(self.tmpdir, 'reuse.txt'), 'v2')
        info = self.report_model.lookup('reuse')
        self.assertIn('reuse.txt', info['uri'])

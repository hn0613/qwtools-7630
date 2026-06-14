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

"""
Unit tests for the debug report lifecycle management.

These tests cover the shared helpers, model operations (lookup / rename /
delete), sosreport collection, generation cleanup, and edge cases that
the integration tests in test_rest.py do not reach.
"""

import os
import shutil
import tempfile
import unittest

import mock
from wok.exception import InvalidParameter
from wok.exception import NotFoundError
from wok.exception import OperationFailed

from wok.plugins.gingerbase.model.debugreports import (
    _extract_extension,
    _find_report_file,
    _get_all_report_names,
    _validate_name,
    DebugReportsModel,
    DebugReportModel,
    delete_the_sosreport_md5_file,
    sosreport_collection,
)


# ---------------------------------------------------------------------------
# Shared helper tests
# ---------------------------------------------------------------------------

class TestValidateName(unittest.TestCase):
    def test_valid_name(self):
        _validate_name('my-report-1')

    def test_valid_name_with_underscore(self):
        _validate_name('my_report_1')

    def test_valid_name_digits_only(self):
        _validate_name('12345')

    def test_empty_string(self):
        with self.assertRaises(InvalidParameter):
            _validate_name('')

    def test_none(self):
        with self.assertRaises(InvalidParameter):
            _validate_name(None)

    def test_whitespace_only(self):
        with self.assertRaises(InvalidParameter):
            _validate_name('   ')

    def test_special_chars(self):
        with self.assertRaises(InvalidParameter):
            _validate_name('report@name')

    def test_spaces(self):
        with self.assertRaises(InvalidParameter):
            _validate_name('my report')

    def test_slash(self):
        with self.assertRaises(InvalidParameter):
            _validate_name('path/name')


class TestExtractExtension(unittest.TestCase):
    def test_tar_xz(self):
        self.assertEqual(_extract_extension('report.tar.xz'), '.tar.xz')

    def test_single_extension(self):
        self.assertEqual(_extract_extension('report.txt'), '.txt')

    def test_no_extension(self):
        self.assertEqual(_extract_extension('report'), '')

    def test_tgz(self):
        self.assertEqual(_extract_extension('archive.tgz'), '.tgz')


# ---------------------------------------------------------------------------
# Filesystem-backed tests
# ---------------------------------------------------------------------------

class FilesystemTestBase(unittest.TestCase):
    """Base class that provides a real tempdir as the debugreports storage."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._path_patcher = mock.patch(
            'wok.plugins.gingerbase.model.debugreports'
            '.config.get_debugreports_path',
            return_value=self.tmpdir)
        self._path_patcher.start()

    def tearDown(self):
        self._path_patcher.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _create_file(self, name):
        """Create an empty report file in the temp directory."""
        path = os.path.join(self.tmpdir, name)
        with open(path, 'w') as f:
            f.write('test')
        return path


class TestFindReportFile(FilesystemTestBase):
    def test_finds_existing(self):
        self._create_file('my-report.tar.xz')
        result = _find_report_file('my-report')
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith('my-report.tar.xz'))

    def test_returns_none_when_missing(self):
        result = _find_report_file('nonexistent')
        self.assertIsNone(result)

    def test_finds_txt_extension(self):
        self._create_file('mock-report.txt')
        result = _find_report_file('mock-report')
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith('mock-report.txt'))

    def test_name_with_dots_is_escaped(self):
        """glob.escape ensures dots in the name are treated literally."""
        self._create_file('my.report.tar.xz')
        result = _find_report_file('my.report')
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith('my.report.tar.xz'))


class TestGetAllReportNames(FilesystemTestBase):
    def test_empty_directory(self):
        self.assertEqual(_get_all_report_names(), [])

    def test_lists_sorted_names(self):
        self._create_file('report-b.tar.xz')
        self._create_file('report-a.txt')
        names = _get_all_report_names()
        self.assertEqual(names, ['report-a', 'report-b'])

    def test_ignores_directories(self):
        os.makedirs(os.path.join(self.tmpdir, 'subdir.tmp'))
        self._create_file('real.txt')
        names = _get_all_report_names()
        self.assertEqual(names, ['real'])

    def test_ignores_extensionless_files(self):
        self._create_file('report-a.tar.xz')
        with open(os.path.join(self.tmpdir, 'noext'), 'w') as f:
            f.write('test')
        names = _get_all_report_names()
        self.assertEqual(names, ['report-a'])


# ---------------------------------------------------------------------------
# Model operation tests
# ---------------------------------------------------------------------------

class TestLookup(FilesystemTestBase):
    def test_returns_uri_and_ctime(self):
        self._create_file('my-report.tar.xz')
        model = DebugReportModel()
        info = model.lookup('my-report')
        self.assertIn('uri', info)
        self.assertIn('ctime', info)
        self.assertIn('my-report.tar.xz', info['uri'])

    def test_not_found(self):
        model = DebugReportModel()
        with self.assertRaises(NotFoundError):
            model.lookup('nonexistent')


class TestUpdate(FilesystemTestBase):
    def test_rename_success(self):
        self._create_file('old-name.tar.xz')
        model = DebugReportModel()
        result = model.update('old-name', {'name': 'new-name'})
        self.assertEqual(result, 'new-name')
        self.assertIsNone(_find_report_file('old-name'))
        self.assertIsNotNone(_find_report_file('new-name'))
        self.assertTrue(
            _find_report_file('new-name').endswith('new-name.tar.xz'))

    def test_rename_preserves_extension_when_name_in_ext(self):
        """Regression: report named 'tar' stored as 'tar.tar.xz'.

        The old str.replace approach would produce 'new.new.xz'.
        """
        self._create_file('tar.tar.xz')
        model = DebugReportModel()
        result = model.update('tar', {'name': 'new'})
        self.assertEqual(result, 'new')
        self.assertTrue(
            _find_report_file('new').endswith('new.tar.xz'))

    def test_rename_not_found(self):
        model = DebugReportModel()
        with self.assertRaises(NotFoundError):
            model.update('nonexistent', {'name': 'new'})

    def test_rename_to_existing_raises(self):
        self._create_file('report-a.tar.xz')
        self._create_file('report-b.tar.xz')
        model = DebugReportModel()
        with self.assertRaises(InvalidParameter):
            model.update('report-a', {'name': 'report-b'})

    def test_rename_preserves_tgz(self):
        self._create_file('s390-report.tgz')
        model = DebugReportModel()
        result = model.update('s390-report', {'name': 'renamed'})
        self.assertEqual(result, 'renamed')
        self.assertTrue(
            _find_report_file('renamed').endswith('renamed.tgz'))


class TestDelete(FilesystemTestBase):
    def test_delete_success(self):
        self._create_file('my-report.tar.xz')
        model = DebugReportModel()
        model.delete('my-report')
        self.assertIsNone(_find_report_file('my-report'))

    def test_delete_not_found(self):
        model = DebugReportModel()
        with self.assertRaises(NotFoundError):
            model.delete('nonexistent')

    def test_list_after_delete(self):
        self._create_file('report-a.tar.xz')
        self._create_file('report-b.txt')
        model = DebugReportModel()
        model.delete('report-a')
        names = _get_all_report_names()
        self.assertEqual(names, ['report-b'])


class TestGetList(FilesystemTestBase):
    def test_returns_sorted_names(self):
        self._create_file('b-report.tar.xz')
        self._create_file('a-report.txt')
        names = _get_all_report_names()
        self.assertEqual(names, ['a-report', 'b-report'])


# ---------------------------------------------------------------------------
# sosreport_collection tests
# ---------------------------------------------------------------------------

class TestSosreportCollection(unittest.TestCase):
    @mock.patch(
        'wok.plugins.gingerbase.model.debugreports.glob.glob')
    @mock.patch(
        'wok.plugins.gingerbase.model.debugreports.run_command')
    def test_success(self, mock_run, mock_glob):
        mock_run.return_value = ('output\n', '', 0)
        mock_glob.return_value = [
            '/var/tmp/sosreport-test-20240101.tar.xz']
        result = sosreport_collection('test')
        self.assertEqual(
            result, '/var/tmp/sosreport-test-20240101.tar.xz')

    @mock.patch(
        'wok.plugins.gingerbase.model.debugreports.glob.glob')
    @mock.patch(
        'wok.plugins.gingerbase.model.debugreports.run_command')
    def test_underscore_allowed(self, mock_run, mock_glob):
        """Underscores in name should NOT be rejected."""
        mock_run.return_value = ('output\n', '', 0)
        mock_glob.return_value = [
            '/var/tmp/sosreport-my_report-20240101.tar.xz']
        result = sosreport_collection('my_report')
        self.assertEqual(
            result, '/var/tmp/sosreport-my_report-20240101.tar.xz')

    @mock.patch(
        'wok.plugins.gingerbase.model.debugreports.run_command')
    def test_command_failure_raises(self, mock_run):
        mock_run.return_value = ('', 'error msg', 1)
        with self.assertRaises(OperationFailed):
            sosreport_collection('test')

    @mock.patch(
        'wok.plugins.gingerbase.model.debugreports.glob.glob')
    @mock.patch(
        'wok.plugins.gingerbase.model.debugreports.run_command')
    def test_no_output_file_raises(self, mock_run, mock_glob):
        mock_run.return_value = ('output\n', '', 0)
        mock_glob.return_value = []
        with self.assertRaises(OperationFailed):
            sosreport_collection('test')

    @mock.patch(
        'wok.plugins.gingerbase.model.debugreports.glob.glob')
    @mock.patch(
        'wok.plugins.gingerbase.model.debugreports.run_command')
    def test_empty_output_raises(self, mock_run, mock_glob):
        """When sosreport produces no output lines, still raises."""
        mock_run.return_value = ('', '', 0)
        with self.assertRaises(OperationFailed):
            sosreport_collection('test')


# ---------------------------------------------------------------------------
# Generation cleanup tests (try/finally)
# ---------------------------------------------------------------------------

class TestSosreportGenerateCleanup(FilesystemTestBase):
    @mock.patch(
        'wok.plugins.gingerbase.model.debugreports.delete_the_sosreport_md5_file')
    @mock.patch(
        'wok.plugins.gingerbase.model.debugreports.shutil.move')
    @mock.patch(
        'wok.plugins.gingerbase.model.debugreports.sosreport_collection')
    def test_success_calls_cb_and_moves(self, mock_collection,
                                        mock_move, mock_delete_md5):
        mock_collection.return_value = (
            '/var/tmp/sosreport-test-20240101.tar.xz')
        cb = mock.MagicMock()
        DebugReportsModel.sosreport_generate(cb, 'test')
        cb.assert_called_once_with('OK', True)
        mock_move.assert_called_once()
        mock_delete_md5.assert_called_once()

    @mock.patch(
        'wok.plugins.gingerbase.model.debugreports.shutil.move')
    @mock.patch(
        'wok.plugins.gingerbase.model.debugreports.sosreport_collection')
    def test_failure_cleans_up_orphans(self, mock_collection, mock_move):
        """If move fails, intermediate files should be cleaned up."""
        sosreport_path = os.path.join(
            self.tmpdir, 'sosreport-fail-20240101.tar.xz')
        md5_path = sosreport_path + '.md5'
        with open(sosreport_path, 'w') as f:
            f.write('fake')
        with open(md5_path, 'w') as f:
            f.write('abc')

        mock_collection.return_value = sosreport_path
        mock_move.side_effect = OSError('disk full')

        cb = mock.MagicMock()
        with self.assertRaises(OSError):
            DebugReportsModel.sosreport_generate(cb, 'fail')

        # Both files should be cleaned up by the finally block
        self.assertFalse(os.path.exists(sosreport_path))
        self.assertFalse(os.path.exists(md5_path))


class TestDebugreportGenerateCleanup(FilesystemTestBase):
    @mock.patch(
        'wok.plugins.gingerbase.model.debugreports.run_command')
    @mock.patch(
        'wok.plugins.gingerbase.model.debugreports.sosreport_collection')
    def test_dbginfo_failure_cleans_sosreport(self, mock_collection,
                                              mock_run):
        """If dbginfo.sh fails, sosreport intermediates are cleaned up."""
        sosreport_path = os.path.join(
            self.tmpdir, 'sosreport-test-20240101.tar.xz')
        md5_path = sosreport_path + '.md5'
        with open(sosreport_path, 'w') as f:
            f.write('fake')
        with open(md5_path, 'w') as f:
            f.write('abc')

        mock_collection.return_value = sosreport_path
        # dbginfo.sh fails
        mock_run.return_value = ('', 'error', 1)

        cb = mock.MagicMock()
        with self.assertRaises(OperationFailed):
            DebugReportsModel.debugreport_generate(cb, 'test')

        self.assertFalse(os.path.exists(sosreport_path))
        self.assertFalse(os.path.exists(md5_path))

    @mock.patch(
        'wok.plugins.gingerbase.model.debugreports.shutil.move')
    @mock.patch(
        'wok.plugins.gingerbase.model.debugreports.run_command')
    @mock.patch(
        'wok.plugins.gingerbase.model.debugreports.glob.glob')
    @mock.patch(
        'wok.plugins.gingerbase.model.debugreports.sosreport_collection')
    def test_tar_failure_cleans_all(self, mock_collection, mock_glob,
                                    mock_run, mock_move):
        """If tar compression fails, all intermediates are cleaned up."""
        sosreport_path = os.path.join(
            self.tmpdir, 'sosreport-test-20240101.tar.xz')
        md5_path = sosreport_path + '.md5'
        dbginfo_path = os.path.join(
            self.tmpdir, 'DBGINFO-2024-01-01-00-00-00-000.tgz')
        with open(sosreport_path, 'w') as f:
            f.write('fake')
        with open(md5_path, 'w') as f:
            f.write('abc')
        with open(dbginfo_path, 'w') as f:
            f.write('fake')

        mock_collection.return_value = sosreport_path
        # First call (dbginfo.sh) succeeds, second call (tar) fails
        mock_run.side_effect = [
            ('some output\n', '', 0),   # dbginfo.sh
            ('', 'tar error', 1),       # tar
        ]
        mock_glob.return_value = [dbginfo_path]

        cb = mock.MagicMock()
        with self.assertRaises(OperationFailed):
            DebugReportsModel.debugreport_generate(cb, 'test')

        self.assertFalse(os.path.exists(sosreport_path))
        self.assertFalse(os.path.exists(md5_path))
        self.assertFalse(os.path.exists(dbginfo_path))


# ---------------------------------------------------------------------------
# delete_the_sosreport_md5_file tests
# ---------------------------------------------------------------------------

class TestDeleteMd5File(FilesystemTestBase):
    def test_deletes_existing(self):
        md5_path = os.path.join(self.tmpdir, 'test.md5')
        with open(md5_path, 'w') as f:
            f.write('abc123')
        delete_the_sosreport_md5_file(md5_path)
        self.assertFalse(os.path.exists(md5_path))

    def test_handles_missing_file(self):
        """Should not raise when the md5 file does not exist."""
        delete_the_sosreport_md5_file(
            os.path.join(self.tmpdir, 'nonexistent.md5'))


if __name__ == '__main__':
    unittest.main()

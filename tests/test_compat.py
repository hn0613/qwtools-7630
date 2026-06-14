# -*- coding: utf-8 -*-
#
# Project Ginger Base
#
# Copyright IBM Corp, 2015-2016
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
Regression tests for the compatibility boundary layer (compat.py).

Covers three categories:
1. Normal environment: architecture classification, psutil modern API
2. Information missing: psutil APIs unavailable, capability tools absent
3. Capability limited: only partial tools available, zypper priority
"""
import unittest
from collections import namedtuple

import mock

from wok.plugins.gingerbase import compat


class TestArchClassification(unittest.TestCase):
    """Verify architecture family classification for known machine strings."""

    @mock.patch.object(compat, 'ARCH_FAMILY', 'x86_64')
    def test_is_x86_true(self):
        self.assertTrue(compat.is_x86())
        self.assertFalse(compat.is_s390x())
        self.assertFalse(compat.is_ppc())

    @mock.patch.object(compat, 'ARCH_FAMILY', 's390x')
    def test_is_s390x_true(self):
        self.assertTrue(compat.is_s390x())
        self.assertFalse(compat.is_x86())
        self.assertFalse(compat.is_ppc())

    @mock.patch.object(compat, 'ARCH_FAMILY', 'ppc64')
    def test_is_ppc_true(self):
        self.assertTrue(compat.is_ppc())
        self.assertFalse(compat.is_x86())
        self.assertFalse(compat.is_s390x())


class TestPsutilGetTotalPhymem(unittest.TestCase):
    """Test psutil physical memory detection across API versions."""

    def test_modern_psutil(self):
        """virtual_memory().total used when available."""
        VmemResult = namedtuple('VmemResult', ['total'])
        with mock.patch('wok.plugins.gingerbase.compat.psutil') as mock_psutil:
            mock_psutil.virtual_memory.return_value = VmemResult(total=8192)
            result = compat.get_total_phymem()
            self.assertEqual(result, 8192)

    def test_legacy_psutil(self):
        """phymem_usage().total used when virtual_memory is missing."""
        PhymemResult = namedtuple('PhymemResult', ['total'])
        with mock.patch('wok.plugins.gingerbase.compat.psutil') as mock_psutil:
            del mock_psutil.virtual_memory
            mock_psutil.phymem_usage.return_value = PhymemResult(total=4096)
            result = compat.get_total_phymem()
            self.assertEqual(result, 4096)

    def test_no_api(self):
        """Returns None when neither API is available."""
        with mock.patch('wok.plugins.gingerbase.compat.psutil') as mock_psutil:
            del mock_psutil.virtual_memory
            del mock_psutil.phymem_usage
            result = compat.get_total_phymem()
            self.assertIsNone(result)


class TestPsutilGetOnlineCpus(unittest.TestCase):
    """Test psutil CPU count detection across API versions."""

    def test_modern_psutil(self):
        """cpu_count() used when available."""
        with mock.patch('wok.plugins.gingerbase.compat.psutil') as mock_psutil:
            mock_psutil.cpu_count.return_value = 4
            result = compat.get_online_cpus()
            self.assertEqual(result, 4)

    def test_legacy_num_cpus(self):
        """NUM_CPUS used when cpu_count is missing."""
        with mock.patch('wok.plugins.gingerbase.compat.psutil') as mock_psutil:
            del mock_psutil.cpu_count
            mock_psutil.NUM_CPUS = 2
            result = compat.get_online_cpus()
            self.assertEqual(result, 2)

    def test_no_api(self):
        """Returns None when no CPU count API is available."""
        with mock.patch('wok.plugins.gingerbase.compat.psutil') as mock_psutil:
            del mock_psutil.cpu_count
            del mock_psutil.NUM_CPUS
            del mock_psutil._psplatform
            result = compat.get_online_cpus()
            self.assertIsNone(result)

    def test_cpu_count_returns_zero(self):
        """Falls through when cpu_count returns 0 or None."""
        with mock.patch('wok.plugins.gingerbase.compat.psutil') as mock_psutil:
            mock_psutil.cpu_count.return_value = 0
            mock_psutil.NUM_CPUS = 8
            result = compat.get_online_cpus()
            self.assertEqual(result, 8)


class TestPsutilGetNetIoCounters(unittest.TestCase):
    """Test psutil network I/O counter detection across API versions."""

    def test_modern_psutil(self):
        """net_io_counters() used when available."""
        with mock.patch('wok.plugins.gingerbase.compat.psutil') as mock_psutil:
            mock_psutil.net_io_counters.return_value = {'eth0': 'data'}
            result = compat.get_net_io_counters(per_nic=True)
            self.assertEqual(result, {'eth0': 'data'})

    def test_legacy_psutil(self):
        """network_io_counters() used when net_io_counters is missing."""
        with mock.patch('wok.plugins.gingerbase.compat.psutil') as mock_psutil:
            del mock_psutil.net_io_counters
            mock_psutil.network_io_counters.return_value = {'eth0': 'data'}
            result = compat.get_net_io_counters(per_nic=True)
            self.assertEqual(result, {'eth0': 'data'})

    def test_no_api(self):
        """Returns None when neither API is available."""
        with mock.patch('wok.plugins.gingerbase.compat.psutil') as mock_psutil:
            del mock_psutil.net_io_counters
            del mock_psutil.network_io_counters
            result = compat.get_net_io_counters(per_nic=True)
            self.assertIsNone(result)


class TestProbePackageManager(unittest.TestCase):
    """Test package manager detection priority and zypper fix."""

    def _make_import_side_effect(self, available_modules):
        """Create a side_effect for __import__ that only allows
        specified modules to import successfully."""
        real_import = __builtins__.__import__ if hasattr(
            __builtins__, '__import__') else __import__

        def fake_import(name, *args, **kwargs):
            if name in available_modules:
                return real_import(name, *args, **kwargs)
            raise ImportError('No module named %s' % name)
        return fake_import

    @mock.patch('subprocess.call', return_value=1)
    def test_dnf_detected_first(self, _mock_subproc):
        """dnf takes priority over yum, apt, portage, zypper."""
        with mock.patch('builtins.__import__',
                        side_effect=self._make_import_side_effect(
                            {'dnf', 'yum'})):
            result = compat.probe_package_manager()
            self.assertTrue(result.available)
            self.assertEqual(result.tool, 'dnf')

    @mock.patch('subprocess.call', return_value=0)
    def test_zypper_does_not_override_dnf(self, _mock_subproc):
        """Regression: zypper must NOT override a detected Python manager."""
        with mock.patch('builtins.__import__',
                        side_effect=self._make_import_side_effect({'dnf'})):
            result = compat.probe_package_manager()
            self.assertTrue(result.available)
            self.assertEqual(result.tool, 'dnf')
            # subprocess.call should not be called when dnf is found
            _mock_subproc.assert_not_called()

    @mock.patch('subprocess.call', return_value=0)
    def test_zypper_fallback(self, _mock_subproc):
        """zypper selected only when no Python manager is importable."""
        with mock.patch('builtins.__import__',
                        side_effect=self._make_import_side_effect(set())):
            result = compat.probe_package_manager()
            self.assertTrue(result.available)
            self.assertEqual(result.tool, 'zypper')

    @mock.patch('subprocess.call', side_effect=OSError('not found'))
    def test_nothing_available(self, _mock_subproc):
        """Returns unavailable when no manager is found."""
        with mock.patch('builtins.__import__',
                        side_effect=self._make_import_side_effect(set())):
            result = compat.probe_package_manager()
            self.assertFalse(result.available)
            self.assertIsNone(result.tool)


class TestProbeRepoManager(unittest.TestCase):
    """Test repository manager detection."""

    def test_dnf_maps_to_yum(self):
        """dnf import maps to tool type 'yum'."""
        with mock.patch('builtins.__import__', return_value=mock.MagicMock()):
            result = compat.probe_repo_manager()
            self.assertTrue(result.available)
            self.assertEqual(result.tool, 'yum')

    def test_nothing_available(self):
        """Returns unavailable when no repo manager is importable."""
        def fail_import(name, *args, **kwargs):
            raise ImportError()
        with mock.patch('builtins.__import__', side_effect=fail_import):
            result = compat.probe_repo_manager()
            self.assertFalse(result.available)
            self.assertIsNone(result.tool)


class TestProbeReportTool(unittest.TestCase):
    """Test debug report tool detection."""

    @mock.patch('subprocess.call', return_value=0)
    def test_dbginfo_priority(self, mock_call):
        """dbginfo.sh is checked first."""
        result = compat.probe_report_tool()
        self.assertTrue(result.available)
        self.assertEqual(result.tool, 'dbginfo')

    @mock.patch('subprocess.call')
    def test_sosreport_fallback(self, mock_call):
        """sosreport used when dbginfo.sh fails."""
        mock_call.side_effect = [1, 0]  # dbginfo fails, sosreport succeeds
        result = compat.probe_report_tool()
        self.assertTrue(result.available)
        self.assertEqual(result.tool, 'sosreport')

    @mock.patch('subprocess.call', side_effect=OSError('not found'))
    def test_nothing_available(self, _mock_call):
        """Returns unavailable when no tool is found."""
        result = compat.probe_report_tool()
        self.assertFalse(result.available)
        self.assertIsNone(result.tool)


class TestProbeSmt(unittest.TestCase):
    """Test SMT capability probe."""

    @mock.patch.object(compat, 'ARCH_FAMILY', 's390x')
    def test_s390x_available(self):
        result = compat.probe_smt()
        self.assertTrue(result.available)

    @mock.patch.object(compat, 'ARCH_FAMILY', 'x86_64')
    def test_non_s390x_unavailable(self):
        result = compat.probe_smt()
        self.assertFalse(result.available)
        self.assertIn('s390x', result.detail)


class TestCapabilityResultStructure(unittest.TestCase):
    """Verify CapabilityResult namedtuple contract."""

    def test_fields(self):
        r = compat.CapabilityResult(available=True, tool='test', detail=None)
        self.assertTrue(r.available)
        self.assertEqual(r.tool, 'test')
        self.assertIsNone(r.detail)

    def test_unavailable(self):
        r = compat.CapabilityResult(available=False, tool=None,
                                     detail='reason')
        self.assertFalse(r.available)
        self.assertIsNone(r.tool)
        self.assertEqual(r.detail, 'reason')

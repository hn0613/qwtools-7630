# -*- coding: utf-8 -*-
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
Regression tests for the sysinfo compatibility layer.

Three categories of scenarios are covered:
1. Normal environment -- all sources available, values make sense.
2. Information missing -- files not found, commands fail.
3. Capability limited -- psutil APIs absent, lscpu broken, partial data.
"""

import io
import os
import unittest

import mock
from mock import MagicMock, patch


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

# We import the module under test via its package path.  Because the
# actual wok framework may not be present in every test environment,
# we fall back to importing the file directly when the package path
# is not available.
try:
    from wok.plugins.gingerbase import sysinfo
except ImportError:
    import sys
    _here = os.path.dirname(os.path.abspath(__file__))
    _root = os.path.dirname(_here)
    if _root not in sys.path:
        sys.path.insert(0, _root)
    import sysinfo


# The ``open`` builtin as seen from sysinfo's module namespace.
# Patching ``sysinfo.open`` works on both Python 2 and Python 3
# without needing to know whether the builtins module is called
# ``__builtin__`` or ``builtins``.
_SYSINFO_OPEN = 'wok.plugins.gingerbase.sysinfo.open' \
    if hasattr(sysinfo, '__package__') and sysinfo.__package__ \
    else 'sysinfo.open'

# Similarly for run_command
_SYSINFO_RUN_CMD = 'wok.plugins.gingerbase.sysinfo.run_command' \
    if hasattr(sysinfo, '__package__') and sysinfo.__package__ \
    else 'sysinfo.run_command'


def _open_fake(content):
    """Return a side_effect function that mocks ``open()``.

    Usage::

        @patch(_SYSINFO_OPEN, side_effect=_open_fake(SOME_CONTENT))
        def test_something(self, mock_open):
            ...

    The returned callable produces a ``StringIO`` object that
    supports both ``with`` statement and line-by-line iteration,
    which is required by the parsing functions in sysinfo.
    """
    def _side_effect(*args, **kwargs):
        return io.StringIO(content)
    return _side_effect


# Sample /proc/cpuinfo content for x86
_X86_CPUINFO = u"""\
processor\t: 0
vendor_id\t: GenuineIntel
cpu family\t: 6
model\t\t: 142
model name\t: Intel(R) Core(TM) i7-8550U CPU @ 1.80GHz
stepping\t: 10
microcode\t: 0xde
cpu MHz\t\t: 2000.000
cache size\t: 8192 KB
"""

# Sample /proc/cpuinfo content for PowerPC
_PPC_CPUINFO = u"""\
processor\t: 0
cpu\t\t: POWER8 (raw), altivec supported
clock\t\t: 3425.000000MHz
revision\t: 2.0 (pvr 004d 0200)

processor\t: 1
cpu\t\t: POWER8 (raw), altivec supported
clock\t\t: 3425.000000MHz
revision\t: 2.0 (pvr 004d 0200)
"""

# Sample /proc/sysinfo content for s390x
_S390X_SYSINFO = u"""\
Manufacturer:         IBM
Type:                 2964
Model:                702              N96
LPAR Number:          42
LPAR Name:            TESTLPAR
LPAR CPUs Dedicated:  4
LPAR CPUs Shared:     0
"""


def _make_mock_lscpu(total_cpus=4, sockets=1, cores=2, threads=2,
                      hypervisor=None, hypervisor_vendor=None,
                      books=None, raise_on_init=False):
    """Build a mock that quacks like :class:`LsCpu`."""
    if raise_on_init:
        raise Exception('lscpu binary not found')

    lscpu = MagicMock()
    lscpu.get_total_cpus.return_value = total_cpus
    lscpu.get_sockets.return_value = sockets
    lscpu.get_cores_per_socket.return_value = cores
    lscpu.get_threads_per_core.return_value = threads
    lscpu.get_hypervisor.return_value = hypervisor
    lscpu.get_hypervisor_vendor.return_value = hypervisor_vendor
    lscpu.get_books.return_value = books
    return lscpu


# ==================================================================
# Category 1: Normal environment
# ==================================================================


class TestArchDetection(unittest.TestCase):
    """Architecture detection should always return a known family."""

    def test_get_arch_type_returns_known_value(self):
        self.assertIn(sysinfo.get_arch_type(),
                      ('s390x', 'ppc', 'x86'))

    def test_is_s390x_is_bool(self):
        self.assertIsInstance(sysinfo.IS_S390X, bool)

    def test_is_ppc_is_bool(self):
        self.assertIsInstance(sysinfo.IS_PPC, bool)


class TestOnlineCpuCount(unittest.TestCase):
    """get_online_cpu_count should return a positive int on a real system."""

    def test_returns_positive_int(self):
        count = sysinfo.get_online_cpu_count()
        # On any real system with psutil installed this should succeed
        self.assertIsNotNone(count)
        self.assertGreater(count, 0)


class TestGetCpuCounts(unittest.TestCase):

    def test_returns_expected_keys(self):
        lscpu = _make_mock_lscpu(total_cpus=8)
        result = sysinfo.get_cpu_counts(lscpu)
        self.assertIn('online', result)
        self.assertIn('offline', result)

    def test_values_are_ints(self):
        lscpu = _make_mock_lscpu(total_cpus=8)
        result = sysinfo.get_cpu_counts(lscpu)
        self.assertIsInstance(result['online'], int)
        self.assertIsInstance(result['offline'], int)

    def test_offline_is_non_negative(self):
        lscpu = _make_mock_lscpu(total_cpus=4)
        result = sysinfo.get_cpu_counts(lscpu)
        self.assertGreaterEqual(result['offline'], 0)


class TestGetMemoryInfo(unittest.TestCase):

    def test_returns_expected_keys(self):
        result = sysinfo.get_memory_info()
        self.assertIn('online', result)
        self.assertIn('offline', result)

    def test_values_are_ints(self):
        result = sysinfo.get_memory_info()
        self.assertIsInstance(result['online'], int)
        self.assertIsInstance(result['offline'], int)

    def test_online_is_positive(self):
        result = sysinfo.get_memory_info()
        self.assertGreater(result['online'], 0)


class TestDetectCpuModel(unittest.TestCase):

    def test_returns_string(self):
        result = sysinfo.detect_cpu_model()
        self.assertIsInstance(result, str)


class TestGetCpuThreadTopology(unittest.TestCase):

    def test_returns_expected_keys(self):
        lscpu = _make_mock_lscpu()
        result = sysinfo.get_cpu_thread_topology(lscpu)
        self.assertIn('sockets', result)
        self.assertIn('cores_per_socket', result)
        self.assertIn('threads_per_core', result)

    def test_values_from_lscpu(self):
        lscpu = _make_mock_lscpu(sockets=2, cores=4, threads=2)
        result = sysinfo.get_cpu_thread_topology(lscpu)
        self.assertEqual(result['sockets'], 2)
        self.assertEqual(result['cores_per_socket'], 4)
        self.assertEqual(result['threads_per_core'], 2)

    @patch.object(sysinfo, 'IS_S390X', True)
    def test_s390x_includes_books(self):
        lscpu = _make_mock_lscpu(books='4')
        result = sysinfo.get_cpu_thread_topology(lscpu)
        self.assertIn('books', result)
        self.assertEqual(result['books'], '4')


class TestDiscoverNetworkDevices(unittest.TestCase):

    def test_nics_returns_list(self):
        self.assertIsInstance(sysinfo.discover_nics(), list)

    def test_wlans_returns_list(self):
        self.assertIsInstance(sysinfo.discover_wlans(), list)


class TestGetNetIoCounters(unittest.TestCase):

    def test_returns_non_none_on_real_system(self):
        result = sysinfo.get_net_io_counters(True)
        self.assertIsNotNone(result)


# ==================================================================
# Category 2: Information missing
# ==================================================================


class TestCpuCountsNoPsutil(unittest.TestCase):
    """When every psutil CPU API is missing, fall back to lscpu total."""

    @patch.object(sysinfo, 'get_online_cpu_count', return_value=None)
    def test_falls_back_to_lscpu_total(self, _mock_online):
        lscpu = _make_mock_lscpu(total_cpus=16)
        result = sysinfo.get_cpu_counts(lscpu)
        self.assertEqual(result['online'], 16)
        self.assertEqual(result['offline'], 0)


class TestCpuCountsAllFail(unittest.TestCase):
    """When both psutil and lscpu fail, return zeros."""

    @patch.object(sysinfo, 'get_online_cpu_count', return_value=None)
    def test_zeros_when_everything_fails(self, _mock_online):
        lscpu = MagicMock()
        lscpu.get_total_cpus.side_effect = Exception('no lscpu')
        result = sysinfo.get_cpu_counts(lscpu)
        self.assertEqual(result['online'], 0)
        self.assertEqual(result['offline'], 0)


class TestMemoryInfoLsmemFails(unittest.TestCase):
    """On s390x, if lsmem command fails, memory values degrade to 0."""

    @patch.object(sysinfo, 'IS_S390X', True)
    @patch(_SYSINFO_RUN_CMD, return_value=('', 'error', 1))
    def test_returns_zeros(self, _mock_cmd):
        result = sysinfo.get_memory_info()
        self.assertEqual(result['online'], 0)
        self.assertEqual(result['offline'], 0)


class TestDetectCpuModelProcMissing(unittest.TestCase):
    """When /proc/cpuinfo is not readable, return empty string."""

    @patch(_SYSINFO_OPEN, side_effect=IOError('no such file'))
    @patch.object(sysinfo, 'IS_S390X', False)
    @patch.object(sysinfo, 'IS_PPC', False)
    def test_x86_returns_empty(self, _mock_open):
        result = sysinfo.detect_cpu_model()
        self.assertEqual(result, '')

    @patch(_SYSINFO_OPEN, side_effect=IOError('no such file'))
    @patch.object(sysinfo, 'IS_S390X', False)
    @patch.object(sysinfo, 'IS_PPC', True)
    def test_ppc_returns_empty(self, _mock_open):
        result = sysinfo.detect_cpu_model()
        self.assertEqual(result, '')


class TestParseS390xSysinfoMissing(unittest.TestCase):
    """When /proc/sysinfo is absent, return empty dict."""

    @patch(_SYSINFO_OPEN, side_effect=IOError('no such file'))
    def test_returns_empty_dict(self, _mock_open):
        result = sysinfo.parse_s390x_sysinfo()
        self.assertEqual(result, {})


class TestNetIoCountersNoPsutil(unittest.TestCase):
    """When psutil has neither net API, return None."""

    def test_returns_none(self):
        with patch.object(sysinfo, 'psutil') as mock_psutil:
            del mock_psutil.net_io_counters
            del mock_psutil.network_io_counters
            result = sysinfo.get_net_io_counters(True)
            self.assertIsNone(result)


# ==================================================================
# Category 3: Capability limited
# ==================================================================


class TestCpuThreadTopologyLscpuFails(unittest.TestCase):
    """When lscpu methods raise, topology fields degrade to defaults."""

    def test_degrades_to_zeros(self):
        lscpu = MagicMock()
        lscpu.get_sockets.side_effect = Exception('lscpu failed')
        lscpu.get_cores_per_socket.side_effect = Exception('lscpu failed')
        lscpu.get_threads_per_core.side_effect = Exception('lscpu failed')
        result = sysinfo.get_cpu_thread_topology(lscpu)
        self.assertEqual(result['sockets'], 0)
        self.assertEqual(result['cores_per_socket'], 0)
        self.assertEqual(result['threads_per_core'], 0)

    @patch.object(sysinfo, 'IS_S390X', True)
    def test_s390x_books_degrades_to_none(self):
        lscpu = MagicMock()
        lscpu.get_sockets.side_effect = Exception('fail')
        lscpu.get_cores_per_socket.side_effect = Exception('fail')
        lscpu.get_threads_per_core.side_effect = Exception('fail')
        lscpu.get_books.side_effect = Exception('fail')
        result = sysinfo.get_cpu_thread_topology(lscpu)
        self.assertIsNone(result['books'])


class TestS390xHypervisorInfoLscpuFails(unittest.TestCase):

    def test_degrades_to_none(self):
        lscpu = MagicMock()
        lscpu.get_hypervisor.side_effect = Exception('fail')
        lscpu.get_hypervisor_vendor.side_effect = Exception('fail')
        result = sysinfo.get_s390x_hypervisor_info(lscpu)
        self.assertIsNone(result['hypervisor'])
        self.assertIsNone(result['hypervisor_vendor'])


class TestX86CpuModelParsing(unittest.TestCase):
    """Verify x86 /proc/cpuinfo parsing with sample content."""

    @patch(_SYSINFO_OPEN, side_effect=_open_fake(_X86_CPUINFO))
    def test_parses_model_name(self, _mock_open):
        result = sysinfo._parse_cpu_model_x86()
        self.assertIn('Intel', result)
        self.assertIn('i7-8550U', result)


class TestPpcCpuModelParsing(unittest.TestCase):
    """Verify PPC /proc/cpuinfo parsing with sample content."""

    @patch(_SYSINFO_OPEN, side_effect=_open_fake(_PPC_CPUINFO))
    def test_parses_cpu_revision_clock(self, _mock_open):
        result = sysinfo._parse_cpu_model_ppc()
        self.assertIn('POWER8', result)
        self.assertIn('GHz', result)


class TestS390xSysinfoParsing(unittest.TestCase):
    """Verify /proc/sysinfo parsing with sample content."""

    @patch(_SYSINFO_OPEN, side_effect=_open_fake(_S390X_SYSINFO))
    def test_parses_all_fields(self, _mock_open):
        result = sysinfo.parse_s390x_sysinfo()
        self.assertEqual(result['manufacturer'], 'IBM')
        self.assertEqual(result['type'], '2964')
        self.assertEqual(result['model'], '702 N96')
        self.assertEqual(result['lpar_number'], 42)
        self.assertEqual(result['lpar_name'], 'TESTLPAR')
        self.assertEqual(result['cpus_dedicated'], 4)
        self.assertEqual(result['cpus_shared'], 0)


class TestS390xCpuModelFromSysinfo(unittest.TestCase):
    """s390x CPU model is derived from /proc/sysinfo fields."""

    @patch(_SYSINFO_OPEN, side_effect=_open_fake(_S390X_SYSINFO))
    @patch.object(sysinfo, 'IS_S390X', True)
    def test_assembles_manufacturer_type_model(self, _mock_open):
        result = sysinfo.detect_cpu_model()
        self.assertEqual(result, 'IBM/2964/702 N96')


class TestOnlineCpuCountPsutilFallbackChain(unittest.TestCase):
    """Verify the psutil CPU count fallback chain."""

    def test_uses_cpu_count_when_available(self):
        with patch.object(sysinfo, 'psutil') as mock_psutil:
            mock_psutil.cpu_count.return_value = 8
            result = sysinfo.get_online_cpu_count()
            self.assertEqual(result, 8)

    def test_falls_back_to_NUM_CPUS(self):
        with patch.object(sysinfo, 'psutil') as mock_psutil:
            del mock_psutil.cpu_count
            mock_psutil.NUM_CPUS = 4
            result = sysinfo.get_online_cpu_count()
            self.assertEqual(result, 4)

    def test_returns_none_when_all_fail(self):
        with patch.object(sysinfo, 'psutil') as mock_psutil:
            del mock_psutil.cpu_count
            del mock_psutil.NUM_CPUS
            del mock_psutil._psplatform
            result = sysinfo.get_online_cpu_count()
            self.assertIsNone(result)


class TestGetCpuCountsGracefulDegradation(unittest.TestCase):
    """Comprehensive test: psutil returns 0, lscpu total is valid."""

    @patch.object(sysinfo, 'get_online_cpu_count', return_value=0)
    def test_zero_online_falls_back_to_total(self, _mock):
        lscpu = _make_mock_lscpu(total_cpus=8)
        result = sysinfo.get_cpu_counts(lscpu)
        self.assertEqual(result['online'], 8)
        self.assertEqual(result['offline'], 0)

    @patch.object(sysinfo, 'get_online_cpu_count', return_value=6)
    def test_offline_calculation(self, _mock):
        lscpu = _make_mock_lscpu(total_cpus=8)
        result = sysinfo.get_cpu_counts(lscpu)
        self.assertEqual(result['online'], 6)
        self.assertEqual(result['offline'], 2)


class TestMemoryInfoOtherArchPsutilFails(unittest.TestCase):
    """On non-s390x, if psutil memory APIs are absent, return 0."""

    @patch.object(sysinfo, 'IS_S390X', False)
    def test_returns_zero_online(self):
        with patch.object(sysinfo, 'psutil') as mock_psutil:
            del mock_psutil.phymem_usage
            del mock_psutil.virtual_memory
            result = sysinfo.get_memory_info()
            self.assertEqual(result['online'], 0)
            self.assertEqual(result['offline'], 0)


if __name__ == '__main__':
    unittest.main()

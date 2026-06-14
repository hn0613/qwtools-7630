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
System information compatibility layer for Ginger Base.

All platform-specific detection, API version fallbacks, and system file
parsing for host information collection are centralized here.

When adapting to a new architecture or environment, changes should be
confined to this module.  Upper-level code in model/host.py should
remain architecture-agnostic and only call functions from here.

Output structure contract
=========================

HostModel.lookup() returns the following structure.  Fields marked
[s390x] are only present on that architecture.

All architectures::

    {
        'os_distro':    str,
        'os_version':   str,
        'os_codename':  str,
        'architecture': str,
        'host':         str,
        'memory':       {'online': int, 'offline': int},
        'cpu_threads':  {'sockets': int, 'cores_per_socket': int,
                         'threads_per_core': int},
        'cpus':         {'online': int, 'offline': int},
        'cpu_model':    str,
    }

s390x extensions (added on top of the above)::

    'cpu_threads' additionally contains:
        'books': str or None

    'cpus' additionally contains:
        'dedicated': int,
        'shared':    int

    additional top-level key:
        'virtualization': {
            'hypervisor':        str or None,
            'hypervisor_vendor': str or None,
            'lpar_name':         str,
            'lpar_number':       int or str,
        }

Error handling convention
=========================

Every public collector function in this module follows the same rule:
*never raise an exception for environment-related failures*.  When a
system file is missing, a command fails, a tool is unavailable, or
permissions are insufficient, the function logs a warning via wok_log
and returns a safe default value (0, '', None, or an empty dict/list
depending on the return type).  This ensures that a single missing
capability never crashes the entire host-info response.
"""

import glob
import platform
import re

import psutil
from wok.utils import run_command, wok_log


# ===================================================================
# Section 1: Architecture Detection
# ===================================================================
#
# Single source of truth for architecture classification.  Other
# modules should import IS_S390X / IS_PPC / get_arch_type from here
# rather than calling platform.machine() independently.
# ===================================================================

_ARCH = platform.machine()

IS_S390X = _ARCH.startswith('s390x')
IS_PPC = _ARCH.startswith('ppc')


def get_arch_type():
    """Return the normalized architecture family.

    Returns one of ``'s390x'``, ``'ppc'``, or ``'x86'``.
    """
    if IS_S390X:
        return 's390x'
    if IS_PPC:
        return 'ppc'
    return 'x86'


# ===================================================================
# Section 2: psutil API Compatibility
# ===================================================================
#
# psutil has changed its public API across versions.  The helpers
# below encapsulate every version-specific access path so that
# callers never need to know which psutil they are running against.
#
# Adaptation point: if a future psutil version changes these APIs
# again, add the new access path here.
# ===================================================================

def get_online_cpu_count():
    """Return the number of online CPUs via the best available psutil API.

    Tries, in order:
      1. ``psutil.cpu_count()``           (psutil >= 2.0)
      2. ``psutil.NUM_CPUS``             (psutil 1.x)
      3. ``psutil._psplatform._get_num_cpus`` / ``get_num_cpus``
         (very old psutil)

    Returns:
        int or None: the online CPU count, or *None* if every access
        path failed.
    """
    if hasattr(psutil, 'cpu_count'):
        try:
            count = psutil.cpu_count()
            if count:
                return count
        except Exception:
            pass

    if hasattr(psutil, 'NUM_CPUS'):
        try:
            return psutil.NUM_CPUS
        except Exception:
            pass

    if hasattr(psutil, '_psplatform'):
        for method_name in ('_get_num_cpus', 'get_num_cpus'):
            method = getattr(psutil._psplatform, method_name, None)
            if method is not None:
                try:
                    return method()
                except Exception:
                    pass

    return None


def get_net_io_counters(pernic=True):
    """Return network IO counters via the best available psutil API.

    Tries ``psutil.net_io_counters`` first, then falls back to the
    older ``psutil.network_io_counters``.

    Returns:
        The psutil counter object, or *None* if neither API is
        available.
    """
    if hasattr(psutil, 'net_io_counters'):
        return psutil.net_io_counters(pernic)
    elif hasattr(psutil, 'network_io_counters'):
        return psutil.network_io_counters(pernic)
    return None


# ===================================================================
# Section 3: Collector Functions
# ===================================================================
#
# Each function collects one aspect of system information.  They
# handle all architecture branching, fallback chains, and error
# recovery internally.  The return type is always stable (same keys,
# same value types) regardless of which code path was taken.
#
# Error handling: log + return safe default.  Never raise.
#
# Adaptation point: to support a new information source for any of
# these aspects, add it inside the corresponding function.
# ===================================================================

_PROC_CPUINFO = '/proc/cpuinfo'
_PROC_SYSINFO = '/proc/sysinfo'
_LSMEM_CMD = 'lsmem'


def get_cpu_counts(lscpu_instance):
    """Return online and offline CPU counts.

    The online count comes from psutil (see
    :func:`get_online_cpu_count`).  The total count comes from
    *lscpu_instance*.  Offline = total - online.

    If the online count cannot be determined, it falls back to the
    total count (optimistic assumption: all CPUs online).  If even
    the total count is unavailable, both values are 0.

    Returns:
        dict: ``{'online': int, 'offline': int}``
    """
    total_cpus = 0
    try:
        total_cpus = int(lscpu_instance.get_total_cpus())
    except Exception as e:
        wok_log.warning('sysinfo: lscpu total CPU count unavailable: %s', e)

    online_cpus = get_online_cpu_count()

    if online_cpus and online_cpus > 0:
        offline_cpus = max(0, total_cpus - online_cpus)
    elif total_cpus > 0:
        # psutil completely unavailable; assume all CPUs online
        online_cpus = total_cpus
        offline_cpus = 0
    else:
        online_cpus = 0
        offline_cpus = 0

    return {'online': online_cpus, 'offline': offline_cpus}


def _get_memory_s390x():
    """Collect memory info on s390x via the ``lsmem`` command.

    Returns:
        dict: ``{'online': int, 'offline': int}`` (values in bytes)
    """
    online = 0
    offline = 0
    online_pat = r'^Total online memory :\s+(\d+)\s+MB$'
    offline_pat = r'^Total offline memory:\s+(\d+)\s+MB$'

    out, err, rc = run_command(_LSMEM_CMD)
    if rc:
        wok_log.warning(
            'sysinfo: lsmem command failed (rc=%d): %s', rc, err)
        return {'online': online, 'offline': offline}

    try:
        m = re.search(online_pat, out.strip(), re.M | re.I)
        if m and len(m.groups()) == 1:
            online = int(m.group(1)) * 1024 * 1024

        m = re.search(offline_pat, out.strip(), re.M | re.I)
        if m and len(m.groups()) == 1:
            offline = int(m.group(1)) * 1024 * 1024
    except Exception as e:
        wok_log.warning('sysinfo: failed to parse lsmem output: %s', e)

    return {'online': online, 'offline': offline}


def _get_memory_other():
    """Collect memory info on non-s390x via psutil.

    Returns:
        dict: ``{'online': int, 'offline': int}``
    """
    online = 0
    try:
        if hasattr(psutil, 'phymem_usage'):
            online = psutil.phymem_usage().total
        elif hasattr(psutil, 'virtual_memory'):
            online = psutil.virtual_memory().total
    except Exception as e:
        wok_log.warning(
            'sysinfo: psutil memory query failed: %s', e)
    return {'online': online, 'offline': 0}


def get_memory_info():
    """Return online and offline physical memory in bytes.

    Dispatches to ``lsmem`` on s390x, psutil on all other
    architectures.

    Returns:
        dict: ``{'online': int, 'offline': int}``
    """
    if IS_S390X:
        return _get_memory_s390x()
    return _get_memory_other()


# --- CPU model detection ---

def _parse_cpu_model_x86():
    """Parse ``/proc/cpuinfo`` for x86 ``model name``."""
    try:
        with open(_PROC_CPUINFO) as f:
            for line in f:
                if 'model name' in line:
                    return line.split(':')[1].strip()
    except Exception as e:
        wok_log.warning(
            'sysinfo: failed to read x86 CPU model from %s: %s',
            _PROC_CPUINFO, e)
    return ''


def _parse_cpu_model_ppc():
    """Parse ``/proc/cpuinfo`` for PowerPC cpu/revision/clock."""
    res = {}
    try:
        with open(_PROC_CPUINFO) as f:
            for line in f:
                for key in ('cpu', 'revision', 'clock'):
                    if key in line:
                        info = line.split(':')[1].strip()
                        if key == 'clock':
                            value = (float(info.split('MHz')[0].strip())
                                     / 1000)
                        else:
                            value = info.split('(')[0].strip()
                        res[key] = value
                        if len(res) == 3:
                            return ('%(cpu)s (%(revision)s) '
                                    '@ %(clock)s GHz' % res)
    except Exception as e:
        wok_log.warning(
            'sysinfo: failed to read PPC CPU model from %s: %s',
            _PROC_CPUINFO, e)
    return ''


def _parse_cpu_model_s390x():
    """Derive CPU model string from ``/proc/sysinfo``."""
    sysinfo_data = parse_s390x_sysinfo()
    parts = []
    if sysinfo_data.get('manufacturer'):
        parts.append(sysinfo_data['manufacturer'])
    if sysinfo_data.get('type'):
        parts.append(sysinfo_data['type'])
    if sysinfo_data.get('model'):
        parts.append(sysinfo_data['model'])
    return '/'.join(parts) if parts else ''


def detect_cpu_model():
    """Detect the CPU model string for the current architecture.

    Returns:
        str: a human-readable CPU model description, or ``''`` if
        detection fails.
    """
    try:
        if IS_S390X:
            return _parse_cpu_model_s390x()
        elif IS_PPC:
            return _parse_cpu_model_ppc()
        else:
            return _parse_cpu_model_x86()
    except Exception as e:
        wok_log.warning('sysinfo: CPU model detection failed: %s', e)
        return ''


# --- CPU thread topology ---

def get_cpu_thread_topology(lscpu_instance):
    """Return CPU thread topology from lscpu.

    If lscpu is unavailable, every field degrades to a safe default
    (0 for integers, None for optional strings).

    Returns:
        dict with keys ``sockets`` (int), ``cores_per_socket`` (int),
        ``threads_per_core`` (int), and on s390x ``books``
        (str or None).
    """
    result = {
        'sockets': 0,
        'cores_per_socket': 0,
        'threads_per_core': 0,
    }

    try:
        result['sockets'] = lscpu_instance.get_sockets()
    except Exception as e:
        wok_log.warning('sysinfo: lscpu sockets unavailable: %s', e)

    try:
        result['cores_per_socket'] = \
            lscpu_instance.get_cores_per_socket()
    except Exception as e:
        wok_log.warning(
            'sysinfo: lscpu cores_per_socket unavailable: %s', e)

    try:
        result['threads_per_core'] = \
            lscpu_instance.get_threads_per_core()
    except Exception as e:
        wok_log.warning(
            'sysinfo: lscpu threads_per_core unavailable: %s', e)

    if IS_S390X:
        try:
            result['books'] = lscpu_instance.get_books()
        except Exception as e:
            wok_log.warning('sysinfo: lscpu books unavailable: %s', e)
            result['books'] = None

    return result


# --- s390x system information ---

# Key constants used when parsing /proc/sysinfo.  Defined here so
# that the parsing logic is self-contained and easy to adapt.
_S390X_CPUS_DEDICATED = 'cpus_dedicated'
_S390X_CPUS_SHARED = 'cpus_shared'
_S390X_LPAR_NAME = 'lpar_name'
_S390X_LPAR_NUMBER = 'lpar_number'


def parse_s390x_sysinfo():
    """Parse ``/proc/sysinfo`` for s390x-specific system details.

    Extracts manufacturer, type, model, LPAR name/number, and
    dedicated/shared CPU counts.

    Returns:
        dict: parsed fields, or empty dict if the file is
        unavailable or unparseable.
    """
    result = {}

    try:
        with open(_PROC_SYSINFO) as f:
            for line in f:
                if ':' not in line or len(line.split(':')) != 2:
                    continue
                key, value = line.split(':')
                key = key.strip()
                value = value.strip()

                if key == 'Model' and len(value.split()) == 2:
                    result['model'] = (value.split()[0].strip() +
                                       ' ' + value.split()[1].strip())
                elif key == 'Manufacturer':
                    result['manufacturer'] = value
                elif key == 'Type':
                    result['type'] = value
                elif key == 'LPAR Number':
                    result[_S390X_LPAR_NUMBER] = int(value)
                elif key == 'LPAR Name':
                    result[_S390X_LPAR_NAME] = value
                elif key == 'LPAR CPUs Dedicated':
                    result[_S390X_CPUS_DEDICATED] = int(value)
                elif key == 'LPAR CPUs Shared':
                    result[_S390X_CPUS_SHARED] = int(value)
    except IOError:
        wok_log.info(
            'sysinfo: %s not available (expected on non-s390x)',
            _PROC_SYSINFO)
    except Exception as e:
        wok_log.warning(
            'sysinfo: failed to parse %s: %s', _PROC_SYSINFO, e)

    return result


def get_s390x_hypervisor_info(lscpu_instance):
    """Return s390x hypervisor details from lscpu.

    Returns:
        dict: ``{'hypervisor': str or None,
                'hypervisor_vendor': str or None}``
    """
    result = {'hypervisor': None, 'hypervisor_vendor': None}
    try:
        result['hypervisor'] = lscpu_instance.get_hypervisor()
    except Exception as e:
        wok_log.warning(
            'sysinfo: lscpu hypervisor info unavailable: %s', e)
    try:
        result['hypervisor_vendor'] = \
            lscpu_instance.get_hypervisor_vendor()
    except Exception as e:
        wok_log.warning(
            'sysinfo: lscpu hypervisor vendor unavailable: %s', e)
    return result


# ===================================================================
# Section 4: Network Device Discovery
# ===================================================================
#
# Simple sysfs-based helpers for enumerating network interfaces.
# Extracted from HostStatsModel so that the discovery logic is
# available independently of the stats singleton.
# ===================================================================

_SYSFS_NET_WLAN = '/sys/class/net/*/wireless'
_SYSFS_NET_NIC = '/sys/class/net/*/device'


def discover_wlans():
    """Return names of wireless interfaces found under sysfs."""
    return [p.split('/')[-2] for p in glob.glob(_SYSFS_NET_WLAN)]


def discover_nics():
    """Return names of non-wireless NICs found under sysfs."""
    wlans = set(discover_wlans())
    all_devs = [p.split('/')[-2] for p in glob.glob(_SYSFS_NET_NIC)]
    return list(set(all_devs) - wlans)

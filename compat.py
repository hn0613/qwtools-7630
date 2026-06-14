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
Compatibility Boundary Layer for Ginger Base.

This module is the SINGLE source of truth for:
1. Architecture classification (ARCH_FAMILY, is_s390x(), is_ppc(), is_x86())
2. psutil version-portable API (get_total_phymem, get_online_cpus,
   get_net_io_counters)
3. Capability probing (probe_package_manager, probe_repo_manager,
   probe_report_tool, probe_smt)

RULES FOR CONSUMERS:
- Never call platform.machine() directly. Use ARCH_FAMILY or ARCH_RAW.
- Never use hasattr(psutil, ...) fallback chains. Use get_*() functions.
- Never implement your own capability detection. Use probe_*() functions.
- When a get_*() function returns None, it means "information unavailable"
  (not an error). Callers choose their own fallback strategy.

RULES FOR MODIFYING THIS MODULE:
- When adding a new architecture, update the ARCH_FAMILY classification block
  and add a corresponding is_*() predicate.
- When adapting to a new psutil version, update the relevant get_*() function.
- When adding a new capability check, add a probe_*() function returning
  CapabilityResult.
- All detection logic should be free of circular imports: only import stdlib,
  psutil, and subprocess here. Never import gingerbase model classes.
"""
import logging
import platform
import subprocess
from collections import namedtuple

import psutil


log = logging.getLogger('Compat')

# ---------------------------------------------------------------------------
# Architecture Classification
# ---------------------------------------------------------------------------

_RAW_ARCH = platform.machine()

if _RAW_ARCH.startswith('ppc'):
    ARCH_FAMILY = 'ppc64'
elif _RAW_ARCH.startswith('s390'):
    ARCH_FAMILY = 's390x'
else:
    ARCH_FAMILY = 'x86_64'

ARCH_RAW = _RAW_ARCH


def is_s390x():
    """True if running on IBM System Z (s390x)."""
    return ARCH_FAMILY == 's390x'


def is_ppc():
    """True if running on IBM POWER (ppc64/ppc64le)."""
    return ARCH_FAMILY == 'ppc64'


def is_x86():
    """True if running on x86_64 / AMD64 (default)."""
    return ARCH_FAMILY == 'x86_64'


# ---------------------------------------------------------------------------
# psutil Version-Portable API
# ---------------------------------------------------------------------------

def get_total_phymem():
    """Return total physical memory in bytes, or None if unavailable.

    Handles psutil API changes across versions:
    - psutil >= 2.0: psutil.virtual_memory().total
    - psutil < 2.0:  psutil.phymem_usage().total
    """
    if hasattr(psutil, 'virtual_memory'):
        try:
            return psutil.virtual_memory().total
        except Exception:
            pass
    if hasattr(psutil, 'phymem_usage'):
        try:
            return psutil.phymem_usage().total
        except Exception:
            pass
    return None


def get_online_cpus():
    """Return online CPU count as int, or None if unavailable.

    Handles psutil API changes across versions:
    - psutil >= 2.0: psutil.cpu_count()
    - psutil 1.x:    psutil.NUM_CPUS
    - psutil < 1.0:  psutil._psplatform._get_num_cpus() or get_num_cpus()
    """
    if hasattr(psutil, 'cpu_count'):
        result = psutil.cpu_count()
        if result is not None and result > 0:
            return result

    if hasattr(psutil, 'NUM_CPUS'):
        result = psutil.NUM_CPUS
        if result is not None and result > 0:
            return result

    if hasattr(psutil, '_psplatform'):
        for method_name in ('_get_num_cpus', 'get_num_cpus'):
            method = getattr(psutil._psplatform, method_name, None)
            if method is not None:
                try:
                    result = method()
                    if result > 0:
                        return result
                except Exception:
                    continue

    return None


def get_net_io_counters(per_nic=False):
    """Return network I/O counters, or None if unavailable.

    Handles psutil API changes across versions:
    - psutil >= 2.0: psutil.net_io_counters()
    - psutil < 2.0:  psutil.network_io_counters()

    When per_nic=True, returns dict of {nic_name: counters}.
    When per_nic=False, returns aggregate counters object.
    Returns None if no suitable psutil API is found.
    """
    if hasattr(psutil, 'net_io_counters'):
        try:
            return psutil.net_io_counters(per_nic)
        except Exception:
            pass
    if hasattr(psutil, 'network_io_counters'):
        try:
            return psutil.network_io_counters(per_nic)
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# Capability Probing
# ---------------------------------------------------------------------------

# Structured result for capability probes.
# available: bool - whether the capability is present
# tool: str or None - the name/type of the detected tool
# detail: str or None - additional detail (error message if not available)
CapabilityResult = namedtuple('CapabilityResult',
                              ['available', 'tool', 'detail'])


def probe_package_manager():
    """Detect available package manager for software updates.

    Returns CapabilityResult. The .tool field is one of:
    'dnf', 'yum', 'apt', 'portage', 'zypper', or None.

    Priority order: dnf > yum > apt > portage > zypper.
    Python-importable managers take precedence. Zypper is only selected
    if no Python-native manager is available (subprocess-only detection).
    """
    import_checks = [
        ('dnf', 'dnf'),
        ('yum', 'yum'),
        ('apt', 'apt'),
        ('portage', 'portage'),
    ]

    for module_name, tool_name in import_checks:
        try:
            __import__(module_name)
            return CapabilityResult(available=True, tool=tool_name, detail=None)
        except ImportError:
            continue

    # zypper has no Python module; detect via subprocess
    try:
        rc = subprocess.call(
            ['zypper', '--help'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        if rc == 0:
            return CapabilityResult(available=True, tool='zypper', detail=None)
    except (OSError, IOError):
        pass

    return CapabilityResult(available=False, tool=None,
                            detail='No compatible package manager found')


def probe_repo_manager():
    """Detect available repository management tool.

    Returns CapabilityResult. The .tool field is one of:
    'yum', 'deb', or None.

    Priority order: dnf (maps to 'yum') > yum > apt_pkg (maps to 'deb').
    """
    for module_name, tool_type in [('dnf', 'yum'), ('yum', 'yum'),
                                   ('apt_pkg', 'deb')]:
        try:
            __import__(module_name)
            return CapabilityResult(available=True, tool=tool_type, detail=None)
        except ImportError:
            continue

    return CapabilityResult(available=False, tool=None,
                            detail='No repository management tool found')


def probe_report_tool():
    """Detect available system debug report tool.

    Returns CapabilityResult. The .tool field is one of:
    'dbginfo', 'sosreport', or None.

    Priority order: dbginfo.sh (s390x IBM tool) > sosreport.
    """
    tools = [
        (['/usr/sbin/dbginfo.sh', '--help'], 'dbginfo'),
        (['sosreport', '--help'], 'sosreport'),
    ]

    for cmd_args, tool_name in tools:
        try:
            rc = subprocess.call(
                cmd_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            if rc == 0:
                return CapabilityResult(available=True, tool=tool_name,
                                        detail=None)
        except (OSError, IOError):
            continue

    return CapabilityResult(available=False, tool=None,
                            detail='No debug report tool found')


def probe_smt():
    """Check if SMT capability is architecturally possible (s390x only).

    Returns CapabilityResult. This only checks whether the architecture
    supports SMT operations. The actual SMT availability check (IFL CPUs
    etc.) is performed by SmtModel.check_smt_support().
    """
    if not is_s390x():
        return CapabilityResult(available=False, tool=None,
                                detail='SMT is only supported on s390x')
    return CapabilityResult(available=True, tool='smt_s390x', detail=None)

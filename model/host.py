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
import distro
import os
import platform
import time
from collections import defaultdict

import psutil
from cherrypy.process.plugins import BackgroundTask
from wok.asynctask import AsyncTask
from wok.basemodel import Singleton
from wok.exception import InvalidOperation
from wok.exception import OperationFailed
from wok.model.tasks import TaskModel
from wok.plugins.gingerbase.config import config
from wok.plugins.gingerbase.i18n import messages
from wok.plugins.gingerbase.lscpu import LsCpu
from wok.plugins.gingerbase.model.debugreports import DebugReportsModel
from wok.plugins.gingerbase.model.smt import SmtModel
from wok.plugins.gingerbase.repositories import Repositories
from wok.plugins.gingerbase.swupdate import SoftwareUpdate
from wok.plugins.gingerbase import sysinfo
from wok.utils import run_command
from wok.utils import wok_log

HOST_STATS_INTERVAL = 1
DOM_STATE_MAP = {0: 'nostate',
                 1: 'running',
                 2: 'blocked',
                 3: 'paused',
                 4: 'shutdown',
                 5: 'shutoff',
                 6: 'crashed',
                 7: 'pmsuspended'}


class HostModel(object):
    def __init__(self, **kargs):
        # self.conn = kargs['conn']
        self.objstore = kargs['objstore']
        self.task = TaskModel(**kargs)
        self.lscpu = LsCpu()

    def _get_s390x_extensions(self, host_info):
        """Collect s390x-specific fields that extend the base info.

        Returns a dict with keys ``cpus`` (augmented with
        *dedicated* and *shared*), ``cpu_model``, and
        ``virtualization``.  All values degrade gracefully when
        ``/proc/sysinfo`` or lscpu is unavailable.
        """
        ext = {}
        sysinfo_data = sysinfo.parse_s390x_sysinfo()

        # Augment cpus with s390x-specific dedicated/shared counts
        cpus = dict(host_info.get('cpus', {}))
        cpus['dedicated'] = sysinfo_data.get('cpus_dedicated', 0)
        cpus['shared'] = sysinfo_data.get('cpus_shared', 0)
        ext['cpus'] = cpus

        # CPU model from /proc/sysinfo (manufacturer/type/model)
        ext['cpu_model'] = sysinfo.detect_cpu_model()

        # Virtualization / LPAR details
        hyp = sysinfo.get_s390x_hypervisor_info(self.lscpu)
        ext['virtualization'] = {
            'hypervisor': hyp.get('hypervisor'),
            'hypervisor_vendor': hyp.get('hypervisor_vendor'),
            'lpar_name': sysinfo_data.get('lpar_name', ''),
            'lpar_number': sysinfo_data.get('lpar_number', ''),
        }

        return ext

    def _get_memory(self):
        """Return memory info delegated to sysinfo compatibility layer."""
        return sysinfo.get_memory_info()

    def _get_cpus(self):
        """Return CPU counts delegated to sysinfo compatibility layer."""
        return sysinfo.get_cpu_counts(self.lscpu)

    def _get_base_info(self):
        """Collect host information common to all architectures.

        Architecture-specific branching and fallback logic are
        handled inside the ``sysinfo`` compatibility layer.
        """
        common_info = {}
        os_distro, version, codename = distro.linux_distribution(
            full_distribution_name=False)
        common_info['os_distro'] = os_distro
        common_info['os_version'] = version
        common_info['os_codename'] = codename
        common_info['architecture'] = sysinfo.get_arch_type()
        common_info['host'] = platform.node()
        common_info['memory'] = self._get_memory()
        common_info['cpu_threads'] = sysinfo.get_cpu_thread_topology(
            self.lscpu)
        return common_info

    def lookup(self, *name):
        """Assemble full host info: base + cpus + cpu_model + arch extensions."""
        host_info = self._get_base_info()
        host_info['cpus'] = self._get_cpus()
        host_info['cpu_model'] = sysinfo.detect_cpu_model()

        if sysinfo.IS_S390X:
            host_info.update(self._get_s390x_extensions(host_info))

        return host_info

    def swupdate(self, *name):
        try:
            swupdate = SoftwareUpdate()
        except Exception:
            raise OperationFailed('GGBPKGUPD0004E')

        pkgs = swupdate.getNumOfUpdates()
        if pkgs == 0:
            wok_log.debug(messages['GGBPKGUPD0001E'])
            return {'message': messages['GGBPKGUPD0001E']}

        wok_log.debug('Host is going to be updated.')
        taskid = AsyncTask('/plugins/gingerbase/host/swupdate',
                           swupdate.doUpdate).id
        return self.task.lookup(taskid)

    def shutdown(self, args=None):
        # Check for running vms before shutdown
        running_vms = self.get_vmlist_bystate('running')
        if len(running_vms) > 0:
            raise OperationFailed('GGBHOST0001E')

        wok_log.info('Host is going to shutdown.')
        os.system('shutdown -h now')

    def reboot(self, args=None):
        # Check for running vms before reboot
        running_vms = self.get_vmlist_bystate('running')
        if len(running_vms) > 0:
            raise OperationFailed('GGBHOST0002E')

        wok_log.info('Host is going to reboot.')
        os.system('reboot')

    def get_vmlist_bystate(self, state='running'):
        try:
            libvirt_mod = __import__('libvirt')
        except Exception as e:
            wok_log.info('Unable to import libvirt module. Details:',
                         e.message)
            # Ignore any error and assume there is no vm running in the host
            return []

        libvirtd_running = ['systemctl', 'is-active', 'libvirtd', '--quiet']
        _, _, rcode = run_command(libvirtd_running, silent=True)
        if rcode != 0:
            return []

        try:
            conn = libvirt_mod.open(None)
            return [dom.name().decode('utf-8')
                    for dom in conn.listAllDomains(0)
                    if (DOM_STATE_MAP[dom.info()[0]] == state)]
        except Exception as e:
            wok_log.info('Unable to get virtual machines information. '
                         'Details:', e.message)
            raise OperationFailed('GGBHOST0003E')


class HostStatsModel(object):
    __metaclass__ = Singleton

    def __init__(self, **kargs):
        self.host_stats = defaultdict(list)
        gbconfig = config.get('gingerbase', {})
        self.statshistory_on = gbconfig.get('statshistory_on', True)

        # create thread to collect statistcs and cache values only if
        # statshistory_on is enabled in gingerbase.conf
        if self.statshistory_on:
            self.host_stats_thread = BackgroundTask(HOST_STATS_INTERVAL,
                                                    self.update_host_stats)
            self.host_stats_thread.start()

    def lookup(self, *name):
        if not self.statshistory_on:
            self.update_host_stats()

        return {'cpu_utilization': self.host_stats['cpu_utilization'][-1],
                'memory': self.host_stats['memory'][-1],
                'disk_read_rate': self.host_stats['disk_read_rate'][-1],
                'disk_write_rate': self.host_stats['disk_write_rate'][-1],
                'net_recv_rate': self.host_stats['net_recv_rate'][-1],
                'net_sent_rate': self.host_stats['net_sent_rate'][-1]}

    def update_host_stats(self):
        preTimeStamp = self.host_stats['timestamp']
        timestamp = time.time()
        # FIXME when we upgrade psutil, we can get uptime by psutil.uptime
        # we get uptime by float(open("/proc/uptime").readline().split()[0])
        # and calculate the first io_rate after the OS started.
        with open('/proc/uptime') as time_f:
            seconds = (timestamp - preTimeStamp if preTimeStamp else
                       float(time_f.readline().split()[0]))

        self.host_stats['timestamp'] = timestamp
        self._get_host_disk_io_rate(seconds)
        self._get_host_network_io_rate(seconds)

        self._get_percentage_host_cpu_usage()
        self._get_host_memory_stats()

        # store only 60 stats (1 min)
        for key, value in self.host_stats.items():
            if isinstance(value, list):
                if len(value) == 60:
                    self.host_stats[key] = value[10:]

    def _get_percentage_host_cpu_usage(self):
        # This is cpu usage producer. This producer will calculate the usage
        # at an interval of HOST_STATS_INTERVAL.
        # The psutil.cpu_percent works as non blocking.
        # psutil.cpu_percent maintains a cpu time sample.
        # It will update the cpu time sample when it is called.
        # So only this producer can call psutil.cpu_percent in gingerbase.
        self.host_stats['cpu_utilization'].append(psutil.cpu_percent(None))

    def _get_host_memory_stats(self):
        virt_mem = psutil.virtual_memory()
        # available:
        #  the actual amount of available memory that can be given
        #  instantly to processes that request more memory in bytes; this
        #  is calculated by summing different memory values depending on
        #  the platform (e.g. free + buffers + cached on Linux)
        memory_stats = {'total': virt_mem.total,
                        'free': virt_mem.free,
                        'cached': virt_mem.cached,
                        'buffers': virt_mem.buffers,
                        'avail': virt_mem.available}
        self.host_stats['memory'].append(memory_stats)

    def _get_host_disk_io_rate(self, seconds):
        disk_read_bytes = self.host_stats['disk_read_bytes']
        disk_write_bytes = self.host_stats['disk_write_bytes']
        prev_read_bytes = disk_read_bytes[-1] if disk_read_bytes else 0
        prev_write_bytes = disk_write_bytes[-1] if disk_write_bytes else 0

        disk_io = psutil.disk_io_counters(False)
        read_bytes = disk_io.read_bytes
        write_bytes = disk_io.write_bytes

        rd_rate = int(float(read_bytes - prev_read_bytes) / seconds + 0.5)
        wr_rate = int(float(write_bytes - prev_write_bytes) / seconds + 0.5)

        self.host_stats['disk_read_rate'].append(rd_rate)
        self.host_stats['disk_write_rate'].append(wr_rate)
        self.host_stats['disk_read_bytes'].append(read_bytes)
        self.host_stats['disk_write_bytes'].append(write_bytes)

    def _get_host_network_io_rate(self, seconds):
        net_recv_bytes = self.host_stats['net_recv_bytes']
        net_sent_bytes = self.host_stats['net_sent_bytes']
        prev_recv_bytes = net_recv_bytes[-1] if net_recv_bytes else 0
        prev_sent_bytes = net_sent_bytes[-1] if net_sent_bytes else 0

        net_ios = sysinfo.get_net_io_counters(True)

        recv_bytes = 0
        sent_bytes = 0
        if net_ios is not None:
            for key in set(self.nics() +
                           self.wlans()) & set(net_ios.keys()):
                recv_bytes = recv_bytes + net_ios[key].bytes_recv
                sent_bytes = sent_bytes + net_ios[key].bytes_sent

        rx_rate = int(float(recv_bytes - prev_recv_bytes) / seconds + 0.5)
        tx_rate = int(float(sent_bytes - prev_sent_bytes) / seconds + 0.5)

        self.host_stats['net_recv_rate'].append(rx_rate)
        self.host_stats['net_sent_rate'].append(tx_rate)
        self.host_stats['net_recv_bytes'].append(recv_bytes)
        self.host_stats['net_sent_bytes'].append(sent_bytes)

    def wlans(self):
        return sysinfo.discover_wlans()

    # FIXME if we do not want to list usb nic
    def nics(self):
        return sysinfo.discover_nics()


class HostStatsHistoryModel(object):
    def __init__(self, **kargs):
        self.history = HostStatsModel(**kargs)

    def lookup(self, *name):
        if not self.history.statshistory_on:
            # return values of only one execution
            return self.history.lookup()

        return {'cpu_utilization': self.history.host_stats['cpu_utilization'],
                'memory': self.history.host_stats['memory'],
                'disk_read_rate': self.history.host_stats['disk_read_rate'],
                'disk_write_rate': self.history.host_stats['disk_write_rate'],
                'net_recv_rate': self.history.host_stats['net_recv_rate'],
                'net_sent_rate': self.history.host_stats['net_sent_rate']}


class CapabilitiesModel(object):
    __metaclass__ = Singleton

    def __init__(self, **kargs):
        wok_log.info('*** Ginger Base: Running capabilities tests ***')
        self.report_tool = self.has_report_tool()
        wok_log.info('System Report Tool ...: %s' % str(self.report_tool))

        try:
            SoftwareUpdate()
        except Exception:
            self.update_tool = False
        else:
            self.update_tool = True
        wok_log.info('System Update Tool ...: %s' % str(self.update_tool))

        try:
            repo = Repositories()
        except Exception:
            self.repo_mngt_tool = None
        else:
            self.repo_mngt_tool = repo._pkg_mnger.TYPE
        wok_log.info('Repo Management Tool .: %s' % str(self.repo_mngt_tool))
        wok_log.info('*** Ginger Base: Capabilities tests completed ***')

    def has_report_tool(self):
        return bool(DebugReportsModel.get_system_report_tool())

    def has_smt(self):
        if not sysinfo.IS_S390X:
            return False
        try:
            return bool(SmtModel().check_smt_support())
        except Exception as e:
            wok_log.warning(
                'SMT support check failed: %s', e)
            return False

    def lookup(self, *ident):
        self.report_tool = self.has_report_tool()
        self.smt = self.has_smt()
        return {'system_report_tool': self.report_tool,
                'update_tool': self.update_tool,
                'repo_mngt_tool': self.repo_mngt_tool,
                'smt': self.smt}


class RepositoriesModel(object):
    def __init__(self, **kargs):
        try:
            self.host_repositories = Repositories()
        except Exception:
            self.host_repositories = None

    def get_list(self):
        if self.host_repositories is None:
            raise InvalidOperation('GGBREPOS0014E')

        return sorted(self.host_repositories.getRepositories())

    def create(self, params):
        if self.host_repositories is None:
            raise InvalidOperation('GGBREPOS0014E')

        return self.host_repositories.addRepository(params)


class RepositoryModel(object):
    def __init__(self, **kargs):
        try:
            self._repositories = Repositories()
        except Exception:
            self._repositories = None

    def lookup(self, repo_id):
        if self._repositories is None:
            raise InvalidOperation('GGBREPOS0014E')

        return self._repositories.getRepository(repo_id)

    def enable(self, repo_id):
        if self._repositories is None:
            raise InvalidOperation('GGBREPOS0014E')

        return self._repositories.enableRepository(repo_id)

    def disable(self, repo_id):
        if self._repositories is None:
            raise InvalidOperation('GGBREPOS0014E')

        return self._repositories.disableRepository(repo_id)

    def update(self, repo_id, params):
        if self._repositories is None:
            raise InvalidOperation('GGBREPOS0014E')

        return self._repositories.updateRepository(repo_id, params)

    def delete(self, repo_id):
        if self._repositories is None:
            raise InvalidOperation('GGBREPOS0014E')

        return self._repositories.removeRepository(repo_id)

# -*- coding: utf-8 -*-
#
# Project Ginger Base
#
# Copyright IBM Corp, 2015-2017
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
import json
import platform
import time
import unittest
from functools import partial

import cherrypy
import distro
import mock
from mock import patch
from wok.plugins.gingerbase.compat import get_total_phymem
from wok.plugins.gingerbase.model.host import HostModel

from tests.utils import patch_auth
from tests.utils import request
from tests.utils import run_server
from tests.utils import wait_task

test_server = None
model = None


def setUpModule():
    global test_server, model, tmpfile

    patch_auth()
    test_server = run_server(test_mode=True)
    model = cherrypy.tree.apps['/plugins/gingerbase'].root.model


def tearDownModule():
    test_server.stop()


class HostTests(unittest.TestCase):
    def setUp(self):
        self.request = partial(request)

    def test_hostinfo(self):
        resp = self.request('/plugins/gingerbase/host').read()
        info = json.loads(resp)
        # All architectures now return a stable set of keys
        keys = ['os_distro', 'os_version', 'os_codename', 'cpu_model',
                'memory', 'cpus', 'architecture', 'host', 'virtualization',
                'cpu_threads']
        if not platform.machine().startswith('s390x'):
            total_phymem = get_total_phymem()
            if total_phymem is not None:
                self.assertEqual(total_phymem, info['memory']['online'])
        self.assertEqual(sorted(keys), sorted(info.keys()))

        distro_info = distro.linux_distribution(full_distribution_name=False)
        self.assertEqual(distro_info[0], info['os_distro'])
        self.assertEqual(distro_info[1], info['os_version'])
        self.assertEqual(distro_info[2], info['os_codename'])
        self.assertEqual(platform.node(), info['host'])
        self.assertEqual(platform.machine(), info['architecture'])

        # Verify stable cpus structure
        self.assertIn('online', info['cpus'])
        self.assertIn('offline', info['cpus'])
        self.assertIn('dedicated', info['cpus'])
        self.assertIn('shared', info['cpus'])
        # cpus values must always be integers, never 'unknown'
        self.assertIsInstance(info['cpus']['online'], int)
        self.assertIsInstance(info['cpus']['offline'], int)

        # Verify stable cpu_threads structure
        self.assertIn('books', info['cpu_threads'])

        # Verify virtualization key semantics
        if platform.machine().startswith('s390x'):
            self.assertIsInstance(info['virtualization'], dict)
        else:
            self.assertIsNone(info['virtualization'])

    def test_hoststats(self):
        time.sleep(1)
        stats_keys = ['cpu_utilization', 'memory', 'disk_read_rate',
                      'disk_write_rate', 'net_recv_rate', 'net_sent_rate']
        resp = self.request('/plugins/gingerbase/host/stats').read()
        stats = json.loads(resp)
        self.assertEquals(sorted(stats_keys), sorted(stats.keys()))

        cpu_utilization = stats['cpu_utilization']
        self.assertIsInstance(cpu_utilization, float)
        self.assertGreaterEqual(cpu_utilization, 0.0)
        self.assertTrue(cpu_utilization <= 100.0)

        memory_stats = stats['memory']
        self.assertIn('total', memory_stats)
        self.assertIn('free', memory_stats)
        self.assertIn('cached', memory_stats)
        self.assertIn('buffers', memory_stats)
        self.assertIn('avail', memory_stats)

        resp = self.request('/plugins/gingerbase/host/stats/history').read()
        history = json.loads(resp)
        self.assertEquals(sorted(stats_keys), sorted(history.keys()))

    def test_host_actions(self):
        resp = self.request('/plugins/gingerbase/host/shutdown', '{}', 'POST')
        self.assertEquals(200, resp.status)
        resp = self.request('/plugins/gingerbase/host/reboot', '{}', 'POST')
        self.assertEquals(200, resp.status)

    def test_packages_update(self):
        def _task_lookup(taskid):
            return json.loads(self.request('/plugins/gingerbase/tasks/%s' %
                                           taskid).read())

        resp = self.request('/plugins/gingerbase/host/packagesupdate',
                            None, 'GET')
        pkgs = json.loads(resp.read())
        self.assertEquals(5, len(pkgs))

        pkg_keys = ['package_name', 'repository', 'arch', 'version']
        for p in pkgs:
            name = p['package_name']
            resp = self.request('/plugins/gingerbase/host/packagesupdate/' +
                                name, None, 'GET')
            info = json.loads(resp.read())
            self.assertEquals(sorted(pkg_keys), sorted(info.keys()))

            # Get dependencies list
            deps_uri = '/plugins/gingerbase/host/packagesupdate/%s/deps'
            resp = self.request(deps_uri % name, None, 'GET')
            info = json.loads(resp.read())
            self.assertEquals(type(info), list)

        # Test system update of specific package. Since package 'ginger' has
        # 'wok' as dependency, both packages must be selected to be updated
        # and, in the end of the process, we have only 3 packages to update.
        uri = '/plugins/gingerbase/host/packagesupdate/ginger/upgrade'
        resp = self.request(uri, '{}', 'POST')
        task = json.loads(resp.read())
        task_params = [u'id', u'message', u'status', u'target_uri']
        self.assertEquals(sorted(task_params), sorted(task.keys()))

        resp = self.request('/plugins/gingerbase/tasks/' + task[u'id'],
                            None, 'GET')
        task_info = json.loads(resp.read())
        self.assertEquals(task_info['status'], 'running')
        wait_task(_task_lookup, task_info['id'])
        resp = self.request('/plugins/gingerbase/tasks/' + task[u'id'],
                            None, 'GET')
        task_info = json.loads(resp.read())
        self.assertEquals(task_info['status'], 'finished')
        self.assertIn(u'All packages updated', task_info['message'])
        pkgs = model.packagesupdate_get_list()
        self.assertEquals(3, len(pkgs))

        # test system update of the rest of packages
        resp = self.request('/plugins/gingerbase/host/swupdate', '{}', 'POST')
        task = json.loads(resp.read())
        task_params = [u'id', u'message', u'status', u'target_uri']
        self.assertEquals(sorted(task_params), sorted(task.keys()))

        resp = self.request('/tasks/' + task[u'id'], None, 'GET')
        task_info = json.loads(resp.read())
        self.assertEquals(task_info['status'], 'running')
        wait_task(_task_lookup, task_info['id'])
        resp = self.request('/tasks/' + task[u'id'], None, 'GET')
        task_info = json.loads(resp.read())
        self.assertEquals(task_info['status'], 'finished')
        self.assertIn(u'All packages updated', task_info['message'])
        pkgs = model.packagesupdate_get_list()
        self.assertEquals(0, len(pkgs))

    def test_swupdateprogress(self):
        resp = self.request('/plugins/gingerbase/host/swupdateprogress',
                            None, 'GET')
        task = json.loads(resp.read())
        self.assertEquals(202, resp.status)

        for i in range(1, 6):
            resp = self.request('/tasks/' + task['id'], None, 'GET')
            task = json.loads(resp.read())
            self.assertEquals(200, resp.status)
            self.assertIn('*', task['message'].rstrip('\n'))
            time.sleep(1)

        resp = self.request('/tasks/' + task['id'], None, 'GET')
        task = json.loads(resp.read())
        self.assertEquals(200, resp.status)
        self.assertEqual(task['status'], 'finished')
        time.sleep(1)

    @mock.patch('wok.plugins.gingerbase.model.host.wok_log')
    def test_get_vmlist_bystate_import_error(self, mock_woklog):

        def failed_libvirt_import(module, *args, **kwargs):
            raise ImportError()

        with patch('__builtin__.__import__', failed_libvirt_import):
            vms = HostModel(objstore=None).get_vmlist_bystate()
            self.assertEqual(vms, [])

    @mock.patch('wok.plugins.gingerbase.model.host.run_command')
    def test_vmlist_bystate_libvirtd_not_running(self, mock_run_cmd):

        def successful_libvirt_import(module, *args, **kwargs):
            pass

        mock_run_cmd.return_value = ['', '', 3]

        with patch('__builtin__.__import__', successful_libvirt_import):
            vms = HostModel(objstore=None).get_vmlist_bystate()
            self.assertEqual(vms, [])
            cmd = ['systemctl', 'is-active', 'libvirtd', '--quiet']
            mock_run_cmd.assert_called_once_with(cmd, silent=True)


class TestHostDegradation(unittest.TestCase):
    """Test graceful degradation when system info is partially unavailable."""

    @mock.patch('wok.plugins.gingerbase.model.host.get_online_cpus',
                return_value=None)
    @mock.patch('wok.plugins.gingerbase.model.host.LsCpu')
    def test_cpus_fallback_when_psutil_unavailable(self, mock_lscpu_cls,
                                                    mock_cpus):
        """_get_cpus returns integers (not 'unknown') when psutil fails."""
        mock_lscpu = mock.MagicMock()
        mock_lscpu.get_total_cpus.return_value = 4
        mock_lscpu_cls.return_value = mock_lscpu

        host = HostModel(objstore=None)
        host.lscpu = mock_lscpu
        cpus = host._get_cpus()

        self.assertIsInstance(cpus['online'], int)
        self.assertIsInstance(cpus['offline'], int)
        # When psutil is unavailable, fallback to total_cpus from lscpu
        self.assertEqual(cpus['online'], 4)
        self.assertEqual(cpus['offline'], 0)

    @mock.patch('wok.plugins.gingerbase.model.host.get_net_io_counters',
                return_value=None)
    def test_network_io_handles_none_gracefully(self, _mock_net_io):
        """No crash when net_io_counters is unavailable."""
        from collections import defaultdict
        from wok.plugins.gingerbase.model.host import HostStatsModel

        stats_model = HostStatsModel.__new__(HostStatsModel)
        stats_model.host_stats = defaultdict(list)
        stats_model.host_stats['net_recv_bytes'] = [100]
        stats_model.host_stats['net_sent_bytes'] = [200]

        # Should not raise - gracefully appends 0
        stats_model._get_host_network_io_rate(1.0)

        self.assertEqual(stats_model.host_stats['net_recv_rate'], [0])
        self.assertEqual(stats_model.host_stats['net_sent_rate'], [0])

    @mock.patch('wok.plugins.gingerbase.model.host.get_total_phymem',
                return_value=None)
    @mock.patch('wok.plugins.gingerbase.model.host.is_s390x',
                return_value=False)
    @mock.patch('wok.plugins.gingerbase.model.host.LsCpu')
    def test_memory_fallback_when_psutil_unavailable(self, mock_lscpu_cls,
                                                      _mock_arch,
                                                      _mock_phymem):
        """_get_memory returns zeros when psutil cannot determine memory."""
        mock_lscpu = mock.MagicMock()
        mock_lscpu_cls.return_value = mock_lscpu

        host = HostModel(objstore=None)
        host.lscpu = mock_lscpu
        memory = host._get_memory()

        self.assertEqual(memory['online'], 0)
        self.assertEqual(memory['offline'], 0)

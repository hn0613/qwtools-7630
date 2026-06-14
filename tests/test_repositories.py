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
import time
import unittest
from unittest.mock import MagicMock, patch

try:
    from wok.exception import InvalidOperation
    from wok.exception import InvalidParameter
    from wok.exception import MissingParameter
    from wok.exception import NotFoundError
    _wok_available = True
except ImportError:
    _wok_available = False

    # Minimal shims so facade/handler tests run without wok installed
    class InvalidParameter(Exception):
        pass

    class InvalidOperation(Exception):
        pass

    class MissingParameter(Exception):
        pass

    class NotFoundError(Exception):
        pass


# ---------------------------------------------------------------------------
# Fake handlers for testing the Repositories facade in isolation
# ---------------------------------------------------------------------------

class FakeYumHandler(object):
    TYPE = 'yum'
    CONFIG_ENTRY = ('repo_name', 'mirrorlist', 'metalink', 'gpgcheck', 'gpgkey')

    def __init__(self):
        self.repos = {}

    def addRepo(self, params):
        repo_id = params.get('repo_id', 'test_repo_%s' % int(time.time() * 1000))
        if repo_id in self.repos:
            raise InvalidOperation('GGBREPOS0022E', {'repo_id': repo_id})
        self.repos[repo_id] = {'repo_id': repo_id, 'enabled': True,
                               'baseurl': params.get('baseurl', ''),
                               'config': params.get('config', {})}
        return repo_id

    def updateRepo(self, repo_id, params):
        if repo_id not in self.repos:
            raise NotFoundError('GGBREPOS0012E', {'repo_id': repo_id})
        self.repos[repo_id].update(params)
        return repo_id

    def removeRepo(self, repo_id):
        if repo_id not in self.repos:
            raise NotFoundError('GGBREPOS0012E', {'repo_id': repo_id})
        del self.repos[repo_id]

    def toggleRepo(self, repo_id, enable):
        if repo_id not in self.repos:
            raise NotFoundError('GGBREPOS0012E', {'repo_id': repo_id})
        self.repos[repo_id]['enabled'] = enable
        return repo_id

    def getRepo(self, repo_id):
        if repo_id not in self.repos:
            raise NotFoundError('GGBREPOS0012E', {'repo_id': repo_id})
        return self.repos[repo_id]

    def getRepositoriesList(self):
        return list(self.repos.keys())


class FakeAptHandler(object):
    TYPE = 'deb'
    CONFIG_ENTRY = ('dist', 'comps')

    def __init__(self):
        self.repos = {}

    def addRepo(self, params):
        config = params.get('config', None)
        if config is None:
            raise MissingParameter('GGBREPOS0019E')
        if 'dist' not in config:
            raise MissingParameter('GGBREPOS0019E')
        uri = params.get('baseurl', None)
        if uri is None:
            raise MissingParameter('GGBREPOS0013E')
        dist = config['dist']
        comps = config.get('comps', [])
        repo_id = '%s-%s-%s' % (uri.split('/')[2], dist, '-'.join(comps))
        if repo_id in self.repos:
            raise InvalidOperation('GGBREPOS0022E', {'repo_id': repo_id})
        self.repos[repo_id] = {'repo_id': repo_id, 'enabled': True,
                               'baseurl': uri,
                               'config': {'dist': dist, 'comps': comps}}
        return repo_id

    def updateRepo(self, repo_id, params):
        if repo_id not in self.repos:
            raise NotFoundError('GGBREPOS0012E', {'repo_id': repo_id})
        repo = self.repos[repo_id]
        if 'baseurl' in params:
            repo['baseurl'] = params['baseurl']
        if 'config' in params:
            repo['config'].update(params['config'])
        return repo_id

    def removeRepo(self, repo_id):
        if repo_id not in self.repos:
            raise NotFoundError('GGBREPOS0012E', {'repo_id': repo_id})
        del self.repos[repo_id]

    def toggleRepo(self, repo_id, enable):
        if repo_id not in self.repos:
            raise NotFoundError('GGBREPOS0012E', {'repo_id': repo_id})
        self.repos[repo_id]['enabled'] = enable
        return repo_id

    def getRepo(self, repo_id):
        if repo_id not in self.repos:
            raise NotFoundError('GGBREPOS0012E', {'repo_id': repo_id})
        return self.repos[repo_id]

    def getRepositoriesList(self):
        return list(self.repos.keys())


def _make_facade(handler):
    """Create a Repositories facade with injected handler.
    When wok is available, uses the real Repositories class (bypassing
    __init__ auto-detection). Otherwise uses a local mirror of the
    facade validation logic so boundary tests still run."""
    if _wok_available:
        from wok.plugins.gingerbase.repositories import Repositories
        facade = object.__new__(Repositories)
        facade._pkg_mnger = handler
        return facade

    # Local facade that mirrors the shared validation boundary
    return _LocalFacade(handler)


class _LocalFacade(object):
    """Mirrors the Repositories facade validation logic from repositories.py
    for testing without wok installed."""

    def __init__(self, handler):
        self._pkg_mnger = handler

    def addRepository(self, params):
        config = params.get('config', {})
        extra_keys = list(
            set(config.keys()).difference(set(self._pkg_mnger.CONFIG_ENTRY)))
        if len(extra_keys) > 0:
            raise InvalidParameter('GGBREPOS0028E',
                                   {'items': ','.join(extra_keys)})
        return self._pkg_mnger.addRepo(params)

    def getRepositories(self):
        return self._pkg_mnger.getRepositoriesList()

    def getRepository(self, repo_id):
        info = self._pkg_mnger.getRepo(repo_id)
        info['repo_id'] = repo_id
        return info

    def enableRepository(self, repo_id):
        return self._pkg_mnger.toggleRepo(repo_id, True)

    def disableRepository(self, repo_id):
        return self._pkg_mnger.toggleRepo(repo_id, False)

    def updateRepository(self, repo_id, params):
        config = params.get('config', {})
        extra_keys = list(
            set(config.keys()).difference(set(self._pkg_mnger.CONFIG_ENTRY)))
        if len(extra_keys) > 0:
            raise InvalidParameter('GGBREPOS0028E',
                                   {'items': ','.join(extra_keys)})
        return self._pkg_mnger.updateRepo(repo_id, params)

    def removeRepository(self, repo_id):
        return self._pkg_mnger.removeRepo(repo_id)


# ---------------------------------------------------------------------------
# Test: Facade config key validation (shared boundary)
# ---------------------------------------------------------------------------

class TestFacadeConfigValidation(unittest.TestCase):
    """Verify that both addRepository and updateRepository reject
    unknown config keys at the facade level."""

    def test_add_rejects_unknown_config_keys_yum(self):
        handler = FakeYumHandler()
        facade = _make_facade(handler)
        params = {'repo_id': 'test1', 'baseurl': 'http://example.com',
                  'config': {'unknown_key': 'value'}}
        self.assertRaises(InvalidParameter, facade.addRepository, params)

    def test_update_rejects_unknown_config_keys_yum(self):
        handler = FakeYumHandler()
        handler.repos['test1'] = {'repo_id': 'test1', 'enabled': True,
                                  'baseurl': 'http://example.com', 'config': {}}
        facade = _make_facade(handler)
        params = {'config': {'bad_key': 'value'}}
        self.assertRaises(InvalidParameter, facade.updateRepository,
                          'test1', params)

    def test_add_rejects_unknown_config_keys_apt(self):
        handler = FakeAptHandler()
        facade = _make_facade(handler)
        params = {'baseurl': 'http://example.com',
                  'config': {'dist': 'focal', 'unsupported': 'x'}}
        self.assertRaises(InvalidParameter, facade.addRepository, params)

    def test_update_rejects_unknown_config_keys_apt(self):
        handler = FakeAptHandler()
        handler.repos['test1'] = {'repo_id': 'test1', 'enabled': True,
                                  'baseurl': 'http://example.com',
                                  'config': {'dist': 'focal', 'comps': []}}
        facade = _make_facade(handler)
        params = {'config': {'wrong_field': 'value'}}
        self.assertRaises(InvalidParameter, facade.updateRepository,
                          'test1', params)

    def test_add_accepts_valid_yum_config_keys(self):
        handler = FakeYumHandler()
        facade = _make_facade(handler)
        params = {'repo_id': 'valid1', 'baseurl': 'http://example.com',
                  'config': {'repo_name': 'My Repo', 'mirrorlist': ''}}
        repo_id = facade.addRepository(params)
        self.assertEqual('valid1', repo_id)

    def test_update_accepts_valid_yum_config_keys(self):
        handler = FakeYumHandler()
        handler.repos['r1'] = {'repo_id': 'r1', 'enabled': True,
                               'baseurl': 'http://a.com', 'config': {}}
        facade = _make_facade(handler)
        params = {'config': {'gpgcheck': True, 'gpgkey': 'http://k.com/key'}}
        result = facade.updateRepository('r1', params)
        self.assertEqual('r1', result)

    def test_add_accepts_valid_apt_config_keys(self):
        handler = FakeAptHandler()
        facade = _make_facade(handler)
        params = {'baseurl': 'http://archive.ubuntu.com/ubuntu/',
                  'config': {'dist': 'focal', 'comps': ['main']}}
        repo_id = facade.addRepository(params)
        self.assertIn('focal', repo_id)

    def test_update_accepts_valid_apt_config_keys(self):
        handler = FakeAptHandler()
        handler.repos['t1'] = {'repo_id': 't1', 'enabled': True,
                               'baseurl': 'http://a.com',
                               'config': {'dist': 'focal', 'comps': []}}
        facade = _make_facade(handler)
        params = {'config': {'dist': 'jammy'}}
        result = facade.updateRepository('t1', params)
        self.assertEqual('t1', result)


# ---------------------------------------------------------------------------
# Test: Duplicate detection
# ---------------------------------------------------------------------------

class TestDuplicateDetection(unittest.TestCase):
    """Verify that both YUM and APT paths reject duplicate repositories."""

    def test_yum_duplicate_repo_id_rejected(self):
        handler = FakeYumHandler()
        facade = _make_facade(handler)
        params = {'repo_id': 'dup1', 'baseurl': 'http://example.com'}
        facade.addRepository(params)
        self.assertRaises(InvalidOperation, facade.addRepository, params)

    def test_apt_duplicate_uri_dist_comps_rejected(self):
        handler = FakeAptHandler()
        facade = _make_facade(handler)
        params = {'baseurl': 'http://archive.ubuntu.com/ubuntu/',
                  'config': {'dist': 'focal', 'comps': ['main']}}
        facade.addRepository(params)
        self.assertRaises(InvalidOperation, facade.addRepository, params)

    def test_apt_different_dist_not_duplicate(self):
        handler = FakeAptHandler()
        facade = _make_facade(handler)
        params1 = {'baseurl': 'http://archive.ubuntu.com/ubuntu/',
                   'config': {'dist': 'focal', 'comps': ['main']}}
        params2 = {'baseurl': 'http://archive.ubuntu.com/ubuntu/',
                   'config': {'dist': 'jammy', 'comps': ['main']}}
        facade.addRepository(params1)
        repo_id2 = facade.addRepository(params2)
        self.assertIn('jammy', repo_id2)


# ---------------------------------------------------------------------------
# Test: Missing/invalid input
# ---------------------------------------------------------------------------

class TestMissingInput(unittest.TestCase):
    """Verify that missing required fields produce clean errors."""

    def test_apt_missing_baseurl(self):
        handler = FakeAptHandler()
        facade = _make_facade(handler)
        params = {'config': {'dist': 'focal'}}
        self.assertRaises(MissingParameter, facade.addRepository, params)

    def test_apt_missing_config(self):
        handler = FakeAptHandler()
        facade = _make_facade(handler)
        params = {'baseurl': 'http://example.com'}
        self.assertRaises(MissingParameter, facade.addRepository, params)

    def test_apt_missing_dist_in_config(self):
        handler = FakeAptHandler()
        facade = _make_facade(handler)
        params = {'baseurl': 'http://example.com',
                  'config': {'comps': ['main']}}
        self.assertRaises(MissingParameter, facade.addRepository, params)

    def test_empty_config_no_crash(self):
        """addRepository with empty config should not crash the facade."""
        handler = FakeYumHandler()
        facade = _make_facade(handler)
        params = {'repo_id': 'x', 'baseurl': 'http://example.com',
                  'config': {}}
        repo_id = facade.addRepository(params)
        self.assertEqual('x', repo_id)


# ---------------------------------------------------------------------------
# Test: Delete then operate
# ---------------------------------------------------------------------------

class TestDeleteThenOperate(unittest.TestCase):
    """Verify that operating on deleted repos raises NotFoundError."""

    def setUp(self):
        self.handler = FakeYumHandler()
        self.facade = _make_facade(self.handler)
        self.facade.addRepository(
            {'repo_id': 'ephemeral', 'baseurl': 'http://example.com'})
        self.facade.removeRepository('ephemeral')

    def test_lookup_after_delete(self):
        self.assertRaises(NotFoundError, self.facade.getRepository, 'ephemeral')

    def test_delete_after_delete(self):
        self.assertRaises(NotFoundError, self.facade.removeRepository,
                          'ephemeral')

    def test_enable_after_delete(self):
        self.assertRaises(NotFoundError, self.facade.enableRepository,
                          'ephemeral')

    def test_disable_after_delete(self):
        self.assertRaises(NotFoundError, self.facade.disableRepository,
                          'ephemeral')

    def test_update_after_delete(self):
        self.assertRaises(NotFoundError, self.facade.updateRepository,
                          'ephemeral', {'baseurl': 'http://new.com'})


# ---------------------------------------------------------------------------
# Test: Toggle then edit (state preservation)
# ---------------------------------------------------------------------------

class TestToggleThenEdit(unittest.TestCase):
    """Verify that editing a disabled repo preserves its disabled state."""

    def test_update_disabled_repo_stays_disabled(self):
        handler = FakeYumHandler()
        facade = _make_facade(handler)
        facade.addRepository(
            {'repo_id': 'myrepo', 'baseurl': 'http://example.com'})
        facade.disableRepository('myrepo')

        # Verify disabled
        info = facade.getRepository('myrepo')
        self.assertFalse(info['enabled'])

        # Update (change baseurl)
        facade.updateRepository('myrepo', {'baseurl': 'http://new.example.com'})

        # Should still be disabled
        info = facade.getRepository('myrepo')
        self.assertFalse(info['enabled'])

    def test_update_enabled_repo_stays_enabled(self):
        handler = FakeYumHandler()
        facade = _make_facade(handler)
        facade.addRepository(
            {'repo_id': 'myrepo2', 'baseurl': 'http://example.com'})

        # Update
        facade.updateRepository('myrepo2', {'baseurl': 'http://new.com'})

        # Should still be enabled
        info = facade.getRepository('myrepo2')
        self.assertTrue(info['enabled'])


# ---------------------------------------------------------------------------
# Test: Full lifecycle via MockModel
# ---------------------------------------------------------------------------

@unittest.skipUnless(_wok_available, 'wok not installed')
class TestMockModelLifecycle(unittest.TestCase):
    """Integration test using MockModel to exercise the full
    create -> update -> disable -> enable -> delete -> error chain."""

    def setUp(self):
        from wok.basemodel import Singleton
        Singleton._instances = {}

        self.tmp_store = '/tmp/gingerbase-repos-test'
        try:
            from wok.plugins.gingerbase.mockmodel import MockModel
            self.model = MockModel(objstore_loc=self.tmp_store)
        except Exception:
            self.skipTest('MockModel dependencies not available')

    def tearDown(self):
        import os
        if os.path.exists(self.tmp_store):
            os.unlink(self.tmp_store)

    def test_create_lookup_delete_lifecycle(self):
        repo_id = self.model.repositories_create(
            {'repo_id': 'lifecycle-test',
             'baseurl': 'http://example.com/repo'})
        self.assertEqual('lifecycle-test', repo_id)

        info = self.model.repository_lookup(repo_id)
        self.assertEqual(repo_id, info['repo_id'])
        self.assertTrue(info['enabled'])

        self.model.repository_delete(repo_id)
        self.assertRaises(NotFoundError, self.model.repository_lookup, repo_id)

    def test_disable_enable_cycle(self):
        repo_id = self.model.repositories_create(
            {'repo_id': 'toggle-test',
             'baseurl': 'http://example.com/repo'})

        self.model.repository_disable(repo_id)
        info = self.model.repository_lookup(repo_id)
        self.assertFalse(info['enabled'])

        self.model.repository_enable(repo_id)
        info = self.model.repository_lookup(repo_id)
        self.assertTrue(info['enabled'])

        # Cleanup
        self.model.repository_delete(repo_id)

    def test_enable_already_enabled_rejected(self):
        repo_id = self.model.repositories_create(
            {'repo_id': 'idem-test',
             'baseurl': 'http://example.com/repo'})
        # Newly created repo is enabled
        self.assertRaises(InvalidOperation,
                          self.model.repository_enable, repo_id)
        self.model.repository_delete(repo_id)

    def test_disable_already_disabled_rejected(self):
        repo_id = self.model.repositories_create(
            {'repo_id': 'idem-test2',
             'baseurl': 'http://example.com/repo'})
        self.model.repository_disable(repo_id)
        self.assertRaises(InvalidOperation,
                          self.model.repository_disable, repo_id)
        self.model.repository_delete(repo_id)

    def test_create_duplicate_rejected(self):
        self.model.repositories_create(
            {'repo_id': 'dup-test', 'baseurl': 'http://example.com/repo'})
        self.assertRaises(InvalidOperation,
                          self.model.repositories_create,
                          {'repo_id': 'dup-test',
                           'baseurl': 'http://other.com/repo'})
        self.model.repository_delete('dup-test')

    def test_create_invalid_config_rejected(self):
        self.assertRaises(InvalidParameter,
                          self.model.repositories_create,
                          {'repo_id': 'bad-cfg',
                           'baseurl': 'http://example.com',
                           'config': {'nonsense_key': 'value'}})

    def test_update_invalid_config_rejected(self):
        repo_id = self.model.repositories_create(
            {'repo_id': 'upd-cfg-test',
             'baseurl': 'http://example.com/repo'})
        self.assertRaises(InvalidParameter,
                          self.model.repository_update, repo_id,
                          {'config': {'invalid_key': 'bad'}})
        self.model.repository_delete(repo_id)

    def test_update_nonexistent_rejected(self):
        self.assertRaises(NotFoundError,
                          self.model.repository_update, 'ghost',
                          {'baseurl': 'http://x.com'})

    def test_invalid_url_format_rejected(self):
        self.assertRaises(InvalidParameter,
                          self.model.repositories_create,
                          {'repo_id': 'bad-url',
                           'baseurl': '://missing-protocol.com'})

    def test_update_with_invalid_url_rejected(self):
        repo_id = self.model.repositories_create(
            {'repo_id': 'url-test',
             'baseurl': 'http://example.com/repo'})
        self.assertRaises(InvalidParameter,
                          self.model.repository_update, repo_id,
                          {'baseurl': 'badproto://invalid'})
        self.model.repository_delete(repo_id)


if __name__ == '__main__':
    unittest.main()

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
import glob
import logging
import os
import re
import shutil
import subprocess
import time

from wok.asynctask import AsyncTask
from wok.exception import InvalidParameter
from wok.exception import NotFoundError
from wok.exception import OperationFailed
from wok.exception import WokException
from wok.model.tasks import TaskModel
from wok.plugins.gingerbase import config
from wok.utils import run_command
from wok.utils import wok_log


# ---------------------------------------------------------------------------
# Shared helpers — single source of truth for file discovery and name rules
# ---------------------------------------------------------------------------

# Characters allowed in a debug report name: letters, digits, underscore,
# hyphen.  Underscores are explicitly allowed (i18n messages GGBDR0007E /
# GGBDR0013E and API.json schemas both permit them, and sosreport accepts
# them in the --name argument).
_NAME_RE = re.compile(r'^[A-Za-z0-9_-]+$')


def _find_report_file(name):
    """Locate the report file for *name* on disk.

    Returns the full path to the first matching file, or ``None`` when no
    file exists.  All lifecycle operations (lookup / update / delete) share
    this helper so that glob patterns and error handling stay consistent.
    """
    path = config.get_debugreports_path()
    pattern = os.path.join(path, glob.escape(name) + '.*')
    matches = glob.glob(pattern)
    return matches[0] if matches else None


def _get_all_report_names():
    """Return a sorted list of all report names in the storage directory.

    Uses ``os.listdir`` instead of ``glob('*.*')`` so that the discovery
    logic is explicit and easy to reason about.  Only regular files with
    at least one dot in the name are considered reports.
    """
    path = config.get_debugreports_path()
    names = set()
    try:
        for entry in os.listdir(path):
            full = os.path.join(path, entry)
            if os.path.isfile(full) and '.' in entry:
                # Name is everything before the first dot.
                names.add(entry[:entry.find('.')])
    except OSError:
        pass
    return sorted(names)


def _validate_name(name):
    """Validate a report name synchronously.

    Raises ``InvalidParameter`` immediately so the caller can return a
    clear HTTP error *before* entering an async task.
    """
    if not name or not name.strip():
        raise InvalidParameter('GGBDR0013E', {'name': name or ''})
    if not _NAME_RE.match(name):
        raise InvalidParameter('GGBDR0007E', {'name': name})


def _extract_extension(filename):
    """Return the extension portion of *filename* (e.g. ``'.tar.xz'``).

    Uses the first-dot position rather than ``os.path.splitext`` so that
    multi-segment extensions like ``.tar.xz`` are preserved intact.
    """
    dot_pos = filename.find('.')
    return filename[dot_pos:] if dot_pos >= 0 else ''


# ---------------------------------------------------------------------------
# Model classes
# ---------------------------------------------------------------------------

class DebugReportsModel(object):
    def __init__(self, **kargs):
        self.objstore = kargs['objstore']
        self.task = TaskModel(**kargs)

    def create(self, params):
        ident = params.get('name', '').strip()
        # Generate a name with time and millisec precision, if necessary
        if not ident:
            ident = 'report-' + str(int(time.time() * 1000))

        # Validate name format synchronously — invalid names produce HTTP 400
        # instead of failing deep inside an async task.
        _validate_name(ident)

        if ident in _get_all_report_names():
            raise InvalidParameter('GGBDR0008E', {'name': ident})

        taskid = self._gen_debugreport_file(ident)
        return self.task.lookup(taskid)

    def get_list(self):
        return _get_all_report_names()

    def _gen_debugreport_file(self, name):
        gen_cmd = self.get_system_report_tool()

        if gen_cmd is not None:
            return AsyncTask('/plugins/gingerbase/debugreports/%s' % name,
                             gen_cmd, name).id

        raise OperationFailed('GGBDR0002E')

    @staticmethod
    def debugreport_generate(cb, name):
        def log_error(e):
            _logger = logging.getLogger('Model')
            _logger.warning('Exception in generating debug file: %s', e)

        # Track intermediate files for cleanup on failure.
        sosreport_file = None
        md5_file = None
        dbginfo_reportfile = None
        final_tar = None

        try:
            # Sosreport generation
            sosreport_file = sosreport_collection(name)
            md5_file = sosreport_file + '.md5'
            report_file_extension = _extract_extension(
                os.path.basename(sosreport_file))

            # If the platform is a system Z machine.
            path_debugreport = '/var/tmp/'
            dbgreport_regex = (
                path_debugreport + 'DBGINFO-'
                '[0-9][0-9][0-9][0-9]-'
                '[0-9][0-9]-'
                '[0-9][0-9]-'
                '[0-9][0-9]-'
                '[0-9][0-9]-'
                '[0-9][0-9]-'
                '*-'
                '*.tgz')
            command = ['/usr/sbin/dbginfo.sh', '-d', path_debugreport]
            output, error, retcode = run_command(command)
            if retcode != 0:
                raise OperationFailed('GGBDR0009E',
                                      {'retcode': retcode, 'err': error})

            # Checking for dbginforeport file.
            dbginfo_report = []
            if output.splitlines():
                dbginfo_report = glob.glob(dbgreport_regex)
            if not dbginfo_report:
                raise OperationFailed('GGBDR0012E',
                                      {'retcode': retcode, 'err': error})
            dbginfo_reportfile = dbginfo_report[-1]

            final_tar_name = name + report_file_extension
            sosreport_tar = sosreport_file.split('/', 3)[3]
            dbginfo_tar = dbginfo_reportfile.split('/', 3)[3]
            msg = ('Compressing the sosreport and debug info files into '
                   'final report file')
            wok_log.info(msg)

            # Compressing the sosreport and dbginfo reports into one
            # tar file
            command = ['tar', '-cvzf', '%s' % final_tar_name,
                       '-C', path_debugreport, dbginfo_tar,
                       sosreport_tar]
            output, error, retcode = run_command(command)
            if retcode != 0:
                raise OperationFailed('GGBDR0010E',
                                      {'retcode': retcode,
                                       'error': error})

            final_tar = final_tar_name
            path = config.get_debugreports_path()
            dbg_target = os.path.join(path,
                                      name + report_file_extension)
            # Moving final tar file to debugreports path
            msg = 'Moving final debug report file "%s" to "%s"' % \
                  (final_tar_name, dbg_target)
            wok_log.info(msg)
            shutil.move(final_tar_name, dbg_target)
            final_tar = None  # successfully moved

            # Deleting the sosreport md5 file
            delete_the_sosreport_md5_file(md5_file)
            md5_file = None  # successfully cleaned

            # Deleting the dbginfo report file
            msg = 'Deleting the dbginfo file "%s" ' \
                  % dbginfo_reportfile
            wok_log.info(msg)
            os.remove(dbginfo_reportfile)
            dbginfo_reportfile = None  # successfully cleaned

            # Deleting the sosreport file
            msg = 'Deleting the sosreport file "%s" ' % sosreport_file
            wok_log.info(msg)
            os.remove(sosreport_file)
            sosreport_file = None  # successfully cleaned

            wok_log.info('The debug report file has been moved')
            cb('OK', True)
            return

        except WokException as e:
            log_error(e)
            raise

        except OSError as e:
            log_error(e)
            raise

        except Exception as e:
            # No need to call cb to update the task status here.
            # The task object will catch the exception raised here
            # and update the task status there
            log_error(e)
            raise OperationFailed('GGBDR0011E', {'name': name, 'err': e})

        finally:
            # Clean up any intermediate files that were not successfully
            # removed or moved above.  Each variable is set to None once
            # its file has been handled, so only orphans remain here.
            for f in [sosreport_file, md5_file,
                      dbginfo_reportfile, final_tar]:
                if f is not None and os.path.exists(f):
                    try:
                        os.remove(f)
                    except OSError:
                        pass

    @staticmethod
    def sosreport_generate(cb, name):
        def log_error(e):
            _logger = logging.getLogger('Model')
            _logger.warning('Exception in generating debug file: %s', e)

        # Track intermediate files for cleanup on failure.
        sosreport_file = None
        md5_file = None

        try:
            # Sosreport collection
            sosreport_file = sosreport_collection(name)
            md5_file = sosreport_file + '.md5'
            report_file_extension = _extract_extension(
                os.path.basename(sosreport_file))
            path = config.get_debugreports_path()
            sosreport_target = os.path.join(path,
                                            name + report_file_extension)
            msg = 'Moving debug report file "%s" to "%s"' \
                  % (sosreport_file, sosreport_target)
            wok_log.info(msg)
            shutil.move(sosreport_file, sosreport_target)
            sosreport_file = None  # successfully moved
            delete_the_sosreport_md5_file(md5_file)
            md5_file = None  # successfully cleaned
            cb('OK', True)
            return

        except WokException as e:
            log_error(e)
            raise

        except OSError as e:
            log_error(e)
            raise

        except Exception as e:
            # No need to call cb to update the task status here.
            # The task object will catch the exception raised here
            # and update the task status there
            log_error(e)
            raise OperationFailed('GGBDR0005E', {'name': name, 'err': e})

        finally:
            for f in [sosreport_file, md5_file]:
                if f is not None and os.path.exists(f):
                    try:
                        os.remove(f)
                    except OSError:
                        pass

    @staticmethod
    def get_system_report_tool():
        # Please add new possible debug report command here
        # and implement the report generating function
        # based on the new report command
        report_tools = ({'cmd': '/usr/sbin/dbginfo.sh --help',
                         'fn': DebugReportsModel.debugreport_generate},
                        {'cmd': 'sosreport --help',
                         'fn': DebugReportsModel.sosreport_generate},)

        # check if the command can be found by shell one by one
        for helper_tool in report_tools:
            try:
                retcode = subprocess.call(helper_tool['cmd'], shell=True,
                                          stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE)
                if retcode == 0:
                    return helper_tool['fn']
            except Exception as e:
                wok_log.info('Exception running command: %s', e)

        return None


class DebugReportModel(object):
    def __init__(self, **kargs):
        pass

    def lookup(self, name):
        file_target = _find_report_file(name)
        if file_target is None:
            raise NotFoundError('GGBDR0001E', {'name': name})

        # Use st_ctime (inode change time on Linux) instead of st_mtime.
        # st_mtime is updated by mv/rename, causing the displayed "creation
        # time" to jump on every rename; st_ctime is not affected by rename.
        ctime = os.stat(file_target).st_ctime
        ctime = time.strftime('%Y-%m-%d-%H:%M:%S', time.localtime(ctime))
        basename = os.path.split(file_target)[-1]
        uri = os.path.join('plugins/gingerbase/data/debugreports', basename)
        return {'uri': uri,
                'ctime': ctime}

    def update(self, name, params):
        file_source = _find_report_file(name)
        if file_source is None:
            raise NotFoundError('GGBDR0001E', {'name': name})

        new_name = params['name']

        # Build the target path by preserving the original extension.
        # This avoids the old str.replace approach which could corrupt
        # the extension when the report name appeared in it (e.g. a
        # report named "tar" stored as "tar.tar.xz").
        extension = _extract_extension(os.path.basename(file_source))
        file_target = os.path.join(config.get_debugreports_path(),
                                   new_name + extension)
        if os.path.isfile(file_target):
            raise InvalidParameter('GGBDR0008E', {'name': new_name})

        shutil.move(file_source, file_target)
        wok_log.info('%s renamed to %s' % (file_source, file_target))
        return new_name

    def delete(self, name):
        file_target = _find_report_file(name)
        if file_target is None:
            raise NotFoundError('GGBDR0001E', {'name': name})

        os.remove(file_target)


class DebugReportContentModel(object):
    def __init__(self, **kargs):
        self._debugreport = DebugReportModel()

    def lookup(self, name):
        return self._debugreport.lookup(name)


def delete_the_sosreport_md5_file(md5_file):
    """Delete the md5 checksum file left behind by sosreport.

    If the file does not exist (e.g. already cleaned up or sosreport
    version that doesn't produce md5), log a note and return without
    error.
    """
    msg = 'Deleting report md5 file: "%s"' % md5_file
    wok_log.info(msg)
    if not os.path.exists(md5_file):
        wok_log.info('MD5 file not found, skipping: "%s"', md5_file)
        return
    with open(md5_file) as f:
        md5 = f.read().strip()
        wok_log.info('Md5 file content: "%s"', md5)
    os.remove(md5_file)


def sosreport_collection(name):
    """
    Code for the collection of sosreport in the path
    /var/tmp as specified in the command.
    """
    path_sosreport = '/var/tmp/'
    command = ['sosreport', '--batch', '--name=%s' % name,
               '--tmp-dir=%s' % path_sosreport]
    output, error, retcode = run_command(command)
    if retcode != 0:
        raise OperationFailed('GGBDR0003E', {'name': name,
                                             'err': error})
    # Checking for sosreport file generation.
    sosreport_file = []
    if output.splitlines():
        sosreport_pattern = path_sosreport + 'sosreport-' \
            + name + '-' + '*.tar.xz'
        sosreport_file = glob.glob(sosreport_pattern)
    if not sosreport_file:
        raise OperationFailed('GGBDR0004E', {'name': name,
                                             'err': retcode})
    return sosreport_file[0]

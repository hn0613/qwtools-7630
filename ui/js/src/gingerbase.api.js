/*
 * Project Ginger Base
 *
 * Copyright IBM Corp, 2015-2016
 *
 * Code derived from Project Kimchi
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
var gingerbase = {

    widget: {},

    _activeTasks: {},

    /**
     *
     * Get host capabilities
     * suc: callback if succeed err: callback if failed
     */
    getCapabilities : function(suc, err, done) {
        done = typeof done !== 'undefined' ? done: function(){};
        wok.requestJSON({
            url : "plugins/gingerbase/host/capabilities",
            type : "GET",
            contentType : "application/json",
            dataType : "json",
            success: suc,
            error: err,
            complete: done
        });
    },

    /**
     * Get the i18 strings.
     */
    getI18n: function(suc, err, url, sync) {
        wok.requestJSON({
            url : url ? url : 'plugins/gingerbase/i18n.json',
            type : 'GET',
            resend: true,
            dataType : 'json',
            async : !sync,
            success : suc,
            error: err
        });
    },

    /**
     * Get the host static information.
     */
    getHost: function(suc, err) {
        wok.requestJSON({
            url : 'plugins/gingerbase/host',
            type : 'GET',
            resend: true,
            contentType : 'application/json',
            dataType : 'json',
            success : suc,
            error: err
        });
    },

    /**
     * Get the dynamic host stats (usually used for monitoring).
     */
    getHostStats : function(suc, err) {
        wok.requestJSON({
            url : 'plugins/gingerbase/host/stats',
            type : 'GET',
            contentType : 'application/json',
            headers: {'Wok-Robot': 'wok-robot'},
            dataType : 'json',
            success : suc,
            error: err
        });
    },

    /**
     * Get the historic host stats.
     */
    getHostStatsHistory : function(suc, err) {
        wok.requestJSON({
            url : 'plugins/gingerbase/host/stats/history',
            type : 'GET',
            resend: true,
            contentType : 'application/json',
            headers: {'Wok-Robot': 'wok-robot'},
            dataType : 'json',
            success : suc,
            error: err
        });
    },

    // Deprecated: use getTaskAsync instead to avoid blocking the UI thread
    getTask : function(taskId, suc, err) {
        wok.requestJSON({
            url : 'plugins/gingerbase/tasks/' + encodeURIComponent(taskId),
            type : 'GET',
            async: false,
            contentType : 'application/json',
            dataType : 'json',
            success : suc,
            error : err
        });
    },

    getTaskAsync : function(taskId, suc, err) {
        wok.requestJSON({
            url : 'plugins/gingerbase/tasks/' + encodeURIComponent(taskId),
            type : 'GET',
            contentType : 'application/json',
            dataType : 'json',
            success : suc,
            error : err
        });
    },

    getTasksByFilter : function(filter, suc, err, sync) {
        wok.requestJSON({
            url : 'plugins/gingerbase/tasks?' + filter,
            type : 'GET',
            contentType : 'application/json',
            dataType : 'json',
            async : !sync,
            success : suc,
            error : err
        });
    },

    TaskTracker : function(taskID, options) {
        var defaults = {
            interval: 2000,
            maxRetries: 0,
            timeout: 0,
            onProgress: null,
            onSuccess: null,
            onError: null,
            onUnreachable: null
        };

        var settings = $.extend({}, defaults, options);
        var timer = null;
        var startTime = Date.now();
        var retryCount = 0;
        var stopped = false;

        var self = {
            taskID: taskID,
            stop: stop,
            start: start
        };

        function stop() {
            stopped = true;
            if (timer) {
                clearTimeout(timer);
                timer = null;
            }
            delete gingerbase._activeTasks[taskID];
        }

        function poll() {
            if (stopped) return;

            if (settings.timeout > 0 && (Date.now() - startTime) > settings.timeout) {
                stop();
                settings.onUnreachable && settings.onUnreachable({reason: 'timeout'});
                return;
            }

            gingerbase.getTaskAsync(taskID, function(result) {
                if (stopped) return;
                retryCount = 0;

                switch (result['status']) {
                case 'running':
                    settings.onProgress && settings.onProgress(result);
                    timer = setTimeout(poll, settings.interval);
                    break;
                case 'finished':
                    stop();
                    settings.onSuccess && settings.onSuccess(result);
                    break;
                case 'failed':
                    stop();
                    settings.onError && settings.onError(result);
                    break;
                default:
                    timer = setTimeout(poll, settings.interval);
                    break;
                }
            }, function(error) {
                if (stopped) return;
                retryCount++;
                if (settings.maxRetries > 0 && retryCount >= settings.maxRetries) {
                    stop();
                    settings.onUnreachable && settings.onUnreachable(error);
                } else {
                    timer = setTimeout(poll, settings.interval);
                }
            });
        }

        function start() {
            if (gingerbase._activeTasks[taskID]) {
                return gingerbase._activeTasks[taskID];
            }
            stopped = false;
            startTime = Date.now();
            retryCount = 0;
            gingerbase._activeTasks[taskID] = self;
            poll();
            return self;
        }

        return start();
    },

    recoverTasks : function(filter, options) {
        gingerbase.getTasksByFilter(filter, function(tasks) {
            for (var i = 0; i < tasks.length; i++) {
                if (!gingerbase._activeTasks[tasks[i].id]) {
                    new gingerbase.TaskTracker(tasks[i].id, options);
                }
            }
        }, null);
    },

    listReports : function(suc, err) {
        wok.requestJSON({
            url : 'plugins/gingerbase/debugreports',
            type : 'GET',
            contentType : 'application/json',
            dataType : 'json',
            resend: true,
            success : suc,
            error : err
        });
    },

    trackTask : function(taskID, suc, err, progress) {
        return new gingerbase.TaskTracker(taskID, {
            interval: 2000,
            maxRetries: 60,
            onProgress: progress,
            onSuccess: suc,
            onError: err,
            onUnreachable: err
        });
    },

    createReport: function(settings, suc, err, progress) {
        var onResponse = function(data) {
            taskID = data['id'];
            gingerbase.trackTask(taskID, suc, err, progress);
        };

        wok.requestJSON({
            url : 'plugins/gingerbase/debugreports',
            type : "POST",
            contentType : "application/json",
            data : JSON.stringify(settings),
            dataType : "json",
            success : onResponse,
            error : err
        });
    },

    renameReport : function(name, settings, suc, err) {
        $.ajax({
            url : "plugins/gingerbase/debugreports/" + encodeURIComponent(name),
            type : 'PUT',
            contentType : 'application/json',
            data : JSON.stringify(settings),
            dataType : 'json',
            success: suc,
            error: err
        });
    },

    deleteReport: function(settings, suc, err) {
        var reportName = encodeURIComponent(settings['name']);
        wok.requestJSON({
            url : 'plugins/gingerbase/debugreports/' + reportName,
            type : 'DELETE',
            contentType : 'application/json',
            dataType : 'json',
            success : suc,
            error : err
        });
    },

    downloadReport: function(settings, suc, err) {
        window.open(settings['file'],'_blank');
    },

    shutdown: function(settings, suc, err) {
        var reboot = settings && settings['reboot'] === true;
        var url = 'plugins/gingerbase/host/' + (reboot ? 'reboot' : 'shutdown');
        wok.requestJSON({
            url : url,
            type : 'POST',
            contentType : 'application/json',
            dataType : 'json',
            success : suc,
            error : err
        });
    },

    listSoftwareUpdates : function(suc, err) {
        wok.requestJSON({
            url : 'plugins/gingerbase/host/packagesupdate',
            type : 'GET',
            contentType : 'application/json',
            dataType : 'json',
            resend: true,
            success : suc,
            error : err
        });
    },

    getPackageDeps : function(pkg, suc, err) {
        var pkg_name = encodeURIComponent(pkg);
        wok.requestJSON({
            url : "plugins/gingerbase/host/packagesupdate/" + pkg_name + "/deps",
            type : 'GET',
            contentType : 'application/json',
            dataType : 'json',
            async: false,
            success : suc,
            error : err
        });
    },

    softwareUpdateProgress : function(suc, err, progress) {
        wok.requestJSON({
            url : 'plugins/gingerbase/host/swupdateprogress',
            type : "GET",
            contentType : "application/json",
            dataType : "json",
            success : function(data) {
                new gingerbase.TaskTracker(data['id'], {
                    interval: 1000,
                    maxRetries: 120,
                    onProgress: progress,
                    onSuccess: suc,
                    onError: suc,
                    onUnreachable: err
                });
            },
            error : err
        });
    },

    updateSoftware : function(pack, suc, err, progress) {
        wok.requestJSON({
            url : 'plugins/gingerbase/host/packagesupdate/' + pack + '/upgrade',
            type : "POST",
            contentType : "application/json",
            dataType : "json",
            success : function(data) {
                new gingerbase.TaskTracker(data['id'], {
                    interval: 1000,
                    maxRetries: 120,
                    onProgress: function() {
                        progress && progress();
                    },
                    onSuccess: suc,
                    onError: suc,
                    onUnreachable: err
                });
            },
            error : err
        });
    },

    updateAllSoftware : function(suc, err, progress) {
        wok.requestJSON({
            url : 'plugins/gingerbase/host/swupdate',
            type : "POST",
            contentType : "application/json",
            dataType : "json",
            success : function(data) {
                new gingerbase.TaskTracker(data['id'], {
                    interval: 700,
                    maxRetries: 170,
                    onProgress: progress,
                    onSuccess: suc,
                    onError: suc,
                    onUnreachable: err
                });
            },
            error : err
        });
    },

    createRepository : function(settings, suc, err) {
        wok.requestJSON({
            url : "plugins/gingerbase/host/repositories",
            type : "POST",
            contentType : "application/json",
            data : JSON.stringify(settings),
            dataType : "json",
            success: suc,
            error: err
        });
    },

    retrieveRepository : function(repository, suc, err) {
        var reposID = encodeURIComponent(repository);
        wok.requestJSON({
            url : "plugins/gingerbase/host/repositories/" + reposID,
            type : 'GET',
            contentType : 'application/json',
            dataType : 'json',
            success : suc,
            error : err
        });
    },

    updateRepository : function(name, settings, suc, err) {
        var reposID = encodeURIComponent(name);
        $.ajax({
            url : "plugins/gingerbase/host/repositories/" + reposID,
            type : 'PUT',
            contentType : 'application/json',
            data : JSON.stringify(settings),
            dataType : 'json',
            success : suc,
            error : err
        });
    },

    enableRepository : function(name, enable, suc, err) {
        var reposID = encodeURIComponent(name);
        $.ajax({
            url : "plugins/gingerbase/host/repositories/" + reposID +
                  '/' + (enable === true ? 'enable' : 'disable'),
            type : 'POST',
            contentType : 'application/json',
            dataType : 'json',
            success : suc,
            error : err
        });
    },

    deleteRepository : function(repository, suc, err) {
        var reposID = encodeURIComponent(repository);
        wok.requestJSON({
            url : 'plugins/gingerbase/host/repositories/' + reposID,
            type : 'DELETE',
            contentType : 'application/json',
            dataType : 'json',
            success : suc,
            error : err
        });
    },

    listRepositories : function(suc, err) {
        wok.requestJSON({
            url : 'plugins/gingerbase/host/repositories',
            type : 'GET',
            contentType : 'application/json',
            dataType : 'json',
            resend: true,
            success : suc,
            error : err
        });
    },

    getCPUInfo : function(suc, err) {
        wok.requestJSON({
            url : 'plugins/gingerbase/host/cpuinfo',
            type : 'GET',
            contentType : 'application/json',
            dataType : 'json',
            resend : true,
            success : suc,
            error : err ? err : function(data) {
                wok.message.error(data.responseJSON.reason);
            }
        });
    },
    // get smt data
    getSMT: function(suc, err) {
        wok.requestJSON({
            url : 'plugins/gingerbase/host/smt',
            type : 'GET',
            resend: true,
            contentType : 'application/json',
            dataType : 'json',
            resend : true,
            success : suc,
            error: err
        });
    },
    enablesmt : function(value, suc, err) {
        wok.requestJSON({
            url : "plugins/gingerbase/host/smt/enable",
            type : "POST",
            contentType : "application/json",
            data : JSON.stringify(value),
            dataType : "json",
            success: suc,
            error: err
        });
    },
    disablesmt : function(value, suc, err) {
        wok.requestJSON({
            url : "plugins/gingerbase/host/smt/disable",
            type : "POST",
            contentType : "application/json",
            data : JSON.stringify(value),
            dataType : "json",
            success: suc,
            error: err
        });
    },
    capabilities: undefined
};

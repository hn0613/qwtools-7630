/*
 * Project Ginger Base
 *
 * Copyright IBM Corp, 2015-2017
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

/**
 * gingerbase.taskTracker — Unified long-running task polling engine.
 *
 * Manages polling of Wok AsyncTask instances with consistent state handling,
 * double-submission protection, abort support, and network error tolerance.
 *
 * This module is UI-agnostic. Callers provide callbacks for success, error,
 * and progress events.
 */
(function() {
    'use strict';

    var POLL_INTERVAL = 2000;
    var MAX_NETWORK_ERRORS = 5;
    var instances = {};

    /**
     * Start tracking a task by its ID.
     *
     * @param {Object}   opts
     * @param {string}   opts.key         Unique guard key (e.g. 'swupdate', 'report:myname')
     * @param {string}   opts.taskId      AsyncTask ID from the POST response
     * @param {Function} [opts.onSuccess]  fn(taskResult) — called on status='finished'
     * @param {Function} [opts.onError]    fn(taskResult) — called on status='failed'
     * @param {Function} [opts.onProgress] fn(taskResult) — called on each status='running'
     * @returns {Object|null} Tracker handle {key, taskId}, or null if key already tracked
     */
    function start(opts) {
        if (instances[opts.key]) {
            return null;
        }

        var inst = {
            key:        opts.key,
            taskId:     opts.taskId,
            onSuccess:  opts.onSuccess  || function() {},
            onError:    opts.onError    || function() {},
            onProgress: opts.onProgress || function() {},
            _generation: 0,
            _aborted:    false,
            _netErrors:  0
        };

        instances[opts.key] = inst;
        _poll(inst, inst._generation);
        return { key: opts.key, taskId: opts.taskId };
    }

    /**
     * Submit a POST request to create a task, then start polling.
     * Reserves the key immediately to prevent double-click races.
     *
     * @param {Object}   opts
     * @param {string}   opts.key          Unique guard key
     * @param {string}   opts.url          POST endpoint URL
     * @param {Object}   [opts.data]       POST body (will be JSON-serialized)
     * @param {Function} [opts.onSuccess]   fn(taskResult)
     * @param {Function} [opts.onError]     fn(taskResult)
     * @param {Function} [opts.onProgress]  fn(taskResult)
     * @param {Function} [opts.onPostError] fn(jqXHR) — POST-level error (before task exists)
     * @returns {boolean} false if key is already tracked, true if submission started
     */
    function submit(opts) {
        if (instances[opts.key]) {
            return false;
        }

        var placeholder = { _reserved: true, key: opts.key };
        instances[opts.key] = placeholder;

        wok.requestJSON({
            url: opts.url,
            type: 'POST',
            contentType: 'application/json',
            data: opts.data ? JSON.stringify(opts.data) : undefined,
            dataType: 'json',
            success: function(data) {
                delete instances[opts.key];
                start({
                    key:        opts.key,
                    taskId:     data['id'],
                    onSuccess:  opts.onSuccess,
                    onError:    opts.onError,
                    onProgress: opts.onProgress
                });
            },
            error: function(jqXHR) {
                delete instances[opts.key];
                opts.onPostError && opts.onPostError(jqXHR);
            }
        });

        return true;
    }

    /**
     * Abort tracking for a specific key.
     * No callbacks will fire after abort.
     */
    function abort(key) {
        var inst = instances[key];
        if (inst) {
            inst._aborted = true;
            inst._generation++;
            delete instances[key];
        }
    }

    /**
     * Abort all active trackers. Call on component teardown.
     */
    function abortAll() {
        for (var key in instances) {
            if (instances.hasOwnProperty(key)) {
                instances[key]._aborted = true;
                instances[key]._generation++;
            }
        }
        instances = {};
    }

    /**
     * Check if a key is currently being tracked.
     */
    function isTracked(key) {
        return !!instances[key] && !instances[key]._reserved;
    }

    /**
     * Get all tracked task IDs (for recovery deduplication).
     */
    function getTrackedTaskIds() {
        var ids = [];
        for (var key in instances) {
            if (instances.hasOwnProperty(key) && !instances[key]._reserved) {
                ids.push(instances[key].taskId);
            }
        }
        return ids;
    }

    /**
     * Internal: poll cycle with generation-based staleness detection.
     * Uses async getTask to avoid blocking the UI thread.
     */
    function _poll(inst, generation) {
        if (inst._aborted || generation !== inst._generation) {
            return;
        }

        gingerbase.getTask(inst.taskId, function(result) {
            if (inst._aborted || generation !== inst._generation) {
                return;
            }

            inst._netErrors = 0;
            var status = result['status'];

            switch (status) {
            case 'running':
                inst.onProgress(result);
                setTimeout(function() {
                    _poll(inst, generation);
                }, POLL_INTERVAL);
                break;
            case 'finished':
                delete instances[inst.key];
                inst.onSuccess(result);
                break;
            case 'failed':
                delete instances[inst.key];
                inst.onError(result);
                break;
            default:
                setTimeout(function() {
                    _poll(inst, generation);
                }, POLL_INTERVAL);
                break;
            }
        }, function() {
            if (inst._aborted || generation !== inst._generation) {
                return;
            }

            inst._netErrors++;
            if (inst._netErrors >= MAX_NETWORK_ERRORS) {
                delete instances[inst.key];
                inst.onError({
                    status:  'failed',
                    message: 'Connection lost after ' + MAX_NETWORK_ERRORS + ' attempts'
                });
                return;
            }

            setTimeout(function() {
                _poll(inst, generation);
            }, POLL_INTERVAL * inst._netErrors);
        });
    }

    /**
     * Debug helper — inspect active trackers from browser console.
     */
    function _debug() {
        var result = {};
        for (var key in instances) {
            if (instances.hasOwnProperty(key)) {
                result[key] = {
                    taskId:    instances[key].taskId,
                    aborted:   instances[key]._aborted,
                    reserved:  !!instances[key]._reserved,
                    netErrors: instances[key]._netErrors || 0
                };
            }
        }
        console.table(result);
        return result;
    }

    gingerbase.taskTracker = {
        start:             start,
        submit:            submit,
        abort:             abort,
        abortAll:          abortAll,
        isTracked:         isTracked,
        getTrackedTaskIds: getTrackedTaskIds,
        POLL_INTERVAL:     POLL_INTERVAL,
        MAX_NETWORK_ERRORS: MAX_NETWORK_ERRORS,
        _debug:            _debug
    };
})();

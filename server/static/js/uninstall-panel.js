/* AppEase - Deploy Software, Uninstall mode.
 *
 * Opens in Install mode, always: nothing reads or writes storage, cookie or
 * URL. Switching modes clears both sides' selections and searches.
 *
 * Only a view: the list and every reason come from
 * /api/uninstall-candidates (worded by uninstall_blocker()), and
 * /uninstall-batch re-checks every row. Names are set with textContent:
 * they came from a library form or a lab PC, and none is markup. Programs
 * group by the record's identity, never a library entry or a name.
 */
(function () {
    var panel = document.getElementById('uninstall-panel');
    var installForm = document.getElementById('deploy-batch-form');
    var modeButtons = Array.prototype.slice.call(document.querySelectorAll('[data-deploy-mode]'));
    if (!panel || !installForm || modeButtons.length === 0) {
        return;
    }

    var description = document.getElementById('deploy-description');
    var statusLine = document.getElementById('uninstall-status');
    var compCount = document.getElementById('uninstall-computers-count');
    var compSearch = document.getElementById('uninstall-computers-search');
    var compEmpty = document.getElementById('uninstall-computers-empty');
    var compList = document.getElementById('uninstall-computers-list');
    var progSearch = document.getElementById('uninstall-programs-search');
    var progEmpty = document.getElementById('uninstall-programs-empty');
    var progList = document.getElementById('uninstall-programs-list');
    var selWrap = document.getElementById('uninstall-selection');
    var selEmpty = document.getElementById('uninstall-selection-empty');
    var selProgGroup = document.getElementById('uninstall-selection-programs-group');
    var selProgBox = document.getElementById('uninstall-selection-programs');
    var selProgCount = document.getElementById('uninstall-selection-programs-count');
    var selCompGroup = document.getElementById('uninstall-selection-computers-group');
    var selCompBox = document.getElementById('uninstall-selection-computers');
    var selCompCount = document.getElementById('uninstall-selection-computers-count');
    var tallyPrograms = document.getElementById('uninstall-tally-programs');
    var tallyComputers = document.getElementById('uninstall-tally-computers');
    var tallySkipped = document.getElementById('uninstall-tally-skipped');
    var tallyJobs = document.getElementById('uninstall-tally-jobs');
    var uninstallButton = document.getElementById('uninstall-button');
    var dialog = document.getElementById('uninstall-confirm');
    var dialogTitle = document.getElementById('uninstall-confirm-title');
    var dialogPrograms = document.getElementById('uninstall-confirm-programs');
    var dialogSkippedWrap = document.getElementById('uninstall-confirm-skipped-wrap');
    var dialogSkippedCount = document.getElementById('uninstall-confirm-skipped-count');
    var dialogSkipped = document.getElementById('uninstall-confirm-skipped');
    var dialogCancel = document.getElementById('uninstall-confirm-cancel');
    var dialogOk = document.getElementById('uninstall-confirm-ok');

    var mode = 'install';
    var data = null;
    var loadSeq = 0;              // a newer load makes an older reply stale
    var selectedComputers = {};
    var selectedGroups = {};

    function deploy() {
        return window.AppEaseDeploy || null;
    }

    function plural(n, one, many) {
        return n + ' ' + (n === 1 ? one : many);
    }

    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) { node.className = className; }
        if (text !== undefined && text !== null) { node.textContent = text; }
        return node;
    }

    function setStatus(text) {
        statusLine.textContent = text || '';
        statusLine.classList.toggle('hidden', !text);
    }

    // ---- Mode ------------------------------------------------------------

    function clearUninstall() {
        selectedComputers = {};
        selectedGroups = {};
        compSearch.value = '';
        progSearch.value = '';
    }

    function setMode(next) {
        if (next === mode) {
            return;
        }
        mode = next;
        // Both sides start clean, whichever way the switch went.
        clearUninstall();
        if (deploy() && deploy().resetSelections) {
            deploy().resetSelections();
        }
        modeButtons.forEach(function (button) {
            button.setAttribute('aria-pressed', button.getAttribute('data-deploy-mode') === mode ? 'true' : 'false');
        });
        installForm.classList.toggle('hidden', mode !== 'install');
        panel.classList.toggle('hidden', mode !== 'uninstall');
        if (description) {
            description.textContent = description.getAttribute(mode === 'uninstall' ? 'data-uninstall-text' : 'data-install-text') || '';
        }
        if (mode === 'uninstall') {
            load();
        } else {
            data = null;
            render();
        }
    }

    modeButtons.forEach(function (button) {
        button.addEventListener('click', function () {
            setMode(button.getAttribute('data-deploy-mode'));
        });
    });

    // ---- Data ------------------------------------------------------------

    function load() {
        var seq = ++loadSeq;
        data = null;
        render();
        setStatus('Loading what AppEase installed…');
        fetch(panel.getAttribute('data-candidates-url'), {
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            credentials: 'same-origin'
        })
            .then(function (res) {
                var ctype = res.headers.get('Content-Type') || '';
                if (!res.ok || ctype.indexOf('application/json') === -1) {
                    throw new Error('candidates request failed');
                }
                return res.json();
            })
            .then(function (reply) {
                if (seq !== loadSeq || mode !== 'uninstall') { return; }
                data = reply;
                setStatus('');
                render();
            })
            .catch(function () {
                if (seq !== loadSeq || mode !== 'uninstall') { return; }
                setStatus('Could not load what AppEase installed. Switch to Install and back to try again, or reload the page.');
            });
    }

    function computers() {
        return (data && data.computers) || [];
    }

    function pickable(computer) {
        return !computer.reason;
    }

    function selectedIds() {
        return computers().filter(function (c) {
            return pickable(c) && selectedComputers[c.id];
        }).map(function (c) { return c.id; });
    }

    function hostOf(id) {
        var found = computers().filter(function (c) { return c.id === id; });
        return found.length ? found[0].hostname : '';
    }

    // The programs on the selected computers, grouped by the server's key.
    // A group is removable when at least one selected computer can remove it.
    function groups() {
        var picked = {};
        selectedIds().forEach(function (id) { picked[id] = true; });
        var byKey = {};
        var order = [];
        ((data && data.installations) || []).forEach(function (row) {
            if (!picked[row.computer_id]) { return; }
            if (!byKey[row.group]) {
                byKey[row.group] = { key: row.group, rows: [], removable: [], blocked: [] };
                order.push(row.group);
            }
            var group = byKey[row.group];
            group.rows.push(row);
            (row.reason ? group.blocked : group.removable).push(row);
        });
        return order.map(function (key) {
            var group = byKey[key];
            group.name = mostCommon(group.rows.map(function (r) { return r.name; }));
            var versions = unique(group.rows.map(function (r) { return r.version; }));
            group.version = versions.length === 1 ? versions[0] : 'versions differ';
            group.label = group.version ? (group.name + ' ' + group.version) : group.name;
            group.methodLabel = group.rows[0].method_label;
            group.computerCount = unique(group.removable.map(function (r) { return r.computer_id; })).length;
            return group;
        }).sort(function (a, b) {
            return a.label.toLowerCase().localeCompare(b.label.toLowerCase());
        });
    }

    function unique(list) {
        var seen = {};
        var out = [];
        list.forEach(function (item) {
            var k = String(item);
            if (!seen[k]) { seen[k] = true; out.push(item); }
        });
        return out;
    }

    function mostCommon(list) {
        var counts = {};
        var best = list[0] || '';
        list.forEach(function (item) {
            counts[item] = (counts[item] || 0) + 1;
            if (counts[item] > (counts[best] || 0)) { best = item; }
        });
        return best;
    }

    function reasonCounts(rows) {
        var counts = {};
        var order = [];
        rows.forEach(function (row) {
            if (!counts[row.reason_text]) { counts[row.reason_text] = 0; order.push(row.reason_text); }
            counts[row.reason_text] += 1;
        });
        return order.map(function (text) { return { text: text, count: counts[text] }; });
    }

    // ---- Rendering -------------------------------------------------------

    function render() {
        renderComputers();
        renderPrograms();
        renderSelection();
    }

    function renderComputers() {
        compList.textContent = '';
        var list = computers();
        var query = compSearch.value.trim().toLowerCase();
        var pickableCount = list.filter(pickable).length;
        compCount.textContent = data ? ('· ' + pickableCount + ' of ' + list.length + ' can uninstall') : '';

        var locations = [];
        var byLocation = {};
        list.forEach(function (computer) {
            var match = !query || computer.hostname.toLowerCase().indexOf(query) !== -1
                || (computer.location || '').toLowerCase().indexOf(query) !== -1;
            if (!match) { return; }
            var key = computer.location || '';
            if (!byLocation[key]) { byLocation[key] = []; locations.push(key); }
            byLocation[key].push(computer);
        });

        locations.forEach(function (key) {
            var head = el('div', 'deploy-subhead flex items-center justify-between gap-2');
            head.appendChild(el('span', '', key || 'Unassigned'));
            head.appendChild(el('span', 'deploy-tab-count', String(byLocation[key].length)));
            compList.appendChild(head);
            var body = el('div', 'divide-y divide-rule');
            byLocation[key].forEach(function (computer) {
                var off = !pickable(computer);
                var row = el('label', 'flex items-center gap-2 px-3 py-2 text-sm ' + (off ? 'deploy-row-off' : 'text-label'));
                var box = el('input', 'h-4 w-4 rounded border-field text-primary focus:ring-2 focus:ring-primary');
                box.type = 'checkbox';
                box.value = String(computer.id);
                box.setAttribute('data-uninstall-computer', String(computer.id));
                box.disabled = off;
                box.checked = !off && !!selectedComputers[computer.id];
                if (off) { box.setAttribute('aria-label', computer.hostname + ': ' + computer.reason_text); }
                row.appendChild(box);
                row.appendChild(el('span', 'h-2 w-2 rounded-full ' + (computer.online ? 'bg-online' : 'bg-faint')));
                row.appendChild(el('span', '', computer.hostname));
                if (off) {
                    row.appendChild(el('span', 'deploy-reason ml-auto', computer.reason_text));
                } else if (computer.methods_note) {
                    row.appendChild(el('span', 'pill-tone-info-soft ml-auto rounded px-2 py-0.5 text-xs font-medium', computer.methods_note));
                }
                body.appendChild(row);
            });
            compList.appendChild(body);
        });

        var shown = locations.length > 0;
        var emptyText = !data ? '' : (list.length === 0
            ? 'There are no computers to show.'
            : 'No computers match this search.');
        compEmpty.textContent = emptyText;
        compEmpty.classList.toggle('hidden', !data || shown);
        compList.classList.toggle('hidden', !data || !shown);
    }

    function renderPrograms() {
        progList.textContent = '';
        var picked = selectedIds();
        if (!data || picked.length === 0) {
            selectedGroups = {};
            progEmpty.textContent = data ? 'Select a computer that can uninstall to see what AppEase installed on it.' : '';
            progEmpty.classList.toggle('hidden', !data);
            progList.classList.add('hidden');
            return;
        }

        var all = groups();
        var removable = all.filter(function (g) { return g.removable.length > 0; });
        var blocked = all.filter(function (g) { return g.removable.length === 0; });
        // A group that can no longer be removed here is not still selected.
        var keep = {};
        removable.forEach(function (g) { if (selectedGroups[g.key]) { keep[g.key] = true; } });
        selectedGroups = keep;

        var where = picked.length === 1 ? hostOf(picked[0]) : 'these ' + picked.length + ' computers';
        var emptyText = '';
        if (all.length === 0) {
            emptyText = 'AppEase has no install records for ' + where + '. Only software installed after their agent started keeping records is listed.';
        } else if (removable.length === 0) {
            emptyText = 'Nothing AppEase installed on ' + where + ' can be uninstalled from here.';
        }
        progEmpty.textContent = emptyText;
        progEmpty.classList.toggle('hidden', !emptyText);

        var query = progSearch.value.trim().toLowerCase();
        function matches(group) {
            return !query || group.label.toLowerCase().indexOf(query) !== -1;
        }

        var body = el('div', 'divide-y divide-rule');
        removable.filter(matches).forEach(function (group) {
            var row = el('label', 'flex items-start gap-2 px-3 py-2 text-sm text-label');
            var box = el('input', 'mt-0.5 h-4 w-4 rounded border-field text-primary focus:ring-2 focus:ring-primary');
            box.type = 'checkbox';
            box.setAttribute('data-uninstall-group', group.key);
            box.checked = !!selectedGroups[group.key];
            row.appendChild(box);
            var text = el('span', 'min-w-0 flex-1');
            var line = el('span', 'flex flex-wrap items-center gap-2');
            line.appendChild(el('span', '', group.label));
            line.appendChild(el('span', 'pill-tone-info-soft rounded px-2 py-0.5 text-xs font-medium', group.methodLabel));
            text.appendChild(line);
            reasonCounts(group.blocked).forEach(function (item) {
                text.appendChild(el('span', 'deploy-reason block', item.count + ' more: ' + item.text));
            });
            row.appendChild(text);
            row.appendChild(el('span', 'deploy-on', 'on ' + group.computerCount + ' of ' + picked.length));
            body.appendChild(row);
        });
        progList.appendChild(body);

        var shownBlocked = blocked.filter(matches);
        if (shownBlocked.length) {
            var head = el('div', 'deploy-subhead flex items-center justify-between gap-2');
            head.appendChild(el('span', '', 'Can’t be uninstalled from here'));
            head.appendChild(el('span', 'deploy-tab-count', String(shownBlocked.length)));
            progList.appendChild(head);
            var off = el('div', 'divide-y divide-rule');
            shownBlocked.forEach(function (group) {
                var reason = reasonCounts(group.blocked).sort(function (a, b) { return b.count - a.count; })[0];
                var row = el('div', 'deploy-row-off flex items-start gap-2 px-3 py-2 text-sm');
                row.setAttribute('aria-disabled', 'true');
                var box = el('input', 'mt-0.5 h-4 w-4 rounded border-field');
                box.type = 'checkbox';
                box.disabled = true;
                box.setAttribute('aria-label', group.label + ': ' + reason.text);
                row.appendChild(box);
                var text = el('span', 'min-w-0 flex-1');
                var line = el('span', 'flex flex-wrap items-center gap-2');
                line.appendChild(el('span', '', group.label));
                line.appendChild(el('span', 'pill-tone-neutral rounded px-2 py-0.5 text-xs font-medium', group.methodLabel));
                text.appendChild(line);
                text.appendChild(el('span', 'deploy-reason block', reason.text));
                row.appendChild(text);
                var on = unique(group.blocked.map(function (r) { return r.computer_id; })).length;
                row.appendChild(el('span', 'deploy-on', 'on ' + on));
                off.appendChild(row);
            });
            progList.appendChild(off);
        }
        var anyShown = removable.filter(matches).length + shownBlocked.length > 0;
        progList.classList.toggle('hidden', !anyShown);
        if (!anyShown && !emptyText) {
            progEmpty.textContent = 'No programs match this search.';
            progEmpty.classList.remove('hidden');
        }
    }

    // What the current selection would queue, and what it would skip.
    function plan() {
        var picked = selectedIds();
        var chosen = groups().filter(function (g) { return selectedGroups[g.key] && g.removable.length > 0; });
        var jobs = [];
        var skipped = [];
        var skippedRows = [];
        var computersInPlan = {};
        chosen.forEach(function (group) {
            group.removable.forEach(function (row) {
                jobs.push(row);
                computersInPlan[row.computer_id] = true;
            });
            group.blocked.forEach(function (row) {
                skippedRows.push(row);
                skipped.push({ computer: hostOf(row.computer_id), software: group.label, reason_text: row.reason_text });
            });
        });
        return {
            picked: picked,
            groups: chosen,
            jobs: jobs,
            skipped: skipped,
            skippedRows: skippedRows,
            computerCount: Object.keys(computersInPlan).length
        };
    }

    function chip(text, onRemove) {
        var node = el('span', 'inline-flex items-center gap-1.5 rounded-md border border-line bg-canvas px-2 py-1 text-xs text-label');
        node.appendChild(el('span', '', text));
        var remove = el('button', 'text-muted hover:text-danger', '×');
        remove.type = 'button';
        remove.setAttribute('aria-label', 'Remove ' + text);
        remove.addEventListener('click', onRemove);
        node.appendChild(remove);
        return node;
    }

    function renderSelection() {
        var p = data ? plan() : { picked: [], groups: [], jobs: [], skipped: [], computerCount: 0 };
        selProgBox.textContent = '';
        selCompBox.textContent = '';
        p.groups.forEach(function (group) {
            selProgBox.appendChild(chip(group.label, function () {
                delete selectedGroups[group.key];
                renderPrograms();
                renderSelection();
            }));
        });
        p.picked.forEach(function (id) {
            selCompBox.appendChild(chip(hostOf(id), function () {
                delete selectedComputers[id];
                render();
            }));
        });
        selProgGroup.classList.toggle('hidden', p.groups.length === 0);
        selCompGroup.classList.toggle('hidden', p.picked.length === 0);
        selProgCount.textContent = String(p.groups.length);
        selCompCount.textContent = String(p.picked.length);
        var any = p.groups.length + p.picked.length > 0;
        selWrap.classList.toggle('hidden', !any);
        selEmpty.classList.toggle('hidden', any);
        tallyPrograms.textContent = String(p.groups.length);
        tallyComputers.textContent = String(p.picked.length);
        tallySkipped.textContent = String(p.skipped.length);
        tallyJobs.textContent = String(p.jobs.length);
        uninstallButton.disabled = p.jobs.length === 0;
    }

    compList.addEventListener('change', function (event) {
        var id = event.target.getAttribute('data-uninstall-computer');
        if (!id) { return; }
        if (event.target.checked) {
            selectedComputers[Number(id)] = true;
        } else {
            delete selectedComputers[Number(id)];
        }
        renderPrograms();
        renderSelection();
    });
    progList.addEventListener('change', function (event) {
        var key = event.target.getAttribute('data-uninstall-group');
        if (!key) { return; }
        if (event.target.checked) {
            selectedGroups[key] = true;
        } else {
            delete selectedGroups[key];
        }
        renderSelection();
    });
    compSearch.addEventListener('input', renderComputers);
    progSearch.addEventListener('input', renderPrograms);

    // ---- Confirm and submit ----------------------------------------------

    function openConfirm() {
        var p = plan();
        if (p.jobs.length === 0) {
            return;
        }
        dialogTitle.textContent = 'Uninstall ' + plural(p.groups.length, 'program', 'programs') +
            ' from ' + plural(p.computerCount, 'computer', 'computers') + '?';
        dialogPrograms.textContent = '';
        p.groups.forEach(function (group) {
            var li = el('li');
            li.appendChild(el('span', 'font-medium text-ink', group.label));
            li.appendChild(el('span', 'text-muted', ' · ' + plural(group.computerCount, 'computer', 'computers')));
            dialogPrograms.appendChild(li);
        });
        dialogSkipped.textContent = '';
        p.skipped.forEach(function (item) {
            var li = el('li', 'flex items-start justify-between gap-3');
            var who = el('span');
            who.appendChild(el('span', 'font-mono text-ink', item.computer));
            who.appendChild(el('span', 'block text-muted', item.software));
            li.appendChild(who);
            li.appendChild(el('span', 'text-right text-muted', item.reason_text));
            dialogSkipped.appendChild(li);
        });
        dialogSkippedCount.textContent = String(p.skipped.length);
        dialogSkippedWrap.classList.toggle('hidden', p.skipped.length === 0);
        dialogOk.disabled = false;
        dialog.showModal();
    }

    function submit() {
        var p = plan();
        if (p.jobs.length === 0) {
            dialog.close();
            return;
        }
        dialogOk.disabled = true;
        // The rows it will skip go too: the server refuses each one, says why,
        // and its own skipped list is what the progress modal shows - the
        // server decides, never this page.
        var body = new FormData();
        p.jobs.concat(p.skippedRows).forEach(function (row) { body.append('installation_ids', String(row.id)); });
        fetch(panel.getAttribute('data-submit-url'), {
            method: 'POST',
            body: body,
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            credentials: 'same-origin'
        })
            .then(function (res) {
                var ctype = res.headers.get('Content-Type') || '';
                if (ctype.indexOf('application/json') === -1) {
                    throw new Error('not json');
                }
                return res.json().then(function (reply) { return { ok: res.ok, reply: reply }; });
            })
            .then(function (result) {
                dialog.close();
                var api = deploy();
                var reply = result.reply || {};
                if (result.ok && reply.ok && api) {
                    // Nothing stays picked once it is queued.
                    clearUninstall();
                    render();
                    api.openUninstallProgress(reply);
                    return;
                }
                if (!api) { return; }
                if (!result.ok) {
                    api.openInfoModal('Nothing was queued', reply.error || 'Something went wrong.');
                    return;
                }
                var reasons = unique((reply.skipped || []).map(function (s) { return s.reason_text; }));
                api.openInfoModal('Nothing was queued', reasons.length ? reasons.join(' ') : (reply.message || ''));
                load();
            })
            .catch(function () {
                dialog.close();
                if (deploy()) {
                    deploy().openInfoModal('Could not reach the server',
                        'Nothing may have been queued. Check Recent Deployment Jobs on the dashboard before trying again.');
                }
            });
    }

    uninstallButton.addEventListener('click', openConfirm);
    dialogCancel.addEventListener('click', function () { dialog.close(); });
    dialogOk.addEventListener('click', submit);
    dialog.addEventListener('click', function (event) {
        // A click on the backdrop - the dialog itself, outside its panel -
        // cancels, as Escape does.
        if (event.target === dialog) { dialog.close(); }
    });
})();

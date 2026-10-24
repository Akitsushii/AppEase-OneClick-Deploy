(function () {
    'use strict';

    var POLL_INTERVAL_MS = 8000;

    // Minimum time a filter-switch placeholder stays up. The endpoint answers
    // in ~6ms on the LAN, so without a floor it would appear and vanish inside
    // two frames, which reads as a glitch rather than a load.
    var SKELETON_MIN_MS = 300;

    // Purely a TIMING guard: it delays a valid reply until the floor has
    // passed; pollGeneration decides validity.
    var skeletonHoldUntil = 0;

    // Request generation, as in chocoSearch(): a filter change bumps it, and
    // a reply whose captured value has moved is dropped. Otherwise a poll
    // sent just before the change paints the old filter's rows until the
    // next poll, up to 8s later.
    var pollGeneration = 0;

    var pollTimer = null;
    var root, bell, badge, panel, list, empty;
    var newCount, markReadBtn;
    // Highest id currently rendered, so "mark all as read" can clear the
    // dots immediately instead of waiting up to 8s for the next poll.
    var maxRenderedId = 0;
    var agentDot, agentText;
    var activeSeverity = '';
    var filterButtons = [];

    // From the last poll; buildRow() marks newer ids unread, mirroring the
    // server's unread_count per row.
    var lastSeenId = 0;

    // Last payload rendered, so markRead() can repaint without a fetch.
    var lastItems = [];

    // How many of the rendered rows are unread. Read by closePanel() so a
    // panel that had nothing new in it does not POST on every dismissal.
    var unreadRendered = 0;

    // The source of truth for "open": the panel stays in the DOM while it
    // fades, so .hidden lags and must NOT be read for this.
    var panelOpen = false;
    var closeTimer = null;

    function init() {
        root = document.getElementById('notif-root');
        if (!root) {
            return;
        }

        agentDot = document.getElementById('agent-status-dot');
        agentText = document.getElementById('agent-status-text');

        bell = document.getElementById('notif-bell');
        badge = document.getElementById('notif-badge');
        panel = document.getElementById('notif-panel');
        list = document.getElementById('notif-list');
        empty = document.getElementById('notif-empty');
        newCount = document.getElementById('notif-newcount');
        markReadBtn = document.getElementById('notif-mark-read');

        if (markReadBtn) {
            markReadBtn.addEventListener('click', function (event) {
                event.stopPropagation();
                markRead();
            });
        }

        bell.addEventListener('click', onBellClick);
        document.addEventListener('click', onDocumentClick);
        document.addEventListener('keydown', onKeydown);
        document.addEventListener('visibilitychange', onVisibilityChange);

        filterButtons = Array.prototype.slice.call(
            root.querySelectorAll('[data-notif-filter]')
        );
        for (var i = 0; i < filterButtons.length; i++) {
            filterButtons[i].addEventListener('click', onFilterClick);
        }
        updateFilterButtonStyles();

        poll();
        pollTimer = window.setInterval(poll, POLL_INTERVAL_MS);
    }

    function onVisibilityChange() {
        if (document.visibilityState !== 'hidden') {
            poll();
        }
    }

    // withSkeleton is passed ONLY by onFilterClick. The interval and
    // visibilitychange paths call poll() with no argument, so a background
    // refresh never blanks a list someone is reading.
    function poll(withSkeleton) {
        if (document.visibilityState === 'hidden') {
            return;
        }

        var url = '/api/notifications';
        if (activeSeverity) {
            url += '?severity=' + encodeURIComponent(activeSeverity);
        }

        if (withSkeleton) {
            showSkeleton();
            skeletonHoldUntil = Date.now() + SKELETON_MIN_MS;
        }

        // Captured BEFORE the request goes out, so a filter change while it is
        // in flight leaves this reply behind.
        var generation = pollGeneration;

        fetch(url, { credentials: 'same-origin' })
            .then(function (response) {
                if (!response.ok) {
                    throw new Error('Bad response');
                }
                return response.json();
            })
            .then(function (data) {
                applyPollResult(generation, withSkeleton, function () {
                    lastSeenId = data.last_seen_id || 0;
                    updateBadge(data.unread_count);
                    updateAgents(data.agents);
                    renderList(data.items);
                });
            })
            .catch(function () {
                // Fail silently and keep polling - but never leave a
                // placeholder on screen. Repainting the last payload clears it
                // through renderList()'s own teardown.
                applyPollResult(generation, withSkeleton, function () {
                    if (withSkeleton) {
                        renderList(lastItems);
                    }
                });
            });
    }

    // Two independent guards, and they are not redundant:
    //
    //   pollGeneration  decides WHETHER a reply may paint. A reply from an
    //                   older generation describes a filter the user has left,
    //                   so it is dropped outright, however late it arrives.
    //
    //   skeletonHoldUntil decides WHEN a valid reply paints, so the
    //                   placeholder stays at least SKELETON_MIN_MS.
    //
    // The generation check cannot enforce the floor, and the floor cannot
    // recognise a stale filter, so neither replaces the other.
    function applyPollResult(generation, withSkeleton, apply) {
        if (generation !== pollGeneration) {
            return;
        }

        var wait = skeletonHoldUntil - Date.now();

        // A background reply is still legitimate here - same generation, so
        // the same severity - but painting it now would cut the placeholder
        // short. The filter's own reply is moments behind and will paint.
        if (!withSkeleton) {
            if (wait <= 0) {
                apply();
            }
            return;
        }

        if (wait > 0) {
            window.setTimeout(function () {
                // Re-checked on the far side of the wait: the user can switch
                // filters again inside those 300ms.
                if (generation !== pollGeneration) {
                    return;
                }
                skeletonHoldUntil = 0;
                apply();
            }, wait);
        } else {
            skeletonHoldUntil = 0;
            apply();
        }
    }

    // Placeholder rows for a filter switch. Written straight into #notif-list,
    // so renderList()'s existing `while (list.firstChild)` teardown removes
    // them - there is no second cleanup path to forget. The empty line is
    // hidden here and left for renderList() to decide on, so a placeholder and
    // "No notifications yet." can never be on screen together.
    function showSkeleton() {
        while (list.firstChild) {
            list.removeChild(list.firstChild);
        }
        empty.classList.add('hidden');

        for (var i = 0; i < 5; i++) {
            var row = document.createElement('div');
            row.className = 'notif-skeleton-row';
            row.setAttribute('aria-hidden', 'true');

            var message = document.createElement('div');
            message.className = 'skeleton block h-4 w-full';

            var meta = document.createElement('div');
            meta.className = 'notif-skeleton-meta';

            var pill = document.createElement('div');
            pill.className = 'skeleton block h-4 w-1/3';

            var time = document.createElement('div');
            time.className = 'skeleton block h-4 w-1/3';

            meta.appendChild(pill);
            meta.appendChild(time);
            row.appendChild(message);
            row.appendChild(meta);
            list.appendChild(row);
        }

        list.setAttribute('aria-busy', 'true');
    }

    // The pill is optional markup - login and report do not have it, faculty
    // never render it, and neither does an older cached base.html - so every
    // path here is null-guarded, and `agents` itself is null for faculty.
    function updateAgents(agents) {
        if (!agentText || !agents) {
            return;
        }

        var online = agents.online || 0;
        var total = agents.total || 0;
        var retired = agents.retired || 0;
        // Must read exactly like the first paint in base.html, or the pill
        // visibly changes on the first poll. Retired machines are already
        // left out of online and total by computer_counts() on the server.
        agentText.textContent = online + ' of ' + total + ' computer' + (total === 1 ? '' : 's') + ' online' +
            (retired > 0 ? ' \u00b7 ' + retired + ' retired' : '');

        if (agentDot) {
            // Inline style, not a class swap: these colours must survive a
            // tailwind.css that has not been rebuilt.
            agentDot.style.backgroundColor = online > 0 ? '#38a169' : '#9ca3af';
        }
    }

    function updateBadge(unreadCount) {
        var count = unreadCount || 0;
        if (count > 0) {
            badge.textContent = count > 99 ? '99+' : String(count);
            badge.classList.remove('hidden');
        } else {
            badge.classList.add('hidden');
        }
    }

    function renderList(items) {
        items = items || [];
        lastItems = items;

        var unread = 0;
        for (var n = 0; n < items.length; n++) {
            if (typeof items[n].id === 'number') {
                if (items[n].id > maxRenderedId) { maxRenderedId = items[n].id; }
                if (items[n].id > lastSeenId) { unread += 1; }
            }
        }
        unreadRendered = unread;
        if (newCount) {
            newCount.textContent = unread + ' new';
            newCount.classList.toggle('hidden', unread === 0);
        }
        if (markReadBtn) {
            markReadBtn.classList.toggle('hidden', unread === 0);
        }

        while (list.firstChild) {
            list.removeChild(list.firstChild);
        }
        list.removeAttribute('aria-busy');

        if (items.length === 0) {
            empty.textContent = activeSeverity
                ? 'No notifications match this filter.'
                : 'No notifications yet.';
            empty.classList.remove('hidden');
            return;
        }
        empty.classList.add('hidden');

        for (var i = 0; i < items.length; i++) {
            list.appendChild(buildRow(items[i]));
        }
    }

    function buildRow(item) {
        var hasLink = !!item.link_url;
        var isUnread = typeof item.id === 'number' && item.id > lastSeenId;

        // A <div>, not an <a>: the card has its own Open button, and a button
        // inside a link is invalid and can be unreachable by keyboard.
        var row = document.createElement('div');
        row.className = 'notif-card' + (isUnread ? ' is-unread' : '');

        var head = document.createElement('div');
        head.className = 'notif-card-head';

        var message = document.createElement('p');
        message.className = 'notif-card-msg';
        message.textContent = item.message;
        head.appendChild(message);

        if (isUnread) {
            var dot = document.createElement('span');
            dot.className = 'notif-card-dot';
            dot.setAttribute('aria-label', 'Unread');
            head.appendChild(dot);
        }
        row.appendChild(head);

        var meta = document.createElement('div');
        meta.className = 'notif-card-meta';

        var pill = document.createElement('span');
        pill.className = 'notif-sev';
        pill.setAttribute('data-sev', severityKey(item.severity));
        pill.textContent = severityLabel(item.severity);
        meta.appendChild(pill);

        if (hasLink) {
            var open = document.createElement('a');
            open.className = 'notif-open';
            open.href = item.link_url;
            open.appendChild(document.createTextNode('Open'));
            var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            svg.setAttribute('viewBox', '0 0 24 24');
            svg.setAttribute('fill', 'none');
            svg.setAttribute('stroke', 'currentColor');
            svg.setAttribute('stroke-width', '2');
            svg.setAttribute('stroke-linecap', 'round');
            svg.setAttribute('stroke-linejoin', 'round');
            svg.setAttribute('aria-hidden', 'true');
            ['M15 3h6v6', 'M10 14 21 3',
             'M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6'].forEach(function (dAttr) {
                var path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
                path.setAttribute('d', dAttr);
                svg.appendChild(path);
            });
            open.appendChild(svg);
            /* Following the link navigates away, so closePanel() never runs
             * and the badge would survive the very act of reading the thing
             * it was counting. A normal fetch() can be cancelled mid-flight
             * by that navigation, which is what sendBeacon exists for: the
             * browser owns the request and delivers it during unload. */
            open.addEventListener('click', function () {
                markReadOnUnload();
            });
            meta.appendChild(open);
        }

        var timestamp = document.createElement('span');
        timestamp.className = 'notif-card-time';
        timestamp.textContent = item.created_at_display;
        meta.appendChild(timestamp);

        row.appendChild(meta);
        return row;
    }

    function severityKey(severity) {
        if (severity === 'error') { return 'error'; }
        if (severity === 'warning') { return 'warning'; }
        return 'info';
    }

    function severityLabel(severity) {
        var key = severityKey(severity);
        return key.charAt(0).toUpperCase() + key.slice(1);
    }

    function onFilterClick(event) {
        event.stopPropagation();
        activeSeverity = event.currentTarget.getAttribute('data-notif-filter') || '';
        updateFilterButtonStyles();
        // Anything already in flight describes the filter we just left.
        pollGeneration += 1;
        poll(true);
    }

    function updateFilterButtonStyles() {
        for (var i = 0; i < filterButtons.length; i++) {
            var btn = filterButtons[i];
            var isActive = (btn.getAttribute('data-notif-filter') || '') === activeSeverity;
            // aria-selected drives the styling (app.css): one source of truth.
            btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
        }
    }

    function onBellClick(event) {
        event.stopPropagation();
        if (panelOpen) {
            closePanel();
        } else {
            openPanel();
        }
    }

    function openPanel() {
        if (panelOpen) {
            return;
        }
        panelOpen = true;

        if (closeTimer !== null) {
            window.clearTimeout(closeTimer);
            closeTimer = null;
        }

        panel.classList.remove('hidden');
        // Force a reflow so the opacity:0 start state paints before the open
        // class lands, or the transition is skipped.
        void panel.offsetWidth;
        panel.classList.add('notif-panel-open');
    }

    function closePanel() {
        if (!panelOpen) {
            return;
        }
        panelOpen = false;

        /* Closing the panel clears the badge. On CLOSE, not open: read state
         * is one high-water mark, so marking on open would erase "which are
         * new" while you read. Skipped when nothing is unread, so an idle
         * open/close does not POST. */
        if (unreadRendered > 0) {
            markRead();
        }

        panel.classList.remove('notif-panel-open');

        // Re-hide only after the fade finishes, so the panel is not focusable
        // while invisible. Must match the transition duration in app.css.
        if (closeTimer !== null) {
            window.clearTimeout(closeTimer);
        }
        closeTimer = window.setTimeout(function () {
            closeTimer = null;
            if (!panelOpen) {
                panel.classList.add('hidden');
            }
        }, 140);
    }

    function markReadOnUnload() {
        /* Same endpoint as markRead(), but fire-and-forget and without the
         * repaint - the page is about to be replaced, so there is nothing
         * left to repaint. Falls back to keepalive fetch where sendBeacon
         * is missing, and does nothing at all if neither exists rather than
         * throwing on the way out. */
        if (unreadRendered === 0) {
            return;
        }
        var url = '/api/notifications/mark-read';
        try {
            if (navigator.sendBeacon && navigator.sendBeacon(url, new Blob([], { type: 'text/plain' }))) {
                return;
            }
        } catch (err) {
            // Fall through to fetch below.
        }
        try {
            fetch(url, { method: 'POST', credentials: 'same-origin', keepalive: true });
        } catch (err) {
            // Nothing more to try; the next poll re-syncs the badge.
        }
    }

    function markRead() {
        updateBadge(0);
        // Clear the dots now rather than on the next poll. lastSeenId is what
        // buildRow() compares against, so moving it past everything rendered
        // makes every current row read.
        if (maxRenderedId > lastSeenId) {
            lastSeenId = maxRenderedId;
        }
        renderList(lastItems);

        fetch('/api/notifications/mark-read', {
            method: 'POST',
            credentials: 'same-origin'
        }).catch(function () {
            // Fail silently; badge will re-sync on the next poll.
        });
    }

    function onDocumentClick(event) {
        if (!panelOpen) {
            return;
        }
        if (!root.contains(event.target)) {
            closePanel();
        }
    }

    function onKeydown(event) {
        if (event.key === 'Escape' && panelOpen) {
            closePanel();
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
/* AppEase - data table.
 *
 * Search, faceted filters, sortable headers, column visibility,
 * pagination, row selection and a floating action bar, all client-side
 * over the rows the server rendered. Binds to [data-dt-root].
 */
(function () {
    'use strict';

    var SVG_NS = 'http://www.w3.org/2000/svg';

    function icon(paths, extraClass) {
        var svg = document.createElementNS(SVG_NS, 'svg');
        svg.setAttribute('viewBox', '0 0 24 24');
        svg.setAttribute('fill', 'none');
        svg.setAttribute('stroke', 'currentColor');
        svg.setAttribute('stroke-width', '2');
        svg.setAttribute('stroke-linecap', 'round');
        svg.setAttribute('stroke-linejoin', 'round');
        svg.setAttribute('aria-hidden', 'true');
        svg.setAttribute('class', 'dt-icon ' + (extraClass || ''));
        paths.forEach(function (d) {
            var p = document.createElementNS(SVG_NS, 'path');
            p.setAttribute('d', d);
            svg.appendChild(p);
        });
        return svg;
    }

    var ICONS = {
        plusCircle: ['M12 8v8', 'M8 12h8', 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z'],
        calendar: ['M8 2v4', 'M16 2v4', 'M3 10h18', 'M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z'],
        sort: ['m3 16 4 4 4-4', 'M7 20V4', 'm21 8-4-4-4 4', 'M17 4v16'],
        view: ['M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z', 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z'],
        chevUpDown: ['m7 15 5 5 5-5', 'm7 9 5-5 5 5'],
        chevUp: ['m5 15 7-7 7 7'],
        chevDown: ['m19 9-7 7-7-7'],
        first: ['m11 17-5-5 5-5', 'm18 17-5-5 5-5'],
        prev: ['m15 18-6-6 6-6'],
        next: ['m9 18 6-6-6-6'],
        last: ['m6 17 5-5-5-5', 'm13 17 5-5-5-5'],
        check: ['M20 6 9 17l-5-5'],
        x: ['M18 6 6 18', 'm6 6 12 12'],
        download: ['M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4', 'm7 10 5 5 5-5', 'M12 15V3'],
        trash: ['M3 6h18', 'M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6', 'M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2'],
        printer: ['M6 9V2h12v7', 'M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2', 'M6 14h12v8H6z'],
        dots: ['M12 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z', 'M19 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z', 'M5 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z'],
        search: ['M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z', 'm21 21-4.3-4.3'],
        xCircle: ['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z', 'm15 9-6 6', 'm9 9 6 6'],
        grip: ['M9 5h.01', 'M9 12h.01', 'M9 19h.01', 'M15 5h.01', 'M15 12h.01', 'M15 19h.01'],
        chevRight: ['m9 18 6-6-6-6'],
        pencil: ['M12 20h9', 'M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4Z'],
        info: ['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z', 'M12 16v-4', 'M12 8h.01'],
        external: ['M15 3h6v6', 'M10 14 21 3', 'M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6'],
        key: ['M2 18v3h3l8.5-8.5', 'M15.5 12.5a5 5 0 1 0-4-4'],
        refresh: ['M3 12a9 9 0 0 1 9-9 9 9 0 0 1 6.7 3L21 8', 'M21 3v5h-5',
                  'M21 12a9 9 0 0 1-9 9 9 9 0 0 1-6.7-3L3 16', 'M3 21v-5h5'],
        archive: ['M3 3h18a1 1 0 0 1 1 1v3a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z',
                  'M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8', 'M10 12h4'],
        archiveRestore: ['M3 3h18a1 1 0 0 1 1 1v3a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z',
                         'M4 8v11a2 2 0 0 0 2 2h2', 'M20 8v11a2 2 0 0 1-2 2h-2', 'm9 15 3-3 3 3', 'M12 12v9']
    };

    /* Search field pinned to the top of a menu. */
    function menuSearch(placeholder, onInput) {
        var wrap = el('div', 'dt-menu-search');
        wrap.appendChild(icon(ICONS.search));
        var input = el('input');
        input.type = 'text';
        input.placeholder = placeholder;
        input.setAttribute('aria-label', placeholder);
        input.addEventListener('input', function () { onInput(input.value.trim().toLowerCase()); });
        // Keeps a click in the field from closing the menu.
        input.addEventListener('click', function (e) { e.stopPropagation(); });
        wrap.appendChild(input);
        return { wrap: wrap, input: input };
    }

    function el(tag, cls, text) {
        var node = document.createElement(tag);
        if (cls) { node.className = cls; }
        if (text !== undefined) { node.textContent = text; }
        return node;
    }

    /* Cell text for search, faceting and sorting. data-sort wins: dates
     * render localised but carry the raw ISO value there. */
    function cellValue(cell) {
        if (!cell) { return ''; }
        var explicit = cell.getAttribute('data-sort');
        if (explicit !== null) { return explicit; }
        // COLLAPSED, not just trimmed: a cell with a badge spans several
        // source lines, and interior newlines broke the CSV and split one
        // facet value into two.
        return (cell.textContent || '').replace(/\s+/g, ' ').trim();
    }

    function build(root) {
        var table = root.querySelector('[data-dt-table]');
        var tbody = table && table.querySelector('tbody');
        if (!table || !tbody) { return; }

        var headers = Array.prototype.slice.call(table.querySelectorAll('thead th'));
        var allRows = Array.prototype.slice.call(tbody.querySelectorAll('tr'));
        var state = {
            search: '',
            // Sort keys in order; [] means server order.
            sorts: [],
            page: 1,
            pageSize: parseInt(root.getAttribute('data-dt-pagesize') || '10', 10),
            facets: {},
            hidden: {},
            from: null,
            to: null
        };

        var columns = headers.map(function (th, index) {
            return {
                index: index,
                label: (th.getAttribute('data-dt-label') || th.textContent || '').trim(),
                sortable: th.hasAttribute('data-dt-sort'),
                facet: th.hasAttribute('data-dt-facet'),
                dateRange: th.hasAttribute('data-dt-daterange'),
                select: th.hasAttribute('data-dt-select'),
                actions: th.hasAttribute('data-dt-actions'),
                hideable: !th.hasAttribute('data-dt-select') && !th.hasAttribute('data-dt-actions')
            };
        });

        /* data-dt-hidden: a column the page starts hidden. Still searched,
         * still filterable, one click away under View. */
        columns.forEach(function (col) {
            if (col.hideable && headers[col.index].hasAttribute('data-dt-hidden')) {
                state.hidden[col.index] = true;
            }
        });

        /* A column is hidden only while nothing filters on it: an export or
         * print missing its own filter's column is wrong. The View choice is
         * kept, so clearing the filter hides it again. */
        function filteredOn(col) {
            if (state.facets[col.index]) { return true; }
            return !!(state.from && state.dateCol === col.index);
        }

        function isHidden(col) {
            return !!state.hidden[col.index] && !filteredOn(col);
        }

        var rows = allRows.map(function (tr) {
            var cells = Array.prototype.slice.call(tr.children);
            return {
                tr: tr,
                id: tr.getAttribute('data-dt-id') || '',
                status: tr.getAttribute('data-dt-status') || '',
                values: cells.map(cellValue),
                haystack: cells.map(function (c, i) {
                    return columns[i] && columns[i].select ? '' : (c.textContent || '');
                }).join(' ').toLowerCase()
            };
        });

        /* Basecoat alert-dialog, not window.confirm() (unstyled, reads like a
         * browser error); <dialog>.showModal() traps focus and handles Esc.
         * pendingForm is the form to post once confirmed. */
        var pendingForm = null;

        function confirmThen(kind, count, form, sourceRow) {
            var dialog = root.querySelector('[data-dt-confirm="' + kind + '"]');
            if (!dialog || typeof dialog.showModal !== 'function') {
                // No dialog in the markup: submit rather than silently doing
                // nothing, so the action still works.
                form.submit();
                return;
            }
            var label = dialog.querySelector('[data-dt-confirm-count]');
            if (label) {
                // The table's own noun (Software Library deletes software).
                var one = root.getAttribute('data-dt-noun') || 'job';
                var many = root.getAttribute('data-dt-noun-plural') || (one + 's');
                label.textContent = count === 1 ? ('this ' + one) : ('these ' + count + ' ' + many);
            }
            /* Per-row wording (Accounts' reset depends on whether an address
             * is on file): announce, let the page fill it in, then open. */
            root.dispatchEvent(new CustomEvent('dt:confirm', {
                bubbles: true,
                detail: { kind: kind, dialog: dialog, row: sourceRow || null, count: count }
            }));
            pendingForm = form;
            dialog.showModal();
        }

        /* The reverse of dt:confirm: a page asks for one of the table's
         * confirm dialogs for its own form, so buttons outside the table get
         * the same dialog rather than a copy. */
        root.addEventListener('dt:request-confirm', function (event) {
            var detail = event.detail || {};
            if (detail.kind && detail.form) {
                confirmThen(detail.kind, detail.count || 1, detail.form, null);
            }
        });

        Array.prototype.forEach.call(root.querySelectorAll('[data-dt-confirm]'), function (dialog) {
            dialog.addEventListener('click', function (event) {
                if (event.target.closest('[data-dt-confirm-cancel]')) {
                    pendingForm = null;
                    dialog.close();
                    return;
                }
                var ok = event.target.closest('[data-dt-confirm-ok]');
                if (ok) {
                    var form = pendingForm;
                    pendingForm = null;
                    /* A slow confirm (Reset password waits on email) stays
                     * open in the shared busy state until answered, buttons
                     * and Esc out of reach, so it cannot be sent twice. */
                    if (ok.hasAttribute('data-busy') && typeof window.markBusy === 'function') {
                        window.markBusy(ok);
                        dialog.setAttribute('data-dt-busy', '');
                        Array.prototype.forEach.call(dialog.querySelectorAll('[data-dt-confirm-cancel]'), function (b) {
                            b.disabled = true;
                        });
                    } else {
                        dialog.close();
                    }
                    // Detached row-menu forms must be in the document to
                    // submit. The bulk path passes a {submit} wrapper, and
                    // document.contains throws on a non-Node.
                    if (form && form.nodeType === 1 && !document.contains(form)) {
                        document.body.appendChild(form);
                    }
                    if (form) { form.submit(); }
                }
            });
            // Esc / backdrop dismissal must not leave a form armed.
            dialog.addEventListener('close', function () { pendingForm = null; });
            // ...and Esc does nothing while a confirmed request is on its way.
            dialog.addEventListener('cancel', function (event) {
                if (dialog.hasAttribute('data-dt-busy')) { event.preventDefault(); }
            });
        });

        // ---- toolbar ----------------------------------------------------
        var toolbar = root.querySelector('[data-dt-toolbar]');
        var footer = root.querySelector('[data-dt-footer]');
        var bar = root.querySelector('[data-dt-actionbar]');
        /* A STACK: the sort panel's pickers open on top of it, and Esc peels
         * one layer at a time instead of dismissing everything. */
        var menuStack = [];
        var openMenu = null;

        function syncTop() { openMenu = menuStack[menuStack.length - 1] || null; }

        function closeTopMenu() {
            var entry = menuStack.pop();
            if (!entry) { return; }
            entry.menu.remove();
            if (entry.trigger) { entry.trigger.setAttribute('aria-expanded', 'false'); }
            syncTop();
        }

        function closeMenu() {
            while (menuStack.length) { closeTopMenu(); }
        }

        /* Menus go on <body>, positioned fixed: inside the table's
         * overflow-x-auto wrapper they would be clipped.
         *
         * The trigger is measured BEFORE closeMenu(): the sort panel's pickers
         * are children of the open menu, and once detached they measure as
         * all zeros, putting the picker in the top-left corner. */
        function place(menu, box, align) {
            var width = menu.offsetWidth;
            var left = align === 'right' ? box.right - width : box.left;
            left = Math.max(8, Math.min(left, window.innerWidth - width - 8));
            var top = box.bottom + 6;
            if (top + menu.offsetHeight > window.innerHeight - 8) {
                top = Math.max(8, box.top - menu.offsetHeight - 6);
            }
            menu.style.left = left + 'px';
            menu.style.top = Math.max(8, top) + 'px';
        }

        function openPopover(trigger, menu, align, nested) {
            var box = trigger.getBoundingClientRect();
            if (nested) {
                // Drop any sibling already open at this depth, keeping the
                // parent - reopening the direction picker should not stack.
                while (menuStack.length && menuStack[menuStack.length - 1].depth >= 1) { closeTopMenu(); }
            } else {
                closeMenu();
            }
            // ADD, never assign: menus carry their own class (dt-sort-panel),
            // which assigning would wipe.
            menu.classList.add('dt-menu');
            document.body.appendChild(menu);
            place(menu, box, align);
            trigger.setAttribute('aria-expanded', 'true');
            menuStack.push({ menu: menu, trigger: trigger, align: align, depth: nested ? 1 : 0 });
            syncTop();
        }

        /* Fixed menus do not scroll with the page, so re-place against the
         * trigger, or close if it has scrolled out of view. */
        function reposition() {
            if (!menuStack.length) { return; }
            var base = menuStack[0];
            if (!document.contains(base.trigger)) { closeMenu(); return; }
            var baseBox = base.trigger.getBoundingClientRect();
            if (baseBox.bottom < 0 || baseBox.top > window.innerHeight) { closeMenu(); return; }
            menuStack.forEach(function (entry) {
                if (!document.contains(entry.trigger)) { return; }
                place(entry.menu, entry.trigger.getBoundingClientRect(), entry.align);
            });
        }
        // Capture phase, so scrolls inside the table wrapper count too.
        window.addEventListener('scroll', reposition, true);

        document.addEventListener('click', function (event) {
            if (!menuStack.length) { return; }
            var inside = menuStack.some(function (entry) {
                return entry.menu.contains(event.target) || entry.trigger.contains(event.target);
            });
            if (!inside) { closeMenu(); }
        });
        document.addEventListener('keydown', function (event) {
            // One layer per press: Esc in the field picker returns you to the
            // sort panel rather than throwing away the whole edit.
            if (event.key === 'Escape' && menuStack.length) { closeTopMenu(); }
        });
        window.addEventListener('resize', reposition);

        function toolbarButton(label, iconName, onClick, opts) {
            var btn = el('button', 'dt-btn');
            btn.type = 'button';
            btn.setAttribute('aria-haspopup', (opts && opts.menu) ? 'true' : 'false');
            btn.setAttribute('aria-expanded', 'false');
            if (iconName) {
                // Wrapped so the facet buttons can swap ⊕ for ⊗ later without
                // rebuilding the whole button.
                var slot = el('span', 'dt-icon-slot');
                slot.appendChild(icon(ICONS[iconName]));
                btn.appendChild(slot);
                btn.iconSlot = slot;
            }
            btn.appendChild(el('span', '', label));
            if (opts && opts.chips) {
                var chips = el('span', 'dt-chips hidden');
                btn.appendChild(chips);
                btn.chipSlot = chips;
            }
            if (opts && opts.badge) {
                var badge = el('span', 'dt-badge');
                btn.appendChild(badge);
                btn.badgeNode = badge;
            }
            btn.addEventListener('click', function (event) {
                event.stopPropagation();
                onClick(btn);
            });
            return btn;
        }

        var searchInput, facetButtons = [], dateButton, sortButton, viewButton, exportButton;

        function buildToolbar() {
            if (!toolbar) { return; }
            toolbar.innerHTML = '';

            var left = el('div', 'dt-toolbar-left');
            searchInput = el('input', 'dt-search');
            searchInput.type = 'text';
            searchInput.placeholder = root.getAttribute('data-dt-searchlabel') || 'Search...';
            searchInput.setAttribute('aria-label', searchInput.placeholder);
            searchInput.addEventListener('input', function () {
                state.search = searchInput.value.trim().toLowerCase();
                state.page = 1;
                render();
            });
            left.appendChild(searchInput);

            columns.forEach(function (col) {
                if (!col.facet) { return; }
                var values = [];
                rows.forEach(function (row) {
                    var v = row.values[col.index];
                    if (v && values.indexOf(v) === -1) { values.push(v); }
                });
                values.sort();
                // Counted once over the full row set, so the numbers stay put
                // while you tick boxes.
                var counts = {};
                rows.forEach(function (row) {
                    var v = row.values[col.index];
                    if (v) { counts[v] = (counts[v] || 0) + 1; }
                });

                var btn = toolbarButton(col.label, 'plusCircle', function () {
                    if (openMenu && openMenu.trigger === btn) { closeMenu(); return; }
                    var menu = el('div', 'dt-menu-panel');
                    var list = el('div', 'dt-menu-list');
                    var none = el('p', 'dt-menu-none hidden', 'No results.');

                    var search = menuSearch(col.label, function (term) {
                        var shown = 0;
                        Array.prototype.forEach.call(list.children, function (node) {
                            var hit = !term || node.getAttribute('data-value').toLowerCase().indexOf(term) !== -1;
                            node.classList.toggle('hidden', !hit);
                            if (hit) { shown += 1; }
                        });
                        none.classList.toggle('hidden', shown !== 0);
                    });
                    menu.appendChild(search.wrap);

                    var footer = el('div', 'dt-menu-footer hidden');
                    var clear = el('button', 'dt-menu-item dt-menu-clear');
                    clear.type = 'button';
                    clear.textContent = 'Clear filters';
                    clear.addEventListener('click', function (event) {
                        event.stopPropagation();
                        delete state.facets[col.index];
                        state.page = 1;
                        closeMenu();
                        render();
                    });
                    footer.appendChild(clear);

                    function syncFooter() {
                        footer.classList.toggle('hidden', !state.facets[col.index]);
                    }

                    values.forEach(function (value) {
                        var item = el('button', 'dt-menu-item');
                        item.type = 'button';
                        item.setAttribute('data-value', value);
                        var box = el('span', 'dt-check');
                        function paint() {
                            var on = !!(state.facets[col.index] && state.facets[col.index][value]);
                            box.innerHTML = '';
                            box.classList.toggle('is-on', on);
                            if (on) { box.appendChild(icon(ICONS.check)); }
                            item.setAttribute('aria-pressed', on ? 'true' : 'false');
                        }
                        paint();
                        item.appendChild(box);
                        item.appendChild(el('span', 'dt-menu-text', value));
                        item.appendChild(el('span', 'dt-menu-count', String(counts[value] || 0)));
                        item.addEventListener('click', function (event) {
                            // Stays OPEN, so several values can be ticked in
                            // one visit.
                            event.stopPropagation();
                            var set = state.facets[col.index] || (state.facets[col.index] = {});
                            if (set[value]) { delete set[value]; } else { set[value] = true; }
                            if (!Object.keys(set).length) { delete state.facets[col.index]; }
                            state.page = 1;
                            paint();
                            syncFooter();
                            render();
                        });
                        list.appendChild(item);
                    });

                    menu.appendChild(list);
                    menu.appendChild(none);
                    menu.appendChild(footer);
                    syncFooter();
                    openPopover(btn, menu);
                    search.input.focus();
                }, { menu: true, chips: true });

                // Clicking the ⊗ clears rather than opening, matching the
                // reference. Capture phase so it beats the open handler.
                btn.addEventListener('click', function (event) {
                    if (!state.facets[col.index]) { return; }
                    if (!btn.iconSlot || !btn.iconSlot.contains(event.target)) { return; }
                    event.stopPropagation();
                    delete state.facets[col.index];
                    state.page = 1;
                    closeMenu();
                    render();
                }, true);
                btn.colIndex = col.index;
                facetButtons.push(btn);
                left.appendChild(btn);
            });

            var dateCol = columns.filter(function (c) { return c.dateRange; })[0];
            if (dateCol) {
                /* The custom range runs from the oldest timestamp actually in
                 * the table (older rows are not loaded, so offering them would
                 * be a lie) to today, or later if a row is. parseDate is the
                 * filter's own parse, so the bounds share its time space. */
                var boundMin = null, boundMax = null;
                rows.forEach(function (row) {
                    var when = parseDate(row.values[dateCol.index]);
                    if (!when) { return; }
                    if (!boundMin || when < boundMin) { boundMin = when; }
                    if (!boundMax || when > boundMax) { boundMax = when; }
                });
                var todayEnd = new Date();
                todayEnd.setHours(23, 59, 59, 999);
                if (!boundMax || boundMax < todayEnd) { boundMax = todayEnd; }
                // Whole DAYS: the inputs deal in days, and a timestamp bound
                // made the oldest day unpickable (00:00 is before 03:49).
                if (boundMin) { boundMin = new Date(boundMin.getFullYear(), boundMin.getMonth(), boundMin.getDate(), 0, 0, 0); }
                boundMax = new Date(boundMax.getFullYear(), boundMax.getMonth(), boundMax.getDate(), 23, 59, 59);

                function applyRange(from, to, label) {
                    state.from = from;
                    state.to = to;
                    state.dateLabel = label;
                    state.dateCol = dateCol.index;
                    state.page = 1;
                }

                dateButton = toolbarButton(dateCol.label, 'calendar', function () {
                    if (openMenu && openMenu.trigger === dateButton) { closeMenu(); return; }
                    var menu = el('div', 'dt-menu-panel');
                    var list = el('div', 'dt-menu-list');

                    [['Today', 0], ['Last 7 days', 6], ['Last 30 days', 29], ['All time', null]]
                        .forEach(function (preset) {
                            var item = el('button', 'dt-menu-item');
                            item.type = 'button';
                            item.appendChild(el('span', 'dt-menu-text', preset[0]));
                            var mark = el('span', 'dt-menu-mark');
                            if (state.dateLabel === preset[0]) { mark.appendChild(icon(ICONS.check)); }
                            item.appendChild(mark);
                            item.addEventListener('click', function (event) {
                                event.stopPropagation();
                                if (preset[1] === null) {
                                    applyRange(null, null, 'All time');
                                } else {
                                    var now = new Date();
                                    var end = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 23, 59, 59);
                                    var start = new Date(end);
                                    start.setDate(start.getDate() - preset[1]);
                                    start.setHours(0, 0, 0, 0);
                                    applyRange(start, end, preset[0]);
                                }
                                closeMenu();
                                render();
                            });
                            list.appendChild(item);
                        });
                    menu.appendChild(list);

                    /* Two native date inputs, not a hand-built calendar:
                     * keyboard accessible, localised and browser-validated. */
                    var custom = el('div', 'dt-range');
                    custom.appendChild(el('p', 'dt-range-title', 'Custom range'));

                    function isoOf(d) {
                        if (!d) { return ''; }
                        return d.getFullYear() + '-' +
                            String(d.getMonth() + 1).padStart(2, '0') + '-' +
                            String(d.getDate()).padStart(2, '0');
                    }

                    var minIso = isoOf(boundMin), maxIso = isoOf(boundMax);

                    var fields = el('div', 'dt-range-fields');
                    var fromInput = el('input', 'dt-range-input');
                    fromInput.type = 'date';
                    fromInput.setAttribute('aria-label', 'From date');
                    fromInput.value = isoOf(state.from);
                    var toInput = el('input', 'dt-range-input');
                    toInput.type = 'date';
                    toInput.setAttribute('aria-label', 'To date');
                    toInput.value = isoOf(state.to);
                    [fromInput, toInput].forEach(function (input) {
                        // min/max grey out the unreachable days in the native
                        // picker AND drive :invalid, but they do NOT stop a
                        // typed or pasted value - the clamp below is what
                        // actually enforces the range.
                        if (minIso) { input.min = minIso; }
                        if (maxIso) { input.max = maxIso; }
                    });
                    fields.appendChild(fromInput);
                    fields.appendChild(el('span', 'dt-range-sep', '\u2192'));
                    fields.appendChild(toInput);
                    custom.appendChild(fields);

                    var note = el('p', 'dt-range-note hidden');
                    custom.appendChild(note);

                    var apply = el('button', 'dt-btn dt-btn-solid');
                    apply.type = 'button';
                    apply.textContent = 'Apply range';
                    apply.addEventListener('click', function (event) {
                        event.stopPropagation();
                        var f = fromInput.value ? new Date(fromInput.value + 'T00:00:00') : null;
                        var t = toInput.value ? new Date(toInput.value + 'T23:59:59') : null;
                        if (!f && !t) {
                            note.textContent = 'Pick at least one date.';
                            note.classList.remove('hidden');
                            return;
                        }
                        // Clamp rather than reject - the nearest date that can
                        // return rows is what was meant - but say so: silently
                        // moving a typed date would be worse.
                        var clamped = false;
                        if (f && boundMin && f < boundMin) {
                            f = new Date(boundMin.getFullYear(), boundMin.getMonth(), boundMin.getDate(), 0, 0, 0);
                            fromInput.value = isoOf(f);
                            clamped = true;
                        }
                        if (t && boundMax && t > boundMax) {
                            t = new Date(boundMax.getFullYear(), boundMax.getMonth(), boundMax.getDate(), 23, 59, 59);
                            toInput.value = isoOf(t);
                            clamped = true;
                        }
                        if (clamped) {
                            // Stay open: the note explaining the corrected
                            // dates lives in this menu.
                            note.textContent = 'Adjusted to the available range: '
                                + (minIso || '...') + ' to ' + (maxIso || '...') + '. Apply again to use it.';
                            note.classList.remove('hidden');
                            return;
                        }
                        note.classList.add('hidden');
                        if (f && t && f > t) {
                            // Swap rather than reject: the intent is obvious
                            // and an error here would just be pedantry.
                            var tmp = f; f = t; t = tmp;
                            fromInput.value = isoOf(f);
                            toInput.value = isoOf(t);
                        }
                        // An open-ended side becomes an unbounded bound, so
                        // "from 1 Aug" means everything since 1 Aug.
                        applyRange(f || new Date(0), t || new Date(8640000000000000),
                                   (fromInput.value || '...') + ' \u2192 ' + (toInput.value || '...'));
                        closeMenu();
                        render();
                    });
                    custom.appendChild(apply);
                    menu.appendChild(custom);

                    openPopover(dateButton, menu);
                }, { menu: true, chips: true });

                // Same ⊗-clears behaviour as the facet triggers.
                dateButton.addEventListener('click', function (event) {
                    if (!state.from) { return; }
                    if (!dateButton.iconSlot || !dateButton.iconSlot.contains(event.target)) { return; }
                    event.stopPropagation();
                    applyRange(null, null, null);
                    closeMenu();
                    render();
                }, true);
                left.appendChild(dateButton);
            }

            var right = el('div', 'dt-toolbar-right');

            sortButton = toolbarButton('Sort', 'sort', function () {
                if (openMenu && openMenu.trigger === sortButton) { closeMenu(); return; }
                openPopover(sortButton, buildSortPanel(), 'right');
            }, { menu: true, badge: true });
            right.appendChild(sortButton);

            viewButton = toolbarButton('View', 'view', function () {
                if (openMenu && openMenu.trigger === viewButton) { closeMenu(); return; }
                var menu = el('div', 'dt-menu-panel');
                var list = el('div', 'dt-menu-list');
                var none = el('p', 'dt-menu-none hidden', 'No results.');
                var search = menuSearch('Search columns...', function (term) {
                    var shown = 0;
                    Array.prototype.forEach.call(list.children, function (n) {
                        var hit = !term || n.textContent.toLowerCase().indexOf(term) !== -1;
                        n.classList.toggle('hidden', !hit);
                        if (hit) { shown += 1; }
                    });
                    none.classList.toggle('hidden', shown !== 0);
                });
                menu.appendChild(search.wrap);

                columns.filter(function (c) { return c.hideable; }).forEach(function (col) {
                    var item = el('button', 'dt-menu-item');
                    item.type = 'button';
                    item.appendChild(el('span', 'dt-menu-text', col.label));
                    var mark = el('span', 'dt-menu-mark');
                    function paint() {
                        mark.innerHTML = '';
                        var on = !isHidden(col);
                        if (on) { mark.appendChild(icon(ICONS.check)); }
                        item.setAttribute('aria-pressed', on ? 'true' : 'false');
                        // A filtered column cannot be hidden, so the toggle
                        // says so instead of silently doing nothing.
                        var locked = filteredOn(col);
                        item.disabled = locked;
                        if (locked) { item.setAttribute('title', 'Shown while it has a filter'); }
                        else { item.removeAttribute('title'); }
                    }
                    paint();
                    item.appendChild(mark);
                    item.addEventListener('click', function (event) {
                        // Stays open, like the facet menu. No guard needed:
                        // paint() disables the toggle on a filtered column.
                        event.stopPropagation();
                        if (state.hidden[col.index]) { delete state.hidden[col.index]; }
                        else { state.hidden[col.index] = true; }
                        paint();
                        render();
                    });
                    list.appendChild(item);
                });
                menu.appendChild(list);
                menu.appendChild(none);
                openPopover(viewButton, menu, 'right');
                search.input.focus();
            }, { menu: true });
            right.appendChild(viewButton);

            /* Export. One scope for both formats: the ticked rows, else
             * everything the search, filters and date range leave, across
             * ALL pages. Hidden columns stay out of the file. */
            if (root.hasAttribute('data-dt-print')) {
                exportButton = toolbarButton('Export', 'download', function () {
                    if (openMenu && openMenu.trigger === exportButton) { closeMenu(); return; }
                    var menu = el('div', 'dt-menu-panel');
                    var list = el('div', 'dt-menu-list');

                    function option(label, iconName, hint, run) {
                        var item = el('button', 'dt-menu-item');
                        item.type = 'button';
                        item.appendChild(icon(ICONS[iconName]));
                        item.appendChild(el('span', 'dt-menu-text', label));
                        if (hint) { item.appendChild(el('span', 'dt-menu-hint', hint)); }
                        item.addEventListener('click', function (event) {
                            event.stopPropagation();
                            closeMenu();
                            run();
                        });
                        list.appendChild(item);
                    }

                    var scoped = exportScope();
                    var hint = scoped.length + (scoped.length === 1 ? ' row' : ' rows');
                    option('PDF', 'printer', hint, printScoped);
                    option('Excel', 'download', hint, exportXlsx);

                    menu.appendChild(list);
                    openPopover(exportButton, menu, 'right');
                }, { menu: true });
                right.appendChild(exportButton);
            }

            toolbar.appendChild(left);
            toolbar.appendChild(right);
        }

        /* Sort panel, rebuilt in place rather than re-opened so the popover
         * does not jump while you edit it. */
        function buildSortPanel() {
            var panel = el('div', 'dt-sort-panel');

            function redraw() {
                panel.innerHTML = '';
                var sortable = columns.filter(function (c) { return c.sortable; });

                panel.appendChild(el('p', 'dt-sort-title',
                    state.sorts.length ? 'Sort by' : 'No sorting applied'));

                var list = el('div', 'dt-sort-list');
                state.sorts.forEach(function (entry, position) {
                    var row = el('div', 'dt-sort-row');
                    row.draggable = true;

                    var fieldBtn = el('button', 'dt-sort-field');
                    fieldBtn.type = 'button';
                    var current = columns[entry.index];
                    fieldBtn.appendChild(el('span', '', current ? current.label : 'Field'));
                    fieldBtn.appendChild(icon(ICONS.chevUpDown));
                    fieldBtn.addEventListener('click', function (event) {
                        event.stopPropagation();
                        var menu = el('div', 'dt-menu-panel');
                        var items = el('div', 'dt-menu-list');
                        var none = el('p', 'dt-menu-none hidden', 'No results.');
                        var search = menuSearch('Search fields...', function (term) {
                            var shown = 0;
                            Array.prototype.forEach.call(items.children, function (n) {
                                var hit = !term || n.textContent.toLowerCase().indexOf(term) !== -1;
                                n.classList.toggle('hidden', !hit);
                                if (hit) { shown += 1; }
                            });
                            none.classList.toggle('hidden', shown !== 0);
                        });
                        menu.appendChild(search.wrap);
                        sortable.forEach(function (col) {
                            // A field already used by another sort row is not
                            // offered again - two sorts on one column is a
                            // no-op and just clutters the panel.
                            var taken = state.sorts.some(function (s2, i2) {
                                return i2 !== position && s2.index === col.index;
                            });
                            if (taken) { return; }
                            var item = el('button', 'dt-menu-item');
                            item.type = 'button';
                            item.appendChild(el('span', 'dt-menu-text', col.label));
                            item.addEventListener('click', function (e2) {
                                e2.stopPropagation();
                                entry.index = col.index;
                                closeTopMenu();
                                redraw();
                                render();
                            });
                            items.appendChild(item);
                        });
                        menu.appendChild(items);
                        menu.appendChild(none);
                        openPopover(fieldBtn, menu, null, true);
                        search.input.focus();
                    });
                    row.appendChild(fieldBtn);

                    var dirBtn = el('button', 'dt-sort-dir');
                    dirBtn.type = 'button';
                    dirBtn.appendChild(el('span', '', entry.dir === 'asc' ? 'Asc' : 'Desc'));
                    dirBtn.appendChild(icon(ICONS.chevDown));
                    dirBtn.addEventListener('click', function (event) {
                        event.stopPropagation();
                        var menu = el('div', 'dt-menu-panel');
                        var items = el('div', 'dt-menu-list');
                        [['asc', 'Asc'], ['desc', 'Desc']].forEach(function (pair) {
                            var item = el('button', 'dt-menu-item');
                            item.type = 'button';
                            item.appendChild(el('span', 'dt-menu-text', pair[1]));
                            var mark = el('span', 'dt-menu-mark');
                            if (entry.dir === pair[0]) { mark.appendChild(icon(ICONS.check)); }
                            item.appendChild(mark);
                            item.addEventListener('click', function (e2) {
                                e2.stopPropagation();
                                entry.dir = pair[0];
                                closeTopMenu();
                                redraw();
                                render();
                            });
                            items.appendChild(item);
                        });
                        menu.appendChild(items);
                        openPopover(dirBtn, menu, null, true);
                    });
                    row.appendChild(dirBtn);

                    var del = el('button', 'dt-icon-btn dt-sort-remove');
                    del.type = 'button';
                    del.setAttribute('aria-label', 'Remove this sort');
                    del.appendChild(icon(ICONS.trash));
                    del.addEventListener('click', function (event) {
                        event.stopPropagation();
                        state.sorts.splice(position, 1);
                        redraw();
                        render();
                    });
                    row.appendChild(del);

                    var grip = el('span', 'dt-sort-grip');
                    grip.setAttribute('aria-hidden', 'true');
                    grip.appendChild(icon(ICONS.grip));
                    row.appendChild(grip);

                    row.addEventListener('dragstart', function (event) {
                        event.stopPropagation();
                        dragFrom = position;
                        row.classList.add('is-dragging');
                        if (event.dataTransfer) {
                            event.dataTransfer.effectAllowed = 'move';
                            try { event.dataTransfer.setData('text/plain', String(position)); } catch (err) { /* ignore */ }
                        }
                    });
                    row.addEventListener('dragend', function () { row.classList.remove('is-dragging'); });
                    row.addEventListener('dragover', function (event) {
                        if (dragFrom === null) { return; }
                        event.preventDefault();
                        event.stopPropagation();
                    });
                    row.addEventListener('drop', function (event) {
                        if (dragFrom === null) { return; }
                        event.preventDefault();
                        event.stopPropagation();
                        // Sort order IS the precedence order, so reordering
                        // these rows re-ranks the keys.
                        var moved = state.sorts.splice(dragFrom, 1)[0];
                        state.sorts.splice(position, 0, moved);
                        dragFrom = null;
                        redraw();
                        render();
                    });

                    list.appendChild(row);
                });
                panel.appendChild(list);

                var actions = el('div', 'dt-sort-actions');
                var add = el('button', 'dt-btn dt-btn-solid');
                add.type = 'button';
                add.textContent = 'Add sort';
                var free = sortable.filter(function (col) {
                    return !state.sorts.some(function (s2) { return s2.index === col.index; });
                });
                add.disabled = free.length === 0;
                add.addEventListener('click', function (event) {
                    event.stopPropagation();
                    if (!free.length) { return; }
                    state.sorts.push({ index: free[0].index, dir: 'asc' });
                    redraw();
                    render();
                });
                actions.appendChild(add);

                var reset = el('button', 'dt-btn');
                reset.type = 'button';
                reset.textContent = 'Reset sorting';
                reset.disabled = state.sorts.length === 0;
                reset.addEventListener('click', function (event) {
                    event.stopPropagation();
                    state.sorts = [];
                    redraw();
                    render();
                });
                actions.appendChild(reset);
                panel.appendChild(actions);
            }

            var dragFrom = null;
            redraw();
            return panel;
        }

        // ---- sortable headers -------------------------------------------
        function wireHeaders() {
            columns.forEach(function (col) {
                var th = headers[col.index];
                if (col.select) {
                    var master = th.querySelector('input[type="checkbox"]');
                    if (master) {
                        master.addEventListener('change', function () {
                            visibleRows().forEach(function (row) {
                                var box = row.tr.querySelector('[data-dt-check]');
                                if (box) { box.checked = master.checked; }
                            });
                            syncSelection();
                        });
                    }
                    return;
                }
                if (!col.sortable) { return; }
                th.classList.add('dt-th-sortable');
                th.setAttribute('role', 'button');
                th.setAttribute('tabindex', '0');
                var arrow = el('span', 'dt-arrow');
                th.appendChild(arrow);
                col.arrow = arrow;
                function toggle() {
                    // A header click REPLACES the sort list: stacking would
                    // make the arrows lie. The Sort panel is for stacking.
                    var existing = state.sorts.length === 1 && state.sorts[0].index === col.index
                        ? state.sorts[0] : null;
                    state.sorts = [{
                        index: col.index,
                        dir: existing && existing.dir === 'asc' ? 'desc' : 'asc'
                    }];
                    render();
                }
                th.addEventListener('click', toggle);
                th.addEventListener('keydown', function (event) {
                    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); toggle(); }
                });
            });
        }

        // ---- filtering / sorting ----------------------------------------
        function parseDate(value) {
            if (!value) { return null; }
            var d = new Date(value.replace(' ', 'T'));
            return isNaN(d.getTime()) ? null : d;
        }

        function matches(row) {
            if (state.search && row.haystack.indexOf(state.search) === -1) { return false; }
            for (var key in state.facets) {
                if (!Object.prototype.hasOwnProperty.call(state.facets, key)) { continue; }
                if (!state.facets[key][row.values[key]]) { return false; }
            }
            if (state.from && state.dateCol !== undefined) {
                var when = parseDate(row.values[state.dateCol]);
                if (!when || when < state.from || when > state.to) { return false; }
            }
            return true;
        }

        var filtered = [];
        function recompute() {
            filtered = rows.filter(matches);
            if (state.sorts.length) {
                filtered = filtered.slice().sort(function (a, b) {
                    for (var k = 0; k < state.sorts.length; k += 1) {
                        var entry = state.sorts[k];
                        var dir = entry.dir === 'asc' ? 1 : -1;
                        var av = a.values[entry.index], bv = b.values[entry.index];
                        var an = parseFloat(av), bn = parseFloat(bv);
                        // Numeric only when BOTH sides parse cleanly AND the
                        // whole cell is the number - otherwise "10.0.0.2"
                        // would sort as 10.
                        var numeric = !isNaN(an) && !isNaN(bn) && String(an) === av && String(bn) === bv;
                        var cmp = numeric
                            ? (an - bn) * dir
                            : av.localeCompare(bv, undefined, { numeric: true, sensitivity: 'base' }) * dir;
                        // Fall through to the next key only on a genuine tie.
                        if (cmp !== 0) { return cmp; }
                    }
                    return 0;
                });
            }
        }

        function visibleRows() {
            var start = (state.page - 1) * state.pageSize;
            return filtered.slice(start, start + state.pageSize);
        }

        // ---- selection + action bar --------------------------------------
        function selectedIds() {
            return rows.filter(function (row) {
                var box = row.tr.querySelector('[data-dt-check]');
                return box && box.checked;
            }).map(function (row) { return row.id; });
        }

        function syncSelection() {
            var ids = selectedIds();
            /* Against the SELECTABLE rows: Accounts has no checkbox on your
             * own row, so counting every row left the master stuck
             * indeterminate. */
            var page = visibleRows().filter(function (row) {
                return !!row.tr.querySelector('[data-dt-check]');
            });
            var pageChecked = page.filter(function (row) {
                return row.tr.querySelector('[data-dt-check]').checked;
            }).length;

            var master = table.querySelector('thead [type="checkbox"]');
            if (master) {
                master.checked = page.length > 0 && pageChecked === page.length;
                // Partly selected is indeterminate: an unchecked master beside
                // ticked rows reads as a bug.
                master.indeterminate = pageChecked > 0 && pageChecked < page.length;
            }

            rows.forEach(function (row) {
                var box = row.tr.querySelector('[data-dt-check]');
                row.tr.classList.toggle('dt-row-selected', !!(box && box.checked));
            });

            if (bar) {
                bar.classList.toggle('hidden', ids.length === 0);
                var label = bar.querySelector('[data-dt-count]');
                if (label) { label.textContent = ids.length + ' selected'; }
                // Ids written at submit time, so a stale selection is never
                // posted, under the table's own field name.
                var idField = root.getAttribute('data-dt-idfield') || 'job_ids';
                Array.prototype.forEach.call(bar.querySelectorAll('form[data-dt-form]'), function (form) {
                    Array.prototype.forEach.call(form.querySelectorAll('input[data-dt-bulkid]'), function (n) {
                        n.parentNode.removeChild(n);
                    });
                    ids.forEach(function (id) {
                        var input = document.createElement('input');
                        input.type = 'hidden';
                        input.name = idField;
                        input.value = id;
                        input.setAttribute('data-dt-bulkid', '');
                        form.appendChild(input);
                    });
                });
            }

            /* Cancel/Retry are disabled unless the selection holds a job they
             * can act on, rather than letting the server bounce it. Only on
             * tables that opt in (data-dt-jobrules): others have no job
             * lifecycle. */
            if (bar && root.hasAttribute('data-dt-jobrules')) {
                var statuses = rows.filter(function (row) {
                    var box = row.tr.querySelector('[data-dt-check]');
                    return box && box.checked;
                }).map(function (row) { return row.status; });
                var canCancel = statuses.some(function (st) {
                    return st === 'pending' || st === 'in_progress';
                });
                var canRetry = statuses.some(function (st) {
                    return st === 'failed' || st === 'cancelled';
                });
                var canDelete = statuses.some(function (st) {
                    return st !== 'pending' && st !== 'in_progress';
                });
                var setEnabled = function (selector, enabled, why) {
                    var node = bar.querySelector(selector);
                    if (!node) { return; }
                    node.disabled = !enabled;
                    if (enabled) { node.removeAttribute('title'); }
                    else { node.setAttribute('title', why); }
                };
                setEnabled('[data-dt-act="cancel"]', canCancel, 'Only queued or running jobs can be cancelled.');
                setEnabled('[data-dt-act="retry"]', canRetry, 'Only failed or cancelled jobs can be retried.');
                setEnabled('[data-dt-act="delete"]', canDelete, 'Queued and running jobs cannot be deleted.');
            }

            var info = footer && footer.querySelector('[data-dt-selectedinfo]');
            if (info) {
                info.textContent = ids.length + ' of ' + filtered.length + ' row(s) selected.';
            }
        }

        tbody.addEventListener('change', function (event) {
            if (event.target.matches('[data-dt-check]')) { syncSelection(); }
        });

        /* Row "..." menu. The available actions come from data attributes the
         * template sets per row, so the status rules live in one place (the
         * server's own view of the job) instead of being re-derived here. */
        tbody.addEventListener('click', function (event) {
            var trigger = event.target.closest('[data-dt-rowmenu]');
            if (!trigger) { return; }
            event.stopPropagation();
            if (openMenu && openMenu.trigger === trigger) { closeMenu(); return; }

            var tr = trigger.closest('tr');
            var menu = el('div', 'dt-menu-panel');
            var list = el('div', 'dt-menu-list');

            function action(label, iconName, url, tone, confirmKind) {
                var form = document.createElement('form');
                form.method = 'POST';
                form.action = url;
                var input = document.createElement('input');
                input.type = 'hidden';
                input.name = root.getAttribute('data-dt-idfield') || 'job_ids';
                input.value = tr.getAttribute('data-dt-id');
                form.appendChild(input);
                var item = el('button', 'dt-menu-item');
                item.type = 'button';
                if (tone) { item.setAttribute('data-tone', tone); }
                item.appendChild(icon(ICONS[iconName]));
                item.appendChild(el('span', 'dt-menu-text', label));
                item.addEventListener('click', function (e2) {
                    e2.stopPropagation();
                    closeMenu();
                    if (confirmKind) { confirmThen(confirmKind, 1, form, tr); }
                    else { document.body.appendChild(form); form.submit(); }
                });
                form.appendChild(item);
                list.appendChild(form);
            }

            /* Open: a plain link the row carries, first as the
             * non-destructive default. */
            if (tr.getAttribute('data-dt-open-url')) {
                var open = document.createElement('a');
                open.className = 'dt-menu-item';
                open.href = tr.getAttribute('data-dt-open-url');
                open.appendChild(icon(ICONS.external));
                open.appendChild(el('span', 'dt-menu-text', 'Open'));
                open.addEventListener('click', function () { closeMenu(); });
                list.appendChild(open);
            }

            /* A second, page-worded link (Lab Computers' "Print QR label"),
             * so a table can carry it beside Open. data-dt-link-target lets a
             * print sheet open beside the table, keeping your filters. */
            if (tr.getAttribute('data-dt-link-url')) {
                var link = document.createElement('a');
                link.className = 'dt-menu-item';
                link.href = tr.getAttribute('data-dt-link-url');
                var linkTarget = tr.getAttribute('data-dt-link-target');
                if (linkTarget) {
                    link.target = linkTarget;
                    /* noopener or the opened page can reach back through
                     * window.opener and navigate this one. */
                    link.rel = 'noopener';
                }
                var linkIcon = ICONS[tr.getAttribute('data-dt-link-icon')] || ICONS.external;
                link.appendChild(icon(linkIcon));
                link.appendChild(el('span', 'dt-menu-text',
                    tr.getAttribute('data-dt-link-label') || 'Open'));
                link.addEventListener('click', function () { closeMenu(); });
                list.appendChild(link);
            }

            /* Details is the page's business: announce the row, as dt:edit. */
            if (trigger.hasAttribute('data-dt-details')) {
                var details = el('button', 'dt-menu-item');
                details.type = 'button';
                details.appendChild(icon(ICONS.info));
                details.appendChild(el('span', 'dt-menu-text', 'Details'));
                details.addEventListener('click', function (event2) {
                    event2.stopPropagation();
                    closeMenu();
                    root.dispatchEvent(new CustomEvent('dt:details', {
                        bubbles: true,
                        detail: { row: tr, id: tr.getAttribute('data-dt-id') || '' }
                    }));
                });
                list.appendChild(details);
            }

            /* Edit opens something the page owns (Software Library's
             * drawer), so this only announces the row with a dt:edit event.
             * Tables without data-dt-edit never get the item. */
            if (tr.hasAttribute('data-dt-edit')) {
                var edit = el('button', 'dt-menu-item');
                edit.type = 'button';
                edit.appendChild(icon(ICONS.pencil));
                edit.appendChild(el('span', 'dt-menu-text', 'Edit'));
                edit.addEventListener('click', function (event2) {
                    event2.stopPropagation();
                    closeMenu();
                    root.dispatchEvent(new CustomEvent('dt:edit', {
                        bubbles: true,
                        detail: { row: tr, id: tr.getAttribute('data-dt-id') || '' }
                    }));
                });
                list.appendChild(edit);
            }

            /* A page-worded POST (Archive, Restore). No confirm: it is for
             * actions that can be undone; anything else belongs behind a
             * dialog. Unknown icons fall back to a check. */
            if (tr.getAttribute('data-dt-post-url')) {
                var postIcon = tr.getAttribute('data-dt-post-icon');
                action(tr.getAttribute('data-dt-post-label') || 'Submit',
                       ICONS[postIcon] ? postIcon : 'check',
                       tr.getAttribute('data-dt-post-url'), null, null);
            }

            /* Reset password. A confirm-then-post like Delete rather than a
             * drawer - there is nothing to fill in, only something to agree
             * to - but it gets its OWN dialog: the message names the account
             * and says whether an address is on file, and it is not
             * destructive, so it must not wear the red button. */
            if (trigger.hasAttribute('data-dt-reset') && tr.getAttribute('data-dt-reset-url')) {
                action('Reset password', 'key', tr.getAttribute('data-dt-reset-url'), null, 'reset');
            }

            if (tr.getAttribute('data-dt-cancel-url')) {
                action('Cancel', 'x', tr.getAttribute('data-dt-cancel-url'), null, 'cancel');
            }
            if (tr.getAttribute('data-dt-retry-url')) {
                action('Retry', 'refresh', tr.getAttribute('data-dt-retry-url'), null, 'retry');
            }
            // Only with a delete url, or the item would post to "null".
            if (trigger.getAttribute('data-dt-delete-url')) {
                if (list.children.length) {
                    list.appendChild(el('hr'));
                }
                action('Delete', 'trash', trigger.getAttribute('data-dt-delete-url'), 'destructive', 'delete');
            }

            menu.appendChild(list);
            openPopover(trigger, menu, 'right');
        });

        if (bar) {
            Array.prototype.forEach.call(bar.querySelectorAll('form[data-dt-form]'), function (form) {
                form.addEventListener('submit', function (event) {
                    var kind = form.querySelector('[data-dt-act]');
                    kind = kind ? kind.getAttribute('data-dt-act') : '';
                    if (!root.querySelector('[data-dt-confirm="' + kind + '"]')) { return; }
                    if (form.dataset.dtConfirmed === '1') {
                        // Second pass, fired by the dialog - let it through.
                        delete form.dataset.dtConfirmed;
                        return;
                    }
                    event.preventDefault();
                    // Counted by the marker attribute: field names differ
                    // per table, and a fixed one would count zero.
                    var ids = form.querySelectorAll('input[data-dt-bulkid]').length;
                    confirmThen(kind, ids, {
                        submit: function () { form.dataset.dtConfirmed = '1'; form.submit(); }
                    });
                });
            });

            var clear = bar.querySelector('[data-dt-clear]');
            if (clear) {
                clear.addEventListener('click', function () {
                    rows.forEach(function (row) {
                        var box = row.tr.querySelector('[data-dt-check]');
                        if (box) { box.checked = false; }
                    });
                    syncSelection();
                });
            }
            var exportBtn = bar.querySelector('[data-dt-export]');
            if (exportBtn) { exportBtn.addEventListener('click', exportSelected); }

            /* A page URL with the scope's ids, via exportScope(), so it
             * agrees with Export beside it on what "selected" means. */
            var scopeBtn = bar.querySelector('[data-dt-scope-url]');
            if (scopeBtn) {
                scopeBtn.addEventListener('click', function () {
                    var scope = exportScope();
                    if (!scope.length) { return; }
                    var base = scopeBtn.getAttribute('data-dt-scope-url') || '';
                    var ids = scope.map(function (row) { return row.id; }).join(',');
                    var join = base.indexOf('?') === -1 ? '?' : '&';
                    window.open(base + join + 'ids=' + encodeURIComponent(ids),
                                '_blank', 'noopener');
                });
            }
        }

        /* Rows an export covers: the ticked ones, else everything the
         * filters leave. Shared by PDF, Excel and the menu's count. */
        function exportScope() {
            var ticked = rows.filter(function (row) {
                var box = row.tr.querySelector('[data-dt-check]');
                return box && box.checked;
            });
            return ticked.length ? ticked : filtered;
        }

        function exportColumns() {
            return columns.filter(function (c) {
                return !c.select && !c.actions && !isHidden(c);
            });
        }

        /* PDF via the print dialog, reusing the print stylesheet rather than
         * a second layout. Rows in scope are force-shown while printing (the
         * table paginates), then restored on afterprint and on a timer, as
         * afterprint is unreliable when the dialog is dismissed. */
        function printScoped() {
            var scope = exportScope();
            var inScope = {};
            scope.forEach(function (row) { inScope[row.id] = true; });

            rows.forEach(function (row) {
                row.tr.classList.add(inScope[row.id] ? 'dt-print-in' : 'dt-print-out');
            });

            var restored = false;
            function restore() {
                if (restored) { return; }
                restored = true;
                rows.forEach(function (row) {
                    row.tr.classList.remove('dt-print-in');
                    row.tr.classList.remove('dt-print-out');
                });
                window.removeEventListener('afterprint', restore);
            }
            window.addEventListener('afterprint', restore);
            window.setTimeout(restore, 1000);

            window.print();
        }

        /* Excel: the server builds the .xlsx (no zip writer here), from the
         * grid this file filtered. Sent as a FILE part, since Werkzeug caps
         * non-file form values near 500KB, via fetch() so a failure is a
         * toast rather than a lost page. */
        function exportXlsx() {
            var cols = exportColumns();
            var scope = exportScope();
            if (!cols.length || !scope.length) { return; }

            var payload = {
                filename: root.getAttribute('data-dt-filename') || 'export',
                title: root.getAttribute('data-print-title') || 'Export',
                columns: cols.map(function (c) { return c.label; }),
                rows: scope.map(function (row) {
                    return cols.map(function (c) { return row.values[c.index]; });
                })
            };

            var body = new FormData();
            body.append('payload', new Blob([JSON.stringify(payload)], { type: 'application/json' }), 'grid.json');

            function failed(message) {
                if (typeof window.showToast === 'function') {
                    window.showToast(message, 'error');
                } else {
                    window.alert(message);
                }
            }

            fetch(root.getAttribute('data-dt-xlsx-url') || '/export/xlsx', {
                method: 'POST',
                body: body,
                credentials: 'same-origin'
            }).then(function (response) {
                if (!response.ok) { throw new Error('HTTP ' + response.status); }
                return response.blob();
            }).then(function (blob) {
                var url = URL.createObjectURL(blob);
                var a = document.createElement('a');
                a.href = url;
                a.download = payload.filename + '.xlsx';
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
            }).catch(function () {
                failed('The Excel export could not be created. Please try again.');
            });
        }

        function exportSelected() {
            var chosen = rows.filter(function (row) {
                var box = row.tr.querySelector('[data-dt-check]');
                return box && box.checked;
            });
            if (!chosen.length) { return; }
            var cols = columns.filter(function (c) {
                return !c.select && !c.actions && !isHidden(c);
            });
            function cell(v) {
                var s = String(v === undefined || v === null ? '' : v);
                return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
            }
            var lines = [cols.map(function (c) { return cell(c.label); }).join(',')];
            chosen.forEach(function (row) {
                lines.push(cols.map(function (c) { return cell(row.values[c.index]); }).join(','));
            });
            // The BOM makes Excel read UTF-8 instead of mangling accents.
            var blob = new Blob(['\ufeff' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8;' });
            var url = URL.createObjectURL(blob);
            var a = document.createElement('a');
            a.href = url;
            a.download = (root.getAttribute('data-dt-filename') || 'export') + '.csv';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }

        // ---- footer ------------------------------------------------------
        var pageLabel, prevBtn, nextBtn, firstBtn, lastBtn, sizeSelect;

        function buildFooter() {
            if (!footer) { return; }
            footer.innerHTML = '';
            footer.appendChild(el('span', 'dt-selected-info', '')).setAttribute('data-dt-selectedinfo', '');

            var right = el('div', 'dt-footer-right');

            var sizeWrap = el('div', 'dt-rows-per-page');
            sizeWrap.appendChild(el('span', 'dt-footer-label', 'Rows per page'));
            sizeSelect = el('select', 'dt-select-native');
            [10, 25, 50, 100].forEach(function (n) {
                var opt = el('option', '', String(n));
                opt.value = String(n);
                if (n === state.pageSize) { opt.selected = true; }
                sizeSelect.appendChild(opt);
            });
            sizeSelect.setAttribute('aria-label', 'Rows per page');
            sizeSelect.addEventListener('change', function () {
                state.pageSize = parseInt(sizeSelect.value, 10);
                state.page = 1;
                render();
            });
            sizeWrap.appendChild(sizeSelect);
            right.appendChild(sizeWrap);

            pageLabel = el('span', 'dt-footer-label', '');
            right.appendChild(pageLabel);

            function pagerBtn(iconName, label, handler) {
                var btn = el('button', 'dt-icon-btn');
                btn.type = 'button';
                btn.setAttribute('aria-label', label);
                btn.appendChild(icon(ICONS[iconName]));
                btn.addEventListener('click', handler);
                return btn;
            }
            firstBtn = pagerBtn('first', 'First page', function () { state.page = 1; render(); });
            prevBtn = pagerBtn('prev', 'Previous page', function () { state.page = Math.max(1, state.page - 1); render(); });
            nextBtn = pagerBtn('next', 'Next page', function () { state.page = Math.min(pageCount(), state.page + 1); render(); });
            lastBtn = pagerBtn('last', 'Last page', function () { state.page = pageCount(); render(); });
            [firstBtn, prevBtn, nextBtn, lastBtn].forEach(function (b) { right.appendChild(b); });

            footer.appendChild(right);
        }

        function pageCount() {
            return Math.max(1, Math.ceil(filtered.length / state.pageSize));
        }

        // ---- render ------------------------------------------------------
        function render() {
            recompute();
            var total = pageCount();
            if (state.page > total) { state.page = total; }

            var show = visibleRows();
            rows.forEach(function (row) { row.tr.classList.add('hidden'); });
            show.forEach(function (row) { row.tr.classList.remove('hidden'); });

            // Re-appended in `filtered` order (filtered-out rows trailing),
            // since showing and hiding in place keeps the server's order. Only
            // when the order differs: a no-op would move the node under an
            // open row menu.
            var desired = filtered.slice();
            rows.forEach(function (row) {
                if (filtered.indexOf(row) === -1) { desired.push(row); }
            });
            var kids = tbody.children, dirty = false;
            for (var di = 0; di < desired.length; di += 1) {
                if (kids[di] !== desired[di].tr) { dirty = true; break; }
            }
            if (dirty) {
                var frag = document.createDocumentFragment();
                desired.forEach(function (row) { frag.appendChild(row.tr); });
                tbody.appendChild(frag);
            }

            columns.forEach(function (col) {
                var off = isHidden(col);
                if (headers[col.index]) { headers[col.index].classList.toggle('hidden', off); }
                rows.forEach(function (row) {
                    var cell = row.tr.children[col.index];
                    if (cell) { cell.classList.toggle('hidden', off); }
                });
                if (col.arrow) {
                    col.arrow.innerHTML = '';
                    var active = null;
                    for (var si = 0; si < state.sorts.length; si += 1) {
                        if (state.sorts[si].index === col.index) { active = state.sorts[si]; break; }
                    }
                    var name = active ? (active.dir === 'asc' ? 'chevUp' : 'chevDown') : 'chevUpDown';
                    col.arrow.appendChild(icon(ICONS[name], active ? 'is-active' : ''));
                }
            });

            facetButtons.forEach(function (btn) {
                var set = state.facets[btn.colIndex];
                var chosen = set ? Object.keys(set) : [];
                btn.classList.toggle('is-active', chosen.length > 0);
                // Active trigger: the ⊕ becomes an ⊗ that clears the filter,
                // and the chosen values ride alongside the label as chips -
                // up to two, then a count, so a wide selection cannot push the
                // rest of the toolbar off screen.
                if (btn.iconSlot) {
                    btn.iconSlot.innerHTML = '';
                    btn.iconSlot.appendChild(icon(ICONS[chosen.length ? 'xCircle' : 'plusCircle']));
                }
                if (btn.chipSlot) {
                    btn.chipSlot.innerHTML = '';
                    btn.chipSlot.classList.toggle('hidden', chosen.length === 0);
                    if (chosen.length) {
                        btn.chipSlot.appendChild(el('span', 'dt-chip-sep'));
                        if (chosen.length > 2) {
                            btn.chipSlot.appendChild(el('span', 'dt-chip', chosen.length + ' selected'));
                        } else {
                            chosen.forEach(function (value) {
                                btn.chipSlot.appendChild(el('span', 'dt-chip', value));
                            });
                        }
                    }
                }
            });
            if (dateButton) {
                var dateOn = !!state.from;
                dateButton.classList.toggle('is-active', dateOn);
                if (dateButton.iconSlot) {
                    dateButton.iconSlot.innerHTML = '';
                    dateButton.iconSlot.appendChild(icon(ICONS[dateOn ? 'xCircle' : 'calendar']));
                }
                if (dateButton.chipSlot) {
                    dateButton.chipSlot.innerHTML = '';
                    dateButton.chipSlot.classList.toggle('hidden', !dateOn);
                    if (dateOn) {
                        dateButton.chipSlot.appendChild(el('span', 'dt-chip-sep'));
                        dateButton.chipSlot.appendChild(el('span', 'dt-chip', state.dateLabel || 'Custom'));
                    }
                }
            }
            if (sortButton && sortButton.badgeNode) {
                var n = state.sorts.length;
                sortButton.classList.toggle('is-active', n > 0);
                sortButton.badgeNode.textContent = n ? String(n) : '';
                sortButton.badgeNode.classList.toggle('hidden', !n);
            }

            if (pageLabel) { pageLabel.textContent = 'Page ' + state.page + ' of ' + total; }
            if (firstBtn) { firstBtn.disabled = state.page === 1; }
            if (prevBtn) { prevBtn.disabled = state.page === 1; }
            if (nextBtn) { nextBtn.disabled = state.page === total; }
            if (lastBtn) { lastBtn.disabled = state.page === total; }

            var empty = root.querySelector('[data-dt-empty]');
            if (empty) { empty.classList.toggle('hidden', filtered.length !== 0); }

            syncSelection();
        }

        buildToolbar();
        buildFooter();
        wireHeaders();
        render();
    }

    function init() {
        Array.prototype.forEach.call(document.querySelectorAll('[data-dt-root]'), build);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
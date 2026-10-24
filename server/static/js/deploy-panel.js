/* AppEase - Deploy Software panel.
 *
 * The category-icon base path arrives as the form's data-icon-base, so this
 * file needs no template rendering and caches like any static asset. It
 * touches only #deploy-batch-form and its own progress modal.
 */
(function () {
    var form = document.getElementById('deploy-batch-form');
    if (!form) {
        return;
    }

    // Grouping and search for the checklists: a client-side regrouping of
    // labels the server rendered flat; the form fields are unchanged.
    function renderGroupState(group, forceOpen) {
        var open = forceOpen || !group.collapsed;
        group.content.classList.toggle('hidden', !open);
        group.chevron.textContent = open ? '\u2212' : '+';
    }

    function groupChecklist(container, groupKeyFn, groupLabelFn, sortFn, accentFn, iconFn) {
        if (!container) {
            return [];
        }
        // LABELS only: innerHTML is cleared below, so any other child (the
        // computers list's empty-state <p>) would be lost or grouped as a row.
        var labels = Array.prototype.slice.call(container.children).filter(function (child) {
            return child.tagName === 'LABEL';
        });
        var labelsByKey = {};
        var order = [];
        labels.forEach(function (label) {
            var key = groupKeyFn(label);
            if (!labelsByKey[key]) {
                labelsByKey[key] = [];
                order.push(key);
            }
            labelsByKey[key].push(label);
        });
        order.sort(sortFn);

        container.innerHTML = '';
        var groups = [];
        order.forEach(function (key) {
            var section = document.createElement('div');

            // Accent is keyed off the group, not the row: each app row
            // already carries a blue Local / green Chocolatey pill, so a
            // per-row category colour would fight it and repeat what the
            // grouping already says. Unknown keys fall through to grey.
            var accent = (accentFn && accentFn(key)) || NEUTRAL_ACCENT;

            var header = document.createElement('button');
            header.type = 'button';
            // sticky + bg-canvas: the header stays visible and OPAQUE while
            // rows scroll under it. A left accent bar, not a full wash, which
            // cost contrast. hover:bg-subtle: brightness-95 is invisible on
            // white.
            header.className = 'sticky top-0 z-10 flex w-full items-center justify-between gap-2 border-b border-line bg-canvas px-3 py-2 text-left transition hover:bg-subtle ' + accent.bar;

            var titleSpan = document.createElement('span');
            titleSpan.className = 'flex items-center gap-2';

            // Category icon on the group header only: on a row it would read
            // as the app's own logo. No iconFn (computers) means no slot. The
            // 20px box is an INLINE STYLE: a missing Tailwind class would
            // leave a full-size PNG. The slot is always appended, so titles
            // align; onerror empties it rather than show a broken glyph.
            if (iconFn) {
                var iconFile = iconFn(key);
                var iconSlot = document.createElement('span');
                iconSlot.style.width = '20px';
                iconSlot.style.height = '20px';
                iconSlot.style.flex = 'none';
                if (iconFile) {
                    var iconImg = document.createElement('img');
                    iconImg.src = CATEGORY_ICON_BASE + iconFile;
                    iconImg.alt = '';
                    iconImg.setAttribute('aria-hidden', 'true');
                    iconImg.style.width = '20px';
                    iconImg.style.height = '20px';
                    iconImg.style.objectFit = 'contain';
                    iconImg.onerror = function () {
                        if (iconSlot.firstChild) {
                            iconSlot.removeChild(iconSlot.firstChild);
                        }
                    };
                    iconSlot.appendChild(iconImg);
                }
                titleSpan.appendChild(iconSlot);
            }

            var titleText = document.createElement('span');
            titleText.className = 'text-xs font-semibold text-label';
            titleText.textContent = groupLabelFn(key, labelsByKey[key].length);
            titleSpan.appendChild(titleText);
            header.appendChild(titleSpan);

            var tail = document.createElement('span');
            tail.className = 'flex items-center gap-2 text-muted';

            var countPill = document.createElement('span');
            // The group's own tint, so the pill shows on a plain header.
            countPill.className = 'rounded-full px-2 py-0.5 text-[11px] font-medium ' + accent.pill;
            countPill.textContent = String(labelsByKey[key].length);
            tail.appendChild(countPill);

            var chevron = document.createElement('span');
            chevron.className = 'text-xs font-semibold';
            chevron.textContent = '\u2212';
            tail.appendChild(chevron);
            header.appendChild(tail);

            var content = document.createElement('div');
            content.className = 'divide-y divide-rule';
            labelsByKey[key].forEach(function (label) {
                content.appendChild(label);
            });

            section.appendChild(header);
            section.appendChild(content);
            container.appendChild(section);

            var group = {
                section: section,
                content: content,
                chevron: chevron,
                countPill: countPill,
                labels: labelsByKey[key],
                collapsed: false
            };
            header.addEventListener('click', function () {
                group.collapsed = !group.collapsed;
                renderGroupState(group, false);
            });
            groups.push(group);
        });
        return groups;
    }

    function wireChecklistSearch(searchInput, groups) {
        if (!searchInput) {
            return;
        }
        function render() {
            var query = searchInput.value.trim().toLowerCase();
            groups.forEach(function (group) {
                var anyVisible = false;
                var visibleCount = 0;
                group.labels.forEach(function (label) {
                    var match = !query || label.textContent.toLowerCase().indexOf(query) !== -1;
                    label.style.display = match ? '' : 'none';
                    if (match) {
                        anyVisible = true;
                        visibleCount += 1;
                    }
                });
                group.section.style.display = anyVisible ? '' : 'none';
                // Visible rows only, not the whole group behind a search.
                if (group.countPill) {
                    group.countPill.textContent = String(query ? visibleCount : group.labels.length);
                }
                renderGroupState(group, !!query && anyVisible);
            });
        }
        searchInput.addEventListener('input', render);
    }

    // Keys are the built-in categories; an admin-added one falls through to
    // NEUTRAL_ACCENT and the generic icon, by intent. Accents use the `cat`
    // tokens, softer than Tailwind's stock hues. Class strings are whole,
    // never concatenated: Tailwind scans for literal class names. Icon
    // files share these keys and must match static/img/ exactly, case
    // included (Linux is case-sensitive); t/test_static_refs.py checks.
                    var CATEGORY_ICON_BASE = form.getAttribute('data-icon-base') || '/static/img/';
    var CATEGORY_ICONS = {
        'Browser':               'browser.png',
        'Development / IDE':     'development.png',
        'Multimedia':            'multimedia.png',
        'Office / Productivity': 'office.png',
        'Utility':               'utility.png',
        'Security / Antivirus':  'security.png',
        'Networking':            'networking.png',
        'Design / Graphics':     'design.png',
        'Virtualization':        'virtualization.png',
        'Runtime / Framework':   'runtime.png'
    };
    var NEUTRAL_ACCENT = { bar: 'border-l-4 border-line', pill: 'bg-subtle text-muted' };
    var CATEGORY_ACCENTS = {
        'Browser':              { bar: 'border-l-4 border-cat-browser',  pill: 'bg-cat-browser/10 text-label' },
        'Development / IDE':    { bar: 'border-l-4 border-cat-ide',      pill: 'bg-cat-ide/10 text-label' },
        'Multimedia':           { bar: 'border-l-4 border-cat-media',    pill: 'bg-cat-media/10 text-label' },
        'Office / Productivity':{ bar: 'border-l-4 border-cat-office',   pill: 'bg-cat-office/10 text-label' },
        'Utility':              { bar: 'border-l-4 border-cat-utility',  pill: 'bg-cat-utility/10 text-label' },
        'Security / Antivirus': { bar: 'border-l-4 border-cat-security', pill: 'bg-cat-security/10 text-label' },
        'Networking':           { bar: 'border-l-4 border-cat-network',  pill: 'bg-cat-network/10 text-label' },
        'Design / Graphics':    { bar: 'border-l-4 border-cat-design',   pill: 'bg-cat-design/10 text-label' },
        'Virtualization':       { bar: 'border-l-4 border-cat-virt',     pill: 'bg-cat-virt/10 text-label' },
        'Runtime / Framework':  { bar: 'border-l-4 border-cat-runtime',  pill: 'bg-cat-runtime/10 text-label' }
    };
    var appsListEl = document.getElementById('batch-apps-list');
    var appGroups = groupChecklist(
        appsListEl,
        function (label) {
            return label.getAttribute('data-category') || '';
        },
        function (key, count) {
            return key || 'Uncategorized';
        },
        function (a, b) {
            if (a === '') { return 1; }
            if (b === '') { return -1; }
            return a.toLowerCase().localeCompare(b.toLowerCase());
        },
        function (key) {
            return CATEGORY_ACCENTS[key] || NEUTRAL_ACCENT;
        },
        function (key) {
            // An admin-added category: the generic icon. Uncategorized: none.
            return CATEGORY_ICONS[key] || (key ? 'other.png' : '');
        }
    );
    wireChecklistSearch(document.getElementById('batch-apps-search'), appGroups);

    // Computers stay flat; search and the status tabs are two predicates
    // applied together, so neither can strand the other.
    var computersListEl = document.getElementById('batch-computers-list');
    var computerLabels = computersListEl
        ? Array.prototype.slice.call(computersListEl.querySelectorAll('label'))
        : [];
    var computerTabs = Array.prototype.slice.call(
        document.querySelectorAll('[data-computer-tab]')
    );
    var activeComputerTab = 'all';
    var computersSearch = document.getElementById('batch-computers-search');
    var computersEmpty = document.getElementById('batch-computers-empty');

    function labelStatus(label) {
        var checkbox = label.querySelector('.batch-computer-checkbox');
        return checkbox && checkbox.getAttribute('data-status') === 'online'
            ? 'online' : 'offline';
    }

    function paintComputerTabs() {
        computerTabs.forEach(function (tab) {
            var isActive = tab.getAttribute('data-computer-tab') === activeComputerTab;
            // Basecoat's tabs style off aria-selected, and app.css matches it,
            // so this works with or without the component CSS.
            tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
            tab.setAttribute('tabindex', isActive ? '0' : '-1');
        });
    }

    function renderComputers() {
        var query = computersSearch ? computersSearch.value.trim().toLowerCase() : '';
        var shown = 0;
        computerLabels.forEach(function (label) {
            var matchesTab = activeComputerTab === 'all'
                || labelStatus(label) === activeComputerTab;
            var matchesQuery = !query
                || label.textContent.toLowerCase().indexOf(query) !== -1;
            var visible = matchesTab && matchesQuery;
            label.style.display = visible ? '' : 'none';
            if (visible) { shown += 1; }
        });
        if (computersEmpty) {
            computersEmpty.classList.toggle('hidden', shown > 0);
        }
        if (computersListEl) {
            computersListEl.classList.toggle('hidden', shown === 0);
        }
        // Hoisted: declared further down, after groupChecklist has run.
        repaintLocationGroups();
    }

    computerTabs.forEach(function (tab) {
        tab.addEventListener('click', function () {
            activeComputerTab = tab.getAttribute('data-computer-tab') || 'all';
            paintComputerTabs();
            renderComputers();
        });
    });

    (function setTabCounts() {
        var counts = { all: computerLabels.length, online: 0, offline: 0 };
        computerLabels.forEach(function (label) { counts[labelStatus(label)] += 1; });
        computerTabs.forEach(function (tab) {
            var key = tab.getAttribute('data-computer-tab');
            var slot = tab.querySelector('[data-computer-tab-count]');
            if (slot) { slot.textContent = String(counts[key] || 0); }
        });
    })();

    // Grouped by location, headers NEUTRAL: locations are invented at
    // runtime, so a hue map would run out or be arbitrary. Server order is
    // kept (named rooms first); the comparator only pins '' last.
    var locationGroups = groupChecklist(
        computersListEl,
        function (label) {
            return label.getAttribute('data-location') || '';
        },
        function (key) {
            return key || 'Unassigned';
        },
        function (a, b) {
            if (a === b) { return 0; }
            if (a === '') { return 1; }
            if (b === '') { return -1; }
            return 0;
        },
        null
    );

    // A select-all checkbox cannot live INSIDE the header, which is a
    // <button> - nesting a control in a button is invalid and the click
    // lands on the button. So each header is wrapped in a sticky row
    // that holds the checkbox beside it; the header keeps its own click
    // handler for collapsing and the two never fight.
    locationGroups.forEach(function (group) {
        var header = group.section.firstChild;
        var titleText = header.textContent;

        var row = document.createElement('div');
        row.className = 'sticky top-0 z-10 flex items-center gap-2 border-b border-line bg-canvas px-3';

        var box = document.createElement('input');
        box.type = 'checkbox';
        box.className = 'batch-location-checkbox h-4 w-4 rounded border-field text-primary focus:ring-2 focus:ring-primary';
        box.setAttribute('aria-label', 'Select all computers in ' + titleText);

        header.className = 'flex flex-1 items-center justify-between gap-2 py-2 text-left transition hover:bg-subtle';

        group.section.insertBefore(row, header);
        row.appendChild(box);
        row.appendChild(header);
        group.selectAll = box;

        box.addEventListener('change', function () {
            // Only what is VISIBLE: on the Online tab, ticking "Lab 1"
            // must select Lab 1's online machines and nothing hidden.
            visibleBoxes(group).forEach(function (checkbox) {
                if (checkbox.disabled) { return; }
                checkbox.checked = box.checked;
            });
            updateCount();
        });
    });

    function visibleBoxes(group) {
        var found = [];
        group.labels.forEach(function (label) {
            if (label.style.display === 'none') { return; }
            var checkbox = label.querySelector('.batch-computer-checkbox');
            if (checkbox) { found.push(checkbox); }
        });
        return found;
    }

    // Hides a group whose rows are all filtered out (an empty "Lab 2"
    // header on the Offline tab reads as a bug), and keeps the count
    // pill honest about what is actually on screen.
    function repaintLocationGroups() {
        if (!locationGroups) { return; }
        locationGroups.forEach(function (group) {
            var boxes = visibleBoxes(group);
            group.section.style.display = boxes.length ? '' : 'none';
            if (group.countPill) {
                group.countPill.textContent = String(boxes.length);
            }
        });
    }

    function refreshLocationCheckboxes() {
        if (!locationGroups) { return; }
        locationGroups.forEach(function (group) {
            if (!group.selectAll) { return; }
            var boxes = visibleBoxes(group);
            var checked = boxes.filter(function (checkbox) { return checkbox.checked; }).length;
            group.selectAll.checked = boxes.length > 0 && checked === boxes.length;
            group.selectAll.indeterminate = checked > 0 && checked < boxes.length;
            // In all-online mode every row box is disabled, so the group
            // box has nothing to drive and follows them.
            group.selectAll.disabled = boxes.length === 0 || boxes.every(function (checkbox) {
                return checkbox.disabled;
            });
        });
    }

    paintComputerTabs();
    if (computersSearch) {
        computersSearch.addEventListener('input', renderComputers);
    }
    renderComputers();

    var appCheckboxes = Array.prototype.slice.call(document.querySelectorAll('.batch-app-checkbox'));
    var computerCheckboxes = Array.prototype.slice.call(document.querySelectorAll('.batch-computer-checkbox'));
    var allOnlineToggle = document.getElementById('batch_all_online_toggle');
    var targetAllOnlineInput = document.getElementById('batch_target_all_online');
    var computersList = document.getElementById('batch-computers-list');
    var countLabel = document.getElementById('batch-count-label');
    var deployButton = document.getElementById('batch-deploy-button');

    var modalOverlay = document.getElementById('batch-modal-overlay');
    var modalPanel = document.getElementById('batch-modal-panel');
    var modalTitle = document.getElementById('batch-modal-title');
    var modalMessage = document.getElementById('batch-modal-message');
    var modalWarning = document.getElementById('batch-modal-warning');
    var modalCancelBtn = document.getElementById('batch-modal-cancel');
    var modalConfirmBtn = document.getElementById('batch-modal-confirm');
    var modalCloseTimer = null;

    // .hidden cannot be transitioned, so showModal() forces a reflow before
    // adding .modal-open, and closeModal() re-adds .hidden only after the
    // fade. Must match app.css's 140ms.
    function showModal() {
        if (!modalOverlay) {
            return;
        }
        if (modalCloseTimer !== null) {
            window.clearTimeout(modalCloseTimer);
            modalCloseTimer = null;
        }
        modalOverlay.classList.remove('hidden');
        void modalOverlay.offsetWidth;
        modalOverlay.classList.add('modal-open');
    }

    function closeModal() {
        if (!modalOverlay) {
            return;
        }
        modalOverlay.classList.remove('modal-open');
        if (modalCloseTimer !== null) {
            window.clearTimeout(modalCloseTimer);
        }
        modalCloseTimer = window.setTimeout(function () {
            modalCloseTimer = null;
            if (!modalOverlay.classList.contains('modal-open')) {
                modalOverlay.classList.add('hidden');
            }
        }, 140);
        modalConfirmBtn.onclick = null;
    }

    function setModalAccent(isWarning) {
        if (!modalPanel) {
            return;
        }
        modalPanel.classList.remove('border-primary', 'border-warn-strong');
        modalPanel.classList.add(isWarning ? 'border-warn-strong' : 'border-primary');
    }

    function openInfoModal(title, message) {
        if (!modalOverlay) {
            return;
        }
        modalTitle.textContent = title;
        modalMessage.textContent = message;
        modalWarning.classList.add('hidden');
        modalWarning.textContent = '';
        setModalAccent(false);
        modalCancelBtn.classList.add('hidden');
        modalConfirmBtn.textContent = 'OK';
        modalConfirmBtn.onclick = closeModal;
        showModal();
    }

    function openConfirmModal(message, warningText, onConfirm) {
        if (!modalOverlay) {
            return;
        }
        modalTitle.textContent = 'Confirm deployment';
        modalMessage.textContent = message;
        if (warningText) {
            modalWarning.textContent = warningText;
            modalWarning.classList.remove('hidden');
            setModalAccent(true);
        } else {
            modalWarning.classList.add('hidden');
            modalWarning.textContent = '';
            setModalAccent(false);
        }
        modalCancelBtn.classList.remove('hidden');
        modalConfirmBtn.textContent = 'Deploy';
        modalConfirmBtn.onclick = function () {
            closeModal();
            onConfirm();
        };
        showModal();
    }

    if (modalCancelBtn) {
        modalCancelBtn.addEventListener('click', closeModal);
    }
    if (modalOverlay) {
        modalOverlay.addEventListener('click', function (event) {
            if (event.target === modalOverlay) {
                closeModal();
            }
        });
    }

    var totalOnlineComputers = computerCheckboxes.filter(function (checkbox) {
        return checkbox.getAttribute('data-status') === 'online';
    }).length;

    function checkedCount(list) {
        return list.filter(function (cb) { return cb.checked; }).length;
    }

    var selectionWrap = document.getElementById('batch-selection');
    var selectionEmpty = document.getElementById('batch-selection-empty');
    var selAppsGroup = document.getElementById('batch-selection-apps-group');
    var selAppsBox = document.getElementById('batch-selection-apps');
    var selAppsCount = document.getElementById('batch-selection-apps-count');
    var selCompsGroup = document.getElementById('batch-selection-computers-group');
    var selCompsBox = document.getElementById('batch-selection-computers');
    var selCompsCount = document.getElementById('batch-selection-computers-count');
    var tallyApps = document.getElementById('batch-tally-apps');
    var tallyComputers = document.getElementById('batch-tally-computers');
    var tallyJobs = document.getElementById('batch-tally-jobs');

    // textContent everywhere: hostnames come from agents and software
    // names from the library, so neither is safe to interpolate.
    function buildChip(text, onRemove) {
        var chip = document.createElement('span');
        chip.className = 'inline-flex items-center gap-1.5 rounded-md border border-line bg-canvas px-2 py-1 text-xs text-label';

        var name = document.createElement('span');
        name.textContent = text;
        chip.appendChild(name);

        if (onRemove) {
            var remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'text-muted hover:text-danger';
            remove.textContent = '\u00d7';
            remove.setAttribute('aria-label', 'Remove ' + text);
            remove.addEventListener('click', onRemove);
            chip.appendChild(remove);
        }
        return chip;
    }

    // Chips carry the NAME only: the label also holds a dot and a badge,
    // and the version matters for picking, not as a reminder.
    function stripVersion(text) {
        return text.replace(/\s+/g, ' ').replace(/\s*\(v[^)]*\)\s*$/, '').trim();
    }

    function checkboxLabelText(checkbox) {
        var label = checkbox.closest('label');
        if (!label) { return checkbox.value; }
        var spans = label.querySelectorAll('span');
        for (var i = 0; i < spans.length; i++) {
            if (!spans[i].getAttribute('class')) {
                return stripVersion(spans[i].textContent);
            }
        }
        return stripVersion(label.textContent);
    }

    function clearBox(box) {
        while (box && box.firstChild) {
            box.removeChild(box.firstChild);
        }
    }

    // Apps and computers are the two operands of "apps x computers", so
    // they get their own labelled groups rather than one mixed pile in
    // which a hostname and an app name looked identical.
    function renderSelection() {
        if (!selectionWrap) { return; }
        clearBox(selAppsBox);
        clearBox(selCompsBox);

        var allOnline = !!(allOnlineToggle && allOnlineToggle.checked);
        var appChips = [];
        var compChips = [];

        appCheckboxes.forEach(function (cb) {
            if (cb.checked) {
                appChips.push(buildChip(checkboxLabelText(cb), function () {
                    cb.checked = false;
                    updateCount();
                }));
            }
        });

        if (allOnline) {
            // One chip for the toggle rather than N unremovable ones -
            // the individual boxes are disabled in this mode, so a
            // per-computer x would have nothing to switch off.
            compChips.push(buildChip('All online computers (' + totalOnlineComputers + ')', function () {
                allOnlineToggle.checked = false;
                toggleAllOnline();
            }));
        } else {
            computerCheckboxes.forEach(function (cb) {
                if (cb.checked) {
                    compChips.push(buildChip(checkboxLabelText(cb), function () {
                        cb.checked = false;
                        updateCount();
                    }));
                }
            });
        }

        appChips.forEach(function (chip) { selAppsBox.appendChild(chip); });
        compChips.forEach(function (chip) { selCompsBox.appendChild(chip); });

        // A group with nothing in it hides its heading too, so the rail
        // never shows a "Computers" label above empty space.
        if (selAppsGroup) { selAppsGroup.classList.toggle('hidden', appChips.length === 0); }
        if (selCompsGroup) { selCompsGroup.classList.toggle('hidden', compChips.length === 0); }
        if (selAppsCount) { selAppsCount.textContent = String(appChips.length); }
        // The all-online chip is ONE chip standing for N machines; the
        // count must say what will be deployed to, not how many chips.
        if (selCompsCount) {
            selCompsCount.textContent = String(
                allOnline ? totalOnlineComputers : compChips.length
            );
        }

        var total = appChips.length + compChips.length;
        selectionWrap.classList.toggle('hidden', total === 0);
        if (selectionEmpty) {
            selectionEmpty.classList.toggle('hidden', total > 0);
        }
    }

    function updateCount() {
        var checkedApps = checkedCount(appCheckboxes);
        var checkedComputers = (allOnlineToggle && allOnlineToggle.checked)
            ? totalOnlineComputers
            : checkedCount(computerCheckboxes);
        var total = checkedApps * checkedComputers;
        if (countLabel) {
            countLabel.textContent = (checkedApps === 1 ? '1 software item' : checkedApps + ' software items') +
            ' \u00d7 ' + checkedComputers + ' computers = ' + total + ' jobs';
        }
        if (tallyApps) { tallyApps.textContent = String(checkedApps); }
        if (tallyComputers) { tallyComputers.textContent = String(checkedComputers); }
        if (tallyJobs) { tallyJobs.textContent = String(total); }
        // Plain 'Deploy' at zero: the click explains what is missing, and
        // 'Deploy 0 jobs' reads like a real action.
        if (deployButton) {
            deployButton.textContent = total > 0
                ? 'Deploy ' + total + (total === 1 ? ' job' : ' jobs')
                : 'Deploy';
        }
        renderSelection();
        // Single funnel: chip removal, a row click, the tabs and the
        // all-online toggle all end up here, so the group boxes can
        // never disagree with the rows beneath them.
        refreshLocationCheckboxes();
    }

    function toggleAllOnline() {
        var isOn = allOnlineToggle.checked;
        targetAllOnlineInput.value = isOn ? '1' : '0';
        computerCheckboxes.forEach(function (cb) {
            cb.disabled = isOn;
        });
        if (computersList) {
            computersList.style.opacity = isOn ? '0.5' : '1';
        }
        updateCount();
    }

    appCheckboxes.forEach(function (cb) { cb.addEventListener('change', updateCount); });
    computerCheckboxes.forEach(function (cb) { cb.addEventListener('change', updateCount); });
    if (allOnlineToggle) {
        allOnlineToggle.addEventListener('change', toggleAllOnline);
    }
    updateCount();

    if (deployButton) {
        deployButton.addEventListener('click', function (event) {
            event.preventDefault();

            var checkedApps = checkedCount(appCheckboxes);
            if (checkedApps === 0) {
                openInfoModal('Select software', 'Choose at least one software item to deploy.');
                return;
            }

            var isAllOnline = allOnlineToggle && allOnlineToggle.checked;
            var checkedComputers = isAllOnline ? totalOnlineComputers : checkedCount(computerCheckboxes);

            if (!isAllOnline && checkedComputers === 0) {
                openInfoModal('Select computers', 'Choose at least one computer, or use \u2018All online computers\u2019.');
                return;
            }

            var offlineCount = 0;
            if (!isAllOnline) {
                offlineCount = computerCheckboxes.filter(function (checkbox) {
                    if (!checkbox.checked) {
                        return false;
                    }
                    return checkbox.getAttribute('data-status') !== 'online';
                }).length;
            }

            var totalJobs = checkedApps * checkedComputers;
            var computerLabel = isAllOnline ? 'all online computers' : (checkedComputers + ' computer(s)');
            var message = 'You\'re about to create up to ' + totalJobs + ' deployment job(s) across ' + computerLabel + '. Deploy?';
            var warningText = offlineCount > 0
                ? ('\u26A0 ' + offlineCount + ' of the selected computer(s) are offline - their jobs will queue and run when those PCs come back online.')
                : '';

            openConfirmModal(message, warningText, submitDeploy);
        });
    }

    // ---- Deployment progress modal ----------------------------
    // Submitted with fetch() so the page, and this modal, survive; the
    // plain POST is the fallback when fetch() itself fails (offline, or an
    // expired session answering with the login page).
    var POLL_MS = 2000;
    var IDLE_LIMIT = 90;

    var progressOverlay = document.getElementById('deploy-progress-overlay');
    var progressPanel = document.getElementById('deploy-progress-panel');
    var progressTitle = document.getElementById('deploy-progress-title');
    var progressBar = document.getElementById('deploy-progress-bar');
    var progressCount = document.getElementById('deploy-progress-count');
    var progressBreakdown = document.getElementById('deploy-progress-breakdown');
    var progressNote = document.getElementById('deploy-progress-note');
    var progressList = document.getElementById('deploy-progress-list');
    var progressHint = document.getElementById('deploy-progress-hint');
    var progressResumeBtn = document.getElementById('deploy-progress-resume');
    var progressCloseBtn = document.getElementById('deploy-progress-close');
    var progressDismissBtn = document.getElementById('deploy-progress-dismiss');
    var progressActive = document.getElementById('deploy-progress-active');
    var progressTrack = document.getElementById('deploy-progress-track');
    var progressBeat = document.getElementById('deploy-progress-beat');

    var progressCloseTimer = null;
    var pollTimer = null;
    var pollBatchId = null;
    var lastSignature = '';
    var idleTicks = 0;
    // Built once and updated in place: rebuilding every poll restarted
    // every CSS animation on a 2s cycle.
    var rowRegistry = null;
    var lastCountText = '';

    // Stated plainly rather than faked with a spinner: agents poll
    // on an interval, so Pending genuinely sits still for a while.
    var DEFAULT_HINT = 'Agents check in on an interval, so jobs sit on Pending for a few seconds before they start. Deployment keeps running if you close this.';

    var STATUS_TEXT = {
        completed: 'Success',
        failed: 'Failed',
        in_progress: 'Installing',
        pending: 'Pending',
        cancelled: 'Cancelled'
    };

    // app.css classes on the shared --tone-* pairs, so a job status and an
    // audit severity agree on what red means, and follow the theme.
    var STATUS_CLASSES = {
        completed: 'pill-tone-good',
        failed: 'pill-tone-bad',
        in_progress: 'pill-tone-info',
        pending: 'pill-tone-warn',
        cancelled: 'pill-tone-neutral'
    };

    // An uninstall batch reads its own words; every install word above is
    // unchanged. "Removed" is only for a removal the server counts as one
    // (job.removed): a completed uninstall nothing confirmed reads
    // "Finished", in the warning tone, beside its own reason.
    var progressKind = 'install';
    var UNINSTALL_STATUS_TEXT = {
        completed: 'Removed',
        failed: 'Failed',
        in_progress: 'Removing',
        pending: 'Waiting',
        cancelled: 'Cancelled'
    };
    var UNINSTALL_HINT = 'Agents check in on an interval, so uninstalls sit on Waiting for a few seconds. Each is checked against Windows\u2019 program list for up to 2 minutes after it runs. Uninstalls keep running if you close this.';

    // Same reflow-then-fade convention as the confirm modal above.
    function showProgressModal() {
        if (!progressOverlay) {
            return;
        }
        if (progressCloseTimer !== null) {
            window.clearTimeout(progressCloseTimer);
            progressCloseTimer = null;
        }
        progressOverlay.classList.remove('hidden');
        void progressOverlay.offsetWidth;
        progressOverlay.classList.add('modal-open');
    }

    function setProgressAccent(accentClass) {
        if (!progressPanel) {
            return;
        }
        progressPanel.classList.remove('border-primary', 'border-danger', 'border-warn-strong');
        progressPanel.classList.add(accentClass);
    }

    function stopPolling() {
        if (pollTimer !== null) {
            window.clearTimeout(pollTimer);
            pollTimer = null;
        }
    }

    // Close reloads rather than just hiding. The Recent Deployment
    // Jobs table below was rendered before these jobs existed, and
    // the reload also clears the checked apps/computers so the same
    // batch cannot be fired off twice by accident.
    function closeProgressModal() {
        stopPolling();
        if (!progressOverlay) {
            return;
        }
        progressOverlay.classList.remove('modal-open');
        if (progressCloseTimer !== null) {
            window.clearTimeout(progressCloseTimer);
        }
        progressCloseTimer = window.setTimeout(function () {
            progressCloseTimer = null;
            // The dashboard, where Recent Deployment Jobs shows the queued
            // jobs; reloading here would show only an empty form.
            var target = form.getAttribute('data-done-url');
            window.location.assign(target || window.location.pathname);
        }, 140);
    }

    function pauseUpdates(reason) {
        stopPolling();
        hideHeartbeat();
        if (progressHint) {
            progressHint.textContent = reason;
        }
        if (progressResumeBtn) {
            progressResumeBtn.classList.remove('hidden');
        }
    }

    function resumeUpdates() {
        if (progressResumeBtn) {
            progressResumeBtn.classList.add('hidden');
        }
        if (progressHint) {
            progressHint.textContent = progressKind === 'uninstall' ? UNINSTALL_HINT : DEFAULT_HINT;
        }
        idleTicks = 0;
        lastSignature = '';
        pollProgress();
    }

    // Restarting a one-shot CSS animation needs the class removed, a
    // forced reflow, then the class re-added - same idiom the modal
    // fades use for .modal-open.
    function restartAnimation(el, classes, wanted) {
        if (!el) {
            return;
        }
        for (var i = 0; i < classes.length; i++) {
            el.classList.remove(classes[i]);
        }
        void el.offsetWidth;
        el.classList.add(wanted);
    }

    var FLASH_CLASSES = [
        'deploy-row-changed',
        'deploy-row-changed-good',
        'deploy-row-changed-bad'
    ];

    function flashRow(row, status) {
        var wanted = status === 'failed' ? 'deploy-row-changed-bad'
            : (status === 'completed' ? 'deploy-row-changed-good' : 'deploy-row-changed');
        restartAnimation(row, FLASH_CLASSES, wanted);
    }

    function tickHeartbeat() {
        if (!progressBeat) {
            return;
        }
        progressBeat.classList.remove('deploy-heartbeat-off');
        restartAnimation(progressBeat, ['deploy-heartbeat-tick'], 'deploy-heartbeat-tick');
    }

    // A still dot must always mean "not polling", never "polling but
    // nothing changed" - otherwise the cue is worthless.
    function hideHeartbeat() {
        if (!progressBeat) {
            return;
        }
        progressBeat.classList.remove('deploy-heartbeat-tick');
        progressBeat.classList.add('deploy-heartbeat-off');
    }

    // The dot is static on every status and pulses only on Installing,
    // which is the one status that is genuinely mid-action. Pending
    // stays still on purpose: the agent has not picked the job up yet,
    // and animating it would claim otherwise.
    function applyPill(pill, status, removed) {
        var unconfirmed = progressKind === 'uninstall' && status === 'completed' && !removed;
        pill.className = 'rounded-full px-2.5 py-1 text-xs font-medium ' +
            (unconfirmed ? 'pill-tone-warn' : (STATUS_CLASSES[status] || 'pill-tone-neutral'));
        pill.textContent = '';
        var dot = document.createElement('span');
        dot.className = 'deploy-dot' + (status === 'in_progress' ? ' deploy-dot-live' : '');
        pill.appendChild(dot);
        var words = progressKind === 'uninstall' ? UNINSTALL_STATUS_TEXT : STATUS_TEXT;
        pill.appendChild(document.createTextNode(unconfirmed ? 'Finished' : (words[status] || status)));
    }

    function jobKey(label, hostname) {
        return label + '\u0000' + hostname;
    }

    // Built with textContent, never innerHTML: hostnames arrive from
    // whatever the agents report and are not trusted as markup.
    // Grouped by app (the server already orders failures to the top
    // of each group) because a batch is a few apps across many PCs.
    function buildRows(groups) {
        progressList.textContent = '';
        rowRegistry = {};
        var index = 0;
        for (var g = 0; g < groups.length; g++) {
            var group = groups[g];
            var header = document.createElement('div');
            header.className = 'bg-subtle px-3 py-2 text-xs font-medium text-muted';
            header.textContent = group.label;
            progressList.appendChild(header);

            var body = document.createElement('div');
            body.className = 'divide-y divide-rule';
            for (var j = 0; j < group.jobs.length; j++) {
                var job = group.jobs[j];
                var row = document.createElement('div');
                row.className = 'deploy-row-enter flex items-center justify-between gap-2 px-3 py-2';
                // Capped so a 36-job batch does not spend four seconds
                // finishing its entrance.
                row.style.animationDelay = (Math.min(index, 12) * 30) + 'ms';

                var left = document.createElement('span');
                left.className = 'font-mono text-xs text-body';
                var host = document.createElement('span');
                host.textContent = job.hostname;
                left.appendChild(host);

                var retry = document.createElement('span');
                retry.className = 'text-xs text-faint';
                left.appendChild(retry);

                // The reason sits beside the pill as plain toned text. Its
                // label and tone come from the server (job_result()), and it
                // is written with textContent like everything else here.
                var right = document.createElement('span');
                right.className = 'flex items-center gap-2';
                var result = document.createElement('span');
                result.className = 'job-result';
                var pill = document.createElement('span');
                right.appendChild(result);
                right.appendChild(pill);
                row.appendChild(left);
                row.appendChild(right);
                body.appendChild(row);

                rowRegistry[jobKey(group.label, job.hostname)] = {
                    row: row,
                    pill: pill,
                    retry: retry,
                    result: result,
                    status: null
                };
                index++;
            }
            progressList.appendChild(body);
        }
    }

    // Rows keep their position once painted. The server sorts failures
    // to the top of each group, which decides the initial order; after
    // that a row that changes status stays put rather than jumping out
    // from under the pointer. The flash is what marks the change.
    function renderRows(groups) {
        if (!progressList) {
            return;
        }

        var needsBuild = !rowRegistry;
        if (!needsBuild) {
            for (var g = 0; g < groups.length && !needsBuild; g++) {
                for (var j = 0; j < groups[g].jobs.length; j++) {
                    if (!rowRegistry[jobKey(groups[g].label, groups[g].jobs[j].hostname)]) {
                        needsBuild = true;
                        break;
                    }
                }
            }
        }
        if (needsBuild) {
            buildRows(groups);
        }

        for (var gi = 0; gi < groups.length; gi++) {
            var group = groups[gi];
            for (var ji = 0; ji < group.jobs.length; ji++) {
                var job = group.jobs[ji];
                var entry = rowRegistry[jobKey(group.label, job.hostname)];
                if (!entry) {
                    continue;
                }
                if (entry.status !== job.status) {
                    applyPill(entry.pill, job.status, job.removed);
                    // Never flash on first paint - only on a real move.
                    if (entry.status !== null) {
                        flashRow(entry.row, job.status);
                    }
                    entry.status = job.status;
                }
                var retryText = (job.retry_count > 0 && job.status !== 'completed')
                    ? (' \u00b7 retry ' + job.retry_count)
                    : '';
                if (entry.retry.textContent !== retryText) {
                    entry.retry.textContent = retryText;
                }
                var result = job.result || null;
                var resultText = (result && result.label) || '';
                if (entry.result.textContent !== resultText) {
                    entry.result.textContent = resultText;
                }
                entry.result.setAttribute('data-tone', (result && result.tone) || 'neutral');
                entry.result.title = (result && result.exit_code) ? ('Exit code ' + result.exit_code) : '';
            }
        }
    }

    function renderProgress(data) {
        var counts = data.counts || {};
        var total = data.total || 0;
        var finished = data.finished || 0;
        var running = counts.in_progress || 0;
        var pct = total ? Math.round((finished / total) * 100) : 0;
        // Second segment covers jobs currently installing, so the bar
        // stops sitting at 0% while agents are visibly working. Still
        // real data - it is the in-flight count, not padding.
        var pctActive = total ? Math.round(((finished + running) / total) * 100) : 0;

        // Inline width, not a Tailwind class: arbitrary percentage
        // widths are not in the compiled stylesheet.
        if (progressBar) {
            progressBar.style.width = pct + '%';
        }
        if (progressActive) {
            progressActive.style.width = pctActive + '%';
            progressActive.classList.toggle('deploy-bar-active', running > 0);
        }
        // role=progressbar without a live aria-valuenow announces a static
        // 0% for the whole deployment.
        if (progressTrack) {
            progressTrack.setAttribute('aria-valuenow', String(pct));
        }
        if (progressCount) {
            var countText = progressKind === 'uninstall'
                ? ((counts.removed || 0) + ' of ' + total + ' removed')
                : (finished + ' / ' + total + ' complete');
            if (countText !== lastCountText) {
                progressCount.textContent = countText;
                if (lastCountText) {
                    restartAnimation(progressCount, ['deploy-count-bump'], 'deploy-count-bump');
                }
                lastCountText = countText;
            }
        }
        if (progressBreakdown && progressKind === 'uninstall') {
            progressBreakdown.textContent =
                (counts.removed || 0) + ' removed \u00b7 ' +
                (counts.failed || 0) + ' failed \u00b7 ' +
                (counts.in_progress || 0) + ' removing \u00b7 ' +
                (counts.pending || 0) + ' waiting' +
                ((counts.cancelled || 0) ? (' \u00b7 ' + counts.cancelled + ' cancelled') : '');
        } else if (progressBreakdown) {
            progressBreakdown.textContent =
                (counts.completed || 0) + ' succeeded \u00b7 ' +
                (counts.failed || 0) + ' failed \u00b7 ' +
                (counts.in_progress || 0) + ' installing \u00b7 ' +
                (counts.pending || 0) + ' pending' +
                ((counts.cancelled || 0) ? (' \u00b7 ' + counts.cancelled + ' cancelled') : '');
        }

        renderRows(data.groups || []);
        setProgressAccent(counts.failed ? 'border-danger' : 'border-primary');

        if (data.done && progressTitle) {
            if (progressKind === 'uninstall') {
                progressTitle.textContent = counts.failed
                    ? 'Uninstall finished with failures'
                    : 'Uninstall complete';
            } else {
                progressTitle.textContent = counts.failed
                    ? 'Deployment finished with failures'
                    : 'Deployment complete';
            }
        }
    }

    function pollProgress() {
        if (!pollBatchId) {
            return;
        }
        fetch('/api/deployments/' + encodeURIComponent(pollBatchId) + '/progress', {
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            credentials: 'same-origin'
        })
            .then(function (res) {
                if (!res.ok) {
                    throw new Error('progress request failed');
                }
                return res.json();
            })
            .then(function (data) {
                renderProgress(data);
                tickHeartbeat();

                if (data.done) {
                    stopPolling();
                    hideHeartbeat();
                    if (progressHint) {
                        progressHint.textContent = progressKind === 'uninstall'
                            ? 'All uninstalls finished. Close to see them on the dashboard.'
                            : 'All jobs finished. Close to refresh the dashboard.';
                    }
                    return;
                }

                // Nothing has moved for a while: a target PC is
                // probably switched off. Pause and say so rather
                // than polling every 2s indefinitely.
                var signature = JSON.stringify(data.counts);
                if (signature === lastSignature) {
                    idleTicks++;
                } else {
                    idleTicks = 0;
                    lastSignature = signature;
                }
                if (idleTicks >= IDLE_LIMIT) {
                    pauseUpdates(progressKind === 'uninstall'
                        ? 'No change for a few minutes, so updates are paused. An uninstall whose computer goes offline is cancelled after 2 minutes.'
                        : 'No change for a few minutes, so updates are paused. Offline computers still run their jobs when they come back online.');
                    return;
                }

                pollTimer = window.setTimeout(pollProgress, POLL_MS);
            })
            .catch(function () {
                pauseUpdates('Could not reach the server, so updates are paused. The queued jobs themselves are unaffected.');
            });
    }

    function openProgressModal(data) {
        progressKind = 'install';
        renderSkipped([]);
        pollBatchId = data.batch_id;
        idleTicks = 0;
        lastSignature = '';

        var appLabel = data.app_count === 1 ? '1 software item' : (data.app_count + ' software items');
        var pcLabel = data.computer_count === 1 ? '1 computer' : (data.computer_count + ' computers');
        if (progressTitle) {
            progressTitle.textContent = 'Deploying ' + appLabel + ' to ' + pcLabel;
        }

        // What no job row can show: skipped duplicates and offline targets.
        var notes = [];
        if (data.skipped_count) {
            notes.push(data.skipped_count + ' duplicate pending job(s) were skipped.');
        }
        if (data.offline_count) {
            notes.push(data.offline_count + ' target computer(s) are offline - their jobs run when those PCs come back online.');
        }
        if (progressNote) {
            if (notes.length) {
                progressNote.textContent = notes.join(' ');
                progressNote.classList.remove('hidden');
            } else {
                progressNote.textContent = '';
                progressNote.classList.add('hidden');
            }
        }

        if (progressHint) {
            progressHint.textContent = DEFAULT_HINT;
        }
        if (progressResumeBtn) {
            progressResumeBtn.classList.add('hidden');
        }
        setProgressAccent('border-primary');
        if (progressBar) {
            progressBar.style.width = '0%';
        }
        if (progressCount) {
            progressCount.textContent = '0 / ' + data.created_count + ' complete';
        }
        if (progressBreakdown) {
            progressBreakdown.textContent = '';
        }
        if (progressList) {
            progressList.textContent = '';
        }
        // Reset so a second batch in the same page life rebuilds its
        // rows instead of reusing the previous batch's registry.
        rowRegistry = null;
        lastCountText = '';
        if (progressBar) {
            progressBar.style.width = '0%';
        }
        if (progressTrack) {
            progressTrack.setAttribute('aria-valuenow', '0');
        }
        if (progressActive) {
            progressActive.style.width = '0%';
            progressActive.classList.remove('deploy-bar-active');
        }

        showProgressModal();
        pollProgress();
    }

    var progressSkipped = document.getElementById('deploy-progress-skipped');
    var progressSkippedCount = document.getElementById('deploy-progress-skipped-count');
    var progressSkippedList = document.getElementById('deploy-progress-skipped-list');

    // What /uninstall-batch skipped. They never became jobs, so no poll can
    // show them; listed once, from the reply, with textContent only.
    function renderSkipped(skipped) {
        if (!progressSkipped || !progressSkippedList) {
            return;
        }
        progressSkippedList.textContent = '';
        skipped.forEach(function (item) {
            var li = document.createElement('li');
            li.className = 'flex items-start justify-between gap-3';
            var who = document.createElement('span');
            var host = document.createElement('span');
            host.className = 'font-mono text-ink';
            host.textContent = item.computer || '';
            var what = document.createElement('span');
            what.className = 'block text-muted';
            what.textContent = item.software || '';
            who.appendChild(host);
            who.appendChild(what);
            var why = document.createElement('span');
            why.className = 'text-right text-muted';
            why.textContent = item.reason_text || '';
            li.appendChild(who);
            li.appendChild(why);
            progressSkippedList.appendChild(li);
        });
        if (progressSkippedCount) {
            progressSkippedCount.textContent = String(skipped.length);
        }
        progressSkipped.classList.toggle('hidden', skipped.length === 0);
    }

    // An uninstall batch, from /uninstall-batch's reply: the same modal and
    // the same polling, in uninstall words.
    function openUninstallProgress(reply) {
        progressKind = 'uninstall';
        pollBatchId = reply.batch_id;
        idleTicks = 0;
        lastSignature = '';
        var created = reply.created || [];
        var programs = {};
        var computers = {};
        created.forEach(function (item) {
            programs[item.software] = true;
            computers[item.computer] = true;
        });
        var p = Object.keys(programs).length;
        var c = Object.keys(computers).length;
        if (progressTitle) {
            progressTitle.textContent = 'Uninstalling ' + (p === 1 ? '1 program' : p + ' programs') +
                ' on ' + (c === 1 ? '1 computer' : c + ' computers');
        }
        if (progressNote) {
            progressNote.textContent = '';
            progressNote.classList.add('hidden');
        }
        renderSkipped(reply.skipped || []);
        if (progressHint) {
            progressHint.textContent = UNINSTALL_HINT;
        }
        if (progressResumeBtn) {
            progressResumeBtn.classList.add('hidden');
        }
        setProgressAccent('border-primary');
        if (progressCount) {
            progressCount.textContent = '0 of ' + created.length + ' removed';
        }
        if (progressBreakdown) {
            progressBreakdown.textContent = '';
        }
        if (progressList) {
            progressList.textContent = '';
        }
        rowRegistry = null;
        lastCountText = '';
        if (progressBar) {
            progressBar.style.width = '0%';
        }
        if (progressTrack) {
            progressTrack.setAttribute('aria-valuenow', '0');
        }
        if (progressActive) {
            progressActive.style.width = '0%';
            progressActive.classList.remove('deploy-bar-active');
        }
        showProgressModal();
        pollProgress();
    }

    // Uninstall mode clears Install mode's selections on every switch:
    // nothing picked in one mode carries into the other.
    function resetSelections() {
        appCheckboxes.forEach(function (cb) { cb.checked = false; });
        computerCheckboxes.forEach(function (cb) { cb.checked = false; });
        if (allOnlineToggle && allOnlineToggle.checked) {
            allOnlineToggle.checked = false;
            toggleAllOnline();
        }
        ['batch-apps-search', 'batch-computers-search'].forEach(function (id) {
            var input = document.getElementById(id);
            if (input && input.value) {
                input.value = '';
                input.dispatchEvent(new Event('input'));
            }
        });
        activeComputerTab = 'all';
        paintComputerTabs();
        renderComputers();
        updateCount();
    }

    function submitDeploy() {
        if (deployButton) {
            deployButton.disabled = true;
        }
        var canFallback = true;

        fetch(form.action, {
            method: 'POST',
            body: new FormData(form),
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            credentials: 'same-origin'
        })
            .then(function (res) {
                var ctype = res.headers.get('Content-Type') || '';
                if (ctype.indexOf('application/json') === -1) {
                    // An expired session answers with the login page
                    // as HTML - let the plain POST navigate to it.
                    throw new Error('not json');
                }
                // Past this point the POST reached the route and the
                // jobs may already exist, so never re-submit.
                canFallback = false;
                return res.json().then(function (data) {
                    return { ok: res.ok, data: data };
                });
            })
            .then(function (result) {
                if (deployButton) {
                    deployButton.disabled = false;
                }
                if (!result.ok || !result.data.ok) {
                    openInfoModal('Deployment failed', result.data.error || 'Something went wrong.');
                    return;
                }
                if (!result.data.created_count) {
                    // Every job duplicated one already pending, so
                    // there is no new batch to watch.
                    openInfoModal('Nothing to deploy', result.data.message);
                    return;
                }
                openProgressModal(result.data);
            })
            .catch(function () {
                if (canFallback) {
                    form.submit();
                    return;
                }
                if (deployButton) {
                    deployButton.disabled = false;
                }
                openInfoModal('Deployment started', 'The jobs were created, but the progress view could not be shown. Refresh the dashboard to see them.');
            });
    }

    if (progressCloseBtn) {
        progressCloseBtn.addEventListener('click', closeProgressModal);
    }
    if (progressDismissBtn) {
        progressDismissBtn.addEventListener('click', closeProgressModal);
    }
    if (progressResumeBtn) {
        progressResumeBtn.addEventListener('click', resumeUpdates);
    }
    if (progressOverlay) {
        progressOverlay.addEventListener('click', function (event) {
            if (event.target === progressOverlay) {
                closeProgressModal();
            }
        });
    }

    window.AppEaseDeploy = {
        openInfoModal: openInfoModal,
        openUninstallProgress: openUninstallProgress,
        resetSelections: resetSelections
    };
})();
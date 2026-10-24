// AppEase - corner toast notifications. Vanilla ES5, no libraries.
//
// Pages call showToast(message, type) on DOMContentLoaded; the look is
// #toast-container and the .toast classes in app.css. A JS-owned timer is
// the source of truth, and a toast leaves the DOM only after its fade:
// FADE_MS must match .toast's 220ms transition there.

(function () {
    var AUTO_DISMISS_MS = 4000;
    var FADE_MS = 220;

    // Unrecognised types fall back to success. Icons are author-written
    // markup; the message itself is set with textContent, never innerHTML.
    var ICONS = {
        success: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10" /><polyline points="9 12 11 14 15 10" /></svg>',
        info:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10" /><line x1="12" y1="11" x2="12" y2="16" /><line x1="12" y1="8" x2="12.01" y2="8" /></svg>',
        warning: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z" /><line x1="12" y1="9" x2="12" y2="13" /><line x1="12" y1="17" x2="12.01" y2="17" /></svg>',
        error:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" /></svg>'
    };

    function getContainer() {
        return document.getElementById('toast-container');
    }

    window.showToast = function (message, type) {
        var container = getContainer();
        if (!container || !message) {
            return;
        }

        var kind = ICONS[type] ? type : 'success';

        var toast = document.createElement('div');
        toast.className = 'toast toast-' + kind;

        var icon = document.createElement('span');
        icon.className = 'toast-icon';
        icon.innerHTML = ICONS[kind];
        toast.appendChild(icon);

        var text = document.createElement('span');
        text.className = 'toast-message';
        text.textContent = message;
        toast.appendChild(text);

        var closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.className = 'toast-close';
        closeBtn.setAttribute('aria-label', 'Dismiss notification');
        closeBtn.textContent = '\u00D7';
        toast.appendChild(closeBtn);

        container.appendChild(toast);

        // Force a reflow so the start state paints before .toast-show, or the
        // transition is skipped.
        void toast.offsetWidth;
        toast.classList.add('toast-show');

        var autoDismissTimer = null;
        var removeTimer = null;

        function dismiss() {
            if (autoDismissTimer !== null) {
                window.clearTimeout(autoDismissTimer);
                autoDismissTimer = null;
            }
            if (removeTimer !== null) {
                window.clearTimeout(removeTimer);
            }
            toast.classList.remove('toast-show');
            toast.classList.add('toast-hide');
            removeTimer = window.setTimeout(function () {
                if (toast.parentNode) {
                    toast.parentNode.removeChild(toast);
                }
            }, FADE_MS);
        }

        closeBtn.addEventListener('click', dismiss);
        autoDismissTimer = window.setTimeout(dismiss, AUTO_DISMISS_MS);
    };
})();
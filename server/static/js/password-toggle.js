(function () {
    // The same style as base.html's inline SVGs: 24-unit viewBox,
    // currentColor stroke, stroke-width 2.
    var EYE = '<svg class="pw-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z" /><circle cx="12" cy="12" r="3" /></svg>';
    var EYE_OFF = '<svg class="pw-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9.88 9.88a3 3 0 1 0 4.24 4.24" /><path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68" /><path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61" /><line x1="2" x2="22" y1="2" y2="22" /></svg>';

    function wire(input) {
        // Guard against double-wiring if this ever runs twice on one page.
        if (input.getAttribute('data-pw-wired') === '1') {
            return;
        }
        var parent = input.parentNode;
        if (!parent) {
            return;
        }
        input.setAttribute('data-pw-wired', '1');

        // Only the input is wrapped, so the button positions against it and
        // the sibling error <span> and its layout are untouched.
        var wrap = document.createElement('span');
        wrap.className = 'pw-wrap';
        parent.insertBefore(wrap, input);
        wrap.appendChild(input);

        // type="button" is load-bearing: these fields sit inside a <form>,
        // and a bare <button> would default to submit.
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'pw-toggle';
        btn.setAttribute('aria-label', 'Show password');
        btn.setAttribute('aria-pressed', 'false');
        btn.innerHTML = EYE;

        btn.addEventListener('click', function () {
            var wasVisible = input.type === 'text';
            // The caret is kept across the type swap, or it jumps to the end
            // mid-typing - the very moment people reach for the toggle.
            var start = input.selectionStart;
            var end = input.selectionEnd;

            input.type = wasVisible ? 'password' : 'text';
            btn.innerHTML = wasVisible ? EYE : EYE_OFF;
            btn.setAttribute('aria-label', wasVisible ? 'Show password' : 'Hide password');
            btn.setAttribute('aria-pressed', wasVisible ? 'false' : 'true');

            input.focus();
            try {
                input.setSelectionRange(start, end);
            } catch (e) {}
        });

        wrap.appendChild(btn);
    }

    function initAll() {
        var inputs = document.querySelectorAll('input[type="password"]');
        Array.prototype.forEach.call(inputs, wire);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initAll);
    } else {
        initAll();
    }
})();

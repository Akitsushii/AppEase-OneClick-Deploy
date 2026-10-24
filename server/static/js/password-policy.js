(function () {
    // Mirrors validate_password_strength() in app.py, in the same order.
    // If either side changes, change both.
    var MIN = 8;
    var MAX = 128;

    var RULES = [
        { id: 'length', label: '8 to 128 characters', test: function (v) { return v.length >= MIN && v.length <= MAX; } },
        { id: 'upper',  label: 'One uppercase letter', test: function (v) { return /[A-Z]/.test(v); } },
        { id: 'lower',  label: 'One lowercase letter', test: function (v) { return /[a-z]/.test(v); } },
        { id: 'digit',  label: 'One number',           test: function (v) { return /[0-9]/.test(v); } },
        { id: 'symbol', label: 'One symbol',           test: function (v) { return /[^A-Za-z0-9]/.test(v); } }
    ];

    // An error string, or null. The single client-side source for the
    // pages' validators; the server re-checks everything.
    window.passwordPolicyError = function (value) {
        var v = value || '';
        if (!v) {
            return 'Password is required.';
        }
        for (var i = 0; i < RULES.length; i++) {
            if (!RULES[i].test(v)) {
                return 'Password does not meet all requirements.';
            }
        }
        return null;
    };

    function render(list, value) {
        var items = list.querySelectorAll('[data-pw-rule]');
        for (var i = 0; i < items.length; i++) {
            var id = items[i].getAttribute('data-pw-rule');
            var rule = null;
            for (var r = 0; r < RULES.length; r++) {
                if (RULES[r].id === id) { rule = RULES[r]; }
            }
            if (!rule) { continue; }
            var ok = rule.test(value || '');
            if (ok) {
                items[i].classList.add('pw-rule-ok');
            } else {
                items[i].classList.remove('pw-rule-ok');
            }
            items[i].setAttribute('aria-checked', ok ? 'true' : 'false');
        }
    }

    function wire(input) {
        if (input.getAttribute('data-pw-policy-wired') === '1') {
            return;
        }
        input.setAttribute('data-pw-policy-wired', '1');

        // password-toggle.js runs first (both are deferred, in document order)
        // and may have wrapped this input in a .pw-wrap span. Anchor to the
        // wrapper when present so the checklist lands after the whole field,
        // not inside the positioned wrapper.
        var host = input.parentNode && input.parentNode.className === 'pw-wrap'
            ? input.parentNode
            : input;
        if (!host.parentNode) {
            return;
        }

        var list = document.createElement('ul');
        list.className = 'pw-rules';
        list.setAttribute('aria-label', 'Password requirements');
        for (var i = 0; i < RULES.length; i++) {
            var li = document.createElement('li');
            li.className = 'pw-rule';
            li.setAttribute('data-pw-rule', RULES[i].id);
            li.setAttribute('role', 'checkbox');
            li.setAttribute('aria-checked', 'false');
            li.textContent = RULES[i].label;
            list.appendChild(li);
        }
        host.parentNode.insertBefore(list, host.nextSibling);

        input.addEventListener('input', function () {
            render(list, input.value);
        });
        render(list, input.value);
    }

    function initAll() {
        var inputs = document.querySelectorAll('[data-password-policy]');
        Array.prototype.forEach.call(inputs, wire);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initAll);
    } else {
        initAll();
    }
})();

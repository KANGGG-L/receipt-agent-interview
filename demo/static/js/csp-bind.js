/* Strict CSP binder.
   Static pages store a JSON plan in data-csp-<event>.
   HTML strings assigned through innerHTML / outerHTML / insertAdjacentHTML
   are rewritten before the parser sees on* or style attributes.
   Plans call existing functions. They never eval. */
(function (root) {
    'use strict';

    var EVENTS = {
        click: 1, dblclick: 1, change: 1, input: 1, focus: 1, blur: 1,
        keydown: 1, keyup: 1, keypress: 1, submit: 1, mousedown: 1, mouseup: 1,
        mousemove: 1, mouseenter: 1, mouseleave: 1, contextmenu: 1, reset: 1
    };
    var DENY = { eval: 1, Function: 1, setTimeout: 1, setInterval: 1, execScript: 1 };
    var EL_METHODS = { select: 1, focus: 1, blur: 1, click: 1, showPicker: 1 };

    function ParseError(msg) { this.message = msg; }

    function Parser(src) {
        this.s = String(src || '').replace(/^\s+|\s+$/g, '');
        this.i = 0;
        this.n = this.s.length;
    }

    Parser.prototype.peek = function () { return this.i < this.n ? this.s.charAt(this.i) : ''; };
    Parser.prototype.skip = function () {
        while (this.i < this.n && ' \t\r\n'.indexOf(this.s.charAt(this.i)) >= 0) this.i += 1;
    };
    Parser.prototype.starts = function (word) {
        this.skip();
        if (this.s.substr(this.i, word.length) !== word) return false;
        var nxt = this.s.charAt(this.i + word.length);
        return !(nxt && /[A-Za-z0-9_$]/.test(nxt));
    };
    Parser.prototype.expect = function (word) {
        this.skip();
        if (!word.charAt(0) || !(/[A-Za-z_$]/.test(word.charAt(0)))) {
            if (this.s.substr(this.i, word.length) !== word) throw new ParseError('expected ' + word);
            this.i += word.length;
            return;
        }
        if (!this.starts(word)) throw new ParseError('expected ' + word);
        this.i += word.length;
    };
    Parser.prototype.parse = function () {
        var steps = this.seq(null);
        this.skip();
        if (this.i < this.n) throw new ParseError('trailing');
        if (!steps.length) throw new ParseError('empty');
        return steps;
    };
    Parser.prototype.seq = function (end) {
        var steps = [];
        while (true) {
            this.skip();
            if (this.i >= this.n) {
                if (end !== null) throw new ParseError('unclosed');
                break;
            }
            if (end !== null && this.peek() === end) break;
            steps = steps.concat(this.stmt());
            this.skip();
            if (this.peek() === ';') { this.i += 1; continue; }
            this.skip();
            if (end !== null && this.peek() === end) break;
            if (this.i >= this.n) break;
            throw new ParseError('expected semicolon');
        }
        return steps;
    };
    Parser.prototype.stmtOrBlock = function () {
        this.skip();
        if (this.peek() === '{') {
            this.i += 1;
            var steps = this.seq('}');
            this.expect('}');
            return steps;
        }
        return this.stmt();
    };
    Parser.prototype.stmt = function () {
        this.skip();
        if (this.starts('if')) return [this.ifStmt()];
        if (this.starts('try')) return [this.tryStmt()];
        if (this.starts('return')) {
            this.expect('return');
            this.expect('false');
            return [{ op: 'retfalse' }];
        }
        if (this.starts('document')) return [this.documentStmt()];
        if (this.starts('event')) return [this.eventStmt()];
        if (this.starts('this')) return [this.thisStmt()];
        var name = this.ident();
        this.expect('(');
        var args = this.args();
        this.expect(')');
        return [{ op: 'call', f: name, a: args }];
    };
    Parser.prototype.ifStmt = function () {
        this.expect('if');
        this.expect('(');
        var cond = this.cond();
        this.expect(')');
        var thenSteps = this.stmtOrBlock();
        this.skip();
        var elseSteps = [];
        if (this.starts('else')) {
            this.expect('else');
            elseSteps = this.stmtOrBlock();
        }
        if (cond.kind === 'guard') return { op: 'guard', f: cond.data, then: thenSteps, else: elseSteps };
        return { op: 'key', keys: cond.data, then: thenSteps, else: elseSteps };
    };
    Parser.prototype.cond = function () {
        this.skip();
        if (this.starts('typeof')) {
            this.expect('typeof');
            var name = this.ident();
            this.expect('===');
            var lit = this.string();
            if (lit !== 'function') throw new ParseError('typeof');
            return { kind: 'guard', data: name };
        }
        if (this.starts('event')) {
            var keys = [];
            while (true) {
                this.expect('event');
                this.expect('.');
                if (this.ident() !== 'key') throw new ParseError('event.key');
                this.expect('===');
                keys.push(this.string());
                this.skip();
                if (this.s.substr(this.i, 2) === '||') { this.i += 2; continue; }
                break;
            }
            return { kind: 'key', data: keys };
        }
        throw new ParseError('cond');
    };
    Parser.prototype.tryStmt = function () {
        this.expect('try');
        this.expect('{');
        var body = this.seq('}');
        this.expect('}');
        this.expect('catch');
        this.expect('(');
        this.ident();
        this.expect(')');
        this.expect('{');
        this.seq('}');
        this.expect('}');
        return { op: 'try', body: body };
    };
    Parser.prototype.documentStmt = function () {
        this.expect('document');
        this.expect('.');
        this.expect('getElementById');
        this.expect('(');
        var elId = this.string();
        this.expect(')');
        this.expect('.');
        var nxt = this.ident();
        if (nxt === 'classList') {
            this.expect('.');
            this.expect('toggle');
            this.expect('(');
            var cls = this.string();
            this.expect(')');
            return { op: 'toggleclass', id: elId, c: cls };
        }
        if (nxt !== 'click' && nxt !== 'showPicker') throw new ParseError('document method');
        this.expect('(');
        this.expect(')');
        return { op: 'byid', id: elId, m: nxt };
    };
    Parser.prototype.eventStmt = function () {
        this.expect('event');
        this.expect('.');
        var name = this.ident();
        this.expect('(');
        this.expect(')');
        if (name === 'preventDefault') return { op: 'prevent' };
        if (name === 'stopPropagation') return { op: 'stop' };
        throw new ParseError('event method');
    };
    Parser.prototype.thisStmt = function () {
        this.expect('this');
        this.expect('.');
        var name = this.ident();
        this.skip();
        if (this.peek() !== '(') throw new ParseError('this call');
        this.expect('(');
        var args = this.args();
        this.expect(')');
        return { op: 'elcall', m: name, a: args };
    };
    Parser.prototype.args = function () {
        var args = [];
        this.skip();
        if (this.peek() === ')') return args;
        while (true) {
            args.push(this.expr());
            this.skip();
            if (this.peek() === ',') { this.i += 1; continue; }
            break;
        }
        return args;
    };
    Parser.prototype.expr = function () {
        this.skip();
        var ch = this.peek();
        if (ch === "'" || ch === '"') return { t: 'lit', v: this.string() };
        if (ch >= '0' && ch <= '9' || (ch === '-' && /[0-9]/.test(this.s.charAt(this.i + 1)))) return { t: 'lit', v: this.number() };
        if (this.starts('true')) { this.expect('true'); return { t: 'lit', v: true }; }
        if (this.starts('false')) { this.expect('false'); return { t: 'lit', v: false }; }
        if (this.starts('null')) { this.expect('null'); return { t: 'lit', v: null }; }
        if (this.starts('this')) return this.thisExpr();
        if (this.starts('event')) return this.eventExpr();
        throw new ParseError('expr');
    };
    Parser.prototype.thisExpr = function () {
        this.expect('this');
        this.skip();
        if (this.peek() !== '.') return { t: 'el' };
        this.expect('.');
        var name = this.ident();
        this.skip();
        if (this.peek() === '(') {
            this.expect('(');
            if (name !== 'getAttribute') throw new ParseError('this call expr');
            var arg = this.string();
            this.expect(')');
            return { t: 'attr', n: arg };
        }
        return { t: 'prop', p: name };
    };
    Parser.prototype.eventExpr = function () {
        this.expect('event');
        var path = [];
        this.skip();
        while (this.peek() === '.') {
            this.expect('.');
            path.push(this.ident());
            this.skip();
            if (this.peek() === '(') throw new ParseError('event call expr');
        }
        if (!path.length) return { t: 'event' };
        return { t: 'epath', p: path };
    };
    Parser.prototype.ident = function () {
        this.skip();
        var start = this.i;
        if (!/[A-Za-z_$]/.test(this.peek())) throw new ParseError('ident');
        this.i += 1;
        while (this.i < this.n && /[A-Za-z0-9_$]/.test(this.s.charAt(this.i))) this.i += 1;
        return this.s.slice(start, this.i);
    };
    Parser.prototype.string = function () {
        this.skip();
        var q = this.peek();
        if (q !== "'" && q !== '"') throw new ParseError('string');
        this.i += 1;
        var out = '';
        while (this.i < this.n) {
            var c = this.s.charAt(this.i);
            if (c === '\\') {
                this.i += 1;
                var esc = this.s.charAt(this.i);
                var map = { n: '\n', t: '\t', r: '\r', '\\': '\\', "'": "'", '"': '"', '/': '/' };
                out += (map[esc] !== undefined ? map[esc] : esc);
                this.i += 1;
                continue;
            }
            if (c === q) { this.i += 1; return out; }
            out += c;
            this.i += 1;
        }
        throw new ParseError('unterminated');
    };
    Parser.prototype.number = function () {
        this.skip();
        var start = this.i;
        if (this.peek() === '-') this.i += 1;
        while (this.i < this.n && this.s.charAt(this.i) >= '0' && this.s.charAt(this.i) <= '9') this.i += 1;
        if (this.peek() === '.') {
            this.i += 1;
            while (this.i < this.n && this.s.charAt(this.i) >= '0' && this.s.charAt(this.i) <= '9') this.i += 1;
        }
        var raw = this.s.slice(start, this.i);
        return raw.indexOf('.') >= 0 ? parseFloat(raw) : parseInt(raw, 10);
    };

    function decodeHtml(s) {
        return String(s)
            .replace(/&quot;/g, '"')
            .replace(/&#34;/g, '"')
            .replace(/&#39;|&apos;/g, "'")
            .replace(/&lt;/g, '<')
            .replace(/&gt;/g, '>')
            .replace(/&amp;/g, '&');
    }

    function parseHandler(src) {
        return new Parser(decodeHtml(src)).parse();
    }

    function attrEscape(s) {
        return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
    }

    function argVal(spec, el, event) {
        if (!spec) return undefined;
        if (spec.t === 'lit') return spec.v;
        if (spec.t === 'el') return el;
        if (spec.t === 'prop') return el ? el[spec.p] : undefined;
        if (spec.t === 'attr') return el ? el.getAttribute(spec.n) : null;
        if (spec.t === 'event') return event;
        if (spec.t === 'epath') {
            var cur = event;
            var path = spec.p || [];
            for (var i = 0; i < path.length; i++) cur = cur ? cur[path[i]] : undefined;
            return cur;
        }
        return undefined;
    }

    function callFn(name, el, args) {
        if (!/^[A-Za-z_$][\w$]*$/.test(name) || DENY[name]) return undefined;
        var fn = root[name];
        if (typeof fn !== 'function') return undefined;
        return fn.apply(root, args);
    }

    function runSteps(steps, el, event) {
        for (var i = 0; i < steps.length; i++) {
            var s = steps[i];
            if (!s) continue;
            if (s.op === 'call') {
                var args = [];
                var list = s.a || [];
                for (var a = 0; a < list.length; a++) args.push(argVal(list[a], el, event));
                var ret = callFn(s.f, el, args);
                if (ret === false) return false;
            } else if (s.op === 'elcall') {
                if (!EL_METHODS[s.m] || !el || typeof el[s.m] !== 'function') continue;
                var eargs = [];
                var elist = s.a || [];
                for (var b = 0; b < elist.length; b++) eargs.push(argVal(elist[b], el, event));
                el[s.m].apply(el, eargs);
            } else if (s.op === 'prevent') {
                if (event && event.preventDefault) event.preventDefault();
            } else if (s.op === 'stop') {
                if (event && event.stopPropagation) event.stopPropagation();
            } else if (s.op === 'retfalse') {
                return false;
            } else if (s.op === 'byid') {
                var node = root.document && root.document.getElementById(s.id);
                if (node && (s.m === 'click' || s.m === 'showPicker') && typeof node[s.m] === 'function') {
                    try { node[s.m](); } catch (err) { /* showPicker can reject */ }
                }
            } else if (s.op === 'toggleclass') {
                var box = root.document && root.document.getElementById(s.id);
                if (box && box.classList) box.classList.toggle(s.c);
            } else if (s.op === 'guard') {
                var exists = typeof root[s.f] === 'function';
                var branched = runSteps(exists ? (s.then || []) : (s.else || []), el, event);
                if (branched === false) return false;
            } else if (s.op === 'key') {
                var key = event && event.key;
                var keys = s.keys || [];
                var hit = false;
                for (var k = 0; k < keys.length; k++) if (keys[k] === key) hit = true;
                var taken = runSteps(hit ? (s.then || []) : (s.else || []), el, event);
                if (taken === false) return false;
            } else if (s.op === 'try') {
                try {
                    var inner = runSteps(s.body || [], el, event);
                    if (inner === false) return false;
                } catch (ignore) { /* matches try/catch around showPicker */ }
            }
        }
        return undefined;
    }

    function readPlan(el, type) {
        if (!el || !el.getAttribute) return null;
        var raw = el.getAttribute('data-csp-' + type);
        if (!raw) return null;
        try { return JSON.parse(raw); } catch (e) { return null; }
    }

    function invoke(el, event, type) {
        var plan = readPlan(el, type);
        if (!plan) return;
        var ret = runSteps(plan, el, event);
        if (ret === false && event) {
            if (event.preventDefault) event.preventDefault();
            if (event.stopPropagation) event.stopPropagation();
        }
    }

    function chain(target, type) {
        var out = [];
        var node = target;
        while (node && node.nodeType === 1) {
            if (node.getAttribute && node.getAttribute('data-csp-' + type)) out.push(node);
            node = node.parentElement;
        }
        return out;
    }

    function onBubble(event) {
        if (!event) return;
        if (event.type === 'click' && event.target && event.target.closest) {
            var link = event.target.closest('[data-csp-void]');
            if (link && event.preventDefault) event.preventDefault();
        }
        var nodes = chain(event.target, event.type);
        for (var i = 0; i < nodes.length; i++) {
            if (event.cancelBubble) break;
            invoke(nodes[i], event, event.type);
        }
    }

    function onFocusLike(event) {
        var el = event && event.target;
        if (!el || !el.getAttribute || !el.getAttribute('data-csp-' + event.type)) return;
        invoke(el, event, event.type);
    }

    function pointerChain(target, type, related) {
        var out = [];
        var node = target && target.closest ? target.closest('[data-csp-' + type + ']') : null;
        while (node) {
            if (node.getAttribute && node.getAttribute('data-csp-' + type)) {
                if (!(related && (related === node || (node.contains && node.contains(related))))) out.push(node);
            }
            node = node.parentElement;
        }
        out.reverse();
        return out;
    }

    function onMouseOver(event) {
        var nodes = pointerChain(event.target, 'mouseenter', event.relatedTarget);
        for (var i = 0; i < nodes.length; i++) invoke(nodes[i], event, 'mouseenter');
    }

    function onMouseOut(event) {
        var nodes = pointerChain(event.target, 'mouseleave', event.relatedTarget);
        for (var i = 0; i < nodes.length; i++) invoke(nodes[i], event, 'mouseleave');
    }

    function applyStyles(rootEl) {
        if (!rootEl || !rootEl.querySelectorAll) return;
        var nodes = rootEl.querySelectorAll('[data-csp-style]');
        for (var i = 0; i < nodes.length; i++) {
            nodes[i].style.cssText = nodes[i].getAttribute('data-csp-style');
            nodes[i].removeAttribute('data-csp-style');
        }
        if (rootEl.getAttribute && rootEl.hasAttribute && rootEl.hasAttribute('data-csp-style')) {
            rootEl.style.cssText = rootEl.getAttribute('data-csp-style');
            rootEl.removeAttribute('data-csp-style');
        }
    }

    function rewriteAttr(name, value) {
        var lname = String(name || '').toLowerCase();
        if (lname === 'style') {
            return { kind: 'style', value: decodeHtml(value) };
        }
        if (lname.indexOf('on') === 0 && EVENTS[lname.slice(2)]) {
            try {
                return { kind: 'event', event: lname.slice(2), plan: parseHandler(value) };
            } catch (err) {
                if (root.__CSP_UNPARSED) root.__CSP_UNPARSED.push(String(value).slice(0, 180));
                return { kind: 'drop' };
            }
        }
        return null;
    }

    function rewriteHtml(html) {
        if (typeof html !== 'string' || html.length === 0) return html;
        if (!/[\s"']on[a-z]+\s*=|\sstyle\s*=|javascript:/i.test(html)) return html;
        var eventNames = 'click|dblclick|change|input|focus|blur|keydown|keyup|keypress|submit|mousedown|mouseup|mousemove|mouseenter|mouseleave|contextmenu|reset';
        var re = new RegExp('\\s(?:on(' + eventNames + ')|style)\\s*=\\s*([\'"])([\\s\\S]*?)\\2', 'gi');
        var out = html.replace(re, function (full, ev, q, val) {
            if (!ev) {
                var decoded = decodeHtml(val).replace(/^\s+|\s+$/g, '');
                if (!decoded) return '';
                return ' data-csp-style="' + attrEscape(decoded) + '"';
            }
            try {
                var plan = parseHandler(val);
                return ' data-csp-' + ev.toLowerCase() + '="' + attrEscape(JSON.stringify(plan)) + '"';
            } catch (err) {
                if (root.__CSP_UNPARSED) root.__CSP_UNPARSED.push(String(val).slice(0, 180));
                return '';
            }
        });
        out = out.replace(/\shref\s*=\s*(['"])javascript:void\(0\);?\1/gi, ' href="#" data-csp-void="1"');
        return out;
    }

    function install() {
        if (!root.document || root.__CSP_BIND_INSTALLED) return;
        root.__CSP_BIND_INSTALLED = true;
        root.__CSP_UNPARSED = [];
        var doc = root.document;
        ['click', 'dblclick', 'change', 'input', 'keydown', 'keyup', 'keypress', 'submit', 'mousedown', 'mouseup', 'mousemove', 'contextmenu', 'reset'].forEach(function (name) {
            doc.addEventListener(name, onBubble, false);
        });
        doc.addEventListener('focus', onFocusLike, true);
        doc.addEventListener('blur', onFocusLike, true);
        doc.addEventListener('mouseover', onMouseOver, false);
        doc.addEventListener('mouseout', onMouseOut, false);

        var inner = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
        if (inner && inner.set && inner.get) {
            Object.defineProperty(Element.prototype, 'innerHTML', {
                configurable: true,
                enumerable: inner.enumerable,
                get: function () { return inner.get.call(this); },
                set: function (v) {
                    var next = typeof v === 'string' ? rewriteHtml(v) : v;
                    inner.set.call(this, next);
                    if (typeof next === 'string') applyStyles(this);
                }
            });
        }
        var outer = Object.getOwnPropertyDescriptor(Element.prototype, 'outerHTML');
        if (outer && outer.set) {
            Object.defineProperty(Element.prototype, 'outerHTML', {
                configurable: true,
                enumerable: outer.enumerable,
                get: outer.get ? function () { return outer.get.call(this); } : undefined,
                set: function (v) {
                    var parent = this.parentNode;
                    var next = typeof v === 'string' ? rewriteHtml(v) : v;
                    outer.set.call(this, next);
                    if (parent && parent.querySelectorAll) applyStyles(parent.nodeType === 1 ? parent : doc.body || parent);
                }
            });
        }
        var insert = Element.prototype.insertAdjacentHTML;
        if (insert) {
            Element.prototype.insertAdjacentHTML = function (position, html) {
                var next = typeof html === 'string' ? rewriteHtml(html) : html;
                insert.call(this, position, next);
                var host = this.parentElement || this;
                applyStyles(host);
            };
        }
        var setAttr = Element.prototype.setAttribute;
        Element.prototype.setAttribute = function (name, value) {
            var found = rewriteAttr(name, value);
            if (!found) return setAttr.call(this, name, value);
            if (found.kind === 'style') {
                this.style.cssText = found.value;
                return;
            }
            if (found.kind === 'event') {
                setAttr.call(this, 'data-csp-' + found.event, JSON.stringify(found.plan));
                return;
            }
        };
    }

    var api = {
        parseHandler: parseHandler,
        rewriteHtml: rewriteHtml,
        runSteps: runSteps,
        applyStyles: applyStyles,
        install: install
    };
    root.CSPBind = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    if (root.document) install();
})(typeof window !== 'undefined' ? window : globalThis);

const test = require('node:test');
const assert = require('node:assert');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

function createMockDom() {
  const elementsById = new Map();
  const listeners = new Map();

  function makeTextNode(txt) {
    return {
      nodeType: 3,
      textContent: String(txt),
      parentElement: null,
      parentNode: null,
      isConnected: true
    };
  }

  function makeElement(tag, explicitId = '') {
    let currentClassName = '';
    const classes = new Set();

    function syncClassesFromString(val) {
      classes.clear();
      const parts = String(val || '').trim().split(/\s+/);
      for (const p of parts) {
        if (p) classes.add(p);
      }
    }

    const el = {
      nodeType: 1,
      tagName: tag.toUpperCase(),
      dataset: {},
      children: [],
      childNodes: [],
      attributes: {},
      hidden: false,
      disabled: false,
      checked: false,
      value: '',
      parentElement: null,
      parentNode: null,
      style: {},
      tabIndex: 0,
      isConnected: true,
      _listeners: {},

      get id() {
        return this.getAttribute('id') || '';
      },
      set id(val) {
        const old = this.getAttribute('id');
        if (old && elementsById.get(old) === this) {
          elementsById.delete(old);
        }
        if (val) {
          this.setAttribute('id', String(val));
          elementsById.set(String(val), this);
        } else {
          this.removeAttribute('id');
        }
      },

      get className() {
        return currentClassName;
      },
      set className(val) {
        currentClassName = String(val || '');
        syncClassesFromString(currentClassName);
      },

      classList: {
        add(...items) {
          for (const item of items) if (item) classes.add(item);
          currentClassName = Array.from(classes).join(' ');
        },
        remove(...items) {
          for (const item of items) classes.delete(item);
          currentClassName = Array.from(classes).join(' ');
        },
        toggle(item, force) {
          if (force === undefined) {
            if (classes.has(item)) classes.delete(item); else classes.add(item);
          } else if (force) {
            classes.add(item);
          } else {
            classes.delete(item);
          }
          currentClassName = Array.from(classes).join(' ');
          return classes.has(item);
        },
        contains(item) {
          return classes.has(item);
        }
      },

      get textContent() {
        let out = '';
        for (const child of this.childNodes) {
          out += child.textContent || '';
        }
        return out;
      },
      set textContent(val) {
        this.children = [];
        this.childNodes = [];
        if (val !== '' && val !== null && val !== undefined) {
          const t = makeTextNode(val);
          t.parentElement = this;
          t.parentNode = this;
          this.childNodes.push(t);
        }
      },

      get firstChild() {
        return this.childNodes[0] || null;
      },

      focus() {
        doc.activeElement = el;
      },

      getClientRects() {
        let curr = el;
        while (curr) {
          if (curr.hidden || curr.isConnected === false) return [];
          curr = curr.parentElement;
        }
        return [{ width: 10, height: 10 }];
      },

      getAttribute(k) {
        return Object.prototype.hasOwnProperty.call(this.attributes, k) ? this.attributes[k] : null;
      },
      setAttribute(k, v) {
        this.attributes[k] = String(v);
        if (k === 'id') {
          elementsById.set(String(v), this);
        }
      },
      removeAttribute(k) {
        if (k === 'id') {
          const old = this.attributes[k];
          if (old) elementsById.delete(old);
        }
        delete this.attributes[k];
      },

      addEventListener(evt, fn) {
        this._listeners[evt] = this._listeners[evt] || [];
        this._listeners[evt].push(fn);
      },
      dispatchEvent(evt) {
        evt.target = evt.target || el;
        const fns = this._listeners[evt.type] || [];
        for (const fn of fns) fn(evt);
      },
      click() {
        if (this.disabled) return;
        this.dispatchEvent({ type: 'click', target: el });
      },

      append(...nodes) {
        for (const n of nodes) {
          const child = (typeof n === 'string' || typeof n === 'number') ? makeTextNode(n) : n;
          if (child.nodeType === 1) this.children.push(child);
          this.childNodes.push(child);
          child.parentElement = this;
          child.parentNode = this;
        }
      },
      prepend(...nodes) {
        for (const n of [...nodes].reverse()) {
          const child = (typeof n === 'string' || typeof n === 'number') ? makeTextNode(n) : n;
          if (child.nodeType === 1) this.children.unshift(child);
          this.childNodes.unshift(child);
          child.parentElement = this;
          child.parentNode = this;
        }
      },
      replaceChildren(...nodes) {
        this.children = [];
        this.childNodes = [];
        this.append(...nodes);
      },
      insertBefore(newNode, refNode) {
        const child = (typeof newNode === 'string' || typeof newNode === 'number') ? makeTextNode(newNode) : newNode;
        const idxChildNodes = this.childNodes.indexOf(refNode);
        if (idxChildNodes === -1) {
          this.append(child);
        } else {
          this.childNodes.splice(idxChildNodes, 0, child);
          child.parentElement = this;
          child.parentNode = this;
          if (child.nodeType === 1) {
            const idxChildren = this.children.indexOf(refNode);
            if (idxChildren === -1) {
              this.children.push(child);
            } else {
              this.children.splice(idxChildren, 0, child);
            }
          }
        }
      },
      remove() {
        if (this.parentElement) {
          const cIdx = this.parentElement.children.indexOf(this);
          if (cIdx !== -1) this.parentElement.children.splice(cIdx, 1);
          const cnIdx = this.parentElement.childNodes.indexOf(this);
          if (cnIdx !== -1) this.parentElement.childNodes.splice(cnIdx, 1);
        }
        this.parentElement = null;
        this.parentNode = null;
        this.isConnected = false;
      },
      scrollIntoView() {},
      querySelector(sel) {
        return this.querySelectorAll(sel)[0] || null;
      },
      querySelectorAll(sel) {
        return queryAllDescendants(this, sel);
      },
      contains(target) {
        let curr = target;
        while (curr) {
          if (curr === this) return true;
          curr = curr.parentElement;
        }
        return false;
      }
    };

    if (explicitId) {
      el.id = explicitId;
    }
    return el;
  }

  function matchCompoundSelector(el, part) {
    if (!el || el.nodeType !== 1) return false;
    const regex = /([.#]?[a-zA-Z0-9_-]+|\[[^\]]+\])/g;
    const tokens = part.match(regex);
    if (!tokens || tokens.length === 0) return false;

    for (const token of tokens) {
      if (token.startsWith('.')) {
        if (!el.classList.contains(token.slice(1))) return false;
      } else if (token.startsWith('#')) {
        if (el.id !== token.slice(1)) return false;
      } else if (token.startsWith('[')) {
        const m = token.match(/^\[([a-zA-Z0-9_-]+)(?:=["']?([^"'\]]*)["']?)?\]$/);
        if (!m) return false;
        const attr = m[1];
        const val = m[2];
        if (attr.startsWith('data-')) {
          const key = attr.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
          if (val === undefined) {
            if (!(key in el.dataset)) return false;
          } else if (el.dataset[key] !== val) {
            return false;
          }
        } else {
          const attrVal = el.getAttribute(attr);
          if (val === undefined) {
            if (attrVal === null) return false;
          } else if (attrVal !== val) {
            return false;
          }
        }
      } else {
        if (el.tagName.toLowerCase() !== token.toLowerCase()) return false;
      }
    }
    return true;
  }

  function matchSelector(el, selectorString) {
    const subSelectors = selectorString.split(',').map(s => s.trim()).filter(Boolean);
    for (const single of subSelectors) {
      const parts = single.split(/\s+/);
      if (parts.length === 1) {
        if (matchCompoundSelector(el, parts[0])) return true;
      } else {
        const targetPart = parts[parts.length - 1];
        if (!matchCompoundSelector(el, targetPart)) continue;
        let currentParent = el.parentElement;
        let parentPartIdx = parts.length - 2;
        let matchedAncestor = false;
        while (currentParent && parentPartIdx >= 0) {
          if (matchCompoundSelector(currentParent, parts[parentPartIdx])) {
            parentPartIdx--;
            if (parentPartIdx < 0) {
              matchedAncestor = true;
              break;
            }
          }
          currentParent = currentParent.parentElement;
        }
        if (matchedAncestor) return true;
      }
    }
    return false;
  }

  function queryAllDescendants(root, sel) {
    const res = [];
    const walk = (node) => {
      for (const ch of node.children || []) {
        if (matchSelector(ch, sel)) res.push(ch);
        walk(ch);
      }
    };
    walk(root);
    return res;
  }

  const doc = {
    activeElement: null,
    body: makeElement('body'),
    getElementById(id) {
      return elementsById.get(String(id)) || null;
    },
    createElement(tag) {
      return makeElement(tag);
    },
    createTextNode(txt) {
      return makeTextNode(txt);
    },
    querySelectorAll(sel) {
      const res = [];
      if (matchSelector(doc.body, sel)) res.push(doc.body);
      res.push(...queryAllDescendants(doc.body, sel));
      return res;
    },
    querySelector(sel) {
      return doc.querySelectorAll(sel)[0] || null;
    },
    addEventListener(evt, fn) {
      listeners.set(evt, listeners.get(evt) || []);
      listeners.get(evt).push(fn);
    },
    dispatchEvent(evt) {
      for (const fn of listeners.get(evt.type) || []) fn(evt);
    }
  };

  return { doc, elementsById };
}

function setupContext(fetchHandler) {
  const { doc } = createMockDom();

  const layout = doc.createElement('div');
  layout.className = 'layout';
  const sidebar = doc.createElement('div');
  sidebar.className = 'sidebar';
  doc.body.append(layout, sidebar);

  const drawer = doc.createElement('div');
  drawer.id = 'drawer';
  drawer.hidden = true;
  const closeDetail = doc.createElement('button');
  closeDetail.id = 'close-detail';
  const detailTitle = doc.createElement('h2');
  detailTitle.id = 'detail-title';
  const detailBody = doc.createElement('div');
  detailBody.id = 'detail-body';
  drawer.append(closeDetail, detailTitle, detailBody);
  doc.body.append(drawer);

  const requiredIds = [
    'toast', 'error-banner', 'records', 'empty-state', 'history-total', 'table-caption',
    'services', 'control-banner', 'run-check', 'stop-app', 'mode', 'scenario',
    'project-select', 'run-label', 'job-banner', 'job-icon', 'job-message', 'job-caption',
    'job-detail', 'metric-records', 'metric-incidents', 'metric-blocked', 'nav-count',
    'updated-at', 'refresh', 'search', 'breadcrumb-view', 'page-title', 'page-subtitle',
    'main', 'launch-panel', 'metrics', 'projects-panel', 'content-grid', 'history-panel',
    'right-column', 'new-project', 'history-title', 'drawer-backdrop',
    'projects-error', 'project-count', 'projects-summary', 'projects-empty',
    'projects-list', 'project-add-panel', 'project-name', 'project-path',
    'cancel-add-project', 'project-search', 'project-form', 'add-project'
  ];
  for (const id of requiredIds) {
    if (!doc.getElementById(id)) {
      const el = doc.createElement('div');
      el.id = id;
      doc.body.append(el);
    }
  }

  const ctx = vm.createContext({
    document: doc,
    navigator: { clipboard: { writeText: async () => {} } },
    AbortSignal: { timeout: () => ({}) },
    setTimeout: (fn, delay) => setTimeout(fn, delay || 0),
    clearTimeout: (t) => clearTimeout(t),
    Intl,
    Date,
    String,
    Number,
    JSON,
    Array,
    Object,
    Set,
    Map,
    Promise,
    Error,
    encodeURIComponent,
    fetch: fetchHandler
  });

  const appPath = path.resolve(__dirname, '../../src/aegisops/dashboard/static/app.js');
  let appCode = fs.readFileSync(appPath, 'utf8');
  appCode = appCode.replace(/showView\(["']projects["']\);\s*refresh\(\);\s*$/, '');
  vm.runInContext(appCode, ctx);

  return { ctx, doc };
}

test('1. Chooser menu does not send POST and renders check options', async () => {
  const calls = [];
  const fetchMock = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET' });
    return { ok: true, json: async () => ({}) };
  };

  const { ctx, doc } = setupContext(fetchMock);
  const project = { id: 'p1', name: 'AlphaProject', path: '/alpha' };
  const origin = doc.createElement('button');

  ctx.chooseProjectCheck(project, origin);

  const postCalls = calls.filter(c => c.method === 'POST');
  assert.strictEqual(postCalls.length, 0, 'chooseProjectCheck must not execute any POST request');
  const drawer = doc.getElementById('drawer');
  assert.strictEqual(drawer.hidden, false, 'Drawer should be visible');
  assert.ok(drawer.querySelectorAll('.action-option').length >= 3, 'Should render action check options');
});

test('2. projectResults history tabs use GET-only and Arrow/Home/End manage selection & tabindex', async () => {
  const calls = [];
  const fetchMock = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET' });
    if (url.includes('/api/projects/p2')) return { ok: true, json: async () => ({ reports: [] }) };
    if (url.includes('/api/project-runs/p2')) return { ok: true, json: async () => ({ runs: [] }) };
    if (url.includes('/api/project-reviews/p2')) return { ok: true, json: async () => ({ reviews: [] }) };
    return { ok: true, json: async () => ({}) };
  };

  const { ctx, doc } = setupContext(fetchMock);
  const project = { id: 'p2', name: 'BetaProject', path: '/beta' };
  const origin = doc.createElement('button');

  await ctx.projectResults(project, origin, 'files');

  const tablist = doc.querySelector('[role="tablist"]');
  assert.ok(tablist, 'Tab list element should exist');
  const tabs = tablist.children;
  assert.strictEqual(tabs.length, 3);

  assert.strictEqual(tabs[0].getAttribute('aria-selected'), 'true');
  assert.strictEqual(tabs[0].tabIndex, 0);
  assert.strictEqual(tabs[1].getAttribute('aria-selected'), 'false');
  assert.strictEqual(tabs[1].tabIndex, -1);

  tabs[0].dispatchEvent({ type: 'keydown', key: 'ArrowRight', preventDefault() {} });
  await new Promise(r => setImmediate(r));

  assert.strictEqual(tabs[1].getAttribute('aria-selected'), 'true');
  assert.strictEqual(tabs[1].tabIndex, 0);
  assert.strictEqual(tabs[0].getAttribute('aria-selected'), 'false');
  assert.strictEqual(tabs[0].tabIndex, -1);

  tabs[1].dispatchEvent({ type: 'keydown', key: 'End', preventDefault() {} });
  await new Promise(r => setImmediate(r));
  assert.strictEqual(tabs[2].getAttribute('aria-selected'), 'true');
  assert.strictEqual(tabs[2].tabIndex, 0);

  tabs[2].dispatchEvent({ type: 'keydown', key: 'Home', preventDefault() {} });
  await new Promise(r => setImmediate(r));
  assert.strictEqual(tabs[0].getAttribute('aria-selected'), 'true');
  assert.strictEqual(tabs[0].tabIndex, 0);

  const nonGetCalls = calls.filter(c => c.method !== 'GET');
  assert.strictEqual(nonGetCalls.length, 0, 'projectResults tabs must only perform GET queries throughout navigation');
  assert.ok(calls.some(c => c.url.includes('/api/projects/p2')));
  assert.ok(calls.some(c => c.url.includes('/api/project-runs/p2')));
  assert.ok(calls.some(c => c.url.includes('/api/project-reviews/p2')));
});

test('3. Uncertain runner POST blocks consent, environment and start button permanently', async () => {
  let postCalls = 0;
  const fetchMock = async (url, opts = {}) => {
    if (opts.method === 'POST' && url.includes('/api/jobs')) {
      postCalls++;
      return {
        ok: false,
        status: 500,
        json: async () => ({ error: 'Connection timeout with runner engine' })
      };
    }
    return { ok: true, json: async () => ({}) };
  };

  const { ctx, doc } = setupContext(fetchMock);
  const project = { id: 'p3', name: 'GammaProject', path: '/gamma' };
  const origin = doc.createElement('button');

  await ctx.projectRuns(project, origin, 'python');

  const consent = doc.getElementById('run-consent-p3');
  const envSelect = doc.getElementById('runner-environment');
  const startBtn = doc.querySelector('.runner-start');

  assert.ok(consent && envSelect && startBtn, 'Controls must be present');
  consent.checked = true;
  consent.dispatchEvent({ type: 'change' });
  assert.strictEqual(startBtn.disabled, false, 'Start button should be enabled after consent');

  startBtn.click();
  await new Promise(r => setImmediate(r));

  assert.strictEqual(postCalls, 1, 'Only one initial POST attempt');
  assert.strictEqual(consent.disabled, true, 'Consent must be disabled after uncertain failure');
  assert.strictEqual(envSelect.disabled, true, 'Environment select must remain disabled');
  assert.strictEqual(startBtn.disabled, true, 'Start button must remain disabled');

  consent.dispatchEvent({ type: 'change' });
  envSelect.dispatchEvent({ type: 'change' });
  ctx.renderJob({ state: 'idle' });
  assert.strictEqual(startBtn.disabled, true, 'Start button must stay disabled even after external events');
});

test('4. Delayed staticCheck POST does not close or overwrite subsequent project drawer', async () => {
  let resolvePost;
  const postPromise = new Promise(resolve => { resolvePost = resolve; });
  const calls = [];

  const fetchMock = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET' });
    if (opts.method === 'POST' && url.includes('/api/jobs')) {
      await postPromise;
      return {
        ok: true,
        json: async () => ({
          job: {
            id: 'job-1',
            state: 'completed',
            mode: 'project',
            message: 'Static check done',
            report_id: 'rep-1'
          }
        })
      };
    }
    return { ok: true, json: async () => ({}) };
  };

  const { ctx, doc } = setupContext(fetchMock);
  const projectA = { id: 'proj-A', name: 'Project Alpha', path: '/alpha' };
  const projectB = { id: 'proj-B', name: 'Project Beta', path: '/beta' };
  const originA = doc.createElement('button');
  const originB = doc.createElement('button');

  ctx.staticCheck(projectA, originA);
  const runButton = doc.querySelector('.project-scan');
  assert.ok(runButton, 'Run static check button must exist');

  runButton.click();
  await new Promise(r => setImmediate(r));
  assert.strictEqual(calls.filter(c => c.method === 'POST').length, 1);

  ctx.chooseProjectCheck(projectB, originB);
  const expectedTitle = 'Как проверить «Project Beta»?';
  assert.strictEqual(doc.getElementById('detail-title').textContent, expectedTitle);

  resolvePost();
  await new Promise(r => setImmediate(r));

  const drawer = doc.getElementById('drawer');
  assert.strictEqual(drawer.hidden, false, 'Subsequent drawer should not be closed by delayed POST resolution');
  assert.strictEqual(doc.getElementById('detail-title').textContent, expectedTitle, 'Delayed POST must not overwrite subsequent drawer title');
});

test('5. Detached/inaccessible origin falls back to project controls instead of failing focus', async () => {
  const fetchMock = async () => ({ ok: true, json: async () => ({}) });
  const { ctx, doc } = setupContext(fetchMock);

  const originBtn = doc.createElement('button');
  originBtn.dataset.projectId = 'p5';
  originBtn.dataset.projectAction = 'check';
  originBtn.isConnected = true;

  const resultsBtn = doc.createElement('button');
  resultsBtn.dataset.projectId = 'p5';
  resultsBtn.dataset.projectAction = 'results';
  resultsBtn.isConnected = true;
  resultsBtn.disabled = false;
  doc.body.append(resultsBtn);

  ctx.openRunnerDrawer('Test Project Drawer', originBtn);

  originBtn.isConnected = false;
  ctx.closeDetail();

  assert.strictEqual(doc.activeElement, resultsBtn, 'Focus must fall back to the available project results control');
});

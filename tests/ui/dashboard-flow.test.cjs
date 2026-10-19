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
    'projects-audit-note', 'projects-loading-note', 'audit-warning', 'overview-error', 'projects-stale-note',
    'projects-list', 'project-add-panel', 'project-name', 'project-path',
    'cancel-add-project', 'project-search', 'project-form', 'add-project'
  ];
  const initialHiddenIds = new Set([
    'toast', 'error-banner', 'overview-error', 'audit-warning', 'launch-panel', 'job-banner',
    'job-detail', 'control-banner', 'metrics', 'project-add-panel', 'projects-error',
    'projects-audit-note', 'projects-loading-note', 'projects-stale-note', 'content-grid',
    'empty-state', 'drawer-backdrop', 'new-project', 'project-select', 'scenario'
  ]);
  for (const id of requiredIds) {
    if (!doc.getElementById(id)) {
      const el = doc.createElement('div');
      el.id = id;
      if (initialHiddenIds.has(id)) el.hidden = true;
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
    if (url.includes('/api/project-checks/p2')) return { ok: true, json: async () => ({ checks: [] }) };
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
  assert.strictEqual(tabs.length, 4);

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

  tabs[1].dispatchEvent({ type: 'keydown', key: 'ArrowRight', preventDefault() {} });
  await new Promise(r => setImmediate(r));
  assert.strictEqual(tabs[2].getAttribute('aria-selected'), 'true');
  assert.strictEqual(tabs[2].tabIndex, 0);
  assert.strictEqual(tabs[1].getAttribute('aria-selected'), 'false');
  assert.strictEqual(tabs[1].tabIndex, -1);

  tabs[2].dispatchEvent({ type: 'keydown', key: 'End', preventDefault() {} });
  await new Promise(r => setImmediate(r));
  assert.strictEqual(tabs[3].getAttribute('aria-selected'), 'true');
  assert.strictEqual(tabs[3].tabIndex, 0);

  tabs[3].dispatchEvent({ type: 'keydown', key: 'Home', preventDefault() {} });
  await new Promise(r => setImmediate(r));
  assert.strictEqual(tabs[0].getAttribute('aria-selected'), 'true');
  assert.strictEqual(tabs[0].tabIndex, 0);

  const nonGetCalls = calls.filter(c => c.method !== 'GET');
  assert.strictEqual(nonGetCalls.length, 0, 'projectResults tabs must only perform GET queries throughout navigation');
  assert.ok(calls.some(c => c.url.includes('/api/projects/p2')));
  assert.ok(calls.some(c => c.url.includes('/api/project-runs/p2')));
  assert.ok(calls.some(c => c.url.includes('/api/project-checks/p2')));
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

  vm.runInContext('if (typeof state !== "undefined") { state.registryReady = true; }', ctx);
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

  vm.runInContext('if (typeof state !== "undefined") { state.registryReady = true; }', ctx);
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

test('6. Before first deferred GET projects finishes: projects-empty is hidden, no false zero count, success empty reveals empty, failure keeps error without false empty', async () => {
  let resolveProjects;
  let rejectProjects;
  const projectsDeferred = new Promise((resolve, reject) => {
    resolveProjects = resolve;
    rejectProjects = reject;
  });

  const fetchMock = async (url) => {
    if (url.includes('/api/projects')) {
      return projectsDeferred;
    }
    return { ok: true, json: async () => ({ records: [], services: {}, job: null }) };
  };

  const { ctx, doc } = setupContext(fetchMock);
  const emptyEl = doc.getElementById('projects-empty');
  const summaryEl = doc.getElementById('projects-summary');

  const pendingLoad = ctx.loadProjects();
  assert.strictEqual(emptyEl.hidden, true, 'projects-empty must be hidden while initial projects GET is unresolved');
  assert.ok(!summaryEl.textContent.includes('Проектов: 0'), 'projects-summary must not claim 0 projects while unresolved');

  resolveProjects({ ok: true, json: async () => ({ projects: [] }) });
  await pendingLoad;

  assert.strictEqual(emptyEl.hidden, false, 'Confirmed empty list must reveal projects-empty');
  assert.strictEqual(doc.getElementById('projects-error').hidden, true, 'Error must remain hidden on successful empty load');

  let rejectSecond;
  const secondDeferred = new Promise((_, rej) => { rejectSecond = rej; });
  const failingFetch = async (url) => {
    if (url.includes('/api/projects')) return secondDeferred;
    return { ok: true, json: async () => ({ records: [], services: {}, job: null }) };
  };
  const { ctx: ctx2, doc: doc2 } = setupContext(failingFetch);
  const pendingFail = ctx2.loadProjects();
  rejectSecond(new Error('Network offline'));
  await pendingFail.catch(() => {});

  assert.strictEqual(doc2.getElementById('projects-error').hidden, false, 'Failure must display projects-error');
  assert.strictEqual(doc2.getElementById('projects-empty').hidden, true, 'projects-empty must not be shown on failure when no confirmed projects exist');
});

test('7. Refresh failure keeps existing project cards, marks stale, disables mutating actions across search/renderJob, keeps results enabled, 0 POST', async () => {
  const calls = [];
  let shouldFail = false;
  const initialProjects = [{ id: 'p-stale-1', name: 'Project Stale', path: '/local/stale' }];

  const fetchMock = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET' });
    if (url.includes('/api/projects')) {
      if (shouldFail) throw new Error('Registry unavailable');
      return { ok: true, json: async () => ({ projects: initialProjects }) };
    }
    if (url.includes('/api/overview')) {
      return { ok: true, json: async () => ({ records: [], services: {}, job: null, checked_at: Date.now() }) };
    }
    return { ok: true, json: async () => ({}) };
  };

  const { ctx, doc } = setupContext(fetchMock);
  await ctx.loadProjects();

  assert.strictEqual(doc.querySelectorAll('.project-card').length, 1, 'Initial card rendered');

  shouldFail = true;
  const refreshDeferred = ctx.loadProjects();
  const loadingNote = doc.getElementById('projects-loading-note');
  assert.strictEqual(loadingNote.hidden, false, 'Loading note must be shown immediately during deferred refresh');
  await refreshDeferred;

  assert.strictEqual(doc.querySelectorAll('.project-card').length, 1, 'Cards must remain rendered after failed refresh');
  const staleNote = doc.getElementById('projects-stale-note');
  assert.ok(staleNote && staleNote.hidden === false, 'projects-stale-note must be strictly visible');

  const chooseBtn = doc.querySelector('.project-scan');
  const removeBtn = doc.querySelector('.project-remove');
  const resultsBtn = doc.querySelector('.project-results');

  assert.strictEqual(chooseBtn.disabled, true, 'Choose check button must be disabled on unconfirmed/stale registry');
  assert.ok(removeBtn, 'Remove button should exist');
  assert.strictEqual(removeBtn.disabled, true, 'Remove button must be disabled on unconfirmed/stale registry');
  assert.strictEqual(resultsBtn.disabled, false, 'Results view button must remain enabled (GET-only)');

  ctx.renderJob(null);
  ctx.renderProjects();

  const chooseAfter = doc.querySelector('.project-scan');
  const removeAfter = doc.querySelector('.project-remove');
  const resultsAfter = doc.querySelector('.project-results');
  assert.strictEqual(chooseAfter.disabled, true, 'renderJob/renderProjects must not re-enable choose action while registry is stale');
  assert.strictEqual(removeAfter.disabled, true, 'renderJob/renderProjects must not re-enable remove button while registry is stale');
  assert.strictEqual(resultsAfter.disabled, false, 'renderJob/renderProjects must keep results button enabled');
  const postCalls = calls.filter(c => c.method === 'POST');
  assert.strictEqual(postCalls.length, 0, 'No mutating POST request must be performed');
});

test('8. registryReady=false in open Python runner blocks start despite consent/renderJob; registry recovery restores it by consent, uncertain failure permanently blocks', async () => {
  let runnerPostCount = 0;
  const fetchMock = async (url, opts = {}) => {
    if (opts.method === 'POST' && url.includes('/api/jobs')) {
      runnerPostCount++;
      return { ok: false, status: 500, json: async () => ({ error: 'Process timeout' }) };
    }
    return { ok: true, json: async () => ({ projects: [{ id: 'p8', name: 'ReadyGateProject', path: '/p8' }] }) };
  };

  const { ctx, doc } = setupContext(fetchMock);
  const project = { id: 'p8', name: 'ReadyGateProject', path: '/p8' };
  const origin = doc.createElement('button');

  vm.runInContext('if (typeof state !== "undefined") { state.registryReady = false; }', ctx);
  await ctx.projectRuns(project, origin, 'python');

  const consent = doc.getElementById('run-consent-p8');
  const startBtn = doc.querySelector('.runner-start');
  assert.ok(consent && startBtn, 'Consent and start button must exist');

  consent.checked = true;
  consent.dispatchEvent({ type: 'change' });
  ctx.renderJob({ state: 'idle' });
  assert.strictEqual(startBtn.disabled, true, 'Start button must stay disabled while registryReady is false');

  await ctx.loadProjects();
  ctx.renderJob({ state: 'idle' });
  assert.strictEqual(startBtn.disabled, false, 'Start button restored once real loadProjects succeeds and consent is checked');

  startBtn.click();
  await new Promise(r => setImmediate(r));
  assert.strictEqual(runnerPostCount, 1, 'Initial runner execution submitted');

  assert.strictEqual(startBtn.disabled, true, 'After uncertain failure, runner must be permanently blocked');
  vm.runInContext('if (typeof state !== "undefined") { state.registryReady = true; }', ctx);
  consent.dispatchEvent({ type: 'change' });
  ctx.renderJob({ state: 'idle' });
  assert.strictEqual(startBtn.disabled, true, 'Uncertain failure must never be re-enabled even when registry is ready');
});

test('9. data.database_error in refresh keeps projects-audit-note scoped, preserves operational error in error-banner, switches to audit-warning on history/system without clearing operation error', async () => {
  const fetchMock = async (url) => {
    if (url.includes('/api/overview')) {
      return {
        ok: true,
        json: async () => ({
          records: [],
          services: { control: 'healthy' },
          job: null,
          database_error: true,
          checked_at: Date.now()
        })
      };
    }
    if (url.includes('/api/projects')) {
      return { ok: true, json: async () => ({ projects: [{ id: 'p9', name: 'Proj9', path: '/p9' }] }) };
    }
    return { ok: true, json: async () => ({}) };
  };

  const { ctx, doc } = setupContext(fetchMock);
  const errorBanner = doc.getElementById('error-banner');
  const auditNote = doc.getElementById('projects-audit-note');
  const auditWarning = doc.getElementById('audit-warning');

  ctx.error('Critical operation failure: cannot delete project');
  assert.strictEqual(errorBanner.textContent, 'Critical operation failure: cannot delete project');
  assert.strictEqual(errorBanner.hidden, false);

  await ctx.refresh();

  assert.strictEqual(errorBanner.textContent, 'Critical operation failure: cannot delete project', 'error-banner must preserve operational error and not be overwritten by database_error');
  assert.strictEqual(auditNote.hidden, false, 'projects-audit-note must be visible on projects view');
  assert.strictEqual(auditWarning.hidden, true, 'audit-warning must not be shown on projects view');

  ctx.showView('history');
  assert.strictEqual(auditWarning.hidden, false, 'audit-warning must be visible on history view');
  assert.strictEqual(auditNote.hidden, true, 'projects-audit-note must be hidden on history view');
  assert.strictEqual(errorBanner.textContent, 'Critical operation failure: cannot delete project', 'Operation error must still persist after view transition');

  ctx.showView('projects');
  assert.strictEqual(errorBanner.hidden, false, 'Operation error must not be dismissed when switching back to projects');
  assert.strictEqual(auditNote.hidden, false, 'projects-audit-note must be shown again on projects view');
});

test('10. Generic overview auth failure shows overview-error without soft audit note; subsequent successful refresh clears only overview-error', async () => {
  let shouldFailOverview = true;
  const fetchMock = async (url) => {
    if (url.includes('/api/overview')) {
      if (shouldFailOverview) {
        return { ok: false, status: 401, json: async () => ({ error: 'Unauthorized session' }) };
      }
      return { ok: true, json: async () => ({ records: [], services: {}, job: null, checked_at: Date.now() }) };
    }
    if (url.includes('/api/projects')) {
      return { ok: true, json: async () => ({ projects: [] }) };
    }
    return { ok: true, json: async () => ({}) };
  };

  const { ctx, doc } = setupContext(fetchMock);
  const overviewError = doc.getElementById('overview-error');
  const auditNote = doc.getElementById('projects-audit-note');
  const errorBanner = doc.getElementById('error-banner');

  ctx.error('Active upload in progress error');

  await ctx.refresh();

  assert.strictEqual(overviewError.hidden, false, 'overview-error must be visible on generic auth failure');
  assert.ok(overviewError.textContent.includes('Unauthorized') || overviewError.textContent.length > 0);
  assert.strictEqual(auditNote.hidden, true, 'Auth failure must not be presented as a soft audit database_error note');
  assert.strictEqual(errorBanner.textContent, 'Active upload in progress error', 'Operational error banner must remain untouched');

  shouldFailOverview = false;
  await ctx.refresh();

  assert.strictEqual(overviewError.hidden, true, 'Subsequent successful refresh must clear overview-error');
  assert.strictEqual(errorBanner.textContent, 'Active upload in progress error', 'Operational error banner must still be preserved');
});

test('11. Overlapping loadProjects requests discard stale delayed response in favor of latest request guard', async () => {
  let resolveFirst;
  let resolveSecond;
  const firstPromise = new Promise(r => { resolveFirst = r; });
  const secondPromise = new Promise(r => { resolveSecond = r; });
  let requestCount = 0;

  const fetchMock = async (url) => {
    if (url.includes('/api/projects')) {
      requestCount++;
      if (requestCount === 1) return firstPromise;
      if (requestCount === 2) return secondPromise;
    }
    return { ok: true, json: async () => ({}) };
  };

  const { ctx, doc } = setupContext(fetchMock);

  const req1 = ctx.loadProjects();
  const req2 = ctx.loadProjects();

  const latestProjects = [{ id: 'new-p', name: 'New Project', path: '/new' }];
  resolveSecond({ ok: true, json: async () => ({ projects: latestProjects }) });
  await req2;

  assert.strictEqual(doc.querySelectorAll('.project-card').length, 1, 'Latest response rendered');
  const card = doc.querySelector('.project-card');
  const cardTitle = card ? card.querySelector('h3') : null;
  assert.ok(cardTitle, 'Project card heading should be present');
  assert.strictEqual(cardTitle.textContent, 'New Project');

  resolveFirst({ ok: true, json: async () => ({ projects: [{ id: 'old-p', name: 'Old Stale', path: '/old' }] }) });
  await req1;

  assert.strictEqual(doc.querySelectorAll('.project-card').length, 1, 'Old response must not overwrite list of projects');
  const cardAfter = doc.querySelector('.project-card');
  const cardTitleAfter = cardAfter ? cardAfter.querySelector('h3') : null;
  assert.strictEqual(cardTitleAfter.textContent, 'New Project', 'Latest list must remain active after delayed response arrives');
});


test('12. malformed GET body keeps existing cards, sets registryReady=false, displays error, and disables action controls', async () => {
  let getCallCount = 0;
  let postCallCount = 0;
  const initialProjects = [{ id: 'p9', name: 'KeepOldCardProject', path: '/p9' }];
  const fetchMock = async (url, opts = {}) => {
    const method = (opts.method || 'GET').toUpperCase();
    if (method === 'GET') {
      getCallCount++;
      if (getCallCount === 1) {
        return { ok: true, json: async () => ({ projects: initialProjects }) };
      }
      return { ok: true, json: async () => ({}) };
    }
    if (method === 'POST') {
      postCallCount++;
      return { ok: true, json: async () => ({}) };
    }
    return { ok: true, json: async () => ({}) };
  };

  const { ctx, doc } = setupContext(fetchMock);
  await ctx.loadProjects();
  assert.strictEqual(vm.runInContext('state.registryReady', ctx), true);
  const initialCard = doc.querySelector('.project-card');
  assert.ok(initialCard, 'Initial project card must exist');
  assert.strictEqual(initialCard.querySelector('h3').textContent, 'KeepOldCardProject');

  await ctx.loadProjects();

  assert.strictEqual(postCallCount, 0, 'No POST requests should be made');
  assert.strictEqual(vm.runInContext('state.registryReady', ctx), false, 'registryReady must be false');

  const card = doc.querySelector('.project-card');
  assert.ok(card, 'Old project card must be preserved');
  const heading = card.querySelector('h3');
  assert.ok(heading, 'Project card heading must exist');
  assert.strictEqual(heading.textContent, 'KeepOldCardProject', 'Old card name must be preserved');

  const errorEl = doc.querySelector('#projects-error');
  assert.ok(errorEl, 'Projects error element must exist');
  assert.strictEqual(errorEl.hidden, false, 'Error element must be visible');
  assert.match(errorEl.textContent, /некорректный список/i, 'Error message must mention invalid list');

  const chooseBtn = card.querySelector('.project-scan');
  assert.ok(chooseBtn, 'Choose button must exist');
  assert.strictEqual(chooseBtn.disabled, true, 'Choose button must be disabled');

  const removeBtn = card.querySelector('.project-remove');
  assert.ok(removeBtn, 'Remove button must exist');
  assert.strictEqual(removeBtn.disabled, true, 'Remove button must be disabled');

  const resultsBtn = card.querySelector('.project-results');
  assert.ok(resultsBtn, 'Results button must exist');
  assert.strictEqual(resultsBtn.disabled, false, 'Results button must not be disabled');
});


test('13. Full check language chooser and run without consent does not POST, consent change sends exact payload', async () => {
  const calls = [];
  const fetchMock = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET', body: opts.body ? JSON.parse(opts.body) : null });
    if (opts.method === 'POST') return { ok: true, json: async () => ({ job: { state: 'running', mode: 'project_full_check', message: 'Job started' } }) };
    return { ok: true, json: async () => ({}) };
  };
  const { ctx, doc } = setupContext(fetchMock);
  const project = { id: 'p_full_1', name: 'PyProj', path: '/py' };
  const origin = doc.createElement('button');

  ctx.chooseProjectCheck(project, origin);
  const options = doc.getElementById('detail-body').querySelectorAll('.action-option');
  options[0].click();
  assert.strictEqual(calls.filter(c => c.method === 'POST').length, 0);

  vm.runInContext('state.registryReady = true;', ctx);
  await ctx.projectRuns(project, origin, 'python', true);
  const runBtn = doc.querySelector('.runner-start');
  const consent = doc.getElementById(`run-consent-${project.id}`);
  assert.ok(runBtn && consent);
  assert.strictEqual(runBtn.disabled, true);

  runBtn.click();
  assert.strictEqual(calls.filter(c => c.method === 'POST').length, 0);

  doc.getElementById('runner-environment').value = 'minimal';
  consent.checked = true;
  consent.dispatchEvent({ type: 'change', target: consent });
  assert.strictEqual(runBtn.disabled, false);

  runBtn.click();
  await new Promise(r => setImmediate(r));
  const postCalls = calls.filter(c => c.method === 'POST');
  assert.strictEqual(postCalls.length, 1);
  assert.deepStrictEqual(postCalls[0].body, {
    mode: 'project_full_check',
    project_id: 'p_full_1',
    consent: 'project-full-check-v1',
    environment: 'minimal'
  });
});

test('14. Unknown POST error locks controls and prevents automatic repeated calls', async () => {
  let postCount = 0;
  const fetchMock = async (url, opts = {}) => {
    if (opts.method === 'POST') {
      postCount++;
      return { ok: false, status: 500, json: async () => ({ error: 'Runner engine failure' }) };
    }
    return { ok: true, json: async () => ({}) };
  };
  const { ctx, doc } = setupContext(fetchMock);
  const project = { id: 'p_err', name: 'ErrProj', path: '/err' };
  const origin = doc.createElement('button');
  vm.runInContext('state.registryReady = true;', ctx);

  await ctx.projectRuns(project, origin, 'python', true);
  const runBtn = doc.querySelector('.runner-start');
  const consent = doc.getElementById(`run-consent-${project.id}`);
  const envSelect = doc.getElementById('runner-environment');
  consent.checked = true;
  consent.dispatchEvent({ type: 'change', target: consent });
  runBtn.click();
  await new Promise(r => setImmediate(r));

  assert.strictEqual(postCount, 1);
  assert.strictEqual(runBtn.disabled, true);
  assert.strictEqual(consent.disabled, true);
  assert.strictEqual(envSelect.disabled, true);
  assert.ok(doc.getElementById('detail-body').textContent.includes('Запрос не повторяется'));
  runBtn.click();
  assert.strictEqual(postCount, 1);
});

test('15. projectResults checks tab uses GET-only comparison controls', async () => {
  const calls = [];
  const fetchMock = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET' });
    if (url.includes('/api/project-checks/p_chk')) {
      return {
        ok: true,
        json: async () => ({
          checks: [
            { claim: { id: 'chk_1', created_at: new Date().toISOString(), environment: 'minimal' }, result: { status: 'passed' } },
            { claim: { id: 'chk_2', created_at: new Date().toISOString(), environment: 'minimal' }, result: { status: 'issues' } }
          ]
        })
      };
    }
    return { ok: true, json: async () => ({}) };
  };
  const { ctx, doc } = setupContext(fetchMock);
  const project = { id: 'p_chk', name: 'ChkProj', path: '/chk' };
  const origin = doc.createElement('button');

  await ctx.projectResults(project, origin, 'checks');
  const nonGet = calls.filter(c => c.method !== 'GET');
  assert.strictEqual(nonGet.length, 0);
  assert.ok(calls.some(c => c.url.includes('/api/project-checks/p_chk')));
  assert.ok(doc.querySelector('.result-comparison'));
  const rows = doc.querySelectorAll('.result-row');
  assert.strictEqual(rows.length, 2);
});

test('16. openFullCheck renders wrapper issues overall, reports counts, back button, and ignores stale detail response', async () => {
  let resolveDelay;
  const delayPromise = new Promise(r => { resolveDelay = r; });
  const fetchMock = async (url) => {
    if (url.includes('/api/project-check/chk_slow')) {
      await delayPromise;
      return { ok: true, json: async () => ({ claim: { id: 'chk_slow', project_id: 'p_dtl', created_at: '2026-01-01T00:00:00Z' }, result: { status: 'passed' } }) };
    }
    if (url.includes('/api/project-check/chk_act')) {
      return {
        ok: true,
        json: async () => ({
          claim: { id: 'chk_act', project_id: 'p_dtl', created_at: '2026-01-01T00:00:00Z', environment: 'minimal' },
          result: { status: 'issues', static_report_id: 'rep_1' }
        })
      };
    }
    if (url.includes('/api/project-run/chk_act')) {
      return {
        ok: true,
        json: async () => ({
          result: {
            status: 'passed',
            reason: 'checks_completed',
            container_removed: true,
            snapshot: { files: 5, skipped: 1, sha256: 'abc123hash' }
          }
        })
      };
    }
    if (url.includes('/api/project-reports/rep_1')) {
      return { ok: true, json: async () => ({ files_checked: 2, findings: [{ rule: 'rule_1' }], partial: false }) };
    }
    return { ok: true, json: async () => ({}) };
  };

  const { ctx, doc } = setupContext(fetchMock);
  ctx.fixture = { id: 'p_dtl', name: 'DetailProj', path: '/dtl' };
  vm.runInContext('state.projects = [fixture];', ctx);
  const origin = doc.createElement('button');

  const slowCall = ctx.openFullCheck('chk_slow', origin);
  await ctx.openFullCheck('chk_act', origin);
  resolveDelay();
  await slowCall;

  const body = doc.getElementById('detail-body');
  assert.ok(body.textContent.includes('chk_act'));
  assert.ok(!body.textContent.includes('chk_slow'));
  assert.ok(doc.querySelector('.drawer-back'));

  const statusBadge = body.querySelector('.status');
  assert.ok(statusBadge);
  assert.strictEqual(statusBadge.textContent, 'Нужны исправления');

  const pairs = body.querySelectorAll('.detail-kv');
  let filesCheckedVal = null;
  let findingsVal = null;
  for (const p of pairs) {
    const dt = p.querySelector('dt');
    const dd = p.querySelector('dd');
    if (dt && dt.textContent.includes('Файлов проверено')) {
      filesCheckedVal = dd ? dd.textContent : null;
    }
    if (dt && dt.textContent.includes('Замечаний')) {
      findingsVal = dd ? dd.textContent : null;
    }
  }
  assert.strictEqual(filesCheckedVal, '2');
  assert.strictEqual(findingsVal, '1');
  assert.ok(body.textContent.includes('Контейнер удалён'));
});



async function openTestFullAi(h, id) {
  const originBtn = h.doc.createElement('button');
  h.doc.body.append(originBtn);
  await h.ctx.openFullCheck(id, originBtn);
  await new Promise(r => setImmediate(r));
}

function setupFullAiHarness(opts = {}) {
  const calls = [];
  const checkId = opts.checkId || 'chk-1';
  const aiStatus = opts.aiStatus || 'not_requested';
  const aiProfile = opts.aiProfile !== undefined ? opts.aiProfile : 'project-full-check-summary-v1';
  const aiModel = opts.aiModel || 'deepseek/deepseek-v4.1-flash';
  const aiResponse = opts.aiResponse !== undefined ? opts.aiResponse : {
    summary: 'Summary text <script>alert(1)</script>',
    actions: [{ priority: 'high', code: 'syntax', explanation: 'expl <script>', next_step: 'step <script>' }],
    limitations: 'String limitations notes'
  };
  const jobPostResponse = opts.jobPostResponse !== undefined ? opts.jobPostResponse : { job: { id: 'job-ai-1', state: 'running' } };

  const fetchHandler = async (url, fetchOpts = {}) => {
    const urlStr = String(url);
    const method = (fetchOpts.method || 'GET').toUpperCase();
    calls.push({ url: urlStr, method, opts: fetchOpts });

    if (method === 'POST') {
      if (urlStr === '/api/jobs') {
        if (typeof jobPostResponse === 'function') return jobPostResponse(fetchOpts);
        if (jobPostResponse && jobPostResponse.error) {
          return { ok: false, status: 400, json: async () => jobPostResponse };
        }
        return { ok: true, status: 200, json: async () => jobPostResponse };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    }

    if (urlStr.startsWith('/api/project-check/')) {
      const id = decodeURIComponent(urlStr.replace('/api/project-check/', ''));
      const hasResult = opts.hasResult !== undefined ? opts.hasResult : true;
      return {
        ok: true,
        status: 200,
        json: async () => ({
          claim: { id, project_id: 'p-1', created_at: '2026-03-30T10:00:00Z', environment: 'minimal' },
          result: hasResult ? { status: opts.checkStatus || 'issues' } : null
        })
      };
    }
    if (urlStr.startsWith('/api/project-run/')) {
      const id = decodeURIComponent(urlStr.replace('/api/project-run/', ''));
      return {
        ok: true,
        status: 200,
        json: async () => ({
          result: { status: 'passed', reason: 'checks_completed', checks: [], execution_scope: 'container', container_removed: true }
        })
      };
    }
    if (urlStr.startsWith('/api/project-check-ai/')) {
      const id = decodeURIComponent(urlStr.replace('/api/project-check-ai/', ''));
      if (opts.aiDelayed) {
        await new Promise(r => setTimeout(r, 50));
      }
      if (opts.aiGetThrows) throw new Error('AI network fail');
      const effectiveCheckId = opts.aiResponseCheckId !== undefined ? opts.aiResponseCheckId : id;
      return {
        ok: true,
        status: 200,
        json: async () => ({
          check_id: effectiveCheckId,
          profile: aiProfile,
          model: aiModel,
          payload: { profile: aiProfile, summary: {} },
          analysis: { status: aiStatus, response: aiResponse }
        })
      };
    }
    if (urlStr.startsWith('/api/project-reports/')) {
      return { ok: true, status: 200, json: async () => ({ files_checked: 5, findings: [], partial: false }) };
    }
    if (urlStr === '/api/projects') {
      return { ok: true, status: 200, json: async () => [] };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  };

  const { ctx, doc } = setupContext(fetchHandler);
  vm.runInContext('state.projects = [{ id: "p-1", name: "Proj 1" }];', ctx);
  return { ctx, doc, calls, checkId };
}

test('Test 17: open not_requested makes GET-only 0POST, preview button disabled before consent, checked exact POST and drawer closes', async () => {
  const harness = setupFullAiHarness({ aiStatus: 'not_requested' });
  const { doc, calls, checkId } = harness;
  await openTestFullAi(harness, checkId);

  const postCallsBefore = calls.filter(c => c.method === 'POST');
  assert.strictEqual(postCallsBefore.length, 0);

  const startBtn = doc.querySelector('.project-ai-start');
  assert.ok(startBtn, 'AI start button should exist');
  assert.strictEqual(startBtn.disabled, true);

  const consentCheckbox = doc.getElementById(`full-ai-consent-${checkId}`);
  assert.ok(consentCheckbox, 'consent checkbox should exist');
  consentCheckbox.checked = true;
  consentCheckbox.dispatchEvent({ type: 'change' });
  assert.strictEqual(startBtn.disabled, false);

  startBtn.click();
  await new Promise(r => setImmediate(r));

  const postCalls = calls.filter(c => c.method === 'POST' && c.url === '/api/jobs');
  assert.strictEqual(postCalls.length, 1);
  const parsedBody = JSON.parse(postCalls[0].opts.body);
  assert.deepStrictEqual(parsedBody, {
    mode: 'project_check_ai',
    check_id: checkId,
    consent: 'project-full-check-summary-v1'
  });
  assert.strictEqual(doc.getElementById('drawer').hidden, true);
});

test('Test 18: unknown POST error locks button + consent permanently; active job disables and restores', async () => {
  const harness = setupFullAiHarness({
    aiStatus: 'not_requested',
    jobPostResponse: { error: 'Payment rejected or unknown failure' }
  });
  const { doc, checkId } = harness;
  await openTestFullAi(harness, checkId);
  const startBtn = doc.querySelector('.project-ai-start');
  const consentCheckbox = doc.getElementById(`full-ai-consent-${checkId}`);

  consentCheckbox.checked = true;
  consentCheckbox.dispatchEvent({ type: 'change' });
  assert.strictEqual(startBtn.disabled, false);

  startBtn.click();
  await new Promise(r => setImmediate(r));

  assert.strictEqual(startBtn.disabled, true);
  assert.strictEqual(consentCheckbox.disabled, true);
  assert.strictEqual(startBtn.dataset.compatible, 'false');

  // Even if checkbox toggles, remains locked
  consentCheckbox.checked = true;
  consentCheckbox.dispatchEvent({ type: 'change' });
  assert.strictEqual(startBtn.disabled, true);

  // Active job test
  const harness2 = setupFullAiHarness({ aiStatus: 'not_requested' });
  await openTestFullAi(harness2, 'chk-2');
  const btn2 = harness2.doc.querySelector('.project-ai-start');
  const consent2 = harness2.doc.getElementById('full-ai-consent-chk-2');
  consent2.checked = true;

  vm.runInContext('state.job = { state: "running" };', harness2.ctx);
  consent2.dispatchEvent({ type: 'change' });
  assert.strictEqual(btn2.disabled, true);

  vm.runInContext('state.job = null;', harness2.ctx);
  consent2.dispatchEvent({ type: 'change' });
  assert.strictEqual(btn2.disabled, false);
});

test('Test 19: saved completed summary/action literal HTML-like text not created as DOM nodes; pending/failed have no button', async () => {
  const harness = setupFullAiHarness({
    aiStatus: 'completed',
    aiResponse: {
      summary: 'Safe summary <script id="bad1"></script>',
      actions: [{ priority: 'high', code: 'syntax', explanation: '<b id="bad2">syntax</b>', next_step: '<i id="bad3">fix</i>' }],
      limitations: 'Limited scope text <script id="bad4"></script>'
    }
  });
  const { doc, checkId } = harness;
  await openTestFullAi(harness, checkId);
  assert.strictEqual(doc.getElementById('bad1'), null);
  assert.strictEqual(doc.getElementById('bad2'), null);
  assert.strictEqual(doc.getElementById('bad3'), null);
  assert.strictEqual(doc.getElementById('bad4'), null);

  const summaryEl = doc.querySelector('.ai-summary');
  assert.ok(summaryEl.textContent.includes('<script id="bad1">'));
  const card = doc.querySelector('.ai-action');
  assert.ok(card.textContent.includes('Приоритет: Высокий'));
  assert.ok(card.textContent.includes('Ошибка синтаксиса или формата'));
  assert.ok(card.textContent.includes('Следующий шаг'));
  assert.strictEqual(doc.querySelector('.project-ai-start'), null);

  for (const st of ['pending', 'failed']) {
    const h = setupFullAiHarness({ aiStatus: st });
    await openTestFullAi(h, 'chk-pf');
    assert.strictEqual(h.doc.querySelector('.project-ai-start'), null);
    const postCalls = h.calls.filter(c => c.method === 'POST');
    assert.strictEqual(postCalls.length, 0);
  }
});

test('Test 20: pending fullreport result null skips AI endpoint; wrong profile/bad response fallback while sections remain', async () => {
  const hNull = setupFullAiHarness({ hasResult: false });
  await openTestFullAi(hNull, 'chk-null');
  const aiCalls = hNull.calls.filter(c => c.url.startsWith('/api/project-check-ai/'));
  assert.strictEqual(aiCalls.length, 0);

  const hBad = setupFullAiHarness({
    aiProfile: 'fictional-profile',
    checkStatus: 'issues'
  });
  await openTestFullAi(hBad, 'chk-bad');
  const body = hBad.doc.getElementById('detail-body');
  assert.ok(body.textContent.includes('Объяснение результатов недоступно. Отчёт файлов и тестов сохранён'));
  assert.ok(body.textContent.includes('Тесты и логи'));
});

test('Test 21: delayed old AI GET resolved after new drawer cannot append stale canary', async () => {
  const harness = setupFullAiHarness({
    aiStatus: 'completed',
    aiDelayed: true,
    aiResponse: {
      summary: 'STALE_CANARY_SUMMARY',
      actions: [],
      limitations: 'stale'
    }
  });
  const { ctx, doc, calls } = harness;
  const originBtn1 = doc.createElement('button');
  const originBtn2 = doc.createElement('button');
  doc.body.append(originBtn1, originBtn2);

  await ctx.openFullCheck('chk-old', originBtn1);
  ctx.openRunnerDrawer('Другая статическая проверка', originBtn2);
  await new Promise(r => setTimeout(r, 60));

  const aiCalls = calls.filter(c => c.url.startsWith('/api/project-check-ai/'));
  assert.strictEqual(aiCalls.length, 1);
  const body = doc.getElementById('detail-body');
  assert.strictEqual(body.textContent.includes('STALE_CANARY_SUMMARY'), false);
});


test('22. openFullComparison renders comparable comparison with regressed top section', async () => {
  const calls = [];
  const fixture = {
    project_id: 'p1',
    base: { id: 'b1', created_at: '2026-03-30T10:00:00Z', status: 'issues', profile: 'python-checks-v1', environment: 'minimal', rules_version: 'local-static-v2', image_id: 'sha256:' + 'a'.repeat(64) },
    target: { id: 't1', created_at: '2026-03-30T11:00:00Z', status: 'passed', profile: 'python-checks-v1', environment: 'minimal', rules_version: 'local-static-v2', image_id: 'sha256:' + 'a'.repeat(64) },
    comparable: true,
    reasons: [],
    same_snapshot: false,
    static: {
      added: [{ severity: 'error', file: '<b>app.py</b>', line: 12, location: 'main()', message: '<script>alert(1)</script>' }],
      removed: [{ severity: 'warning', file: 'util.py', line: 5, message: 'deprecated' }],
      unchanged: 3
    },
    runner: {
      transitions: [
        { tool: 'pytest', before: 'passed', after: 'failed', change: 'no_longer_passed' },
        { tool: 'ruff', before: 'failed', after: 'passed', change: 'now_passed' }
      ]
    }
  };
  const { ctx, doc } = setupContext(async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET' });
    return { ok: true, json: async () => fixture };
  });
  vm.runInContext('state.projects = [{ id: "p1", name: "P1", path: "/p1" }]', ctx);
  const origin = doc.createElement('button');
  await ctx.openFullComparison('b1', 't1', origin);

  assert.strictEqual(calls.filter(c => c.method !== 'GET').length, 0);
  const text = doc.getElementById('detail-body').textContent;
  assert.ok(!text.includes('исправлено'));
  assert.ok(text.includes('Сначала проверьте'));
  assert.ok(text.includes('Требует внимания'));
  assert.ok(text.includes('pytest'));
  assert.ok(text.includes('Новые замечания'));
  assert.ok(text.includes('Больше не отмечаются'));
  assert.ok(text.includes('Остались в отчёте'));
  const priorityIdx = text.indexOf('Сначала проверьте');
  const findingsIdx = text.indexOf('Новые замечания: 1');
  assert.ok(priorityIdx !== -1 && findingsIdx !== -1 && priorityIdx < findingsIdx);
  assert.strictEqual(doc.querySelectorAll('script').length, 0);
  assert.strictEqual(doc.querySelectorAll('b').length, 0);
  assert.ok(text.includes('<b>app.py</b>'));
  assert.ok(text.includes('<script>alert(1)</script>'));
});

test('23. openFullComparison renders incompatible and unfinished states without fake counts', async () => {
  const fixture = {
    project_id: 'p1',
    base: { id: 'b1', created_at: '2026-03-30T10:00:00Z', status: 'pending', profile: 'p-v1', environment: 'minimal', rules_version: 'r1' },
    target: { id: 't1', created_at: '2026-03-30T11:00:00Z', status: 'passed', profile: 'p-v2', environment: 'docker', rules_version: 'r2' },
    comparable: false,
    reasons: ['environment_changed', 'image_changed'],
    static: null,
    runner: null
  };
  const calls = [];
  const { ctx, doc } = setupContext(async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET' });
    return { ok: true, json: async () => fixture };
  });
  const origin = doc.createElement('button');
  await ctx.openFullComparison('b1', 't1', origin);

  assert.strictEqual(calls.filter(c => c.method !== 'GET').length, 0);
  assert.strictEqual(doc.querySelectorAll('.report-stat').length, 0);
  assert.strictEqual(doc.querySelectorAll('.finding-card').length, 0);
  const bodyText = doc.getElementById('detail-body').textContent;
  assert.ok(bodyText.includes('Условия проверки различаются'));
  assert.ok(bodyText.includes('Выбраны разные окружения.'));
  assert.ok(bodyText.includes('Образы проверок различаются.'));
  const buttons = doc.querySelectorAll('.comparison-controls button');
  assert.strictEqual(buttons.length, 2);
});

test('24. comparisonControls submits selected IDs via GET on button click', async () => {
  const calls = [];
  const { ctx, doc } = setupContext(async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET' });
    return { ok: true, json: async () => ({ project_id: 'p', base: { id: 'b', created_at: '2026-01-01T00:00:00Z' }, target: { id: 't', created_at: '2026-01-02T00:00:00Z' }, reasons: [] }) };
  });
  const origin = doc.createElement('button');
  const items = [
    { id: 't_id', time: '2026-03-30T12:00:00Z' },
    { id: 'b_id', time: '2026-03-30T11:00:00Z' }
  ];
  const controls = ctx.comparisonControls(items, 'check', origin);
  doc.getElementById('detail-body').append(controls);
  const compareBtn = controls.querySelector('button');
  const selects = controls.querySelectorAll('select');
  assert.strictEqual(selects[0].value, 'b_id');
  assert.strictEqual(selects[1].value, 't_id');

  compareBtn.click();
  await new Promise(r => setImmediate(r));
  assert.strictEqual(calls.filter(c => c.method !== 'GET').length, 0);
  assert.ok(calls.some(c => c.url.includes('/api/project-check-comparison/b_id/t_id')));
});

test('25. previousFullCheckControl selects nearest older entry including pending', async () => {
  const calls = [];
  const history = {
    checks: [
      { claim: { id: 'chk_new', created_at: '2026-03-30T04:00:00Z', project_id: 'p1' }, result: { status: 'passed' } },
      { claim: { id: 'chk_curr', created_at: '2026-03-30T03:00:00Z', project_id: 'p1' }, result: { status: 'passed' } },
      { claim: { id: 'chk_older_pending', created_at: '2026-03-30T02:00:00Z', project_id: 'p1' }, result: { status: 'pending' } },
      { claim: { id: 'chk_oldest', created_at: '2026-03-30T01:00:00Z', project_id: 'p1' }, result: { status: 'passed' } }
    ]
  };
  const { ctx, doc } = setupContext(async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET' });
    if (url.includes('/api/project-checks/p1')) return { ok: true, json: async () => history };
    return { ok: true, json: async () => ({ project_id: 'p1', base: { id: 'chk_older_pending', created_at: '2026-03-30T02:00:00Z' }, target: { id: 'chk_curr', created_at: '2026-03-30T03:00:00Z' }, reasons: [] }) };
  });
  const origin = doc.createElement('button');
  const seq = ctx.openRunnerDrawer('Тест предыдущей', origin);
  const claim = { id: 'chk_curr', created_at: '2026-03-30T03:00:00Z', project_id: 'p1' };
  const control = ctx.previousFullCheckControl(claim, origin, seq);
  doc.getElementById('detail-body').append(control);

  const btn = control.querySelector('button');
  btn.click();
  await new Promise(r => setImmediate(r));

  assert.strictEqual(calls.filter(c => c.method !== 'GET').length, 0);
  assert.ok(calls.some(c => c.url.includes('/api/project-check-comparison/chk_older_pending/chk_curr')));
});

test('26. previousFullCheckControl handles no older, malformed checks, and stale drawer responses', async () => {
  let resolveHistory;
  const historyPromise = new Promise(resolve => { resolveHistory = resolve; });
  const calls = [];
  let historyPayload = { checks: null };
  let deferHistory = false;

  const { ctx, doc } = setupContext(async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET' });
    if (url.includes('/api/project-checks/p1')) {
      if (deferHistory) {
        await historyPromise;
        return { ok: true, json: async () => ({ checks: [{ claim: { id: 'old1', created_at: '2026-03-30T01:00:00Z', project_id: 'p1' } }] }) };
      }
      return { ok: true, json: async () => historyPayload };
    }
    return { ok: true, json: async () => ({}) };
  });

  const origin = doc.createElement('button');
  let seq = ctx.openRunnerDrawer('Тест', origin);
  let claim = { id: 'chk_curr', created_at: '2026-03-30T03:00:00Z', project_id: 'p1' };
  let control = ctx.previousFullCheckControl(claim, origin, seq);
  doc.getElementById('detail-body').append(control);
  let callout = control.querySelector('.detail-callout');

  control.querySelector('button').click();
  await new Promise(r => setImmediate(r));
  assert.strictEqual(callout.hidden, false);
  assert.ok(callout.textContent.includes('Не удалось прочитать историю проверок.'));

  historyPayload = { checks: [] };
  control.querySelector('button').click();
  await new Promise(r => setImmediate(r));
  assert.strictEqual(callout.hidden, false);
  assert.ok(callout.textContent.includes('нет предыдущей проверки'));

  deferHistory = true;
  seq = ctx.openRunnerDrawer('Stale Check', origin);
  claim = { id: 'chk_stale', created_at: '2026-03-30T03:00:00Z', project_id: 'p1' };
  control = ctx.previousFullCheckControl(claim, origin, seq);
  doc.getElementById('detail-body').append(control);
  control.querySelector('button').click();
  await new Promise(r => setImmediate(r));

  ctx.openRunnerDrawer('New Drawer Opened', origin);
  resolveHistory();
  await new Promise(r => setImmediate(r));

  assert.strictEqual(calls.filter(c => c.url.includes('/api/project-checks/p1')).length, 3);
  assert.strictEqual(calls.filter(c => c.url.includes('/api/project-check-comparison/')).length, 0);
  assert.strictEqual(calls.filter(c => c.method !== 'GET').length, 0);
});


function setupCodeReviewHarness(options = {}) {
  const calls = [];
  const {
    projectId = 'proj-1',
    previewId = 'prev-1',
    catalog = { files: ['src/a.js', 'src/b.js', 'src/c.js', 'src/d.js', 'src/e.js', 'src/f.js'] },
    history = { reviews: [] },
    previewData = null,
    previewPostResponse = { preview_id: previewId },
    jobPostResponse = { job: { id: 'job-1', state: 'running' } }
  } = options;

  const fetchHandler = async (url, opts = {}) => {
    const parsedUrl = String(url);
    const method = (opts.method || 'GET').toUpperCase();
    calls.push({ url: parsedUrl, method, opts });

    if (parsedUrl.startsWith('/api/project-code-files/')) {
      return {
        ok: true,
        status: 200,
        json: async () => catalog
      };
    }
    if (parsedUrl.startsWith('/api/project-code-history/')) {
      return {
        ok: true,
        status: 200,
        json: async () => history
      };
    }
    if (parsedUrl === '/api/project-code-preview' && method === 'POST') {
      if (options.previewPostError) {
        return {
          ok: false,
          status: 400,
          json: async () => ({ error: options.previewPostError })
        };
      }
      return {
        ok: true,
        status: 200,
        json: async () => previewPostResponse
      };
    }
    if (parsedUrl.startsWith('/api/project-code/')) {
      const resp = previewData || {
        status: 'not_requested',
        preview: {
          id: previewId,
          project_id: projectId,
          created_at: '2025-01-01T00:00:00Z',
          profile: 'project-code-review-v1',
          files: [
            { id: 'f1', path: 'src/a.js', language: 'javascript', lines: 1, content: 'console.log(1);' }
          ]
        },
        wire_payload: {
          profile: 'project-code-review-v1',
          files: [{ id: 'f1', language: 'javascript', content: 'console.log(1);' }]
        }
      };
      return {
        ok: true,
        status: 200,
        json: async () => resp
      };
    }
    if (parsedUrl === '/api/jobs' && method === 'POST') {
      if (options.jobPostError) {
        return {
          ok: false,
          status: 500,
          json: async () => ({ error: options.jobPostError })
        };
      }
      return {
        ok: true,
        status: 200,
        json: async () => jobPostResponse
      };
    }
    if (parsedUrl === '/api/projects') {
      return {
        ok: true,
        status: 200,
        json: async () => ({ projects: [{ id: projectId, name: 'Test Project', path: '/test' }] })
      };
    }
    return {
      ok: true,
      status: 200,
      json: async () => ({})
    };
  };

  const { ctx, doc } = setupContext(fetchHandler);
  vm.runInContext(`
    state.projects = [{ id: '${projectId}', name: 'Test Project', path: '/test' }];
    state.registryReady = true;
    state.job = null;
  `, ctx);

  return { ctx, doc, calls, projectId, previewId };
}

test('Test 27: chooseCodeReview performs only GET catalog and history, never POST on open', async () => {
  const harness = setupCodeReviewHarness();
  const { ctx, calls, projectId } = harness;

  await vm.runInContext(`chooseCodeReview({ id: '${projectId}', name: 'Test Project' });`, ctx);

  const getCalls = calls.filter(c => c.method === 'GET');
  const postCalls = calls.filter(c => c.method === 'POST');

  assert.strictEqual(postCalls.length, 0, 'No POST calls should be made on open');
  assert.ok(getCalls.some(c => c.url.includes(`/api/project-code-files/${projectId}`)), 'Catalog GET call required');
  assert.ok(getCalls.some(c => c.url.includes(`/api/project-code-history/${projectId}`)), 'History GET call required');
});

test('Test 28: file selection capped at 5 and prepare preview triggers exact POST and GET preview without AI consent', async () => {
  const harness = setupCodeReviewHarness();
  const { ctx, doc, calls, projectId, previewId } = harness;

  await vm.runInContext(`chooseCodeReview({ id: '${projectId}', name: 'Test Project' });`, ctx);

  const detailBody = doc.getElementById('detail-body');
  const checkboxes = detailBody.querySelectorAll('input').filter(el => el.type === 'checkbox');
  assert.strictEqual(checkboxes.length, 6, 'Should display all 6 files from catalog');

  const prepareBtn = doc.querySelector('.primary-button');
  assert.ok(prepareBtn, 'Prepare button must exist');
  assert.strictEqual(prepareBtn.disabled, true, 'Prepare button disabled initially');

  for (let i = 0; i < 6; i++) {
    checkboxes[i].checked = true;
    checkboxes[i].dispatchEvent({ type: 'change' });
  }

  const checkedBoxes = Array.from(checkboxes).filter(b => b.checked);
  assert.strictEqual(checkedBoxes.length, 5, 'Selection must be capped at 5 files');
  assert.strictEqual(prepareBtn.disabled, false, 'Prepare button enabled when files selected');

  prepareBtn.click();
  await new Promise(r => setImmediate(r));

  const previewPost = calls.find(c => c.method === 'POST' && c.url === '/api/project-code-preview');
  assert.ok(previewPost, 'Must send POST to /api/project-code-preview');
  const postData = JSON.parse(previewPost.opts.body);
  assert.strictEqual(postData.project_id, projectId);
  assert.strictEqual(postData.files.length, 5);

  const previewGet = calls.find(c => c.method === 'GET' && c.url === `/api/project-code/${previewId}`);
  assert.ok(previewGet, 'Must fetch created preview without prior AI consent');

  const aiJobPost = calls.filter(c => c.method === 'POST' && c.url === '/api/jobs');
  assert.strictEqual(aiJobPost.length, 0, 'No AI job must be requested prior to consent');
});

test('Test 29: openCodeReview displays literal HTML-like content safely and formats raw wire_payload JSON', async () => {
  const harness = setupCodeReviewHarness({
    previewData: {
      status: 'not_requested',
      preview: {
        id: 'prev-xss',
        project_id: 'proj-1',
        created_at: '2025-01-01T00:00:00Z',
        profile: 'project-code-review-v1',
        files: [
          {
            id: 'f-xss',
            path: 'index.html',
            language: 'html',
            lines: 1,
            content: '<script>alert("pwn");</script>'
          }
        ]
      },
      wire_payload: {
        profile: 'project-code-review-v1',
        files: ['index.html'],
        raw_meta: { safe: true }
      }
    }
  });
  const { ctx, doc } = harness;

  await vm.runInContext(`openCodeReview('prev-xss');`, ctx);

  const scriptTags = doc.querySelectorAll('script');
  assert.strictEqual(scriptTags.length, 0, 'No executable script elements created');

  const preElements = doc.querySelectorAll('pre.code-preview');
  assert.ok(preElements.length >= 2, 'Should have pre elements for code and wire_payload');

  const codeText = preElements[0].textContent;
  assert.ok(codeText.includes('<script>alert("pwn");</script>'), 'Content preserved as literal text');

  const wireText = preElements[1].textContent;
  assert.ok(wireText.includes('"safe": true'), 'wire_payload rendered as formatted JSON string');
});

test('Test 30: consent must be checked before paid POST, and unknown error locks button and consent permanently', async () => {
  const harness = setupCodeReviewHarness({
    jobPostError: 'Unknown failure from payment gateway'
  });
  const { ctx, doc, calls, previewId } = harness;

  await vm.runInContext(`openCodeReview('${previewId}');`, ctx);

  const consentCheckbox = doc.getElementById(`code-ai-consent-${previewId}`);
  const startBtn = doc.querySelector('.project-ai-start');

  assert.ok(consentCheckbox, 'Consent checkbox must be present');
  assert.ok(startBtn, 'Start analysis button must be present');
  assert.strictEqual(startBtn.disabled, true, 'Start button disabled before consent');

  consentCheckbox.checked = true;
  consentCheckbox.dispatchEvent({ type: 'change' });
  assert.strictEqual(startBtn.disabled, false, 'Start button enabled when consent checked');

  startBtn.click();
  await new Promise(r => setImmediate(r));

  const jobPostCalls = calls.filter(c => c.method === 'POST' && c.url === '/api/jobs');
  assert.strictEqual(jobPostCalls.length, 1, 'Exact paid POST call sent');
  const sentPayload = JSON.parse(jobPostCalls[0].opts.body);
  assert.deepStrictEqual(sentPayload, {
    mode: 'project_code_ai',
    preview_id: previewId,
    consent: 'project-code-review-v1'
  });

  assert.strictEqual(startBtn.disabled, true, 'Start button locked after error');
  assert.strictEqual(consentCheckbox.disabled, true, 'Consent checkbox locked after error');
  assert.strictEqual(startBtn.dataset.compatible, 'false', 'Compatible flag set to false');

  consentCheckbox.checked = true;
  consentCheckbox.dispatchEvent({ type: 'change' });
  assert.strictEqual(startBtn.disabled, true, 'Button remains locked on consent toggle');

  vm.runInContext(`renderJob({ id: 'any-job', state: 'failed' });`, ctx);
  consentCheckbox.dispatchEvent({ type: 'change' });
  assert.strictEqual(startBtn.disabled, true, 'Button still locked even after renderJob');

  const jobPostCallsAfter = calls.filter(c => c.method === 'POST' && c.url === '/api/jobs');
  assert.strictEqual(jobPostCallsAfter.length, 1, 'No second POST attempted');
});

test('Test 31: completed, pending, and failed states show no paid button, display limitations, and empty findings caveat', async () => {
  const harnessCompleted = setupCodeReviewHarness({
    previewData: {
      status: 'completed',
      preview: {
        id: 'prev-comp',
        project_id: 'proj-1',
        created_at: '2025-01-01T00:00:00Z',
        profile: 'project-code-review-v1',
        files: [{ id: 'f1', path: 'src/a.js', content: 'code' }]
      },
      result: {
        response: {
          summary: 'Review completed without errors.',
          findings: [],
          limitations: 'Static analysis only.'
        }
      }
    }
  });
  await vm.runInContext(`openCodeReview('prev-comp');`, harnessCompleted.ctx);

  let startBtn = harnessCompleted.doc.querySelector('.project-ai-start');
  assert.strictEqual(startBtn, null, 'No paid button shown when status is completed');
  const textCompleted = harnessCompleted.doc.getElementById('detail-body').textContent;
  assert.ok(textCompleted.includes('Модель не вернула замечаний. Пустой список не доказывает отсутствие ошибок.'), 'Empty findings caveat shown');
  assert.ok(textCompleted.includes('Ограничения ответа: Static analysis only.'), 'Response limitations shown');

  const harnessPending = setupCodeReviewHarness({
    previewData: {
      status: 'pending',
      preview: {
        id: 'prev-pend',
        project_id: 'proj-1',
        created_at: '2025-01-01T00:00:00Z',
        profile: 'project-code-review-v1',
        files: [{ id: 'f1', path: 'src/a.js', content: 'code' }]
      }
    }
  });
  await vm.runInContext(`openCodeReview('prev-pend');`, harnessPending.ctx);
  assert.strictEqual(harnessPending.doc.querySelector('.project-ai-start'), null, 'No paid button when pending');
  assert.ok(harnessPending.doc.getElementById('detail-body').textContent.includes('Запрос уже отправлен.'), 'Pending warning displayed');

  const harnessFailed = setupCodeReviewHarness({
    previewData: {
      status: 'failed',
      preview: {
        id: 'prev-fail',
        project_id: 'proj-1',
        created_at: '2025-01-01T00:00:00Z',
        profile: 'project-code-review-v1',
        files: []
      }
    }
  });
  await vm.runInContext(`openCodeReview('prev-fail');`, harnessFailed.ctx);
  assert.strictEqual(harnessFailed.doc.querySelector('.project-ai-start'), null, 'No paid button when failed');
  assert.ok(harnessFailed.doc.getElementById('detail-body').textContent.includes('Результат не подтверждён. Повторного запроса нет'), 'Failed warning displayed');
});

test('Test 32: stale responses from old calls cannot overwrite or close newer drawer', async () => {
  let resolveFirst;
  const firstPromise = new Promise(resolve => { resolveFirst = resolve; });

  const harness = setupCodeReviewHarness();
  const { ctx, doc } = harness;

  const customFetch = async (url, opts) => {
    const parsedUrl = String(url);
    if (parsedUrl.includes('/api/project-code/prev-slow')) {
      await firstPromise;
      return {
        ok: true,
        status: 200,
        json: async () => ({
          status: 'not_requested',
          preview: {
            id: 'prev-slow',
            project_id: 'proj-1',
            created_at: '2025-01-01T00:00:00Z',
            profile: 'project-code-review-v1',
            files: [{ id: 'f1', path: 'src/slow.js', content: 'slow' }]
          }
        })
      };
    }
    if (parsedUrl.includes('/api/project-code/prev-fast')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          status: 'not_requested',
          preview: {
            id: 'prev-fast',
            project_id: 'proj-1',
            created_at: '2025-01-01T00:00:00Z',
            profile: 'project-code-review-v1',
            files: [{ id: 'f2', path: 'src/fast.js', content: 'fast' }]
          }
        })
      };
    }
    return {
      ok: true,
      status: 200,
      json: async () => ({})
    };
  };

  ctx.fetch = customFetch;

  const slowCall = vm.runInContext(`openCodeReview('prev-slow');`, ctx);
  await vm.runInContext(`openCodeReview('prev-fast');`, ctx);

  assert.ok(doc.getElementById('detail-body').textContent.includes('src/fast.js'), 'Faster newer preview displayed');

  resolveFirst();
  await slowCall;

  const currentBody = doc.getElementById('detail-body').textContent;
  assert.ok(currentBody.includes('src/fast.js'), 'Newer preview must not be overwritten');
  assert.strictEqual(currentBody.includes('src/slow.js'), false, 'Stale preview must not leak into current drawer');
});

test('Test 33: job-detail route preview-id opens openCodeReview', async () => {
  const harness = setupCodeReviewHarness();
  const { ctx, doc, previewId } = harness;

  vm.runInContext(`
    renderJob({
      id: 'job-review-1',
      mode: 'project_code_ai',
      state: 'completed',
      preview_id: '${previewId}'
    });
  `, ctx);

  const jobDetailBtn = doc.getElementById('job-detail');
  assert.ok(jobDetailBtn, 'Job detail button must exist');
  assert.strictEqual(jobDetailBtn.hidden, false, 'Job detail button must be visible for active job');

  jobDetailBtn.click();
  await new Promise(r => setImmediate(r));

  assert.strictEqual(doc.getElementById('drawer').hidden, false, 'Drawer must be open');
  const drawerTitle = doc.getElementById('detail-title').textContent;
  assert.ok(drawerTitle.includes('Разбор кода'), 'Job detail navigated to code review drawer');
});
test('Test 34: delayed local preview POST response cannot overwrite or close newer drawer', async () => {
  let resolvePreviewPost;
  const previewPostPromise = new Promise(resolve => { resolvePreviewPost = resolve; });

  const harness = setupCodeReviewHarness();
  const { ctx, doc, projectId } = harness;

  const customFetch = async (url, opts = {}) => {
    const parsedUrl = String(url);
    const method = (opts.method || 'GET').toUpperCase();
    if (parsedUrl === '/api/project-code-preview' && method === 'POST') {
      await previewPostPromise;
      return {
        ok: true,
        status: 200,
        json: async () => ({ preview_id: 'prev-slow-post' })
      };
    }
    if (parsedUrl.startsWith('/api/project-code-files/')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ files: ['src/slow.js'] })
      };
    }
    if (parsedUrl.startsWith('/api/project-code-history/')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ reviews: [] })
      };
    }
    if (parsedUrl.includes('/api/project-code/prev-newer')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          status: 'not_requested',
          preview: {
            id: 'prev-newer',
            project_id: projectId,
            created_at: '2025-01-01T00:00:00Z',
            profile: 'project-code-review-v1',
            files: [{ id: 'f2', path: 'src/newer.js', content: 'newer content' }]
          }
        })
      };
    }
    if (parsedUrl.includes('/api/project-code/prev-slow-post')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          status: 'not_requested',
          preview: {
            id: 'prev-slow-post',
            project_id: projectId,
            created_at: '2025-01-01T00:00:00Z',
            profile: 'project-code-review-v1',
            files: [{ id: 'f1', path: 'src/slow.js', content: 'slow content' }]
          }
        })
      };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  };

  ctx.fetch = customFetch;

  await vm.runInContext(`chooseCodeReview({ id: '${projectId}', name: 'Test Project' });`, ctx);
  const detailBody = doc.getElementById('detail-body');
  const checkboxes = detailBody.querySelectorAll('input').filter(el => el.type === 'checkbox');
  checkboxes[0].checked = true;
  checkboxes[0].dispatchEvent({ type: 'change' });

  const prepareBtn = doc.querySelector('.primary-button');
  prepareBtn.click();

  await vm.runInContext(`openCodeReview('prev-newer');`, ctx);
  assert.ok(doc.getElementById('detail-body').textContent.includes('src/newer.js'), 'Newer drawer opened');

  resolvePreviewPost();
  await new Promise(r => setImmediate(r));
  await new Promise(r => setImmediate(r));

  const bodyText = doc.getElementById('detail-body').textContent;
  assert.ok(bodyText.includes('src/newer.js'), 'Newer preview content retained');
  assert.strictEqual(bodyText.includes('src/slow.js'), false, 'Stale preview POST must not overwrite newer drawer');
});

test('Test 35: delayed paid POST response cannot overwrite or close newer drawer', async () => {
  let resolveJobPost;
  const jobPostPromise = new Promise(resolve => { resolveJobPost = resolve; });

  const harness = setupCodeReviewHarness();
  const { ctx, doc, previewId, projectId } = harness;

  const customFetch = async (url, opts = {}) => {
    const parsedUrl = String(url);
    const method = (opts.method || 'GET').toUpperCase();
    if (parsedUrl === '/api/jobs' && method === 'POST') {
      await jobPostPromise;
      return {
        ok: true,
        status: 200,
        json: async () => ({ job: { id: 'job-delayed', state: 'running' } })
      };
    }
    if (parsedUrl.includes('/api/project-code/prev-newer')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          status: 'not_requested',
          preview: {
            id: 'prev-newer',
            project_id: projectId,
            created_at: '2025-01-01T00:00:00Z',
            profile: 'project-code-review-v1',
            files: [{ id: 'f-newer', path: 'src/newer.js', content: 'newer file' }]
          }
        })
      };
    }
    if (parsedUrl.includes(`/api/project-code/${previewId}`)) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          status: 'not_requested',
          preview: {
            id: previewId,
            project_id: projectId,
            created_at: '2025-01-01T00:00:00Z',
            profile: 'project-code-review-v1',
            files: [{ id: 'f1', path: 'src/a.js', content: 'code' }]
          }
        })
      };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  };

  ctx.fetch = customFetch;

  await vm.runInContext(`openCodeReview('${previewId}');`, ctx);
  const consentCheckbox = doc.getElementById(`code-ai-consent-${previewId}`);
  const startBtn = doc.querySelector('.project-ai-start');
  consentCheckbox.checked = true;
  consentCheckbox.dispatchEvent({ type: 'change' });
  startBtn.click();

  await vm.runInContext(`openCodeReview('prev-newer');`, ctx);
  assert.ok(doc.getElementById('detail-body').textContent.includes('src/newer.js'), 'Newer preview drawer opened');

  resolveJobPost();
  await new Promise(r => setImmediate(r));
  await new Promise(r => setImmediate(r));

  const bodyText = doc.getElementById('detail-body').textContent;
  assert.ok(bodyText.includes('src/newer.js'), 'Newer drawer kept intact after delayed job POST');
  assert.strictEqual(doc.getElementById('drawer').hidden, false, 'Drawer remains open');
});

test('openCodeMap uses GET only and safely renders file paths', async () => {
  const calls = [];
  const { ctx, doc } = setupContext(async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET' });
    return { ok: true, json: async () => ({ files: [{ path: '<script>alert(1)</script>', symbols: ['fn1'], imports: ['mod1'] }], parsed_count: 1 }) };
  });
  vm.runInContext('state.registryReady = true;', ctx);
  await vm.runInContext('openCodeMap({ id: "p1", name: "Proj 1" }, null)', ctx);
  await new Promise(setImmediate);
  assert.strictEqual(calls.length, 1);
  assert.strictEqual(calls[0].method, 'GET');
  assert.strictEqual(calls[0].url, '/api/project-code-map/p1');
  const body = doc.getElementById('detail-body');
  assert.ok(body.textContent.includes('<script>alert(1)</script>'));
});

function createValidV2Catalog() {
  const langs = ['python', 'javascript', 'typescript'];
  const kinds = ['bug', 'clean', 'injection'];
  const cases = [];
  for (let i = 0; i < 9; i++) {
    const lang = langs[i % 3];
    const kind = kinds[Math.floor(i / 3)];
    const expected = kind === 'bug'
      ? [{ file_id: 'f1', start_line: 1, end_line: 2, categories: ['correctness'] }]
      : [];
    cases.push({
      id: `case_${i + 1}`,
      language: lang,
      kind: kind,
      files: [{
        id: 'f1',
        path: `src/module_${i + 1}.${lang === 'python' ? 'py' : lang === 'javascript' ? 'js' : 'ts'}`,
        language: lang,
        content: 'line 1\nline 2\nline 3\n'
      }],
      expected: expected
    });
  }
  return {
    scope: 'synthetic-file-overlap-v2',
    max_requests: 9,
    max_output_tokens: 25200,
    cases: cases,
    history: []
  };
}

test('openCodeEvaluation requires explicit consent before single jobs POST', async () => {
  const calls = [];
  const { ctx, doc } = setupContext(async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET', body: opts.body ? JSON.parse(opts.body) : null });
    if (url === '/api/project-code-evaluation-v2') return { ok: true, json: async () => createValidV2Catalog() };
    return { ok: true, json: async () => ({ job: { id: 'j1', state: 'queued' } }) };
  });
  vm.runInContext('state.registryReady = true;', ctx);
  await vm.runInContext('openCodeEvaluation(null)', ctx);
  await new Promise(setImmediate);
  assert.strictEqual(calls.length, 1);
  assert.strictEqual(calls[0].url, '/api/project-code-evaluation-v2');
  const btn = doc.querySelector('button.project-ai-start');
  assert.ok(btn && btn.disabled);
  const checkbox = doc.querySelectorAll('input').find(i => i.id === 'code-eval-consent');
  assert.ok(checkbox);
  checkbox.checked = true;
  checkbox.dispatchEvent({ type: 'change' });
  assert.strictEqual(btn.disabled, false);
  btn.click();
  await new Promise(setImmediate);
  assert.strictEqual(calls.length, 2);
  assert.deepStrictEqual(calls[1], {
    url: '/api/jobs',
    method: 'POST',
    body: { mode: 'project_code_evaluation', consent: 'project-code-evaluation-v2' }
  });
});

test('openCodeLab displays preview and locks on POST failure preventing second POST', async () => {
  const calls = [];
  const { ctx, doc } = setupContext(async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET', body: opts.body ? JSON.parse(opts.body) : null });
    if (url === '/api/project-code-lab/prev-1') {
      return { ok: true, json: async () => ({
        preview: { project_id: 'p1' },
        review: { findings: [{ title: 'Finding 1' }] },
        lab: { status: 'not_requested' },
        payloads: [{ finding_index: 0, profile: 'project-code-fix-v1' }]
      }) };
    }
    throw new Error('network down');
  });
  vm.runInContext('state.registryReady = true;', ctx);
  await vm.runInContext('openCodeLab("prev-1", null)', ctx);
  await new Promise(setImmediate);
  const body = doc.getElementById('detail-body');
  assert.ok(body.textContent.includes('Finding 1'));
  const btn = doc.querySelector('button.project-ai-start');
  const checkbox = doc.querySelectorAll('input').find(i => i.id === 'code-fix-consent-prev-1');
  checkbox.checked = true;
  checkbox.dispatchEvent({ type: 'change' });
  btn.click();
  await new Promise(setImmediate);
  assert.strictEqual(calls.length, 2);
  assert.deepStrictEqual(calls[1], { url: '/api/jobs', method: 'POST', body: { mode: 'project_code_fix', preview_id: 'prev-1', finding_index: 0, consent: 'project-code-fix-v1' } });
  assert.strictEqual(btn.disabled, true);
  btn.click();
  await new Promise(setImmediate);
  assert.strictEqual(calls.length, 2);
});

test('openAssistantSettings performs single GET, renders selection, command and safe labels without input or POST', async () => {
  const calls = [];
  const fixtureData = {
    selection: { provider: 'openai', model: 'gpt-5.4-mini' },
    configured: true,
    legacy: false,
    providers: [
      { provider: 'openai', label: 'OpenAI Cloud <main>', model: 'gpt-5.4-mini' },
      { provider: 'anthropic', label: 'Anthropic Claude', model: 'claude-3-opus' },
      { provider: 'local', label: 'Ollama Local', model: 'llama3' },
      { provider: 'custom', label: 'Custom Endpoint', model: 'custom-model' }
    ],
    setup_command: 'Connect-Neuroassistant.cmd',
    direct_budget_note: 'limits'
  };
  const { ctx, doc } = setupContext(async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET' });
    return { ok: true, json: async () => fixtureData };
  });
  await vm.runInContext('openAssistantSettings(null)', ctx);
  await new Promise(setImmediate);
  assert.strictEqual(calls.length, 1);
  assert.strictEqual(calls[0].method, 'GET');
  assert.strictEqual(calls[0].url, '/api/assistant');
  const postCalls = calls.filter(c => c.method === 'POST');
  assert.strictEqual(postCalls.length, 0);
  const body = doc.getElementById('detail-body');
  assert.ok(body.textContent.includes('gpt-5.4-mini'));
  assert.ok(body.textContent.includes('Connect-Neuroassistant.cmd'));
  assert.ok(body.textContent.includes('OpenAI Cloud <main>'));
  assert.ok(body.textContent.includes('limits'));
  const inputs = body.querySelectorAll('input, textarea, select');
  assert.strictEqual(inputs.length, 0);
});

test('openAssistantSettings discarded when closeDrawer called before response resolves', async () => {
  let resolveFetch;
  const fetchPromise = new Promise(resolve => { resolveFetch = resolve; });
  const fixtureData = {
    selection: { provider: 'openai', model: 'gpt-5.4-mini' },
    configured: true,
    legacy: false,
    providers: [],
    setup_command: 'Connect-Neuroassistant.cmd'
  };
  const { ctx, doc } = setupContext(async () => {
    await fetchPromise;
    return { ok: true, json: async () => fixtureData };
  });
  const originBtn = doc.createElement('button');
  doc.body.append(originBtn);
  ctx.settingsOrigin = originBtn;
  const pending = vm.runInContext('openAssistantSettings(settingsOrigin)', ctx);
  vm.runInContext('closeDetail()', ctx);
  const drawer = doc.getElementById('drawer');
  assert.strictEqual(drawer.hidden, true);
  resolveFetch();
  await pending;
  await new Promise(setImmediate);
  assert.strictEqual(drawer.hidden, true);
  const body = doc.getElementById('detail-body');
  assert.strictEqual(body.textContent.includes('gpt-5.4-mini'), false);
});


test('completed code review and lab expose download export links with GET-only requests', async () => {
  const calls = [];
  const preview = {
    id: 'p1',
    project_id: 'proj1',
    created_at: 1700000000,
    files: [{ id: 'f1', path: 'index.js', language: 'javascript', lines: 10, content: 'console.log(1);' }]
  };
  const { ctx, doc } = setupContext(async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET' });
    if (url === '/api/project-code/p1') {
      return {
        ok: true,
        json: async () => ({
          status: 'completed',
          preview,
          result: {
            response: {
              summary: 'Summary review',
              findings: [{
                file_id: 'f1',
                title: 'Bug',
                severity: 'high',
                category: 'security',
                evidence: 'bad call',
                explanation: 'leak',
                recommendation: 'fix it',
                start_line: 1,
                end_line: 2
              }]
            }
          }
        })
      };
    }
    if (url === '/api/project-code-lab/p1') {
      return {
        ok: true,
        json: async () => ({
          preview,
          review: { findings: [{ title: 'Bug' }] },
          lab: {
            status: 'completed',
            diff: '--- a\n+++ b',
            result: {
              proposal: {
                file_id: 'f1',
                explanation: 'Fixed proposal',
                test_code: 'test("ok", () => {});'
              }
            }
          },
          payloads: [{}]
        })
      };
    }
    return { ok: true, json: async () => ({}) };
  });

  await vm.runInContext('openCodeReview("p1", null)', ctx);
  await new Promise(setImmediate);

  const reviewExport = doc.querySelectorAll('a').find(a => a.textContent === 'Скачать отчёт');
  assert.ok(reviewExport, 'Review export link rendered');
  assert.strictEqual(reviewExport.href, '/api/project-code-export/p1');
  assert.strictEqual(reviewExport.download, 'aegis-review.md');

  await vm.runInContext('openCodeLab("p1", null)', ctx);
  await new Promise(setImmediate);

  const labExport = doc.querySelectorAll('a').find(a => a.textContent === 'Скачать исправление и тест');
  assert.ok(labExport, 'Fix export link rendered');
  assert.strictEqual(labExport.href, '/api/project-fix-export/p1');
  assert.strictEqual(labExport.download, 'aegis-fix.zip');

  const diffExport = doc.querySelectorAll('a').find(a => a.textContent === 'Скачать diff');
  assert.ok(diffExport, 'Existing diff export link still present');
  assert.strictEqual(diffExport.download, 'aegis-proposal.patch');

  assert.ok(calls.every(c => c.method === 'GET'), 'Only GET requests were dispatched');
  assert.strictEqual(calls.some(c => c.method === 'POST'), false);
});

test('non-completed statuses in code review and lab do not render export links', async () => {
  const nonCompletedCases = [
    { status: 'pending' },
    { status: 'failed' },
    { status: 'not_requested' },
    { status: 'unknown' }
  ];

  for (const item of nonCompletedCases) {
    const calls = [];
    const preview = {
      id: 'p_nc',
      project_id: 'proj1',
      profile: 'project-code-review-v1',
      created_at: 1700000000,
      files: [{ id: 'f1', path: 'index.js', language: 'javascript', lines: 10, content: 'x=1;' }]
    };
    const { ctx, doc } = setupContext(async (url, opts = {}) => {
      calls.push({ url, method: opts.method || 'GET' });
      if (url === '/api/project-code/p_nc') {
        return {
          ok: true,
          json: async () => ({ status: item.status, preview })
        };
      }
      if (url === '/api/project-code-lab/p_nc') {
        return {
          ok: true,
          json: async () => ({
            preview,
            review: { findings: [{ title: 'Finding' }] },
            lab: { status: item.status },
            payloads: [{}]
          })
        };
      }
      return { ok: true, json: async () => ({}) };
    });

    await vm.runInContext('openCodeReview("p_nc", null)', ctx);
    await new Promise(setImmediate);
    assert.strictEqual(doc.querySelectorAll('a').find(a => a.textContent === 'Скачать отчёт'), undefined);

    await vm.runInContext('openCodeLab("p_nc", null)', ctx);
    await new Promise(setImmediate);
    assert.strictEqual(doc.querySelectorAll('a').find(a => a.textContent === 'Скачать исправление и тест'), undefined);
  }
});

test('stale late GET response does not append old export link after drawer changes', async () => {
  let resolveLateReview;
  const lateReviewPromise = new Promise(resolve => { resolveLateReview = resolve; });
  const preview1 = {
    id: 'p_late',
    project_id: 'proj1',
    created_at: 1700000000,
    files: [{ id: 'f1', path: 'a.js', language: 'javascript', lines: 5, content: 'a' }]
  };
  const preview2 = {
    id: 'p_fast',
    project_id: 'proj1',
    created_at: 1700000000,
    files: [{ id: 'f2', path: 'b.js', language: 'javascript', lines: 5, content: 'b' }]
  };

  const { ctx, doc } = setupContext(async (url) => {
    if (url === '/api/project-code/p_late') {
      await lateReviewPromise;
      return {
        ok: true,
        json: async () => ({
          status: 'completed',
          preview: preview1,
          result: { response: { summary: 'Late summary', findings: [] } }
        })
      };
    }
    if (url === '/api/project-code/p_fast') {
      return {
        ok: true,
        json: async () => ({
          status: 'failed',
          preview: preview2
        })
      };
    }
    return { ok: true, json: async () => ({}) };
  });

  const firstOpen = vm.runInContext('openCodeReview("p_late", null)', ctx);
  await vm.runInContext('openCodeReview("p_fast", null)', ctx);
  await new Promise(setImmediate);

  resolveLateReview();
  await firstOpen;
  await new Promise(setImmediate);

  const staleExportLink = doc.querySelectorAll('a').find(a => a.textContent === 'Скачать отчёт');
  assert.strictEqual(staleExportLink, undefined, 'Stale delayed response must not inject export link');
});

test('code review start files recommendation plan flow', async () => {
  const calls = [];
  const mockFetch = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET' });
    if (url === '/api/project-code-files/p1') return { ok: true, json: async () => ({ files: ['main.py', 'util.py', 'test_main.py', 'pyproject.toml'] }) };
    if (url === '/api/project-code-history/p1') return { ok: true, json: async () => ({ reviews: [] }) };
    if (url === '/api/project-code-recommendation/p1') {
      return {
        ok: true,
        json: async () => ({
          profile: 'project-code-selection-v1',
          files: [
            { path: 'main.py', bytes: 10, sha256: 'a'.repeat(64), reason: 'Entry <script>literal</script>' },
            { path: 'pyproject.toml', bytes: 20, sha256: 'b'.repeat(64), reason: 'Config' }
          ],
          total_bytes: 30,
          max_files: 5,
          max_total_bytes: 24576,
          partial: false,
          limitations: ['heuristic']
        })
      };
    }
    return { ok: false, status: 404 };
  };

  const { ctx, doc } = setupContext(mockFetch);
  vm.runInContext('state.registryReady=true', ctx);
  await vm.runInContext('chooseCodeReview({id:"p1",name:"demo"}, null)', ctx);
  await new Promise(setImmediate);
  await new Promise(setImmediate);

  assert.strictEqual(calls.filter(c => c.method === 'GET').length, 2);
  assert.strictEqual(calls.some(c => c.url.includes('/api/project-code-recommendation/')), false);

  const buttons = Array.from(doc.querySelectorAll('button'));
  const recBtn = buttons.find(b => b.textContent === 'Подобрать стартовые файлы');
  const prepBtn = buttons.find(b => b.textContent === 'Подготовить предпросмотр');
  assert.ok(recBtn && !recBtn.disabled);
  assert.ok(prepBtn && prepBtn.disabled);

  recBtn.click();
  await new Promise(setImmediate);
  await new Promise(setImmediate);

  assert.strictEqual(calls.filter(c => c.url.includes('/api/project-code-recommendation/')).length, 1);
  const checkboxes = Array.from(doc.querySelectorAll('input')).filter(i => i.type === 'checkbox');
  assert.ok(checkboxes.every(b => !b.checked));
  assert.strictEqual(doc.querySelectorAll('script').length, 0);
  assert.ok(doc.body.textContent.includes('Entry <script>literal</script>'));

  const applyBtn = Array.from(doc.querySelectorAll('button')).find(b => b.textContent === 'Выбрать эти файлы');
  assert.ok(applyBtn);
  applyBtn.click();
  await new Promise(setImmediate);
  await new Promise(setImmediate);

  assert.strictEqual(checkboxes[0].checked, true);
  assert.strictEqual(checkboxes[1].checked, false);
  assert.strictEqual(checkboxes[2].checked, false);
  assert.strictEqual(checkboxes[3].checked, true);
  assert.ok(doc.body.textContent.includes('Выбрано файлов: 2 из 5'));
  assert.strictEqual(prepBtn.disabled, false);
  assert.strictEqual(calls.some(c => c.method === 'POST'), false);
});

test('delayed recommendation disables controls and restores without preview', async () => {
  let resolveRec;
  const mockFetch = async (url) => {
    if (url === '/api/project-code-files/p1') return { ok: true, json: async () => ({ files: ['main.py', 'util.py', 'test_main.py', 'pyproject.toml'] }) };
    if (url === '/api/project-code-history/p1') return { ok: true, json: async () => ({ reviews: [] }) };
    if (url === '/api/project-code-recommendation/p1') {
      return new Promise(res => {
        resolveRec = () => res({
          ok: true,
          json: async () => ({
            profile: 'project-code-selection-v1',
            files: [{ path: 'main.py', bytes: 10, sha256: 'a'.repeat(64), reason: 'main' }],
            total_bytes: 10,
            max_files: 5,
            max_total_bytes: 24576,
            partial: false,
            limitations: ['heuristic']
          })
        });
      });
    }
    return { ok: false, status: 404 };
  };

  const { ctx, doc } = setupContext(mockFetch);
  vm.runInContext('state.registryReady=true', ctx);
  await vm.runInContext('chooseCodeReview({id:"p1",name:"demo"}, null)', ctx);
  await new Promise(setImmediate);
  await new Promise(setImmediate);

  const recBtn = Array.from(doc.querySelectorAll('button')).find(b => b.textContent === 'Подобрать стартовые файлы');
  const checkboxes = Array.from(doc.querySelectorAll('input')).filter(i => i.type === 'checkbox');
  recBtn.click();
  await new Promise(setImmediate);

  assert.ok(recBtn.disabled);
  assert.ok(checkboxes.every(b => b.disabled));

  resolveRec();
  await new Promise(setImmediate);
  await new Promise(setImmediate);

  assert.strictEqual(recBtn.disabled, false);
  assert.ok(checkboxes.every(b => !b.disabled));
  assert.ok(checkboxes.every(b => !b.checked));
});

test('malformed recommendation payload rejects and preserves existing selection', async () => {
  const mockFetch = async (url) => {
    if (url === '/api/project-code-files/p1') return { ok: true, json: async () => ({ files: ['main.py', 'util.py', 'test_main.py', 'pyproject.toml'] }) };
    if (url === '/api/project-code-history/p1') return { ok: true, json: async () => ({ reviews: [] }) };
    if (url === '/api/project-code-recommendation/p1') {
      return {
        ok: true,
        json: async () => ({
          profile: 'project-code-selection-v1',
          files: [{ path: 'unknown.py', bytes: 10, sha256: 'a'.repeat(64), reason: 'x' }],
          total_bytes: 10,
          max_files: 5,
          max_total_bytes: 24576,
          partial: false
        })
      };
    }
    return { ok: false, status: 404 };
  };

  const { ctx, doc } = setupContext(mockFetch);
  vm.runInContext('state.registryReady=true', ctx);
  await vm.runInContext('chooseCodeReview({id:"p1",name:"demo"}, null)', ctx);
  await new Promise(setImmediate);
  await new Promise(setImmediate);

  const checkboxes = Array.from(doc.querySelectorAll('input')).filter(i => i.type === 'checkbox');
  checkboxes[0].checked = true;
  checkboxes[0].dispatchEvent({ type: 'change' });
  await new Promise(setImmediate);
  assert.ok(checkboxes[0].checked);

  const recBtn = Array.from(doc.querySelectorAll('button')).find(b => b.textContent === 'Подобрать стартовые файлы');
  recBtn.click();
  await new Promise(setImmediate);
  await new Promise(setImmediate);

  assert.ok(doc.body.textContent.includes('Подбор стартовых файлов недоступен. Код никуда не отправлялся.'));
  assert.strictEqual(Array.from(doc.querySelectorAll('button')).some(b => b.textContent === 'Выбрать эти файлы'), false);
  assert.ok(checkboxes[0].checked);
});

test('stale recommendation discarded on view switch and guarded when not ready', async () => {
  let resolveRec;
  const mockFetch = async (url) => {
    if (url === '/api/project-code-files/p1') return { ok: true, json: async () => ({ files: ['main.py', 'util.py'] }) };
    if (url === '/api/project-code-history/p1') return { ok: true, json: async () => ({ reviews: [] }) };
    if (url === '/api/project-code-map/p1') return { ok: true, json: async () => ({ files: [], partial: false, limitations: [] }) };
    if (url === '/api/project-code-recommendation/p1') {
      return new Promise(res => {
        resolveRec = () => res({
          ok: true,
          json: async () => ({
            profile: 'project-code-selection-v1',
            files: [{ path: 'main.py', bytes: 10, sha256: 'a'.repeat(64), reason: 'ok' }],
            total_bytes: 10,
            max_files: 5,
            max_total_bytes: 24576,
            partial: false
          })
        });
      });
    }
    return { ok: false, status: 404 };
  };

  const { ctx, doc } = setupContext(mockFetch);
  vm.runInContext('state.registryReady=false', ctx);
  await vm.runInContext('chooseCodeReview({id:"p1",name:"demo"}, null)', ctx);
  await new Promise(setImmediate);
  await new Promise(setImmediate);

  let recBtn = Array.from(doc.querySelectorAll('button')).find(b => b.textContent === 'Подобрать стартовые файлы');
  assert.strictEqual(recBtn.disabled, true);

  vm.runInContext('state.registryReady=true; state.job={state:"running"}', ctx);
  await vm.runInContext('chooseCodeReview({id:"p1",name:"demo"}, null)', ctx);
  await new Promise(setImmediate);
  await new Promise(setImmediate);

  recBtn = Array.from(doc.querySelectorAll('button')).find(b => b.textContent === 'Подобрать стартовые файлы');
  assert.strictEqual(recBtn.disabled, true);

  vm.runInContext('state.job=null', ctx);
  await vm.runInContext('chooseCodeReview({id:"p1",name:"demo"}, null)', ctx);
  await new Promise(setImmediate);
  await new Promise(setImmediate);

  recBtn = Array.from(doc.querySelectorAll('button')).find(b => b.textContent === 'Подобрать стартовые файлы');
  recBtn.click();
  await new Promise(setImmediate);

  await vm.runInContext('openCodeMap({id:"p1",name:"demo"}, null)', ctx);
  await new Promise(setImmediate);
  await new Promise(setImmediate);

  resolveRec();
  await new Promise(setImmediate);
  await new Promise(setImmediate);

  assert.strictEqual(Array.from(doc.querySelectorAll('button')).some(b => b.textContent === 'Выбрать эти файлы'), false);
});
test('malformed catalog contract prevents consent rendering and POST request', async () => {
  const calls = [];
  const malformedCatalog = createValidV2Catalog();
  malformedCatalog.scope = 'invalid-scope';
  malformedCatalog.max_requests = 10;

  const { ctx, doc } = setupContext(async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET' });
    return { ok: true, json: async () => malformedCatalog };
  });
  vm.runInContext('state.registryReady = true;', ctx);
  await vm.runInContext('openCodeEvaluation(null)', ctx);
  await new Promise(setImmediate);

  assert.strictEqual(calls.length, 1);
  const consentCheckbox = doc.getElementById('code-eval-consent');
  assert.strictEqual(consentCheckbox, null);
  const startBtn = doc.querySelector('button.project-ai-start');
  assert.strictEqual(startBtn, null);
  const text = doc.getElementById('detail-body').textContent;
  assert.ok(text.includes('Контракт набора не подтверждён'));
});

test('late catalog response after pane close is ignored', async () => {
  let resolveCatalog;
  const catalogPromise = new Promise(r => { resolveCatalog = r; });
  const { ctx, doc } = setupContext(async (url) => {
    if (url === '/api/project-code-evaluation-v2') {
      return catalogPromise.then(cat => ({ ok: true, json: async () => cat }));
    }
    return { ok: true, json: async () => ({}) };
  });
  const origin = doc.createElement('button');
  doc.body.append(origin);
  ctx.evalOrigin = origin;
  vm.runInContext('state.registryReady = true;', ctx);
  const openPromise = vm.runInContext('openCodeEvaluation(evalOrigin)', ctx);
  await new Promise(setImmediate);

  vm.runInContext('closeDetail()', ctx);
  resolveCatalog(createValidV2Catalog());
  await openPromise;
  await new Promise(setImmediate);

  const checkbox = doc.getElementById('code-eval-consent');
  assert.strictEqual(checkbox, null);
});

test('unknown POST error locks checkbox and start button without retry', async () => {
  const calls = [];
  const { ctx, doc } = setupContext(async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET' });
    if (url === '/api/project-code-evaluation-v2') {
      return { ok: true, json: async () => createValidV2Catalog() };
    }
    return { ok: false, json: async () => ({ error: 'Unknown server fault' }) };
  });
  vm.runInContext('state.registryReady = true;', ctx);
  await vm.runInContext('openCodeEvaluation(null)', ctx);
  await new Promise(setImmediate);

  const btn = doc.querySelector('button.project-ai-start');
  const checkbox = doc.getElementById('code-eval-consent');
  assert.ok(btn && checkbox);
  checkbox.checked = true;
  checkbox.dispatchEvent({ type: 'change' });
  assert.strictEqual(btn.disabled, false);

  btn.click();
  await new Promise(setImmediate);

  assert.strictEqual(calls.filter(c => c.method === 'POST').length, 1);
  assert.strictEqual(btn.disabled, true);
  assert.strictEqual(checkbox.disabled, true);

  btn.click();
  await new Promise(setImmediate);
  assert.strictEqual(calls.filter(c => c.method === 'POST').length, 1);
  const bodyText = doc.getElementById('detail-body').textContent;
  assert.ok(bodyText.includes('Повтор автоматически не выполняется'));
});

test('history partial masks overall metrics, shows coverage status, literals not scripts, and legacy history via details', async () => {
  const catalogWithHistory = createValidV2Catalog();
  const maliciousHtml = '<script>alert("xss")</script>';
  catalogWithHistory.cases[0].files[0].content = `${maliciousHtml}\nconst safe = true;\n`;
  catalogWithHistory.cases[0].title = '<b>XSS Title</b>';
  catalogWithHistory.history = [
    {
      claim: {
        suite_profile: 'synthetic-file-overlap-v2',
        fixture_sha256: 'a'.repeat(64)
      },
      status: 'failed',
      result: {
        report: {
          scope: 'synthetic-file-overlap-v2',
          complete: false,
          answered: 4,
          total: 9,
          overall_precision: 0.987654321,
          tp: 1,
          fp: 2,
          fn: 3,
          per_case: {
            case_1: { answered: true, tp: 1, fp: 0, fn: 0 }
          },
          by_language: {
            python: { answered: 1, total: 3, precision: 0.987654321, tp: 1, fp: 0, fn: 0 }
          },
          limitations: 'Тестовые ограничения'
        },
        usage: {
          model_a: { input_tokens: 10, output_tokens: 20 }
        }
      }
    },
    {
      claim: {
        suite_profile: 'synthetic-line-overlap-v1',
        fixture_sha256: 'b'.repeat(64)
      },
      status: 'completed',
      result: {
        report: {
          scope: 'synthetic-line-overlap-v1',
          complete: true,
          answered: 3,
          total: 3
        }
      }
    }
  ];

  let getCalls = 0;
  const { ctx, doc } = setupContext(async (url) => {
    if (url === '/api/project-code-evaluation-v2') {
      getCalls++;
      return { ok: true, json: async () => catalogWithHistory };
    }
    return { ok: true, json: async () => ({}) };
  });
  vm.runInContext('state.registryReady = true;', ctx);
  await vm.runInContext('openCodeEvaluation(null)', ctx);
  await new Promise(setImmediate);

  assert.strictEqual(getCalls, 1);
  const body = doc.getElementById('detail-body');
  const paragraphs = Array.from(body.querySelectorAll('p')).map(p => p.textContent || '');
  const humanText = paragraphs.join(' ');

  assert.ok(humanText.includes('Отчёт неполный: precision и recall недоступны (недостаточно данных).'));
  assert.ok(!humanText.includes('0.987654321'));
  assert.ok(humanText.includes('Статус: failed'));
  assert.ok(humanText.includes('Отвечено: 4 из 9'));
  assert.ok(humanText.includes('Итого: TP 1 · FP 2 · FN 3'));
  assert.ok(humanText.includes('python: отвечено 1 из 3 · precision/recall недоступны'));
  assert.ok(humanText.includes('Токены: вход 10 · выход 20'));
  assert.ok(humanText.includes('Тестовые ограничения'));
  assert.ok(body.textContent.includes(maliciousHtml));
  assert.strictEqual(doc.querySelectorAll('script').length, 0);

  const detailsElements = Array.from(doc.querySelectorAll('details'));
  const legacyDetails = detailsElements.find(d => (d.textContent || '').includes('synthetic-line-overlap-v1'));
  assert.ok(legacyDetails);
});

test('latest completed v2 history renders metrics, tokens, and coverage without missing data marks', async () => {
  const catalog = createValidV2Catalog();
  catalog.history = [
    {
      claim: {
        suite_profile: 'synthetic-file-overlap-v2',
        fixture_sha256: 'c'.repeat(64)
      },
      status: 'completed',
      result: {
        report: {
          scope: 'synthetic-file-overlap-v2',
          complete: true,
          answered: 9,
          total: 9,
          overall_precision: 0.625,
          overall_recall: 0.5,
          tp: 5,
          fp: 3,
          fn: 5,
          per_case: {
            case_1: { answered: true, tp: 2, fp: 1, fn: 1 }
          },
          by_language: {
            python: { answered: 3, total: 3, precision: 0.75, recall: 0.6, tp: 3, fp: 1, fn: 2 }
          }
        },
        usage: {
          step1: { input_tokens: 15, output_tokens: 25 }
        }
      }
    }
  ];

  let getCalls = 0;
  const { ctx, doc } = setupContext(async (url) => {
    if (url === '/api/project-code-evaluation-v2') {
      getCalls++;
      return { ok: true, json: async () => catalog };
    }
    return { ok: true, json: async () => ({}) };
  });
  vm.runInContext('state.registryReady = true;', ctx);
  await vm.runInContext('openCodeEvaluation(null)', ctx);
  await new Promise(setImmediate);

  assert.strictEqual(getCalls, 1);
  const body = doc.getElementById('detail-body');
  const humanText = Array.from(body.querySelectorAll('p')).map(p => p.textContent || '').join(' ');
  assert.ok(humanText.includes('Precision: 0.625 · Recall: 0.5'));
  assert.ok(humanText.includes('python: отвечено 3 из 3 · precision 0.75 · recall 0.6 · TP 3 · FP 1 · FN 2'));
  assert.ok(humanText.includes('Токены: вход 15 · выход 25'));
  assert.ok(!humanText.includes('?'));
});

test('latest legacy v1 history renders legacy callout and preserves scope without incomplete claim', async () => {
  const catalog = createValidV2Catalog();
  catalog.history = [
    {
      claim: {
        suite_profile: 'synthetic-line-overlap-v1',
        fixture_sha256: 'd'.repeat(64)
      },
      status: 'completed',
      result: {
        report: {
          scope: 'synthetic-line-overlap-v1',
          complete: true,
          answered: 3,
          total: 3
        }
      }
    }
  ];

  let getCalls = 0;
  const { ctx, doc } = setupContext(async (url) => {
    if (url === '/api/project-code-evaluation-v2') {
      getCalls++;
      return { ok: true, json: async () => catalog };
    }
    return { ok: true, json: async () => ({}) };
  });
  vm.runInContext('state.registryReady = true;', ctx);
  await vm.runInContext('openCodeEvaluation(null)', ctx);
  await new Promise(setImmediate);

  assert.strictEqual(getCalls, 1);
  const body = doc.getElementById('detail-body');
  const bodyText = body.textContent || '';
  assert.ok(bodyText.includes('Отчёт формата v1 (устаревший). Метрики v2 недоступны; ниже показан сохранённый отчёт как есть.'));
  assert.ok(bodyText.includes('synthetic-line-overlap-v1'));
  assert.ok(!bodyText.includes('Отчёт неполный'));
});
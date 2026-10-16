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

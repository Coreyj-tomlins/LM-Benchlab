// Exercise the real inline application functions with a small DOM/storage adapter.
// No browser, package install, network connection, or inference server is required.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8');
const scripts = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)];
const application = scripts.at(-1)[1];
// Withhold only startup: each test calls the actual application functions itself.
const source = application.replace(/\r?\ninit\(\);\s*$/, '\n');
assert.notEqual(source, application, 'Expected the application startup call');

const categories = ['Interface', 'SIEM', 'Chat', 'Sales', 'Support', 'Coding', 'Writing', 'Analysis', 'Summarization']
  .map(id => ({ id, label: id === 'Interface' ? 'Networking' : id,
    description: `${id} test cases`, system_prompt: `Built-in ${id} instructions` }));
const catalog = { categories, default_enabled: categories.map(category => category.id) };
const nativeConfig = { name: 'LM Benchlab', version: 'test-release', default_engine_host: '127.0.0.1' };
const settingsKey = 'lm-benchlab-settings';
const enginesKey = 'lm-benchlab-engines';
const themeKey = 'lm-benchlab-theme';
const legacySettingsKey = 'llm-lab-settings';
const legacyEnginesKey = 'llm-lab-engines';
const legacyThemeKey = 'llm-lab-theme';
const plain = value => JSON.parse(JSON.stringify(value));

function harness(savedSettings, config = nativeConfig, initialStorage = {}) {
  const elements = new Map();
  const storage = new Map(Object.entries(initialStorage));
  if (savedSettings !== undefined) storage.set(settingsKey, savedSettings);
  const requests = [];
  const defaultResponder = url => url === '/api/config' ? { ok: true, data: config }
    : url === '/api/prompt-types' ? { ok: true, data: catalog }
      : { ok: false, data: { error: 'Stopped after capturing the run payload' } };
  let responder = defaultResponder;

  class Element {
    constructor(tag = 'div') {
      Object.assign(this, { tag, children: [], value: '', checked: false, disabled: false,
        dataset: {}, style: {}, attributes: {}, className: '', classList: { toggle() {}, add() {}, remove() {} } });
      this._text = '';
    }
    set id(value) { this._id = value; elements.set(value, this); }
    get id() { return this._id; }
    get options() { return this.children.filter(child => child.tag === 'option'); }
    get selectedOptions() { return this.options.filter(option => option.value === this.value); }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text; }
    append(...children) { children.forEach(child => { child.parent = this; this.children.push(child); }); }
    replaceChildren(...children) { this.children = []; this.append(...children); }
    closest(tag) { return this.tag === tag ? this : this.parent?.closest(tag); }
    focus() { this.focused = true; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name] ?? null; }
    reset() {}
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this); }
    querySelectorAll(selector) {
      if (selector === 'option[data-profile]') return this.options.filter(option => option.dataset.profile);
      throw Error(`Unexpected test DOM selector: ${selector}`);
    }
    querySelector(tag) {
      for (const child of this.children) {
        if (child.tag === tag) return child;
        const found = child.querySelector(tag);
        if (found) return found;
      }
      return null;
    }
  }

  // Use the HTML's real static IDs so missing controls fail the tests.
  for (const match of html.matchAll(/<(\w+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
    const element = new Element(match[1]);
    element.id = match[3];
    element.value = /\bvalue="([^"]*)"/.exec(match[2])?.[1] || '';
    element.disabled = /\bdisabled\b/.test(match[2]);
  }
  for (const match of html.matchAll(/<select\b[^>]*\bid="([^"]+)"[^>]*>([\s\S]*?)<\/select>/g)) {
    const select = elements.get(match[1]);
    for (const optionMatch of match[2].matchAll(/<option\b([^>]*)>([\s\S]*?)<\/option>/g)) {
      const option = new Element('option');
      option.value = /\bvalue="([^"]*)"/.exec(optionMatch[1])?.[1] || '';
      for (const data of optionMatch[1].matchAll(/\bdata-([\w-]+)="([^"]*)"/g)) {
        option.dataset[data[1].replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = data[2];
      }
      option.textContent = optionMatch[2];
      select.append(option);
    }
    select.value = select.options[0]?.value || '';
  }

  const document = { documentElement: new Element('html'), title: '',
    getElementById: id => elements.get(id) || null, createElement: tag => new Element(tag),
    querySelector: () => null };
  const context = vm.createContext({
    document,
    localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) },
    fetch: async (url, options) => {
      requests.push({ url, options, body: options.body ? JSON.parse(options.body) : undefined });
      const response = await responder(url, options);
      return { ok: response.ok, status: response.ok ? 200 : 400, json: async () => response.data };
    },
  });
  vm.runInContext(scripts[0][1], context, { filename: 'index.html initial theme and preference migration' });
  vm.runInContext(source, context, { filename: 'index.html application' });
  const run = expression => vm.runInContext(expression, context);
  const get = id => elements.get(id);
  const choose = ids => {
    categories.forEach(category => { get(`category-${category.id}`).checked = ids.includes(category.id); });
    run('updateCategorySelection()');
  };
  const load = async () => { run('setBusy(false)'); await run('loadPromptTypes()'); run('loadSettings()'); };
  return { run, get, load, choose, requests, storage, document, defaultResponder,
    respond: callback => { responder = callback; } };
}

test('new workspaces load all prompt types and canonical instructions', async () => {
  const ui = harness();
  await ui.load();
  assert.deepEqual(plain(ui.run('selectedCategories()')), catalog.default_enabled);
  assert.equal(ui.get('category-options').children.length, 9);
  assert.equal(ui.get('prompt-editors').children.length, 9);
  assert.equal(ui.get('prompt-Coding').value, 'Built-in Coding instructions');
  assert.equal(ui.get('prompt-Coding').maxLength, 10000);
  assert.equal(ui.get('run').disabled, false);
  assert.equal(ui.get('settings-save').disabled, false);
  assert.match(ui.get('active-categories').textContent, /Networking/);
});

test('runtime configuration supplies native host defaults and the displayed version', async () => {
  const ui = harness();
  await ui.load();
  assert.deepEqual(ui.requests.slice(0, 2).map(request => request.url), ['/api/config', '/api/prompt-types']);
  assert.equal(ui.get('app-version-badge').textContent, 'VERSION test-release');
  assert.equal(ui.get('app-name').textContent, 'LM Benchlab');
  assert.equal(ui.get('app-build-name').textContent, 'LM Benchlab');
  assert.equal(ui.document.title, 'LM Benchlab — Inference Benchmarking');
  assert.match(ui.get('endpoint-label').textContent, /as seen from LM Benchlab/);
  assert.equal(ui.get('host').value, nativeConfig.default_engine_host);
  assert.equal(ui.get('judge-host').value, nativeConfig.default_engine_host);
  assert.equal(ui.run('workspaceSettings().judge.host'), nativeConfig.default_engine_host);
  for (const option of ui.get('engine').options.filter(option => ['lm-studio', 'vllm'].includes(option.value))) {
    assert.equal(option.dataset.host, nativeConfig.default_engine_host);
  }
  ui.get('engine').value = 'vllm';
  ui.run('chooseEngine()');
  assert.equal(ui.get('host').value, nativeConfig.default_engine_host);
  assert.equal(ui.get('port').value, '8000');
  ui.get('engine-host').value = 'temporary.example.com';
  ui.run('closeEngineModal()');
  assert.equal(ui.get('engine-host').value, nativeConfig.default_engine_host);
  assert.doesNotMatch(html, /host\.docker\.internal|VERSION\s+\d/);
});

test('Docker defaults come from the server and preserve saved profile and judge hosts', async () => {
  const saved = JSON.stringify({ judge: { host: 'saved-judge.example.com', port: 8443 } });
  const config = { version: 'docker-test-release', default_engine_host: 'host.docker.internal' };
  const ui = harness(saved, config);
  const profiles = JSON.stringify([{ id: 'saved', name: 'Saved engine', host: 'saved-engine.example.com', port: 9000 }]);
  ui.storage.set('llm-lab-engines', profiles);
  ui.run('renderEngineProfiles()');
  ui.get('engine').value = 'profile:saved';
  await ui.load();
  assert.equal(ui.get('host').value, 'saved-engine.example.com');
  assert.equal(ui.get('judge-host').value, 'saved-judge.example.com');
  assert.equal(ui.get('judge-port').value, 8443);
  assert.equal(ui.get('engine-host').value, config.default_engine_host);
  assert.equal(ui.get('app-version-badge').textContent, 'VERSION docker-test-release');
  assert.equal(ui.storage.get(settingsKey), saved);
  assert.equal(ui.storage.get('llm-lab-engines'), profiles);
  ui.get('engine').value = 'lm-studio';
  ui.run('chooseEngine()');
  assert.equal(ui.get('host').value, config.default_engine_host);
});

test('configuration is awaited before the catalog or any inference requests', async () => {
  const ui = harness();
  let resolveConfig;
  ui.respond(url => url === '/api/config'
    ? new Promise(resolve => { resolveConfig = resolve; }) : ui.defaultResponder(url));
  const loading = ui.run('loadPromptTypes()');
  assert.deepEqual(ui.requests.map(request => request.url), ['/api/config']);
  assert.equal(ui.get('run').disabled, true);
  assert.equal(ui.get('settings-save').disabled, true);
  assert.equal(ui.get('host').value, '');
  await ui.run('fetchModels()');
  await ui.run('testBenchmarkConnection()');
  await ui.run('fetchJudgeModels()');
  await ui.run('testJudgeConnection()');
  await ui.run('saveEngine({preventDefault(){}})');
  await ui.run('startRun()');
  assert.deepEqual(ui.requests.map(request => request.url), ['/api/config']);
  resolveConfig({ ok: true, data: nativeConfig });
  await loading;
  assert.deepEqual(ui.requests.map(request => request.url), ['/api/config', '/api/prompt-types']);
  assert.equal(ui.get('run').disabled, false);
});

test('invalid configuration has no fallback host and can be retried safely', async () => {
  const ui = harness();
  ui.respond(() => ({ ok: true, data: { version: 'test-release' } }));
  await ui.load();
  assert.deepEqual(ui.requests.map(request => request.url), ['/api/config']);
  assert.equal(ui.get('host').value, '');
  assert.equal(ui.get('judge-host').value, '');
  assert.equal(ui.get('run').disabled, true);
  assert.equal(ui.get('settings-save').disabled, true);
  assert.match(ui.get('category-options').textContent, /invalid workspace configuration/);
  ui.respond(ui.defaultResponder);
  await ui.get('category-options').querySelector('button').onclick();
  assert.equal(ui.get('host').value, nativeConfig.default_engine_host);
  assert.equal(ui.get('run').disabled, false);
});

test('legacy settings preserve custom edits and migrate unchanged old defaults', async () => {
  const ui = harness(JSON.stringify({ prompts: {
    Chat: 'My custom conversational instructions',
    Sales: 'You are an experienced ethical MSSP account consultant. Produce useful, practical business writing appropriate to the task; avoid unsubstantiated claims, invented prices or guarantees. Keep it concise.',
  }, judge: { model: 'previous-judge-model', enabled: false } }));
  await ui.load();
  assert.equal(ui.get('prompt-Chat').value, 'My custom conversational instructions');
  assert.equal(ui.get('prompt-Sales').value, 'Built-in Sales instructions');
  assert.equal(ui.get('prompt-Coding').value, 'Built-in Coding instructions');
  assert.equal(ui.get('judge-model').value, 'previous-judge-model');
  assert.equal(ui.run('selectedCategories().length'), 9);
});

test('Chat and Sales selection survives save/reload; empty selection cannot overwrite it', async () => {
  const ui = harness();
  await ui.load();
  ui.choose(['Chat', 'Sales']);
  ui.run('saveSettings()');
  const saved = ui.storage.get(settingsKey);
  assert.deepEqual(JSON.parse(saved).enabled_categories, ['Chat', 'Sales']);
  assert.match(ui.get('active-categories').textContent, /Chat, Sales/);
  ui.run('renderPromptControls()');
  assert.deepEqual(plain(ui.run('selectedCategories()')), ['Chat', 'Sales']);
  ui.choose([]);
  ui.run('saveSettings()');
  assert.equal(ui.storage.get(settingsKey), saved);
  assert.match(ui.get('settings-status').textContent, /not saved/);
  assert.match(ui.get('category-error').textContent, /at least one/);
  assert.equal(ui.get('category-Interface').focused, true);
});

test('oversized prompt instructions cannot be saved', async () => {
  const ui = harness();
  await ui.load();
  ui.run('saveSettings()');
  const saved = ui.storage.get(settingsKey);
  ui.get('prompt-Chat').value = 'x'.repeat(10001);
  ui.run('saveSettings()');
  assert.equal(ui.storage.get(settingsKey), saved);
  assert.match(ui.get('settings-status').textContent, /10,000/);
  assert.equal(ui.get('prompt-Chat').focused, true);
});

test('runs use saved categories and instructions even when Settings has unsaved edits', async () => {
  const ui = harness();
  await ui.load();
  ui.choose(['Chat', 'Sales']);
  ui.get('prompt-Chat').value = 'Saved Chat instructions';
  ui.run('saveSettings()');
  ui.choose(['Support']);
  ui.get('prompt-Chat').value = 'Unsaved Chat instructions';
  ui.get('model').append(Object.assign({ tag: 'option' }, { value: 'fixture-model' }));
  ui.get('model').value = 'fixture-model';
  ui.get('engine').append({ tag: 'option', value: 'fixture-engine', dataset: { engine: 'Fixture engine' } });
  ui.get('engine').value = 'fixture-engine';
  await ui.run('startRun()');
  const payload = ui.requests.find(request => request.url === '/api/run').body;
  assert.deepEqual(payload.enabled_categories, ['Chat', 'Sales']);
  assert.equal(payload.prompt_overrides.Chat, 'Saved Chat instructions');
  assert.equal(ui.run('state.busy'), false);
  ui.run("state.suite='agentic12'");
  await ui.run('startRun()');
  assert.equal('enabled_categories' in ui.requests.at(-1).body, false);
});

test('catalog failures keep controls unavailable and Retry recovers without a page reload', async () => {
  const ui = harness();
  ui.respond(url => url === '/api/config' ? ui.defaultResponder(url)
    : { ok: false, data: { error: 'Temporary catalog failure' } });
  ui.run('setBusy(false)');
  await ui.run('loadPromptTypes()');
  assert.equal(ui.get('run').disabled, true);
  assert.equal(ui.get('settings-save').disabled, true);
  assert.match(ui.get('category-options').textContent, /Temporary catalog failure/);
  const retry = ui.get('category-options').querySelector('button');
  assert.equal(retry.textContent, 'Retry initialization');
  ui.respond(ui.defaultResponder);
  await retry.onclick();
  assert.equal(ui.get('run').disabled, false);
  assert.equal(ui.get('settings-save').disabled, false);
  assert.equal(ui.run('selectedCategories().length'), 9);
});

test('malformed browser storage recovers to defaults', async () => {
  const ui = harness('invalid json');
  await ui.load();
  assert.equal(ui.run('selectedCategories().length'), 9);
  assert.equal(ui.get('prompt-Chat').value, 'Built-in Chat instructions');
});

test('legacy browser preferences migrate with custom settings and synthetic credentials intact', async () => {
  const settings = JSON.stringify({ enabled_categories: ['Chat', 'Sales'], prompts: { Chat: 'My saved custom instructions' },
    judge: { enabled: true, host: 'saved-judge.example.com', model: 'saved-model', apiKey: 'synthetic-legacy-judge-key' } });
  const profiles = JSON.stringify([{ id: 'old-profile', name: 'Existing engine', host: 'saved-engine.example.com',
    port: 9000, apiKey: 'synthetic-legacy-engine-key' }]);
  const legacy = { [legacySettingsKey]: settings, [legacyEnginesKey]: profiles, [legacyThemeKey]: 'dark' };
  const ui = harness(undefined, nativeConfig, legacy);
  assert.equal(ui.document.documentElement.getAttribute('data-theme'), 'dark');
  await ui.load();
  assert.equal(ui.storage.get(settingsKey), settings);
  assert.equal(ui.storage.get(enginesKey), profiles);
  assert.equal(ui.storage.get(themeKey), 'dark');
  for (const [key, value] of Object.entries(legacy)) assert.equal(ui.storage.get(key), value);
  assert.deepEqual(plain(ui.run('selectedCategories()')), ['Chat', 'Sales']);
  assert.equal(ui.get('prompt-Chat').value, 'My saved custom instructions');
  assert.equal(ui.get('judge-api-key').value, 'synthetic-legacy-judge-key');
  assert.equal(ui.run('engineProfiles()[0].apiKey'), 'synthetic-legacy-engine-key');
  ui.run("applyTheme('light')");
  ui.run('saveSettings()');
  assert.equal(ui.storage.get(themeKey), 'light');
  assert.equal(ui.storage.get(legacyThemeKey), 'dark');
  assert.equal(JSON.parse(ui.storage.get(settingsKey)).judge.apiKey, 'synthetic-legacy-judge-key');
  assert.equal(ui.storage.get(legacySettingsKey), settings);
});

test('canonical browser preferences take precedence over legacy data', async () => {
  const canonical = JSON.stringify({ enabled_categories: ['Support'], prompts: { Chat: 'Canonical instructions' },
    judge: { host: 'canonical-judge.example.com', apiKey: 'synthetic-canonical-key' } });
  const profiles = JSON.stringify([{ id: 'canonical', host: 'canonical-engine.example.com' }]);
  const ui = harness(canonical, nativeConfig, {
    [legacySettingsKey]: JSON.stringify({ enabled_categories: ['Chat', 'Sales'], judge: { apiKey: 'synthetic-old-key' } }),
    [enginesKey]: profiles, [legacyEnginesKey]: JSON.stringify([{ id: 'legacy', host: 'old-engine.example.com' }]),
    [themeKey]: 'light', [legacyThemeKey]: 'dark',
  });
  assert.notEqual(ui.document.documentElement.getAttribute('data-theme'), 'dark');
  await ui.load();
  assert.deepEqual(plain(ui.run('selectedCategories()')), ['Support']);
  assert.equal(ui.get('prompt-Chat').value, 'Canonical instructions');
  assert.equal(ui.get('judge-api-key').value, 'synthetic-canonical-key');
  assert.equal(ui.run('engineProfiles()[0].id'), 'canonical');
  assert.equal(ui.storage.get(settingsKey), canonical);
  assert.equal(ui.storage.get(enginesKey), profiles);
  assert.equal(ui.storage.get(themeKey), 'light');
  assert.equal(ui.storage.get(legacyThemeKey), 'dark');
});

test('empty canonical preferences do not revive legacy selections or profiles', async () => {
  const ui = harness(JSON.stringify({ enabled_categories: [] }), nativeConfig, {
    [legacySettingsKey]: JSON.stringify({ enabled_categories: ['Chat'] }),
    [enginesKey]: '[]', [legacyEnginesKey]: JSON.stringify([{ id: 'old-profile' }]),
    [themeKey]: '', [legacyThemeKey]: 'dark',
  });
  await ui.load();
  assert.deepEqual(plain(ui.run('selectedCategories()')), []);
  assert.deepEqual(plain(ui.run('engineProfiles()')), []);
  assert.equal(ui.run("storedPreference('theme')"), '');
  assert.notEqual(ui.document.documentElement.getAttribute('data-theme'), 'dark');
});

test('the favicon uses the renamed native vector asset', () => {
  const mark = readFileSync(new URL('../lm-benchlab-mark.svg', import.meta.url), 'utf8').trim();
  const favicon = /<link rel="icon"[^>]+href="data:image\/svg\+xml,([^"]+)"/.exec(html);
  assert.ok(favicon);
  assert.equal(decodeURIComponent(favicon[1]), mark);
  assert.match(mark, /<title[^>]*>LM Benchlab<\/title>/);
  assert.match(html, /<title>LM Benchlab — Inference Benchmarking<\/title>/);
});

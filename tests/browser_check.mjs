/** Real Chrome checks. Node built-ins only; no downloads or non-loopback requests.
 * Screenshots are artifacts, not a claim of manual visual review.
 * FALLOUT_BROWSER_STORY_ONLY=true runs both actual false-success/recovery stories.
 * Leave all *_ONLY flags unset for the original full suite. The target URL can
 * be overridden with FALLOUT_BROWSER_TEST_URL (default: current UI preview port 8516).
 */
import {spawn} from 'node:child_process';
import {mkdir, writeFile, readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const runDirectory = resolve(root, '.runtime/browser-validation', `run-${Date.now()}-${process.pid}`);
const artifacts = runDirectory;
await mkdir(runDirectory, {recursive: true});
const testUrl = process.env.FALLOUT_BROWSER_TEST_URL || 'http://127.0.0.1:8516';
assert(/^http:\/\/127\.0\.0\.1:[0-9]{4,5}$/.test(testUrl), 'Browser tests must target a loopback app');
const visualOnly = process.env.FALLOUT_BROWSER_VISUAL_ONLY === 'true';
const layoutOnly = process.env.FALLOUT_BROWSER_LAYOUT_ONLY === 'true';
const storyOnly = process.env.FALLOUT_BROWSER_STORY_ONLY === 'true';
const expectedBackend = process.env.FALLOUT_BROWSER_EXPECT_BACKEND || 'in-memory';
assert(['in-memory', 'falkordb'].includes(expectedBackend), 'Known expected backend');
assert([visualOnly, layoutOnly, storyOnly].filter(Boolean).length <= 1, 'Choose only one browser-only mode');
const report = {status: 'RUNNING', mode: storyOnly ? 'story-only' : layoutOnly ? 'layout-only' : visualOnly ? 'visual-only' : 'full', url: testUrl, artifactDirectory: artifacts, startedAt: new Date().toISOString(),
  workflows: [], layouts: [], graphs: [], labs: [], downloads: [], verifications: [], stages: [], stories: [], artifacts: [], browserErrors: [], blockedExternalOrigins: [],
  limitations: ['Automated DOM/SVG and layout checks; screenshots were not manually visually reviewed.',
    'AI off is asserted from rendered server UI; no process environment or private files are inspected.']};
const browser = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const processHandle = spawn(browser, [
  '--headless=new', '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
  `--user-data-dir=${runDirectory}/profile`, `--disk-cache-dir=${runDirectory}/cache`,
  '--no-first-run', '--no-default-browser-check', '--disable-background-networking',
  '--disable-component-update', '--disable-sync', '--disable-breakpad', '--disable-crash-reporter',
  '--disable-default-apps', '--disable-domain-reliability', '--disable-extensions',
  '--metrics-recording-only', '--password-store=basic', '--use-mock-keychain',
  '--proxy-server=http://127.0.0.1:9', '--proxy-bypass-list=127.0.0.1;localhost',
  '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1',
  'about:blank',
], {cwd: root, env: {HOME: runDirectory,
  XDG_CACHE_HOME: runDirectory, XDG_CONFIG_HOME: runDirectory, TMPDIR: runDirectory}, stdio: ['ignore', 'ignore', 'pipe']});

let socket, page, evaluate, capture, phase = 'browser startup', lastFrame = 0;
const pending = new Map();
const sleep = ms => new Promise(accept => setTimeout(accept, ms));
const quote = JSON.stringify;
const phone = 'Phone support is available.';
const discount = 'A 20% discount is available.';
const staleContent = discount + '\n' + phone;
const investigate = 'Investigate & Repair';
const navigationLabels = {'Overview': 'Overview', [investigate]: 'Repair', 'Verify & Test': 'Verify'};
const visualSizes = [[1920, 1080], [1440, 900], [1280, 900], [1024, 900], [768, 900], [390, 844]];
const downloadEvents = [];

try {
  const endpoint = await new Promise((accept, reject) => {
    const timer = setTimeout(() => reject(new Error('Browser did not expose local DevTools within 20 seconds')), 20000);
    processHandle.on('error', error => {clearTimeout(timer); reject(error);});
    processHandle.on('exit', code => {clearTimeout(timer); reject(new Error(`Browser exited: ${code}`));});
    processHandle.stderr.on('data', data => {
      const match = data.toString().match(/DevTools listening on (ws:\/\/127\.0\.0\.1:[^\s]+)/);
      if (match) {clearTimeout(timer); accept(match[1]);}
    });
  });
  phase = 'loopback server health (up to 60 seconds)';
  const healthDeadline = Date.now() + 60000;
  let healthy = false;
  while (Date.now() < healthDeadline) {
    try {
      const response = await fetch(testUrl + '/_stcore/health', {redirect: 'error', signal: AbortSignal.timeout(2000)});
      healthy = response.ok && (await response.text()).trim() === 'ok';
      if (healthy) break;
    } catch { /* Only this loopback endpoint is retried. */ }
    await sleep(500);
  }
  assert(healthy, 'Harness environment: AI-off app health unavailable after 60 seconds');
  socket = new WebSocket(endpoint);
  await new Promise((accept, reject) => {socket.onopen = accept; socket.onerror = reject;});
  let serial = 0;
  const send = (method, params = {}, sessionId) => new Promise((accept, reject) => {
    const id = ++serial;
    const timer = setTimeout(() => {pending.delete(id); reject(new Error(`CDP timeout: ${method}`));}, 20000);
    pending.set(id, {accept, reject, timer});
    socket.send(JSON.stringify({id, method, params, ...(sessionId ? {sessionId} : {})}));
  });
  socket.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const {accept, reject, timer} = pending.get(message.id);
      clearTimeout(timer); pending.delete(message.id);
      message.error ? reject(new Error(JSON.stringify(message.error))) : accept(message.result);
    }
    if (message.method === 'Network.webSocketFrameReceived') lastFrame = Date.now();
    if (message.method?.startsWith('Browser.download')) downloadEvents.push({method: message.method, ...message.params});
    if (message.method === 'Runtime.exceptionThrown') {
      report.browserErrors.push(message.params.exceptionDetails.exception?.description || message.params.exceptionDetails.text);
    }
    if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error') {
      report.browserErrors.push(message.params.args.map(x => x.value || x.description || '').join(' '));
    }
    if (message.method === 'Log.entryAdded' && message.params.entry.level === 'error') report.browserErrors.push(message.params.entry.text);
    if (message.method === 'Fetch.requestPaused') {
      const {requestId, request} = message.params;
      const url = new URL(request.url);
      const allowed = ['127.0.0.1', 'localhost'].includes(url.hostname) || ['data:', 'blob:', 'about:'].includes(url.protocol);
      if (!allowed) report.blockedExternalOrigins.push(url.origin);
      void send(allowed ? 'Fetch.continueRequest' : 'Fetch.failRequest',
        allowed ? {requestId} : {requestId, errorReason: 'BlockedByClient'}, message.sessionId).catch(error => {
        report.browserErrors.push(`Request interception: ${error.message}`);
      });
    }
  };
  const {targetId} = await send('Target.createTarget', {url: 'about:blank'});
  const {sessionId} = await send('Target.attachToTarget', {targetId, flatten: true});
  page = (method, params = {}) => send(method, params, sessionId);
  evaluate = async expression => {
    const result = await page('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
    return result.result.value;
  };
  // Closed expanders may be mounted: their status must never count as primary evidence.
  const helpers = `
    window.visible = e => !!e && e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}) &&
      ![...document.querySelectorAll('details:not([open])')].some(d => d.contains(e) && !d.querySelector('summary')?.contains(e));
    window.all = selector => [...document.querySelectorAll(selector)].filter(visible);
    window.primary = () => all('[data-testid="stMarkdownContainer"], [data-testid="stText"], [data-testid="stHeading"], [data-testid="stCaptionContainer"], .fo-memory-lines, .fo-verification, .fo-snapshot-note')
      .filter(e => !e.closest('details')).map(e => e.innerText).join('\\n');
    window.visibleText = () => {
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      const parts = []; let node;
      while (node = walker.nextNode()) {
        if (!node.textContent.trim() || ['SCRIPT','STYLE'].includes(node.parentElement.tagName) || !visible(node.parentElement)) continue;
        const range = document.createRange(); range.selectNodeContents(node);
        if ([...range.getClientRects()].some(r => r.width && r.height)) parts.push(node.textContent.trim());
      }
      return parts.join('\\n');
    };
    window.busy = () => !!all('[data-testid="stStatusWidget"]').find(e => /running/i.test(e.innerText)) ||
      !!all('[data-stale="true"]').length;
    window.graphFrame = () => all('iframe').find(e => e.contentDocument?.querySelector('#graph'));
    window.graphDocument = () => graphFrame()?.contentDocument;
    window.headerStatus = () => all('.st-key-workbench_header .fo-status').map(e=>
      e.innerText + ' revision ' + e.dataset.revision);
    window.graphVisible = e => !!e && e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}) &&
      !e.closest('details:not([open])') && !e.closest('[hidden]');
  `;
  const wait = async (expression, label) => {
    if (phase !== label) console.log(`CHECK: ${label}`);
    phase = label;
    const start = Date.now();
    while (Date.now() - start < 25000) {
      if (await evaluate(expression)) return;
      await sleep(150);
    }
    throw new Error(`UI timeout: ${label}`);
  };
  // Require quiet Streamlit traffic plus a stable non-stale visible control tree.
  const settle = async () => {
    const start = Date.now(); let previous, stableSince = Date.now();
    while (Date.now() - start < 25000) {
      const state = await evaluate(`({busy:busy(),text:visibleText(),controls:all('button,input').map(e=>[e.textContent,e.disabled,e.checked,e.value])})`);
      const signature = JSON.stringify(state);
      if (signature !== previous || state.busy || Date.now() - lastFrame < 800) stableSince = Date.now();
      previous = signature;
      if (Date.now() - stableSince >= 1000) return;
      await sleep(150);
    }
    throw new Error(`Harness synchronization timeout: rerun did not settle during ${phase}`);
  };
  const expect = async (expression, label) => {
    await wait(expression, label);
    await settle();
    assert.equal(await evaluate(expression), true, `Settled UI assertion: ${label}`);
  };
  const nativeClick = async (expression, localOnly = false) => {
    if (!localOnly) await settle();
    const point = await evaluate(`(()=>{const e=${expression};if(!visible(e)||e.disabled)return null;
      e.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'});
      const r=e.getBoundingClientRect();const x=r.x+r.width/2,y=r.y+r.height/2;
      const hit=document.elementFromPoint(x,y);return hit && (e.contains(hit)||hit.contains(e)) ? {x,y}:null;})()`);
    assert(point, `Missing, disabled or obscured native control: ${expression}`);
    await page('Input.dispatchMouseEvent', {type: 'mouseMoved', ...point});
    await page('Input.dispatchMouseEvent', {type: 'mousePressed', ...point, button: 'left', clickCount: 1});
    await page('Input.dispatchMouseEvent', {type: 'mouseReleased', ...point, button: 'left', clickCount: 1});
    if (localOnly) await sleep(50); // JSON expansion is local DOM state, not a Streamlit rerun.
    else await settle();
  };
  const click = (selector, text) => nativeClick(`all(${quote(selector)}).find(e=>e.textContent.trim()===${quote(text)})`);
  const button = text => click('button', text);
  const expand = async (text, open = true) => {
    phase = `${open ? 'expand' : 'collapse'} ${text}`;
    const expression = `all('summary').find(e=>e.textContent.includes(${quote(text)}))`;
    if (await evaluate(`(${expression})?.parentElement.open !== ${open}`)) await nativeClick(expression);
    await expect(`(${expression})?.parentElement.open === ${open}`, phase);
  };
  const navigate = async name => {
    phase = `navigate to ${name}`;
    const label = navigationLabels[name];
    assert(label, `Known navigation value: ${name}`);
    await click('.st-key-section label', label);
    await expect(`all('.st-key-section label').some(e=>e.textContent.trim()===${quote(label)} && e.querySelector('input')?.checked)`, phase);
    if (name !== 'Overview') await expect(`all('h2').some(e=>e.textContent.trim()===${quote(name)})`, `${name} heading`);
  };
  // Snapshot/context changes preserve an explicit iframe view choice. Native
  // navigation unmounts it without clearing approvals, operations or reports.
  const freshOverviewGraph = async label => {
    const before = await evaluate('headerStatus()');
    await navigate('Verify & Test');
    await expect(`!graphFrame()`, `${label}: Verify unmounts the graph`);
    await navigate('Overview');
    await graphReady();
    assert.deepEqual(await evaluate('headerStatus()'), before, `${label}: native remount preserves revision and verdict`);
  };
  const select = async (container, option) => {
    if (container === '.st-key-fault_choice') await expand('Advanced · fault injection');
    if (container === '.st-key-lab_run') await expand('Advanced · isolated verification lab');
    phase = `select ${option}`;
    await nativeClick(`all(${quote(container + ' [data-baseweb="select"]')})[0]`);
    // Long Streamlit lists are virtualized: type into the focused searchable
    // combobox so an off-screen option is actually mounted before clicking it.
    await page('Input.insertText', {text: option});
    await wait(`all('[role="option"]').some(e=>e.textContent.trim()===${quote(option)})`, `native options for ${option}`);
    await click('[role="option"]', option);
    await expect(`all(${quote(container)}).some(e=>e.innerText.includes(${quote(option)}))`, `selected ${option}`);
  };
  capture = async name => {
    const image = await page('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false});
    await writeFile(resolve(artifacts, name + '.png'), Buffer.from(image.data, 'base64'));
    const dom = await evaluate(`({visible:typeof visibleText==='function'?visibleText():document.body.innerText,
      primary:typeof primary==='function'?primary():'',html:document.documentElement.outerHTML})`);
    await writeFile(resolve(artifacts, name + '.txt'), `VISIBLE DOM TEXT\n${dom.visible}\n\nPRIMARY (NO ADVANCED DETAILS)\n${dom.primary}\n`);
    await writeFile(resolve(artifacts, name + '.html'), dom.html);
    report.artifacts.push(name + '.png', name + '.txt', name + '.html');
    const graphHTML=await evaluate(`typeof graphDocument==='function'?graphDocument()?.documentElement.outerHTML:null`);
    if(graphHTML){await writeFile(resolve(artifacts,name+'.graph.html'),graphHTML);report.artifacts.push(name+'.graph.html');}
  };
  const checkpoint = async (name, workflow) => {await capture(name); report.workflows.push(workflow);};
  const top = async () => {await evaluate(`all('[data-testid="stMain"]')[0]?.scrollTo({top:0,behavior:'instant'});window.scrollTo(0,0)`); await sleep(200);};
  const layout = async (name, control, aboveFold = false) => {
    await settle();
    const metrics = await evaluate(`(()=>{const e=all('button').find(e=>e.textContent.trim()===${quote(control)});
      if(!e)return null;${aboveFold ? '' : "e.scrollIntoView({block:'center',behavior:'instant'});"}
      const r=e.getBoundingClientRect(),s=getComputedStyle(e),p=e.querySelector('p')||e,ps=getComputedStyle(p);
      const range=document.createRange();range.selectNodeContents(p);const text=range.getBoundingClientRect();
      return {name:${quote(name)},viewport:[innerWidth,innerHeight],documentWidth:document.documentElement.scrollWidth,
        bodyWidth:document.body.scrollWidth,mainWidth:document.querySelector('[data-testid="stMain"]')?.scrollWidth,
        control:${quote(control)},x:r.x,y:r.y,width:r.width,height:r.height,font:parseFloat(ps.fontSize),
        textFits:text.left>=r.left&&text.right<=r.right+1&&text.top>=r.top&&text.bottom<=r.bottom+1,
        inViewport:r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight,
        primary:e.getAttribute('kind')==='primary'||e.getAttribute('data-testid')==='stBaseButton-primary',
        background:s.backgroundColor,color:ps.color};})()`);
    assert(metrics, `${name}: primary control exists`);
    report.layouts.push(metrics);
    assert(metrics.documentWidth <= metrics.viewport[0] && metrics.bodyWidth <= metrics.viewport[0] && metrics.mainWidth <= metrics.viewport[0], `${name}: horizontal page overflow`);
    assert(metrics.width >= 120 && metrics.height >= 44 && metrics.font >= 14 && metrics.textFits && metrics.inViewport, `${name}: readable primary control`);
    assert(metrics.primary, `${name}: primary styling`);
    return metrics;
  };
  const noErrors = async () => {
    assert.equal(report.browserErrors.length, 0, `Browser errors: ${report.browserErrors.join('; ')}`);
    assert.equal(await evaluate(`all('[data-testid="stException"]').length`), 0, 'No rendered app exception');
    assert.equal(await evaluate(`visibleText().includes("Cannot create property 'bottom'")`), false, 'No graph padding exception');
  };
  const result = async (verdict, checks = 30) => {
    await expect(`headerStatus().length===1 && headerStatus()[0].includes(${quote('VERIFIER / ' + verdict)})`, `truthful primary header ${verdict}`);
    const text = verdict === 'PASS' ? 'PASS — The saved memory matches the independently rebuilt result.' :
      verdict === 'FAIL' ? 'FAIL — The saved memory does not match the independently rebuilt result.' :
        'NOT RUN — This version of the memory has not been independently checked.';
    await expect(`primary().includes(${quote(text)})`, `visible independent ${verdict}`);
    if (verdict === 'PASS' || verdict === 'FAIL') {
      const counts = await evaluate(`({cards:all('.fo-verification').filter(e=>!e.closest('details')).map(e=>({
        verdict:e.querySelector('strong')?.textContent,counts:[...e.querySelectorAll('span b')].map(b=>Number(b.textContent))})),
        header:all('.st-key-workbench_header .fo-header-comparisons').map(e=>e.textContent.trim())})`);
      assert.equal(counts.cards.length, 1, 'One primary independent comparison card');
      assert.equal(counts.cards[0].verdict, verdict);
      const [executed, mismatches] = counts.cards[0].counts;
      assert(Number.isInteger(executed) && executed > 0 && Number.isInteger(mismatches) && mismatches >= 0 && mismatches <= executed,
        'Actual nonzero executed comparisons and bounded mismatches');
      assert.deepEqual(counts.header, [`${executed} of ${executed} checks compared`], 'Primary header and comparison card agree');
      assert.equal(mismatches === 0, verdict === 'PASS', 'Primary mismatch count agrees with verdict');
      if (verdict === 'PASS' && checks !== null) assert.equal(executed, checks, 'Expected count in primary comparison card');
      const scenario = await evaluate(`all('.st-key-scenario_choice')[0]?.innerText`);
      await memoryEvidence(scenario?.includes(documentation.label) ? documentation : pricing, verdict);
    }
  };
  const actualRepair = async stale => {
    const title = 'Repair details · facts, lineage and execution';
    const wasOpen = await evaluate(`all('summary').find(e=>e.textContent.includes(${quote(title)}))?.parentElement.open`);
    await expand(title);
    const data = await evaluate(`(()=>{const t=all('summary').find(e=>e.textContent.includes(${quote(title)})).parentElement.innerText;return {
      scope:'expanded repair details, not primary evidence',
      stayed:t.split('What stayed')[1]?.split('What was removed from usable memory')[0]?.trim(),
      removed:t.split('What was removed from usable memory')[1]?.split('What was updated')[0]?.trim(),
      updated:t.split('What was updated')[1]?.split('Independent check')[0]?.split('AFTER /')[1]?.trim()};})()`);
    assert(data.stayed?.includes(phone) && data.stayed.includes('supporting sources: pricing') && !data.stayed.includes(discount), 'Actual phone_support preserved with pricing support');
    assert(data.removed?.includes(discount) && !data.removed.includes(phone), 'Actual discount removed from usable memory');
    assert(data.updated?.includes(phone), 'Actual summary contains phone support');
    assert.equal(data.updated.includes(discount), stale, 'Actual summary discount membership');
    if (stale) assert(data.updated.includes('The summary is still out of date.'), 'Actual stale summary warning');
    else assert(data.updated.includes('The saved summary now reads:'), 'Actual saved summary updated');
    if (!wasOpen) await expand(title, false);
    return data;
  };
  // Read only the rendered, explicitly expanded report, never session/backend internals.
  const renderedJSON = async (title, directContainer = null) => {
    if (title?.startsWith('Lab evidence · ')) await expand('Advanced · isolated verification lab');
    if (!directContainer) await expand(title);
    const container = directContainer || `all('summary').find(e=>e.querySelector('[data-testid="stMarkdownContainer"]')?.textContent.trim()===${quote(title)}).parentElement`;
    // Expand real DOM branches with native clicks, never read component/React state.
    for (let count = 0; count < 1200; count++) {
      const collapsed = ` [...(${container}).querySelectorAll('[data-testid="stJson"] .object-key-val')].find(e=>
        !e.querySelector(':scope > .pushed-content > .object-content') && !e.querySelector(':scope > .brace-row'))?.querySelector(':scope > span')`;
      if (!await evaluate(`!!(${collapsed})`)) break;
      assert(count < 1199, 'Harness JSON expansion limit');
      await nativeClick(collapsed, true);
    }
    // Streamlit renders an expandable JSON tree, not JSON text (commas are omitted
    // and array indices are shown). Decode only the displayed tree, not React or
    // backend state. Fail closed if a nonempty branch is collapsed.
    const data = await evaluate(`(()=>{
      const root=(${container}).querySelector('[data-testid="stJson"] .pretty-json-container > .object-content > .object-key-val');
      const parse=row=>{
        if(row.classList.contains('variable-row')){
          const value=row.querySelector(':scope > .variable-value');
          const string=value.querySelector('.string-value');
          if(string){const t=string.textContent;try{return JSON.parse(t);}catch{return t.slice(1,-1);}}
          const scalar=value.textContent.trim();
          return scalar==='NULL'?null:JSON.parse(scalar);
        }
        const header=row.querySelector(':scope > span').textContent.trim();
        const array=header.endsWith('['), content=row.querySelector(':scope > .pushed-content > .object-content');
        if(!content){
          if(!row.querySelector(':scope > .brace-row'))throw Error('Harness cannot read collapsed JSON branch');
          return array?[]:{};
        }
        const value=array?[]:{};
        for(const child of content.children){
          if(!child.matches('.variable-row,.object-key-val'))continue;
          const key=child.querySelector(':scope > span .object-key, :scope > span .array-key');
          const index=child.querySelector(':scope > span')?.textContent.trim().match(/^([0-9]+):$/);
          if(!key && !(array && index))throw Error('Harness JSON tree key missing');
          const name=!key?Number(index[1]):key.classList.contains('array-key')?Number(key.textContent):JSON.parse(key.textContent);
          value[name]=parse(child);
        }
        return value;
      };
      return parse(root);
    })()`);
    return data;
  };
  const technicalReport = async (label, expectedCount = 30) => {
    await expand('Advanced details · technical verification report');
    const data = await renderedJSON('Complete verifier report / dataset identity');
    const count = data.comparisons.length;
    assert(count > 0, 'Actual report has nonzero comparisons');
    if (expectedCount !== null) assert.equal(count, expectedCount, 'Actual pricing report has 30 comparisons');
    assert.deepEqual(data.comparisons.map(c=>c.path).sort(), [...data.required_paths].sort(), 'All scheduled comparisons executed');
    assert.equal(new Set(data.required_paths).size, count, 'No duplicate comparison paths');
    assert.equal(await evaluate(`all('.st-key-workbench_header .fo-status')[0]?.dataset.revision`), String(data.revision), 'Compact header revision matches rendered verifier evidence');
    await expect(`visibleText().includes(${quote(`${count} of ${count} comparisons shown`)})`, 'real complete comparison ledger');
    assert(data.started_at && data.finished_at && data.revision > 0, 'Actual verifier execution timestamps and revision');
    const scenario = await evaluate(`all('.st-key-scenario_choice')[0]?.innerText`);
    await memoryEvidence(scenario?.includes(documentation.label) ? documentation : pricing, data.status, data);
    report.verifications.push({label, ...data});
    return data;
  };
  const comparison = (data, path, expected, actual) => {
    const row = data.comparisons.find(item => item.path === path);
    assert(row, `Executed comparison ${path}`);
    assert.deepEqual(row.expected, expected, `${path}: expected reference`);
    assert.deepEqual(row.actual, actual, `${path}: actual memory`);
    assert.equal(row.match, JSON.stringify(expected) === JSON.stringify(actual), `${path}: comparison verdict`);
  };
  const preview = async name => {
    await button('Preview changes');
    await stageBanners('preview');
    await previewHighlight(pricing, true);
    await expect(`primary().includes('Preview only — nothing has changed. These are expected outcomes, not repair results.')`, 'preview is not execution');
    assert.equal(await evaluate(`primary().includes('What actually happened')`), false, 'Preview must not display execution results');
    await expand('Impact details · facts and proposed summaries');
    await expect(`all('[data-testid="stText"]').some(e=>e.innerText.trim()===${quote(phone)})`, 'expanded preview proposed phone-only summary');
    await expand('Impact details · facts and proposed summaries', false);
    const data = await renderedJSON('Advanced details · exact preview');
    assert.deepEqual(data.affected_facts, ['discount_20', 'phone_support'], 'Real preview facts');
    assert.deepEqual(data.remaining_support.phone_support, ['pricing'], 'Real preview remaining support');
    await expand('Advanced details · exact preview', false);
    await expand('Advanced details · operation and audit evidence');
    await expect(`visibleText().includes('NO OPERATIONS / Execution evidence appears only after explicit confirmation.')`, 'no operation before explicit confirmation');
    await expand('Advanced details · operation and audit evidence', false);
    await expect(`headerStatus().length===1 && headerStatus()[0].includes('NOT RUN')&&headerStatus()[0].includes('revision 0')`, 'preview leaves primary revision zero and verifier NOT RUN');
    await layout(name, 'Confirm removal and repair');
    await checkpoint(name, 'Preview: no execution, revision 0, NOT RUN, supported phone-only proposed summary');
    return data;
  };

  const aiOff = async () => {
    await expect(`all('.fo-header-state').some(e=>e.textContent.includes('AI OFF'))`, 'server header explicitly AI OFF');
    await expand('AI assistance · optional explanations and proposals');
    await expect(`visibleText().includes('AI is off. No external request will be made.')`, 'AI explicitly disabled');
    assert.equal(await evaluate(`all('button').some(e=>e.textContent.trim()==='Request AI assistance')`), false, 'AI off has no request action');
    await expand('AI assistance · optional explanations and proposals', false);
  };
  const graphReady = async () => {
    await expect(`!!graphDocument()?.querySelector('.node')`, 'primary same-origin SVG graph');
    assert.equal(await evaluate(`!!graphFrame().closest('details')`), false, 'Graph is primary, not inside advanced details');
    assert.equal(await evaluate(`document.querySelectorAll('.st-key-graph_fallback').length`), 0, 'Obsolete graph fallback checkbox is removed');
    assert.equal(await evaluate(`all('summary').some(e=>e.textContent.includes('Relationship records / accessible graph alternative'))`), false,
      'Healthy graph does not render error-only relationship records expander');
    assert.deepEqual(await evaluate(`all('.st-key-graph_view label').map(e=>e.textContent.trim()).sort()`),
      ['Before last operation', 'Current state', 'Original dataset'], 'Segmented snapshot toolbar retains all three radio labels');
  };
  const inspectionCaption = async entity => {
    await expect(`all('[data-testid="stCaptionContainer"]').some(e=>e.textContent.includes(${quote('Inspecting: ' + entity)}))`,
      `inspection caption tracks ${entity}`);
  };
  const previewHighlight = async (config, active) => {
    await graphReady();
    const data = await evaluate(`(()=>{const d=graphDocument();return {
      affected:[...d.querySelectorAll('.node')].filter(e=>e.classList.contains('affected')||
        e.dataset.affected==='true'||!!e.querySelector('.affected')||/\\baffected\\b/i.test(e.getAttribute('aria-label')||''))
        .map(e=>e.dataset.entity),
      captions:all('[data-testid="stCaptionContainer"]').map(e=>e.textContent)};})()`);
    const source = `sources.${config.source}`;
    if (active) {
      assert(data.affected.includes(source), `${config.id}: current preview highlights its actual source ${source}`);
      assert.deepEqual(data.affected.filter(e=>e.startsWith('sources.')), [source], `${config.id}: unrelated sources are not marked affected`);
      assert(data.captions.some(text=>/preview target/i.test(text)&&text.includes(config.source)), `${config.id}: caption explicitly names preview target`);
    } else assert.deepEqual(data.affected, [], `${config.id}: idle graph has no affected/amber nodes`);
    report.graphs.push({label:`${config.id}: ${active?'preview':'idle'} affected nodes`,...data});
  };
  const graphClick = async selector => {
    phase = `native graph click ${selector}`;
    await settle();
    const point = await evaluate(`(()=>{
      const frame=graphFrame(), doc=frame?.contentDocument,
        e=[...doc?.querySelectorAll(${quote(selector)})||[]].find(graphVisible);
      if(!e || e.disabled || e.getAttribute('aria-disabled')==='true')return null;
      const target=e.querySelector('.halo')||e;
      // The native-height graph can be taller than the browser window. Center
      // its target last, not the entire iframe (which could hide the target).
      frame.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'});
      target.scrollIntoView({block:'center',inline:'center',behavior:'instant'});
      const r=target.getBoundingClientRect(), f=frame.getBoundingClientRect();
      const cx=r.x+r.width/2,cy=r.y+r.height/2,hit=doc.elementFromPoint(cx,cy);
      const x=f.x+frame.clientLeft+cx,y=f.y+frame.clientTop+cy;
      return hit&&(e.contains(hit)||hit.contains(e))&&document.elementFromPoint(x,y)===frame ? {x,y}:null;
    })()`);
    assert(point, `Harness native iframe target missing or obscured: ${selector}`);
    for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) {
      await page('Input.dispatchMouseEvent', {type, ...point, ...(type==='mouseMoved'?{}:{button:'left',clickCount:1})});
    }
    await settle();
  };
  const inspected = async (entity, status) => {
    await expect(`all('.st-key-entity_id').some(e=>e.innerText.includes(${quote(entity)})) &&
      all('.fo-id').some(e=>e.textContent===${quote(entity.split('.')[1])}) &&
      graphDocument()?.querySelector('.node[aria-pressed="true"]')?.dataset.entity===${quote(entity)}`,
    `Python inspector, selectbox and graph synchronized: ${entity}`);
    await inspectionCaption(entity);
    const text = await evaluate(`document.querySelector('.st-key-entity_id').closest('[data-testid="stColumn"]').innerText`);
    if (status) assert(text.includes(status), `Python inspector recorded status ${status}`);
    return text;
  };
  const graphState = async (label, repaired = false, stale = false) => {
    await graphReady();
    const data = await evaluate(`(()=>{const d=graphDocument();return {
      nodes:[...d.querySelectorAll('.node')].map(e=>({entity:e.dataset.entity,label:e.getAttribute('aria-label'),text:e.textContent})),
      edges:[...d.querySelectorAll('.edge')].map(e=>({title:e.querySelector('title').textContent,
        active:e.dataset.active==='true',dash:e.querySelector('path').getAttribute('stroke-dasharray')}))};})()`);
    assert.deepEqual(data.nodes.map(n=>n.entity).sort(), ['facts.discount_20','facts.phone_support','sources.pricing','sources.sales','summaries.customer_summary']);
    const edges = [
      ['sources.pricing','facts.phone_support',true], ['sources.sales','facts.phone_support',!repaired],
      ['sources.sales','facts.discount_20',!repaired], ['facts.discount_20','summaries.customer_summary',!repaired],
      ['facts.phone_support','summaries.customer_summary',true],
    ];
    assert.equal(data.edges.length, 5, 'All five retained relationships rendered');
    for (const [a,b,active] of edges) {
      const edge=data.edges.find(e=>e.title.startsWith(`${a} → ${b}\n`));
      assert(edge, `Graph relationship ${a} → ${b}`);
      assert.equal(edge.active, active, `${label}: active edge ${a} → ${b}`);
      assert.equal(edge.dash, active?'none':'6 4', `${label}: inactive edges actually dashed`);
    }
    for (const [entity, status] of [['sources.pricing','active'],['sources.sales',repaired?'revoked':'active'],
      ['facts.phone_support','valid'],['facts.discount_20',repaired?'invalid':'valid'],
      ['summaries.customer_summary',stale?'stale':'fresh']]) {
      assert(data.nodes.find(n=>n.entity===entity).label.toLowerCase().includes(status), `${label}: ${entity} ${status}`);
    }
    report.graphs.push({label,...data});
    return data;
  };
  const snapshotView = async name => {
    await click('.st-key-graph_view label', name);
    await expect(`all('.st-key-graph_view label').some(e=>e.textContent.trim()===${quote(name)}&&e.querySelector('input')?.checked)`, `graph snapshot ${name}`);
  };
  // Readiness never chooses a view. A freshly mounted component must respond to
  // resizing before any explicit Graph choice (which intentionally persists).
  const graphDefault = async label => {
    await graphReady();
    const data = await evaluate(`(()=>{const d=graphDocument(),v=d.querySelector('#viewport'),
      compact=innerWidth<600||d.querySelector('main').clientWidth<600;return {
      compact,viewport:graphVisible(v),table:graphVisible(d.querySelector('#relationships')),
      camera:graphVisible(d.querySelector('#camera')),graphPressed:d.querySelector('#graph-view').getAttribute('aria-pressed'),
      tablePressed:d.querySelector('#table-view').getAttribute('aria-pressed')};})()`);
    assert.equal(data.table, data.compact, `${label}: Relationships is the default below 600px parent or component width`);
    assert.equal(data.viewport, !data.compact, `${label}: default graph visibility`);
    assert.equal(data.camera, !data.compact, `${label}: camera hidden with default table`);
    assert.equal(data.graphPressed, String(!data.compact));
    assert.equal(data.tablePressed, String(data.compact));
    report.graphs.push({label: `${label}: default view`, ...data});
  };
  const chooseGraph = async () => {
    await graphClick('#graph-view');
    await expect(`graphVisible(graphDocument()?.querySelector('#viewport')) &&
      !graphVisible(graphDocument()?.querySelector('#relationships')) &&
      graphDocument()?.querySelector('#graph-view').getAttribute('aria-pressed')==='true'`, 'explicit Graph opt-in');
  };
  const relationshipTable = async label => {
    const wasGraph = await evaluate(`graphVisible(graphDocument()?.querySelector('#viewport'))`);
    if (wasGraph) await graphClick('#table-view');
    const data = await evaluate(`(()=>{const d=graphDocument();return {
      visible:graphVisible(d.querySelector('#relationships')),caption:d.querySelector('caption').textContent,
      edges:[...d.querySelectorAll('.edge')].map(e=>({reference:e.dataset.reference,relation:e.dataset.relation,
        endpoints:e.querySelector('title').textContent.split('\\n')[0].split(' → '),active:e.dataset.active==='true'})),
      rows:[...d.querySelectorAll('#relationship-rows tr')].map(e=>({reference:e.dataset.reference,
        visible:graphVisible(e),cells:[...e.querySelectorAll('td')].map(c=>c.textContent.trim()),
        endpoints:[...e.querySelectorAll('button')].map(b=>({entity:b.dataset.entity,visible:graphVisible(b)}))})),
      text:[...d.querySelectorAll('#relationships caption,#relationships th,#relationships td span,#relationships td button,#relationships > p')]
        .filter(e=>graphVisible(e)&&!e.closest('#relationships.compact thead')).map(e=>{
          let p=e,background='rgba(0, 0, 0, 0)';
          while(p){background=d.defaultView.getComputedStyle(p).backgroundColor;if(background!=='rgba(0, 0, 0, 0)')break;p=p.parentElement;}
          const style=d.defaultView.getComputedStyle(e),r=e.getBoundingClientRect();
          return {text:e.textContent,color:style.color,background,font:parseFloat(style.fontSize),
            minimum:e.matches('button,caption')?15:e.matches('.edge-reference-number')?12:13,
            inFrame:r.left>=0&&r.right<=graphFrame().clientWidth+1};})};})()`);
    assert(data.visible && data.caption === 'Source → relationship → target', `${label}: visible full endpoint table`);
    assert(data.edges.length > 0);
    assert.deepEqual(data.edges.map(e=>e.reference), data.edges.map((_,i)=>String(i+1)), `${label}: complete ordered graph reference numbers`);
    assert.equal(data.rows.length, data.edges.length, `${label}: table retains every graph edge`);
    assert.equal(new Set(data.rows.map(r=>r.reference)).size, data.edges.length, `${label}: unique numbered references`);
    for (const edge of data.edges) {
      const row = data.rows.find(r=>r.reference === edge.reference);
      assert(row?.visible, `${label}: visible table row #${edge.reference}`);
      assert.deepEqual(row.cells, ['#'+edge.reference, edge.endpoints[0], edge.relation, edge.endpoints[1], edge.active?'Active':'Retained inactive']);
      assert.deepEqual(row.endpoints.map(e=>e.entity), edge.endpoints, `${label}: complete selectable endpoints`);
      assert(row.endpoints.every(e=>e.visible), `${label}: visible endpoint controls`);
    }
    assert(data.text.length>0, `${label}: rendered relationship text samples`);
    for (const text of data.text) {
      assert(text.font>=text.minimum&&text.inFrame, `${label}: readable relationship text within component: ${text.text}`);
      assert(contrast(text.color,text.background)>=4.5, `${label}: relationship text contrast >=4.5: ${text.text}`);
    }
    report.graphs.push({label: `${label}: complete relationship table`, ...data});
    if (wasGraph) await chooseGraph();
  };
  const luminance = color => {
    const rgb = /^rgb\((\d+),\s*(\d+),\s*(\d+)\)$/.exec(color);
    assert(rgb, `Opaque computed RGB color required: ${color}`);
    const values = rgb.slice(1).map(Number).map(value => {
      const channel = value / 255;
      return channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4;
    });
    return values[0] * .2126 + values[1] * .7152 + values[2] * .0722;
  };
  const contrast = (foreground, background) => {
    const a = luminance(foreground), b = luminance(background);
    return (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
  };
  const graphLayout = async label => {
    await graphReady();
    const data = await evaluate(`(()=>{const f=graphFrame(),d=f.contentDocument,s=d.querySelector('svg'),r=f.getBoundingClientRect(),
      v=d.querySelector('#viewport'),vr=v.getBoundingClientRect(),sr=s.getBoundingClientRect(),scale=s.getScreenCTM().a;
      const rect=e=>{const b=e.getBoundingClientRect();return {left:b.left,right:b.right,top:b.top,bottom:b.bottom};};
      return {label:${quote(label)},width:innerWidth,page:document.documentElement.scrollWidth,body:document.body.scrollWidth,
        main:document.querySelector('[data-testid="stMain"]').scrollWidth,frameWidth:r.width,scale,
        visible:graphVisible(s)&&graphVisible(v),compact:innerWidth<600||d.querySelector('main').clientWidth<600,
        scenario:all('.st-key-scenario_choice')[0]?.innerText,
        nodes:[...s.querySelectorAll('.node')].map(e=>({entity:e.dataset.entity,
          id:[...e.querySelectorAll('text.label')].filter(graphVisible).map(t=>t.textContent).join('')})),
        labels:[...s.querySelectorAll('text')].filter(graphVisible).map(t=>{const b=t.getBoundingClientRect(),style=d.defaultView.getComputedStyle(t);
          return {text:t.textContent,kind:t.getAttribute('class'),entity:t.closest('.node')?.dataset.entity,
            visible:graphVisible(t),font:parseFloat(style.fontSize)*scale,color:style.fill,
            background:d.defaultView.getComputedStyle(t.matches('.edge-label')?t.parentElement.querySelector('.edge-chip'):s)[t.matches('.edge-label')?'fill':'backgroundColor'],
            rect:rect(t),inSVG:b.left>=sr.left-1&&b.right<=sr.right+1&&b.top>=sr.top-1&&b.bottom<=sr.bottom+1,
            inViewport:b.left>=vr.left-1&&b.right<=vr.left+v.clientWidth+1&&b.top>=vr.top-1&&b.bottom<=vr.top+v.clientHeight+1};}),
        edgeCount:s.querySelectorAll('.edge').length,
        references:[...s.querySelectorAll('.edge-chip')].filter(graphVisible).map(e=>({reference:e.parentElement.dataset.reference,rect:rect(e)})),
        buttons:[...d.querySelectorAll('button')].filter(graphVisible).map(e=>{const r=e.getBoundingClientRect(),range=d.createRange();range.selectNodeContents(e);
          const t=range.getBoundingClientRect();return {text:e.textContent,font:parseFloat(d.defaultView.getComputedStyle(e).fontSize),
            color:d.defaultView.getComputedStyle(e).color,background:d.defaultView.getComputedStyle(e).backgroundColor,
            width:r.width,height:r.height,fits:t.left>=r.left-1&&t.right<=r.right+1&&t.top>=r.top-1&&t.bottom<=r.bottom+1,
            inFrame:r.left>=0&&r.right<=f.clientWidth};})};})()`);
    report.layouts.push(data);
    assert(data.page<=data.width&&data.body<=data.width&&data.main<=data.width, `${label}: no horizontal page overflow`);
    assert(data.visible && data.labels.length > 0, `${label}: graph explicitly visible`);
    assert(data.scenario?.includes(pricing.label) || data.scenario?.includes(documentation.label), `${label}: known rendered scenario`);
    const ids = data.scenario.includes(documentation.label) ?
      ['sources.obsolete_bulletin','sources.api_reference','sources.installation_guide','facts.cursor_pagination',
        'facts.legacy_query_tokens','facts.rate_limit','facts.python_requirement','summaries.api_overview',
        'summaries.migration_notes','summaries.installation_summary'] :
      ['sources.sales','sources.pricing','facts.phone_support','facts.discount_20','summaries.customer_summary'];
    assert.deepEqual(data.nodes.map(n=>n.entity).sort(), ids.sort(), `${label}: every scenario node ID is present`);
    for (const node of data.nodes) assert.equal(node.id, node.entity.split('.')[1], `${label}: complete visible node ID, even before progressive labels activate`);
    assert(Math.abs(data.scale-1)<.01, `${label}: default native camera, not shrunken Fit labels`);
    for (const text of data.labels) {
      const minimum = text.kind === 'label' ? 15 : text.kind === 'edge-label' ? 12 : 13;
      assert(text.visible&&text.font>=minimum-.01&&text.inSVG, `${label}: readable unclipped ${text.entity||text.kind} text ${text.text}`);
      if (!data.compact) assert(text.inViewport, `${label}: desktop default label inside graph viewport: ${text.text}`);
      text.contrast = contrast(text.color, text.background);
      assert(text.contrast>=4.5, `${label}: graph text contrast >=4.5: ${text.text} (${text.contrast})`);
    }
    const overlaps = (a,b) => a.left<b.right-.5&&a.right>b.left+.5&&a.top<b.bottom-.5&&a.bottom>b.top+.5;
    assert.equal(data.references.length, data.edgeCount, `${label}: every edge retains its visible reference`);
    assert.equal(data.references.length, data.labels.filter(t=>t.kind==='edge-label').length, `${label}: one label per edge reference`);
    for (let i=0; i<data.references.length; i++) {
      const ref=data.references[i];
      for (const other of data.references.slice(i+1)) assert(!overlaps(ref.rect,other.rect), `${label}: edge refs #${ref.reference}/#${other.reference} do not overlap`);
      for (const text of data.labels.filter(t=>t.text!=='#'+ref.reference)) assert(!overlaps(ref.rect,text.rect), `${label}: edge ref #${ref.reference} does not cover ${text.text}`);
    }
    assert(data.buttons.length>=5, `${label}: visible graph and camera controls`);
    for (const b of data.buttons) {
      assert(b.font>=13&&b.height>=40&&b.fits&&b.inFrame, `${label}: readable graph button ${b.text}`);
      assert(contrast(b.color,b.background)>=4.5, `${label}: graph button text contrast: ${b.text}`);
    }
    await evaluate(`graphFrame().scrollIntoView({block:'center',behavior:'instant'})`);
    await capture(label);
  };
  const camera = async () => {
    assert.equal(await evaluate(`graphVisible(graphDocument()?.querySelector('#viewport'))`), true, 'Camera requires explicit visible Graph view; never change the default silently');
    const measure = () => evaluate(`(()=>{const s=graphDocument().querySelector('svg'),n=s.querySelector('.halo').getBoundingClientRect();return {
      width:s.getBoundingClientRect().width,height:s.getBoundingClientRect().height,scale:s.getScreenCTM().a,nodeWidth:n.width,
      label:graphDocument().querySelector('#zoom').textContent};})()`);
    const before=await measure();
    await graphClick('#in'); const bigger=await measure();
    assert(bigger.width>before.width&&bigger.height>before.height&&bigger.nodeWidth>before.nodeWidth&&bigger.scale>before.scale, 'Zoom in actually scales SVG and node geometry');
    await graphClick('#out'); const smaller=await measure();
    assert(Math.abs(smaller.scale-before.scale)<.01&&smaller.nodeWidth<bigger.nodeWidth, 'Zoom out actually restores SVG scale');
    await graphClick('#in'); await graphClick('#fit'); const fitted=await measure();
    assert(fitted.scale<bigger.scale&&fitted.nodeWidth<bigger.nodeWidth, 'Fit actually scales SVG, not just its output label');
    const fits=await evaluate(`(()=>{const d=graphDocument(),s=d.querySelector('svg').getBoundingClientRect(),v=d.querySelector('#viewport');return s.width<=v.clientWidth+1&&s.height<=v.clientHeight+1;})()`);
    assert(fits, 'Fit puts whole SVG within internal viewport');
    report.graphs.push({label:'actual camera geometry',before,bigger,smaller,fitted});
  };
  const renderedSnapshot = async () => {
    await snapshotView('Current state');
    const graph=await evaluate(`(()=>{const d=graphDocument();return {
      nodes:[...d.querySelectorAll('.node')].map(e=>e.getAttribute('aria-label')),
      edges:[...d.querySelectorAll('.edge')].map(e=>e.textContent)};})()`);
    const inspectors={};
    for (const entity of ['sources.pricing','sources.sales','facts.discount_20','facts.phone_support','summaries.customer_summary']) {
      await select('.st-key-entity_id',entity);
      inspectors[entity]=await inspected(entity);
    }
    return {header:await evaluate('headerStatus()'),graph,inspectors};
  };
  const validateEvidence = (data, verdict) => {
    assert.equal(data.status,verdict);
    assert.equal(data.comparisons.length,30);
    assert(new Set(data.comparisons.map(c=>c.path)).size===30, 'Thirty distinct executed comparisons');
    assert(data.started_at&&data.finished_at&&data.generation&&data.revision>0, 'Executed revision-bound evidence');
    comparison(data,'facts.phone_support.status','valid','valid');
    comparison(data,'facts.phone_support.active_support',['pricing'],['pricing']);
    comparison(data,'facts.discount_20.status','invalid','invalid');
    comparison(data,'summaries.customer_summary.content',phone,verdict==='PASS'?phone:staleContent);
    if(verdict==='PASS')assert(data.comparisons.every(c=>c.match)&&data.mismatches.length===0, 'All thirty comparisons pass');
    else {
      assert.deepEqual(data.mismatches.find(c=>c.path==='summaries.customer_summary.content'),
        {path:'summaries.customer_summary.content',expected:phone,actual:staleContent}, 'Exact independently reported stale-content mismatch');
      assert(data.comparisons.some(c=>!c.match), 'FAIL has executed mismatching comparisons');
    }
  };
  const lab = async (mainGeneration, label) => {
    await expand('Advanced · isolated verification lab');
    await button('Run isolated comparison');
    await nativeClick(`all('.st-key-lab_run [data-baseweb="select"]')[0]`);
    const options=await evaluate(`all('[role="option"]').map(e=>e.textContent.trim())`);
    assert(options.length>0,'Lab history options are rendered');
    await click('[role="option"]',options.at(-1));
    const scenarios=[];
    for(const [name,verdict] of [['normal','PASS'],['skip_summary','FAIL']]) {
      const scenario=await renderedJSON('Lab evidence · '+name);
      validateEvidence(scenario.verification,verdict);
      assert.equal(scenario.verdict,verdict);
      assert.equal(scenario.before.revision,0);
      assert.equal(scenario.before.generation,scenario.after.generation);
      assert.equal(scenario.after.generation,scenario.verification.generation);
      assert.notEqual(scenario.after.generation,mainGeneration, 'Lab owns a different generation than main');
      assert.equal(scenario.after.state.summaries.customer_summary.content,verdict==='PASS'?phone:staleContent);
      scenarios.push(scenario);
      await expand('Lab evidence · '+name,false);
    }
    assert.notEqual(scenarios[0].after.generation,scenarios[1].after.generation, 'Lab scenarios own separate generations');
    const identity=await evaluate(`all('[data-testid="stCaptionContainer"]').find(e=>e.textContent.includes('ISOLATED LAB ·'))?.textContent`);
    assert(identity, 'Rendered lab run identity');
    report.labs.push({label,identity,options,scenarios});
    await checkpoint(label,'Real isolated Lab normal PASS / skip_summary FAIL, exact stale-content evidence, separate generations');
    return {identity,options,scenarios};
  };
  const download = async expected => {
    phase='real evidence download';
    const start=downloadEvents.length;
    await button('Download verification evidence');
    const deadline=Date.now()+20000;
    let event,completed;
    while(Date.now()<deadline){
      event=downloadEvents.slice(start).find(e=>e.method==='Browser.downloadWillBegin');
      completed=event&&downloadEvents.slice(start).find(e=>e.guid===event.guid&&e.state==='completed');
      if(completed)break;
      await sleep(150);
    }
    assert(completed, 'Harness CDP download must complete after actual button click');
    assert.equal(event.suggestedFilename,'fallout-verification.json');
    assert(/^[a-zA-Z0-9-]+$/.test(event.guid),'Safe Chrome generated download ID');
    const bytes=await readFile(resolve(runDirectory,event.guid));
    const data=JSON.parse(bytes.toString('utf8'));
    assert.deepEqual(data.verification,expected,'Downloaded bytes equal rendered verifier report');
    assert.equal(typeof data.verification_id,'string');
    assert.deepEqual(Object.keys(data).sort(),['scope','verification','verification_id'], 'Export only contains synthetic verification evidence');
    report.downloads.push({guid:event.guid,filename:event.suggestedFilename,bytes:bytes.length,data});
  };

  const documentation = {
    id: 'documentation', label: 'Technical documentation withdrawal', source: 'obsolete_bulletin',
    preserved: 'cursor_pagination', support: ['api_reference'], removed: 'legacy_query_tokens',
    summaries: {
      api_overview: {before: 'Cursor pagination is supported.\nLegacy query-string access tokens are accepted.\nThe API allows 100 requests per minute.',
        after: 'Cursor pagination is supported.\nThe API allows 100 requests per minute.', members: ['cursor_pagination', 'rate_limit']},
      migration_notes: {before: 'Cursor pagination is supported.\nLegacy query-string access tokens are accepted.',
        after: 'Cursor pagination is supported.', members: ['cursor_pagination']},
      installation_summary: {before: 'Installation requires Python 3.11 or later.',
        after: 'Installation requires Python 3.11 or later.', members: ['python_requirement']},
    }, skipped: 'api_overview', unaffected: 'installation_summary',
  };
  const pricing = {id: 'pricing', label: 'Pricing withdrawal', source: 'sales',
    preserved: 'phone_support', support: ['pricing'], removed: 'discount_20',
    summaries: {customer_summary: {before: staleContent, after: phone, members: ['phone_support']}},
    skipped: 'customer_summary'};
  const completeEvidence = (data, verdict, config, falseSuccess = false) => {
    const count = data.comparisons.length;
    assert(count > 0 && data.complete === true && data.report_version === 2, 'Complete executed report');
    if (config.id === 'pricing') assert.equal(count, 30, 'Pricing retains 30 checks');
    assert.deepEqual(data.comparisons.map(c=>c.path).sort(), [...data.required_paths].sort());
    assert.equal(new Set(data.required_paths).size, count);
    assert(data.generation && data.revision > 0 && Date.parse(data.finished_at) >= Date.parse(data.started_at));
    assert.equal(data.status, verdict);
    const failures = [];
    for (const {path, expected, actual, match} of data.comparisons) {
      const equal = JSON.stringify(expected) === JSON.stringify(actual);
      assert.equal(match, equal, `${path}: truthful match flag`);
      if (!equal) failures.push({path, expected, actual});
    }
    assert.deepEqual(data.mismatches, failures, 'Exact mismatch ledger');
    assert.equal(failures.length === 0, verdict === 'PASS');
    comparison(data, `sources.${config.source}.status`, 'revoked', 'revoked');
    comparison(data, `facts.${config.preserved}.status`, 'valid', 'valid');
    comparison(data, `facts.${config.preserved}.active_support`, config.support, config.support);
    comparison(data, `facts.${config.removed}.status`, 'invalid', 'invalid');
    comparison(data, 'operations.incomplete', [], []);
    for (const [id, summary] of Object.entries(config.summaries)) {
      const skipped = falseSuccess && id === config.skipped;
      comparison(data, `summaries.${id}.content`, summary.after, skipped ? summary.before : summary.after);
      if (!skipped) comparison(data, `summaries.${id}.members`, summary.members, summary.members);
      comparison(data, `summaries.${id}.status`, 'fresh', skipped ? 'stale' : 'fresh');
    }
    if (falseSuccess) {
      assert(failures.some(c=>c.path === `summaries.${config.skipped}.content`), 'False completion defeated by actual content mismatch');
      assert(failures.every(c=>c.path.startsWith(`summaries.${config.skipped}.`)), 'Only the skipped summary mismatches, not operations.incomplete');
    }
  };
  const operationEvidence = async (config, pending) => {
    const operation = await renderedJSON('Operation detail');
    assert.equal(operation.status, 'repaired', 'Recorded completion claim is repaired');
    assert.equal(operation.source_id, config.source);
    assert.equal(operation.false_success_pending, pending);
    assert.equal(operation.skipped_summary, pending ? config.skipped : null);
    if (pending) assert(['SKIPPED', 'PARTIAL'].includes(operation.steps.rebuild_summaries.status));
    else assert(Object.values(operation.steps).every(step=>step.status === 'COMPLETED'));
    await expand('Operation detail', false);
    return operation;
  };
  const mismatchContent = async (config, evidence, label) => {
    const path = `summaries.${config.skipped}.content`, summary = config.summaries[config.skipped];
    comparison(evidence, path, summary.after, summary.before);
    const primaryMismatch = await evaluate(`(()=>{const e=all('[class*="st-key-content_mismatch_"]').find(e=>
      !e.closest('details')&&e.classList.contains(${quote('st-key-content_mismatch_' + path.replaceAll('.', '-'))}));return e?{
        captions:[...e.querySelectorAll('[data-testid="stCaptionContainer"]')].filter(visible).map(n=>n.innerText.trim()),
        values:[...e.querySelectorAll('[data-testid="stText"]')].filter(visible).map(n=>n.textContent)}:null;})()`);
    assert(primaryMismatch, `${label}: exact mismatch is primary, outside collapsed report and IDs`);
    assert(primaryMismatch.captions.includes('STALE CONTENT / ' + path));
    assert(primaryMismatch.captions.includes('EXPECTED / independent reference') && primaryMismatch.captions.includes('ACTUAL / recorded memory'));
    assert.deepEqual(primaryMismatch.values, [summary.after, summary.before], `${label}: exact primary reference and saved content`);
    await select('.st-key-comparison_path', path);
    await expect(`visibleText().includes('EXPECTED / clean reference') && visibleText().includes('ACTUAL / recorded memory')`, `${label}: expected/actual inspector labels`);
    await evaluate(`(()=>{const e=all('[data-testid="stText"]').find(e=>!e.closest('details')&&e.textContent===${quote(summary.before)});
      e.scrollIntoView({block:'center',behavior:'instant'});})()`);
    await capture(label);
    return {path, expected:summary.after, actual:summary.before};
  };
  const falseSuccessLab = async (config, mainGeneration, label) => {
    await expand('Advanced · isolated verification lab');
    await button('Run false-success proof');
    const scenarios = [];
    for (const name of ['normal', 'false_success']) {
      const data = await renderedJSON('Lab evidence · ' + name);
      const failed = name === 'false_success';
      assert.equal(data.scenario_id, config.id);
      completeEvidence(data.verification, failed ? 'FAIL' : 'PASS', config, failed);
      assert.equal(data.verdict, failed ? 'FAIL' : 'PASS');
      assert.equal(data.before.revision, 0);
      assert.equal(data.before.generation, data.after.generation);
      assert.equal(data.after.generation, data.verification.generation);
      assert.notEqual(data.after.generation, mainGeneration);
      assert.equal(data.operation.status, 'repaired');
      for (const [id, summary] of Object.entries(config.summaries)) {
        assert.equal(data.before.state.summaries[id].content, summary.before);
        assert.equal(data.after.state.summaries[id].content, failed && id === config.skipped ? summary.before : summary.after);
      }
      if (config.unaffected) assert.deepEqual(data.before.state.summaries[config.unaffected], data.after.state.summaries[config.unaffected], 'Entire unrelated lab summary unchanged, including builds');
      if (failed) {
        assert.equal(data.operation.false_success_pending, true);
        assert.equal(data.operation.skipped_summary, config.skipped);
        const recovery = data.recovery;
        assert.equal(recovery.operation_id, data.operation.id, 'Recovery retries same isolated operation');
        assert.equal(recovery.operation.id, data.operation.id);
        assert.equal(recovery.operation.false_success_pending, false);
        assert.equal(recovery.operation.skipped_summary, null);
        assert.equal(recovery.operation.attempts, data.operation.attempts + 1);
        assert.deepEqual(recovery.before, data.after, 'Failure snapshot retained before recovery');
        assert.equal(recovery.after.generation, data.after.generation);
        assert(recovery.after.revision > data.after.revision);
        assert.notEqual(recovery.verification_id, data.verification_id);
        completeEvidence(recovery.verification, 'PASS', config);
        assert.equal(recovery.verdict, 'PASS');
        if (config.unaffected) assert.deepEqual(recovery.before.state.summaries[config.unaffected], recovery.after.state.summaries[config.unaffected]);
      }
      scenarios.push(data);
      await expand('Lab evidence · ' + name, false);
    }
    assert.notEqual(scenarios[0].after.generation, scenarios[1].after.generation);
    await expect(`visibleText().includes('Recovery · PASS') && visibleText().includes('Stale-content diagnosis:')`, 'Visible lab diagnosis and separately verified recovery');
    const identity = await evaluate(`all('[data-testid="stCaptionContainer"]').find(e=>e.textContent.includes('ISOLATED LAB ·'))?.textContent`);
    report.labs.push({label, identity, scenarios});
    await checkpoint(label, `${config.id}: isolated normal PASS, false-success FAIL with operations.incomplete matching; same-operation recovery PASS`);
    return scenarios;
  };
  const reset = async () => {
    await expand('Start over · reset the local demo');
    await button('Preview reset');
    await expect(`all('.st-key-confirm_reset button').some(e=>e.disabled)`, 'reset requires fresh acknowledgment');
    await nativeClick(`all('.st-key-reset_ack label')[0]`);
    await button('Confirm in-memory reset');
    await result('NOT RUN');
  };
  const documentationGraph = async (label, repaired, pending = false) => {
    await graphReady();
    const data = await evaluate(`(()=>{const d=graphDocument();return {
      nodes:[...d.querySelectorAll('.node')].map(e=>({entity:e.dataset.entity,label:e.getAttribute('aria-label')})),
      edges:[...d.querySelectorAll('.edge')].map(e=>({title:e.querySelector('title').textContent,active:e.dataset.active==='true',dash:e.querySelector('path').getAttribute('stroke-dasharray')}))};})()`);
    const statuses = {'sources.obsolete_bulletin': repaired ? 'revoked' : 'active', 'sources.api_reference': 'active',
      'sources.installation_guide': 'active', 'facts.cursor_pagination': 'valid', 'facts.legacy_query_tokens': repaired ? 'invalid' : 'valid',
      'facts.rate_limit': 'valid', 'facts.python_requirement': 'valid', 'summaries.api_overview': pending ? 'stale' : 'fresh',
      'summaries.migration_notes': 'fresh', 'summaries.installation_summary': 'fresh'};
    assert.deepEqual(data.nodes.map(n=>n.entity).sort(), Object.keys(statuses).sort());
    for (const node of data.nodes) assert(node.label.toLowerCase().includes(statuses[node.entity]), `${label}: ${node.entity} ${statuses[node.entity]}`);
    const pairs = [
      ['sources.obsolete_bulletin','facts.cursor_pagination',!repaired], ['sources.obsolete_bulletin','facts.legacy_query_tokens',!repaired],
      ['sources.api_reference','facts.cursor_pagination',true], ['sources.api_reference','facts.rate_limit',true],
      ['sources.installation_guide','facts.python_requirement',true], ['facts.cursor_pagination','summaries.api_overview',true],
      ['facts.legacy_query_tokens','summaries.api_overview',!repaired], ['facts.rate_limit','summaries.api_overview',true],
      ['facts.cursor_pagination','summaries.migration_notes',true], ['facts.legacy_query_tokens','summaries.migration_notes',!repaired],
      ['facts.python_requirement','summaries.installation_summary',true],
    ];
    assert.equal(data.edges.length, pairs.length);
    for (const [from,to,active] of pairs) {
      const edge = data.edges.find(e=>e.title.startsWith(`${from} → ${to}\n`));
      assert(edge, `${label}: retained ${from} → ${to}`);
      assert.equal(edge.active, active);
      assert.equal(edge.dash, active ? 'none' : '6 4');
    }
    report.graphs.push({label,...data});
  };
  const unrelatedEvidence = async label => {
    const title = 'Repair details · facts, lineage and execution';
    const container = `all('summary').find(e=>e.textContent.includes(${quote(title)})).parentElement`;
    const wasOpen = await evaluate(`(${container}).open`);
    await expand(title);
    await expect(`(${container}).innerText.includes('Unaffected summaries · recorded before / after evidence') &&
      (${container}).innerText.includes('Summary ID: installation_summary · UNCHANGED')`, 'Expanded repair details retain unrelated summary evidence (not primary)');
    const evidence = await renderedJSON(null, `[...(${container}).querySelectorAll('[data-testid="stJson"]')].find(e=>visible(e) && e.textContent.includes('installation_summary')).parentElement`);
    assert.equal(evidence.summary_id, documentation.unaffected);
    assert.equal(evidence.unchanged, true);
    assert.deepEqual(evidence.before, evidence.after, 'Unrelated summary entire before/after record unchanged');
    assert.equal(evidence.before.content, documentation.summaries.installation_summary.before);
    assert.equal(evidence.before.build_count, 0, 'Unrelated summary was not rebuilt');
    report.workflows.push(`${label}: expanded repair details show unrelated summary unchanged, exact full before/after records including build count; not primary evidence`);
    if (!wasOpen) await expand(title, false);
    return evidence;
  };
  const documentationSnapshot = async () => {
    await snapshotView('Current state');
    const inspectors = {};
    for (const entity of ['sources.obsolete_bulletin','sources.api_reference','sources.installation_guide','facts.cursor_pagination',
      'facts.legacy_query_tokens','facts.rate_limit','facts.python_requirement','summaries.api_overview','summaries.migration_notes','summaries.installation_summary']) {
      await select('.st-key-entity_id', entity);
      inspectors[entity] = await inspected(entity);
    }
    return {header: await evaluate('headerStatus()'), inspectors};
  };
  const documentationPreview = async label => {
    await button('Preview changes');
    await stageBanners('preview', documentation);
    await previewHighlight(documentation, true);
    await expect(`primary().includes('Preview only — nothing has changed.')`, 'Documentation preview not execution');
    const preview = await renderedJSON('Advanced details · exact preview');
    assert.equal(preview.source_id, documentation.source);
    assert.deepEqual(preview.affected_facts, ['cursor_pagination','legacy_query_tokens']);
    assert.deepEqual(preview.affected_summaries, ['api_overview','migration_notes']);
    assert.deepEqual(preview.remaining_support.cursor_pagination, ['api_reference']);
    assert.deepEqual(preview.invalidated_facts, ['legacy_query_tokens']);
    await expand('Advanced details · exact preview', false);
    await expect(`headerStatus()[0].includes('revision 0') && headerStatus()[0].includes('NOT RUN')`, 'Documentation preview keeps revision zero');
    await checkpoint(label, 'Documentation real preview: independently supported cursor pagination preserved, legacy tokens invalidated, two affected summaries only');
    return preview;
  };

  const stageBanners = async (state, config = pricing, operation = null, earlier = null) => {
    await settle();
    const data = await evaluate(`({banners:all('.fo-stage').map(e=>({title:e.querySelector('b')?.textContent,
      detail:e.querySelector('span')?.textContent,tone:e.className,role:e.getAttribute('role'),advanced:!!e.closest('details')})),
      steps:all('.fo-workflow li').map(e=>({label:[...e.childNodes].filter(n=>n.nodeType===Node.TEXT_NODE).map(n=>n.textContent).join('').trim(),
        done:e.classList.contains('done'),current:e.getAttribute('aria-current')==='step'})),
      statuses:all('.fo-status').filter(e=>!e.closest('details')&&!e.closest('.st-key-workbench_header')).map(e=>e.innerText),
      header:headerStatus(),revision:all('.st-key-workbench_header .fo-status')[0]?.dataset.revision})`);
    const previewing = state === 'preview', executed = ['normal','injected','recovery'].includes(state), recovered = state === 'recovery';
    const titles = previewing ? ['PREVIEW · NOTHING CHANGED'] : executed ? ['REPAIR EXECUTED · reported complete',
      ...(state === 'injected' ? ['FAILURE INJECTED · repair skipped a rebuild'] : []),
      ...(recovered ? ['RECOVERY · missed work repaired','EARLIER VERIFICATION · FAIL'] : [])] : [];
    assert.deepEqual(data.banners.map(b=>b.title), titles, `${state}: only truthful primary stage banners`);
    assert(data.banners.every(b=>!b.advanced), `${state}: banners are primary evidence`);
    assert.deepEqual(data.steps.map(s=>s.label), ['Impact','Repair','Verify','Recover']);
    const done = executed ? [true,true,true,recovered] : [previewing,false,false,false];
    assert.deepEqual(data.steps.map(s=>s.done), done, `${state}: progress derived from recorded actions, not armed fault choice`);
    assert.deepEqual(data.steps.map((s,i)=>s.current?i:-1).filter(i=>i>=0), executed ? (state==='injected'?[3]:[]) : [previewing?1:0]);
    if (!executed) {
      assert.equal(await evaluate(`all('.fo-memory-line.stale').filter(e=>!e.closest('details')).length`), 0,
        `${state}: no false Left stale label before current independent evidence, including an armed fault`);
      assert.equal(await evaluate(`all('[class*="st-key-content_mismatch_"]').filter(e=>!e.closest('details')).length`), 0,
        `${state}: no fabricated primary content mismatch before verification`);
    }
    assert.equal(data.header.length, 1, `${state}: single compact primary verifier`);
    assert(/^(0|[1-9][0-9]*)$/.test(data.revision), `${state}: explicit revision attribute required`);
    const verdict = executed ? state==='injected'?'FAIL':'PASS' : 'NOT RUN';
    assert(data.header[0].includes('VERIFIER / '+verdict));
    assert.equal(data.statuses.length, previewing||executed?1:0, `${state}: separate execution-area verifier strip`);
    if (data.statuses.length) {
      assert(data.statuses[0].includes('VERIFIER / '+verdict), `${state}: execution does not replace independent verdict`);
      assert(new RegExp('revision '+data.revision+'\\b').test(data.statuses[0]), `${state}: execution-area verifier uses the same current revision`);
    }
    if (previewing) {
      assert.equal(data.banners[0].detail, 'Impact only. Explicit approval is still required.');
      assert(data.banners[0].tone.includes('pending'));
    }
    if (executed) {
      const completed = operation ? Object.values(operation.steps).filter(s=>s.status==='COMPLETED').length : state==='injected'?2:3;
      const total = operation ? Object.keys(operation.steps).length : 3;
      const attempt = operation?.attempts ?? (recovered?2:1);
      assert.equal(data.banners[0].detail, `${completed} of ${total} steps completed in attempt ${attempt}. Execution is not verification.`);
      assert(data.banners[0].tone.includes('neutral'));
    }
    if (state === 'injected') {
      assert.equal(data.banners[1].detail, `Skipped summary: ${config.skipped}. See the independent result separately.`);
      assert(data.banners[1].tone.includes('fail'));
    }
    if (recovered) {
      assert(operation && earlier, 'Recovery banner must be checked against rendered operation and earlier FAIL report');
      assert.equal(data.banners[1].detail, `Same operation ID · ${operation.id}. Verification remains a separate claim.`);
      assert.equal(data.banners[2].detail, `Revision ${earlier.revision} · ${earlier.mismatches.length} mismatches. Preserved in session history; not the current verdict.`);
      assert(data.banners[2].tone.includes('historical'));
    }
    report.stages.push({scenario:config.id,state,...data});
    report.workflows.push(`${config.id}: ${state} stage banners and revision-bound verdict checked separately`);
  };

  // Saved-text evidence stays primary; legacy facts and IDs are not substitutes.
  // Only the injected summary may be labelled stale, and its reference must
  // agree with the current revision's rendered independent report when supplied.
  const memoryEvidence = async (config, verdict, evidence = null) => {
    const data = await evaluate(`({revision:all('.st-key-workbench_header .fo-status')[0]?.dataset.revision,
      header:headerStatus(),groups:all('.fo-memory-lines').filter(e=>!e.closest('details')).map(e=>({
        side:e.closest('[data-testid="stColumn"]')?.querySelector('[data-testid="stCaptionContainer"]')?.innerText.trim(),
        parts:[...e.querySelectorAll('.fo-memory-line')].filter(visible).map(line=>({
          kind:[...line.classList].find(c=>c!=='fo-memory-line'),
          label:line.querySelector('.fo-diff-label')?.textContent,
          text:line.querySelector('.fo-diff-text')?.textContent}))}))})`);
    assert.equal(data.header.length, 1, 'Memory markings have one current verifier header');
    assert(data.header[0].includes('VERIFIER / ' + verdict), 'Memory markings agree with current verdict');
    if (evidence) {
      assert.equal(String(evidence.revision), data.revision, 'Stale reference is bound to the displayed current revision');
      assert.equal(evidence.status, verdict);
    }
    const summaries = Object.entries(config.summaries).sort(([a],[b])=>a.localeCompare(b));
    assert.equal(data.groups.length, summaries.length * 2, 'Every saved summary has primary BEFORE and NOW diff groups');
    const labels = {removed:'Removed',retained:'Retained',added:'Added',stale:'Left stale',current:'Recorded'};
    for (const [index, [id, summary]] of summaries.entries()) {
      const before = data.groups[index * 2], now = data.groups[index * 2 + 1];
      const failed = verdict === 'FAIL' && id === config.skipped;
      assert.equal(before.side, 'BEFORE / saved before withdrawal');
      assert.equal(now.side, 'NOW / actual recorded memory');
      assert.equal(before.parts.map(p=>p.text).join(''), summary.before, `${id}: lossless primary saved-before text`);
      assert.equal(now.parts.map(p=>p.text).join(''), failed ? summary.before : summary.after, `${id}: lossless primary actual text`);
      for (const part of [...before.parts,...now.parts]) {
        assert(Object.hasOwn(labels, part.kind), `${id}: known diff category`);
        assert.equal(part.label, labels[part.kind], `${id}: explicit semantic diff label`);
        assert(part.text?.length, `${id}: nonempty exact diff text`);
      }
      assert(before.parts.every(p=>!['stale','added'].includes(p.kind)), `${id}: earlier snapshot is not falsely stale or added`);
      assert(now.parts.every(p=>p.kind!=='removed'), `${id}: removed text never presented as current memory`);
      assert.equal(now.parts.some(p=>p.kind==='stale'), failed, `${id}: stale marking only for the currently mismatching summary`);
      // The diff is line-based and lossless: losing a final newline can mark
      // otherwise identical wording Added rather than Retained.
      const lines = text => text.match(/[^\n]*\n|[^\n]+$/g) || [];
      if (lines(summary.before).some(line=>lines(summary.after).includes(line))) {
        assert(now.parts.some(p=>p.kind==='retained'), `${id}: unchanged complete lines remain retained`);
      }
      if (failed) {
        const extra = lines(summary.before).filter(line=>!lines(summary.after).includes(line)).join('');
        assert.equal(now.parts.filter(p=>p.kind==='stale').map(p=>p.text).join(''), extra,
          `${id}: only unsupported actual lines are marked Left stale`);
      }
      if (!failed && summary.before !== summary.after) assert(before.parts.some(p=>p.kind==='removed'), `${id}: withdrawn text marked removed in BEFORE`);
      if (evidence) {
        comparison(evidence, `summaries.${id}.content`, summary.after, failed ? summary.before : summary.after);
        const mismatch = evidence.mismatches.find(c=>c.path===`summaries.${id}.content`);
        assert.equal(!!mismatch, failed, `${id}: Left stale requires a current content mismatch, not just status`);
        if (mismatch) assert.deepEqual(mismatch, {path:`summaries.${id}.content`,expected:summary.after,actual:summary.before});
      }
    }
    report.layouts.push({label:`${config.id}: primary saved-memory diff ${verdict}`,reportBound:!!evidence,...data});
  };

  // Shared by story, layout, visual-only and full workflows.
  const visualStyles = async label => {
      phase = `${label}: brand, header contrast and dark graph`;
      const data = await evaluate(`(()=>{
        const brand=all('.fo-brand')[0],logo=brand?.querySelector('.fo-logo[role="img"],img.fo-logo');
        const heading=brand?.querySelector('h1'),wordmark=heading?.parentElement;
        if(!visible(logo)||!visible(heading)||!visible(wordmark)||wordmark.tagName!=='DIV')return null;
        const a=logo.getBoundingClientRect(),b=wordmark.getBoundingClientRect();
        const status=all('.st-key-workbench_header .fo-status')[0];
        const statusColor=status?.classList.contains('pass')?'rgb(127, 209, 160)':status?.classList.contains('fail')?'rgb(255, 122, 107)':
          status?.classList.contains('wait')?'rgb(240, 179, 74)':'rgb(232, 237, 233)';
        const tokens=[
          ['instrument','.st-key-instrument','backgroundColor','rgb(23, 28, 26)'],
          ['mast','.fo-mast','backgroundColor','rgb(23, 28, 26)'],
          ['wordmark','.fo-brand h1','color','rgb(232, 237, 233)'],
          ['state','.fo-header-state','color','rgb(169, 181, 174)'],
          ['status background','.st-key-workbench_header .fo-status','backgroundColor','rgb(23, 28, 26)'],
          ['current status','.st-key-workbench_header .fo-status b','color',statusColor],
          // Paragraph color, not the radio label's inherited color, is painted.
          ['selected navigation','.st-key-section label:has(input:checked) p','color','rgb(232, 237, 233)'],
          ['selected navigation background','.st-key-section label:has(input:checked)','backgroundColor','rgb(45, 57, 50)'],
          ['unselected navigation','.st-key-section label:has(input:not(:checked)) p','color','rgb(169, 181, 174)']
        ].map(([name,selector,property,expected])=>{const e=all(selector)[0];return {
          name,expected,actual:e?getComputedStyle(e)[property]:null};});
        const contrastSelectors=['.fo-brand h1',
          '.fo-scenario-label','.fo-header-comparisons','.st-key-scenario_bar [data-baseweb="select"] [value]',
          '.fo-snapshot-note b','.fo-snapshot-note span',
          '.fo-header-state','.fo-status b','.fo-status span','.st-key-section label p',
          '.fo-intro h2','.fo-intro p','.fo-counts','.fo-counts small',
          '.st-key-entity_inspector .fo-id',
          '.fo-stage b','.fo-stage span','.fo-workflow li','.fo-workflow li span','.fo-section',
          '.fo-impact b','.fo-impact span','.fo-verification strong','.fo-verification span','.fo-verification small',
          '.fo-memory-line .fo-diff-label','.fo-memory-line .fo-diff-text',
          '.st-key-instrument [data-testid="stAlert"] p','.st-key-instrument [data-testid="stWidgetLabel"] p',
          '.st-key-instrument button p','.st-key-instrument [data-testid="stMarkdownContainer"] p',
          '.fo-identifiers li',
          '[data-testid="stMainBlockContainer"] [data-testid="stCaptionContainer"] p',
          '[data-testid="stMainBlockContainer"] [data-testid="stHeading"] h3'];
        // Wrapper colors can pass while a nested p/pre/span paints unreadable
        // text. Sample each element owning a real text node, including select
        // values and the footer, against its actual inherited surface.
        const paintedRoots=[
          '[data-testid="stMainBlockContainer"] [data-testid="stText"]',
          '.fo-privacy-note', '[data-testid="stMainBlockContainer"] [data-baseweb="select"]'];
        const painted=paintedRoots.flatMap(selector=>all(selector).flatMap(root=>[root,...root.querySelectorAll('*')]
          .filter(e=>visible(e)&&!["STYLE","SCRIPT"].includes(e.tagName)&&
            [...e.childNodes].some(n=>n.nodeType===Node.TEXT_NODE&&n.textContent.trim()))
          .map(e=>({selector:selector+' / painted '+e.tagName.toLowerCase(),e}))));
        const contrasts=[...contrastSelectors.flatMap(selector=>all(selector).map(e=>({selector,e}))),...painted].map(({selector,e})=>{
          let p=e,background='rgba(0, 0, 0, 0)';
          while(p){background=getComputedStyle(p).backgroundColor;if(background!=='rgba(0, 0, 0, 0)')break;p=p.parentElement;}
          const minimum=e.matches('.fo-status b,.fo-status span,.fo-header-state,.fo-stage b,.fo-workflow li span,.fo-section,.fo-impact b,.fo-scenario-label,.fo-header-comparisons,.fo-diff-label')?12:
            e.matches('.fo-snapshot-note b,.fo-snapshot-note span')||e.closest('.fo-privacy-note')?13:
            e.closest('[data-testid="stCaptionContainer"], [data-testid="stWidgetLabel"], .st-key-section')||e.matches('.fo-stage span,.fo-counts small,.fo-workflow li,.fo-verification small,.fo-identifiers li,.fo-impact span')?13:15;
          const style=getComputedStyle(e),r=e.getBoundingClientRect(),range=document.createRange();range.selectNodeContents(e);
          return {selector,text:e.textContent,color:style.color,background,font:parseFloat(style.fontSize),minimum,
            diffKind:e.closest('.fo-memory-line')?.className,decoration:style.textDecorationLine,
            decorationColor:style.textDecorationColor,
            textFits:[...range.getClientRects()].every(t=>t.left>=r.left-1&&t.right<=r.right+1&&t.top>=r.top-1&&t.bottom<=r.bottom+1)};
        });
        const selects=all('[data-testid="stMainBlockContainer"] [data-baseweb="select"]').map(e=>{
          const surface=e.querySelector(':scope > div')||e;
          let p=surface,background='rgba(0, 0, 0, 0)';
          while(p){background=getComputedStyle(p).backgroundColor;if(background!=='rgba(0, 0, 0, 0)')break;p=p.parentElement;}
          return {text:e.innerText,background};
        });
        const d=graphDocument();
        const controls=['.fo-brand','.st-key-section [role="radiogroup"]','.st-key-scenario_bar [data-baseweb="select"]','.st-key-workbench_header .fo-status'];
        const scenario=all('.st-key-scenario_bar [data-baseweb="select"] [value]')[0];
        const range=document.createRange();range.selectNodeContents(scenario);
        const sr=scenario.getBoundingClientRect(),tr=range.getBoundingClientRect();
        return {label:${quote(label)},width:innerWidth,revision:status?.dataset.revision,compactText:status?.innerText,
          pageWidth:document.documentElement.scrollWidth,mainWidth:document.querySelector('[data-testid="stMain"]').scrollWidth,
          containerWidth:document.querySelector('[data-testid="stMainBlockContainer"]').getBoundingClientRect().width,
          availableWidth:document.querySelector('[data-testid="stMain"]').clientWidth,
          scenario:{text:scenario.textContent,fits:tr.left>=sr.left-1&&tr.right<=sr.right+1},
          panels:['.st-key-recorded_memory','.st-key-recent_operation','.st-key-graph_panel','.st-key-entity_inspector','.st-key-try_demo button'].map(selector=>{
            const e=all(selector)[0],r=e?.getBoundingClientRect();return {selector,visible:!!e,left:r?.left,right:r?.right,top:r?.top,bottom:r?.bottom};}),
          obsoleteBrand:brand.querySelectorAll('h1 small,:scope > div > span').length,
          headerControls:controls.map(selector=>{const e=all(selector)[0];
            // The verifier pill and AI/environment metadata are siblings in
            // one header group. Measure the group, not the now-smaller pill.
            const box=e===status&&e?.parentElement.querySelector(':scope > .fo-header-state')?e.parentElement:e;
            const r=box?.getBoundingClientRect();return {selector,visible:!!e,
            left:r?.left,right:r?.right,top:r?.top,bottom:r?.bottom};}),
          logo:{role:logo.getAttribute('role'),tag:logo.tagName,name:logo.getAttribute('aria-label')||logo.getAttribute('alt'),loaded:logo.tagName!=='IMG'||(logo.complete&&logo.naturalWidth>0),
            width:a.width,height:a.height,centerY:a.y+a.height/2},
          wordmark:{text:heading.textContent,width:b.width,height:b.height,centerY:b.y+b.height/2},tokens,contrasts,selects,
          paintedTextRoots:all('[data-testid="stMainBlockContainer"] [data-testid="stText"]').filter(e=>e.textContent.trim()).length,
          diffLines:all('.fo-memory-line').filter(e=>!e.closest('details')).map(e=>({kind:[...e.classList].find(c=>c!=='fo-memory-line'),
            border:getComputedStyle(e).borderLeftColor,background:getComputedStyle(e).backgroundColor})),
          privacy:all('.fo-privacy-note').map(e=>e.textContent),
          graphBackgrounds:d?['html','body','#viewport','svg'].map(selector=>{
            const e=d?.querySelector(selector);return {selector,color:e?d.defaultView.getComputedStyle(e).backgroundColor:null};
          }):[]};})()`);
      assert(data, `${label}: visible brand logo with img role and wordmark parent div exist`);
      report.layouts.push(data);
      assert(data.logo.name && data.logo.width > 0 && data.logo.height > 0, `${label}: accessible visible brand icon`);
      assert(data.logo.loaded, `${label}: logo image actually decoded`);
      assert.equal(data.logo.width, 28, `${label}: compact 28px logo width`);
      assert.equal(data.logo.height, 28, `${label}: compact 28px logo height`);
      assert.equal(data.obsoleteBrand, 0, `${label}: no version or brand subline`);
      assert(/^(0|[1-9][0-9]*)$/.test(data.revision), `${label}: mandatory numeric data-revision on compact header status`);
      assert(!/revision/i.test(data.compactText), `${label}: compact header does not render revision text`);
      assert(data.wordmark.text.includes('FALLOUT') && data.wordmark.width > 0 && data.wordmark.height > 0, `${label}: visible FALLOUT wordmark`);
      assert(Math.abs(data.logo.centerY - data.wordmark.centerY) <= 8, `${label}: icon and wordmark parent vertical centers aligned within 8px`);
      for (const token of data.tokens) assert.equal(token.actual, token.expected, `${label}: header contrast token ${token.name}`);
      assert(data.contrasts.length >= 8, `${label}: all header text and navigation contrast samples exist`);
      for (const sample of data.contrasts) {
        sample.ratio = contrast(sample.color, sample.background);
        assert(sample.ratio >= 4.5, `${label}: readable text contrast for ${sample.selector}: ${sample.ratio}`);
        assert(sample.font >= sample.minimum, `${label}: minimum ${sample.minimum}px text for ${sample.selector}: ${sample.font}`);
      }
      if (data.paintedTextRoots) assert(data.contrasts.some(s=>s.selector.includes('[data-testid="stText"] / painted ')), `${label}: actual inner stText paint sampled, not only wrappers`);
      const diffTokens = {removed:'rgb(240, 179, 74)',retained:'rgb(127, 209, 160)',added:'rgb(127, 209, 160)',stale:'rgb(255, 122, 107)',current:'rgb(101, 120, 107)'};
      const diffSamples = data.contrasts.filter(s=>s.diffKind);
      assert(diffSamples.length >= data.diffLines.length * 2, `${label}: actual diff label and text paint sampled for every primary line`);
      for (const line of data.diffLines) {
        assert(Object.hasOwn(diffTokens, line.kind), `${label}: known diff paint category`);
        assert.equal(line.border, diffTokens[line.kind], `${label}: ${line.kind} border paint`);
        assert.equal(line.background, line.kind==='stale'?'rgb(48, 37, 34)':'rgb(32, 40, 36)', `${label}: ${line.kind} surface paint`);
      }
      for (const sample of diffSamples) {
        const kind=sample.diffKind.split(' ').find(c=>c!=='fo-memory-line');
        assert(sample.textFits, `${label}: diff text wraps without clipping`);
        if (sample.selector.endsWith('.fo-diff-label')) {
          assert.equal(sample.color, ['removed','stale'].includes(kind)?diffTokens[kind]:'rgb(169, 181, 174)', `${label}: ${kind} label paint`);
        } else {
          assert.equal(sample.color, 'rgb(232, 237, 233)', `${label}: readable saved diff text paint`);
          assert.equal(sample.decoration.includes('line-through'), kind==='removed', `${label}: only removed saved text is struck through`);
          if (kind==='removed') assert.equal(sample.decorationColor, diffTokens.removed, `${label}: removed strike paint`);
        }
      }
      if (data.revision==='0' || !data.compactText.includes('VERIFIER / FAIL')) {
        assert(data.diffLines.every(line=>line.kind!=='stale'), `${label}: no Left stale paint without a current independent FAIL`);
      }
      assert(data.privacy.length>0&&data.privacy.every(text=>text.trim()), `${label}: visible footer privacy notice`);
      assert(data.contrasts.some(s=>s.selector.startsWith('.fo-privacy-note / painted ')), `${label}: actual privacy text contrast sampled`);
      assert(data.selects.length>0, `${label}: visible dark select surfaces sampled`);
      for (const select of data.selects) assert(luminance(select.background)<.1, `${label}: select is dark, not a light default: ${select.text}`);
      assert(data.contrasts.some(s=>s.selector.includes('[data-baseweb="select"] / painted ')), `${label}: actual selected-value contrast sampled`);
      for (const background of data.graphBackgrounds) {
        assert.equal(background.color, 'rgb(23, 28, 26)', `${label}: computed dark graph ${background.selector}`);
      }
      assert(data.headerControls.every(c=>c.visible&&c.left>=0&&c.right<=data.width), `${label}: visible compact header controls without horizontal clipping`);
      assert(data.pageWidth<=data.width&&data.mainWidth<=data.width, `${label}: page and main have no horizontal overflow`);
      assert(Math.abs(data.containerWidth-data.availableWidth)<=1, `${label}: content uses the full available device width`);
      assert(data.scenario.fits, `${label}: complete scenario label fits without truncation: ${data.scenario.text}`);
      const aligned=(a,b)=>Math.abs((a.top+a.bottom-b.top-b.bottom)/2)<=1;
      const [brand,nav,scenarioControl,verdict]=data.headerControls;
      assert(aligned(brand,verdict), `${label}: brand and verifier share an exact center line`);
      for (const c of data.headerControls) assert(Math.abs(c.bottom-c.top-44)<=1, `${label}: shared 44px header control height: ${c.selector}`);
      // CSS deliberately switches the header to a grid at <=1100px.
      if (data.width > 1100) {
        assert(aligned(brand,nav)&&aligned(nav,scenarioControl), `${label}: scenario and navigation share the brand/verifier center line`);
        assert(Math.max(...data.headerControls.map(c=>c.top)) < Math.min(...data.headerControls.map(c=>c.bottom)), `${label}: desktop brand, navigation, scenario and verifier share one row`);
        for(let i=1;i<data.headerControls.length;i++) assert(data.headerControls[i-1].right<=data.headerControls[i].left+1, `${label}: desktop header controls do not overlap`);
      } else if(data.width>600) {
        assert(aligned(nav,scenarioControl)&&nav.top>brand.bottom, `${label}: tablet second-row navigation and scenario align`);
      } else {
        assert(nav.top>verdict.bottom&&scenarioControl.top>nav.bottom, `${label}: mobile navigation and scenario have separate full-width rows`);
      }
      const [memory,recent,graph,inspector,action]=data.panels;
      for(const panel of data.panels.filter(p=>p.visible)) assert(panel.left>=0&&panel.right<=data.width, `${label}: panel fits: ${panel.selector}`);
      if(memory.visible&&recent.visible&&data.width>900) {
        assert(Math.abs(memory.top-recent.top)<=1&&Math.abs(memory.bottom-recent.bottom)<=1, `${label}: evidence cards have equal top and bottom edges`);
      }
      if(graph.visible&&inspector.visible&&data.width>1100) {
        assert(Math.abs(graph.top-inspector.top)<=1, `${label}: graph panel and inspector top edges align`);
        if(action.visible) assert(Math.abs(action.left-inspector.left)<=1&&Math.abs(action.right-inspector.right)<=1, `${label}: toolbar action aligns with inspector column`);
        if(memory.visible) assert(Math.abs(memory.left-graph.left)<=1&&Math.abs(recent.left-inspector.left)<=1, `${label}: evidence cards follow graph/inspector columns`);
      }
    };
  const focusRings = async label => {
    const before = await evaluate(`({header:headerStatus(),selected:graphDocument()?.querySelector('.node[aria-pressed="true"]')?.dataset.entity})`);
    const targets = [
      {selector:'.st-key-section input:checked', ring:'label'},
      {selector:'.st-key-graph_view input:checked', ring:'label'},
      {selector:'.st-key-scenario_bar [role="combobox"]'},
      {selector:'.st-key-entity_id [role="combobox"]'},
      {selector:'.st-key-instrument button[kind="primary"], .st-key-preview button'},
      ...(await evaluate(`graphVisible(graphDocument()?.querySelector('#viewport'))`) ?
        [{selector:'#graph-view',graph:true},{selector:'.node[aria-pressed="true"]',graph:true,node:true}] : []),
    ];
    for (const target of targets) {
      // Seed the tab position, then leave and re-enter with actual keyboard
      // navigation. Programmatic focus after an iframe mouse click alone does
      // not establish :focus-visible in Chrome.
      await evaluate(`(()=>{const d=${target.graph?'graphDocument()':'document'};
        [...d.querySelectorAll(${quote(target.selector)})].find(e=>!e.disabled)?.focus();})()`);
      for(const modifiers of [8,0]) {
        await page('Input.dispatchKeyEvent', {type:'keyDown',key:'Tab',code:'Tab',windowsVirtualKeyCode:9,modifiers});
        await page('Input.dispatchKeyEvent', {type:'keyUp',key:'Tab',code:'Tab',windowsVirtualKeyCode:9,modifiers});
      }
      const data = await evaluate(`(()=>{const d=${target.graph?'graphDocument()':'document'},
        e=[...d.querySelectorAll(${quote(target.selector)})].find(e=>!e.disabled);
        if(!e)return null;
        ${target.graph?"graphFrame().scrollIntoView({block:'center',behavior:'instant'});":''}
        e.scrollIntoView({block:'center',inline:'center',behavior:'instant'});
        const ring=${target.node?"e.querySelector('.focus-ring')":target.ring?`e.closest(${quote(target.ring)})`:'e'},s=d.defaultView.getComputedStyle(ring),r=ring.getBoundingClientRect();
        let p=ring.parentElement,background='rgba(0, 0, 0, 0)';
        while(p){background=d.defaultView.getComputedStyle(p).backgroundColor;if(background!=='rgba(0, 0, 0, 0)')break;p=p.parentElement;}
        const color=s[${quote(target.node?'stroke':'outlineColor')}],width=parseFloat(s[${quote(target.node?'strokeWidth':'outlineWidth')}]);
        const extent=${target.node?'width/2':'Math.max(0,parseFloat(s.outlineOffset)+width)'};
        const bounds={left:r.left-extent,right:r.right+extent,top:r.top-extent,bottom:r.bottom+extent};
        let clipped=false;
        for(let p=ring.parentElement;p;p=p.parentElement){const ps=d.defaultView.getComputedStyle(p),b=p.getBoundingClientRect();
          if(/hidden|clip|auto|scroll/.test(ps.overflowX)&&(bounds.left<b.left-1||bounds.right>b.right+1))clipped=true;
          if(/hidden|clip|auto|scroll/.test(ps.overflowY)&&(bounds.top<b.top-1||bounds.bottom>b.bottom+1))clipped=true;}
        return {selector:${quote(target.selector)},focused:d.activeElement===e,focusVisible:e.matches(':focus-visible'),
          visible:${target.graph?'graphVisible':'visible'}(ring),color,width,background,clipped,
          style:${target.node?"s.strokeDasharray":"s.outlineStyle"}};})()`);
      report.layouts.push({label:`${label}: keyboard focus probe`,...data});
      assert(data?.focused&&data.focusVisible&&data.visible, `${label}: keyboard focus visible on ${target.selector}: ${JSON.stringify(data)}`);
      assert(data.width>=3&&data.style!=='none', `${label}: >=3px focus ring on ${target.selector}`);
      assert(!data.clipped, `${label}: focus ring not clipped on ${target.selector}`);
      assert(contrast(data.color,data.background)>=3, `${label}: focus ring contrast on ${target.selector}`);
      report.layouts.push({label:`${label}: keyboard focus`,...data});
    }
    assert.deepEqual(await evaluate(`({header:headerStatus(),selected:graphDocument()?.querySelector('.node[aria-pressed="true"]')?.dataset.entity})`), before, `${label}: focus alone never selects or mutates memory`);
  };

  const story = async config => {
    const prefix = `story-${config.id}`;
    await select('.st-key-scenario_choice', config.label);
    await freshOverviewGraph(prefix);
    await expect(`headerStatus().length===1&&headerStatus()[0].includes('NOT RUN')&&headerStatus()[0].includes('revision 0')`, `${prefix}: untouched scenario`);
    await stageBanners('idle', config);
    await previewHighlight(config, false);
    // Do not assume an alphabetically first fact is initially selected. Keep
    // documentation's rendered default flexible while checking all three views
    // (node, selectbox, caption) agree on a real entity.
    const initialEntity = await evaluate(`graphDocument()?.querySelector('.node[aria-pressed="true"]')?.dataset.entity`);
    assert(initialEntity, `${prefix}: default entity is selected`);
    if (config.id === 'pricing') assert.equal(initialEntity, 'sources.sales');
    await inspected(initialEntity);
    await top();
    await visualStyles(`${prefix}: idle`);
    await checkpoint(`${prefix}-01-idle`, `${config.id}: idle NOT RUN without preview highlighting`);

    await navigate('Verify & Test');
    await select('.st-key-fault_choice', 'Skip one rebuild but report completion');
    await stageBanners('idle', config);
    await navigate(investigate);
    await aiOff();
    await expect(`primary().includes('A deliberate mistake is enabled: Skip one rebuild but report completion.')`, `${prefix}: false_success armed through the UI`);
    await stageBanners('idle', config);
    await previewHighlight(config, false);
    await chooseGraph();
    const inspectionSources = config.id === 'pricing' ? ['sources.sales', 'sources.pricing'] : ['sources.obsolete_bulletin', 'sources.api_reference'];
    for (const entity of inspectionSources) {
      await graphClick(`.node[data-entity="${entity}"]`);
      await inspected(entity, 'SOURCES / ACTIVE');
    }
    const planned = config.id === 'pricing' ? await preview(`${prefix}-02-preview`) : await documentationPreview(`${prefix}-02-preview`);
    assert.equal(planned.source_id, config.source, `${prefix}: inspector selection never changes the preview source`);
    // Inspection is independent from the pending mutation target: clicking the
    // other source must update the caption, not transfer amber highlighting.
    for (const entity of inspectionSources) {
      await graphClick(`.node[data-entity="${entity}"]`);
      await inspected(entity, 'SOURCES / ACTIVE');
      await previewHighlight(config, true);
    }
    await visualStyles(`${prefix}: preview`);
    await top();
    await checkpoint(`${prefix}-03-preview-top`, `${config.id}: explicit preview banner; inspection and preview target distinguished`);
    await button('Confirm removal and repair');
    await result('FAIL');
    await stageBanners('injected', config);
    if (config.id === 'pricing') await actualRepair(true);
    else await unrelatedEvidence(`${prefix}: failed repair`);
    await expect(`all('.fo-stage.fail').some(e=>!e.closest('details')&&e.textContent.includes(${quote('Skipped summary: ' + config.skipped + '.')}))`, `${prefix}: primary banner names skipped rebuild, not a successful verification`);
    await top();
    await visualStyles(`${prefix}: injected FAIL`);
    await checkpoint(`${prefix}-04-fail-top`, `${config.id}: reported completion and injected failure banners with independent FAIL`);

    await navigate('Verify & Test');
    const failed = await technicalReport(`${prefix}: actual false_success`, null);
    completeEvidence(failed, 'FAIL', config, true);
    const failedOperation = await operationEvidence(config, true);
    await stageBanners('injected', config, failedOperation);
    await renderedJSON('Operation detail');
    await capture(`${prefix}-05-failed-operation`);
    await expand('Operation detail', false);
    const mismatch = await mismatchContent(config, failed, `${prefix}-05-exact-mismatch`);
    await button('Retry repair without a mistake');
    await result('PASS', null);
    const recovered = await technicalReport(`${prefix}: actual same-operation recovery`, null);
    completeEvidence(recovered, 'PASS', config);
    const recoveredOperation = await operationEvidence(config, false);
    assert.equal(recoveredOperation.id, failedOperation.id, `${prefix}: retry reuses the same operation`);
    assert.equal(recoveredOperation.attempts, failedOperation.attempts + 1);
    assert.equal(recovered.generation, failed.generation);
    assert.equal(recovered.dataset_id, failed.dataset_id);
    assert(recovered.revision > failed.revision && recovered.started_at !== failed.started_at, `${prefix}: recovery gets a separate independent verification`);
    await result('PASS', recovered.comparisons.length);
    await stageBanners('recovery', config, recoveredOperation, failed);
    await top();
    await visualStyles(`${prefix}: recovered PASS and earlier FAIL`);
    await checkpoint(`${prefix}-06-recovery-top`, `${config.id}: current PASS, recovery banner and earlier FAIL retained separately`);
    // Capture readable evidence separately from the top-of-page story. The
    // reports below came exclusively from native actions and expanded UI JSON.
    await renderedJSON('Operation detail');
    await capture(`${prefix}-07-recovered-operation`);
    await expand('Operation detail', false);
    await navigate(investigate);
    await stageBanners('recovery', config, recoveredOperation, failed);
    if (config.id === 'pricing') await actualRepair(false);
    else await unrelatedEvidence(`${prefix}: recovered repair`);
    await top();
    await checkpoint(`${prefix}-08-recovered-repair-top`, `${config.id}: actual repaired memory, same-operation recovery and historical FAIL`);
    report.stories.push({scenario:config.id,preview:planned,mismatch,failedOperation,failed,recoveredOperation,recovered});
    await noErrors();
  };

  await send('Browser.setDownloadBehavior',{behavior:'allowAndName',downloadPath:runDirectory,eventsEnabled:true});
  await page('Page.enable');
  await page('Runtime.enable');
  await page('Network.enable');
  await page('Log.enable');
  await page('Fetch.enable', {patterns: [{urlPattern: '*'}]});
  await page('Emulation.setDeviceMetricsOverride', {width: 1440, height: 900, deviceScaleFactor: 1, mobile: false});
  await page('Page.navigate', {url: report.url});
  await wait(`!!document.querySelector('.fo-intro')`, 'initial Overview');
  await evaluate(helpers);
  await settle();
  await expand('Advanced details · environment');
  await expect(`visibleText().includes(${quote('Backend: ' + expectedBackend + ' · Target: ')})`, 'actual rendered backend matches requested validation');
  report.backend = expectedBackend;
  await expand('Advanced details · environment', false);
  if (storyOnly) {
    // No reload/reset, backend injection or isolated-lab substitute: each
    // scenario is exercised in its own banked working session via native UI.
    for (const config of [pricing, documentation]) await story(config);
    await noErrors();
    assert.equal(report.blockedExternalOrigins.length, 0, 'No external requests during actual two-scenario story');
  } else if (layoutOnly) {
    // All three pages at each width, in both datasets. No repairs or live AI.
    for (const config of [pricing, documentation]) {
      await select('.st-key-scenario_choice', config.label);
      for (const section of ['Overview', investigate, 'Verify & Test']) {
        await navigate(section);
        for (const [width,height] of visualSizes) {
          await page('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
          await settle(); await top();
          const label=`layout-${config.id}-${section.split(' ')[0]}-${width}`;
          await stageBanners('idle', config);
          await visualStyles(label);
          await capture(label);
          await noErrors();
          report.workflows.push(`${label}: full header, controls, panels and rendered text checked`);
        }
        await page('Emulation.setDeviceMetricsOverride',{width:1440,height:900,deviceScaleFactor:1,mobile:false});
        await settle();
      }
    }
    assert.equal(report.blockedExternalOrigins.length,0,'No external requests during layout checks');
  } else if (visualOnly) {
    // Visual acceptance only: no repairs, verifier executions, labs or JSON expansion.
    // Native view/selection controls may change presentation, never working memory.
    assert.equal(await evaluate(`document.querySelector('.fo-intro h2').textContent`), 'A completed repair can still be wrong.', 'Instrument introduction');
    assert.deepEqual(await evaluate(`all('.st-key-section label').map(e=>e.textContent.trim())`), Object.values(navigationLabels), 'Navigation retains radios with short labels');
    assert.equal(await evaluate(`all('[role="tab"]').length`), 0, 'Navigation is not tabs');
    for (const config of [pricing, documentation]) {
      await select('.st-key-scenario_choice', config.label);
      await navigate('Overview');
      for (const [width, height] of visualSizes) {
        const label = `visual-${config.id}-${width}x${height}`;
        // Native navigation clears only the iframe's explicit view choice.
        // Reload would discard session evidence; snapshot toggles retain view.
        await freshOverviewGraph(label);
        await page('Emulation.setDeviceMetricsOverride', {width, height, deviceScaleFactor: 1, mobile: false});
        await settle();
        await expect(`all('.st-key-section label').some(e=>e.textContent.trim()==='Overview'&&e.querySelector('input')?.checked)`, `${label}: Overview selected`);
        await expect(`headerStatus().length===1&&headerStatus()[0].includes('NOT RUN')&&headerStatus()[0].includes('revision 0')`, `${label}: unmodified NOT RUN revision zero`);
        await expect(`all('.fo-header-state').some(e=>e.textContent.includes('AI OFF'))`, `${label}: AI OFF`);
        await graphReady();
        await graphDefault(label);
        await stageBanners('idle', config);
        await previewHighlight(config, false);
        if (config.id === 'pricing') await graphState(`${label}: initial graph`);
        else await documentationGraph(`${label}: initial graph`, false);
        await top();
        await visualStyles(label);
        // Scrolling is legitimate: the small-screen CTA can be below the fold.
        await layout(`${label}-cta`, 'Investigate source change', false);
        await capture(`${label}-cta`);
        await top();
        await capture(`${label}-overview`);
        await relationshipTable(label);
        // On mobile, capture/assert Relationships first. Only now opt in to Graph.
        await chooseGraph();
        await graphLayout(`${label}-default-graph`);
        await focusRings(label);
        const source = `sources.${config.source}`, fact = `facts.${config.preserved}`;
        // End on a different entity each time so inspector updates are observable.
        await graphClick(`.node[data-entity="${fact}"]`);
        await inspected(fact, 'FACTS / VALID');
        await graphClick(`.node[data-entity="${source}"]`);
        await inspected(source, 'SOURCES / ACTIVE');
        if (config.id === 'pricing') {
          await graphClick('.node[data-entity="sources.pricing"]');
          await inspected('sources.pricing', 'SOURCES / ACTIVE');
        }
        await graphClick(`.node[data-entity="${fact}"]`);
        await inspected(fact, 'FACTS / VALID');
        await capture(`${label}-fact-inspector`);
        // Same native Enter path as the full harness; focus is not selection.
        await evaluate(`(()=>{const f=graphFrame(),e=graphDocument().querySelector(${quote(`.node[data-entity="${source}"]`)});
          f.scrollIntoView({block:'center'});e.scrollIntoView({block:'center',inline:'center'});e.focus();})()`);
        await page('Input.dispatchKeyEvent', {type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
        await page('Input.dispatchKeyEvent', {type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
        await inspected(source, 'SOURCES / ACTIVE');
        await capture(`${label}-source-inspector`);
        await camera();
        await capture(`${label}-camera-fit`);
        // Fit may shrink labels. Restore native camera via real snapshot controls
        // before testing readability, for BOTH pricing and documentation.
        await snapshotView('Original dataset');
        await snapshotView('Current state');
        await chooseGraph();
        await graphLayout(`${label}-native-graph`);
        await relationshipTable(`${label}-after-camera`);
        await expect(`headerStatus().length===1&&headerStatus()[0].includes('NOT RUN')&&headerStatus()[0].includes('revision 0')`, `${label}: visual interactions left memory unmodified`);
        await noErrors();
        assert.equal(report.blockedExternalOrigins.length, 0, `${label}: no external page request attempted`);
        report.workflows.push(`${label}: Overview, brand alignment/contrast, dark graph, native source/fact clicks, Enter selection, camera geometry and native-scale graph readability`);
      }
      await page('Emulation.setDeviceMetricsOverride', {width:1440,height:900,deviceScaleFactor:1,mobile:false});
      await button('Investigate source change');
      await expect(`all('h2').some(e=>e.textContent.trim()===${quote(investigate)})`, `${config.id}: CTA opens Investigate & Repair`);
      await aiOff();
      await graphReady();
      await top();
      await checkpoint(`visual-${config.id}-investigate`, `${config.id}: Investigate visited without executing repair`);
      await navigate('Verify & Test');
      await result('NOT RUN');
      await layout(`visual-${config.id}-verify`, 'Check current memory', false);
      await top();
      await checkpoint(`visual-${config.id}-verify`, `${config.id}: Verify visited, truthful NOT RUN without executing verification`);
    }
    await noErrors();
    assert.equal(report.blockedExternalOrigins.length, 0, 'No external page request attempted');
  } else {
  assert.equal(await evaluate(`document.querySelector('.fo-intro h2').textContent`), 'A completed repair can still be wrong.', 'Instrument introduction');
  assert.deepEqual(await evaluate(`all('.st-key-section label').map(e=>e.textContent.trim())`), Object.values(navigationLabels), 'Navigation retains radios with short labels');
  assert.equal(await evaluate(`all('[role="tab"]').length`), 0, 'Navigation is not tabs');
  await expect(`headerStatus().length===1&&headerStatus()[0].includes('NOT RUN')&&headerStatus()[0].includes('revision 0')`, 'Fresh main NOT RUN revision zero');
  await expect(`all('.fo-header-state').some(e=>e.textContent.includes('AI OFF'))`, 'Fresh app AI OFF');
  await graphState('initial graph');
  await graphDefault('initial graph');
  await stageBanners('idle');
  await previewHighlight(pricing, false);
  await inspected('sources.sales', 'SOURCES / ACTIVE');
  await visualStyles('initial instrument');
  await relationshipTable('initial graph');
  await chooseGraph();
  await graphLayout('initial-default-graph-1440');
  await focusRings('initial instrument');
  await top();
  await layout('initial-1440', 'Investigate source change', true);
  await checkpoint('01-overview-1440', 'Overview: primary graph, truthful NOT RUN header, prominent source-change CTA, redesigned navigation');
  await button('Investigate source change');
  await expect(`all('h2').some(e=>e.textContent===${quote(investigate)})`, 'CTA opens Investigate & Repair');
  await aiOff();
  await stageBanners('idle');
  await graphClick('.node[data-entity="sources.sales"]');
  await inspected('sources.sales','SOURCES / ACTIVE');
  await graphClick('.node[data-entity="sources.pricing"]');
  await inspected('sources.pricing','SOURCES / ACTIVE');
  await graphClick('.node[data-entity="facts.phone_support"]');
  await inspected('facts.phone_support','FACTS / VALID');
  await select('.st-key-entity_id','sources.pricing');
  await inspected('sources.pricing','SOURCES / ACTIVE');
  // Focus only sets keyboard location; native CDP Enter must activate the node
  // and cause a Python rerun, not a fabricated component message.
  await evaluate(`(()=>{const f=graphFrame(),e=graphDocument().querySelector('.node[data-entity="sources.sales"]');
    f.scrollIntoView({block:'center'});e.scrollIntoView({block:'center',inline:'center'});e.focus();})()`);
  await page('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
  await page('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
  await inspected('sources.sales','SOURCES / ACTIVE');
  await graphLayout('02-default-graph-1440');
  await camera();
  // Healthy renders expose the accessible Relationships view inside the
  // iframe. The Python relationship expander now belongs only to render errors.
  await graphClick('#table-view');
  await relationshipTable('native relationship alternative');
  await graphClick('#relationship-rows button[data-entity="facts.phone_support"]');
  await inspected('facts.phone_support','FACTS / VALID');
  await capture('02-relationship-table');
  await chooseGraph();
  await snapshotView('Original dataset');
  await snapshotView('Current state');
  await expect(`all('.st-key-source_choice').some(e=>e.innerText.includes('sales'))`, 'sales selected');
  const initialPreview=await preview('03-preview-1440');
  await visualStyles('pricing preview');
  const initialSnapshot=await renderedSnapshot();
  await navigate('Verify & Test');
  await result('NOT RUN');
  await expand('Advanced details · technical verification report');
  await expect(`visibleText().includes('EMPTY / No operation events have been recorded in this session.')`, 'Fresh main has no operation events');
  const initialMainEvidence=await evaluate(`all('summary').find(e=>e.textContent.includes('Advanced details · technical verification report')).parentElement.innerText`);
  const firstLab=await lab(initialPreview.generation,'04-fresh-lab');
  await result('NOT RUN');
  assert.deepEqual(await evaluate(`all('summary').find(e=>e.textContent.includes('Advanced details · technical verification report')).parentElement.innerText`), initialMainEvidence, 'Fresh Lab leaves main report and before/after/audit evidence unchanged');
  await navigate(investigate);
  assert.deepEqual(await renderedJSON('Advanced details · exact preview'),initialPreview, 'Lab preserves existing exact preview without minting/replacing it');
  await expand('Advanced details · exact preview',false);
  assert.deepEqual(await renderedSnapshot(),initialSnapshot, 'Fresh Lab preserves every rendered main record and NOT RUN rev0');
  report.workflows.push('Fresh isolated Lab preserves pending preview, all five rendered inspector records, graph, empty main report/audit and NOT RUN revision 0');
  await button('Confirm removal and repair');
  await result('PASS');
  await stageBanners('normal');
  await visualStyles('pricing normal execution');
  report.normalResult = await actualRepair(false);
  await top();
  await checkpoint('03-repair-pass', 'Explicit confirmation: primary PASS, 30 checks, phone_support preserved, discount removed, phone-only saved summary');

  await graphState('normal repair current',true);
  await graphClick('.node[data-entity="sources.sales"]');
  await inspected('sources.sales','SOURCES / REVOKED');
  await graphClick('.node[data-entity="facts.phone_support"]');
  const phoneInspector=await inspected('facts.phone_support','FACTS / VALID');
  assert(phoneInspector.includes('pricing'), 'Python inspector contains retained pricing support');
  await snapshotView('Before last operation');
  await expect(`primary().includes('Captured before confirmation · revision 0')`, 'Actual captured historical revision');
  await graphState('before operation historical');
  await graphClick('.node[data-entity="sources.sales"]');
  await inspected('sources.sales','SOURCES / ACTIVE');
  await snapshotView('Original dataset');
  await expect(`primary().includes('Original input projection · not the last-operation snapshot')`, 'Original graph honestly labelled');
  await graphState('original dataset');
  await inspected('sources.sales','SOURCES / ACTIVE');
  await snapshotView('Current state');
  await graphState('return to current',true);
  await inspected('sources.sales','SOURCES / REVOKED');
  await noErrors();
  const repairedSnapshot=await renderedSnapshot();
  await checkpoint('06-primary-graph', 'Primary native SVG: five nodes/five retained active/dashed edges, mouse and Enter update Python inspector/selectbox, dropdown sync, historical/current/original states, real zoom and fit');
  await navigate('Verify & Test');
  await result('PASS');
  const first = await technicalReport('normal repair');
  validateEvidence(first,'PASS');
  assert.equal(first.status, 'PASS');
  assert(first.comparisons.every(item => item.match) && first.mismatches.length === 0, '30 real matching comparisons');
  comparison(first, 'facts.phone_support.status', 'valid', 'valid');
  comparison(first, 'facts.discount_20.status', 'invalid', 'invalid');
  comparison(first, 'summaries.customer_summary.content', phone, phone);
  await button('Check current memory');
  await result('PASS');
  const verified = await technicalReport('explicit Check current memory');
  validateEvidence(verified,'PASS');
  assert.notEqual(verified.started_at, first.started_at, 'Check current memory actually executes a new verifier run');
  await download(verified);
  const secondLab=await lab(verified.generation,'07-repeat-lab');
  assert.equal(secondLab.options.length,2,'Repeated Lab appends a second history entry');
  assert.equal(secondLab.options[0],firstLab.options[0],'Earlier Lab history is retained');
  assert.notEqual(secondLab.identity,firstLab.identity,'Repeated Lab has a new run identity');
  assert.equal(new Set([...firstLab.scenarios,...secondLab.scenarios].map(s=>s.after.generation)).size,4,'Every Lab scenario has its own generation');
  await select('.st-key-lab_run',firstLab.options[0]);
  for(const scenario of firstLab.scenarios){
    assert.deepEqual(await renderedJSON('Lab evidence · '+scenario.name),scenario,'Historical Lab evidence unchanged');
    await expand('Lab evidence · '+scenario.name,false);
  }
  await result('PASS');
  assert.deepEqual(await technicalReport('main after repeated lab'),verified,'Lab never overwrites main verifier report');
  await navigate(investigate);
  assert.deepEqual(await renderedSnapshot(),repairedSnapshot,'Repeated Lab preserves every rendered repaired main record and graph');
  await navigate('Verify & Test');
  report.workflows.push('Repeated isolated Lab retains historical exact evidence; main report and five-record snapshot unchanged; real downloaded JSON matches main report');
  await falseSuccessLab(pricing, verified.generation, '07-pricing-false-success-lab');
  await result('PASS');
  assert.deepEqual(await technicalReport('pricing main after false-success lab'), verified, 'False-success lab leaves working report unchanged');
  await navigate(investigate);
  assert.deepEqual(await renderedSnapshot(), repairedSnapshot, 'False-success lab preserves working graph and records');
  await navigate('Verify & Test');
  await checkpoint('05-verify-evidence', 'Verify & Test: visible PASS and actual 30-row evidence; explicit check has new execution timestamp');
  await expand('Start over · reset the local demo');
  await button('Preview reset');
  await result('PASS');
  await expect(`all('.st-key-confirm_reset button').some(e=>e.disabled)`, 'reset requires acknowledgment');
  await nativeClick(`all('.st-key-reset_ack label')[0]`);
  await expect(`all('.st-key-confirm_reset button').some(e=>!e.disabled)`, 'acknowledged reset ready');
  await button('Confirm in-memory reset');
  await result('NOT RUN');
  assert.equal(await evaluate(`primary().includes('PASS —')`), false, 'Reset clears primary PASS');
  await stageBanners('idle');
  await top();
  await checkpoint('06-reset-not-run', 'Reset preview does not mutate; explicit acknowledgment and confirmation restore NOT RUN');

  await select('.st-key-fault_choice', 'Skip one rebuild but report completion');
  await expect(`all('summary').find(e=>e.textContent.includes('Advanced · fault injection'))?.parentElement.innerText.includes('The operation will report completion while skipping one affected summary rebuild.')`, 'false-success fault choice committed in expanded advanced controls');
  await navigate(investigate);
  const armed = `primary().includes('A deliberate mistake is enabled: Skip one rebuild but report completion.')`;
  await expect(armed, 'armed fault warning persists across navigation');
  await stageBanners('idle');
  await previewHighlight(pricing, false);
  await top();
  await checkpoint('08-armed-fault', 'Native mouse fault selection persists when navigating to Investigate & Repair');
  await preview('08-fault-preview');
  await expect(armed, 'armed fault warning persists after preview rerun');
  await button('Confirm removal and repair');
  await result('FAIL');
  await stageBanners('injected');
  await visualStyles('pricing injected FAIL');
  await focusRings('pricing injected FAIL');
  report.faultResult = await actualRepair(true);
  await graphState('false_success actual graph',true,true);
  await relationshipTable('pricing injected FAIL');
  await chooseGraph();
  await graphLayout('pricing-injected-fail-graph');
  await graphClick('.node[data-entity="summaries.customer_summary"]');
  const falseInspector = await inspected('summaries.customer_summary','SUMMARIES / STALE');
  assert(falseInspector.includes(discount) && falseInspector.includes(phone), 'Actual selected false-success summary retains exact old facts');
  await expect(`all('.fo-stage.fail').some(e=>e.textContent.includes('Skipped summary: customer_summary.')) &&
    all('.fo-stage').some(e=>e.textContent.includes('REPAIR EXECUTED · reported complete')) &&
    all('.fo-stage.fail').some(e=>e.textContent.includes('FAILURE INJECTED · repair skipped a rebuild')) &&
    headerStatus()[0].includes('VERIFIER / FAIL')`, 'False-success claim visibly distinguished from independent FAIL');
  await top();
  await checkpoint('09-repair-fail', 'Confirmed deliberate stale-summary fault: primary FAIL and actual old summary containing the removed discount');
  await navigate('Verify & Test');
  await result('FAIL');
  const failed = await technicalReport('pricing actual false-success');
  validateEvidence(failed,'FAIL');
  completeEvidence(failed,'FAIL',pricing,true);
  const failedOperation = await operationEvidence(pricing,true);
  await stageBanners('injected', pricing, failedOperation);
  assert.equal(failed.status, 'FAIL');
  const stale = failed.comparisons.find(item => item.path === 'summaries.customer_summary.content');
  assert.equal(stale.actual,staleContent,'Exact old summary, not substring evidence');
  comparison(failed, 'summaries.customer_summary.content', phone, staleContent);
  assert(failed.mismatches.some(item => item.path === stale.path && item.expected === phone && item.actual === stale.actual), 'Exact expected/actual stale-content mismatch');
  await mismatchContent(pricing, failed, '10-exact-failed-content');
  await checkpoint('10-fail-technical-evidence', 'Technical FAIL report includes summaries.customer_summary.content with actual stale and expected phone-only values');
  await button('Retry repair without a mistake');
  await result('PASS');
  const retried = await technicalReport('retry without mistake');
  validateEvidence(retried,'PASS');
  completeEvidence(retried,'PASS',pricing);
  const recoveredOperation = await operationEvidence(pricing,false);
  await stageBanners('recovery', pricing, recoveredOperation, failed);
  await visualStyles('pricing recovery PASS with earlier FAIL');
  assert.equal(recoveredOperation.id,failedOperation.id,'Actual retry reuses false-success operation ID');
  assert.equal(recoveredOperation.attempts,failedOperation.attempts+1);
  assert.equal(retried.generation,failed.generation);
  assert(retried.revision>failed.revision && retried.started_at!==failed.started_at,'Separate actual recovery verification');
  assert.equal(retried.status, 'PASS');
  assert(retried.comparisons.every(item => item.match) && retried.mismatches.length === 0, 'Retry actually repaired all mismatches');
  comparison(retried, 'summaries.customer_summary.content', phone, phone);
  await navigate(investigate);
  await result('PASS');
  await actualRepair(false);
  await graphState('retry current graph',true);
  await relationshipTable('pricing recovered graph');
  await focusRings('pricing recovery');
  await top();
  await checkpoint('11-retry-pass', 'Retry without mistake: actual PASS, 30 matching comparisons, saved phone-only summary');

  for (const [width, height] of visualSizes) {
    // Fresh native component, then resize: never carry a previous Graph opt-in
    // into a test of the compact default, and never reload away FAIL history.
    await freshOverviewGraph(`responsive ${width}`);
    await page('Emulation.setDeviceMetricsOverride', {width, height, deviceScaleFactor: 1, mobile: false});
    await settle();
    await graphDefault(`responsive ${width}`);
    await visualStyles(`recovered overview ${width}`);
    await stageBanners('recovery', pricing, recoveredOperation, failed);
    await top();
    await layout(`overview-${width}`, 'Investigate source change', width>1100);
    await capture(`12-overview-${width}`);
    await relationshipTable(`responsive ${width}`);
    await chooseGraph();
    await graphLayout(`12-default-graph-${width}`);
    await graphState(`responsive ${width} graph`,true);
    await graphClick('.node[data-entity="facts.phone_support"]');
    await inspected('facts.phone_support','FACTS / VALID');
    await camera();
    await button('Investigate source change');
    await result('PASS');
    await actualRepair(false);
    // Native snapshot selection resets a camera retained across section changes.
    await snapshotView('Original dataset');
    await snapshotView('Current state');
    await chooseGraph();
    await graphLayout(`13-investigate-default-graph-${width}`);
    await top();
    await capture(`13-repair-${width}`);
    await navigate('Verify & Test');
    await result('PASS');
    await layout(`verify-${width}`, 'Check current memory');
    await capture(`14-verify-${width}`);
    report.workflows.push(`${width}x${height}: no horizontal page overflow, desktop above-fold/responsive reachable Overview CTA and readable Verify primary control; actual Repair PASS visible; compact default precedes explicit Graph opt-in`);
  }
  await page('Emulation.setDeviceMetricsOverride', {width:1440,height:900,deviceScaleFactor:1,mobile:false});
  const pricingBank = await technicalReport('pricing report before scenario banking');
  await expand('Advanced · isolated verification lab');
  const pricingLabIdentity = await evaluate(`all('[data-testid="stCaptionContainer"]').find(e=>e.textContent.includes('ISOLATED LAB ·'))?.textContent`);
  assert(pricingLabIdentity, 'Pricing lab identity is visible in explicitly expanded lab');
  await navigate(investigate);
  const pricingBankSnapshot = await renderedSnapshot();
  await select('.st-key-scenario_choice', documentation.label);
  await expect(`headerStatus()[0].includes('NOT RUN') && headerStatus()[0].includes('revision 0')`, 'Fresh documentation scenario, not pricing state');
  await freshOverviewGraph('documentation initial');
  await documentationGraph('documentation original graph',false);
  await graphDefault('documentation initial');
  await stageBanners('idle', documentation);
  await previewHighlight(documentation, false);
  await visualStyles('documentation initial');
  await relationshipTable('documentation initial');
  await chooseGraph();
  await button('Investigate source change');
  await aiOff();
  await graphClick('.node[data-entity="sources.obsolete_bulletin"]');
  await inspected('sources.obsolete_bulletin','SOURCES / ACTIVE');
  await graphClick('.node[data-entity="facts.cursor_pagination"]');
  const cursorBefore = await inspected('facts.cursor_pagination','FACTS / VALID');
  assert(cursorBefore.includes('api_reference') && cursorBefore.includes('obsolete_bulletin'));
  await graphLayout('15-documentation-original-graph');
  const docPreview = await documentationPreview('15-documentation-preview');
  await visualStyles('documentation preview');
  assert.notEqual(docPreview.dataset_id,pricingBank.dataset_id,'Real documentation dataset identity differs');
  const docBefore = await documentationSnapshot();
  await navigate('Verify & Test');
  const docLabs = await falseSuccessLab(documentation,docPreview.generation,'16-documentation-false-success-lab');
  await result('NOT RUN');
  await navigate(investigate);
  assert.deepEqual(await renderedJSON('Advanced details · exact preview'),docPreview,'Documentation lab preserves pending preview');
  await expand('Advanced details · exact preview',false);
  assert.deepEqual(await documentationSnapshot(),docBefore,'Documentation lab preserves all ten working records');
  await select('.st-key-scenario_choice',pricing.label);
  assert.deepEqual(await renderedSnapshot(),pricingBankSnapshot,'Switch restores all pricing records and selected scenario status');
  await navigate('Verify & Test');
  assert.deepEqual(await technicalReport('pricing bank restored during documentation preview'),pricingBank);
  await expand('Advanced · isolated verification lab');
  assert.equal(await evaluate(`all('[data-testid="stCaptionContainer"]').find(e=>e.textContent.includes('ISOLATED LAB ·'))?.textContent`),pricingLabIdentity,'Pricing lab history is banked');
  await select('.st-key-scenario_choice',documentation.label);
  await expect(`all('h2').some(e=>e.textContent===${quote(investigate)})`, 'Documentation investigation navigation bank restored');
  assert.deepEqual(await renderedJSON('Advanced details · exact preview'),docPreview,'Scenario switch preserves exact pending documentation preview');
  await expand('Advanced details · exact preview',false);
  await button('Confirm removal and repair');
  await result('PASS',null);
  await stageBanners('normal', documentation);
  await visualStyles('documentation normal execution');
  const unrelatedNormal = await unrelatedEvidence('Documentation normal repair');
  await documentationGraph('documentation normal actual graph',true);
  await graphClick('.node[data-entity="facts.cursor_pagination"]');
  const cursorAfter = await inspected('facts.cursor_pagination','FACTS / VALID');
  assert(cursorAfter.includes('api_reference'));
  await graphClick('.node[data-entity="summaries.api_overview"]');
  const docSummary = await inspected('summaries.api_overview','SUMMARIES / FRESH');
  assert(docSummary.includes(documentation.summaries.api_overview.after) && !docSummary.includes('Legacy query-string access tokens are accepted.'));
  await checkpoint('17-documentation-normal-repair','Documentation actual native graph selection after normal repair; two rebuilt summaries and unrelated summary unchanged');
  await navigate('Verify & Test');
  const docNormal = await technicalReport('documentation actual normal',null);
  completeEvidence(docNormal,'PASS',documentation);
  await result('PASS',docNormal.comparisons.length);
  assert.equal(docNormal.dataset_id,docPreview.dataset_id);
  assert.equal(docNormal.comparisons.length,docLabs[0].verification.comparisons.length,'Normal lab and real documentation schedules agree');
  await download(docNormal);
  await reset();
  await stageBanners('idle', documentation);
  await select('.st-key-fault_choice','Skip one rebuild but report completion');
  await navigate(investigate);
  await stageBanners('idle', documentation);
  await previewHighlight(documentation, false);
  await documentationPreview('18-documentation-false-success-preview');
  await button('Confirm removal and repair');
  await result('FAIL');
  await stageBanners('injected', documentation);
  await visualStyles('documentation injected FAIL');
  await expect(`all('.fo-stage.fail').some(e=>!e.closest('details')&&e.textContent.includes('Skipped summary: api_overview.'))`, 'Documentation primary false-success banner names skipped summary');
  assert.deepEqual(await unrelatedEvidence('Documentation false-success'),unrelatedNormal,'Unrelated summary remains exactly original after reset and injected failure');
  await documentationGraph('documentation false_success actual graph',true,true);
  await relationshipTable('documentation injected FAIL');
  await chooseGraph();
  await graphLayout('documentation-injected-fail-graph');
  await graphClick('.node[data-entity="summaries.api_overview"]');
  const staleDocInspector = await inspected('summaries.api_overview','SUMMARIES / STALE');
  assert(staleDocInspector.includes(documentation.summaries.api_overview.before),'Actual selected skipped summary still contains withdrawn claim');
  await graphClick('.node[data-entity="summaries.migration_notes"]');
  const rebuiltDocInspector = await inspected('summaries.migration_notes','SUMMARIES / FRESH');
  assert(rebuiltDocInspector.includes('Cursor pagination is supported.') && !rebuiltDocInspector.includes('Legacy query-string access tokens are accepted.'),'Other affected summary actually rebuilt');
  await graphClick('.node[data-entity="summaries.installation_summary"]');
  await inspected('summaries.installation_summary','SUMMARIES / FRESH');
  await checkpoint('19-documentation-false-success-graph','Actual false-success graph: skipped api_overview stale, migration_notes rebuilt, installation_summary unchanged');
  await navigate('Verify & Test');
  const docFailed = await technicalReport('documentation actual false-success',null);
  completeEvidence(docFailed,'FAIL',documentation,true);
  const docFailedOperation = await operationEvidence(documentation,true);
  await stageBanners('injected', documentation, docFailedOperation);
  await mismatchContent(documentation, docFailed, '20-exact-documentation-failed-content');
  await button('Select entity in Memory lineage');
  await navigate(investigate);
  await inspected('summaries.api_overview','SUMMARIES / STALE');
  await checkpoint('20-documentation-report-graph-selection','Actual comparison selection synchronizes Python inspector and failed graph node');
  const failedDocBank = await documentationSnapshot();
  await select('.st-key-scenario_choice',pricing.label);
  await navigate('Verify & Test');
  assert.deepEqual(await technicalReport('pricing unaffected by documentation failure'),pricingBank);
  await result('PASS');
  await select('.st-key-scenario_choice',documentation.label);
  assert.deepEqual(await documentationSnapshot(),failedDocBank,'False-success documentation state survives scenario banking');
  await navigate('Verify & Test');
  assert.deepEqual(await technicalReport('documentation failed bank restored',null),docFailed);
  assert.deepEqual(await operationEvidence(documentation,true),docFailedOperation);
  assert.deepEqual(await renderedJSON('Lab evidence · false_success'),docLabs[1],'Documentation lab history survives reset, failure and scenario switches');
  await expand('Lab evidence · false_success',false);
  await button('Retry repair without a mistake');
  const docRecovered = await technicalReport('documentation actual recovery',null);
  completeEvidence(docRecovered,'PASS',documentation);
  await result('PASS',docRecovered.comparisons.length);
  const docRecoveredOperation = await operationEvidence(documentation,false);
  await stageBanners('recovery', documentation, docRecoveredOperation, docFailed);
  await visualStyles('documentation recovery PASS with earlier FAIL');
  assert.equal(docRecoveredOperation.id,docFailedOperation.id);
  assert.equal(docRecoveredOperation.attempts,docFailedOperation.attempts+1);
  assert.equal(docRecovered.generation,docFailed.generation);
  assert(docRecovered.revision>docFailed.revision && docRecovered.started_at!==docFailed.started_at);
  await download(docRecovered);
  await navigate(investigate);
  assert.deepEqual(await unrelatedEvidence('Documentation recovery'),unrelatedNormal);
  await documentationGraph('documentation recovered actual graph',true);
  await relationshipTable('documentation recovered graph');
  await chooseGraph();
  await graphClick('.node[data-entity="summaries.api_overview"]');
  const recoveredDocInspector = await inspected('summaries.api_overview','SUMMARIES / FRESH');
  assert(recoveredDocInspector.includes(documentation.summaries.api_overview.after) && !recoveredDocInspector.includes('Legacy query-string access tokens are accepted.'));
  await graphLayout('21-documentation-recovery-graph');
  await checkpoint('21-documentation-recovery','Same actual operation recovered; report-derived comparison count; exact supported summary and unchanged unrelated summary');
  report.workflows.push('Scenario banking: distinct dataset/generation, pending preview, repaired pricing report, failed documentation report/operation, navigation, all rendered records and lab history preserved');
  await noErrors();
  await aiOff();
  assert.equal(report.blockedExternalOrigins.length,0,'No external page request attempted');
  }
  report.status = 'PASS';
} catch (error) {
  report.status = 'FAIL';
  report.failure = {phase, message: error.message, stack: error.stack};
  try {
    if (capture) await capture('failure');
    if (evaluate) report.failure.diagnostics = await evaluate(`({ready:document.readyState,url:location.href,
      busy:typeof busy==='function'?busy():null,exceptions:[...document.querySelectorAll('[data-testid="stException"]')].map(e=>e.innerText),
      buttons:[...document.querySelectorAll('button')].map(e=>({text:e.textContent,disabled:e.disabled})),
      stale:document.querySelectorAll('[data-stale="true"]').length})`);
  } catch (captureError) {report.failure.captureError = captureError.message;}
  report.failure.classification = /Harness|CDP|Browser did not|Browser exited|ECONN/.test(error.message) ? 'harness/environment blocker' :
    report.failure.diagnostics?.busy ? 'rerun synchronization blocker; not established as app bug' :
      'settled UI assertion failed; inspect captured DOM to distinguish app defect from selector mismatch';
  process.exitCode = 1;
} finally {
  report.finishedAt = new Date().toISOString();
  report.blockedExternalOrigins = [...new Set(report.blockedExternalOrigins)];
  await writeFile(resolve(artifacts, 'result.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({...report, graphs:report.graphs.map(({label})=>label),
    stories:report.stories.map(({scenario,mismatch,failedOperation,failed,recoveredOperation,recovered})=>({scenario,mismatch,
      operationId:failedOperation.id,attempts:[failedOperation.attempts,recoveredOperation.attempts],
      verdicts:[failed.status,recovered.status],revisions:[failed.revision,recovered.revision]})),
    labs:report.labs.map(({label,identity,scenarios})=>({label,identity,scenarios:scenarios.map(s=>({name:s.name,status:s.verdict,checks:s.verification.comparisons.length}))})),
    downloads:report.downloads.map(({filename,bytes})=>({filename,bytes})),
    verifications: report.verifications.map(({label,status,comparisons}) => ({label,status,checks:comparisons.length}))}, null, 2));
  for (const {timer} of pending.values()) clearTimeout(timer);
  socket?.close();
  // Only terminate this invocation's dedicated child, never other Chrome/app processes.
  processHandle.kill('SIGTERM');
}
/** Offline arithmetic/DOM regression checks for the iframe design review.
 * No browser, Python, environment files, packages, network or server required.
 * Deliberately independent of browser_check.mjs and its old layout assertions.
 * Text measurement is conservative, not a substitute for parent pixel validation.
 */
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const html = readFileSync(new URL('../fallout/graph_component/index.html', import.meta.url), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
assert.equal((html.match(/<script\b/g) || []).length, 1);
assert(!/<(?:script|link|img|iframe)\b[^>]*(?:src|href)=/i.test(html));
for (const forbidden of ['innerHTML', 'outerHTML', 'insertAdjacentHTML', 'fetch(', 'XMLHttpRequest', 'eval(']) {
  assert(!html.includes(forbidden), `No unsafe/remote API: ${forbidden}`);
}
for (const color of ['#171C1A', '#E8EDE9', '#A9B5AE']) assert(html.includes(color));
assert.match(html, /text\s*\{[^}]*font-size:\s*15px/);
assert.match(html, /\.note, \.support\s*\{[^}]*font-size:\s*13px/);
assert.match(html, /\.edge-label\s*\{[^}]*font-size:\s*12px/);
const legend = html.match(/<div class="legend"[^>]*>([\s\S]*?)<\/div>/)[1];
assert.deepEqual([...legend.matchAll(/<\/i>([^<]+)<\/span>/g)].map(match => match[1]),
  ['Source', 'Fact', 'Summary', 'Selected', 'Preview impact', 'Mismatch']);
assert.match(html, /<details id="reading-help">/); // Collapsed, never open by default.
for (const meaning of ['SUPPORTED_BY means', 'COMPOSED_OF means', 'Dashed edges',
  'Inspection never changes the preview target', 'Red is reserved for mismatches']) assert(html.includes(meaning));
assert.match(html, /\.node \.halo\s*\{[^}]*transition: stroke 140ms ease, stroke-width 140ms ease;/);
assert.match(html, /@media \(prefers-reduced-motion: reduce\)\s*\{\s*\.node \.halo \{ transition: none;/);
assert.equal((html.match(/transition:/g) || []).length, 2, 'Only halo strokes transition');
assert(!/@keyframes|animation\s*:/.test(html), 'No mount animations');

function textWidth(text, size = 15, bold = false) {
  return [...String(text)].length * size * (bold ? 0.7 : 0.65);
}

function harness(internalWidth = 650, outerWidth = 1200, crossOrigin = false) {
  let document, resize;
  const ids = {}, messages = [], handlers = {};
  class Element {
    constructor(tag) {
      this.tagName = tag; this.children = []; this.attrs = {}; this.dataset = {};
      this.style = {}; this.listeners = {}; this._text = ''; this.hidden = false;
      this.disabled = false; this.scrollLeft = 0; this.scrollTop = 0;
      this.classList = {toggle: (token, enabled) => {
        const classes = new Set((this.attrs.class || '').split(' ').filter(Boolean));
        if (enabled) classes.add(token); else classes.delete(token);
        this.attrs.class = [...classes].join(' ');
      }};
    }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
    get clientWidth() { return this.hidden ? 0 : internalWidth; }
    get clientHeight() { return this.hidden ? 0 : parseFloat(this.style.height) || 480; }
    setAttribute(name, value) {
      this.attrs[name] = String(value);
      if (name === 'id') ids[value] = this;
      if (name.startsWith('data-')) this.dataset[name.slice(5)] = String(value);
      if (name === 'hidden') this.hidden = true;
    }
    getAttribute(name) { return this.attrs[name]; }
    toggleAttribute(name, force) {
      if (force) this.setAttribute(name, '');
      else { delete this.attrs[name]; if (name === 'hidden') this.hidden = false; }
    }
    appendChild(child) { this.children.push(child); return child; }
    replaceChildren() { this.children = []; this._text = ''; }
    contains(target) { return target === this || this.children.some(child => child.contains(target)); }
    querySelectorAll(selector) {
      return this.children.flatMap(child => {
        const matches = selector.startsWith('.') ? (child.attrs.class || '').split(' ').includes(selector.slice(1))
          : child.tagName === selector;
        return [...(matches ? [child] : []), ...child.querySelectorAll(selector)];
      });
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0]; }
    addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
    dispatch(type, event = {}) {
      if (type === 'click' && this.disabled) return;
      for (const callback of this.listeners[type] || []) callback(event);
    }
    focus() { document.activeElement = this; }
    getBoundingClientRect() {
      return {width: this.clientWidth, height: (ids.viewport?.clientHeight || 480) + 180};
    }
  }
  // Only the static body structure is needed. Script/style are not interpreted
  // by this double; the actual inline script is executed separately below.
  const root = new Element('body'), stack = [root];
  const body = html.split('<body>')[1].split('<script>')[0];
  for (const match of body.matchAll(/<(\/?)([\w-]+)([^>]*)>/g)) {
    const [, closing, tag, attributes] = match;
    if (closing) { stack.pop(); continue; }
    const element = new Element(tag);
    for (const attr of attributes.matchAll(/([\w-]+)(?:="([^"]*)")?/g)) {
      element.setAttribute(attr[1], attr[2] || '');
    }
    stack.at(-1).appendChild(element);
    if (!['br', 'meta', 'input', 'hr'].includes(tag)) stack.push(element);
  }
  document = {
    activeElement: root,
    getElementById: id => ids[id],
    querySelector: selector => root.querySelector(selector),
    querySelectorAll: selector => root.querySelectorAll(selector),
    createElementNS: (_, tag) => new Element(tag),
    createElement: tag => {
      const element = new Element(tag);
      if (tag === 'canvas') element.getContext = () => ({font: '15px system-ui', measureText(text) {
        const size = Number(this.font.match(/([\d.]+)px/)[1]);
        return {width: textWidth(text, size, this.font.startsWith('600 '))};
      }});
      return element;
    },
  };
  const media = {matches: outerWidth < 600, addEventListener: (_, callback) => {media.change = callback;}};
  const parent = {postMessage: message => messages.push(message), matchMedia: () => media};
  Object.defineProperty(parent, 'location', {get() {
    if (crossOrigin) throw new Error('Cross-origin access blocked');
    return {origin: 'http://local.test'};
  }});
  const window = {parent, location: {origin: 'http://local.test'}, addEventListener: (type, callback) => {handlers[type] = callback;}};
  class ResizeObserver { constructor(callback) { resize = callback; } observe() {} }
  vm.runInNewContext(script, {document, window, ResizeObserver, Date, console});
  return {
    ids, messages, document, parent,
    render(payload, disabled = false, sender = parent) {
      handlers.message({source: sender, data: {type: 'streamlit:render', args: {payload}, disabled}});
    },
    resize(width) { internalWidth = width; resize(); },
    parentResize(width) { media.matches = width < 600; media.change?.(); },
    selections: () => messages.filter(message => message.type === 'streamlit:setComponentValue'),
  };
}

// Real fixture identities/relationships, serialized into the documented payload
// shape. Production projection semantics remain covered by the Python tests.
function fixture(file) {
  const data = JSON.parse(readFileSync(new URL(`../data/${file}.json`, import.meta.url), 'utf8'));
  const groups = [['sources', 'SOURCE', 'square', 'active'], ['facts', 'FACT', 'circle', 'valid'],
    ['summaries', 'SUMMARY', 'diamond', 'fresh']];
  const nodes = groups.flatMap(([group, kind, shape, status], column) =>
    [...data[group]].sort((a, b) => a.id.localeCompare(b.id)).map((item, row) => ({
      entity: `${group}.${item.id}`, id: item.id, kind, shape, status, note: status,
      x: 90 + column * 280, y: 60 + row * 115, fill: '#ffffff', stroke: '#42554d',
      selected: false, affected: false,
      ...(kind === 'FACT' ? {support_sources: [...item.sources].sort(),
        active_edge_sources: [...item.sources].sort(), recorded_active_support: [...item.sources]} : {}),
    })));
  const edges = [
    ...data.facts.flatMap(fact => fact.sources.map(source => ({source_entity: `sources.${source}`,
      target_entity: `facts.${fact.id}`, relation: 'SUPPORTED_BY', from: source, to: fact.id}))),
    ...data.summaries.flatMap(summary => summary.facts.map(fact => ({source_entity: `facts.${fact}`,
      target_entity: `summaries.${summary.id}`, relation: 'COMPOSED_OF', from: fact, to: summary.id}))),
  ].map(edge => ({...edge, active: true, color: '#87948b'}));
  return {nodes, edges, context: file, selected: nodes[0].entity, height: 500};
}

function bounds(h, payload, dense = false) {
  const {graph, viewport} = h.ids;
  const [, , width, height] = graph.getAttribute('viewBox').split(' ').map(Number);
  assert.equal(Number(graph.getAttribute('width')), Math.max(650, viewport.clientWidth));
  assert.equal(Number(graph.getAttribute('height')), height);
  assert(height >= 480);
  if (!dense) assert.equal(viewport.clientHeight, height, 'The complete native SVG is visible, not just inside its viewBox');
  const boxes = [];
  function box(x, y, w, h, label) {
    assert(x >= 0 && y >= 0 && x + w <= width + 0.001 && y + h <= height, `In SVG: ${label}`);
    for (const other of boxes) {
      assert(x >= other.x + other.w || other.x >= x + w || y >= other.y + other.h || other.y >= y + h,
        `No label-region overlap: ${label} / ${other.label}`);
    }
    boxes.push({x, y, w, h, label});
  }
  for (const text of [...graph.querySelectorAll('.heading'), ...graph.querySelectorAll('.lane-relation')]) {
    const lines = text.children.length ? text.children : [text];
    const longest = Math.max(...lines.map(line => textWidth(line.textContent, 13)));
    box(Number(text.attrs.x) - longest / 2, Number(text.attrs.y) - 13, longest,
      17 + (lines.length - 1) * 18, text.textContent);
  }
  for (const node of graph.querySelectorAll('.node')) {
    const record = payload.nodes.find(item => item.entity === node.dataset.entity);
    const compact = text => text.replace(/\s+/g, '');
    assert.equal(compact(node.querySelector('.label').textContent), compact(record.id), 'Complete node ID stays visible');
    assert.equal(compact(node.querySelector('.note').textContent), compact(record.note), 'Complete status stays visible');
    const [x, y] = node.getAttribute('transform').match(/-?[\d.]+/g).map(Number);
    const ring = node.querySelector('.focus-ring');
    box(x + Number(ring.attrs.x), y + Number(ring.attrs.y), Number(ring.attrs.width), Number(ring.attrs.height), node.dataset.entity);
    for (const text of node.querySelectorAll('text')) {
      const label = text.attrs.class === 'label', size = label ? 15 : 13;
      const longest = Math.max(...text.children.map(line => textWidth(line.textContent, size, label)));
      const end = y + Number(text.attrs.y) + text.children.slice(1).reduce((sum, line) => sum + Number(line.attrs.dy), 0) + 4;
      assert(x - longest / 2 >= 0 && x + longest / 2 <= width);
      assert(end <= height && (dense || end <= viewport.clientHeight), 'Final label line visible');
    }
  }
  const edges = graph.querySelectorAll('.edge');
  assert.equal(edges.length, payload.edges.length);
  assert.equal(graph.querySelectorAll('marker').length, payload.edges.length);
  edges.forEach((group, index) => {
    const edge = payload.edges[index], chip = group.querySelector('.edge-chip'), label = group.querySelector('.edge-label');
    assert.equal(group.querySelector('title').textContent,
      `${edge.source_entity} → ${edge.target_entity}\n${edge.relation} — ${edge.active ? 'active' : 'retained inactive'}`);
    assert.equal(group.querySelector('path').attrs['stroke-dasharray'], edge.active ? 'none' : '6 4');
    assert.equal(label.textContent, `#${index + 1}`);
    assert(!label.getAttribute('transform'), 'Edge references are horizontal');
    box(Number(chip.attrs.x), Number(chip.attrs.y), Number(chip.attrs.width), Number(chip.attrs.height), label.textContent);
    const row = h.ids['relationship-rows'].children[index];
    assert.equal(row.children[0].textContent, label.textContent);
    assert.equal(row.children[1].textContent, edge.source_entity);
    assert.equal(row.children[2].textContent, edge.relation);
    assert.equal(row.children[3].textContent, edge.target_entity);
    assert.equal(row.children[4].textContent, edge.active ? 'Active' : 'Retained inactive');
  });
  assert.equal(h.ids['entity-list'].children.length, payload.nodes.length);
}

function supportWordBoundaries(h, payload, context) {
  const normalWords = new Set(['Shared', 'support', 'Support', 'active', 'edges', 'sales', 'pricing', 'none']);
  const facts = payload.nodes.filter(node => node.kind === 'FACT' &&
    h.ids.graph.querySelectorAll('.node').some(item => item.dataset.entity === node.entity && item.attrs['aria-pressed'] === 'true'));
  const supports = h.ids.graph.querySelectorAll('.support');
  assert.equal(supports.length, facts.length, `${context}: only the activated fact has support text`);
  for (const fact of facts) {
    const node = h.ids.graph.querySelectorAll('.node').find(item => item.dataset.entity === fact.entity);
    const support = node.querySelector('.support');
    assert(support, `${context}: support text for ${fact.entity}`);
    const lines = support.children.map(line => line.textContent);
    assert(lines.length > 0, `${context}: support text has rendered lines`);
    const count = fact.support_sources.length;
    const expected = `${count > 1 ? 'Shared support' : 'Support'}: ${fact.active_edge_sources.length}/${count} active edges · ${fact.support_sources.join(', ') || 'none'}`;
    const compact = text => text.replace(/\s+/g, '');
    assert.equal(lines.map(compact).join(''), compact(expected), `${context}: complete support text for ${fact.entity}`);
    let offset = 0;
    const boundaries = lines.slice(0, -1).map(line => (offset += compact(line).length));
    offset = 0;
    for (const token of expected.split(/\s+/)) {
      const word = token.replace(/[,:]$/, '');
      // Check prose and short source names, not overlong identifiers whose
      // character-level fallback is intentional (e.g. installation_guide).
      if (normalWords.has(word)) {
        assert(!boundaries.some(boundary => boundary > offset && boundary < offset + word.length),
          `${context}: ${fact.entity} must not split "${word}" across support lines: ${JSON.stringify(lines)}`);
      }
      offset += token.length;
    }
  }
}

function distinctRelationRails(h, payload, context) {
  const kinds = new Map(payload.nodes.map(node => [node.entity, node.kind]));
  const lanes = new Map();
  h.ids.graph.querySelectorAll('.edge').forEach((group, index) => {
    const edge = payload.edges[index];
    const lane = `${kinds.get(edge.source_entity)}:${kinds.get(edge.target_entity)}`;
    // Each H x V y pair starts a vertical rail in the orthogonal edge path.
    const d = group.querySelector('path').getAttribute('d');
    const rails = [...d.matchAll(/\bH\s+(-?[\d.]+)\s+V\b/g)].map(match => Number(match[1]));
    assert.equal(rails.length, 2, `${context}: edge #${index + 1} has two vertical rails: ${d}`);
    assert(rails.every(Number.isFinite), `${context}: finite rail coordinates`);
    if (!lanes.has(lane)) lanes.set(lane, []);
    lanes.get(lane).push(rails);
  });
  assert.equal(lanes.size, 2, `${context}: both relation lanes are checked`);
  for (const [lane, rails] of lanes) {
    assert(rails.length > 1, `${context}: ${lane} exercises competing edge rails`);
    assert.equal(new Set(rails.flat()).size, rails.length * 2,
      `${context}: ${lane} paths must have distinct vertical rail x values`);
    for (const side of [0, 1]) {
      const values = rails.map(pair => pair[side]);
      assert(Math.max(...values) - Math.min(...values) <= 8 + 0.001,
        `${context}: ${lane} rail offsets stay within an 8px band`);
    }
  }
}

for (const file of ['demo', 'documentation']) {
  const payload = fixture(file), snapshot = JSON.stringify(payload);
  for (const width of [650, 719, 840, 1100]) {
    const h = harness(width);
    h.render(payload);
    assert.equal(h.ids.viewport.hidden, false);
    assert.equal(h.ids.zoom.textContent, '100%');
    bounds(h, payload);
    assert.equal(h.ids.graph.querySelectorAll('.support').length, 0, 'Idle support labels are hidden');
    assert.equal(h.ids.graph.querySelectorAll('.lane-relation').length, 0, 'Default selection does not disclose lanes');
    distinctRelationRails(h, payload, `${file} at ${width}px`);
    assert.equal(h.selections().length, 0);
    const impact = () => h.ids.graph.querySelectorAll('.node').map(node => [node.dataset.entity, node.dataset.affected]);
    const initialImpact = impact();
    for (const [index, fact] of payload.nodes.filter(node => node.kind === 'FACT').entries()) {
      const target = h.ids.graph.querySelectorAll('.node').find(node => node.dataset.entity === fact.entity);
      target.focus();
      if (index % 2) target.dispatch('click');
      else target.dispatch('keydown', {key: 'Enter', preventDefault() {}});
      assert.equal(h.document.activeElement.dataset.entity, fact.entity, 'Focus survives disclosure layout');
      assert.equal(h.ids.graph.querySelectorAll('.lane-relation').length, 2);
      assert.deepEqual(h.ids.graph.querySelectorAll('.lane-relation').map(node => node.textContent), ['SUPPORTED_BY', 'COMPOSED_OF']);
      supportWordBoundaries(h, payload, `${file} at ${width}px`);
      bounds(h, payload);
      distinctRelationRails(h, payload, `${file} activated at ${width}px`);
      assert.deepEqual(impact(), initialImpact, 'Inspection does not change preview impact');
    }
    h.ids.graph.querySelectorAll('.node').find(node => node.dataset.entity === payload.nodes[0].entity).dispatch('click');
    assert.equal(h.ids.graph.querySelectorAll('.support').length, 0, 'Support collapses when inspecting a source');
    bounds(h, payload);
    const changed = structuredClone(payload);
    changed.nodes[0].status = 'revoked'; changed.nodes[0].note = 'revoked'; changed.edges[0].active = false;
    h.render(changed); bounds(h, changed);
    const painted = () => h.ids.graph.querySelector('.node').children.find(child => child.attrs.stroke);
    assert.equal(painted().attrs.fill, '#303D35', 'Ordinary invalid/revoked states stay neutral');
    changed.nodes[0].note = 'revoked / MISMATCH';
    h.render(changed); bounds(h, changed);
    assert.equal(painted().attrs.fill, '#51312F', 'Only explicit mismatch evidence gets red');
  }
  assert.equal(JSON.stringify(payload), snapshot, 'Rendering does not mutate the payload');
}

// A preselected fact (including a new server selection) is not activation.
// Use real endpoints and mixed impact/inactive evidence, not selection-derived impact.
const disclosurePayload = fixture('demo');
const factEntity = disclosurePayload.nodes.find(node => node.kind === 'FACT').entity;
disclosurePayload.selected = factEntity;
disclosurePayload.nodes.forEach(node => { node.affected = node.kind !== 'SOURCE'; });
disclosurePayload.edges[0].active = false;
const disclosureSnapshot = JSON.stringify(disclosurePayload);
const disclosure = harness(650);
const graphNode = entity => disclosure.ids.graph.querySelectorAll('.node').find(node => node.dataset.entity === entity);
const hiddenDetails = () => {
  assert.equal(disclosure.ids.graph.querySelectorAll('.lane-relation').length, 0);
  assert.equal(disclosure.ids.graph.querySelectorAll('.support').length, 0);
};
disclosure.render(disclosurePayload, true);
graphNode(factEntity).dispatch('click');
graphNode(factEntity).dispatch('keydown', {key: ' ', preventDefault() {}});
hiddenDetails();
assert.equal(disclosure.selections().length, 0, 'Disabled nodes cannot disclose or select');
disclosure.render(disclosurePayload);
hiddenDetails();
disclosure.ids['reading-help'].open = true;
disclosure.ids['reading-help'].dispatch('toggle');
hiddenDetails(); // Definitions are available in help, lane labels still need activation.
disclosure.ids['table-view'].dispatch('click');
hiddenDetails();
assert.equal(disclosure.selections().length, 0, 'Opening help/table does not select');
const endpoint = disclosure.ids['relationship-rows'].children[0].children[3].children[0];
endpoint.focus(); endpoint.dispatch('click'); // Native button also handles Enter/Space in a browser.
assert.equal(disclosure.selections()[0].value.entity, endpoint.dataset.entity);
assert(disclosure.ids.relationships.contains(disclosure.document.activeElement));
disclosure.ids['graph-view'].dispatch('click');
assert.equal(disclosure.ids.graph.querySelectorAll('.lane-relation').length, 2);
supportWordBoundaries(disclosure, disclosurePayload, 'table activation');
bounds(disclosure, disclosurePayload);
assert.deepEqual(disclosure.ids.graph.querySelectorAll('.node').map(node => node.dataset.affected),
  disclosurePayload.nodes.map(node => String(node.affected)));
disclosure.render(disclosurePayload);
assert.equal(disclosure.ids.graph.querySelectorAll('.support').length, 1, 'Disclosure survives server echo');
disclosure.render({...disclosurePayload, context: 'new-snapshot'});
hiddenDetails();
assert.equal(disclosure.selections().length, 1, 'Rerender/context changes never select');
assert.equal(JSON.stringify(disclosurePayload), disclosureSnapshot, 'Disclosure does not mutate evidence');

const payload = fixture('documentation');
for (const [internal, outer, crossOrigin] of [[390, 390, false], [840, 390, false], [390, 1200, true]]) {
  const h = harness(internal, outer, crossOrigin);
  h.render(payload);
  assert.equal(h.ids.viewport.hidden, true, 'Mobile defaults to the relationship table');
  assert.equal(h.ids.relationships.hidden, false);
  assert.equal(h.ids['table-view'].attrs['aria-pressed'], 'true');
  h.ids['entity-list'].children[0].dispatch('click');
  assert.equal(h.selections().length, 1);
  h.ids['graph-view'].dispatch('click');
  assert.equal(h.ids.viewport.hidden, false, 'Graph remains explicitly available on mobile');
  bounds(h, payload);
  h.render(payload);
  assert.equal(h.ids.viewport.hidden, false, 'Explicit choice survives rerender');
}

const h = harness(840), first = payload.nodes[0].entity, second = payload.nodes[1].entity;
assert.equal(h.messages[0].type, 'streamlit:componentReady');
assert(h.messages.every(message => message.isStreamlitMessage));
h.render(payload, false, {});
assert.equal(h.ids.graph.querySelectorAll('.node').length, 0, 'Ignore non-parent render');
h.render(payload);
const node = entity => h.ids.graph.querySelectorAll('.node').find(item => item.dataset.entity === entity);
let prevented = false;
node(second).dispatch('keydown', {key: ' ', preventDefault() { prevented = true; }});
assert(prevented);
node(first).dispatch('keydown', {key: 'Enter', preventDefault() {}});
node(first).dispatch('keydown', {key: 'Enter', repeat: true, preventDefault() {}});
assert.equal(h.selections().length, 2);
assert(Number.isSafeInteger(h.selections()[0].value.sequence));
assert(h.selections()[1].value.sequence > h.selections()[0].value.sequence);
assert.deepEqual(Object.keys(h.selections()[0].value).sort(), ['context', 'entity', 'sequence']);
assert.equal(h.selections()[0].value.context, payload.context);
assert.equal(h.selections()[0].dataType, 'json');
node(second).dispatch('click');
h.render(payload);
assert.equal(node(second).attrs['aria-pressed'], 'true', 'Stale server echo does not undo selection');
h.ids.in.dispatch('click');
const zoomed = h.ids.graph.attrs.width;
h.render(payload);
assert.equal(h.ids.graph.attrs.width, zoomed, 'Camera survives rerender');
h.ids.fit.dispatch('click');
assert(Number(h.ids.graph.attrs.width) <= h.ids.viewport.clientWidth);
assert(Number(h.ids.graph.attrs.height) <= h.ids.viewport.clientHeight);
h.ids['table-view'].dispatch('click');
assert(Number(h.ids.graph.attrs.width) > 0, 'Switching away from Fit must not collapse the SVG');
h.ids['graph-view'].dispatch('click');
h.render(payload, true);
const count = h.selections().length;
node(second).dispatch('click');
h.ids['entity-list'].children[0].dispatch('click');
assert.equal(h.selections().length, count);
assert(h.ids.fit.disabled && h.ids.in.disabled && h.ids.out.disabled);
h.render({...payload, context: 'empty', nodes: [], edges: [], selected: null});
assert(h.ids.graph.hidden && !h.ids.empty.hidden);
assert.equal(h.ids.viewport.clientHeight, 480);
assert.equal(h.ids['relationship-rows'].children.length, 0);

const responsive = harness(840);
responsive.render(payload); responsive.parentResize(390);
assert(responsive.ids.viewport.hidden, 'Parent breakpoint changes update the default view');
responsive.parentResize(1200); responsive.resize(390);
assert(responsive.ids.viewport.hidden, 'Own width is also a responsive fallback');
responsive.resize(650); assert(!responsive.ids.viewport.hidden); bounds(responsive, payload);

const large = fixture('documentation');
large.nodes[0].id = '<img src=x onerror=alert(1)>' + 'long_identifier_'.repeat(20);
for (let index = 0; index < 35; index++) {
  large.nodes.push({...large.nodes[0], entity: `sources.synthetic_${index}`, id: `synthetic_${index}`, y: 1000 + index * 115});
}
const dense = harness(650); dense.render(large);
bounds(dense, large, true);
assert(Number(dense.ids.graph.attrs.height) > dense.ids.viewport.clientHeight, 'Synthetic dense graph can scroll');
assert.equal(dense.ids.graph.querySelectorAll('img').length, 0, 'Untrusted IDs remain text');
console.log('Graph design review: offline geometry, table, palette and protocol checks passed.');
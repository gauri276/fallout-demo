"""Offline graph contract tests; no Streamlit, environment files or network needed.

The optional Node harness executes the shipped inline script against a small DOM
double. It checks protocol, layout arithmetic and interaction, not browser pixels.
"""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from fallout import graph_component
from fallout.database import InMemoryBackend
from fallout.engine import Engine
from fallout.models import Dataset, MemoryState, initial_state
from fallout.presentation import entity_options, graph_records


FRONTEND = Path(graph_component.__file__).with_suffix("") / "index.html"


class GraphComponentTests(unittest.TestCase):
    def setUp(self):
        self.dataset = Dataset.load()
        self.state = initial_state(self.dataset)
        self.context = "dataset:generation:current:revision-0"

    def test_payload_preserves_actual_positions_and_relationships(self):
        projected = graph_records(self.state, "facts.phone_support", "sales")
        payload = graph_component.graph_payload(self.state, "facts.phone_support", "sales", context=self.context)
        self.assertEqual(payload["context"], self.context)
        self.assertEqual(payload["selected"], "facts.phone_support")
        self.assertGreaterEqual(payload["height"], 330)
        self.assertEqual(len(payload["nodes"]), 5)
        self.assertEqual(len(payload["edges"]), 5)
        for actual, expected in zip(payload["nodes"], projected["nodes"]):
            self.assertEqual({key: actual[key] for key in expected}, expected)
        for actual, expected in zip(payload["edges"], projected["edges"]):
            self.assertEqual({key: actual[key] for key in expected}, expected)
            self.assertIn(actual["source_entity"], entity_options(self.state))
            self.assertIn(actual["target_entity"], entity_options(self.state))
        phone = next(node for node in payload["nodes"] if node["entity"] == "facts.phone_support")
        self.assertEqual(phone["support_sources"], ["pricing", "sales"])
        self.assertEqual(phone["active_edge_sources"], ["pricing", "sales"])
        self.assertEqual(json.loads(json.dumps(payload)), payload)

    def test_revoked_inactive_edges_and_verified_mismatches(self):
        engine = Engine(self.dataset, InMemoryBackend(self.dataset))
        operation = engine.revoke_source("sales", engine.confirm_preview(engine.preview_impact("sales")))
        engine.repair_memory(operation["id"], "skip_summary")
        state = engine.backend.read().state
        report = engine.verify_rebuild()
        before, evidence = deepcopy(state), deepcopy(report["mismatches"])
        payload = graph_component.graph_payload(state, "sources.sales", "sales", evidence, self.context)
        nodes = {node["id"]: node for node in payload["nodes"]}
        self.assertEqual(nodes["sales"]["status"], "revoked")
        self.assertEqual(nodes["discount_20"]["status"], "invalid")
        self.assertEqual(nodes["phone_support"]["active_edge_sources"], ["pricing"])
        self.assertEqual(nodes["phone_support"]["support_sources"], ["pricing", "sales"])
        self.assertIn("MISMATCH", nodes["customer_summary"]["note"])
        self.assertEqual(nodes["customer_summary"]["stroke"], "#a42b27")
        self.assertEqual(sum(not edge["active"] for edge in payload["edges"]), 3)
        self.assertEqual(len(payload["edges"]), 5)  # Retained, not deleted.
        self.assertEqual(state, before)
        self.assertEqual(evidence, report["mismatches"])
        unverified = graph_component.graph_payload(state, None, "sales")
        self.assertFalse(any("MISMATCH" in node["note"] for node in unverified["nodes"]))

    def test_no_mutation_or_silent_repair_of_support(self):
        self.state.facts["phone_support"]["active_support"] = ["sales"]
        before = deepcopy(self.state)
        payload = graph_component.graph_payload(self.state, None, "pricing")
        phone = next(node for node in payload["nodes"] if node["id"] == "phone_support")
        self.assertEqual(phone["recorded_active_support"], ["sales"])
        self.assertEqual(phone["active_edge_sources"], ["pricing", "sales"])
        phone["recorded_active_support"].append("not_real")
        phone["support_sources"].clear()
        payload["edges"][0]["active"] = False
        self.assertEqual(self.state, before)

    def test_empty_graph_and_absent_endpoints(self):
        state = MemoryState()
        before = deepcopy(state)
        payload = graph_component.graph_payload(state, "facts.unknown", "missing", context=self.context)
        self.assertEqual(payload, {"nodes": [], "edges": [], "height": 330,
                                   "selected": None, "context": self.context})
        self.assertEqual(state, before)
        self.assertIsNone(graph_component.validated_selection(
            {"entity": "facts.unknown", "context": self.context, "sequence": 1}, self.context, []))
        self.state.support_edges.append(("missing", "phone_support"))
        self.assertEqual(len(graph_component.graph_payload(self.state, None, "sales")["edges"]), 5)

    def test_valid_event_returns_entity_without_mutation(self):
        value = {"entity": "facts.phone_support", "context": self.context, "sequence": 1}
        options = entity_options(self.state)
        before = deepcopy((value, options, self.state))
        self.assertEqual(graph_component.validated_selection(value, self.context, options), "facts.phone_support")
        self.assertEqual((value, options, self.state), before)
        value["sequence"] = 2**53 - 1
        self.assertEqual(graph_component.validated_selection(value, self.context, options), "facts.phone_support")

    def test_malformed_stale_unknown_events_are_rejected(self):
        good = {"entity": "facts.phone_support", "context": self.context, "sequence": 1}
        invalid = [None, [], "facts.phone_support", 1, {}, {"entity": good["entity"]},
                   {**good, "extra": True}, {**good, "entity": "sources.phone_support"},
                   {**good, "entity": []}, {**good, "entity": None},
                   {**good, "context": "old-generation"}, {**good, "context": None},
                   {**good, "context": []}]
        invalid.extend({**good, "sequence": value} for value in
                       (True, False, 0, -1, 1.0, float("nan"), float("inf"), "1", None, [], 2**53))
        options = entity_options(self.state)
        for value in invalid:
            with self.subTest(value=value):
                self.assertIsNone(graph_component.validated_selection(value, self.context, options))
        self.assertIsNone(graph_component.validated_selection(good, None, options))
        self.assertEqual(options, entity_options(self.state))

    def test_context_requires_string(self):
        with self.assertRaises(TypeError):
            graph_component.graph_payload(self.state, None, "sales", context={"revision": 1})

    def test_import_is_lazy_without_streamlit(self):
        # Execute a fresh module with imports explicitly blocked. No UI startup.
        with patch.dict(sys.modules, {"streamlit": None, "streamlit.components": None,
                                      "streamlit.components.v1": None}):
            spec = importlib.util.spec_from_file_location("fallout._isolated_graph_component", graph_component.__file__)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertIsNone(module._component)
            self.assertEqual(len(module.graph_payload(self.state, None, "sales")["nodes"]), 5)

    def test_render_declares_local_path_lazily_once_and_returns_raw_value(self):
        value = {"entity": "facts.phone_support", "context": self.context, "sequence": 10}
        component = Mock(return_value=value)
        api = ModuleType("streamlit.components.v1")
        api.declare_component = Mock(return_value=component)
        modules = {"streamlit": ModuleType("streamlit"),
                   "streamlit.components": ModuleType("streamlit.components"),
                   "streamlit.components.v1": api}
        before = deepcopy(self.state)
        with patch.dict(sys.modules, modules), patch.object(graph_component, "_component", None):
            returned = graph_component.render_graph(self.state, None, "sales", context=self.context)
            self.assertIs(returned, value)
            graph_component.render_graph(self.state, "facts.phone_support", "sales", context="next-revision")
            api.declare_component.assert_called_once_with("fallout_lineage_graph", path=str(FRONTEND.parent))
            for call in component.call_args_list:
                self.assertEqual(call.kwargs["key"], "lineage_graph")
                self.assertIsNone(call.kwargs["default"])
            graph_component.render_graph(self.state, None, "sales", key="other_graph")
            self.assertEqual(component.call_args.kwargs["key"], "other_graph")
        self.assertEqual(self.state, before)

    def test_frontend_has_only_inline_resources_and_safe_dom(self):
        html = FRONTEND.read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"<script\b", html)), 1)
        self.assertNotRegex(html, r"<(?:script|link|img|iframe)\b[^>]*(?:src|href)=")
        for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "fetch(",
                          "XMLHttpRequest", "eval(", "localStorage", "sessionStorage"):
            self.assertNotIn(forbidden, html)
        self.assertIn("textContent", html)
        self.assertIn("ResizeObserver", html)
        self.assertIn(":focus-visible", html)

    def test_progressive_disclosure_legend_and_motion_contract(self):
        html = FRONTEND.read_text(encoding="utf-8")
        legend = re.search(r'<div class="legend"[^>]*>(.*?)</div>', html, re.S).group(1)
        expected = ["Source", "Fact", "Summary", "Selected", "Preview impact", "Mismatch"]
        self.assertEqual(re.findall(r'</i>([^<]+)</span>', legend), expected)
        self.assertIn('<details id="reading-help">', html)
        meanings = ["SUPPORTED_BY means", "COMPOSED_OF means", "Dashed edges", "Inspection never changes the preview target", "Red is reserved for mismatches"]
        self.assertTrue(all(meaning in html for meaning in meanings))
        self.assertIn("transition: stroke 140ms ease, stroke-width 140ms ease", html)
        self.assertRegex(html, r"@media \(prefers-reduced-motion: reduce\)\s*\{\s*\.node \.halo \{ transition: none;")
        self.assertNotRegex(html, r"@keyframes|animation\s*:")

    def test_inline_javascript_protocol_interactions_and_responsive_layout(self):
        # Explicit local candidates avoid reading PATH/HOME or invoking shims.
        candidates = [Path("/usr/bin/node"), Path("/usr/local/bin/node"), Path("/opt/homebrew/bin/node")]
        node = next((path for path in candidates if path.is_file()), None)
        if node is None:
            self.skipTest("No local Node executable; Python/static tests still run")
        source = re.search(r"<script>(.*?)</script>", FRONTEND.read_text(encoding="utf-8"), re.S).group(1)
        payload = graph_component.graph_payload(self.state, "sources.sales", "sales", context=self.context)
        result = subprocess.run([str(node), "-e", DOM_HARNESS],
                                input=json.dumps({"source": source, "payload": payload,
                                                  "html": FRONTEND.read_text(encoding="utf-8")}), text=True,
                                capture_output=True, timeout=30, env={})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("protocol, interaction, camera and layout checks passed", result.stdout)


DOM_HARNESS = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {source, payload, html} = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
let document, availableWidth = 840;
const ids = {};
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.attrs = {}; this.dataset = {};
    this.style = {}; this.listeners = {}; this.textContent = ''; this.scrollLeft = 0; this.scrollTop = 0;
    this.hidden = false; this.disabled = false;
    this.classList = {toggle: (token, enabled) => {
      const classes = new Set((this.attrs.class || '').split(' ').filter(Boolean));
      if (enabled) classes.add(token); else classes.delete(token);
      this.attrs.class = [...classes].join(' ');
    }};
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  get clientWidth() { return this.hidden ? 0 : availableWidth; }
  get clientHeight() { return this.hidden ? 0 : parseFloat(this.style.height) || 480; }
  setAttribute(name, value) { this.attrs[name] = String(value);
    if (name === 'id') ids[value] = this;
    if (name === 'hidden') this.hidden = true;
    if (name === 'disabled') this.disabled = true;
    if (name.startsWith('data-')) this.dataset[name.slice(5)] = String(value); }
  getAttribute(name) { return this.attrs[name]; }
  toggleAttribute(name, force) { if (force) this.setAttribute(name, ''); else {
    delete this.attrs[name]; if (name === 'hidden') this.hidden = false;
  } }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren() { this.children = []; this._text = ''; }
  contains(target) { return target === this || this.children.some(child => child.contains(target)); }
  querySelectorAll(selector) {
    const result = [];
    for (const child of this.children) {
      if (selector.startsWith('.') ? (child.attrs.class || '').split(' ').includes(selector.slice(1))
                                  : child.tagName === selector) result.push(child);
      result.push(...child.querySelectorAll(selector));
    }
    return result;
  }
  addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
  dispatch(type, event = {}) { if (type === 'click' && this.disabled) return;
    for (const listener of this.listeners[type] || []) listener(event); }
  focus() { document.activeElement = this; }
  getBoundingClientRect() { return {height: 510, width: this.clientWidth}; }
}
// Build the real static hierarchy so containment, focus and table selections
// exercise the same DOM as the shipped component, not detached placeholder IDs.
const root = new Element('body'), stack = [root];
for (const match of html.split('<body>')[1].split('<script>')[0].matchAll(/<(\/?)([\w-]+)([^>]*)>/g)) {
  const [, closing, tag, attributes] = match;
  if (closing) { stack.pop(); continue; }
  const element = new Element(tag);
  for (const attr of attributes.matchAll(/([\w-]+)(?:="([^"]*)")?/g)) element.setAttribute(attr[1], attr[2] || '');
  stack.at(-1).appendChild(element);
  if (!['br', 'meta', 'input', 'hr'].includes(tag)) stack.push(element);
}
document = {activeElement: root, getElementById: id => ids[id],
  querySelector: selector => root.querySelectorAll(selector)[0],
  querySelectorAll: selector => root.querySelectorAll(selector),
  createElementNS: (_, tag) => new Element(tag),
  createElement: tag => {
    const element = new Element(tag);
    if (tag === 'canvas') element.getContext = () => ({measureText: text => ({width: [...text].length * 7})});
    return element;
  }};
const messages = [], handlers = {};
const parent = {postMessage: message => messages.push(message)};
const window = {parent, addEventListener: (type, callback) => {handlers[type] = callback;}};
let resize;
class ResizeObserver { constructor(callback) {resize = callback;} observe() {} }
vm.runInNewContext(source, {document, window, ResizeObserver, console, Date});
const render = (data, sender = parent, disabled = false) => handlers.message({source: sender, data: {type: 'streamlit:render', args: {payload: data}, disabled}});
const events = () => messages.filter(message => message.type === 'streamlit:setComponentValue');
const nodes = () => ids.graph.querySelectorAll('.node');
const button = entity => nodes().find(node => node.dataset.entity === entity);
const selected = () => nodes().find(node => node.getAttribute('aria-pressed') === 'true')?.dataset.entity;
assert.equal(messages[0].type, 'streamlit:componentReady');
assert.equal(messages[0].apiVersion, 1);
assert(messages.every(message => message.isStreamlitMessage === true));
assert(messages.some(message => message.type === 'streamlit:setFrameHeight' && message.height >= 330));
render(payload, {}); assert.equal(nodes().length, 0); // Ignore non-parent render.
render(payload); assert.equal(nodes().length, 5);
assert.equal(ids.graph.getAttribute('hidden'), undefined);
assert.equal(events().length, 0);
assert.equal(selected(), 'sources.sales');
assert.equal(ids.graph.querySelectorAll('.edge').length, 5);
assert.equal(ids.graph.querySelectorAll('marker').length, 5);
assert.equal(ids.graph.querySelectorAll('.edge-label').length, 5);
assert.equal(ids['relationship-rows'].children.length, payload.edges.length);
payload.edges.forEach((edge, index) => {
  const cells = ids['relationship-rows'].children[index].children;
  assert.deepEqual(cells.map(cell => cell.textContent),
    [`#${index + 1}`, edge.source_entity, edge.relation, edge.target_entity, 'Active']);
  assert.equal(ids.graph.querySelectorAll('.edge-label')[index].textContent, `#${index + 1}`);
});
const phone = button('facts.phone_support');
assert(phone.getAttribute('aria-label').includes('active_support: pricing, sales'));
assert.equal(ids.graph.querySelectorAll('.support').length, 0);
assert.equal(ids.graph.querySelectorAll('.lane-relation').length, 0);
const preview = nodes().map(node => node.dataset.affected);
phone.dispatch('click'); assert.equal(selected(), 'facts.phone_support');
assert.equal(ids.graph.querySelectorAll('.lane-relation').length, 2);
assert.equal(ids.graph.querySelectorAll('.support').length, 1);
assert(button('facts.phone_support').querySelectorAll('.support')[0].children.map(t => t.textContent).join('').includes('Shared support: 2/2'));
assert.deepEqual(nodes().map(node => node.dataset.affected), preview);
assert(ids.relationships.querySelectorAll('.entity-button').filter(node => node.dataset.entity === 'facts.phone_support')
  .every(node => node.getAttribute('aria-pressed') === 'true'));
assert.equal(events().length, 1);
assert.equal(events()[0].dataType, 'json');
assert.deepEqual(Object.keys(events()[0].value).sort(), ['context', 'entity', 'sequence']);
assert.equal(events()[0].value.context, payload.context);
assert(Number.isSafeInteger(events()[0].value.sequence));
render(payload); assert.equal(selected(), 'facts.phone_support'); // Old server echo must not undo click.
let prevented = false;
button('facts.phone_support').focus();
button('facts.phone_support').dispatch('keydown', {key: ' ', preventDefault: () => {prevented = true;}});
assert(prevented); assert.equal(events().length, 2);
button('facts.phone_support').dispatch('keydown', {key: 'Enter', repeat: true, preventDefault() {}});
assert.equal(events().length, 2);
button('facts.phone_support').dispatch('keydown', {key: 'ArrowRight'});
assert.equal(events().length, 2);
button('sources.pricing').dispatch('keydown', {key: 'Enter', preventDefault() {}});
assert.equal(events().length, 3);
assert.equal(ids.graph.querySelectorAll('.support').length, 0);
assert.equal(ids.graph.querySelectorAll('.lane-relation').length, 2);
assert(events()[2].value.sequence > events()[1].value.sequence);
ids.in.dispatch('click');
const zoomed = ids.graph.getAttribute('width');
ids.viewport.scrollTop = 45; ids.viewport.scrollLeft = 20;
render({...payload, selected: 'sources.pricing'});
assert.equal(ids.graph.getAttribute('width'), zoomed);
assert.equal(ids.viewport.scrollTop, 45); assert.equal(ids.viewport.scrollLeft, 20);
assert.equal(document.activeElement.dataset.entity, 'facts.phone_support');
assert.equal(selected(), 'sources.pricing');
ids.out.dispatch('click'); assert(Number(ids.graph.getAttribute('width')) < Number(zoomed));
ids.fit.dispatch('click');
assert(Number(ids.graph.getAttribute('width')) <= ids.viewport.clientWidth);
assert(Number(ids.graph.getAttribute('height')) <= ids.viewport.clientHeight + .001);
assert.equal(events().length, 3); // No camera/render/resize selection events.
render({...payload, context: 'next', selected: 'sources.sales'});
assert.equal(ids.zoom.textContent, '100%'); assert.equal(ids.viewport.scrollTop, 0);
assert.equal(selected(), 'sources.sales');
assert.equal(ids.graph.querySelectorAll('.lane-relation').length, 0, 'New context resets disclosure');
assert.equal(ids.graph.querySelectorAll('.support').length, 0);

function assertTextBounds() {
  const [, , width, height] = ids.graph.getAttribute('viewBox').split(' ').map(Number);
  const boxes = [];
  for (const node of nodes()) {
    const [x, y] = node.getAttribute('transform').match(/-?[\d.]+/g).map(Number);
    const ring = node.querySelectorAll('.focus-ring')[0];
    const box = {x: x + Number(ring.getAttribute('x')), y: y + Number(ring.getAttribute('y')),
                 w: Number(ring.getAttribute('width')), h: Number(ring.getAttribute('height'))};
    for (const other of boxes) {
      assert(box.x >= other.x + other.w || other.x >= box.x + box.w ||
             box.y >= other.y + other.h || other.y >= box.y + box.h, 'node label regions do not overlap');
    }
    boxes.push(box);
    for (const text of node.querySelectorAll('text')) {
      const tx = Number(text.getAttribute('x')), ty = Number(text.getAttribute('y'));
      const longest = Math.max(...text.children.map(line => [...line.textContent].length * 7));
      const start = x + tx - (text.getAttribute('text-anchor') === 'middle' ? longest / 2 : 0);
      assert(start >= 0 && start + longest <= width, 'label within horizontal bounds');
      const leading = text.children.slice(1).reduce((sum, line) => sum + Number(line.getAttribute('dy')), 0);
      assert(y + ty - 12 >= 0 && y + ty + leading + 4 <= height, 'label within vertical bounds');
    }
  }
}
assertTextBounds();
const long = JSON.parse(JSON.stringify(payload));
long.nodes[0].id = 'a'.repeat(64);
long.nodes[1].id = '<img src=x onerror=alert(1)>';
for (const available of [320, 390, 719, 840]) {
  availableWidth = available; resize(); render(long);
  assert.equal(events().length, 3); assertTextBounds();
  assert.equal(ids.zoom.textContent, '100%');
  assert.equal(ids.viewport.hidden, available < 600);
  assert.equal(ids.relationships.hidden, available >= 600);
  assert.equal((ids.relationships.attrs.class || '').includes('compact'), available < 600);
  const [, , width, height] = ids.graph.getAttribute('viewBox').split(' ').map(Number);
  assert.equal(width, Math.max(650, available)); // Native three lanes; mobile defaults to table.
  assert.equal(parseFloat(ids.viewport.style.height), height); // Entire fixture, no clipped last row.
  const transforms = nodes().map(node => node.getAttribute('transform').match(/[\d.]+/g).map(Number));
  assert.equal(new Set(transforms.map(([x]) => x)).size, 3);
  for (let index = 1; index < payload.nodes.length; index++) {
    if (payload.nodes[index].kind === payload.nodes[index - 1].kind) assert(transforms[index][1] > transforms[index - 1][1]);
  }
  assert.equal(ids.graph.querySelectorAll('img').length, 0); // Text, never parsed markup.
}
const inactive = JSON.parse(JSON.stringify(payload));
inactive.edges[0].active = false;
inactive.nodes[0].status = 'revoked'; inactive.nodes[0].note = 'revoked / MISMATCH';
render(inactive);
assert.equal(ids.graph.querySelectorAll('.edge')[0].querySelectorAll('path')[0].getAttribute('stroke-dasharray'), '6 4');
assert(nodes()[0].getAttribute('aria-label').includes('revoked / MISMATCH'));
render(inactive, parent, true);
nodes()[0].dispatch('click'); assert.equal(events().length, 3);
assert(ids.fit.disabled && ids.in.disabled && ids.out.disabled);
assert(nodes().every(node => node.getAttribute('aria-disabled') === 'true' && node.getAttribute('tabindex') === '-1'));
ids['entity-list'].children[0].dispatch('click'); assert.equal(events().length, 3);
assert(ids.relationships.querySelectorAll('.entity-button').every(node => node.disabled));
render(inactive);
ids['table-view'].dispatch('click');
assert(ids.viewport.hidden && !ids.relationships.hidden && ids.camera.hidden);
const tableButton = ids['entity-list'].children[1];
tableButton.focus(); tableButton.dispatch('click');
assert.equal(events().length, 4);
assert.equal(selected(), tableButton.dataset.entity);
render(inactive);
assert(ids.relationships.contains(document.activeElement), 'Table focus stays in table after redraw');
assert.equal(document.activeElement.dataset.entity, tableButton.dataset.entity);
ids['graph-view'].dispatch('click');
assert(!ids.viewport.hidden && ids.relationships.hidden && !ids.camera.hidden);
render({...payload, context: 'empty', nodes: [], edges: [], selected: null});
assert.equal(nodes().length, 0); assert.equal(ids.empty.hidden, false);
assert.equal(ids.empty.textContent, 'No entities exist in this snapshot.');
assert.equal(ids.graph.getAttribute('hidden'), '');
assert(ids.fit.disabled && ids.in.disabled && ids.out.disabled);
assert.equal(ids['relationship-rows'].children.length, 0);
assert.equal(ids['entity-list'].children.length, 0);
assert.equal(ids['no-relationships'].hidden, false);
assert.equal(events().length, 4);
console.log('protocol, interaction, camera and layout checks passed');
"""


if __name__ == "__main__":
    unittest.main()
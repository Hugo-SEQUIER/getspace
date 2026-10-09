import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
import * as content from "../lib/space/document-content.ts";

const require = createRequire(import.meta.url);

// Compile the actual TSX with the installed compiler. Unrelated UI dependencies
// are inert here; both production Markdown exports run with real React rendering.
function component(file, imports) {
  const source = readFileSync(new URL(file, import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const module = { exports: {} };
  new Function("require", "module", "exports", compiled)(
    (name) => {
      if (Object.hasOwn(imports, name)) return imports[name];
      if (name === "react" || name === "react/jsx-runtime") return require(name);
      throw new Error(`Unexpected component dependency: ${name}`);
    },
    module,
    module.exports,
  );
  return module.exports;
}

const { DocumentMarkdown } = component("../app/components/space/document-markdown.tsx", {
  "../../../lib/space/document-content": content,
});
const { Markdown } = component("../app/components/space/ui.tsx", {
  "./document-markdown": { DocumentMarkdown },
  "./ui-icon": {}, "./provider": {},
  "../../../lib/space/profile": {},
  "../../../lib/space/agents": {}, "./harness-logo": {},
  "../notifications": {},
});

test("document, task and source readers retain formatting and escape untrusted HTML", () => {
  for (const Component of [DocumentMarkdown, Markdown]) {
    const html = renderToStaticMarkup(createElement(Component, {
      text: "## First\n## Second\n\n**bold** *italic* `code` [source](/safe) <script>alert(1)</script>\n\n- one\n- two\n\n```js\n## literal\n```",
      anchors: true,
    }));
    for (const fragment of ["<h2", ">First</h2>", ">Second</h2>", "<strong>bold</strong>", "<em>italic</em>", '<a href="/safe">source</a>', "&lt;script&gt;", "<li>one</li>", "<code>## literal</code>"])
      assert.ok(html.includes(fragment), fragment);
    assert.ok(!html.includes("<script>"));
    assert.ok(!html.includes("<section>"), "headings are flat, never recursively nested");
  }
});

test("reader node budgets include inline formatting across separate blocks", () => {
  const text = "## *a* *b* *c*\n".repeat(1000);
  for (const Component of [DocumentMarkdown, Markdown]) {
    const html = renderToStaticMarkup(createElement(Component, { text }));
    assert.ok(html.includes("Shown as plain text"));
    assert.ok(html.includes(`<pre>${text}</pre>`), "the fallback preserves all content");
    assert.ok(!html.includes("<h2"));
  }
});

test("shared readers render semantic tables with safe inline content and alignment", () => {
  const text = "| **Name** | Count |\n| :--- | ---: |\n| `a\\|b` [source](/safe) | 12 |\n| <img src=x onerror=alert(1)> | [unsafe](javascript:alert) |";
  for (const Component of [DocumentMarkdown, Markdown]) {
    const html = renderToStaticMarkup(createElement(Component, { text }));
    for (const fragment of [
      'class="sp-markdown-table-scroll"', 'role="region"', 'tabindex="0"', "<table>", "<thead>", "<tbody>",
      '<th scope="col" style="text-align:left"><strong>Name</strong></th>',
      '<td style="text-align:right">12</td>', '<code>a|b</code>', '<a href="/safe">source</a>',
      "&lt;img", "[unsafe](javascript:alert)",
    ]) assert.ok(html.includes(fragment), fragment);
    assert.ok(!html.includes("<img"));
    assert.ok(!html.includes('href="javascript:'));

    const empty = renderToStaticMarkup(createElement(Component, { text: "| Header |\n| --- |" }));
    assert.ok(empty.includes("<thead>"));
    assert.ok(!empty.includes("<tbody>"));

    const large = "| A | B |\n| --- | --- |\n" + "| *a* *b* *c* | *d* *e* *f* |\n".repeat(400);
    const fallback = renderToStaticMarkup(createElement(Component, { text: large }));
    assert.ok(fallback.includes("Shown as plain text"));
    assert.ok(!fallback.includes("<table>"));
    assert.ok(fallback.includes(large));
  }
});

test("maximum-size hostile Markdown stays responsive through actual rendering", () => {
  // A subprocess deadline also bounds a regression to synchronous quadratic work.
  const result = spawnSync(process.execPath, ["--experimental-strip-types", "--input-type=module", "-e", `
    import assert from "node:assert/strict";
    import { documentInline, documentBlocks } from ${JSON.stringify(new URL("../lib/space/document-content.ts", import.meta.url).href)};
    const start = performance.now();
    for (const pattern of ["[", "[a](", "*", "\x60"]) {
      const text = pattern.repeat(Math.ceil(700000 / pattern.length)).slice(0, 700000);
      assert.deepEqual(documentInline(text), [{ type: "text", text }]);
    }
    const headings = "## x\\n".repeat(140000);
    assert.deepEqual(documentBlocks(headings), [{ type: "plain", text: headings }]);
    console.log(JSON.stringify({ milliseconds: performance.now() - start }));
  `], { encoding: "utf8", timeout: 5000 });
  assert.equal(result.error, undefined, String(result.error));
  assert.equal(result.status, 0, result.stderr);
  assert.ok(JSON.parse(result.stdout).milliseconds < 1500, result.stdout);

  for (const Component of [DocumentMarkdown, Markdown]) {
    for (const text of ["## x\n".repeat(140000), "[".repeat(700000)]) {
      const started = performance.now();
      const html = renderToStaticMarkup(createElement(Component, { text }));
      assert.ok(performance.now() - started < 1500);
      assert.ok(html.includes(text));
      assert.ok(!html.includes("<h2"));
      if (text.startsWith("##")) assert.ok(html.includes("Shown as plain text"));
    }
  }
});

test("readers show embedded images with escaped descriptions and keep unsafe media literal", () => {
  const src = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAGUlEQVR4nGPQbcr7TwlmGDVg1IBRA4aLAQDzORwf2MdseAAAAABJRU5ErkJggg==";
  for (const Component of [DocumentMarkdown, Markdown]) {
    const html = renderToStaticMarkup(createElement(Component, { text: `![A "plan" <script>](${src})` }));
    assert.ok(html.includes('<img '));
    assert.ok(html.includes(`src="${src}"`));
    assert.ok(html.includes('alt="A &quot;plan&quot; &lt;script&gt;"'));
    assert.ok(html.includes('loading="lazy"'));
    assert.ok(!html.includes('<script>'));
    for (const destination of ["https://example.com/tracker.png", "data:image/svg+xml;base64,PHN2Zz4=", "javascript:alert"]) {
      const unsafe = renderToStaticMarkup(createElement(Component, { text: `![Image](${destination})` }));
      assert.ok(!unsafe.includes('<img'));
      assert.ok(unsafe.includes(destination));
    }
  }
});

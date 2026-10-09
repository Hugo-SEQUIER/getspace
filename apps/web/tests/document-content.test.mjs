import test from "node:test";
import assert from "node:assert/strict";
import {
  documentBlocks,
  documentInline,
  MAX_MARKDOWN_NODES,
  safeDocumentLink,
  formatDocument,
  safeDocumentImage,
  documentImageMimeType,
  insertDocumentImage,
  documentText,
  documentImageDraft,
  documentImageBody,
  MAX_DOCUMENT_BODY_LENGTH,
  MAX_DOCUMENT_IMAGE_LENGTH,
} from "../lib/space/document-content.ts";

test("document outline ignores code headings and preserves fenced blank lines", () => {
  const blocks = documentBlocks(
    "# Overview\nOpening paragraph.\n\n```md\n## Not a heading\n\nA blank line above.\n```\n\n## Overview\n- First\n- Second\n\n> Quoted\n> context\n\n1. One\n2. Two",
  );
  assert.deepEqual(
    blocks.filter((b) => b.type === "heading").map((b) => b.id),
    ["document-section-0", "document-section-1"],
  );
  assert.equal(
    blocks.find((b) => b.type === "code").text,
    "## Not a heading\n\nA blank line above.",
  );
  assert.deepEqual(
    blocks.filter((b) => b.type === "list").map((b) => b.ordered),
    [false, true],
  );
  assert.equal(blocks.find((b) => b.type === "quote").text, "Quoted\ncontext");
});

test("inline markup preserves ordinary formatting, literal HTML and safe links", () => {
  assert.deepEqual(
    documentInline("Plain **bold** *italic* `code` [source](https://example.com) <img>"),
    [
      { type: "text", text: "Plain " },
      { type: "strong", text: "bold" },
      { type: "text", text: " " },
      { type: "em", text: "italic" },
      { type: "text", text: " " },
      { type: "code", text: "code" },
      { type: "text", text: " " },
      { type: "link", text: "source", href: "https://example.com" },
      { type: "text", text: " <img>" },
    ],
  );
  for (const text of ["[unsafe](javascript:alert)", "[unsafe](//example.com)", "[unfinished](https://example.com", "*line\nbreak*"])
    assert.deepEqual(documentInline(text), [{ type: "text", text }]);
  assert.deepEqual(documentInline("[IPv6](http://[::1]/)"), [
    { type: "link", text: "IPv6", href: "http://[::1]/" },
  ]);
});

test("pipe tables support alignment, optional outer pipes and uneven rows", () => {
  const blocks = documentBlocks(
    "Introduction\n| Name | Status | Count |\n:--- | :-: | ---:\n**Space** | Ready | 12\nShort\nA | B | 3 | ignored\n\nAfterward",
  );
  assert.deepEqual(blocks, [
    { type: "paragraph", text: "Introduction" },
    {
      type: "table",
      header: ["Name", "Status", "Count"],
      align: ["left", "center", "right"],
      rows: [["**Space**", "Ready", "12"], ["Short", "", ""], ["A", "B", "3"]],
    },
    { type: "paragraph", text: "Afterward" },
  ]);
  assert.deepEqual(documentBlocks("Name | Value\r\n--- | ---\r\nA | 1")[0].rows, [["A", "1"]]);
});

test("tables preserve escaped pipes and inline syntax and stop at another block", () => {
  const blocks = documentBlocks(String.raw`| f\|oo | Code |
| --- | --- |
| **a\|b** | \`x\|y\` |
> After the table`.replaceAll("\\`", "`"));
  assert.deepEqual(blocks[0], {
    type: "table", header: ["f|oo", "Code"], align: [null, null],
    rows: [["**a|b**", "`x|y`"]],
  });
  assert.deepEqual(blocks[1], { type: "quote", text: "After the table" });
  assert.deepEqual(documentBlocks("| Only |\n| --- |\n\n## Next")[0].rows, []);
});

test("ordinary pipes, mismatched delimiters and fenced examples stay literal", () => {
  for (const text of ["a | b\nnot a delimiter", "| a | b |\n| --- |\n| c |", "a | b\n--- | nope"]) {
    assert.deepEqual(documentBlocks(text), [{ type: "paragraph", text }]);
  }
  const table = "| Name | Value |\n| --- | --- |\n| A | 1 |";
  assert.deepEqual(documentBlocks("```md\n" + table + "\n```"), [
    { type: "code", language: "md", text: table },
  ]);
});

test("table node budgets bound cells, rows and inline formatting without losing content", () => {
  const inputs = [
    "| A | B |\n| --- | --- |\n" + "| x | y |\n".repeat(MAX_MARKDOWN_NODES),
    "|" + "A|".repeat(MAX_MARKDOWN_NODES) + "\n|" + "---|".repeat(MAX_MARKDOWN_NODES),
    "| A | B |\n| --- | --- |\n" + "| *a* *b* *c* | *d* *e* *f* |\n".repeat(400),
  ];
  for (const text of inputs)
    assert.deepEqual(documentBlocks(text), [{ type: "plain", text }]);
});

test("formatting limits preserve the entire body for plain-text display", () => {
  for (const text of ["## x\n".repeat(MAX_MARKDOWN_NODES + 1), "- x\n".repeat(MAX_MARKDOWN_NODES + 1)])
    assert.deepEqual(documentBlocks(text), [{ type: "plain", text }]);
  assert.equal(documentInline("*x* ".repeat(MAX_MARKDOWN_NODES)), null);
  assert.equal(documentInline("plain", 0), null);
  const formatted = "## *a* *b* *c*\n".repeat(1000);
  assert.deepEqual(documentBlocks(formatted), [{ type: "plain", text: formatted }]);
  assert.deepEqual(documentBlocks("## First\n## Second\nText"), [
    { type: "heading", level: 2, text: "First", id: "document-section-0" },
    { type: "heading", level: 2, text: "Second", id: "document-section-1" },
    { type: "paragraph", text: "Text" },
  ]);
});

test("document links allow normal destinations and reject executable or disguised schemes", () => {
  for (const href of [
    "https://example.com",
    "http://localhost:3101",
    "mailto:a@example.com",
    "/preview",
    "#section",
  ])
    assert.equal(safeDocumentLink(href), true);
  for (const href of [
    "javascript:alert(1)",
    "data:text/html,x",
    "//example.com",
    "/\\example.com",
    "https://example.com\n",
    "java\tscript:alert(1)",
  ])
    assert.equal(safeDocumentLink(href), false);
});

test("formatting preserves surrounding text and keeps inserted content selected", () => {
  const result = formatDocument("before word after", 7, 11, "bold");
  assert.equal(result.text, "before **word** after");
  assert.equal(result.text.slice(result.start, result.end), "word");
  assert.equal(
    formatDocument("first\nsecond\nthird", 0, 13, "list").text,
    "- first\n- second\nthird",
  );
  const code = formatDocument("abcxyz", 3, 3, "code");
  assert.equal(code.text, "abc\n```\ncode\n```\nxyz");
  assert.equal(code.text.slice(code.start, code.end), "code");
  assert.equal(
    formatDocument("\nexisting", 0, 0, "heading").text,
    "## \nexisting",
  );
});

const imageSrc = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAGUlEQVR4nGPQbcr7TwlmGDVg1IBRA4aLAQDzORwf2MdseAAAAABJRU5ErkJggg==";

test("embedded raster images round-trip with descriptions, without remote fetches or executable destinations", () => {
  assert.equal(safeDocumentImage(imageSrc), true);
  assert.deepEqual(documentInline(`Before ![A plan](${imageSrc}) after`), [
    { type: "text", text: "Before " },
    { type: "image", text: "A plan", src: imageSrc },
    { type: "text", text: " after" },
  ]);
  assert.deepEqual(documentInline(`![](${imageSrc})`), [{ type: "image", text: "", src: imageSrc }]);
  for (const src of [
    "https://example.com/tracking.png", "/image.png", "//example.com/image.png", "javascript:alert",
    "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=", "data:text/html;base64,PGltZz4=",
    "data:image/png;base64,PGh0bWw+", imageSrc.replace("image/png", "image/jpeg"),
    imageSrc + "?extra", "data:image/png;base64," + "A".repeat(MAX_DOCUMENT_IMAGE_LENGTH),
  ]) {
    assert.equal(safeDocumentImage(src), false, src.slice(0, 100));
    const literal = `![Untrusted](${src})`;
    assert.deepEqual(documentInline(literal), [{ type: "text", text: literal }]);
  }
  assert.equal(documentImageMimeType(new Uint8Array([255, 216, 255])), "image/jpeg");
  assert.equal(documentImageMimeType(new TextEncoder().encode("GIF89a")), "image/gif");
  assert.equal(documentImageMimeType(new TextEncoder().encode("RIFFabcdWEBP")), "image/webp");
  assert.equal(documentImageMimeType(new TextEncoder().encode("<svg>")), null);
});

test("image insertion preserves surrounding content, selects a safe description and enforces total body size", () => {
  const result = insertDocumentImage("before word after", 7, 11, imageSrc, "A [plan]\\\n");
  assert.equal(result.text, `before \n\n![A  plan](${imageSrc})\n\n after`);
  assert.equal(result.text.slice(result.start, result.end), "A  plan");
  assert.equal(documentText(result.text), "before \n\nA  plan\n\n after");
  assert.ok(!documentText(result.text).includes("base64"));
  assert.throws(() => insertDocumentImage("x".repeat(MAX_DOCUMENT_BODY_LENGTH), 0, 0, imageSrc, "Image"), /no room/);
  assert.throws(() => insertDocumentImage("draft", 0, 0, "https://example.com/image.png", "Image"), /cannot be embedded/);
});

test("image editor references are short, reversible and do not turn removed images into hidden attachments", () => {
  const body = `## Plan\n\n![Floor plan](${imageSrc})\n\nAfterward`;
  const draft = documentImageDraft(body);
  assert.ok(draft.text.includes("![Floor plan](space-image:"));
  assert.ok(!draft.text.includes("base64"));
  assert.equal(Object.keys(draft.images).length, 1);
  assert.equal(documentImageBody(draft.text, draft.images), body);
  const edited = draft.text.replace("Floor plan", "Revised plan");
  assert.equal(documentImageBody(edited, draft.images), body.replace("Floor plan", "Revised plan"));
  const removed = draft.text.replace(/!\[[^\]]+\]\([^)]+\)/, "");
  assert.ok(!documentImageBody(removed, draft.images).includes("base64"));
  const unknown = "![Image](space-image:00000000-0000-0000-0000-000000000000)";
  assert.equal(documentImageBody(unknown, {}), unknown);
});

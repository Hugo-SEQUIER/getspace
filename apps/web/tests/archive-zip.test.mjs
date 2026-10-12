import test from "node:test";
import assert from "node:assert/strict";
import { zipSync, strToU8 } from "fflate";
import { archiveZip, readArchive } from "../lib/space/archive-zip.ts";

const archive = {
  format: "space.archive.v1",
  source_space: "space",
  snapshot: 3,
  exported_at: "2026-10-11T00:00:00Z",
  entries: [{ entity: "resource", current: true, value: { title: "Café ☕" } }],
};

test("an archive written in pieces reads back as the same JSON", async () => {
  const zip = await archiveZip("space.json");
  const text = JSON.stringify(archive);
  for (let start = 0; start < text.length; start += 7) zip.write(text.slice(start, start + 7));
  const blob = zip.finish();
  assert.equal(blob.type, "application/zip");
  assert.deepEqual(JSON.parse(await readArchive(blob, 1024)), archive);
});

test("a plain JSON archive still reads", async () => {
  const text = JSON.stringify(archive);
  assert.equal(await readArchive(new Blob([text]), 1024), text);
});

test("a ZIP is refused when its archive is too large, missing or ambiguous", async () => {
  const big = new Blob([zipSync({ "space.json": strToU8("x".repeat(2048)) })]);
  await assert.rejects(readArchive(big, 1024), /at most/);
  const none = new Blob([zipSync({ "notes.txt": strToU8("hello") })]);
  await assert.rejects(readArchive(none, 1024), /holds no Space archive/);
  const two = new Blob([zipSync({ "a.json": strToU8("{}"), "b.json": strToU8("{}") })]);
  await assert.rejects(readArchive(two, 1024), /one Space archive/);
  const broken = new Blob([new Uint8Array([0x50, 0x4b, 1, 2, 3])]);
  await assert.rejects(readArchive(broken, 1024), /could not be read/);
});

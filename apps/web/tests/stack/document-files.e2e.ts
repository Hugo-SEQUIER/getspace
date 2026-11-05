import { mkdtemp, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { test } from '@e2e-dev/web';
import { expect } from 'e2e';
import { pngFixture } from '../import-fixtures.mjs';
import { spaceId, unique } from './support/stack.ts';

// An original file on the real stack: uploaded through the proxy in 1 MiB parts, stored by
// the backend, read back part by part after a reload and checked whole by the viewer (size
// and SHA-256), then deleted together with its parts.
test('a file over two parts is kept, read back intact from the backend, and deleted with its parts', { session: 'owner' }, async ({ app, screen, browser }) => {
  const title = unique('Stack ledger');
  const rows = Array.from({ length: 90_000 }, (_, row) => `2026-${String(row % 12 + 1).padStart(2, '0')},${row},ledger row ${row}`);
  const csv = Buffer.from(`month,amount,label\n${rows.join('\n')}\n`);
  expect(csv.length, 'the fixture spans three parts').toBeGreaterThan(2 * 1_048_576);
  const path = join(await mkdtemp(join(tmpdir(), 'space-stack-file-')), `${title}.csv`);
  await writeFile(path, csv);

  await app.open('/app/documents');
  await screen.getByRole('button', 'Upload files').tap();
  const dialog = screen.getByRole('dialog', 'Upload files');
  await dialog.getByLabel('Choose a file to import').setInputFiles(path);
  await expect(dialog.getByLabel('Document title')).toHaveValue(title);
  await dialog.getByRole('button', 'Save file').tap();
  await expect(browser).toHaveURL(/\/app\/documents\/[0-9a-f-]{36}$/);
  const id = (await browser.evaluate(() => location.pathname)).split('/').at(-1)!;
  const space = await spaceId(browser);

  // A reload empties the tab's file cache, so the preview below is read from the backend.
  await browser.reload();
  const original = screen.getByRole('region', 'Original file');
  await expect(original.getByText(`${title}.csv`)).toBeVisible();
  await expect(original.getByText(/^CSV · 2\.\d MB$/)).toBeVisible();
  await expect.poll(() => browser.evaluate(() => document.querySelector('.sp-file-text')?.textContent?.slice(0, 18) ?? '')).toBe('month,amount,label');
  await expect(original.getByRole('alert')).toHaveCount(0);
  const saved = await browser.evaluate(async (path: string) => (await fetch(path)).json(), `/api/spaces/${space}/resources/${id}`);
  expect(saved.file).toMatchObject({ name: `${title}.csv`, type: 'text/csv', bytes: csv.length, parts: 3 });
  expect(saved.body, 'agents read the file through its text').toContain('ledger row 1');

  await screen.getByRole('button', 'Delete document').tap();
  await screen.getByRole('dialog', 'Delete document?').getByRole('button', 'Delete document').tap();
  await expect(browser).toHaveURL(/\/app\/documents$/);
  const gone = await browser.evaluate(async (path: string) => (await fetch(path)).status, `/api/spaces/${space}/resources/${id}/file/0`);
  expect(gone, 'the file is gone from the backend with its document').toBe(404);

  // An upload that stops part-way is discarded through the proxy, freeing its staged parts.
  const discarded = await browser.evaluate(async (base: string) => {
    const headers = { 'Content-Type': 'application/json' };
    const staged = await fetch(`${base}/parts/0`, { method: 'POST', headers, body: JSON.stringify({ data: btoa('staged bytes') }) });
    // The app's transport sends every write as JSON, a DELETE included; the proxy requires it.
    const removed = await fetch(base, { method: 'DELETE', headers, body: '{}' });
    return { staged: staged.status, removed: removed.status, body: await removed.json() };
  }, `/api/spaces/${space}/files/${crypto.randomUUID()}`);
  expect(discarded).toEqual({ staged: 200, removed: 200, body: { discarded: 1 } });
});

// A picture on the real stack: kept as its original bytes with the thumbnail and size the
// browser read, both stored by the backend; after a reload the Files tab reads the
// thumbnail through its own route, and deleting the document removes it with the file.
test('a picture keeps its thumbnail and size on the backend and the Files tab reads it back', { session: 'owner' }, async ({ app, screen, browser }) => {
  const title = unique('Stack garden');
  const png = pngFixture(640, 480);
  const path = join(await mkdtemp(join(tmpdir(), 'space-stack-picture-')), `${title}.png`);
  await writeFile(path, png);

  await app.open('/app/documents');
  await screen.getByRole('button', 'Upload files').tap();
  const dialog = screen.getByRole('dialog', 'Upload files');
  await dialog.getByLabel('Choose a file to import').setInputFiles(path);
  await expect(dialog.getByRole('radio', /Keep the original file/)).toBeChecked();
  await expect(dialog.getByText(/^PNG · [\d.]+ KB · 640×480$/)).toBeVisible();
  await dialog.getByRole('button', 'Save file').tap();
  await expect(browser).toHaveURL(/\/app\/documents\/[0-9a-f-]{36}$/);
  const id = (await browser.evaluate(() => location.pathname)).split('/').at(-1)!;
  const space = await spaceId(browser);

  const saved = await browser.evaluate(async (path: string) => (await fetch(path)).json(), `/api/spaces/${space}/resources/${id}`);
  expect(saved.file).toMatchObject({ name: `${title}.png`, type: 'image/png', bytes: png.length, parts: 1, width: 640, height: 480 });
  expect(saved.file.thumbnail).toMatchObject({ width: 480, height: 360, parts: 1 });
  expect(saved.file.thumbnail.bytes).toBeLessThanOrEqual(65_536);
  const thumbnail = await browser.evaluate(async (path: string) => (await fetch(path)).json(), `/api/spaces/${space}/resources/${id}/file/thumbnail`);
  expect(thumbnail.type).toBe(saved.file.thumbnail.type);
  const bytes = Buffer.from(thumbnail.data, 'base64');
  expect(bytes.length).toBe(saved.file.thumbnail.bytes);
  expect(createHash('sha256').update(bytes).digest('hex')).toBe(saved.file.thumbnail.sha256);

  // A reload empties the tab's caches, so the tile's thumbnail below comes from the backend.
  await browser.goto('/app/documents');
  await screen.getByRole('button', /^Files/).tap();
  const tile = screen.getByRole('list', 'Files').getByRole('link', new RegExp(title));
  await expect(tile.getByText(/^PNG · [\d.]+ KB · 640×480/)).toBeVisible();
  await expect.poll(() => browser.evaluate((name: string) => {
    const link = [...document.querySelectorAll<HTMLAnchorElement>('.sp-file-tile')].find(item => item.textContent?.includes(name));
    const image = link?.querySelector('img');
    return image ? image.naturalWidth : 0;
  }, title)).toBe(480);

  await tile.tap();
  await screen.getByRole('button', 'Delete document').tap();
  await screen.getByRole('dialog', 'Delete document?').getByRole('button', 'Delete document').tap();
  await expect(browser).toHaveURL(/\/app\/documents$/);
  const gone = await browser.evaluate(async (path: string) => (await fetch(path)).status, `/api/spaces/${space}/resources/${id}/file/thumbnail`);
  expect(gone, 'the thumbnail is gone from the backend with its document').toBe(404);
});

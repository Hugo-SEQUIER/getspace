import { mkdtemp, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from '@e2e-dev/web';
import { expect } from 'e2e';
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
  await screen.getByRole('button', 'Upload document').tap();
  const dialog = screen.getByRole('dialog', 'Import a document');
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

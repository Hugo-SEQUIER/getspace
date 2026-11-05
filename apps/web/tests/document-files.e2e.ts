import { createHash, randomBytes } from 'node:crypto';
import { mkdtemp, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect } from 'e2e';
import { docx, pdfFixture, pptx } from './import-fixtures.mjs';
import { FILE_PART_BYTES, test } from './support/space-api.ts';

async function fixture(name: string, bytes: Buffer) {
  const file = join(await mkdtemp(join(tmpdir(), 'space-file-')), name);
  await writeFile(file, bytes);
  return file;
}

// A PDF is kept as the original by default: the reader shows its pages, a download, and the
// text the file holds for search and agents; the library lists its type and size.
test('a PDF keeps its original file with a page preview, download and list hint', async ({ app, screen, browser, space }) => {
  const pdf = pdfFixture('Quarterly plan for the launch.');
  await app.open('/app/documents');
  await screen.getByRole('button', 'Upload document').tap();
  const dialog = screen.getByRole('dialog', 'Import a document');
  await dialog.getByLabel('Choose a file to import').setInputFiles(await fixture('Quarterly plan.pdf', pdf));
  await expect(dialog.getByRole('radio', /Keep the original file/)).toBeChecked();
  await expect(dialog.getByText(/Agents and search can read 5 words/)).toBeVisible();
  await expect(dialog.getByLabel('Document title')).toHaveValue('Quarterly plan');
  await dialog.getByRole('button', 'Save file').tap();

  await expect(browser).toHaveURL(/\/app\/documents\/aaaaaaaa-aaaa-4aaa-8aaa-000000000001$/);
  await expect(screen.getByRole('heading', 'Quarterly plan', { level: 1 })).toBeVisible();
  const original = screen.getByRole('region', 'Original file');
  await expect(original.getByText('Quarterly plan.pdf')).toBeVisible();
  await expect(original.getByText(/^PDF · \d+ B · 1 page$/)).toBeVisible();
  await expect(original.getByRole('img', 'Page 1')).toBeVisible();
  await expect(original.getByRole('button', 'Download')).toBeEnabled();
  await expect(original.getByRole('button', 'Open')).toBeEnabled();

  const [saved] = space.fileCommands as Record<string, string | number>[];
  expect(saved.name).toBe('Quarterly plan.pdf');
  expect(saved.type).toBe('application/pdf');
  expect(saved.bytes).toBe(pdf.length);
  expect(saved.sha256).toBe(createHash('sha256').update(pdf).digest('hex'));
  expect(saved.body).toBe('Quarterly plan for the launch.');
  expect(space.fileParts.get(saved.file_id as string)).toHaveLength(1);
  // The file is saved through its own route, never as a plain page.
  expect(space.resourceCommands).toEqual([]);

  await screen.getByText('Text for search and agents · 5 words').tap();
  await expect(screen.getByText('Quarterly plan for the launch.')).toBeVisible();

  await screen.getByRole('link', 'Documents').first().tap();
  await expect(screen.getByRole('row', /Quarterly plan/).getByText(/^PDF · \d+ B$/)).toBeVisible();
});

// A file larger than one part travels in 1 MiB parts both ways and arrives intact; a type
// Space cannot display is offered as a download only.
test('a multi-part file uploads in parts and downloads intact', async ({ app, screen, browser, space }) => {
  const bytes = randomBytes(FILE_PART_BYTES * 2 + 4321);
  await app.open('/app/documents');
  await screen.getByRole('button', 'Upload document').tap();
  const dialog = screen.getByRole('dialog', 'Import a document');
  await dialog.getByLabel('Choose a file to import').setInputFiles(await fixture('archive.zip', bytes));
  await expect(dialog.getByText('Agents and search see this file’s title, not its contents.')).toBeVisible();
  await expect(dialog.getByRole('radio', /Convert to an editable page/)).toHaveCount(0);
  await dialog.getByRole('button', 'Save file').tap();

  await expect(screen.getByRole('heading', 'archive', { level: 1 })).toBeVisible();
  const [saved] = space.fileCommands as Record<string, string | number>[];
  const parts = space.fileParts.get(saved.file_id as string)!;
  expect(parts).toHaveLength(3);
  expect(parts.slice(0, 2).map(part => Buffer.from(part, 'base64').length)).toEqual([FILE_PART_BYTES, FILE_PART_BYTES]);
  expect(Buffer.concat(parts.map(part => Buffer.from(part, 'base64'))).equals(bytes)).toBe(true);

  const original = screen.getByRole('region', 'Original file');
  await expect(original.getByText('Space can’t show this type of file here. Download it to open it.')).toBeVisible();
  expect(space.fileReads).toEqual([]);
  await original.getByRole('button', 'Download').tap();
  await expect.poll(() => space.fileReads.length).toBe(3);
  await expect(original.getByRole('alert')).toHaveCount(0);
  await expect(browser).toHaveURL(/\/app\/documents\/aaaaaaaa-aaaa-4aaa-8aaa-000000000001$/);
});

// PDF and Word files can still become editable pages, the earlier import behaviour.
test('a PDF can still be converted to an editable page', async ({ app, screen, space }) => {
  await app.open('/app/documents');
  await screen.getByRole('button', 'Upload document').tap();
  const dialog = screen.getByRole('dialog', 'Import a document');
  await dialog.getByLabel('Choose a file to import').setInputFiles(await fixture('notes.pdf', pdfFixture()));
  await dialog.getByRole('radio', /Convert to an editable page/).check();
  await expect(dialog.getByLabel('Imported content')).toHaveValue(/Acquisition notes from PDF\./);
  await dialog.getByRole('button', 'Import document').tap();
  await expect(screen.getByRole('heading', 'notes', { level: 1 })).toBeVisible();
  expect(space.fileCommands).toEqual([]);
  expect(space.resourceCommands).toHaveLength(1);
  expect(space.fileParts.size).toBe(0);
});

// Office markup comes from the file, so Word and PowerPoint previews render in a frame that
// may not run scripts; slides are read in the presentation's order, not file order.
test('Word and PowerPoint files preview in a script-free frame', async ({ app, screen, browser, space }) => {
  await app.open('/app/documents');
  await screen.getByRole('button', 'Upload document').tap();
  let dialog = screen.getByRole('dialog', 'Import a document');
  await dialog.getByLabel('Choose a file to import').setInputFiles(await fixture('plan.pptx', pptx));
  await expect(dialog.getByText('Agents and search can read 11 words from this file.')).toBeVisible();
  await dialog.getByRole('button', 'Save file').tap();
  const slides = browser.frameLocator('iframe[title="Presentation preview"]');
  await expect(slides.getByText('First slide: launch plan')).toBeVisible();
  await expect(slides.getByText('Second slide: budget')).toBeVisible();
  await expect(screen.getByRole('region', 'Original file').getByText(/^PPTX · [\d.]+ KB · 2 slides$/)).toBeVisible();
  expect((space.fileCommands[0] as { body: string }).body).toBe('Slide 1\nFirst slide: launch plan\n\nSlide 2\nSecond slide: budget');

  await screen.getByRole('link', 'Documents').first().tap();
  await screen.getByRole('button', 'Upload document').tap();
  dialog = screen.getByRole('dialog', 'Import a document');
  await dialog.getByLabel('Choose a file to import').setInputFiles(await fixture('notes.docx', docx));
  await expect(dialog.getByRole('radio', /Keep the original file/)).toBeChecked();
  await dialog.getByRole('button', 'Save file').tap();
  const page = browser.frameLocator('iframe[title="Word document preview"]');
  await expect(page.getByText('Acquisition notes from Word.')).toBeVisible();
  const sandboxes = await browser.evaluate(() => [...document.querySelectorAll('.sp-file-frame iframe')].map(frame => frame.getAttribute('sandbox')));
  expect(sandboxes).toEqual(['allow-same-origin']);
});

// An upload that fails part-way discards its staged parts at once rather than leaving them to
// count towards storage until the hourly cleanup.
test('a failed upload discards the parts it already sent', async ({ app, screen, space }) => {
  space.failFilePart = 1;
  await app.open('/app/documents');
  await screen.getByRole('button', 'Upload document').tap();
  const dialog = screen.getByRole('dialog', 'Import a document');
  await dialog.getByLabel('Choose a file to import').setInputFiles(await fixture('backup.zip', randomBytes(FILE_PART_BYTES * 2 + 5)));
  await dialog.getByRole('button', 'Save file').tap();
  await expect(screen.getByText('This Space has reached its storage limit.')).toBeVisible();
  await expect.poll(() => space.fileDiscards.length).toBe(1);
  expect(space.fileParts.size).toBe(0);
  expect(space.fileCommands).toEqual([]);
});

// A preview downloads the whole file, so a file over 25 MB waits until the person asks.
test('a large file loads its preview only when asked', { timeout: 300_000 }, async ({ app, screen, browser, space }) => {
  const rows = Array.from({ length: 1_300_000 }, (_, row) => `row ${row},value ${row}`).join('\n');
  const csv = Buffer.from(`label,value\n${rows}\n`);
  expect(csv.length).toBeGreaterThan(25 * FILE_PART_BYTES);
  await app.open('/app/documents');
  await screen.getByRole('button', 'Upload document').tap();
  const dialog = screen.getByRole('dialog', 'Import a document');
  await dialog.getByLabel('Choose a file to import').setInputFiles(await fixture('ledger.csv', csv));
  await dialog.getByRole('button', 'Save file').tap();
  // Twenty-six parts of base64 through the mocked proxy take a while on a busy machine.
  await expect.poll(
    () => browser.evaluate(() => /^\/app\/documents\/[0-9a-f-]{36}$/.test(location.pathname)),
    { timeout: 180_000 },
  ).toBe(true);
  const original = screen.getByRole('region', 'Original file');
  await expect(original.getByText(/^This file is \d+ MB, so its preview loads when you ask\.$/)).toBeVisible();
  expect(space.fileReads).toEqual([]);
  await original.getByRole('button', 'Load preview').tap();
  await expect.poll(() => space.fileReads.length, { timeout: 30_000 }).toBe(Math.ceil(csv.length / FILE_PART_BYTES));
  await expect(original.getByText(/^label,value/)).toBeVisible();
});

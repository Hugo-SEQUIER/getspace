import { createHash, randomBytes } from 'node:crypto';
import { mkdtemp, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect } from 'e2e';
import { docx, pdfFixture, pngFixture, pptx, webm } from './import-fixtures.mjs';
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
  await screen.getByRole('button', 'Upload files').tap();
  const dialog = screen.getByRole('dialog', 'Upload files');
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
  await screen.getByRole('button', 'Upload files').tap();
  const dialog = screen.getByRole('dialog', 'Upload files');
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
  await screen.getByRole('button', 'Upload files').tap();
  const dialog = screen.getByRole('dialog', 'Upload files');
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
  await screen.getByRole('button', 'Upload files').tap();
  let dialog = screen.getByRole('dialog', 'Upload files');
  await dialog.getByLabel('Choose a file to import').setInputFiles(await fixture('plan.pptx', pptx));
  await expect(dialog.getByText('Agents and search can read 11 words from this file.')).toBeVisible();
  await dialog.getByRole('button', 'Save file').tap();
  const slides = browser.frameLocator('iframe[title="Presentation preview"]');
  await expect(slides.getByText('First slide: launch plan')).toBeVisible();
  await expect(slides.getByText('Second slide: budget')).toBeVisible();
  await expect(screen.getByRole('region', 'Original file').getByText(/^PPTX · [\d.]+ KB · 2 slides$/)).toBeVisible();
  expect((space.fileCommands[0] as { body: string }).body).toBe('Slide 1\nFirst slide: launch plan\n\nSlide 2\nSecond slide: budget');

  await screen.getByRole('link', 'Documents').first().tap();
  await screen.getByRole('button', 'Upload files').tap();
  dialog = screen.getByRole('dialog', 'Upload files');
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
  await screen.getByRole('button', 'Upload files').tap();
  const dialog = screen.getByRole('dialog', 'Upload files');
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
  await screen.getByRole('button', 'Upload files').tap();
  const dialog = screen.getByRole('dialog', 'Upload files');
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

// A picture is kept as the original file, with the thumbnail and size the browser read from
// it; the Files tab shows the thumbnail, read once through its own route.
test('a picture is kept as the original with a thumbnail and shows in Files', async ({ app, screen, browser, space }) => {
  const png = pngFixture(640, 480);
  await app.open('/app/documents');
  await screen.getByRole('button', 'Upload files').tap();
  const dialog = screen.getByRole('dialog', 'Upload files');
  await dialog.getByLabel('Choose a file to import').setInputFiles(await fixture('Garden.png', png));
  await expect(dialog.getByRole('radio', /Keep the original file/)).toBeChecked();
  await expect(dialog.getByRole('radio', /Convert to an editable page/)).toBeEnabled();
  await expect(dialog.getByText(/^PNG · [\d.]+ KB · 640×480$/)).toBeVisible();
  await expect(dialog.getByLabel('Document title')).toHaveValue('Garden');
  await dialog.getByRole('button', 'Save file').tap();

  await expect(screen.getByRole('heading', 'Garden', { level: 1 })).toBeVisible();
  await expect(screen.getByText('Picture')).toBeVisible();
  const original = screen.getByRole('region', 'Original file');
  await expect(original.getByText(/^PNG · [\d.]+ KB · 640×480$/)).toBeVisible();
  await expect(original.getByRole('img', 'Garden')).toBeVisible();
  const [saved] = space.fileCommands as Record<string, unknown>[];
  expect(saved.width).toBe(640);
  expect(saved.height).toBe(480);
  const thumbnail = saved.thumbnail as { id: string; type: string; bytes: number; sha256: string; width: number; height: number };
  expect(['image/webp', 'image/jpeg']).toContain(thumbnail.type);
  expect(thumbnail.width).toBe(480);
  expect(thumbnail.height).toBe(360);
  expect(thumbnail.bytes).toBeLessThanOrEqual(65_536);
  const small = Buffer.from(space.fileParts.get(thumbnail.id)![0], 'base64');
  expect(small.length).toBe(thumbnail.bytes);
  expect(createHash('sha256').update(small).digest('hex')).toBe(thumbnail.sha256);
  // The original is untouched: one part holding the PNG itself, never an embedded copy.
  expect(Buffer.from(space.fileParts.get(saved.file_id as string)![0], 'base64').equals(png)).toBe(true);
  expect(space.resourceCommands).toEqual([]);

  await screen.getByRole('link', 'Documents').first().tap();
  await screen.getByRole('button', /^Files/).tap();
  const tile = screen.getByRole('list', 'Files').getByRole('link', /Garden/);
  await expect(tile.getByText(/^PNG · [\d.]+ KB · 640×480/)).toBeVisible();
  await expect.poll(() => space.fileReads.filter(read => read.endsWith('/thumbnail')).length).toBe(1);
  await expect.poll(() => browser.evaluate(() => {
    const image = document.querySelector<HTMLImageElement>('.sp-file-tile img');
    return image ? image.naturalWidth : 0;
  })).toBe(480);
  await expect(screen.getByText('1 file')).toBeVisible();
});

// A video keeps its original bytes, a frame as its thumbnail and its length; agents see its title.
test('a video is kept with a frame thumbnail and its duration', async ({ app, screen, space }) => {
  await app.open('/app/documents');
  await screen.getByRole('button', 'Upload files').tap();
  const dialog = screen.getByRole('dialog', 'Upload files');
  await dialog.getByLabel('Choose a file to import').setInputFiles(await fixture('Launch clip.webm', webm));
  await expect(dialog.getByText('WEBM · 821 B · 64×48 · 0:02')).toBeVisible();
  await expect(dialog.getByText('Agents and search see this file’s title, not its contents.')).toBeVisible();
  await expect(dialog.getByRole('radio', /Convert to an editable page/)).toHaveCount(0);
  await dialog.getByRole('button', 'Save file').tap();
  await expect(screen.getByRole('heading', 'Launch clip', { level: 1 })).toBeVisible();
  await expect(screen.getByText('Video')).toBeVisible();
  const [saved] = space.fileCommands as Record<string, unknown>[];
  expect(saved.duration).toBeCloseTo(2.4, 1);
  expect(saved.width).toBe(64);
  expect(saved.height).toBe(48);
  const thumbnail = saved.thumbnail as Record<string, unknown>;
  expect(thumbnail.width).toBe(64);
  expect(thumbnail.height).toBe(48);
  expect(saved.body).toBe('');
  await expect(screen.getByRole('region', 'Original file').getByText('WEBM · 821 B · 64×48 · 0:02')).toBeVisible();
});

// Several files chosen together are saved one after another under their own names, without
// a review step, and the library opens on Files with all of them.
test('several files upload at once and land in Files', async ({ app, screen, browser, space }) => {
  await app.open('/app/documents');
  await screen.getByRole('button', 'Upload files').tap();
  const dialog = screen.getByRole('dialog', 'Upload files');
  await dialog.getByLabel('Choose a file to import').setInputFiles([
    await fixture('Quarterly plan.pdf', pdfFixture('Quarterly plan for the launch.')),
    await fixture('Garden.png', pngFixture(320, 240)),
    await fixture('archive.zip', randomBytes(2048)),
  ]);
  const list = dialog.getByRole('list', 'Files to save');
  await expect(list.getByRole('listitem')).toHaveCount(3);
  await expect(dialog.getByLabel('Document title')).toHaveCount(0);
  await dialog.getByRole('button', 'Save 3 files').tap();

  await expect(screen.getByRole('button', /^Files/)).toHaveAttribute('aria-pressed', 'true');
  await expect(browser).toHaveURL(/\/app\/documents$/);
  const grid = screen.getByRole('list', 'Files');
  await expect(grid.getByRole('listitem')).toHaveCount(3);
  await expect(grid.getByRole('link', /Quarterly plan/)).toBeVisible();
  await expect(grid.getByRole('link', /Garden/)).toBeVisible();
  await expect(grid.getByRole('link', /archive/)).toBeVisible();
  const commands = space.fileCommands as Record<string, unknown>[];
  expect(commands.map(command => command.title)).toEqual(['Quarterly plan', 'Garden', 'archive']);
  expect(commands[0].body).toBe('Quarterly plan for the launch.');
  // The PDF's first page and the picture have thumbnails; the archive has none.
  expect(commands.map(command => !!command.thumbnail)).toEqual([true, true, false]);
  expect(space.resourceCommands).toEqual([]);
});

// A file that fails in a batch is reported in its row while the others still save; the
// dialog then offers to retry it.
test('a failed file in a batch is reported while the others save', async ({ app, screen, space }) => {
  space.failFilePart = 1;
  await app.open('/app/documents');
  await screen.getByRole('button', 'Upload files').tap();
  const dialog = screen.getByRole('dialog', 'Upload files');
  await dialog.getByLabel('Choose a file to import').setInputFiles([
    await fixture('notes.txt', Buffer.from('plain notes')),
    await fixture('backup.zip', randomBytes(FILE_PART_BYTES * 2 + 5)),
  ]);
  await dialog.getByRole('button', 'Save 2 files').tap();
  await expect(dialog.getByRole('status', 'notes.txt: Saved')).toBeVisible();
  await expect(dialog.getByRole('status', 'backup.zip: Failed')).toBeVisible();
  await expect(dialog.getByText(/This Space has reached its storage limit\./)).toBeVisible();
  await expect(dialog.getByRole('button', 'Retry the failed file')).toBeEnabled();
  expect(space.fileCommands).toHaveLength(1);
  await expect.poll(() => space.fileDiscards.length).toBe(1);
  await dialog.getByRole('button', 'Done').tap();
  await expect(screen.getByRole('button', /^Files/)).toHaveAttribute('aria-pressed', 'true');
  await expect(screen.getByRole('list', 'Files').getByRole('link', /notes/)).toBeVisible();
});

// Files dragged from the desktop onto the library open the upload dialog with them.
test('files dropped on the library open the upload dialog', async ({ app, screen, browser, space }) => {
  await app.open('/app/documents');
  await expect(screen.getByRole('button', 'Upload files')).toBeVisible();
  await browser.evaluate((encoded: string) => {
    const bytes = Uint8Array.from(atob(encoded), (char) => char.charCodeAt(0));
    const transfer = new DataTransfer();
    transfer.items.add(new File([bytes], 'Dropped plan.pdf', { type: 'application/pdf' }));
    const library = document.querySelector('.sp-doc-library')!;
    library.dispatchEvent(new DragEvent('dragenter', { bubbles: true, cancelable: true, dataTransfer: transfer }));
    library.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: transfer }));
    return true;
  }, pdfFixture('Dropped onto the library.').toString('base64'));
  const dialog = screen.getByRole('dialog', 'Upload files');
  await expect(dialog.getByText('Dropped plan.pdf')).toBeVisible();
  await expect(dialog.getByLabel('Document title')).toHaveValue('Dropped plan');
  await dialog.getByRole('button', 'Save file').tap();
  await expect(screen.getByRole('heading', 'Dropped plan', { level: 1 })).toBeVisible();
  expect((space.fileCommands[0] as { body: string }).body).toBe('Dropped onto the library.');
});

// Pictures up to 10 MB can still become an image page, the earlier behaviour, as a choice.
test('a picture can still be converted to an image page', async ({ app, screen, space }) => {
  await app.open('/app/documents');
  await screen.getByRole('button', 'Upload files').tap();
  const dialog = screen.getByRole('dialog', 'Upload files');
  await dialog.getByLabel('Choose a file to import').setInputFiles(await fixture('Garden.png', pngFixture(320, 240)));
  await dialog.getByRole('radio', /Convert to an editable page/).check();
  await expect(dialog.getByLabel('Image description')).toHaveValue('Garden');
  await dialog.getByRole('button', 'Import document').tap();
  await expect(screen.getByRole('heading', 'Garden', { level: 1 })).toBeVisible();
  expect(space.fileCommands).toEqual([]);
  expect(space.resourceCommands).toHaveLength(1);
  expect((space.resourceCommands[0] as { body: string }).body).toMatch(/^!\[Garden\]\(data:image\/webp;base64,/);
});

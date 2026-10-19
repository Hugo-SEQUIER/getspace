import { createHash } from 'node:crypto';
import { mkdtemp, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { strFromU8, unzipSync } from 'fflate';
import { beforeEach, type Browser } from '@e2e-dev/web';
import { expect, type Screen } from 'e2e';
import {
  defaultColumns, extraTasks, otherId, createdId, taskId, test,
} from './support/space-api.ts';

// The task layouts and drag paths below are written for a large desktop window.
beforeEach(async ({ browser }) => {
  await browser.setViewport({ width: 1440, height: 1000 });
});

const preview = (browser: Browser) => browser.locator('.sp-task-drag-preview');
const column = (browser: Browser, status: string) => browser.locator(`[data-status="${status}"]`);
// Waits two rendered frames, so effects that measure layout after a state change have run.
const nextFrames = (browser: Browser) =>
  browser.evaluate(() => new Promise<null>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve(null)))));

// Parameters: a task title, the target column value, and the part of the card to grab.
// What it does: drags with the mouse past the 6 px activation distance and drops into the column.
// Output: none; checks that the drop neither opened the task nor changed the URL.
async function drag(screen: Screen, browser: Browser, task: string, target: string, from: 'title' | 'project' = 'project') {
  const handle = from === 'title'
    ? screen.getByRole('link', task).getByText(task)
    : browser.locator(`a.sp-board-task-link[aria-label="${task}"] .sp-task-project`);
  const destination = column(browser, target);
  await destination.scrollIntoView();
  const start = (await handle.boundingBox())!;
  const end = (await destination.boundingBox())!;
  const x = start.x + start.width / 2, y = start.y + start.height / 2;
  await browser.mouse.move(x, y);
  await browser.mouse.down();
  await expect(preview(browser)).toHaveCount(0);
  for (const offset of [4, 8, 12]) await browser.mouse.move(x + offset, y);
  await expect(preview(browser)).toBeVisible();
  const toX = end.x + end.width / 2, toY = end.y + 80;
  for (let step = 1; step <= 12; step++)
    await browser.mouse.move(x + 12 + (toX - x - 12) * step / 12, y + (toY - y) * step / 12);
  await expect(browser).toHaveClass(destination, /is-drop-target/);
  await browser.mouse.up();
  await expect(screen.getByRole('dialog', 'Task details')).toHaveCount(0);
  await expect(browser).toHaveURL(/\/tasks(?:\?view=board)?$/);
}

test('board and list views show task summaries without reading task bodies', async ({ app, screen, browser, space }) => {
  await app.open('/app/tasks');
  const card = screen.getByRole('article', 'Keep task content');
  const cardLink = screen.getByRole('link', 'Keep task content');
  await expect(column(browser, 'todo').getByRole('link', 'Keep task content')).toBeVisible();
  await expect(browser.locator('.sp-board-task select')).toHaveCount(0);
  await expect(browser.locator('.sp-board-task .sp-status-dot')).toHaveCount(0);
  await expect(card.getByText('High')).toBeVisible();
  await expect(browser.locator('article[aria-label="Independent task"] .sp-board-task-footer')).toHaveCount(0);
  const navigation = screen.getByRole('navigation', 'Main navigation');
  await expect(browser).toHaveClass(navigation.getByRole('link', 'Tasks'), /\bactive\b/);
  await expect(browser).not.toHaveClass(navigation.getByRole('link', 'Home'), /\bactive\b/);
  await nextFrames(browser);
  expect(space.resourceReads, 'opening the board must not prefetch every task body').toEqual([]);

  await expect(screen.getByRole('button', 'Board')).toHaveAttribute('aria-pressed', 'true');
  await screen.getByLabel('Filter tasks by project').selectOption({ value: 'project-a' });
  await expect(screen.getByRole('link', 'Keep task content')).toBeVisible();
  await expect(screen.getByRole('link', 'Independent task')).toHaveCount(0);
  await screen.getByRole('button', 'New task').tap();
  const form = screen.getByRole('dialog', 'New task');
  await expect(form.getByRole('combobox', 'Project')).toHaveValue('project-a');
  await form.getByRole('button', 'Cancel').tap();
  await screen.getByRole('button', 'List').tap();
  await expect(browser).toHaveURL(/tasks\?view=list$/);
  await expect(browser.locator('.sp-task-row')).toHaveCount(1);
  await screen.getByLabel('Filter tasks by project').selectOption('All projects');
  await expect(browser.locator('.sp-task-row')).toHaveCount(2);
  await expect(screen.getByRole('button', 'Change status of Keep task content')).toBeEnabled();
  await screen.getByRole('button', 'Board').tap();
  await expect(browser).toHaveURL(/tasks\?view=board$/);
  expect(space.resourceReads, 'project filtering and view changes use summaries').toEqual([]);

  await expect(card.getByRole('button')).toHaveCount(0);
  await expect(card.getByRole('link')).toHaveCount(1);
  await expect(cardLink).toHaveAttribute('role', 'link');
  const cardBox = (await card.boundingBox())!, linkBox = (await cardLink.boundingBox())!;
  expect(Math.abs(cardBox.width - linkBox.width), 'the link fills the whole card').toBeLessThanOrEqual(2);
  expect(Math.abs(cardBox.height - linkBox.height), 'the link fills the whole card').toBeLessThanOrEqual(2);
  await browser.mouse.move(linkBox.x + 6, linkBox.y + 6);
  await browser.mouse.down();
  // A negated assertion holds for a second: holding still never starts a drag.
  await expect(preview(browser)).not.toBeVisible();
  for (const x of [12, 18, 24]) await browser.mouse.move(linkBox.x + x, linkBox.y + 6);
  await expect(preview(browser)).toBeVisible();
  for (const x of [18, 12, 6]) await browser.mouse.move(linkBox.x + x, linkBox.y + 6);
  await browser.mouse.up();
  await expect(preview(browser)).toHaveCount(0);
  await expect(screen.getByRole('dialog', 'Task details')).toHaveCount(0);
  await expect(browser).toHaveURL(/tasks\?view=board$/);
  expect(space.statusCommands, 'dropping back in place neither opens nor writes').toEqual([]);
});

test('a dragged card moves at once, stays movable while saving, and rolls back on failure', async ({ app, screen, browser, space }) => {
  await app.open('/app/tasks');
  const card = screen.getByRole('article', 'Keep task content');
  const cardLink = screen.getByRole('link', 'Keep task content');
  const held = space.hold(`status:${taskId}`);
  await drag(screen, browser, 'Keep task content', 'in_progress');
  await expect(cardLink).not.toHaveAttribute('aria-disabled', 'true');
  await expect(cardLink).toHaveAttribute('aria-roledescription', 'draggable task link');
  await expect(card).toHaveAttribute('aria-busy', 'true');
  await expect(card.getByRole('status')).toHaveText('Saving status…');
  await expect(column(browser, 'in_progress').getByRole('link', 'Keep task content')).toBeVisible();

  const listReadsBefore = space.taskListReads;
  const independentLink = screen.getByRole('link', 'Independent task');
  await expect(independentLink).toHaveAttribute('aria-roledescription', 'draggable task link');
  await drag(screen, browser, 'Independent task', 'done');
  await expect(independentLink).toHaveAttribute('aria-roledescription', 'draggable task link');
  await expect.poll(() => space.taskListReads).toBeGreaterThan(listReadsBefore);
  await expect(column(browser, 'done').getByRole('link', 'Independent task')).toBeVisible();
  await expect(column(browser, 'in_progress').getByRole('link', 'Keep task content')).toBeVisible();
  await expect(card.getByRole('status')).toHaveText('Saving status…');
  expect(space.statusCommands.filter(command => command.id === taskId), 'one drag sends one status write').toHaveLength(1);
  expect(space.resourceReads, 'status drags must not fetch full task bodies').toEqual([]);
  await app.screenshot('status saving');
  await expect.poll(() => held.arrivals).toBe(1);
  held.release();
  await expect(card).toHaveAttribute('aria-busy', 'false');
  await expect(card.getByRole('status')).toHaveCount(0);
  expect(Object.keys(space.statusCommands[0]).sort()).toEqual(['command_id', 'expected_revision', 'id', 'status']);
  expect(space.tasks[0]).toMatchObject({
    body: '## Description\nDo not erase this description.', project: 'project-a', priority: 'High', status: 'in_progress',
  });

  space.failStatus.add(taskId);
  const failing = space.hold(`status:${taskId}`);
  await drag(screen, browser, 'Keep task content', 'done');
  await expect(column(browser, 'done').getByRole('link', 'Keep task content')).toBeVisible();
  await expect.poll(() => failing.arrivals).toBe(1);
  failing.release();
  await expect(card.getByRole('status')).toHaveCount(0);
  await expect(column(browser, 'in_progress').getByRole('link', 'Keep task content')).toBeVisible();
  const errorToast = browser.locator('.sp-toast[role="alert"]').filter({ hasText: /Status save failed|This task changed/ });
  const noticeToast = browser.locator('.sp-toast[role="status"]').filter({ hasText: 'Task status saved' });
  await expect(errorToast).toContainText('Status save failed');
  const failed = space.statusCommands.at(-1)!;
  const sent = space.statusCommands.length;
  await drag(screen, browser, 'Keep task content', 'done');
  await expect(column(browser, 'done').getByRole('link', 'Keep task content')).toBeVisible();
  await expect.poll(() => space.statusCommands.length).toBe(sent + 1);
  expect(space.statusCommands.at(-1)!.command_id, 'an uncertain retry reuses its command ID').toBe(failed.command_id);
  await expect(card).toHaveAttribute('aria-busy', 'false');
  await expect(noticeToast).toContainText('Task status saved');
  await expect(errorToast).toContainText('Status save failed');
  const errorBox = (await errorToast.boundingBox())!, noticeBox = (await noticeToast.boundingBox())!;
  expect(errorBox.y + errorBox.height <= noticeBox.y || noticeBox.y + noticeBox.height <= errorBox.y, 'error and success notifications stack without overlap').toBe(true);
  await noticeToast.getByRole('button', 'Dismiss notification').tap();
  await expect(noticeToast).toHaveCount(0);
  await expect(errorToast).toBeVisible();
  await errorToast.getByRole('button', 'Dismiss error').tap();
  await expect(errorToast).toHaveCount(0);

  space.tasks = space.tasks.map(task => task.id === taskId
    ? { ...task, revision: task.revision + 1, body: `${task.body}\nConcurrent edit kept.` } : task);
  await drag(screen, browser, 'Keep task content', 'in_progress');
  await expect(errorToast).toContainText('This task changed');
  await expect(column(browser, 'done').getByRole('link', 'Keep task content')).toBeVisible();
  await expect(cardLink).toHaveAttribute('aria-roledescription', 'draggable task link');
  expect(space.tasks[0].status).toBe('done');
  expect(space.tasks[0].body).toMatch(/Concurrent edit kept/);
});

test('custom columns persist, and cards move by title drag and by keyboard', async ({ app, screen, browser, space }) => {
  // Five columns with Needs you: wide enough that dragging needs no sideways scroll.
  await browser.setViewport({ width: 1700, height: 900 });
  await app.open('/app/tasks');
  const cardLink = screen.getByRole('link', 'Keep task content');
  await screen.getByRole('button', 'Add column').tap();
  const columnDialog = screen.getByRole('dialog', 'New task column');
  await columnDialog.getByLabel('Column name').fill('Backlog');
  await columnDialog.getByRole('button', 'Add column').tap();
  await expect(columnDialog).toBeHidden();
  const backlog = screen.getByRole('region', 'Backlog tasks');
  await expect(backlog).toBeVisible();
  await drag(screen, browser, 'Keep task content', space.columns.columns.at(-1)!.value, 'title');
  await expect(backlog.getByRole('link', 'Keep task content')).toBeVisible();
  await expect(cardLink).toHaveAttribute('aria-roledescription', 'draggable task link');

  await cardLink.focus();
  await browser.keyboard.press('Space');
  await expect(preview(browser)).toBeVisible();
  // Keyboard moves use column positions that dnd-kit measures just after the drag starts.
  await nextFrames(browser);
  await browser.keyboard.press('ArrowLeft');
  await expect(browser).toHaveClass(column(browser, 'done'), /is-drop-target/);
  await browser.keyboard.press('Space');
  await expect(column(browser, 'done').getByRole('link', 'Keep task content')).toBeVisible();
  await expect(cardLink).toHaveAttribute('aria-roledescription', 'draggable task link');
  await expect(cardLink).toBeFocused();

  const sent = space.statusCommands.length;
  await cardLink.focus();
  await browser.keyboard.press('Space');
  await expect(preview(browser)).toBeVisible();
  await nextFrames(browser);
  await browser.keyboard.press('ArrowRight');
  await browser.keyboard.press('Escape');
  await expect(preview(browser)).toHaveCount(0);
  await expect(column(browser, 'done').getByRole('link', 'Keep task content')).toBeVisible();
  expect(space.statusCommands, 'cancelling a keyboard drag must not write status').toHaveLength(sent);
  await expect(cardLink).toBeFocused();

  await browser.reload();
  await expect(backlog).toBeVisible();
  await expect(column(browser, 'done').getByRole('link', 'Keep task content')).toBeVisible();
  await expect(browser.locator('.sp-board-task select')).toHaveCount(0);
});

test('cards open details, and a card moved again while saving sends the latest move next', async ({ app, screen, browser, space }) => {
  await app.open('/app/tasks');
  const card = screen.getByRole('article', 'Keep task content');
  const cardLink = screen.getByRole('link', 'Keep task content');
  const dialog = screen.getByRole('dialog', 'Task details');
  for (const surface of ['padding', 'project', 'priority'] as const) {
    if (surface === 'padding') await cardLink.tap({ position: { x: 5, y: 5 } });
    else await cardLink.getByText(surface === 'project' ? 'Fixture project' : 'High').tap();
    await expect(dialog).toBeVisible();
    await expect(preview(browser)).toHaveCount(0);
    await browser.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
  }
  await cardLink.focus();
  await browser.keyboard.press('Enter');
  await expect(dialog).toBeVisible();
  await expect(preview(browser)).toHaveCount(0);
  await expect(dialog).toContainText('Do not erase this description.');
  await expect(dialog).toContainText('Fixture project');

  const detailStatus = dialog.getByRole('combobox', 'Task status');
  await expect(detailStatus).toHaveValue('todo');
  const sent = space.statusCommands.length;
  const held = space.hold(`status:${taskId}`);
  await browser.evaluate(() => {
    const select = document.querySelector<HTMLSelectElement>('select[aria-label="Task status"]')!;
    select.value = 'in_progress';
    select.dispatchEvent(new Event('change', { bubbles: true }));
    select.dispatchEvent(new Event('change', { bubbles: true }));
    return null;
  });
  await expect(detailStatus).toBeEnabled();
  await expect(detailStatus).toHaveValue('in_progress');
  await expect(card).toHaveAttribute('aria-busy', 'true');
  await expect(card.getByRole('status')).toHaveText('Saving status…');
  await expect.poll(() => held.arrivals).toBe(1);
  expect(space.statusCommands, 'same-tick duplicate detail events send one write').toHaveLength(sent + 1);

  await dialog.getByRole('button', 'Close dialog').tap();
  await drag(screen, browser, 'Keep task content', 'done');
  await expect(column(browser, 'done').getByRole('link', 'Keep task content')).toBeVisible();
  expect(space.statusCommands, 'a move made while saving waits for the first save').toHaveLength(sent + 1);
  held.release();
  await expect.poll(() => space.statusCommands.length).toBe(sent + 2);
  const [first, queued] = space.statusCommands.slice(-2);
  expect(first.status).toBe('in_progress');
  expect(queued.status).toBe('done');
  expect(queued.expected_revision, 'the queued move starts from the revision the first save produced').toBe(first.expected_revision + 1);
  await expect(card).toHaveAttribute('aria-busy', 'false');
  await expect(card.getByRole('status')).toHaveCount(0);
  await expect(column(browser, 'done').getByRole('article', 'Keep task content')).toHaveCount(1);
  expect(space.tasks.find(task => task.id === taskId)!.status).toBe('done');
  expect(space.statusCommands, 'duplicate status events stay deduplicated after saving').toHaveLength(sent + 2);
  await cardLink.tap({ position: { x: 5, y: 5 } });
  await expect(detailStatus).toHaveValue('done');
});

test('task details are centred on desktop and fit a phone', async ({ app, screen, browser }) => {
  await app.open('/app/tasks');
  const dialog = screen.getByRole('dialog', 'Task details');
  const layout = () => browser.evaluate(() => {
    const node = document.querySelector('dialog[open]')!;
    const middle = (item: Element) => { const box = item.getBoundingClientRect(); return box.top + box.height / 2; };
    return {
      toolbar: [...node.querySelectorAll('.sp-task-dialog-toolbar > *')].map(middle),
      properties: [...node.querySelectorAll('.sp-properties > div')].map(item => item.getBoundingClientRect().top),
      width: parseFloat(getComputedStyle(document.documentElement).width),
    };
  });
  await screen.getByRole('link', 'Keep task content').tap();
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText('Do not erase this description.');
  const desktop = await layout();
  expect(desktop.toolbar).toHaveLength(2);
  expect(Math.abs(desktop.toolbar[0] - desktop.toolbar[1]), 'status, Edit and Delete share one row').toBeLessThan(2);
  expect(desktop.properties).toHaveLength(3);
  expect(Math.max(...desktop.properties) - Math.min(...desktop.properties), 'properties share one row').toBeLessThan(2);
  const box = (await dialog.boundingBox())!;
  expect(Math.abs(box.x + box.width / 2 - desktop.width / 2), 'centred within the scrollbar gutter').toBeLessThan(16);
  expect(Math.abs(box.y + box.height / 2 - 500), 'vertically centred').toBeLessThan(3);
  await app.screenshot('task details desktop');
  await browser.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
  await expect(browser).toHaveURL(/tasks\?view=board$/);

  await browser.setViewport({ width: 390, height: 844 });
  await browser.locator('a.sp-board-task-link[aria-label="Independent task"] .sp-task-project').tap();
  await expect(dialog).toContainText('Second description');
  await expect(preview(browser)).toHaveCount(0);
  const phone = (await dialog.boundingBox())!;
  expect(phone.x >= 0 && phone.x + phone.width <= 391, 'the dialog stays inside the phone viewport').toBe(true);
  const mobile = await layout();
  expect(Math.abs(mobile.toolbar[0] - mobile.toolbar[1]), 'status and actions share one row on a phone').toBeLessThan(2);
  await app.screenshot('task details phone');
  await dialog.getByRole('button', 'Close dialog').tap();
  await expect(dialog).toBeHidden();
});

/** Records the bytes of every Blob the app hands to a download, as base64. */
async function captureDownloads(browser: Browser) {
  await browser.addInitScript(() => {
    const create = URL.createObjectURL.bind(URL);
    const saved: string[] = [];
    Object.assign(window, { e2eDownloads: saved });
    URL.createObjectURL = (object: Blob | MediaSource) => {
      if (object instanceof Blob)
        void object.arrayBuffer().then(buffer => {
          let binary = '';
          for (const byte of new Uint8Array(buffer)) binary += String.fromCharCode(byte);
          saved.push(btoa(binary));
        });
      return create(object);
    };
  });
}

/** Exports the Space and returns the downloaded ZIP and the archive JSON inside it. */
async function exportArchive(screen: Screen, browser: Browser) {
  // The engine saves downloads under the attempt's artifacts, so read the bytes the app hands to the download.
  const download = await browser.waitForDownload(() => screen.getByRole('button', 'Export complete Space').tap());
  expect(download.suggestedFilename).toMatch(/^space-\d{4}-\d{2}-\d{2}\.zip$/);
  await expect.poll(() => browser.evaluate(() => (window as unknown as { e2eDownloads: string[] }).e2eDownloads.length)).toBe(1);
  const zip = Buffer.from(await browser.evaluate(() => (window as unknown as { e2eDownloads: string[] }).e2eDownloads[0]), 'base64');
  const files = unzipSync(new Uint8Array(zip));
  expect(Object.keys(files)).toEqual([download.suggestedFilename.replace(/\.zip$/, '.json')]);
  return { zip, text: strFromU8(Object.values(files)[0]) };
}

test('the Space archive downloads as a ZIP and keeps custom task columns', async ({ app, screen, browser, space }) => {
  space.columns = { columns: [...defaultColumns, { value: 'column_backlog', label: 'Backlog' }], revision: 2 };
  await captureDownloads(browser);
  await app.open('/app/settings/data');
  const { text } = await exportArchive(screen, browser);
  const archive = JSON.parse(text);
  expect(archive.format).toBe('space.archive.v1');
  expect(archive.task_columns).toEqual(space.columns.columns);
  expect(archive.entries.map((entry: { value: { id: string } }) => entry.value.id)).toEqual(space.tasks.map(task => task.id));
  // One page per task: every later page continues from the cursor with the first page's snapshot.
  expect(space.exportRequests).toEqual([{ after: null }, { after: '1', snapshot: 1 }]);
  await expect(screen.getByText(`Export ready. Download started with ${space.tasks.length} entries.`)).toBeVisible();
});

test('a downloaded Space ZIP uploads for restore as its archive JSON', async ({ app, screen, browser, space }) => {
  await captureDownloads(browser);
  await app.open('/app/settings/data');
  const { zip, text } = await exportArchive(screen, browser);
  const file = join(await mkdtemp(join(tmpdir(), 'space-restore-')), 'space-export.zip');
  await writeFile(file, zip);
  await screen.getByLabel('Space archive').setInputFiles(file);
  await expect(screen.getByRole('heading', 'Review merge')).toBeVisible();
  for (const task of space.tasks) await expect(screen.getByRole('cell', `${task.title}resource`)).toBeVisible();
  expect(space.restore!.parts.join('')).toBe(text);
  expect(space.restore!.manifest.bytes).toBe(Buffer.byteLength(text));
  expect(space.restore!.manifest.sha256).toBe(createHash('sha256').update(text).digest('hex'));
});

test('task creation shows progress, keeps the draft on failure, and retries with the same command', async ({ app, screen, browser, space }) => {
  await app.open('/app/tasks');
  await screen.getByRole('button', 'New task').tap();
  const createDialog = screen.getByRole('dialog', 'New task');
  await createDialog.getByLabel('Title').fill('Patient task creation');
  await createDialog.getByLabel('Content').fill('Show this body without waiting for a second request.');
  const firstCreate = space.hold('create');
  space.failCreate = true;
  await browser.evaluate(() => {
    const form = document.querySelector<HTMLFormElement>('dialog[open] form')!;
    form.requestSubmit();
    form.requestSubmit();
    return null;
  });
  await expect(browser.locator('dialog[open] form')).toHaveAttribute('aria-busy', 'true');
  await expect(createDialog.getByRole('status')).toHaveText('Creating task…');
  await expect(createDialog.getByRole('button', 'Save')).toBeDisabled();
  await expect(createDialog.getByLabel('Title')).toBeDisabled();
  await expect(createDialog.getByLabel('Priority')).toBeDisabled();
  await browser.keyboard.press('Escape');
  await expect(createDialog).toBeVisible();
  await expect.poll(() => firstCreate.arrivals).toBe(1);
  expect(space.resourceCommands, 'same-tick duplicate submits send one create').toHaveLength(1);
  await app.screenshot('task create saving');
  firstCreate.release();
  await expect(createDialog.getByRole('alert')).toContainText('Task creation failed');
  await expect(createDialog.getByLabel('Title')).toHaveValue('Patient task creation');
  await expect(createDialog.getByLabel('Content')).toHaveValue('Show this body without waiting for a second request.');
  await expect(createDialog.getByText('Unsaved changes')).toBeVisible();
  await expect(createDialog.getByRole('button', 'Save')).toBeEnabled();
  expect(space.tasks.some(task => task.id === createdId)).toBe(false);

  const retry = space.hold('create');
  const detail = space.hold(`detail:${createdId}`);
  await createDialog.getByRole('button', 'Save').tap();
  await expect(createDialog.getByRole('status')).toHaveText('Creating task…');
  await expect.poll(() => retry.arrivals).toBe(1);
  expect(space.resourceCommands).toHaveLength(2);
  expect(space.resourceCommands[1].command_id, 'an uncertain create retry reuses its command ID').toBe(space.resourceCommands[0].command_id);
  retry.release();
  await expect(createDialog).toBeHidden();
  const details = screen.getByRole('dialog', 'Task details');
  await expect(details).toContainText('Show this body without waiting for a second request.');
  await expect.poll(() => detail.arrivals).toBeGreaterThan(0);
  expect(space.tasks.filter(task => task.id === createdId)).toHaveLength(1);
  detail.release();
  await details.getByRole('button', 'Close dialog').tap();
});

test('several tasks can be selected, moved and deleted together', async ({ app, screen, browser, space }) => {
  space.tasks = [...space.tasks, ...extraTasks(4)];
  await app.open('/app/tasks');
  await screen.getByRole('button', 'Select').tap();
  const bar = screen.getByRole('toolbar', 'Selected tasks');
  await expect(bar.getByRole('status')).toHaveText('0 selected');
  await expect(screen.getByRole('link', 'Independent task')).toHaveCount(0);
  await screen.getByRole('checkbox', 'Select Independent task').check();
  await screen.getByRole('article', 'Extra task 2').tap();
  await expect(bar.getByRole('status')).toHaveText('2 selected');
  await expect(screen.getByRole('dialog')).toHaveCount(0);
  expect(space.resourceReads, 'selecting a card does not open it').toEqual([]);

  const sent = space.statusCommands.length;
  await bar.getByLabel('Move selected tasks to').selectOption({ value: 'in_progress' });
  const progress = column(browser, 'in_progress');
  await expect(progress.getByRole('checkbox', 'Select Independent task')).toBeVisible();
  await expect(progress.getByRole('checkbox', 'Select Extra task 2')).toBeVisible();
  await expect.poll(() => space.statusCommands.length).toBe(sent + 2);
  expect(space.statusCommands.slice(-2).map(command => command.status)).toEqual(['in_progress', 'in_progress']);
  await expect(bar.getByRole('status')).toHaveText('2 selected');
  await app.screenshot('bulk board');

  space.failDelete.add(otherId);
  await bar.getByRole('button', 'Delete').tap();
  const confirm = screen.getByRole('dialog');
  await expect(confirm).toContainText('Delete 2 tasks?');
  await expect(confirm).toContainText('Independent task');
  await confirm.getByRole('button', 'Delete 2 tasks').tap();
  await expect(confirm.getByRole('alert')).toContainText('Deleted 1 of 2.');
  await expect(screen.getByRole('checkbox', 'Select Extra task 2')).toHaveCount(0);
  await expect(bar.getByRole('status')).toHaveText('1 selected');
  await confirm.getByRole('button', 'Delete task').tap();
  await expect(screen.getByRole('dialog')).toHaveCount(0);
  await expect(screen.getByRole('checkbox', 'Select Independent task')).toHaveCount(0);
  await expect(bar.getByRole('status')).toHaveText('0 selected');
  expect(space.tasks.some(task => task.id === otherId)).toBe(false);
  expect(space.deletes.filter(entry => entry.id === otherId), 'a failed delete can be retried from the same dialog').toHaveLength(2);

  await screen.getByRole('button', 'List').tap();
  await screen.getByRole('checkbox', 'Select Extra task 4').check();
  await expect(browser.locator('.sp-task-row.is-checked')).toHaveCount(1);
  await expect(bar.getByRole('status')).toHaveText('1 selected');
  await bar.getByRole('button', 'Select all').tap();
  await expect(bar.getByRole('status')).toHaveText(`${space.tasks.length} selected`);
  await app.screenshot('bulk list');
  await bar.getByRole('button', 'Clear selection').tap();
  await expect(bar.getByRole('status')).toHaveText('0 selected');
  await bar.getByRole('button', 'Done').tap();
  await expect(bar).toHaveCount(0);
  await expect(screen.getByRole('checkbox', 'Select Extra task 4')).toHaveCount(0);

  await browser.setViewport({ width: 390, height: 844 });
  await screen.getByRole('button', 'Select').tap();
  const phone = (await bar.boundingBox())!;
  expect(phone.x >= 0 && phone.x + phone.width <= 391, 'bulk actions stay inside a phone viewport').toBe(true);
  await app.screenshot('bulk phone');
  await bar.getByRole('button', 'Done').tap();
});

test('view-only cards open on click while every write stays disabled', async ({ app, screen, browser, space }) => {
  space.space.role = 'viewer';
  await app.open('/app/tasks');
  const card = screen.getByRole('article', 'Keep task content');
  const cardLink = screen.getByRole('link', 'Keep task content');
  const dialog = screen.getByRole('dialog', 'Task details');
  await expect(screen.getByRole('button', 'Select')).toBeDisabled();
  await expect(cardLink).not.toHaveAttribute('aria-disabled', 'true');
  await expect(cardLink).not.toHaveAttribute('aria-describedby');
  await cardLink.focus();
  await browser.keyboard.press('Space');
  await expect(preview(browser)).toHaveCount(0);
  const box = (await cardLink.boundingBox())!;
  await browser.mouse.move(box.x + 8, box.y + 8);
  await browser.mouse.down();
  await expect(preview(browser)).not.toBeVisible();
  await browser.mouse.up();
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', 'Close dialog').tap();
  expect(space.statusCommands, 'read-only cards never send drag writes').toEqual([]);
  await expect(browser.locator('.sp-board-task select')).toHaveCount(0);
  await expect(card).toHaveAttribute('aria-busy', 'false');
  await expect(card.getByRole('status')).toHaveCount(0);
  await expect(screen.getByRole('button', 'New task')).toBeDisabled();
  await cardLink.tap();
  await expect(dialog.getByRole('combobox', 'Task status')).toBeDisabled();
  await dialog.getByRole('button', 'Close dialog').tap();
  await screen.getByRole('button', 'List').tap();
  await expect(screen.getByRole('button', 'Change status of Keep task content')).toBeDisabled();
});

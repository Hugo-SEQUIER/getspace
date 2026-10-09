import type { Browser } from '@e2e-dev/web';
import { expect } from 'e2e';
import { createDatabase, type WorkspaceDatabase } from '../lib/space/databases.ts';
import { test, type Item, type SpaceMock } from './support/space-api.ts';

// A task database and a plain one answered by the fake API. Synthetic rows only.
const launchId = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd';
const researchId = 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee';
const launch = createDatabase('tasks', 'Launch tasks', 'Synthetic records only.', launchId);
const statusField = `${launchId}-status`, plannedField = `${launchId}-planned`;
const row = (id: string, name: string, status: string, planned: string | null) => ({
  id, name, notes: '', createdAt: '2026-09-28T09:00:00Z', updatedAt: '2026-09-30T09:00:00Z',
  values: { [statusField]: status, [plannedField]: planned, [`${launchId}-due`]: null },
});
launch.records = [
  row('copy', 'Write launch copy', 'To do', null),
  row('footer', 'Fix the footer', 'In progress', '2020-01-06'),
  row('shipped', 'Ship the beta', 'Done', null),
];
const research = createDatabase('blank', 'Research', '', researchId);
research.fields = [{ id: 'stage', name: 'Stage', type: 'select', options: ['Open', 'Closed'] }];
research.records = [{
  id: 'interview', name: 'Interview three users', notes: '', values: { stage: 'Open' },
  createdAt: '2026-09-28T09:00:00Z', updatedAt: '2026-09-30T09:00:00Z',
}];

const seed = (space: SpaceMock, databases: WorkspaceDatabase[]) =>
  space.databases.push(...databases.map(database => ({ ...database, kind: 'database', revision: 1 }) as unknown as Item));
const saved = (space: SpaceMock, id: string) => space.databases.find(item => item.id === id) as unknown as WorkspaceDatabase;
const stored = (space: SpaceMock, id: string, recordId: string) => saved(space, id).records.find(record => record.id === recordId)!.values;
const localDay = (browser: Browser) => browser.evaluate(() => {
  const now = new Date(), pad = (n: number) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
});
const columnCss = (label: string) => `section[aria-label="${label} tasks"]`;
const column = (browser: Browser, label: string) => browser.locator(columnCss(label));
const cardCss = (label: string, name: string) => `${columnCss(label)} article[aria-label="${name}"]`;
const card = (browser: Browser, label: string, name: string) => browser.locator(cardCss(label, name));

// Drags a card by its heading onto a column with real pointer moves.
async function drag(browser: Browser, from: string, name: string, to: string) {
  const handle = browser.locator(`${cardCss(from, name)} .sp-board-task-heading`);
  const destination = column(browser, to);
  await destination.scrollIntoView();
  const start = (await handle.boundingBox())!, end = (await destination.boundingBox())!;
  const x = start.x + start.width / 2, y = start.y + start.height / 2;
  await browser.mouse.move(x, y);
  await browser.mouse.down();
  for (const offset of [4, 8, 12]) await browser.mouse.move(x + offset, y);
  const toX = end.x + end.width / 2, toY = end.y + 80;
  for (let step = 1; step <= 12; step++)
    await browser.mouse.move(x + 12 + (toX - x - 12) * step / 12, y + (toY - y) * step / 12);
  await expect(browser).toHaveClass(destination, /is-drop-target/);
  await browser.mouse.up();
}

test('rows of task databases are added to the Tasks board, moved and closed there', async ({ app, screen, browser, space }) => {
  await browser.setViewport({ width: 1440, height: 1000 });
  seed(space, [launch, research]);
  await app.open('/app/tasks');

  // An open row planned on an earlier day stays on the board under its status, with its day.
  await expect(card(browser, 'In progress', 'Fix the footer')).toBeVisible();
  await expect(card(browser, 'In progress', 'Fix the footer')).toContainText('Planned');
  await expect(card(browser, 'In progress', 'Fix the footer')).toContainText('Launch tasks');

  await screen.getByRole('button', 'Add from databases').tap();
  const picker = screen.getByRole('dialog', 'Add from databases');
  // Closed rows and rows of databases that are not task lists are never offered.
  await expect(picker.getByText('Write launch copy')).toBeVisible();
  await expect(picker.getByText('Ship the beta')).toBeHidden();
  await expect(picker.getByText('Interview three users')).toBeHidden();
  await picker.getByRole('button', 'Add Write launch copy to today').tap();
  await expect(picker.getByText('Every open row is already on the board.')).toBeVisible();
  await picker.getByRole('button', 'Done').tap();
  await expect(picker).toBeHidden();

  await expect(card(browser, 'To do', 'Write launch copy')).toBeVisible();
  const today = await localDay(browser);
  await expect.poll(() => stored(space, launchId, 'copy')[plannedField]).toBe(today);

  // Dragging a row card to Done closes the row in its database; it stays on today's board.
  await drag(browser, 'To do', 'Write launch copy', 'Done');
  await expect(card(browser, 'Done', 'Write launch copy')).toBeVisible();
  await expect.poll(() => stored(space, launchId, 'copy')[statusField]).toBe('Done');

  // Removing a row from today takes it off the board and clears its planned day.
  await card(browser, 'In progress', 'Fix the footer').hover();
  await screen.getByRole('button', 'Remove Fix the footer from today').tap();
  await expect(card(browser, 'In progress', 'Fix the footer')).toBeHidden();
  await expect.poll(() => stored(space, launchId, 'footer')[plannedField]).toBeNull();
});

test('an existing database is turned into a task list from its settings', async ({ app, screen, browser, space }) => {
  seed(space, [research]);
  await app.open(`/app/databases/${researchId}`);
  await screen.getByRole('button', 'Database settings').tap();
  await screen.getByRole('checkbox', 'Track rows as tasks').tap();
  await expect(screen.getByLabel('Status property')).toHaveValue('stage');
  // “Closed” is recognised as the done status; “Open” is not.
  await expect(screen.getByRole('checkbox', 'Closed')).toBeChecked();
  await expect(screen.getByRole('checkbox', 'Open')).not.toBeChecked();
  await expect(screen.getByLabel('Planned day property')).toHaveValue('new');
  await screen.getByRole('button', 'Save changes').tap();
  await expect(screen.getByRole('dialog')).toBeHidden();

  await expect.poll(() => saved(space, researchId).tasks ?? null).not.toBeNull();
  const database = saved(space, researchId);
  const planned = database.fields.find(field => field.name === 'Planned for')!;
  expect(planned.type).toBe('date');
  expect(database.tasks).toEqual({ status: 'stage', done: ['Closed'], planned: planned.id });

  // “Open” names no board column, so the row lands in the first one.
  await browser.goto('/app/tasks');
  await screen.getByRole('button', 'Add from databases').tap();
  await screen.getByRole('dialog', 'Add from databases').getByRole('button', 'Add Interview three users to today').tap();
  await screen.getByRole('dialog', 'Add from databases').getByRole('button', 'Done').tap();
  await expect(card(browser, 'To do', 'Interview three users')).toBeVisible();
});

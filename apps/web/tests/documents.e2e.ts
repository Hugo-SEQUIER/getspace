import type { Browser } from '@e2e-dev/web';
import { expect } from 'e2e';
import { test, type Document, type FolderRow, type SectionRow } from './support/space-api.ts';

// The Documents directory: the Space's folders as tiles grouped by section, one list of every
// document with its folder path, facets, search over titles and bodies, unread marks, sortable
// headers, the shared pager, a row menu and the folder dialogs from the main pane.
const agent = { id: '88888888-8888-4888-8888-888888888888', name: 'AIC Agent', harness: 'hermes', revision: 1 };
const planId = '99999999-9999-4999-8999-999999999901';
const briefId = '99999999-9999-4999-8999-999999999902';
const decisionId = '99999999-9999-4999-8999-999999999903';
const interviewId = '99999999-9999-4999-8999-999999999904';
const onePagerId = '99999999-9999-4999-8999-999999999905';
const handoffId = '99999999-9999-4999-8999-999999999906';

function sections(): SectionRow[] {
  return [
    { id: 'sec-work', kind: 'section', name: 'Work', description: 'Current projects', revision: 1, deleted: false },
    { id: 'sec-library', kind: 'section', name: 'Library', description: 'Reference material', revision: 1, deleted: false },
  ];
}
function folders(): FolderRow[] {
  return [
    { id: 'fld-notes', kind: 'folder', name: 'Website notes', description: 'Plans and notes for the website.', section_id: 'sec-work', revision: 1, deleted: false },
    { id: 'fld-billing', kind: 'folder', name: 'Billing', description: 'Pricing and plans.', section_id: 'sec-work', revision: 1, deleted: false },
    { id: 'fld-research', kind: 'folder', name: 'Research', description: '', section_id: 'sec-library', revision: 1, deleted: false },
    { id: 'fld-meetings', kind: 'folder', name: 'Meeting notes', description: 'Weekly status.', section_id: null, revision: 1, deleted: false },
  ];
}
function documents(): Document[] {
  return [
    { id: planId, kind: 'document', title: 'Launch plan', body: 'Week one covers the pricing cards and the welcome flow.', status: 'todo', revision: 3, folder: 'fld-notes', project: 'project-a', author: 'agent', author_agent: agent, updated_at: '2026-10-22T09:00:00Z' },
    { id: briefId, kind: 'document', title: 'Launch brief', body: 'One page for the launch.', status: 'todo', revision: 2, folder: 'fld-notes', author: 'you', updated_at: '2026-10-10T09:00:00Z' },
    { id: decisionId, kind: 'document', title: 'Billing: Starter free month stays permanent', body: 'Remove the deadline from D26.', status: 'todo', revision: 2, folder: 'fld-billing', author: 'agent', author_agent: agent, updated_at: '2026-10-22T07:00:00Z' },
    { id: handoffId, kind: 'document', title: 'Onboarding handoff', body: 'Where the onboarding work stands.', status: 'todo', revision: 1, folder: 'fld-billing', author: 'agent', author_agent: agent, updated_at: '2026-10-21T08:00:00Z', recall: { category: 'handoff' } },
    { id: interviewId, kind: 'document', title: 'Interview notes', body: 'What the first users said.', status: 'todo', revision: 1, author: 'you', updated_at: '2026-10-19T09:00:00Z' },
    { id: onePagerId, kind: 'document', title: 'Space one-pager', body: '', status: 'todo', revision: 1, author: 'you', updated_at: '2026-10-18T09:00:00Z',
      file: { id: 'file-1', name: 'Space one-pager.pdf', type: 'application/pdf', bytes: 2_400_000, sha256: 'a'.repeat(64), parts: 3 } },
  ];
}
/** The rows of the list, without the header row. */
const bodyRows = (browser: Browser) => browser.locator('.sp-doc-table tbody tr');

function extraDocuments(count: number): Document[] {
  return Array.from({ length: count }, (_, index) => ({
    id: `55555555-5555-4555-8555-${String(index + 1).padStart(12, '0')}`,
    kind: 'document', title: `Extra document ${String(index + 1).padStart(2, '0')}`, body: '', status: 'todo', revision: 1,
    author: 'you', updated_at: `2026-09-${String(1 + (index % 28)).padStart(2, '0')}T09:00:00Z`,
  }));
}

test.beforeEach(async ({ browser, space }) => {
  await browser.setViewport({ width: 1440, height: 1000 });
  space.sections = sections();
  space.folders = folders();
  space.documents = documents();
  // The person read the plan at its current revision; the agent's decision and handoff are new to them.
  // Opened just now, so the Recently opened card reads "Opened today" whatever the day.
  space.views.push({ resource_id: planId, seen_revision: 3, seen_at: new Date().toISOString() });
});

test('the root shows the folders of the Space by section and every document with its path', async ({ app, screen, browser }) => {
  await app.open('/app/documents');
  // The first visit of a run loads the page's chunks; wait for the directory to mount.
  await browser.locator('.sp-doc-library').waitFor();
  await expect(screen.getByRole('heading', 'Documents', { level: 1 })).toBeVisible();
  await expect(screen.getByRole('region', 'Recently opened').getByRole('link', /^Launch plan/)).toContainText('Opened today');

  const work = screen.getByRole('region', 'Work');
  await expect(work.getByRole('link', 'Website notes')).toBeVisible();
  await expect(work.getByText('Plans and notes for the website.')).toBeVisible();
  await expect(work).toContainText('2 folders · 4 documents');
  await expect(work.getByRole('link', 'Billing')).toBeVisible();
  await expect(work).toContainText('2 unread');
  const library = screen.getByRole('region', 'Library');
  await expect(library.getByRole('link', 'Research')).toBeVisible();
  await expect(library).toContainText('0 documents');
  await expect(screen.getByRole('region', 'Folders').getByRole('link', 'Meeting notes')).toBeVisible();

  const list = screen.getByRole('region', 'All documents');
  await expect(list.getByRole('row', /Launch plan/)).toContainText('Work / Website notes');
  await expect(list.getByRole('row', /Interview notes/)).not.toContainText('/');
  await expect(list.getByRole('row', /Space one-pager/)).toContainText('PDF · 2.3 MB');
  await expect(list.getByRole('row', /Onboarding handoff/)).toContainText('Session handoff');
  const facets = screen.getByRole('group', 'Document type');
  await expect(facets.getByRole('button', /^All/)).toContainText('6');
  await expect(facets.getByRole('button', /^Files/)).toContainText('1');
  await expect(facets.getByRole('button', /^Handoffs/)).toContainText('1');
  await facets.getByRole('button', /^Files/).tap();
  await expect(browser).toHaveURL(/type=files/);
  await expect(screen.getByRole('list', 'Files').getByRole('listitem')).toHaveCount(1);
  await expect(screen.getByText('1 file')).toBeVisible();
  await facets.getByRole('button', /^All/).tap();
  await expect(bodyRows(browser)).toHaveCount(6);
  await expect(browser.locator('.sp-doc-tree')).toContainText('New since you looked');
});

test('a folder tile opens the folder with its description and only its documents', async ({ app, screen, browser }) => {
  await app.open('/app/documents');
  await screen.getByRole('region', 'Work').getByRole('link', 'Billing').tap();
  await expect(browser).toHaveURL(/\/app\/documents\?folder=fld-billing$/);
  await expect(screen.getByRole('heading', 'Documents', { level: 1 })).toBeVisible();
  await expect(screen.getByRole('heading', 'Billing', { level: 2 })).toBeVisible();
  await expect(screen.getByText('Pricing and plans.')).toBeVisible();
  await expect(screen.getByText('Section: Work')).toBeVisible();
  const list = screen.getByRole('region', 'Documents in Billing');
  await expect(list.getByRole('row', /Billing: Starter/)).toBeVisible();
  await expect(list.getByRole('row', /Onboarding handoff/)).toBeVisible();
  await expect(list.getByRole('row', /Launch plan/)).toHaveCount(0);
  await expect(list.getByRole('row', /Billing: Starter/)).not.toContainText('Work /');
  await expect(screen.getByRole('region', 'Folders')).toHaveCount(0);

  const location = screen.getByRole('navigation', 'Location');
  await expect(location).toContainText('Work');
  await location.getByRole('link', 'All documents').tap();
  await expect(browser).toHaveURL(/\/app\/documents$/);
  await expect(screen.getByRole('region', 'Work')).toBeVisible();
});

test('documents an agent changed since the person looked are marked and can be filtered', async ({ app, screen, browser }) => {
  await app.open('/app/documents');
  const list = screen.getByRole('region', 'All documents');
  await expect(list.getByRole('row', /Billing: Starter/).getByRole('img', 'New since you looked')).toBeVisible();
  await expect(list.getByRole('row', /Launch plan/).getByRole('img', 'New since you looked')).toHaveCount(0);
  await expect(list.getByRole('row', /Interview notes/).getByRole('img', 'New since you looked')).toHaveCount(0);

  await screen.getByRole('button', /^Unread/).tap();
  await expect(browser).toHaveURL(/unread=1/);
  await expect(bodyRows(browser)).toHaveCount(2);
  await expect(list.getByRole('row', /Billing: Starter/)).toBeVisible();
  await expect(list.getByRole('row', /Onboarding handoff/)).toBeVisible();
  await expect(screen.getByRole('region', 'Work')).toHaveCount(0);
  await screen.getByRole('button', /^Unread/).tap();
  await expect(browser).toHaveURL(/\/app\/documents$/);
  await expect(bodyRows(browser)).toHaveCount(6);
});

test('search finds a page by the text inside it, through the index read once', async ({ app, screen, browser, space }) => {
  await app.open('/app/documents');
  const list = screen.getByRole('region', 'All documents');
  await screen.getByRole('textbox', 'Search documents').fill('welcome flow');
  await expect(list.getByRole('row', /Launch plan/)).toBeVisible();
  await expect(bodyRows(browser)).toHaveCount(1);
  await expect(screen.getByRole('region', 'Work')).toHaveCount(0);
  await screen.getByRole('textbox', 'Search documents').fill('welcome flow and');
  await expect(bodyRows(browser)).toHaveCount(0);
  await expect(screen.getByRole('heading', 'No documents found')).toBeVisible();
  expect(space.searchIndexReads).toBe(1);
  // The empty state and the footer both offer to clear the filters.
  await screen.getByRole('button', 'Clear filters').first().tap();
  await expect(bodyRows(browser)).toHaveCount(6);
  await expect(screen.getByRole('region', 'Work')).toBeVisible();
});

test('headers sort the list and the pager keeps 50 rows at a time', async ({ app, screen, browser, space }) => {
  space.documents.push(...extraDocuments(60));
  await app.open('/app/documents');
  await expect(bodyRows(browser)).toHaveCount(50);
  await expect(screen.getByRole('navigation', 'List pages')).toContainText('1–50 of 66');
  await expect(bodyRows(browser).first()).toContainText('Launch plan');
  await screen.getByRole('button', 'Next page').tap();
  await expect(bodyRows(browser)).toHaveCount(16);
  await expect(screen.getByRole('navigation', 'List pages')).toContainText('51–66 of 66');

  await screen.getByRole('button', 'Name').tap();
  await expect(screen.getByRole('navigation', 'List pages')).toContainText('1–50 of 66');
  await expect(bodyRows(browser).first()).toContainText('Billing: Starter');
  await screen.getByRole('button', 'Name').tap();
  await expect(bodyRows(browser).first()).toContainText('Space one-pager');
});

test("a row's menu moves a document to another folder without dragging", async ({ app, screen, space }) => {
  await app.open('/app/documents');
  const list = screen.getByRole('region', 'All documents');
  await list.getByRole('button', 'Actions for Interview notes').tap();
  await screen.getByRole('menuitem', 'Move to folder…').tap();
  const dialog = screen.getByRole('dialog', 'Move to folder');
  await dialog.getByLabel('Folder').selectOption({ label: 'Research' });
  await dialog.getByRole('button', 'Move document').tap();
  await expect(list.getByRole('row', /Interview notes/)).toContainText('Library / Research');
  const command = space.resourceCommands.at(-1) as Record<string, unknown>;
  expect(command.resource_id).toBe(interviewId);
  expect(command.expected_revision).toBe(1);
  expect(command.folder).toBe('fld-research');
  expect(command.title).toBe('Interview notes');
  await expect(screen.getByRole('region', 'Library')).toContainText('1 document');
});

test('a folder is renamed from its tile and opens afterwards', async ({ app, screen, browser, space }) => {
  await app.open('/app/documents');
  await screen.getByRole('region', 'Folders').getByRole('button', 'Folder options for Meeting notes').tap();
  await screen.getByRole('menuitem', 'Edit folder…').tap();
  const dialog = screen.getByRole('dialog', 'Edit folder');
  await expect(dialog.getByLabel('Description')).toHaveValue('Weekly status.');
  await dialog.getByLabel('Folder name').fill('Call notes');
  await dialog.getByRole('button', 'Save folder').tap();
  await expect(browser).toHaveURL(/\/app\/documents\?folder=fld-meetings$/);
  await expect(screen.getByRole('heading', 'Call notes', { level: 2 })).toBeVisible();
  const command = space.itemCommands.at(-1) as { item_id: string; expected_revision: number; data: { name: string; section_id: string | null } };
  expect(command.item_id).toBe('fld-meetings');
  expect(command.expected_revision).toBe(1);
  expect(command.data.name).toBe('Call notes');
  expect(command.data.section_id).toBeNull();
});

test('the grid view is remembered in this browser', async ({ app, screen, browser }) => {
  await app.open('/app/documents');
  await screen.getByRole('button', 'Grid view').tap();
  await expect(screen.getByRole('list', 'Documents').getByRole('listitem')).toHaveCount(6);
  await app.open('/app/documents');
  await expect(screen.getByRole('button', 'Grid view')).toHaveAttribute('aria-pressed', 'true');
  await expect(screen.getByRole('list', 'Documents').getByRole('listitem')).toHaveCount(6);
  await screen.getByRole('button', 'List view').tap();
  await expect(bodyRows(browser)).toHaveCount(6);
});

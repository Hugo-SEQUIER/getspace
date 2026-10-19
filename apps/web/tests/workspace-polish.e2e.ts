import { beforeEach, type Browser } from '@e2e-dev/web';
import { expect } from 'e2e';
import { test, type Document } from './support/space-api.ts';

const report: Document = {
  id: '77777777-7777-4777-8777-777777777772', kind: 'document', title: 'Daily SEO report', body: 'Clicks rose.',
  status: 'todo', revision: 2, author: 'agent',
  author_agent: { id: '88888888-8888-4888-8888-888888888888', name: 'AIC Agent', harness: 'hermes', revision: 1 },
  updated_at: '2026-10-08T05:00:00Z',
};
const launchPlan: Document = {
  id: '77777777-7777-4777-8777-777777777774', kind: 'document', title: 'Launch plan', body: 'The plan.',
  status: 'todo', revision: 1, project: 'project-a', author: 'you', updated_at: '2026-10-01T00:00:00Z',
};

beforeEach(async ({ browser }) => {
  await browser.setViewport({ width: 1600, height: 1000 });
});

const dialogWidth = (browser: Browser) =>
  browser.evaluate(() => document.querySelector('dialog[open]')!.getBoundingClientRect().width);

test('home waits for agents and seen marks, then opens in its final layout', async ({ app, screen, space }) => {
  space.documents.push(report);
  const agents = space.hold('agents');
  await app.open('/app/');
  await expect(screen.getByRole('heading', { level: 1 })).toBeVisible();
  await expect(screen.getByRole('status', 'Loading home')).toBeVisible();
  await expect.poll(() => agents.arrivals).toBeGreaterThan(0);
  // Without connections the checklist would claim no agent is connected yet.
  await expect(screen.getByRole('region', 'Get started')).toHaveCount(0);
  await expect(screen.getByRole('region', 'New since you looked')).toHaveCount(0);

  agents.release();
  await expect(screen.getByRole('region', 'New since you looked').getByRole('link', 'Daily SEO report')).toBeVisible();
  await expect(screen.getByRole('status', 'Loading home')).toHaveCount(0);
});

test('dialogs keep their own width on pages that end on the valley', async ({ app, screen, browser }) => {
  await app.open('/app/tasks');
  await screen.getByRole('button', 'New task').first().tap();
  await expect(screen.getByRole('dialog', 'New task')).toBeVisible();
  expect(await dialogWidth(browser)).toBeLessThanOrEqual(760);
  await browser.keyboard.press('Escape');

  await browser.goto('/app/agents');
  await screen.getByRole('button', 'Connect an agent').first().tap();
  await expect(screen.getByRole('dialog', 'Connect an agent')).toBeVisible();
  // The wide modal, not the full width of the main column.
  expect(await dialogWidth(browser)).toBeLessThanOrEqual(760);
});

test('agents are connected, never created, from the agents page', async ({ app, screen }) => {
  await app.open('/app/agents');
  await expect(screen.getByRole('heading', 'Connect your first agent')).toBeVisible();
  await expect(screen.getByRole('button', /New agent|Create agent/)).toHaveCount(0);
  await screen.getByRole('main').getByRole('button', 'Connect an agent').last().tap();
  await expect(screen.getByRole('dialog', 'Connect an agent')).toBeVisible();
});

test('the search close button lines up with the search field', async ({ app, screen, browser }) => {
  await app.open('/app/tasks');
  await screen.getByRole('button', 'Search your space').tap();
  await expect(screen.getByLabel('Quick search')).toBeVisible();
  const offset = await browser.evaluate(() => {
    const centre = (selector: string) => {
      const box = document.querySelector(`dialog[open] ${selector}`)!.getBoundingClientRect();
      return box.top + box.height / 2;
    };
    return Math.abs(centre('.sp-modal-head .sp-icon-button') - centre('.sp-command-input'));
  });
  expect(offset).toBeLessThanOrEqual(1);
});

const columns = (browser: Browser) =>
  browser.evaluate(() => getComputedStyle(document.querySelector('.sp-project-overview')!).gridTemplateColumns.split(' ').length);
// How many columns the documents, tasks and databases sections are spread across.
const sideColumns = (browser: Browser) => browser.evaluate(() =>
  new Set([...document.querySelectorAll('.sp-project-overview-side > section')].map(section => Math.round(section.getBoundingClientRect().left))).size);

test('a project overview puts its contents beside the description, and stacks them when narrow', async ({ app, screen, browser, space }) => {
  space.documents.push(launchPlan);
  await browser.setViewport({ width: 2000, height: 1000 });
  await app.open('/app/projects/project-a');
  await expect(screen.getByRole('complementary', 'Project contents')).toBeVisible();
  expect(await sideColumns(browser)).toBe(3);

  await browser.setViewport({ width: 1600, height: 1000 });
  const side = screen.getByRole('complementary', 'Project contents');
  await expect(side.getByRole('region', 'Documents').getByRole('link', /^Launch plan/)).toBeVisible();
  await expect(side.getByRole('region', 'Tasks').getByRole('link', /Keep task content/)).toBeVisible();
  await expect(side.getByRole('region', 'Project databases')).toBeVisible();
  expect(await columns(browser)).toBe(2);
  expect(await sideColumns(browser)).toBe(1);

  await browser.setViewport({ width: 1000, height: 1000 });
  await expect.poll(() => columns(browser)).toBe(1);
  await browser.setViewport({ width: 390, height: 900 });
  await expect.poll(() => columns(browser)).toBe(1);
  expect(await browser.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('the memory categories dialog uses the wide modal size', async ({ app, screen, browser }) => {
  await app.open('/app/memory');
  await screen.getByRole('button', 'Categories').tap();
  await expect(screen.getByRole('dialog', 'Organize memory')).toBeVisible();
  expect(await dialogWidth(browser)).toBeGreaterThan(700);
});

test('the task board always shows Needs you, even with no flagged task', async ({ app, screen, space }) => {
  expect(space.tasks.some(task => task.status === 'needs_you')).toBe(false);
  await app.open('/app/tasks');
  await expect(screen.getByRole('region', 'Needs you tasks')).toBeVisible();
});

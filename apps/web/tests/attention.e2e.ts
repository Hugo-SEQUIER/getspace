import { beforeEach, type Browser } from '@e2e-dev/web';
import { expect } from 'e2e';
import { test, type Document, type Task } from './support/space-api.ts';

// Agent-authored fixtures: a task flagged as waiting on the person, a report the person
// has not opened, and a report they opened at its current revision.
const flaggedId = '77777777-7777-4777-8777-777777777771';
const reportId = '77777777-7777-4777-8777-777777777772';
const seenReportId = '77777777-7777-4777-8777-777777777773';
const agent = { id: '88888888-8888-4888-8888-888888888888', name: 'AIC Agent', harness: 'hermes', revision: 1 };

const flagged: Task = {
  id: flaggedId, kind: 'task', title: 'Approve the outreach batch', body: 'Twelve drafts wait in Gmail.', status: 'needs_you',
  revision: 3, project: 'project-a', priority: 'High', author: 'agent', author_agent: agent, updated_at: '2026-10-08T06:00:00Z',
};
const report: Document = {
  id: reportId, kind: 'document', title: 'Daily SEO report', body: 'Clicks rose.', status: 'todo', revision: 2,
  project: 'project-a', author: 'agent', author_agent: agent, updated_at: '2026-10-08T05:00:00Z',
};
const seenReport: Document = {
  id: seenReportId, kind: 'document', title: 'Yesterday report', body: 'Nothing new.', status: 'todo', revision: 4,
  author: 'agent', author_agent: agent, updated_at: '2026-10-07T05:00:00Z',
};

beforeEach(async ({ browser }) => {
  await browser.setViewport({ width: 1440, height: 1000 });
});

const badge = (browser: Browser) => browser.locator('.sp-nav-badge');

test('the home page lists what waits on the person and what agents changed since they looked', async ({ app, screen, browser, space }) => {
  space.tasks.push(flagged);
  space.documents.push(report, seenReport);
  space.views.push({ resource_id: seenReportId, seen_revision: 4, seen_at: '2026-10-07T06:00:00Z' });
  await app.open('/app/');

  const needsYou = screen.getByRole('region', 'Needs you');
  await expect(needsYou.getByRole('link', 'Approve the outreach batch')).toBeVisible();
  await expect(needsYou.getByText('AIC Agent · Fixture project', { exact: false })).toBeVisible();
  await expect(needsYou.getByRole('link', 'Daily SEO report')).toHaveCount(0);

  const unseen = screen.getByRole('region', 'New since you looked');
  await expect(unseen.getByRole('link', 'Daily SEO report')).toBeVisible();
  await expect(unseen.getByRole('link', 'Yesterday report')).toHaveCount(0);
  await expect(unseen.getByRole('link', 'Keep task content')).toHaveCount(0);
  await expect(browser.locator('.sp-home-attention-projects')).toContainText('Fixture project 1');
  await expect(badge(browser)).toHaveText('2');
  expect(space.seenCommands, 'reading the home page marks nothing as seen').toEqual([]);
});

test('opening an item marks its current revision as seen and removes it from the list', async ({ app, screen, browser, space }) => {
  space.documents.push(report);
  await app.open('/app/');
  await expect(badge(browser)).toHaveText('1');
  await screen.getByRole('region', 'New since you looked').getByRole('link', 'Daily SEO report').tap();
  await expect(browser).toHaveURL(new RegExp(`/app/documents/${reportId}$`));
  await expect(screen.getByText('Clicks rose.')).toBeVisible();
  await expect.poll(() => space.seenCommands).toEqual([{ items: [{ resource_id: reportId, revision: 2 }] }]);
  await expect(badge(browser)).toHaveCount(0);

  await screen.getByRole('navigation', 'Main navigation').getByRole('link', 'Home').tap();
  await expect(browser).toHaveURL(/\/app\/?$/);
  await expect(screen.getByRole('heading', 'Jump back in')).toBeVisible();
  await expect(screen.getByRole('region', 'New since you looked')).toHaveCount(0);
  expect(space.seenCommands).toHaveLength(1);
});

test('mark all as seen records every unseen item in one request', async ({ app, screen, browser, space }) => {
  space.documents.push(report, seenReport);
  space.tasks.push(flagged);
  await app.open('/app/');
  const unseen = screen.getByRole('region', 'New since you looked');
  await expect(unseen.getByRole('link', 'Daily SEO report')).toBeVisible();
  await expect(unseen.getByRole('link', 'Yesterday report')).toBeVisible();
  await unseen.getByRole('button', 'Mark all as seen').tap();
  await expect(screen.getByRole('region', 'New since you looked')).toHaveCount(0);
  expect(space.seenCommands).toHaveLength(1);
  expect(space.seenCommands[0].items.map(item => item.resource_id).sort()).toEqual([reportId, seenReportId].sort());
  expect(space.seenCommands[0].items.find(item => item.resource_id === seenReportId)?.revision).toBe(4);
  // The flagged task stays until the person changes its status.
  await expect(screen.getByRole('region', 'Needs you').getByRole('link', 'Approve the outreach batch')).toBeVisible();
  await expect(badge(browser)).toHaveText('1');
});

test('the task board shows a Needs you column before Done only while a task is flagged', async ({ app, screen, browser, space }) => {
  await app.open('/app/tasks');
  await expect(browser.locator('[data-status="needs_you"]')).toHaveCount(0);
  space.tasks.push(flagged);
  await app.open('/app/tasks');
  await expect(browser.locator('[data-status="needs_you"]').getByRole('link', 'Approve the outreach batch')).toBeVisible();
  await expect(screen.getByRole('region', 'Needs you tasks')).toBeVisible();
  const order = await browser.evaluate(() => Array.from(document.querySelectorAll('[data-status]')).map(node => node.getAttribute('data-status')));
  expect(order.indexOf('needs_you')).toBeLessThan(order.indexOf('done'));
});

test('only tasks wait on the person: a document carrying the old flag is not listed under Needs you', async ({ app, screen, space }) => {
  space.tasks.push(flagged);
  space.documents.push({ ...report, status: 'needs_you' });
  await app.open('/app/');
  const needsYou = screen.getByRole('region', 'Needs you');
  await expect(needsYou.getByRole('link', 'Approve the outreach batch')).toBeVisible();
  await expect(needsYou.getByRole('link', 'Daily SEO report')).toHaveCount(0);
  await expect(screen.getByRole('region', 'New since you looked').getByRole('link', 'Daily SEO report')).toBeVisible();
});

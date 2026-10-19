import { type Browser } from '@e2e-dev/web';
import { expect } from 'e2e';
import { createDatabase } from '../lib/space/databases.ts';
import { extraTasks, taskId, test, type Document, type Item } from './support/space-api.ts';

// The workspace shares the hosted product's look: the evening valley strip on Home, the
// engraved valley on first-use empty states, the setup dialog, the agent connection pages
// and the not-found page, and on every page pill actions, status pills and a top bar that
// floats once the page scrolls. Every /api request is answered in the browser.
const after = (browser: Browser, selector: string) =>
  browser.evaluate((query) => getComputedStyle(document.querySelector(query)!, '::after').backgroundImage, selector);
const fits = (browser: Browser) => browser.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
const footer = (browser: Browser) => browser.evaluate(() => {
  const valley = document.querySelector('.sp-valley-footer');
  return valley ? getComputedStyle(valley, '::before').backgroundImage : 'none';
});
const radius = (browser: Browser, selector: string) => browser.evaluate((query) => getComputedStyle(document.querySelector(query)!).borderRadius, selector);
const weight = (browser: Browser, selector: string) => browser.evaluate((query) => getComputedStyle(document.querySelector(query)!).fontWeight, selector);

const databaseId = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1';
const documentId = '66666666-6666-4666-8666-666666666661';
function seedDatabase(): Item {
  const database = createDatabase('blank', 'Consulting tasks', 'Synthetic records only.', databaseId);
  database.fields = [{ id: 'workstream', name: 'Workstream', type: 'select', options: ['Governance', 'Delivery'] }];
  database.records = [{ id: 'row-1', name: 'Decide who owns the programme', notes: '', values: { workstream: 'Governance' }, createdAt: '2026-09-28T09:00:00Z', updatedAt: '2026-09-30T09:00:00Z' }];
  return { ...database, kind: 'database', revision: 1 } as unknown as Item;
}
const launchPlan: Document = {
  id: documentId, kind: 'document', title: 'Launch plan', body: 'A long page.\n\n' + 'Paragraph of notes.\n\n'.repeat(60),
  status: 'todo', revision: 1, author: 'you', updated_at: '2026-10-01T00:00:00Z',
};

for (const [name, width] of [['desktop', 1280], ['phone', 390]] as const) {
  test(`Home opens on the evening valley and page titles are semibold on ${name}`, async ({ app, screen, browser, space }) => {
    await browser.setViewport({ width, height: 900 });
    await app.open('/app/');
    await expect(screen.getByRole('heading', { level: 1 })).toContainText('Good');
    const band = await browser.evaluate(() => {
      const style = getComputedStyle(document.querySelector('.sp-home-band')!);
      return { image: style.backgroundImage, size: style.backgroundSize, height: style.height };
    });
    expect(band.image).toContain('home-evening-1920');
    // Drawn at half its pixel width, so the dots are cropped, never rescaled.
    expect(band.size).toBe('960px 180px');
    expect(band.height).toBe(width > 760 ? '180px' : '120px');
    expect(await browser.evaluate(() => getComputedStyle(document.querySelector('h1')!).fontWeight)).toBe('600');
    expect(await fits(browser)).toBe(true);

    expect(space.tasks.length).toBeGreaterThan(0);
    await browser.goto('/app/tasks');
    await expect(screen.getByRole('heading', { level: 1 })).toHaveText('Tasks');
    expect(await browser.evaluate(() => getComputedStyle(document.querySelector('h1')!).fontWeight)).toBe('600');
  });
}

test('first-use empty states end on the engraved valley; filtered ones do not', async ({ app, screen, browser, space }) => {
  await browser.setViewport({ width: 1280, height: 900 });
  space.tasks = [];
  await app.open('/app/');
  for (const [path, title] of [
    ['/app/agents', 'Connect your first agent'],
    ['/app/documents', 'A place for your first idea'],
    ['/app/memory', 'A little more context, every session'],
  ] as const) {
    await browser.goto(path);
    await expect(screen.getByRole('heading', title)).toBeVisible();
    expect(`${path} ${await after(browser, '.sp-empty')}`).toMatch(new RegExp(`^${path} .*footer-engraving`));
  }
  // The memory page still centres its empty state over the graph area.
  expect(await browser.evaluate(() => getComputedStyle(document.querySelector('.sp-empty')!).position)).toBe('absolute');

  // Tasks show their empty state in the list view. A search with no results is not a
  // first visit, so it has no picture.
  await browser.goto('/app/tasks');
  await screen.getByRole('button', 'List').click();
  await expect(screen.getByRole('heading', 'A little room to make progress')).toBeVisible();
  expect(await after(browser, '.sp-empty')).toContain('footer-engraving');
  await screen.getByRole('textbox', 'Search tasks').fill('nothing matches this');
  await expect(screen.getByText('No tasks match these filters.')).toBeVisible();
  expect(await browser.evaluate(() => document.querySelector('.sp-empty')!.hasAttribute('data-valley'))).toBe(false);
});

test('a connected agent shows as a status pill', async ({ app, browser, space }) => {
  await browser.setViewport({ width: 1280, height: 900 });
  space.connections.push({ id: 'c0000000-0000-4000-8000-000000000001', client_id: 'c0000000-0000-4000-8000-000000000002', agent_id: null, name: 'Codex', harness: 'codex', can_write: true, revoked_at: null });
  await app.open('/app/agents');
  const pill = browser.locator('.sp-connection-state');
  await expect(pill.first()).toHaveText('Connected');
  expect(await browser.evaluate(() => {
    const style = getComputedStyle(document.querySelector('.sp-connection-state.connected')!);
    return `${style.backgroundColor} ${style.borderRadius}`;
  })).toBe('rgb(230, 237, 231) 999px');
});

test('the setup dialog opens on the valley and still fits a 720px screen', async ({ app, screen, browser }) => {
  await browser.setViewport({ width: 1280, height: 720 });
  await app.open('/app/?setup=1');
  const dialog = screen.getByRole('dialog', 'Get started with Space');
  await expect(dialog.getByRole('heading', 'Welcome to Space')).toBeVisible();
  expect(await browser.evaluate(() => getComputedStyle(document.querySelector('.sp-onboarding-valley')!).backgroundImage)).toContain('footer-engraving');
  expect(await browser.evaluate(() => {
    const box = document.querySelector('.sp-onboarding')!;
    return box.scrollHeight <= box.clientHeight && box.getBoundingClientRect().bottom <= window.innerHeight;
  })).toBe(true);
  // The close button floats over the picture, with no white box behind it.
  expect(await browser.evaluate(() => getComputedStyle(document.querySelector('.sp-onboarding > .sp-modal-head')!).backgroundColor)).toBe('rgba(0, 0, 0, 0)');
  await dialog.getByRole('button', 'Continue').click();
  await expect(dialog.getByRole('heading', 'What will you use Space for?')).toBeVisible();
  await expect(browser.locator('.sp-onboarding-valley')).toHaveCount(0);
});

for (const [name, width] of [['desktop', 1280], ['phone', 390]] as const) {
  test(`the not-found and connection pages carry the green mark and the valley on ${name}`, async ({ app, screen, browser }) => {
    await browser.setViewport({ width, height: 900 });
    await app.open('/no-such-page');
    await expect(screen.getByRole('heading', { level: 1 })).toHaveText('Page not found');
    await expect(screen.getByRole('link', 'Space home')).toBeVisible();
    expect(await after(browser, '.sp-status-page')).toContain('footer-engraving');
    expect(await fits(browser)).toBe(true);

    await browser.goto('/connect');
    await expect(screen.getByRole('link', 'Space home')).toBeVisible();
    expect(await browser.evaluate(() => getComputedStyle(document.querySelector('main header .sp-mark')!).backgroundColor)).toBe('rgb(66, 100, 76)');
    expect(await after(browser, 'main.sp-valley')).toContain('footer-engraving');
    expect(await fits(browser)).toBe(true);
  });
}

test('Home, Tasks and lighter pages sit on the valley, dense pages stay clear, and the top bar floats on scroll', async ({ app, screen, browser, space }) => {
  await browser.setViewport({ width: 1280, height: 720 });
  space.databases.push(seedDatabase());
  space.documents.push(launchPlan);
  space.tasks.push(...extraTasks(40));
  await app.open('/app/agents');
  await expect(screen.getByRole('heading', { level: 1 })).toHaveText('Agents');
  await expect(browser.locator('.sp-valley-footer')).toHaveCount(1);
  expect(await footer(browser)).toContain('footer-engraving');
  // The valley shows at the foot of the window without scrolling, behind the page.
  expect(await browser.evaluate(() => Math.round(document.querySelector('.sp-valley-footer')!.getBoundingClientRect().bottom))).toBe(720);
  expect(await browser.evaluate(() => getComputedStyle(document.querySelector('.sp-valley-footer')!).zIndex)).toBe('-1');
  expect(await radius(browser, '.sp-main .sp-primary')).toBe('999px');
  for (const path of ['/app', '/app/tasks', `/app/tasks/${taskId}`, '/app/projects', '/app/databases', '/app/settings']) {
    await browser.goto(path);
    await expect(screen.getByRole('heading', { level: 1 }).first()).toBeVisible();
    // The footer appears once the page's own lists have loaded.
    await expect(browser.locator('.sp-valley-footer')).toHaveCount(1);
    expect(`${path} ${await footer(browser)}`).toContain('footer-engraving');
  }
  for (const path of ['/app/documents', '/app/memory', `/app/databases/${databaseId}`, '/app/projects/project-a']) {
    await browser.goto(path);
    await expect(screen.getByRole('heading', { level: 1 }).first()).toBeVisible();
    expect(`${path} ${await footer(browser)}`).toBe(`${path} none`);
  }

  // On a long page the valley stays pinned to the window's foot while the content scrolls
  // over it, and the bar floats once the page moves.
  await browser.goto('/app/tasks');
  await screen.getByRole('button', 'List').click();
  await expect(screen.getByRole('link', 'Extra task 1')).toBeVisible();
  const bar = browser.locator('.sp-workspace-bar');
  await expect(bar).not.toHaveAttribute('data-floating');
  await browser.evaluate(() => { window.scrollTo(0, 400); return true; });
  expect(await browser.evaluate(() => Math.round(document.querySelector('.sp-valley-footer')!.getBoundingClientRect().bottom))).toBe(720);
  await expect(bar).toHaveAttribute('data-floating', '');
  expect(await browser.evaluate(() => Math.round(document.querySelector('.sp-workspace-bar')!.getBoundingClientRect().top))).toBe(0);
  await browser.evaluate(() => { window.scrollTo(0, 0); return true; });
  await expect(bar).not.toHaveAttribute('data-floating');
});

test('detail pages use semibold titles, pill actions and status pills', async ({ app, screen, browser, space }) => {
  await browser.setViewport({ width: 1280, height: 900 });
  space.databases.push(seedDatabase());
  space.documents.push(launchPlan);
  await app.open('/app/projects/project-a');
  await expect(screen.getByRole('heading', { level: 1 })).toHaveText('Fixture project');
  expect(await weight(browser, '.sp-project-heading h1')).toBe('600');
  expect(await radius(browser, '.sp-project-heading .sp-with-status')).toBe('999px');

  await browser.goto(`/app/databases/${databaseId}`);
  await expect(screen.getByRole('heading', { level: 1 })).toHaveText('Consulting tasks');
  expect(await weight(browser, '.sp-db-title-row h1')).toBe('600');
  expect(await radius(browser, '.sp-db-option')).toBe('999px');
  await expect(screen.getByRole('button', 'New record').first()).toBeVisible();
  expect(await browser.evaluate(() => getComputedStyle([...document.querySelectorAll('button')].find((button) => button.textContent?.trim() === 'New record')!).borderRadius)).toBe('999px');

  await browser.goto(`/app/documents/${documentId}`);
  await expect(screen.getByRole('heading', { level: 1 })).toHaveText('Launch plan');
  expect(await weight(browser, '.sp-doc-reader h1')).toBe('600');
  // The Documents tree stays below the sticky top bar while the page scrolls.
  await browser.evaluate(() => { window.scrollTo(0, 300); return true; });
  expect(await browser.evaluate(() => Math.round(document.querySelector('.sp-doc-tree-pane')!.getBoundingClientRect().top) >= 48)).toBe(true);
});

test('on a phone the top bar stays in the page flow and the valley footer still fits', async ({ app, screen, browser }) => {
  await browser.setViewport({ width: 390, height: 844 });
  await app.open('/app/agents');
  await expect(screen.getByRole('heading', { level: 1 })).toHaveText('Agents');
  expect(await browser.evaluate(() => getComputedStyle(document.querySelector('.sp-workspace-bar')!).position)).toBe('static');
  expect(await browser.evaluate(() => getComputedStyle(document.querySelector('.sp-valley-footer')!, '::before').height)).toBe('140px');
  expect(await fits(browser)).toBe(true);
});

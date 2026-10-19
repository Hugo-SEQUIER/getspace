import { expect } from 'e2e';
import { test } from './support/space-api.ts';

// Titles the workspace already lists answer before the full-text index has arrived.
test('titles match while the full-text index is still loading', async ({ app, screen, space }) => {
  const index = space.hold('index');
  await app.open('/app/search');
  const box = screen.getByLabel('Search this space');
  await box.fill('Keep task');
  await expect(screen.getByRole('link', /Keep task content/)).toBeVisible();
  await expect(screen.getByText(/Loading full text/)).toBeVisible();
  // Body text is not known yet, so a body-only term waits for the index.
  await box.fill('erase');
  await expect(screen.getByRole('link', /Keep task content/)).toHaveCount(0);
  expect(index.arrivals).toBe(1);
  index.release();
  await expect(screen.getByRole('link', /Keep task content/)).toBeVisible();
  await expect(screen.getByText(/Loading full text/)).toHaveCount(0);
  expect(space.searchIndexReads).toBe(1);
});

// The Space's search index loads once; typing, filtering and reopening search match in the browser.
test('search matches body text locally after one index read', async ({ app, screen, browser, space }) => {
  await app.open('/app/search');
  const box = screen.getByLabel('Search this space');
  await box.fill('erase');
  await expect(screen.getByRole('link', /Keep task content/)).toBeVisible();
  await expect(screen.getByRole('link', /Independent task/)).toHaveCount(0);

  await box.fill('second description');
  await expect(screen.getByRole('link', /Independent task/)).toBeVisible();
  await expect(screen.getByRole('link', /Keep task content/)).toHaveCount(0);
  await screen.getByRole('button', 'Documents').tap();
  await expect(screen.getByRole('link', /Independent task/)).toHaveCount(0);
  await screen.getByRole('button', 'All').tap();

  await box.fill('no such words anywhere');
  await expect(screen.getByText('0 results in your Space')).toBeVisible();

  // After in-app navigation the palette reuses the same cached index.
  await screen.getByRole('navigation', 'Main navigation').getByRole('link', 'Tasks').tap();
  await expect(browser).toHaveURL('/app/tasks');
  await screen.getByRole('button', 'Search your space').tap();
  await screen.getByLabel('Quick search').fill('erase');
  await expect(screen.getByRole('dialog', 'Search your space').getByRole('link', /Keep task content/)).toBeVisible();
  expect(space.searchIndexReads).toBe(1);
});

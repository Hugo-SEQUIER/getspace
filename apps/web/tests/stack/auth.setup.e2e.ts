import { test } from '@e2e-dev/web';
import { expect } from 'e2e';
import { stack } from './support/stack.ts';

// Unlocks the website through the real unlock form with this run's access key, then opens
// the workspace once so every test starts from the owner's ready Private Space.
test.setup('unlock as the owner', { sessions: ['owner'] }, async ({ app, screen, browser, session }) => {
  await app.open('/app');
  await expect(screen.getByRole('heading', 'Unlock this Space')).toBeVisible();
  await screen.getByLabel('Access key').fill(stack().accessKey);
  await screen.getByRole('button', 'Unlock').tap();
  await expect(browser).toHaveURL(/\/app\/?$/);
  // A fresh workspace opens the first-run tour; dismiss it so the saved session carries
  // that choice and every test starts on a usable home page.
  const tour = screen.getByRole('dialog', 'Get started with Space');
  try { await tour.waitFor({ state: 'visible', timeout: 5000 }); } catch { /* Already dismissed. */ }
  if (await tour.isVisible()) await tour.getByRole('button', 'Skip for now').tap();
  await expect(screen.getByRole('dialog', 'Get started with Space')).toHaveCount(0);
  await expect(screen.getByRole('group', 'Quick actions').getByRole('button', 'New page')).toBeVisible();
  await session.save('owner');
});

import { expect } from 'e2e';
import { fixtureAccessKey, test } from './support/space-api.ts';

// The fake API stands in for the website's server: it keeps this browser locked until the
// unlock form sends its fixture access key, and answers 401 to every other call meanwhile.

test('a locked Space refuses a wrong access key and opens with the right one', async ({ app, screen, browser, space }) => {
  space.access.unlocked = false;
  await app.open('/app/tasks');
  await expect(screen.getByRole('heading', 'Unlock this Space')).toBeVisible();
  await expect(screen.getByText('SPACE_ACCESS_KEY')).toBeVisible();
  await expect(screen.getByRole('link', 'Source code (AGPL-3.0)')).toHaveAttribute('href', 'https://github.com/Hugo-SEQUIER/getspace');
  await expect(screen.getByRole('navigation', 'Main navigation')).toHaveCount(0);

  const key = screen.getByLabel('Access key');
  await key.fill('not-the-access-key-at-all-0123456789');
  await screen.getByRole('button', 'Unlock').tap();
  await expect(screen.getByRole('alert')).toHaveText('That access key is not correct.');
  await expect(screen.getByRole('heading', 'Unlock this Space')).toBeVisible();

  await key.fill(fixtureAccessKey);
  await screen.getByRole('button', 'Unlock').tap();
  await expect(screen.getByRole('navigation', 'Main navigation')).toBeVisible();
  await expect(screen.getByRole('heading', 'Tasks', { level: 1 })).toBeVisible();
  await expect(browser).toHaveURL('/app/tasks');
  expect(space.unlockAttempts).toEqual(['not-the-access-key-at-all-0123456789', fixtureAccessKey]);
});

test('locking returns this browser to the unlock page until the key is entered again', async ({ app, screen, browser, space }) => {
  await app.open('/app');
  await expect(screen.getByRole('navigation', 'Main navigation')).toBeVisible();
  await screen.getByRole('button', 'Account menu').tap();
  await expect(screen.getByText('Unlocked with the access key')).toBeVisible();
  await screen.getByRole('button', 'Lock').tap();
  await expect(screen.getByRole('heading', 'Unlock this Space')).toBeVisible();
  // The page clears itself at once; the server forgets the session as the request lands.
  await expect.poll(() => space.access.unlocked).toBe(false);

  await browser.reload();
  await expect(screen.getByRole('heading', 'Unlock this Space')).toBeVisible();
  await screen.getByLabel('Access key').fill(fixtureAccessKey);
  await screen.getByRole('button', 'Unlock').tap();
  await expect(screen.getByRole('navigation', 'Main navigation')).toBeVisible();
});

test('a locked tab opens by itself once another tab unlocks the Space', async ({ app, screen, browser, space }) => {
  space.access.unlocked = false;
  await app.open('/app');
  await expect(screen.getByRole('heading', 'Unlock this Space')).toBeVisible();
  // Another tab of the same browser unlocks; this one notices when it is in view again.
  space.access.unlocked = true;
  await browser.evaluate(() => { window.dispatchEvent(new Event('focus')); return null; });
  await expect(screen.getByRole('navigation', 'Main navigation')).toBeVisible();
  expect(space.unlockAttempts).toEqual([]);
});

test('a private Space opens without a key and offers nothing to lock', async ({ app, screen, space }) => {
  space.access = { mode: 'private', unlocked: false };
  space.ownerName = 'Ada Lovelace';
  await app.open('/app/settings/security');
  await expect(screen.getByRole('heading', 'Security', { level: 1 })).toBeVisible();
  await expect(screen.getByText(/runs without an access key/)).toBeVisible();
  await expect(screen.getByRole('button', 'Lock this browser')).toHaveCount(0);
  await screen.getByRole('button', 'Account menu').tap();
  await expect(screen.getByText('Open on a private network')).toBeVisible();
  await expect(screen.getByRole('button', 'Lock')).toHaveCount(0);
  await expect(screen.getByRole('link', 'Source code (AGPL-3.0)')).toHaveAttribute('href', 'https://github.com/Hugo-SEQUIER/getspace');
  await expect(screen.getByRole('button', 'Account menu')).toContainText('Ada Lovelace');
  expect(space.unlockAttempts).toEqual([]);
});

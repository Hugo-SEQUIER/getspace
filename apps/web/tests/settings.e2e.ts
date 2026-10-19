import { mkdtemp, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { beforeEach } from '@e2e-dev/web';
import { expect } from 'e2e';
import { spaceId, test } from './support/space-api.ts';

beforeEach(async ({ browser }) => {
  await browser.setViewport({ width: 1440, height: 1000 });
});

test('the account page shows the owner and has no appearance or keyboard settings', async ({ app, screen }) => {
  await app.open('/app/settings/account');
  await expect(screen.getByRole('heading', 'Profile')).toBeVisible();
  await expect(screen.getByRole('region', 'Profile').getByText('Owner', { exact: true })).toBeVisible();
  await expect(screen.getByText('The operator sets this name with SPACE_OWNER_NAME.')).toBeVisible();
  await expect(screen.getByRole('heading', 'Appearance')).toHaveCount(0);
  await expect(screen.getByRole('heading', 'Keyboard')).toHaveCount(0);
  await expect(screen.getByRole('button', 'Compact')).toHaveCount(0);
  await expect(screen.getByRole('button', 'Edit name')).toHaveCount(0);
});

test('a chosen picture shows everywhere, and removing it goes back to the initials', async ({ app, screen, browser, space }) => {
  await app.open('/app/settings/account');
  await expect(screen.getByRole('button', 'Remove picture')).toHaveCount(0);
  // A synthetic 2×1 PNG: the page crops it to a square and re-encodes it.
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAIAAAABCAYAAAD0In+KAAAAEUlEQVR4nGPQ9zH/f7Mp6j8AEHcEZuj93UEAAAAASUVORK5CYII=', 'base64');
  const file = join(await mkdtemp(join(tmpdir(), 'space-picture-')), 'me.png');
  await writeFile(file, png);
  await screen.getByLabel('Choose a profile picture').setInputFiles(file);
  await expect(screen.getByText(/Picture you chose/)).toBeVisible();
  expect(space.profileImageCommands).toEqual(['POST']);
  expect(space.profileImage).toMatch(/^data:image\/(webp|jpeg);base64,/);
  // The sidebar's account button shows the same picture.
  await expect.poll(() => browser.evaluate(() =>
    [...document.querySelectorAll<HTMLImageElement>('.sp-avatar-photo')].some(image => image.src.startsWith('data:image/')))).toBe(true);

  await screen.getByRole('button', 'Remove picture').tap();
  await expect(screen.getByRole('button', 'Remove picture')).toHaveCount(0);
  expect(space.profileImageCommands).toEqual(['POST', 'DELETE']);
});

test('the data page warns that export takes time and restores through a labelled picker', async ({ app, screen }) => {
  await app.open('/app/settings/data');
  await expect(screen.getByText('A large Space can take several minutes to export.', { exact: false })).toBeVisible();
  await expect(screen.getByRole('button', 'Choose an archive')).toBeVisible();
});

test('the processing page loads the processor settings by itself', async ({ app, screen }) => {
  await app.open('/app/settings/processing');
  await expect(screen.getByRole('heading', 'Memory processing', { level: 1 })).toBeVisible();
  await expect(screen.getByText('Not configured', { exact: true })).toBeVisible();
  await expect(screen.getByRole('button', 'Load processing settings')).toHaveCount(0);
});

test('finished and failed extraction jobs leave no status on the memory page', async ({ app, screen, browser, space }) => {
  space.memoryJobs = [
    { id: '99999999-9999-4999-8999-999999999991', status: 'failed', attempts: 3, error_code: 'invalid_output', result: null },
    { id: '99999999-9999-4999-8999-999999999992', status: 'ready', attempts: 1, error_code: null, result: null },
  ];
  await app.open('/app/memory');
  await expect(screen.getByRole('heading', 'Memory', { level: 1 })).toBeVisible();
  // The job list is first read a few seconds after the workspace opens.
  await browser.waitForResponse(`**/api/spaces/${spaceId}/memory/jobs`);
  await expect(screen.getByText(/Memory extraction/)).toHaveCount(0);

  space.memoryJobs = [{ id: '99999999-9999-4999-8999-999999999993', status: 'processing', attempts: 1, error_code: null, result: null }];
  await app.open('/app/memory');
  await expect(screen.getByText('Memory extraction · 1 in progress')).toBeVisible({ timeout: 10_000 });
});

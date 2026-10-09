import { test } from '@e2e-dev/web';
import { expect } from 'e2e';
import { unique } from './support/stack.ts';

// Agent identities are created and listed through the real backend.
test('a named agent identity is created, listed and survives a reload', { session: 'owner' }, async ({ app, screen }) => {
  const name = unique('Research assistant');
  await app.open('/app/agents');
  await screen.getByRole('button', 'New agent').tap();
  await screen.getByLabel('Name').fill(name);
  await screen.getByRole('button', 'Save agent').tap();
  // The new identity opens in the detail pane and joins the list.
  await expect(screen.getByRole('heading', name)).toBeVisible();
  await expect(screen.getByRole('button', new RegExp(`^${name} `))).toBeVisible();
  await app.open('/app/agents');
  await expect(screen.getByRole('button', new RegExp(`^${name} `))).toBeVisible();
});

test('account appearance switches density and the choice persists for this browser', { session: 'owner' }, async ({ app, screen }) => {
  await app.open('/app/settings/account');
  await screen.getByRole('button', 'Compact').tap();
  await expect(screen.getByRole('button', 'Compact')).toHaveAttribute('aria-pressed', 'true');
  await app.open('/app/settings/account');
  await expect(screen.getByRole('button', 'Compact')).toHaveAttribute('aria-pressed', 'true');
  await screen.getByRole('button', 'Comfortable').tap();
  await expect(screen.getByRole('button', 'Comfortable')).toHaveAttribute('aria-pressed', 'true');
});

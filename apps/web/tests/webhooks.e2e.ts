import { beforeEach } from '@e2e-dev/web';
import { expect } from 'e2e';
import { databaseId, sampleDatabase, test, type Item } from './support/space-api.ts';

// Settings → Webhooks against the fake API: an inbox subscription needs no receiver or
// secret, an HTTPS receiver keeps the secret flow, and inbox receipts read as agent pulls.
beforeEach(async ({ browser }) => {
  await browser.setViewport({ width: 1440, height: 1000 });
});

test('an inbox subscription is created without an endpoint or a signing secret', async ({ app, screen, space }) => {
  space.databases.push(sampleDatabase() as unknown as Item);
  await app.open('/app/settings/webhooks');
  await expect(screen.getByRole('heading', 'Create webhook')).toBeVisible();
  await expect(screen.getByLabel('Deliver to')).toHaveValue('inbox');
  await expect(screen.getByLabel('HTTPS endpoint')).toHaveCount(0);
  await expect(screen.getByText(/pull_events/)).toBeVisible();

  await screen.getByLabel('Database').selectOption('Pipeline');
  await screen.getByLabel('Transition filter (optional)').selectOption('Stage');
  await screen.getByLabel('Changes to').selectOption('Review');
  await screen.getByRole('button', 'Create webhook').tap();

  const row = screen.getByRole('listitem', 'Webhook for Pipeline at Agent inbox');
  await expect(row).toBeVisible();
  await expect(row.getByText('Agent inbox')).toBeVisible();
  await expect(row.getByText('Stage → Review · Revision 1')).toBeVisible();
  await expect(row.getByRole('button', 'Rotate secret')).toHaveCount(0);
  await expect(screen.getByRole('region', 'One-time signing secret')).toHaveCount(0);
  expect(space.webhookCommands).toHaveLength(1);
  expect(space.webhookCommands[0]).toMatchObject({ database_id: databaseId, delivery: 'inbox', transition: { field_id: 'stage', to: 'Review' } });
  expect(space.webhookCommands[0]).not.toHaveProperty('endpoint_url');
});

test('an HTTPS receiver still needs an endpoint and shows its one-time secret', async ({ app, screen, space }) => {
  space.databases.push(sampleDatabase() as unknown as Item);
  await app.open('/app/settings/webhooks');
  await screen.getByLabel('Database').selectOption('Pipeline');
  await screen.getByLabel('Deliver to').selectOption('An HTTPS receiver you run');
  await expect(screen.getByRole('button', 'Create webhook')).toBeDisabled();
  await screen.getByLabel('HTTPS endpoint').fill('https://receiver.example.com/events');
  await screen.getByRole('button', 'Create webhook').tap();

  await expect(screen.getByRole('region', 'One-time signing secret')).toBeVisible();
  const row = screen.getByRole('listitem', 'Webhook for Pipeline at https://receiver.example.com/events');
  await expect(row.getByRole('button', 'Rotate secret')).toBeVisible();
  expect(space.webhookCommands[0]).toMatchObject({ delivery: 'endpoint', endpoint_url: 'https://receiver.example.com/events' });
});

test('inbox receipts read as agent pulls and acknowledgements', async ({ app, screen, space }) => {
  space.databases.push(sampleDatabase() as unknown as Item);
  space.webhooks = [{
    id: '00000001-5555-4555-8555-555555555555', space_id: space.space.id, database_id: databaseId, delivery: 'inbox', endpoint_url: null,
    transition: null, paused: false, revision: 1, created_at: '2026-10-14T09:00:00Z', updated_at: '2026-10-14T09:00:00Z', removed_at: null,
  }];
  space.webhookDeliveries = [{
    id: '00000001-7777-4777-8777-777777777777', event_id: '00000001-8888-4888-8888-888888888888', record_id: 'deal-1',
    event_type: 'database.record.changed', revision: 2, previous_revision: 1, status: 'processing', attempts: 2, cycle: 0,
    created_at: '2026-10-14T09:05:00Z', updated_at: '2026-10-14T09:20:00Z', available_at: '2026-10-14T09:05:00Z', error_code: null, status_code: null,
    receipts: [
      { cycle: 0, attempt: 1, started_at: '2026-10-14T09:05:00Z', finished_at: '2026-10-14T09:15:00Z', status_code: null, error_code: 'worker_lost' },
      { cycle: 0, attempt: 2, started_at: '2026-10-14T09:20:00Z', finished_at: null, status_code: null, error_code: null },
    ],
  }];
  await app.open('/app/settings/webhooks');
  await screen.getByRole('button', 'View receipts').tap();
  const receipts = screen.getByRole('region', 'Delivery receipts');
  await expect(receipts.getByText('Agent inbox')).toBeVisible();
  await expect(receipts.getByText(/Each attempt is one pull by an agent/)).toBeVisible();
  await receipts.getByText('Attempt receipts (2)').tap();
  await expect(receipts.getByText('Outcome: Lease expired')).toBeVisible();
  await expect(receipts.getByText('Outcome: Leased to an agent')).toBeVisible();
  await expect(receipts.getByText('HTTP status', { exact: false })).toHaveCount(0);
  await expect(receipts.getByRole('button', 'Redeliver')).toBeDisabled();
});

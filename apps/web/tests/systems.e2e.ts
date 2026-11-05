import { beforeEach } from '@e2e-dev/web';
import { expect } from 'e2e';
import { labEntryId, sampleSystem, systemId, test } from './support/space-api.ts';

// Synthetic fixtures only: every /api request is answered in the browser.
beforeEach(async ({ browser }) => {
  await browser.setViewport({ width: 1440, height: 1000 });
});

test('a system lists, opens, and its entry is promoted, used and sent back to testing', async ({ app, screen, browser, space }) => {
  const { entry } = sampleSystem(space);
  // A second entry without evidence or source, to show the gate refusing it.
  space.labEntries.push({ ...entry, id: 'cdcdcdcd-cdcd-4dcd-8dcd-cdcdcdcdcdcd', name: 'Short subjects lift the open rate.', evidence: 'untested', source: '', updated_at: '2026-10-18T10:00:00Z' });

  await app.open('/app/systems');
  await expect(screen.getByRole('heading', 'Systems', { level: 1 })).toBeVisible();
  await expect(screen.getByRole('link', 'Systems')).toHaveAttribute('aria-current', 'page');
  const health = screen.getByRole('region', 'Health of your systems');
  await expect(health.getByText('Needs attention')).toBeVisible();
  await expect(health.getByText('No verdicts yet')).toBeVisible();
  // The reuse ring names its share and the target notch.
  await expect(health.getByRole('img', 'Reuse –, target 30%')).toBeVisible();
  await expect(screen.getByRole('region', 'Recent in the lab').getByRole('listitem').first()).toContainText('Added as measured');
  await expect(screen.getByRole('link', /Cold email/)).toContainText('Review due');

  // The Lab tab lists every entry across systems and filters them.
  await screen.getByRole('button', /^Lab/).tap();
  await expect(browser).toHaveURL(/\/app\/systems\?tab=lab$/);
  await screen.getByLabel('Evidence').selectOption('Measured');
  await expect(screen.getByText('1 of 2 entries')).toBeVisible();
  await screen.getByRole('button', /^Systems/).tap();

  await screen.getByRole('link', /Cold email/).tap();
  await expect(browser).toHaveURL(`/app/systems/${systemId}`);
  await expect(screen.getByRole('heading', 'Cold email', { level: 1 })).toBeVisible();
  await expect(screen.getByRole('region', 'Testing, 2 entries')).toBeVisible();

  // The gate keeps an untested entry in the lab and says why.
  await screen.getByRole('button', /^Short subjects lift the open rate\./).tap();
  await expect(screen.getByRole('button', 'Promote: works')).toBeDisabled();
  await expect(screen.getByText("Not ready to promote: Set the evidence to Measured or Owner's call.")).toBeVisible();

  await screen.getByRole('button', /^A first line naming/).tap();
  await expect(browser).toHaveURL(`/app/systems/${systemId}?entry=${labEntryId}`);
  await screen.getByRole('button', 'Promote: works').tap();
  const promote = screen.getByRole('dialog', 'Promote to “works”?');
  await promote.getByLabel(/Note for this revision/).fill('Measured over cohort 5.');
  await promote.getByRole('button', 'Promote: works').tap();
  // The verdict stamp lands on the card, which then moves to Works.
  await expect(screen.getByRole('button', /^A first line naming/).getByRole('status')).toHaveText(/works/i);
  await expect(screen.getByRole('region', 'Works, 1 entry')).toContainText('A first line naming');
  await expect(screen.getByRole('region', 'Recent in the lab').getByRole('listitem').first()).toContainText('Promoted: works');
  await expect(screen.getByText('Agents recall this as “Works”.')).toBeVisible();
  await expect(screen.getByRole('button', 'Edit')).toBeDisabled();
  // A system whose verdicts agents recall cannot be archived until they leave recall.
  await screen.getByRole('button', 'Edit system').tap();
  const edit = screen.getByRole('dialog', 'Edit system');
  await expect(edit.getByRole('button', 'Archive system')).toBeDisabled();
  await expect(edit.getByText(/Move this system's verdicts back to Testing/)).toBeVisible();
  await edit.getByRole('button', 'Cancel').tap();
  expect(space.systemCommands.at(-1)).toMatchObject({ move: 'promote', id: labEntryId, expected_revision: 1, verdict: 'validated', note: 'Measured over cohort 5.' });

  await screen.getByRole('button', 'Record use').tap();
  const use = screen.getByRole('dialog', 'Record a use');
  await use.getByLabel('Used in').fill('Cohort 6 first emails');
  await use.getByRole('button', 'Record use').tap();
  await expect(screen.getByRole('complementary').getByText('Cohort 6 first emails', { exact: true })).toBeVisible();
  expect(space.systemCommands.at(-1)).toMatchObject({ move: 'uses', expected_revision: 2, used_in: 'Cohort 6 first emails' });

  await screen.getByRole('button', 'Back to testing').tap();
  await screen.getByRole('dialog', 'Back to testing?').getByRole('button', 'Move to Testing').tap();
  await expect(screen.getByRole('region', 'Testing, 2 entries')).toContainText('1 retry');
  await expect(screen.getByRole('region', 'Works, 0 entries')).toBeVisible();
  expect(space.systemCommands.at(-1)).toMatchObject({ move: 'demote', expected_revision: 3, status: 'testing' });
  // The timeline reads the history: added, promoted, used, then the retry.
  await expect(screen.getByRole('region', 'Recent in the lab').getByRole('listitem').first()).toContainText('Back to testing (retry 1)');
  const history = screen.getByRole('region', 'History');
  await expect(history).toContainText('Back to testing (retry 1)');
  await expect(history).toContainText('Used in Cohort 6 first emails');
  await expect(history).toContainText('Promoted: works');
  await expect(history).toContainText('Measured over cohort 5.');
  await expect(history).toContainText('Added as measured');
});

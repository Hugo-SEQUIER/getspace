import { test } from '@e2e-dev/web';
import { expect } from 'e2e';
import { unique } from './support/stack.ts';

// Systems and lab entries are workspace items on the real backend. The stack has no memory
// provider, so this covers everything but the verdict memory: create, the promotion gate
// the backend and the page agree on, retirement and the history it leaves.
test('a system is created, gets a lab entry, refuses to promote it untested, and retires it', { session: 'owner' }, async ({ app, screen, browser }) => {
  const name = unique('Stack system');
  const lesson = `${unique('Stack lesson')} keeps the first line short.`;
  await app.open('/app/systems');
  await screen.getByRole('button', 'New system').tap();
  const create = screen.getByRole('dialog', 'New system');
  await create.getByLabel('Name').fill(name);
  await create.getByLabel('Goal metric').fill('Reply rate');
  await create.getByRole('button', 'Create system').tap();
  await expect(browser).toHaveURL(/\/app\/systems\/[0-9a-f-]{36}$/);
  await expect(screen.getByRole('heading', name, { level: 1 })).toBeVisible();

  await screen.getByRole('button', 'New entry').tap();
  const add = screen.getByRole('dialog', 'New lab entry');
  await add.getByLabel(/Hypothesis or lesson/).fill(lesson);
  await add.getByLabel('Domain').selectOption('Copy & hooks');
  await add.getByRole('button', 'Add to the lab').tap();
  await expect(screen.getByRole('region', 'Testing, 1 entry')).toContainText(lesson);

  await screen.getByRole('button', new RegExp(`^${lesson.split(' ').slice(0, 3).join(' ')}`)).tap();
  await expect(screen.getByRole('button', 'Promote: works')).toBeDisabled();
  await screen.getByRole('button', 'Retire').tap();
  await screen.getByRole('dialog', 'Retire this entry?').getByRole('button', 'Retire entry').tap();
  await expect(screen.getByRole('region', 'Retired, 1 entry')).toContainText(lesson);
  await expect(screen.getByRole('region', 'History')).toContainText('Added as untested idea');

  await app.open('/app/systems');
  await expect(screen.getByRole('link', new RegExp(name))).toBeVisible();
});

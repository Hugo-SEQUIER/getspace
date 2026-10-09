import { expect } from 'e2e';
import { createDatabase } from '../lib/space/databases.ts';
import { projectId, test, type Item, type SpaceMock } from './support/space-api.ts';

// A task database and a project answered by the fake API. Synthetic rows only.
const launchId = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';
const launch = createDatabase('tasks', 'Launch tasks', 'Synthetic records only.', launchId);
launch.records = [{
  id: 'copy', name: 'Write launch copy', notes: '', createdAt: '2026-09-28T09:00:00Z', updatedAt: '2026-09-30T09:00:00Z',
  values: { [`${launchId}-status`]: 'To do', [`${launchId}-planned`]: null, [`${launchId}-due`]: null },
}];

const placement = (space: SpaceMock) => space.databases.find(item => item.id === launchId)?.project_id ?? null;

test('a database is linked to a project and shown on its page', async ({ app, screen, browser, space }) => {
  space.projects.push({ id: projectId, kind: 'project', name: 'Website launch', description: '', status: 'in_progress', revision: 1 });
  space.databases.push({ ...launch, kind: 'database', revision: 1 } as unknown as Item);

  // Linked from the database's settings.
  await app.open(`/app/databases/${launchId}`);
  await screen.getByRole('button', 'Database settings').tap();
  await screen.getByLabel('Project').selectOption('Website launch');
  await screen.getByRole('button', 'Save changes').tap();
  await expect(screen.getByRole('dialog')).toBeHidden();
  await expect.poll(() => placement(space)).toBe(projectId);

  await browser.goto(`/app/projects/${projectId}`);
  const section = screen.getByRole('region', 'Project databases');
  await expect(section.getByRole('link', 'Launch tasks')).toBeVisible();
  await expect(section.getByText('Task list')).toBeVisible();
  await expect(section.getByText('1 record')).toBeVisible();

  // Unlinked and linked again from the project page.
  await section.getByRole('button', 'Unlink Launch tasks').tap();
  await expect(section.getByText('No database is linked to this project yet.')).toBeVisible();
  await expect.poll(() => placement(space)).toBeNull();
  await section.getByLabel('Link a database').selectOption('Launch tasks');
  await expect(section.getByRole('link', 'Launch tasks')).toBeVisible();
  await expect.poll(() => placement(space)).toBe(projectId);

  // The Databases tab shows the same list.
  await screen.getByRole('button', 'Databases').tap();
  await expect(section.getByRole('link', 'Launch tasks')).toBeVisible();
  await section.getByRole('link', 'Launch tasks').tap();
  await expect(browser).toHaveURL(`/app/databases/${launchId}`);
});

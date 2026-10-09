import { test } from '@e2e-dev/web';
import { expect } from 'e2e';
import { openHome, unique } from './support/stack.ts';

// Projects are workspace items on the backend; a task created under one shows up in
// the project's Tasks tab and in the task filter.
test('a project is created, renamed, and groups a task written under it', { session: 'owner' }, async ({ app, screen, browser }) => {
  const name = unique('Stack project');
  const renamed = `${name} renamed`;
  await app.open('/app/projects');
  await screen.getByRole('button', 'New project').tap();
  await screen.getByPlaceholder('What are you working on?').fill(name);
  await screen.getByRole('button', 'Create project').tap();
  await expect(browser).toHaveURL(/\/app\/projects\/[0-9a-f-]{36}$/);
  await expect(screen.getByRole('heading', name)).toBeVisible();

  await screen.getByRole('button', 'Edit project').tap();
  await screen.getByPlaceholder('What are you working on?').fill(renamed);
  await screen.getByRole('button', 'Save project').tap();
  await expect(screen.getByRole('heading', renamed)).toBeVisible();

  const task = unique('Project task');
  await openHome(app, screen);
  await screen.getByRole('group', 'Quick actions').getByRole('button', 'New task').tap();
  const dialog = screen.getByRole('dialog', 'New task');
  await dialog.getByLabel('Title').fill(task);
  await dialog.getByRole('combobox', 'Project').selectOption(renamed);
  await dialog.getByRole('button', 'Save').tap();
  await expect(browser).toHaveURL(/\/app\/tasks\/[0-9a-f-]{36}$/);

  await app.open('/app/tasks?view=list');
  await screen.getByLabel('Filter tasks by project').selectOption(renamed);
  await expect(screen.getByRole('link', task)).toBeVisible();

  await app.open('/app/projects');
  await expect(screen.getByRole('link', new RegExp(renamed))).toBeVisible();
});

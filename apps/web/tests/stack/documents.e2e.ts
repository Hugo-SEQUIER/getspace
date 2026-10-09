import { test } from '@e2e-dev/web';
import { expect } from 'e2e';
import { openHome, spaceId, unique } from './support/stack.ts';

// A document's life on the real stack: written, edited into a second revision, both
// revisions readable from history, then deleted with every revision.
test('a document is edited into a new revision, its history stays readable, and it can be deleted', { session: 'owner' }, async ({ app, screen, browser }) => {
  const title = unique('Stack history page');
  await openHome(app, screen);
  await screen.getByRole('group', 'Quick actions').getByRole('button', 'New page').tap();
  const create = screen.getByRole('dialog', 'New document');
  await create.getByLabel('Title').fill(title);
  await create.getByLabel('Content').fill('First draft.');
  await create.getByRole('button', 'Save').tap();
  await expect(browser).toHaveURL(/\/app\/documents\/[0-9a-f-]{36}$/);
  const id = (await browser.evaluate(() => location.pathname)).split('/').at(-1)!;
  const space = await spaceId(browser);
  await expect(screen.getByRole('button', 'Revision 1')).toBeVisible();

  await screen.getByRole('button', 'Edit').tap();
  const edit = screen.getByRole('dialog', 'Edit document');
  await edit.getByLabel('Content').fill('Second draft, saved through the real backend.');
  await edit.getByRole('button', 'Save').tap();
  await expect(screen.getByRole('dialog', 'Edit document')).toHaveCount(0);
  await expect(screen.getByText('Second draft, saved through the real backend.')).toBeVisible();
  await expect(screen.getByRole('button', 'Revision 2')).toBeVisible();

  await screen.getByRole('button', 'Document history').tap();
  await screen.getByRole('button', 'Revision 1 Saved version').tap();
  await expect(browser).toHaveURL(new RegExp(`/app/documents/${id}\\?revision=1$`));
  await expect(screen.getByText('First draft.')).toBeVisible();
  await screen.getByRole('button', 'Back to current').tap();
  await expect(browser).toHaveURL(new RegExp(`/app/documents/${id}$`));
  await expect(screen.getByText('Second draft, saved through the real backend.')).toBeVisible();

  await screen.getByRole('button', 'Delete document').tap();
  await screen.getByRole('dialog', 'Delete document?').getByRole('button', 'Delete document').tap();
  await expect(browser).toHaveURL(/\/app\/documents$/);
  await expect(screen.getByRole('link', title)).toHaveCount(0);
  const gone = await browser.evaluate(async (path: string) => (await fetch(path)).status, `/api/spaces/${space}/resources/${id}`);
  expect(gone, 'every revision is gone from the backend').toBe(404);
});

import { test } from '@e2e-dev/web';
import { expect } from 'e2e';
import { randomUUID } from 'node:crypto';
import { strToU8, zipSync } from 'fflate';
import { rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { openHome, spaceId, unique } from './support/stack.ts';

// Moving from the hosted Space means restoring one large export. Archives over 100 MiB are
// common once databases keep their history, so this restores one of about 107 MiB, zipped
// the way the export downloads it, through the real screen: unzipped and read in the
// browser, uploaded in parts, reviewed and applied.
test('a zipped archive larger than 100 MiB is uploaded, reviewed and restored', { session: 'owner', timeout: 300_000 }, async ({ app, screen, browser }) => {
  await openHome(app, screen);
  const space = await spaceId(browser);
  const seed = await browser.evaluate(async (space: string) => {
    const headers = { 'Content-Type': 'application/json' };
    const saved = await fetch(`/api/spaces/${space}/resources`, {
      method: 'POST', headers,
      body: JSON.stringify({ command_id: crypto.randomUUID(), expected_revision: 0, kind: 'document', title: 'Archive seed', body: 'Seed.' }),
    });
    const { id } = await saved.json();
    const first = await fetch(`/api/spaces/${space}/export`, { method: 'POST', headers, body: '{}' });
    const page = await first.json();
    let after = page.next_cursor;
    let entry = page.entries.find((e: { value: { id: string } }) => e.value.id === id);
    while (!entry && after) {
      const next = await (await fetch(`/api/spaces/${space}/export`, {
        method: 'POST', headers, body: JSON.stringify({ after, snapshot: page.snapshot }),
      })).json();
      entry = next.entries.find((e: { value: { id: string } }) => e.value.id === id);
      after = next.next_cursor;
    }
    return { envelope: { format: page.format, source_space: page.source_space, snapshot: page.snapshot, exported_at: page.exported_at }, entry };
  }, space);
  expect(seed.entry, 'the seed document is in the export').toBeTruthy();

  // One new document with 165 full-size revisions: about 107 MiB, all of it valid content.
  const id = randomUUID();
  const title = unique('Large archive page');
  const body = 'A line of archived history that fills the largest allowed document.\n'.repeat(10_000);
  const revisions = 165;
  const { author_agent: _, ...template } = seed.entry.value;
  const entries = Array.from({ length: revisions }, (_, index) => ({
    entity: 'resource',
    current: index === revisions - 1,
    value: { ...template, id, title, body, revision: index + 1 },
  }));
  const file = join(tmpdir(), `space-large-archive-${id}.zip`);
  writeFileSync(file, zipSync({ 'space-archive.json': strToU8(JSON.stringify({ ...seed.envelope, entries })) }));
  try {
    await app.open('/app/settings/data');
    await expect(screen.getByRole('heading', 'Restore an archive')).toBeVisible();
    await screen.getByLabel('Space archive').setInputFiles([file]);
    await expect(screen.getByRole('heading', 'Review merge')).toBeVisible({ timeout: 240_000 });
    await screen.getByRole('button', 'Apply reviewed merge').tap();
    // The Space reloads after a restore; the confirmation must survive it.
    await expect(screen.getByRole('status').filter({ hasText: 'Archive restored' })).toContainText(
      `1 documents or memories, 0 projects, databases or other items, 0 agent identities and ${revisions} archived revisions`,
      { timeout: 120_000 },
    );
  } finally {
    rmSync(file, { force: true });
  }

  const restored = await browser.evaluate(async ([space, id]: string[]) => {
    const response = await fetch(`/api/spaces/${space}/resources/${id}`);
    const value = await response.json();
    return { status: response.status, title: value.title, revision: value.revision };
  }, [space, id]);
  expect(restored).toEqual({ status: 200, title, revision: revisions });
});

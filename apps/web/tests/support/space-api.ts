import { test as base, type Browser } from '@e2e-dev/web';
import { expect } from 'e2e';
import { PROXY_LOCAL, proxyAllowed, proxyPath } from '../../lib/space/proxy-allowlist.ts';

export const spaceId = '11111111-1111-4111-8111-111111111111';
export const taskId = '22222222-2222-4222-8222-222222222222';
export const otherId = '33333333-3333-4333-8333-333333333333';
export const createdId = '44444444-4444-4444-8444-444444444444';
export const defaultColumns = [
  { value: 'todo', label: 'To do' },
  { value: 'in_progress', label: 'In progress' },
  { value: 'done', label: 'Done' },
];

export type Task = {
  id: string;
  kind: 'task';
  title: string;
  body: string;
  status: string;
  revision: number;
  project?: string;
  priority?: string;
  author: string;
  author_agent?: { id: string; name: string; harness: string; revision: number } | null;
  updated_at: string;
};
export type Document = Omit<Task, 'kind'> & { kind: 'document' };
export type SeenMark = { resource_id: string; seen_revision: number; seen_at: string };
/** A project or database as the items API returns it: its data plus identity and revision. */
export type Item = { id: string; kind: 'project' | 'database'; revision: number; deleted?: boolean; [field: string]: unknown };
export const projectId = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
/** The fake API's access key. It is a fixture value, not a credential of any installation. */
export const fixtureAccessKey = 'e2e-fixture-access-key-0123456789abcdef';
/** The connection token the fake API issues; it grants nothing anywhere. */
export const fixtureConnectionToken = 'spc_E2eFixtureConnectionToken0000';
export const createdAgentId = '99999999-9999-4999-8999-999999999999';
export const createdClientId = '77777777-0000-4000-8000-000000000001';
export type AgentIdentity = { id: string; name: string; harness: string; revision: number };
export type Connection = { id: string; client_id: string; agent_id: string | null; name: string; harness: string; can_write: boolean; revoked_at: string | null };
export type SeenCommand = { items: { resource_id: string; revision: number }[] };
export type StatusCommand = { id: string; command_id: string; expected_revision: number; status: string };

/** Holds every matching request until `release()`, then lets later ones through at once. */
export class Gate {
  arrivals = 0;
  release!: () => void;
  readonly opened = new Promise<void>(resolve => { this.release = resolve; });
}

export function sampleTasks(): Task[] {
  return [
    { id: taskId, kind: 'task', title: 'Keep task content', body: '## Description\nDo not erase this description.', project: 'project-a', priority: 'High', status: 'todo', revision: 1, author: 'you', updated_at: '2026-09-23T00:00:00Z' },
    { id: otherId, kind: 'task', title: 'Independent task', body: 'Second description', status: 'todo', revision: 1, author: 'you', updated_at: '2026-09-23T00:00:00Z' },
  ];
}

export function extraTasks(count: number): Task[] {
  return Array.from({ length: count }, (_, index) => ({
    id: `55555555-5555-4555-8555-${String(index + 1).padStart(12, '0')}`,
    kind: 'task', title: `Extra task ${index + 1}`, body: 'Extra fixture description.', status: 'todo', revision: 1,
    author: 'you', updated_at: '2026-09-23T00:00:00Z',
  }));
}

// Parameters: the browser and the app origin.
// What it does: answers every /api request from in-memory Space data, keeps the app's own
// assets on the network, and aborts every other origin.
// Output: the mutable data plus the requests the app sent, for assertions.
export async function mockSpace(browser: Browser, baseUrl: string) {
  const state = {
    space: { id: spaceId, name: 'Task fixture', role: 'owner' as 'owner' | 'viewer', kind: 'personal', mcp_url: '' },
    /** How the website guards this Space, and whether this browser is unlocked. */
    access: { mode: 'key' as 'key' | 'private', unlocked: true },
    /** Every key typed into the unlock form, in order. */
    unlockAttempts: [] as string[],
    /** The owner's display name, as SPACE_OWNER_NAME sets it. */
    ownerName: 'Owner',
    tasks: sampleTasks(),
    documents: [] as Document[],
    projects: [{ id: 'project-a', kind: 'project', name: 'Fixture project', description: '', status: 'todo', revision: 1 }] as Item[],
    databases: [] as Item[],
    /** Every project or database save the app sent, in order. */
    itemCommands: [] as Record<string, unknown>[],
    agents: [] as AgentIdentity[],
    connections: [] as Connection[],
    /** Every POST /agent-tokens body, in order. */
    tokenRequests: [] as Record<string, unknown>[],
    /** The revision of each resource the signed-in person last opened. */
    views: [] as SeenMark[],
    seenCommands: [] as SeenCommand[],
    columns: { columns: [...defaultColumns], revision: 1 },
    statusCommands: [] as StatusCommand[],
    resourceCommands: [] as Record<string, unknown>[],
    resourceReads: [] as string[],
    deletes: [] as { id: string; expected: number }[],
    taskListReads: 0,
    /** Full search-index reads; typing must never add one. */
    searchIndexReads: 0,
    /** Task IDs whose next status save fails with 503. */
    failStatus: new Set<string>(),
    /** Task IDs whose next delete fails with 409. */
    failDelete: new Set<string>(),
    failCreate: false,
    exportRequests: [] as { after: string | null; snapshot?: number }[],
    /** The last archive upload: its manifest and the text of each part. */
    restore: null as { manifest: { id: string; sha256: string; bytes: number; parts: number }; parts: string[] } | null,
    unexpected: [] as string[],
    /** Requests the app sent that the website's /api proxy would answer with 404. */
    unproxied: [] as string[],
    gates: new Map<string, Gate>(),
    /** Holds `status:<id>`, `create` or `detail:<id>` requests until the gate is released. */
    hold(key: string) {
      const gate = new Gate();
      state.gates.set(key, gate);
      return gate;
    },
  };
  async function pass(key: string) {
    const gate = state.gates.get(key);
    if (!gate) return;
    gate.arrivals++;
    await gate.opened;
  }
  const origin = new URL(baseUrl).origin;
  await browser.route('**/*', async route => {
    const url = new URL(route.request.url);
    if (url.origin !== origin) return route.abort();
    if (!url.pathname.startsWith('/api/')) return route.continue();
    const path = url.pathname.slice('/api/'.length);
    const method = route.request.method;
    // The fixture answers in the browser, so it must also stand in for the proxy's gate:
    // a path the hosted app would refuse is a bug even when the fake API answers it.
    if (!PROXY_LOCAL.test(path) && !proxyAllowed(proxyPath(path), method)) state.unproxied.push(`${method} ${path}`);
    const body = route.request.postData ? JSON.parse(route.request.postData) : undefined;
    const send = (json: unknown, status = 200) => route.fulfill({ json: json as never, status });
    // The website's own auth/* routes: the session state, unlocking and locking.
    if (path === 'auth/session') return send(state.access.mode === 'private' ? { mode: 'private', unlocked: true } : state.access);
    if (path === 'auth/unlock' && method === 'POST') {
      if (state.access.mode === 'private') return send({ mode: 'private', unlocked: true });
      state.unlockAttempts.push(body.key);
      if (body.key !== fixtureAccessKey) return send({ error: 'That access key is not correct.' }, 401);
      state.access.unlocked = true;
      return send(state.access);
    }
    if (path === 'auth/lock' && method === 'POST') {
      if (state.access.mode === 'private') return send({ mode: 'private', unlocked: true });
      state.access.unlocked = false;
      return send(state.access);
    }
    if (state.access.mode === 'key' && !state.access.unlocked)
      return send({ error: 'This Space is locked. Enter the access key to continue.' }, 401);
    if (path === 'workspace')
      return send({ ...state.space, user: { id: 'owner', email: null }, profile: { displayName: state.ownerName, avatarUrl: null }, access_mode: state.access.mode });
    if (path === 'workspaces') return send({ items: [state.space] });
    if (path.endsWith('/views')) {
      if (method === 'POST') {
        state.seenCommands.push(body);
        for (const item of body.items as { resource_id: string; revision: number }[]) {
          const existing = state.views.find(view => view.resource_id === item.resource_id);
          if (existing) existing.seen_revision = Math.max(existing.seen_revision, item.revision);
          else state.views.push({ resource_id: item.resource_id, seen_revision: item.revision, seen_at: '2026-09-23T00:00:00Z' });
        }
      }
      return send({ items: state.views });
    }
    if (path.endsWith('/search/index')) {
      state.searchIndexReads++;
      const entries = [...state.tasks, ...state.documents].map(({ body, author, revision, ...entry }) => ({ ...entry, text: body }));
      return send({ items: entries, next_cursor: null });
    }
    if (path.endsWith('/tasks/columns')) {
      if (method === 'POST') {
        expect(body.expected_revision).toBe(state.columns.revision);
        expect(body.command_id).toBeTruthy();
        state.columns = { columns: [...state.columns.columns, { value: body.value, label: body.label }], revision: state.columns.revision + 1 };
      }
      return send(state.columns);
    }
    const status = /\/tasks\/([^/]+)\/status$/.exec(path);
    if (status) {
      const id = status[1];
      state.statusCommands.push({ ...body, id });
      await pass(`status:${id}`);
      if (state.failStatus.delete(id)) return send({ error: 'Status save failed. Please retry.' }, 503);
      const task = state.tasks.find(item => item.id === id);
      if (!task || task.revision !== body.expected_revision)
        return send({ error: 'This task changed. Reopen it to review the latest version.' }, 409);
      const saved = { ...task, status: body.status, revision: task.revision + 1 };
      state.tasks = state.tasks.map(item => item.id === id ? saved : item);
      return send(saved);
    }
    if (path.endsWith('/resources')) {
      if (method === 'POST') {
        state.resourceCommands.push(body);
        expect(body.expected_revision).toBe(0);
        expect(body.kind).toBe('task');
        expect(body.command_id).toBeTruthy();
        await pass('create');
        if (state.failCreate) { state.failCreate = false; return send({ error: 'Task creation failed. Please retry.' }, 503); }
        const { command_id, expected_revision, resource_id, ...draft } = body;
        const saved = { ...draft, id: createdId, revision: 1, author: 'you', updated_at: '2026-09-23T00:00:00Z' };
        state.tasks = [...state.tasks, saved];
        return send(saved);
      }
      const tasks = url.searchParams.get('kind') === 'task';
      if (tasks) state.taskListReads++;
      const listed: (Task | Document)[] = tasks ? state.tasks : state.documents;
      return send({ items: listed.map(({ body, ...summary }) => summary), next_cursor: null });
    }
    const resource = /\/resources\/([^/]+)$/.exec(path);
    if (resource && method === 'DELETE') {
      const id = resource[1], expected = Number(url.searchParams.get('expected_revision'));
      state.deletes.push({ id, expected });
      const task = state.tasks.find(item => item.id === id);
      if (state.failDelete.delete(id) || (task && task.revision !== expected))
        return send({ error: 'This resource changed. Reload before deleting.' }, 409);
      state.tasks = state.tasks.filter(item => item.id !== id);
      return send({ deleted: true });
    }
    if (resource) {
      state.resourceReads.push(resource[1]);
      await pass(`detail:${resource[1]}`);
      const task = state.tasks.find(item => item.id === resource[1])
        ?? state.documents.find(item => item.id === resource[1]);
      return task ? send(task) : send({ error: 'Task not found' }, 404);
    }
    const listOf = (kind: unknown) => kind === 'project' ? 'projects' : kind === 'database' ? 'databases' : null;
    const item = /\/items\/([^/]+)(\/visibility)?$/.exec(path);
    if (item) {
      const key = (['projects', 'databases'] as const).find(name => state[name].some(entry => entry.id === item[1]));
      const current = key && state[key].find(entry => entry.id === item[1]);
      if (!key || !current) return send({ error: 'Workspace item not found' }, 404);
      if (method === 'GET' && !item[2]) return send(current);
      if (method !== 'POST' || !item[2]) { state.unexpected.push(`${method} ${path}`); return send({ error: 'Unexpected item request' }, 500); }
      if (current.revision !== body.expected_revision) return send({ error: 'This workspace item changed. Refresh before saving.' }, 409);
      const saved = { ...current, deleted: body.deleted, revision: current.revision + 1 };
      state[key] = state[key].map(entry => entry.id === saved.id ? saved : entry);
      return send(saved);
    }
    if (path.endsWith('/items') && method === 'POST') {
      state.itemCommands.push(body);
      const key = listOf(body.kind);
      if (!key) { state.unexpected.push(`${method} ${path} ${body.kind}`); return send({ error: 'Unexpected item kind' }, 500); }
      const current = state[key].find(entry => entry.id === body.item_id);
      if ((current?.revision ?? 0) !== body.expected_revision) return send({ error: 'This workspace item changed. Refresh before saving.' }, 409);
      const saved: Item = { ...body.data, id: body.item_id, kind: body.kind, revision: body.expected_revision + 1, deleted: false, updated_at: '2026-09-23T00:00:00Z', author_agent: null };
      state[key] = current ? state[key].map(entry => entry.id === saved.id ? saved : entry) : [...state[key], saved];
      return send(saved);
    }
    if (path.endsWith('/items')) {
      const key = listOf(url.searchParams.get('kind'));
      const deleted = url.searchParams.get('deleted') === 'true';
      return send({ items: key ? state[key].filter(entry => !!entry.deleted === deleted) : [], next_cursor: null });
    }
    if (path.endsWith('/search') && method === 'POST') {
      const query = String(body.query ?? '').toLowerCase();
      const items = [...state.tasks, ...state.documents]
        .filter(resource => (!body.project || resource.project === body.project) && resource.title.toLowerCase().includes(query))
        .map(resource => ({ id: resource.id, kind: resource.kind, title: resource.title, description: resource.body, status: resource.status, updated_at: resource.updated_at }));
      return send({ items, next_cursor: null, snapshot: 1, scanned: items.length });
    }
    if (path.endsWith('/export')) {
      // One task per page, like a large Space: later pages carry the cursor and the snapshot.
      state.exportRequests.push(body);
      const start = body?.after ? Number(body.after) : 0;
      return send({ format: 'space.archive.v1', source_space: spaceId, snapshot: 1, exported_at: '2026-09-23T00:00:00Z',
        ...(start ? {} : { task_columns: state.columns.columns }),
        entries: state.tasks.slice(start, start + 1).map(value => ({ entity: 'resource', current: true, value })),
        next_cursor: start + 1 < state.tasks.length ? String(start + 1) : null });
    }
    if (path.endsWith('/usage'))
      return send({ retained_bytes: 1000, storage_limit_bytes: null, items: 2, item_limit: null, model_requests: 0, model_request_limit: null, plan: null });
    if (path.endsWith('/restores') && method === 'POST') {
      state.restore = { manifest: body, parts: [] };
      return send({ id: body.id, state: 'uploading' });
    }
    const restorePart = path.match(/\/restores\/[^/]+\/parts\/(\d+)$/);
    if (restorePart && method === 'POST') {
      state.restore!.parts[Number(restorePart[1])] = body.text;
      return send({ received: true });
    }
    if (/\/restores\/[^/]+\/review$/.test(path) && method === 'POST') {
      const archive = JSON.parse(state.restore!.parts.join(''));
      return send({ snapshot: archive.snapshot, issues: [], items: archive.entries.map((entry: { value: Task }) => ({
        key: entry.value.id, title: entry.value.title, entity: 'resource', action: 'add', conflict: false, incoming_revisions: 1, existing_revision: null,
      })) });
    }
    if (path.endsWith('/restores')) return send({ items: [] });
    if (path.endsWith('/memories')) return send({ items: [], next_cursor: null, provider: { enabled: false } });
    if (path === 'agent-identities' && method === 'POST') {
      const agent = { id: createdAgentId, name: body.name, harness: body.harness, revision: 1 };
      state.agents = [...state.agents.filter(item => item.id !== agent.id), agent];
      return send(agent);
    }
    if (path === 'agent-identities') return send({ items: state.agents });
    if (path === 'agents') return send({ items: state.connections });
    if (path === 'agent-tokens' && method === 'POST') {
      state.tokenRequests.push(body);
      const connection = { id: '77777777-0000-4000-8000-000000000002', client_id: createdClientId, agent_id: body.agent_id, name: body.name, harness: body.harness, can_write: body.can_write, revoked_at: null };
      state.connections = [...state.connections, connection];
      const headers: Record<string, string> = { Authorization: `Bearer ${fixtureConnectionToken}` };
      if (body.agent_id) headers['X-Space-Agent-ID'] = body.agent_id;
      return send({ access_token: fixtureConnectionToken, token_type: 'Bearer', expires_in: 7_776_000, agent_id: body.agent_id, mcp_url: 'https://space.example.invalid/mcp/', headers });
    }
    const connection = /^agents\/([^/]+)$/.exec(path);
    if (connection && method === 'DELETE') {
      state.connections = state.connections.map(item => item.client_id === connection[1] ? { ...item, revoked_at: '2026-10-09T00:00:00Z' } : item);
      return send({ revoked: true });
    }
    if (path.endsWith('/memory/jobs')) return send({ items: [] });
    state.unexpected.push(`${method} ${path}`);
    return send({ error: `Unexpected fixture request: ${path}` }, 500);
  });
  return state;
}

export type SpaceMock = Awaited<ReturnType<typeof mockSpace>>;

/** Records uncaught page errors in session storage, so they survive reloads within the test. */
export async function watchPageErrors(browser: Browser) {
  await browser.addInitScript(() => {
    const record = (message: string) => {
      try {
        const errors = JSON.parse(sessionStorage.getItem('e2e:page-errors') ?? '[]');
        sessionStorage.setItem('e2e:page-errors', JSON.stringify([...errors, message]));
      } catch {}
    };
    // Browsers report a ResizeObserver callback that resizes what it observes (an
    // auto-growing textarea) as an error event; it is a scheduling notice, not a failure.
    addEventListener('error', event => {
      if (!/^ResizeObserver loop (completed with undelivered notifications|limit exceeded)/.test(event.message)) record(event.message);
    });
    addEventListener('unhandledrejection', event => record(String(event.reason?.message ?? event.reason)));
  });
}

export function pageErrors(browser: Browser) {
  return browser.evaluate(() => {
    try { return JSON.parse(sessionStorage.getItem('e2e:page-errors') ?? '[]') as string[]; }
    catch { return []; }
  });
}

/** `test` with a mocked Space API; each test starts from fresh fixture data. */
export const test = base.extend<{ space: SpaceMock }>({
  space: async ({ browser, app }, use) => {
    await watchPageErrors(browser);
    const space = await mockSpace(browser, app.baseUrl!);
    await use(space);
    expect(space.unexpected).toEqual([]);
    expect(space.unproxied, 'every request must pass the website proxy allowlist').toEqual([]);
    expect(await pageErrors(browser)).toEqual([]);
  },
});

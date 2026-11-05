import { createHash } from 'node:crypto';
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
export type FileThumbnail = { id: string; type: string; bytes: number; sha256: string; width: number; height: number; parts: number };
export type DocumentFile = {
  id: string; name: string; type: string; bytes: number; sha256: string; parts: number;
  width?: number; height?: number; duration?: number; thumbnail?: FileThumbnail;
};
export type Document = Omit<Task, 'kind'> & { kind: 'document'; folder?: string; file?: DocumentFile; recall?: { category: string } };
export type FolderRow = { id: string; kind: 'folder'; name: string; description: string; section_id: string | null; project_id?: string | null; revision: number; deleted: boolean };
export type SectionRow = { id: string; kind: 'section'; name: string; description: string; revision: number; deleted: boolean };
/** The fixture backend's part size, the same as the real one. */
export const FILE_PART_BYTES = 1_048_576;
/** Parameters: the namespace UUID and a name, as the backend calls uuid5(command_id, kind).
 * What it does: builds the version 5 UUID (SHA-1 of the namespace bytes and the name) that a
 * create derives from its command, so a retried create lands on the item it already made.
 * Output: the UUID string. */
export function uuid5(namespace: string, name: string) {
  const digest = createHash('sha1')
    .update(Buffer.concat([Buffer.from(namespace.replace(/-/g, ''), 'hex'), Buffer.from(name, 'utf8')]))
    .digest();
  digest[6] = (digest[6] & 0x0f) | 0x50;
  digest[8] = (digest[8] & 0x3f) | 0x80;
  const hex = digest.subarray(0, 16).toString('hex');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
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
    /** The memory processor the backend reports with the memory listing. */
    memoryProvider: { enabled: false } as Record<string, unknown>,
    tasks: sampleTasks(),
    documents: [] as Document[],
    projects: [{ id: 'project-a', kind: 'project', name: 'Fixture project', description: '', status: 'todo', revision: 1 }] as Item[],
    databases: [] as Item[],
    /** Document folders and the sections that group them, active and archived. */
    folders: [] as FolderRow[],
    sections: [] as SectionRow[],
    /** Every project, database, folder or section save the app sent, in order. */
    itemCommands: [] as Record<string, unknown>[],
    agents: [] as AgentIdentity[],
    connections: [] as Connection[],
    /** Every POST /agent-tokens body, in order. */
    tokenRequests: [] as Record<string, unknown>[],
    /** The revision of each resource the signed-in person last opened. */
    views: [] as SeenMark[],
    memoryJobs: [] as { id: string; status: string; attempts: number; error_code: string | null; result: null }[],
    /** Bodies of every database change command, in order. */
    databaseCommands: [] as Record<string, unknown>[],
    /** Database detail reads, by database ID: a listing never carries rows. */
    databaseReads: [] as string[],
    /** Systems and lab entries, active and archived, every revision of each entry, and the
     * bodies of every Systems command, in order. */
    systems: [] as SystemRow[],
    labEntries: [] as LabEntryRow[],
    labHistory: {} as Record<string, (LabEntryRow & { author: string })[]>,
    systemCommands: [] as Record<string, unknown>[],
    webhooks: [] as WebhookRow[],
    /** Bodies of every subscription create or change command, in order. */
    webhookCommands: [] as Record<string, unknown>[],
    webhookDeliveries: [] as Record<string, unknown>[],
    profileImage: null as string | null,
    profileImageCommands: [] as string[],
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
    /** Staged base64 parts of each uploaded file, by file ID. */
    fileParts: new Map<string, string[]>(),
    /** Bodies of every file save, in order, and the document each command created. */
    fileCommands: [] as Record<string, unknown>[],
    fileDocuments: new Map<string, Document>(),
    /** `<document id>/<part>` for every file part the app read. */
    fileReads: [] as string[],
    /** File IDs whose staged parts the app discarded after a failed upload. */
    fileDiscards: [] as string[],
    /** A part number whose upload is refused, as a part the Space already holds. */
    failFilePart: null as number | null,
    /** The last archive upload: its manifest and the text of each part. */
    restore: null as { manifest: { id: string; sha256: string; bytes: number; parts: number }; parts: string[] } | null,
    unexpected: [] as string[],
    /** Requests the app sent that the website's /api proxy would answer with 404. */
    unproxied: [] as string[],
    gates: new Map<string, Gate>(),
    /** Holds `status:<id>`, `create`, `detail:<id>`, `index` or `agents` requests until the gate is released. */
    hold(key: string) {
      const gate = new Gate();
      state.gates.set(key, gate);
      return gate;
    },
  };
  function putEntry(saved: LabEntryRow) {
    state.labEntries = [...state.labEntries.filter(item => item.id !== saved.id), saved];
    (state.labHistory[saved.id] ??= []).push({ ...saved, author: 'you' });
  }
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
    if (path === 'profile/image') {
      if (method !== 'GET') state.profileImageCommands.push(method);
      if (method === 'POST') state.profileImage = body.image;
      if (method === 'DELETE') state.profileImage = null;
      return send({ image: state.profileImage });
    }
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
      await pass('index');
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
    // Original files: staged parts, the save that turns them into a document, and part reads,
    // with the backend's checks on part size, part count and the whole file's SHA-256.
    const filePart = /\/files\/([^/]+)\/parts\/(\d+)$/.exec(path);
    if (filePart && method === 'POST') {
      const [, fileId, index] = filePart, part = Number(index);
      const raw = Buffer.from(body.data, 'base64');
      if (part >= 250) return send({ error: 'A file has at most 250 parts' }, 422);
      if (!raw.length) return send({ error: 'A file part cannot be empty' }, 422);
      if (raw.length > FILE_PART_BYTES) return send({ error: 'A file part holds at most 1 MiB' }, 413);
      // The one refusal a Space can give a part it already holds: other bytes under the same
      // number. The whole upload is retried, never the part alone.
      if (part === state.failFilePart) return send({ error: 'This file part was already uploaded with different content' }, 409);
      const parts = state.fileParts.get(fileId) ?? [];
      if (parts[part] !== undefined && parts[part] !== body.data)
        return send({ error: 'This file part was already uploaded with different content' }, 409);
      parts[part] = body.data;
      state.fileParts.set(fileId, parts);
      return send({ file_id: fileId, part, bytes: raw.length });
    }
    const fileDiscard = /\/files\/([^/]+)$/.exec(path);
    if (fileDiscard && method === 'DELETE') {
      state.fileDiscards.push(fileDiscard[1]);
      const committed = state.documents.some(item => item.file?.id === fileDiscard[1] || item.file?.thumbnail?.id === fileDiscard[1]);
      const parts = committed ? [] : state.fileParts.get(fileDiscard[1]) ?? [];
      if (!committed) state.fileParts.delete(fileDiscard[1]);
      return send({ discarded: parts.filter(Boolean).length });
    }
    if (path.endsWith('/files') && method === 'POST') {
      state.fileCommands.push(body);
      expect(body.command_id).toBeTruthy();
      const earlier = state.fileDocuments.get(body.command_id);
      if (earlier) return send(earlier);
      const staged = state.fileParts.get(body.file_id) ?? [];
      const count = Math.ceil(body.bytes / FILE_PART_BYTES);
      const parts = staged.map(part => Buffer.from(part, 'base64'));
      if (staged.length !== count || parts.some((part, index) => !part || (index < count - 1 && part.length !== FILE_PART_BYTES)))
        return send({ error: 'Some parts of this file are missing. Upload it again.' }, 409);
      const whole = Buffer.concat(parts);
      if (whole.length !== body.bytes || createHash('sha256').update(whole).digest('hex') !== body.sha256)
        return send({ error: 'The uploaded parts do not match this file. Upload it again.' }, 409);
      // A thumbnail is one staged part under its own ID, checked like the file's parts; it
      // must fit the backend's 64 KiB cap and be WebP or JPEG.
      let thumbnail: FileThumbnail | undefined;
      if (body.thumbnail) {
        const small = (state.fileParts.get(body.thumbnail.id) ?? []).map(part => part && Buffer.from(part, 'base64'));
        if (small.length !== 1 || !small[0] || small[0].length !== body.thumbnail.bytes
          || createHash('sha256').update(small[0]).digest('hex') !== body.thumbnail.sha256)
          return send({ error: 'The thumbnail upload is incomplete. Upload the file again.' }, 409);
        if (body.thumbnail.bytes > 65_536 || !['image/webp', 'image/jpeg'].includes(body.thumbnail.type)
          || body.thumbnail.id === body.file_id)
          return send({ error: 'Invalid thumbnail.' }, 422);
        thumbnail = { ...body.thumbnail, parts: 1 };
      }
      const saved: Document = {
        id: `aaaaaaaa-aaaa-4aaa-8aaa-${String(state.fileDocuments.size + 1).padStart(12, '0')}`,
        kind: 'document', title: body.title, body: body.body, status: 'todo', revision: 1, author: 'you',
        updated_at: '2026-10-22T09:00:00Z',
        ...(body.project ? { project: body.project } : {}), ...(body.folder ? { folder: body.folder } : {}),
        file: {
          id: body.file_id, name: body.name, type: body.type.toLowerCase(), bytes: body.bytes, sha256: body.sha256, parts: count,
          ...(body.width ? { width: body.width, height: body.height } : {}),
          ...(body.duration ? { duration: body.duration } : {}),
          ...(thumbnail ? { thumbnail } : {}),
        },
      };
      state.fileDocuments.set(body.command_id, saved);
      state.documents = [...state.documents, saved];
      return send(saved);
    }
    const thumbnailRead = /\/resources\/([^/]+)\/file\/thumbnail$/.exec(path);
    if (thumbnailRead && method === 'GET') {
      const document = state.documents.find(item => item.id === thumbnailRead[1]);
      state.fileReads.push(`${thumbnailRead[1]}/thumbnail`);
      const thumbnail = document?.file?.thumbnail;
      const data = thumbnail && state.fileParts.get(thumbnail.id)?.[0];
      return data ? send({ data, type: thumbnail.type }) : send({ error: 'This file has no thumbnail.' }, 404);
    }
    const fileRead = /\/resources\/([^/]+)\/file\/(\d+)$/.exec(path);
    if (fileRead && method === 'GET') {
      const document = state.documents.find(item => item.id === fileRead[1]);
      state.fileReads.push(`${fileRead[1]}/${fileRead[2]}`);
      const data = document?.file && state.fileParts.get(document.file.id)?.[Number(fileRead[2])];
      return data ? send({ data }) : send({ error: 'This document has no file.' }, 404);
    }
    if (path.endsWith('/resources')) {
      if (method === 'POST') {
        state.resourceCommands.push(body);
        expect(['task', 'document']).toContain(body.kind);
        expect(body.command_id).toBeTruthy();
        if (body.resource_id) {
          // An update: the website sends the whole resource at the revision it last saw.
          const list: (Task | Document)[] = body.kind === 'document' ? state.documents : state.tasks;
          const current = list.find(item => item.id === body.resource_id);
          if (!current || current.revision !== body.expected_revision)
            return send({ error: 'This resource changed. Load the latest revision before saving.' }, 409);
          const { command_id, expected_revision, resource_id, folder, project, ...fields } = body;
          const saved = {
            ...current, ...fields, revision: current.revision + 1, updated_at: '2026-10-22T10:00:00Z',
            ...(folder !== undefined ? { folder: folder || undefined } : {}),
            ...(project !== undefined ? { project: project || undefined } : {}),
          };
          if (body.kind === 'document') state.documents = state.documents.map(item => item.id === saved.id ? saved as Document : item);
          else state.tasks = state.tasks.map(item => item.id === saved.id ? saved as Task : item);
          return send(saved);
        }
        expect(body.expected_revision).toBe(0);
        await pass('create');
        if (state.failCreate) { state.failCreate = false; return send({ error: 'Task creation failed. Please retry.' }, 503); }
        const { command_id, expected_revision, resource_id, ...draft } = body;
        const saved = { ...draft, id: createdId, revision: 1, author: 'you', updated_at: '2026-09-23T00:00:00Z' };
        if (draft.kind === 'task') state.tasks = [...state.tasks, saved];
        else state.documents = [...state.documents, saved];
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
      // Deleting a document that keeps a file removes the file and its thumbnail too.
      const file = state.documents.find(item => item.id === id)?.file;
      if (file) {
        state.fileParts.delete(file.id);
        if (file.thumbnail) state.fileParts.delete(file.thumbnail.id);
        state.documents = state.documents.filter(item => item.id !== id);
      }
      return send({ deleted: true });
    }
    if (resource) {
      state.resourceReads.push(resource[1]);
      await pass(`detail:${resource[1]}`);
      const task = state.tasks.find(item => item.id === resource[1])
        ?? state.documents.find(item => item.id === resource[1]);
      return task ? send(task) : send({ error: 'Task not found' }, 404);
    }
    // Systems: one read of the area, saves with a revision check, and the lab's moves with the
    // backend's rules: the promotion gate, no edits to a live verdict, uses only on a verdict.
    if (path.endsWith('/systems') && method === 'GET') {
      const active = state.labEntries.filter(entry => !entry.deleted);
      return send({
        systems: state.systems.filter(system => !system.deleted).sort((a, b) => a.name.localeCompare(b.name))
          .map(system => ({ ...system, stats: labStats(active.filter(entry => entry.system_id === system.id)) })),
        entries: [...active].sort((a, b) => b.updated_at.localeCompare(a.updated_at)),
        domains: LAB_DOMAINS,
      });
    }
    if (path.endsWith('/systems') && method === 'POST') {
      state.systemCommands.push(body);
      expect(body.command_id).toBeTruthy();
      // A create names no system and saves at revision 0; an update names the system it read.
      if ((body.system_id === undefined) !== (body.expected_revision === 0))
        return send({ error: 'Create with revision 0; updates require the system ID' }, 422);
      // The ID of a created system comes from its command, so a retry lands on the same one.
      const id = body.system_id ?? uuid5(body.command_id, 'system');
      const current = state.systems.find(system => system.id === id);
      if ((current?.revision ?? 0) !== body.expected_revision)
        return send({ error: 'This system changed. Refresh before saving.' }, 409);
      if (!body.data.name?.trim()) return send({ error: 'A name is required' }, 400);
      const saved: SystemRow = {
        ...blankSystem(), ...current, ...body.data, id, kind: 'system',
        revision: body.expected_revision + 1, deleted: false, updated_at: fixtureNow(),
      };
      state.systems = [...state.systems.filter(system => system.id !== saved.id), saved];
      return send({ ...saved, stats: labStats(state.labEntries.filter(entry => !entry.deleted && entry.system_id === saved.id)) });
    }
    if (path.endsWith('/systems/entries') && method === 'POST') {
      state.systemCommands.push(body);
      const current = state.labEntries.find(entry => entry.id === body.entry_id);
      if ((current?.revision ?? 0) !== body.expected_revision)
        return send({ error: 'This lab entry changed. Refresh before saving.' }, 409);
      if (current && (current.status === 'validated' || current.status === 'invalidated'))
        return send({ error: 'This entry is a verdict. Move it back to Testing to change it' }, 409);
      if (!state.systems.some(system => system.id === body.data.system_id && !system.deleted))
        return send({ error: 'Choose an active system' }, 409);
      const saved: LabEntryRow = {
        ...blankEntry(), ...current, ...body.data, id: body.entry_id ?? uuid5(body.command_id, 'lab_entry'), revision: body.expected_revision + 1, updated_at: fixtureNow(),
      };
      putEntry(saved);
      return send(saved);
    }
    if (path.endsWith('/systems/activity') && method === 'GET') {
      const system = url.searchParams.get('system_id');
      const events = state.labEntries.filter(entry => !entry.deleted && (!system || entry.system_id === system))
        .flatMap(entry => (state.labHistory[entry.id] ?? []).map((revision, index, all) => {
          const { author, ...rest } = revision;
          const previous = index ? (({ author: _, ...earlier }) => earlier)(all[index - 1]) : null;
          return { at: revision.updated_at, author, author_agent: null, entry: rest, previous };
        }))
        .sort((a, b) => b.at.localeCompare(a.at));
      return send({ events: events.slice(0, 30) });
    }
    const labMove = /\/systems\/entries\/([^/]+)\/(promote|demote|uses|history)$/.exec(path);
    if (labMove) {
      const [, id, move] = labMove;
      const entry = state.labEntries.find(item => item.id === id);
      if (!entry) return send({ error: 'Lab entry not found' }, 404);
      if (move === 'history') return send({ revisions: state.labHistory[id] ?? [] });
      state.systemCommands.push({ ...body, id, move });
      expect(body.command_id).toBeTruthy();
      if (entry.revision !== body.expected_revision)
        return send({ error: 'This lab entry changed. Refresh before saving.' }, 409);
      const live = entry.status === 'validated' || entry.status === 'invalidated';
      const next = { ...entry, revision: entry.revision + 1, updated_at: fixtureNow(), note: body.note ?? '' };
      if (move === 'promote') {
        if (entry.status === body.verdict) return send({ error: 'This entry already has that verdict' }, 409);
        const system = state.systems.find(item => item.id === entry.system_id && !item.deleted);
        const problems = [
          ...(['measured', 'owner_call'].includes(entry.evidence) ? [] : ["Set the evidence to Measured or Owner's call"]),
          ...(entry.source.trim() ? [] : ['Name the source of the evidence']),
          ...(system ? [] : ['Assign an active system']),
        ];
        if (problems.length) return send({ error: `Not promoted: ${problems.join('; ')}` }, 409);
        putEntry({ ...next, status: body.verdict, verdict: {
          memory_id: `${id}-verdict-${next.revision}`, label: body.verdict === 'validated' ? 'works' : 'does_not_work',
          promoted_at: fixtureNow(), promoted_by: null,
        } });
      } else if (move === 'demote') {
        if (entry.status === body.status) return send({ error: `This entry is already ${body.status}` }, 409);
        putEntry({ ...next, status: body.status, verdict: null, retries: entry.retries + (body.status === 'testing' ? 1 : 0) });
      } else {
        if (!live) return send({ error: "Only a verdict's use is recorded" }, 409);
        const use = { used_in: body.used_in.trim(), at: fixtureNow().slice(0, 10), by: null };
        putEntry({ ...next, note: '', uses: [...entry.uses, use], last_used: use.at });
      }
      return send(state.labEntries.find(item => item.id === id));
    }
    const areaVisibility = /\/items\/([^/]+)\/visibility$/.exec(path);
    const areaItem = areaVisibility && (state.systems.find(item => item.id === areaVisibility[1]) ?? state.labEntries.find(item => item.id === areaVisibility[1]));
    if (areaVisibility && areaItem && method === 'POST') {
      state.systemCommands.push({ ...body, id: areaItem.id, move: 'visibility' });
      if (areaItem.revision !== body.expected_revision)
        return send({ error: 'This workspace item changed. Refresh before saving.' }, 409);
      if (areaItem.kind === 'system' && body.deleted && state.labEntries.some(entry => !entry.deleted && entry.system_id === areaItem.id && entry.verdict))
        return send({ error: "Move this system's verdicts back to Testing or retire them first" }, 409);
      if (areaItem.kind === 'lab_entry' && body.deleted && areaItem.verdict)
        return send({ error: 'Move this verdict back to Testing or retire it first' }, 409);
      const saved = { ...areaItem, revision: areaItem.revision + 1, deleted: body.deleted, updated_at: fixtureNow() };
      if (saved.kind === 'system') state.systems = state.systems.map(item => item.id === saved.id ? saved as SystemRow : item);
      else putEntry(saved as LabEntryRow);
      return send(saved);
    }
    // Folders and sections archive or restore through visibility, like a project.
    const containerVisibility = /\/items\/([^/]+)\/visibility$/.exec(path);
    const container = containerVisibility && (state.folders.find(entry => entry.id === containerVisibility[1]) ?? state.sections.find(entry => entry.id === containerVisibility[1]));
    if (containerVisibility && container && method === 'POST') {
      state.itemCommands.push({ ...body, id: container.id });
      if (body.expected_revision !== container.revision)
        return send({ error: 'This workspace item changed. Refresh before saving.' }, 409);
      const saved = { ...container, revision: container.revision + 1, deleted: body.deleted };
      if (saved.kind === 'folder') state.folders = state.folders.map(entry => entry.id === saved.id ? saved as FolderRow : entry);
      else state.sections = state.sections.map(entry => entry.id === saved.id ? saved as SectionRow : entry);
      return send(saved);
    }
    const listOf = (kind: unknown) => kind === 'project' ? 'projects' : kind === 'database' ? 'databases' : null;
    const item = /\/items\/([^/]+)(\/visibility)?$/.exec(path);
    if (item) {
      const key = (['projects', 'databases'] as const).find(name => state[name].some(entry => entry.id === item[1]));
      const current = key && state[key].find(entry => entry.id === item[1]);
      if (!key || !current) return send({ error: 'Workspace item not found' }, 404);
      if (method === 'GET' && !item[2]) {
        // Only this read returns the rows of a database; a listing carries a count.
        if (key === 'databases') state.databaseReads.push(item[1]);
        return send(current);
      }
      if (method !== 'POST' || !item[2]) { state.unexpected.push(`${method} ${path}`); return send({ error: 'Unexpected item request' }, 500); }
      if (current.revision !== body.expected_revision) return send({ error: 'This workspace item changed. Refresh before saving.' }, 409);
      const saved = { ...current, deleted: body.deleted, revision: current.revision + 1 };
      state[key] = state[key].map(entry => entry.id === saved.id ? saved : entry);
      return send(saved);
    }
    // Folders and sections: saves with a revision check, then archive through visibility below.
    if (path.endsWith('/items') && method === 'POST' && (body?.kind === 'folder' || body?.kind === 'section')) {
      state.itemCommands.push(body);
      const list: (FolderRow | SectionRow)[] = body.kind === 'folder' ? state.folders : state.sections;
      const current = list.find(entry => entry.id === body.item_id);
      if ((current?.revision ?? 0) !== body.expected_revision)
        return send({ error: 'This workspace item changed. Refresh before saving.' }, 409);
      const saved = { ...(current ?? {}), ...body.data, id: body.item_id, kind: body.kind, revision: body.expected_revision + 1, deleted: false };
      if (body.kind === 'folder') state.folders = [...state.folders.filter(entry => entry.id !== saved.id), saved as FolderRow];
      else state.sections = [...state.sections.filter(entry => entry.id !== saved.id), saved as SectionRow];
      return send(saved);
    }
    if (path.endsWith('/items') && method === 'POST') {
      state.itemCommands.push(body);
      const key = listOf(body.kind);
      if (!key) { state.unexpected.push(`${method} ${path} ${body.kind}`); return send({ error: 'Unexpected item kind' }, 500); }
      const current = state[key].find(entry => entry.id === body.item_id);
      if ((current?.revision ?? 0) !== body.expected_revision) return send({ error: 'This workspace item changed. Refresh before saving.' }, 409);
      // A database save that omits records keeps the stored rows, as the Space does.
      const { records: sentRecords, ...data } = (body.data ?? {}) as { records?: DatabaseRow['records'] } & Record<string, unknown>;
      const saved: Item = {
        ...data, id: body.item_id, kind: body.kind, revision: body.expected_revision + 1, deleted: false,
        updated_at: '2026-09-23T00:00:00Z', author_agent: null,
        ...(body.kind === 'database' ? { records: sentRecords ?? recordsOf(current ?? { id: '', kind: 'database', revision: 0 }) } : {}),
      };
      state[key] = current ? state[key].map(entry => entry.id === saved.id ? saved : entry) : [...state[key], saved];
      return send(saved);
    }
    if (path.endsWith('/items')) {
      const kind = url.searchParams.get('kind');
      const deleted = url.searchParams.get('deleted') === 'true';
      if (kind === 'folder') return send({ items: state.folders.filter(entry => !!entry.deleted === deleted), next_cursor: null });
      if (kind === 'section') return send({ items: state.sections.filter(entry => !!entry.deleted === deleted), next_cursor: null });
      const key = listOf(kind);
      const listed = key ? state[key].filter(entry => !!entry.deleted === deleted) : [];
      // A database listing carries the schema and a record count, never the rows.
      return send({ items: key === 'databases' ? listed.map(listedDatabase) : listed, next_cursor: null });
    }
    const change = /\/databases\/([^/]+)\/changes$/.exec(path);
    if (change && method === 'POST') {
      state.databaseCommands.push(body);
      expect(body.command_id).toBeTruthy();
      const database = state.databases.find(entry => entry.id === change[1]);
      if (!database || database.revision !== body.expected_revision)
        return send({ error: 'This workspace item changed. Refresh before saving.' }, 409);
      const rows = recordsOf(database);
      const upsert = (body.upsert ?? []) as DatabaseRow['records'];
      const removed = new Set<string>(body.delete ?? []);
      const records = [
        ...rows.filter(record => !removed.has(record.id)).map(record => upsert.find(row => row.id === record.id) ?? record),
        ...upsert.filter(row => !rows.some(record => record.id === row.id)),
      ];
      const saved: Item = { ...database, ...(body.data ?? {}), revision: database.revision + 1, records };
      state.databases = state.databases.map(entry => entry.id === saved.id ? saved : entry);
      const counts = { inserted: upsert.filter(row => !rows.some(record => record.id === row.id)).length,
        updated: upsert.filter(row => rows.some(record => record.id === row.id)).length, deleted: removed.size };
      // A replay answers with no rows; the fixture never replays a command.
      return send({ database: listedDatabase(saved), changes: counts, replayed: false, records: upsert, deleted: [...removed] });
    }
    if (path.endsWith('/webhooks')) {
      if (method === 'POST') {
        state.webhookCommands.push(body);
        const subscription: WebhookRow = {
          id: `${String(state.webhooks.length + 1).padStart(8, '0')}-5555-4555-8555-555555555555`, space_id: spaceId,
          database_id: body.database_id, delivery: body.delivery ?? 'endpoint', endpoint_url: body.endpoint_url ?? null,
          transition: body.transition ?? null, paused: false, revision: 1, created_at: '2026-10-14T09:00:00Z', updated_at: '2026-10-14T09:00:00Z', removed_at: null,
        };
        state.webhooks = [...state.webhooks, subscription];
        return send(subscription.delivery === 'endpoint' ? { subscription, signing_secret: 'synthetic-fixture-secret-not-a-credential' } : { subscription });
      }
      return send({ items: state.webhooks });
    }
    if (/\/webhooks\/[^/]+\/deliveries$/.test(path)) return send({ items: state.webhookDeliveries });
    const webhook = /\/webhooks\/([^/]+)$/.exec(path);
    if (webhook && method === 'POST') {
      state.webhookCommands.push({ ...body, id: webhook[1] });
      const current = state.webhooks.find(item => item.id === webhook[1])!;
      const subscription: WebhookRow = {
        ...current, revision: current.revision + 1,
        paused: body.action === 'pause' ? true : body.action === 'resume' ? false : current.paused,
        removed_at: body.action === 'remove' ? '2026-10-14T10:00:00Z' : current.removed_at,
      };
      state.webhooks = state.webhooks.map(item => item.id === subscription.id ? subscription : item);
      return send(body.action === 'rotate' ? { subscription, signing_secret: 'synthetic-rotated-secret-not-a-credential' } : { subscription });
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
    if (path.endsWith('/memories')) return send({ items: [], next_cursor: null, provider: state.memoryProvider });
    if (path.endsWith('/memory/provider')) return send(state.memoryProvider);
    if (path === 'agent-identities' && method === 'POST') {
      const agent = { id: createdAgentId, name: body.name, harness: body.harness, revision: 1 };
      state.agents = [...state.agents.filter(item => item.id !== agent.id), agent];
      return send(agent);
    }
    if (path === 'agent-identities') {
      await pass('agents');
      return send({ items: state.agents });
    }
    if (path === 'agents') {
      await pass('agents');
      return send({ items: state.connections });
    }
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
    if (path.endsWith('/memory/jobs')) return send({ items: state.memoryJobs });
    state.unexpected.push(`${method} ${path}`);
    return send({ error: `Unexpected fixture request: ${path}` }, 500);
  });
  return state;
}

export type SpaceMock = Awaited<ReturnType<typeof mockSpace>>;

export type WebhookRow = {
  id: string; space_id: string; database_id: string; delivery: 'endpoint' | 'inbox'; endpoint_url: string | null;
  transition: { field_id: string; to: string } | null; paused: boolean; revision: number;
  created_at: string; updated_at: string; removed_at: string | null;
};

export const systemId = '99999999-9999-4999-8999-999999999999';
export const labEntryId = 'abababab-abab-4bab-8bab-abababababab';
export const LAB_DOMAINS = ['Copy & hooks', 'Social', 'Outreach & sales', 'SEO/GEO', 'Product & positioning',
  'Dev & engineering', 'Agents & automation', 'Video', 'Ops & business'];

export type SystemRow = {
  id: string; kind: 'system'; revision: number; deleted: boolean; updated_at: string; author_agent: null;
  name: string; description: string; status: string; goal_metric: string; goal_target: string; current_value: string;
  counter_metric: string; source_of_truth: string; review_cadence: string | null; last_review: string | null;
  next_decision: string; playbook_id: string | null;
};
export type LabEntryRow = {
  id: string; kind: 'lab_entry'; revision: number; deleted: boolean; updated_at: string; author_agent: null;
  name: string; system_id: string; domain: string; status: string; evidence: string; why: string; apply: string;
  applies_to: string; source: string; captured: string | null; note: string;
  uses: { used_in: string; at: string; by: string | null }[]; last_used: string | null;
  verdict: { memory_id: string; label: string; promoted_at: string; promoted_by: string | null } | null; retries: number;
};

/** A moment that moves forward with each call, so newer saves sort first. */
let fixtureClock = Date.parse('2026-10-20T09:00:00Z');
function fixtureNow() {
  fixtureClock += 60_000;
  return new Date(Math.max(fixtureClock, Date.now())).toISOString();
}

function blankSystem(): Omit<SystemRow, 'id' | 'revision' | 'updated_at'> {
  return { kind: 'system', deleted: false, author_agent: null, name: '', description: '', status: 'not_measured', goal_metric: '', goal_target: '',
    current_value: '', counter_metric: '', source_of_truth: '', review_cadence: null, last_review: null, next_decision: '', playbook_id: null };
}

function blankEntry(): Omit<LabEntryRow, 'id' | 'revision' | 'updated_at' | 'name' | 'system_id' | 'domain'> {
  return { kind: 'lab_entry', deleted: false, author_agent: null, status: 'testing', evidence: 'untested', why: '', apply: '', applies_to: '',
    source: '', captured: null, note: '', uses: [], last_used: null, verdict: null, retries: 0 };
}

/** A system's lab counted the way the backend counts it. */
export function labStats(entries: LabEntryRow[]) {
  const count = (status: string) => entries.filter(entry => entry.status === status).length;
  const verdicts = count('validated') + count('invalidated');
  const reused = entries.filter(entry => entry.verdict && entry.uses.some(use => {
    const days = (Date.parse(use.at) - Date.parse(entry.verdict!.promoted_at.slice(0, 10))) / 86400000;
    return days >= 0 && days <= 30;
  })).length;
  return { entries: entries.length, testing: count('testing'), works: count('validated'), does_not_work: count('invalidated'),
    retired: count('retired'), verdicts, reused_30d: reused, reuse_share: verdicts ? Math.round(reused / verdicts * 10000) / 10000 : null };
}

/** One system with a measured hypothesis ready to promote, its first revision in the history. */
export function sampleSystem(space: SpaceMock) {
  const system: SystemRow = {
    ...blankSystem(), id: systemId, revision: 1, updated_at: '2026-10-19T09:00:00Z', name: 'Cold email', status: 'needs_attention',
    description: 'Outbound to product teams.', goal_metric: 'Reply rate', goal_target: '8%', current_value: '5.1%',
    counter_metric: 'Unsubscribe rate', source_of_truth: 'Campaign report', review_cadence: 'weekly', last_review: '2026-10-01',
  };
  const entry: LabEntryRow = {
    ...blankEntry(), id: labEntryId, revision: 1, updated_at: '2026-10-19T10:00:00Z', system_id: systemId, domain: 'Outreach & sales',
    name: "A first line naming the prospect's last release gets more replies.", evidence: 'measured', source: 'Campaign report, cohort 5',
    why: 'Cohort 5 replied at 7.9% with the release line, 4.2% without.',
  };
  space.systems = [system];
  space.labEntries = [entry];
  space.labHistory = { [entry.id]: [{ ...entry, author: 'you' }] };
  return { system, entry };
}

export const databaseId = '66666666-6666-4666-8666-666666666666';

export type DatabaseRow = {
  id: string; kind: 'database'; name: string; description: string; revision: number; deleted: boolean;
  fields: { id: string; name: string; type: string; options?: string[] }[];
  records: { id: string; name: string; notes: string; values: Record<string, unknown>; createdAt?: string | null; updatedAt: string }[];
  view: 'table' | 'board'; groupBy: string | null; tasks?: { status: string; done: string[]; planned: string } | null;
};

/** The rows a stored item holds; an item saved without records keeps the rows it had. */
function recordsOf(item: Item): DatabaseRow['records'] {
  return (item as { records?: DatabaseRow['records'] }).records ?? [];
}

/** A database as the Space lists it: the schema and a record count, never the rows. */
export function listedDatabase(item: Item): Item {
  const { records: _records, ...listed } = item as Item & { records?: DatabaseRow['records'] };
  return { ...listed, record_count: recordsOf(item).length };
}

/** A pipeline database with one select field, so a webhook can filter on Stage → Review. */
export function sampleDatabase(): DatabaseRow {
  return {
    id: databaseId, kind: 'database', name: 'Pipeline', description: '', revision: 1, deleted: false,
    fields: [
      { id: 'stage', name: 'Stage', type: 'select', options: ['New', 'Review', 'Won'] },
      { id: 'notes', name: 'Notes', type: 'text' },
    ], records: [], view: 'table', groupBy: 'stage',
  };
}

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

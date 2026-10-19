import { usePathname } from "@web/lib/navigation";
import { QueryClientProvider } from "@tanstack/react-query";
import { teamsAvailable } from "../../../lib/features";
import { workspaceReadPlan, workspaceReadPath, type WorkspaceRead } from "../../../lib/space/loading";

import { deleteAgentWithConnections } from "../../../lib/space/agent-deletion";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  api, ApiError, message, cachedApi, watchApi, configureReadCache,
  resetReadCache, invalidateReads, ACCESS_RESET_EVENT,
  getReadQueryClient,
} from "../../../lib/client";
import { normalizeMemories } from "../../../lib/space/memory-model";
import { agentTokenRequest, type AgentToken } from "../../../lib/space/agent-tokens";
import { resourceSaveCommand, type ResourceDraft } from "../../../lib/space/resource-model";
import type { Profile } from "../../../lib/space/profile";

/** How this installation guards access: an access key, or nothing on a private network. */
export type AccessMode = "key" | "private";
import { statuses, type ResourceView } from "../../../lib/space/types";
import { mergeSeen, seenRevisions } from "../../../lib/space/attention";
import { mergeTaskSummary, optimisticTask, settleLimited, taskColumnLabel, taskColumnsWithResources } from "../../../lib/space/task-model";
import {
  invitationToken,
  pendingInvitationKey,
  selectedSpaceKey,
  selectWorkspace,
} from "../../../lib/space/teams";
import {
  isWorkspaceDatabase,
  type WorkspaceDatabase,
} from "../../../lib/space/databases";
import type {
  AgentIdentity,
  DocumentFolder,
  Grant,
  MemoryResponse,
  MemoryAssignment,
  OrganizationItem,
  MemoryVector,
  Project,
  Provider,
  Resource,
  Status,
  TaskColumns,
  Space,
} from "../../../lib/space/types";

// Keep initial requests small; the API supports larger explicit pages when needed.
const MEMORY_PAGE_SIZE = 50;
type ItemKind = "project" | "database" | "category" | "section" | "folder";
type WorkspaceItem = (Project | WorkspaceDatabase | OrganizationItem | DocumentFolder) & { kind: ItemKind };
type MemoryPage = { items: MemoryResponse[]; next_cursor: string | null; provider: Provider };

type MemoryJob = {
  id: string;
  status: "queued" | "processing" | "ready" | "failed";
  attempts: number;
  error_code: string | null;
  result: { resource_id: string; revision: number } | null;
};

function useData() {
  const pathname = usePathname();
  const plan = workspaceReadPlan(pathname);
  const requiredReads = plan.required.join(",");
  const supportingReads = plan.supporting.join(",");
  const [loadedReads, setLoadedReads] = useState<Partial<Record<WorkspaceRead, boolean>>>({});
  const [readErrors, setReadErrors] = useState<Partial<Record<WorkspaceRead, string>>>({});
  const [dismissedReadError, setDismissedReadError] = useState("");
  // The owner's identity once the workspace opens, null while this browser is locked, and
  // undefined while that is not known yet.
  const [accountId, setAccountId] = useState<string | null | undefined>(undefined);
  const [accessMode, setAccessMode] = useState<AccessMode | null>(null);
  const [profile, setProfile] = useState<Profile>({ displayName: "", avatarUrl: null });
  const [space, setSpace] = useState<Space | null>(null);
  const [workspaces, setWorkspaces] = useState<Space[]>([]);
  const [resources, setResources] = useState<Resource[]>([]);
  // Which revision of each document or task the owner last opened.
  const [views, setViews] = useState<Record<string, number>>({});
  const latestViews = useRef(views);
  latestViews.current = views;
  const [viewsReady, setViewsReady] = useState(false);
  const [taskColumnState, setTaskColumnState] = useState<TaskColumns>({ columns: statuses, revision: 1 });
  const [taskColumnsReady, setTaskColumnsReady] = useState(false);
  const [taskColumnsError, setTaskColumnsError] = useState("");
  const [pendingTaskStatuses, setPendingTaskStatuses] = useState<Record<string, Status>>({});
  const taskStatusSaving = useRef(new Set<string>());
  // The latest status asked for while a task's earlier status is still saving.
  const taskStatusRequests = useRef(new Map<string, Status>());
  const newColumnValues = useRef(new Map<string, string>());
  const [projects, setProjects] = useState<Project[]>([]);
  const [folders, setFolders] = useState<DocumentFolder[]>([]);
  const [organizationItems, setOrganizationItems] = useState<OrganizationItem[]>([]);
  const [memories, setMemories] = useState<MemoryVector[]>([]);
  const [memoryCursor, setMemoryCursor] = useState<string | null>(null);
  const [memoryJobs, setMemoryJobs] = useState<MemoryJob[]>([]);
  const [jobError, setJobError] = useState("");
  const [jobPollVersion, setJobPollVersion] = useState(0);
  const knownJobs = useRef(new Map<string, string>());
  const [grants, setGrants] = useState<Grant[]>([]);
  const [agents, setAgents] = useState<AgentIdentity[]>([]);
  const [databases, setDatabases] = useState<WorkspaceDatabase[]>([]);
  const [trashedItems, setTrashedItems] = useState<
    ((Project | WorkspaceDatabase) & { kind: "project" | "database" })[]
  >([]);
  const [provider, setProvider] = useState<Provider | null>(null);
  const [cursors, setCursors] = useState<Record<string, string | null>>({});
  const [loading, setLoading] = useState(true);
  const [memoryLoading, setMemoryLoading] = useState(false);
  const [memoryMoreLoading, setMemoryMoreLoading] = useState(false);
  const [memoryLoaded, setMemoryLoaded] = useState(false);
  const [error, setError] = useState("");
  const [memoryError, setMemoryError] = useState("");
  const [notice, setNotice] = useState("");
  const [compact, setCompact] = useState(false);
  const generation = useRef(0);
  const pending = useRef(new Map<string, string>());
  const memoryRequests = useRef(0);
  const memoryMoreBusy = useRef(false);
  const latestResources = useRef(resources);
  latestResources.current = resources;
  const memoryHasLoaded = useRef(memoryLoaded);
  memoryHasLoaded.current = memoryLoaded;

  useEffect(() => {
    try {
      const invitation = invitationToken(window.location.href);
      if (invitation) {
        sessionStorage.setItem(pendingInvitationKey, invitation);
        window.history.replaceState(
          null,
          "",
          window.location.pathname + window.location.search,
        );
      }
      setCompact(localStorage.getItem("space-density") === "compact");
    } catch {
      /* An unavailable browser store must not block the workspace. */
    }
  }, []);

  const applyItems = useCallback((kind: ItemKind, deleted: boolean, items: WorkspaceItem[]) => {
    if (kind === "folder") {
      if (!deleted) setFolders(items as DocumentFolder[]);
    } else if (kind === "project" && !deleted) setProjects(items as Project[]);
    else if (kind === "database" && !deleted) setDatabases(items as WorkspaceDatabase[]);
    else if (kind === "category" || kind === "section")
      setOrganizationItems(old => [...old.filter(i => i.kind !== kind || !!i.deleted !== deleted), ...items as OrganizationItem[]]);
    else setTrashedItems(old => [...old.filter(i => i.kind !== kind), ...items as typeof old]);
  }, []);

  const refreshItems = useCallback(async (spaceId: string, fresh = true) => {
    const current = generation.current;
    await Promise.all((["project", "database", "category", "section", "folder"] as const).map(async kind => {
      const page = await cachedApi<{ items: WorkspaceItem[] }>(
        `spaces/${spaceId}/items?kind=${kind}&deleted=false`, { allPages: true, fresh },
      );
      if (current === generation.current) applyItems(kind, false, page.items);
    }));
  }, [applyItems]);

  const watchItems = useCallback((kind: ItemKind, deleted = false, onLoaded?: () => void, onError?: (error: unknown) => void) => {
    if (!space) { onLoaded?.(); return () => {}; }
    const current = generation.current;
    return watchApi<{ items: WorkspaceItem[] }>(`spaces/${space.id}/items?kind=${kind}&deleted=${deleted}`, page => {
      if (current !== generation.current) return;
      applyItems(kind, deleted, page.items);
      onLoaded?.();
    }, e => { if (current === generation.current) (onError ?? (e => setError(message(e))))(e); }, true);
  }, [space, applyItems]);

  const watchAgents = useCallback(() => {
    if (!space) return () => {};
    const current = generation.current;
    const failed = (e: unknown) => { if (current === generation.current) setError(message(e)); };
    const stops = [
      watchApi<{ items: Grant[] }>(`agents?workspace_id=${space.id}`, page => {
        if (current === generation.current) setGrants(page.items);
      }, failed),
      watchApi<{ items: AgentIdentity[] }>(`agent-identities?workspace_id=${space.id}`, page => {
        if (current === generation.current) setAgents(page.items);
      }, failed),
    ];
    return () => stops.forEach(stop => stop());
  }, [space]);

  const refreshAgents = useCallback(async () => {
    if (!space) throw new Error("Open your Space to refresh agents.");
    const current = generation.current;
    const [connections, identities] = await Promise.all([
      cachedApi<{ items: Grant[] }>(`agents?workspace_id=${space.id}`, { fresh: true }),
      cachedApi<{ items: AgentIdentity[] }>(`agent-identities?workspace_id=${space.id}`, { fresh: true }),
    ]);
    if (current !== generation.current) throw new Error("Your workspace changed. Refresh connections again.");
    setGrants(connections.items);
    setAgents(identities.items);
    return connections.items;
  }, [space]);

  const initialize = useCallback(async () => {
    resetReadCache(0, "", false, true);
    const current = ++generation.current;
    setLoading(true);
    setAccountId(undefined);
    setError("");
    try {
      // The website's server adds the owner's identity and how access is guarded.
      const {
        user,
        profile: accountProfile,
        browser_cache_key: browserCacheKey,
        read_version_supported: readVersionSupported,
        access_mode: mode,
        ...value
      } = await api<Space & { user: { id: string }; profile?: Profile; browser_cache_key?: string; read_version_supported?: boolean; access_mode?: AccessMode }>(
        "workspace",
        "POST",
      );
      if (current !== generation.current) return;
      setAccountId(user.id);
      setAccessMode(mode === "private" ? "private" : "key");
      setProfile(accountProfile ?? { displayName: "", avatarUrl: null });
      const available = await api<{ items: Space[] }>("workspaces");
      if (current !== generation.current) return;
      await configureReadCache(user.id, browserCacheKey, [...available.items.map(item => item.id), value.id], readVersionSupported === true);
      if (current !== generation.current) return;
      setWorkspaces(available.items);
      let requested = new URLSearchParams(window.location.search).get(
        "workspace",
      );
      try {
        requested ??= sessionStorage.getItem(selectedSpaceKey);
      } catch {}
      // Without teams there is no switcher, so a remembered team Space would strand the
      // person there; always open the personal Space instead.
      const selected = selectWorkspace(available.items, teamsAvailable ? requested : null, value);
      try {
        sessionStorage.setItem(selectedSpaceKey, selected.id);
      } catch {}
      setSpace(selected);
    } catch (e) {
      if (current !== generation.current) return;
      if (e instanceof ApiError && e.status === 401) setAccountId(null);
      else setError(message(e));
    } finally {
      if (current === generation.current) setLoading(false);
    }
  }, []);
  useEffect(() => {
    void initialize();
    return () => {
      generation.current++;
      memoryRequests.current++;
    };
  }, [initialize]);

  useEffect(() => {
    const reset = (event: Event) => {
      const { status, error } = (event as CustomEvent<{ status: number; error: string }>).detail;
      clearData();
      if (status !== 401 && error) { setAccountId(undefined); setError(error); }
    };
    window.addEventListener(ACCESS_RESET_EVENT, reset);
    return () => window.removeEventListener(ACCESS_RESET_EVENT, reset);
    // The reset only uses state setters and refs; it must not resubscribe on reads.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!space || loading) return;
    const current = generation.current;
    let disposed = false;
    const stops: (() => void)[] = [];
    const kinds = (value: string) => value ? value.split(",") as WorkspaceRead[] : [];
    const pendingRequired = new Set(kinds(requiredReads));
    let timer: ReturnType<typeof setTimeout> | undefined;
    const startSupporting = () => {
      if (pendingRequired.size || timer !== undefined) return;
      // Mounted views get to start detail, memory, overview and agent reads first.
      timer = setTimeout(() => kinds(supportingReads).forEach(subscribe), 0);
    };
    const subscribe = (kind: WorkspaceRead) => {
      const loaded = () => {
        if (disposed || current !== generation.current) return;
        setLoadedReads(old => old[kind] ? old : { ...old, [kind]: true });
        setReadErrors(old => old[kind] ? { ...old, [kind]: undefined } : old);
        pendingRequired.delete(kind);
        startSupporting();
      };
      const failed = (error: unknown) => {
        if (disposed || current !== generation.current) return;
        setReadErrors(old => ({ ...old, [kind]: message(error) }));
      };
      if (kind === "document" || kind === "task") {
        stops.push(watchApi<{ items: Resource[]; next_cursor: string | null }>(workspaceReadPath(space.id, kind), page => {
          if (disposed || current !== generation.current) return;
          setResources(old => {
            const previous = new Map(old.map(resource => [resource.id, resource]));
            const items = kind === "task"
              ? page.items.map(task => mergeTaskSummary(task, previous.get(task.id)))
              : page.items;
            return [...old.filter(r => r.kind !== kind), ...items];
          });
          setCursors(old => ({ ...old, [kind]: page.next_cursor }));
          loaded();
        }, failed, kind === "document"));
      } else stops.push(watchItems(kind, false, loaded, failed));
    };
    kinds(requiredReads).forEach(subscribe);
    startSupporting();
    return () => {
      disposed = true;
      clearTimeout(timer);
      stops.forEach(stop => stop());
    };
  }, [space, loading, watchItems, requiredReads, supportingReads]);

  const pageError = plan.required.filter(kind => !loadedReads[kind]).map(kind => readErrors[kind]).find(Boolean);
  const pageLoading = plan.required.some(kind => !loadedReads[kind] && !readErrors[kind]);
  const backgroundError = [...plan.required, ...plan.supporting].map(kind => readErrors[kind]).find(Boolean);
  useEffect(() => setDismissedReadError(""), [backgroundError]);
  function retryReads(kinds: WorkspaceRead[]) {
    if (!space) return;
    for (const kind of kinds) {
      if (!readErrors[kind]) continue;
      setReadErrors(old => ({ ...old, [kind]: undefined }));
      invalidateReads(workspaceReadPath(space.id, kind));
    }
  }

  // Memory may arrive before the sidebar's source summaries.
  useEffect(() => {
    const titles = new Map(resources.map(resource => [resource.id, resource.title]));
    setMemories(old => {
      let changed = false;
      const next = old.map(memory => {
        const title = memory.sourceId ? titles.get(memory.sourceId) : undefined;
        if (title === undefined || title === memory.sourceTitle) return memory;
        changed = true;
        return { ...memory, sourceTitle: title };
      });
      return changed ? next : old;
    });
  }, [resources]);

  // Parameters: the Space, an optional search query, and an optional `after` cursor.
  // What it does: reads one page of memory from the server. A query matches every
  //   memory in the Space and returns its best matches without a cursor; a page without
  //   a query walks the Space newest first and reports the cursor for the next page.
  // Output: {items, next_cursor, provider}.
  const memoryPage = useCallback(
    async (
      spaceId: string,
      search: { query?: string; after?: string | null } = {},
    ) => {
      const params = new URLSearchParams({ limit: String(MEMORY_PAGE_SIZE) });
      if (search.query) params.set("query", search.query);
      if (search.after) params.set("after", search.after);
      const result = await cachedApi<MemoryPage>(`spaces/${spaceId}/memories?${params}`);
      setProvider(result.provider);
      return result;
    },
    [],
  );

  const loadMemories = useCallback(async () => {
    if (!space) return;
    const current = ++memoryRequests.current;
    setMemoryLoading(true);
    setMemoryError("");
    try {
      const result = await cachedApi<MemoryPage>(`spaces/${space.id}/memories?limit=${MEMORY_PAGE_SIZE}`, { fresh: true });
      if (current !== memoryRequests.current) return;
      setMemories(normalizeMemories(result.items, resources));
      setProvider(result.provider);
      setMemoryCursor(result.next_cursor);
      setMemoryLoaded(true);
    } catch (e) {
      if (current === memoryRequests.current) setMemoryError(message(e));
    } finally {
      if (current === memoryRequests.current) setMemoryLoading(false);
    }
  }, [space, resources]);

  const watchMemories = useCallback(() => {
    if (!space) return () => {};
    const current = generation.current;
    setMemoryLoading(!memoryHasLoaded.current);
    return watchApi<MemoryPage>(`spaces/${space.id}/memories?limit=${MEMORY_PAGE_SIZE}`, page => {
      if (current !== generation.current) return;
      memoryRequests.current++;
      setMemories(normalizeMemories(page.items, latestResources.current));
      setMemoryCursor(page.next_cursor);
      setProvider(page.provider);
      setMemoryLoaded(true);
      setMemoryLoading(false);
      setMemoryError("");
    }, e => {
      if (current !== generation.current) return;
      setMemoryLoading(false);
      setMemoryError(message(e));
    });
  }, [space]);

  useEffect(() => {
    if (!space) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const current = generation.current;
    async function poll() {
      if (document.visibilityState !== "visible") {
        timer = setTimeout(() => void poll(), 30000);
        return;
      }
      try {
        const value = await api<{ items: MemoryJob[] }>(
          `spaces/${space!.id}/memory/jobs`,
        );
        if (disposed || current !== generation.current) return;
        const becameReady = value.items.some(
          (job) =>
            job.status === "ready" && knownJobs.current.get(job.id) !== "ready",
        );
        value.items.forEach((job) => knownJobs.current.set(job.id, job.status));
        setMemoryJobs(value.items);
        setJobError("");
        if (becameReady) invalidateReads(`spaces/${space!.id}/memories`);
        if (!disposed)
          timer = setTimeout(
            () => void poll(),
            value.items.some((job) =>
              ["queued", "processing"].includes(job.status),
            )
              ? 3000
              : 30000,
          );
      } catch {
        if (!disposed && current === generation.current) {
          setJobError("Could not refresh extraction status.");
          timer = setTimeout(() => void poll(), 10000);
        }
      }
    }
    timer = setTimeout(() => void poll(), jobPollVersion ? 0 : 5000);
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [space, jobPollVersion]);

  // Parameters: none. It continues from the loaded cursor.
  // What it does: appends the next newest-first page and keeps the cursor that follows
  //   it. A full reload started while this page was in flight owns the list instead.
  // Output: nothing. Failures reach the view as memoryError.
  const loadMoreMemories = useCallback(async () => {
    if (!space || !memoryCursor || memoryMoreBusy.current) return;
    const current = memoryRequests.current;
    memoryMoreBusy.current = true;
    setMemoryMoreLoading(true);
    setMemoryError("");
    try {
      const result = await memoryPage(space.id, { after: memoryCursor });
      if (current !== memoryRequests.current) return;
      const page = normalizeMemories(result.items, resources);
      setMemories((old) => [
        ...old,
        ...page.filter((m) => !old.some((o) => o.id === m.id)),
      ]);
      setMemoryCursor(result.next_cursor);
      setMemoryLoaded(true);
    } catch (e) {
      if (current === memoryRequests.current) setMemoryError(message(e));
    } finally {
      memoryMoreBusy.current = false;
      setMemoryMoreLoading(false);
    }
  }, [space, memoryCursor, resources, memoryPage]);

  // Parameters: the search text. It must not be reduced to the pages already loaded.
  // What it does: asks the server to match every memory in the Space. The caller
  //   debounces the term and drops responses that a newer term has replaced.
  // Output: the matching memories, ranked by the server.
  const searchMemories = useCallback(
    async (query: string) => {
      if (!space) return [];
      const result = await memoryPage(space.id, { query });
      return normalizeMemories(result.items, resources);
    },
    [space, resources, memoryPage],
  );

  async function loadMore(kind: "document" | "task") {
    if (!space || !cursors[kind]) return;
    const current = generation.current;
    try {
      const result = await api<{
        items: Resource[];
        next_cursor: string | null;
      }>(
        `${workspaceReadPath(space.id, kind)}&after=${encodeURIComponent(cursors[kind]!)}`,
      );
      if (current !== generation.current) return;
      setResources((old) => [
        ...old,
        ...result.items.filter((r) => !old.some((o) => o.id === r.id)),
      ]);
      setCursors((old) => ({ ...old, [kind]: result.next_cursor }));
    } catch (e) {
      if (current === generation.current) setError(message(e));
    }
  }
  async function command<T>(path: string, change: object) {
    if (space?.role === "viewer")
      throw new Error("You have view-only access to this Space.");
    const signature = JSON.stringify({ path, change });
    if (!pending.current.has(signature))
      pending.current.set(signature, crypto.randomUUID());
    const value = await api<T>(path, "POST", {
      ...change,
      command_id: pending.current.get(signature),
    });
    pending.current.delete(signature);
    return value;
  }
  const watchViews = useCallback(() => {
    if (!space) return () => {};
    const current = generation.current;
    return watchApi<{ items: ResourceView[] }>(`spaces/${space.id}/views`, value => {
      if (current !== generation.current) return;
      setViews(seenRevisions(value.items));
      setViewsReady(true);
    }, () => {
      // Seen marks are a convenience; without them the home page simply shows no "new" list.
    });
  }, [space]);
  // Parameters: the documents or tasks the person just opened, at the revision they saw.
  // What it does: records them as seen, at once locally and then for every device.
  // Output: nothing; a failed save only shows the item as new again on the next visit.
  const markSeen = useCallback(async (items: { id: string; revision: number }[]) => {
    const pending = items.filter(item => (latestViews.current[item.id] ?? 0) < item.revision);
    if (!pending.length) return;
    latestViews.current = mergeSeen(latestViews.current, pending);
    setViews(old => mergeSeen(old, pending));
    if (!space) return;
    try {
      await api(`spaces/${space.id}/views`, "POST", {
        items: pending.map(item => ({ resource_id: item.id, revision: item.revision })),
      });
    } catch {
      // Marking seen never interrupts reading.
    }
  }, [space]);
  const watchTaskColumns = useCallback(() => {
    if (!space) return () => {};
    const current = generation.current;
    return watchApi<TaskColumns>(`spaces/${space.id}/tasks/columns`, value => {
      if (current !== generation.current) return;
      setTaskColumnState(value);
      setTaskColumnsReady(true);
      setTaskColumnsError("");
    }, error => {
      if (current === generation.current) setTaskColumnsError(message(error));
    });
  }, [space]);

  async function addTaskColumn(label: string) {
    if (!space) throw new Error("Open a space first.");
    if (space.role === "viewer") throw new Error("You have view-only access to this Space.");
    if (!taskColumnsReady) throw new Error("Wait for task columns to load, then try again.");
    const name = taskColumnLabel(label, taskColumnState.columns);
    if (taskColumnState.columns.length >= 30) throw new Error("A board can have up to 30 columns.");
    if (!newColumnValues.current.has(name)) newColumnValues.current.set(name, `column_${crypto.randomUUID()}`);
    const value = newColumnValues.current.get(name)!;
    const current = generation.current;
    const saved = await command<TaskColumns>(`spaces/${space.id}/tasks/columns`, {
      expected_revision: taskColumnState.revision, value, label: name,
    });
    if (current === generation.current) {
      setTaskColumnState(saved);
      newColumnValues.current.delete(name);
      setNotice("Task column added");
    }
    return saved;
  }

  function writableSpace() {
    if (!space) throw new Error("Open a space first.");
    if (space.role === "viewer") throw new Error("You have view-only access to this Space.");
    return space;
  }
  // Parameters: tasks and the status they should move to.
  // What it does: shows each move at once. A task that is already saving keeps only the
  // latest request, which its running save sends next instead of locking the task.
  // Output: the tasks whose save must be started now.
  function requestTaskStatus(tasks: Resource[], status: Status) {
    const moving = tasks.filter(task => task.kind === "task" && task.status !== status);
    const start = moving.filter(task => !taskStatusSaving.current.has(task.id));
    for (const task of moving) {
      taskStatusRequests.current.set(task.id, status);
      taskStatusSaving.current.add(task.id);
    }
    if (moving.length)
      setPendingTaskStatuses(old => ({ ...old, ...Object.fromEntries(moving.map(task => [task.id, status])) }));
    return start;
  }
  async function sendTaskStatus(task: Resource, status: Status) {
    if (!space) throw new Error("Open a space first.");
    return command<Resource>(`spaces/${space.id}/tasks/${encodeURIComponent(task.id)}/status`, {
      expected_revision: task.revision, status,
    });
  }
  // Parameters: a task whose status request is recorded and not yet sent.
  // What it does: sends requests one at a time from the latest saved revision until none remain.
  // Output: the last saved task; a failure drops later requests and restores the saved status.
  async function saveRequestedStatus(task: Resource) {
    const current = generation.current;
    let base = task, saved: Resource | undefined;
    try {
      for (;;) {
        const status = taskStatusRequests.current.get(task.id);
        taskStatusRequests.current.delete(task.id);
        if (status === undefined || status === base.status) return saved;
        const next = await sendTaskStatus(base, status);
        if (current !== generation.current) return;
        setResources(old => {
          const existing = old.find(resource => resource.id === next.id);
          if (existing && existing.revision > next.revision) return old;
          return existing ? old.map(resource => resource.id === next.id ? next : resource) : [next, ...old];
        });
        setMemories(old => old.map(memory => memory.sourceId === next.id ? {
          ...memory, stale: memory.sourceRevision !== next.revision,
        } : memory));
        base = saved = next;
      }
    } finally {
      if (current === generation.current) {
        taskStatusRequests.current.delete(task.id);
        taskStatusSaving.current.delete(task.id);
        setPendingTaskStatuses(old => {
          const next = { ...old };
          delete next[task.id];
          return next;
        });
      }
    }
  }
  async function changeTaskStatus(task: Resource, status: Status) {
    writableSpace();
    if (task.kind !== "task") throw new Error("Only tasks have board columns.");
    const [start] = requestTaskStatus([task], status);
    if (!start) return;
    const saved = await saveRequestedStatus(start);
    if (saved) setNotice("Task status saved");
    return saved;
  }
  // Parameters: selected tasks and one status.
  // What it does: moves every task on screen at once, then saves a few at a time.
  // Output: settled results for the tasks whose save started here, with those tasks.
  async function changeTaskStatuses(tasks: Resource[], status: Status) {
    writableSpace();
    const started = requestTaskStatus(tasks, status);
    const current = generation.current;
    const results = await settleLimited(started, 4, saveRequestedStatus);
    const moved = results.filter(result => result.status === "fulfilled" && result.value).length;
    if (moved && current === generation.current)
      setNotice(moved === 1 ? "Task status saved" : `${moved} tasks moved`);
    return started.map((task, index) => ({ task, result: results[index] }));
  }
  async function readResource(
    id: string,
    revision?: number,
    fresh = false,
  ): Promise<Resource> {
    if (!space) throw new Error("Open a space first.");
    return cachedApi<Resource>(
      `spaces/${space.id}/resources/${encodeURIComponent(id)}${revision ? `?revision=${revision}` : ""}`,
      { fresh },
    );
  }
  function watchResource(id: string, revision: number | undefined, onValue: (value: Resource) => void, onError: (error: unknown) => void) {
    if (!space) return () => {};
    return watchApi<Resource>(`spaces/${space.id}/resources/${encodeURIComponent(id)}${revision ? `?revision=${revision}` : ""}`, value => {
      onValue(value);
      // Reading the current revision on the website is what "seen" means.
      if (!revision) void markSeen([{ id: value.id, revision: value.revision }]);
    }, onError);
  }
  async function loadSelectedMemory(id: string) {
    if (!space) return;
    const current = generation.current;
    const response = await cachedApi<MemoryResponse>(
      `spaces/${space.id}/memories/${encodeURIComponent(id)}`,
    );
    if (current !== generation.current) return;
    const loaded = normalizeMemories([response], resources);
    setMemories((old) => [...old.filter((m) => m.memoryId !== id), ...loaded]);
  }
  async function saveResource(draft: ResourceDraft, previous?: Resource) {
    if (!space) throw new Error("Open a space first.");
    if (draft.kind === "task" && draft.folder)
      throw new Error("Only documents can be placed in folders.");
    const saved = await command<Resource>(`spaces/${space.id}/resources`, resourceSaveCommand(draft, previous));
    setResources((old) => [saved, ...old.filter((r) => r.id !== saved.id)]);
    setMemories((old) =>
      old.map((memory) =>
        memory.sourceId === saved.id
          ? {
              ...memory,
              sourceTitle: saved.title,
              stale: memory.sourceRevision !== saved.revision,
            }
          : memory,
      ),
    );
    setMemoryLoaded(false);
    setNotice("Saved successfully");
    return saved;
  }
  async function deleteResource(resource: Resource) {
    await removeResource(resource);
    setNotice(resource.kind === "task" ? "Task deleted" : "Document deleted");
  }
  // Parameters: selected tasks.
  // What it does: deletes a few at a time and keeps going when one fails.
  // Output: settled results in the same order as the tasks.
  async function deleteTasks(tasks: Resource[]) {
    writableSpace();
    const results = await settleLimited(tasks, 4, removeResource);
    const deleted = results.filter(result => result.status === "fulfilled").length;
    if (deleted) setNotice(deleted === 1 ? "Task deleted" : `${deleted} tasks deleted`);
    return results;
  }
  async function removeResource(resource: Resource) {
    const target = writableSpace();
    if (resource.recall?.category === "memory")
      throw new Error("Generated memory is changed from the memory view.");
    await api(`spaces/${target.id}/resources/${encodeURIComponent(resource.id)}?expected_revision=${resource.revision}`, "DELETE");
    setResources(old => old.filter(item => item.id !== resource.id));
  }
  async function changeMemory(
    memory: MemoryVector,
    action: "edit" | "forget",
    value?: string,
  ) {
    if (!space) throw new Error("Open a space first.");
    await command(`spaces/${space.id}/memory/change`, {
      resource_id: memory.memoryId,
      expected_revision: memory.revision,
      entry_id: memory.id.slice(memory.memoryId.length + 1),
      action,
      ...(action === "edit" ? { value } : {}),
    });
    await loadMemories();
    setNotice(action === "forget" ? "Memory deleted" : "Memory updated");
  }
  async function buildMemory(sourceId: string) {
    const source = resources.find((r) => r.id === sourceId);
    if (!space || !source) return;
    const existing = memories.find((m) => m.sourceId === sourceId);
    const job = await command<MemoryJob>(`spaces/${space.id}/memory/jobs`, {
      source: { resource_id: sourceId, revision: source.revision },
      ...(existing
        ? {
            resource_id: existing.memoryId,
            expected_revision: existing.revision,
          }
        : {}),
    });
    setMemoryJobs((old) => [job, ...old.filter((item) => item.id !== job.id)]);
    setJobPollVersion((version) => version + 1);
    setNotice(
      "Source saved for extraction. Memory will be available when processing finishes.",
    );
  }
  // Parameters: a name and harness, and the identity they replace when editing.
  // What it does: creates or renames an agent identity and keeps the list current.
  // Output: the saved identity.
  async function putAgent(
    values: { name: string; harness: string },
    previous?: AgentIdentity,
  ) {
    const normalized = {
      ...values,
      name: values.name.normalize("NFKC").trim().replace(/\s+/g, " "),
    };
    const saved = await api<AgentIdentity>(
      previous ? `agent-identities/${previous.id}` : "agent-identities",
      "POST",
      {
        ...normalized,
        ...(previous ? { expected_revision: previous.revision } : {}),
      },
    );
    setAgents((items) => [
      saved,
      ...items.filter((item) => item.id !== saved.id),
    ]);
    return saved;
  }
  async function saveAgent(
    values: { name: string; harness: string },
    previous?: AgentIdentity,
  ) {
    const saved = await putAgent(values, previous);
    setNotice(previous ? "Agent updated" : "Agent ready to connect");
    return saved;
  }

  async function deleteAgent(agent: AgentIdentity) {
    await deleteAgentWithConnections(agent, grants, api);
    setAgents((items) => items.filter((item) => item.id !== agent.id));
    setGrants((items) => items.filter((item) => item.agent_id !== agent.id));
    setNotice("Agent deleted. Saved work is preserved.");
  }

  // Parameters: an agent identity and whether its connection may change the Space.
  // What it does: creates a connection token for this Space, linked to that identity. The
  // connection list refreshes on its own, because the command invalidates it.
  // Output: the token response; the caller shows it once and keeps it nowhere.
  async function createAgentToken(agent: AgentIdentity, canWrite: boolean) {
    if (!space) throw new Error("Open a space first.");
    return api<AgentToken>("agent-tokens", "POST", agentTokenRequest({
      name: agent.name, harness: agent.harness, canWrite, agentId: agent.id, spaceId: space.id,
    }));
  }

  async function assignAgent(connection: Grant, agentId: string) {
    await api(`agents/${connection.client_id}/identity`, "POST", {
      agent_id: agentId,
    });
    setGrants((items) =>
      items.map((g) =>
        g.id === connection.id ? { ...g, agent_id: agentId } : g,
      ),
    );
    setNotice("Connection linked to agent");
  }

  async function revoke(grant: Grant) {
    await api(`agents/${grant.client_id}`, "DELETE");
    await refreshAgents();
    setNotice("Agent access revoked");
  }
  // Parameters: none. What it does: forgets this browser's session, so the access key is
  // needed again here; other unlocked browsers stay unlocked. Output: nothing.
  async function lock() {
    await api("auth/lock", "POST");
    clearData();
  }
  function clearData() {
    generation.current++;
    memoryRequests.current++;
    pending.current.clear();
    taskStatusSaving.current.clear();
    taskStatusRequests.current.clear();
    newColumnValues.current.clear();
    setPendingTaskStatuses({});
    setTaskColumnState({ columns: statuses, revision: 1 });
    setTaskColumnsReady(false);
    setTaskColumnsError("");
    setViews({});
    latestViews.current = {};
    setViewsReady(false);
    setProvider(null);
    setCursors({});
    setLoadedReads({});
    setReadErrors({});
    setDismissedReadError("");
    setMemoryCursor(null);
    memoryMoreBusy.current = false;
    setMemoryMoreLoading(false);
    setMemoryError("");
    setError("");
    setLoading(false);
    setMemoryLoading(false);
    setAccountId(null);
    setWorkspaces([]);
    setProfile({ displayName: "", avatarUrl: null });
    setSpace(null);
    setResources([]);
    setMemories([]);
    setMemoryJobs([]);
    setJobError("");
    knownJobs.current.clear();
    setGrants([]);
    setAgents([]);
    setProjects([]);
    setFolders([]);
    setOrganizationItems([]);
    setTrashedItems([]);
    setDatabases([]);
    setMemoryLoaded(false);
  }
  async function saveOrganizationItem(item: OrganizationItem) {
    if (!space) throw new Error("Open a space first.");
    const saved = await command<OrganizationItem>(`spaces/${space.id}/items`, {
      item_id: item.id, expected_revision: item.revision ?? 0, kind: item.kind,
      data: { name: item.name, description: item.description },
    });
    setOrganizationItems(old => [...old.filter(i => i.id !== item.id), saved]);
    setNotice(`${item.kind === "category" ? "Category" : "Section"} saved`);
  }
  async function archiveOrganizationItem(item: OrganizationItem, deleted: boolean) {
    if (!space) throw new Error("Open a space first.");
    const saved = await command<OrganizationItem>(`spaces/${space.id}/items/${item.id}/visibility`, {
      expected_revision: item.revision, deleted,
    });
    setOrganizationItems(old => [...old.filter(i => i.id !== item.id), saved]);
    setNotice(deleted ? "Archived" : "Restored");
  }
  async function classifyMemory(memory: MemoryVector, assignment?: MemoryAssignment) {
    if (!space) throw new Error("Open a space first.");
    let classification = assignment;
    if (assignment?.section_ids.length) {
      const legacy = await cachedApi<{ items: OrganizationItem[] }>(
        `spaces/${space.id}/items?kind=section&deleted=false`, { allPages: true, fresh: true },
      );
      classification = { ...assignment, section_ids: assignment.section_ids.filter(
        id => legacy.items.some(item => item.id === id && !item.deleted),
      ) };
    }
    await command(`spaces/${space.id}/memory/change`, {
      resource_id: memory.memoryId, expected_revision: memory.revision,
      entry_id: memory.id.slice(memory.memoryId.length + 1), action: "classify",
      ...(classification ? { classification } : {}),
    });
    await loadMemories();
    setNotice(assignment ? "Memory organization saved" : "Memory classification updated");
  }
  async function saveFolder(folder: DocumentFolder) {
    if (!space) throw new Error("Open a space first.");
    if (space.role === "viewer") throw new Error("You have view-only access to this Space.");
    const current = generation.current;
    const { id, revision = 0, name, description, section_id = null, project_id = null } = folder;
    const saved = await command<DocumentFolder>(`spaces/${space.id}/items`, {
      item_id: id,
      expected_revision: revision,
      kind: "folder",
      // Placement is kept so a website edit never moves the folder in the Space map.
      data: { name, description, section_id, ...(project_id ? { project_id } : {}) },
    });
    if (current === generation.current) {
      setFolders(old => [...old.filter(item => item.id !== saved.id), ...(!saved.deleted ? [saved] : [])]);
      setNotice("Folder saved");
    }
    return saved;
  }
  async function archiveFolder(folder: DocumentFolder, deleted: boolean) {
    if (!space) throw new Error("Open a space first.");
    if (space.role === "viewer") throw new Error("You have view-only access to this Space.");
    const current = generation.current;
    const saved = await command<DocumentFolder>(`spaces/${space.id}/items/${folder.id}/visibility`, {
      expected_revision: folder.revision,
      deleted,
    });
    if (current === generation.current) {
      setFolders(old => [...old.filter(item => item.id !== folder.id), ...(!saved.deleted ? [saved] : [])]);
      setNotice(deleted ? "Folder removed. Documents are kept." : "Folder restored");
    }
    return saved;
  }
  async function saveProject(project: Project) {
    if (!space) throw new Error("Open a space first.");
    const { id, revision = 0, name, description, status, section_id = null } = project;
    const saved = await command<Project>(`spaces/${space.id}/items`, {
      item_id: id,
      expected_revision: revision,
      kind: "project",
      data: { name, description, status, section_id },
    });
    setProjects((old) => [...old.filter((p) => p.id !== saved.id), saved]);
    setNotice("Project saved");
    return saved;
  }
  async function setItemDeleted(
    item: (Project | WorkspaceDatabase) & { kind: "project" | "database" },
    deleted: boolean,
  ) {
    if (!space) throw new Error("Open a space first.");
    const saved = await command<typeof item>(
      `spaces/${space.id}/items/${item.id}/visibility`,
      {
        expected_revision: item.revision,
        deleted,
      },
    );
    setTrashedItems((old) =>
      deleted
        ? [...old.filter((i) => i.id !== item.id), saved]
        : old.filter((i) => i.id !== item.id),
    );
    if (item.kind === "project")
      setProjects((old) => [
        ...old.filter((i) => i.id !== item.id),
        ...(!deleted ? [saved as Project] : []),
      ]);
    else
      setDatabases((old) => [
        ...old.filter((i) => i.id !== item.id),
        ...(!deleted ? [saved as WorkspaceDatabase] : []),
      ]);
    setNotice(deleted ? "Moved to trash" : "Restored");
  }
  async function saveDatabase(database: WorkspaceDatabase) {
    if (!space) throw new Error("Open a space first.");
    if (space.role === "viewer") throw new Error("You have view-only access to this Space.");
    if (!isWorkspaceDatabase(database))
      throw new Error(
        "Check the database properties and record values before saving.",
      );
    const {
      id,
      revision = 0,
      name,
      description,
      fields,
      records,
      view,
      groupBy,
      tasks = null,
      project_id = null,
      section_id = null,
    } = database;
    const saved = await command<WorkspaceDatabase>(`spaces/${space.id}/items`, {
      item_id: id,
      expected_revision: revision,
      kind: "database",
      data: {
        name, description, fields, records, view, groupBy,
        ...(tasks ? { tasks } : {}),
        // Agents place databases; a website edit must not clear that placement.
        ...(project_id ? { project_id } : {}),
        ...(section_id ? { section_id } : {}),
      },
    });
    setDatabases((old) => [...old.filter((d) => d.id !== id), saved]);
    return saved;
  }
  async function latestDatabase(id: string) {
    if (!space) throw new Error("Open a space first.");
    const latest = await api<WorkspaceDatabase>(`spaces/${space.id}/items/${id}`);
    if (!latest || latest.deleted || !isWorkspaceDatabase(latest))
      throw new Error("This database is no longer available. Reload the page.");
    setDatabases((old) => [...old.filter((d) => d.id !== id), latest]);
    return latest;
  }
  async function deleteDatabase(id: string) {
    const database = databases.find((d) => d.id === id);
    if (!database) throw new Error("Database not found.");
    await setItemDeleted({ ...database, kind: "database" }, true);
  }
  function setDensity(value: boolean) {
    setCompact(value);
    try {
      localStorage.setItem("space-density", value ? "compact" : "comfortable");
    } catch {}
  }
  function switchWorkspace(id: string, destination = "/app") {
    resetReadCache(0, "", false, true);
    // A fresh page also discards editors, searches and pending response handlers
    // belonging to the previous Space. The server validates membership again.
    try {
      sessionStorage.setItem(selectedSpaceKey, id);
    } catch {}
    window.location.assign(
      `${destination}?workspace=${encodeURIComponent(id)}`,
    );
  }
  return {
    workspaces,
    // A self-hosted Space has no plan: only a viewer role makes it read-only.
    canWrite: space?.role !== "viewer",
    deleteResource,
    deleteTasks,
    switchWorkspace,
    agents,
    saveAgent,
    deleteAgent,
    assignAgent,
    // The connect dialog reports its own progress, so this creates an identity quietly.
    createAgentIdentity: (values: { name: string; harness: string }) => putAgent(values),
    createAgentToken,
    accountId,
    locked: accountId === null,
    accessMode,
    profile,
    space,
    resources: resources.map(resource => optimisticTask(resource, pendingTaskStatuses)),
    views,
    viewsReady,
    watchViews,
    markSeen,
    resourcesReady: !!loadedReads.document && !!loadedReads.task,
    taskColumns: taskColumnsWithResources(taskColumnState.columns, resources),
    taskColumnsReady,
    taskColumnsError,
    watchTaskColumns,
    retryTaskColumns: () => space && invalidateReads(`spaces/${space.id}/tasks/columns`),
    addTaskColumn,
    changeTaskStatus,
    changeTaskStatuses,
    pendingTaskStatuses,
    projects,
    folders,
    databases,
    memories,
    grants,
    provider,
    cursors,
    loading,
    pageLoading,
    pageError,
    retryPage: () => retryReads(plan.required),
    sectionReady: !!loadedReads.section,
    sectionError: readErrors.section,
    retrySections: () => retryReads(["section"]),
    databaseReady: !!loadedReads.database,
    databaseError: readErrors.database,
    retryDatabases: () => retryReads(["database"]),
    folderReady: !!loadedReads.folder,
    folderError: readErrors.folder,
    retryFolders: () => retryReads(["folder"]),
    organizationReady: (["project", "category"] as const).every(kind => loadedReads[kind]),
    organizationError: (["project", "category"] as const).map(kind => readErrors[kind]).find(Boolean),
    retryOrganization: () => retryReads(["project", "category"]),
    memoryLoading,
    memoryMoreLoading,
    memoryLoaded,
    memoryCursor,
    memoryError,
    error: error || (pageError || dismissedReadError === backgroundError ? "" : backgroundError) || "",
    notice,
    compact,
    setDensity,
    setError: (value: string) => {
      setError(value);
      if (!value) setDismissedReadError(backgroundError ?? "");
    },
    setNotice,
    initialize,
    loadMore,
    loadMemories,
    loadMoreMemories,
    searchMemories,
    readResource,
    loadSelectedMemory,
    saveResource,
    changeMemory,
    buildMemory,
    memoryJobs,
    jobError,
    revoke,
    lock,
    saveProject,
    saveFolder,
    archiveFolder,
    organizationItems,
    saveOrganizationItem,
    archiveOrganizationItem,
    classifyMemory,
    trashedItems,
    setItemDeleted,
    refreshItems,
    watchItems,
    watchAgents,
    refreshAgents,
    watchMemories,
    watchResource,
    saveDatabase,
    latestDatabase,
    deleteDatabase,
  };
}

const Context = createContext<ReturnType<typeof useData> | null>(null);
export function SpaceProvider({ children }: { children: ReactNode }) {
  const value = useData();
  const [queryClient] = useState(getReadQueryClient);
  return <QueryClientProvider client={queryClient}>
    <Context.Provider value={value}>{children}</Context.Provider>
  </QueryClientProvider>;
}
export function useSpace() {
  const context = useContext(Context);
  if (!context) throw new Error("SpaceProvider is required");
  return context;
}

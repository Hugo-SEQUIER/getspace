export type Status = string;
export type TaskColumn = { value: Status; label: string };
export type TaskColumns = { columns: TaskColumn[]; revision: number };
export type AgentIdentity = {
  id: string;
  name: string;
  harness: string;
  revision: number;
};
export type Resource = {
  id: string;
  kind: "document" | "task";
  title: string;
  body?: string;
  status: Status;
  revision: number;
  updated_at: string;
  author: string;
  author_client_id?: string | null;
  author_agent?: AgentIdentity | null;
  project?: string;
  folder?: string;
  priority?: "High" | "Medium" | "Low";
  recall?: {
    category: string;
    sources?: { resource_id: string; revision: number }[];
  };
};
export type DocumentFolder = {
  id: string;
  kind: "folder";
  name: string;
  description: string;
  section_id?: string | null;
  /** Where the folder belongs in the Space map; documents keep their own project. */
  project_id?: string | null;
  revision?: number;
  deleted?: boolean;
};
export type OrganizationItem = {
  id: string;
  kind: "category" | "section";
  name: string;
  description: string;
  revision?: number;
  deleted?: boolean;
};
export type MemoryAssignment = {
  scope: "global" | "project" | "unassigned";
  category_ids: string[];
  project_ids: string[];
  section_ids: string[];
};
export type MemoryClassification = MemoryAssignment & {
  method: "manual" | "decision" | "unassigned";
  reason: "matched" | "uncertain" | "disabled" | "unavailable" | "catalog_limit" | "input_limit";
};
export type Project = {
  section_id?: string | null;
  revision?: number;
  deleted?: boolean;
  id: string;
  name: string;
  description: string;
  status: Status;
};
export type MemoryVector = {
  id: string;
  memoryId: string;
  revision: number;
  title: string;
  value: string;
  category: string;
  classification?: MemoryClassification;
  creator: string | null;
  creatorBasis?: "captured" | "user_attributed";
  revisionAgent?: AgentIdentity | null;
  sourceId?: string;
  sourceRevision?: number;
  sourceTitle?: string;
  importOrigin?: "memora" | null;
  evidence?: string;
  stale: boolean;
  edited?: boolean;
  createdAt?: string;
  x?: number;
  y?: number;
  size?: number;
};
export type Grant = {
  id: string;
  client_id: string;
  name?: string;
  agent_id?: string | null;
  harness?: string;
  can_write: boolean;
  revoked_at: string | null;
};
export type Provider = {
  enabled: boolean;
  provider: string;
  endpoint: string | null;
  text_model: string | null;
  relevance_model?: string | null;
  relevance_provider?: "model" | "decision";
  classification_provider?: "disabled" | "decision";
  relationship_provider?: "disabled" | "decision";
  // The decision model and where it is reached, when any memory feature asks it.
  decision_model?: string | null;
  decision_endpoint?: string | null;
  embedding_model: string | null;
  embedding_dim: number;
  index: {
    claims: number;
    memories: number;
    other_model: number;
    engine: string | null;
  };
};
export type Space = {
  id: string;
  name: string;
  mcp_url: string;
  organization_id?: string;
  kind?: "personal" | "team";
  role?: "owner" | "admin" | "member" | "viewer";
  visibility?: "private" | "restricted" | "shared";
  is_default?: boolean;
};
export type MemoryResponse = {
  import_origin?: "memora" | null;
  id: string;
  revision: number;
  title: string;
  stale: boolean;
  author_agent?: AgentIdentity | null;
  recall?: { sources?: { resource_id: string; revision: number }[] };
  claims: {
    creator?: { kind: "human" | "agent"; agent: AgentIdentity | null; basis: "captured" | "user_attributed" } | null;
    id: string;
    index: string;
    value: string;
    memory_type: string;
    classification?: MemoryClassification;
    edited: boolean;
    created_at?: string;
    source: string | null;
  }[];
};
export const statuses: { value: Status; label: string }[] = [
  { value: "todo", label: "To do" },
  { value: "in_progress", label: "In progress" },
  { value: "needs_you", label: "Needs you" },
  { value: "done", label: "Done" },
];
/** The revision of a document or task the owner last opened. */
export type ResourceView = { resource_id: string; seen_revision: number; seen_at: string };
// Category and creator colours come from the public site's evening valley: dusk blue,
// apricot, olive hills and plum cloud first (categoryPalette in memory-view.tsx).
export const categoryColors: Record<string, string> = {
  Product: "#5b6fb5",
  Design: "#d9825a",
  Engineering: "#6f8a4a",
  Research: "#a8668f",
};

// Synthetic workspace data for unit tests of the memory graph, the resource model and
// databases. Every name, address and figure here is fictional.
import type { AgentIdentity, DocumentFolder, Grant, MemoryVector, OrganizationItem, Project, Resource } from "../../lib/space/types.ts";
import type { ActivityDay, Measures, Overview, OverviewSnapshot, Period } from "../../lib/space/overview.ts";
import { createDatabase, type FieldValue, type WorkspaceDatabase } from "../../lib/space/databases.ts";

const date = "2026-09-12T08:30:00Z";
export const demoDocuments: Resource[] = [
  {
    id: "launch-plan",
    kind: "document",
    folder: "website-notes",
    title: "Launch plan",
    body: "A focused plan for the first release.\n\n## Overview\nBring the landing page, onboarding, and agent setup into one coherent first experience. Keep the first release focused on individual use.\n\n## Decisions\n- Start with one private space.\n- Connect an agent after sign-up.\n- Keep documents, tasks, and memory together.\n\n## Open questions\n- What should a new user see first?\n- How much setup belongs in onboarding?",
    status: "todo",
    revision: 3,
    updated_at: date,
    author: "Codex",
    project: "website-launch",
  },
  {
    id: "research-notes",
    kind: "document",
    folder: "reference-notes",
    title: "Research notes",
    body: "What we learned from the first conversations.\n\n## The recurring problem\nUseful work gets lost between agent sessions. People want to return to the decisions, sources, and next steps without explaining everything again.\n\n## What matters\n- Make the first save effortless.\n- Show the source behind a memory.\n- Keep the interface quiet and easy to explore.",
    status: "todo",
    revision: 2,
    updated_at: "2026-09-11T15:00:00Z",
    author: "You",
    project: "website-launch",
  },
  {
    id: "session-handoff",
    kind: "document",
    folder: "website-notes",
    title: "Session handoff",
    body: "Where we left things, and what comes next.\n\n## Completed\nThe visual direction is selected. The first release is personal. Memory is shared across authorized agents.\n\n## Next session\n- Refine the onboarding flow.\n- Build the document experience.\n- Review the Memory vector map.",
    status: "todo",
    revision: 1,
    updated_at: "2026-09-11T11:00:00Z",
    author: "Codex",
    project: "website-launch",
    recall: { category: "handoff" },
  },
  {
    id: "agent-guide",
    kind: "document",
    folder: "reference-notes",
    title: "Agent setup guide",
    body: "Connect your agent to the place your work lives.\n\n## Getting started\nOpen Agents in your space and follow the connection guide. Review the requested access before authorizing.\n\n## Your memory\nA memory can be used by different authorized agents. Its creator is recorded as metadata.",
    status: "todo",
    revision: 1,
    updated_at: "2026-09-10T11:00:00Z",
    author: "You",
    project: "agent-setup",
  },
  {
    id: "launch-brief",
    kind: "document",
    folder: "website-notes",
    title: "Launch brief",
    body: "## Focus\nFocus the first release on personal workspaces.\n\n## Principles\nA calm place to inspect, correct, and continue your work. Agents use the same saved sources and memory.",
    status: "todo",
    revision: 3,
    updated_at: date,
    author: "You",
    project: "website-launch",
  },
];
export const demoTasks: Resource[] = [
  ["landing", "Draft landing page", "in_progress", "High"],
  ["first-use", "Map first-use flow", "in_progress", "Medium"],
  ["outreach", "Approve the outreach drafts", "needs_you", "High"],
  ["onboarding", "Review onboarding", "todo", "High"],
  ["guide", "Write agent setup guide", "todo", "Medium"],
  ["sources", "Check source links", "todo", "Low"],
  ["checklist", "Prepare launch checklist", "todo", "Medium"],
  ["scope", "Define personal space", "done", "Low"],
].map(([id, title, status, priority]) => ({
  id,
  title,
  status: status as Resource["status"],
  priority: priority as Resource["priority"],
  kind: "task",
  revision: 1,
  updated_at: date,
  author: "Codex",
  project: "website-launch",
  body: "Create a clear first impression and show how saved work carries into the next session.",
}));
export const demoSections: OrganizationItem[] = [
  { id: "sample-work", kind: "section", name: "Work", description: "Current projects" },
  { id: "sample-library", kind: "section", name: "Library", description: "Reference material" },
];
export const demoFolders: DocumentFolder[] = [
  {
    id: "website-notes",
    kind: "folder",
    name: "Website notes",
    description: "Plans and notes for the website.",
    section_id: "sample-work",
    revision: 1,
  },
  {
    id: "reference-notes",
    kind: "folder",
    name: "Reference notes",
    description: "Guides and research to return to.",
    section_id: "sample-library",
    revision: 1,
  },
];
export const demoProjects: Project[] = [
  {
    id: "website-launch",
    section_id: "sample-work",
    name: "Website launch",
    description:
      "Shape the first release around a clear landing page and a simple onboarding flow.",
    status: "in_progress",
  },
  {
    id: "agent-setup",
    section_id: "sample-work",
    name: "Agent setup",
    description: "Connect your agent to a personal space.",
    status: "todo",
  },
  {
    id: "research-library",
    section_id: "sample-library",
    name: "Research library",
    description: "Keep source notes easy to find.",
    status: "todo",
  },
];
export const demoGrants: Grant[] = [
  {
    id: "sample-codex",
    client_id: "sample-codex",
    agent_id: "sample-codex-agent",
    name: "Codex",
    can_write: true,
    revoked_at: null,
  },
];

export function makeDemoHistories(): Record<string, Resource[]> {
  return Object.fromEntries(
    demoDocuments
      .filter((d) => d.revision > 1)
      .map((document) => [
        document.id,
        Array.from({ length: document.revision - 1 }, (_, i) => ({
          ...document,
          revision: i + 1,
          updated_at: `2026-09-${String(8 + i).padStart(2, "0")}T09:00:00Z`,
          body:
            i === 0
              ? `## Initial notes\n${document.title}: the starting point.\n\nGather the context and decide what belongs in the first release.`
              : document.body,
        })),
      ]),
  );
}

// Reproducible illustrative coordinates for graph and memory model tests.
export function makeDemoVectors(): MemoryVector[] {
  let seed = 91;
  const random = () => {
    seed = (seed * 16807) % 2147483647;
    return (seed - 1) / 2147483646;
  };
  const groups = [
    {
      name: "Product",
      x: 335,
      y: 305,
      labels: [
        "First release",
        "Personal workspaces",
        "One shared memory",
        "Simple onboarding",
      ],
      facts: [
        "The first release is for individual users.",
        "Start with one private space.",
        "Authorized agents can reuse the same memory.",
        "Connect an agent after creating your account.",
      ],
    },
    {
      name: "Design",
      x: 665,
      y: 305,
      labels: [
        "Interface principles",
        "Reading space",
        "Quiet navigation",
        "Category colors",
      ],
      facts: [
        "Keep the interface compact and quiet.",
        "Give documents room to breathe.",
        "Use a consistent sidebar across the workspace.",
        "Use category colors to help explore memory.",
      ],
    },
    {
      name: "Engineering",
      x: 325,
      y: 650,
      labels: [
        "Agent setup flow",
        "Session continuity",
        "Source revisions",
        "Explicit changes",
      ],
      facts: [
        "An agent needs permission before reading the space.",
        "Save a handoff before ending a session.",
        "Source citations point to a retained revision.",
        "Changing memory is an explicit action.",
      ],
    },
    {
      name: "Research",
      x: 655,
      y: 660,
      labels: [
        "Research findings",
        "Retain source revisions",
        "Evidence matters",
        "Recall context",
      ],
      facts: [
        "Show where a remembered fact came from.",
        "A source update does not erase saved memory.",
        "Evidence helps distinguish memory from assumptions.",
        "Find useful context across agent sessions.",
      ],
    },
  ];
  const nodes: MemoryVector[] = [];
  groups.forEach((group, categoryIndex) => {
    for (let branch = 0; branch < 16; branch++) {
      const angle = (branch / 16) * Math.PI * 2 + random() * 0.3;
      const distance = branch === 0 ? 0 : 70 + random() * 155;
      const hubX = group.x + Math.cos(angle) * distance,
        hubY = group.y + Math.sin(angle) * distance;
      for (let leaf = 0; leaf < 9; leaf++) {
        const theta = angle + (random() - 0.5) * 4.5;
        const radius = leaf === 0 ? 0 : 28 + random() * 62;
        const index = branch % 4;
        nodes.push({
          id: `vector-${categoryIndex}-${branch}-${leaf}`,
          memoryId: `sample-memory-${categoryIndex}-${branch}-${leaf}`,
          revision: 1,
          title: group.labels[index],
          value: group.facts[index],
          category: group.name,
          classification: {
            scope: leaf === 0 ? "project" : "unassigned",
            category_ids: branch === 15 ? [] : [group.name],
            project_ids: leaf === 0 ? ["website-launch"] : [],
            section_ids: [],
            method: "manual",
            reason: "matched",
          },
          creator: (branch + leaf) % 3 === 1 ? "You" : "Codex",
          sourceId:
            categoryIndex === 0
              ? "launch-brief"
              : categoryIndex === 3
                ? "research-notes"
                : "launch-plan",
          sourceTitle:
            categoryIndex === 0
              ? "Launch brief"
              : categoryIndex === 3
                ? "Research notes"
                : "Launch plan",
          sourceRevision:
            categoryIndex === 0 && branch === 0
              ? 2
              : categoryIndex === 3
                ? 2
                : 3,
          evidence:
            categoryIndex === 0
              ? "Focus the first release on personal workspaces."
              : group.facts[index],
          stale: categoryIndex === 0 && branch === 0,
          x: Math.max(45, Math.min(970, hubX + Math.cos(theta) * radius)),
          y: Math.max(55, Math.min(900, hubY + Math.sin(theta) * radius)),
          size:
            leaf === 0
              ? branch === 0
                ? 10
                : branch === 3
                  ? 8.5
                  : 4 + random() * 3
              : 1.8 + random() * 1.5,
        });
      }
    }
  });
  return nodes;
}

export function makeDemoDatabases(): WorkspaceDatabase[] {
  const db = createDatabase(
    "clients",
    "Clients",
    "A little context for every conversation. Track prospects from first hello to a new client.",
    "sample-clients",
  );
  db.records = [
    [
      "Northstar Studio",
      "Alex Morgan",
      "alex@northstar.example",
      "Proposal",
      12000,
      "2026-09-15",
    ],
    [
      "Forma",
      "Jamie Chen",
      "jamie@forma.example",
      "Qualified",
      8500,
      "2026-09-16",
    ],
    [
      "Sunday Supply",
      "Sam Rivera",
      "sam@sunday.example",
      "Contacted",
      6000,
      "2026-09-18",
    ],
    [
      "Atelier Nine",
      "Charlie Reed",
      "charlie@atelier.example",
      "New",
      null,
      "",
    ],
    [
      "Common Ground",
      "Taylor Kim",
      "taylor@common.example",
      "Won",
      15000,
      "2026-09-21",
    ],
    [
      "Fieldwork",
      "Jordan Blake",
      "jordan@fieldwork.example",
      "Contacted",
      9500,
      "2026-09-17",
    ],
  ].map(([name, contact, email, stage, value, followup], index) => ({
    id: `sample-client-${index}`,
    name: String(name),
    notes: "",
    createdAt: `2026-09-${String(index + 1).padStart(2, "0")}T09:00:00Z`,
    updatedAt: "2026-09-12T09:00:00Z",
    values: Object.fromEntries(
      db.fields.map((field, i) => [
        field.id,
        [contact, email, stage, value, followup][i],
      ]),
    ) as Record<string, FieldValue>,
  }));
  return [db];
}

export function sampleOverviewSnapshot(
  resources: Resource[], memories: MemoryVector[], agents: AgentIdentity[], now = new Date(),
): OverviewSnapshot {
  const periods = {
    7: sampleOverview(7, resources, memories, agents, now),
    30: sampleOverview(30, resources, memories, agents, now),
    90: sampleOverview(90, resources, memories, agents, now),
  };
  return { ...periods[90], periods };
}

const emptyMeasures = (): Measures => ({
  document_writes: 0,
  documents_created: 0,
  memories_created: 0,
  memory_writes: 0,
  retrievals: 0,
  with_results: 0,
  empty: 0,
  errors: 0,
  p50_ms: null,
  p95_ms: null,
});

/** Synthetic overview history that reconciles chart, totals and agent activity. */
export function sampleOverview(
  days: Period,
  resources: Resource[],
  memories: MemoryVector[],
  agents: AgentIdentity[],
  now = new Date(),
): Overview {
  const today = Date.UTC(
    now.getUTCFullYear(),
    now.getUTCMonth(),
    now.getUTCDate(),
  );
  const documents = resources.filter(
    (r) =>
      r.kind === "document" && (!r.recall || r.recall.category === "source"),
  );
  const activity: ActivityDay[] = Array.from({ length: days }, (_, i) => {
    const age = days - 1 - i;
    return {
      date: new Date(today - age * 86400000).toISOString().slice(0, 10),
      available: true,
      document_writes:
        age < 14 && age % 3 === 0 ? Math.min(2, documents.length) : 0,
      memories_created:
        age === 8
          ? Math.floor(memories.length / 2)
          : age === 2
            ? Math.ceil(memories.length / 2)
            : 0,
      retrievals: age % 7 === 0 ? 0 : 5 + ((age * 7 + 3) % 18),
    };
  });
  const summary = emptyMeasures();
  for (const row of activity) {
    summary.document_writes += row.document_writes!;
    summary.memories_created += row.memories_created!;
    summary.retrievals += row.retrievals!;
  }
  summary.documents_created = days >= 30 ? documents.length : 0;
  summary.memory_writes = activity.filter((row) => row.memories_created).length;
  summary.errors = Math.floor(summary.retrievals * 0.02);
  summary.empty = Math.floor(summary.retrievals * 0.12);
  summary.with_results = summary.retrievals - summary.empty - summary.errors;
  summary.p50_ms = 342;
  summary.p95_ms = 1240;
  return {
    space_id: "sample",
    days,
    as_of: now.toISOString(),
    period_start: activity[0].date + "T00:00:00Z",
    tracking_started_at: new Date(today - 100 * 86400000).toISOString(),
    totals: {
      documents: documents.length,
      tasks: resources.filter((r) => r.kind === "task").length,
      memories: memories.length,
      document_lines: documents.reduce(
        (n, r) =>
          n + (r.body ? r.body.replace(/\n$/, "").split("\n").length : 0),
        0,
      ),
      measured_documents: documents.length,
    },
    summary: { ...summary, reused_memories: Math.floor(memories.length * 0.7) },
    activity,
    agents: [
      {
        ...summary,
        agent_id: agents[0]?.id ?? null,
        actor_type: "agent",
        last_active_at: now.toISOString(),
      },
    ],
    strategies: [{ ...summary, strategy: "auto" }],
    recent: documents.slice(0, 3).map((r, i) => ({
      id: r.id,
      created_at: new Date(now.getTime() - (i + 1) * 3600000).toISOString(),
      agent_id: agents[0]?.id ?? null,
      actor_type: "agent",
      kind: "document",
      resource_id: r.id,
      resource_exists: true,
      new_items: 0,
      item_count: 1,
      outcome: "success",
      duration_ms: null,
    })),
    usage: {
      retained_bytes: 2457600,
      storage_limit_bytes: 536870912,
      items: resources.length,
      item_limit: 10000,
      model_requests: 184,
      model_request_limit: 2000,
    },
  };
}

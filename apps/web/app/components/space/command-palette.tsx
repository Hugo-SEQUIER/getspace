import Link from "@web/lib/navigation";
import { useEffect, useState } from "react";
import { useSpace } from "./provider";
import { useWorkspaceSearch } from "./workspace-search";
import { Icon, type IconName } from "./ui-icon";
import { Modal } from "./ui";
import { useFeedback } from "../notifications";

const filters = [
  ["all", "All"], ["task", "Tasks"], ["document", "Documents"],
  ["project", "Projects"], ["memory", "Memory"], ["agent", "Agents"], ["database", "Databases"],
] as const;
type Kind = typeof filters[number][0];
type Result = { id: string; title: string; type: string; href: string };
const icons: Record<string, IconName> = { document: "document", task: "tasks", project: "folder", memory: "memory", database: "database", agent: "agents", category: "folder" };
const names: Record<string, string> = { document: "Document", task: "Task", project: "Project", memory: "Memory", database: "Database", agent: "Agent", category: "Category" };

export function CommandPalette({ base, onClose, onCreate }: {
  base: string; onClose: () => void; onCreate: (kind: "document" | "task") => void;
}) {
  const data = useSpace();
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Kind>("all");
  const search = useWorkspaceSearch(data.space?.id, base, query, !!query.trim() && filter !== "agent");
  useEffect(() => filter === "agent" ? data.watchAgents() : undefined, [filter, data.watchAgents]);
  const local: Result[] = [
    ...[...data.resources].filter(r => r.recall?.category !== "memory").sort((a, b) => b.updated_at.localeCompare(a.updated_at)).map(r => ({ id: r.id, title: r.title, type: r.kind, href: `${base}/${r.kind === "task" ? "tasks" : "documents"}/${encodeURIComponent(r.id)}` })),
    ...data.projects.map(p => ({ id: p.id, title: p.name, type: "project", href: `${base}/projects/${encodeURIComponent(p.id)}` })),
    ...data.databases.map(d => ({ id: d.id, title: d.name, type: "database", href: `${base}/databases/${encodeURIComponent(d.id)}` })),
    ...data.memories.map(m => ({ id: m.id, title: m.title || m.value, type: "memory", href: `${base}/memory/${encodeURIComponent(m.id)}` })),
    ...data.agents.map(a => ({ id: a.id, title: a.name, type: "agent", href: `${base}/agents` })),
  ];
  const useLocal = !query.trim() || filter === "agent";
  const source: Result[] = useLocal
    ? local : search.items.map(r => ({ ...r, type: r.kind }));
  const results = source.filter(r => (filter === "all" || r.type === filter) && (!useLocal || r.title.toLowerCase().includes(query.trim().toLowerCase()))).slice(0, query.trim() ? 8 : 5);
  const searching = !!query.trim() && filter !== "agent";
  useFeedback({ error: searching ? search.error : null, loading: searching && search.loading ? "Searching your space…" : null });
  return <Modal title="Search your space" className="sp-command-modal sp-refined-command" onClose={onClose}>
    <div className="sp-command" onKeyDown={event => {
      if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
      if ((event.target as Element).closest('[role="group"]')) return;
      const items = [...event.currentTarget.querySelectorAll<HTMLElement>("input, .sp-command-result, .sp-command-quick-action, .sp-command-all")];
      const index = items.indexOf(document.activeElement as HTMLElement);
      if (index < 0 || !items.length) return;
      event.preventDefault();
      items[(index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus();
    }}>
      <label className="sp-command-input"><Icon name="search" size={21} /><input data-autofocus value={query} onChange={event => setQuery(event.target.value)} placeholder="Search anything…" aria-label="Quick search" /></label>
      <div className="sp-command-filters" role="group" aria-label="Search by type">
        {filters.map(([value, label]) => <button key={value} type="button" aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}</button>)}
      </div>
      <div className="sp-command-results">
        <p className="sp-command-label"><span>{query.trim() ? "Results" : "Recent & ready to open"}</span></p>
        {searching && search.error && <button onClick={search.retry}>Restart search</button>}
        {results.map(r => <Link className="sp-command-result" href={r.href} key={`${r.type}:${r.id}`} onClick={onClose}>
          <span className={`sp-command-type-icon is-${r.type}`}><Icon name={icons[r.type] ?? "document"} size={19} /></span>
          <span className="sp-command-result-copy"><strong>{r.title}</strong><small>{names[r.type] ?? r.type} · {data.space?.name ?? "Your space"}</small></span>
          <Icon name="arrow" size={16} />
        </Link>)}
        {!results.length && !(searching && (search.loading || search.error)) && <div className="sp-command-empty" role="status"><Icon name="search" size={24} /><strong>{query.trim() ? "No matching items" : "A fresh start"}</strong><p>{query.trim() ? "Try another title or choose a different type." : "Create something below. You’ll find it here next time."}</p></div>}
      </div>
      {!query.trim() && <div className="sp-command-quick"><p className="sp-command-label">Quick actions</p>
        {([['task', 'New task', 'tasks'], ['document', 'New document', 'document']] as const).map(([kind, title, icon]) => <button key={kind} className="sp-command-quick-action" disabled={!data.canWrite} onClick={() => { onClose(); onCreate(kind); }}><Icon name={icon} size={17} /><span>{title}</span><Icon name="plus" size={15} /></button>)}
        <Link className="sp-command-quick-action" href={`${base}/projects`} onClick={onClose}><Icon name="folder" size={17} /><span>Open projects</span><Icon name="arrow" size={15} /></Link>
      </div>}
      <Link className="sp-command-all" href={`${base}/search`} onClick={onClose}>Open full search <Icon name="arrow" size={15} /></Link>
      <footer><span><kbd>↑</kbd> <kbd>↓</kbd> navigate</span><span><kbd>↵</kbd> open</span><span><kbd>Esc</kbd> close</span></footer>
    </div>
  </Modal>;
}

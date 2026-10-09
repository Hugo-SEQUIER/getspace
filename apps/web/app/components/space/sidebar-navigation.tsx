
import Link from "@web/lib/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { attentionItems } from "../../../lib/space/attention";
import { useSpace } from "./provider";
import { Modal } from "./ui";
import { PageIconPicker } from "./page-icon-picker";
import { Icon, type IconName } from "./ui-icon";
import {
  PageIcon,
  useSidebarPreferences,
  type SidebarPage,
} from "./sidebar-preferences";

const navigation: { path: string; title: string; icon: IconName }[] = [
  { path: "tasks", title: "Tasks", icon: "tasks" },
  { path: "documents", title: "Documents", icon: "document" },
  { path: "memory", title: "Memory", icon: "memory" },
  { path: "databases", title: "Databases", icon: "database" },
  { path: "overview", title: "Overview", icon: "chart" },
  { path: "projects", title: "Projects", icon: "folder" },
  { path: "agents", title: "Agents", icon: "agents" },
];

function SidebarGroup({
  id,
  title,
  actions,
  children,
}: {
  id: string;
  title: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  const { preferences, update } = useSidebarPreferences();
  const collapsed = !!preferences.collapsed[id];
  return (
    <section className={`sp-sidebar-group sp-sidebar-group-${id}`} aria-label={title}>
      <div className="sp-sidebar-group-head">
        <button
          className="sp-sidebar-group-toggle"
          aria-expanded={!collapsed}
          aria-controls={`sidebar-${id}`}
          onClick={() =>
            update((p) => ({
              ...p,
              collapsed: { ...p.collapsed, [id]: !p.collapsed[id] },
            }))
          }
        >
          <Icon name={collapsed ? "chevron" : "down"} size={12} />
          <span>{title}</span>
        </button>
        {actions}
      </div>
      <div id={`sidebar-${id}`} hidden={collapsed}>
        {children}
      </div>
    </section>
  );
}

export function SidebarNavigation({
  base,
  view,
  id,
}: {
  base: string;
  view: string;
  id?: string;
}) {
  const data = useSpace();
  useEffect(() => data.watchViews(), [data.watchViews]);
  const attention = data.resourcesReady && data.viewsReady ? attentionItems(data.resources, data.views) : null;
  const attentionCount = attention ? attention.needsYou.length + attention.unseen.length : 0;
  const { preferences, pages } = useSidebarPreferences();
  const [editing, setEditing] = useState(false);
  const [options, setOptions] = useState<SidebarPage | null>(null);
  const byKey = new Map(pages.map((page) => [page.key, page]));
  function navigationLinks(items: typeof navigation) {
    return items.map(item => <Link
      href={`${base}/${item.path}`} key={item.path}
      className={`sp-nav-link ${view === item.path ? "active" : ""}`}
      aria-current={view === item.path ? "page" : undefined}
      aria-label={item.title} title={item.title}
    ><Icon name={item.icon} active={view === item.path} /><span>{item.title}</span></Link>);
  }
  function shortcuts(keys: string[]) {
    const items = keys.flatMap((key) =>
      byKey.get(key) ? [byKey.get(key)!] : [],
    );
    return items.length ? (
      items.map((page) => {
        const active = page.href === `${base}/${view}/${id}`;
        return (
          <div
            className={`sp-sidebar-shortcut ${active ? "selected" : ""}`}
            key={page.key}
          >
            <Link
              href={page.href}
              aria-current={active ? "page" : undefined}
              title={page.title}
            >
              <PageIcon pageKey={page.key} fallback={page.icon} size={16} />
              <span>{page.title}</span>
            </Link>
            <button
              className="sp-sidebar-action"
              aria-label={`Options for ${page.title}`}
              onClick={() => setOptions(page)}
            >
              <Icon name="more" size={15} />
            </button>
          </div>
        );
      })
    ) : (
      <button
        className="sp-sidebar-empty"
        onClick={() => setEditing(true)}
      >
        Add a page
      </button>
    );
  }
  return (
    <>
      <div className="sp-sidebar-scroll">
        <nav aria-label="Main navigation">
          <Link
            href={base}
            className={`sp-nav-link ${view === "home" ? "active" : ""}`}
            aria-current={view === "home" ? "page" : undefined}
            aria-label="Home"
            title="Home"
          >
            <Icon name="home" active={view === "home"} />
            <span>Home</span>
            {attentionCount > 0 && <span className="sp-nav-badge" aria-label={`${attentionCount} items need attention`}>{attentionCount}</span>}
          </Link>
          {navigationLinks(navigation.slice(0, 4))}
          <SidebarGroup id="workspace" title="Workspace">{navigationLinks(navigation.slice(4))}</SidebarGroup>
        </nav>
        <SidebarGroup
          id="favorites"
          title="Favorites"
          actions={
            <button
              className="sp-sidebar-action"
              aria-label="Manage favorites"
              title="Add or remove favorites"
              onClick={() => setEditing(true)}
            >
              <Icon name="plus" size={14} />
            </button>
          }
        >
          {shortcuts(preferences.favorites)}
        </SidebarGroup>
      </div>
      {editing && (
        <FavoritesEditor onClose={() => setEditing(false)} />
      )}
      {options && (
        <PageOptions page={options} onClose={() => setOptions(null)} />
      )}
    </>
  );
}

function FavoritesEditor({ onClose }: { onClose: () => void }) {
  const { preferences, pages, update } = useSidebarPreferences();
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(preferences.favorites);
  const filtered = pages.filter((page) =>
    page.title.toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <Modal
      title="Favorites"
      onClose={onClose}
      className="sp-sidebar-dialog"
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          update((p) => ({
            ...p,
            favorites: selected,
            collapsed: { ...p.collapsed, favorites: false },
          }));
          onClose();
        }}
      >
        <p className="sp-sidebar-dialog-note">
          Keep the pages you use most within reach.
        </p>
        <label className="sp-sidebar-page-search">
          <Icon name="search" size={16} />
          <input
            autoFocus
            aria-label="Find pages"
            placeholder="Find a page…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <div className="sp-sidebar-page-list" aria-label="Choose pages">
          {filtered.map((page) => (
            <label key={page.key}>
              <input
                type="checkbox"
                checked={selected.includes(page.key)}
                onChange={(event) =>
                  setSelected((keys) =>
                    event.target.checked
                      ? [...keys, page.key]
                      : keys.filter((key) => key !== page.key),
                  )
                }
              />
              <PageIcon pageKey={page.key} fallback={page.icon} size={18} />
              <span>
                {page.title}
                <small>{page.type}</small>
              </span>
            </label>
          ))}
          {!filtered.length && (
            <p>
              No matching pages. Create a page in your workspace to add it here.
            </p>
          )}
        </div>
        <div className="sp-modal-footer sp-sidebar-dialog-footer">
          <span className="sp-sidebar-local-note">Favorites are saved in this browser.</span>
          <div className="sp-actions">
            <button type="button" onClick={onClose}>
              Cancel
            </button>
            <button className="sp-primary" type="submit">
              Save
            </button>
          </div>
        </div>
      </form>
    </Modal>
  );
}

export function PageOptions({
  page,
  onClose,
}: {
  page: SidebarPage;
  onClose: () => void;
}) {
  const { preferences, update } = useSidebarPreferences();
  const icon = preferences.icons[page.key];
  const favorite = preferences.favorites.includes(page.key);
  return (
    <Modal
      title="Customize page"
      onClose={onClose}
      className="sp-sidebar-dialog sp-page-options"
    >
      <div className="sp-page-options-title">
        <PageIcon pageKey={page.key} fallback={page.icon} size={23} />
        <strong>{page.title}</strong>
      </div>
      <PageIconPicker
        value={icon}
        fallback={page.icon}
        onChange={(choice) =>
          update((p) => {
            const icons = { ...p.icons };
            if (choice) icons[page.key] = choice;
            else delete icons[page.key];
            return { ...p, icons };
          })
        }
      />
      <button
        className={`sp-page-favorite-toggle ${favorite ? "is-favorite" : ""}`}
        aria-pressed={favorite}
        onClick={() =>
          update((p) => ({
            ...p,
            favorites: favorite
              ? p.favorites.filter((key) => key !== page.key)
              : [...p.favorites, page.key],
            collapsed: { ...p.collapsed, favorites: false },
          }))
        }
      >
        <Icon name="star" size={18} />
        {favorite ? "Remove from favorites" : "Add to favorites"}
        <Icon name={favorite ? "check" : "plus"} size={15} />
      </button>
      <div className="sp-modal-footer sp-sidebar-dialog-footer">
        <p className="sp-sidebar-local-note">
          Icons and shortcuts are saved in this browser.
        </p>
        <button className="sp-primary" onClick={onClose}>
          Done
        </button>
      </div>
    </Modal>
  );
}

export function DocumentIconButton({
  id,
  title,
  fallback = "document",
}: {
  id: string;
  title: string;
  fallback?: IconName;
}) {
  const data = useSpace();
  const [open, setOpen] = useState(false);
  const page: SidebarPage = {
    key: `document:${id}`,
    title,
    href: `/app/documents/${id}`,
    icon: fallback,
    type: "Document",
  };
  return (
    <>
      <button
        className="sp-doc-reader-icon sp-document-icon-button"
        aria-label="Change page icon"
        title="Change icon or favorite"
        onClick={() => setOpen(true)}
      >
        <PageIcon pageKey={page.key} fallback={fallback} size={29} />
        <span className="sp-icon-edit-hint">
          <Icon name="edit" size={11} />
        </span>
      </button>
      {open && <PageOptions page={page} onClose={() => setOpen(false)} />}
    </>
  );
}

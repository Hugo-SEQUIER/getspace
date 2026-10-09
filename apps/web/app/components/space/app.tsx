import "../../workspace-polish.css";
import { teamsAvailable } from "../../../lib/features";
import { useWorkspaceNotifications } from "./workspace-notifications";
import { WorkspacePicker } from "./team-settings";
import Link, { useRouter } from "@web/lib/navigation";
import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Unlock } from "../unlock";
import { Icon } from "./ui-icon";
import { useSidebarPreferences } from "./sidebar-preferences";
import { SidebarNavigation } from "./sidebar-navigation";
import { useSpace } from "./provider";
import { CommandPalette } from "./command-palette";
import { Avatar, Modal, ConfirmProvider } from "./ui";
import { ShellActionsContext } from "./shell-actions";
import { message } from "../../../lib/client";
import { docsLinks, sourceCode } from "../../../lib/docs-links";
import { Wordmark } from "./brand";
import { SyncStatus } from "./sync-status";

const ResourceForm = lazy(() =>
  import("./resource-views").then((module) => ({ default: module.ResourceForm })),
);

export function SpaceApp({
  path = [],
  children,
}: {
  path?: string[];
  children: ReactNode;
}) {
  const data = useSpace(),
    router = useRouter();
  useWorkspaceNotifications({
    error: data.error || data.pageError || "",
    notice: data.notice,
    loading: data.loading ? "Opening your space…" : data.pageLoading ? "Loading page…" : undefined,
    onDismissError: () => data.setError(""),
    onDismissNotice: () => data.setNotice(""),
  });
  const { preferences, update: updateSidebar } = useSidebarPreferences();
  const sidebarCollapsed = preferences.sidebarCollapsed;
  const base = "/app",
    view = path[0] ?? "home",
    id = path[1];
  const [mobileNav, setMobileNav] = useState(false),
    [accountOpen, setAccountOpen] = useState(false);
  const [command, setCommand] = useState(false);
  const [compose, setCompose] = useState<"document" | "task" | null>(null);
  const [searchShortcut, setSearchShortcut] = useState("Ctrl K");
  const onSearch = useCallback(() => setCommand(true), []);
  const shellActions = useMemo(
    () => ({ onSearch, searchShortcut }),
    [onSearch, searchShortcut],
  );
  const accountRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (/Mac|iPhone|iPad/.test(navigator.platform)) setSearchShortcut("⌘ K");
  }, []);
  useEffect(() => {
    if (!accountOpen) return;
    const dismiss = (event: PointerEvent) => {
      if (!accountRef.current?.contains(event.target as Node))
        setAccountOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setAccountOpen(false);
      accountRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
    };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", escape);
    };
  }, [accountOpen]);
  useEffect(() => {
    const shortcut = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCommand((open) => !open);
      }
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, []);
  useEffect(() => {
    setMobileNav(false);
    setAccountOpen(false);
  }, [view, id]);
  useEffect(() => {
    const desktop = window.matchMedia("(min-width: 761px)");
    const closeOnDesktop = () => {
      if (desktop.matches) setMobileNav(false);
    };
    desktop.addEventListener("change", closeOnDesktop);
    return () => desktop.removeEventListener("change", closeOnDesktop);
  }, []);
  useEffect(() => {
    if (!mobileNav) return;
    document.querySelector<HTMLElement>(".sp-sidebar a")?.focus();
    const navigate = (event: KeyboardEvent) => {
      if (document.querySelector("dialog[open]")) return;
      if (event.key === "Escape") {
        setMobileNav(false);
        document
          .querySelector<HTMLElement>('[aria-label="Open navigation"]')
          ?.focus();
      }
      if (event.key !== "Tab") return;
      const items = [
        ...document.querySelectorAll<HTMLElement>(
          ".sp-sidebar a[href], .sp-sidebar button:not([disabled])",
        ),
      ].filter((element) => element.offsetParent !== null);
      const first = items[0],
        last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener("keydown", navigate);
    return () => document.removeEventListener("keydown", navigate);
  }, [mobileNav]);
  if (!data.space)
    return (
      <main className="sp-auth-page">
        <Link href="/app" prefetch={false} className="sp-auth-brand">
          <Wordmark />
        </Link>
        {data.locked ? (
          <Unlock onUnlocked={() => void data.initialize()} />
        ) : (
          <div className="sp-auth-loading">
            {data.loading ? (
              <>
                <span className="sp-loading-orbit" />
                <h1>Opening your space…</h1>
                <p>Your work is right where you left it.</p>
              </>
            ) : (
              <>
                <h1>Let’s open your space.</h1>
                <p>Try connecting to your workspace again.</p>
                <button
                  className="sp-primary"
                  onClick={() => void data.initialize()}
                >
                  Try again
                </button>
              </>
            )}
          </div>
        )}
      </main>
    );

  return (
    <div
      className={`sp-app ${data.compact ? "sp-compact" : ""} ${sidebarCollapsed ? "sp-sidebar-collapsed" : ""}`}
    >
      <ConfirmProvider>
      <a className="sp-skip-link" href="#main-content">
        Skip to content
      </a>
      <div className="sp-mobile-bar">
        <button
          className="sp-icon-button"
          aria-label="Open navigation"
          aria-expanded={mobileNav}
          aria-controls="workspace-navigation"
          onClick={() => setMobileNav(true)}
        >
          <Icon name="menu" />
        </button>
        <Wordmark />
        <button
          className="sp-icon-button"
          aria-label="Search"
          onClick={() => setCommand(true)}
        >
          <Icon name="search" />
        </button>
      </div>
      {mobileNav && (
        <button
          className="sp-sidebar-backdrop"
          aria-label="Close navigation"
          onClick={() => setMobileNav(false)}
        />
      )}
      <aside
        id="workspace-navigation"
        className={`sp-sidebar ${mobileNav ? "is-open" : ""}`}
      >
        <div className="sp-sidebar-brand">
          <Link href={base} aria-label="Space home" title="Space home">
            <Wordmark />
          </Link>
          <button
            type="button"
            className="sp-icon-button sp-sidebar-toggle"
            aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-expanded={!sidebarCollapsed}
            aria-controls="workspace-navigation"
            onClick={() => {
              setAccountOpen(false);
              updateSidebar((previous) => ({
                ...previous,
                sidebarCollapsed: !previous.sidebarCollapsed,
              }));
            }}
          >
            <Icon
              name="chevron"
              size={17}
              style={{ transform: sidebarCollapsed ? undefined : "rotate(180deg)" }}
            />
          </button>
        </div>
        {/* One personal Space per account until teams launch: nothing to switch to. */}
        {teamsAvailable && <WorkspacePicker base={base} />}
        <SidebarNavigation base={base} view={view} id={id} />
        <div className="sp-sidebar-bottom">
          <Link className="sp-nav-link sp-setup-entry" href={`${base}?setup=1`} aria-label="Getting started" title="Getting started"><Icon name="sparkle" /><span>Getting started</span></Link>
          <SyncStatus />
          <Link
            className={`sp-nav-link ${view === "settings" ? "active" : ""}`}
            href={`${base}/settings/account`}
            aria-label="Settings"
            title="Settings"
          >
            <Icon name="settings" active={view === "settings"} />
            <span>Settings</span>
          </Link>
          <a
            className="sp-nav-link"
            href={docsLinks.index}
            target="_blank"
            rel="noreferrer"
            aria-label="Help & documentation"
            title="Help & documentation"
          >
            <Icon name="help" />
            <span>Help</span>
          </a>
          <div className="sp-account-wrap" ref={accountRef}>
            <button
              className="sp-account-button"
              aria-label="Account menu"
              aria-expanded={accountOpen}
              onClick={() => setAccountOpen(!accountOpen)}
            >
              <Avatar name={data.profile.displayName || "You"} />
              <span>
                {data.profile.displayName || "Owner"}
                <small>{data.space?.name ?? "Personal space"}</small>
              </span>
              <Icon name="down" size={13} />
            </button>
            {accountOpen && (
              <div className="sp-account-menu">
                <small>
                  {data.accessMode === "private"
                    ? "Open on a private network"
                    : "Unlocked with the access key"}
                </small>
                <Link href={`${base}/settings/account`}>
                  Account preferences
                </Link>
                <a href={sourceCode.href} target="_blank" rel="noreferrer">
                  {sourceCode.label}
                </a>
                {data.accessMode === "key" && (
                  <button
                    onClick={() =>
                      void data
                        .lock()
                        .catch((e) => data.setError(message(e)))
                    }
                  >
                    Lock
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
      </aside>
      <main
        className="sp-main"
        id="main-content"
        tabIndex={-1}
        inert={mobileNav}
      >
        <header className="sp-workspace-bar">
          <div className="sp-workspace-location"><Link href={base}>Workspace</Link><span aria-hidden="true">/</span><span>{view === "home" ? "Home" : view.charAt(0).toUpperCase() + view.slice(1)}</span>{!data.canWrite && <span className="sp-tag" title="You can read and search this Space.">View only</span>}</div>
          <button className="sp-workspace-search" onClick={onSearch} aria-label="Search your space"><Icon name="search" size={16} /><span>Search in Space…</span><kbd aria-hidden="true">{searchShortcut}</kbd></button>
          <button className="sp-icon-button sp-workspace-create" disabled={!data.canWrite} aria-label="Create document" title="New document" onClick={() => setCompose("document")}><Icon name="plus" size={19} /></button>
        </header>
        {data.pageError ? (
          <div className="sp-page">
            <button className="sp-button" onClick={data.retryPage} aria-label="Retry loading page">Try again</button>
          </div>
        ) : data.loading || data.pageLoading ? (
          <div className="sp-page" role="status" aria-label="Loading page">
            <div className="sp-skeleton" />
          </div>
        ) : (
          <ShellActionsContext.Provider value={shellActions}>
            {children}
          </ShellActionsContext.Provider>
        )}
      </main>
      {compose && (
        <Suspense
          fallback={
            <Modal title={compose === "task" ? "New task" : "New document"} onClose={() => setCompose(null)}>
              <p role="status">Opening editor…</p>
            </Modal>
          }
        >
          <ResourceForm
            kind={compose}
            onClose={() => setCompose(null)}
            onSaved={(r) => router.push(`${base}/${r.kind === "task" ? "tasks" : "documents"}/${r.id}`)}
          />
        </Suspense>
      )}
      {command && <CommandPalette base={base} onClose={() => setCommand(false)} onCreate={setCompose} />}
      </ConfirmProvider>
    </div>
  );
}

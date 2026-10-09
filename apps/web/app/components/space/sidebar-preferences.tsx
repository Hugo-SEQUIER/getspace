
import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useSpace } from "./provider";
import { Icon, type IconName } from "./ui-icon";
import { iconColors, readPageIcon, type PageIconChoice } from "./page-icons";

type Preferences = {
  sidebarCollapsed: boolean;
  collapsed: Record<string, boolean>;
  favorites: string[];
  icons: Record<string, PageIconChoice>;
};
export type SidebarPage = {
  key: string;
  title: string;
  href: string;
  icon: IconName;
  type: string;
};

function defaults(): Preferences {
  return {
    sidebarCollapsed: false,
    collapsed: {},
    icons: {},
    favorites: [],
  };
}

function readPreferences(raw: string): Preferences {
  const input = JSON.parse(raw);
  const fallback = defaults();
  if (!input || typeof input !== "object" || Array.isArray(input))
    return fallback;
  const record = (value: unknown): value is Record<string, unknown> =>
    !!value && typeof value === "object" && !Array.isArray(value);
  const keys = (value: unknown) =>
    Array.isArray(value)
      ? [
          ...new Set(
            value.filter(
              (key): key is string =>
                typeof key === "string" &&
                /^(document|database|project):.{1,200}$/.test(key),
            ),
          ),
        ]
      : [];
  const favorites = Array.isArray(input.favorites)
    ? keys(input.favorites)
    : fallback.favorites;
  const legacyShortcuts = Array.isArray(input.sections)
    ? input.sections.flatMap((section: unknown) =>
        record(section) ? keys(section.items) : [],
      )
    : [];
  return {
    sidebarCollapsed: input.sidebarCollapsed === true,
    favorites: [...new Set([...favorites, ...legacyShortcuts])],
    collapsed: record(input.collapsed)
      ? (Object.fromEntries(
          Object.entries(input.collapsed).filter(
            ([key, value]) =>
              typeof value === "boolean" &&
              (key === "workspace" || key === "favorites" ||
                key === "document-tree:folders" || key === "document-tree:unfiled" ||
                /^document-tree:(section|folder):.{1,200}$/.test(key)),
          ),
        ) as Record<string, boolean>)
      : {},
    icons: record(input.icons)
      ? Object.fromEntries(
          Object.entries(input.icons)
            .slice(0, 2000)
            .flatMap(([key, value]) => {
              const icon = readPageIcon(value);
              return keys([key]).length && icon ? [[key, icon]] : [];
            }),
        )
      : {},
  };
}

const PreferencesContext = createContext<{
  preferences: Preferences;
  update: (change: (previous: Preferences) => Preferences) => void;
  pages: SidebarPage[];
} | null>(null);

// Presentation preferences are browser-local, separate from versioned document content.
export function SidebarPreferencesProvider({
  children,
}: {
  children: ReactNode;
}) {
  const data = useSpace();
  const scope = `${encodeURIComponent(data.accountId ?? "anonymous")}:${encodeURIComponent(data.space?.id ?? "pending")}`;
  return (
    <PreferenceState key={scope} storageKey={`space-navigation-v1:${scope}`}>
      {children}
    </PreferenceState>
  );
}

function PreferenceState({
  storageKey,
  children,
}: {
  storageKey: string;
  children: ReactNode;
}) {
  const data = useSpace();
  const [preferences, setPreferences] = useState(defaults);
  const current = useRef(preferences);
  useEffect(() => {
    try {
      const saved = localStorage.getItem(storageKey);
      if (saved) {
        current.current = readPreferences(saved);
        const migrated = JSON.stringify(current.current);
        if (migrated !== saved) {
          try {
            localStorage.setItem(storageKey, migrated);
          } catch {
            data.setNotice(
              "Your sidebar changes are kept for this session. Browser storage is unavailable.",
            );
          }
        }
      }
    } catch {
      current.current = defaults();
    }
    setPreferences(current.current);
  }, [storageKey]);
  function update(change: (previous: Preferences) => Preferences) {
    const next = change(current.current);
    current.current = next;
    setPreferences(next);
    try {
      localStorage.setItem(storageKey, JSON.stringify(next));
    } catch {
      data.setNotice(
        "Your sidebar changes are kept for this session. Browser storage is unavailable.",
      );
    }
  }
  const base = "/app";
  const pages: SidebarPage[] = [
    ...data.resources
      .filter((r) => r.kind === "document" && r.recall?.category !== "memory")
      .map((r): SidebarPage => ({
        key: `document:${r.id}`,
        title: r.title,
        href: `${base}/documents/${r.id}`,
        type: "Document",
        icon: r.recall?.category === "handoff" ? "arrow" : "document",
      })),
    ...data.databases.map((d): SidebarPage => ({
      key: `database:${d.id}`,
      title: d.name,
      href: `${base}/databases/${d.id}`,
      type: "Database",
      icon: "database",
    })),
    ...data.projects.map((p): SidebarPage => ({
      key: `project:${p.id}`,
      title: p.name,
      href: `${base}/projects/${p.id}`,
      type: "Project",
      icon: "folder",
    })),
  ];
  return (
    <PreferencesContext.Provider value={{ preferences, update, pages }}>
      {children}
    </PreferencesContext.Provider>
  );
}

export function useSidebarPreferences() {
  const value = useContext(PreferencesContext);
  if (!value) throw new Error("Sidebar preferences require a provider");
  return value;
}

export function PageIcon({
  pageKey,
  fallback = "document",
  size = 18,
}: {
  pageKey: string;
  fallback?: IconName;
  size?: number;
}) {
  const { preferences } = useSidebarPreferences();
  const choice = preferences.icons[pageKey];
  if (choice && typeof choice !== "string")
    return (
      <Icon
        name={choice.name}
        size={size}
        className="sp-custom-page-icon"
        style={{ color: iconColors[choice.color].value, strokeWidth: 1.8 }}
      />
    );
  return choice ? (
    <span
      className="sp-page-emoji"
      aria-hidden="true"
      style={{ fontSize: size, width: size, height: size }}
    >
      {choice}
    </span>
  ) : (
    <Icon name={fallback} size={size} />
  );
}

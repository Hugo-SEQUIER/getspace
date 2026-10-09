import type { CSSProperties } from "react";

const paths = {
  grip: "M9 5h.01M15 5h.01M9 12h.01M15 12h.01M9 19h.01M15 19h.01",
  search: "M17 17l5 5M19 10A9 9 0 1 1 1 10a9 9 0 0 1 18 0",
  plus: "M12 5v14M5 12h14",
  edit: "m16 3 5 5L9 20l-6 1 1-6ZM14 5l5 5",
  trash: "M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7",
  chevron: "m9 5 7 7-7 7",
  down: "m6 9 6 6 6-6",
  close: "m6 6 12 12M6 18 18 6",
  lock: "M5 10h14v11H5Zm3 0V6a4 4 0 0 1 8 0v4",
  filter: "M3 4h18l-7 8v7l-4 2v-9Z",
  list: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
  board: "M3 4h7v16H3ZM14 4h7v10h-7Z",
  graph:
    "m6 7 6 10 7-12M3 5a2 2 0 1 0 4 0 2 2 0 0 0-4 0Zm7 14a2 2 0 1 0 4 0 2 2 0 0 0-4 0Zm7-16a2 2 0 1 0 4 0 2 2 0 0 0-4 0",
  arrow: "M4 12h16m-6-6 6 6-6 6",
  external: "M14 3h7v7M21 3 10 14M10 4H4v16h16v-6",
  check: "m5 12 4 4L19 6",
  clock: "M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0ZM12 6v6l4 2",
  upload: "M12 16V3m-5 5 5-5 5 5M4 15v6h16v-6",
  download: "M12 3v13m-5-5 5 5 5-5M4 16v5h16v-5",
  refresh: "M20 7A9 9 0 1 0 21 15M20 2v6h-6",
  menu: "M4 6h16M4 12h16M4 18h16",
  logout: "M9 3H3v18h6M10 12h12m-5-5 5 5-5 5",
  user: "M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0ZM4 21v-3c0-6 16-6 16 0v3Z",
  copy: "M9 8h12v13H9ZM15 8V2H3v13h6",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  fit: "M9 3H3v6m12-6h6v6M3 15v6h6m6 0h6v-6",
  minus: "M5 12h14",
  pause: "M8 5v14M16 5v14",
  play: "m8 4 12 8-12 8Z",
  star: "m12 2 3 6 7 1-5 5 1 7-6-3-6 3 1-7-5-5 7-1Z",
  book: "M12 5C9 3 5 3 2 4v15c3-1 7-1 10 1 3-2 7-2 10-1V4c-3-1-7-1-10 1Zm0 0v15",
  pin: "m16 3 5 5-3 1-4 4v4l-7-7h4l4-4ZM7 17l-4 4",
  rocket:
    "M12 15 9 12c2-6 6-9 12-9 0 6-3 10-9 12ZM9 12H4l3-5h5M12 15v5l5-3v-5M7 16c-3-1-4 2-4 5 3 0 6-1 5-4M15 8h.01",
  target:
    "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-4 0a5 5 0 1 1-10 0 5 5 0 0 1 10 0Zm-4 0a1 1 0 1 1-2 0 1 1 0 0 1 2 0",
  idea: "M9 18h6M9 21h6M8 15c0-3-3-3-3-7a7 7 0 0 1 14 0c0 4-3 4-3 7ZM12 15v-5m-3-2 3 2 3-2",
  brain:
    "M12 5c-3-5-8-2-7 2-4 1-4 6-1 8-1 5 5 8 8 4 3 4 9 1 8-4 3-2 3-7-1-8 1-4-4-7-7-2Zm0 0v14M5 7c2 0 3 1 3 3M4 15c2-1 4 0 4 2M19 7c-2 0-3 1-3 3M20 15c-2-1-4 0-4 2",
  flask: "M9 3h6M10 3v6L4 19a1 1 0 0 0 1 2h14a1 1 0 0 0 1-2L14 9V3M7 14h10",
  compass: "M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0ZM16 8l-3 5-5 3 3-5Z",
  leaf: "M20 3C7 2 2 8 5 15s15 4 15-12ZM4 21 16 9",
  palette:
    "M12 3a9 9 0 1 0 0 18h1a2 2 0 0 0 1-4c-1-1 0-3 2-3h2c5 0 4-11-6-11ZM7 8h.01M12 6h.01M17 8h.01M6 13h.01",
  code: "m8 5-6 7 6 7M16 5l6 7-6 7M14 3l-4 18",
  laptop: "M5 4h14v13H5ZM2 17h20v3H2Z",
  calendar:
    "M4 5h16v16H4ZM4 10h16M8 2v6M16 2v6M8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01",
  people:
    "M14 7a3 3 0 1 1-6 0 3 3 0 0 1 6 0ZM3 21v-3c0-6 16-6 16 0v3M18 4c4 1 4 5 0 6M19 14c3 0 4 3 3 7",
  briefcase: "M3 7h18v14H3ZM8 7V3h8v4M3 12c6 3 12 3 18 0M10 13v3h4v-3",
  chat: "M21 11a9 9 0 0 1-9 9H3l1-5a9 9 0 1 1 17-4ZM8 10h8M8 14h5",
  globe:
    "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM3 12h18M12 3c-5 5-5 13 0 18 5-5 5-13 0-18Z",
  sun: "M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0ZM12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5 19 19M5 19l1.5-1.5M17.5 6.5 19 5",
  moon: "M20 15A9 9 0 0 1 9 4a9 9 0 1 0 11 11Z",
  heart: "M12 21 3 12C-3 4 7-1 12 6c5-7 15-2 9 6Z",
  music:
    "M9 18V5l12-2v13M9 7l12-2M9 18c0 4-6 4-6 1s6-4 6-1ZM21 16c0 4-6 4-6 1s6-4 6-1Z",
  coffee:
    "M4 8h13v8c0 7-13 7-13 0ZM17 9h2c5 0 3 7-2 7M6 2v3M10 2v3M14 2v3M2 22h19",
  flag: "M4 22V3c5-4 11 4 16 0v11c-5 4-11-4-16 0",
  bolt: "m13 2-9 12h7l-1 8 10-12h-8Z",
  mail: "M3 5h18v14H3Zm0 0 9 8 9-8",
  card: "M3 5h18v14H3ZM3 10h18M7 15h4",
  camera: "M3 7h4l2-4h6l2 4h4v14H3Zm13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
  image: "M3 3h18v18H3ZM3 16l6-6 6 6 3-3 3 3M16 7h.01",
  headphones: "M3 14v-3a9 9 0 0 1 18 0v3M3 12h4v9H3ZM17 12h4v9h-4Z",
  link: "M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-2 2M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l2-2",
  cube: "m12 2 10 5v10l-10 5-10-5V7Zm0 10v10M2 7l10 5 10-5M7 4.5l10 5V15",
  trophy:
    "M7 3h10v7c0 7-10 7-10 0ZM7 5H3v3c0 4 4 4 4 4M17 5h4v3c0 4-4 4-4 4M12 15v6M7 21h10",
} as const;

// Core concept glyphs, shared by navigation and page content: a closed `shape` that fills
// softly when active, plus optional `detail` strokes. One glyph per concept across the app.
const glyphs = {
  home: { shape: "M4 10.2 12 4l8 6.2V19a1.5 1.5 0 0 1-1.5 1.5H15v-5a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v5H5.5A1.5 1.5 0 0 1 4 19Z" },
  tasks: {
    shape: "M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v11a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 17.5Z",
    detail: "m8.5 12.2 2.4 2.4 4.6-5",
  },
  document: {
    shape: "M6.5 3.5H13l5 5V19a1.5 1.5 0 0 1-1.5 1.5h-10A1.5 1.5 0 0 1 5 19V5a1.5 1.5 0 0 1 1.5-1.5Z",
    detail: "M13 3.5V8a.5.5 0 0 0 .5.5H18M8.5 12.5h7M8.5 16h5",
  },
  // Stacked layers: saved context building up, as in the public site's memory illustration.
  memory: { shape: "M12 3.5 20.5 8 12 12.5 3.5 8Z", detail: "M3.5 12 12 16.5 20.5 12M3.5 16 12 20.5 20.5 16" },
  database: {
    shape: "M4 6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2Z",
    detail: "M4 9.5h16M4 14.75h16M10 9.5V20",
  },
  chart: {
    shape: "M4 6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2Z",
    detail: "M8.5 16v-3M12 16V8.5M15.5 16v-5",
  },
  folder: { shape: "M3.5 7a2 2 0 0 1 2-2h3.6a2 2 0 0 1 1.5.7L11.7 7h6.8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2Z" },
  agents: {
    shape: "M4.5 10A3 3 0 0 1 7.5 7h9a3 3 0 0 1 3 3v7a3 3 0 0 1-3 3h-9a3 3 0 0 1-3-3Z",
    detail: "M12 7V4.5M12 3.25h.01M9.25 12.5v1.5M14.75 12.5v1.5M9.5 16.75h5M2.5 12v3M21.5 12v3",
  },
  sparkle: { shape: "M10.5 4.5 12.2 9.3 17 11l-4.8 1.7-1.7 4.8-1.7-4.8L4 11l4.8-1.7Z", detail: "M18 15v4.5M15.75 17.25h4.5" },
  settings: {
    shape: "M18.75 9.81 21.01 10.13 21.01 13.87 18.75 14.19 18.33 15.22 19.69 17.05 17.05 19.69 15.22 18.33 14.19 18.75 13.87 21.01 10.13 21.01 9.81 18.75 8.78 18.33 6.95 19.69 4.31 17.05 5.67 15.22 5.25 14.19 2.99 13.87 2.99 10.13 5.25 9.81 5.67 8.78 4.31 6.95 6.95 4.31 8.78 5.67 9.81 5.25 10.13 2.99 13.87 2.99 14.19 5.25 15.22 5.67 17.05 4.31 19.69 6.95 18.33 8.78Z",
    detail: "M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z",
  },
  // View and display options; the gear stays reserved for settings.
  sliders: { shape: "M13 7a2 2 0 1 0 4 0 2 2 0 0 0-4 0ZM7 17a2 2 0 1 0 4 0 2 2 0 0 0-4 0Z", detail: "M4 7h9M17 7h3M4 17h3M11 17h9" },
  help: {
    shape: "M12 20.5a8.5 8.5 0 1 0 0-17 8.5 8.5 0 0 0 0 17Z",
    detail: "M9.6 9.6a2.5 2.5 0 0 1 4.9.7c0 1.7-2.5 2.1-2.5 3.7M12 16.9h.01",
  },
} as const satisfies Record<string, { shape: string; detail?: string }>;
export type IconName = keyof typeof paths | keyof typeof glyphs;
export function Icon({
  name,
  size = 18,
  active = false,
  className,
  style,
}: {
  name: IconName;
  size?: number;
  active?: boolean;
  className?: string;
  style?: CSSProperties;
}) {
  const glyph: { shape: string; detail?: string } | undefined = name in glyphs ? glyphs[name as keyof typeof glyphs] : undefined;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={[glyph && "sp-glyph", active && "is-active", className].filter(Boolean).join(" ") || undefined}
      style={style}
    >
      {glyph ? <>
        <path className="sp-glyph-shape" d={glyph.shape} />
        {glyph.detail && <path d={glyph.detail} />}
      </> : <path d={paths[name as keyof typeof paths]} />}
    </svg>
  );
}

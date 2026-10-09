import type { IconName } from "./ui-icon";

export const svgPageIcons = [
  { name: "document", label: "Document", keywords: "page paper write" },
  { name: "edit", label: "Pencil", keywords: "notes draft writing" },
  { name: "book", label: "Book", keywords: "reading library knowledge" },
  { name: "folder", label: "Folder", keywords: "project files collection" },
  { name: "database", label: "Database", keywords: "table clients crm data" },
  { name: "calendar", label: "Calendar", keywords: "date schedule event" },
  { name: "tasks", label: "Checklist", keywords: "todo done task check" },
  { name: "board", label: "Board", keywords: "kanban tasks planning" },
  { name: "rocket", label: "Rocket", keywords: "launch startup ship" },
  { name: "target", label: "Target", keywords: "goals objective focus" },
  { name: "flag", label: "Flag", keywords: "milestone goal mark" },
  { name: "trophy", label: "Trophy", keywords: "achievement award win" },
  { name: "idea", label: "Lightbulb", keywords: "idea light brainstorm" },
  { name: "brain", label: "Brain", keywords: "memory mind knowledge cortex" },
  { name: "agents", label: "Sparkles", keywords: "agent ai magic" },
  { name: "graph", label: "Connections", keywords: "graph network links" },
  { name: "flask", label: "Flask", keywords: "research science experiment" },
  { name: "compass", label: "Compass", keywords: "explore direction travel" },
  { name: "leaf", label: "Leaf", keywords: "growth nature plant" },
  { name: "globe", label: "Globe", keywords: "world web internet" },
  { name: "palette", label: "Palette", keywords: "art design color creative" },
  { name: "image", label: "Image", keywords: "photo picture media" },
  { name: "camera", label: "Camera", keywords: "photo video film" },
  { name: "music", label: "Music", keywords: "audio song sound" },
  { name: "code", label: "Code", keywords: "development engineering software" },
  { name: "laptop", label: "Laptop", keywords: "computer technology" },
  { name: "settings", label: "Settings", keywords: "gear configuration tools" },
  { name: "cube", label: "Cube", keywords: "product package component" },
  { name: "chart", label: "Chart", keywords: "analytics metrics growth data" },
  {
    name: "briefcase",
    label: "Briefcase",
    keywords: "work business acquisition",
  },
  {
    name: "people",
    label: "People",
    keywords: "clients team contacts community",
  },
  { name: "user", label: "Person", keywords: "profile account client" },
  { name: "chat", label: "Conversation", keywords: "chat message feedback" },
  { name: "mail", label: "Mail", keywords: "email inbox outreach" },
  {
    name: "headphones",
    label: "Headphones",
    keywords: "audio podcast support",
  },
  { name: "link", label: "Link", keywords: "url bookmark connection" },
  { name: "home", label: "Home", keywords: "house personal" },
  { name: "pin", label: "Pin", keywords: "important saved" },
  { name: "star", label: "Star", keywords: "favorite priority" },
  { name: "heart", label: "Heart", keywords: "love health care" },
  { name: "sun", label: "Sun", keywords: "day light morning" },
  { name: "moon", label: "Moon", keywords: "night dark evening" },
  { name: "coffee", label: "Coffee", keywords: "break daily routine" },
  { name: "bolt", label: "Lightning", keywords: "energy quick power" },
  { name: "clock", label: "Clock", keywords: "time history meeting" },
  { name: "lock", label: "Lock", keywords: "private secure secret" },
  { name: "arrow", label: "Arrow", keywords: "handoff next direction forward" },
  { name: "list", label: "List", keywords: "outline notes items" },
] as const satisfies readonly {
  name: IconName;
  label: string;
  keywords: string;
}[];

export const iconColors = {
  default: { label: "Default", value: "#575752" },
  gray: { label: "Gray", value: "#858580" },
  brown: { label: "Brown", value: "#98745d" },
  orange: { label: "Orange", value: "#c47b38" },
  yellow: { label: "Yellow", value: "#b49432" },
  green: { label: "Green", value: "#4c8b68" },
  blue: { label: "Blue", value: "#4e86b5" },
  purple: { label: "Purple", value: "#9270ba" },
  pink: { label: "Pink", value: "#bc7094" },
  red: { label: "Red", value: "#c26863" },
} as const;
export type PageIconColor = keyof typeof iconColors;
export type SvgPageIcon = (typeof svgPageIcons)[number]["name"];
export type PageIconChoice =
  string | { type: "svg"; name: SvgPageIcon; color: PageIconColor };

// Keep previously saved emoji strings readable; SVG choices store only allowed IDs.
export function readPageIcon(value: unknown): PageIconChoice | undefined {
  if (typeof value === "string")
    return emojiPageIcons.some(([emoji]) => emoji === value)
      ? value
      : undefined;
  if (!value || typeof value !== "object" || Array.isArray(value)) return;
  const entry = value as Record<string, unknown>;
  if (
    entry.type !== "svg" ||
    !svgPageIcons.some((icon) => icon.name === entry.name) ||
    typeof entry.color !== "string" ||
    !Object.hasOwn(iconColors, entry.color)
  )
    return;
  return {
    type: "svg",
    name: entry.name as SvgPageIcon,
    color: entry.color as PageIconColor,
  };
}

export const emojiPageIcons = [
  ["📄", "Page"],
  ["📝", "Notes"],
  ["📚", "Books"],
  ["📌", "Pin"],
  ["🚀", "Rocket"],
  ["🎯", "Target"],
  ["💡", "Idea"],
  ["✨", "Sparkles"],
  ["🧠", "Brain"],
  ["🔬", "Research"],
  ["🧭", "Compass"],
  ["🌱", "Growth"],
  ["🎨", "Art"],
  ["💻", "Computer"],
  ["🛠️", "Tools"],
  ["⚙️", "Settings"],
  ["📊", "Chart"],
  ["🗂️", "Files"],
  ["📅", "Calendar"],
  ["✅", "Check"],
  ["👥", "People"],
  ["🤝", "Handshake"],
  ["💼", "Work"],
  ["💬", "Conversation"],
  ["🏡", "Home"],
  ["🌍", "World"],
  ["☀️", "Sun"],
  ["🌙", "Moon"],
  ["❤️", "Heart"],
  ["⭐", "Star"],
  ["🎵", "Music"],
  ["☕", "Coffee"],
] as const;

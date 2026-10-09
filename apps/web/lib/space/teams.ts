import type { Space } from "./types";

export type TeamRole = "owner" | "admin" | "member" | "viewer";
export type SpaceVisibility = "private" | "restricted" | "shared";
export type TeamMember = {
  id: string;
  name: string;
  role: TeamRole;
  is_self: boolean;
};
export type TeamMembers = {
  items: TeamMember[];
  role: TeamRole;
  default_space_id: string;
};
export type TeamInvitation = { id: string; role: TeamRole; expires_at: string };
export const selectedSpaceKey = "space-selected-workspace";
export const pendingInvitationKey = "space-pending-invitation";

export function invitationToken(value: string): string | null {
  const text = value.trim();
  if (/^[A-Za-z0-9_-]{43}$/.test(text)) return text;
  try {
    const url = new URL(text);
    const token = new URLSearchParams(url.hash.slice(1)).get("invite");
    return token && /^[A-Za-z0-9_-]{43}$/.test(token) ? token : null;
  } catch {
    return null;
  }
}

export function selectWorkspace(
  spaces: Space[],
  requested: string | null,
  personal: Space,
): Space {
  return spaces.find((space) => space.id === requested) ?? personal;
}

export function visibilityLabel(value?: SpaceVisibility) {
  return value === "shared"
    ? "Everyone in the organization"
    : value === "restricted"
      ? "Selected people"
      : "Only you";
}

export function scopedAgentPath(path: string, spaceId: string | null): string {
  if (!spaceId || !/^(agents|agent-identities)(\/|\?|$)/.test(path))
    return path;
  const [pathname, query = ""] = path.split("?");
  const params = new URLSearchParams(query);
  if (!params.has("workspace_id")) params.set("workspace_id", spaceId);
  return `${pathname}?${params}`;
}

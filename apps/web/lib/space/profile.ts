// Display-only details of the owner; they never determine ownership or permissions.
export type Profile = { displayName: string; avatarUrl: string | null };

export function avatarInitials(name: string): string {
  const words = name
    .split("@")[0]
    .trim()
    .split(/[\s._-]+/)
    .filter(Boolean);
  return words.length > 1
    ? `${Array.from(words[0])[0]}${Array.from(words.at(-1)!)[0]}`.toUpperCase()
    : Array.from(words[0] ?? "?")
        .slice(0, 1)
        .join("")
        .toUpperCase();
}

export function avatarTone(name: string): number {
  return Array.from(name.trim().toLowerCase()).reduce(
    (hash, character) => (hash * 31 + character.codePointAt(0)!) % 6,
    0,
  );
}


import Link from "@web/lib/navigation";
import { useEffect, useRef, useState } from "react";
import { useFeedback } from "@web/app/components/notifications";
import { teamsAvailable } from "../../../lib/features";
import { api, message } from "../../../lib/client";
import type { Space } from "../../../lib/space/types";
import {
  invitationToken,
  pendingInvitationKey,
  visibilityLabel,
  type SpaceVisibility,
  type TeamInvitation,
  type TeamMember,
  type TeamMembers,
} from "../../../lib/space/teams";
import { useSpace } from "./provider";
import { Icon } from "./ui-icon";
import styles from "./team-settings.module.css";
import { useConfirm } from "./ui";

export function WorkspacePicker({ base }: { base: string }) {
  const data = useSpace();
  const [open, setOpen] = useState(false);
  const picker = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const dismiss = (event: PointerEvent) => {
      if (!picker.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      trigger.current?.focus({ preventScroll: true });
    };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  const workspaces = data.workspaces.length ? data.workspaces : data.space ? [data.space] : [];
  const groups = [...new Set(workspaces.map((space) => space.organization_id))];
  return (
    <div
      ref={picker}
      className={styles.picker}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
    >
      <button
        ref={trigger}
        className="sp-space-picker"
        type="button"
        aria-expanded={open}
        aria-controls="space-switcher"
        onClick={() => setOpen(!open)}
      >
        <Icon
          name={data.space?.visibility === "private" ? "lock" : data.space?.kind === "team" ? "people" : "user"}
          size={16}
        />
        <span>
          {data.space?.name}
          <small>
            {data.space?.kind === "team"
              ? visibilityLabel(data.space.visibility)
              : "Personal workspace"}
          </small>
        </span>
        <Icon name="down" size={13} />
      </button>
      {open && (
        <div
          id="space-switcher"
          className={styles.switcher}
          role="region"
          aria-label="Switch workspace"
        >
          {groups.map((org) => {
            const spaces = workspaces.filter(
              (space) => space.organization_id === org,
            );
            const home = spaces.find((space) => space.is_default) ?? spaces[0];
            return (
              <section key={org ?? "personal"}>
                <h3>{home?.kind === "team" ? home.name : "Personal"}</h3>
                {spaces.map((space) => (
                  <button
                    type="button"
                    key={space.id}
                    aria-current={
                      space.id === data.space?.id ? "true" : undefined
                    }
                    onClick={() => {
                      setOpen(false);
                      if (space.id !== data.space?.id) data.switchWorkspace(space.id);
                    }}
                  >
                    <Icon
                      name={space.visibility === "private" ? "lock" : "folder"}
                      size={15}
                    />
                    <span>
                      {space.name}
                      <small>{visibilityLabel(space.visibility)}</small>
                    </span>
                    {space.id === data.space?.id && (
                      <Icon name="check" size={14} />
                    )}
                  </button>
                ))}
              </section>
            );
          })}
          {teamsAvailable && (
            <Link href={`${base}/settings/team`} onClick={() => setOpen(false)}>
              <Icon name="plus" size={15} /> Create or join an organization
            </Link>
          )}
          <Link href={`${base}/settings/space`} onClick={() => setOpen(false)}>
            <Icon name="settings" size={15} /> Space access & settings
          </Link>
        </div>
      )}
    </div>
  );
}

function AccessFields({
  visibility,
  setVisibility,
  selected,
  setSelected,
  members,
}: {
  visibility: SpaceVisibility;
  setVisibility: (value: SpaceVisibility) => void;
  selected: string[];
  setSelected: (value: string[]) => void;
  members: TeamMember[];
}) {
  return (
    <>
      <label>
        Who can access this Space?
        <select
          value={visibility}
          onChange={(event) => {
            setVisibility(event.target.value as SpaceVisibility);
            setSelected([]);
          }}
        >
          <option value="private">Only me</option>
          <option value="restricted">Selected people</option>
          <option value="shared">Everyone in the organization</option>
        </select>
      </label>
      {visibility === "restricted" && (
        <fieldset className={styles.people}>
          <legend>Select people</legend>
          {members
            .filter((member) => !member.is_self)
            .map((member) => (
              <label key={member.id}>
                <input
                  type="checkbox"
                  checked={selected.includes(member.id)}
                  onChange={(event) =>
                    setSelected(
                      event.target.checked
                        ? [...selected, member.id]
                        : selected.filter((id) => id !== member.id),
                    )
                  }
                />
                <span>{member.name}</span>
                <small>
                  {member.role === "viewer" ? "Can view" : "Can edit"}
                </small>
              </label>
            ))}
          {!members.some((member) => !member.is_self) && (
            <p>Invite people to your organization to share with them.</p>
          )}
        </fieldset>
      )}
      <p className="sp-muted">
        Documents, tasks, databases and memory inherit this access. Your
        personal workspace remains separate.
      </p>
    </>
  );
}

export function TeamSettings() {
  const confirm = useConfirm();
  const data = useSpace();
  const homes = data.workspaces.filter(
    (space) => space.kind === "team" && space.is_default,
  );
  const [organization, setOrganization] = useState(
    data.space?.kind === "team"
      ? data.space.organization_id!
      : (homes[0]?.organization_id ?? ""),
  );
  const [roster, setRoster] = useState<TeamMembers | null>(null);
  const [invitations, setInvitations] = useState<TeamInvitation[]>([]);
  const [revision, setRevision] = useState(0);
  const [name, setName] = useState("");
  const [displayName, setDisplayName] = useState(
    data.profile.displayName || "",
  );
  const [joinLink, setJoinLink] = useState("");
  const [spaceName, setSpaceName] = useState("");
  const [visibility, setVisibility] = useState<SpaceVisibility>("private");
  const [selected, setSelected] = useState<string[]>([]);
  const [inviteRole, setInviteRole] = useState("member");
  const [inviteLink, setInviteLink] = useState("");
  const [busy, setBusy] = useState(false);
  const [operation, setOperation] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  useFeedback({
    error,
    notice,
    loading: busy ? operation : loading && organization ? "Loading organization members…" : undefined,
    clearError: () => setError(""),
    clearNotice: () => setNotice(""),
  });
  const commandIds = useRef(new Map<string, string>());
  async function createOnce(path: string, body: object) {
    const key = JSON.stringify({ path, body });
    if (!commandIds.current.has(key))
      commandIds.current.set(key, crypto.randomUUID());
    return api<Space>(path, "POST", {
      ...body,
      command_id: commandIds.current.get(key),
    });
  }
  const isAdmin = roster?.role === "owner" || roster?.role === "admin";
  useEffect(() => {
    try {
      setJoinLink(sessionStorage.getItem(pendingInvitationKey) ?? "");
    } catch {}
  }, []);
  useEffect(() => {
    if (!organization) return;
    let canceled = false;
    setLoading(true);
    setRoster(null);
    setInvitations([]);
    setSelected([]);
    setInviteLink("");
    setError("");
    void (async () => {
      try {
        const members = await api<TeamMembers>(
          `organizations/${organization}/members`,
        );
        const invitations = ["owner", "admin"].includes(members.role)
          ? await api<{ items: TeamInvitation[] }>(
              `organizations/${organization}/invitations`,
            )
          : { items: [] };
        if (!canceled) {
          setRoster(members);
          setInvitations(invitations.items);
        }
      } catch (error) {
        if (!canceled) setError(message(error));
      } finally {
        if (!canceled) setLoading(false);
      }
    })();
    return () => {
      canceled = true;
    };
  }, [organization, revision]);

  async function run(label: string, action: () => Promise<void>) {
    if (busy) return;
    setOperation(label);
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
    }
  }
  const orgPath = `organizations/${organization}`;
  return (
    <div className={styles.settings}>
      <h1>Team & organization</h1>
      <p className="sp-subtitle">
        A shared home for your team, with room for private work.
      </p>
      <fieldset disabled={busy} className={styles.form}>
        <section>
          <h2>Your name</h2>
          <p className="sp-muted">
            Used when creating or joining an organization.
          </p>
          <label>
            Display name
            <input
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              maxLength={100}
              placeholder="Your name"
              autoComplete="name"
            />
          </label>
        </section>
        {homes.length > 0 && (
          <section>
            <label>
              Organization
              <select
                value={organization}
                onChange={(event) => setOrganization(event.target.value)}
              >
                {homes.map((home) => (
                  <option
                    key={home.organization_id}
                    value={home.organization_id}
                  >
                    {home.name}
                  </option>
                ))}
              </select>
            </label>
            {roster && (
              <>
                <h2>
                  Members{" "}
                  <span className={styles.count}>{roster.items.length}</span>
                </h2>
                <p className="sp-muted">
                  Members see Spaces shared with everyone and Spaces shared with
                  them. Private Spaces are visible only to their creator.
                </p>
                <ul className={styles.roster}>
                  {roster.items.map((member) => {
                    const canManage =
                      member.role !== "owner" &&
                      (roster.role === "owner" ||
                        (roster.role === "admin" &&
                          ["member", "viewer"].includes(member.role)));
                    return (
                      <li key={member.id}>
                        <span className={styles.avatar}>
                          {member.name.slice(0, 1).toUpperCase()}
                        </span>
                        <strong>
                          {member.name}
                          {member.is_self && <small> (you)</small>}
                        </strong>
                        {canManage ? (
                          <select
                            aria-label={`Role for ${member.name}`}
                            value={member.role}
                            onChange={(event) =>
                              void run("Updating member role…", async () => {
                                await api(
                                  `${orgPath}/members/${member.id}`,
                                  "POST",
                                  { role: event.target.value },
                                );
                                setRevision((value) => value + 1);
                                setNotice("Member role updated.");
                              })
                            }
                          >
                            {roster.role === "owner" && (
                              <option value="admin">Admin</option>
                            )}
                            <option value="member">Member</option>
                            <option value="viewer">Viewer</option>
                          </select>
                        ) : (
                          <span className={styles.role}>{member.role}</span>
                        )}
                        {member.role !== "owner" &&
                          (canManage || member.is_self) && (
                            <button
                              type="button"
                              className="sp-text-button"
                              onClick={async () => {
                                if (
                                  !(await confirm({
                                    title: member.is_self ? "Leave organization?" : "Remove member?",
                                    message: member.is_self
                                      ? "Leave this organization? You will lose access to its Spaces."
                                      : `Remove ${member.name} and revoke their agent connections?`,
                                    confirmLabel: member.is_self ? "Leave organization" : "Remove member",
                                  }))
                                )
                                  return;
                                void run(member.is_self ? "Leaving organization…" : "Removing member…", async () => {
                                  await api(
                                    `${orgPath}/members/${member.id}`,
                                    "DELETE",
                                  );
                                  if (member.is_self)
                                    data.switchWorkspace(
                                      data.workspaces.find(
                                        (space) => space.kind === "personal",
                                      )!.id,
                                    );
                                  else setRevision((value) => value + 1);
                                  setNotice(member.is_self ? "You left the organization." : "Member removed.");
                                });
                              }}
                            >
                              {member.is_self ? "Leave" : "Remove"}
                            </button>
                          )}
                      </li>
                    );
                  })}
                </ul>
                <p className="sp-muted">
                  Admins manage invitations and members. Members can edit
                  accessible Spaces. Viewers can read. Only the owner can
                  appoint admins.
                </p>
              </>
            )}
          </section>
        )}
        {roster && isAdmin && (
          <section>
            <h2>Invite people</h2>
            <p>
              Each invitation link admits one signed-in person and expires after
              seven days. Share it with the person you want to invite.
            </p>
            <div className={styles.inline}>
              <label>
                Invite as
                <select
                  value={inviteRole}
                  onChange={(event) => setInviteRole(event.target.value)}
                >
                  <option value="member">Member</option>
                  <option value="viewer">Viewer</option>
                  {roster.role === "owner" && (
                    <option value="admin">Admin</option>
                  )}
                </select>
              </label>
              <button
                type="button"
                className="sp-primary"
                onClick={() =>
                  void run("Creating invitation link…", async () => {
                    const invitation = await api<{ url: string }>(
                      `${orgPath}/invitations`,
                      "POST",
                      { role: inviteRole },
                    );
                    setInviteLink(invitation.url);
                    const result = await api<{ items: TeamInvitation[] }>(
                      `${orgPath}/invitations`,
                    );
                    setInvitations(result.items);
                    setNotice("Invitation link created.");
                  })
                }
              >
                Create invitation link
              </button>
            </div>
            {inviteLink && (
              <div className={styles.copy}>
                <label>
                  Invitation link
                  <input
                    readOnly
                    value={inviteLink}
                    onFocus={(event) => event.target.select()}
                  />
                </label>
                <button
                  type="button"
                  className="sp-button"
                  onClick={() =>
                    void run("Copying invitation link…", async () => {
                      await navigator.clipboard.writeText(inviteLink);
                      setNotice("Invitation link copied.");
                    })
                  }
                >
                  Copy link
                </button>
              </div>
            )}
            {invitations.length > 0 && (
              <ul className={styles.invites}>
                {invitations.map((invitation) => (
                  <li key={invitation.id}>
                    <span>
                      {invitation.role} invitation{" "}
                      <small>
                        Expires{" "}
                        {new Date(invitation.expires_at).toLocaleDateString()}
                      </small>
                    </span>
                    <button
                      type="button"
                      className="sp-text-button"
                      onClick={() =>
                        void run("Revoking invitation…", async () => {
                          await api(
                            `${orgPath}/invitations/${invitation.id}`,
                            "DELETE",
                          );
                          setInvitations((items) =>
                            items.filter((item) => item.id !== invitation.id),
                          );
                          setInviteLink("");
                          setNotice("Invitation revoked.");
                        })
                      }
                    >
                      Revoke
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}
        {roster && roster.role !== "viewer" && (
          <section>
            <h2>Create a Space</h2>
            <p>Keep a project, a department, or your private notes together.</p>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void run("Creating Space…", async () => {
                  const space = await createOnce(`${orgPath}/spaces`, {
                    name: spaceName.trim(),
                    visibility,
                    member_ids: selected,
                  });
                  data.switchWorkspace(space.id);
                });
              }}
            >
              <label>
                Space name
                <input
                  required
                  value={spaceName}
                  onChange={(event) => setSpaceName(event.target.value)}
                  maxLength={100}
                  placeholder="e.g. Product research"
                />
              </label>
              <AccessFields
                visibility={visibility}
                setVisibility={setVisibility}
                selected={selected}
                setSelected={setSelected}
                members={roster.items}
              />
              <button className="sp-primary" disabled={!spaceName.trim()}>
                Create Space
              </button>
            </form>
          </section>
        )}
        <section>
          <h2>Create an organization</h2>
          <p>
            Your team starts with a Space shared with everyone. Add private or
            restricted Spaces as you need them.
          </p>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void run("Creating organization…", async () => {
                const space = await createOnce("organizations", {
                  name: name.trim(),
                  display_name: displayName.trim(),
                });
                data.switchWorkspace(space.id, "/app/settings/team");
              });
            }}
          >
            <label>
              Organization name
              <input
                required
                value={name}
                onChange={(event) => setName(event.target.value)}
                maxLength={100}
                placeholder="e.g. Acme Studio"
              />
            </label>
            <button
              className="sp-primary"
              disabled={!name.trim() || !displayName.trim()}
            >
              Create organization
            </button>
          </form>
        </section>
        <section>
          <h2>Join an organization</h2>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void run("Joining organization…", async () => {
                const token = invitationToken(joinLink);
                if (!token) throw new Error("Paste a valid invitation link.");
                const space = await api<Space>("invitations/accept", "POST", {
                  token,
                  display_name: displayName.trim(),
                });
                try {
                  sessionStorage.removeItem(pendingInvitationKey);
                } catch {}
                data.switchWorkspace(space.id, "/app/settings/team");
              });
            }}
          >
            <label>
              Invitation link
              <input
                required
                value={joinLink}
                onChange={(event) => setJoinLink(event.target.value)}
                placeholder="Paste the link shared with you"
                autoComplete="off"
              />
            </label>
            <button
              className="sp-primary"
              disabled={!invitationToken(joinLink) || !displayName.trim()}
            >
              Accept invitation
            </button>
          </form>
        </section>
      </fieldset>
    </div>
  );
}

export function SpaceAccessSettings() {
  const data = useSpace();
  const space = data.space;
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [visibility, setVisibility] = useState<SpaceVisibility>(
    space?.visibility ?? "private",
  );
  const [selected, setSelected] = useState<string[]>([]);
  const [canManage, setCanManage] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  useFeedback({
    error,
    loading: busy ? "Saving Space access…" : loading && space?.kind === "team" ? "Loading Space access…" : undefined,
    clearError: () => setError(""),
  });
  useEffect(() => {
    if (space?.kind !== "team") return;
    let canceled = false;
    setLoading(true);
    void Promise.all([
      api<TeamMembers>(`organizations/${space.organization_id}/members`),
      api<{ member_ids: string[]; can_manage: boolean }>(
        `spaces/${space.id}/access`,
      ),
    ])
      .then(([roster, access]) => {
        if (!canceled) {
          setMembers(roster.items);
          setSelected(access.member_ids);
          setCanManage(access.can_manage && !space.is_default);
        }
      })
      .catch((error) => {
        if (!canceled) setError(message(error));
      })
      .finally(() => {
        if (!canceled) setLoading(false);
      });
    return () => {
      canceled = true;
    };
  }, [space]);
  return (
    <section className={styles.settings}>
      <h2>Who has access</h2>
      <p>
        <strong>
          {space?.kind === "team"
            ? visibilityLabel(space.visibility)
            : "You and the agents you authorize"}
        </strong>
      </p>
      {space?.kind === "team" && (
        <p className="sp-muted">
          Your access: {space.role === "viewer" ? "Can view" : "Can edit"}.
          Documents and memory use this Space’s access.
        </p>
      )}
      {canManage && (
        <form
          className={styles.form}
          onSubmit={(event) => {
            event.preventDefault();
            setBusy(true);
            setError("");
            void api<Space>(`spaces/${space!.id}/access`, "POST", {
              visibility,
              member_ids: selected,
            })
              .then(() =>
                data.switchWorkspace(space!.id, "/app/settings/space"),
              )
              .catch((error) => {
                setError(message(error));
                setBusy(false);
              });
          }}
        >
          <fieldset disabled={busy} className={styles.form}>
            <AccessFields
              visibility={visibility}
              setVisibility={setVisibility}
              selected={selected}
              setSelected={setSelected}
              members={members}
            />
            <p className="sp-muted">
              Saving access revokes this Space’s agent connections. Reconnect
              agents that should keep access.
            </p>
            <button className="sp-primary">Save access</button>
          </fieldset>
        </form>
      )}
    </section>
  );
}

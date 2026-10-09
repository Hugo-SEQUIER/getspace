# Teams and shared Spaces

The backend has an organization model: organizations with members and roles, several Spaces per organization, invitations and per-Space access. A self-hosted installation has one person, the owner, and no user accounts, so there is nobody to invite and no second account to share with. The website therefore hides all of it: `teamsAvailable` in `apps/web/lib/features.ts` is off, which removes the Space switcher and the organization settings. This page records what the backend still does, for contributors.

## Organizations and Spaces

The owner's personal organization holds the Private Space, created at the API's first start. `POST /v1/organizations` creates a team organization whose first Space is shared with all of its members, and `POST /v1/organizations/{id}/spaces` adds further Spaces. Each Space has one of three access settings:

| Access | Who can read and write its content |
|---|---|
| Only me | The Space's creator, while they remain in the organization. Other members and organization admins cannot read it. |
| Selected people | The creator and the selected active members. |
| Everyone in the organization | Every active member, including members who join later. |

Documents, tasks, projects, databases, history, search, exports and memory inherit their Space's access. There are no per-document permissions and no transfers between Spaces. A Space's creator controls its sharing; an organization's first Space always stays shared with everyone.

## Roles

| Organization role | Content | Administration |
|---|---|---|
| Owner | Read and write; private and restricted Spaces still require content access | Invite any role; change or remove other members |
| Admin | Read and write; private and restricted Spaces still require content access | Invite, change or remove members and viewers |
| Member | Read and write | Create Spaces; manage access to the Spaces they create; leave the organization |
| Viewer | Read only, including revisions and exports | Leave the organization; connect a read-only agent with an existing identity |

The organization's owner cannot be removed and cannot leave. Transferring ownership and deleting an organization are not implemented.

## Invitations

Owners and admins create single-use invitation links that expire after seven days. No email is sent. The database stores only the SHA-256 digest of a random 256-bit token, and the link carries the token in its fragment, out of request URLs and referrers. Accepting an invitation again from the same active member changes nothing, another person cannot reuse it, and accepting a second link never raises an existing member's role. Removing or changing a member also revokes the invitations they created. In a self-hosted installation no one else can sign in to accept a link.

## Authorization and isolation

Each membership has an organization-local member ID used by the sharing API. Revisions keep the principal that actually wrote them, so a teammate's revisions are labelled as a teammate's.

Each Space has its own metadata, key, canonical Tasks collection and content scope. Row-level policies and the shared command boundary both check live membership and Space access, and viewers are refused writes at both. Administering an organization does not grant access to its private or restricted Spaces.

Scoped commands take a shared organization lock and recheck access after acquiring it; membership and sharing changes take the exclusive lock. Removing a member revokes their agent connections, and restoring the membership does not reactivate old tokens. Changing a Space's sharing revokes that Space's agent connections, which must then reconnect.

## Agent access

A connection targets one Space: `POST /v1/agent-tokens` and `POST /v1/device/consent` take an optional `space_id`, and default to the Private Space. Every MCP call resolves that Space and checks the live grant and the agent identity. Viewer connections cannot write. Members manage only their own connections.

## API

These endpoints under `/v1` accept the owner only, never agent tokens or API keys.

| Endpoint | Purpose |
|---|---|
| `GET /workspaces` | List accessible Spaces with their organization, role, visibility and default-Space status |
| `POST /organizations` | Create an organization and its shared first Space: `command_id`, `name`, `display_name` |
| `GET /organizations/{id}/members` | List member IDs, display names and roles, including one's own membership |
| `POST /organizations/{id}/members/{member_id}` | Change a member's role |
| `DELETE /organizations/{id}/members/{member_id}` | Remove a member, or leave |
| `GET`, `POST /organizations/{id}/invitations` | List pending invitations, or create a link for one role |
| `DELETE /organizations/{id}/invitations/{invitation_id}` | Revoke an invitation |
| `POST /invitations/accept` | Accept with `token` and `display_name` |
| `POST /organizations/{id}/spaces` | Create a named Space with `command_id`, `visibility` and `member_ids` |
| `GET`, `POST /spaces/{id}/access` | Read or replace a Space's access |

Resource endpoints keep their explicit Space path. The agent-management endpoints accept `workspace_id` as a query selector; it selects a Space and never grants access.

## Verification

`backend/tests/test_teams.py` runs against a disposable PostgreSQL with the restricted runtime login. It covers shared content, personal isolation, private and restricted Spaces, owner and admin boundaries, viewer write denial, invitation expiry, revocation and reuse, grant retargeting and protection against purging an account with team history.

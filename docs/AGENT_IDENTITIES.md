# Agent identities and connections

Space attributes every write to an agent identity, separately from the credential an agent connects with. [Connecting agents](CONNECTING_AGENTS.md) shows how to set up a client; this page gives the contract behind it.

## Contract

An **agent identity** is a stable UUID with a name and a harness, scoped to one Space. A **connection** is a connection grant (a row in `space.oauth_grants`, a historical name) with its own grant ID, client ID, read or read-write permission and revocation state. Several connections can refer to the same agent identity. Neither the name nor the Agent ID is a credential: in key mode every call still needs a valid agent or OAuth token and a live grant.

The owner can create, rename or retire identities on the Agents page, choose an existing identity when creating a connection or approving a device code or an app's sign-in, and assign an existing connection to an identity. A new connection can also call `identify_agent` with an existing ID, or with a name and a harness. A connection that already has an identity cannot reassign itself; only the owner can change that assignment on the website. The harness is one of the values listed by `identify_agent`, such as `codex`, `claude-code`, `cursor`, `claude`, `chatgpt` or `gemini`, or `other`. These labels and their icons do not establish client compatibility or attest to the software actually running.

Creating the same normalized name and harness reuses one identity. Normalization folds Unicode compatibility forms, whitespace and case. Different names in the same harness remain separate identities. The stable UUID is the durable reference: after a rename, clients keep using it rather than recreating the old name. Concurrent creation is serialized and checked against a fingerprint keyed with the Space key. Names and harnesses are stored in the identity's JSON metadata, beside that fingerprint. Each Space can have up to 200 identities.

In private mode agents connect without a token. Each agent identity then gets its own connection when it calls `identify_agent`, so attribution and the Agents page work the same way; see [Architecture](ARCHITECTURE.md#identity-and-access).

## Every MCP request carries the ID

Call `workspace_info` at session start. It returns the assigned `agent_id`, so a client does not need a separate identification call when the owner already chose the identity for its connection. If the ID is null, use `list_agent_identities` to find an existing identity and then `identify_agent(agent_id=...)`, or call `identify_agent(name="Coding partner", harness="codex")` once.

Configure this HTTP header in the MCP client to attach the ID automatically:

```http
X-Space-Agent-ID: 11111111-1111-4111-8111-111111111111
```

For a Codex remote MCP server, the corresponding server configuration entry is:

```toml
http_headers = { "X-Space-Agent-ID" = "11111111-1111-4111-8111-111111111111" }
```

Clients that cannot set custom headers send the same ID as the top-level `agent_id` tool argument. It is a Space tool argument, not a new MCP protocol field or a property inside `change`/`request`:

```json
{
  "name": "list_resources",
  "arguments": {
    "space_id": "22222222-2222-4222-8222-222222222222",
    "kind": "document",
    "agent_id": "11111111-1111-4111-8111-111111111111"
  }
}
```

A content operation without identification is rejected before any content is read. The ID must match the connection's current assignment. Invalid UUIDs and conflicting header and argument IDs fail. `server_info`, `workspace_info` and identification are available without an ID so a new session can start. A revoked connection cannot use identification or content tools even if it knows a valid Agent ID.

## Attribution, reconnects and queued work

New resource revisions, handoffs and generated memory carry an `author_agent` snapshot containing the ID, name, harness and identity revision. Renaming an identity changes future attribution; saved revisions keep the name used when they were written. Revisions written before identities existed keep their generic attribution, because their original identity cannot be established safely. The memory interface labels this as revision authorship rather than inventing the original creator of each claim.

Revoking one connection preserves the identity, the other connections and saved work. A new connection can reuse the same identity, while the revoked grant stays invalid. When the owner reassigns a connection, it must send the new Agent ID; the old ID fails, and command and job idempotency cannot silently replay work as the new identity.

Queued memory jobs keep the submitting Agent ID along with their original grant. Reassignment or revocation prevents a job from running under a different actor. Jobs queued before identities existed may finish only while their original grant is still active and unassigned.

Retiring an identity requires revoking its connections first. A retired identity disappears from the Agents page, but its name stays on the revisions it wrote.

## Owner API

These endpoints accept the owner, that is a website session or the access key, and refuse agent tokens and API keys.

| Endpoint | Purpose |
|---|---|
| `GET /v1/agent-identities` | List identities as `{ "items": [...] }` |
| `POST /v1/agent-identities` | Create or reuse `{ "name": "...", "harness": "codex" }` |
| `POST /v1/agent-identities/{agent_id}` | Rename or change the harness, with `expected_revision` |
| `DELETE /v1/agent-identities/{agent_id}?expected_revision=N` | Retire an identity with no active connection |
| `GET /v1/agents` | List connections |
| `POST /v1/agents/{client_id}/identity` | Assign an active connection with `{ "agent_id": "..." }` |
| `DELETE /v1/agents/{client_id}` | Revoke a connection |
| `POST /v1/agent-tokens` | Create a connection and its token, with an optional `agent_id` |
| `POST /v1/device/consent` | Approve or deny a device-code request, with an optional `agent_id` |

Identity edits compare revisions to prevent lost updates; renaming into another existing identity returns a conflict. MCP identification may initialize an unassigned connection, but it grants no write permission and no access management.

## Validation

`backend/tests/test_agents.py` covers creation and deduplication on a real PostgreSQL, concurrent connects, read-only permissions, cross-owner isolation, header and argument transport, immutable attribution, rename conflicts, reconnection and revocation, and job receipts. The website's unit tests cover connection grouping and revision attribution.

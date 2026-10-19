# Connecting agents

Agents use Space through one MCP endpoint, served over streamable HTTP at your public address plus `/mcp/`, for example `https://space.example.com/mcp/`. Keep the final slash: a client that drops it is redirected to `/mcp/` by Caddy, at the https address, so the connection still works. This page shows how to connect common clients in each access mode, and how agents identify themselves.

## Key mode: one token per agent

1. On the website, open **Agents** and choose **Connect an agent**.
2. Name the connection, pick the client, and choose whether it may write. Read-only agents can search, recall and read; read-write agents can also create, change and delete content.
3. Copy the token. It is shown once; Space keeps only a digest of it.
4. Add the server to your client with the token as a bearer header, as below.

Revoke a connection on the same page at any time: only that agent loses access. Tokens last 180 days; create a new connection when one expires.

### Claude Code

```sh
claude mcp add --transport http space https://space.example.com/mcp/ \
  --header "Authorization: Bearer spc_..."
```

### Codex

In `~/.codex/config.toml`, with the token kept in an environment variable:

```toml
[mcp_servers.space]
url = "https://space.example.com/mcp/"
bearer_token_env_var = "SPACE_TOKEN"
```

Then `export SPACE_TOKEN=spc_...` in the shell that starts Codex. Option names can differ between Codex versions; see its MCP documentation if this shape is rejected.

### Cursor, VS Code and other JSON-configured clients

```json
{
  "mcpServers": {
    "space": {
      "url": "https://space.example.com/mcp/",
      "headers": { "Authorization": "Bearer spc_..." }
    }
  }
}
```

Cursor reads this from `~/.cursor/mcp.json`. Other clients use the same shape under their own file name; some call the key `servers` or require `"type": "http"`.

### Device code, for agents that cannot paste a token

An agent or script can ask for a connection itself:

1. `POST /v1/device/code` with `{"client_name": "My agent", "harness": "other"}`. The answer holds a six-digit `user_code`, a `device_code` and the page to visit.
2. You open `https://space.example.com/connect`, enter the code and approve the access.
3. The agent polls `POST /v1/device/token` with `{"device_code": "..."}` every few seconds until it receives its token and MCP settings.

The resulting connection is listed and revoked on the Agents page like any other.

## Key mode: apps that sign in through the browser

Hosted assistants such as claude.ai and ChatGPT, and other clients that sign in to a remote MCP server, need only the MCP address. Space includes the OAuth sign-in they use, so there is no token to create or paste:

1. In the app, add a custom connector (a remote MCP server) with your MCP address, for example `https://space.example.com/mcp/`. The **Connect an agent** dialog on the Agents page shows it, ready to copy.
2. The app opens Space in your browser. Unlock it with the access key if this browser is locked, then choose the agent identity, the Space and whether the app may write, and allow access.
3. Choose **Return to** the app. The app finishes connecting on its own.

Each app becomes a connection on the Agents page under the identity you chose, and revoking it there disconnects the app at once. The app holds an access token that lasts one hour and a refresh token it trades for a new pair; an app left unused for 180 days must sign in again.

Hosted apps reach Space from their own servers, so they need key mode on a public HTTPS address. An installation in private mode, or on a private network, is out of their reach.

Desktop and command-line clients can sign in the same way instead of using a token. Claude Code, for example, offers to sign in from its `/mcp` menu when the server is added without a header:

```sh
claude mcp add --transport http space https://space.example.com/mcp/
```

Such clients receive the answer on their own computer, at an address like `http://127.0.0.1:<port>/callback`. If your browser runs on another computer, approve as usual, then use **Finish by hand** on the result page to open the link where the client runs.

Space does not verify the name an app registers. Before you allow access, check that you started the connection yourself and that the consent page returns you to the app you expect, such as `claude.ai` or `chatgpt.com`.

## Private mode: no token

Agents connect without credentials:

```sh
claude mcp add --transport http space http://127.0.0.1:8080/mcp/
```

Every agent identity still gets its own connection, so attribution and the Agents page work as in key mode. Before an agent identifies itself it can only discover the Space and identify.

## Identifying an agent

Space attributes every write to an agent identity: a name and a harness such as `claude-code`, `codex` or `cursor`. On its first use, an agent calls the `identify_agent` tool with its name and harness, or with an existing `agent_id`. Identities with the same name and harness are reused, so a second Claude Code connection becomes the same identity.

After that the agent sends its Agent ID on every content request, either as the `X-Space-Agent-ID` header or as the `agent_id` tool argument. Clients that support custom headers can set it once:

```sh
claude mcp add --transport http space https://space.example.com/mcp/ \
  --header "Authorization: Bearer spc_..." \
  --header "X-Space-Agent-ID: 2f6d..."
```

The served guide (the `memory_guide` prompt, or the `space://memory/guide` resource) explains the rules every connected agent follows: what to remember, how to recall, and the limits of this installation.

## Agent skills

The [skills](../skills/README.md) folder holds optional workflows, such as resuming a session, refining a conversation into memory or writing a weekly review. Copy the skills you want into your agent's skills directory, for example `~/.claude/skills/` for Claude Code. They work with any authorized connection.

## Personal API keys

For your own scripts, create an API key under **Settings → API keys** and send it as `Authorization: Bearer spk_...` to the REST API at `/v1/`. A key reaches one Space, read-only or read-write, and cannot manage agents, keys or webhooks. The [API reference](openapi.json) lists every route a key may call.

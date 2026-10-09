"""Agent connections on a private-network installation, where agents send no token.

Every MCP request without credentials acts for the owner through a connection grant, so
attribution, read/write access and the website's agent list work as they do for token
connections. Before an agent identifies itself it shares one unidentified connection,
which can only discover and identify. identify_agent then gives each agent identity its
own connection, derived from the identity's ID, and the agent sends that ID on every
request as X-Space-Agent-ID or agent_id.
"""

from dataclasses import replace
from uuid import NAMESPACE_URL, UUID, uuid5

from space import agents
from space.security.auth import Identity, owner_identity
from space.workspace import CommandError

UNIDENTIFIED_CLIENT = uuid5(NAMESPACE_URL, "space:private-network:unidentified")


def agent_client(agent_id) -> UUID:
    """The connection client ID for one agent identity, stable across restarts."""
    return uuid5(NAMESPACE_URL, f"space:private-network:agent:{UUID(str(agent_id))}")


class PrivateConnections:
    def __init__(self, workspace):
        self.workspace = workspace

    def identity(self, agent_id: str | None = None) -> Identity:
        """The connection identity for this agent ID, or the unidentified one."""
        client = agent_client(agent_id) if agent_id else UNIDENTIFIED_CLIENT
        grant = self._grant(client, agent_id)
        return replace(owner_identity(), client_id=str(client), grant_id=grant, agent_id=agent_id)

    def _grant(self, client: UUID, agent_id: str | None) -> str:
        owner = owner_identity()
        with self.workspace.db.transaction(owner) as connection:
            self.workspace._scope(connection, owner, human=True, write=True)
            # Two first requests from the same agent must not both create its grant.
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 7))", (str(client),)
            )
            row = connection.execute(
                "SELECT id FROM space.oauth_grants WHERE client_id=%s AND resource=%s "
                "AND revoked_at IS NULL",
                (client, self.workspace.resource_url),
            ).fetchone()
            if row:
                return str(row["id"])
            return self.workspace._grant(connection, owner, client, True, agent_id)

    def identify(self, actor: Identity, *, agent_id=None, profile=None) -> dict:
        """identify_agent without a token: discover, reuse or create the agent identity,
        then give it its own connection. Output: the identify_agent response."""
        if agent_id is None and profile is None:
            return agents.identify(self.workspace, actor)
        if agent_id is not None and profile is not None:
            raise CommandError("Supply an agent_id or a name and harness", 400)
        if profile is not None:
            agent_id = agents.save_profile(self.workspace, actor, profile, human=False)["id"]
        connection = self.identity(str(UUID(str(agent_id))))
        return {
            "agent": agents.current_profile(
                self.workspace, connection, agent_id=UUID(connection.agent_id)
            ),
            "required_header": "X-Space-Agent-ID",
            "instructions": (
                "Send agent.id as X-Space-Agent-ID on every request, or as agent_id on every "
                "content tool call. Reuse this identity for other connections."
            ),
        }

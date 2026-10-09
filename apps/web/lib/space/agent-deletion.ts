import type { AgentIdentity, Grant } from "./types";

type DeleteRequest = (path: string, method: string) => Promise<{
  revoked?: boolean;
  deleted?: boolean;
}>;

export async function deleteAgentWithConnections(
  agent: AgentIdentity,
  grants: Grant[],
  request: DeleteRequest,
) {
  for (const connection of grants.filter((grant) => grant.agent_id === agent.id)) {
    const result = await request(`agents/${connection.client_id}`, "DELETE");
    // Every connection must lose its access before the identity it uses can go.
    if (!result.revoked) throw new Error("Could not revoke this connection. Retry deleting the agent.");
  }
  const result = await request(
    `agent-identities/${agent.id}?expected_revision=${agent.revision}`,
    "DELETE",
  );
  if (!result.deleted) throw new Error("Could not delete this agent. Please try again.");
}

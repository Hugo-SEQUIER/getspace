"""REST transport: independently verified browser sessions and shared commands."""

from dataclasses import replace
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict

from space import agents as agent_profiles
from space import (
    api_keys,
    database_sql,
    device_auth,
    memora_jobs,
    oauth_server,
    overview,
    quotas,
    read_version,
    resource_views,
    restore_merge,
    restore_staging,
    space_map,
    tasks,
    teams,
    webhooks,
    workspace_archive,
    workspace_items,
    workspace_search,
)
from space.admission import dispatch
from space.api.reference_docs import DocumentedRoute
from space.memory import (
    MAX_MEMORY_LIST,
    ChangeMemory,
    RecallRequest,
    RememberClaims,
    RememberMemory,
    SaveHandoff,
    SetMemoryLinks,
)
from space.memory_creator import SetMemoryCreator
from space.memory_pipeline import BuildMemory
from space.security.auth import TokenVerifier, Unauthenticated
from space.settings import Settings
from space.workspace import CommandError, SaveResource, Workspace


class ConnectionAgent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: UUID


bearer = HTTPBearer(
    auto_error=False,
    scheme_name="ApiKey",
    description="A personal API key (spk_...) created in Settings for one Space.",
)


def create_routes(workspace: Workspace, verifier: TokenVerifier, settings: Settings):
    # Browser-session routes stay out of the published reference.
    router = APIRouter(prefix="/v1", include_in_schema=False)
    # Routes an API key may call; together they are the published API reference.
    public = APIRouter(prefix="/v1", route_class=DocumentedRoute)

    def authenticated(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)] = None,
        selected_space: Annotated[
            UUID | None, Query(alias="workspace_id", include_in_schema=False)
        ] = None,
    ):
        # Private mode admits the owner without credentials; key mode refuses them here.
        token = credentials.credentials if credentials else None
        if token is None and not verifier.private:
            raise Unauthenticated("Unlock this Space with its access key")
        return replace(verifier.verify(token), selected_space_id=selected_space), token

    signed_in = Depends(authenticated)

    def session_only(session=signed_in):
        if session[0].api_key:
            raise CommandError("API keys cannot perform this operation. Use the website.", 403)
        return session

    def key_access(write: bool):
        """Admits a browser session, or an API key on its own Space with enough access.
        The workspace scope enforces the same rules again for every command."""

        def admitted(request: Request, session=signed_in):
            key = session[0].api_key
            if key:
                if write and not key.can_write:
                    raise CommandError("This API key is read-only.", 403)
                space = request.path_params.get("space_id")
                try:
                    foreign = space is not None and UUID(space) != key.space_id
                except ValueError:
                    foreign = True
                if foreign:
                    raise CommandError("Space not found or access was removed.", 404)
            return session

        return Depends(admitted)

    # Every route refuses API keys unless it opts in with read or write.
    auth = Depends(session_only)
    read, write = key_access(False), key_access(True)

    @router.post("/workspace")
    def bootstrap(session=auth):
        return {**workspace.bootstrap(session[0]), "read_version_supported": True}

    @public.get("/workspace", tags=["Space"], summary="Get the key's Space")
    def info(session=read):
        return workspace.info(session[0])

    @public.get(
        "/spaces/{space_id}/read-version", tags=["Space"], summary="Get the Space read version"
    )
    def space_read_version(space_id: UUID, session=read):
        return read_version.snapshot(workspace, session[0], space_id)

    # Seen state is personal website state: never published, never reachable by a key.
    @router.get("/spaces/{space_id}/views")
    def resource_view_listing(space_id: UUID, session=auth):
        return resource_views.listing(workspace, session[0], space_id)

    @router.post("/spaces/{space_id}/views")
    def mark_resources_seen(space_id: UUID, body: resource_views.MarkSeen, session=auth):
        return resource_views.mark(workspace, session[0], space_id, body)

    @public.get(
        "/spaces/{space_id}/resources",
        tags=["Documents and tasks"],
        summary="List documents or tasks",
    )
    def resources(
        space_id: UUID, kind: Literal["document", "task"], after: UUID | None = None, session=read
    ):
        return workspace.list_resources(session[0], space_id, kind, after)

    @public.get(
        "/spaces/{space_id}/resources/{resource_id}",
        tags=["Documents and tasks"],
        summary="Get a document or task",
    )
    def resource(
        space_id: UUID,
        resource_id: UUID,
        revision: Annotated[int | None, Query(ge=1)] = None,
        session=read,
    ):
        return workspace.get_resource(session[0], space_id, resource_id, revision)

    @public.delete(
        "/spaces/{space_id}/resources/{resource_id}",
        tags=["Documents and tasks"],
        summary="Delete a document or task",
    )
    def delete_resource(
        space_id: UUID,
        resource_id: UUID,
        expected_revision: Annotated[int, Query(ge=1)],
        session=write,
    ):
        from space.resource_deletion import delete_resource as remove

        return remove(workspace, session[0], space_id, resource_id, expected_revision)

    @public.post(
        "/spaces/{space_id}/resources",
        tags=["Documents and tasks"],
        summary="Create or update a document or task",
    )
    def save(space_id: UUID, body: SaveResource, session=write):
        return workspace.save(session[0], space_id, body)

    @public.get(
        "/spaces/{space_id}/tasks/columns",
        tags=["Documents and tasks"],
        summary="List task columns",
    )
    def task_columns(space_id: UUID, session=read):
        return tasks.columns(workspace, session[0], space_id)

    @public.post(
        "/spaces/{space_id}/tasks/columns",
        tags=["Documents and tasks"],
        summary="Add a task column",
    )
    def add_task_column(space_id: UUID, body: tasks.AddColumn, session=write):
        return tasks.add_column(workspace, session[0], space_id, body)

    @public.post(
        "/spaces/{space_id}/tasks/{resource_id}/status",
        tags=["Documents and tasks"],
        summary="Move a task",
    )
    def set_task_status(space_id: UUID, resource_id: UUID, body: tasks.SetStatus, session=write):
        return tasks.set_status(workspace, session[0], space_id, resource_id, body)

    @public.post(
        "/spaces/{space_id}/handoffs", tags=["Documents and tasks"], summary="Save a handoff"
    )
    def handoff(space_id: UUID, body: SaveHandoff, session=write):
        return workspace.save_handoff(session[0], space_id, body)

    @public.get("/spaces/{space_id}/usage", tags=["Space"], summary="Get usage")
    def usage(space_id: UUID, session=read):
        return quotas.usage(workspace, session[0], space_id)

    @public.get("/spaces/{space_id}/overview", tags=["Space"], summary="Get activity overview")
    def space_overview(space_id: UUID, days: int = 30, all_periods: bool = False, session=read):
        return overview.snapshot(workspace, session[0], space_id, days, all_periods=all_periods)

    @public.post(
        "/spaces/{space_id}/export", tags=["Export"], summary="Export one page of the Space"
    )
    def export(space_id: UUID, body: workspace_archive.ExportPage, session=read):
        return workspace_archive.export_page(workspace, session[0], space_id, body)

    @router.get("/spaces/{space_id}/restores")
    def restores(space_id: UUID, session=auth):
        return restore_staging.listing(workspace, session[0], space_id)

    @router.post("/spaces/{space_id}/restores")
    def start_restore(space_id: UUID, body: restore_staging.Start, session=auth):
        return restore_staging.start(workspace, session[0], space_id, body)

    @router.get("/spaces/{space_id}/restores/{restore_id}")
    def restore_status(space_id: UUID, restore_id: UUID, session=auth):
        return restore_merge.status(workspace, session[0], space_id, restore_id)

    @router.delete("/spaces/{space_id}/restores/{restore_id}")
    def cancel_restore(space_id: UUID, restore_id: UUID, session=auth):
        return restore_staging.cancel(workspace, session[0], space_id, restore_id)

    @router.post("/spaces/{space_id}/restores/{restore_id}/parts/{part}")
    def restore_part(
        space_id: UUID, restore_id: UUID, part: int, body: restore_staging.Chunk, session=auth
    ):
        return restore_staging.upload(workspace, session[0], space_id, restore_id, part, body)

    @router.post("/spaces/{space_id}/restores/{restore_id}/review")
    def review_restore(space_id: UUID, restore_id: UUID, body: restore_merge.Review, session=auth):
        return restore_merge.review(workspace, session[0], space_id, restore_id, body)

    @router.post("/spaces/{space_id}/restores/{restore_id}/commit")
    def commit_restore(space_id: UUID, restore_id: UUID, body: restore_merge.Commit, session=auth):
        return restore_merge.commit(workspace, session[0], space_id, restore_id, body)

    @public.post(
        "/spaces/{space_id}/search", tags=["Search and recall"], summary="Search the Space"
    )
    def search(space_id: UUID, body: workspace_search.Search, session=read):
        return workspace_search.search(workspace, session[0], space_id, body)

    # The website loads this once per Space version and matches as people type.
    @router.get("/spaces/{space_id}/search/index")
    def search_index(
        space_id: UUID,
        after: Annotated[str | None, Query(pattern=r"^[ir]:[0-9a-f-]{36}$")] = None,
        session=read,
    ):
        return workspace_search.index(workspace, session[0], space_id, after)

    @public.get(
        "/spaces/{space_id}/memories/{resource_id}", tags=["Memory"], summary="Get a memory"
    )
    def selected_memory(space_id: UUID, resource_id: UUID, session=read):
        return workspace_search.read_memory(workspace, session[0], space_id, resource_id)

    @public.post(
        "/spaces/{space_id}/context", tags=["Search and recall"], summary="Recall cited context"
    )
    async def context(space_id: UUID, body: RecallRequest, session=read):
        # Queries stay out of URL/access logs.
        return await dispatch(workspace.context, session[0], space_id, body)

    @router.get("/agents")
    def agents(session=auth):
        return workspace.grants(session[0])

    @public.get(
        "/spaces/{space_id}/items",
        tags=["Projects, folders and databases"],
        summary="List workspace items of one kind",
    )
    def items(
        space_id: UUID,
        kind: Literal["project", "database", "category", "section", "folder"],
        after: UUID | None = None,
        deleted: bool = False,
        session=read,
    ):
        return workspace_items.listing(workspace, session[0], space_id, kind, after, deleted)

    @public.get(
        "/spaces/{space_id}/items/{item_id}",
        tags=["Projects, folders and databases"],
        summary="Get a workspace item",
    )
    def item(space_id: UUID, item_id: UUID, revision: int | None = None, session=read):
        return workspace_items.get(workspace, session[0], space_id, item_id, revision)

    @public.post(
        "/spaces/{space_id}/items",
        tags=["Projects, folders and databases"],
        summary="Create or update a workspace item",
    )
    def save_item(space_id: UUID, body: workspace_items.SaveItem, session=write):
        return workspace_items.save(workspace, session[0], space_id, body)

    @public.get("/spaces/{space_id}/map", tags=["Space"], summary="Get the Space map")
    def get_space_map(space_id: UUID, max_chars: int = space_map.DEFAULT_CHARS, session=read):
        request = space_map.MapRequest(max_chars=max_chars)
        return space_map.build(workspace, session[0], space_id, request)

    @public.post(
        "/spaces/{space_id}/databases/{database_id}/query",
        tags=["Projects, folders and databases"],
        summary="Query a database",
    )
    def query_database(space_id: UUID, database_id: UUID, body: database_sql.Query, session=read):
        return database_sql.execute(workspace, session[0], space_id, database_id, body)

    @router.post("/spaces/{space_id}/items/{item_id}/visibility")
    def item_visibility(
        space_id: UUID, item_id: UUID, body: workspace_items.ChangeVisibility, session=auth
    ):
        return workspace_items.visibility(workspace, session[0], space_id, item_id, body)

    @router.get("/spaces/{space_id}/webhooks")
    def webhook_subscriptions(space_id: UUID, session=auth):
        return webhooks.listing(workspace, session[0], space_id)

    @router.post("/spaces/{space_id}/webhooks")
    def create_webhook(space_id: UUID, body: webhooks.CreateSubscription, session=auth):
        return webhooks.create(workspace, session[0], space_id, body)

    @router.post("/spaces/{space_id}/webhooks/{subscription_id}")
    def change_webhook(
        space_id: UUID, subscription_id: UUID, body: webhooks.ChangeSubscription, session=auth
    ):
        return webhooks.change(workspace, session[0], space_id, subscription_id, body)

    @router.get("/spaces/{space_id}/webhooks/{subscription_id}/deliveries")
    def webhook_deliveries(space_id: UUID, subscription_id: UUID, session=auth):
        return webhooks.deliveries(workspace, session[0], space_id, subscription_id)

    @router.post("/spaces/{space_id}/webhooks/{subscription_id}/deliveries/{delivery_id}/redeliver")
    def redeliver_webhook(
        space_id: UUID,
        subscription_id: UUID,
        delivery_id: UUID,
        body: webhooks.Redeliver,
        session=auth,
    ):
        return webhooks.redeliver(
            workspace, session[0], space_id, subscription_id, delivery_id, body
        )

    @router.get("/agent-identities")
    def identities(session=auth):
        session[0].check(human=True)
        return agent_profiles.list_profiles(workspace, session[0])

    @router.post("/agent-identities")
    def create_identity(body: agent_profiles.AgentProfile, session=auth):
        return agent_profiles.save_profile(workspace, session[0], body)

    @router.post("/agent-identities/{agent_id}")
    def edit_identity(agent_id: UUID, body: agent_profiles.EditAgent, session=auth):
        return agent_profiles.save_profile(workspace, session[0], body, agent_id)

    @router.delete("/agent-identities/{agent_id}")
    def delete_identity(
        agent_id: UUID, expected_revision: Annotated[int, Query(ge=1)], session=auth
    ):
        return agent_profiles.delete_profile(workspace, session[0], agent_id, expected_revision)

    @router.post("/agents/{client_id}/identity")
    def assign_identity(client_id: UUID, body: ConnectionAgent, session=auth):
        return agent_profiles.bind(workspace, session[0], body.agent_id, client_id=client_id)

    @public.get(
        "/spaces/{space_id}/memory/provider", tags=["Memory"], summary="Get memory provider details"
    )
    def memory_info(space_id: UUID, session=read):
        return workspace.memory_info(session[0], space_id)

    @public.get("/spaces/{space_id}/memories", tags=["Memory"], summary="List memories")
    def memories(
        space_id: UUID,
        query: str = "",
        after: UUID | None = None,
        limit: int = MAX_MEMORY_LIST,
        session=read,
    ):
        return workspace.list_memories(session[0], space_id, query=query, after=after, limit=limit)

    @public.put(
        "/spaces/{space_id}/memories/{resource_id}/links",
        tags=["Memory"],
        summary="Set memory links",
    )
    async def set_memory_links(
        space_id: UUID, resource_id: UUID, body: SetMemoryLinks, session=write
    ):
        if body.resource_id != resource_id:
            raise CommandError("Link command resource ID must match the route")
        return await dispatch(workspace.set_memory_links, session[0], space_id, body)

    @public.get(
        "/spaces/{space_id}/memories/{resource_id}/links",
        tags=["Memory"],
        summary="List memory links",
    )
    def memory_links(space_id: UUID, resource_id: UUID, limit: int = 50, session=read):
        return workspace.memory_links(session[0], space_id, resource_id, limit=limit)

    @public.post(
        "/spaces/{space_id}/memory/build", tags=["Memory"], summary="Build memory synchronously"
    )
    async def build_memory(space_id: UUID, body: BuildMemory, session=write):
        return await dispatch(workspace.build_memory, session[0], space_id, body)

    @public.post(
        "/spaces/{space_id}/memory/claims", tags=["Memory"], summary="Remember structured claims"
    )
    async def remember_claims(space_id: UUID, body: RememberClaims, session=write):
        return await dispatch(workspace.remember_claims, session[0], space_id, body)

    @router.post("/spaces/{space_id}/memory/imports/preview")
    def preview_memory_import(space_id: UUID, body: memora_jobs.Preview, session=auth):
        return memora_jobs.preview(workspace, session[0], space_id, body)

    @router.post("/spaces/{space_id}/memory/imports", status_code=202)
    def start_memory_import(space_id: UUID, body: memora_jobs.Start, session=auth):
        return memora_jobs.start(workspace, session[0], space_id, body)

    @router.get("/spaces/{space_id}/memory/imports")
    def memory_imports(space_id: UUID, session=auth):
        return memora_jobs.recent(workspace, session[0], space_id)

    @router.post("/spaces/{space_id}/memory/imports/{job_id}/retry", status_code=202)
    def retry_memory_import(space_id: UUID, job_id: UUID, body: memora_jobs.Retry, session=auth):
        return memora_jobs.retry(workspace, session[0], space_id, job_id, body)

    @public.post(
        "/spaces/{space_id}/memory/jobs",
        status_code=202,
        tags=["Memory"],
        summary="Start a memory extraction job",
    )
    def extract_memory(space_id: UUID, body: RememberMemory | BuildMemory, session=write):
        return workspace.extract_memory(session[0], space_id, body)

    @public.get(
        "/spaces/{space_id}/memory/jobs/{job_id}", tags=["Memory"], summary="Get a memory job"
    )
    def memory_job(space_id: UUID, job_id: UUID, session=read):
        return workspace.memory_job(session[0], space_id, job_id)

    @public.get("/spaces/{space_id}/memory/jobs", tags=["Memory"], summary="List memory jobs")
    def memory_jobs(space_id: UUID, session=read):
        return workspace.memory_jobs(session[0], space_id)

    @public.post(
        "/spaces/{space_id}/memory/remember", tags=["Memory"], summary="Remember authored text"
    )
    async def remember(space_id: UUID, body: RememberMemory, session=write):
        """Organize, embed and store authored text without extraction or a job."""
        return await dispatch(workspace.remember, session[0], space_id, body)

    @public.delete(
        "/spaces/{space_id}/memories/{resource_id}", tags=["Memory"], summary="Delete a memory"
    )
    def delete_memory(
        space_id: UUID,
        resource_id: UUID,
        expected_revision: Annotated[int, Query(ge=1)],
        session=write,
    ):
        from space.resource_deletion import delete_memory as remove

        return remove(workspace, session[0], space_id, resource_id, expected_revision)

    @public.post(
        "/spaces/{space_id}/memory/change",
        tags=["Memory"],
        summary="Edit, classify or forget a claim",
    )
    async def change_memory(space_id: UUID, body: ChangeMemory, session=write):
        return await dispatch(workspace.change_memory, session[0], space_id, body)

    @public.post(
        "/spaces/{space_id}/memory/creator", tags=["Memory"], summary="Correct claim attribution"
    )
    async def set_memory_creator(space_id: UUID, body: SetMemoryCreator, session=write):
        return await dispatch(workspace.set_memory_creator, session[0], space_id, body)

    @router.delete("/agents/{client_id}")
    def revoke(client_id: UUID, session=auth):
        return workspace.revoke(session[0], client_id)

    # Agent connection tokens created directly on the website, without the device code.
    @router.post("/agent-tokens")
    def agent_token(body: device_auth.DirectConnection, session=auth):
        return device_auth.issue(workspace, session[0], body)

    # OAuth consent for clients that connect through a browser sign-in: the website reads
    # the pending request and answers it; the client's own endpoints live under /oauth.
    @router.get("/oauth/details")
    def oauth_details(authorization_id: UUID, session=auth):
        return oauth_server.details(workspace, session[0], authorization_id)

    @router.post("/oauth/consent")
    def oauth_consent(body: oauth_server.Consent, session=auth):
        return oauth_server.decide(workspace, session[0], body)

    # Device-code connections: the agent calls the first two without credentials,
    # the signed-in person answers the other two from the website.
    @router.post("/device/code")
    def device_code(body: device_auth.DeviceStart):
        return device_auth.begin(workspace, body)

    @router.post("/device/token")
    def device_token(body: device_auth.DevicePoll):
        return device_auth.poll(workspace, body)

    @router.post("/device/claim")
    def device_claim(body: device_auth.DeviceClaim, session=auth):
        return device_auth.claim(workspace, session[0], body)

    @router.post("/device/consent")
    def device_consent(body: device_auth.DeviceConsent, session=auth):
        return device_auth.decide(workspace, session[0], body)

    @router.get("/workspaces")
    def workspaces(session=auth):
        return teams.listing(workspace, session[0])

    @router.post("/organizations")
    def create_organization(body: teams.CreateTeam, session=auth):
        return teams.create(workspace, session[0], body)

    @router.post("/invitations/accept")
    def accept_invitation(body: teams.AcceptInvitation, session=auth):
        return teams.accept(workspace, session[0], body)

    @router.get("/organizations/{organization_id}/members")
    def members(organization_id: UUID, session=auth):
        return teams.members(workspace, session[0], organization_id)

    @router.post("/organizations/{organization_id}/members/{member_id}")
    def member_role(organization_id: UUID, member_id: UUID, body: teams.MemberRole, session=auth):
        return teams.change_member(workspace, session[0], organization_id, member_id, body.role)

    @router.delete("/organizations/{organization_id}/members/{member_id}")
    def remove_member(organization_id: UUID, member_id: UUID, session=auth):
        return teams.change_member(workspace, session[0], organization_id, member_id)

    @router.get("/organizations/{organization_id}/invitations")
    def invitations(organization_id: UUID, session=auth):
        return teams.invitations(workspace, session[0], organization_id)

    @router.post("/organizations/{organization_id}/invitations")
    def invite(organization_id: UUID, body: teams.Invitation, session=auth):
        return teams.invite(workspace, session[0], organization_id, body)

    @router.delete("/organizations/{organization_id}/invitations/{invitation_id}")
    def revoke_invitation(organization_id: UUID, invitation_id: UUID, session=auth):
        return teams.revoke_invitation(workspace, session[0], organization_id, invitation_id)

    @router.post("/organizations/{organization_id}/spaces")
    def create_space(organization_id: UUID, body: teams.CreateSpace, session=auth):
        return teams.create_space(workspace, session[0], organization_id, body)

    @router.get("/spaces/{space_id}/api-keys")
    def api_key_listing(space_id: UUID, session=auth):
        return api_keys.listing(workspace, session[0], space_id)

    @router.post("/spaces/{space_id}/api-keys")
    def create_api_key(space_id: UUID, body: api_keys.CreateApiKey, session=auth):
        return api_keys.create(workspace, session[0], space_id, body)

    @router.delete("/spaces/{space_id}/api-keys/{key_id}")
    def revoke_api_key(space_id: UUID, key_id: UUID, session=auth):
        return api_keys.revoke(workspace, session[0], space_id, key_id)

    @router.get("/spaces/{space_id}/access")
    def space_access(space_id: UUID, session=auth):
        return teams.access(workspace, session[0], space_id)

    @router.post("/spaces/{space_id}/access")
    def set_space_access(space_id: UUID, body: teams.SpaceAccess, session=auth):
        return teams.set_access(workspace, session[0], space_id, body)

    routes = APIRouter()
    routes.include_router(public)
    routes.include_router(router)
    return routes

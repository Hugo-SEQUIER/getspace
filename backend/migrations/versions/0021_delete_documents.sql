-- Existing tenant and member-delete policies still apply to these operations.
GRANT DELETE ON space.events, space.commands, space.resource_revisions,
    space.resources TO space_app;

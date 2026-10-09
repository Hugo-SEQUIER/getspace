"""Allow infrastructure liveness probes without broadening application host access."""

from starlette.middleware.trustedhost import TrustedHostMiddleware


class HealthCheckedHostMiddleware(TrustedHostMiddleware):
    async def __call__(self, scope, receive, send):
        # Container health probes may use an address as Host. This endpoint only
        # reports process liveness; every other route keeps host validation.
        if (
            scope["type"] == "http"
            and scope.get("method") == "GET"
            and scope.get("path") == "/health/live"
        ):
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)

import * as Cause from "effect/Cause";
import * as Context from "effect/Context";
import * as Effect from "effect/Effect";
import * as Exit from "effect/Exit";

export class ApiError extends Error {
  readonly _tag = "ApiError";
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export class BrowserRequestError extends Error {
  readonly _tag = "BrowserRequestError";
  readonly kind: "network" | "invalid-response";

  constructor(kind: BrowserRequestError["kind"]) {
    super(kind === "network"
      ? "Could not connect to your Space. Check your connection and try again."
      : "Your Space returned an unreadable response. Try again.");
    this.name = "BrowserRequestError";
    this.kind = kind;
  }
}

export type BrowserRequest = {
  path: string;
  method: string;
  body?: unknown;
  signal?: AbortSignal;
};
export type BrowserHttpFailure = ApiError | BrowserRequestError | DOMException;

/** Same-origin transport only. Authentication and account fencing stay in client.ts. */
export class BrowserHttp extends Context.Service<BrowserHttp, {
  request<T>(request: BrowserRequest): Effect.Effect<T, BrowserHttpFailure>;
}>()("space/BrowserHttp") {}

export function createBrowserHttp(fetcher: typeof fetch = (...args) => fetch(...args)): BrowserHttp["Service"] {
  return BrowserHttp.of({
    request: <T>(request: BrowserRequest) => Effect.tryPromise({
      try: async (interruption) => {
        const signal = request.signal
          ? AbortSignal.any([interruption, request.signal])
          : interruption;
        signal.throwIfAborted();
        const response = await fetcher(`/api/${request.path}`, {
          method: request.method,
          credentials: "same-origin",
          cache: "no-store",
          redirect: "error",
          headers: { "Content-Type": "application/json" },
          body: request.method === "GET" ? undefined : JSON.stringify(request.body ?? {}),
          signal,
        });
        let value: unknown;
        try { value = await response.json(); }
        catch (error) {
          signal.throwIfAborted();
          // Preserve access-rejection status even when an upstream error body is not JSON.
          if (!response.ok) throw new ApiError("Request failed", response.status);
          throw new BrowserRequestError("invalid-response");
        }
        signal.throwIfAborted();
        if (!response.ok) {
          const error = value && typeof value === "object" && "error" in value ? value.error : undefined;
          throw new ApiError(typeof error === "string" ? error : "Request failed", response.status);
        }
        return value as T;
      },
      catch: (error): BrowserHttpFailure => {
        if (request.signal?.aborted)
          return request.signal.reason instanceof DOMException
            ? request.signal.reason : new DOMException("Request cancelled", "AbortError");
        if (error instanceof ApiError || error instanceof BrowserRequestError || error instanceof DOMException)
          return error;
        return new BrowserRequestError("network");
      },
    }),
  });
}

const liveBrowserHttp = createBrowserHttp();

export const browserRequest = Effect.fnUntraced(function*<T>(request: BrowserRequest) {
  const http = yield* BrowserHttp;
  return yield* http.request<T>(request);
});

/** Promise boundary for TanStack Query and existing forms; never retry a write. */
export async function sendBrowserRequest<T>(request: BrowserRequest, service = liveBrowserHttp): Promise<T> {
  const exit = await Effect.runPromiseExit(
    browserRequest<T>(request).pipe(Effect.provideService(BrowserHttp, service)),
    { signal: request.signal },
  );
  if (Exit.isSuccess(exit)) return exit.value;
  // Expose the original cancellation/deadline instead of an Effect interruption wrapper.
  if (request.signal?.aborted) throw request.signal.reason;
  throw Cause.squash(exit.cause);
}

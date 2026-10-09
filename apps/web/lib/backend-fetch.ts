const transientCodes = new Set([
  "ECONNRESET", "ECONNREFUSED", "ETIMEDOUT", "EAI_AGAIN",
  "UND_ERR_SOCKET", "UND_ERR_CONNECT_TIMEOUT", "UND_ERR_HEADERS_TIMEOUT",
]);

export function backendFailureCode(error: unknown) {
  if (!(error instanceof Error)) return "unknown";
  if (error.name === "TimeoutError") return "timeout";
  if (error.name === "AbortError") return "cancelled";
  const cause = error.cause as { code?: unknown } | undefined;
  if (typeof cause?.code === "string" && transientCodes.has(cause.code)) return cause.code;
  if (error instanceof TypeError) return "fetch_failed";
  if (error instanceof SyntaxError) return "invalid_json";
  return "unknown";
}

async function retryDelay(milliseconds: number, signal?: AbortSignal | null) {
  signal?.throwIfAborted();
  await new Promise<void>((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(signal?.reason); };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", abort);
      resolve();
    }, milliseconds);
    signal?.addEventListener("abort", abort, { once: true });
  });
}

/**
 * Parameters: the final backend response after any safe-read retry.
 * What it does: keeps non-JSON HTTP failures from becoming generic proxy 503s.
 * Output: parsed JSON or a safe error body; transport and successful-response parse errors still throw.
 */
export async function backendResponseJson(response: Response) {
  try {
    return await response.json();
  } catch (error) {
    if (!(error instanceof SyntaxError) || response.ok) throw error;
    console.warn("space_backend_invalid_error_body", { status: response.status });
    return { error: response.status >= 500
      ? `Service temporarily unavailable (HTTP ${response.status}). Please retry shortly.`
      : `Request failed (HTTP ${response.status}).` };
  }
}

export async function fetchBackend(url: URL, init: RequestInit, send: typeof fetch = fetch) {
  // Workers supports manual redirects, but rejects redirect: "error".
  // Never follow an API redirect with the user's authorization header.
  const read = (init.method ?? "GET").toUpperCase() === "GET";
  for (let attempt = 0; ; attempt++) {
    init.signal?.throwIfAborted();
    let response: Response;
    try {
      response = await send(url, { ...init, redirect: "manual" });
    } catch (error) {
      const code = backendFailureCode(error);
      if (!read || attempt > 0 || init.signal?.aborted
        || (code !== "fetch_failed" && !transientCodes.has(code))) throw error;
      console.warn("space_backend_retry", { reason: "transport", code });
      await retryDelay(500, init.signal);
      continue;
    }
    if (response.status >= 300 && response.status < 400) {
      await response.body?.cancel();
      throw new Error("Backend redirects are not allowed");
    }
    const reachedApi = /(?:^|,)\s*api_total;/.test(response.headers.get("server-timing") ?? "");
    if (response.status >= 500) {
      console.warn("space_backend_status", { status: response.status, reached_api: reachedApi });
    }
    // Safe reads can also fail inside Python (for example, a lost database connection).
    // One shared abort signal bounds both attempts and the delay. Persistent failures
    // retain their final response; writes are never replayed after uncertain delivery.
    const retryAfter = response.headers.get("retry-after");
    const delay = retryAfter === null ? 500
      : /^\d+$/.test(retryAfter) ? Math.max(500, Number(retryAfter) * 1000) : Infinity;
    if (read && attempt === 0 && [502, 503, 504].includes(response.status)
      && delay <= 5000 && !init.signal?.aborted) {
      await response.body?.cancel();
      console.warn("space_backend_retry", { reason: reachedApi ? "application" : "gateway", status: response.status });
      await retryDelay(delay, init.signal);
      continue;
    }
    return response;
  }
}

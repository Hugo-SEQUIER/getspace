export const READ_TIMEOUT_MS = 45_000;

export function backendTimeoutMs(path: string, method: string) {
  const modelOperation = /\/memory\/(build|remember|claims|change)$/.test(path)
    || /\/context$/.test(path)
    || /\/restores\/[^/]+\/commit$/.test(path);
  return method === "POST" && modelOperation ? 210_000 : READ_TIMEOUT_MS;
}

export function isTimeout(error: unknown) {
  return error instanceof Error && error.name === "TimeoutError";
}

export function timeoutMessage(method: string) {
  return method === "GET"
    ? "Loading took too long. Please retry."
    : "The request took too long. Check whether your changes were saved before trying again.";
}

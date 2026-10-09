import { ApiError } from "../../../lib/client";
import { BrowserRequestError } from "../../../lib/browser-http";
import type { DatabaseSaveRecovery } from "../../../lib/space/database-save-queue";

export const saveRecovery: DatabaseSaveRecovery = {
  // The proxy answers 502-504 when the backend outcome is unknown.
  transient: (error) => (error instanceof BrowserRequestError && error.kind === "network")
    || (error instanceof ApiError && [502, 503, 504].includes(error.status)),
  stale: (error) => error instanceof ApiError && error.status === 409,
};

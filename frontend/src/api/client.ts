/** Typed API client. Paths and shapes come from the backend's OpenAPI schema (`npm run gen:api`). */
import createClient, { type Middleware } from "openapi-fetch";

import { ApiError, CSRF_HEADERS } from "../api";
import type { components, paths } from "./schema";

export type S = components["schemas"];
export type Account = S["AccountOut"];
export type Txn = S["TxnOut"];
export type Category = S["CategoryOut"];
export type Dashboard = S["Dashboard"];
export type Mapping = S["Mapping"];
export type Preview = S["Preview"];

const csrf: Middleware = {
  onRequest({ request }) {
    if (request.method !== "GET" && request.method !== "HEAD") {
      for (const [k, v] of Object.entries(CSRF_HEADERS)) request.headers.set(k, v);
    }
    return request;
  },
};

export const client = createClient<paths>({ baseUrl: "", credentials: "same-origin" });
client.use(csrf);

/** Turn an openapi-fetch result into data, or throw an ApiError with the server's message. */
export async function ok<T>(
  call: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await call;
  if (error !== undefined || !response.ok) {
    const detail = (error as { detail?: unknown } | undefined)?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? "Some fields aren't valid."
          : response.statusText || "Something went wrong.";
    throw new ApiError(response.status, message);
  }
  return data as T;
}

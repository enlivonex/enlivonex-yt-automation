import { ACTIVATION_TTL_MS, digest, makeToken } from "./worker-helpers.js";

const MAX_BODY_BYTES = 8192;
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const PC_TYPES = new Set(["dedicated-gpu", "integrated-gpu", "unsure"]);
const encoder = new TextEncoder();

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Cache-Control": "no-store",
      "Content-Type": "application/json; charset=utf-8",
      "X-Content-Type-Options": "nosniff",
    },
  });
}

function fail(message, status) {
  return jsonResponse({ error: message }, status);
}

async function authorized(request, env) {
  const secret = env.ADMIN_SECRET;
  const authorization = request.headers.get("Authorization") ?? "";
  if (
    !secret ||
    authorization.length > 256 ||
    !authorization.startsWith("Bearer ")
  ) {
    return false;
  }

  const provided = await digest(authorization.slice(7));
  const expected = await digest(secret);
  let mismatch = 0;
  for (let index = 0; index < expected.length; index += 1) {
    mismatch |= expected.charCodeAt(index) ^ provided.charCodeAt(index);
  }
  return mismatch === 0;
}

async function readJson(request) {
  const contentType = request.headers.get("Content-Type") ?? "";
  const contentLength = Number(request.headers.get("Content-Length") ?? 0);
  if (!contentType.toLowerCase().includes("application/json")) {
    return { error: "Send the form as JSON.", status: 415 };
  }
  if (contentLength > MAX_BODY_BYTES) {
    return { error: "The request is too large.", status: 413 };
  }

  const text = await request.text();
  if (encoder.encode(text).byteLength > MAX_BODY_BYTES) {
    return { error: "The request is too large.", status: 413 };
  }
  try {
    const value = JSON.parse(text);
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return { error: "The request body must be a JSON object.", status: 400 };
    }
    return { value };
  } catch {
    return { error: "The request body is not valid JSON.", status: 400 };
  }
}

function sameOrigin(request) {
  const origin = request.headers.get("Origin");
  return !origin || origin === new URL(request.url).origin;
}

function getPublicDownloadUrl(env) {
  if (typeof env.PUBLIC_DOWNLOAD_URL !== "string") {
    return "";
  }
  try {
    const url = new URL(env.PUBLIC_DOWNLOAD_URL);
    if (
      url.protocol !== "https:" ||
      url.username ||
      url.password ||
      url.hash
    ) {
      return "";
    }
    return url.href;
  } catch {
    return "";
  }
}

async function verifyGoogleCredential(credential, env) {
  if (!env.GOOGLE_CLIENT_ID) {
    return { status: "unconfigured" };
  }
  if (
    typeof credential !== "string" ||
    credential.length < 20 ||
    credential.length > 8000
  ) {
    return { status: "invalid" };
  }

  const verificationUrl = new URL("https://oauth2.googleapis.com/tokeninfo");
  verificationUrl.searchParams.set("id_token", credential);

  let response;
  let claims;
  try {
    response = await fetch(verificationUrl);
    if (!response.ok) {
      return response.status >= 500
        ? { status: "unavailable" }
        : { status: "invalid" };
    }
    claims = await response.json();
  } catch {
    return { status: "unavailable" };
  }

  const expiry = Number(claims.exp);
  const email =
    typeof claims.email === "string" ? claims.email.trim().toLowerCase() : "";
  if (
    claims.aud !== env.GOOGLE_CLIENT_ID ||
    !["accounts.google.com", "https://accounts.google.com"].includes(claims.iss) ||
    !(claims.email_verified === true || claims.email_verified === "true") ||
    !EMAIL_PATTERN.test(email) ||
    !email.endsWith("@gmail.com") ||
    typeof claims.sub !== "string" ||
    !Number.isFinite(expiry) ||
    expiry <= Math.floor(Date.now() / 1000)
  ) {
    return { status: "invalid" };
  }
  return { status: "valid", email };
}

function identityError(status) {
  if (status === "unconfigured") {
    return fail("Google sign-in is not configured yet.", 503);
  }
  if (status === "unavailable") {
    return fail("Google verification is temporarily unavailable. Please try again.", 503);
  }
  return fail("Sign in with a verified @gmail.com account and try again.", 401);
}

async function createRequest(request, env) {
  if (!sameOrigin(request)) {
    return fail("This request is not allowed from another website.", 403);
  }
  const parsed = await readJson(request);
  if (parsed.error) {
    return fail(parsed.error, parsed.status);
  }

  const { credential, pcType, company, privacyConsent } = parsed.value;
  if (typeof company === "string" && company.trim()) {
    return jsonResponse({ received: true });
  }
  if (!PC_TYPES.has(pcType) || privacyConsent !== true) {
    return fail(
      "Choose the option that best describes your PC and accept the privacy terms.",
      400,
    );
  }
  if (!env.DB || !env.ADMIN_SECRET || !getPublicDownloadUrl(env)) {
    return fail("Beta access is not configured yet. Please check back later.", 503);
  }

  const identity = await verifyGoogleCredential(credential, env);
  if (identity.status !== "valid") {
    return identityError(identity.status);
  }

  const requestId = crypto.randomUUID();
  const createdAt = new Date().toISOString();
  await env.DB.prepare(
    `INSERT INTO access_requests (id, email, pc_type, status, created_at)
     VALUES (?, ?, ?, 'requested', ?)
     ON CONFLICT(email) DO NOTHING`,
  )
    .bind(requestId, identity.email, pcType, createdAt)
    .run();

  const record = await env.DB.prepare(
    "SELECT id FROM access_requests WHERE email = ?",
  )
    .bind(identity.email)
    .first();
  if (!record) {
    throw new Error("Access request was not persisted.");
  }

  return jsonResponse(
    { received: true, reference: record.id, email: identity.email },
    201,
  );
}

async function verifyIdentity(request, env) {
  if (!sameOrigin(request)) {
    return fail("This request is not allowed from another website.", 403);
  }
  const parsed = await readJson(request);
  if (parsed.error) {
    return fail(parsed.error, parsed.status);
  }
  const identity = await verifyGoogleCredential(parsed.value.credential, env);
  if (identity.status !== "valid") {
    return identityError(identity.status);
  }
  return jsonResponse({ email: identity.email });
}

async function listRequests(request, env) {
  if (!(await authorized(request, env))) {
    return fail("Admin authentication failed.", 401);
  }
  const result = await env.DB.prepare(
    `SELECT id, email, pc_type, status, created_at, payment_verified_at,
            activation_expires_at, redeemed_at
     FROM access_requests
     ORDER BY created_at DESC
     LIMIT 200`,
  ).all();
  return jsonResponse({ requests: result.results ?? [] });
}

async function issueActivation(request, env) {
  if (!sameOrigin(request)) {
    return fail("This request is not allowed from another website.", 403);
  }
  if (!(await authorized(request, env))) {
    return fail("Admin authentication failed.", 401);
  }
  const parsed = await readJson(request);
  if (parsed.error) {
    return fail(parsed.error, parsed.status);
  }
  const { email, paymentVerified } = parsed.value;
  if (
    typeof email !== "string" ||
    !EMAIL_PATTERN.test(email) ||
    !email.trim().toLowerCase().endsWith("@gmail.com") ||
    paymentVerified !== true
  ) {
    return fail(
      "Confirm a verified tester email and independently checked payment before issuing a code.",
      400,
    );
  }
  if (!env.GOOGLE_CLIENT_ID || !getPublicDownloadUrl(env)) {
    return fail("Google sign-in and the public download are not configured yet.", 503);
  }

  const activationCode = `EX-${makeToken(24)}`;
  const activationHash = await digest(activationCode);
  const now = new Date();
  const expiresAt = new Date(now.getTime() + ACTIVATION_TTL_MS).toISOString();
  const result = await env.DB.prepare(
    `UPDATE access_requests
     SET status = 'approved', payment_verified_at = ?,
         activation_hash = ?, activation_expires_at = ?,
         redeemed_at = NULL, download_token_hash = NULL,
         download_token_expires_at = NULL
     WHERE email = ?`,
  )
    .bind(
      now.toISOString(),
      activationHash,
      expiresAt,
      email.trim().toLowerCase(),
    )
    .run();
  if (result.meta?.changes !== 1) {
    return fail("No access request exists for that email address.", 404);
  }

  return jsonResponse({ activationCode, expiresAt });
}

async function deleteRequest(request, env) {
  if (!sameOrigin(request)) {
    return fail("This request is not allowed from another website.", 403);
  }
  if (!(await authorized(request, env))) {
    return fail("Admin authentication failed.", 401);
  }
  const parsed = await readJson(request);
  if (parsed.error) {
    return fail(parsed.error, parsed.status);
  }
  const email =
    typeof parsed.value.email === "string"
      ? parsed.value.email.trim().toLowerCase()
      : "";
  if (
    !EMAIL_PATTERN.test(email) ||
    !email.toLowerCase().endsWith("@gmail.com")
  ) {
    return fail("Enter a verified Gmail address.", 400);
  }
  const result = await env.DB.prepare(
    "DELETE FROM access_requests WHERE email = ?",
  )
    .bind(email)
    .run();
  if (result.meta?.changes !== 1) {
    return fail("No access request exists for that email address.", 404);
  }
  return jsonResponse({ deleted: true });
}

async function redeemActivation(request, env) {
  if (!sameOrigin(request)) {
    return fail("This request is not allowed from another website.", 403);
  }
  const parsed = await readJson(request);
  if (parsed.error) {
    return fail(parsed.error, parsed.status);
  }
  const { credential, activationCode } = parsed.value;
  if (
    typeof activationCode !== "string" ||
    activationCode.length < 10 ||
    activationCode.length > 100
  ) {
    return fail("Enter the one-time access code from your email.", 400);
  }
  if (!env.DB || !env.ADMIN_SECRET || !getPublicDownloadUrl(env)) {
    return fail("The download is not configured yet. Please contact support.", 503);
  }

  const identity = await verifyGoogleCredential(credential, env);
  if (identity.status !== "valid") {
    return identityError(identity.status);
  }

  const nowIso = new Date().toISOString();
  const result = await env.DB.prepare(
    `UPDATE access_requests
     SET status = 'redeemed', redeemed_at = ?,
         activation_hash = NULL, activation_expires_at = NULL,
         download_token_hash = NULL, download_token_expires_at = NULL
     WHERE email = ? AND activation_hash = ? AND status = 'approved'
       AND activation_expires_at > ?`,
  )
    .bind(
      nowIso,
      identity.email,
      await digest(activationCode.trim()),
      nowIso,
    )
    .run();
  if (result.meta?.changes !== 1) {
    return fail(
      "That code is invalid, expired, or already used for this Gmail account. Contact support if you need a reissue.",
      401,
    );
  }

  return jsonResponse({ downloadUrl: getPublicDownloadUrl(env) });
}

async function routeApi(request, env) {
  const { pathname } = new URL(request.url);
  if (pathname === "/api/config" && request.method === "GET") {
    const downloadReady = Boolean(getPublicDownloadUrl(env));
    return jsonResponse({
      googleClientId: env.GOOGLE_CLIENT_ID ?? "",
      requestsEnabled: Boolean(
        env.GOOGLE_CLIENT_ID &&
          env.ADMIN_SECRET &&
          env.DB &&
          downloadReady,
      ),
    });
  }
  if (pathname === "/api/requests" && request.method === "POST") {
    return createRequest(request, env);
  }
  if (pathname === "/api/identity" && request.method === "POST") {
    return verifyIdentity(request, env);
  }
  if (pathname === "/api/redeem" && request.method === "POST") {
    return redeemActivation(request, env);
  }
  if (pathname === "/api/admin/requests" && request.method === "GET") {
    return listRequests(request, env);
  }
  if (pathname === "/api/admin/issue" && request.method === "POST") {
    return issueActivation(request, env);
  }
  if (pathname === "/api/admin/delete" && request.method === "POST") {
    return deleteRequest(request, env);
  }
  return fail("API route not found.", 404);
}

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (pathname.startsWith("/api/")) {
      try {
        return await routeApi(request, env);
      } catch (error) {
        console.error("Access API request failed.", error);
        return fail("The request could not be completed. Please try again later.", 500);
      }
    }
    return env.ASSETS.fetch(request);
  },
};

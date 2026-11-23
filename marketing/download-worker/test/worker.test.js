import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import worker from "../worker.js";
import { digest } from "../worker-helpers.js";

class MockStatement {
  constructor(database, query) {
    this.database = database;
    this.query = query;
    this.parameters = [];
  }

  bind(...parameters) {
    this.parameters = parameters;
    return this;
  }

  async run() {
    const { database, parameters, query } = this;
    if (query.includes("INSERT INTO access_requests")) {
      const [id, email, pcType, createdAt] = parameters;
      if (!database.requests.has(email)) {
        database.requests.set(email, {
          id,
          email,
          pc_type: pcType,
          status: "requested",
          created_at: createdAt,
          payment_verified_at: null,
          activation_hash: null,
          activation_expires_at: null,
          redeemed_at: null,
          download_token_hash: null,
          download_token_expires_at: null,
        });
        return { meta: { changes: 1 } };
      }
      return { meta: { changes: 0 } };
    }
    if (query.includes("SET status = 'redeemed'")) {
      const [redeemedAt, email, activationHash, now] = parameters;
      const record = database.requests.get(email);
      if (
        record?.status === "approved" &&
        record.activation_hash === activationHash &&
        record.activation_expires_at > now
      ) {
        Object.assign(record, {
          status: "redeemed",
          redeemed_at: redeemedAt,
          activation_hash: null,
          activation_expires_at: null,
          download_token_hash: null,
          download_token_expires_at: null,
        });
        return { meta: { changes: 1 } };
      }
      return { meta: { changes: 0 } };
    }
    if (query.includes("UPDATE access_requests")) {
      const [verifiedAt, activationHash, activationExpires, email] = parameters;
      const record = database.requests.get(email);
      if (!record) {
        return { meta: { changes: 0 } };
      }
      Object.assign(record, {
        status: "approved",
        payment_verified_at: verifiedAt,
        activation_hash: activationHash,
        activation_expires_at: activationExpires,
        redeemed_at: null,
        download_token_hash: null,
        download_token_expires_at: null,
      });
      return { meta: { changes: 1 } };
    }
    if (query.includes("DELETE FROM access_requests")) {
      return { meta: { changes: database.requests.delete(parameters[0]) ? 1 : 0 } };
    }
    throw new Error(`Unsupported mock SQL: ${query}`);
  }

  async first() {
    const { database, parameters, query } = this;
    if (query.includes("SELECT id FROM access_requests WHERE email")) {
      const record = database.requests.get(parameters[0]);
      return record ? { id: record.id } : null;
    }
    throw new Error(`Unsupported mock SQL: ${query}`);
  }

  async all() {
    return { results: [...this.database.requests.values()] };
  }
}

function createEnvironment() {
  const database = {
    requests: new Map(),
    prepare(query) {
      return new MockStatement(this, query);
    },
  };
  return {
    ADMIN_SECRET: "test-admin-secret",
    GOOGLE_CLIENT_ID: "test-client-id.apps.googleusercontent.com",
    PUBLIC_DOWNLOAD_URL:
      "https://github.com/enlivonex/app/releases/latest/download/Enlivonex-Windows.zip",
    DB: database,
    ASSETS: { fetch: async () => new Response("static") },
  };
}

const validCredential = "valid-google-id-token-value-123456";

function googleClaims(email = "tester@gmail.com") {
  return {
    aud: "test-client-id.apps.googleusercontent.com",
    iss: "https://accounts.google.com",
    email,
    email_verified: true,
    sub: "google-user-id",
    exp: String(Math.floor(Date.now() / 1000) + 3600),
  };
}

function mockGoogleVerification(claims = googleClaims()) {
  globalThis.fetch = async (url) => {
    assert.equal(new URL(String(url)).origin, "https://oauth2.googleapis.com");
    assert.equal(new URL(String(url)).pathname, "/tokeninfo");
    assert.equal(new URL(String(url)).searchParams.has("id_token"), true);
    return Response.json(claims);
  };
}

function request(path, method = "GET", body, headers = {}) {
  return new Request(`https://enlivonex.example${path}`, {
    method,
    headers: {
      Origin: "https://enlivonex.example",
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
});

test("public configuration enables the paid flow only when required settings exist", async () => {
  const env = createEnvironment();
  const incomplete = await worker.fetch(request("/api/config"), {
    ...env,
    PUBLIC_DOWNLOAD_URL: "",
  });
  const incompleteConfig = await incomplete.json();
  assert.equal(incompleteConfig.requestsEnabled, false);
  assert.equal("adminSecret" in incompleteConfig, false);

  const complete = await worker.fetch(request("/api/config"), env);
  const completeConfig = await complete.json();
  assert.equal(completeConfig.requestsEnabled, true);
  assert.equal(completeConfig.googleClientId, env.GOOGLE_CLIENT_ID);
  assert.equal("adminSecret" in completeConfig, false);
});

test("Google identity endpoint accepts verified Gmail and rejects other identities", async () => {
  const env = createEnvironment();
  mockGoogleVerification(googleClaims("Tester@Gmail.com"));
  const verified = await worker.fetch(
    request("/api/identity", "POST", { credential: validCredential }),
    env,
  );
  assert.equal(verified.status, 200);
  assert.deepEqual(await verified.json(), { email: "tester@gmail.com" });

  for (const claims of [
    googleClaims("tester@outlook.com"),
    { ...googleClaims(), email_verified: false },
    { ...googleClaims(), aud: "another-client.apps.googleusercontent.com" },
    { ...googleClaims(), exp: String(Math.floor(Date.now() / 1000) - 1) },
  ]) {
    mockGoogleVerification(claims);
    const response = await worker.fetch(
      request("/api/identity", "POST", { credential: validCredential }),
      env,
    );
    assert.equal(response.status, 401);
  }

  const crossOrigin = await worker.fetch(
    request("/api/identity", "POST", { credential: validCredential }, {
      Origin: "https://attacker.example",
    }),
    env,
  );
  assert.equal(crossOrigin.status, 403);
});

test("a request stores only a Google-verified Gmail address", async () => {
  const env = createEnvironment();
  mockGoogleVerification(googleClaims("Tester@Gmail.com"));
  const response = await worker.fetch(
    request("/api/requests", "POST", {
      credential: validCredential,
      pcType: "unsure",
      privacyConsent: true,
    }),
    env,
  );
  const body = await response.json();
  assert.equal(response.status, 201);
  assert.equal(body.email, "tester@gmail.com");
  assert.match(body.reference, /^[0-9a-f-]{36}$/u);
  assert.equal(env.DB.requests.get("tester@gmail.com").status, "requested");

  const withoutConsent = await worker.fetch(
    request("/api/requests", "POST", {
      credential: validCredential,
      pcType: "unsure",
      privacyConsent: false,
    }),
    env,
  );
  assert.equal(withoutConsent.status, 400);

  mockGoogleVerification(googleClaims("tester@tempmail.example"));
  const rejected = await worker.fetch(
    request("/api/requests", "POST", {
      credential: validCredential,
      pcType: "unsure",
      privacyConsent: true,
    }),
    env,
  );
  assert.equal(rejected.status, 401);
  assert.equal(env.DB.requests.size, 1);
});

test("request flow fails closed when Google or download configuration is missing", async () => {
  const env = createEnvironment();
  const unconfigured = await worker.fetch(
    request("/api/requests", "POST", {
      credential: validCredential,
      pcType: "unsure",
      privacyConsent: true,
    }),
    { ...env, GOOGLE_CLIENT_ID: "" },
  );
  assert.equal(unconfigured.status, 503);

  const unsafeUrl = await worker.fetch(
    request("/api/config"),
    { ...env, PUBLIC_DOWNLOAD_URL: "http://example.com/app.zip" },
  );
  assert.equal((await unsafeUrl.json()).requestsEnabled, false);
});

test("admin-issued code is redeemed once by the same verified Gmail account", async () => {
  const env = createEnvironment();
  mockGoogleVerification(googleClaims("buyer@gmail.com"));

  const submitted = await worker.fetch(
    request("/api/requests", "POST", {
      credential: validCredential,
      pcType: "integrated-gpu",
      privacyConsent: true,
    }),
    env,
  );
  assert.equal(submitted.status, 201);

  const adminHeaders = { Authorization: "Bearer test-admin-secret" };
  const unverifiedPayment = await worker.fetch(
    request(
      "/api/admin/issue",
      "POST",
      { email: "buyer@gmail.com", paymentVerified: false },
      adminHeaders,
    ),
    env,
  );
  assert.equal(unverifiedPayment.status, 400);

  const issued = await worker.fetch(
    request(
      "/api/admin/issue",
      "POST",
      { email: "buyer@gmail.com", paymentVerified: true },
      adminHeaders,
    ),
    env,
  );
  const { activationCode } = await issued.json();
  assert.equal(issued.status, 200);
  assert.match(activationCode, /^EX-/u);
  assert.equal(
    env.DB.requests.get("buyer@gmail.com").activation_hash,
    await digest(activationCode),
  );

  mockGoogleVerification(googleClaims("different@gmail.com"));
  const wrongAccount = await worker.fetch(
    request("/api/redeem", "POST", {
      credential: validCredential,
      activationCode,
    }),
    env,
  );
  assert.equal(wrongAccount.status, 401);

  mockGoogleVerification(googleClaims("buyer@gmail.com"));
  const redeemed = await worker.fetch(
    request("/api/redeem", "POST", {
      credential: validCredential,
      activationCode,
    }),
    env,
  );
  const redemption = await redeemed.json();
  assert.equal(redeemed.status, 200);
  assert.equal(redemption.downloadUrl, env.PUBLIC_DOWNLOAD_URL);
  assert.equal(env.DB.requests.get("buyer@gmail.com").status, "redeemed");

  const duplicate = await worker.fetch(
    request("/api/redeem", "POST", {
      credential: validCredential,
      activationCode,
    }),
    env,
  );
  assert.equal(duplicate.status, 401);
});

test("admin routes reject missing credentials and non-Gmail requests", async () => {
  const env = createEnvironment();
  const unauthorized = await worker.fetch(
    request("/api/admin/requests"),
    env,
  );
  assert.equal(unauthorized.status, 401);

  const notGmail = await worker.fetch(
    request(
      "/api/admin/issue",
      "POST",
      { email: "buyer@example.com", paymentVerified: true },
      { Authorization: "Bearer test-admin-secret" },
    ),
    env,
  );
  assert.equal(notGmail.status, 400);
});

test("Google verification outages return an explicit retryable error", async () => {
  const env = createEnvironment();
  globalThis.fetch = async () => {
    throw new Error("network unavailable");
  };
  const response = await worker.fetch(
    request("/api/identity", "POST", { credential: validCredential }),
    env,
  );
  assert.equal(response.status, 503);
});

test("malformed non-object JSON is rejected instead of causing a server error", async () => {
  const response = await worker.fetch(
    new Request("https://enlivonex.example/api/identity", {
      method: "POST",
      headers: {
        Origin: "https://enlivonex.example",
        "Content-Type": "application/json",
      },
      body: "null",
    }),
    createEnvironment(),
  );
  assert.equal(response.status, 400);
});

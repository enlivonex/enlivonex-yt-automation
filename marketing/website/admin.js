const loginForm = document.querySelector("#admin-login");
const secretInput = document.querySelector("#admin-secret");
const statusMessage = document.querySelector("#admin-status");
const results = document.querySelector("#admin-results");
const logoutButton = document.querySelector("#admin-logout");
let adminSecret = "";

function setStatus(message, isError = false) {
  statusMessage.textContent = message;
  statusMessage.dataset.state = isError ? "error" : "success";
}

async function adminRequest(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      Authorization: `Bearer ${adminSecret}`,
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });
  const result = await response.json();
  if (!response.ok) {
    throw new Error(result.error || "Admin request failed.");
  }
  return result;
}

function requestCard(record) {
  const article = document.createElement("article");
  article.className = "admin-request";

  const title = document.createElement("h2");
  title.textContent = record.email;
  const details = document.createElement("p");
  const pcType = {
    "dedicated-gpu": "Dedicated graphics card",
    "integrated-gpu": "Integrated graphics",
    unsure: "PC type not sure",
  }[record.pc_type] ?? "PC type not available";
  details.textContent =
    `Ref ${record.id} · ${pcType} · ${record.status} · requested ${new Date(record.created_at).toLocaleString()}`;
  article.append(title, details);

  if (record.payment_verified_at) {
    const payment = document.createElement("p");
    payment.textContent = `Payment verified: ${new Date(record.payment_verified_at).toLocaleString()}`;
    article.append(payment);
  }
  if (record.redeemed_at) {
    const redeemed = document.createElement("p");
    redeemed.textContent = `Code redeemed: ${new Date(record.redeemed_at).toLocaleString()}`;
    article.append(redeemed);
  }

  const controls = document.createElement("div");
  controls.className = "admin-actions";
  const issueButton = document.createElement("button");
  issueButton.className = "button button-primary";
  issueButton.type = "button";
  issueButton.textContent =
    record.status === "redeemed" ? "Verify payment and reissue code" : "Verify payment and issue code";
  issueButton.addEventListener("click", async () => {
    const confirmed = window.confirm(
      `Have you independently confirmed the exact ₹100 payment from ${record.email} in your own account?`,
    );
    if (!confirmed) {
      return;
    }
    issueButton.disabled = true;
    try {
      const { activationCode, expiresAt } = await adminRequest("/api/admin/issue", {
        method: "POST",
        body: JSON.stringify({ email: record.email, paymentVerified: true }),
      });
      const codePanel = document.createElement("p");
      codePanel.className = "generated-code";
      const codeLabel = document.createElement("span");
      codeLabel.textContent =
        `One-time code (email manually; expires ${new Date(expiresAt).toLocaleDateString()}): `;
      const codeValue = document.createElement("code");
      codeValue.textContent = activationCode;
      const copyButton = document.createElement("button");
      copyButton.className = "button button-outline";
      copyButton.type = "button";
      copyButton.textContent = "Copy code";
      copyButton.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(activationCode);
          setStatus("Code copied. Email it to the buyer using the email above.");
        } catch {
          setStatus("Clipboard access was denied. Select and copy the code manually.", true);
        }
      });
      codePanel.append(codeLabel, codeValue, copyButton);
      article.append(codePanel);
      record.status = "approved";
      record.payment_verified_at = new Date().toISOString();
      issueButton.textContent = "Verify payment and reissue code";
      setStatus("Code issued. Copy it from the request card and email it to the buyer.");
    } catch (error) {
      setStatus(error.message, true);
    } finally {
      issueButton.disabled = false;
    }
  });

  const deleteButton = document.createElement("button");
  deleteButton.className = "button button-outline";
  deleteButton.type = "button";
  deleteButton.textContent = "Delete stored request";
  deleteButton.addEventListener("click", async () => {
    if (!window.confirm(`Permanently delete the request and access record for ${record.email}?`)) {
      return;
    }
    deleteButton.disabled = true;
    try {
      await adminRequest("/api/admin/delete", {
        method: "POST",
        body: JSON.stringify({ email: record.email }),
      });
      setStatus("Request deleted.");
      await loadRequests();
    } catch (error) {
      setStatus(error.message, true);
      deleteButton.disabled = false;
    }
  });
  controls.append(issueButton, deleteButton);
  article.append(controls);
  return article;
}

async function loadRequests() {
  const { requests } = await adminRequest("/api/admin/requests");
  results.replaceChildren();
  if (requests.length === 0) {
    const empty = document.createElement("p");
    empty.className = "admin-empty";
    empty.textContent = "No requests have been submitted.";
    results.append(empty);
    return;
  }
  for (const record of requests) {
    results.append(requestCard(record));
  }
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  adminSecret = secretInput.value;
  try {
    await loadRequests();
    setStatus("Authenticated. Your secret remains in this page only.");
    secretInput.value = "";
    logoutButton.hidden = false;
  } catch (error) {
    adminSecret = "";
    setStatus(error.message, true);
  }
});

logoutButton.addEventListener("click", () => {
  adminSecret = "";
  results.replaceChildren();
  logoutButton.hidden = true;
  setStatus("Admin secret cleared.");
});

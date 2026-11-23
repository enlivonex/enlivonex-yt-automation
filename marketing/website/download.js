const accessRequestForm = document.querySelector("#access-request-form");
const requestStatus = document.querySelector("#request-status");
const requestSubmit = document.querySelector("#request-submit");
const redemptionForm = document.querySelector("#redemption-form");
const redemptionStatus = document.querySelector("#redemption-status");
const redeemSubmit = document.querySelector("#redeem-submit");
const downloadLink = document.querySelector("#download-link");
const googleStatus = document.querySelector("#google-status");
const redeemGoogleStatus = document.querySelector("#redeem-google-status");
let googleCredential = "";
let verifiedEmail = "";
let requestsEnabled = false;

function showStatus(element, message, isError = false) {
  element.textContent = message;
  element.dataset.state = isError ? "error" : "success";
}

async function postJson(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok) {
    throw new Error(result.error || "The request could not be completed.");
  }
  return result;
}

function updateSignInState() {
  const signedIn = Boolean(googleCredential && verifiedEmail);
  const message = signedIn
    ? `Verified Google account: ${verifiedEmail}.`
    : "Sign in with a verified @gmail.com account.";
  showStatus(googleStatus, message);
  showStatus(redeemGoogleStatus, message);
  requestSubmit.disabled = !requestsEnabled || !signedIn;
  redeemSubmit.disabled = !requestsEnabled || !signedIn;
}

function loadGoogleIdentityScript() {
  if (window.google?.accounts?.id) {
    return Promise.resolve();
  }
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://accounts.google.com/gsi/client";
    script.async = true;
    script.onload = () => {
      if (window.google?.accounts?.id) {
        resolve();
      } else {
        reject(new Error("Google sign-in could not be initialized."));
      }
    };
    script.onerror = () => reject(new Error("Google sign-in could not load."));
    document.head.append(script);
  });
}

async function handleGoogleCredential(response) {
  googleCredential = "";
  verifiedEmail = "";
  requestSubmit.disabled = true;
  redeemSubmit.disabled = true;
  showStatus(googleStatus, "Verifying your Google account…");
  showStatus(redeemGoogleStatus, "Verifying your Google account…");
  try {
    const result = await postJson("/api/identity", {
      credential: response.credential,
    });
    googleCredential = response.credential;
    verifiedEmail = result.email;
    updateSignInState();
  } catch (error) {
    showStatus(googleStatus, error.message, true);
    showStatus(redeemGoogleStatus, error.message, true);
  }
}

async function configureRequestForm() {
  try {
    const response = await fetch("/api/config", { cache: "no-store" });
    if (!response.ok) {
      throw new Error("Online access requests are not configured.");
    }
    const config = await response.json();
    requestsEnabled = Boolean(config.requestsEnabled && config.googleClientId);
    if (!requestsEnabled) {
      const message = "Access requests are not open yet. Please check back later.";
      showStatus(googleStatus, message, true);
      showStatus(redeemGoogleStatus, message, true);
      return;
    }

    await loadGoogleIdentityScript();
    window.google.accounts.id.initialize({
      client_id: config.googleClientId,
      callback: handleGoogleCredential,
    });
    for (const elementId of [
      "google-signin-button",
      "redeem-google-signin-button",
    ]) {
      window.google.accounts.id.renderButton(
        document.getElementById(elementId),
        { theme: "filled_blue", size: "large", shape: "rectangular" },
      );
    }
    showStatus(googleStatus, "Sign in with Google to verify your Gmail account.");
    showStatus(
      redeemGoogleStatus,
      "Sign in with the Gmail account used for your request.",
    );
  } catch (error) {
    showStatus(googleStatus, error.message, true);
    showStatus(redeemGoogleStatus, error.message, true);
  }
}

accessRequestForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!googleCredential || !verifiedEmail) {
    showStatus(requestStatus, "Sign in with a verified Gmail account first.", true);
    return;
  }

  requestSubmit.disabled = true;
  showStatus(requestStatus, "Sending your request…");
  const values = new FormData(accessRequestForm);
  try {
    const result = await postJson("/api/requests", {
      credential: googleCredential,
      pcType: values.get("pcType"),
      company: values.get("company"),
      privacyConsent: values.get("privacyConsent") === "on",
    });
    showStatus(
      requestStatus,
      `Request received for ${result.email}. Save this reference: ${result.reference}. We review requests manually; do not pay until you receive payment details from us.`,
    );
    accessRequestForm.reset();
  } catch (error) {
    showStatus(requestStatus, error.message, true);
  } finally {
    updateSignInState();
  }
});

redemptionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!googleCredential || !verifiedEmail) {
    showStatus(redemptionStatus, "Sign in with the Gmail account used for your request.", true);
    return;
  }

  redeemSubmit.disabled = true;
  downloadLink.hidden = true;
  showStatus(redemptionStatus, "Checking your code…");
  const values = new FormData(redemptionForm);
  try {
    const result = await postJson("/api/redeem", {
      credential: googleCredential,
      activationCode: values.get("activationCode"),
    });
    downloadLink.href = result.downloadUrl;
    downloadLink.rel = "noopener noreferrer";
    downloadLink.hidden = false;
    showStatus(
      redemptionStatus,
      "Code accepted. This is a public link; anyone who has it can download or share the ZIP.",
    );
  } catch (error) {
    showStatus(redemptionStatus, error.message, true);
  } finally {
    updateSignInState();
  }
});

configureRequestForm();

let rawBaseUrl =
  process.env.REACT_APP_API_BASE_URL || "http://localhost:3001";

// Auto-correct legacy port 5000 (from previous configurations) to port 3001
if (rawBaseUrl.includes("localhost:5000") || rawBaseUrl.includes("127.0.0.1:5000")) {
  console.warn(
    "Detected legacy backend port 5000 in REACT_APP_API_BASE_URL. Redirecting API requests to port 3001."
  );
  rawBaseUrl = rawBaseUrl.replace(":5000", ":3001");
}

// Strip trailing slash
rawBaseUrl = rawBaseUrl.replace(/\/+$/, "");

// Normalize base URL so it maps cleanly to /token and /assessment-result
rawBaseUrl = rawBaseUrl.replace(/\/api$/, "");

const API_BASE_URL = rawBaseUrl;

export async function getToken(identity = "candidate", room) {
  if (!API_BASE_URL) {
    throw new Error("REACT_APP_API_BASE_URL is not configured.");
  }

  const params = new URLSearchParams();
  if (identity) {
    params.append("identity", identity);
  }
  if (room) {
    params.append("room", room);
  }

  const queryString = params.toString();
  const url = `${API_BASE_URL}/token${queryString ? `?${queryString}` : ""}`;

  let response;
  try {
    response = await fetch(url);
  } catch (networkError) {
    throw new Error(
      `Failed to connect to backend at ${url}. ${networkError.message || ""}`
    );
  }

  if (!response.ok) {
    let message = `Failed to get LiveKit token (HTTP ${response.status}).`;

    try {
      const data = await response.json();

      if (data?.error) {
        message = data.error;
      }
    } catch {}

    throw new Error(message);
  }

  return response.json();
}

export async function getAssessmentResult(assessmentId) {
  if (!assessmentId) {
    throw new Error("Assessment ID is required.");
  }

  const url = `${API_BASE_URL}/assessment-result?assessmentId=${encodeURIComponent(
    assessmentId
  )}`;

  let response;
  try {
    response = await fetch(url);
  } catch (networkError) {
    throw new Error(
      `Failed to connect to backend at ${url}. ${networkError.message || ""}`
    );
  }

  if (!response.ok) {
    let message = `Assessment result is not ready yet (HTTP ${response.status}).`;

    try {
      const data = await response.json();

      if (data?.error) {
        message = data.error;
      }
    } catch {}

    throw new Error(message);
  }

  return response.json();
}
const rawBaseUrl = process.env.REACT_APP_API_BASE_URL;

const API_BASE_URL = (
  rawBaseUrl ||
  (process.env.NODE_ENV === "production"
    ? ""
    : "http://localhost:3001")
).replace(/\/+$/, "");

async function requestJson(url, fallbackMessage) {
  let response;

  try {
    response = await fetch(url);
  } catch (networkError) {
    throw new Error(
      `Failed to connect to backend at ${url}. ${
        networkError.message || ""
      }`
    );
  }

  if (!response.ok) {
    let message = `${fallbackMessage} (HTTP ${response.status}).`;

    try {
      const data = await response.json();
      if (data?.error) {
        message = data.error;
      }
    } catch {
      // Keep the fallback message if the response is not JSON.
    }

    throw new Error(message);
  }

  return response.json();
}

function requireApiBaseUrl() {
  if (!API_BASE_URL) {
    throw new Error(
      "Backend URL is missing. Configure REACT_APP_API_BASE_URL in your deployment settings."
    );
  }

  return API_BASE_URL;
}

export async function getToken(identity = "candidate", room) {
  const baseUrl = requireApiBaseUrl();
  const params = new URLSearchParams();

  if (identity) {
    params.append("identity", identity);
  }

  if (room) {
    params.append("room", room);
  }

  const queryString = params.toString();
  const url = `${baseUrl}/token${
    queryString ? `?${queryString}` : ""
  }`;

  return requestJson(url, "Failed to get LiveKit token");
}

export async function getAssessmentResult(assessmentId) {
  if (!assessmentId) {
    throw new Error("Assessment ID is required.");
  }

  const baseUrl = requireApiBaseUrl();
  const url = `${baseUrl}/assessment-result?assessmentId=${encodeURIComponent(
    assessmentId
  )}`;

  return requestJson(url, "Assessment result is not ready yet");
}
const KNOWN_BACKUP_HOST = "behavioral-ai-api-backup.onrender.com";
const DEFAULT_FEATURE_BACKEND = "https://behavioral-backend-gc62.onrender.com";

const rawBaseUrl = process.env.REACT_APP_API_BASE_URL;

export function resolveApiBaseUrl(raw = rawBaseUrl, nodeEnv = process.env.NODE_ENV) {
  if (raw && typeof raw === "string") {
    const trimmed = raw.trim().replace(/\/+$/, "");
    if (trimmed.includes(KNOWN_BACKUP_HOST)) {
      console.warn(
        `[API] REACT_APP_API_BASE_URL points to known backup host (${KNOWN_BACKUP_HOST}) which lacks recording functionality. Directing to feature backend (${DEFAULT_FEATURE_BACKEND}).`
      );
      return DEFAULT_FEATURE_BACKEND;
    }
    return trimmed;
  }
  if (nodeEnv === "production") {
    return DEFAULT_FEATURE_BACKEND;
  }
  return "http://localhost:3001";
}

const API_BASE_URL = resolveApiBaseUrl();

async function parseResponse(response, fallbackMessage) {
  if (!response.ok) {
    let message = `${fallbackMessage} (HTTP ${response.status}).`;
    let responseBody = null;

    try {
      const data = await response.json();
      responseBody = data;
      if (data?.error) {
        message = data.error;
      }
    } catch {
      // Keep the fallback message if the response is not JSON.
    }

    const error = new Error(message);
    error.status = response.status;
    error.responseBody = responseBody;
    throw error;
  }

  return response.json();
}

async function fetchWithFallback(endpointPath, options, fallbackMessage) {
  const baseUrl = requireApiBaseUrl();
  const normalizedPath = endpointPath.startsWith("/") ? endpointPath : `/${endpointPath}`;
  const primaryUrl = `${baseUrl}${normalizedPath}`;

  let response;
  try {
    response = await fetch(primaryUrl, options);
  } catch (networkError) {
    throw new Error(
      `Failed to connect to backend at ${primaryUrl}. ${
        networkError.message || ""
      }`
    );
  }

  // If 404 and baseUrl does not already contain /api and path does not start with /api, try /api prefix
  if (
    response.status === 404 &&
    !baseUrl.endsWith("/api") &&
    !normalizedPath.startsWith("/api/")
  ) {
    const fallbackUrl = `${baseUrl}/api${normalizedPath}`;
    try {
      const fallbackResponse = await fetch(fallbackUrl, options);
      if (fallbackResponse.status !== 404) {
        return parseResponse(fallbackResponse, fallbackMessage);
      }
    } catch {
      // Ignore network errors on fallback and proceed with primary response
    }
  }

  return parseResponse(response, fallbackMessage);
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
  const params = new URLSearchParams();

  if (identity) {
    params.append("identity", identity);
  }

  if (room) {
    params.append("room", room);
  }

  const queryString = params.toString();
  const path = `/token${queryString ? `?${queryString}` : ""}`;

  return fetchWithFallback(path, undefined, "Failed to get LiveKit token");
}

export async function getAssessmentResult(assessmentId, token) {
  if (!assessmentId) {
    throw new Error("Assessment ID is required.");
  }

  const path = `/assessment-result?assessmentId=${encodeURIComponent(
    assessmentId
  )}`;

  const options = token
    ? {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      }
    : undefined;

  return fetchWithFallback(path, options, "Assessment result is not ready yet");
}

export async function startRecording(room) {
  if (!room) {
    throw new Error("Room name is required to start recording.");
  }

  return fetchWithFallback(
    "/recording/start",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ room }),
    },
    "Failed to start recording"
  );
}

export async function stopRecording(room, token) {
  if (!room) {
    throw new Error("Room name is required to stop recording.");
  }

  const options = {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ room }),
  };

  return fetchWithFallback(
    "/recording/stop",
    options,
    "Failed to stop recording"
  );
}

export async function getRecordingStatus(room, token) {
  if (!room) {
    throw new Error("Room name is required to check recording status.");
  }

  const options = token
    ? {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      }
    : undefined;

  return fetchWithFallback(
    `/recording/status?room=${encodeURIComponent(room)}`,
    options,
    "Recording status is not available yet"
  );
}

export { API_BASE_URL };

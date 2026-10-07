const API_BASE_URL = process.env.REACT_APP_API_BASE_URL;

if (!API_BASE_URL) {
  throw new Error("REACT_APP_API_BASE_URL is not configured.");
}

export async function getToken() {
  const response = await fetch(`${API_BASE_URL}/token`);

  if (!response.ok) {
    let message = "Failed to get LiveKit token.";

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

  const response = await fetch(
    `${API_BASE_URL}/assessment-result?assessmentId=${encodeURIComponent(
      assessmentId
    )}`
  );

  if (!response.ok) {
    let message = "Assessment result is not ready yet.";

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
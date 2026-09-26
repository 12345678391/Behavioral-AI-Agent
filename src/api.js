export async function getToken(identity) {
  const response = await fetch(
    "http://localhost:3001/token"
  );

  if (!response.ok) {
    throw new Error("Failed to get LiveKit token");
  }

  return response.json();
}


export async function getAssessmentResult() {
  const response = await fetch(
    "http://localhost:3001/assessment-result"
  );

  if (!response.ok) {
    throw new Error(
      "Assessment result is not ready yet"
    );
  }

  return response.json();
}
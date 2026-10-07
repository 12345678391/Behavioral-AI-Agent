const express = require("express");
const { AccessToken } = require("livekit-server-sdk");
const cors = require("cors");
const fs = require("fs");
const path = require("path");
require("dotenv").config({ path: path.join(__dirname, ".env") });

const app = express();

app.use(
  cors({
     origin: process.env.FRONTEND_ORIGIN,
  })
);


// ---------------------------------------------------------
// LIVEKIT TOKEN
// ---------------------------------------------------------

app.get("/token", async (req, res) => {
  try {
    const rawRoom = req.query.room;
    const room =
      rawRoom ||
      `behavior-room-${Date.now()}-${Math.random().toString(36).substring(2, 8)}`;

    // Create a unique participant identity for each request.
    const identity = req.query.identity || `web-user-${Date.now()}`;

    const token = new AccessToken(
      process.env.LIVEKIT_API_KEY,
      process.env.LIVEKIT_API_SECRET,
      {
        identity,
      }
    );

    token.addGrant({
      roomJoin: true,
      room,
      canPublish: true,
      canSubscribe: true,
    });

    res.json({
      token: await token.toJwt(),
      room,
      serverUrl: process.env.LIVEKIT_URL,
    });

  } catch (error) {
    console.error("Token generation failed:", error);

    res.status(500).json({
      error: "Failed to generate LiveKit token",
    });
  }
});


// ---------------------------------------------------------
// ASSESSMENT RESULT
// ---------------------------------------------------------

app.get("/assessment-result", (req, res) => {
  try {
    const assessmentId = req.query.assessmentId;
    let resultPath;

    if (assessmentId) {
      const safeId = assessmentId.replace(/[^a-zA-Z0-9_-]/g, "_");
      resultPath = path.join(
        __dirname,
        "..",
        "agent",
        "assessments",
        `${safeId}.json`
      );
    } else {
      resultPath = path.join(
        __dirname,
        "..",
        "agent",
        "assessment_result.json"
      );
    }

    if (!fs.existsSync(resultPath)) {
      return res.status(404).json({
        error: "Assessment result not found",
      });
    }

    const result = fs.readFileSync(
      resultPath,
      "utf-8"
    );

    res.json(JSON.parse(result));

  } catch (error) {
    console.error(
      "Failed to read assessment result:",
      error
    );

    res.status(500).json({
      error: "Failed to read assessment result",
    });
  }
});


// ---------------------------------------------------------
// SERVER
// ---------------------------------------------------------

app.listen(3001, () => {
  console.log(
    "Token + Assessment API running on http://localhost:3001"
  );
});
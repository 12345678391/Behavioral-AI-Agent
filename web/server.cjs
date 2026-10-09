const express = require("express");
const { AccessToken } = require("livekit-server-sdk");
const cors = require("cors");
const fs = require("fs");
const path = require("path");
require("dotenv").config({ path: path.join(__dirname, ".env") });
require("dotenv").config({ path: path.join(__dirname, "..", ".env") });

const app = express();

const PRODUCTION_ORIGIN = "https://behavioral-ai-agent-src.vercel.app";

const configuredOrigins = (process.env.FRONTEND_ORIGIN || "")
  .split(",")
  .map((origin) => origin.trim().replace(/\/+$/, ""))
  .filter(Boolean);

const allowedOrigins = Array.from(
  new Set([PRODUCTION_ORIGIN, ...configuredOrigins])
);

function isOriginAllowed(origin) {
  // Allow non-browser requests (tools, curl, server-to-server, health checks)
  if (!origin) return true;

  const normalized = origin.trim().replace(/\/+$/, "");

  // 1. Exact match against production or configured origins
  if (allowedOrigins.includes(normalized)) {
    return true;
  }

  // 2. Local development origins (localhost or 127.0.0.1 on any port)
  const isLocalhost =
    /^http:\/\/localhost(:\d+)?$/i.test(normalized) ||
    /^http:\/\/127\.0\.0\.1(:\d+)?$/i.test(normalized);
  if (isLocalhost) {
    return true;
  }

  // 3. Safe, explicit Vercel preview deployments for this project
  const isVercelPreview =
    /^https:\/\/behavioral-ai-agent-src(-[a-zA-Z0-9_-]+)?\.vercel\.app$/i.test(
      normalized
    );
  if (isVercelPreview) {
    return true;
  }

  return false;
}

const corsOptions = {
  origin: (origin, callback) => {
    if (isOriginAllowed(origin)) {
      return callback(null, true);
    }
    callback(new Error("CORS request blocked by policy."));
  },
  credentials: true,
  methods: ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
  allowedHeaders: ["Content-Type", "Authorization", "X-Requested-With"],
};

app.use(cors(corsOptions));

app.use(express.json());

// ---------------------------------------------------------
// HEALTH CHECK
// ---------------------------------------------------------
app.get(["/health", "/api/health"], (req, res) => {
  res.json({
    status: "ok",
    message: "Token + Assessment API running on http://localhost:3001",
  });
});

// ---------------------------------------------------------
// LIVEKIT TOKEN
// ---------------------------------------------------------
app.get(["/token", "/api/token"], async (req, res) => {
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

app.get(["/assessment-result", "/api/assessment-result"], (req, res) => {
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
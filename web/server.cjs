const express = require("express");
const cors = require("cors");
const path = require("path");
require("dotenv").config({ path: path.join(__dirname, ".env") });
require("dotenv").config({ path: path.join(__dirname, "..", ".env") });

const routes = require("../backend/routes");

const app = express();
const PORT = process.env.PORT || 3001;

const DEFAULT_ALLOWED_ORIGINS = [
  "https://behavioral-ai-agent-src.vercel.app",
  "https://behavioral-ai-agent-i7so64hrb-behaviour-ai.vercel.app",
  "https://behavioral-ai-agent.vercel.app",
];

function getConfiguredOrigins() {
  return (process.env.FRONTEND_ORIGIN || "")
    .split(",")
    .map((origin) => origin.trim().replace(/\/+$/, ""))
    .filter(Boolean);
}

const allowedOrigins = Array.from(
  new Set([...DEFAULT_ALLOWED_ORIGINS, ...getConfiguredOrigins()])
);

function isOriginAllowed(origin) {
  // Allow non-browser requests (tools, curl, server-to-server, health checks)
  if (!origin) return true;

  const normalized = origin.trim().replace(/\/+$/, "");

  // 1. Exact match against default or statically configured origins
  if (allowedOrigins.includes(normalized)) {
    return true;
  }

  // 2. Exact match against dynamic/runtime FRONTEND_ORIGIN configuration
  const runtimeConfigured = getConfiguredOrigins();
  if (runtimeConfigured.includes(normalized)) {
    return true;
  }

  // 3. Local development origins (localhost or 127.0.0.1 on any port)
  const isLocalhost =
    /^http:\/\/localhost(:\d+)?$/i.test(normalized) ||
    /^http:\/\/127\.0\.0\.1(:\d+)?$/i.test(normalized);
  if (isLocalhost) {
    return true;
  }

  // 4. Safe, explicit Vercel preview and branch deployments for this project
  const isVercelPreview =
    /^https:\/\/(behavioral-ai-agent|behavioral-ai-agent-src)(-[a-zA-Z0-9_-]+)?\.vercel\.app$/i.test(
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

// Mount shared routes at both /api and / for compatibility with all frontend configs
app.use("/api", routes);
app.use("/", routes);

// ---------------------------------------------------------
// HEALTH CHECK
// ---------------------------------------------------------
app.get(["/health", "/api/health"], (req, res) => {
  res.json({
    status: "ok",
    message: `Token + Assessment + Recording API running on http://localhost:${PORT}`,
  });
});

// Error handler for CORS and general errors
app.use((err, req, res, next) => {
  if (err && err.message === "CORS request blocked by policy.") {
    return res.status(403).json({ error: "CORS request blocked by policy." });
  }
  next(err);
});

// ---------------------------------------------------------
// SERVER
// ---------------------------------------------------------
if (require.main === module) {
  app.listen(PORT, () => {
    console.log(
      `Token + Assessment + Recording API running on http://localhost:${PORT}`
    );
  });
}

module.exports = { app, isOriginAllowed };
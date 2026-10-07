const path = require("path");
require("dotenv").config({ path: path.join(__dirname, ".env") });
require("dotenv").config({ path: path.join(__dirname, "..", ".env") });

const express = require("express");
const cors = require("cors");
const routes = require("./routes");

const app = express();
const PORT = process.env.PORT || 5000;

// ============================================================
// CORS CONFIGURATION
// ============================================================
const allowedOrigin = process.env.FRONTEND_ORIGIN || "http://localhost:3000";

app.use(
  cors({
    origin: (origin, callback) => {
      // Allow non-browser requests (tools, curl, server-to-server)
      if (!origin) return callback(null, true);

      // Check allowed origin and local development patterns
      const isLocalhost =
        /^http:\/\/localhost(:\d+)?$/.test(origin) ||
        /^http:\/\/127\.0\.0\.1(:\d+)?$/.test(origin);

      if (origin === allowedOrigin || isLocalhost) {
        return callback(null, true);
      }

      callback(new Error("CORS request blocked by policy."));
    },
    credentials: true,
  })
);

app.use(express.json());

// Mount routes at both /api and / for compatibility with all frontend configs
app.use("/api", routes);
app.use("/", routes);

app.get("/health", (req, res) => {
  res.json({ status: "ok", message: "Behavioral AI Backend is running" });
});

app.listen(PORT, () => {
  console.log(`Server running on port ${PORT}`);
});
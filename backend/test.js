const express = require("express");
const routes = require("./routes");
const http = require("http");

process.env.LIVEKIT_API_KEY = "mock_key";
process.env.LIVEKIT_API_SECRET = "mock_secret_123456789012345678901234567890";
process.env.LIVEKIT_URL = "wss://mock.livekit.cloud";

const app = express();
app.use(express.json());
app.use("/api", routes);
app.use("/", routes);

const server = app.listen(0, async () => {
  const port = server.address().port;
  let failures = 0;

  try {
    // Test 1: Invalid identity query parameter (expect 400)
    const res1 = await fetch(`http://localhost:${port}/api/token?identity=bad%20identity!`);
    const data1 = await res1.json();
    if (res1.status === 400 && data1.error) {
      console.log("PASS: Invalid identity rejected with 400");
    } else {
      console.error("FAIL: Invalid identity test", res1.status, data1);
      failures++;
    }

    // Test 2: Missing assessmentId (expect 400)
    const res2 = await fetch(`http://localhost:${port}/api/assessment-result`);
    const data2 = await res2.json();
    if (res2.status === 400 && data2.error) {
      console.log("PASS: Missing assessmentId rejected with 400");
    } else {
      console.error("FAIL: Missing assessmentId test", res2.status, data2);
      failures++;
    }

    // Test 3: Non-existent assessmentId (expect 404)
    const res3 = await fetch(`http://localhost:${port}/api/assessment-result?assessmentId=test-nonexistent-123`);
    const data3 = await res3.json();
    if (res3.status === 404 && data3.error) {
      console.log("PASS: Nonexistent assessmentId returned 404");
    } else {
      console.error("FAIL: Nonexistent assessmentId test", res3.status, data3);
      failures++;
    }

    // Test 4: Token generation with unique dynamic room (expect 200)
    const res4 = await fetch(`http://localhost:${port}/api/token?identity=candidate-test`);
    const data4 = await res4.json();
    if (res4.status === 200 && data4.token && data4.room && data4.room.startsWith("behavior-room-")) {
      console.log("PASS: Token generated with dynamic room:", data4.room);
    } else {
      console.error("FAIL: Token generation test", res4.status, data4);
      failures++;
    }

    // Test 5: Verify dual-mounted /token route without /api prefix
    const res5 = await fetch(`http://localhost:${port}/token?identity=candidate-root`);
    const data5 = await res5.json();
    if (res5.status === 200 && data5.token) {
      console.log("PASS: Dual-mounted root route /token works");
    } else {
      console.error("FAIL: Root route test", res5.status, data5);
      failures++;
    }
  } catch (err) {
    console.error("Test execution error:", err);
    failures++;
  } finally {
    server.close();
    if (failures > 0) {
      process.exit(1);
    } else {
      console.log("All backend integration tests passed!");
      process.exit(0);
    }
  }
});

import {
  resolveApiBaseUrl,
  startRecording,
  stopRecording,
  getToken,
  getAssessmentResult,
  getRecordingStatus,
} from "./api";

describe("api.js module unit tests", () => {
  const originalFetch = global.fetch;

  afterEach(() => {
    global.fetch = originalFetch;
    jest.restoreAllMocks();
  });

  describe("resolveApiBaseUrl", () => {
    test("redirects backup backend host to new feature backend", () => {
      const backupUrl = "https://behavioral-ai-api-backup.onrender.com";
      const resolved = resolveApiBaseUrl(backupUrl, "production");
      expect(resolved).toBe("https://behavioral-backend-gc62.onrender.com");
    });

    test("redirects backup backend host even in non-production environments", () => {
      const backupUrl = "https://behavioral-ai-api-backup.onrender.com/";
      const resolved = resolveApiBaseUrl(backupUrl, "development");
      expect(resolved).toBe("https://behavioral-backend-gc62.onrender.com");
    });

    test("defaults to feature backend in production when no url provided", () => {
      const resolved = resolveApiBaseUrl("", "production");
      expect(resolved).toBe("https://behavioral-backend-gc62.onrender.com");
    });

    test("defaults to localhost:3001 in development when no url provided", () => {
      const resolved = resolveApiBaseUrl("", "development");
      expect(resolved).toBe("http://localhost:3001");
    });

    test("preserves valid custom backend url without trailing slashes", () => {
      const customUrl = "https://custom-backend.example.com///";
      const resolved = resolveApiBaseUrl(customUrl, "production");
      expect(resolved).toBe("https://custom-backend.example.com");
    });
  });

  describe("stopRecording endpoint integration", () => {
    test("includes Authorization Bearer header when token is provided", async () => {
      const mockResponse = {
        ok: true,
        json: jest.fn().mockResolvedValue({ success: true, status: "finalizing" }),
      };
      global.fetch = jest.fn().mockResolvedValue(mockResponse);

      const result = await stopRecording("test-room-123", "capability-token-xyz");
      expect(result).toEqual({ success: true, status: "finalizing" });

      expect(global.fetch).toHaveBeenCalledTimes(1);
      const [url, options] = global.fetch.mock.calls[0];
      expect(url).toMatch(/\/recording\/stop$/);
      expect(options.method).toBe("POST");
      expect(options.headers["Content-Type"]).toBe("application/json");
      expect(options.headers["Authorization"]).toBe("Bearer capability-token-xyz");
      expect(JSON.parse(options.body)).toEqual({ room: "test-room-123" });
    });

    test("omits Authorization header when token is not provided", async () => {
      const mockResponse = {
        ok: true,
        json: jest.fn().mockResolvedValue({ success: true, status: "finalizing" }),
      };
      global.fetch = jest.fn().mockResolvedValue(mockResponse);

      await stopRecording("test-room-456");
      const [, options] = global.fetch.mock.calls[0];
      expect(options.headers["Authorization"]).toBeUndefined();
    });

    test("throws error if room name is missing", async () => {
      await expect(stopRecording("")).rejects.toThrow("Room name is required");
    });
  });

  describe("startRecording endpoint integration", () => {
    test("sends POST to /recording/start with room in body", async () => {
      const mockResponse = {
        ok: true,
        json: jest.fn().mockResolvedValue({ success: true, status: "active", egressId: "egress-1" }),
      };
      global.fetch = jest.fn().mockResolvedValue(mockResponse);

      const result = await startRecording("room-abc");
      expect(result).toEqual({ success: true, status: "active", egressId: "egress-1" });

      expect(global.fetch).toHaveBeenCalledTimes(1);
      const [url, options] = global.fetch.mock.calls[0];
      expect(url).toMatch(/\/recording\/start$/);
      expect(options.method).toBe("POST");
      expect(JSON.parse(options.body)).toEqual({ room: "room-abc" });
    });

    test("throws error if room name is missing", async () => {
      await expect(startRecording("")).rejects.toThrow("Room name is required");
    });
  });
});

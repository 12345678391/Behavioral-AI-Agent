const crypto = require("crypto");
const {
  EgressClient,
  EncodedFileOutput,
  S3Upload,
  GCPUpload,
  AzureBlobUpload,
  EgressStatus,
} = require("livekit-server-sdk");

function sanitizeEnvValue(val) {
  if (!val || typeof val !== "string") return "";
  let cleaned = val.trim().replace(/[\r\n]/g, "").trim();
  if (
    (cleaned.startsWith('"') && cleaned.endsWith('"')) ||
    (cleaned.startsWith("'") && cleaned.endsWith("'"))
  ) {
    cleaned = cleaned.slice(1, -1).trim();
  }
  return cleaned;
}

// In-memory registry for recording sessions (keyed by roomName)
const recordingSessions = new Map();
let mockEgressClient = null;

function setEgressClientForTest(client) {
  mockEgressClient = client;
}

function resetRecordingSessionsForTest() {
  recordingSessions.clear();
  mockEgressClient = null;
}

function getStorageConfig() {
  // 1. AWS S3 / S3-compatible (Cloudflare R2, MinIO, Wasabi, AWS S3)
  const s3Key = sanitizeEnvValue(
    process.env.S3_KEY_ID || process.env.AWS_ACCESS_KEY_ID || process.env.R2_ACCESS_KEY_ID
  );
  const s3Secret = sanitizeEnvValue(
    process.env.S3_KEY_SECRET || process.env.AWS_SECRET_ACCESS_KEY || process.env.R2_SECRET_ACCESS_KEY
  );
  const s3Bucket = sanitizeEnvValue(
    process.env.S3_BUCKET || process.env.AWS_BUCKET || process.env.R2_BUCKET
  );
  const s3Region = sanitizeEnvValue(
    process.env.S3_REGION || process.env.AWS_REGION || process.env.R2_REGION || "auto"
  );
  const s3Endpoint = sanitizeEnvValue(process.env.S3_ENDPOINT || process.env.R2_ENDPOINT);
  const isR2 = Boolean(s3Endpoint && s3Endpoint.includes("r2.cloudflarestorage.com"));
  const forcePathStyle = process.env.S3_FORCE_PATH_STYLE !== undefined ? process.env.S3_FORCE_PATH_STYLE === "true" : isR2;

  if (s3Key && s3Secret && s3Bucket) {
    return {
      type: "s3",
      configured: true,
      bucket: s3Bucket,
      region: s3Region,
      endpoint: s3Endpoint,
      uploadObject: new S3Upload({
        accessKey: s3Key,
        secret: s3Secret,
        bucket: s3Bucket,
        region: s3Region,
        endpoint: s3Endpoint,
        forcePathStyle,
      }),
    };
  }

  // 2. Google Cloud Storage (GCP)
  const gcpBucket = sanitizeEnvValue(process.env.GCP_BUCKET);
  const gcpCredentials = sanitizeEnvValue(process.env.GCP_CREDENTIALS);
  if (gcpBucket && gcpCredentials) {
    return {
      type: "gcp",
      configured: true,
      bucket: gcpBucket,
      uploadObject: new GCPUpload({
        credentials: gcpCredentials,
        bucket: gcpBucket,
      }),
    };
  }

  // 3. Azure Blob Storage
  const azureAccount = sanitizeEnvValue(process.env.AZURE_ACCOUNT_NAME);
  const azureKey = sanitizeEnvValue(process.env.AZURE_ACCOUNT_KEY);
  const azureContainer = sanitizeEnvValue(process.env.AZURE_CONTAINER_NAME);
  if (azureAccount && azureKey && azureContainer) {
    return {
      type: "azure",
      configured: true,
      account: azureAccount,
      container: azureContainer,
      uploadObject: new AzureBlobUpload({
        accountName: azureAccount,
        accountKey: azureKey,
        containerName: azureContainer,
      }),
    };
  }

  return {
    type: null,
    configured: false,
  };
}

function computePlaybackUrl(storageConfig, filepath, fileResults, result) {
  // If a public base URL is configured, it takes priority for browser playback (e.g. Cloudflare R2, CDN)
  const publicBaseUrl = sanitizeEnvValue(
    process.env.RECORDING_BASE_URL ||
    process.env.S3_PUBLIC_BASE_URL ||
    process.env.R2_PUBLIC_URL ||
    process.env.STORAGE_PUBLIC_URL
  );

  if (publicBaseUrl && filepath) {
    return `${publicBaseUrl.replace(/\/+$/, "")}/${filepath.replace(/^\/+/, "")}`;
  }

  // If fileResults provided a direct HTTP URL, use it directly
  if (Array.isArray(fileResults) && fileResults.length > 0) {
    for (const res of fileResults) {
      if (res && typeof res.location === "string" && /^https?:\/\//i.test(res.location)) {
        return res.location;
      }
    }
  }

  // Check protobuf oneof result (e.g. result: { case: 'file', value: { location: ... } })
  if (
    result &&
    result.value &&
    typeof result.value.location === "string" &&
    /^https?:\/\//i.test(result.value.location)
  ) {
    return result.value.location;
  }

  if (!storageConfig || !storageConfig.configured || !filepath) {
    return null;
  }

  const cleanFilepath = filepath.replace(/^\/+/, "");

  if (storageConfig.type === "s3") {
    if (storageConfig.endpoint) {
      return `${storageConfig.endpoint.replace(/\/+$/, "")}/${storageConfig.bucket}/${cleanFilepath}`;
    }
    return `https://${storageConfig.bucket}.s3.${storageConfig.region}.amazonaws.com/${cleanFilepath}`;
  }

  if (storageConfig.type === "gcp") {
    return `https://storage.googleapis.com/${storageConfig.bucket}/${cleanFilepath}`;
  }

  if (storageConfig.type === "azure") {
    return `https://${storageConfig.account}.blob.core.windows.net/${storageConfig.container}/${cleanFilepath}`;
  }

  return null;
}

function getSignatureKey(key, dateStamp, regionName, serviceName) {
  const kDate = crypto.createHmac("sha256", "AWS4" + key).update(dateStamp).digest();
  const kRegion = crypto.createHmac("sha256", kDate).update(regionName).digest();
  const kService = crypto.createHmac("sha256", kRegion).update(serviceName).digest();
  const kSigning = crypto.createHmac("sha256", kService).update("aws4_request").digest();
  return kSigning;
}

function generatePresignedPlaybackUrl(
  storageConfig,
  filepath,
  expiresInSeconds = 900,
  now = new Date()
) {
  if (
    !storageConfig ||
    !storageConfig.configured ||
    storageConfig.type !== "s3" ||
    !filepath
  ) {
    return null;
  }

  const accessKeyId = sanitizeEnvValue(
    process.env.S3_KEY_ID || process.env.AWS_ACCESS_KEY_ID
  );
  const secretAccessKey = sanitizeEnvValue(
    process.env.S3_KEY_SECRET || process.env.AWS_SECRET_ACCESS_KEY
  );
  const bucket = sanitizeEnvValue(
    storageConfig.bucket || process.env.S3_BUCKET || process.env.AWS_BUCKET || process.env.R2_BUCKET
  );
  const region = sanitizeEnvValue(
    storageConfig.region || process.env.S3_REGION || process.env.AWS_REGION || process.env.R2_REGION || "auto"
  );
  const rawEndpoint = sanitizeEnvValue(
    storageConfig.endpoint || process.env.S3_ENDPOINT || process.env.R2_ENDPOINT
  );

  if (!accessKeyId || !secretAccessKey || !bucket) {
    return null;
  }

  const cleanFilepath = filepath.replace(/^\/+/, "");
  let host = "";
  let protocol = "https:";
  let canonicalUri = "";

  if (rawEndpoint) {
    try {
      const parsedEndpoint = new URL(rawEndpoint);
      host = parsedEndpoint.host;
      protocol = parsedEndpoint.protocol;
      canonicalUri = `/${encodeURIComponent(bucket)}/${cleanFilepath
        .split("/")
        .map(encodeURIComponent)
        .join("/")}`;
    } catch (_) {
      return null;
    }
  } else {
    host = `${bucket}.s3.${region}.amazonaws.com`;
    protocol = "https:";
    canonicalUri = `/${cleanFilepath
      .split("/")
      .map(encodeURIComponent)
      .join("/")}`;
  }

  const amzDate = now.toISOString().replace(/[:-]|\.\d{3}/g, "");
  const dateStamp = amzDate.substring(0, 8);
  const service = "s3";
  const credentialScope = `${dateStamp}/${region}/${service}/aws4_request`;

  const queryParams = [
    `X-Amz-Algorithm=AWS4-HMAC-SHA256`,
    `X-Amz-Credential=${encodeURIComponent(`${accessKeyId}/${credentialScope}`)}`,
    `X-Amz-Date=${amzDate}`,
    `X-Amz-Expires=${expiresInSeconds}`,
    `X-Amz-SignedHeaders=host`,
  ];
  const canonicalQueryString = queryParams.join("&");

  // Only host is signed. The HTTP Range header sent by HTML5 video players
  // is deliberately excluded to support video seeking, scrubbing, and 206 Partial Content.
  const canonicalHeaders = `host:${host}\n`;
  const signedHeaders = "host";
  const payloadHash = "UNSIGNED-PAYLOAD";

  const canonicalRequest = [
    "GET",
    canonicalUri,
    canonicalQueryString,
    canonicalHeaders,
    signedHeaders,
    payloadHash,
  ].join("\n");

  const canonicalRequestHash = crypto
    .createHash("sha256")
    .update(canonicalRequest, "utf8")
    .digest("hex");

  const stringToSign = [
    "AWS4-HMAC-SHA256",
    amzDate,
    credentialScope,
    canonicalRequestHash,
  ].join("\n");

  const signingKey = getSignatureKey(secretAccessKey, dateStamp, region, service);
  const signature = crypto
    .createHmac("sha256", signingKey)
    .update(stringToSign, "utf8")
    .digest("hex");

  return `${protocol}//${host}${canonicalUri}?${canonicalQueryString}&X-Amz-Signature=${signature}`;
}

function getEgressClient() {
  if (mockEgressClient) {
    return mockEgressClient;
  }

  const livekitUrl = sanitizeEnvValue(process.env.LIVEKIT_URL);
  const apiKey = sanitizeEnvValue(process.env.LIVEKIT_API_KEY);
  const apiSecret = sanitizeEnvValue(process.env.LIVEKIT_API_SECRET);

  if (!livekitUrl || !apiKey || !apiSecret) {
    return null;
  }

  return new EgressClient(livekitUrl, apiKey, apiSecret);
}

function isValidRoomName(room) {
  return (
    typeof room === "string" &&
    room.length > 0 &&
    room.length <= 128 &&
    /^[a-zA-Z0-9_-]+$/.test(room)
  );
}

async function startRecording(roomName) {
  if (!isValidRoomName(roomName)) {
    return {
      success: false,
      status: "failed",
      error: "Invalid room name for recording.",
    };
  }

  // Check if recording already active or finalizing
  const existing = recordingSessions.get(roomName);
  if (existing && (existing.status === "active" || existing.status === "starting")) {
    return {
      success: true,
      status: existing.status,
      egressId: existing.egressId,
    };
  }

  const storageConfig = getStorageConfig();
  if (!storageConfig.configured) {
    const session = {
      roomName,
      status: "failed",
      egressId: null,
      filepath: null,
      videoUrl: null,
      error:
        "Recording storage destination is not configured. Set S3, Azure Blob, or GCP credentials in server environment.",
      startedAt: Date.now(),
      endedAt: null,
      storageType: null,
    };
    recordingSessions.set(roomName, session);
    return {
      success: false,
      status: "failed",
      error: session.error,
    };
  }

  const client = getEgressClient();
  if (!client) {
    const session = {
      roomName,
      status: "failed",
      egressId: null,
      filepath: null,
      videoUrl: null,
      error: "LiveKit server credentials are not configured on server.",
      startedAt: Date.now(),
      endedAt: null,
      storageType: storageConfig.type,
    };
    recordingSessions.set(roomName, session);
    return {
      success: false,
      status: "failed",
      error: session.error,
    };
  }

  const timestamp = Date.now();
  const filepath = `recordings/${roomName}-${timestamp}.mp4`;

  try {
    const fileOutput = new EncodedFileOutput({
      filepath,
      output: {
        case: storageConfig.type,
        value: storageConfig.uploadObject,
      },
    });

    const info = await client.startRoomCompositeEgress(
      roomName,
      fileOutput,
      { layout: "speaker" }
    );

    const egressId = info?.egressId;
    if (!egressId) {
      throw new Error(info?.error || "LiveKit Egress service did not return an egressId.");
    }

    if (
      info.status === EgressStatus.EGRESS_FAILED ||
      info.status === 4 ||
      info.status === EgressStatus.EGRESS_ABORTED ||
      info.status === 5
    ) {
      throw new Error(info.error || "LiveKit Egress failed to start.");
    }

    const session = {
      roomName,
      status: "active",
      egressId,
      filepath,
      videoUrl: null,
      error: null,
      startedAt: timestamp,
      endedAt: null,
      storageType: storageConfig.type,
    };

    recordingSessions.set(roomName, session);

    return {
      success: true,
      status: "active",
      egressId,
    };
  } catch (error) {
    // Sanitize error message to prevent accidental credential or auth header leakage
    const rawError = error?.message || String(error);
    const sanitizedError = rawError.replace(/(Bearer\s+[A-Za-z0-9._-]+)/gi, "[REDACTED]");

    console.error(`[Recording] LiveKit Egress start error for room ${roomName}:`, sanitizedError);

    const isQuotaOrLimitError =
      /limit exceeded|quota exceeded|free-tier|resource limit|project limit|egress limit|429|exceeded.*limit/i.test(
        sanitizedError
      );

    const status = isQuotaOrLimitError ? "unavailable" : "failed";
    const errorMessage = isQuotaOrLimitError
      ? "LiveKit Cloud recording is unavailable: free-tier project egress limit exceeded. The assessment and behavioral scoring continue normally."
      : `LiveKit Egress service unavailable: ${sanitizedError}`;

    const session = {
      roomName,
      status,
      egressId: null,
      filepath: null,
      videoUrl: null,
      error: errorMessage,
      startedAt: timestamp,
      endedAt: null,
      storageType: storageConfig.type,
    };

    recordingSessions.set(roomName, session);

    return {
      success: false,
      status,
      error: session.error,
    };
  }
}

async function stopRecording(roomName) {
  if (!isValidRoomName(roomName)) {
    return {
      success: false,
      status: "failed",
      error: "Invalid room name.",
    };
  }

  const session = recordingSessions.get(roomName);
  if (!session) {
    return {
      success: true,
      roomName,
      status: "unavailable",
      videoUrl: null,
      error: "No recording session found for this room.",
    };
  }

  if (
    session.status === "completed" ||
    session.status === "failed" ||
    session.status === "unavailable"
  ) {
    return {
      success: true,
      ...session,
    };
  }

  session.status = "finalizing";
  session.endedAt = Date.now();

  if (session.egressId) {
    try {
      const client = getEgressClient();
      if (client && typeof client.stopEgress === "function") {
        const info = await client.stopEgress(session.egressId);
        if (info) {
          const isComplete =
            info.status === EgressStatus.EGRESS_COMPLETE ||
            info.status === 3 ||
            info.status === "EGRESS_COMPLETE";

          const isFailed =
            info.status === EgressStatus.EGRESS_FAILED ||
            info.status === 4 ||
            info.status === "EGRESS_FAILED" ||
            info.status === EgressStatus.EGRESS_ABORTED ||
            info.status === 5;

          if (isComplete) {
            session.status = "completed";
            const storageConfig = getStorageConfig();
            session.videoUrl = computePlaybackUrl(
              storageConfig,
              session.filepath,
              info.fileResults,
              info.result
            );
            session.error = null;
          } else if (isFailed) {
            session.status = "failed";
            session.error = info.error || "Recording failed during LiveKit processing.";
          }
        }
      }
    } catch (err) {
      console.warn(`[Recording] Notice on stopEgress for room ${roomName}:`, err?.message || err);
    }
  }

  return {
    success: true,
    ...session,
  };
}

async function getRecordingStatus(roomName) {
  if (!isValidRoomName(roomName)) {
    return {
      success: false,
      status: "failed",
      error: "Invalid room name.",
    };
  }

  const session = recordingSessions.get(roomName);
  if (!session) {
    return {
      success: true,
      room: roomName,
      status: "unavailable",
      videoUrl: null,
      error: "No recording was started for this interview.",
    };
  }

  // If already completed, failed, or unavailable, return latest status
  if (
    session.status === "completed" ||
    session.status === "failed" ||
    session.status === "unavailable"
  ) {
    return {
      success: true,
      room: roomName,
      status: session.status,
      videoUrl: session.videoUrl,
      error: session.error,
    };
  }

  // If active or finalizing with an egressId, query LiveKit Egress server
  if (session.egressId) {
    try {
      const client = getEgressClient();
      if (client && typeof client.listEgress === "function") {
        const items = await client.listEgress({ egressId: session.egressId });
        const info = Array.isArray(items) && items.length > 0 ? items[0] : null;

        if (info) {
          const isComplete =
            info.status === EgressStatus.EGRESS_COMPLETE ||
            info.status === 3 ||
            info.status === "EGRESS_COMPLETE";

          const isFailed =
            info.status === EgressStatus.EGRESS_FAILED ||
            info.status === 4 ||
            info.status === "EGRESS_FAILED" ||
            info.status === EgressStatus.EGRESS_ABORTED ||
            info.status === 5;

          if (isComplete) {
            session.status = "completed";
            const storageConfig = getStorageConfig();
            session.videoUrl = computePlaybackUrl(
              storageConfig,
              session.filepath,
              info.fileResults,
              info.result
            );
            session.error = null;
          } else if (isFailed) {
            session.status = "failed";
            session.error = info.error || "Recording failed during LiveKit processing.";
          }
        }
      }
    } catch (err) {
      // Non-fatal status polling notice
    }
  }

  return {
    success: true,
    room: roomName,
    status: session.status,
    filepath: session.filepath,
    videoUrl: session.videoUrl,
    error: session.error,
  };
}

function getRecordingSession(roomName) {
  return recordingSessions.get(roomName) || null;
}

function setRecordingSession(roomName, sessionData) {
  recordingSessions.set(roomName, sessionData);
}

module.exports = {
  getStorageConfig,
  computePlaybackUrl,
  generatePresignedPlaybackUrl,
  startRecording,
  stopRecording,
  getRecordingStatus,
  getRecordingSession,
  setRecordingSession,
  setEgressClientForTest,
  resetRecordingSessionsForTest,
  isValidRoomName,
};

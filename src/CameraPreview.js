import React, { useEffect, useRef, useState, useCallback } from "react";

export default function CameraPreview({ isActive = true }) {
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const isRequestingRef = useRef(false);
  const isMountedRef = useRef(true);

  const [status, setStatus] = useState("idle"); // 'idle' | 'requesting' | 'active' | 'denied' | 'not-found' | 'busy' | 'unsupported' | 'error'
  const [errorMessage, setErrorMessage] = useState("");

  const stopCamera = useCallback(() => {
    if (streamRef.current) {
      try {
        const tracks = streamRef.current.getTracks();
        tracks.forEach((track) => {
          track.stop();
        });
      } catch (err) {
        console.warn("[CameraPreview] Error stopping tracks:", err);
      }
      streamRef.current = null;
    }

    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }

    if (isMountedRef.current) {
      setStatus("idle");
      setErrorMessage("");
    }
  }, []);

  const startCamera = useCallback(async () => {
    // Check browser support
    if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== "function") {
      if (isMountedRef.current) {
        setStatus("unsupported");
        setErrorMessage("Camera access is not supported by your current browser or environment.");
      }
      return;
    }

    // Prevent duplicate concurrent requests or duplicate streams
    if (isRequestingRef.current) {
      return;
    }
    if (streamRef.current && streamRef.current.active) {
      return;
    }

    // Stop any stale tracks before starting fresh
    if (streamRef.current) {
      stopCamera();
    }

    isRequestingRef.current = true;
    if (isMountedRef.current) {
      setStatus("requesting");
      setErrorMessage("");
    }

    try {
      // Local-only video: explicitly audio: false to prevent touching or colliding with LiveKit mic
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          width: { ideal: 640 },
          height: { ideal: 480 },
          facingMode: "user",
        },
        audio: false,
      });

      // If component unmounted or was deactivated while awaiting getUserMedia, abort immediately
      if (!isMountedRef.current || !isActive) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }

      streamRef.current = stream;

      // Handle external track ending (e.g. webcam unplugged)
      stream.getVideoTracks().forEach((track) => {
        track.onended = () => {
          if (isMountedRef.current) {
            setStatus("not-found");
            setErrorMessage("Camera device was disconnected.");
          }
        };
      });

      if (videoRef.current) {
        videoRef.current.srcObject = stream;
      }

      if (isMountedRef.current) {
        setStatus("active");
        setErrorMessage("");
      }
    } catch (err) {
      if (!isMountedRef.current) return;

      const name = err?.name || "";
      if (name === "NotAllowedError" || name === "PermissionDeniedError") {
        setStatus("denied");
        setErrorMessage(
          "Camera access was denied. Please allow camera permissions in your browser settings to display your video preview."
        );
      } else if (name === "NotFoundError" || name === "DevicesNotFoundError") {
        setStatus("not-found");
        setErrorMessage("No camera detected. Please connect a webcam to display your video preview.");
      } else if (name === "NotReadableError" || name === "TrackStartError") {
        setStatus("busy");
        setErrorMessage("Camera is currently in use by another application or busy.");
      } else {
        setStatus("error");
        setErrorMessage(err?.message || "Unable to access camera preview.");
      }
    } finally {
      isRequestingRef.current = false;
    }
  }, [isActive, stopCamera]);

  useEffect(() => {
    isMountedRef.current = true;

    if (isActive) {
      startCamera();
    } else {
      stopCamera();
    }

    return () => {
      isMountedRef.current = false;
      stopCamera();
    };
  }, [isActive, startCamera, stopCamera]);

  return (
    <div
      className="rounded-3xl border border-slate-800 bg-slate-900 p-6"
      data-testid="camera-preview-card"
    >
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold flex items-center gap-2">
          <span aria-hidden="true">📹</span> Camera Preview
        </h3>

        <span
          className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
            status === "active"
              ? "bg-green-500/10 text-green-400 border border-green-500/20"
              : status === "requesting"
              ? "bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 animate-pulse"
              : "bg-slate-800 text-slate-400"
          }`}
          data-testid="camera-status-badge"
        >
          {status === "active"
            ? "Live Preview"
            : status === "requesting"
            ? "Connecting..."
            : "Off"}
        </span>
      </div>

      <div className="relative rounded-2xl overflow-hidden bg-slate-950 aspect-video flex items-center justify-center border border-slate-800">
        {/* Active live video stream */}
        <video
          ref={videoRef}
          autoPlay
          playsInline
          muted
          data-testid="camera-video-element"
          className={`w-full h-full object-cover -scale-x-100 ${
            status === "active" ? "block" : "hidden"
          }`}
        />

        {status === "active" && (
          <div className="absolute bottom-2.5 left-2.5 rounded-md bg-slate-900/80 px-2 py-0.5 text-[11px] font-medium text-slate-300 backdrop-blur-sm border border-slate-700/50">
            Self-View (Live)
          </div>
        )}

        {/* Requesting state */}
        {status === "requesting" && (
          <div className="flex flex-col items-center justify-center p-4 text-center">
            <svg
              className="h-6 w-6 animate-spin text-indigo-400 mb-2"
              viewBox="0 0 24 24"
              fill="none"
            >
              <circle
                className="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                strokeWidth="4"
              />
              <path
                className="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
              />
            </svg>
            <p className="text-xs text-slate-400">Requesting camera access...</p>
          </div>
        )}

        {/* Error / Denied / Not found / Unsupported state */}
        {status !== "active" && status !== "requesting" && (
          <div className="flex flex-col items-center justify-center p-5 text-center text-slate-400">
            <span className="text-3xl mb-2" aria-hidden="true">
              {status === "denied"
                ? "🚫"
                : status === "not-found"
                ? "📷"
                : status === "busy"
                ? "⚠️"
                : "📹"}
            </span>

            <p className="text-xs leading-relaxed text-slate-300 max-w-[220px]">
              {errorMessage || "Camera preview is currently inactive."}
            </p>

            {isActive && status !== "idle" && status !== "unsupported" && (
              <button
                type="button"
                onClick={startCamera}
                data-testid="retry-camera-btn"
                className="mt-3 rounded-lg border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:bg-slate-700 hover:text-white"
              >
                Retry Camera
              </button>
            )}
          </div>
        )}
      </div>

      <p className="mt-3 text-xs text-slate-500">
        Local self-view only. Your video is not recorded or shared.
      </p>
    </div>
  );
}

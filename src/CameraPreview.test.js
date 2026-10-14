import { render, screen, fireEvent, act } from "@testing-library/react";
import CameraPreview from "./CameraPreview";

describe("CameraPreview component", () => {
  let originalMediaDevices;
  let mockTrack;
  let mockStream;

  beforeEach(() => {
    mockTrack = {
      kind: "video",
      stop: jest.fn(),
      onended: null,
    };

    mockStream = {
      active: true,
      getTracks: jest.fn(() => [mockTrack]),
      getVideoTracks: jest.fn(() => [mockTrack]),
    };

    originalMediaDevices = navigator.mediaDevices;
    Object.defineProperty(navigator, "mediaDevices", {
      writable: true,
      value: {
        getUserMedia: jest.fn().mockResolvedValue(mockStream),
      },
    });
  });

  afterEach(() => {
    Object.defineProperty(navigator, "mediaDevices", {
      writable: true,
      value: originalMediaDevices,
    });
    jest.clearAllMocks();
  });

  test("renders camera preview card with live status and attaches stream", async () => {
    await act(async () => {
      render(<CameraPreview isActive={true} />);
    });

    expect(screen.getByTestId("camera-preview-card")).toBeInTheDocument();
    expect(screen.getByText(/Camera Preview/i)).toBeInTheDocument();
    expect(screen.getByTestId("camera-status-badge")).toHaveTextContent(/Live Preview/i);
    expect(screen.getByText(/Self-View \(Live\)/i)).toBeInTheDocument();

    const videoEl = screen.getByTestId("camera-video-element");
    expect(videoEl).toBeInTheDocument();
    expect(videoEl.srcObject).toBe(mockStream);
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledWith({
      video: {
        width: { ideal: 640 },
        height: { ideal: 480 },
        facingMode: "user",
      },
      audio: false,
    });
  });

  test("handles permission denied with professional message and retry button", async () => {
    const permError = new Error("Permission denied");
    permError.name = "NotAllowedError";
    navigator.mediaDevices.getUserMedia.mockRejectedValueOnce(permError);

    await act(async () => {
      render(<CameraPreview isActive={true} />);
    });

    expect(screen.getByTestId("camera-status-badge")).toHaveTextContent(/Off/i);
    expect(
      screen.getByText(/Camera access was denied\. Please allow camera permissions/i)
    ).toBeInTheDocument();

    const retryBtn = screen.getByTestId("retry-camera-btn");
    expect(retryBtn).toBeInTheDocument();

    // Clicking retry attempts to request again
    navigator.mediaDevices.getUserMedia.mockResolvedValueOnce(mockStream);
    await act(async () => {
      fireEvent.click(retryBtn);
    });

    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("camera-status-badge")).toHaveTextContent(/Live Preview/i);
  });

  test("handles no camera detected (NotFoundError) with clear guidance", async () => {
    const notFoundError = new Error("No device");
    notFoundError.name = "NotFoundError";
    navigator.mediaDevices.getUserMedia.mockRejectedValueOnce(notFoundError);

    await act(async () => {
      render(<CameraPreview isActive={true} />);
    });

    expect(
      screen.getByText(/No camera detected\. Please connect a webcam/i)
    ).toBeInTheDocument();
    expect(screen.getByTestId("retry-camera-btn")).toBeInTheDocument();
  });

  test("handles busy camera (NotReadableError) with clear notice", async () => {
    const busyError = new Error("Hardware busy");
    busyError.name = "NotReadableError";
    navigator.mediaDevices.getUserMedia.mockRejectedValueOnce(busyError);

    await act(async () => {
      render(<CameraPreview isActive={true} />);
    });

    expect(
      screen.getByText(/Camera is currently in use by another application or busy/i)
    ).toBeInTheDocument();
  });

  test("handles unsupported browser environment when getUserMedia is missing", async () => {
    delete navigator.mediaDevices.getUserMedia;

    await act(async () => {
      render(<CameraPreview isActive={true} />);
    });

    expect(
      screen.getByText(/Camera access is not supported by your current browser/i)
    ).toBeInTheDocument();
    expect(screen.queryByTestId("retry-camera-btn")).not.toBeInTheDocument();
  });

  test("immediately stops all tracks when isActive transitions to false (disconnection/end)", async () => {
    const { rerender } = render(<CameraPreview isActive={true} />);
    await act(async () => {});

    expect(mockTrack.stop).not.toHaveBeenCalled();

    // Deactivate preview (e.g. room disconnected or assessment ended)
    await act(async () => {
      rerender(<CameraPreview isActive={false} />);
    });

    expect(mockTrack.stop).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("camera-status-badge")).toHaveTextContent(/Off/i);
  });

  test("stops all tracks on unmount to prevent media leaks", async () => {
    const { unmount } = render(<CameraPreview isActive={true} />);
    await act(async () => {});

    expect(mockTrack.stop).not.toHaveBeenCalled();

    unmount();
    expect(mockTrack.stop).toHaveBeenCalledTimes(1);
  });

  test("prevents duplicate streams when already active", async () => {
    const { rerender } = render(<CameraPreview isActive={true} />);
    await act(async () => {});

    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledTimes(1);

    // Re-render with same active status should not re-query getUserMedia
    rerender(<CameraPreview isActive={true} />);
    await act(async () => {});

    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledTimes(1);
  });
});

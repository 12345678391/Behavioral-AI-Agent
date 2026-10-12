import {
  Room,
  RoomEvent,
  Track,
} from "livekit-client";

export async function joinRoom(
  token,
  serverUrl,
  onData,
  onConnected,
  onDisconnected,
  onMicrophoneError,
  onAudioPlaybackStatusChanged
) {
  const room = new Room({
    adaptiveStream: true,
    dynacast: true,
  });

  // ==========================================================
  // AI AUDIO
  // ==========================================================

  room.on(
    RoomEvent.TrackSubscribed,
    async (track, publication, participant) => {
      console.log(
        "[LiveKit Audio] Track subscribed:",
        track.kind,
        "name:",
        track.name,
        "sid:",
        track.sid,
        "from:",
        participant.identity
      );

      if (track.kind !== Track.Kind.Audio) {
        return;
      }

      console.log("[LiveKit Audio] AI audio track received. Attaching media element...");

      let audioElement = document.querySelector(
        `[data-livekit-track-sid="${track.sid}"]`
      );

      if (!audioElement) {
        audioElement = track.attach();
        audioElement.autoplay = true;
        audioElement.muted = false;
        audioElement.volume = 1;

        // Use offscreen layout instead of display: none to prevent browser audio pipeline throttling
        audioElement.style.position = "fixed";
        audioElement.style.top = "-9999px";
        audioElement.style.left = "-9999px";
        audioElement.style.width = "1px";
        audioElement.style.height = "1px";
        audioElement.style.opacity = "0.001";
        audioElement.style.pointerEvents = "none";
        audioElement.setAttribute("data-livekit-track-sid", track.sid);

        // Attach comprehensive audio diagnostic listeners
        audioElement.addEventListener("playing", () => {
          console.log(
            `[LiveKit Audio] AI audio element is actively PLAYING. Volume=${audioElement.volume}, Muted=${audioElement.muted}`
          );
          if (onAudioPlaybackStatusChanged) {
            onAudioPlaybackStatusChanged(true);
          }
        });

        audioElement.addEventListener("pause", () => {
          console.warn("[LiveKit Audio] AI audio element was PAUSED.");
        });

        audioElement.addEventListener("waiting", () => {
          console.log("[LiveKit Audio] AI audio element is WAITING for audio frames...");
        });

        audioElement.addEventListener("error", () => {
          console.error(
            "[LiveKit Audio] AI audio element playback ERROR:",
            audioElement.error
          );
          if (onAudioPlaybackStatusChanged) {
            onAudioPlaybackStatusChanged(false);
          }
        });

        document.body.appendChild(audioElement);
      }

      try {
        await audioElement.play();
        console.log("[LiveKit Audio] AI audio element play() succeeded.");
        if (onAudioPlaybackStatusChanged) {
          onAudioPlaybackStatusChanged(true);
        }
      } catch (error) {
        console.warn(
          "[LiveKit Audio] Initial audioElement.play() blocked by browser autoplay policy:",
          error?.message || error
        );

        try {
          await room.startAudio();
          await audioElement.play();
          console.log("[LiveKit Audio] AI audio playback started after room.startAudio().");
          if (onAudioPlaybackStatusChanged) {
            onAudioPlaybackStatusChanged(true);
          }
        } catch (audioError) {
          console.warn(
            "[LiveKit Audio] room.startAudio() also requires user gesture. Flagging audio as blocked for UI prompt:",
            audioError?.message || audioError
          );
          if (onAudioPlaybackStatusChanged) {
            onAudioPlaybackStatusChanged(false);
          }
        }
      }
    }
  );

  // ==========================================================
  // AUDIO PLAYBACK STATUS CHANGED (AUTOPLAY POLICY WATCHDOG)
  // ==========================================================

  room.on(RoomEvent.AudioPlaybackStatusChanged, () => {
    console.log(
      `[LiveKit Audio] Room audio playback status changed: canPlaybackAudio=${room.canPlaybackAudio}`
    );
    if (onAudioPlaybackStatusChanged) {
      onAudioPlaybackStatusChanged(room.canPlaybackAudio);
    }
  });

  // ==========================================================
  // TRACK UNSUBSCRIBED (CLEANUP AUDIO ELEMENTS)
  // ==========================================================

  room.on(RoomEvent.TrackUnsubscribed, (track) => {
    if (track.kind === Track.Kind.Audio) {
      console.log("[LiveKit Audio] Detaching AI audio track:", track.sid);
      try {
        const detached = track.detach();
        detached.forEach((element) => {
          if (element && element.parentNode) {
            element.parentNode.removeChild(element);
          }
        });
      } catch (detachError) {
        console.warn("[LiveKit Audio] Notice while detaching track:", detachError);
      }
      const remaining = document.querySelectorAll(
        `[data-livekit-track-sid="${track.sid}"]`
      );
      remaining.forEach((el) => {
        if (el.parentNode) {
          el.parentNode.removeChild(el);
        }
      });
    }
  });

  // ==========================================================
  // DATA FROM PYTHON AGENT
  // ==========================================================

  room.on(
    RoomEvent.DataReceived,
    (
      payload,
      participant,
      kind,
      topic
    ) => {
      try {
        const text =
          new TextDecoder().decode(payload);

        const data = JSON.parse(text);

        console.log(
          "LiveKit assessment event:",
          data
        );

        if (onData) {
          onData(data);
        }
      } catch (error) {
        console.error(
          "Could not read LiveKit data:",
          error
        );
      }
    }
  );

  // ==========================================================
  // CONNECTED
  // ==========================================================

  room.on(
    RoomEvent.Connected,
    async () => {
      console.log(
        "[LiveKit] Connected to room:",
        room.name
      );

      console.log("[LiveKit] Microphone publication started.");

      try {
        await room.localParticipant.setMicrophoneEnabled(
          true
        );

        console.log(
          "[LiveKit] Microphone publication completed successfully."
        );
      } catch (error) {
        const isCancelled =
          error?.message?.includes("Cancelled publication") ||
          room.state === "disconnected";

        if (isCancelled) {
          console.log(
            "[LiveKit] Microphone publication was cancelled (room disconnected or track unpublished)."
          );
        } else {
          console.error(
            "[LiveKit] Microphone setup error:",
            error?.message || error
          );

          if (onMicrophoneError) {
            onMicrophoneError(error);
          }
        }
      }

      try {
        await room.startAudio();

        console.log(
          "[LiveKit] LiveKit audio playback started."
        );
      } catch (error) {
        console.warn(
          "[LiveKit] startAudio warning:",
          error
        );
      }

      if (onConnected) {
        onConnected();
      }
    }
  );

  // ==========================================================
  // DISCONNECTED
  // ==========================================================

  room.on(
    RoomEvent.Disconnected,
    (reason) => {
      console.log(
        "[LiveKit] Room disconnected event received. Reason:",
        reason
      );

      // Clean up any remaining attached audio elements
      const elements = document.querySelectorAll(
        "[data-livekit-track-sid]"
      );
      elements.forEach((el) => {
        if (el.parentNode) {
          el.parentNode.removeChild(el);
        }
      });

      if (onDisconnected) {
        onDisconnected(reason);
      }
    }
  );

  // ==========================================================
  // CONNECT
  // ==========================================================

  console.log(
    "[LiveKit] Room connection started..."
  );

  try {
    await room.connect(
      serverUrl,
      token,
      {
        autoSubscribe: true,
      }
    );

    console.log(
      "[LiveKit] Room connection completed. Room name:",
      room.name
    );
  } catch (error) {
    console.error(
      "[LiveKit] Failed to connect to room:",
      error?.message || error
    );
    throw error;
  }

  return room;
}
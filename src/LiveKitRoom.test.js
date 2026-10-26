import { joinRoom } from './LiveKitRoom';
import { Room, RoomEvent, Track } from 'livekit-client';

jest.mock('livekit-client', () => {
  const listeners = {};
  class MockRoom {
    constructor() {
      this.state = 'connected';
      this.name = 'test-room-stats';
      this.localParticipant = {
        setMicrophoneEnabled: jest.fn().mockResolvedValue(undefined),
      };
      this.connect = jest.fn().mockResolvedValue(undefined);
      this.startAudio = jest.fn().mockResolvedValue(undefined);
      this.disconnect = jest.fn();
      this.on = jest.fn((event, handler) => {
        listeners[event] = handler;
      });
    }
  }

  MockRoom.listeners = listeners;

  return {
    Room: MockRoom,
    RoomEvent: {
      TrackSubscribed: 'trackSubscribed',
      TrackUnsubscribed: 'trackUnsubscribed',
      AudioPlaybackStatusChanged: 'audioPlaybackStatusChanged',
      DataReceived: 'dataReceived',
      Connected: 'connected',
      Disconnected: 'disconnected',
    },
    Track: {
      Kind: {
        Audio: 'audio',
        Video: 'video',
      },
    },
  };
});

describe('LiveKitRoom audio and WebRTC statistics', () => {
  afterEach(() => {
    document.querySelectorAll('[data-livekit-track-sid]').forEach((el) => el.remove());
  });

  test('attaches audio element with preload=auto, non-throttled styling, and queries receiver stats', async () => {
    const mockAudioTrack = {
      kind: 'audio',
      name: 'roomio_audio',
      sid: 'TR_test_123',
      attach: jest.fn(() => {
        const el = document.createElement('audio');
        el.setAttribute('data-livekit-track-sid', 'TR_test_123');
        el.play = jest.fn().mockResolvedValue(undefined);
        return el;
      }),
      detach: jest.fn(() => []),
      getReceiverStats: jest.fn().mockResolvedValue({
        jitter: 0.005,
        packetsLost: 0,
        packetsReceived: 250,
        concealedSamples: 0,
      }),
    };

    const onAudioStatus = jest.fn();
    const room = await joinRoom(
      'mock-token',
      'wss://test.livekit.cloud',
      jest.fn(),
      jest.fn(),
      jest.fn(),
      jest.fn(),
      onAudioStatus
    );

    expect(room).toBeDefined();
    expect(room.state).toBe('connected');

    const listeners = Room.listeners;

    // Simulate TrackSubscribed for AI audio
    await listeners['trackSubscribed'](
      mockAudioTrack,
      {},
      { identity: 'agent-123' }
    );

    const attached = document.querySelector('[data-livekit-track-sid="TR_test_123"]');
    expect(attached).not.toBeNull();
    expect(attached.preload).toBe('auto');
    expect(attached.style.position).toBe('fixed');
    expect(attached.style.opacity).toBe('0.001');

    // Simulate TrackUnsubscribed: cleans up elements
    listeners['trackUnsubscribed'](mockAudioTrack);
    const afterDetach = document.querySelector('[data-livekit-track-sid="TR_test_123"]');
    expect(afterDetach).toBeNull();
  });
});

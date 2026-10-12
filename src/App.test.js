import { render, screen, fireEvent, act } from '@testing-library/react';
import App from './App';
import * as api from './api';
import * as livekitRoom from './LiveKitRoom';

jest.mock('./api', () => ({
  getToken: jest.fn(),
  getAssessmentResult: jest.fn(),
}));

jest.mock('./LiveKitRoom', () => ({
  joinRoom: jest.fn(),
}));

describe('Behavioral AI Agent App', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('renders Behavioral AI assessment landing page', () => {
    render(<App />);

    const headings = screen.getAllByText(/Behavioral AI/i);
    expect(headings.length).toBeGreaterThan(0);

    const startButton = screen.getByRole('button', { name: /Start Assessment/i });
    expect(startButton).toBeInTheDocument();
    expect(startButton).not.toBeDisabled();

    expect(screen.getByText(/Ready/i)).toBeInTheDocument();
  });

  test('normal startup: clicking start connects room and transitions state', async () => {
    const mockRoom = {
      name: 'test-room',
      disconnect: jest.fn(),
      localParticipant: {
        publishData: jest.fn(),
      },
    };

    api.getToken.mockResolvedValueOnce({
      token: 'fake-jwt',
      serverUrl: 'wss://test.livekit.cloud',
      room: 'test-room',
    });

    livekitRoom.joinRoom.mockImplementation((token, url, onData, onConnected) => {
      if (onConnected) onConnected();
      return Promise.resolve(mockRoom);
    });

    render(<App />);
    const startButton = screen.getByRole('button', { name: /Start Assessment/i });

    await act(async () => {
      fireEvent.click(startButton);
    });

    expect(api.getToken).toHaveBeenCalledTimes(1);
    expect(livekitRoom.joinRoom).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/Assessment Active/i)).toBeInTheDocument();
  });

  test('repeated start clicks: guard prevents duplicate connection attempts', async () => {
    let resolveToken;
    const tokenPromise = new Promise((resolve) => {
      resolveToken = resolve;
    });
    api.getToken.mockReturnValue(tokenPromise);

    render(<App />);
    const startButton = screen.getByRole('button', { name: /Start Assessment/i });

    // Click multiple times rapidly
    act(() => {
      fireEvent.click(startButton);
      fireEvent.click(startButton);
      fireEvent.click(startButton);
    });

    // Guard should ensure getToken is called exactly once
    expect(api.getToken).toHaveBeenCalledTimes(1);
    expect(startButton).toBeDisabled();
    expect(screen.getByText(/Connecting to AI Interviewer.../i)).toBeInTheDocument();

    // Resolve token to finish cleanly
    await act(async () => {
      resolveToken({ token: 'fake-jwt', serverUrl: 'wss://test.livekit.cloud', room: 'test-room' });
    });
  });

  test('connection failure: resets guard and displays error message', async () => {
    api.getToken.mockRejectedValueOnce(new Error('Network error reaching backend'));

    render(<App />);
    const startButton = screen.getByRole('button', { name: /Start Assessment/i });

    await act(async () => {
      fireEvent.click(startButton);
    });

    expect(
      screen.getByText(/Could not start the assessment: Network error reaching backend/i)
    ).toBeInTheDocument();

    // Guard should be reset, button re-enabled
    expect(startButton).not.toBeDisabled();
    expect(screen.getByText(/Start Assessment/i)).toBeInTheDocument();
  });

  test('component unmount: disconnects active room cleanly', async () => {
    const mockRoom = {
      name: 'test-room-unmount',
      disconnect: jest.fn(),
      localParticipant: { publishData: jest.fn() },
    };

    api.getToken.mockResolvedValueOnce({
      token: 'fake-jwt',
      serverUrl: 'wss://test.livekit.cloud',
      room: 'test-room-unmount',
    });
    livekitRoom.joinRoom.mockResolvedValueOnce(mockRoom);

    const { unmount } = render(<App />);
    const startButton = screen.getByRole('button', { name: /Start Assessment/i });

    await act(async () => {
      fireEvent.click(startButton);
    });

    expect(mockRoom.disconnect).not.toHaveBeenCalled();

    // Unmount component
    unmount();

    expect(mockRoom.disconnect).toHaveBeenCalledTimes(1);
  });

  test('ending assessment: publishes end_assessment data without calling disconnect', async () => {
    const mockRoom = {
      name: 'test-room-end',
      disconnect: jest.fn(),
      localParticipant: {
        publishData: jest.fn().mockResolvedValue(true),
      },
    };

    api.getToken.mockResolvedValueOnce({
      token: 'fake-jwt',
      serverUrl: 'wss://test.livekit.cloud',
      room: 'test-room-end',
    });
    livekitRoom.joinRoom.mockResolvedValueOnce(mockRoom);

    render(<App />);
    const startButton = screen.getByRole('button', { name: /Start Assessment/i });

    await act(async () => {
      fireEvent.click(startButton);
    });

    const endButton = screen.getByRole('button', { name: /End Assessment/i });
    await act(async () => {
      fireEvent.click(endButton);
    });

    expect(mockRoom.localParticipant.publishData).toHaveBeenCalledTimes(1);
    const [payload, opts] = mockRoom.localParticipant.publishData.mock.calls[0];
    const decoded = JSON.parse(new TextDecoder().decode(payload));
    expect(decoded.type).toBe('end_assessment');
    expect(opts.topic).toBe('behavioral-ui');

    // Must NOT disconnect immediately because python agent needs connection to speak closing message
    expect(mockRoom.disconnect).not.toHaveBeenCalled();
  });
});

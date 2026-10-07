import { render, screen } from '@testing-library/react';
import App from './App';

test('renders Behavioral AI assessment landing page', () => {
  render(<App />);

  // Verify header and title
  const headings = screen.getAllByText(/Behavioral AI/i);
  expect(headings.length).toBeGreaterThan(0);

  // Verify Start Assessment CTA button
  const startButton = screen.getByRole('button', { name: /Start Assessment/i });
  expect(startButton).toBeInTheDocument();

  // Verify status badge
  expect(screen.getByText(/Ready/i)).toBeInTheDocument();
});

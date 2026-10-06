// The live region exists from the first render, so later messages in it are announced.
export function StatusBar() {
  return <div className="status-bar" role="status" aria-live="polite" />;
}

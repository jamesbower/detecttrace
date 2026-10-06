import "./ErrorState.css";

export function ErrorState({ message }: { message: string }) {
  return (
    <main className="error-state">
      <div className="error-state-box" role="alert">
        <h1 className="error-state-title">The dashboard could not load</h1>
        <p>{message}</p>
      </div>
    </main>
  );
}

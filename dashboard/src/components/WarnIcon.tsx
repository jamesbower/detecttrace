/** The warning triangle. Decorative: the words beside it always say what it warns of. */
export function WarnIcon({ className }: { className: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d="M8 1.8 15 14H1zM8 6.2v3.6M8 11.8v.2" />
    </svg>
  );
}

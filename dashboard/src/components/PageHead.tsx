import "./PageHead.css";

type PageHeadProps = { eyebrow: string; title: string; description?: string };

export function PageHead({ eyebrow, title, description }: PageHeadProps) {
  return (
    <header className="page-head">
      <p className="page-head-eyebrow">{eyebrow}</p>
      <h1 className="page-head-title">{title}</h1>
      {description !== undefined && <p className="page-head-lead">{description}</p>}
      <div className="page-head-marks" aria-hidden="true">
        <span />
        <span />
        <span />
        <span />
      </div>
    </header>
  );
}

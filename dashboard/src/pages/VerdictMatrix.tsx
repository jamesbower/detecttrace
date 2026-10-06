import { PageHead } from "../components/PageHead";

export function VerdictMatrix() {
  return (
    <PageHead
      eyebrow="Verdict matrix"
      title="Verdict matrix"
      description="Agent verdict against analyst verdict, all versions together. Outlined cells are where a dangerous false close would fall: the analyst found a true positive, and the agent closed it as a false positive or benign."
    />
  );
}

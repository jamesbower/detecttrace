import { PageHead } from "../components/PageHead";

export function WeeklyTrend() {
  return (
    <PageHead
      eyebrow="Weekly trend"
      title="Weekly trend"
      description="One line per version, with a point only in weeks where that version has cases, plus all versions together. ISO weeks in UTC."
    />
  );
}

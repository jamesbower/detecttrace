# Metrics

This page defines every number on the dashboard, how its interval is built, and how it is rounded. For where the inputs come from, see [Trace attributes](attributes.md) and [Checklists](checklists.md).

## Which cases count

A case is scored when its case ID is in both the traces and the verdict file. Its alert class comes from the verdict file. Each metric then uses the cases it can:

| Metric | Cases used |
|---|---|
| Verdict agreement, chance-corrected agreement, confusion matrix, dangerous false closes | Cases with both an analyst verdict and an agent verdict |
| Evidence completeness, skipped steps | Every case of a class with a checklist |

A verdict is missing when the label has no mapping, when the agent span has no verdict, or when a case's verdict rows disagree. Each of these is reported in the data notes.

Two coverage lines, on the Overview and Data notes pages, show how many verdicts found a trace and how many traces found a verdict. A side below half is marked as low coverage, which usually means a wrong case ID mapping.

## Verdict agreement

The share of cases where the agent's verdict equals the analyst's, over the three verdicts `true_positive`, `false_positive` and `benign`.

    agreement = agreed / n

The interval is the 95% Wilson score interval.

## Chance-corrected agreement (κ)

Cohen's kappa. It corrects agreement for the agreement expected by chance from each side's verdict mix:

    κ = (p_o − p_e) / (1 − p_e)

- `p_o` is the observed agreement: the share of cases on the diagonal of the confusion matrix.
- `p_e` is the expected agreement: the sum, over the three verdicts, of the analyst's share times the agent's share.

κ is 1 for perfect agreement and 0 for agreement no better than chance. It can be negative. An agent that always says "benign" on a class that is 85% benign agrees 85% of the time, and has κ = 0.

It is computed in whole numbers, so the edge cases are exact:

| Case | Shown |
|---|---|
| No case has both verdicts | "n/a (no cases with both verdicts)" |
| `p_e` = 1: both sides give one and the same verdict for every case | "n/a (all cases have the same verdict)" |
| Otherwise, one side gives the same verdict for every case | "0.00", with no interval: "(one side always gives the same verdict)" |

In the last case κ is exactly 0 and its standard error is 0. An interval of zero width would look precise, so none is shown.

## Confusion matrix

Per alert class, all versions together. Rows are the analyst's verdict and columns the agent's, both in the order true positive, false positive, benign. Cell shading is the cell's share of its row. The two cells where the analyst said true positive and the agent said false positive or benign are outlined, and flagged when they hold any case.

## Dangerous false closes

Cases where the analyst said `true_positive` and the agent said `false_positive` or `benign`: the agent closed a real threat. Shown as a count per version, with the case table's "Dangerous false closes" filter to list them. `check --json` writes their case IDs.

**True positives with no agent verdict** are counted apart. They are cases where the analyst said `true_positive` and the agent's verdict is missing or unmapped. They are not dangerous false closes and not in agreement, since the agent's answer is unknown. Each version row lists up to three of their case IDs ("2 true positives with no agent verdict: …"), and a data note gives the count per class.

## Evidence completeness

Per case: the share of checklist items satisfied (see [outcomes](checklists.md#outcomes)).

    completeness = satisfied items / checklist items

Per class or per version: the mean over its cases. A class without a checklist shows "No checklist for this class."

The interval:

| Cases (n) | Interval |
|---|---|
| 1 | None: "No interval (one case)" |
| 2 or more, all with the same value | None: "No interval (all cases equal)" |
| 2 to 29 | Percentile bootstrap of the mean |
| 30 or more | Student's t-interval for the mean |

Both are cut to 0%–100%.

## Skipped steps by version

For each checklist item and each version: the share of cases where the item was not satisfied (failed or missed).

    skip rate = (failed + missed) / cases

Each cell shows the rate and the counts ("284 of 910"). It has no interval.

## Weekly trend

Per alert class, evidence completeness and verdict agreement for each ISO 8601 week, such as `2026-W38`. A case's week comes from its agent span's start time, in UTC. There is one line for all versions, one for each shown version, and one for the pooled versions. A week where a line has no cases is a gap. A point with fewer than 10 cases has a hollow marker. Points have no interval.

## Versions

A case's version is its prompt version attribute (see [Trace attributes](attributes.md#case-attributes)). Cases without one are grouped as "(no version)".

Within a class, rows come in this order: "All versions", then each shown version by first appearance (its earliest case start, then its name), then the pooled versions, then "(no version)".

At most six versions are shown per class. When a class has more, the six with the most cases are shown. Ties go to the version that appeared first, then to the name. The rest are pooled into one row, "(other versions: v3, v5)", with a note. "(no version)" is never pooled and doesn't take one of the six places. Each version keeps one color on every page. Colors follow the order in which versions first appear.

## Intervals

Every interval is a 95% interval.

| Metric | Method |
|---|---|
| Verdict agreement | Wilson score interval |
| κ, 100 or more cases | Analytic: κ ± 1.96 × SE, with the large-sample standard error of Fleiss, Cohen and Everitt (1969), cut to −1 to 1 |
| κ, fewer than 100 cases | Percentile bootstrap |
| Evidence completeness | See [Evidence completeness](#evidence-completeness) |

The percentile bootstrap draws 1,000 resamples of the cases, with replacement. The interval runs from the 2.5th to the 97.5th percentile of the statistic over the resamples. Each interval uses its own generator with the same fixed seed, so it doesn't depend on what else was computed.

For κ, a resample in which κ is undefined (`p_e` = 1) is dropped. The κ cell on the Overview and Versions pages then says "N of 1,000 resamples dropped", and a data note names the class and version. With fewer than 900 usable resamples, no interval is shown: "No interval (too few usable resamples)".

## Small samples

A value based on fewer than 10 cases is marked "Few cases." The count is the value's own n: cases with both verdicts for agreement and κ, and cases for completeness and skip rates. Every value shows its n.

## Rounding

Percentages are whole numbers, rounded half up, so 12.5% shows as 13%. At the ends:

| Value | Shown |
|---|---|
| Exactly 1 | 100% |
| At least 99.5% but below 1 | >99% |
| Above 0 but below 0.5% | <1% |
| Exactly 0 | 0% |

So "100%" and "0%" are never shown next to a case that says otherwise. A range with an end at ">99" or "<1" is written with "to", as in "<1% to 4%".

κ has two decimals. A κ that is not exactly 1 never shows as 1.00: 0.996 shows as 0.99. A value that rounds to −0.00 shows as 0.00. Negative values use a minus sign (−), and a range with a negative low end is written with "to", as in "−0.12 to 0.40".

## Determinism

With the same DetectTrace version, the same inputs give the same dashboard on any machine:

- trace files and checklist files are read in POSIX path order, and the first copy of a duplicate span wins;
- cases and classes are sorted by stable keys;
- bootstrap intervals use a fixed seed;
- numbers are formatted without the machine's locale.

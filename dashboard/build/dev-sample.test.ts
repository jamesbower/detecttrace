// @vitest-environment node
import { describe, expect, it } from "vitest";

import { fillDataBlocks } from "./dev-sample";

const PAGE = '<body><script type="application/json" id="dt-view"></script></body>';

describe("fillDataBlocks", () => {
  it("puts the JSON into its block", () => {
    expect(fillDataBlocks(PAGE, { "dt-view": '{"a":1}\n' })).toBe(
      '<body><script type="application/json" id="dt-view">{"a":1}</script></body>',
    );
  });

  it("escapes a closing script tag inside the JSON", () => {
    expect(fillDataBlocks(PAGE, { "dt-view": '{"a":"</script>"}' })).toContain('{"a":"\\u003c/script>"}');
  });

  it("keeps a dollar pattern in the JSON as text", () => {
    expect(fillDataBlocks(PAGE, { "dt-view": '{"a":"$&"}' })).toContain('{"a":"$&"}');
  });

  it("refuses a page without the block", () => {
    expect(() => fillDataBlocks(PAGE, { "dt-results": "{}" })).toThrow('"dt-results"');
  });
});

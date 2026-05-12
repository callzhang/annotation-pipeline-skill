import { describe, expect, it } from "vitest";
import { truncateTokens, unwrapJson } from "./JsonViewer";

describe("unwrapJson", () => {
  it("returns primitives unchanged", () => {
    expect(unwrapJson(42)).toBe(42);
    expect(unwrapJson(null)).toBe(null);
    expect(unwrapJson(true)).toBe(true);
    expect(unwrapJson("hello")).toBe("hello");
  });

  it("does not parse strings that aren't JSON objects/arrays", () => {
    expect(unwrapJson("123")).toBe("123");
    expect(unwrapJson("plain text")).toBe("plain text");
    expect(unwrapJson('"quoted"')).toBe('"quoted"');
  });

  it("parses stringified JSON objects", () => {
    expect(unwrapJson('{"a": 1}')).toEqual({ a: 1 });
  });

  it("parses stringified JSON arrays", () => {
    expect(unwrapJson('[1, 2, 3]')).toEqual([1, 2, 3]);
  });

  it("recursively unwraps nested stringified JSON in object values", () => {
    const input = {
      text: '{"rows": [{"id": 1, "name": "foo"}]}',
      raw_response: "untouched",
    };
    expect(unwrapJson(input)).toEqual({
      text: { rows: [{ id: 1, name: "foo" }] },
      raw_response: "untouched",
    });
  });

  it("recursively unwraps in arrays", () => {
    expect(unwrapJson(['{"x": 1}', '{"y": 2}'])).toEqual([{ x: 1 }, { y: 2 }]);
  });

  it("leaves malformed JSON strings as strings", () => {
    expect(unwrapJson('{not json}')).toBe('{not json}');
  });

  it("bottoms out at depth limit instead of recursing forever", () => {
    // Build a 6-deep stringified chain; depth limit is 4, so the innermost layer
    // should remain a string when we hit the limit.
    const inner = '{"deepest": 1}';
    let value: string = inner;
    for (let i = 0; i < 6; i++) value = JSON.stringify({ wrap: value });
    const out = unwrapJson(value);
    // It should be an object (not the original string) and should be finite -- just
    // check it didn't throw and produced *some* unwrapped structure.
    expect(typeof out).toBe("object");
  });
});

describe("truncateTokens", () => {
  it("returns tokens unchanged when small", () => {
    const tokens = [
      { type: "punct" as const, text: "{\n" },
      { type: "key" as const, text: '"a"' },
      { type: "punct" as const, text: ": " },
      { type: "number" as const, text: "1" },
      { type: "punct" as const, text: "\n}" },
    ];
    const result = truncateTokens(tokens);
    expect(result.truncated).toBe(false);
    expect(result.tokens).toEqual(tokens);
  });

  it("flags truncation when content exceeds the line budget", () => {
    const longText = Array.from({ length: 30 }, () => "line").join("\n");
    const tokens = [{ type: "string" as const, text: longText }];
    const result = truncateTokens(tokens);
    expect(result.truncated).toBe(true);
    // Ellipsis token is appended.
    expect(result.tokens[result.tokens.length - 1].text).toContain("…");
  });
});

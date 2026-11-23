// Unit tests for chrome-extension/lib.js (pure text-matching logic).
// Run: node --test tests/js/
import { test } from "node:test";
import assert from "node:assert/strict";
import lib from "../../chrome-extension/lib.js";

const { foldText, locateQuote, splitSpanAcrossItems } = lib;

test("locateQuote finds exact matches", () => {
  assert.deepEqual(locateQuote("say the thing plainly", "the thing"), [4, 13]);
});

test("locateQuote is case-insensitive via folding", () => {
  const hay = "The Senator is a KNOWN liar.";
  const span = locateQuote(hay, "the senator is a known liar");
  assert.deepEqual(hay.slice(span[0], span[1]), "The Senator is a KNOWN liar");
});

test("locateQuote folds curly quotes both directions", () => {
  const hay = "He said “you’re wrong” and left.";
  const span = locateQuote(hay, `"you're wrong"`);
  assert.equal(hay.slice(span[0], span[1]), "“you’re wrong”");
});

test("locateQuote collapses whitespace runs and newlines", () => {
  const hay = "first  line\n\n\tsecond   line here";
  const span = locateQuote(hay, "first line second line");
  assert.equal(span[0], 0);
  assert.ok(hay.slice(span[0], span[1]).endsWith("second   line"));
});

test("locateQuote treats NBSP as whitespace", () => {
  const hay = "price is right";
  assert.ok(locateQuote(hay, "price is right"));
});

test("locateQuote rejects absent and too-short quotes", () => {
  assert.equal(locateQuote("some text here", "absent phrase"), null);
  assert.equal(locateQuote("some text here", "so"), null);
  assert.equal(locateQuote("some text here", ""), null);
  assert.equal(locateQuote("some text here", null), null);
});

test("foldText maps folded indices back to original offsets", () => {
  const { out, map } = foldText("  A  b C ");
  assert.equal(out, "a b c ");
  assert.equal(map[0], 2); // 'a' came from index 2
});

test("splitSpanAcrossItems keeps a span inside one item local", () => {
  const items = [
    { start: 0, text: "0123456789" },
    { start: 12, text: "abcdefghij" }, // joined with 2-char separator
  ];
  assert.deepEqual(splitSpanAcrossItems([13, 17], items), [{ index: 1, s: 1, e: 5 }]);
});

test("splitSpanAcrossItems splits boundary-straddling spans", () => {
  const items = [
    { start: 0, text: "0123456789" },
    { start: 12, text: "abcdefghij" },
  ];
  assert.deepEqual(splitSpanAcrossItems([8, 15], items), [
    { index: 0, s: 8, e: 10 },
    { index: 1, s: 0, e: 3 },
  ]);
});

test("end-to-end: quote spanning a batch boundary maps to both items", () => {
  const a = "The header title";
  const b = "the body continues here";
  const joined = a + "\n\n" + b;
  const items = [
    { start: 0, text: a },
    { start: a.length + 2, text: b },
  ];
  const span = locateQuote(joined, "title the body");
  const segs = splitSpanAcrossItems(span, items);
  assert.equal(segs.length, 2);
  assert.equal(a.slice(segs[0].s, segs[0].e), "title");
  assert.equal(b.slice(segs[1].s, segs[1].e), "the body");
});

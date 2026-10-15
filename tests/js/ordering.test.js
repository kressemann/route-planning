"use strict";

// Regressionstests fuer die Sortierlogik der Stoppliste.
// Ausfuehren: docker compose run --rm tests-js

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  parseTargetNumber,
  moveToNumber,
  findInsertBefore,
  isPermutation,
  autoScrollDelta,
} = require("../../routeplanning/static/ordering.js");

// Stopps 1..n als Objekte mit Mengen, damit auch "Mengen unveraendert" pruefbar ist.
function tour(n) {
  return Array.from({ length: n }, (_, i) => ({
    customer_id: `c${i + 1}`,
    kisten: i + 1,
    schnittware: (i % 3) * 0.5,
    kresse: i % 2,
  }));
}
const ids = (list) => list.map((s) => s.customer_id);
const range = (a, b) => Array.from({ length: b - a + 1 }, (_, i) => `c${a + i}`);

test("2 -> 16: Stopp traegt danach Nummer 16, 3..16 ruecken auf 2..15, Rest bleibt", () => {
  const before = tour(27);
  const after = moveToNumber(before, 1, 16);
  assert.deepEqual(ids(after), ["c1", ...range(3, 16), "c2", ...range(17, 27)]);
  assert.equal(after.indexOf(before[1]) + 1, 16);
});

test("16 -> 2: Stopp traegt danach Nummer 2, 2..15 ruecken auf 3..16", () => {
  const after = moveToNumber(tour(27), 15, 2);
  assert.deepEqual(ids(after), ["c1", "c16", ...range(2, 15), ...range(17, 27)]);
});

test("um eine Position nach oben und nach unten", () => {
  assert.deepEqual(ids(moveToNumber(tour(5), 2, 2)), ["c1", "c3", "c2", "c4", "c5"]);
  assert.deepEqual(ids(moveToNumber(tour(5), 2, 4)), ["c1", "c2", "c4", "c3", "c5"]);
});

test("erste und letzte Position", () => {
  assert.deepEqual(ids(moveToNumber(tour(5), 4, 1)), ["c5", "c1", "c2", "c3", "c4"]);
  assert.deepEqual(ids(moveToNumber(tour(5), 0, 5)), ["c2", "c3", "c4", "c5", "c1"]);
  assert.deepEqual(ids(moveToNumber(tour(5), 0, 1)), ids(tour(5)));
});

test("ueber viele Stopps: jede Verschiebung bleibt eine Permutation, Mengen unveraendert", () => {
  const before = tour(60);
  for (let from = 0; from < 60; from += 7) {
    for (let target = 1; target <= 60; target += 11) {
      const after = moveToNumber(before, from, target);
      assert.ok(isPermutation(ids(after), ids(before)));
      assert.equal(ids(after)[target - 1], `c${from + 1}`);
      const sum = (list, f) => list.reduce((acc, s) => acc + s[f], 0);
      for (const f of ["kisten", "schnittware", "kresse"]) assert.equal(sum(after, f), sum(before, f));
      // Jedes Objekt (inkl. Mengen) ist unveraendert dasselbe.
      assert.ok(after.every((s) => before.includes(s)));
    }
  }
});

test("moveToNumber veraendert die Eingabe nicht", () => {
  const before = tour(5);
  const snapshot = ids(before);
  moveToNumber(before, 0, 5);
  assert.deepEqual(ids(before), snapshot);
});

test("moveToNumber lehnt ungueltige Positionen ab", () => {
  assert.throws(() => moveToNumber(tour(5), 5, 1), RangeError);
  assert.throws(() => moveToNumber(tour(5), -1, 1), RangeError);
  assert.throws(() => moveToNumber(tour(5), 0, 0), RangeError);
  assert.throws(() => moveToNumber(tour(5), 0, 6), RangeError);
  assert.throws(() => moveToNumber(tour(5), 0, 2.5), RangeError);
});

test("parseTargetNumber: gueltige Eingaben", () => {
  assert.equal(parseTargetNumber("16", 27), 16);
  assert.equal(parseTargetNumber(" 3 ", 27), 3);
  assert.equal(parseTargetNumber("1", 27), 1);
  assert.equal(parseTargetNumber("27", 27), 27);
  assert.equal(parseTargetNumber("007", 27), 7);
});

test("parseTargetNumber: ausserhalb 1..N wird begrenzt", () => {
  assert.equal(parseTargetNumber("0", 27), 1);
  assert.equal(parseTargetNumber("99", 27), 27);
});

test("parseTargetNumber: ungueltige Eingaben ergeben null", () => {
  for (const raw of ["", "  ", "abc", "-3", "2.5", "1e2", "+4", null, undefined, "4a"]) {
    assert.equal(parseTargetNumber(raw, 27), null, `Eingabe ${JSON.stringify(raw)}`);
  }
  assert.equal(parseTargetNumber("1", 0), null);
});

test("findInsertBefore ist zustandslos und monoton", () => {
  const mids = [50, 150, 250, 350];
  assert.equal(findInsertBefore(mids, 0), 0);
  assert.equal(findInsertBefore(mids, 149), 1);
  assert.equal(findInsertBefore(mids, 151), 2);
  assert.equal(findInsertBefore(mids, 1000), -1);
});

test("isPermutation erkennt doppelte und fehlende Stopps", () => {
  assert.ok(isPermutation(["b", "a", "c"], ["a", "b", "c"]));
  assert.ok(!isPermutation(["a", "a", "c"], ["a", "b", "c"]));
  assert.ok(!isPermutation(["a", "b"], ["a", "b", "c"]));
  assert.ok(!isPermutation(["a", "b", "c", "d"], ["a", "b", "c"]));
});

test("autoScrollDelta: nur am Rand, staerker je naeher am Rand", () => {
  assert.equal(autoScrollDelta(400, 800, 80, 24), 0);
  assert.ok(autoScrollDelta(40, 800, 80, 24) < 0);
  assert.ok(autoScrollDelta(0, 800, 80, 24) < autoScrollDelta(40, 800, 80, 24));
  assert.ok(autoScrollDelta(760, 800, 80, 24) > 0);
  assert.equal(autoScrollDelta(800, 800, 80, 24), 24);
  assert.equal(autoScrollDelta(-50, 800, 80, 24), -24);
});

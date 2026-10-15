"use strict";

// Regressionstests fuer die Gesamtmengenuebersicht der Weboberflaeche.
// Ausfuehren: docker compose run --rm tests-js

const test = require("node:test");
const assert = require("node:assert/strict");
const { summarize, formatNumber, formatSummary } = require("../../routeplanning/static/totals.js");
const { moveToNumber } = require("../../routeplanning/static/ordering.js");

const stop = (id, kisten, schnittware, kresse) => ({ customer_id: id, kisten, schnittware, kresse });

test("Summen und Format wie am 15.10.2026", () => {
  // 27 Stopps, deren Mengen sich zu 66 / 19 / 2 summieren.
  const stops = Array.from({ length: 27 }, (_, i) => stop(`c${i}`, 0, 0, 0));
  stops[0].kisten = 40; stops[1].kisten = 26;
  stops[2].schnittware = 14; stops[3].schnittware = 5;
  stops[4].kresse = 2;
  const totals = summarize(stops);
  assert.deepEqual(totals, { stops: 27, kisten: 66, schnittware: 19, kresse: 2 });
  assert.equal(formatSummary(totals), "27 Stopps | 66 Kisten | 19 Schnittware | 2 Kresse");
});

test("leere Tour und Einzahl", () => {
  assert.equal(formatSummary(summarize([])), "0 Stopps | 0 Kisten | 0 Schnittware | 0 Kresse");
  assert.equal(formatSummary(summarize([stop("a", 1, 1, 1)])), "1 Stopp | 1 Kiste | 1 Schnittware | 1 Kresse");
});

test("fehlende/leere Mengen zaehlen als 0, Zahlen-Strings werden gezaehlt", () => {
  const totals = summarize([stop("a", undefined, null, ""), stop("b", "3", 2, 0)]);
  assert.deepEqual(totals, { stops: 2, kisten: 3, schnittware: 2, kresse: 0 });
});

test("Schnittware mit Nachkommastelle im deutschen Format", () => {
  assert.equal(formatNumber(2.5), "2,5");
  assert.equal(formatNumber(0.1 + 0.2), "0,3");
  assert.equal(formatNumber(19), "19");
});

test("manuelle Mengenaenderung wirkt sofort auf die Summe", () => {
  const stops = [stop("a", 3, 0, 0), stop("b", 2, 1, 0)];
  stops[1].kisten = 7;
  assert.equal(summarize(stops).kisten, 10);
});

test("Entfernen eines Stopps wirkt sofort auf die Summe", () => {
  const stops = [stop("a", 3, 1, 0), stop("b", 2, 1, 1)];
  const remaining = stops.filter((s) => s.customer_id !== "b");
  assert.deepEqual(summarize(remaining), { stops: 1, kisten: 3, schnittware: 1, kresse: 0 });
});

test("Verschieben veraendert die Summen nicht", () => {
  const stops = Array.from({ length: 20 }, (_, i) => stop(`c${i}`, i, (i % 4) * 0.5, i % 2));
  const before = summarize(stops);
  for (const [from, to] of [[1, 16], [15, 2], [0, 20], [19, 1]]) {
    assert.deepEqual(summarize(moveToNumber(stops, from, to)), before);
  }
});

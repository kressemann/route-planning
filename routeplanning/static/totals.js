"use strict";

// Gesamtmengen des aktuellen Tourentwurfs - ohne DOM, damit die Logik im
// Browser (window.RouteTotals) und in den Node-Tests (tests/js) identisch
// laeuft. Gegenstueck fuer das PDF: pdf_export.summarize_stops/format_summary.

(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.RouteTotals = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  /** Summen ueber die sichtbaren Stopps (inkl. manueller Mengen). */
  function summarize(stops) {
    return stops.reduce(
      (totals, stop) => ({
        stops: totals.stops + 1,
        kisten: totals.kisten + Number(stop.kisten || 0),
        schnittware: totals.schnittware + Number(stop.schnittware || 0),
        kresse: totals.kresse + Number(stop.kresse || 0),
      }),
      { stops: 0, kisten: 0, schnittware: 0, kresse: 0 }
    );
  }

  /** Ganze Zahlen ohne Nachkommastellen, sonst deutsches Komma (max. 2 Stellen). */
  function formatNumber(value) {
    const rounded = Math.round(value * 100) / 100;
    if (Number.isInteger(rounded)) return String(rounded);
    return String(rounded).replace(".", ",");
  }

  /** z.B. "<n> Stopps | <n> Kisten | <n> Schnittware | <n> Kresse" */
  function formatSummary(totals) {
    return [
      `${formatNumber(totals.stops)} ${totals.stops === 1 ? "Stopp" : "Stopps"}`,
      `${formatNumber(totals.kisten)} ${totals.kisten === 1 ? "Kiste" : "Kisten"}`,
      `${formatNumber(totals.schnittware)} Schnittware`,
      `${formatNumber(totals.kresse)} Kresse`,
    ].join(" | ");
  }

  return { summarize, formatNumber, formatSummary };
});

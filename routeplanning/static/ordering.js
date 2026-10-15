"use strict";

// Reine Sortierlogik der Stoppliste - ohne DOM, damit sie im Browser
// (window.RouteOrdering) und in den Node-Tests (tests/js) identisch laeuft.

(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.RouteOrdering = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  /**
   * Liest eine eingegebene Stoppnummer. Liefert eine ganze Zahl im Bereich
   * 1..count oder null bei ungueltiger Eingabe (leer, keine ganze Zahl,
   * negativ). Ganze Zahlen ausserhalb des Bereichs werden auf 1..count
   * begrenzt.
   */
  function parseTargetNumber(raw, count) {
    const text = String(raw === undefined || raw === null ? "" : raw).trim();
    if (!/^\d+$/.test(text) || !Number.isInteger(count) || count < 1) return null;
    const value = Number(text);
    return Math.min(Math.max(value, 1), count);
  }

  /**
   * Verschiebt den Eintrag an ``fromIndex`` (0-basiert) so, dass er danach
   * die Stoppnummer ``targetNumber`` (1-basiert) traegt. Alle uebrigen
   * Eintraege behalten ihre relative Reihenfolge; die Liste bleibt
   * gleich lang (nichts doppelt, nichts verloren). Gibt eine neue Liste
   * zurueck, die Eingabe bleibt unveraendert.
   */
  function moveToNumber(list, fromIndex, targetNumber) {
    const count = list.length;
    if (!Number.isInteger(fromIndex) || fromIndex < 0 || fromIndex >= count) {
      throw new RangeError(`Ungueltige Ausgangsposition: ${fromIndex}`);
    }
    if (!Number.isInteger(targetNumber) || targetNumber < 1 || targetNumber > count) {
      throw new RangeError(`Ungueltige Zielnummer: ${targetNumber}`);
    }
    const result = list.slice();
    const [moved] = result.splice(fromIndex, 1);
    result.splice(targetNumber - 1, 0, moved);
    return result;
  }

  /**
   * Einfuegeposition beim Ziehen: Index der ersten (nicht gezogenen) Zeile,
   * deren vertikale Mitte unterhalb des Zeigers liegt; -1 = ans Ende.
   * Zustandslos - wird bei jeder Zeiger- oder Scrollbewegung neu berechnet.
   */
  function findInsertBefore(midpoints, pointerY) {
    for (let i = 0; i < midpoints.length; i += 1) {
      if (pointerY < midpoints[i]) return i;
    }
    return -1;
  }

  /**
   * Prueft, ob ``ids`` genau dieselben Eintraege wie ``expectedIds`` enthaelt
   * (jede ID genau einmal) - Schutz gegen doppelte oder verlorene Stopps.
   */
  function isPermutation(ids, expectedIds) {
    if (ids.length !== expectedIds.length) return false;
    const seen = new Set(ids);
    if (seen.size !== ids.length) return false;
    return expectedIds.every((id) => seen.has(id));
  }

  /** Vertikale Scrollgeschwindigkeit (px je Frame) nahe am Fensterrand. */
  function autoScrollDelta(pointerY, viewportHeight, edge, maxSpeed) {
    if (pointerY < edge) {
      return -Math.ceil((maxSpeed * (edge - Math.max(pointerY, 0))) / edge);
    }
    if (pointerY > viewportHeight - edge) {
      const depth = Math.min(pointerY, viewportHeight) - (viewportHeight - edge);
      return Math.ceil((maxSpeed * depth) / edge);
    }
    return 0;
  }

  return { parseTargetNumber, moveToNumber, findInsertBefore, isPermutation, autoScrollDelta };
});

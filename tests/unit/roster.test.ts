import { describe, it, expect } from "vitest";
import { fillRosterSlots } from "@/domain/roster";

interface P {
  id: string;
  position: string | null;
}
const p = (id: string, position: string | null): P => ({ id, position });
const ids = (slots: (P | null)[]) => slots.map((s) => s?.id ?? null);

describe("fillRosterSlots", () => {
  it("places players into their concrete slots, index-aligned", () => {
    const template = ["QB", "RB", "WR", "TE"];
    const players = [p("qb", "QB"), p("rb", "RB"), p("wr", "WR"), p("te", "TE")];
    expect(ids(fillRosterSlots(players, template))).toEqual(["qb", "rb", "wr", "te"]);
  });

  it("sends a surplus RB to a FLEX slot", () => {
    const template = ["RB", "FLEX"];
    const players = [p("rb1", "RB"), p("rb2", "RB")];
    // Pass 1 fills the RB slot; pass 2 puts the extra RB in FLEX.
    expect(ids(fillRosterSlots(players, template))).toEqual(["rb1", "rb2"]);
  });

  it("lets SUPER_FLEX take a spare QB while FLEX would reject it", () => {
    const template = ["QB", "SUPER_FLEX"];
    const players = [p("qb1", "QB"), p("qb2", "QB")];
    expect(ids(fillRosterSlots(players, template))).toEqual(["qb1", "qb2"]);

    const flexTemplate = ["QB", "FLEX"];
    const result = fillRosterSlots([p("qb1", "QB"), p("qb2", "QB")], flexTemplate);
    // FLEX does not accept QB, so the second QB overflows to bench, FLEX stays null.
    expect(ids(result)).toEqual(["qb1", null, "qb2"]);
  });

  it("appends overflow players as extra bench rows", () => {
    const template = ["QB"];
    const players = [p("qb", "QB"), p("x", "RB"), p("y", "WR")];
    const result = fillRosterSlots(players, template);
    expect(result.length).toBe(3);
    expect(ids(result)).toEqual(["qb", "x", "y"]);
  });

  it("never fills a concrete or flex slot with a null-position player", () => {
    const template = ["QB", "FLEX", "BN"];
    const players = [p("mystery", null)];
    const result = fillRosterSlots(players, template);
    expect(ids(result)).toEqual([null, null, "mystery"]);
  });

  it("leaves trailing slots null when there are fewer players than slots", () => {
    const template = ["QB", "RB", "WR"];
    const players = [p("qb", "QB")];
    expect(ids(fillRosterSlots(players, template))).toEqual(["qb", null, null]);
  });
});

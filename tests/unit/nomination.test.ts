import { describe, it, expect } from "vitest";
import { canNominate, type NominationCheck } from "@/domain/nomination";

const clear: NominationCheck = {
  hasActiveRound: false,
  poolEntryExists: true,
  poolEntryInLeague: true,
  isNominated: false,
  isDrafted: false,
};

const codeOf = (v: ReturnType<typeof canNominate>) => (v.ok ? "ok" : v.code);

describe("canNominate", () => {
  it("allows nomination when everything is clear", () => {
    expect(canNominate(clear).ok).toBe(true);
  });

  it("rejects when a round is already active", () => {
    expect(codeOf(canNominate({ ...clear, hasActiveRound: true }))).toBe("round-active");
  });

  it("rejects when the pool entry does not exist", () => {
    expect(codeOf(canNominate({ ...clear, poolEntryExists: false }))).toBe("invalid-pool");
  });

  it("rejects when the pool entry belongs to another league", () => {
    expect(codeOf(canNominate({ ...clear, poolEntryInLeague: false }))).toBe("invalid-pool");
  });

  it("rejects an already-nominated player", () => {
    expect(codeOf(canNominate({ ...clear, isNominated: true }))).toBe("already-nominated");
  });

  it("rejects an already-drafted player", () => {
    expect(codeOf(canNominate({ ...clear, isDrafted: true }))).toBe("already-nominated");
  });

  it("reports the active-round problem before the pool problem", () => {
    expect(codeOf(canNominate({ ...clear, hasActiveRound: true, poolEntryExists: false }))).toBe(
      "round-active",
    );
  });
});

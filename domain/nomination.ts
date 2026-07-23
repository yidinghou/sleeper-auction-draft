import { fail, OK, type Verdict } from "./types";

export interface NominationCheck {
  /** True if any pending or open round already exists for the league. */
  hasActiveRound: boolean;
  poolEntryExists: boolean;
  poolEntryInLeague: boolean;
  isNominated: boolean;
  isDrafted: boolean;
}

/**
 * Decide whether a player may be nominated to open a new round.
 * Order mirrors the original handler: one active round at a time ->
 * pool entry must be valid -> player not already nominated/drafted.
 */
export function canNominate(input: NominationCheck): Verdict {
  if (input.hasActiveRound) {
    return fail(
      "round-active",
      "A round is already pending or open for this league",
    );
  }
  if (!input.poolEntryExists || !input.poolEntryInLeague) {
    return fail("invalid-pool", "Invalid draftPoolId");
  }
  if (input.isDrafted || input.isNominated) {
    return fail("already-nominated", "Player already nominated or drafted");
  }
  return OK;
}

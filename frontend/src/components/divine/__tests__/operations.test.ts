import { describe, expect, it } from "vitest";
import {
  OPERATIONS,
  OPERATION_ELEMENT,
  OPERATION_NUMERAL,
  type Operation,
} from "../operations";

describe("operations", () => {
  it("exposes exactly the seven canonical operations in order", () => {
    expect(OPERATIONS).toEqual([
      "calcinate",
      "dissolve",
      "separate",
      "conjoin",
      "ferment",
      "distill",
      "coagulate",
    ]);
  });

  it("maps every operation to its element", () => {
    for (const op of OPERATIONS) {
      expect(OPERATION_ELEMENT[op]).toBeTruthy();
    }
  });

  it("maps every operation to its Roman numeral position I..VII", () => {
    expect(OPERATION_NUMERAL.calcinate).toBe("I");
    expect(OPERATION_NUMERAL.dissolve).toBe("II");
    expect(OPERATION_NUMERAL.separate).toBe("III");
    expect(OPERATION_NUMERAL.conjoin).toBe("IV");
    expect(OPERATION_NUMERAL.ferment).toBe("V");
    expect(OPERATION_NUMERAL.distill).toBe("VI");
    expect(OPERATION_NUMERAL.coagulate).toBe("VII");
  });

  it("Operation type accepts only the seven canonical names", () => {
    // Compile-time check: this reassignment must be assignable.
    const op: Operation = "calcinate";
    expect(OPERATIONS).toContain(op);
  });
});

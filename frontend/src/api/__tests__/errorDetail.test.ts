import { describe, it, expect } from "vitest";
import { detailMessages, formatErrorDetail, apiErrorMessage } from "../errorDetail";

describe("formatErrorDetail", () => {
  it("returns a string detail unchanged, trimmed", () => {
    expect(formatErrorDetail("  repo not registered  ")).toBe("repo not registered");
  });

  it("prefixes each pydantic error with its field path, dropping the request part", () => {
    expect(
      formatErrorDetail([
        { loc: ["body", "env", 0, "name"], msg: "Field required" },
        { loc: ["query", "limit"], msg: "Input should be a valid integer" },
      ]),
      "loc must render as a dotted field path without the leading body/query",
    ).toBe("env.0.name: Field required; limit: Input should be a valid integer");
  });

  it("drops pydantic's 'Value error, ' prefix from a validator's message", () => {
    expect(
      formatErrorDetail([{ loc: ["body", "variables", 0, "key"], msg: "Value error, Key 'X' is reserved." }]),
    ).toBe("variables.0.key: Key 'X' is reserved.");
    expect(detailMessages([{ msg: "Value error, Key 'X' is reserved." }])).toEqual(["Key 'X' is reserved."]);
  });

  it("shows a whole-body error without a field prefix", () => {
    expect(formatErrorDetail([{ loc: ["body"], msg: "Input should be a valid dictionary" }])).toBe(
      "Input should be a valid dictionary",
    );
  });

  it("keeps plain strings inside a list", () => {
    expect(formatErrorDetail(["first", "  ", "second"])).toBe("first; second");
  });

  it("never reads a rejected input into the message", () => {
    const text = formatErrorDetail([{ loc: ["body", "token"], msg: "too short", input: "s3cret" }]);
    expect(text, "the rejected input value must not be copied into the message").not.toContain("s3cret");
  });

  it("reads a message from an object detail", () => {
    expect(formatErrorDetail({ message: "quota exceeded" })).toBe("quota exceeded");
    expect(formatErrorDetail({ msg: "bad state" })).toBe("bad state");
  });

  it("returns null when nothing readable is there", () => {
    expect(formatErrorDetail(undefined)).toBeNull();
    expect(formatErrorDetail("   ")).toBeNull();
    expect(formatErrorDetail([])).toBeNull();
    expect(formatErrorDetail([{ loc: ["body", "x"] }])).toBeNull();
    expect(formatErrorDetail({ unexpected: true })).toBeNull();
    expect(formatErrorDetail(42)).toBeNull();
  });
});

describe("detailMessages", () => {
  it("lists the trimmed messages of a detail without field paths", () => {
    expect(detailMessages([{ loc: ["body", "label"], msg: " taken " }, "plain", { msg: "" }, null])).toEqual([
      "taken",
      "plain",
    ]);
    expect(detailMessages(" one ")).toEqual(["one"]);
    expect(detailMessages(undefined)).toEqual([]);
  });
});

describe("apiErrorMessage", () => {
  it("is the API's detail, not axios's status sentence", () => {
    const err = { message: "Request failed with status code 409", response: { status: 409, data: { detail: "already running" } } };
    expect(apiErrorMessage(err)).toBe("already running");
  });

  it("names the status when the API sent no detail", () => {
    expect(apiErrorMessage({ message: "Request failed with status code 403", response: { status: 403 } }, "Could not save"))
      .toBe("Could not save (HTTP 403)");
  });

  it("says the server is unreachable on a network failure", () => {
    expect(apiErrorMessage({ code: "ERR_NETWORK", message: "Network Error" })).toBe("Network error — cannot reach the server");
  });

  it("is a thrown Error's own message", () => {
    expect(apiErrorMessage(new Error("answerInput requires issueId or jobId"))).toBe("answerInput requires issueId or jobId");
  });
});

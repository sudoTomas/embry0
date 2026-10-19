/** How careful a person should be before changing a setting. */
export type SettingRisk = "safe" | "check" | "admin";

/** The repo page's Advanced settings sections, in page order. */
export type SettingKey =
  | "testSetup"
  | "appSettings"
  | "workMachine"
  | "prReview"
  | "accounts"
  | "docker"
  | "designStandard";

export interface SettingGuide {
  title: string;
  /** What the setting does, in plain words. */
  what: string;
  /** When a change reaches embry0's work. */
  takesEffect: string;
  risk: SettingRisk;
  note?: string;
  /** How to take a change back, or why it cannot be. */
  undo: string;
}

export const RISK_LABELS: Record<SettingRisk, string> = {
  safe: "Safe to change",
  check: "Check first",
  admin: "Admin only",
};

/**
 * What each technical setting on the repo page does, when a change takes
 * effect and how to undo it. Every sentence says only what the code makes
 * true: a saved test setup reaches running jobs that have not reached their
 * test step, and a removed secret cannot be recovered.
 */
export const SETTING_GUIDE: Record<SettingKey, SettingGuide> = {
  testSetup: {
    title: "Test setup",
    what: "How embry0 starts your app on its own test computer and what it checks after each change.",
    takesEffect: "From the next test run, including jobs already running that have not reached their test step.",
    risk: "check",
    undo: "History keeps every version. Undo from History.",
  },
  appSettings: {
    title: "App settings",
    what:
      "Settings and passwords handed to embry0's test computers for this repository. They never reach your live app, " +
      "but a live password here gives embry0's agents access to that live system.",
    takesEffect: "From the next job.",
    risk: "check",
    undo: "Removing a secret cannot be undone: embry0 does not keep secret values.",
  },
  workMachine: {
    title: "Work machine and branches",
    what:
      "The prepared computer embry0 writes code on, the branch it starts from and asks to merge into, the commit author, " +
      "and the checks it runs before a pull request.",
    takesEffect: "From the next job.",
    risk: "check",
    undo: "History keeps every version. Undo from History.",
  },
  prReview: {
    title: "Pull request review",
    what: "embry0 reads each ready pull request and leaves comments. It never approves or blocks a merge. Each review is a paid job.",
    takesEffect: "From the next pull request.",
    risk: "check",
    undo: "Turn it off again. History keeps every version.",
  },
  accounts: {
    title: "Accounts",
    what: "Which GitHub or GitLab account embry0 pushes as, and which AI account pays for its work.",
    takesEffect: "From the next job. A job already running keeps the account it started with.",
    risk: "admin",
    undo: "Pick the earlier account again. Changes are recorded in the audit log.",
  },
  docker: {
    title: "Docker access",
    what:
      "Lets this repository's code start its own containers. Only needed for apps started with docker compose. " +
      "It also lets that code reach the passwords embry0 stores.",
    takesEffect: "From the next test computer.",
    risk: "admin",
    undo: "An admin can turn it off, but that cannot take back anything already seen.",
  },
  designStandard: {
    title: "Design checks",
    what: "Whether embry0 checks this repository's screens against your company's design rules.",
    takesEffect: "From the next design check.",
    risk: "admin",
    undo: "An admin sets it back on the organization's Standards page.",
  },
};

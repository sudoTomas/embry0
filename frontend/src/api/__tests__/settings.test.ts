import { describe, it, expect, vi, beforeEach } from "vitest";
import type { InternalAxiosRequestConfig } from "axios";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const YAML = "# why\nversion: 1\nsample:\n  per_tick: 5\n";

beforeEach(() => {
  vi.resetModules();
});

async function capturing(responseData: unknown = {}) {
  const { api } = await import("../client");
  const captured: { config?: InternalAxiosRequestConfig } = {};
  api.defaults.adapter = async (config) => {
    captured.config = config;
    return { data: responseData, status: 200, statusText: "OK", headers: {}, config };
  };
  return captured;
}

describe("settings api", () => {
  it("reads the document, the effective values and one revision from their routes", async () => {
    const captured = await capturing({ generation: 3 });
    const settings = await import("../settings");

    await expect(settings.fetchSettings()).resolves.toEqual({ generation: 3 });
    expect(captured.config?.url).toBe("/config/settings");
    expect(captured.config?.method).toBe("get");

    await settings.fetchEffectiveSettings();
    expect(captured.config?.url).toBe("/config/settings/effective");

    await settings.fetchSettingsRevision(12);
    expect(captured.config?.url).toBe("/config/settings/revisions/12");
  });

  it("lists the revisions, unwrapped, asking for the newest 50", async () => {
    const revisions = [{ id: 2 }, { id: 1 }];
    const captured = await capturing({ revisions });
    const { fetchSettingsRevisions } = await import("../settings");

    await expect(fetchSettingsRevisions()).resolves.toEqual(revisions);
    expect(captured.config?.url).toBe("/config/settings/revisions");
    expect(captured.config?.params).toEqual({ limit: 50 });
  });

  it("previews with a dry run against the generation the edit started from, the 422 left to the editor", async () => {
    const captured = await capturing({ would_change: true, text_diff: "", changes: [], unknown_sections: [], unknown_keys: [] });
    const { previewSettings } = await import("../settings");

    await previewSettings(YAML, 3);

    expect(captured.config?.method).toBe("put");
    expect(captured.config?.url).toBe("/config/settings");
    expect(captured.config?.params).toEqual({ dry_run: 1 });
    // The text goes as written, comments included: never JSON-encoded.
    expect(captured.config?.data).toBe(YAML);
    expect(captured.config?.headers["Content-Type"]).toBe("application/yaml");
    expect(captured.config?.headers["X-Requested-With"]).toBe("XMLHttpRequest");
    // A stale tag is a 409 on a dry run too, so the preview is of the version the edit started from.
    expect(captured.config?.headers["If-Match"]).toBe('"3"');
    expect(captured.config?.suppressValidationToast).toBe(true);
  });

  it("saves with If-Match naming the generation the edit started from, and the reason", async () => {
    const captured = await capturing({ generation: 4, revision_id: 9, changes: [], unknown_sections: [] });
    const { saveSettings } = await import("../settings");

    const result = await saveSettings({ yamlText: YAML, generation: 3, reason: "  quieter at night  " });

    expect(result).toEqual({ generation: 4, revision_id: 9, changes: [], unknown_sections: [] });
    expect(captured.config?.method).toBe("put");
    expect(captured.config?.url).toBe("/config/settings");
    expect(captured.config?.data).toBe(YAML);
    expect(captured.config?.headers["If-Match"]).toBe('"3"');
    expect(captured.config?.headers["Content-Type"]).toBe("application/yaml");
    expect(captured.config?.params).toEqual({ reason: "quieter at night" });
    expect(captured.config?.suppressValidationToast).toBe(true);
  });

  it("saves an empty text as an empty body, with no reason when none was given", async () => {
    const captured = await capturing({ generation: 1, revision_id: 1, changes: [], unknown_sections: [] });
    const { saveSettings } = await import("../settings");

    await saveSettings({ yamlText: "", generation: 0, reason: "   " });

    expect(captured.config?.data).toBe("");
    expect(captured.config?.headers["If-Match"]).toBe('"0"');
    expect(captured.config?.params).toBeUndefined();
  });

  it("undoes with a POST to the revision, the 422 left to the caller", async () => {
    const captured = await capturing({ generation: 5, revision_id: 10, changes: [] });
    const { undoSettingsChange } = await import("../settings");

    await expect(undoSettingsChange(9)).resolves.toEqual({ generation: 5, revision_id: 10, changes: [] });
    expect(captured.config?.method).toBe("post");
    expect(captured.config?.url).toBe("/config/settings/revisions/9/undo");
    expect(captured.config?.headers["X-Requested-With"]).toBe("XMLHttpRequest");
    expect(captured.config?.suppressValidationToast).toBe(true);
  });
});

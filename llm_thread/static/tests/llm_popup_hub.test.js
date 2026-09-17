/** @odoo-module **/

import { describe, expect, test } from "@odoo/hoot";
import {
  parsePopupHubState,
  serializePopupHubState,
} from "@llm_thread/services/llm_popup_hub_service";

describe("@llm_thread/llm.PopupHub persistence", () => {
  test("discards corrupt JSON", () => {
    const parsed = parsePopupHubState("{not json");
    expect(parsed.opened).toEqual([]);
    expect(parsed.focusedThreadId).toBe(null);
  });

  test("discards unknown versions and non-objects", () => {
    expect(parsePopupHubState(JSON.stringify({ version: 99, opened: [] })).opened).toEqual(
      []
    );
    expect(parsePopupHubState("[]").opened).toEqual([]);
    expect(parsePopupHubState(null).opened).toEqual([]);
  });

  test("keeps only opaque thread IDs and layout flags", () => {
    const parsed = parsePopupHubState(
      JSON.stringify({
        version: 1,
        focusedThreadId: 42,
        opened: [
          {
            threadId: 42,
            folded: false,
            order: 0,
            name: "Should be ignored",
            body: "PHI should never be restored",
          },
          { threadId: 57, folded: true, order: 1 },
          { threadId: "bad", folded: false, order: 2 },
          { folded: false, order: 3 },
        ],
      })
    );
    expect(parsed.opened).toEqual([
      { threadId: 42, folded: false, order: 0 },
      { threadId: 57, folded: true, order: 1 },
    ]);
    expect(parsed.focusedThreadId).toBe(42);
  });

  test("drops duplicate and stale focused thread IDs", () => {
    const parsed = parsePopupHubState(
      JSON.stringify({
        version: 1,
        focusedThreadId: 999,
        opened: [
          { threadId: 1, folded: false, order: 2 },
          { threadId: 1, folded: true, order: 1 },
          { threadId: 2, folded: true, order: 0 },
        ],
      })
    );
    expect(parsed.opened.map((item) => item.threadId)).toEqual([2, 1]);
    expect(parsed.focusedThreadId).toBe(2);
  });

  test("serialize stores layout only", () => {
    const raw = serializePopupHubState(
      [
        {
          threadId: 7,
          folded: true,
          order: 0,
          name: "Secret conversation",
          messages: [{ body: "patient data" }],
        },
      ],
      7
    );
    const stored = JSON.parse(raw);
    expect(stored).toEqual({
      version: 1,
      opened: [{ threadId: 7, folded: true, order: 0 }],
      focusedThreadId: 7,
    });
    expect(JSON.stringify(stored).includes("Secret")).toBe(false);
    expect(JSON.stringify(stored).includes("patient")).toBe(false);
  });
});

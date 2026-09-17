/** @odoo-module **/

import { Deferred } from "@web/core/utils/concurrency";
import { _t } from "@web/core/l10n/translation";
import { browser } from "@web/core/browser/browser";
import { reactive } from "@odoo/owl";
import { registry } from "@web/core/registry";

export const LLM_POPUP_HUB_STORAGE_KEY = "llm.PopupHub";
export const LLM_POPUP_HUB_STORAGE_VERSION = 1;

function parseOpenedWindow(item, fallbackOrder) {
  if (!item || typeof item !== "object") {
    return null;
  }
  const threadId = Number(item.threadId);
  if (!Number.isInteger(threadId) || threadId <= 0) {
    return null;
  }
  const orderValue = Number(item.order);
  return {
    threadId,
    folded: Boolean(item.folded),
    order: Number.isFinite(orderValue) ? orderValue : fallbackOrder,
  };
}

/**
 * Parse persisted AI popup layout state.
 * Only thread IDs and window layout are accepted; conversation content is ignored.
 *
 * @param {String} raw JSON from localStorage
 * @returns {Object} opened windows and focused thread id
 */
export function parsePopupHubState(raw) {
  const empty = { opened: [], focusedThreadId: null };
  if (!raw || typeof raw !== "string") {
    return empty;
  }
  let data = null;
  try {
    data = JSON.parse(raw);
  } catch {
    return empty;
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    return empty;
  }
  if (data.version !== LLM_POPUP_HUB_STORAGE_VERSION || !Array.isArray(data.opened)) {
    return empty;
  }
  const opened = [];
  const seen = new Set();
  for (const item of data.opened) {
    const parsed = parseOpenedWindow(item, opened.length);
    if (!parsed || seen.has(parsed.threadId)) {
      continue;
    }
    seen.add(parsed.threadId);
    opened.push(parsed);
  }
  opened.sort((a, b) => a.order - b.order);
  opened.forEach((item, index) => {
    item.order = index;
  });
  const focusedThreadId = Number(data.focusedThreadId);
  const focused =
    Number.isInteger(focusedThreadId) && seen.has(focusedThreadId)
      ? focusedThreadId
      : opened[0]?.threadId ?? null;
  return { opened, focusedThreadId: focused };
}

/**
 * Serialize window layout for browser storage. Never include message bodies,
 * prompts, assistant config, or other conversation content.
 *
 * @param {Array<{threadId: Number, folded: Boolean, order: Number}>} windows
 * @param {Number|null} focusedThreadId
 * @returns {String}
 */
export function serializePopupHubState(windows, focusedThreadId) {
  return JSON.stringify({
    version: LLM_POPUP_HUB_STORAGE_VERSION,
    opened: windows.map((win, index) => ({
      threadId: win.threadId,
      folded: Boolean(win.folded),
      order: Number.isFinite(win.order) ? win.order : index,
    })),
    focusedThreadId: focusedThreadId || null,
  });
}

/**
 * Independent AI popup hub. Borrows ChatHub UX ideas without routing
 * llm.thread through Odoo's Discuss ChatHub.
 */
export const llmPopupHubService = {
  dependencies: ["mail.store", "llm.store", "notification", "ui"],

  start(env, { "mail.store": mailStore, "llm.store": llmStore, notification, ui }) {
    const hub = reactive({
      WINDOW: 380,
      WINDOW_GAP: 10,
      WINDOW_INBETWEEN: 5,
      FOLDED_WIDTH: 240,
      windows: [],
      focusedThreadId: null,
      isReady: new Deferred(),

      get hasOpenWindows() {
        return this.windows.length > 0;
      },

      get expandedWindows() {
        return this.windows.filter((win) => !win.folded);
      },

      get foldedWindows() {
        return this.windows.filter((win) => win.folded);
      },

      get maxOpened() {
        if (ui.isSmall) {
          return 1;
        }
        const available = browser.innerWidth - this.WINDOW_GAP * 2;
        return Math.max(
          1,
          Math.floor(available / (this.WINDOW + this.WINDOW_INBETWEEN))
        );
      },

      getWindow(threadId) {
        return this.windows.find((win) => win.threadId === threadId);
      },

      getWindowRight(win) {
        if (ui.isSmall) {
          return 0;
        }
        const expanded = this.expandedWindows;
        const expandedIndex = expanded.findIndex(
          (item) => item.threadId === win.threadId
        );
        if (expandedIndex !== -1) {
          return this.WINDOW_GAP + expandedIndex * (this.WINDOW + this.WINDOW_INBETWEEN);
        }
        const folded = this.foldedWindows;
        const foldedIndex = folded.findIndex(
          (item) => item.threadId === win.threadId
        );
        const expandedWidth =
          expanded.length * (this.WINDOW + this.WINDOW_INBETWEEN);
        return (
          this.WINDOW_GAP +
          expandedWidth +
          foldedIndex * (this.FOLDED_WIDTH + this.WINDOW_INBETWEEN)
        );
      },

      async openFromSystray() {
        await this.isReady;
        if (this.windows.length) {
          const focusId =
            this.focusedThreadId || this.windows[this.windows.length - 1].threadId;
          this.focusThread(focusId);
          return;
        }
        const threads = llmStore.llmThreadList;
        if (threads.length) {
          await this.openThread(threads[0].id);
          return;
        }
        const threadId = await llmStore.createNewThread({ select: false });
        if (threadId) {
          await this.openThread(threadId);
        }
      },

      async openThread(threadId) {
        await this.isReady;
        const existing = this.getWindow(threadId);
        if (existing) {
          this.focusThread(threadId);
          return existing;
        }
        const thread = await llmStore.ensureThreadLoaded(threadId);
        if (!thread) {
          notification.add(
            _t(
              "Could not load this conversation. It may have been deleted or you may not have access."
            ),
            { type: "danger" }
          );
          return null;
        }
        const win = {
          threadId,
          folded: false,
          order: this.windows.length,
          lastFocused: Date.now(),
        };
        this.windows.push(win);
        this.focusedThreadId = threadId;
        this.onRecompute();
        this.save();
        return win;
      },

      focusThread(threadId) {
        const win = this.getWindow(threadId);
        if (!win) {
          return;
        }
        win.folded = false;
        win.lastFocused = Date.now();
        this.focusedThreadId = threadId;
        if (ui.isSmall) {
          for (const other of this.windows) {
            if (other.threadId !== threadId) {
              other.folded = true;
            }
          }
        }
        this.onRecompute();
        this.save();
      },

      foldThread(threadId) {
        const win = this.getWindow(threadId);
        if (!win) {
          return;
        }
        win.folded = true;
        if (this.focusedThreadId === threadId) {
          const next = this.expandedWindows.at(-1);
          this.focusedThreadId = next ? next.threadId : null;
        }
        this.save();
      },

      toggleFold(threadId) {
        const win = this.getWindow(threadId);
        if (!win) {
          return;
        }
        if (win.folded) {
          this.focusThread(threadId);
        } else {
          this.foldThread(threadId);
        }
      },

      closeThread(threadId) {
        const index = this.windows.findIndex((win) => win.threadId === threadId);
        if (index === -1) {
          return;
        }
        this.windows.splice(index, 1);
        this.windows.forEach((win, order) => {
          win.order = order;
        });
        if (this.focusedThreadId === threadId) {
          const next = this.expandedWindows.at(-1) || this.windows.at(-1);
          this.focusedThreadId = next ? next.threadId : null;
        }
        this.save();
      },

      closeAll() {
        this.windows.splice(0, this.windows.length);
        this.focusedThreadId = null;
        this.save();
      },

      async createAndOpenThread() {
        const threadId = await llmStore.createNewThread({ select: false });
        if (threadId) {
          await this.openThread(threadId);
        }
      },

      onRecompute() {
        const expanded = this.expandedWindows;
        const overflow = expanded.length - this.maxOpened;
        if (overflow <= 0) {
          return;
        }
        const byRecency = [...expanded].sort(
          (a, b) => (a.lastFocused || 0) - (b.lastFocused || 0)
        );
        for (let i = 0; i < overflow; i++) {
          byRecency[i].folded = true;
        }
      },

      save() {
        try {
          browser.localStorage.setItem(
            LLM_POPUP_HUB_STORAGE_KEY,
            serializePopupHubState(this.windows, this.focusedThreadId)
          );
        } catch (error) {
          console.warn("Could not persist AI popup layout:", error);
        }
      },

      async restore(raw) {
        const parsed = parsePopupHubState(raw);
        const restored = [];
        for (const item of parsed.opened) {
          const thread = await llmStore.ensureThreadLoaded(item.threadId);
          if (!thread) {
            continue;
          }
          restored.push({
            threadId: item.threadId,
            folded: item.folded,
            order: restored.length,
            lastFocused: Date.now() - restored.length,
          });
        }
        this.windows.splice(0, this.windows.length, ...restored);
        const focusedStillOpen = restored.some(
          (win) => win.threadId === parsed.focusedThreadId
        );
        this.focusedThreadId = focusedStillOpen
          ? parsed.focusedThreadId
          : restored[0]?.threadId ?? null;
        this.onRecompute();
        this.save();
      },

      async initialize() {
        try {
          await Promise.all([mailStore.isReady, llmStore.isReady]);
          await this.restore(
            browser.localStorage.getItem(LLM_POPUP_HUB_STORAGE_KEY)
          );
          this.isReady.resolve();
        } catch (error) {
          console.error("Error initializing AI popup hub:", error);
          this.isReady.resolve();
        }
      },
    });

    browser.addEventListener("storage", (ev) => {
      if (ev.key === LLM_POPUP_HUB_STORAGE_KEY) {
        hub.restore(ev.newValue || undefined);
      } else if (ev.key === null) {
        hub.restore();
      }
    });
    browser.addEventListener("resize", () => {
      hub.onRecompute();
    });

    hub.initialize();
    return hub;
  },
};

registry.category("services").add("llm.popup_hub", llmPopupHubService);

/** @odoo-module **/

import {
  LLM_POPUP_HUB_STORAGE_KEY,
  parsePopupHubState,
  serializePopupHubState,
} from "@llm_thread/services/llm_popup_hub_state";
import { reactive } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { Deferred } from "@web/core/utils/concurrency";

export {
  LLM_POPUP_HUB_STORAGE_KEY,
  LLM_POPUP_HUB_STORAGE_VERSION,
  parsePopupHubState,
  serializePopupHubState,
} from "@llm_thread/services/llm_popup_hub_state";

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
        await this.createAndOpenThread();
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

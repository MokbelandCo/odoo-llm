/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { LLMChatWindow } from "@llm_thread/components/llm_chat_window/llm_chat_window";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

/**
 * Renders zero or more independent AI chat popups.
 * Registered as a long-lived main component, similar to mail.ChatHub.
 */
export class LLMChatHub extends Component {
  static components = { LLMChatWindow };
  static template = "llm_thread.LLMChatHub";
  static props = {};

  setup() {
    this.popupHub = useState(useService("llm.popup_hub"));
    this.mailStore = useState(useService("mail.store"));
    this.ui = useState(useService("ui"));
  }

  get isSmall() {
    return this.ui.isSmall;
  }

  getWindowName(win) {
    const thread = this.mailStore.Thread.get({
      model: "llm.thread",
      id: win.threadId,
    });
    return thread?.name || _t("AI Chat");
  }

  onSwitchWindow(threadId) {
    this.popupHub.focusThread(threadId);
  }
}

export const llmChatHubService = {
  dependencies: ["llm.popup_hub", "ui"],
  start() {
    registry.category("main_components").add("llm.ChatHub", {
      Component: LLMChatHub,
    });
  },
};
registry.category("services").add("llm.chat_hub", llmChatHubService);

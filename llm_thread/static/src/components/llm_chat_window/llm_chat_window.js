/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { LLMChatContainer } from "@llm_thread/components/llm_chat_container/llm_chat_container";
import { _t } from "@web/core/l10n/translation";
import { localization } from "@web/core/l10n/localization";
import { useService } from "@web/core/utils/hooks";

/**
 * Lightweight ChatWindow-like shell around the existing LLMChatContainer.
 * Each window is bound to one llm.thread and never writes mail.store.discuss.thread.
 */
export class LLMChatWindow extends Component {
  static components = { LLMChatContainer };
  static template = "llm_thread.LLMChatWindow";
  static props = {
    window: { type: Object },
  };

  setup() {
    this.popupHub = useState(useService("llm.popup_hub"));
    this.mailStore = useState(useService("mail.store"));
    this.llmStore = useState(useService("llm.store"));
    this.action = useService("action");
    this.ui = useState(useService("ui"));
  }

  get isSmall() {
    return this.ui.isSmall;
  }

  get thread() {
    return this.mailStore.Thread.get({
      model: "llm.thread",
      id: this.props.window.threadId,
    });
  }

  get title() {
    return this.thread?.name || _t("AI Chat");
  }

  get aiLabel() {
    return _t("AI");
  }

  get foldTitle() {
    return this.props.window.folded ? _t("Open") : _t("Minimize");
  }

  get closeTitle() {
    return _t("Close");
  }

  get maximizeTitle() {
    return _t("Open in full screen");
  }

  get attClass() {
    return {
      "o-folded": this.props.window.folded && !this.isSmall,
      "o-mobile w-100 h-100": this.isSmall,
      "o-focused": this.popupHub.focusedThreadId === this.props.window.threadId,
    };
  }

  get style() {
    if (this.isSmall) {
      if (this.props.window.folded) {
        return "display: none;";
      }
      return "";
    }
    const offsetFrom = localization.direction === "rtl" ? "left" : "right";
    const oppositeFrom = offsetFrom === "right" ? "left" : "right";
    const right = this.popupHub.getWindowRight(this.props.window);
    return `${offsetFrom}: ${right}px; ${oppositeFrom}: auto;`;
  }

  onClickHeader() {
    if (this.isSmall) {
      return;
    }
    this.popupHub.focusThread(this.props.window.threadId);
  }

  onToggleFold(ev) {
    ev.stopPropagation();
    this.popupHub.toggleFold(this.props.window.threadId);
  }

  onClose(ev) {
    ev.stopPropagation();
    this.popupHub.closeThread(this.props.window.threadId);
  }

  async onMaximize(ev) {
    ev.stopPropagation();
    const threadId = this.props.window.threadId;
    this.popupHub.closeThread(threadId);
    await this.llmStore.selectThread(threadId);
    await this.action.doAction({
      type: "ir.actions.client",
      tag: "llm_thread.chat_client_action",
      name: _t("AI Chat"),
      context: { active_id: `llm.thread_${threadId}` },
      params: { active_id: `llm.thread_${threadId}` },
    });
  }
}

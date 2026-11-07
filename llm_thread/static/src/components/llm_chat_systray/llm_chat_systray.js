/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

/**
 * Backend systray entry that opens the AI chat popup without navigating.
 */
export class LLMChatSystray extends Component {
  static template = "llm_thread.LLMChatSystray";
  static props = {};

  setup() {
    this.popupHub = useState(useService("llm.popup_hub"));
  }

  get title() {
    return _t("AI Chat");
  }

  get ariaLabel() {
    return _t("Ask AI");
  }

  onClick() {
    this.popupHub.openFromSystray();
  }
}

registry
  .category("systray")
  .add("llm_thread.AIChat", { Component: LLMChatSystray }, { sequence: 24 });

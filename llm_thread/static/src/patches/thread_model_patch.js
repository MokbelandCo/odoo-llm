/** @odoo-module **/

import { Thread } from "@mail/core/common/thread_model";
import { patch } from "@web/core/utils/patch";

const LLM_THREAD_FIELDS = [
  "write_date",
  "provider_id",
  "model_id",
  "tool_ids",
  "res_model",
  "res_id",
  "assistant_id",
  "prompt_id",
];

/**
 * Assign LLM-specific fields that Odoo 17 Thread.update() does not whitelist.
 */
patch(Thread.prototype, {
  update(data) {
    super.update(...arguments);
    if (this.model !== "llm.thread" || !data) {
      return;
    }
    for (const field of LLM_THREAD_FIELDS) {
      if (field in data) {
        this[field] = data[field];
      }
    }
  },
});

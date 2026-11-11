/** @odoo-module **/

import {
  getLLMRecordLink,
  isModifiedClick,
  openLLMRecordLink,
} from "@llm_thread/record_links/record_link";
import { Store } from "@mail/core/common/store_service";
import { patch } from "@web/core/utils/patch";

/**
 * Record links rendered by llm_thread (class o_llm_record_link) open through
 * the action service with breadcrumbs kept. Other links keep the standard
 * mail behaviour.
 */
patch(Store.prototype, {
  handleClickOnLink(ev, thread) {
    const link = getLLMRecordLink(ev);
    if (!link) {
      return super.handleClickOnLink(...arguments);
    }
    if (isModifiedClick(ev)) {
      return true;
    }
    ev.preventDefault();
    openLLMRecordLink(this.env, {
      resModel: link.dataset.oeModel,
      resId: link.dataset.oeId,
    });
    return true;
  },
});

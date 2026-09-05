/** @odoo-module **/

import { ThreadService } from "@mail/core/common/thread_service";
import { patch } from "@web/core/utils/patch";

/**
 * Odoo 17 ThreadService.getFetchRoute() only knows discuss.channel, chatter,
 * and mailbox. LLM threads use type "llm_chat", which would throw
 * "Unknown thread type" and show "An error occurred while fetching messages."
 *
 * Route them through /mail/thread/messages like chatter (llm.thread is a
 * mail.thread).
 */
patch(ThreadService.prototype, {
  getFetchRoute(thread) {
    if (thread?.model === "llm.thread") {
      return "/mail/thread/messages";
    }
    return super.getFetchRoute(thread);
  },

  getFetchParams(thread) {
    if (thread?.model === "llm.thread") {
      return {
        thread_id: thread.id,
        thread_model: "llm.thread",
      };
    }
    return super.getFetchParams(thread);
  },
});

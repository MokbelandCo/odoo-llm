/** @odoo-module **/

import { RPCError } from "@web/core/network/rpc_service";
import { _t } from "@web/core/l10n/translation";

export const LLM_RECORD_LINK_SELECTOR = "a.o_llm_record_link";

const MODEL_NAME_RE = /^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$/;

/**
 * Return the record link anchor for a click, including clicks on nested
 * elements inside the anchor.
 *
 * @param {MouseEvent} ev
 * @returns {HTMLAnchorElement|null}
 */
export function getLLMRecordLink(ev) {
  const target = ev.target;
  if (!(target instanceof Element)) {
    return null;
  }
  return target.closest(LLM_RECORD_LINK_SELECTOR);
}

/**
 * Clicks that should keep the browser's default behaviour (open the
 * fallback URL in a new tab/window).
 *
 * @param {MouseEvent} ev
 * @returns {Boolean}
 */
export function isModifiedClick(ev) {
  return Boolean(
    ev.ctrlKey ||
      ev.metaKey ||
      ev.shiftKey ||
      ev.altKey ||
      (ev.button && ev.button !== 0)
  );
}

/**
 * Open a record in the current action stack (same tab, breadcrumbs kept).
 *
 * The action service runs its usual unsaved-changes check first; when the
 * current view refuses to leave, the returned promise never settles and the
 * chat stays as it is. Access rights are checked by the normal form load.
 *
 * @param {Object} env
 * @param {Object} params
 * @param {String} params.resModel
 * @param {Number|String} params.resId
 * @returns {Promise<Boolean>}
 */
export async function openLLMRecordLink(env, { resModel, resId }) {
  const { action, notification } = env.services;
  const id = Number(resId);
  if (!MODEL_NAME_RE.test(resModel || "") || !Number.isInteger(id) || id <= 0) {
    notification.add(_t("This record link is not valid."), { type: "warning" });
    return false;
  }
  try {
    await action.doAction(
      {
        type: "ir.actions.act_window",
        res_model: resModel,
        res_id: id,
        views: [[false, "form"]],
        target: "current",
      },
      { clearBreadcrumbs: false }
    );
    return true;
  } catch (error) {
    // RPC errors (missing record, access denied) are already shown by the
    // error service; the action service restores the previous controller.
    if (!(error instanceof RPCError)) {
      console.error("Could not open record link:", error);
      notification.add(
        _t(
          "Could not open this record. It may have been deleted or you may not have access."
        ),
        { type: "danger" }
      );
    }
    return false;
  }
}

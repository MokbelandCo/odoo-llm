/** @odoo-module **/

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

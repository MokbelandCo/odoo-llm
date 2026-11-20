/** @odoo-module **/

import { registry } from "@web/core/registry";

const CHAT = ".o_LLMChatContainer";
const INLINE_SETTINGS = `${CHAT} .o-llm-thread-header-settings:not(.o-llm-thread-header-settings--compact)`;
const TOOLS_TOGGLE = `${INLINE_SETTINGS} .o-llm-thread-header-tools > .dropdown-toggle`;
const TOOLS_LIST = ".o-llm-thread-header-tools-list";
const DEFAULT_TOOL = "sel_tour_default";
const OPTIONAL_TOOL = "sel_tour_optional";
const ALWAYS_TOOL = "sel_tour_always";

function toolInput(name) {
  const label = [...document.querySelectorAll(`${TOOLS_LIST} label`)].find(
    (el) => el.innerText.trim() === name
  );
  return label && document.getElementById(label.getAttribute("for"));
}

registry.category("web_tour.tours").add("llm_thread_tool_selection_tour", {
  test: true,
  url: "/web#action=llm_thread.action_llm_chat",
  steps: () => [
    {
      content: "Start a new chat",
      trigger: `${CHAT} button[title="New Thread"]`,
      extra_trigger: `${CHAT} .o-llm-thread-active`,
    },
    {
      content: "The new chat counts only the Default tool",
      trigger: `${CHAT} .o-llm-thread-active[title^="Chat "]`,
      extra_trigger: `${TOOLS_TOGGLE}:contains((1))`,
      isCheck: true,
    },
    {
      content: "Open the Tools menu",
      trigger: TOOLS_TOGGLE,
    },
    {
      content: "Default tool is selected, optional is not, Always Include is hidden",
      trigger: `${TOOLS_LIST} label:contains(${OPTIONAL_TOOL})`,
      run() {
        const defaultInput = toolInput(DEFAULT_TOOL);
        const optionalInput = toolInput(OPTIONAL_TOOL);
        if (!defaultInput || !defaultInput.checked) {
          throw new Error("The Default tool must be preselected in a new chat");
        }
        if (!optionalInput || optionalInput.checked) {
          throw new Error("A non-default optional tool must not be preselected");
        }
        if (toolInput(ALWAYS_TOOL)) {
          throw new Error("Always Include tools must not be listed in the selector");
        }
      },
    },
    {
      content: "Remove the Default tool from this chat",
      trigger: `${TOOLS_LIST} label:contains(${DEFAULT_TOOL})`,
    },
    {
      content: "The Default tool is unchecked and no longer counted",
      trigger: `${TOOLS_LIST} .form-check:has(label:contains(${DEFAULT_TOOL})) input:not(:checked):not([disabled])`,
      extra_trigger: `${TOOLS_TOGGLE}:not(:contains("(1)"))`,
      isCheck: true,
    },
  ],
});

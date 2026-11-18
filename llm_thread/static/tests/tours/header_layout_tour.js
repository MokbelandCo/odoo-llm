/** @odoo-module **/

import { registry } from "@web/core/registry";

const THREAD_NAME = "Header Layout Tour Thread";
const PROVIDER_NAME = "Header Tour Provider";
const MODEL_NAME = "header-tour-model";
const TOOL_NAME = "header_tour_tool";
const CHAT = ".o_LLMChatContainer";
const POPUP = ".o-llm-ChatWindow";
const INLINE_SETTINGS = `${CHAT} .o-llm-thread-header-settings:not(.o-llm-thread-header-settings--compact)`;
const MORE = `${POPUP} .o-llm-ThreadHeader-more`;
const MORE_MENU = ".o-llm-ThreadHeader-moreMenu";
const TOOLS_MENU = ".o-llm-thread-header-tools-menu";

function services() {
  return odoo.__WOWL_DEBUG__.root.env.services;
}

function visible(el) {
  const rect = el.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}

function settingToggles(container) {
  return [...container.querySelectorAll(":scope > .o-dropdown > .dropdown-toggle")];
}

registry.category("web_tour.tours").add("llm_thread_header_layout_tour", {
  test: true,
  url: "/web#action=llm_thread.action_llm_chat",
  steps: () => [
    {
      content: "Full-screen chat shows readable inline settings toggles",
      trigger: `${CHAT} .o-llm-thread-active[title="${THREAD_NAME}"]`,
      extra_trigger: `${INLINE_SETTINGS} .dropdown-toggle:contains(${PROVIDER_NAME})`,
      run() {
        const toggles = settingToggles(document.querySelector(INLINE_SETTINGS));
        const labels = toggles.map((toggle) => toggle.innerText.trim());
        // Provider, model and Tools always; Assistant when llm_assistant is installed.
        if (toggles.length < 3 || labels.some((label) => !label)) {
          throw new Error(`Blank header toggles: ${JSON.stringify(labels)}`);
        }
        if (!labels.includes(MODEL_NAME) || !labels.some((l) => l.startsWith("Tools"))) {
          throw new Error(`Unexpected header toggles: ${JSON.stringify(labels)}`);
        }
        if (toggles.some((toggle) => !visible(toggle) || toggle.closest(".dropdown-menu"))) {
          throw new Error("Header toggles must be visible in the header itself");
        }
        if (document.querySelector(`${CHAT} .o-llm-thread-header .fa-ellipsis-h`)) {
          throw new Error("Full-screen chat must not use the compact ellipsis");
        }
      },
    },
    {
      content: "Open the provider menu",
      trigger: `${INLINE_SETTINGS} .dropdown-toggle:contains(${PROVIDER_NAME})`,
    },
    {
      content: "Provider menu lists the providers",
      trigger: `.o-dropdown--menu .dropdown-item:contains(${PROVIDER_NAME})`,
      async run() {
        const [thread] = await services().orm.searchRead(
          "llm.thread",
          [["name", "=", THREAD_NAME]],
          ["id"]
        );
        await services()["llm.popup_hub"].openThread(thread.id);
      },
    },
    {
      content: "Popup header has exactly one visible ellipsis and no inline settings",
      trigger: MORE,
      run() {
        const header = document.querySelector(`${POPUP} .o-llm-thread-header`);
        const ellipses = [...header.querySelectorAll(".fa-ellipsis-h")];
        if (ellipses.length !== 1 || !visible(ellipses[0]) || ellipses[0].closest(".dropdown-menu")) {
          throw new Error("Popup header must show one visible ellipsis toggle");
        }
        if (
          header.querySelector(
            ".o-llm-thread-header-settings:not(.o-llm-thread-header-settings--compact)"
          )
        ) {
          throw new Error("Popup header must not render the inline settings");
        }
        if (document.querySelector(`${POPUP} .o-llm-sidebar`)) {
          throw new Error("Popup must not render the conversation sidebar");
        }
      },
    },
    {
      content: "Open the compact settings menu",
      trigger: MORE,
    },
    {
      content: "Compact menu holds the labelled settings toggles",
      trigger: `${MORE_MENU} .o-llm-thread-header-settings--compact .dropdown-toggle:contains(${PROVIDER_NAME})`,
      run() {
        const settings = document.querySelector(
          `${MORE_MENU} .o-llm-thread-header-settings--compact`
        );
        const labels = settingToggles(settings).map((toggle) => toggle.innerText.trim());
        if (labels.length < 3 || labels.some((label) => !label)) {
          throw new Error(`Blank compact toggles: ${JSON.stringify(labels)}`);
        }
      },
    },
    {
      content: "Open the Tools submenu from the compact menu",
      trigger: `${MORE_MENU} .o-llm-thread-header-tools > .dropdown-toggle`,
    },
    {
      content: "Tools submenu opens beside the compact menu, inside the viewport",
      trigger: `${TOOLS_MENU} label:contains(${TOOL_NAME})`,
      run() {
        const toggle = document
          .querySelector(`${MORE_MENU} .o-llm-thread-header-tools > .dropdown-toggle`)
          .getBoundingClientRect();
        const tools = document.querySelector(TOOLS_MENU).getBoundingClientRect();
        const rects = JSON.stringify({ toggle, tools, width: window.innerWidth });
        if (tools.right > toggle.left + 1) {
          throw new Error(`Tools submenu should open to the left of the popup menu ${rects}`);
        }
        if (tools.left < 0 || tools.right > window.innerWidth) {
          throw new Error(`Tools submenu overflows the viewport ${rects}`);
        }
      },
    },
    {
      content: "Enable the tour tool",
      trigger: `${TOOLS_MENU} label:contains(${TOOL_NAME})`,
    },
    {
      content: "Tools toggle counts the enabled tool",
      trigger: `${MORE_MENU} .o-llm-thread-header-tools > .dropdown-toggle:contains((1))`,
      isCheck: true,
    },
  ],
});

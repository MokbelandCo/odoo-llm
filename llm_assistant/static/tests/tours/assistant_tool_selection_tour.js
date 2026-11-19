/** @odoo-module **/

import { registry } from "@web/core/registry";

const TOOLS = ".o_field_widget[name=tool_ids]";
const DEFAULT_TOOL = "sel_tour_default";
const OPTIONAL_TOOL = "sel_tour_optional";
const ALWAYS_TOOL = "sel_tour_always";
const PROMPT = "Tool Selection Tour Prompt";

function tagNames() {
  return [...document.querySelectorAll(`${TOOLS} .o_tag`)].map((tag) =>
    tag.innerText.trim()
  );
}

registry.category("web_tour.tours").add("llm_assistant_tool_selection_tour", {
  test: true,
  url: "/web#action=llm_assistant.action_llm_assistant",
  steps: () => [
    {
      content: "Create an assistant",
      trigger: ".o_list_button_add",
    },
    {
      content: "Name it",
      trigger: ".o_field_widget[name=name] input",
      run: "text Tool Selection Tour Assistant",
    },
    {
      content: "Pick the prompt",
      trigger: ".o_field_widget[name=prompt_id] input",
      run: `text ${PROMPT}`,
    },
    {
      content: "Select the prompt in the dropdown",
      trigger: `.o_field_widget[name=prompt_id] .o-autocomplete--dropdown-item:contains(${PROMPT})`,
    },
    {
      content: "Open the Tools page",
      trigger: ".o_notebook .nav-link:contains(Tools)",
    },
    {
      content: "Preferred Tools start with the Default tool only",
      trigger: `${TOOLS} .o_tag:contains(${DEFAULT_TOOL})`,
      run() {
        const names = tagNames();
        if (names.includes(ALWAYS_TOOL) || names.includes(OPTIONAL_TOOL)) {
          throw new Error(`Unexpected preselected tools: ${JSON.stringify(names)}`);
        }
      },
    },
    {
      content: "Remove the Default tool",
      trigger: `${TOOLS} .o_tag:contains(${DEFAULT_TOOL}) .o_delete`,
    },
    {
      content: "Search tools",
      trigger: `${TOOLS} input`,
      extra_trigger: `${TOOLS}:not(:has(.o_tag:contains(${DEFAULT_TOOL})))`,
      run: "text sel_tour",
    },
    {
      content: "Always Include tools are not offered; add the optional tool",
      trigger: `${TOOLS} .o-autocomplete--dropdown-item:contains(${OPTIONAL_TOOL})`,
      run(actions) {
        const items = [
          ...document.querySelectorAll(`${TOOLS} .o-autocomplete--dropdown-item`),
        ].map((item) => item.innerText.trim());
        if (items.includes(ALWAYS_TOOL)) {
          throw new Error(`Always Include tool offered: ${JSON.stringify(items)}`);
        }
        actions.click();
      },
    },
    {
      content: "Save",
      trigger: `.o_form_button_save`,
      extra_trigger: `${TOOLS} .o_tag:contains(${OPTIONAL_TOOL})`,
    },
    {
      content: "Saved",
      trigger: ".o_form_saved",
      isCheck: true,
    },
  ],
});

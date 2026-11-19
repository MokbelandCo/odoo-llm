/** @odoo-module **/

import { registry } from "@web/core/registry";
import { stepUtils } from "@web_tour/tour_service/tour_utils";

const SECTION = ".o_group[name=openai_chat], div[name=openai_chat]";

registry.category("web_tour.tours").add("llm_openai_model_form_tour", {
  test: true,
  steps: () => [
    {
      content: "OpenAI model form offers the capability probe",
      trigger: "button[name=action_openai_probe_capabilities]",
      run() {},
    },
    {
      content: "Capability summary names the verified endpoints",
      trigger: `.o_field_widget[name=openai_capability_summary]:contains(/v1/responses)`,
      extra_trigger: `.o_field_widget[name=openai_endpoint]`,
      run() {},
    },
    {
      content: "Choose low reasoning effort",
      trigger: ".o_field_widget[name=openai_reasoning_effort] select",
      run() {
        const select = document.querySelector(
          ".o_field_widget[name=openai_reasoning_effort] select"
        );
        const option = [...select.options].find((opt) => opt.textContent.trim() === "Low");
        select.value = option.value;
        select.dispatchEvent(new Event("change", { bubbles: true }));
      },
    },
    {
      content: "Set the output token limit",
      trigger: ".o_field_widget[name=openai_max_output_tokens] input",
      run: "text 1500",
    },
    ...stepUtils.saveForm(),
  ],
});

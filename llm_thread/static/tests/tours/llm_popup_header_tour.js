/** @odoo-module **/

import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("llm_thread_popup_header_tour", {
  url: "/odoo",
  steps: () => [
    {
      trigger: ".o-llm-Systray-toggler",
      run: "click",
    },
    {
      trigger: ".o-llm-ChatWindow .o-llm-thread-header",
    },
    {
      trigger:
        ".o-llm-ChatWindow .o-mail-Composer-input[placeholder='Ask anything...']",
    },
    {
      trigger: ".o-llm-ChatWindow .o-llm-ThreadHeader-more",
      run: "click",
    },
    {
      trigger:
        ".o-llm-ThreadHeader-moreMenu button:contains('tour-model-alpha')",
      run: "click",
    },
    {
      trigger: ".o-dropdown--menu .dropdown-item:contains('tour-model-beta')",
      run: "click",
    },
    {
      trigger: ".o-llm-ChatWindow .o-llm-thread-header:not(:has(.fa-spinner))",
    },
    {
      trigger: ".o-llm-ChatWindow .o-llm-ThreadHeader-more",
      run: "click",
    },
    {
      trigger:
        ".o-llm-ThreadHeader-moreMenu button:contains('tour-model-beta')",
    },
  ],
});

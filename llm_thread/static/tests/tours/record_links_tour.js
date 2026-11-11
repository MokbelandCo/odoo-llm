/** @odoo-module **/

import { registry } from "@web/core/registry";

const THREAD_NAME = "Tour Record Link Thread";
const PARTNER_NAME = "Tour Link Partner";
const CHAT = ".o_LLMChatContainer";
const POPUP = ".o-llm-ChatWindow";
const INLINE_LINK =
  "a.o_llm_record_link:not(.o_llm_tool_record_link):contains(Click here)";
const FALLBACK_LINK = `a.o_llm_tool_record_link:contains(${PARTNER_NAME})`;
const DRAFT = "draft kept across navigation";

function services() {
  return odoo.__WOWL_DEBUG__.root.env.services;
}

function assertNoReload() {
  if (!window.__llmRecordLinkTour) {
    throw new Error("The page was reloaded during record link navigation");
  }
}

function assertBreadcrumbs(expected) {
  const previous = [
    ...document.querySelectorAll(".o_breadcrumb .breadcrumb-item a"),
  ].map((el) => el.textContent.trim());
  const current = document.querySelector(
    ".o_breadcrumb .o_last_breadcrumb_item"
  );
  const items = [...previous, current ? current.textContent.trim() : ""];
  const ok =
    items.length === expected.length &&
    expected.every((name, index) =>
      index === expected.length - 1
        ? items[index].startsWith(name)
        : items[index] === name
    );
  if (!ok) {
    throw new Error(`Unexpected breadcrumbs ${JSON.stringify(items)}`);
  }
}

registry.category("web_tour.tours").add("llm_thread_record_links_tour", {
  test: true,
  url: "/web#action=llm_thread.action_llm_chat",
  steps: () => [
    // Full-screen chat: restored history renders the inline and fallback links.
    {
      content: "Inline record link rendered from history",
      trigger: `${CHAT} .o-llm-thread-active[title="${THREAD_NAME}"]`,
      extra_trigger: `${CHAT} ${INLINE_LINK}`,
      run() {
        window.__llmRecordLinkTour = true;
      },
    },
    {
      content: "Fallback link rendered on the tool result",
      trigger: `${CHAT} ${FALLBACK_LINK}`,
      isCheck: true,
    },
    {
      content: "Unknown token stays inert",
      trigger: `${CHAT} .o_llm_record_link_unavailable`,
      isCheck: true,
    },
    {
      content: "Type a composer draft",
      trigger: `${CHAT} .o-mail-Composer-input`,
      run: `text ${DRAFT}`,
    },
    {
      content: "Click the inline record link",
      trigger: `${CHAT} ${INLINE_LINK}`,
    },
    {
      content: "Record form opens in the current action stack",
      trigger: `.o_form_view .o_breadcrumb .active:contains(${PARTNER_NAME})`,
      run() {
        assertNoReload();
        assertBreadcrumbs(["Chat", PARTNER_NAME]);
      },
    },
    {
      content: "Go back to the chat through the breadcrumb",
      trigger: ".o_breadcrumb .breadcrumb-item a:contains(Chat)",
    },
    {
      content: "Same conversation and composer draft are restored",
      trigger: `${CHAT} .o-llm-thread-active[title="${THREAD_NAME}"]`,
      extra_trigger: `${CHAT} .o-mail-Composer-input`,
      run() {
        assertNoReload();
        const input = document.querySelector(`${CHAT} .o-mail-Composer-input`);
        if (input.value !== DRAFT) {
          throw new Error(
            `Composer draft lost: ${JSON.stringify(input.value)}`
          );
        }
      },
    },
    {
      content: "Click the fallback link of the tool result",
      trigger: `${CHAT} ${FALLBACK_LINK}`,
    },
    {
      content: "Fallback link opens the same form",
      trigger: `.o_form_view .o_breadcrumb .active:contains(${PARTNER_NAME})`,
      async run() {
        assertNoReload();
        assertBreadcrumbs(["Chat", PARTNER_NAME]);
        const [thread] = await services().orm.searchRead(
          "llm.thread",
          [["name", "=", THREAD_NAME]],
          ["id"]
        );
        await services()["llm.popup_hub"].openThread(thread.id);
      },
    },
    // Popup over a form: unsaved-change checks still apply.
    {
      content: "Popup shows the inline link",
      trigger: `${POPUP} ${INLINE_LINK}`,
      isCheck: true,
    },
    {
      content: "Make the form invalid (required name)",
      trigger: ".o_form_view .o_field_widget[name=name] input",
      run() {
        const input = document.querySelector(
          ".o_form_view .o_field_widget[name=name] input"
        );
        input.value = "";
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.dispatchEvent(new Event("change", { bubbles: true }));
      },
    },
    {
      content: "Click the popup link while the form is invalid",
      trigger: `${POPUP} ${INLINE_LINK}`,
    },
    {
      content: "Navigation is blocked by the invalid form",
      trigger: ".o_form_view .o_field_invalid[name=name]",
      run() {
        // Still one form above the chat; its title follows the emptied name.
        assertBreadcrumbs(["Chat", ""]);
      },
    },
    {
      content: "Fix the form with an unsaved change",
      trigger: ".o_form_view .o_field_widget[name=name] input",
      run: `text ${PARTNER_NAME} Saved`,
    },
    {
      content: "Click the popup link with a valid dirty form",
      trigger: `${POPUP} ${INLINE_LINK}`,
    },
    {
      content: "Form was saved and the record opens on top; popup stays open",
      trigger: `.o_breadcrumb .active:contains(${PARTNER_NAME} Saved)`,
      extra_trigger: POPUP,
      run() {
        assertNoReload();
        assertBreadcrumbs([
          "Chat",
          `${PARTNER_NAME} Saved`,
          `${PARTNER_NAME} Saved`,
        ]);
      },
    },
  ],
});

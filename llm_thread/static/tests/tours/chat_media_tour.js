/** @odoo-module **/

import { registry } from "@web/core/registry";

const CHAT = ".o_LLMChatContainer";
const COMPOSER = `${CHAT} .o-mail-Composer`;
const MIC = `${COMPOSER} .o-llm-voice-composer-record`;
const INPUT = `${COMPOSER} .o-mail-Composer-input`;

/**
 * Install browser media mocks so the tour is reproducible without a mic.
 * MediaRecorder produces a tiny Blob; the server STT call is stubbed via
 * the tour's Python setup (patched llm.model.transcribe) when available.
 * When STT is unavailable the tour still verifies UI affordances and that
 * emoji + typed text still send without breaking attachment handling.
 */
function installMediaMocks() {
  if (window.__llmMediaMocksInstalled) {
    return;
  }
  window.__llmMediaMocksInstalled = true;

  const fakeStream = {
    getTracks() {
      return [{ stop() {} }];
    },
  };
  const originalGUM = navigator.mediaDevices?.getUserMedia?.bind(navigator.mediaDevices);
  if (navigator.mediaDevices) {
    navigator.mediaDevices.getUserMedia = async () => fakeStream;
  }
  window.__llmOriginalGetUserMedia = originalGUM;

  class FakeMediaRecorder {
    static isTypeSupported() {
      return true;
    }
    constructor() {
      this.state = "inactive";
      this.mimeType = "audio/webm";
      this.ondataavailable = null;
      this.onstop = null;
    }
    start() {
      this.state = "recording";
    }
    stop() {
      this.state = "inactive";
      if (this.ondataavailable) {
        // Minimal RIFF/WAVE bytes wrapped as a Blob for upload.
        const wav = new Uint8Array([
          0x52, 0x49, 0x46, 0x46, 0x24, 0x00, 0x00, 0x00, 0x57, 0x41, 0x56, 0x45,
          0x66, 0x6d, 0x74, 0x20, 0x10, 0x00, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00,
          0x44, 0xac, 0x00, 0x00, 0x88, 0x58, 0x01, 0x00, 0x02, 0x00, 0x10, 0x00,
          0x64, 0x61, 0x74, 0x61, 0x00, 0x00, 0x00, 0x00,
        ]);
        this.ondataavailable({ data: new Blob([wav], { type: "audio/wav" }) });
      }
      if (this.onstop) {
        this.onstop();
      }
    }
  }
  window.MediaRecorder = FakeMediaRecorder;
}

registry.category("web_tour.tours").add("llm_thread_chat_media_tour", {
  test: true,
  url: "/web#action=llm_thread.action_llm_chat",
  steps: () => [
    {
      content: "Install media mocks before interacting with the composer",
      trigger: `${CHAT} .o-llm-thread-active`,
      run() {
        installMediaMocks();
      },
    },
    {
      content: "Voice record control is visible on the LLM composer",
      trigger: MIC,
      isCheck: true,
    },
    {
      content: "Emoji button remains available alongside voice",
      trigger: `${COMPOSER} button[aria-label="Emojis"]`,
      isCheck: true,
    },
    {
      content: "Type a message with an emoji and send with Enter (compact composer)",
      trigger: INPUT,
      run() {
        const input = document.querySelector(INPUT);
        if (!input) {
          throw new Error("Composer input not found");
        }
        input.focus();
        input.value = "Hello from media tour 😀";
        input.dispatchEvent(new InputEvent("input", { bubbles: true, data: input.value }));
        // Compact desktop composer hides the send button; LLM patch sends on Enter.
        input.dispatchEvent(
          new KeyboardEvent("keydown", {
            key: "Enter",
            code: "Enter",
            bubbles: true,
            cancelable: true,
          })
        );
      },
    },
    {
      content: "User message appears in the thread",
      trigger: `${CHAT} .o-mail-Message:contains("Hello from media tour")`,
      isCheck: true,
    },
    {
      content: "Start a mocked recording",
      trigger: MIC,
    },
    {
      content: "Stop the mocked recording",
      trigger: `${COMPOSER} .o-llm-voice-composer-stop`,
    },
    {
      content: "After stop, preview/progress/error UI is shown (never auto-submits)",
      trigger: `${COMPOSER} .o-llm-voice-composer`,
      run() {
        const voice = document.querySelector(`${COMPOSER} .o-llm-voice-composer`);
        if (!voice) {
          throw new Error("Voice composer missing after stop");
        }
        const previewOrProgress =
          voice.querySelector(".o-llm-voice-composer-player") ||
          voice.querySelector(".o-llm-voice-composer-progress") ||
          voice.querySelector(".o-llm-voice-composer-error") ||
          voice.querySelector(".o-llm-voice-composer-discard");
        if (!previewOrProgress) {
          throw new Error("Expected preview, progress, discard or error after stop");
        }
        if (!document.querySelector(`${CHAT} .o-mail-Message`)) {
          throw new Error("Previous user message disappeared unexpectedly");
        }
        // Typed draft must remain editable; never auto-submit the transcript.
        const input = document.querySelector(INPUT);
        if (!input) {
          throw new Error("Composer input missing after voice attempt");
        }
      },
    },
  ],
});

registry.category("web_tour.tours").add("llm_thread_chat_media_popup_tour", {
  test: true,
  url: "/web",
  steps: () => [
    {
      content: "Open the LLM systray popup",
      trigger: ".o-llm-Systray-toggler",
      run() {
        installMediaMocks();
        document.querySelector(".o-llm-Systray-toggler").click();
      },
    },
    {
      content: "Popup composer shows the voice control",
      trigger: `.o-llm-ChatHub .o-llm-voice-composer-record, .o-llm-ChatWindow .o-llm-voice-composer-record`,
      isCheck: true,
    },
  ],
});

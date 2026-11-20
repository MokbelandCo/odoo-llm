/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { Component, onWillUnmount, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

/**
 * Record → upload → transcribe → editable transcript flow for LLM chat.
 * Live duplex voice is MH-1330 and is intentionally not implemented here.
 */
export class LLMVoiceComposer extends Component {
  static template = "llm_thread.LLMVoiceComposer";
  static props = {
    threadId: { type: Number },
    composer: { type: Object },
    mediaCapabilities: { type: Object, optional: true },
  };

  setup() {
    this.notification = useService("notification");
    this.orm = useService("orm");
    this.llmStore = useService("llm.store");
    this.state = useState({
      phase: "idle", // idle | recording | uploading | transcribing | preview | error
      error: null,
      objectUrl: null,
      durationMs: 0,
      canAnalyze: false,
    });
    this._mediaRecorder = null;
    this._mediaStream = null;
    this._chunks = [];
    this._startedAt = 0;
    this._tickTimer = null;
    onWillUnmount(() => this._cleanupCapture({ revokeUrl: true }));
  }

  get isBusy() {
    return ["recording", "uploading", "transcribing"].includes(this.state.phase);
  }

  get showAnalyze() {
    return Boolean(this.props.mediaCapabilities?.direct_audio_chat);
  }

  async startRecording() {
    if (this.isBusy) {
      return;
    }
    this.state.error = null;
    this._cleanupCapture({ revokeUrl: true });
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      this.state.phase = "error";
      this.state.error = _t(
        "This browser does not support microphone recording. Type your message or use a supported browser."
      );
      return;
    }
    try {
      this._mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (error) {
      this.state.phase = "error";
      if (error?.name === "NotAllowedError" || error?.name === "PermissionDeniedError") {
        this.state.error = _t(
          "Microphone permission was denied. Allow the microphone, then try again. Your typed draft was kept."
        );
      } else if (error?.name === "NotFoundError") {
        this.state.error = _t("No microphone was found on this device.");
      } else {
        this.state.error =
          _t("Could not open the microphone.") +
          (error?.message ? ` ${error.message}` : "");
      }
      return;
    }

    const mimeType = this._preferredMimeType();
    try {
      this._mediaRecorder = mimeType
        ? new MediaRecorder(this._mediaStream, { mimeType })
        : new MediaRecorder(this._mediaStream);
    } catch (error) {
      this._cleanupCapture({ revokeUrl: true });
      this.state.phase = "error";
      this.state.error =
        _t("Could not start the recorder.") +
        (error?.message ? ` ${error.message}` : "");
      return;
    }

    this._chunks = [];
    this._mediaRecorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) {
        this._chunks.push(event.data);
      }
    };
    this._mediaRecorder.onstop = () => this._onRecorderStop();
    this._startedAt = Date.now();
    this.state.durationMs = 0;
    this.state.phase = "recording";
    this._tickTimer = setInterval(() => {
      this.state.durationMs = Date.now() - this._startedAt;
    }, 200);
    this._mediaRecorder.start(250);
  }

  stopRecording() {
    if (this.state.phase !== "recording" || !this._mediaRecorder) {
      return;
    }
    if (this._tickTimer) {
      clearInterval(this._tickTimer);
      this._tickTimer = null;
    }
    this.state.durationMs = Date.now() - this._startedAt;
    this._mediaRecorder.stop();
  }

  discardRecording() {
    this._cleanupCapture({ revokeUrl: true });
    this.state.phase = "idle";
    this.state.error = null;
    this.state.durationMs = 0;
  }

  async _onRecorderStop() {
    const mimeType = this._mediaRecorder?.mimeType || this._preferredMimeType() || "audio/webm";
    this._stopTracks();
    const blob = new Blob(this._chunks, { type: mimeType });
    this._chunks = [];
    this._mediaRecorder = null;
    if (!blob.size) {
      this.state.phase = "error";
      this.state.error = _t("The recording was empty. Hold the record button a moment longer.");
      return;
    }
    this.state.objectUrl = URL.createObjectURL(blob);
    this.state.phase = "uploading";
    try {
      const attachmentId = await this._uploadBlob(blob, mimeType);
      this.state.phase = "transcribing";
      const result = await this.llmStore.transcribeVoiceNote(this.props.threadId, attachmentId);
      const transcript = (result?.text || "").trim();
      if (!transcript) {
        this.state.phase = "error";
        this.state.error = _t(
          "Speech-to-text returned an empty transcript. Edit the draft or discard and record again."
        );
        return;
      }
      this._appendTranscript(transcript);
      this.state.phase = "preview";
      this.notification.add(_t("Transcript ready — review and edit before sending."), {
        type: "info",
      });
    } catch (error) {
      this.state.phase = "error";
      this.state.error =
        error?.data?.message ||
        error?.message ||
        _t("Transcription failed. Your typed draft was kept.");
    }
  }

  async analyzeInstead() {
    if (!this.showAnalyze || !this.state.objectUrl) {
      return;
    }
    // Re-fetch is not needed: user already has a dictation-marked attachment
    // from the last upload in the composer attachments list. Prefer marking
    // the latest audio attachment on the composer.
    const attachments = this.props.composer.attachments || [];
    const audio = [...attachments].reverse().find((att) =>
      (att.mimetype || "").startsWith("audio/")
    );
    if (!audio) {
      this.notification.add(_t("Record audio first, then choose Analyze audio."), {
        type: "warning",
      });
      return;
    }
    try {
      await this.llmStore.markAnalyzeAudio(this.props.threadId, audio.id);
      this.notification.add(
        _t("Audio marked for analysis. Add optional text, then send."),
        { type: "info" }
      );
      this.state.phase = "idle";
    } catch (error) {
      this.notification.add(
        error?.data?.message || error?.message || _t("Could not enable audio analysis."),
        { type: "danger" }
      );
    }
  }

  _appendTranscript(transcript) {
    const composer = this.props.composer;
    const current = composer.textInputContent || "";
    const separator = current && !current.endsWith("\n") && !current.endsWith(" ") ? " " : "";
    // Never silently replace typed text and never auto-submit.
    composer.textInputContent = `${current}${separator}${transcript}`;
  }

  async _uploadBlob(blob, mimeType) {
    const buffer = await blob.arrayBuffer();
    const bytes = new Uint8Array(buffer);
    let binary = "";
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
    }
    const datas = btoa(binary);
    const extension = (mimeType.split("/")[1] || "webm").split(";")[0];
    const name = `voice-note-${Date.now()}.${extension}`;
    const attachmentId = await this.orm.call("ir.attachment", "create", [
      {
        name,
        datas,
        mimetype: mimeType.split(";")[0],
        res_model: "llm.thread",
        res_id: this.props.threadId,
        description: "llm_media_role=dictation",
      },
    ]);
    const id = Array.isArray(attachmentId) ? attachmentId[0] : attachmentId;
    // Surface in the stock composer attachment list for preview/send.
    if (this.props.composer.attachments) {
      this.props.composer.attachments.push({
        id,
        name,
        mimetype: mimeType.split(";")[0],
        size: blob.size,
      });
    }
    return id;
  }

  _preferredMimeType() {
    const candidates = [
      "audio/webm;codecs=opus",
      "audio/webm",
      "audio/ogg;codecs=opus",
      "audio/mp4",
    ];
    for (const type of candidates) {
      if (window.MediaRecorder?.isTypeSupported?.(type)) {
        return type;
      }
    }
    return "";
  }

  _stopTracks() {
    if (this._mediaStream) {
      for (const track of this._mediaStream.getTracks()) {
        track.stop();
      }
      this._mediaStream = null;
    }
  }

  _cleanupCapture({ revokeUrl = false } = {}) {
    if (this._tickTimer) {
      clearInterval(this._tickTimer);
      this._tickTimer = null;
    }
    if (this._mediaRecorder && this._mediaRecorder.state !== "inactive") {
      try {
        this._mediaRecorder.ondataavailable = null;
        this._mediaRecorder.onstop = null;
        this._mediaRecorder.stop();
      } catch (_error) {
        // Recorder may already be stopped.
      }
    }
    this._mediaRecorder = null;
    this._chunks = [];
    this._stopTracks();
    if (revokeUrl && this.state.objectUrl) {
      URL.revokeObjectURL(this.state.objectUrl);
      this.state.objectUrl = null;
    }
  }

  formatDuration(ms) {
    const total = Math.max(0, Math.floor((ms || 0) / 1000));
    const minutes = String(Math.floor(total / 60)).padStart(2, "0");
    const seconds = String(total % 60).padStart(2, "0");
    return `${minutes}:${seconds}`;
  }
}

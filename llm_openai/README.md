# OpenAI Provider for Odoo LLM Integration

This module integrates OpenAI's API with the Odoo LLM framework, providing access to GPT models, embeddings, and other OpenAI services.

**Module Type:** 🔧 Provider

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Used By (Any LLM Module)                     │
│  ┌─────────────┐  ┌───────────┐  ┌─────────────┐  ┌───────────┐ │
│  │llm_assistant│  │llm_thread │  │llm_knowledge│  │llm_generate│ │
│  └──────┬──────┘  └─────┬─────┘  └──────┬──────┘  └─────┬─────┘ │
└─────────┼───────────────┼───────────────┼───────────────┼───────┘
          │               │               │               │
          └───────────────┴───────┬───────┴───────────────┘
                                  ▼
          ┌───────────────────────────────────────────────┐
          │          ★ llm_openai (This Module) ★         │
          │              OpenAI Provider                  │
          │  GPT-4o │ GPT-4 │ GPT-3.5 │ DALL-E │ Embeddings │
          └─────────────────────┬─────────────────────────┘
                                │
                                ▼
          ┌───────────────────────────────────────────────┐
          │                    llm                        │
          │              (Core Base Module)               │
          └───────────────────────────────────────────────┘
```

## Installation

### What to Install

**For AI chat with OpenAI:**

```bash
odoo-bin -d your_db -i llm_assistant,llm_openai
```

### Auto-Installed Dependencies

- `llm` (core infrastructure)

### Alternative Providers

| Instead of OpenAI | Use        | Best For              |
| ----------------- | ---------- | --------------------- |
| `llm_ollama`      | Local AI   | Privacy, no API costs |
| `llm_mistral`     | Mistral AI | European, fast        |
| `llm_replicate`   | Replicate  | Image generation      |

### Common Setups

| I want to...          | Install                                  |
| --------------------- | ---------------------------------------- |
| Chat with GPT-4       | `llm_assistant` + `llm_openai`           |
| GPT + document search | Above + `llm_knowledge` + `llm_pgvector` |
| GPT + external tools  | Above + `llm_mcp_server`                 |

## Features

- Connect to OpenAI API with proper authentication
- Support for all OpenAI models (GPT-4o, GPT-4, GPT-3.5, Whisper, etc.)
- Text embeddings support
- Speech-to-text via the OpenAI audio transcription API
- Function calling capabilities
- Multimodal (vision) capabilities for GPT-4o and GPT-4 Vision
- Automatic model discovery

## Multimodal Support (Vision)

GPT-4o and GPT-4 Vision models support image and PDF analysis.

### How to Use

Attach files to your chat message - they're automatically processed:

| Type   | OpenAI Format                     |
| ------ | --------------------------------- |
| Images | `type: "image_url"` with data URL |
| PDFs   | `type: "file"` with file_data     |
| Text   | Appended to message content       |

### Supported Image Formats

- JPEG, PNG, GIF, WebP

### Example

```python
# Analyze an image
thread.message_post(
    body="Describe this image",
    attachment_ids=[(4, image_id)]
)
```

**Note:** Requires a multimodal-capable model (GPT-4o, GPT-4 Vision).

## Configuration

1. Install the module
2. Navigate to **LLM > Configuration > Providers**
3. Create a new provider and select "OpenAI" as the provider type
4. Enter your OpenAI API key
5. Click "Fetch Models" to import available models

## OpenAI-Compatible Endpoints

Many AI providers offer OpenAI-compatible endpoints, allowing you to use the same code and configuration with different services. This module supports connecting to any of these compatible endpoints by changing the base URL in the provider configuration.

### Supported OpenAI-Compatible Endpoints

| Provider   | Base URL                                                 | Models                             | Notes                               |
| ---------- | -------------------------------------------------------- | ---------------------------------- | ----------------------------------- |
| OpenAI     | https://api.openai.com/v1/                               | GPT-4o, GPT-4, GPT-3.5 Turbo, etc. | Official OpenAI API                 |
| Anthropic  | https://api.anthropic.com/v1/                            | Claude 3 Opus, Sonnet, Haiku       | Requires Anthropic API key          |
| DeepSeek   | https://api.deepseek.com/v1/                             | DeepSeek-Coder, DeepSeek-Chat      | Requires DeepSeek API key           |
| Ollama     | http://localhost:11434/v1/                               | Llama, Mistral, Vicuna, etc.       | Local deployment, no API key needed |
| Google AI  | https://generativelanguage.googleapis.com/v1beta/openai/ | Gemini models                      | Requires Google API key             |
| Mistral AI | https://api.mistral.ai/v1/                               | Mistral-7B, Mixtral-8x7B, etc.     | Requires Mistral AI API key         |

### Using OpenAI-Compatible Endpoints

To use an alternative provider with an OpenAI-compatible endpoint:

1. Create a new provider in **LLM > Configuration > Providers**
2. Select "OpenAI" as the provider type
3. Enter your API key for the specific provider
4. Set the "Base URL" field to the appropriate endpoint URL from the table above
5. Click "Fetch Models" to discover available models

On a compatible host, chat keeps using Chat Completions with no per-model
filtering. The capability routing below applies only to the OpenAI API
(empty Base URL, or a host under `api.openai.com`).

## Chat Model Capabilities and Endpoint Routing

Each OpenAI model id is looked up in an explicit capability registry
(`utils/openai_capabilities.py`). Snapshot (`-2025-08-07`) and fine-tune
(`ft:`) ids use their base model's entry. An entry records:

- the model kind: chat, embedding, transcription, speech, image, realtime,
  audio chat, moderation, search or completion
- the endpoints that serve it (`/v1/responses`, `/v1/chat/completions`)
- function tools on each endpoint. Some GPT-5.4+ and GPT-6 models accept
  tools on Chat Completions only with reasoning effort `none`.
- vision, streaming, the accepted reasoning efforts per endpoint, whether
  `temperature` / `top_p` / `logprobs` are accepted, and `verbosity`

**Discovery.** "Fetch Models" skips speech, image, realtime, audio-chat,
moderation and search models. A model that is not in the registry is
imported as **Unverified (probe required)**. Neither kind can be picked for
a chat thread. Run **Probe OpenAI Capabilities** on the model form to send a
few minimal requests and record which endpoints, tools and image input the
model accepts. A model that passes becomes Chat/Multimodal. Upgrading to
17.0.1.12.0 moves non-chat OpenAI models that were imported as Chat to
**Not usable in Odoo**. Nothing is archived or deleted.

**Routing** (the *OpenAI Endpoint* field on the model, default *Automatic*):

- Non-reasoning models that support Chat Completions (GPT-4 class) stay on
  Chat Completions, as before.
- Reasoning models (o-series, GPT-5, GPT-6) and Responses-only models
  (`*-pro`, `gpt-5.3-codex`) use the Responses API.
- When tools are present, an endpoint that cannot take them is skipped.
  Tools are never dropped. If no endpoint can take them, or if a forced
  endpoint cannot, the chat fails with the model, endpoint and parameter.

**Parameters.** *Reasoning Effort*, *Max Output Tokens* and *Verbosity* on
the model form map to each endpoint's own names: `reasoning.effort` or
`reasoning_effort`, `max_output_tokens` or `max_completion_tokens`, and
`text.verbosity` or `verbosity`. A value the model rejects raises an error
that lists the accepted values. Parameters the model does not take at all
(for example `temperature` on reasoning models) are left out and named in
the server log.

**Conversation state.** Odoo replays the history manually and keeps no state
on OpenAI's side. Each request sends `store: false` and rebuilds the input
from the thread, which stays the single source of truth for edits, deletions
and retention. `previous_response_id` is not used. For reasoning models the
encrypted reasoning items (`include: ["reasoning.encrypted_content"]`) are
kept server-side on the assistant message (`mail.message.llm_provider_state`,
system group only, never sent to the browser). They are replayed in their
original order before the text and function calls they produced, with each
`call_id` paired to its `function_call_output`. Reasoning is replayed only to
the same model, and only while the assistant text is unchanged; an edited
answer drops it.

**Responses and streams.** Text, refusals, multiple function calls, usage and
finish status are normalized to the same chunks Chat Completions produces.
Tool calls are released only after the stream completes. An interrupted,
failed or truncated (`max_output_tokens`) response raises an error and runs
no tool. Errors name the model, endpoint, parameter and provider code. The
API key, prompts and response bodies never appear in these errors or in the
warning log.

## Technical Details

This module extends the base LLM integration framework with OpenAI-specific implementations:

- Implements the OpenAI API client with proper authentication and error handling
- Provides model mapping and conversion between OpenAI formats and Odoo LLM formats
- Handles rate limiting and retries according to OpenAI best practices

## Dependencies

- llm (LLM Integration Base)

## License

LGPL-3

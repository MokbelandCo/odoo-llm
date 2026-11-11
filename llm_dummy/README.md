# LLM Dummy Provider

Local/dev provider that registers the `dummy` service and a default `dummy-chat`
model. Chat replies are canned so the AI popup and thread UI can be tested
without an external API.

Keep the service name `dummy` and XML IDs (`llm_provider_dummy`,
`llm_model_dummy_chat`) identical on 17.0 and 19.0.

The `llm_dummy_find_contact` tool (`llm.dummy.tools`) returns a record
reference. Add it to a thread and send `record link for <name>` to get a reply
with a record link (`record link without link for <name>` replies without the
token, leaving only the tool card fallback link).

# LLM Dummy Provider

Local/dev provider that registers the `dummy` service and a default `dummy-chat`
model. Chat replies are canned so the AI popup and thread UI can be tested
without an external API.

Keep the service name `dummy` and XML IDs (`llm_provider_dummy`,
`llm_model_dummy_chat`) identical on 17.0 and 19.0.

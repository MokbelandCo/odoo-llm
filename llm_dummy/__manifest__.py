{
    "name": "LLM Dummy Provider",
    "summary": "In-process dummy LLM provider for local popup and chat UI testing",
    "description": """
        Registers the ``dummy`` provider service and a default chat model.
        Replies are canned and do not call an external LLM API.
        Keep the service name ``dummy`` identical on 17.0 and 19.0.
    """,
    "author": "mokbelhealth inc.",
    "website": "https://www.mokbelhealth.com",
    "category": "Technical",
    "version": "17.0.1.1.0",
    "depends": ["llm", "llm_thread", "llm_assistant"],
    "data": [
        "data/llm_dummy_data.xml",
    ],
    "license": "Other proprietary",
    "installable": True,
    "application": False,
}

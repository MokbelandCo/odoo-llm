{
    "name": "OpenAI LLM Integration",
    "summary": "OpenAI provider integration for LLM module",
    "description": """
        Implements OpenAI provider service for the LLM integration module.
        Supports GPT models for chat, embedding, and speech-to-text capabilities.
    """,
    "author": "Apexive Solutions LLC",
    "website": "https://github.com/apexive/odoo-llm",
    "category": "Technical",
    "version": "17.0.1.9.0",  # Last updated: commit 5b0cabe1 (2026-09-22)
    "depends": ["llm", "llm_tool", "llm_training"],
    "external_dependencies": {
        "python": ["openai"],
    },
    "data": [
        "data/llm_publisher.xml",
    ],
    "images": [
        "static/description/banner.jpeg",
    ],
    "license": "LGPL-3",
    "installable": True,
}

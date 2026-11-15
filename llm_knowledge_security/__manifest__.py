{
    "name": "LLM Knowledge Security",
    "summary": "Live source authorization and collection policy for LLM knowledge",
    "version": "19.0.1.0.0",
    "category": "Technical",
    "author": "Mokbel and Co.",
    "website": "https://github.com/MokbelandCo/odoo-llm",
    "license": "LGPL-3",
    "depends": ["llm_knowledge", "llm_tool"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron_data.xml",
        "views/llm_knowledge_collection_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
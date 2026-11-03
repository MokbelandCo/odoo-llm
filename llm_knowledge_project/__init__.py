from . import models


def post_init_hook(env):
    jobs = env["llm.knowledge.sync.job"].sudo()
    for project in env["project.project"].sudo().search([]):
        jobs.enqueue("project.project", project.id, project._llm_collection_key())
    for task in env["project.task"].sudo().search([]):
        jobs.enqueue("project.task", task.id, task._llm_collection_key())
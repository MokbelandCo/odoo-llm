import json
import logging

from odoo import _, api, http
from odoo.exceptions import MissingError
from odoo.http import Response, request
from odoo.modules.registry import Registry

from .. import record_links

_logger = logging.getLogger(__name__)


class LLMThreadController(http.Controller):
    @staticmethod
    def _parse_allowed_company_ids(raw):
        """Company ids from the browser company switcher, in switcher order.

        The first id is the main company for the call, matching a normal RPC
        context. An empty or missing value means the client did not send a
        selection.
        """
        if raw in (None, False, ""):
            return []
        parts = raw if isinstance(raw, (list, tuple)) else str(raw).split(",")
        company_ids = []
        for part in parts:
            text = str(part).strip()
            if not text.isdigit():
                continue
            company_id = int(text)
            if company_id not in company_ids:
                company_ids.append(company_id)
        return company_ids

    @classmethod
    def _env_with_browser_companies(cls, env, raw):
        """Return ``env`` with ``allowed_company_ids`` set, when the client sent them."""
        company_ids = cls._parse_allowed_company_ids(raw)
        if not company_ids:
            return env
        return env(context=dict(env.context, allowed_company_ids=company_ids))

    @http.route(
        "/llm/thread/<int:thread_id>/update",
        type="json",
        auth="user",
        methods=["POST"],
        csrf=True,
    )
    def llm_thread_update(self, thread_id, **kwargs):
        try:
            thread = request.env["llm.thread"].browse(thread_id)
            if not thread.exists():
                raise MissingError(_("LLM Thread not found."))
            thread.write(kwargs)
            return {"status": "success"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @staticmethod
    def _safe_yield(data_to_yield):
        """Helper generator to yield data safely, handling BrokenPipeError(Disconnected user)."""
        try:
            yield data_to_yield
            return True
        except BrokenPipeError:
            return False
        except Exception:
            return False

    @classmethod
    def _llm_thread_generate(cls, dbname, env, thread_id, user_message_body, **kwargs):
        """Generate LLM responses with streaming and safe yielding."""
        with Registry(dbname).cursor() as cr:
            env = api.Environment(
                cr, env.uid, {**env.context, record_links.CONTEXT_KEY: True}
            )
            llm_thread = env["llm.thread"].browse(int(thread_id))
            if not llm_thread.exists():
                yield from cls._safe_yield(
                    f"data: {json.dumps({'type': 'error', 'error': 'LLM Thread not found.'})}\n\n".encode(),
                )
                return

            client_connected = True
            try:
                for response in llm_thread.generate(user_message_body, **kwargs):
                    json_data = json.dumps(response, default=str)
                    success = yield from cls._safe_yield(
                        f"data: {json_data}\n\n".encode(),
                    )
                    if not success:
                        client_connected = False
                        break

            except GeneratorExit:
                client_connected = False

            except Exception as e:
                _logger.exception(
                    f"Error in llm_thread_generate for thread {thread_id}: {e}",
                )
                # Lock will be automatically released by context manager

                if client_connected:
                    success = yield from cls._safe_yield(
                        f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n".encode(),
                    )
                    if not success:
                        client_connected = False

            finally:
                if client_connected:
                    yield from cls._safe_yield(
                        f"data: {json.dumps({'type': 'done'})}\n\n".encode(),
                    )

    @http.route("/llm/thread/generate", type="http", auth="user", csrf=True)
    def llm_thread_generate(
        self,
        thread_id,
        message=None,
        attachment_ids=None,
        **kwargs,
    ):
        headers = {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        }
        parsed_attachment_ids = []
        if attachment_ids:
            parsed_attachment_ids = [
                int(x) for x in attachment_ids.split(",") if x.strip().isdigit()
            ]
        # The company switcher is not part of the session. The client sends the
        # same ordered list a normal RPC puts in context. Pop it so generate()
        # does not receive it as a method argument.
        raw_companies = kwargs.pop("allowed_company_ids", None)
        env = self._env_with_browser_companies(request.env, raw_companies)
        return Response(
            self._llm_thread_generate(
                request.cr.dbname,
                env,
                thread_id,
                message,
                attachment_ids=parsed_attachment_ids,
                **kwargs,
            ),
            direct_passthrough=True,
            headers=headers,
        )

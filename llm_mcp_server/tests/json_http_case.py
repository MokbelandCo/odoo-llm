# -*- coding: utf-8 -*-
import json as json_lib

from odoo.tests import HttpCase


class JsonHttpCase(HttpCase):
    """Odoo 17 HttpCase.url_open has no json= kwarg; Odoo 19 tests pass json=payload."""

    def url_open(
        self,
        url,
        data=None,
        files=None,
        timeout=12,
        headers=None,
        allow_redirects=True,
        head=False,
        json=None,
    ):
        if json is not None:
            data = json_lib.dumps(json) if not isinstance(json, str) else json
            headers = dict(headers or {})
            headers.setdefault("Content-Type", "application/json")
        return super().url_open(
            url,
            data=data,
            files=files,
            timeout=timeout,
            headers=headers,
            allow_redirects=allow_redirects,
            head=head,
        )

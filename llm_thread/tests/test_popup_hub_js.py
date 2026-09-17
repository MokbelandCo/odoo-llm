from odoo.tests.common import HttpCase, tagged


def _hoot_error_checker(message):
    return "[HOOT]" not in message


@tagged("post_install", "-at_install")
class TestPopupHubHoot(HttpCase):
    def test_popup_hub_persistence_suite(self):
        self.browser_js(
            "/web/tests?headless&loglevel=2&preset=desktop&timeout=15000&filter=PopupHub",
            "",
            "",
            login="admin",
            timeout=180,
            success_signal="[HOOT] Test suite succeeded",
            error_checker=_hoot_error_checker,
        )

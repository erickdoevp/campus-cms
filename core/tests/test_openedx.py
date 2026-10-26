from unittest import mock

import requests
from django.test import SimpleTestCase

from core import openedx


@mock.patch("core.openedx._access_token", return_value="token")
class RequestTests(SimpleTestCase):
    def test_network_errors_are_openedx_errors(self, _token):
        # A slow Studio must be "Open edX didn't answer", not a 500.
        with mock.patch("core.openedx.requests.request", side_effect=requests.ReadTimeout("slow")):
            with self.assertRaises(openedx.OpenEdxError) as raised:
                openedx.studio("PUT", "/api/contentstore/v1/course_details/x")
        assert raised.exception.status_code is None

    def test_writes_wait_longer_than_reads(self, _token):
        response = mock.Mock(status_code=200, content=b"{}", json=lambda: {})
        with mock.patch("core.openedx.requests.request", return_value=response) as request:
            openedx.studio("GET", "/x")
            openedx.studio("PUT", "/x")
        assert [call.kwargs["timeout"] for call in request.call_args_list] == [openedx.TIMEOUT, openedx.WRITE_TIMEOUT]

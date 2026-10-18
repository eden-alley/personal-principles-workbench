import os
from unittest.mock import patch
from django.test.runner import DiscoverRunner

class LocalTestRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        self._offline = patch.dict(os.environ, {"AI_PROVIDER": "demo", "AI_API_KEY": ""})
        self._offline.start()
        super().setup_test_environment(**kwargs)

    def teardown_test_environment(self, **kwargs):
        try:
            super().teardown_test_environment(**kwargs)
        finally:
            self._offline.stop()

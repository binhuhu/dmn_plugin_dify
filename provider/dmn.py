from dify_plugin import ToolProvider


class DmnProvider(ToolProvider):
    def _validate_credentials(self, credentials):
        return None

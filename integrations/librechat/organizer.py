"""Dedicated background Codex lane; reuses the read-only bridge protocol adapter."""
from .bridge import LibreChatBridge
from .provider_config import ProviderConfig, load_provider_config, request_api_completion


class OrganizerModel:
    def __init__(self, config: ProviderConfig | None = None):
        self.config = config or load_provider_config()
        # Keep the SDK bridge independent.  The ordinary API lane never
        # constructs it, so an API configuration cannot silently fall back to
        # a local Codex login.
        self.bridge = LibreChatBridge() if self.config.provider == 'codex' else None

    def __call__(self, prompt):
        if self.config.provider == 'api':
            response = request_api_completion(self.config, [{'role': 'user', 'content': prompt}])
            choices = response.get('choices')
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise ValueError('普通 API 返回缺少回答。')
            message = choices[0].get('message')
            if not isinstance(message, dict) or not isinstance(message.get('content'), str):
                raise ValueError('普通 API 返回缺少回答。')
            return message['content']

        if self.bridge is None:  # defensive invariant for future config changes
            raise RuntimeError('Codex 桥接器未初始化。')
        request = self.bridge.validate_request({'model': 'gpt-6.1-sol', 'reasoning_effort': 'medium',
            'timeout': 120, 'stream': False, 'messages': [{'role': 'user', 'content': prompt}]})
        session = self.bridge.open_request(request)
        try:
            response = self.bridge.run_nonstream(session)
            return response['choices'][0]['message']['content']
        finally:
            if not session.closed:
                session.release(False)

    def close(self):
        if self.bridge is not None:
            self.bridge.close()

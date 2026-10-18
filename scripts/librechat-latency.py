"""Synthetic real-Codex timing probe; no user chat data or service tokens."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from integrations.librechat.bridge import LibreChatBridge


class TimedBridge(LibreChatBridge):
    def _auth_status(self, **kwargs):
        start = time.monotonic()
        try:
            return super()._auth_status(**kwargs)
        finally:
            self.timings['auth_ms'] = round((time.monotonic() - start) * 1000)

    def _ensure_warm(self, deadline):
        start = time.monotonic()
        try:
            return super()._ensure_warm(deadline)
        finally:
            self.timings['runtime_ms'] = round((time.monotonic() - start) * 1000)


def main():
    bridge = TimedBridge(token='isolated-in-process-probe')
    results = []
    try:
        for label, effort in [('cold-medium', 'medium'), ('warm-medium', 'medium'), ('warm-high', 'high')]:
            bridge.timings = {}
            request = bridge.validate_request({'model': 'gpt-6.1-sol', 'reasoning_effort': effort,
                'messages': [{'role': 'user', 'content': '连通性测试，请只回复：连接成功。'}]})
            start = time.monotonic()
            session = bridge.open_request(request)
            ready = time.monotonic()
            first = None
            answer = ''
            success = False
            try:
                for kind, text in session.iter_text():
                    if kind == 'text' and text:
                        if first is None:
                            first = time.monotonic()
                        answer += text
                success = True
            finally:
                session.release(success)
            row = {'label': label, **bridge.timings, 'prepare_ms': round((ready-start)*1000),
                'first_ms': round((first-start)*1000) if first else None,
                'model_wait_ms': round((first-ready)*1000) if first else None,
                'total_ms': round((time.monotonic()-start)*1000), 'answer': answer}
            results.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        bridge.close()
        Path('artifacts/librechat-latency-medium.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__ == '__main__':
    main()

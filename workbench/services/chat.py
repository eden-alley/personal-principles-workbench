"""A direct send saves the user's words first; only complete AI replies are recorded."""
import threading
import uuid
from django.core.exceptions import ValidationError
from workbench.models import Case, Message, Operation, Suggestion
from workbench.ai.base import ProviderError
from .ai import build_context, generate_suggestion_stream
from .ai_settings import provider_config
from .common import Conflict, clean_text, run_once
from .records import create_case, add_message


_chat_lock = threading.Lock()


def needs_reply(case):
    last = case.entries.order_by('-pk').first()
    return last is None or last.role == 'user'


def send_chat(*, content, operation_key, action, case=None, revision=None, depth='daily'):
    if action not in {'send', 'save'}:
        raise ValidationError('请选择发送或仅保存。')
    content = clean_text({'content': content}, 'content')
    token = uuid.UUID(str(operation_key))
    input_key = uuid.uuid5(token, 'chat-input')
    reply_key = uuid.uuid5(token, 'chat-reply')
    if not _chat_lock.acquire(blocking=False):
        raise ProviderError('上一条消息还在处理，请稍等。', code='busy')
    try:
        initial_case = case
        expected = 1 if case is None else revision + 1 if content else revision
        def save_input():
            if initial_case is None:
                return create_case(content, operation_key=input_key, depth=depth)
            if content:
                return add_message(initial_case, content, expected_revision=revision, operation_key=input_key).case
            return initial_case
        case = run_once('chat-turn', token, {'case': case.pk if case else None, 'content': content, 'revision': revision, 'depth': depth}, Case, save_input)
        case.refresh_from_db()
        prior = Operation.objects.filter(key=reply_key, kind='ai').first()
        if prior:
            suggestion = Suggestion.objects.filter(pk=prior.object_id, case=case, task='chat').first()
            if not suggestion:
                raise Conflict('这条回复已被删除，请重新发送。')
            yield {'type': 'complete', 'case': case}
            return
        if case.revision != expected:
            raise Conflict('这个对话已在另一页面更新。请刷新后继续，已保存的原话仍在。')
        if not content and not needs_reply(case):
            raise ValidationError('请写下要继续聊的内容。')
        yield {'type': 'saved', 'case': case}
        if action == 'save':
            yield {'type': 'complete', 'case': case}
            return
        context = build_context('chat', case)
        events = generate_suggestion_stream('chat', case, context=context, operation_key=reply_key,
                                            expected_revision=expected, remote_allowed=True,
                                            config=provider_config())
        try:
            for event in events:
                if event['type'] == 'complete':
                    case.refresh_from_db()
                    yield {'type': 'complete', 'case': case}
                else:
                    yield event
        finally:
            events.close()
    finally:
        _chat_lock.release()

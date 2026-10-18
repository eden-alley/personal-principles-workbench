import hashlib
import json
import uuid
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from workbench.models import Operation


class Conflict(ValidationError):
    pass


class ConfirmationRequired(ValidationError):
    pass


def require_confirmation(confirmed):
    if confirmed is not True:
        raise ConfirmationRequired('请明确勾选确认后保存；AI 建议不会自动成为正式记录。')


def run_once(kind, operation_key, data, model, action):
    try:
        token = uuid.UUID(str(operation_key))
    except (ValueError, TypeError):
        raise ValidationError('操作编号无效，请刷新页面后重试。')
    fingerprint = hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    with transaction.atomic():
        prior = Operation.objects.filter(key=token).first()
        if prior:
            if prior.kind != kind or prior.fingerprint != fingerprint:
                raise Conflict('这次保存编号已使用且内容不同。请重新打开页面后保存。')
            obj = model.objects.filter(pk=prior.object_id).first()
            if obj is None:
                raise Conflict('这次操作对应的记录已经删除。')
            return obj
        obj = action()
        Operation.objects.create(key=token, kind=kind, fingerprint=fingerprint, object_id=obj.pk)
        return obj


def claim(obj, expected_revision, **updates):
    if expected_revision is None or not type(obj).objects.filter(pk=obj.pk, revision=expected_revision).update(revision=F('revision') + 1, **updates):
        raise Conflict('另一个页面已经保存了更新。你的输入仍保留在表单中，请复制后重新打开最新版本再合并。')
    obj.refresh_from_db()


def references(model, ids):
    if any(type(i) is not int for i in ids):
        raise ValidationError('引用编号格式无效。')
    ids = set(ids)
    found = list(model.objects.filter(pk__in=ids))
    if len(found) != len(ids):
        raise ValidationError('引用的内容已不存在，请重新选择参考内容。')
    return found


def clean_text(data, name, *, required=False, limit=20000):
    value = data.get(name, '')
    if not isinstance(value, str):
        raise ValidationError(f'{name} 必须是文字。')
    value = value.strip()
    if len(value) > limit or (required and not value):
        raise ValidationError(f'{name} 不能为空或超过 {limit} 字。')
    return value

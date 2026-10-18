from pathlib import Path
from io import BytesIO
from PIL import Image, UnidentifiedImageError
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.core.files.base import ContentFile
from workbench.models import Source
from .common import run_once, claim, clean_text


def save_source(data, *, operation_key, image=None, source=None, expected_revision=None):
    values = {f: clean_text(data, f, limit=200 if f == 'title' else 500 if f in ['tags', 'attribution'] else 20000) for f in ['title', 'text', 'url', 'transcript', 'attribution', 'tags']}
    # Keep the original verbatim, including blank lines and leading/trailing spaces.
    if len(data.get('text', '')) > 20000:
        raise ValidationError('单条原始文字不能超过 20000 字。')
    values['text'] = data.get('text', '')
    values['kind'] = data.get('kind', source.kind if source else 'text')
    values['verification'] = data.get('verification', source.verification if source else 'unverified')
    if values['kind'] not in dict(Source._meta.get_field('kind').choices) or values['verification'] not in dict(Source._meta.get_field('verification').choices):
        raise ValidationError('素材类型或来源状态无效。')
    if values['url']:
        URLValidator(schemes=['http', 'https'])(values['url'])
    image_bytes = None
    if image and not source:
        if image.size > 10 * 1024 * 1024:
            raise ValidationError('截图不能超过 10 MB。')
        image_bytes = image.read()
        try:
            with Image.open(BytesIO(image_bytes)) as im:
                fmt = im.format
                if fmt not in {'PNG', 'JPEG', 'WEBP'} or im.width * im.height > 40000000:
                    raise ValidationError('请上传 PNG、JPEG 或 WebP 图片，最多 4000 万像素。')
                im.verify()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            raise ValidationError('这张图片无法读取，请重新选择原图。')
        filename = 'original.' + {'PNG': 'png', 'JPEG': 'jpg', 'WEBP': 'webp'}[fmt]
    if not source and not (values['text'].strip() or values['url'] or image_bytes or values['transcript']):
        raise ValidationError('请至少输入文字、链接或上传截图。')
    stored = []
    def action():
        if source:
            # Original text, URL, and image never change through metadata correction.
            updates = {k: v for k, v in values.items() if k not in ['text', 'url']}
            claim(source, expected_revision, **updates)
            return source
        obj = Source(**values)
        if image_bytes:
            obj.image.save(filename, ContentFile(image_bytes), save=False)
            stored.append(obj.image)
        obj.full_clean()
        obj.save()
        return obj
    try:
        import hashlib
        return run_once('source', operation_key, {'data': values, 'id': source.pk if source else None, 'expected': expected_revision, 'image': hashlib.sha256(image_bytes).hexdigest() if image_bytes else None}, Source, action)
    except Exception:
        for file in stored:
            file.storage.delete(file.name)
        raise

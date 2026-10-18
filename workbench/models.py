import uuid
from django.db import models
from django.core.exceptions import ValidationError

KINDS = [('value', '价值取向'), ('belief', '经验判断'), ('strategy', '行动策略')]
STATUSES = [('candidate', '候选'), ('trial', '试用中'), ('adopted', '已采纳'), ('retired', '已停用')]


class Timestamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        abstract = True
        ordering = ['-created_at', '-pk']


def attachment_path(instance, filename):
    return f'{uuid.uuid4().hex}.{filename.rsplit(".", 1)[-1].lower()}'


class Source(Timestamped):
    title = models.CharField(max_length=200, blank=True)
    text = models.TextField(blank=True)
    url = models.URLField(max_length=2000, blank=True)
    image = models.FileField(upload_to=attachment_path, blank=True)
    transcript = models.TextField(blank=True)
    kind = models.CharField(max_length=20, default='text', choices=[('text', '文字'), ('link', '链接'), ('image', '截图'), ('experience', '自己的经历或观察')])
    attribution = models.CharField(max_length=300, blank=True)
    verification = models.CharField(max_length=20, default='unverified', choices=[('unverified', '未核实'), ('self_report', '本人自述'), ('verified', '用户已核实')])
    tags = models.CharField(max_length=500, blank=True)
    revision = models.PositiveIntegerField(default=1)
    is_demo = models.BooleanField(default=False)

    def __str__(self):
        return self.title or self.text[:40] or self.url or '截图素材'


class Principle(Timestamped):
    revision = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUSES, default='candidate')
    kind = models.CharField(max_length=20, choices=KINDS, default='strategy')
    focused = models.BooleanField(default=False)

    @property
    def current(self):
        return self.versions.order_by('-number').first()

    def __str__(self):
        return self.current.title if self.current else '新原则'


class ImmutableQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('历史版本不可覆盖，请创建新版本。')
    def bulk_update(self, *args, **kwargs):
        raise ValidationError('历史版本不可覆盖，请创建新版本。')


class PrincipleVersion(Timestamped):
    principle = models.ForeignKey(Principle, related_name='versions', on_delete=models.CASCADE)
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    statement = models.TextField()
    kind = models.CharField(max_length=20, choices=KINDS)
    status = models.CharField(max_length=20, choices=STATUSES)
    when_to_use = models.TextField(blank=True)
    conditions = models.TextField(blank=True)
    boundaries = models.TextField(blank=True)
    questions = models.TextField(blank=True)
    tags = models.CharField(max_length=500, blank=True)
    reason = models.TextField(blank=True)
    sources = models.ManyToManyField(Source, related_name='principle_versions', blank=True)
    is_demo = models.BooleanField(default=False)
    objects = ImmutableQuerySet.as_manager()

    class Meta:
        ordering = ['-number']
        constraints = [models.UniqueConstraint(fields=['principle', 'number'], name='unique_principle_version')]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('历史版本不可覆盖，请创建新版本。')
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.title} · v{self.number} · {self.get_status_display()}'


class Case(Timestamped):
    situation = models.TextField()
    depth = models.CharField(max_length=10, choices=[('daily', '日常模式'), ('deep', '深入模式')], default='daily')
    revision = models.PositiveIntegerField(default=1)
    versions = models.ManyToManyField(PrincipleVersion, related_name='cases', blank=True)
    experiences = models.ManyToManyField('Observation', related_name='cases', blank=True)


class Message(Timestamped):
    case = models.ForeignKey(Case, related_name='entries', on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=[('user', '用户自述'), ('assistant', 'AI 建议')])
    content = models.TextField()
    references = models.JSONField(default=dict)
    is_demo = models.BooleanField(default=False)
    class Meta:
        ordering = ['created_at', 'pk']


class Decision(Timestamped):
    case = models.ForeignKey(Case, related_name='decisions', null=True, blank=True, on_delete=models.SET_NULL)
    situation = models.TextField()
    choice = models.TextField()
    reason = models.TextField(blank=True)
    expectation = models.TextField(blank=True)
    review_date = models.DateField(blank=True, null=True)
    reviewed = models.BooleanField(default=False)
    versions = models.ManyToManyField(PrincipleVersion, related_name='decisions', blank=True)
    is_demo = models.BooleanField(default=False)
    confirmed_at = models.DateTimeField(auto_now_add=True)


class Observation(Timestamped):
    decision = models.ForeignKey(Decision, related_name='observations', null=True, blank=True, on_delete=models.SET_NULL)
    facts = models.TextField()
    interpretation = models.TextField(blank=True)
    uncertainty = models.TextField(blank=True)
    difference = models.TextField(blank=True)
    effect = models.CharField(max_length=20, choices=[('supports', '支持经历'), ('challenges', '不支持或有反例'), ('uncertain', '暂不判断')], default='uncertain')
    versions = models.ManyToManyField(PrincipleVersion, related_name='observations', blank=True)
    is_demo = models.BooleanField(default=False)

    def __str__(self):
        return f'{self.get_effect_display()} · {self.facts[:70]}'


class Suggestion(Timestamped):
    provider = models.CharField(max_length=20, blank=True)
    model = models.CharField(max_length=120, blank=True)
    task = models.CharField(max_length=20)
    source = models.ForeignKey(Source, null=True, on_delete=models.CASCADE)
    case = models.ForeignKey(Case, null=True, on_delete=models.CASCADE)
    observation = models.ForeignKey(Observation, null=True, on_delete=models.CASCADE)
    principle = models.ForeignKey(Principle, null=True, on_delete=models.CASCADE)
    base_revision = models.PositiveIntegerField(default=0)
    payload = models.JSONField(default=dict)
    context = models.JSONField(default=dict)
    is_demo = models.BooleanField(default=True)
    applied = models.BooleanField(default=False)
    discarded = models.BooleanField(default=False)
    result_type = models.CharField(max_length=30, blank=True)
    result_id = models.PositiveIntegerField(null=True)


class Operation(Timestamped):
    key = models.UUIDField(unique=True)
    kind = models.CharField(max_length=40)
    fingerprint = models.CharField(max_length=64)
    object_id = models.PositiveIntegerField()


class UserSettings(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    revision = models.PositiveIntegerField(default=1)
    retrieval_limit = models.PositiveSmallIntegerField(default=5)
    context_chars = models.PositiveIntegerField(default=12000)

    ai_provider = models.CharField(max_length=20, choices=[('codex', 'Codex（现有 ChatGPT 账号）'), ('api', '普通模型 API'), ('demo', '本地演示')], default='codex')
    codex_model = models.CharField(max_length=120, default='gpt-6.1-sol')
    codex_reasoning_effort = models.CharField(max_length=10, choices=[('low', '低'), ('medium', '中'), ('high', '高'), ('xhigh', '很高'), ('max', '最高'), ('ultra', '超高')], default='high')


class LibraryEntry(Timestamped):
    """Ownership and searchable projection; existing domain objects remain authoritative."""
    owner = models.CharField(max_length=80, db_index=True)
    kind = models.CharField(max_length=16, choices=[('source', '素材'), ('principle', '原则'), ('experience', '经历')])
    object_id = models.PositiveIntegerField()
    revision = models.PositiveIntegerField(default=1)
    removed = models.BooleanField(default=False)
    conversation_id = models.CharField(max_length=80, blank=True)
    message_id = models.CharField(max_length=80, blank=True)
    parse_state = models.CharField(max_length=16, default='ready')
    index_state = models.CharField(max_length=16, default='pending')
    searchable_text = models.TextField(blank=True)
    embedding = models.JSONField(default=list)
    index_chunks = models.JSONField(default=list)
    index_revision = models.PositiveIntegerField(default=0)
    index_model = models.CharField(max_length=120, blank=True)
    error_code = models.CharField(max_length=40, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['kind', 'object_id'], name='library_domain_identity')]
        indexes = [models.Index(fields=['owner', 'kind', 'removed'])]


class LibraryOrganization(Timestamped):
    """Derived material notes, kept separate from original text and personal facts."""
    entry = models.OneToOneField(LibraryEntry, related_name='organization_job', on_delete=models.CASCADE)
    state = models.CharField(max_length=16, default='pending', db_index=True)
    result = models.JSONField(default=dict)
    source_revision = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=40, blank=True)
    lease_token = models.CharField(max_length=36, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    model = models.CharField(max_length=120, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class LibraryAsset(Timestamped):
    entry = models.ForeignKey(LibraryEntry, related_name='assets', on_delete=models.CASCADE)
    original = models.FileField(upload_to=attachment_path)
    name = models.CharField(max_length=200)
    mime = models.CharField(max_length=80)
    sha256 = models.CharField(max_length=64)
    size = models.PositiveIntegerField()


class LibraryEvidence(Timestamped):
    version = models.ForeignKey(PrincipleVersion, related_name='library_evidence', on_delete=models.CASCADE)
    entry = models.ForeignKey(LibraryEntry, on_delete=models.PROTECT)
    entry_revision = models.PositiveIntegerField()
    excerpt = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['version', 'entry'], name='library_version_evidence')]


class LibraryDraft(Timestamped):
    owner = models.CharField(max_length=80, db_index=True)
    conversation_id = models.CharField(max_length=80)
    message_id = models.CharField(max_length=80)
    suggestion = models.OneToOneField(Suggestion, on_delete=models.CASCADE)
    automatic = models.BooleanField(default=True)
    entry = models.ForeignKey(LibraryEntry, null=True, on_delete=models.SET_NULL)


class LibraryConversation(Timestamped):
    owner = models.CharField(max_length=80)
    conversation_id = models.CharField(max_length=80)
    excluded_ids = models.JSONField(default=list)
    selected_ids = models.JSONField(default=list)
    proposals_enabled = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['owner', 'conversation_id'], name='library_conversation_owner')]


class LibraryTurn(Timestamped):
    owner = models.CharField(max_length=80)
    conversation_id = models.CharField(max_length=80)
    message_id = models.CharField(max_length=80)
    references = models.JSONField(default=list)
    source_ids = models.JSONField(default=list)
    notice = models.CharField(max_length=300, blank=True)
    retrieval_ms = models.PositiveIntegerField(default=0)
    completed = models.BooleanField(default=False)
    answer_sha256 = models.CharField(max_length=64, blank=True)
    answer_text = models.TextField(blank=True)
    request_sha256 = models.CharField(max_length=64, blank=True)
    processing_until = models.DateTimeField(null=True, blank=True)
    lease_token = models.CharField(max_length=36, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['owner', 'conversation_id', 'message_id'], name='library_turn_identity')]

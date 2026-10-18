import uuid
from django import forms
from workbench.models import Source, Principle, PrincipleVersion, Observation, UserSettings, KINDS, STATUSES


def area(label, required=False, rows=3, **kwargs):
    return forms.CharField(label=label, required=required, max_length=20000, widget=forms.Textarea(attrs={'rows': rows}), **kwargs)


class OperationForm(forms.Form):
    extra_field_names = ()
    extra_label = '更多选项'
    operation_key = forms.UUIDField(widget=forms.HiddenInput)
    revision = forms.IntegerField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial.setdefault('operation_key', uuid.uuid4())

    @property
    def main_fields(self):
        return [f for f in self.visible_fields() if f.name not in self.extra_field_names]

    @property
    def extra_fields(self):
        return [f for f in self.visible_fields() if f.name in self.extra_field_names]

    @property
    def extra_has_errors(self):
        return any(f.errors for f in self.extra_fields)


class SourceForm(OperationForm):
    extra_field_names = ('title', 'kind', 'transcript', 'attribution', 'verification', 'tags')
    extra_label = '补充标题、标签或来源信息'
    text = area('原始文字', rows=7, strip=False, help_text='原文先保存。标题、人物和标签都可以以后补充。')
    url = forms.URLField(label='来源链接', required=False, max_length=2000, help_text='只保存链接，不自动获取正文。')
    image = forms.FileField(label='原始截图', required=False, widget=forms.ClearableFileInput(attrs={'accept': 'image/png,image/jpeg,image/webp'}), help_text='PNG、JPEG 或 WebP，最多 10 MB；首版不自动识别文字。')
    title = forms.CharField(label='标题（可选）', required=False, max_length=200)
    kind = forms.ChoiceField(label='素材类型', choices=Source._meta.get_field('kind').choices)
    transcript = area('补充或校对文字', help_text='与原始文字和图片分开保存；截图可手动补充文字。')
    attribution = forms.CharField(label='人物或来源说明', required=False, max_length=300)
    verification = forms.ChoiceField(label='来源核实状态', choices=Source._meta.get_field('verification').choices)
    tags = forms.CharField(label='标签', required=False, max_length=500, help_text='多个标签用逗号分隔。')

    def __init__(self, *args, source=None, **kwargs):
        super().__init__(*args, **kwargs)
        if source:
            self.extra_field_names = ('title', 'kind', 'attribution', 'verification', 'tags')
            for field in ['text', 'url', 'image']:
                self.fields[field].disabled = True
            self.fields['text'].help_text = '保留原文。补充、纠错请写入下面的校对文字。'


class PrincipleForm(OperationForm):
    title = forms.CharField(label='原则标题', max_length=200)
    statement = area('当前表述', required=True, rows=5)
    kind = forms.ChoiceField(label='内容类型', choices=KINDS, help_text='价值取向是珍视什么；经验判断是什么可能有效；行动策略是准备怎样做。')
    status = forms.ChoiceField(label='保存后的状态', choices=STATUSES, initial='candidate', help_text='已采纳只表示目前愿意参考；已停用仍保留历史。')
    when_to_use = area('什么时候可以使用')
    conditions = area('适用条件')
    boundaries = area('例外与边界')
    questions = area('尚未解决的问题')
    tags = forms.CharField(label='主题 / 标签', required=False, max_length=500)
    reason = area('保存或修改的理由')
    source_ids = forms.ModelMultipleChoiceField(label='启发来源', queryset=Source.objects.all(), required=False, help_text='关联素材只表示启发来源，不自动成为支持证据。')
    confirmed = forms.BooleanField(label='我已检查内容与状态，确认保存这一版本', required=True)


class CaseForm(OperationForm):
    situation = area('我遇到的事情', required=True, rows=7, help_text='写下具体问题和你在意的事，不必一次交代所有背景。')
    depth = forms.ChoiceField(label='对话深度', choices=[('daily', '日常模式：简短整理与比较'), ('deep', '深入模式：检查前提、反例与冲突')])


class ReferenceForm(OperationForm):
    versions = forms.ModelMultipleChoiceField(label='本次参考的原则版本', queryset=PrincipleVersion.objects.select_related('principle').all(), required=False, help_text='Ctrl / Command 可多选。可以全部移除；候选与停用内容仅在你手动选中后参考。')
    experiences = forms.ModelMultipleChoiceField(label='本次参考的经历', queryset=Observation.objects.all(), required=False)
    depth = forms.ChoiceField(label='对话深度', choices=[('daily', '日常模式'), ('deep', '深入模式')])


class MessageForm(OperationForm):
    content = area('继续补充', required=True, rows=4)


class ChatForm(OperationForm):
    config_token = forms.CharField(required=False, widget=forms.HiddenInput)
    content = area('想聊什么？', rows=4)
    depth = forms.ChoiceField(choices=[('daily', '日常'), ('deep', '深入')], required=False, widget=forms.HiddenInput)
    action = forms.ChoiceField(choices=[('send', '发送'), ('save', '仅保存')], widget=forms.HiddenInput)

    def clean_depth(self):
        return self.cleaned_data.get('depth') or 'daily'


class DecisionForm(OperationForm):
    situation = area('当时的情境', required=True)
    choice = area('我选择了什么', required=True)
    reason = area('当时的主要考虑')
    expectation = area('我希望得到什么', help_text='可以是自在、关系质量、边界感、符合价值观或完成工作。')
    versions = forms.ModelMultipleChoiceField(label='参考的具体原则版本', queryset=PrincipleVersion.objects.all(), required=False, help_text='此处锁定历史版本，以后修订原则不会改变这次选择的依据。')
    review_date = forms.DateField(label='回看日期（可选）', required=False, widget=forms.DateInput(attrs={'type': 'date'}), help_text='仅在应用内显示已到期的回看事项，没有后台推送。')
    confirmed = forms.BooleanField(label='这是我确认的选择，保存为正式记录', required=True)


class ObservationForm(OperationForm):
    facts = area('实际发生了什么', required=True, rows=5)
    interpretation = area('我自己的解释', help_text='这是你的解释，和发生的事情分开保留。')
    uncertainty = area('仍不确定的内容')
    difference = area('与当时期待有什么不同')
    effect = forms.ChoiceField(label='这段经历与原则的关系', choices=Observation._meta.get_field('effect').choices, initial='uncertain')
    versions = forms.ModelMultipleChoiceField(label='关联的原则版本', queryset=PrincipleVersion.objects.all(), required=False)
    confirmed = forms.BooleanField(label='以上是我确认保存的经历与解释，不是 AI 假设的案例', required=True)


class AIPreviewForm(OperationForm):
    context_token = forms.CharField(widget=forms.HiddenInput)
    confirmed_send = forms.BooleanField(label='我已查看本次发送内容，同意通过所选接入方式生成建议', required=False)

    def __init__(self, *args, is_remote=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['confirmed_send'].required = is_remote
        if not is_remote:
            self.fields['confirmed_send'].widget = forms.HiddenInput()


class DeleteForm(forms.Form):
    signature = forms.CharField(widget=forms.HiddenInput)
    confirmed = forms.BooleanField(label='我已看清影响范围，确认永久删除（不可撤销）', required=True)


class SettingsForm(OperationForm):
    extra_field_names = ('codex_model', 'codex_reasoning_effort', 'retrieval_limit', 'context_chars')
    extra_label = '高级设置：模型、推理强度与参考数量'
    ai_provider = forms.ChoiceField(label='AI 接入方式', choices=UserSettings._meta.get_field('ai_provider').choices)
    codex_model = forms.RegexField(label='Codex 模型', regex=r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,119}$', initial='gpt-6.1-sol', help_text='使用当前账号可用的模型名称。默认使用 gpt-6.1-sol。')
    retrieval_limit = forms.IntegerField(label='单次最多参考的原则 / 经历数量', min_value=1, max_value=10, help_text='只限制一次 AI 请求的内容数量，不限制素材库规模。')
    context_chars = forms.IntegerField(label='普通 API / 演示的上下文字符上限', min_value=2000, max_value=50000, help_text='Codex 不使用这个字符上限；由 Codex 原生模型窗口与压缩设置决定容量。')
    codex_reasoning_effort = forms.ChoiceField(label='Codex 推理强度', choices=UserSettings._meta.get_field('codex_reasoning_effort').choices, initial='high')
    confirmed = forms.BooleanField(label='确认保存设置', required=True, initial=True, widget=forms.HiddenInput)


class SourceLinkForm(OperationForm):
    principle = forms.ModelChoiceField(label='关联已有原则', queryset=Principle.objects.all())
    revisions_token = forms.CharField(widget=forms.HiddenInput)
    confirmed = forms.BooleanField(label='确认把这条素材作为启发来源关联；这不会把它标记为支持证据', required=True)

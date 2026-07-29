from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms
from django.urls import reverse

from .models import (
    Comment,
    Discussion,
    InviteCode,
    Message,
    Project,
    ProjectMembership,
    Stage,
    Task,
    TelegramUser,
)
from .permissions import (
    get_accessible_projects,
    get_accessible_tasks,
    get_collaborators,
)

FORM_CONTROL_CLASS = 'w-full rounded-md border border-gray-300 px-3 py-2'
CUSTOM_SELECT_ATTRS = {
    'class': FORM_CONTROL_CLASS,
    'data-custom-select': 'true',
}


class DiscussionForm(forms.ModelForm):
    participants = forms.ModelMultipleChoiceField(
        queryset=TelegramUser.objects.all(),
        widget=forms.CheckboxSelectMultiple,
        required=True,
        label='Участники',
    )

    class Meta:
        model = Discussion
        fields = ['title', 'task', 'participants']
        widgets = {
            'title': forms.TextInput(attrs={'class': FORM_CONTROL_CLASS}),
            'task': forms.Select(attrs=CUSTOM_SELECT_ATTRS),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if self.user:
            self.fields['participants'].queryset = get_collaborators(
                self.user
            ).exclude(pk=self.user.pk)
            self.fields['task'].queryset = get_accessible_tasks(self.user)
            self.fields['task'].empty_label = 'Без привязки к задаче'

    def clean(self):
        cleaned_data = super().clean()
        task = cleaned_data.get('task')
        participants = cleaned_data.get('participants')
        if task and task.project_id and participants:
            allowed_ids = set(task.project.get_members().values_list('id', flat=True))
            invalid_participants = [
                participant
                for participant in participants
                if participant.id not in allowed_ids
            ]
            if invalid_participants:
                self.add_error(
                    'participants',
                    'В обсуждение задачи можно добавить только участников её проекта.',
                )
        return cleaned_data


class InviteCodeRedeemForm(forms.Form):
    code = forms.CharField(
        label='Код приглашения',
        required=False,
        max_length=64,
        strip=True,
        widget=forms.TextInput(
            attrs={
                'class': 'w-full rounded-md border border-gray-300 px-3 py-2',
                'autocomplete': 'off',
                'placeholder': 'Вставьте код из приглашения',
            }
        ),
    )


class MessageForm(forms.ModelForm):
    class Meta:
        model = Message
        fields = ['text']
        widgets = {
            'text': forms.Textarea(attrs={
                'class': FORM_CONTROL_CLASS,
                'rows': 2,
                'placeholder': 'Написать сообщение…',
                'aria-label': 'Текст сообщения',
            }),
        }
        labels = {
            'text': '',
        }


class TaskForm(forms.ModelForm):
    deadline = forms.DateTimeField(
        required=False,
        label='Дедлайн',
        widget=forms.DateTimeInput(
            attrs={'type': 'datetime-local', 'class': FORM_CONTROL_CLASS}
        ),
    )

    class Meta:
        model = Task
        fields = ['title', 'description', 'project', 'stage', 'assignee', 'deadline', 'status']
        labels = {
            'title': 'Название',
            'description': 'Описание',
            'project': 'Проект',
            'stage': 'Этап',
            'assignee': 'Ответственный',
            'status': 'Статус',
        }
        widgets = {
            'title': forms.TextInput(attrs={'class': FORM_CONTROL_CLASS}),
            'description': forms.Textarea(
                attrs={'class': FORM_CONTROL_CLASS, 'rows': 4}
            ),
            'project': forms.Select(attrs=CUSTOM_SELECT_ATTRS),
            'stage': forms.Select(attrs=CUSTOM_SELECT_ATTRS),
            'assignee': forms.Select(attrs=CUSTOM_SELECT_ATTRS),
            'status': forms.Select(attrs=CUSTOM_SELECT_ATTRS),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if not self.user:
            return

        projects = get_accessible_projects(self.user)
        self.fields['project'].queryset = projects
        self.fields['project'].empty_label = 'Без проекта'
        self.fields['stage'].empty_label = 'Без этапа'
        self.fields['assignee'].empty_label = 'Не назначен'
        self.fields['project'].widget.attrs.update(
            {
                'hx-get': reverse('task_form_options'),
                'hx-trigger': 'change',
                'hx-target': '#task-dependent-fields',
                'hx-swap': 'outerHTML',
                'hx-include': 'closest form',
            }
        )

        project_id = None
        if self.is_bound:
            project_id = self.data.get(self.add_prefix('project'))
        elif self.instance.pk:
            project_id = self.instance.project_id
        else:
            initial_project = self.initial.get('project')
            project_id = getattr(initial_project, 'pk', initial_project)

        stages = Stage.objects.none()
        assignees = get_collaborators(self.user)
        if project_id:
            try:
                project = projects.get(pk=project_id)
            except (Project.DoesNotExist, TypeError, ValueError):
                assignees = TelegramUser.objects.none()
            else:
                stages = Stage.objects.filter(
                    project=project,
                    project__is_archived=False,
                    is_archived=False,
                )
                assignees = project.get_members().filter(is_active=True)

        self.fields['stage'].queryset = stages
        self.fields['assignee'].queryset = assignees

    def clean(self):
        cleaned_data = super().clean()
        project = cleaned_data.get('project')
        assignee = cleaned_data.get('assignee')
        if project and assignee and not project.is_member(assignee):
            self.add_error('assignee', 'Исполнитель должен быть участником выбранного проекта.')
        return cleaned_data


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ['name', 'description']
        labels = {
            'name': 'Название',
            'description': 'Описание',
        }
        widgets = {
            'name': forms.TextInput(attrs={
                'class': FORM_CONTROL_CLASS,
                'placeholder': 'Например, «Курс по истории — Древний мир»',
            }),
            'description': forms.Textarea(
                attrs={'class': FORM_CONTROL_CLASS, 'rows': 3}
            ),
        }


class ProjectMembershipCreateForm(forms.ModelForm):
    class Meta:
        model = ProjectMembership
        fields = ['user', 'role']
        widgets = {
            'user': forms.Select(attrs=CUSTOM_SELECT_ATTRS),
            'role': forms.Select(attrs=CUSTOM_SELECT_ATTRS),
        }
        labels = {
            'user': 'Пользователь',
            'role': 'Роль',
        }

    def __init__(self, *args, **kwargs):
        self.project = kwargs.pop('project')
        self.actor = kwargs.pop('actor', None)
        super().__init__(*args, **kwargs)
        existing_user_ids = self.project.project_memberships.values_list(
            'user_id', flat=True
        )
        users = (
            get_collaborators(self.actor)
            if self.actor is not None
            else TelegramUser.objects.none()
        )
        self.fields['user'].queryset = users.exclude(pk__in=existing_user_ids)
        self.fields['user'].empty_label = 'Выберите участника'
        self.fields['role'].choices = [
            (ProjectMembership.Role.ADMIN, ProjectMembership.Role.ADMIN.label),
            (ProjectMembership.Role.MEMBER, ProjectMembership.Role.MEMBER.label),
        ]


class ProjectMembershipRoleForm(forms.ModelForm):
    class Meta:
        model = ProjectMembership
        fields = ['role']
        widgets = {
            'role': forms.Select(attrs=CUSTOM_SELECT_ATTRS),
        }
        labels = {'role': 'Роль'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['role'].choices = [
            (ProjectMembership.Role.ADMIN, ProjectMembership.Role.ADMIN.label),
            (ProjectMembership.Role.MEMBER, ProjectMembership.Role.MEMBER.label),
        ]


class CommentForm(forms.ModelForm):
    class Meta:
        model = Comment
        fields = ['text']
        widgets = {
            'text': forms.Textarea(attrs={
                'class': FORM_CONTROL_CLASS,
                'rows': 3,
                'placeholder': 'Написать комментарий…',
            }),
        }
        labels = {
            'text': 'Комментарий',
        }


class StageForm(forms.ModelForm):
    deadline = forms.DateField(
        required=False,
        label='Дедлайн',
        widget=forms.DateInput(attrs={'type': 'date', 'class': FORM_CONTROL_CLASS}),
    )

    class Meta:
        model = Stage
        fields = ['name', 'order', 'deadline', 'status']
        labels = {
            'name': 'Название',
            'order': 'Порядок',
            'status': 'Статус',
        }
        widgets = {
            'name': forms.TextInput(
                attrs={'class': FORM_CONTROL_CLASS, 'placeholder': 'Название этапа'}
            ),
            'order': forms.NumberInput(attrs={'class': FORM_CONTROL_CLASS}),
            'status': forms.Select(attrs=CUSTOM_SELECT_ATTRS),
        }

    def _post_clean(self):
        # order задаёт желаемую позицию; сервис перестановки применит её
        # после временного освобождения всех активных позиций.
        requested_order = self.cleaned_data.get('order')
        self.cleaned_data['order'] = None
        super()._post_clean()
        self.cleaned_data['order'] = requested_order
        self.instance.order = None


class InviteCodeCreateForm(forms.ModelForm):
    class Meta:
        model = InviteCode
        fields = ['project', 'project_role', 'expires_at']
        labels = {
            'project': 'Добавить в проект',
            'project_role': 'Роль в проекте',
            'expires_at': 'Действует до',
        }
        widgets = {
            'project': forms.Select(attrs=CUSTOM_SELECT_ATTRS),
            'project_role': forms.Select(attrs=CUSTOM_SELECT_ATTRS),
            'expires_at': forms.DateTimeInput(
                attrs={'type': 'datetime-local', 'class': FORM_CONTROL_CLASS}
            ),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['project'].queryset = get_accessible_projects(user)
        self.fields['project'].required = False
        self.fields['project_role'].required = False

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('project'):
            cleaned['project_role'] = ProjectMembership.Role.MEMBER
        return cleaned


class ProfileSettingsForm(forms.ModelForm):
    class Meta:
        model = TelegramUser
        fields = [
            'theme_preference',
            'timezone',
            'notify_deadlines',
            'notify_assignments',
            'notify_comments',
            'notify_messages',
        ]
        labels = {
            'theme_preference': 'Тема приложения',
            'timezone': 'Часовой пояс',
            'notify_deadlines': 'Дедлайны',
            'notify_assignments': 'Назначения задач',
            'notify_comments': 'Комментарии',
            'notify_messages': 'Сообщения',
        }
        widgets = {
            'theme_preference': forms.RadioSelect(),
        }

    def clean_timezone(self):
        value = self.cleaned_data['timezone'].strip()
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise forms.ValidationError('Неизвестный часовой пояс.') from exc
        return value


class ProjectOwnershipTransferForm(forms.Form):
    new_owner = forms.ModelChoiceField(
        queryset=TelegramUser.objects.none(),
        label='Новый владелец',
        widget=forms.Select(attrs=CUSTOM_SELECT_ATTRS),
    )
    confirmation = forms.CharField(label='Подтверждение')

    def __init__(self, *args, project, **kwargs):
        self.project = project
        super().__init__(*args, **kwargs)
        self.fields['new_owner'].queryset = TelegramUser.objects.filter(
            project_memberships__project=project,
            is_active=True,
        ).exclude(pk=project.owner_id)

    def clean_confirmation(self):
        value = self.cleaned_data['confirmation'].strip()
        if value != 'ПЕРЕДАТЬ':
            raise forms.ValidationError('Введите ПЕРЕДАТЬ заглавными буквами.')
        return value


class AccountDeleteForm(forms.Form):
    confirmation = forms.CharField(label='Подтверждение')

    def clean_confirmation(self):
        value = self.cleaned_data['confirmation'].strip()
        if value != 'УДАЛИТЬ':
            raise forms.ValidationError('Введите УДАЛИТЬ заглавными буквами.')
        return value

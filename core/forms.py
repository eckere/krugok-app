from django import forms
from django.urls import reverse

from .models import (
    Comment,
    Discussion,
    Message,
    Project,
    ProjectMembership,
    Stage,
    Task,
    TelegramUser,
)
from .permissions import get_accessible_projects, get_accessible_tasks


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
            'title': forms.TextInput(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2'}),
            'task': forms.Select(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2'}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if self.user:
            self.fields['participants'].queryset = TelegramUser.objects.filter(
                is_active=True
            ).exclude(pk=self.user.pk)
            self.fields['task'].queryset = get_accessible_tasks(self.user)

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


class MessageForm(forms.ModelForm):
    class Meta:
        model = Message
        fields = ['text']
        widgets = {
            'text': forms.Textarea(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2', 'rows': 2, 'placeholder': 'Написать сообщение...'}),
        }
        labels = {
            'text': '',
        }


class TaskForm(forms.ModelForm):
    deadline = forms.DateTimeField(
        required=False,
        label='Дедлайн',
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'w-full rounded-md border border-gray-300 px-3 py-2'}),
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
            'title': forms.TextInput(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2'}),
            'description': forms.Textarea(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2', 'rows': 4}),
            'project': forms.Select(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2'}),
            'stage': forms.Select(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2'}),
            'assignee': forms.Select(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2'}),
            'status': forms.Select(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2'}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if not self.user:
            return

        projects = get_accessible_projects(self.user)
        self.fields['project'].queryset = projects
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
        assignees = TelegramUser.objects.filter(is_active=True)
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
            'name': forms.TextInput(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2', 'placeholder': 'Например, «Курс по истории — Древний мир»'}),
            'description': forms.Textarea(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2', 'rows': 3}),
        }


class ProjectMembershipCreateForm(forms.ModelForm):
    class Meta:
        model = ProjectMembership
        fields = ['user', 'role']
        widgets = {
            'user': forms.Select(
                attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2'}
            ),
            'role': forms.Select(
                attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2'}
            ),
        }
        labels = {
            'user': 'Пользователь',
            'role': 'Роль',
        }

    def __init__(self, *args, **kwargs):
        self.project = kwargs.pop('project')
        super().__init__(*args, **kwargs)
        existing_user_ids = self.project.project_memberships.values_list(
            'user_id', flat=True
        )
        self.fields['user'].queryset = TelegramUser.objects.filter(
            is_active=True
        ).exclude(pk__in=existing_user_ids)
        self.fields['role'].choices = [
            (ProjectMembership.Role.ADMIN, ProjectMembership.Role.ADMIN.label),
            (ProjectMembership.Role.MEMBER, ProjectMembership.Role.MEMBER.label),
        ]


class ProjectMembershipRoleForm(forms.ModelForm):
    class Meta:
        model = ProjectMembership
        fields = ['role']
        widgets = {
            'role': forms.Select(
                attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2'}
            ),
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
            'text': forms.Textarea(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2', 'rows': 3, 'placeholder': 'Написать комментарий...'}),
        }
        labels = {
            'text': 'Комментарий',
        }


class StageForm(forms.ModelForm):
    deadline = forms.DateField(
        required=False,
        label='Дедлайн',
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'w-full rounded-md border border-gray-300 px-3 py-2'}),
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
            'name': forms.TextInput(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2', 'placeholder': 'Название этапа'}),
            'order': forms.NumberInput(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2'}),
            'status': forms.Select(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2'}),
        }

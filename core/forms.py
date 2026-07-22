from django import forms

from .models import Comment, Project, Stage, Task, Discussion, Message, TelegramUser


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
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if user:
            self.fields['participants'].queryset = TelegramUser.objects.exclude(pk=user.pk)


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
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'w-full rounded-md border border-gray-300 px-3 py-2'}),
    )

    class Meta:
        model = Task
        fields = ['title', 'description', 'project', 'stage', 'assignee', 'deadline', 'status']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2'}),
            'description': forms.Textarea(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2', 'rows': 4}),
            'project': forms.Select(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2'}),
            'stage': forms.Select(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2'}),
            'assignee': forms.Select(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2'}),
            'status': forms.Select(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2'}),
        }


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ['name', 'description']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2', 'placeholder': 'Например, «Курс по истории — Древний мир»'}),
            'description': forms.Textarea(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2', 'rows': 3}),
        }


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
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'w-full rounded-md border border-gray-300 px-3 py-2'}),
    )

    class Meta:
        model = Stage
        fields = ['name', 'order', 'deadline', 'status']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2', 'placeholder': 'Название этапа'}),
            'order': forms.NumberInput(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2'}),
            'status': forms.Select(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2'}),
        }

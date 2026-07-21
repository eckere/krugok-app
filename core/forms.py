from django import forms

from .models import Task


class TaskForm(forms.ModelForm):
    deadline = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'w-full rounded-md border border-gray-300 px-3 py-2'}),
    )

    class Meta:
        model = Task
        fields = ['title', 'description', 'project', 'assignee', 'deadline', 'status']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2'}),
            'description': forms.Textarea(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2', 'rows': 4}),
            'project': forms.Select(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2'}),
            'assignee': forms.Select(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2'}),
            'status': forms.Select(attrs={'class': 'w-full rounded-md border border-gray-300 px-3 py-2'}),
        }

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from .models import Project, Stage, Task


class StageAndProjectTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='tester', password='pass')
        self.client.force_login(self.user)
        self.project = Project.objects.create(name='Project 1', description='Test project', owner=self.user)
        self.project.members.add(self.user)

    def test_project_detail_shows_stages(self):
        Stage.objects.create(project=self.project, name='Stage 1', order=1)
        response = self.client.get(reverse('project_detail', args=[self.project.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Stage 1')

    def test_stage_create_assigns_to_project(self):
        response = self.client.post(
            reverse('stage_create', args=[self.project.id]),
            {'name': 'Новый этап', 'order': '1', 'status': 'not_started'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Новый этап')
        self.assertTrue(Stage.objects.filter(project=self.project, name='Новый этап').exists())

    def test_stage_update_reorders_existing_stages(self):
        stage1 = Stage.objects.create(project=self.project, name='Первый', order=1)
        stage2 = Stage.objects.create(project=self.project, name='Второй', order=2)
        response = self.client.post(
            reverse('stage_update', args=[stage1.id]),
            {'name': 'Первый', 'order': '2', 'status': 'not_started'},
        )
        self.assertEqual(response.status_code, 200)
        stage1.refresh_from_db()
        stage2.refresh_from_db()
        self.assertEqual(stage1.order, 2)
        self.assertEqual(stage2.order, 1)

    def test_stage_archive_marks_stage_archived(self):
        stage = Stage.objects.create(project=self.project, name='Скрытый', order=1)
        response = self.client.post(reverse('stage_archive', args=[stage.id]))
        self.assertEqual(response.status_code, 200)
        stage.refresh_from_db()
        self.assertTrue(stage.is_archived)

    def test_stage_delete_removes_stage(self):
        stage = Stage.objects.create(project=self.project, name='Удаляемый', order=1)
        response = self.client.delete(reverse('stage_delete', args=[stage.id]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Stage.objects.filter(pk=stage.pk).exists())

    def test_task_stage_must_belong_to_project(self):
        other_project = Project.objects.create(name='Other', description='Other project', owner=self.user)
        other_project.members.add(self.user)
        other_stage = Stage.objects.create(project=other_project, name='Другой этап', order=1)
        task = Task(title='Задача', project=self.project, stage=other_stage, creator=self.user)
        with self.assertRaises(ValidationError):
            task.full_clean()

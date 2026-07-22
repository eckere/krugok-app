from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from .models import Comment, Project, ProjectMembership, Stage, Task, Discussion, Message


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


class TaskCrudTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='tasker', password='pass')
        self.client.force_login(self.user)
        self.project = Project.objects.create(name='Project Task', description='Test project', owner=self.user)
        self.project.members.add(self.user)

    def test_create_task(self):
        response = self.client.post(reverse('task_create'), {
            'title': 'Task 1',
            'description': 'Desc',
            'project': self.project.id,
            'stage': '',
            'assignee': '',
            'deadline': '',
            'status': 'new',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Task 1')
        self.assertTrue(Task.objects.filter(title='Task 1', project=self.project).exists())

    def test_update_task(self):
        task = Task.objects.create(title='Task 2', description='Desc', project=self.project, creator=self.user)
        response = self.client.post(reverse('task_update', args=[task.id]), {
            'title': 'Task 2 updated',
            'description': 'Desc',
            'project': self.project.id,
            'stage': '',
            'assignee': '',
            'deadline': '',
            'status': 'in_progress',
        })
        self.assertEqual(response.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.title, 'Task 2 updated')
        self.assertEqual(task.status, 'in_progress')

    def test_delete_task_returns_empty_body(self):
        task = Task.objects.create(title='Task 3', description='Desc', project=self.project, creator=self.user)
        response = self.client.delete(reverse('task_delete', args=[task.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'')
        self.assertFalse(Task.objects.filter(pk=task.pk).exists())

    def test_status_task_returns_updated_card(self):
        task = Task.objects.create(title='Task 4', description='Desc', project=self.project, creator=self.user)
        response = self.client.post(reverse('task_status', args=[task.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'В процессе')
        task.refresh_from_db()
        self.assertEqual(task.status, 'in_progress')


class ProjectCrudTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='owner', password='pass')
        self.client.force_login(self.user)

    def test_create_project_sets_owner_and_member(self):
        response = self.client.post(reverse('project_create'), {
            'name': 'Project X',
            'description': 'Some desc',
        })
        self.assertEqual(response.status_code, 200)
        project = Project.objects.get(name='Project X')
        self.assertEqual(project.owner, self.user)
        self.assertTrue(project.members.filter(pk=self.user.pk).exists())

    def test_project_list_excludes_archived(self):
        Project.objects.create(name='Archived', description='X', owner=self.user, is_archived=True)
        active = Project.objects.create(name='Active', description='Y', owner=self.user)
        active.members.add(self.user)
        response = self.client.get(reverse('project_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Active')
        self.assertNotContains(response, 'Archived')

    def test_archive_project_keeps_row(self):
        project = Project.objects.create(name='Project Z', description='Desc', owner=self.user)
        project.members.add(self.user)
        response = self.client.post(reverse('project_archive', args=[project.id]))
        self.assertEqual(response.status_code, 200)
        project.refresh_from_db()
        self.assertTrue(project.is_archived)


class ProjectMembershipTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='memberuser', password='pass')
        self.client.force_login(self.user)

    def test_project_owner_membership_created_on_project_create(self):
        response = self.client.post(reverse('project_create'), {
            'name': 'Owner Project',
            'description': 'Owned project',
        })
        self.assertEqual(response.status_code, 200)
        project = Project.objects.get(name='Owner Project')
        self.assertTrue(ProjectMembership.objects.filter(
            project=project,
            user=self.user,
            role=ProjectMembership.Role.OWNER,
        ).exists())
        self.assertTrue(project.is_owner(self.user))
        self.assertTrue(project.is_member(self.user))

    def test_is_member_returns_true_for_existing_members(self):
        project = Project.objects.create(name='Member Project', description='Desc', owner=self.user)
        project.members.add(self.user)
        self.assertTrue(project.is_member(self.user))
        self.assertIn(self.user, list(project.get_members()))


class PermissionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='owner', password='pass')
        self.other = get_user_model().objects.create_user(username='other', password='pass')
        self.client.force_login(self.other)
        self.project = Project.objects.create(name='Protected', description='X', owner=self.user)
        self.project.members.add(self.user)
        self.task = Task.objects.create(title='Private', description='Desc', project=self.project, creator=self.user)

    def test_project_detail_forbidden_for_non_member(self):
        response = self.client.get(reverse('project_detail', args=[self.project.id]))
        self.assertIn(response.status_code, (403, 404))

    def test_stage_create_forbidden_for_non_member(self):
        response = self.client.post(reverse('stage_create', args=[self.project.id]), {
            'name': 'No', 'order': '1', 'status': 'not_started'
        })
        self.assertIn(response.status_code, (403, 404))

    def test_task_update_forbidden_for_non_member(self):
        response = self.client.post(reverse('task_update', args=[self.task.id]), {
            'title': 'Hack', 'description': 'x', 'project': self.project.id, 'stage': '', 'assignee': '', 'deadline': '', 'status': 'new'
        })
        self.assertIn(response.status_code, (403, 404))

    def test_task_delete_forbidden_for_non_member(self):
        response = self.client.delete(reverse('task_delete', args=[self.task.id]))
        self.assertIn(response.status_code, (403, 404))

    def test_task_status_forbidden_for_non_member(self):
        response = self.client.post(reverse('task_status', args=[self.task.id]))
        self.assertIn(response.status_code, (403, 404))


class TaskDetailAndCommentsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='commenter', password='pass')
        self.client.force_login(self.user)
        self.project = Project.objects.create(name='Project C', description='X', owner=self.user)
        self.project.members.add(self.user)
        self.task = Task.objects.create(title='Task C', description='Desc', project=self.project, creator=self.user)

    def test_task_detail_contains_title_and_description(self):
        response = self.client.get(reverse('task_detail', args=[self.task.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.task.title)
        self.assertContains(response, self.task.description)

    def test_task_detail_denies_non_member(self):
        other = get_user_model().objects.create_user(username='other2', password='pass')
        self.client.force_login(other)
        response = self.client.get(reverse('task_detail', args=[self.task.id]))
        self.assertIn(response.status_code, (403, 404))

    def test_comment_create_adds_comment(self):
        response = self.client.post(reverse('comment_create', args=[self.task.id]), {'text': 'Hello'})
        self.assertEqual(response.status_code, 302)
        comment = Comment.objects.get(task=self.task)
        self.assertEqual(comment.author, self.user)
        self.assertEqual(comment.text, 'Hello')

    def test_comment_create_empty_text_returns_error(self):
        response = self.client.post(reverse('comment_create', args=[self.task.id]), {'text': ''})
        self.assertEqual(response.status_code, 422)
        self.assertFalse(Comment.objects.filter(task=self.task).exists())
        self.assertContains(response, 'Комментарий', status_code=422)

    def test_comments_are_ordered_by_created_at(self):
        first = Comment.objects.create(task=self.task, author=self.user, text='Первый')
        second = Comment.objects.create(task=self.task, author=self.user, text='Второй')
        response = self.client.get(reverse('task_detail', args=[self.task.id]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertTrue(content.index('Первый') < content.index('Второй'))


class DiscussionAndMessageTests(TestCase):
    def setUp(self):
        self.user1 = get_user_model().objects.create_user(username='user1', first_name='Alice')
        self.user2 = get_user_model().objects.create_user(username='user2', first_name='Bob')
        self.user3 = get_user_model().objects.create_user(username='user3', first_name='Charlie')
        self.task = Task.objects.create(title='Test Task', creator=self.user1)

        self.client.force_login(self.user1)

    def test_discussion_create_with_participants(self):
        response = self.client.post(reverse('discussion_create'), {
            'title': 'Test Discussion',
            'task': self.task.id,
            'participants': [self.user2.id, self.user3.id]
        })
        self.assertEqual(response.status_code, 302) # Redirects to detail view
        discussion = Discussion.objects.get(title='Test Discussion')
        self.assertEqual(discussion.created_by, self.user1)
        self.assertIn(self.user1, discussion.participants.all())
        self.assertIn(self.user2, discussion.participants.all())
        self.assertIn(self.user3, discussion.participants.all())
        self.assertEqual(discussion.task, self.task)

    def test_discussion_create_without_task(self):
        response = self.client.post(reverse('discussion_create'), {
            'title': 'General Discussion',
            'participants': [self.user2.id]
        })
        self.assertEqual(response.status_code, 302)
        discussion = Discussion.objects.get(title='General Discussion')
        self.assertIsNone(discussion.task)
        self.assertIn(self.user1, discussion.participants.all())
        self.assertIn(self.user2, discussion.participants.all())

    def test_discussion_list_shows_only_user_discussions(self):
        # Discussion where user1 is creator
        Discussion.objects.create(title='My Discussion', created_by=self.user1)
        # Discussion where user1 is a participant
        d2 = Discussion.objects.create(title='Their Discussion', created_by=self.user2)
        d2.participants.add(self.user1)
        # Discussion where user1 is not involved
        Discussion.objects.create(title='Secret Discussion', created_by=self.user2)

        response = self.client.get(reverse('discussion_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'My Discussion')
        self.assertContains(response, 'Their Discussion')
        self.assertNotContains(response, 'Secret Discussion')

    def test_message_create_adds_message(self):
        discussion = Discussion.objects.create(title='Chat', created_by=self.user1)
        discussion.participants.add(self.user1)

        response = self.client.post(
            reverse('message_create', args=[discussion.id]),
            {'text': 'Hello there!'},
            HTTP_HX_REQUEST='true' # Simulate HTMX request
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Hello there!')
        message = Message.objects.get(discussion=discussion)
        self.assertEqual(message.text, 'Hello there!')
        self.assertEqual(message.sender, self.user1)

    def test_message_create_with_empty_text_fails(self):
        discussion = Discussion.objects.create(title='Chat Empty', created_by=self.user1)
        discussion.participants.add(self.user1)

        response = self.client.post(reverse('message_create', args=[discussion.id]), {'text': ''})
        # As per the view logic, it redirects on failure for non-htmx, let's stick to that
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Message.objects.filter(discussion=discussion).exists())

    def test_non_participant_gets_403_on_detail(self):
        discussion = Discussion.objects.create(title='Private Chat', created_by=self.user2)
        # user1 is not a participant
        self.client.force_login(self.user1)
        response = self.client.get(reverse('discussion_detail', args=[discussion.id]))
        self.assertEqual(response.status_code, 403)

    def test_non_participant_gets_403_on_message_create(self):
        discussion = Discussion.objects.create(title='Private Chat 2', created_by=self.user2)
        # user1 is not a participant
        self.client.force_login(self.user1)
        response = self.client.post(reverse('message_create', args=[discussion.id]), {'text': 'Intrusion!'})
        self.assertEqual(response.status_code, 403)

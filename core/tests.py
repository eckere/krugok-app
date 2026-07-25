from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Comment, Project, ProjectMembership, Stage, Task, Discussion, Message


class DevAccountSwitcherTests(TestCase):
    def setUp(self):
        self.current_user = get_user_model().objects.create_user(
            username='current',
            first_name='Текущий',
        )
        self.other_user = get_user_model().objects.create_user(
            username='other',
            first_name='Другой',
        )
        self.client.force_login(self.current_user)

    @override_settings(DEBUG=True)
    def test_switches_to_active_account_and_redirects_home(self):
        response = self.client.post(
            reverse('dev_switch_account', args=[self.other_user.id])
        )

        self.assertRedirects(response, reverse('index'))
        self.assertEqual(
            int(self.client.session['_auth_user_id']),
            self.other_user.id,
        )

    @override_settings(DEBUG=True)
    def test_home_shows_available_test_accounts(self):
        response = self.client.get(reverse('index'))

        self.assertContains(response, 'Сменить аккаунт')
        self.assertContains(response, self.other_user.display_name)

    @override_settings(DEBUG=False)
    def test_switcher_is_unavailable_outside_debug(self):
        response = self.client.post(
            reverse('dev_switch_account', args=[self.other_user.id])
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            int(self.client.session['_auth_user_id']),
            self.current_user.id,
        )


class StageAndProjectTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='tester', password='pass')
        self.client.force_login(self.user)
        self.project = Project.objects.create(name='Project 1', description='Test project', owner=self.user)
        ProjectMembership.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMembership.Role.OWNER,
        )

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
        ProjectMembership.objects.create(
            project=other_project,
            user=self.user,
            role=ProjectMembership.Role.OWNER,
        )
        other_stage = Stage.objects.create(project=other_project, name='Другой этап', order=1)
        task = Task(title='Задача', project=self.project, stage=other_stage, creator=self.user)
        with self.assertRaises(ValidationError):
            task.full_clean()


class TaskCrudTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='tasker', password='pass')
        self.client.force_login(self.user)
        self.project = Project.objects.create(name='Project Task', description='Test project', owner=self.user)
        ProjectMembership.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMembership.Role.OWNER,
        )

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
        ProjectMembership.objects.create(
            project=active,
            user=self.user,
            role=ProjectMembership.Role.OWNER,
        )
        response = self.client.get(reverse('project_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Active')
        self.assertNotContains(response, 'Archived')

    def test_archive_project_keeps_row(self):
        project = Project.objects.create(name='Project Z', description='Desc', owner=self.user)
        ProjectMembership.objects.create(
            project=project,
            user=self.user,
            role=ProjectMembership.Role.OWNER,
        )
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
        ProjectMembership.objects.create(
            project=project,
            user=self.user,
            role=ProjectMembership.Role.OWNER,
        )
        self.assertTrue(project.is_member(self.user))
        self.assertIn(self.user, list(project.get_members()))


class ProjectMembershipCrudTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(username='membership_owner')
        self.admin = user_model.objects.create_user(username='membership_admin')
        self.candidate = user_model.objects.create_user(username='membership_candidate')
        self.project = Project.objects.create(name='Membership project', owner=self.owner)
        self.owner_membership = ProjectMembership.objects.create(
            project=self.project,
            user=self.owner,
            role=ProjectMembership.Role.OWNER,
        )
        self.admin_membership = ProjectMembership.objects.create(
            project=self.project,
            user=self.admin,
            role=ProjectMembership.Role.ADMIN,
        )

    def test_members_relation_uses_project_membership_as_source(self):
        self.assertIn(self.owner, self.project.members.all())
        self.assertIn(self.admin, self.project.members.all())
        self.assertEqual(self.project.members.count(), 2)

    def test_owner_can_add_member(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse('project_member_create', args=[self.project.id]),
            {
                'user': self.candidate.id,
                'role': ProjectMembership.Role.MEMBER,
            },
        )

        self.assertEqual(response.status_code, 200)
        membership = ProjectMembership.objects.get(
            project=self.project,
            user=self.candidate,
        )
        self.assertEqual(membership.role, ProjectMembership.Role.MEMBER)
        self.assertTrue(self.project.is_member(self.candidate))

    def test_owner_can_change_member_role(self):
        membership = ProjectMembership.objects.create(
            project=self.project,
            user=self.candidate,
            role=ProjectMembership.Role.MEMBER,
        )
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse('project_member_update', args=[membership.id]),
            {'role': ProjectMembership.Role.ADMIN},
        )

        self.assertEqual(response.status_code, 200)
        membership.refresh_from_db()
        self.assertEqual(membership.role, ProjectMembership.Role.ADMIN)
        self.assertTrue(self.project.is_admin(self.candidate))

    def test_owner_can_remove_member_and_revoke_project_access(self):
        membership = ProjectMembership.objects.create(
            project=self.project,
            user=self.candidate,
            role=ProjectMembership.Role.MEMBER,
        )
        self.client.force_login(self.owner)

        response = self.client.delete(
            reverse('project_member_delete', args=[membership.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ProjectMembership.objects.filter(pk=membership.pk).exists())
        self.assertFalse(self.project.is_member(self.candidate))

        self.client.force_login(self.candidate)
        self.assertEqual(
            self.client.get(reverse('project_detail', args=[self.project.id])).status_code,
            403,
        )

    def test_admin_cannot_manage_project_members(self):
        membership = ProjectMembership.objects.create(
            project=self.project,
            user=self.candidate,
            role=ProjectMembership.Role.MEMBER,
        )
        self.client.force_login(self.admin)

        create_response = self.client.post(
            reverse('project_member_create', args=[self.project.id]),
            {'user': self.candidate.id, 'role': ProjectMembership.Role.MEMBER},
        )
        update_response = self.client.post(
            reverse('project_member_update', args=[membership.id]),
            {'role': ProjectMembership.Role.ADMIN},
        )
        delete_response = self.client.delete(
            reverse('project_member_delete', args=[membership.id])
        )

        self.assertEqual(create_response.status_code, 403)
        self.assertEqual(update_response.status_code, 403)
        self.assertEqual(delete_response.status_code, 403)

    def test_owner_membership_cannot_be_changed_or_deleted(self):
        self.client.force_login(self.owner)

        update_response = self.client.post(
            reverse('project_member_update', args=[self.owner_membership.id]),
            {'role': ProjectMembership.Role.MEMBER},
        )
        delete_response = self.client.delete(
            reverse('project_member_delete', args=[self.owner_membership.id])
        )

        self.assertEqual(update_response.status_code, 403)
        self.assertEqual(delete_response.status_code, 403)
        self.owner_membership.refresh_from_db()
        self.assertEqual(self.owner_membership.role, ProjectMembership.Role.OWNER)

    def test_member_form_does_not_offer_owner_role(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse('project_member_create', args=[self.project.id]),
            {
                'user': self.candidate.id,
                'role': ProjectMembership.Role.OWNER,
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertFalse(
            ProjectMembership.objects.filter(
                project=self.project,
                user=self.candidate,
            ).exists()
        )

    def test_model_rejects_invalid_owner_roles(self):
        with self.assertRaises(ValidationError):
            ProjectMembership.objects.create(
                project=self.project,
                user=self.candidate,
                role=ProjectMembership.Role.OWNER,
            )

        self.owner_membership.role = ProjectMembership.Role.MEMBER
        with self.assertRaises(ValidationError):
            self.owner_membership.save()

        self.owner_membership.refresh_from_db()
        with self.assertRaises(ValidationError):
            self.owner_membership.delete()


class PermissionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='owner', password='pass')
        self.other = get_user_model().objects.create_user(username='other', password='pass')
        self.client.force_login(self.other)
        self.project = Project.objects.create(name='Protected', description='X', owner=self.user)
        ProjectMembership.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMembership.Role.OWNER,
        )
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


class AccessControlRegressionTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(username='access_owner')
        self.admin = user_model.objects.create_user(username='access_admin')
        self.member = user_model.objects.create_user(username='access_member')
        self.outsider = user_model.objects.create_user(username='access_outsider')

        self.project = Project.objects.create(name='Visible project', owner=self.owner)
        ProjectMembership.objects.create(
            project=self.project,
            user=self.owner,
            role=ProjectMembership.Role.OWNER,
        )
        ProjectMembership.objects.create(
            project=self.project,
            user=self.admin,
            role=ProjectMembership.Role.ADMIN,
        )
        ProjectMembership.objects.create(
            project=self.project,
            user=self.member,
            role=ProjectMembership.Role.MEMBER,
        )
        self.stage = Stage.objects.create(project=self.project, name='Protected stage', order=1)
        self.project_task = Task.objects.create(
            title='Visible project task',
            project=self.project,
            stage=self.stage,
            creator=self.owner,
            assignee=self.member,
        )

        self.foreign_project = Project.objects.create(
            name='Foreign project',
            owner=self.outsider,
        )
        ProjectMembership.objects.create(
            project=self.foreign_project,
            user=self.outsider,
            role=ProjectMembership.Role.OWNER,
        )
        self.foreign_task = Task.objects.create(
            title='Foreign task',
            project=self.foreign_project,
            creator=self.outsider,
        )

    def test_lists_and_home_hide_inaccessible_objects(self):
        self.client.force_login(self.member)

        for url_name in ('index', 'task_list', 'project_list'):
            response = self.client.get(reverse(url_name))
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'Foreign project')
            self.assertNotContains(response, 'Foreign task')

        self.assertContains(self.client.get(reverse('task_list')), self.project_task.title)
        self.assertContains(self.client.get(reverse('project_list')), self.project.name)

    def test_standalone_task_is_visible_only_to_creator_and_assignee(self):
        standalone = Task.objects.create(
            title='Standalone private',
            creator=self.owner,
            assignee=self.member,
        )

        self.client.force_login(self.outsider)
        response = self.client.get(reverse('task_detail', args=[standalone.id]))
        self.assertEqual(response.status_code, 403)

        self.client.force_login(self.member)
        response = self.client.get(reverse('task_detail', args=[standalone.id]))
        self.assertEqual(response.status_code, 200)

    def test_standalone_assignee_can_change_status_but_cannot_edit_or_delete(self):
        standalone = Task.objects.create(
            title='Assigned standalone',
            creator=self.owner,
            assignee=self.member,
        )
        self.client.force_login(self.member)

        status_response = self.client.post(reverse('task_status', args=[standalone.id]))
        edit_response = self.client.get(reverse('task_update', args=[standalone.id]))
        delete_response = self.client.delete(reverse('task_delete', args=[standalone.id]))

        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(edit_response.status_code, 403)
        self.assertEqual(delete_response.status_code, 403)

    def test_member_cannot_mutate_project_or_stages(self):
        self.client.force_login(self.member)

        self.assertEqual(
            self.client.get(reverse('project_update', args=[self.project.id])).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(reverse('project_archive', args=[self.project.id])).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                reverse('stage_create', args=[self.project.id]),
                {'name': 'Forbidden', 'order': '2', 'status': Stage.Status.NOT_STARTED},
            ).status_code,
            403,
        )

    def test_admin_can_update_project_but_cannot_delete_it(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('project_update', args=[self.project.id]),
            {'name': 'Updated by admin', 'description': 'Allowed'},
        )

        self.assertEqual(response.status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, 'Updated by admin')
        self.assertEqual(
            self.client.delete(reverse('project_delete', args=[self.project.id])).status_code,
            403,
        )

    def test_project_update_changes_existing_row_instead_of_creating_copy(self):
        self.client.force_login(self.owner)
        project_count = Project.objects.count()

        form_response = self.client.get(reverse('project_update', args=[self.project.id]))
        update_response = self.client.post(
            reverse('project_update', args=[self.project.id]),
            {'name': 'Renamed project', 'description': 'Changed'},
        )

        self.assertContains(form_response, reverse('project_update', args=[self.project.id]))
        self.assertEqual(update_response.status_code, 200)
        self.assertEqual(Project.objects.count(), project_count)
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, 'Renamed project')

    def test_task_form_rejects_inaccessible_project(self):
        self.client.force_login(self.member)
        response = self.client.post(
            reverse('task_create'),
            {
                'title': 'Injected task',
                'description': '',
                'project': self.foreign_project.id,
                'stage': '',
                'assignee': '',
                'deadline': '',
                'status': Task.Status.NEW,
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertFalse(Task.objects.filter(title='Injected task').exists())
        self.assertFormError(response.context['form'], 'project', 'Выберите корректный вариант. Вашего варианта нет среди допустимых значений.')

    def test_task_form_rejects_assignee_outside_project(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse('task_create'),
            {
                'title': 'Invalid assignee',
                'description': '',
                'project': self.project.id,
                'stage': self.stage.id,
                'assignee': self.outsider.id,
                'deadline': '',
                'status': Task.Status.NEW,
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertFalse(Task.objects.filter(title='Invalid assignee').exists())
        self.assertTrue(response.context['form'].errors.get('assignee'))

    def test_discussion_form_rejects_inaccessible_task(self):
        self.client.force_login(self.member)
        response = self.client.post(
            reverse('discussion_create'),
            {
                'title': 'Leaked discussion',
                'task': self.foreign_task.id,
                'participants': [self.owner.id],
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertFalse(Discussion.objects.filter(title='Leaked discussion').exists())
        self.assertTrue(response.context['form'].errors.get('task'))

    def test_task_discussion_rejects_participant_outside_project(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse('discussion_create'),
            {
                'title': 'Invalid participant',
                'task': self.project_task.id,
                'participants': [self.outsider.id],
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertFalse(Discussion.objects.filter(title='Invalid participant').exists())
        self.assertTrue(response.context['form'].errors.get('participants'))

    def test_standalone_task_discussion_grants_participant_read_access(self):
        standalone = Task.objects.create(title='Discussed standalone', creator=self.owner)
        discussion = Discussion.objects.create(
            title='Standalone discussion',
            task=standalone,
            created_by=self.owner,
        )
        discussion.participants.add(self.member)
        self.client.force_login(self.member)

        response = self.client.get(reverse('task_detail', args=[standalone.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, standalone.title)


class ProjectTaskWorkflowTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(username='workflow_owner')
        self.member = user_model.objects.create_user(username='workflow_member')
        self.outsider = user_model.objects.create_user(username='workflow_outsider')

        self.project = Project.objects.create(name='Workflow project', owner=self.owner)
        ProjectMembership.objects.create(
            project=self.project,
            user=self.owner,
            role=ProjectMembership.Role.OWNER,
        )
        ProjectMembership.objects.create(
            project=self.project,
            user=self.member,
            role=ProjectMembership.Role.MEMBER,
        )
        self.stage_one = Stage.objects.create(
            project=self.project,
            name='Stage one',
            order=1,
        )
        self.stage_two = Stage.objects.create(
            project=self.project,
            name='Stage two',
            order=2,
        )

        self.foreign_project = Project.objects.create(
            name='Foreign workflow',
            owner=self.outsider,
        )
        ProjectMembership.objects.create(
            project=self.foreign_project,
            user=self.outsider,
            role=ProjectMembership.Role.OWNER,
        )
        self.foreign_stage = Stage.objects.create(
            project=self.foreign_project,
            name='Foreign stage',
            order=1,
        )

    def test_project_detail_shows_staged_and_unassigned_tasks(self):
        staged_task = Task.objects.create(
            title='Task inside stage',
            project=self.project,
            stage=self.stage_one,
            creator=self.owner,
        )
        unassigned_task = Task.objects.create(
            title='Task without stage',
            project=self.project,
            creator=self.owner,
        )
        self.client.force_login(self.member)

        response = self.client.get(reverse('project_detail', args=[self.project.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, staged_task.title)
        self.assertContains(response, unassigned_task.title)
        self.assertContains(response, f'stage-{self.stage_one.id}-task-list')
        self.assertContains(response, 'project-unassigned-task-list')

    def test_task_create_from_stage_preselects_project_and_stage(self):
        self.client.force_login(self.member)
        response = self.client.get(
            reverse('task_create'),
            {
                'project': self.project.id,
                'stage': self.stage_two.id,
                'return_to': 'project',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['form'].initial['project'], self.project)
        self.assertEqual(response.context['form'].initial['stage'], self.stage_two)
        self.assertEqual(response.context['return_to'], 'project')

    def test_member_can_create_task_in_stage_with_htmx_retarget(self):
        self.client.force_login(self.member)
        response = self.client.post(
            reverse('task_create'),
            {
                'title': 'Created from stage',
                'description': '',
                'project': self.project.id,
                'stage': self.stage_one.id,
                'assignee': self.member.id,
                'deadline': '',
                'status': Task.Status.NEW,
                'return_to': 'project',
            },
            HTTP_HX_REQUEST='true',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers['HX-Retarget'],
            f'#stage-{self.stage_one.id}-task-list',
        )
        self.assertEqual(response.headers['HX-Reswap'], 'beforeend')
        task = Task.objects.get(title='Created from stage')
        self.assertEqual(task.project, self.project)
        self.assertEqual(task.stage, self.stage_one)
        self.assertEqual(task.creator, self.member)

    def test_dependent_fields_only_offer_project_stages_and_members(self):
        self.client.force_login(self.member)
        response = self.client.get(
            reverse('task_form_options'),
            {'project': self.project.id},
        )

        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertQuerySetEqual(
            form.fields['stage'].queryset.order_by('id'),
            [self.stage_one, self.stage_two],
        )
        self.assertQuerySetEqual(
            form.fields['assignee'].queryset.order_by('id'),
            [self.owner, self.member],
        )
        self.assertNotIn(self.foreign_stage, form.fields['stage'].queryset)
        self.assertNotIn(self.outsider, form.fields['assignee'].queryset)

    def test_foreign_project_cannot_be_opened_through_task_create_context(self):
        self.client.force_login(self.member)
        response = self.client.get(
            reverse('task_create'),
            {'project': self.foreign_project.id, 'return_to': 'project'},
        )

        self.assertEqual(response.status_code, 403)

    def test_task_move_between_stages_redirects_to_project(self):
        task = Task.objects.create(
            title='Move me',
            project=self.project,
            stage=self.stage_one,
            creator=self.member,
            assignee=self.member,
        )
        self.client.force_login(self.member)

        response = self.client.post(
            reverse('task_update', args=[task.id]),
            {
                'title': task.title,
                'description': '',
                'project': self.project.id,
                'stage': self.stage_two.id,
                'assignee': self.member.id,
                'deadline': '',
                'status': Task.Status.IN_PROGRESS,
                'return_to': 'project',
            },
            HTTP_HX_REQUEST='true',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers['HX-Redirect'],
            reverse('project_detail', args=[self.project.id]),
        )
        task.refresh_from_db()
        self.assertEqual(task.stage, self.stage_two)

    def test_invalid_project_task_form_returns_errors_to_modal(self):
        self.client.force_login(self.member)
        response = self.client.post(
            reverse('task_create'),
            {
                'title': '',
                'description': '',
                'project': self.project.id,
                'stage': self.stage_one.id,
                'assignee': self.member.id,
                'deadline': '',
                'status': Task.Status.NEW,
                'return_to': 'project',
            },
            HTTP_HX_REQUEST='true',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['HX-Retarget'], '#task-modal')
        self.assertEqual(response.headers['HX-Reswap'], 'innerHTML')
        self.assertTrue(response.context['form'].errors.get('title'))


class TaskDetailAndCommentsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='commenter', password='pass')
        self.client.force_login(self.user)
        self.project = Project.objects.create(name='Project C', description='X', owner=self.user)
        ProjectMembership.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMembership.Role.OWNER,
        )
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
        # Ошибка валидации возвращается как OOB-обновление #message-error,
        # а не редиректом: иначе HTMX вставил бы в историю целую страницу.
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'message-error', response.content)
        self.assertFalse(Message.objects.filter(discussion=discussion).exists())

    def test_message_create_success_clears_error_and_placeholder_oob(self):
        discussion = Discussion.objects.create(title='Chat OOB', created_by=self.user1)
        discussion.participants.add(self.user1)

        response = self.client.post(
            reverse('message_create', args=[discussion.id]),
            {'text': 'Hello OOB!'},
            HTTP_HX_REQUEST='true',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'message-error')
        self.assertContains(response, 'no-messages-placeholder')
        self.assertContains(response, 'hx-swap-oob="delete"')

    def test_message_poll_returns_only_messages_after_cursor(self):
        discussion = Discussion.objects.create(title='Chat Poll', created_by=self.user1)
        discussion.participants.add(self.user1)
        first = Message.objects.create(
            discussion=discussion,
            sender=self.user1,
            text='First',
        )
        second = Message.objects.create(
            discussion=discussion,
            sender=self.user2,
            text='Second',
        )

        response = self.client.get(
            reverse('discussion_messages_poll', args=[discussion.id]),
            {'after': first.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'First')
        self.assertContains(response, f'id="message-{second.id}"')
        self.assertContains(response, 'Second')

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


class HtmxModalErrorRetargetTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(username='htmx_owner')
        self.candidate = user_model.objects.create_user(username='htmx_candidate')
        self.project = Project.objects.create(
            name='HTMX project',
            owner=self.owner,
        )
        ProjectMembership.objects.create(
            project=self.project,
            user=self.owner,
            role=ProjectMembership.Role.OWNER,
        )
        self.membership = ProjectMembership.objects.create(
            project=self.project,
            user=self.candidate,
            role=ProjectMembership.Role.MEMBER,
        )
        self.stage = Stage.objects.create(
            project=self.project,
            name='HTMX stage',
            order=1,
        )
        self.client.force_login(self.owner)

    def assert_modal_error(self, response, modal_id):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['HX-Retarget'], modal_id)
        self.assertEqual(response.headers['HX-Reswap'], 'innerHTML')

    def test_project_create_error_returns_to_project_modal(self):
        response = self.client.post(
            reverse('project_create'),
            {'name': '', 'description': ''},
            HTTP_HX_REQUEST='true',
        )
        self.assert_modal_error(response, '#project-modal')

    def test_project_update_error_returns_to_project_modal(self):
        response = self.client.post(
            reverse('project_update', args=[self.project.id]),
            {'name': '', 'description': ''},
            HTTP_HX_REQUEST='true',
        )
        self.assert_modal_error(response, '#project-modal')

    def test_member_create_error_returns_to_member_modal(self):
        response = self.client.post(
            reverse('project_member_create', args=[self.project.id]),
            {'user': '', 'role': ProjectMembership.Role.MEMBER},
            HTTP_HX_REQUEST='true',
        )
        self.assert_modal_error(response, '#member-modal')

    def test_member_update_error_returns_to_member_modal(self):
        response = self.client.post(
            reverse('project_member_update', args=[self.membership.id]),
            {'role': ''},
            HTTP_HX_REQUEST='true',
        )
        self.assert_modal_error(response, '#member-modal')

    def test_stage_create_error_returns_to_stage_modal(self):
        response = self.client.post(
            reverse('stage_create', args=[self.project.id]),
            {'name': '', 'order': '', 'deadline': '', 'status': Stage.Status.NOT_STARTED},
            HTTP_HX_REQUEST='true',
        )
        self.assert_modal_error(response, '#stage-modal')

    def test_stage_update_error_returns_to_stage_modal(self):
        response = self.client.post(
            reverse('stage_update', args=[self.stage.id]),
            {'name': '', 'order': '1', 'deadline': '', 'status': Stage.Status.NOT_STARTED},
            HTTP_HX_REQUEST='true',
        )
        self.assert_modal_error(response, '#stage-modal')

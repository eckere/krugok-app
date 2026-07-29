import json
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    Comment,
    Discussion,
    InviteCode,
    Message,
    Notification,
    OutboundMessage,
    Project,
    ProjectMembership,
    Stage,
    Task,
)
from .telegram_auth import (
    InitDataValidationError,
    LoginWidgetValidationError,
)
from .telegram_notifications import notify, process_outbound


class HealthcheckTests(TestCase):
    def test_healthcheck_returns_ok(self):
        response = self.client.get(reverse('healthcheck'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})


class TelegramAuthenticationTests(TestCase):
    @patch('core.views.validate_init_data')
    def test_valid_init_data_creates_user_and_logs_in(self, validate_init_data):
        validate_init_data.return_value = {
            'id': 987654321,
            'username': 'telegram_user',
            'first_name': 'Ирина',
            'last_name': 'Тестова',
            'photo_url': 'https://example.com/avatar.jpg',
        }

        response = self.client.post(
            reverse('auth_telegram'),
            data=json.dumps({'init_data': 'valid-init-data'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'success': True, 'redirect_url': reverse('index')})
        user = get_user_model().objects.get(telegram_id=987654321)
        self.assertEqual(user.username, 'tg_987654321')
        self.assertEqual(user.telegram_username, 'telegram_user')
        self.assertEqual(user.first_name, 'Ирина')
        self.assertEqual(user.last_name, 'Тестова')
        self.assertEqual(user.photo_url, 'https://example.com/avatar.jpg')

        next_response = self.client.get(reverse('index'))
        self.assertTrue(next_response.wsgi_request.user.is_authenticated)
        self.assertEqual(next_response.wsgi_request.user, user)

    @patch('core.views.validate_init_data')
    def test_valid_init_data_updates_existing_user(self, validate_init_data):
        user = get_user_model().objects.create_user(
            username='old_username',
            telegram_id=123456789,
            first_name='Старое',
            last_name='Имя',
        )
        validate_init_data.return_value = {
            'id': user.telegram_id,
            'username': 'new_username',
            'first_name': 'Новое',
            'last_name': 'Имя',
            'photo_url': 'https://example.com/new-avatar.jpg',
        }

        response = self.client.post(
            reverse('auth_telegram'),
            data=json.dumps({'init_data': 'valid-init-data'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.username, 'tg_123456789')
        self.assertEqual(user.telegram_username, 'new_username')
        self.assertEqual(user.first_name, 'Новое')
        self.assertEqual(user.photo_url, 'https://example.com/new-avatar.jpg')
        self.assertEqual(get_user_model().objects.count(), 1)

    @patch('core.views.validate_init_data')
    def test_invalid_init_data_returns_401_without_logging_in(self, validate_init_data):
        validate_init_data.side_effect = InitDataValidationError('Неверная подпись')

        response = self.client.post(
            reverse('auth_telegram'),
            data=json.dumps({'init_data': 'invalid-init-data'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {'error': 'Не удалось войти'})
        self.assertFalse('_auth_user_id' in self.client.session)
        self.assertFalse(get_user_model().objects.exists())

    def test_empty_or_missing_init_data_returns_401(self):
        for payload in ({}, {'init_data': ''}):
            with self.subTest(payload=payload):
                response = self.client.post(
                    reverse('auth_telegram'),
                    data=json.dumps(payload),
                    content_type='application/json',
                )
                self.assertEqual(response.status_code, 401)
                self.assertFalse('_auth_user_id' in self.client.session)

    @patch('core.views.validate_init_data')
    def test_startapp_invite_is_redeemed_automatically(
        self, validate_init_data
    ):
        invite = InviteCode.objects.create()
        validate_init_data.return_value = {
            'id': 246813579,
            'username': 'invited_user',
            'first_name': 'Гость',
        }

        response = self.client.post(
            reverse('auth_telegram'),
            data=json.dumps(
                {
                    'init_data': (
                        f'auth_date=1&start_param=invite_{invite.code}'
                    )
                }
            ),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['redirect_url'], reverse('index'))
        user = get_user_model().objects.get(telegram_id=246813579)
        invite.refresh_from_db()
        self.assertTrue(user.is_verified)
        self.assertEqual(invite.used_by, user)
        self.assertFalse(invite.is_active)
        self.assertNotIn('pending_invite_code', self.client.session)

    @patch('core.views.validate_init_data')
    def test_invalid_startapp_invite_falls_back_to_redeem_form(
        self, validate_init_data
    ):
        validate_init_data.return_value = {
            'id': 975318642,
            'username': 'invalid_invite_user',
        }
        missing_code = 'missing-invite-code'

        response = self.client.post(
            reverse('auth_telegram'),
            data=json.dumps(
                {
                    'init_data': (
                        f'auth_date=1&start_param=invite_{missing_code}'
                    )
                }
            ),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['redirect_url'], reverse('invite_redeem'))
        self.assertEqual(
            self.client.session['pending_invite_code'],
            missing_code,
        )

    @patch('core.views.validate_init_data')
    def test_unrecognized_startapp_parameter_is_ignored(self, validate_init_data):
        validate_init_data.return_value = {
            'id': 135792468,
            'username': 'regular_user',
        }

        response = self.client.post(
            reverse('auth_telegram'),
            data=json.dumps(
                {'init_data': 'auth_date=1&start_param=unexpected_action'}
            ),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['redirect_url'], reverse('index'))
        self.assertNotIn('pending_invite_code', self.client.session)

    @override_settings(TELEGRAM_ALLOWED_IDS=frozenset({987654321}))
    @patch('core.views.validate_init_data')
    def test_allowlisted_telegram_id_has_access_without_invite(self, validate_init_data):
        validate_init_data.return_value = {
            'id': 987654321,
            'username': 'allowed_user',
            'first_name': 'Разрешённый',
        }

        response = self.client.post(
            reverse('auth_telegram'),
            data=json.dumps({'init_data': 'valid-init-data'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        user = get_user_model().objects.get(telegram_id=987654321)
        self.assertFalse(user.is_verified)
        self.assertEqual(self.client.get(reverse('index')).status_code, 200)
        self.assertRedirects(
            self.client.get(reverse('invite_redeem')),
            reverse('index'),
        )

    @patch('core.views.validate_login_widget_data')
    def test_login_widget_creates_user_and_logs_in(self, validate_login_widget_data):
        validate_login_widget_data.return_value = {
            'id': 765432198,
            'username': 'widget_user',
            'first_name': 'Павел',
            'last_name': 'Виджетов',
            'photo_url': 'https://example.com/widget-avatar.jpg',
        }

        response = self.client.post(
            reverse('auth_telegram_widget'),
            data=json.dumps({'auth_data': {'id': 765432198}}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'success': True, 'redirect_url': reverse('index')})
        user = get_user_model().objects.get(telegram_id=765432198)
        self.assertEqual(user.username, 'tg_765432198')
        self.assertEqual(user.telegram_username, 'widget_user')
        self.assertEqual(int(self.client.session['_auth_user_id']), user.id)

    @patch('core.views.validate_login_widget_data')
    def test_invalid_login_widget_data_returns_401(self, validate_login_widget_data):
        validate_login_widget_data.side_effect = LoginWidgetValidationError('Неверная подпись')

        response = self.client.post(
            reverse('auth_telegram_widget'),
            data=json.dumps({'auth_data': {'id': 765432198}}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {'error': 'Не удалось войти'})
        self.assertFalse('_auth_user_id' in self.client.session)

    @override_settings(DEBUG=True)
    def test_dev_login_creates_verified_local_account(self):
        response = self.client.get(reverse('dev_login'))

        self.assertRedirects(
            response,
            reverse('index'),
            fetch_redirect_response=False,
        )
        user = get_user_model().objects.get(telegram_id=111222333)
        self.assertEqual(int(self.client.session['_auth_user_id']), user.id)
        self.assertTrue(user.is_verified)
        self.assertEqual(self.client.get(reverse('index')).status_code, 200)


class InviteCodeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='unverified_user',
            is_verified=False,
        )
        self.client.force_login(self.user)

    def test_unverified_user_is_redirected_to_invite_form(self):
        response = self.client.get(reverse('discussion_list'))

        self.assertRedirects(response, reverse('invite_redeem'))

    def test_existing_session_redeems_startapp_invite_automatically(self):
        invite = InviteCode.objects.create()

        response = self.client.get(
            reverse('index'),
            {'tgWebAppStartParam': f'invite_{invite.code}'},
        )

        self.assertRedirects(response, reverse('index'))
        self.user.refresh_from_db()
        invite.refresh_from_db()
        self.assertTrue(self.user.is_verified)
        self.assertEqual(invite.used_by, self.user)
        self.assertFalse(invite.is_active)
        self.assertNotIn('pending_invite_code', self.client.session)

    def test_unverified_htmx_request_redirects_to_invite_form(self):
        response = self.client.post(
            reverse('discussion_create'),
            HTTP_HX_REQUEST='true',
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers['HX-Redirect'], reverse('invite_redeem'))

    def test_valid_code_verifies_user_and_deactivates_code(self):
        invite = InviteCode.objects.create()

        response = self.client.post(reverse('invite_redeem'), {'code': invite.code})

        self.assertRedirects(response, reverse('index'))
        self.user.refresh_from_db()
        invite.refresh_from_db()
        self.assertTrue(self.user.is_verified)
        self.assertEqual(invite.used_by, self.user)
        self.assertIsNotNone(invite.used_at)
        self.assertFalse(invite.is_active)

    def test_invalid_or_expired_code_has_single_generic_error(self):
        expired = InviteCode.objects.create(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

        for code in ('not-a-real-code', expired.code):
            with self.subTest(code=code):
                response = self.client.post(reverse('invite_redeem'), {'code': code})
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'Код недействителен.')
                self.assertNotContains(response, 'истёк')
                self.assertNotContains(response, 'использован')

    def test_used_code_cannot_verify_another_user(self):
        invite = InviteCode.objects.create()
        first_user = get_user_model().objects.create_user(
            username='first_invited_user',
            is_verified=False,
        )
        self.client.force_login(first_user)
        self.client.post(reverse('invite_redeem'), {'code': invite.code})

        self.client.force_login(self.user)
        response = self.client.post(reverse('invite_redeem'), {'code': invite.code})

        self.assertContains(response, 'Код недействителен.')
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_verified)

    def test_personal_link_remembers_code_before_authentication(self):
        invite = InviteCode.objects.create()
        self.client.logout()

        response = self.client.get(reverse('invite_link', args=[invite.code]))

        self.assertRedirects(response, reverse('index'))
        self.assertEqual(self.client.session['pending_invite_code'], invite.code)

    @override_settings(
        TELEGRAM_BOT_USERNAME='KruzhokTeamBot',
        TELEGRAM_ADMIN_IDS=frozenset({700000001}),
    )
    def test_app_admin_can_create_invitation(self):
        self.user.telegram_id = 700000001
        self.user.is_verified = True
        self.user.save(update_fields=['telegram_id', 'is_verified'])

        response = self.client.post(reverse('invite_create'))

        self.assertRedirects(response, reverse('invite_list'))
        invite = InviteCode.objects.get(created_by=self.user)
        self.assertTrue(invite.is_active)
        response = self.client.get(reverse('invite_list'))
        self.assertContains(response, invite.code)
        self.assertContains(
            response,
            f'https://t.me/KruzhokTeamBot?startapp=invite_{invite.code}',
        )

    @override_settings(
        TELEGRAM_BOT_USERNAME='',
        TELEGRAM_ADMIN_IDS=frozenset({700000001}),
    )
    def test_invite_link_falls_back_to_web_url_without_bot_username(self):
        self.user.telegram_id = 700000001
        self.user.is_verified = True
        self.user.save(update_fields=['telegram_id', 'is_verified'])
        invite = InviteCode.objects.create(created_by=self.user)

        response = self.client.get(reverse('invite_list'))

        self.assertContains(
            response,
            f'http://testserver{reverse("invite_link", args=[invite.code])}',
        )

    @override_settings(TELEGRAM_ADMIN_IDS=frozenset())
    def test_verified_non_admin_cannot_open_or_create_invitations(self):
        self.user.is_verified = True
        self.user.save(update_fields=['is_verified'])

        list_response = self.client.get(reverse('invite_list'))
        create_response = self.client.post(reverse('invite_create'))

        self.assertEqual(list_response.status_code, 403)
        self.assertEqual(create_response.status_code, 403)


class DevAccountSwitcherTests(TestCase):
    def setUp(self):
        self.current_user = get_user_model().objects.create_user(
            username='current',
            first_name='Текущий',
            is_verified=True,
        )
        self.other_user = get_user_model().objects.create_user(
            username='other',
            first_name='Другой',
            is_verified=True,
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
        self.user = get_user_model().objects.create_user(username='tester', password='pass', is_verified=True)
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

    def test_archived_stage_delete_removes_stage(self):
        stage = Stage.objects.create(
            project=self.project,
            name='Удаляемый',
            order=None,
            is_archived=True,
        )
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
        self.user = get_user_model().objects.create_user(username='tasker', password='pass', is_verified=True)
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
        response = self.client.post(
            reverse('task_status', args=[task.id]),
            {'status': Task.Status.DONE},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Выполнена')
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.DONE)

    def test_status_task_rejects_unknown_value(self):
        task = Task.objects.create(
            title='Task invalid status',
            project=self.project,
            creator=self.user,
        )

        response = self.client.post(
            reverse('task_status', args=[task.id]),
            {'status': 'not-a-status'},
        )

        self.assertEqual(response.status_code, 400)
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.NEW)


class ProjectCrudTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='owner', password='pass', is_verified=True)
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
        self.user = get_user_model().objects.create_user(username='memberuser', password='pass', is_verified=True)
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
        self.owner = user_model.objects.create_user(username='membership_owner', is_verified=True)
        self.admin = user_model.objects.create_user(username='membership_admin', is_verified=True)
        self.candidate = user_model.objects.create_user(username='membership_candidate', is_verified=True)
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
        shared_discussion = Discussion.objects.create(
            title='Existing working context',
            created_by=self.owner,
        )
        shared_discussion.participants.add(self.owner, self.candidate)

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

    @override_settings(TELEGRAM_ADMIN_IDS=frozenset({737320461}))
    def test_app_admin_can_remove_member_from_any_project(self):
        app_admin = get_user_model().objects.create_user(
            username='global_app_admin',
            telegram_id=737320461,
        )
        membership = ProjectMembership.objects.create(
            project=self.project,
            user=self.candidate,
            role=ProjectMembership.Role.MEMBER,
        )
        self.client.force_login(app_admin)

        detail_response = self.client.get(
            reverse('project_detail', args=[self.project.id])
        )
        delete_response = self.client.delete(
            reverse('project_member_delete', args=[membership.id])
        )

        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(delete_response.status_code, 200)
        self.assertFalse(
            ProjectMembership.objects.filter(pk=membership.pk).exists()
        )

    @override_settings(TELEGRAM_ADMIN_IDS=frozenset({737320461}))
    def test_app_admin_cannot_remove_project_owner(self):
        app_admin = get_user_model().objects.create_user(
            username='global_app_admin_owner_guard',
            telegram_id=737320461,
        )
        self.client.force_login(app_admin)

        response = self.client.delete(
            reverse('project_member_delete', args=[self.owner_membership.id])
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            ProjectMembership.objects.filter(pk=self.owner_membership.pk).exists()
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
        self.user = get_user_model().objects.create_user(username='owner', password='pass', is_verified=True)
        self.other = get_user_model().objects.create_user(username='other', password='pass', is_verified=True)
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
        self.owner = user_model.objects.create_user(username='access_owner', is_verified=True)
        self.admin = user_model.objects.create_user(username='access_admin', is_verified=True)
        self.member = user_model.objects.create_user(username='access_member', is_verified=True)
        self.outsider = user_model.objects.create_user(username='access_outsider', is_verified=True)

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

        status_response = self.client.post(
            reverse('task_status', args=[standalone.id]),
            {'status': Task.Status.IN_PROGRESS},
        )
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
        self.owner = user_model.objects.create_user(username='workflow_owner', is_verified=True)
        self.member = user_model.objects.create_user(username='workflow_member', is_verified=True)
        self.outsider = user_model.objects.create_user(username='workflow_outsider', is_verified=True)

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
        self.user = get_user_model().objects.create_user(username='commenter', password='pass', is_verified=True)
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
        other = get_user_model().objects.create_user(username='other2', password='pass', is_verified=True)
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
        Comment.objects.create(task=self.task, author=self.user, text='Первый')
        Comment.objects.create(task=self.task, author=self.user, text='Второй')
        response = self.client.get(reverse('task_detail', args=[self.task.id]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertTrue(content.index('Первый') < content.index('Второй'))


class ProfileTests(TestCase):
    def setUp(self):
        self.viewer = get_user_model().objects.create_user(
            username='profile_viewer',
            first_name='Зритель',
            is_verified=True,
        )
        self.profile_user = get_user_model().objects.create_user(
            username='profile_owner',
            telegram_username='profile_owner',
            first_name='Анна',
            last_name='Смирнова',
            telegram_id=612345678,
            photo_url='https://example.com/avatar.jpg',
            is_verified=True,
        )
        shared_discussion = Discussion.objects.create(
            title='Shared profile context',
            created_by=self.viewer,
        )
        shared_discussion.participants.add(self.viewer, self.profile_user)
        self.client.force_login(self.viewer)

    def test_profile_page_shows_name_username_and_avatar(self):
        response = self.client.get(
            reverse('profile_detail', args=[self.profile_user.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Анна Смирнова')
        self.assertContains(response, '@profile_owner')
        self.assertContains(response, self.profile_user.photo_url)
        self.assertNotContains(response, str(self.profile_user.telegram_id))

    def test_inactive_profile_is_not_exposed(self):
        self.profile_user.is_active = False
        self.profile_user.save(update_fields=['is_active'])

        response = self.client.get(
            reverse('profile_detail', args=[self.profile_user.id])
        )

        self.assertEqual(response.status_code, 404)


class AdminNavigationTests(TestCase):
    def test_built_in_global_admin_has_application_admin_access(self):
        app_admin = get_user_model().objects.create_user(
            username='built_in_global_admin',
            telegram_id=7836566387,
        )
        self.client.force_login(app_admin)

        response = self.client.get(reverse('invite_list'))

        self.assertEqual(response.status_code, 200)

    @override_settings(TELEGRAM_ADMIN_IDS=frozenset())
    def test_regular_user_does_not_see_invitation_section(self):
        user = get_user_model().objects.create_user(
            username='regular_navigation_user',
            is_verified=True,
        )
        self.client.force_login(user)

        response = self.client.get(reverse('index'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '>Пригласить<')

    @override_settings(TELEGRAM_ADMIN_IDS=frozenset({737320461}))
    def test_app_admin_sees_invitation_section_and_profile_link(self):
        app_admin = get_user_model().objects.create_user(
            username='navigation_app_admin',
            telegram_id=737320461,
        )
        self.client.force_login(app_admin)

        response = self.client.get(reverse('index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '>Пригласить<')
        self.assertContains(
            response,
            reverse('profile_detail', args=[app_admin.id]),
        )


class DiscussionAndMessageTests(TestCase):
    def setUp(self):
        self.user1 = get_user_model().objects.create_user(username='user1', first_name='Alice', is_verified=True)
        self.user2 = get_user_model().objects.create_user(username='user2', first_name='Bob', is_verified=True)
        self.user3 = get_user_model().objects.create_user(username='user3', first_name='Charlie', is_verified=True)
        self.task = Task.objects.create(title='Test Task', creator=self.user1)
        project = Project.objects.create(name='Shared workspace', owner=self.user1)
        ProjectMembership.objects.create(
            project=project,
            user=self.user1,
            role=ProjectMembership.Role.OWNER,
        )
        ProjectMembership.objects.create(
            project=project,
            user=self.user2,
            role=ProjectMembership.Role.MEMBER,
        )
        ProjectMembership.objects.create(
            project=project,
            user=self.user3,
            role=ProjectMembership.Role.MEMBER,
        )

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

    def test_discussion_form_uses_custom_task_dropdown(self):
        response = self.client.get(reverse('discussion_create'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-custom-select="true"')
        self.assertContains(response, 'Без привязки к задаче')

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

    def test_chat_drops_repeat_submit_and_deduplicates_racing_poll_response(self):
        discussion = Discussion.objects.create(
            title='Chat without duplicates',
            created_by=self.user1,
        )
        discussion.participants.add(self.user1)

        response = self.client.get(
            reverse('discussion_detail', args=[discussion.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'hx-sync="this:drop"')
        self.assertContains(response, 'hx-disabled-elt="find button[type=\'submit\']"')
        self.assertContains(response, 'data-dedupe-messages')

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

    @override_settings(TELEGRAM_ADMIN_IDS=frozenset({737320461}))
    def test_app_admin_sees_and_deletes_any_discussion(self):
        app_admin = get_user_model().objects.create_user(
            username='discussion_app_admin',
            telegram_id=737320461,
        )
        discussion = Discussion.objects.create(
            title='Admin visible chat',
            created_by=self.user2,
        )
        Message.objects.create(
            discussion=discussion,
            sender=self.user2,
            text='Will be deleted',
        )
        self.client.force_login(app_admin)

        list_response = self.client.get(reverse('discussion_list'))
        detail_response = self.client.get(
            reverse('discussion_detail', args=[discussion.id])
        )
        delete_response = self.client.delete(
            reverse('discussion_delete', args=[discussion.id])
        )

        self.assertContains(list_response, discussion.title)
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(delete_response.status_code, 200)
        self.assertFalse(Discussion.objects.filter(pk=discussion.pk).exists())
        self.assertFalse(
            Message.objects.filter(discussion_id=discussion.pk).exists()
        )

    @override_settings(TELEGRAM_ADMIN_IDS=frozenset())
    def test_regular_participant_cannot_delete_discussion(self):
        discussion = Discussion.objects.create(
            title='Protected from participant',
            created_by=self.user1,
        )

        response = self.client.delete(
            reverse('discussion_delete', args=[discussion.id])
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Discussion.objects.filter(pk=discussion.pk).exists())


class HtmxModalErrorRetargetTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(username='htmx_owner', is_verified=True)
        self.candidate = user_model.objects.create_user(username='htmx_candidate', is_verified=True)
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


class NotificationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='notification_user',
            telegram_id=987654321,
            first_name='Ирина',
            is_verified=True,
        )
        self.client.force_login(self.user)

    def deadline_input(self, value):
        return timezone.localtime(value).strftime('%Y-%m-%dT%H:%M')

    def task_form_data(
        self,
        *,
        title,
        deadline,
        status=Task.Status.NEW,
        assignee=None,
    ):
        return {
            'title': title,
            'description': '',
            'project': '',
            'stage': '',
            'assignee': (assignee or self.user).id,
            'deadline': self.deadline_input(deadline),
            'status': status,
        }

    @patch('core.telegram_notifications.send_telegram_message')
    def test_task_create_with_deadline_queues_notification(self, send_message):
        send_message.return_value = (True, '')
        deadline = (timezone.now() + timedelta(days=2)).replace(
            second=0,
            microsecond=0,
        )

        response = self.client.post(
            reverse('task_create'),
            self.task_form_data(
                title='Задача с дедлайном',
                deadline=deadline,
            ),
        )

        self.assertEqual(response.status_code, 200)
        task = Task.objects.get(title='Задача с дедлайном')
        notification = task.notifications.get(
            kind=Notification.Kind.DEADLINE_SET
        )
        self.assertEqual(notification.status, Notification.Status.PENDING)
        self.assertIsNone(notification.sent_at)
        self.assertTrue(
            OutboundMessage.objects.filter(notification=notification).exists()
        )
        send_message.assert_not_called()

    @patch('core.telegram_notifications.send_telegram_message')
    def test_task_create_survives_notification_failure(self, send_message):
        send_message.return_value = (
            False,
            "Forbidden: bot can't initiate conversation with a user",
        )
        deadline = (timezone.now() + timedelta(days=2)).replace(
            second=0,
            microsecond=0,
        )

        response = self.client.post(
            reverse('task_create'),
            self.task_form_data(
                title='Задача с ошибкой отправки',
                deadline=deadline,
            ),
        )

        self.assertEqual(response.status_code, 200)
        task = Task.objects.get(title='Задача с ошибкой отправки')
        notification = task.notifications.get(
            kind=Notification.Kind.DEADLINE_SET
        )
        process_outbound()
        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.FAILED)
        self.assertIn("can't initiate", notification.error_message)
        self.assertIsNone(notification.sent_at)

    @patch('core.telegram_notifications.send_telegram_message')
    def test_task_update_with_changed_deadline_resets_notifications(
        self,
        send_message,
    ):
        send_message.return_value = (True, '')
        old_deadline = (timezone.now() + timedelta(days=2)).replace(
            second=0,
            microsecond=0,
        )
        new_deadline = old_deadline + timedelta(days=1)
        task = Task.objects.create(
            title='Изменяемая задача',
            creator=self.user,
            assignee=self.user,
            deadline=old_deadline,
        )
        Notification.objects.create(
            task=task,
            recipient=self.user,
            kind=Notification.Kind.DEADLINE_SET,
            status=Notification.Status.SENT,
        )
        Notification.objects.create(
            task=task,
            recipient=self.user,
            kind=Notification.Kind.DEADLINE_APPROACHING,
        )
        Notification.objects.create(
            task=task,
            recipient=self.user,
            kind=Notification.Kind.DEADLINE_OVERDUE,
        )

        response = self.client.post(
            reverse('task_update', args=[task.id]),
            self.task_form_data(
                title=task.title,
                deadline=new_deadline,
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            task.notifications.filter(
                kind=Notification.Kind.DEADLINE_APPROACHING
            ).exists()
        )
        self.assertFalse(
            task.notifications.filter(
                kind=Notification.Kind.DEADLINE_OVERDUE
            ).exists()
        )
        deadline_notifications = task.notifications.filter(
            kind=Notification.Kind.DEADLINE_SET
        )
        self.assertEqual(deadline_notifications.count(), 1)
        self.assertEqual(
            deadline_notifications.get().status,
            Notification.Status.PENDING,
        )
        send_message.assert_not_called()
        process_outbound()
        self.assertEqual(
            deadline_notifications.get().status,
            Notification.Status.SENT,
        )
        send_message.assert_called_once()

    @patch('core.telegram_notifications.send_telegram_message')
    def test_task_update_without_deadline_change_does_not_notify_again(
        self,
        send_message,
    ):
        deadline = (timezone.now() + timedelta(days=2)).replace(
            second=0,
            microsecond=0,
        )
        task = Task.objects.create(
            title='Неизменяемый дедлайн',
            creator=self.user,
            assignee=self.user,
            deadline=deadline,
        )
        Notification.objects.create(
            task=task,
            recipient=self.user,
            kind=Notification.Kind.DEADLINE_SET,
            status=Notification.Status.SENT,
        )

        response = self.client.post(
            reverse('task_update', args=[task.id]),
            self.task_form_data(
                title='Новое название',
                deadline=deadline,
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            task.notifications.filter(
                kind=Notification.Kind.DEADLINE_SET
            ).count(),
            1,
        )
        send_message.assert_not_called()

    @patch('core.telegram_notifications.send_telegram_message')
    def test_check_deadlines_creates_one_overdue_notification(
        self,
        send_message,
    ):
        send_message.return_value = (True, '')
        task = Task.objects.create(
            title='Просроченная задача',
            creator=self.user,
            assignee=self.user,
            deadline=timezone.now() - timedelta(minutes=1),
        )

        call_command('check_deadlines', stdout=StringIO())
        call_command('check_deadlines', stdout=StringIO())

        self.assertEqual(
            task.notifications.filter(
                kind=Notification.Kind.DEADLINE_OVERDUE
            ).count(),
            1,
        )
        send_message.assert_called_once()

    @patch('core.telegram_notifications.send_telegram_message')
    def test_check_deadlines_notifies_only_approaching_task(
        self,
        send_message,
    ):
        send_message.return_value = (True, '')
        approaching = Task.objects.create(
            title='Скорый дедлайн',
            creator=self.user,
            assignee=self.user,
            deadline=timezone.now() + timedelta(hours=12),
        )
        distant = Task.objects.create(
            title='Дальний дедлайн',
            creator=self.user,
            assignee=self.user,
            deadline=timezone.now() + timedelta(days=3),
        )

        call_command('check_deadlines', stdout=StringIO())

        self.assertTrue(
            approaching.notifications.filter(
                kind=Notification.Kind.DEADLINE_APPROACHING
            ).exists()
        )
        self.assertFalse(distant.notifications.exists())
        send_message.assert_called_once()

    @patch('core.telegram_notifications.send_telegram_message')
    def test_check_deadlines_skips_completed_task(self, send_message):
        task = Task.objects.create(
            title='Готовая задача',
            creator=self.user,
            assignee=self.user,
            deadline=timezone.now() - timedelta(days=1),
            status=Task.Status.DONE,
        )

        call_command('check_deadlines', stdout=StringIO())

        self.assertFalse(task.notifications.exists())
        send_message.assert_not_called()

    @patch('core.telegram_notifications.send_telegram_message')
    def test_notify_falls_back_to_creator(self, send_message):
        send_message.return_value = (True, '')
        task = Task.objects.create(
            title='Задача без исполнителя',
            creator=self.user,
            deadline=timezone.now() + timedelta(hours=12),
        )

        notification = notify(
            task,
            Notification.Kind.DEADLINE_APPROACHING,
        )

        self.assertEqual(notification.recipient, self.user)
        self.assertTrue(
            OutboundMessage.objects.filter(
                notification=notification,
                recipient=self.user,
            ).exists()
        )
        send_message.assert_not_called()

    @override_settings(
        TELEGRAM_NOTIFICATION_MAX_ATTEMPTS=3,
        TELEGRAM_NOTIFICATION_RETRY_SECONDS=0,
    )
    @patch('core.telegram_notifications.send_telegram_message')
    def test_failed_notification_is_retried_by_deadline_worker(
        self,
        send_message,
    ):
        send_message.side_effect = [
            (False, 'Temporary Bot API failure'),
            (True, ''),
        ]
        task = Task.objects.create(
            title='Повторная отправка',
            creator=self.user,
            assignee=self.user,
            deadline=timezone.now() + timedelta(days=2),
        )
        notification = notify(task, Notification.Kind.DEADLINE_SET)
        self.assertEqual(notification.status, Notification.Status.PENDING)
        process_outbound()
        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.FAILED)
        self.assertEqual(notification.attempt_count, 1)

        call_command('check_deadlines', stdout=StringIO())

        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.SENT)
        self.assertEqual(notification.attempt_count, 2)
        self.assertIsNone(notification.next_retry_at)
        self.assertEqual(send_message.call_count, 2)

    @patch('core.telegram_notifications.send_telegram_message')
    def test_reassigning_task_resets_notifications_for_new_recipient(
        self,
        send_message,
    ):
        send_message.return_value = (True, '')
        new_assignee = get_user_model().objects.create_user(
            username='new_notification_recipient',
            telegram_id=876543210,
            is_verified=True,
        )
        Task.objects.create(
            title='Existing shared work',
            creator=self.user,
            assignee=new_assignee,
        )
        deadline = (timezone.now() + timedelta(days=2)).replace(
            second=0,
            microsecond=0,
        )
        task = Task.objects.create(
            title='Переназначаемая задача',
            creator=self.user,
            assignee=self.user,
            deadline=deadline,
        )
        Notification.objects.create(
            task=task,
            recipient=self.user,
            kind=Notification.Kind.DEADLINE_SET,
            status=Notification.Status.SENT,
            attempt_count=1,
        )
        Notification.objects.create(
            task=task,
            recipient=self.user,
            kind=Notification.Kind.DEADLINE_APPROACHING,
            status=Notification.Status.SENT,
            attempt_count=1,
        )

        response = self.client.post(
            reverse('task_update', args=[task.id]),
            self.task_form_data(
                title=task.title,
                deadline=deadline,
                assignee=new_assignee,
            ),
        )

        self.assertEqual(response.status_code, 200)
        notifications = task.notifications.all()
        self.assertEqual(notifications.count(), 2)
        replacement = notifications.get(kind=Notification.Kind.DEADLINE_SET)
        self.assertEqual(replacement.kind, Notification.Kind.DEADLINE_SET)
        self.assertEqual(replacement.recipient, new_assignee)
        self.assertEqual(replacement.status, Notification.Status.PENDING)
        process_outbound()
        replacement.refresh_from_db()
        self.assertEqual(replacement.status, Notification.Status.SENT)
        self.assertEqual(send_message.call_count, 2)

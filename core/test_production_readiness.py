from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .access import redeem_invite_code
from .models import (
    AuditLog,
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
from .rate_limit import allow_request
from .services import archive_stage, place_stage, restore_stage
from .telegram_notifications import notify, process_outbound


class PublicOperationalEndpointsTests(TestCase):
    def test_readiness_checks_database(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse('readiness'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ready'})
        self.assertFalse(
            any('integrity_check' in query['sql'].lower() for query in queries)
        )

    def test_legal_pages_are_public(self):
        privacy = self.client.get(reverse('privacy_policy'))
        terms = self.client.get(reverse('terms_of_use'))

        self.assertContains(privacy, 'Политика конфиденциальности')
        self.assertContains(terms, 'Условия использования')


class InviteOnboardingTests(TestCase):
    def test_redeeming_project_invite_adds_exact_role_once(self):
        owner = get_user_model().objects.create_user(
            username='invite_owner',
            is_verified=True,
        )
        newcomer = get_user_model().objects.create_user(username='invite_new')
        project = Project.objects.create(name='Invite project', owner=owner)
        invite = InviteCode.objects.create(
            created_by=owner,
            project=project,
            project_role=ProjectMembership.Role.ADMIN,
        )

        self.assertTrue(redeem_invite_code(newcomer, invite.code))
        self.assertFalse(redeem_invite_code(newcomer, invite.code))
        membership = ProjectMembership.objects.get(
            project=project,
            user=newcomer,
        )
        self.assertEqual(membership.role, ProjectMembership.Role.ADMIN)
        newcomer.refresh_from_db()
        self.assertTrue(newcomer.is_verified)


class PrivacyAndAccountLifecycleTests(TestCase):
    def setUp(self):
        self.viewer = get_user_model().objects.create_user(
            username='privacy_viewer',
            is_verified=True,
        )
        self.target = get_user_model().objects.create_user(
            username='tg_123456',
            telegram_username='public_name',
            telegram_id=123456,
            first_name='Private',
            email='private@example.com',
            is_verified=True,
        )
        self.client.force_login(self.viewer)

    def test_unrelated_profile_is_forbidden(self):
        response = self.client.get(
            reverse('profile_detail', args=[self.target.pk])
        )

        self.assertEqual(response.status_code, 403)

    def test_shared_discussion_allows_profile(self):
        discussion = Discussion.objects.create(
            title='Shared',
            created_by=self.viewer,
        )
        discussion.participants.add(self.viewer, self.target)

        response = self.client.get(
            reverse('profile_detail', args=[self.target.pk])
        )

        self.assertContains(response, '@public_name')
        self.assertNotContains(response, str(self.target.telegram_id))

    def test_anonymize_removes_personal_identifiers(self):
        self.target.anonymize()
        self.target.refresh_from_db()

        self.assertFalse(self.target.is_active)
        self.assertIsNone(self.target.telegram_id)
        self.assertEqual(self.target.telegram_username, '')
        self.assertEqual(self.target.first_name, '')
        self.assertEqual(self.target.email, '')
        self.assertIsNotNone(self.target.anonymized_at)


class ArchiveAndOrderingTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            username='archive_owner',
            is_verified=True,
        )
        self.admin = get_user_model().objects.create_user(
            username='archive_admin',
            is_verified=True,
        )
        self.project = Project.objects.create(name='Ordered', owner=self.owner)
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

    def test_stage_reordering_archive_and_restore_stays_dense(self):
        first = Stage.objects.create(project=self.project, name='First')
        place_stage(first, 1)
        third = Stage.objects.create(project=self.project, name='Third')
        place_stage(third, 2)
        second = Stage.objects.create(project=self.project, name='Second')
        place_stage(second, 2)

        self.assertEqual(
            list(
                self.project.stages.filter(is_archived=False)
                .order_by('order')
                .values_list('name', 'order')
            ),
            [('First', 1), ('Second', 2), ('Third', 3)],
        )
        archive_stage(second)
        self.assertEqual(
            list(
                self.project.stages.filter(is_archived=False)
                .order_by('order')
                .values_list('order', flat=True)
            ),
            [1, 2],
        )
        restore_stage(second)
        self.assertEqual(
            list(
                self.project.stages.filter(is_archived=False)
                .order_by('order')
                .values_list('order', flat=True)
            ),
            [1, 2, 3],
        )

    def test_active_project_cannot_be_permanently_deleted(self):
        self.client.force_login(self.owner)

        response = self.client.delete(
            reverse('project_delete', args=[self.project.pk])
        )

        self.assertEqual(response.status_code, 409)
        self.assertTrue(Project.objects.filter(pk=self.project.pk).exists())

    def test_only_owner_can_delete_archived_project(self):
        self.project.is_archived = True
        self.project.save(update_fields=['is_archived'])
        self.client.force_login(self.admin)
        forbidden = self.client.delete(
            reverse('project_delete', args=[self.project.pk])
        )
        self.client.force_login(self.owner)
        deleted = self.client.delete(
            reverse('project_delete', args=[self.project.pk])
        )

        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(Project.objects.filter(pk=self.project.pk).exists())


class DurableNotificationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='tg_987',
            telegram_id=987,
            is_verified=True,
        )
        self.task = Task.objects.create(
            title='Durable delivery',
            creator=self.user,
            assignee=self.user,
            deadline=timezone.now() + timedelta(days=1),
        )

    @patch('core.telegram_notifications.send_telegram_message')
    def test_web_side_only_queues_then_worker_delivers(self, send_message):
        send_message.return_value = (True, '')

        notification = notify(self.task, Notification.Kind.DEADLINE_SET)

        self.assertEqual(notification.status, Notification.Status.PENDING)
        self.assertEqual(OutboundMessage.objects.count(), 1)
        send_message.assert_not_called()
        sent, failed = process_outbound()
        notification.refresh_from_db()
        self.assertEqual((sent, failed), (1, 0))
        self.assertEqual(notification.status, Notification.Status.SENT)
        send_message.assert_called_once()

    def test_disabled_preference_does_not_create_dead_queue_item(self):
        self.user.notify_deadlines = False
        self.user.save(update_fields=['notify_deadlines'])

        result = notify(self.task, Notification.Kind.DEADLINE_SET)

        self.assertIsNone(result)
        self.assertFalse(Notification.objects.exists())
        self.assertFalse(OutboundMessage.objects.exists())


class AuditAndMessageLifecycleTests(TestCase):
    def setUp(self):
        self.author = get_user_model().objects.create_user(
            username='message_author',
            is_verified=True,
        )
        self.other = get_user_model().objects.create_user(
            username='message_other',
            is_verified=True,
        )
        self.discussion = Discussion.objects.create(
            title='Lifecycle',
            created_by=self.author,
        )
        self.discussion.participants.add(self.author, self.other)
        self.message = Message.objects.create(
            discussion=self.discussion,
            sender=self.author,
            text='Before',
        )

    def test_author_can_edit_and_delete_message_with_audit(self):
        self.client.force_login(self.author)
        updated = self.client.post(
            reverse('message_update', args=[self.message.pk]),
            {'text': 'After'},
        )
        deleted = self.client.delete(
            reverse('message_delete', args=[self.message.pk])
        )

        self.assertContains(updated, 'After')
        self.assertEqual(deleted.status_code, 200)
        self.assertTrue(
            AuditLog.objects.filter(
                actor=self.author,
                action='message.update',
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                actor=self.author,
                action='message.delete',
            ).exists()
        )

    def test_other_participant_cannot_edit_or_delete_message(self):
        self.client.force_login(self.other)

        updated = self.client.post(
            reverse('message_update', args=[self.message.pk]),
            {'text': 'Tampered'},
        )
        deleted = self.client.delete(
            reverse('message_delete', args=[self.message.pk])
        )

        self.assertEqual(updated.status_code, 403)
        self.assertEqual(deleted.status_code, 403)
        self.message.refresh_from_db()
        self.assertEqual(self.message.text, 'Before')


class RateLimitTests(TestCase):
    def test_authenticated_users_do_not_share_one_bucket(self):
        first = get_user_model().objects.create_user(username='rate_first')
        second = get_user_model().objects.create_user(username='rate_second')
        factory = RequestFactory()
        request_one = factory.get('/', REMOTE_ADDR='127.0.0.1')
        request_one.user = first
        request_two = factory.get('/', REMOTE_ADDR='127.0.0.1')
        request_two.user = second

        self.assertTrue(
            allow_request(
                request_one,
                'test',
                limit=1,
                window_seconds=60,
            )
        )
        self.assertFalse(
            allow_request(
                request_one,
                'test',
                limit=1,
                window_seconds=60,
            )
        )
        self.assertTrue(
            allow_request(
                request_two,
                'test',
                limit=1,
                window_seconds=60,
            )
        )


@override_settings(TELEGRAM_ADMIN_IDS=frozenset({777}))
class OperationalStatusTests(TestCase):
    def setUp(self):
        self.regular = get_user_model().objects.create_user(
            username='ops_regular',
            telegram_id=778,
            is_verified=True,
        )
        self.admin = get_user_model().objects.create_user(
            username='ops_admin',
            telegram_id=777,
            is_verified=True,
        )

    def test_only_app_admin_can_read_status(self):
        self.client.force_login(self.regular)
        denied = self.client.get(reverse('operational_status'))
        self.client.force_login(self.admin)
        allowed = self.client.get(reverse('operational_status'))

        self.assertEqual(denied.status_code, 403)
        self.assertEqual(allowed.status_code, 200)
        self.assertIn('outbound', allowed.json())

    @override_settings(APP_RELEASE_SHA='abc123')
    def test_status_exposes_release_and_degraded_queue(self):
        OutboundMessage.objects.create(
            recipient=self.admin,
            kind=Notification.Kind.MESSAGE_ADDED,
            dedupe_key='ops:exhausted',
            text='Failed',
            status=OutboundMessage.Status.FAILED,
            attempt_count=5,
        )
        self.client.force_login(self.admin)

        payload = self.client.get(reverse('operational_status')).json()

        self.assertEqual(payload['release_sha'], 'abc123')
        self.assertEqual(payload['status'], 'degraded')
        self.assertEqual(payload['outbound']['exhausted'], 1)

    def test_admin_can_retry_and_cancel_exhausted_messages(self):
        retried = OutboundMessage.objects.create(
            recipient=self.admin,
            kind=Notification.Kind.MESSAGE_ADDED,
            dedupe_key='ops:retry',
            text='Retry',
            status=OutboundMessage.Status.FAILED,
            attempt_count=5,
            error_message='Network error',
        )
        cancelled = OutboundMessage.objects.create(
            recipient=self.admin,
            kind=Notification.Kind.MESSAGE_ADDED,
            dedupe_key='ops:cancel',
            text='Cancel',
            status=OutboundMessage.Status.FAILED,
            attempt_count=5,
        )
        self.client.force_login(self.admin)

        queue = self.client.get(reverse('outbound_queue'))
        retry_response = self.client.post(
            reverse('outbound_retry', args=[retried.pk])
        )
        cancel_response = self.client.post(
            reverse('outbound_cancel', args=[cancelled.pk])
        )

        self.assertContains(queue, 'Network error')
        self.assertEqual(retry_response.status_code, 302)
        self.assertEqual(cancel_response.status_code, 302)
        retried.refresh_from_db()
        cancelled.refresh_from_db()
        self.assertEqual(retried.status, OutboundMessage.Status.PENDING)
        self.assertEqual(retried.attempt_count, 0)
        self.assertEqual(retried.error_message, '')
        self.assertEqual(cancelled.status, OutboundMessage.Status.CANCELLED)
        self.assertTrue(
            AuditLog.objects.filter(action='outbound.retry').exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(action='outbound.cancel').exists()
        )

    def test_regular_user_cannot_open_queue(self):
        self.client.force_login(self.regular)

        response = self.client.get(reverse('outbound_queue'))

        self.assertEqual(response.status_code, 403)


class ThemeAndListUxTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='ux_user',
            telegram_id=901,
            is_verified=True,
        )
        self.project = Project.objects.create(
            name='UX project',
            owner=self.user,
        )
        ProjectMembership.objects.create(
            project=self.project,
            user=self.user,
            role=ProjectMembership.Role.OWNER,
        )
        self.client.force_login(self.user)

    def test_profile_saves_and_applies_explicit_dark_theme(self):
        response = self.client.post(
            reverse('profile_settings'),
            {
                'theme_preference': 'dark',
                'timezone': 'Europe/Moscow',
                'notify_deadlines': 'on',
                'notify_assignments': 'on',
                'notify_comments': 'on',
                'notify_messages': 'on',
            },
            follow=True,
        )

        self.user.refresh_from_db()
        self.assertEqual(self.user.theme_preference, 'dark')
        self.assertContains(response, 'data-theme="dark"')
        self.assertContains(response, 'theme-picker')

    def test_task_quick_filter_and_compact_search(self):
        overdue = Task.objects.create(
            title='Overdue task',
            project=self.project,
            creator=self.user,
            deadline=timezone.now() - timedelta(hours=1),
        )
        Task.objects.create(
            title='Upcoming task',
            project=self.project,
            creator=self.user,
            deadline=timezone.now() + timedelta(days=2),
        )

        response = self.client.get(
            reverse('task_list'),
            {'quick': 'overdue'},
        )

        self.assertContains(response, overdue.title)
        self.assertNotContains(response, 'Upcoming task')
        self.assertContains(response, 'toolbar__search--tasks')
        self.assertContains(response, 'search-field')
        self.assertContains(response, 'entity-actions-menu')

    def test_project_cards_show_task_progress_and_member_count(self):
        Task.objects.create(
            title='Done',
            project=self.project,
            creator=self.user,
            status=Task.Status.DONE,
        )
        Task.objects.create(
            title='Open',
            project=self.project,
            creator=self.user,
        )

        response = self.client.get(reverse('project_list'))

        self.assertContains(response, '1 из 2')
        self.assertContains(response, 'Участников: 1')
        self.assertContains(response, 'project-card__progress')


class AtomicBusinessOperationTests(TestCase):
    def setUp(self):
        self.author = get_user_model().objects.create_user(
            username='atomic_author',
            telegram_id=1001,
            is_verified=True,
        )
        self.recipient = get_user_model().objects.create_user(
            username='atomic_recipient',
            telegram_id=1002,
            is_verified=True,
        )
        self.client.force_login(self.author)

    @patch('core.views.notify', side_effect=RuntimeError('queue unavailable'))
    def test_task_and_audit_roll_back_when_enqueue_fails(self, _notify):
        with self.assertRaises(RuntimeError):
            self.client.post(
                reverse('task_create'),
                {
                    'title': 'Atomic task',
                    'description': '',
                    'project': '',
                    'stage': '',
                    'assignee': '',
                    'deadline': (
                        timezone.now() + timedelta(days=1)
                    ).strftime('%Y-%m-%dT%H:%M'),
                    'status': Task.Status.NEW,
                },
            )

        self.assertFalse(Task.objects.filter(title='Atomic task').exists())
        self.assertFalse(
            AuditLog.objects.filter(action='task.create').exists()
        )

    @patch(
        'core.views.enqueue_outbound',
        side_effect=RuntimeError('queue unavailable'),
    )
    def test_message_and_audit_roll_back_when_enqueue_fails(self, _enqueue):
        discussion = Discussion.objects.create(
            title='Atomic chat',
            created_by=self.author,
        )
        discussion.participants.add(self.author, self.recipient)

        with self.assertRaises(RuntimeError):
            self.client.post(
                reverse('message_create', args=[discussion.pk]),
                {'text': 'Atomic message'},
            )

        self.assertFalse(
            Message.objects.filter(
                discussion=discussion,
                text='Atomic message',
            ).exists()
        )
        self.assertFalse(
            AuditLog.objects.filter(action='message.create').exists()
        )

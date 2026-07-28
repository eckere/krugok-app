from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
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
        response = self.client.get(reverse('readiness'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ready'})

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
    def test_only_app_admin_can_read_status(self):
        regular = get_user_model().objects.create_user(
            username='ops_regular',
            telegram_id=778,
            is_verified=True,
        )
        admin = get_user_model().objects.create_user(
            username='ops_admin',
            telegram_id=777,
            is_verified=True,
        )
        self.client.force_login(regular)
        denied = self.client.get(reverse('operational_status'))
        self.client.force_login(admin)
        allowed = self.client.get(reverse('operational_status'))

        self.assertEqual(denied.status_code, 403)
        self.assertEqual(allowed.status_code, 200)
        self.assertIn('outbound', allowed.json())

import hashlib
import hmac
import time

from django.test import SimpleTestCase

from .telegram_auth import LoginWidgetValidationError, validate_login_widget_data


class TelegramLoginWidgetValidationTests(SimpleTestCase):
    bot_token = 'test-bot-token'

    def signed_widget_data(self):
        data = {
            'id': '987654321',
            'first_name': 'Ирина',
            'last_name': 'Тестова',
            'username': 'telegram_user',
            'photo_url': 'https://example.com/avatar.jpg',
            'auth_date': str(int(time.time())),
        }
        data_check_string = '\n'.join(
            f'{key}={value}' for key, value in sorted(data.items())
        )
        secret_key = hashlib.sha256(self.bot_token.encode('utf-8')).digest()
        data['hash'] = hmac.new(
            secret_key,
            data_check_string.encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()
        return data

    def test_valid_signed_widget_data_is_accepted(self):
        user = validate_login_widget_data(
            self.signed_widget_data(),
            bot_token=self.bot_token,
        )

        self.assertEqual(user['id'], 987654321)
        self.assertEqual(user['username'], 'telegram_user')
        self.assertEqual(user['first_name'], 'Ирина')

    def test_changed_widget_data_is_rejected(self):
        data = self.signed_widget_data()
        data['username'] = 'attacker'

        with self.assertRaises(LoginWidgetValidationError):
            validate_login_widget_data(data, bot_token=self.bot_token)

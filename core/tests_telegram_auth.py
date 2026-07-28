import hashlib
import hmac
import time

from django.test import SimpleTestCase

from .telegram_auth import (
    LoginWidgetValidationError,
    extract_invite_code,
    extract_invite_code_from_start_param,
    validate_login_widget_data,
)


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


class TelegramStartParamTests(SimpleTestCase):
    def test_start_param_is_parsed(self):
        self.assertEqual(
            extract_invite_code_from_start_param('invite_abcDEF123_-'),
            'abcDEF123_-',
        )

    def test_invite_code_is_extracted(self):
        self.assertEqual(
            extract_invite_code(
                'auth_date=1&start_param=invite_abcDEF123_-&user=%7B%7D'
            ),
            'abcDEF123_-',
        )

    def test_unrecognized_or_unsafe_value_is_ignored(self):
        for init_data in (
            'start_param=other_abc',
            'start_param=invite_abc%2Fdef',
            'start_param=invite_',
        ):
            with self.subTest(init_data=init_data):
                self.assertIsNone(extract_invite_code(init_data))

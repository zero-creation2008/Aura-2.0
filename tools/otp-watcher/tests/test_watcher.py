import unittest
from watcher import extract_code, service_name, notify_telegram

class WatcherTests(unittest.TestCase):
    def test_extract_with_context(self):
        self.assertEqual(extract_code('Instagram verification code', 'Your code is 123456.'), '123456')

    def test_ignores_unrelated_numbers(self):
        self.assertIsNone(extract_code('Invoice 123456', 'This is your bill'))

    def test_telegram_message_contains_no_otp(self):
        from unittest.mock import patch
        with patch('watcher.send_telegram_message') as send:
            notify_telegram('Instagram', 'https://mail.google.com/mail/u/0/#all/demo')
            message = send.call_args.args[0]
            self.assertIn('Instagram', message)
            self.assertIn('mail.google.com', message)
            self.assertNotIn('123456', message)

    def test_service(self):
        self.assertEqual(service_name('Security <security@mail.instagram.com>', 'Your code'), 'Instagram')

if __name__ == '__main__':
    unittest.main()

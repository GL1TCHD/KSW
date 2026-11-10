from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestDurationDisplayLanguage(TransactionCase):
    """The duration is read in the reader's language, not frozen in the writer's.

    Core stores hr.leave.duration_display, so whoever triggered the compute
    (a cron, admin) fixed the language for everybody: Arabic users saw "9 days".
    """

    def test_same_leave_reads_in_each_language(self):
        if not self.env['res.lang']._lang_get('ar_001'):
            self.skipTest('Arabic not installed')
        leave_type = self.env['hr.leave.type'].search([('request_unit', '=', 'day')], limit=1)
        self.assertTrue(leave_type)
        leave = self.env['hr.leave'].new({'holiday_status_id': leave_type.id, 'number_of_days': 9})
        english = leave.with_context(lang='en_US').duration_display
        arabic = leave.with_context(lang='ar_001').duration_display
        self.assertEqual(english, '9 days')
        self.assertIn('أيام', arabic)

    def test_not_stored(self):
        self.assertFalse(self.env['hr.leave']._fields['duration_display'].store)

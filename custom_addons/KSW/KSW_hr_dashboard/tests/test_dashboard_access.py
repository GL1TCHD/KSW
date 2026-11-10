import json

from odoo.tests import HttpCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestDashboardAccess(HttpCase):
    """Every role a dashboard is shared with must be able to open it.

    The original browser test ran as admin, who can read everything, so it
    could not see that HR roles got a 403 for the whole dashboard (core counts
    main_data_model_ids as the viewer) and could never read the payroll chart.
    """

    def _figure_models(self, dashboard):
        data = json.loads(dashboard.spreadsheet_data)
        return {
            figure['data']['metaData']['resModel']
            for sheet in data['sheets'] for figure in sheet.get('figures', [])
            if figure.get('data', {}).get('metaData', {}).get('resModel')
        }

    def test_each_audience_opens_its_dashboards(self):
        dashboards = (self.env.ref('KSW_hr_dashboard.spreadsheet_dashboard_ksw_hr')
                      | self.env.ref('KSW_hr_dashboard.spreadsheet_dashboard_ksw_payroll'))
        for dashboard in dashboards:
            self.assertFalse(dashboard.main_data_model_ids, dashboard.name)
            models = self._figure_models(dashboard)
            self.assertTrue(models)
            for group in dashboard.group_ids:
                xmlid = group.get_external_id()[group.id]
                login = 'dash_%s_%s' % (dashboard.id, group.id)
                user = new_test_user(self.env, login=login, password=login + 'Pw1!',
                                     groups='base.group_user,' + xmlid)
                for model in models:
                    self.assertTrue(self.env[model].with_user(user).has_access('read'),
                                    f'{xmlid} sees "{dashboard.name}" but cannot read {model}')
                self.authenticate(login, login + 'Pw1!')
                response = self.url_open('/spreadsheet/dashboard/data/%d' % dashboard.id)
                self.assertEqual(response.status_code, 200, f'{xmlid} -> {dashboard.name}')

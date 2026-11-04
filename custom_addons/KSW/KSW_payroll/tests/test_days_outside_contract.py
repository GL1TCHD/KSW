# -*- coding: utf-8 -*-
"""Days of the month outside every contract are not paid.

KSWCO SLIP/14977 (Oct 2026): hired 15 Oct, attendance sheet 15–31 Oct all
attended, payslip paid the whole October wage. get_worked_day_lines clipped
its window to the contract dates, so 1–14 Oct were neither attended nor
absent and nothing deducted them from BASIC = contract.wage.

Deductible base = wage + travel + mobile + other = 6000 + 500 = 6500.
"""
from datetime import date

from odoo.tests.common import TransactionCase


class TestDaysOutsideContract(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.struct = cls.env.ref('om_hr_payroll.structure_base')
        cls.d_from = date(2026, 3, 1)
        cls.d_to = date(2026, 3, 31)
        cls.daily = 6500.0 / 31

    def _employee(self, start, end=False, sheet=True):
        emp = self.env['hr.employee'].create({
            'name': f'Outside Contract {start}',
            'x_is_attendance_sheet': sheet,
        })
        version = emp.current_version_id
        version.write({
            'date_version': start,
            'contract_date_start': start,
            'contract_date_end': end,
            'wage': 6000.0, 'travel_allowance': 500.0,
            'mobile_allowance': 0.0, 'other_allowance': 0.0,
            'hra': 1500.0, 'struct_id': self.struct.id,
        })
        emp._compute_current_version_id()
        return emp, version

    def _confirm_sheet(self, emp):
        sheet = self.env['ksw.attendance.sheet'].sudo().create({
            'employee_id': emp.id, 'month': '3', 'year': 2026,
        })
        sheet.action_supervisor_confirm()
        return sheet

    def _worked_days(self, emp):
        Payslip = self.env['hr.payslip']
        versions = self.env['hr.version'].browse(
            Payslip.get_versions(emp, self.d_from, self.d_to))
        vals = Payslip.get_worked_day_lines(versions, self.d_from, self.d_to)
        return {w['code']: w for w in vals}

    def test_mid_month_hire_is_docked_for_days_before_joining(self):
        emp, _version = self._employee(date(2026, 3, 15))
        sheet = self._confirm_sheet(emp)
        self.assertEqual(min(sheet.line_ids.mapped('date')), date(2026, 3, 15))

        by_code = self._worked_days(emp)

        self.assertEqual(by_code['ATT_ABS']['number_of_days'], 14)
        self.assertEqual(by_code['ATT_DED']['amount'], round(self.daily * 14))
        self.assertIn('14', by_code['ATT_ABS']['name'])

    def test_contract_ended_mid_month_is_docked_after_the_end(self):
        emp, _version = self._employee(date(2025, 1, 1), date(2026, 3, 20))

        by_code = self._worked_days(emp)

        # 21–31 Mar outside the contract; the sheet was never confirmed, so
        # 1–20 Mar are absent too (unconfirmed sheet = zero attendance).
        self.assertEqual(by_code['ATT_ABS']['number_of_days'], 31)
        self.assertEqual(self.env['hr.payslip']._days_outside_contract(
            emp, self.d_from, self.d_to), 11)

    def test_renewal_without_a_gap_loses_nothing(self):
        """KSWCO SARFARAZ ELAHI: old contract to 28 Sep, new from 29 Sep."""
        emp, old = self._employee(date(2014, 4, 1), date(2026, 3, 28))
        old.copy({
            'date_version': date(2026, 3, 29),
            'contract_date_start': date(2026, 3, 29),
            'contract_date_end': False,
        })
        self.assertEqual(self.env['hr.payslip']._days_outside_contract(
            emp, self.d_from, self.d_to), 0)

    def test_renewal_with_a_gap_is_docked_for_the_gap(self):
        emp, old = self._employee(date(2014, 4, 1), date(2026, 3, 9))
        old.copy({
            'date_version': date(2026, 3, 15),
            'contract_date_start': date(2026, 3, 15),
            'contract_date_end': False,
        })
        self.assertEqual(self.env['hr.payslip']._days_outside_contract(
            emp, self.d_from, self.d_to), 5)

    def test_full_month_employee_is_not_docked(self):
        emp, _version = self._employee(date(2025, 1, 1))
        self._confirm_sheet(emp)

        by_code = self._worked_days(emp)

        self.assertNotIn('ATT_ABS', by_code)

    def test_biometric_mid_month_hire_is_docked_too(self):
        emp, _version = self._employee(date(2026, 3, 15), sheet=False)

        self.assertEqual(self.env['hr.payslip']._days_outside_contract(
            emp, self.d_from, self.d_to), 14)
        by_code = self._worked_days(emp)
        self.assertGreaterEqual(by_code['ATT_ABS']['number_of_days'], 14)

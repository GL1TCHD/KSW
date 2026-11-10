from odoo import api, fields, models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    # Whose salary or commission the line is, so an entry of 170 lines can be
    # filtered and grouped by person or department like any journal item.
    # Plain fields, written when the entry is booked: the department is the
    # one he worked in that month, not wherever he is transferred later.
    x_employee_id = fields.Many2one(
        'hr.employee', string='Employee', index='btree_not_null', readonly=True,
        ondelete='set null')
    x_department_id = fields.Many2one(
        'hr.department', string='Department', index='btree_not_null', readonly=True,
        ondelete='set null')

    @api.model
    def _ksw_employee_vals(self, employee):
        employee = employee.sudo()
        return {'x_employee_id': employee.id or False,
                'x_department_id': employee.department_id.id or False}

    @api.model
    def _ksw_employee_by_label(self, employees):
        """``label -> employee`` matcher for lines that only name him.

        Commission voucher rows (and entries booked before these fields)
        carry the employee only as his BAS label inside the line label. The
        longest label found as a whole run of words wins, so "AHMED ALI" does
        not claim "MOHAMMED AHMED ALI"'s lines; two employees with the same
        label are left unmatched rather than guessed.
        """
        Journal = self.env['ksw.bas.journal']
        by_who = {}
        for employee in employees.sudo():
            who = ' '.join((Journal.employee_label(employee) or '').split())
            if who:
                by_who.setdefault(who, self.env['hr.employee'])
                by_who[who] |= employee
        candidates = sorted(by_who.items(), key=lambda kv: -len(kv[0]))

        def match(label):
            text = ' %s ' % ' '.join((label or '').split())
            for who, found in candidates:
                if ' %s ' % who in text:
                    return found if len(found) == 1 else self.env['hr.employee']
            return self.env['hr.employee']
        return match

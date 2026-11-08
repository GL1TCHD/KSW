from odoo import api, fields, models


class ResourceCalendar(models.Model):
    _inherit = 'resource.calendar'

    # Schedules whose Saturday is a short shift (<8h); the rest of the 8h day
    # is a weekend extension of Friday (see KSW_payroll
    # hr.payslip._saturday_weekend_extension).  Set on install/
    # upgrade via the data <function> call.  Admins can also tick the boolean
    # manually on any other schedule.
    _SATURDAY_SHORT_OVERTIME_NAMES = (
        'Standard 44 hours/week',
        'Abdullah Mutawa Special Shift',
    )

    @api.model
    def _ksw_set_saturday_short_overtime_defaults(self):
        """Flag the known short-Saturday schedules by name (idempotent, only
        sets True — never clears an admin's choice)."""
        cals = self.search([
            ('name', 'in', list(self._SATURDAY_SHORT_OVERTIME_NAMES)),
            ('x_saturday_short_overtime', '=', False),
        ])
        if cals:
            cals.write({'x_saturday_short_overtime': True})

    is_temp_schedule = fields.Boolean(
        string='Is temporary Schedule',
        help='Indicates if this calendar is a temporary work schedule for employees',
    )

    x_saturday_required = fields.Boolean(
        string='Saturday Required (Deduct + Overtime Offset)',
        help='When set, Saturday is treated as a required workday for '
             'attendance-sheet purposes even if it has no schedule line in '
             'this calendar. Absences are deducted normally and an equal '
             'amount is credited back as Saturday overtime on the payslip.',
    )

    x_saturday_short_overtime = fields.Boolean(
        string='Short Saturday (Weekend Extension)',
        help='When set, Saturday is a short shift (<8h) and the rest of the '
             '8h day is a weekend extension of Friday: it is never deducted. '
             'An absent Saturday loses only its scheduled hours while '
             'Thursday or Sunday was attended; absent on all three, the full '
             '8h day is deducted. '
             'Used for schedules such as "Standard 44 hours/week" (Sat 3h) '
             'and "Abdullah Mutawa Special Shift" (Sat 4h).',
    )

    x_skip_attendance_issues = fields.Boolean(
        string='Skip Late/Early Leave Checks',
        help='When set, late-arrival and early-departure minutes are not '
             'recorded for employees using this schedule. Use for roles '
             'where shift times vary frequently (e.g. executives). '
             'Absence detection is still active.',
    )

    calendar_group_ids = fields.Many2many(
        'resource.calendar.group',
        'resource_calendar_group_rel',
        'calendar_id',
        'group_id',
        string='Calendar Groups',
    )

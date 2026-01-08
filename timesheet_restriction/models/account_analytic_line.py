from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import date


class AccountAnalyticLine(models.Model):
    _inherit = 'account.analytic.line'


    def write(self, vals):
        """
        Restrict modification of analytic entries (timesheets)
        that are not in the current month — unless user has permission.
        """
        for record in self:
            # Only restrict if modifying key fields
            if 'unit_amount' in vals or 'amount' in vals or 'name' in vals:
                record_date = record.date
                today = date.today()

                # Check if record is from a previous month
                if (record_date.year, record_date.month) != (today.year, today.month):
                    has_group = self.env.user.has_group('timesheet_restriction.group_can_reduce_past_timesheets')

                    if not has_group:
                        raise UserError(_(
                            "⛔ You cannot modify analytic entries from past months.\n"
                            "Only users with the 'Can Reduce Past Timesheets' permission can do so."
                        ))

        return super().write(vals)

    def unlink(self):
        """
        Also restrict deletion of past-month entries for unauthorized users.
        """
        for record in self:
            record_date = record.date
            today = date.today()

            if (record_date.year, record_date.month) != (today.year, today.month):
                has_group = self.env.user.has_group('timesheet_restriction.group_can_reduce_past_timesheets')

                if not has_group:
                    raise UserError(_(
                        "You cannot delete analytic entries from past months."
                    ))

        return super().unlink()

from odoo import models, api, _
from odoo.exceptions import ValidationError

class ProjectTask(models.Model):
        _inherit = 'project.task'

        @api.constrains('user_ids')
        def _check_user_assignment(self):
            """Prevent saving a task without at least one assigned user."""
            for task in self:
                if not task.user_ids:
                    raise ValidationError(_(
                        "You cannot create or save a task without assigning at least one user.\n"
                        f"Please assign a responsible user to the task '{task.name}'."
                    ))

        @api.model_create_multi
        def create(self, vals_list):
            """Ensure tasks have assignees on creation."""
            tasks = super().create(vals_list)
            tasks._check_user_assignment()
            return tasks

        def write(self, vals):
            """Re-validate on update."""
            res = super().write(vals)
            self._check_user_assignment()
            return res



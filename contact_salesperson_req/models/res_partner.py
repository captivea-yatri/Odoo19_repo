from odoo import models, api

class ResPartner(models.Model):
    _inherit = 'res.partner'


    @api.model_create_multi
    def create(self, vals_list):
        """Inherit the parent company's user_id when creating a child contact."""
        for vals in vals_list:
            parent_id = vals.get('parent_id')
            # If creating a child and user_id is not defined
            if parent_id and not vals.get('user_id'):
                parent = self.env['res.partner'].browse(parent_id)
                if parent.user_id:
                    vals['user_id'] = parent.user_id.id
        return super().create(vals_list)


    def write(self, vals):
        """When parent's user_id changes, apply the same to its children."""
        res = super().write(vals)

        if 'user_id' in vals:
            for partner in self:
                # Apply only to companies with children
                if partner.child_ids:
                    # Use sudo to ensure no permission errors
                    children = self.env['res.partner'].sudo().search([('parent_id', '=', partner.id)])
                    # Write new user_id or False if cleared
                    children.write({
                        'user_id': partner.user_id.id if partner.user_id else False
                    })
        return res


    @api.onchange('user_id')
    def _onchange_user_id(self):
        """Instantly propagate in UI (only works for editable One2many lines)."""
        for partner in self:
            for child in partner.child_ids:
                child.user_id = partner.user_id

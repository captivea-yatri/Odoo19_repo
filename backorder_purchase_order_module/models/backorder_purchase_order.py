from odoo import models, fields, api, _
from odoo.exceptions import UserError



class BackorderPurchaseOrder(models.Model):
    _name = 'backorder.purchase.order'
    _description = 'Backorder Purchase Order'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Order Reference', required=True, copy=False, readonly=True, default='New')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('confirm', 'Confirmed'),
    ], default='draft', string='Status')
    company_id = fields.Many2one('res.company', string='Company', required=True, default=lambda self: self.env.company)

    order_line = fields.One2many('backorder.purchase.order.line', 'order_id', string='Order Lines')
    move_ids = fields.Many2many('stock.move', 'bo_purchase_stock_move_rel', 'bo_purchase_id', 'move_id',
                                string="Stock Moves")
    vendor_id = fields.Many2one('res.partner', string='Vendor', required=True)
    account_move_id = fields.Many2one('account.move', string='Journal Entry', readonly=True)

    @api.onchange('vendor_id')
    def _onchange_vendor_id_set_on_lines(self):
        """When vendor is changed, auto-fill or update all existing lines."""
        for rec in self:
            if rec.vendor_id:
                for line in rec.order_line:
                    if not line.vendor_id:
                        line.vendor_id = rec.vendor_id

    @api.model
    def create(self, vals_list):
        """Generate sequence-based project reference."""
        for vals in vals_list:
            if vals.get('name', 'New') in [False, '/', 'New']:
                vals['name'] = self.env['ir.sequence'].next_by_code('seq.refernce') or 'New'
        return super(BackorderPurchaseOrder, self).create(vals_list)

    def write(self, vals):
        """
        Restrict editing confirmed orders unless called from the update wizard.
        """
        # ✅ Allow all updates from wizard context
        if self.env.context.get('allow_wizard_write'):
            return super().write(vals)

        # 🚫 Block manual edits on confirmed orders
        for order in self:
            if order.state == 'confirm':
                raise UserError(_("You cannot modify a confirmed order directly. Please use the Update Wizard."))

        return super().write(vals)

    def action_confirm(self):
        for order in self:
            if order.state != 'draft':
                raise UserError(_("You can only confirm a draft order."))

            # Group lines by vendor
            vendor_groups = {}
            for line in order.order_line:
                vendor_groups.setdefault(line.vendor_id, []).append(line)

            for vendor, lines in vendor_groups.items():
                vendor_location = vendor.property_stock_supplier or self.env.ref('stock.stock_location_suppliers')
                stock_location = self.env.ref('stock.stock_location_stock')

                total_amount = 0.0
                created_moves = []

                # ✅ Create stock moves (one per product line)
                for line in lines:
                    total_amount += line.total
                    move = self.env['stock.move'].create({
                        'product_id': line.product_id.id,
                        'product_uom': line.product_id.uom_id.id,
                        'location_id': vendor_location.id,
                        'location_dest_id': stock_location.id,
                        'product_uom_qty': line.quantity,
                        'price_unit': line.price,
                        'state': 'draft',
                        'origin': order.name,  # visible in Move History
                        'reference': order.name,  # ✅ native Odoo field — shown in product move history
                        'value': line.total,
                        'company_id': order.company_id.id,
                    })

                    self.env['stock.move.line'].create({
                        'move_id': move.id,
                        'product_id': line.product_id.id,
                        'product_uom_id': line.product_id.uom_id.id,
                        'qty_done': line.quantity,
                        'location_id': vendor_location.id,
                        'location_dest_id': stock_location.id,
                        'company_id': order.company_id.id,
                    })

                    move._action_confirm()
                    move._action_assign()
                    move._action_done()
                    created_moves.append(move.id)

                # Link moves to order
                if created_moves:
                    order.move_ids = [(6, 0, created_moves)]

                # ✅ Create one accounting entry per vendor group (not per line)
                if total_amount <= 0:
                    raise UserError(_("Cannot create a Journal Entry without a total amount."))

                expense_account = self.env['account.account'].search([
                    ('account_type', '=', 'expense')
                ], limit=1)
                payable_account = self.env['account.account'].search([
                    ('account_type', '=', 'liability_payable')
                ], limit=1)
                journal = self.env['account.journal'].search([('type', '=', 'general')], limit=1)

                if not (expense_account and payable_account and journal):
                    raise UserError(_("Please configure Expense, Payable accounts and a General Journal."))

                move_vals = {
                    'move_type': 'entry',
                    'journal_id': journal.id,
                    'date': fields.Date.context_today(self),
                    'ref': order.name,  # ✅ Use 'ref' (correct field) for account.move
                    'line_ids': [
                        # Debit Expense
                        (0, 0, {
                            'name': f'Backorder Purchase Expense ({order.name})',
                            'account_id': expense_account.id,
                            'debit': total_amount,
                            'credit': 0.0,
                            'partner_id': vendor.id,
                        }),
                        # Credit Payable
                        (0, 0, {
                            'name': f'Payable to Vendor ({order.name})',
                            'account_id': payable_account.id,
                            'credit': total_amount,
                            'debit': 0.0,
                            'partner_id': vendor.id,
                        }),
                    ]
                }

                account_move = self.env['account.move'].create(move_vals)
                account_move.action_post()
                order.account_move_id = account_move.id

            order.state = 'confirm'
            order.message_post(
                body=_("Backorder Purchase Order <b>%s</b> has been confirmed.") % order.name,
                subtype_xmlid="mail.mt_note"
            )

    # def action_set_to_draft(self):
    #     for order in self:
    #         if order.state != 'confirm':
    #             raise UserError("Only confirmed orders can be set to draft.")
    #
    #         order.state = 'draft'
    #         # order.write({'readonly': False})

    def action_view_stock_moves(self):
        view_id = self.env.ref('stock.view_move_tree').id
        return {
            'name': _('Detailed Operations'),
            'view_mode': 'list',
            'type': 'ir.actions.act_window',
            'res_model': 'stock.move',
            'views': [(view_id, 'list')],
            'domain': [('id', 'in', self.move_ids.ids)],
            # 'context': {
            #     'default_picking_id': self.id,
            #     'default_location_id': self.location_id.id,
            #     'default_location_dest_id': self.location_dest_id.id,
            #     'default_company_id': self.company_id.id,
            #     'show_lots_text': self.show_lots_text,
            #     'picking_code': self.picking_type_code,
            #     'create': self.state not in ('done', 'cancel'),
            # }
        }

    def action_view_account_move(self):
        """Open related journal entry."""
        self.ensure_one()
        if not self.account_move_id:
            raise UserError(_("No Journal Entry linked to this order."))

        return {
            'type': 'ir.actions.act_window',
            'name': _('Journal Entry'),
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': self.account_move_id.id,
        }

    def action_open_update_wizard(self):
        """Open wizard to update order information"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'backorder.purchase.update.wizard',
            'view_mode': 'form',
            'target': 'new',  # Open as a popup
            'context': {
                'default_order_id': self.id,
                'default_vendor_id': self.vendor_id.id,
            }
        }


class BackorderPurchaseOrderLine(models.Model):
    _name = 'backorder.purchase.order.line'
    _description = 'Backorder Purchase Order Line'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    active = fields.Boolean(string='Active', default=True)

    product_id = fields.Many2one('product.product', string='Product', required=True)
    vendor_id = fields.Many2one('res.partner', string='Vendor', required=True)
    quantity = fields.Float('Quantity', required=True)
    price = fields.Float('Price', required=True)
    total = fields.Monetary('Total', compute='_compute_total', store=True, currency_field='currency_id')
    order_id = fields.Many2one('backorder.purchase.order', string='Order Reference')
    company_id = fields.Many2one('res.company', string='Company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one('res.currency', 'Currency', related='company_id.currency_id', readonly=True,
                                  required=True)
    move_id = fields.Many2many('stock.move',  string="Stock Moves")


    @api.depends('quantity', 'price')
    def _compute_total(self):
        for record in self:
            record.total = record.quantity * record.price

    @api.onchange('order_id')
    def _onchange_order_id_set_vendor(self):
        """Auto-set vendor from parent order when new line is created"""
        for line in self:
            if line.order_id and line.order_id.vendor_id:
                line.vendor_id = line.order_id.vendor_id

    def write(self, vals):
        if self.env.context.get('allow_wizard_write'):
            return super().write(vals)

        if self.order_id and self.order_id.state == 'confirm':
            raise UserError(_("You cannot modify lines of a confirmed order directly. Please use the Update Wizard."))
        return super().write(vals)


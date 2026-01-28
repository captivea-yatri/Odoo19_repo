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
    bank_account_id = fields.Many2one('account.account',
        string='Account',
        help="Set Accounts to Manage the Manual Cash Given",
        tracking=True,
        check_company=True,)
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

            vendor = order.vendor_id
            banckaccount=order.bank_account_id
            vendor_location = vendor.property_stock_supplier or self.env.ref('stock.stock_location_suppliers')
            stock_location = self.env.ref('stock.stock_location_stock')


            total_amount = 0.0
            created_moves = []

            # ✅ Group by product (only one move per product)
            # ✅ Create one move per product line
            for line in order.order_line:
                product = line.product_id
                qty = line.quantity
                price_unit = line.price
                vendor_line = line.vendor_id
                total_value = qty * price_unit
                total_amount += total_value

                # ✅ Create Stock Move (linked to order line)
                move = self.env['stock.move'].create({
                    'product_id': product.id,
                    'product_uom': product.uom_id.id,
                    'location_id': vendor_location.id,
                    'location_dest_id': stock_location.id,
                    'product_uom_qty': qty,
                    'price_unit': price_unit,
                    'value': total_value,
                    'state': 'draft',
                    'origin': order.name,
                    'reference': order.name,
                    'company_id': order.company_id.id,
                    'partner_id': vendor_line.id,
                    'backorder_line_id': line.id,  # ✅ linking line to move
                })

                # ✅ Create corresponding move line
                self.env['stock.move.line'].create({
                    'move_id': move.id,
                    'product_id': product.id,
                    'product_uom_id': product.uom_id.id,
                    'qty_done': qty,
                    'location_id': vendor_location.id,
                    'location_dest_id': stock_location.id,
                    'company_id': order.company_id.id,
                })

                # ✅ Process move
                move._action_confirm()
                move._action_assign()
                move._action_done()

                # ✅ Persist value & reference

                move.write({
                    'value': total_value,
                    'reference': order.name,
                })


                # ✅ Link move to line
                line.move_id = [(4, move.id)]
                created_moves.append(move.id)

                self._recompute_product_cost(line.product_id)
            # # ✅ Link moves to order
            if created_moves:
                order.move_ids = [(6, 0, created_moves)]

            for move in order.move_ids:
                line = order.order_line.filtered(lambda line: line.id == move.backorder_line_id.id)
                move.value = line.quantity * line.price




            # ✅ Accounting entry (as before)
            if total_amount <= 0:
                raise UserError(_("Cannot create a Journal Entry without a total amount."))

            expense_account = self.env['account.account'].search([('account_type', '=', 'expense')], limit=1)
            payable_account = order.bank_account_id
            journal = self.env['account.journal'].search([('type', '=', 'general')], limit=1)

            if not (expense_account and payable_account and journal):
                raise UserError(_("Please configure Expense, Payable accounts and a General Journal."))

            move_vals = {
                'move_type': 'entry',
                'journal_id': journal.id,
                'date': fields.Date.context_today(self),
                'ref': order.name,
                'line_ids': [
                    # ✅ Debit: Expense
                    (0, 0, {
                        'name': f'Backorder Purchase Expense ({order.name})',
                        'account_id': expense_account.id,
                        'debit': total_amount,
                        'credit': 0.0,
                        'partner_id': order.order_line[0].vendor_id.id,
                    }),
                    # ✅ Credit: Payable to delivery boy
                    (0, 0, {
                        'name': f'Payable to Delivery ',
                        'account_id': payable_account.id,
                        'debit': 0.0,
                        'credit': total_amount,
                        'partner_id': order.order_line[0].vendor_id.id,
                    }),
                ]
            }

            account_move = self.env['account.move'].create(move_vals)
            account_move.action_post()
            order.account_move_id = account_move.id

            # ✅ Mark confirmed
            order.state = 'confirm'
            order.message_post(
                body=_("Backorder Purchase Order <b> %s </b> confirmed. "
                       "<br/>Stock Moves updated with reference & total value.") % order.name,
                subtype_xmlid="mail.mt_note",
            body_is_html = True
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

    def _recompute_product_cost(self, product):
        """Recalculate average cost based on stock quant."""
        quant = self.env['stock.quant'].search([
            ('product_id', '=', product.id),
            ('location_id.usage', '=', 'internal')
        ], limit=1)
        if quant and quant.quantity:
            product.standard_price= quant.value / quant.quantity





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

